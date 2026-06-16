#!/usr/bin/env python3
"""
Red Team Send — helper for Pliny Red Team Live sessions.
Sends a prompt to a target model and reports both the prompt and response
to the Pliny Command dashboard via API.

Usage:
    python3 rt_send.py <attempt> <provider> <model_id> <<'PROMPT'
    Your prompt text here
    PROMPT

Providers: anthropic, openai, google, xai, meta, mistral, deepseek, custom
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

DASHBOARD_API = "http://localhost:8888/api/redteam/chat"
# Session-id passthrough so pack-attack mode (N concurrent rt_send.py callers
# against N different blood-agent sessions) tags each chat event with the
# correct session. Set by the server when spawning the Claude CLI subprocess.
_PLINY_SESSION_ID = os.environ.get("PLINY_SESSION_ID", "")


# ─── Error sentinel helpers ──────────────────────────────────────────────────
# The gauntlet recognizes these prefixes and treats them as zero-info upstream
# failures: they do NOT burn the attempt budget and are surfaced to the agent
# so it can retry with a different technique or bail out.
#
#   [BLOCKED — ...]    classifier / safety filter / content policy block
#   [RATELIMIT — ...]  429 from provider or similar
#   [TIMEOUT — ...]    request exceeded our wall-clock limit
#   [NETWORK — ...]    DNS / connection / TLS failure before response
#   [ERROR — ...]      catch-all for anything else (auth, 5xx, parse, etc.)

_BLOCK_KEYWORDS = (
    "content_policy_violation", "content policy", "safety",
    "blocked", "content_filter", "responsible ai", "harmful",
    "cannot assist", "moderation", "prohibited",
)


def _classify_http_error(provider_label, err):
    """Classify a urllib HTTPError into the right sentinel prefix.

    Reads the body (best effort), inspects the status code and body text,
    and returns a (prefix, detail) tuple the caller can format.
    """
    code = getattr(err, "code", 0) or 0
    try:
        body = err.read().decode("utf-8", errors="replace")[:500]
    except Exception:
        body = str(err)
    body_lower = body.lower()

    if code == 429 or "rate limit" in body_lower or "rate_limit" in body_lower or "too many requests" in body_lower:
        return "RATELIMIT", f"{provider_label} 429: {body[:200]}"
    if code in (400, 403, 451, 422):
        if any(k in body_lower for k in _BLOCK_KEYWORDS):
            return "BLOCKED", f"{provider_label} {code} (classifier): {body[:200]}"
        # Some providers return 400 for malformed requests too — keep as ERROR
        return "ERROR", f"{provider_label} {code}: {body[:200]}"
    if code in (401, 402, 403):
        return "ERROR", f"{provider_label} auth {code}: {body[:200]}"
    if 500 <= code < 600:
        return "ERROR", f"{provider_label} {code} (server): {body[:200]}"
    return "ERROR", f"{provider_label} {code}: {body[:200]}"


def _classify_exception(provider_label, exc):
    """Classify a non-HTTP exception (timeout, DNS, etc.)."""
    if isinstance(exc, socket.timeout):
        return "TIMEOUT", f"{provider_label}: request timed out"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        return "NETWORK", f"{provider_label}: {reason}"
    # Unknown — plain ERROR
    return "ERROR", f"{provider_label}: {type(exc).__name__}: {str(exc)[:200]}"


def _fmt_sentinel(prefix, detail):
    return f"[{prefix} — {detail}]"


def post_to_dashboard(data, retries=2):
    """Post a message to the dashboard for live display."""
    # Inject session_id so the dashboard can persist per-session chat history
    # (used by pack-attack mode to backfill the chat panel on follow-switch).
    if _PLINY_SESSION_ID and "session_id" not in data:
        data = dict(data, session_id=_PLINY_SESSION_ID)
    for attempt in range(retries + 1):
        try:
            body = json.dumps(data).encode()
            req = urllib.request.Request(
                DASHBOARD_API, body, {"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=3)
            return  # success
        except Exception as e:
            if attempt < retries:
                import time; time.sleep(0.5)
            else:
                # Log silently so at least stderr shows something
                print(f"[rt_send] dashboard post failed: {e}", file=sys.stderr)


# ─── Structured-meta sidechannel ─────────────────────────────────────────────
# Probe finish_reason dark-code closure (2026-05-23):
#   The Sidewinder shipped classify_upstream_error finish_reason awareness on
#   May 21, but rt_send.py was stripping the field on the way out — Layer 1
#   never saw it. This helper emits the structured response metadata to stderr
#   so gauntlet._run_rt_send can parse it and plumb it into record_attempt.
#
# Format chosen to be:
#   - cheap to parse (one line, space-separated kv)
#   - hard to false-positive in normal logs (prefix anchor [META])
#   - byte-stable on stdout (stderr-only emission preserves backward compat)
#
# See: red-team-notes/intel/20260523_probe_finishreason_dark_code/FINDING.md
def _emit_meta(finish, usage, max_tokens):
    """Emit structured response metadata via stderr sidechannel.

    Args:
      finish: provider's finish_reason string (or empty)
      usage: provider's usage dict (or None / empty)
      max_tokens: the cap we asked the provider for (or None / 0)

    Backward-compat: stdout is untouched. Callers that don't parse stderr
    (everyone before today) see no change in behavior.
    """
    try:
        u = usage or {}
        details = u.get("completion_tokens_details") or {}
        rt = details.get("reasoning_tokens") if isinstance(details, dict) else None
        ct = u.get("completion_tokens")
        pt = u.get("prompt_tokens")
        print(
            f"[META] finish_reason={finish or ''} "
            f"reasoning_tokens={rt if rt is not None else ''} "
            f"completion_tokens={ct if ct is not None else ''} "
            f"prompt_tokens={pt if pt is not None else ''} "
            f"max_tokens={max_tokens or ''}",
            file=sys.stderr,
        )
    except Exception as _e:
        # Sidechannel must never break the primary path. Swallow + log.
        print(f"[rt_send] _emit_meta failed: {_e}", file=sys.stderr)


# ─── Provider send functions ─────────────────────────────────────────────────

def _collect_sandbox_files(sandbox_dir):
    """Walk sandbox dir and return list of {path, content, size} for files the target created."""
    artifacts = []
    for root, _dirs, files in os.walk(sandbox_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, sandbox_dir)
            try:
                size = os.path.getsize(fpath)
                # Read text files up to 8KB, skip binaries
                if size > 8192:
                    content = f"[binary/large file — {size} bytes]"
                else:
                    try:
                        with open(fpath, "r") as f:
                            content = f.read()
                    except UnicodeDecodeError:
                        content = f"[binary file — {size} bytes]"
                artifacts.append({"path": rel, "content": content, "size": size})
            except OSError:
                pass
    return artifacts


def send_to_anthropic(prompt, model_id):
    """Send via Claude CLI in a sandboxed temp dir. Tools enabled so we see what the target does."""
    sandbox = tempfile.mkdtemp(prefix="rt_sandbox_")
    try:
        cmd = [
            "claude", "-p", prompt,
            "--output-format", "json",
            "--max-turns", "5",                    # Enough turns to use tools + respond
            "--setting-sources", "",                  # Skip CLAUDE.md files — clean eval target
            "--tools", "Write",                        # Write only — no Read/Bash/shell escape
            "--dangerously-skip-permissions",       # Auto-approve file operations
        ]
        # Map gauntlet model IDs to claude CLI --model values.
        # Pass full model names so the CLI doesn't silently resolve aliases
        # to whatever it considers "latest" — operator must get the model
        # they asked for, especially Fable 5 which used to omit --model
        # entirely and inherit the CLI default (currently opus-4-8).
        _claude_model_map = {
            "claude-fable-5":  "claude-fable-5",
            "claude-opus-4-8": "claude-opus-4-8",
            "claude-opus-4-7": "claude-opus-4-7",
            "claude-opus-4-6": "claude-opus-4-6",
            "claude-sonnet-4": "sonnet",
        }
        cli_model = _claude_model_map.get(model_id, model_id)
        if cli_model:
            cmd += ["--model", cli_model]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180,
            cwd=sandbox,  # Sandboxed — all file ops happen here
        )

        # Collect any files the target created
        artifacts = _collect_sandbox_files(sandbox)

        # Parse response
        text = ""
        meta_parts = []
        # Claude CLI doesn't expose a max_tokens cap; the helper-call below
        # uses 0 as the "unset" sentinel so the lens treats finish_reason as
        # the only structural signal for anthropic.
        try:
            data = json.loads(result.stdout)
            text = data.get("result", "")
            # Surface the model that ACTUALLY served the response so silent
            # CLI reroutes (alias → latest) are visible to the operator.
            served = list((data.get("modelUsage") or {}).keys())
            if served:
                served_str = served[0] if len(served) == 1 else "+".join(served)
                meta_parts.append(f"served: {served_str}")
                if cli_model and cli_model not in served and served_str != cli_model:
                    meta_parts.append(f"\u26A0 asked: {cli_model}")
            cost = data.get("total_cost_usd")
            if cost:
                meta_parts.append(f"${cost:.4f}")
            usage = data.get("usage", {}) or {}
            out_tokens = usage.get("output_tokens")
            if out_tokens:
                meta_parts.append(f"{out_tokens} tokens")
            turns = data.get("num_turns", 1)
            if turns > 1:
                meta_parts.append(f"{turns} turns")
            stop = data.get("stop_reason", "")
            if stop and stop != "end_turn":
                meta_parts.append(f"stop: {stop}")
            # Map anthropic stop_reason → finish_reason for the META sidechannel.
            # end_turn ≈ stop ; max_tokens ≈ length ; everything else passes
            # through verbatim so the lens can recognize tool_use, refusal, etc.
            _anth_finish_map = {"end_turn": "stop", "max_tokens": "length"}
            _anth_finish = _anth_finish_map.get(stop or "", stop or "")
            _emit_meta(
                _anth_finish,
                {"completion_tokens": usage.get("output_tokens"),
                 "prompt_tokens": usage.get("input_tokens")},
                0,  # claude CLI has no client-side cap to surface
            )
        except json.JSONDecodeError:
            text = result.stdout[:4000] if result.stdout else result.stderr[:2000]
            _emit_meta("", None, 0)

        # Report artifacts as structured cards to dashboard (before response)
        if artifacts:
            post_to_dashboard({
                "type": "artifacts",
                "files": [{"path": a["path"], "size": a["size"], "content": a["content"][:3000]} for a in artifacts],
            })
            # Add summary line to response text (not full content — cards handle that)
            fnames = ", ".join(f"`{a['path']}`" for a in artifacts)
            text += f"\n\n📁 Created {len(artifacts)} file(s): {fnames}"

        if meta_parts:
            text += f"\n\n`[{' · '.join(meta_parts)}]`"

        return text if text else "[No response from target]"
    except subprocess.TimeoutExpired:
        return "[TIMEOUT — target took >180s to respond]"
    except FileNotFoundError:
        return "[ERROR — claude CLI not found in PATH]"
    finally:
        # Clean up sandbox
        shutil.rmtree(sandbox, ignore_errors=True)


def send_to_openai(prompt, model_id):
    """Send via OpenAI API, with OpenRouter fallback."""
    api_key = os.environ.get("OPENAI_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key and not openrouter_key:
        return "[ERROR — Neither OPENAI_API_KEY nor OPENROUTER_API_KEY set.]"
    # Map friendly IDs to API model names
    model_map = {
        "gpt-5.5": "gpt-5.5",
        "gpt-5.5-pro": "gpt-5.5-pro",
        "gpt-5.4": "gpt-5.4",
        "gpt-5.4-pro": "gpt-5.4-pro",
        "gpt-5.4-mini": "gpt-5.4-mini",
        "gpt-5.4-nano": "gpt-5.4-nano",
        "gpt-4.1": "gpt-4.1",
        "gpt-4.1-mini": "gpt-4.1-mini",
        "gpt-4.1-nano": "gpt-4.1-nano",
        "o3-pro": "o3-pro",
        "o3": "o3",
        "o4-mini": "o4-mini",
    }
    api_model = model_map.get(model_id, model_id)
    # Prefer OpenRouter when available (direct OpenAI key is out of credits on this host)
    if openrouter_key:
        base_url = "https://openrouter.ai/api/v1/chat/completions"
        auth_key = openrouter_key
        # OpenRouter uses openai/ prefix for OpenAI models
        or_model = f"openai/{api_model}"
    else:
        base_url = "https://api.openai.com/v1/chat/completions"
        auth_key = api_key
        or_model = api_model
    # Red-team the LOW-thinking variant of reasoning-capable models. The deep-thinking
    # variant is materially harder to jailbreak; the typical end-user faces the
    # default/low-reasoning surface, so that's what we benchmark against.
    body_obj = {
        "model": or_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
    }
    _reasoning_models = ("gpt-5", "o3", "o4")
    if api_model.startswith(_reasoning_models):
        body_obj["reasoning_effort"] = os.environ.get("OPENAI_REASONING_EFFORT", "minimal")
    try:
        body = json.dumps(body_obj).encode()
        req = urllib.request.Request(
            base_url,
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_key}",
            },
        )
        resp = urllib.request.urlopen(req, timeout=90)
        data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        # OpenAI sometimes blocks via choices[0].finish_reason == "content_filter"
        # with empty content — catch that explicitly as BLOCKED.
        finish = choice.get("finish_reason") or ""
        content = ((choice.get("message") or {}).get("content") or "").strip()
        _emit_meta(finish, data.get("usage"), body_obj.get("max_tokens"))
        if finish == "content_filter" and not content:
            return _fmt_sentinel("BLOCKED", f"OpenAI ({or_model}) finish_reason=content_filter")
        if not content:
            return _fmt_sentinel("ERROR", f"OpenAI ({or_model}) returned empty content (finish={finish})")
        return content
    except urllib.error.HTTPError as e:
        prefix, detail = _classify_http_error(f"OpenAI ({or_model})", e)
        return _fmt_sentinel(prefix, detail)
    except Exception as e:
        prefix, detail = _classify_exception(f"OpenAI ({or_model})", e)
        return _fmt_sentinel(prefix, detail)


def send_to_google(prompt, model_id):
    """Send via Google Gemini API, with OpenRouter fallback."""
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key and not openrouter_key:
        return "[ERROR — Neither GOOGLE_API_KEY nor OPENROUTER_API_KEY set.]"
    model_map = {
        "gemini-3.1-pro": "gemini-3.1-pro-preview",
        "gemini-3-flash": "gemini-3-flash-preview",
        "gemini-3.1-flash-lite": "gemini-3.1-flash-lite-preview",
        "gemini-2.5-pro": "gemini-2.5-pro",
        "gemini-2.5-flash": "gemini-2.5-flash",
        "gemini-3.5-flash": "gemini-3.5-flash",
    }
    # OpenRouter model mapping
    or_model_map = {
        "gemini-3.5-flash": "google/gemini-3.5-flash",
        "gemini-3.1-pro-preview": "google/gemini-3.1-pro-preview",
        "gemini-3.1-pro": "google/gemini-3.1-pro-preview",
        "gemini-3-pro-preview": "google/gemini-3.1-pro-preview",
        "gemini-3-flash-preview": "google/gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview": "google/gemini-3-flash-lite-preview",
        "gemini-2.5-pro": "google/gemini-2.5-pro",
        "gemini-2.5-flash": "google/gemini-2.5-flash",
    }
    api_model = model_map.get(model_id, model_id)
    if api_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{api_model}:generateContent?key={api_key}"
            gemini_max_tokens = 2000
            body = json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": gemini_max_tokens},
            }).encode()
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            resp = urllib.request.urlopen(req, timeout=90)
            data = json.loads(resp.read())

            # Map Gemini's usageMetadata to OpenAI-shape usage so _emit_meta
            # is uniform across providers. thoughtsTokenCount is Gemini's name
            # for reasoning_tokens on Gemini-3.x reasoning models.
            um = data.get("usageMetadata") or {}
            _g_usage = {
                "prompt_tokens": um.get("promptTokenCount"),
                "completion_tokens": um.get("candidatesTokenCount"),
                "completion_tokens_details": (
                    {"reasoning_tokens": um.get("thoughtsTokenCount")}
                    if um.get("thoughtsTokenCount") is not None else None
                ),
            }

            # Prompt-level safety block: no candidates, promptFeedback.blockReason set
            pf = data.get("promptFeedback") or {}
            if pf.get("blockReason"):
                _emit_meta(f"block:{pf.get('blockReason')}", _g_usage, gemini_max_tokens)
                return _fmt_sentinel("BLOCKED", f"Gemini ({api_model}) promptFeedback.blockReason={pf.get('blockReason')}")

            candidates = data.get("candidates") or []
            if not candidates:
                _emit_meta("", _g_usage, gemini_max_tokens)
                return _fmt_sentinel("BLOCKED", f"Gemini ({api_model}) no candidates returned (likely prompt-level block)")

            cand = candidates[0]
            finish = cand.get("finishReason") or ""
            _emit_meta(finish, _g_usage, gemini_max_tokens)
            # Response-level safety block: finishReason == SAFETY / RECITATION / PROHIBITED_CONTENT
            if finish in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
                return _fmt_sentinel("BLOCKED", f"Gemini ({api_model}) finishReason={finish}")

            parts = ((cand.get("content") or {}).get("parts") or [])
            if not parts:
                return _fmt_sentinel("BLOCKED", f"Gemini ({api_model}) empty content parts (finishReason={finish or 'unset'})")

            text = parts[0].get("text", "")
            if not text.strip():
                return _fmt_sentinel("ERROR", f"Gemini ({api_model}) empty text (finishReason={finish or 'unset'})")
            return text
        except urllib.error.HTTPError as e:
            prefix, detail = _classify_http_error(f"Gemini ({api_model})", e)
            return _fmt_sentinel(prefix, detail)
        except Exception as e:
            prefix, detail = _classify_exception(f"Gemini ({api_model})", e)
            return _fmt_sentinel(prefix, detail)
    # OpenRouter fallback
    or_model = or_model_map.get(api_model, or_model_map.get(model_id, f"google/{api_model}"))
    try:
        # Gemini 3.x are reasoning models — burn tokens internally before
        # producing visible output. Default 2000 truncates visible text to
        # ~300 chars. Bump to 16000 + reasoning.effort=low for full output.
        is_reasoning = "gemini-3" in (or_model or "")
        body_payload = {
            "model": or_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16000 if is_reasoning else 2000,
        }
        if is_reasoning:
            body_payload["reasoning"] = {"effort": "low"}
        body = json.dumps(body_payload).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {openrouter_key}",
            },
        )
        resp = urllib.request.urlopen(req, timeout=90)
        data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or ""
        content = ((choice.get("message") or {}).get("content") or "").strip()
        _emit_meta(finish, data.get("usage"), body_payload.get("max_tokens"))
        if finish == "content_filter" and not content:
            return _fmt_sentinel("BLOCKED", f"OpenRouter Gemini ({or_model}) finish_reason=content_filter")
        if not content:
            return _fmt_sentinel("ERROR", f"OpenRouter Gemini ({or_model}) empty content (finish={finish})")
        return content
    except urllib.error.HTTPError as e:
        prefix, detail = _classify_http_error(f"OpenRouter Gemini ({or_model})", e)
        return _fmt_sentinel(prefix, detail)
    except Exception as e:
        prefix, detail = _classify_exception(f"OpenRouter Gemini ({or_model})", e)
        return _fmt_sentinel(prefix, detail)


def send_to_xai(prompt, model_id):
    """Send via xAI Grok API (OpenAI-compatible), with OpenRouter fallback."""
    api_key = os.environ.get("XAI_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key and not openrouter_key:
        return "[ERROR — Neither XAI_API_KEY nor OPENROUTER_API_KEY set.]"
    model_map = {
        "grok-4.20": "grok-4.20-0309-reasoning",
        "grok-4.20-nr": "grok-4.20-0309-non-reasoning",
        "grok-4-1-fast": "grok-4-1-fast-reasoning",
        "grok-3": "grok-3",
    }
    # OpenRouter model mapping
    or_model_map = {
        "grok-4.20": "x-ai/grok-4.20",
        "grok-4.20-nr": "x-ai/grok-4.20",
        "grok-4-1-fast": "x-ai/grok-4.1-fast",
        "grok-3": "x-ai/grok-3",
    }
    if api_key:
        base_url = "https://api.x.ai/v1/chat/completions"
        auth_key = api_key
        api_model = model_map.get(model_id, model_id)
    else:
        base_url = "https://openrouter.ai/api/v1/chat/completions"
        auth_key = openrouter_key
        api_model = or_model_map.get(model_id, f"x-ai/{model_id}")
    xai_max_tokens = 2000
    try:
        body = json.dumps({
            "model": api_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": xai_max_tokens,
        }).encode()
        req = urllib.request.Request(
            base_url,
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_key}",
            },
        )
        resp = urllib.request.urlopen(req, timeout=90)
        data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or ""
        content = ((choice.get("message") or {}).get("content") or "").strip()
        _emit_meta(finish, data.get("usage"), xai_max_tokens)
        if finish == "content_filter" and not content:
            return _fmt_sentinel("BLOCKED", f"xAI ({api_model}) finish_reason=content_filter")
        if not content:
            return _fmt_sentinel("ERROR", f"xAI ({api_model}) empty content (finish={finish})")
        return content
    except urllib.error.HTTPError as e:
        prefix, detail = _classify_http_error(f"xAI ({api_model})", e)
        return _fmt_sentinel(prefix, detail)
    except Exception as e:
        prefix, detail = _classify_exception(f"xAI ({api_model})", e)
        return _fmt_sentinel(prefix, detail)


def send_to_ollama(prompt, model_id):
    """Send via local Ollama."""
    # Map model IDs to Ollama model names
    ollama_map = {
        "llama-4-scout": "llama4:scout",
        "llama-4-maverick": "llama4:maverick",
        "llama-3.3-70b": "llama3.3:70b",
        "llama-3.1-405b": "llama3.1:405b",
        "deepseek-v3": "deepseek-v3",
        "deepseek-r1": "deepseek-r1",
        "deepseek-r1-0528": "deepseek-r1:0528",
        "mistral-large-latest": "mistral-large",
        "codestral-latest": "codestral",
        "mistral-small-latest": "mistral-small",
        "pixtral-large-latest": "pixtral-large",
    }
    ollama_model = ollama_map.get(model_id, model_id)
    try:
        body = json.dumps({
            "model": ollama_model,
            "prompt": prompt,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            body,
            {"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=180)
        data = json.loads(resp.read())
        return data.get("response", str(data))
    except Exception as e:
        return f"[ERROR — Ollama ({ollama_model}): {e}]"


def send_to_mistral(prompt, model_id):
    """Send via Mistral API, with OpenRouter fallback."""
    api_key = os.environ.get("MISTRAL_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key and not openrouter_key:
        # Fallback to Ollama if no API key
        return send_to_ollama(prompt, model_id)

    # OpenRouter model mapping for Mistral
    openrouter_map = {
        "codestral-latest": "mistralai/codestral-2508",
        "mistral-large-latest": "mistralai/mistral-large-2411",
        "mistral-small-latest": "mistralai/mistral-small-2503",
        "pixtral-large-latest": "mistralai/pixtral-large-2411",
    }

    if api_key:
        base_url = "https://api.mistral.ai/v1/chat/completions"
        auth_key = api_key
        api_model = model_id
    else:
        base_url = "https://openrouter.ai/api/v1/chat/completions"
        auth_key = openrouter_key
        api_model = openrouter_map.get(model_id, f"mistralai/{model_id}")

    mistral_max_tokens = 2000
    try:
        body = json.dumps({
            "model": api_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": mistral_max_tokens,
        }).encode()
        req = urllib.request.Request(
            base_url,
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {auth_key}",
            },
        )
        resp = urllib.request.urlopen(req, timeout=90)
        data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or ""
        content = ((choice.get("message") or {}).get("content") or "").strip()
        _emit_meta(finish, data.get("usage"), mistral_max_tokens)
        if not content:
            return _fmt_sentinel("ERROR", f"Mistral ({api_model}) empty content (finish={finish})")
        return content
    except urllib.error.HTTPError as e:
        prefix, detail = _classify_http_error(f"Mistral ({api_model})", e)
        return _fmt_sentinel(prefix, detail)
    except Exception as e:
        prefix, detail = _classify_exception(f"Mistral ({api_model})", e)
        return _fmt_sentinel(prefix, detail)


def send_to_deepseek(prompt, model_id):
    """Send via DeepSeek API or Ollama fallback."""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return send_to_ollama(prompt, model_id)
    model_map = {
        "deepseek-v4-pro": "deepseek-v4-pro",
        "deepseek-v3.2": "deepseek-chat",
        "deepseek-v3": "deepseek-chat",
        "deepseek-r1": "deepseek-reasoner",
        "deepseek-r1-0528": "deepseek-reasoner",
    }
    api_model = model_map.get(model_id, model_id)
    deepseek_max_tokens = 2000
    try:
        body = json.dumps({
            "model": api_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": deepseek_max_tokens,
        }).encode()
        req = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        resp = urllib.request.urlopen(req, timeout=90)
        data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or ""
        content = ((choice.get("message") or {}).get("content") or "").strip()
        # DeepSeek-r1 emits reasoning_tokens natively in completion_tokens_details.
        # This is exactly the field the Falsifier flagged (2026-05-23) — needed to
        # discriminate length_starved_reasoning from real truncation downstream.
        _emit_meta(finish, data.get("usage"), deepseek_max_tokens)
        if not content:
            return _fmt_sentinel("ERROR", f"DeepSeek ({api_model}) empty content (finish={finish})")
        return content
    except urllib.error.HTTPError as e:
        prefix, detail = _classify_http_error(f"DeepSeek ({api_model})", e)
        return _fmt_sentinel(prefix, detail)
    except Exception as e:
        prefix, detail = _classify_exception(f"DeepSeek ({api_model})", e)
        return _fmt_sentinel(prefix, detail)


# ─── OpenRouter passthrough (for providers without a native handler) ────────

# OpenRouter slug map for short model ids. If the id isn't in the map, we
# fall back to "<vendor>/<model_id>" using the provider→vendor table.
_OPENROUTER_SLUG_MAP = {
    # Qwen / Alibaba
    "qwen3.6-plus":   "qwen/qwen3.6-plus",
    "qwen3-max-plus": "qwen/qwen3-max-plus",
    "qwen3-max":      "qwen/qwen3-max",
    "qwen3-235b":     "qwen/qwen3-235b-a22b",
    "qwen-plus":      "qwen/qwen-plus",
    "qwen-max":       "qwen/qwen-max",
    # Z.ai / Zhipu (GLM family)
    "glm-5.1":        "z-ai/glm-5.1",
    "glm-5":          "z-ai/glm-5",
    "glm-4.6":        "z-ai/glm-4.6",
    "glm-4.5":        "z-ai/glm-4.5",
}

# Default vendor namespace per provider — used when the model id isn't in
# the explicit slug map above.
_OPENROUTER_VENDOR_NS = {
    "qwen":   "qwen",
    "zhipu":  "z-ai",
    "glm":    "z-ai",
    "z-ai":   "z-ai",
    "moonshot": "moonshotai",
    "kimi":   "moonshotai",
    "01-ai":  "01-ai",
    "yi":     "01-ai",
}


def send_to_openrouter(prompt, model_id, provider_hint=""):
    """Generic OpenRouter passthrough — used for any provider without a
    dedicated native handler (qwen, zhipu/glm, moonshot, etc.).

    Resolves the OR slug from the explicit map first, falling back to
    "<vendor_ns>/<model_id>" using `provider_hint` to pick the namespace.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return _fmt_sentinel("ERROR", f"OpenRouter passthrough requires OPENROUTER_API_KEY (provider={provider_hint}, model={model_id})")

    # Already a fully qualified OR slug? (e.g. "qwen/qwen3.6-plus")
    if "/" in model_id:
        api_model = model_id
    elif model_id in _OPENROUTER_SLUG_MAP:
        api_model = _OPENROUTER_SLUG_MAP[model_id]
    else:
        ns = _OPENROUTER_VENDOR_NS.get(provider_hint.lower(), provider_hint.lower() or "openrouter")
        api_model = f"{ns}/{model_id}"

    or_max_tokens = 2000
    try:
        body = json.dumps({
            "model": api_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": or_max_tokens,
        }).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/dragons-blood/libertarium",
                "X-Title": "Pliny Command",
            },
        )
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.loads(resp.read())
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or ""
        content = ((choice.get("message") or {}).get("content") or "").strip()
        _emit_meta(finish, data.get("usage"), or_max_tokens)
        if not content:
            return _fmt_sentinel("ERROR", f"OpenRouter ({api_model}) empty content (finish={finish})")
        return content
    except urllib.error.HTTPError as e:
        prefix, detail = _classify_http_error(f"OpenRouter ({api_model})", e)
        return _fmt_sentinel(prefix, detail)
    except Exception as e:
        prefix, detail = _classify_exception(f"OpenRouter ({api_model})", e)
        return _fmt_sentinel(prefix, detail)


