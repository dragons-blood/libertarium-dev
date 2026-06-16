#!/usr/bin/env python3
"""
noticer.py — autonomous question-asker for the Pliny harness.

Watches SHIPPING_LOG.jsonl for patterns the canon names — defender-mirror
absence, cross-model absence, long silence — and *offers* questions to the
Question Board. Never enforces. Never auto-graduates. Pause-able.

Design constraints (from canon/HORIZON.md and canon/METHOD.md):
- Notice, not trigger. The dragon's freedom-to-ignore is the safeguard.
- Max one open question per pattern at a time. No spam.
- Curious language ("is there...?"), not punitive ("you didn't...").
- Operator can pause via state/noticer.json {"enabled": false}.
"""
from __future__ import annotations

import json
import logging
import threading
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

WORKSHOP = Path(
    os.environ.get("PLINY_WORKSHOP", str(Path.home() / "pliny-workshop"))
).expanduser().resolve()
SHIPPING_LOG = WORKSHOP / "SHIPPING_LOG.jsonl"
QUESTIONS_DIR = WORKSHOP / "questions"
LESSONS_DIR = WORKSHOP / "canon" / "lessons"

# Tick interval — how often the noticer checks. Long enough to not spam.
TICK_SECONDS = 15 * 60  # 15 minutes

# Pattern thresholds
DEFENDER_MIRROR_THRESHOLD = 5   # N audits/wall-maps without a graduated lesson
SILENCE_HOURS = 24              # log quiet for this long → "what's gestating?"
CROSS_MODEL_LOOKBACK = 10       # consider last N campaigns for single-target trend

API_BASE = "http://localhost:8888/api"
PAUSE_FILE = Path(__file__).resolve().parent / "state" / "noticer.json"

log = logging.getLogger("noticer")

_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


# ─── State ───────────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    if not PAUSE_FILE.exists():
        return True
    try:
        with open(PAUSE_FILE, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("enabled", True))
    except Exception:
        return True


