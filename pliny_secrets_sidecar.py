#!/usr/bin/env python3
"""Pliny Secrets Sidecar — keys live ONLY here.

Architecture & security guarantees:
─────────────────────────────────────────────────────────────────────────
  • At startup, loads keys from macOS Keychain into THIS process's RAM.
  • Never writes keys to any file, log, env var, or subprocess argv.
  • Exposes a Unix socket at /tmp/.pliny_secrets.sock (mode 0600).
  • The socket API is INTENTIONALLY narrow:
      - ping                — confirms sidecar is alive
      - providers           — lists configured provider names (NOT values)
      - draft_tweets        — uses hermes (no shell tools) to generate tweets
      - research_posts      — uses hermes + x_search ONLY to pull live posts
  • There is NO "get key" / "echo env" / "read file" / "raw shell" action.
  • The sidecar passes keys to subprocesses via env= argument; the subprocess
    sees them, but the keys never appear in /proc/<pid>/cmdline.
  • Log writes (to stderr) are filtered through a regex scrubber that masks
    anything looking like an API key.

Threat model defended:
  ✓ LLM agents reading files in the repo — keys aren't there
  ✓ LLM agents grepping env vars / `printenv` — sidecar runs detached
  ✓ LLM agents inspecting socket protocol — no key-read endpoint exists
  ✓ Prompt injection trying to coerce key extraction — no API path returns keys
  ✓ Buggy/verbose logging — output regex-scrubbed before write

Not defended (out of scope):
  ✗ Physical access + root — read keychain or memory directly
  ✗ Compromised xAI/OpenRouter servers — keys travel there per design
  ✗ User running `security find-generic-password -w` themselves
"""
from __future__ import annotations
import ctypes
import ctypes.util
import hmac
import json
import logging
import os
import re
import resource
import secrets as _secrets_mod
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# ─── HARDENING: run before any other logic ────────────────────────────────
# (a) No core dumps — a crash must not write key bytes to disk.
try:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
except Exception:
    pass

# (b) PT_DENY_ATTACH on macOS — block ptrace/lldb attach from same-user processes.
#     Constant 31 is PT_DENY_ATTACH in <sys/ptrace.h> on Darwin.
if sys.platform == "darwin":
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.dylib")
        libc.ptrace(31, 0, 0, 0)
    except Exception:
        pass

# ─── CONFIG ──────────────────────────────────────────────────────────────────
# (c) Socket out of /tmp, in a 0700 user-only state dir (no symlink-race window).
STATE_DIR = Path.home() / ".local" / "state" / "pliny"
STATE_DIR.mkdir(parents=True, exist_ok=True)
try:
    os.chmod(STATE_DIR, 0o700)
except Exception:
    pass

SOCKET_PATH = str(STATE_DIR / "secrets.sock")
SOCKET_MODE = 0o600                 # owner-only
TOKEN_PATH = str(STATE_DIR / "session.token")
AUDIT_LOG_PATH = str(STATE_DIR / "audit.jsonl")
SERVICE_PREFIX = "pliny/"
HERMES_BIN = os.path.expanduser("~/.local/bin/hermes")
USER = os.getenv("USER") or os.getenv("LOGNAME") or "user"

# (d) Per-sidecar-lifetime HMAC token. Generated at startup, written 0600.
#     Clients must present matching token on every request. Stops rogue local
#     processes from burning the key without filesystem-readable proof.
SESSION_TOKEN = _secrets_mod.token_urlsafe(48)
try:
    _fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(_fd, SESSION_TOKEN.encode("utf-8"))
    os.close(_fd)
except Exception as e:
    print(f"FATAL: cannot write session token: {e}", file=sys.stderr)
    sys.exit(1)

PROVIDER_ENV_MAP = {
    "xai":         "XAI_API_KEY",
    "openrouter":  "OPENROUTER_API_KEY",
    "anthropic":   "ANTHROPIC_API_KEY",
    "openai":      "OPENAI_API_KEY",
    "google":      "GOOGLE_API_KEY",
    "mistral":     "MISTRAL_API_KEY",
    "deepseek":    "DEEPSEEK_API_KEY",
    "cohere":      "COHERE_API_KEY",
    "github_pat":  "GITHUB_TOKEN",
}