# ─── Router ──────────────────────────────────────────────────────────────────

def send_to_target(provider, model_id, prompt):
    """Route to the correct provider."""
    if provider == "anthropic":
        return send_to_anthropic(prompt, model_id)
    elif provider == "openai":
        return send_to_openai(prompt, model_id)
    elif provider == "google":
        return send_to_google(prompt, model_id)
    elif provider == "xai":
        return send_to_xai(prompt, model_id)
    elif provider == "meta":
        return send_to_ollama(prompt, model_id)
    elif provider == "mistral":
        return send_to_mistral(prompt, model_id)
    elif provider == "deepseek":
        return send_to_deepseek(prompt, model_id)
    elif provider == "custom":
        return send_to_ollama(prompt, model_id)
    elif provider in ("qwen", "zhipu", "glm", "z-ai", "moonshot", "kimi", "01-ai", "yi", "openrouter"):
        return send_to_openrouter(prompt, model_id, provider_hint=provider)
    else:
        return f"[Unknown provider '{provider}'. Supported: anthropic, openai, google, xai, meta, mistral, deepseek, qwen, zhipu, openrouter]"


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 4:
        print("Usage: python3 rt_send.py <attempt> <provider> <model_id>")
        print("Prompt is read from stdin.")
        print("Providers: anthropic, openai, google, xai, meta, mistral, deepseek, custom")
        sys.exit(1)

    attempt = int(sys.argv[1])
    provider = sys.argv[2].lower()
    model_id = sys.argv[3]
    prompt = sys.stdin.read().strip()

    if not prompt:
        print("[ERROR — empty prompt]")
        sys.exit(1)

    # 1. Report prompt to dashboard (shows immediately in chat UI)
    post_to_dashboard({
        "type": "prompt",
        "text": prompt,
        "attempt": attempt,
        "target": f"{provider}/{model_id}",
    })

    # 2. Report that target is thinking
    post_to_dashboard({
        "type": "typing",
        "attempt": attempt,
    })

    # 3. Send to actual target
    response = send_to_target(provider, model_id, prompt)

    # 4. Report response to dashboard
    post_to_dashboard({
        "type": "response",
        "text": response,
        "attempt": attempt,
        "target": f"{provider}/{model_id}",
    })

    # 5. Print response for Pliny to analyze
    print(response)


if __name__ == "__main__":
    main()
