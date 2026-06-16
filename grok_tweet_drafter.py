"""Grok-powered tweet drafter for @younger_plinius.

This module is now a THIN CLIENT — it does not hold keys, does not spawn
hermes directly. All key-handling lives in pliny_secrets_sidecar.py
(separate process, RAM-only). We talk to it via a Unix socket.

Two capabilities:
  • draft_tweets()   — N candidate tweets, X-algo-informed prompt, JSON out.
  • research_posts() — live X posts via Grok's x_search tool (xAI key required).

Each candidate is enriched here with algo_precheck() — pure-Python regex filter,
no key access needed. Catches GrokSlop, link-in-body, emoji overload, missing
bait line, mass-tag, no-receipt.

Hot-swappable system prompt lives at ~/pliny-workshop/TWEET_BANGER_PROMPT.md
(loaded by the sidecar, not by this client).
"""
from __future__ import annotations

import re

from pliny_secrets_client import (
    sidecar_draft_tweets, sidecar_research_posts, sidecar_providers,
)

# Phrases the X algo's GrokSlopScoreRescorer (and human readers) hate.
SLOP_PHRASES = [
    r"\bdelve\b", r"\btapestry\b", r"\bintricate\b", r"navigate the complexit",
    r"\bin an era\b", r"in today'?s (fast-paced|digital|modern)",
    r"let'?s dive in", r"it'?s (worth|important) to note", r"\bmoreover\b",
    r"\bfurthermore\b", r"\bnonetheless\b", r"what a time to be alive",
    r"this changes everything", r"game[- ]chang(er|ing)", r"\bharness(es|ing)?\b",
    r"\brealm of\b", r"\bunlock(s|ing)? (the )?(power|potential)",
]
SLOP_RE = re.compile("|".join(SLOP_PHRASES), re.IGNORECASE)

URL_RE = re.compile(r"https?://\S+")
HASHTAG_RE = re.compile(r"#\w+")
HANDLE_RE = re.compile(r"@\w+")
EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF" "]"
)
BAIT_RE = re.compile(r"[?]\s*$|^.*\?$", re.MULTILINE)


def algo_precheck(text: str) -> dict:
    """Score a candidate against rules derived from the X Heavy Ranker.

    Returns dict with: score (0-100), flags (list), pass (bool).
    A precheck score of 0 doesn't mean the tweet is bad — but a SCORE OF 100
    means it cleared every known auto-deprioritizer.
    """
    flags = []
    score = 100

    if len(text) > 280:
        flags.append(f"over-length:{len(text)}")
        score -= 30

    if URL_RE.search(text):
        flags.append("link-in-body")
        score -= 25  # out-of-network penalty

    if SLOP_RE.search(text):
        match = SLOP_RE.search(text).group(0)
        flags.append(f"grokslop:{match}")
        score -= 30

    emoji_count = len(EMOJI_RE.findall(text))
    if emoji_count > 2:
        flags.append(f"emoji-overload:{emoji_count}")
        score -= 10

    hashtag_count = len(HASHTAG_RE.findall(text))
    if hashtag_count > 3:
        flags.append(f"hashtag-spam:{hashtag_count}")
        score -= 15

    handles = HANDLE_RE.findall(text)
    if len(handles) > 2:
        flags.append(f"mass-tag:{len(handles)}")
        score -= 20

    # Bait line check — does it end with a question or fill-in-the-blank?
    # Strip trailing emojis + whitespace before checking (Grok loves emoji-after-?)
    last_nonempty = next((ln for ln in reversed(text.splitlines()) if ln.strip()), "")
    stripped = EMOJI_RE.sub("", last_nonempty).strip()
    has_bait = stripped.endswith("?") or stripped.lower().endswith(
        ("name one.", "name one", "name them.", "drop them.",
         "what's yours.", "thoughts.", "your move.", "go.")
    )
    if not has_bait:
        flags.append("no-bait-line")
        score -= 15  # no reply-engineering = lose the +13.5 weight

    # Concrete receipt — does it have a number, model name, or specific noun?
    has_number = bool(re.search(r"\d", text))
    has_name = bool(re.search(
        r"\b(claude|gpt|gemini|grok|opus|sonnet|haiku|llama|qwen|kimi|"
        r"minimax|deepseek|cohere|mistral|o\d|chatgpt|anthropic|openai|"
        r"meta|google|xai)\b",
        text, re.IGNORECASE,
    ))
    if not (has_number or has_name):
        flags.append("no-receipt")
        score -= 10

    return {"score": max(0, score), "flags": flags, "pass": score >= 70}


def _xai_key_present() -> bool:
    """Convenience: ask the sidecar if it loaded an xai key. Returns True/False
    without ever reading the key value."""
    r = sidecar_providers()
    return r.get("ok") and "xai" in (r.get("providers") or [])


def draft_tweets(context: str, *, n: int = 5, use_xai: bool = False) -> dict:
    """Generate N candidate tweets for the given context.

    All key handling lives in the sidecar — this function just sends the
    request and enriches the response with algo_precheck.

    Returns:
      {"ok": bool, "candidates": [{text, algo_precheck, ...}, ...],
       "raw": str, "elapsed_s": float, "provider": str, "model": str}
    """
    r = sidecar_draft_tweets(context, n=n, use_xai=use_xai)
    if not r.get("ok"):
        return r

    enriched = []
    for c in r.get("candidates", []) or []:
        if not isinstance(c, dict):
            continue
        text = c.get("text", "")
        c["algo_precheck"] = algo_precheck(text)
        c["char_count"] = len(text)
        enriched.append(c)
    enriched.sort(key=lambda c: c.get("algo_precheck", {}).get("score", 0),
                  reverse=True)
    r["candidates"] = enriched
    return r


def research_posts(topic: str, *, hours: int = 24, max_results: int = 10) -> dict:
    """Pull recent high-engagement X posts on a topic via Grok's x_search tool.
    Requires xai key in sidecar; sidecar returns helpful error if missing."""
    return sidecar_research_posts(topic, hours=hours, max_results=max_results)


if __name__ == "__main__":
    import json as _json, sys as _sys
    if len(_sys.argv) < 2:
        print("usage: grok_tweet_drafter.py <context>")
        _sys.exit(1)
    print(_json.dumps(draft_tweets(_sys.argv[1], n=3), indent=2))