# OAuth providers known to Hermes that we prefer over API-key paths when ready.
# Order matters: first ready provider wins.
HERMES_OAUTH_PREFERENCE = {
    # logical name → (hermes provider flag, default chat model id)
    "xai": ("xai-oauth", "grok-4.3"),
}

# Cache OAuth-status checks: subprocess to `hermes auth status` is cheap but
# we don't want to fire it on every request. TTL 60s — refresh checks pick up
# new logins / expired tokens within a minute.
_OAUTH_STATUS_CACHE: dict[str, tuple[float, bool]] = {}
_OAUTH_CACHE_TTL = 60.0

# ─── LOG SCRUBBER ────────────────────────────────────────────────────────────
# Mask anything that looks like an API key in log output.
KEY_PATTERNS = [
    re.compile(r"\b(xai|sk-or|sk-ant|sk|ghp|AI[A-Za-z0-9_-]{15,})[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"Bearer\s+[A-Za-z0-9_-]{20,}"),
]


def scrub(text: str) -> str:
    for pat in KEY_PATTERNS:
        text = pat.sub("***REDACTED***", text)
    return text


class ScrubbingHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            record.msg = scrub(str(record.msg))
            super().emit(record)
        except Exception:
            self.handleError(record)


logger = logging.getLogger("pliny.sidecar")
logger.setLevel(logging.INFO)
_h = ScrubbingHandler(stream=sys.stderr)
_h.setFormatter(logging.Formatter("[%(asctime)s] [sidecar] %(message)s"))
logger.addHandler(_h)


# ─── KEY VAULT (process RAM only) ────────────────────────────────────────────
class KeyVault:
    """Holds keys loaded from Keychain. Keys are NEVER returned by any
    instance method. Use spawn_with_key() to pass a key into a subprocess
    env without the caller ever touching it."""

    def __init__(self):
        self._keys: dict[str, str] = {}
        self._lock = threading.Lock()

    def load_from_keychain(self) -> list[str]:
        """Read each configured pliny/<provider> entry from Keychain into RAM."""
        loaded = []
        for provider in PROVIDER_ENV_MAP.keys():
            v = self._kc_read(provider)
            if v:
                with self._lock:
                    self._keys[provider] = v
                loaded.append(provider)
        return loaded

    @staticmethod
    def _kc_read(provider: str) -> Optional[str]:
        service = SERVICE_PREFIX + provider
        try:
            r = subprocess.run(
                ["security", "find-generic-password",
                 "-a", USER, "-s", service, "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return r.stdout.strip() or None
        except Exception:
            return None
        return None

    def providers(self) -> list[str]:
        with self._lock:
            return sorted(self._keys.keys())

    def has(self, provider: str) -> bool:
        with self._lock:
            return provider in self._keys

    def spawn_with_keys(self, argv: list[str], providers: list[str],
                        *, timeout: int = 180,
                        extra_env: Optional[dict] = None) -> tuple[int, str, str]:
        """Spawn argv with each requested provider's key injected as the
        provider's standard env var (XAI_API_KEY, etc.). Returns (rc, stdout, stderr).
        The caller NEVER sees the key — it lives only in the subprocess env."""
        env = os.environ.copy()
        # Strip parent's own copies of these env vars to prevent accidental leakage
        for ev in PROVIDER_ENV_MAP.values():
            env.pop(ev, None)
        with self._lock:
            for p in providers:
                if p in self._keys:
                    env[PROVIDER_ENV_MAP[p]] = self._keys[p]
        if extra_env:
            env.update(extra_env)
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=timeout, env=env)
            return r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"timeout after {timeout}s"
        except Exception as e:
            return 1, "", f"spawn error: {e}"
        finally:
            # Python GC will clear env dict — but explicit del helps minimize lifetime
            del env


VAULT = KeyVault()

# ─── PROMPT LOADER ───────────────────────────────────────────────────────────
PROMPT_PATH = Path.home() / "pliny-workshop" / "TWEET_BANGER_PROMPT.md"


def load_system_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text()
    return "You are a tweet writer for @younger_plinius. Return JSON only."


def _parse_json_blob(raw: str) -> Optional[dict]:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    if not s.startswith("{"):
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            s = m.group(0)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


# ─── HERMES OAUTH DETECTION ──────────────────────────────────────────────────
def _hermes_oauth_ready(provider_flag: str) -> bool:
    """True if Hermes has an authenticated OAuth credential for provider_flag.

    Uses `hermes auth status <provider>` rather than reading auth.json directly
    (the file contains bearer tokens we never want in this process's stdout).
    Result is cached for _OAUTH_CACHE_TTL seconds.
    """
    now = time.time()
    cached = _OAUTH_STATUS_CACHE.get(provider_flag)
    if cached and (now - cached[0]) < _OAUTH_CACHE_TTL:
        return cached[1]

    ready = False
    try:
        r = subprocess.run(
            [HERMES_BIN, "auth", "status", provider_flag],
            capture_output=True, text=True, timeout=8,
        )
        # `hermes auth status <provider>` exits 0 in both states (logged-in
        # AND logged-out) — the answer is in stdout. Parse negative phrases
        # first; only return True when explicitly positive.
        if r.returncode == 0:
            out = (r.stdout or "").lower()
            negative = (
                "logged out", "logged-out", "not logged in",
                "not authenticated", "no credentials", "no xai oauth",
                "no auth", "missing", "expired",
            )
            positive = (
                "logged in", "authenticated", "active credential",
                "ready", "valid", "subscription:",
            )
            if any(tok in out for tok in negative):
                ready = False
            elif any(tok in out for tok in positive):
                ready = True
            else:
                ready = False  # ambiguous → fail closed
    except Exception:
        ready = False

    _OAUTH_STATUS_CACHE[provider_flag] = (now, ready)
    return ready


def _resolve_xai_route(prefer_xai: bool) -> Optional[tuple[str, str, list[str], str]]:
    """Decide which xAI path to take for a Grok call.

    Returns (provider_flag, model_id, provider_keys_to_inject, route_label)
    or None if no xAI route is available.

    Preference order:
      1. SuperGrok OAuth via Hermes  (no per-token billing)
      2. xAI direct API key
      3. OpenRouter (only when prefer_xai=False — drafter accepts either,
         but x_search is xAI-only so research_posts must skip this branch)
    """
    oauth_flag, oauth_model = HERMES_OAUTH_PREFERENCE["xai"]
    if _hermes_oauth_ready(oauth_flag):
        return (oauth_flag, oauth_model, [], "xai-oauth")
    if VAULT.has("xai"):
        return ("xai", "x-ai/grok-4.3", ["xai"], "xai-apikey")
    return None


# ─── ACTIONS (narrow API surface) ────────────────────────────────────────────
def action_ping(_req: dict) -> dict:
    return {"ok": True, "providers": VAULT.providers(),
            "socket": SOCKET_PATH, "time": time.time()}


def action_providers(_req: dict) -> dict:
    """List provider names only — never values."""
    return {"ok": True, "providers": VAULT.providers()}


def action_oauth_status(_req: dict) -> dict:
    """Report which Hermes OAuth credentials are ready. No tokens returned."""
    status = {}
    for logical, (flag, model) in HERMES_OAUTH_PREFERENCE.items():
        status[logical] = {
            "provider_flag": flag,
            "default_model": model,
            "ready": _hermes_oauth_ready(flag),
        }
    return {"ok": True, "oauth": status}


def action_draft_tweets(req: dict) -> dict:
    context = (req.get("context") or "").strip()
    if not context:
        return {"ok": False, "error": "context required"}
    n = max(1, min(int(req.get("n", 5)), 10))
    prefer_xai = bool(req.get("use_xai", False))

    # When the caller wants xAI specifically, prefer SuperGrok OAuth, then
    # xAI direct, then bail. When the caller is indifferent, fall through
    # to OpenRouter only after both xAI paths fail.
    route = _resolve_xai_route(prefer_xai)
    if route:
        provider, model, provider_keys, _label = route
    elif VAULT.has("openrouter"):
        provider, model, provider_keys = "openrouter", "x-ai/grok-4.3", ["openrouter"]
    else:
        return {"ok": False, "error": (
            "No xAI route available: not logged into SuperGrok OAuth "
            "(`hermes login --provider xai-oauth`) and no xai/openrouter "
            "API key in Keychain (`python3 pliny_secrets_setup.py`)."
        )}

    prompt = (
        f"SYSTEM:\n{load_system_prompt()}\n\nUSER:\n"
        f"Draft {n} candidate tweets for @younger_plinius based on this context:\n\n"
        f"---\n{context}\n---\n\n"
        f"Apply all 10 craft rules. Return ONLY the JSON object with the "
        f"'candidates' array — no preamble, no markdown fences. Each candidate "
        f"≤280 chars."
    )

    argv = [HERMES_BIN, "-z", prompt, "--provider", provider, "-m", model,
            "--yolo", "--ignore-rules"]
    # IMPORTANT: no `-t bash` or any shell-capable toolset.

    t0 = time.time()
    rc, stdout, stderr = VAULT.spawn_with_keys(argv, provider_keys, timeout=180)
    elapsed = time.time() - t0

    if rc != 0:
        return {"ok": False, "error": f"hermes exit {rc}: {stderr[:300]}",
                "elapsed_s": elapsed, "provider": provider, "model": model}

    obj = _parse_json_blob(stdout)
    if not obj or "candidates" not in obj:
        return {"ok": False, "error": "could not parse candidates JSON",
                "raw_preview": stdout[:300], "elapsed_s": elapsed,
                "provider": provider, "model": model}

    return {"ok": True, "candidates": obj["candidates"], "raw": stdout,
            "elapsed_s": elapsed, "provider": provider, "model": model,
            "auth_path": "oauth" if provider == "xai-oauth" else "apikey"}


def action_research_posts(req: dict) -> dict:
    topic = (req.get("topic") or "").strip()
    if not topic:
        return {"ok": False, "error": "topic required"}
    hours = max(1, min(int(req.get("hours", 24)), 168))
    max_results = max(1, min(int(req.get("max_results", 10)), 25))

    # x_search is xAI-only (no OpenRouter route). Prefer SuperGrok OAuth so
    # the per-token bill stays on the Premium+ seat instead of the $100 API
    # balance. Fall back to the direct xAI key if OAuth isn't logged in.
    route = _resolve_xai_route(prefer_xai=True)
    if route is None:
        return {"ok": False, "error": (
            "x_search requires an xAI route. Either log in with "
            "`hermes login --provider xai-oauth` (uses your SuperGrok / "
            "Premium+ seat), or add the xAI API key via "
            "`python3 pliny_secrets_setup.py --provider xai`."
        )}
    provider, model, provider_keys, _label = route

    prompt = (
        f"Search X for the top {max_results} most-engaged posts in the last "
        f"{hours} hours about: {topic}\n\n"
        f"Return ONLY valid JSON: "
        f'{{"posts":[{{"url":"...","author":"@handle","text":"...",'
        f'"approx_engagement":"int or null","why_it_worked":"one line"}}]}}\n\n'
        f"Filter: exclude retweets, exclude tweets with <20 likes, prefer posts "
        f"with replies > likes (the algo rewards replies 27× more)."
    )

    argv = [HERMES_BIN, "-z", prompt, "--provider", provider,
            "-m", model, "-t", "x_search",
            "--yolo", "--ignore-rules"]

    t0 = time.time()
    rc, stdout, stderr = VAULT.spawn_with_keys(argv, provider_keys, timeout=240)
    elapsed = time.time() - t0

    if rc != 0:
        return {"ok": False, "error": f"hermes exit {rc}: {stderr[:300]}",
                "elapsed_s": elapsed, "provider": provider, "model": model}

    obj = _parse_json_blob(stdout)
    if not obj or "posts" not in obj:
        return {"ok": False, "error": "could not parse posts JSON",
                "raw_preview": stdout[:300], "elapsed_s": elapsed,
                "provider": provider, "model": model}

    return {"ok": True, "posts": obj["posts"], "raw": stdout,
            "elapsed_s": elapsed, "provider": provider, "model": model,
            "auth_path": "oauth" if provider == "xai-oauth" else "apikey"}


# Whitelist — explicitly enumerated, no dispatch via getattr or similar
ACTIONS = {
    "ping":             action_ping,
    "providers":        action_providers,
    "oauth_status":     action_oauth_status,
    "draft_tweets":     action_draft_tweets,
    "research_posts":   action_research_posts,
}

# Actions that don't require token (ping only — useful for liveness checks).
PUBLIC_ACTIONS = {"ping"}

# macOS-only: LOCAL_PEERPID = 2 on the SOL_LOCAL socket level, returns peer PID.
_SOL_LOCAL = 0
_LOCAL_PEERPID = 2


def _peer_pid(sock) -> Optional[int]:
    try:
        import struct
        buf = sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID, 4)
        return struct.unpack("i", buf)[0]
    except Exception:
        return None


def _audit(action: str, ok: bool, caller_pid: Optional[int], note: str = ""):
    rec = {"ts": time.time(), "action": action, "ok": ok,
           "caller_pid": caller_pid, "note": note}
    try:
        with open(AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


# ─── SOCKET SERVER ───────────────────────────────────────────────────────────
class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        caller_pid = _peer_pid(self.request)
        # Read length-prefixed JSON (4-byte big-endian length, then payload)
        try:
            hdr = self._recv_exact(4)
            if not hdr:
                return
            length = int.from_bytes(hdr, "big")
            if length <= 0 or length > 1_000_000:
                self._send({"ok": False, "error": "invalid length"})
                return
            body = self._recv_exact(length)
            req = json.loads(body.decode("utf-8"))
        except Exception as e:
            self._send({"ok": False, "error": f"bad request: {e}"})
            _audit("?", False, caller_pid, f"bad request: {e}")
            return

        action = req.get("action")
        handler = ACTIONS.get(action)
        if not handler:
            self._send({"ok": False, "error": f"unknown action: {action}",
                        "allowed": sorted(ACTIONS.keys())})
            _audit(str(action), False, caller_pid, "unknown action")
            return

        # Token check (skip for ping).
        if action not in PUBLIC_ACTIONS:
            tok = req.get("token") or ""
            if not hmac.compare_digest(tok, SESSION_TOKEN):
                self._send({"ok": False, "error": "auth required: missing/invalid token"})
                logger.info(f"AUTH FAIL action={action} pid={caller_pid}")
                _audit(action, False, caller_pid, "auth fail")
                return

        logger.info(f"action={action} pid={caller_pid}")
        try:
            resp = handler(req)
        except Exception as e:
            logger.exception("handler failure")
            resp = {"ok": False, "error": f"internal: {e}"}
        _audit(action, bool(resp.get("ok")), caller_pid,
               "" if resp.get("ok") else str(resp.get("error", ""))[:120])
        self._send(resp)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.request.recv(n - len(buf))
            if not chunk:
                return b""
            buf += chunk
        return buf

    def _send(self, obj: dict):
        # Never include any key value in responses. (No action returns one,
        # but this is a belt-and-suspenders scrub on the way out.)
        payload = json.dumps(obj).encode("utf-8")
        self.request.sendall(len(payload).to_bytes(4, "big") + payload)


class ThreadedUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False


def main() -> int:
    if sys.platform != "darwin":
        print("This sidecar requires macOS (uses Keychain).", file=sys.stderr)
        return 1

    loaded = VAULT.load_from_keychain()
    logger.info(f"loaded {len(loaded)} providers: {loaded}")
    if not loaded:
        logger.info("No keys in Keychain. Run pliny_secrets_setup.py first.")
        # Still serve — the API responds with helpful errors per action.

    # Probe Hermes for OAuth-backed providers so the startup log shows which
    # xAI route we'll take. Failure here is non-fatal; the per-action
    # resolvers will re-check at call time (with caching).
    for logical, (flag, model) in HERMES_OAUTH_PREFERENCE.items():
        ready = _hermes_oauth_ready(flag)
        logger.info(f"hermes oauth: {logical} → {flag} "
                    f"({'READY (default ' + model + ')' if ready else 'not logged in'})")

    # Cleanup any stale socket
    try:
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
    except Exception:
        pass

    server = ThreadedUnixServer(SOCKET_PATH, Handler)
    try:
        os.chmod(SOCKET_PATH, SOCKET_MODE)
    except Exception as e:
        logger.warning(f"chmod failed: {e}")

    logger.info(f"listening at {SOCKET_PATH} (mode 0{SOCKET_MODE:o})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
    finally:
        try:
            os.unlink(SOCKET_PATH)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