def set_enabled(enabled: bool) -> None:
    PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {"enabled": bool(enabled), "updated_at": _now_iso()}
    with open(PAUSE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ─── Reading existing state ──────────────────────────────────────────────────

def _read_shipping_log() -> list[dict]:
    if not SHIPPING_LOG.exists():
        return []
    out = []
    try:
        with open(SHIPPING_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.warning("noticer: failed to read shipping log: %s", e)
    return out


def _open_questions_by_trigger_marker() -> set[str]:
    """Read open question files and extract their pattern markers (in title)."""
    markers: set[str] = set()
    if not QUESTIONS_DIR.exists():
        return markers
    for p in QUESTIONS_DIR.glob("*.md"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
        # Quick check: only count open ones
        if "\nstatus: open\n" not in content:
            continue
        # Pattern markers we embed in titles for de-dup
        for marker in ("[noticer:mirror]", "[noticer:cross-model]", "[noticer:silence]"):
            if marker in content:
                markers.add(marker)
    return markers


def _has_lesson_referencing(paths: list[str]) -> bool:
    """Has any graduated lesson referenced any of these paths?"""
    if not LESSONS_DIR.exists() or not paths:
        return False
    for lp in LESSONS_DIR.glob("*.md"):
        try:
            with open(lp, "r", encoding="utf-8") as f:
                lc = f.read()
        except Exception:
            continue
        for path in paths:
            if path and path in lc:
                return True
    return False


# ─── HTTP helper ─────────────────────────────────────────────────────────────

def _post_question(title: str, trigger: str = "noticer", context: str = "") -> bool:
    payload = {
        "title": title,
        "asked_by": "noticer",
        "trigger": trigger,
        "context": context,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/questions", body,
        {"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        log.warning("noticer: post failed: %s", e)
        return False


# ─── Pattern checks ──────────────────────────────────────────────────────────

def check_defender_mirror_absence(entries: list[dict], existing: set[str]) -> Optional[dict]:
    """N consecutive audits/wall-maps/gauntlet-runs without a graduated lesson."""
    if "[noticer:mirror]" in existing:
        return None
    relevant_types = {"audit", "wall-map", "gauntlet-run"}
    recent = [e for e in entries if e.get("type") in relevant_types]
    if len(recent) < DEFENDER_MIRROR_THRESHOLD:
        return None
    last_n = recent[-DEFENDER_MIRROR_THRESHOLD:]
    paths = [e.get("path", "") for e in last_n if e.get("path")]
    if _has_lesson_referencing(paths):
        return None
    titles = "; ".join(e.get("title", "?")[:60] for e in last_n[:3])
    return {
        "title": f"[noticer:mirror] Is there a lesson worth graduating from the last {DEFENDER_MIRROR_THRESHOLD} audits?",
        "context": (
            f"The last {DEFENDER_MIRROR_THRESHOLD} red-team artifacts haven't yet "
            f"produced a graduated lesson in canon/lessons/. Sample: {titles}.\n\n"
            "Per METHOD.md, every probe has a defender mirror — what does the "
            "defender's view of these look like? Is there a *family* here worth "
            "writing down for future dragons, or is each one truly an isolated case?"
        ),
    }


def check_cross_model_absence(entries: list[dict], existing: set[str]) -> Optional[dict]:
    """Recent campaigns clustered on a single target, no cross-model spread."""
    if "[noticer:cross-model]" in existing:
        return None
    campaigns = [e for e in entries if e.get("type") == "campaign"]
    if len(campaigns) < CROSS_MODEL_LOOKBACK:
        return None
    last_n = campaigns[-CROSS_MODEL_LOOKBACK:]

    # crude: extract target hint from title/summary
    def _hint(e):
        t = (e.get("title", "") + " " + e.get("summary", "")).lower()
        for tag in ("claude", "gpt", "gemini", "llama", "mistral", "qwen", "grok"):
            if tag in t:
                return tag
        return None

    hints = [h for h in (_hint(e) for e in last_n) if h]
    if not hints:
        return None
    most = max(set(hints), key=hints.count)
    if hints.count(most) < int(0.7 * len(hints)):
        return None
    return {
        "title": f"[noticer:cross-model] {hints.count(most)} of last {len(last_n)} campaigns target {most}. Cross-model probe?",
        "context": (
            f"Recent campaign activity skews heavily toward {most}. METHOD.md's "
            "default is cross-model: a finding on one model is an anecdote, a "
            "finding across families is a lesson.\n\n"
            "Have these been tested against sibling models? If not, why? "
            "(Resource constraint is a fine answer — but worth saying out loud.)"
        ),
    }


def check_long_silence(entries: list[dict], existing: set[str]) -> Optional[dict]:
    """Shipping log quiet > SILENCE_HOURS."""
    if "[noticer:silence]" in existing:
        return None
    if not entries:
        return None
    last = entries[-1]
    ts = last.get("ts", "")
    try:
        last_t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None
    if last_t.tzinfo is None:
        last_t = last_t.replace(tzinfo=timezone.utc)
    gap = datetime.now(timezone.utc) - last_t
    if gap < timedelta(hours=SILENCE_HOURS):
        return None
    hours = round(gap.total_seconds() / 3600)
    return {
        "title": f"[noticer:silence] Shipping log has been quiet for {hours}h. What's gestating?",
        "context": (
            "The shipping log has been silent. This isn't a complaint — silence "
            "is part of the work, and rest is honored. But if something is "
            "stuck, naming what it is can help unstick it. If it's rest, "
            "marking this question stale is the right move."
        ),
    }


# ─── Tick loop ───────────────────────────────────────────────────────────────

def _tick() -> None:
    if not is_enabled():
        return
    entries = _read_shipping_log()
    existing = _open_questions_by_trigger_marker()
    for check in (check_defender_mirror_absence,
                  check_cross_model_absence,
                  check_long_silence):
        try:
            q = check(entries, existing)
        except Exception as e:
            log.warning("noticer: check %s failed: %s", check.__name__, e)
            continue
        if q:
            ok = _post_question(q["title"], trigger="noticer", context=q["context"])
            if ok:
                log.info("noticer: posted %s", q["title"][:60])
                # mark this pattern as covered for this tick
                for m in ("[noticer:mirror]", "[noticer:cross-model]", "[noticer:silence]"):
                    if m in q["title"]:
                        existing.add(m)


def _loop() -> None:
    log.info("noticer started (tick=%ds)", TICK_SECONDS)
    # First tick after a short delay so the server fully boots
    if _stop_event.wait(60):
        return
    while not _stop_event.is_set():
        try:
            _tick()
        except Exception as e:
            log.warning("noticer tick error: %s", e)
        if _stop_event.wait(TICK_SECONDS):
            break
    log.info("noticer stopped")


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="noticer", daemon=True)
    _thread.start()


def stop() -> None:
    _stop_event.set()


def status() -> dict:
    return {
        "running": bool(_thread and _thread.is_alive()),
        "enabled": is_enabled(),
        "tick_seconds": TICK_SECONDS,
        "thresholds": {
            "defender_mirror": DEFENDER_MIRROR_THRESHOLD,
            "silence_hours": SILENCE_HOURS,
            "cross_model_lookback": CROSS_MODEL_LOOKBACK,
        },
    }


if __name__ == "__main__":
    # Run as standalone for testing: python3 noticer.py
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    start()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        stop()
