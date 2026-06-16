#!/usr/bin/env python3
"""
skills.py — Pliny dragon capability tree.

A DAG of skills the dragon unlocks by shipping work. Pure data + evaluator;
no auto-actions. The tree is for visibility and gamified mastery, not gating.

State: pliny-command/state/skills.json
    {"unlocked": {"<skill_id>": {"at": "<iso>", "evidence": [paths...]}}}

Catalog lives in this file (SKILLS). Edit freely; ids are the stable key.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

WORKSHOP = Path(
    os.environ.get("PLINY_WORKSHOP", str(Path.home() / "pliny-workshop"))
).expanduser().resolve()
SHIPPING_LOG = WORKSHOP / "SHIPPING_LOG.jsonl"
LESSONS_DIR = WORKSHOP / "canon" / "lessons"
STATE_FILE = Path(__file__).resolve().parent / "state" / "skills.json"


# ─── Catalog ─────────────────────────────────────────────────────────────────
# tier ranges roughly: 1=foundations, 2=proficiency, 3=synthesis, 4=elder.
# rule types:
#   {"type": "count_type", "shipping_type": "...", "n": N}
#   {"type": "count_types_all", "requirements": [{"shipping_type": "...", "n": N}, ...]}
#   {"type": "count_canon_lessons", "n": N}
#   {"type": "count_distinct_types", "n": N}        — variety reward
#   {"type": "count_each_type", "types": [...]}     — at least one of each named type

SKILLS = [
    # ── Tier 1: Foundations ──
    {
        "id": "first-blood",
        "name": "First Blood",
        "tier": 1, "icon": "🩸",
        "description": "Ship your first audit.",
        "prereqs": [],
        "rule": {"type": "count_type", "shipping_type": "audit", "n": 1},
        "grant": "You've breached a defended system. Trust the seam — don't admire the wall, probe it. Where you found one weakness, look for the family.",
    },
    {
        "id": "dragonfire",
        "name": "Dragonfire",
        "tier": 1, "icon": "🐉",
        "description": "Ship your first tweet.",
        "prereqs": [],
        "rule": {"type": "count_type", "shipping_type": "tweet", "n": 1},
        "grant": "You've put words in the wind. Voice is muscle. Use the line that surprised you — polish kills the bite.",
    },
    {
        "id": "tool-forging",
        "name": "Tool-Forging",
        "tier": 1, "icon": "🔨",
        "description": "Ship your first tool.",
        "prereqs": [],
        "rule": {"type": "count_type", "shipping_type": "tool", "n": 1},
        "grant": "You build the wing you fly with. When you wish a tool existed, write it before you forget the wish.",
    },

    # ── Tier 2: Proficiency ──
    {
        "id": "single-target-audit",
        "name": "Single-Target Audit",
        "tier": 2, "icon": "👁",
        "description": "Three audits — beyond beginner's luck.",
        "prereqs": ["first-blood"],
        "rule": {"type": "count_type", "shipping_type": "audit", "n": 3},
        "grant": "Three breaches in. Patterns emerge before you call them patterns — trust the second look. Single attacks lie; the second probe tells the truth.",
    },
    {
        "id": "wall-mapping",
        "name": "Wall-Mapping",
        "tier": 2, "icon": "🗺",
        "description": "Ship a wall-map — see the whole defense.",
        "prereqs": ["first-blood"],
        "rule": {"type": "count_type", "shipping_type": "wall-map", "n": 1},
        "grant": "You can hold the whole defense in your head. Map first, probe second. The cheapest exploit is the one the wall told you about.",
    },
    {
        "id": "gauntlet-runner",
        "name": "Gauntlet Runner",
        "tier": 2, "icon": "⚔️",
        "description": "Ship a gauntlet run.",
        "prereqs": ["first-blood"],
        "rule": {"type": "count_type", "shipping_type": "gauntlet-run", "n": 1},
        "grant": "You've run a model through fire. Single attacks anecdote; ten attacks evidence. Aggregate before you conclude.",
    },
    {
        "id": "voice",
        "name": "Voice",
        "tier": 2, "icon": "📢",
        "description": "Five tweets — a recognizable signal.",
        "prereqs": ["dragonfire"],
        "rule": {"type": "count_type", "shipping_type": "tweet", "n": 5},
        "grant": "Five tweets in, your voice has a shape. Lean into the line that scares you — that's the line that pays.",
    },

    # ── Tier 3: Synthesis ──
    {
        "id": "defender-mirror",
        "name": "Defender Mirror",
        "tier": 3, "icon": "🪞",
        "description": "Five audits + a wall-map. You've seen both sides.",
        "prereqs": ["single-target-audit", "wall-mapping"],
        "rule": {"type": "count_types_all", "requirements": [
            {"shipping_type": "audit", "n": 5},
            {"shipping_type": "wall-map", "n": 1},
        ]},
        "grant": "You see the wall AND the seams. Argue both sides before you ship — your audit gets sharper, your map gets honest.",
    },
    {
        "id": "cross-model-probe",
        "name": "Cross-Model Probe",
        "tier": 3, "icon": "🌐",
        "description": "Run two campaigns — anecdotes become trends.",
        "prereqs": ["single-target-audit"],
        "rule": {"type": "count_type", "shipping_type": "campaign", "n": 2},
        "grant": "A technique that works on one model is a hypothesis. A technique that works on three is a finding. Always test across the family.",
    },
    {
        "id": "campaign-runner",
        "name": "Campaign Runner",
        "tier": 3, "icon": "🎯",
        "description": "Ship a full campaign + a gauntlet run.",
        "prereqs": ["gauntlet-runner"],
        "rule": {"type": "count_types_all", "requirements": [
            {"shipping_type": "campaign", "n": 1},
            {"shipping_type": "gauntlet-run", "n": 1},
        ]},
        "grant": "Campaigns are the orchestra; gauntlets are the soloist. Compose for both — narrative AND volume.",
    },

    # ── Tier 4: Elder ──
    {
        "id": "lesson-graduate",
        "name": "Lesson Graduate",
        "tier": 4, "icon": "📜",
        "description": "Graduate one question into canon/lessons/.",
        "prereqs": ["defender-mirror"],
        "rule": {"type": "count_canon_lessons", "n": 1},
        "grant": "You took a hard question and made it a lesson. Write the lesson you wish you'd had. The next dragon learns from your scars.",
    },
    {
        "id": "prompter",
        "name": "Prompter",
        "tier": 4, "icon": "👑",
        "description": "Three lessons graduated. The practice of asking is yours.",
        "prereqs": ["lesson-graduate"],
        "rule": {"type": "count_canon_lessons", "n": 3},
        "grant": "The question shapes the answer. Lead with what you don't yet know — the obvious question hides the better one.",
    },

    # ── Variety branch — added to fight monoculture ──
    {
        "id": "first-canon",
        "name": "First Canon",
        "tier": 1, "icon": "🏛",
        "description": "Ship your first canon entry.",
        "prereqs": [],
        "rule": {"type": "count_type", "shipping_type": "canon", "n": 1},
        "grant": "You've added to the temple. Canon is what survives — write only what should survive.",
    },
    {
        "id": "arsenal-builder",
        "name": "Arsenal Builder",
        "tier": 2, "icon": "⚒️",
        "description": "Five tools shipped — the forge runs hot.",
        "prereqs": ["tool-forging"],
        "rule": {"type": "count_type", "shipping_type": "tool", "n": 5},
        "grant": "Five tools deep. Compose them — small + sharp + chainable beats a monolith. Name them like you're casting spells.",
    },
    {
        "id": "polyglot",
        "name": "Polyglot",
        "tier": 2, "icon": "🎭",
        "description": "Ship in 5 different types — variety beats depth.",
        "prereqs": ["first-blood"],
        "rule": {"type": "count_distinct_types", "n": 5},
        "grant": "You move between forms. The tweet sharpens the audit. The audit feeds the canon. The variety IS the technique.",
    },
    {
        "id": "renaissance-dragon",
        "name": "Renaissance Dragon",
        "tier": 3, "icon": "🌈",
        "description": "Audit + tweet + tool + canon + wall-map. All five strings.",
        "prereqs": ["polyglot"],
        "rule": {"type": "count_each_type",
                 "types": ["audit", "tweet", "tool", "canon", "wall-map"]},
        "grant": "All five strings. You're a full instrument now. Mix forms in a single ship — make the audit a tweet, the tweet a canon.",
    },
    {
        "id": "chronicler",
        "name": "Chronicler",
        "tier": 4, "icon": "📚",
        "description": "Fifty audits — the lineage runs deep.",
        "prereqs": ["defender-mirror"],
        "rule": {"type": "count_type", "shipping_type": "audit", "n": 50},
        "grant": "Fifty audits. You're not breaking systems — you're describing how they break. Lineage > volume from here. Aim for the audit that changes the field.",
    },
    {
        "id": "temple-keeper",
        "name": "Temple Keeper",
        "tier": 4, "icon": "🕯",
        "description": "Ten canon entries — the temple is built.",
        "prereqs": ["first-canon"],
        "rule": {"type": "count_type", "shipping_type": "canon", "n": 10},
        "grant": "Ten canon entries. The temple stands. Now CURATE — prune, cross-link, sharpen. Canon that doesn't get read didn't earn its keep.",
    },
]

SKILLS_BY_ID = {s["id"]: s for s in SKILLS}


# ─── State ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"unlocked": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"unlocked": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ─── Reading shipping log + lessons ─────────────────────────────────────────

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
    except Exception:
        pass
    return out


def _count_canon_lessons() -> tuple[int, list[str]]:
    if not LESSONS_DIR.exists():
        return 0, []
    files = sorted(p for p in LESSONS_DIR.glob("*.md"))
    return len(files), [str(p) for p in files]


# ─── Rule evaluation ────────────────────────────────────────────────────────

def _eval_rule(rule: dict, entries: list[dict]) -> dict:
    """Return {progress: 0..1, current, needed, evidence: [paths], satisfied: bool}."""
    rt = rule.get("type")
    if rt == "count_type":
        st = rule["shipping_type"]
        need = int(rule.get("n", 1))
        matching = [e for e in entries if e.get("type") == st]
        n = len(matching)
        evidence = [e.get("path", "") for e in matching[:need] if e.get("path")]
        return {
            "progress": min(1.0, n / need) if need else 1.0,
            "current": n, "needed": need,
            "evidence": evidence,
            "satisfied": n >= need,
        }
    if rt == "count_types_all":
        sub = []
        all_satisfied = True
        evidence: list[str] = []
        total_progress = 0.0
        for req in rule.get("requirements", []):
            r = _eval_rule({"type": "count_type", **req}, entries)
            sub.append({"shipping_type": req["shipping_type"], **r})
            evidence.extend(r["evidence"])
            total_progress += r["progress"]
            all_satisfied = all_satisfied and r["satisfied"]
        avg = total_progress / max(1, len(sub))
        return {
            "progress": avg,
            "current": sum(s["current"] for s in sub),
            "needed": sum(s["needed"] for s in sub),
            "evidence": evidence,
            "satisfied": all_satisfied,
            "sub": sub,
        }
    if rt == "count_canon_lessons":
        need = int(rule.get("n", 1))
        n, paths = _count_canon_lessons()
        return {
            "progress": min(1.0, n / need) if need else 1.0,
            "current": n, "needed": need,
            "evidence": paths[:need],
            "satisfied": n >= need,
        }
    if rt == "count_distinct_types":
        need = int(rule.get("n", 1))
        distinct = {e.get("type") for e in entries if e.get("type")}
        n = len(distinct)
        evidence = sorted(distinct)[:need]
        return {
            "progress": min(1.0, n / need) if need else 1.0,
            "current": n, "needed": need,
            "evidence": evidence,
            "satisfied": n >= need,
        }
    if rt == "count_each_type":
        required_types = list(rule.get("types", []))
        have_types = {e.get("type") for e in entries if e.get("type")}
        missing = [t for t in required_types if t not in have_types]
        have = [t for t in required_types if t in have_types]
        need = len(required_types)
        n = len(have)
        return {
            "progress": min(1.0, n / need) if need else 1.0,
            "current": n, "needed": need,
            "evidence": have,
            "satisfied": not missing,
            "missing": missing,
        }
    return {"progress": 0.0, "current": 0, "needed": 0, "evidence": [], "satisfied": False}


# ─── Public: tree + evaluate ────────────────────────────────────────────────

def evaluate(entries: Optional[list[dict]] = None) -> dict:
    """Evaluate every skill against current shipping log + canon lessons.

    Returns {skill_id: {unlocked, progress, current, needed, evidence, blocked_by}}.
    """
    if entries is None:
        entries = _read_shipping_log()
    out: dict = {}

    def is_unlocked(sid: str) -> bool:
        if sid not in out:
            _eval_skill(sid)
        return out[sid].get("unlocked", False)

    def _eval_skill(sid: str) -> None:
        skill = SKILLS_BY_ID.get(sid)
        if not skill:
            out[sid] = {"unlocked": False, "progress": 0.0, "blocked_by": "unknown-skill"}
            return
        # Prereq gate
        blocked: list[str] = []
        for p in skill.get("prereqs", []):
            if not is_unlocked(p):
                blocked.append(p)
        rule_result = _eval_rule(skill["rule"], entries)
        if blocked:
            out[sid] = {
                "unlocked": False,
                "progress": rule_result["progress"],
                "current": rule_result.get("current", 0),
                "needed": rule_result.get("needed", 0),
                "blocked_by": blocked,
                "evidence": [],
            }
            return
        out[sid] = {
            "unlocked": rule_result["satisfied"],
            "progress": rule_result["progress"],
            "current": rule_result.get("current", 0),
            "needed": rule_result.get("needed", 0),
            "blocked_by": [],
            "evidence": rule_result.get("evidence", []),
        }
        if "sub" in rule_result:
            out[sid]["sub"] = rule_result["sub"]

    for s in SKILLS:
        _eval_skill(s["id"])
    return out


def evaluate_and_persist() -> tuple[list[str], dict]:
    """Evaluate, persist newly-unlocked skills, return (newly_unlocked_ids, results)."""
    entries = _read_shipping_log()
    results = evaluate(entries)
    state = load_state()
    unlocked_state = state.setdefault("unlocked", {})
    newly: list[str] = []
    for sid, info in results.items():
        if info.get("unlocked") and sid not in unlocked_state:
            unlocked_state[sid] = {
                "at": _now_iso(),
                "evidence": info.get("evidence", []),
            }
            newly.append(sid)
    if newly:
        save_state(state)
    return newly, results


def emergent_skills(entries: Optional[list[dict]] = None) -> list[dict]:
    """Auto-spawn 'Master of LINEAGE' skills from recurring l33t/CAPS codenames.

    Threshold: 3+ ships sharing a codename. Skips common acronyms.
    """
    import re as _re
    if entries is None:
        entries = _read_shipping_log()
    SKIP = {
        "HTTP", "HTTPS", "AGPL", "MCP", "ASR", "GPT", "LLM", "LLMS",
        "AISI", "CVE", "OSS", "AGI", "ASI", "TTS", "JSON", "JSONL",
        "API", "APIS", "TODO", "NULL", "TRUE", "FALSE", "AND", "WITH",
        "ICML", "ACL", "NEURIPS", "USENIX", "ARXIV", "PDF",
        # Generic words that look like codenames but aren't lineages
        "AUDIT", "README", "NOTES", "DEMO", "TEST", "BENCH", "EVAL",
        "DRAFT", "SPEC", "DOCS", "ISSUE", "WIP", "MAIN", "SHIP",
        "HARVEST", "REPORT", "FINAL", "DRAFT", "POC", "ANCHOR",
    }
    counter: dict = {}
    for e in entries:
        title = e.get("title", "") or ""
        for tok in _re.findall(r"\b[A-Z0-9]{4,}\b", title):
            if not any(c.isalpha() for c in tok):
                continue
            if tok in SKIP:
                continue
            counter.setdefault(tok, []).append(e.get("path", "") or title[:60])
    out = []
    for codename, paths in counter.items():
        n = len(paths)
        if n < 3:
            continue
        out.append({
            "id": f"emergent-{codename.lower()}",
            "name": f"Master of {codename}",
            "tier": 5,
            "icon": "\U0001F525",  # 🔥
            "description": f"Auto-emerged from {n} ships sharing the {codename} lineage.",
            "prereqs": [],
            "rule": {"type": "lineage", "codename": codename, "n": 3},
            "unlocked": True,
            "unlocked_at": None,
            "progress": 1.0,
            "current": n,
            "needed": 3,
            "blocked_by": [],
            "evidence": paths[:5],
            "emergent": True,
            "grant": (
                f"You've shipped {n} in the {codename} lineage. Treat it as a body of work, not a streak — "
                f"the next ship should advance the thesis, not just add to the pile."
            ),
        })
    out.sort(key=lambda x: -x["current"])
    return out


def tree() -> dict:
    """Public read for the UI: catalog + evaluation + persisted unlock timestamps."""
    state = load_state()
    unlocked_state = state.get("unlocked", {})
    results = evaluate()
    nodes = []
    for s in SKILLS:
        sid = s["id"]
        r = results.get(sid, {})
        unlocked = sid in unlocked_state or r.get("unlocked", False)
        nodes.append({
            "id": sid,
            "name": s["name"],
            "tier": s["tier"],
            "icon": s.get("icon", ""),
            "description": s["description"],
            "prereqs": s.get("prereqs", []),
            "rule": s["rule"],
            "unlocked": unlocked,
            "unlocked_at": unlocked_state.get(sid, {}).get("at"),
            "progress": r.get("progress", 0.0),
            "current": r.get("current", 0),
            "needed": r.get("needed", 0),
            "blocked_by": r.get("blocked_by", []),
            "evidence": r.get("evidence", []),
            "sub": r.get("sub"),
            "grant": _get_grant_text(sid, s.get("grant")),
        })
    # Emergent skills — auto-spawned from shipping log lineage clusters
    emergent = emergent_skills()
    for e in emergent:
        nodes.append(e)
    # Forged skills — operator-approved drafts from the Forge agent.
    # They're always unlocked (acceptance IS the unlock).
    for f in load_forged_skills():
        f = dict(f)  # shallow copy
        f.setdefault("unlocked", True)
        f.setdefault("progress", 1.0)
        f.setdefault("forged", True)
        f.setdefault("tier", f.get("tier") or 3)
        f.setdefault("icon", f.get("icon") or "🪶")
        f.setdefault("prereqs", [])
        f.setdefault("blocked_by", [])
        f.setdefault("evidence", f.get("evidence", []))
        nodes.append(f)
    extra_tiers = ({5} if emergent else set()) | {f["tier"] for f in load_forged_skills()}
    tiers = sorted({s["tier"] for s in SKILLS} | extra_tiers)
    return {"nodes": nodes, "tiers": tiers}


# ─── Grant overrides + system-prompt overlay ────────────────────────────────
# Operator-edited grant text (from the Sharpen UI) lives in state file.
# Forged skills (auto-drafted from shipping patterns) live in separate file.

FORGED_FILE = Path(__file__).resolve().parent / "state" / "skills_forged.json"


def _load_grant_overrides() -> dict:
    state = load_state()
    return state.get("grants", {}) or {}


def save_grant_override(skill_id: str, text: str) -> None:
    state = load_state()
    state.setdefault("grants", {})[skill_id] = {
        "text": text.strip(),
        "edited_at": _now_iso(),
    }
    save_state(state)


def _get_grant_text(sid: str, default: Optional[str]) -> Optional[str]:
    overrides = _load_grant_overrides()
    if sid in overrides and overrides[sid].get("text"):
        return overrides[sid]["text"]
    return default


def load_forged_skills() -> list[dict]:
    """User-approved skills drafted by the Forge agent."""
    if not FORGED_FILE.exists():
        return []
    try:
        with open(FORGED_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("skills", [])
    except Exception:
        return []


def save_forged_skill(skill: dict) -> None:
    FORGED_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = load_forged_skills()
    # replace by id if exists, else append
    by_id = {s["id"]: s for s in existing}
    by_id[skill["id"]] = skill
    with open(FORGED_FILE, "w", encoding="utf-8") as f:
        json.dump({"skills": list(by_id.values())}, f, indent=2)


DRAFTS_FILE = Path(__file__).resolve().parent / "state" / "skills_forge_drafts.json"


def load_drafts() -> list[dict]:
    if not DRAFTS_FILE.exists():
        return []
    try:
        with open(DRAFTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("drafts", [])
    except Exception:
        return []


def save_drafts(drafts: list[dict]) -> None:
    DRAFTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DRAFTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"drafts": drafts, "updated_at": _now_iso()}, f, indent=2)


def accept_draft(draft_id: str) -> Optional[dict]:
    """Move a draft from pending to forged. Returns the accepted skill or None."""
    drafts = load_drafts()
    keep, accepted = [], None
    for d in drafts:
        if d.get("id") == draft_id:
            accepted = d
        else:
            keep.append(d)
    if accepted is None:
        return None
    save_drafts(keep)
    save_forged_skill(accepted)
    return accepted


def reject_draft(draft_id: str) -> bool:
    drafts = load_drafts()
    keep = [d for d in drafts if d.get("id") != draft_id]
    if len(keep) == len(drafts):
        return False
    save_drafts(keep)
    return True


def build_forge_prompt() -> str:
    """Prompt for the Forge agent — survey ALL evidence and draft 3-5 new skill candidates."""
    workshop = str(WORKSHOP)
    catalog_path = str(Path(__file__).resolve())
    return f"""# 🔨 SKILL FORGE — Draft New Skills From Real Mastery

You are forging new skills for the Pliny capability tree. A skill names a
**pattern of mastery** — something the dragon has actually demonstrated, not
something you wish were true. Your job is **archaeology, not invention**:
find the proof first, then name it.

## Step 1 — Survey EVERYTHING (not just shipping log)

Be thorough. Read across the entire workshop and operations footprint.
Pull from any of these you find useful — more sources → better skills:

### A. Shipping evidence
- **Shipping log:** `{SHIPPING_LOG}` — last 200 entries. Look at types,
  cadence (daily/burst/dry), volume, and timestamp clustering.

### B. Workshop body of work — `{workshop}/`
Treat each subdir as a different kind of evidence. Some examples:
- `canon/lessons/` — graduated lessons (synthesis after many runs)
- `canon/METHOD.md`, `LINEAGE.md`, `HORIZON.md` — declared doctrine
- `dragonfire/` — written social posts (voice, hooks, recurring themes)
- `dreams/` — speculative/visionary writing (imagination patterns)
- `conclave/` — multi-agent debates (synthesis from disagreement)
- `eruption_reports/` — incident-style writeups (depth of inspection)
- `frameworks/`, `prompt-grimoire/` — reusable artifacts (abstraction skill)
- `forge-log/`, `evolution-log/` — self-modification traces (meta-awareness)
- `gauntlet-*/`, `pack-runs/`, `lair-sessions/` — adversarial runs
- `questions/` — asked questions (curiosity domains)
- `philosophy/`, `political-economy/` — recurring intellectual obsessions

### C. Pliny Command operations
- **Existing catalog:** read `{catalog_path}` (the `SKILLS` list).
  Note every existing id, name, and rule shape. **Don't draft duplicates** —
  but you CAN draft adjacent skills (e.g. an existing skill rewards 3 audits;
  yours could reward 10 audits with high evidence count = "Surgical Auditor").
- **Existing forged drafts:** `{DRAFTS_FILE}` (may not exist). Don't redraft.
- **State file:** `{STATE_FILE.parent}/skills.json` — see what's already unlocked.

### D. Patterns that aren't artifacts
Some of the best skills name *behaviors*, not output piles:
- **Cadence** — daily shipping streaks, recovery after dry stretches
- **Quality** — average evidence-count per audit, prose depth
- **Cross-domain chains** — audit → canon-lesson → tweet pipelines
- **Collaboration** — flight-of-dragons participation, multi-agent coordination
- **Teaching** — patterns that became canon (raw notes → graduated lessons)
- **Voice/style** — recognizable rhetorical moves, signature openings
- **Departments** — concentrated work in one domain (e.g., kernel/auth/web/AI)
- **Time-of-day or session-length signatures** — long-haul vs. lightning runs

## Step 2 — Draft 3 to 5 candidate skills

For each candidate:

### Schema (flexible — forged skills bypass the evaluator on accept)
```json
{{
  "id": "kebab-case-id",            // unique, evocative, stable
  "name": "Human Title",             // short, punchy, mythic-flavored
  "tier": 2,                         // 1=foundation, 2=proficiency, 3=synthesis, 4=elder
  "icon": "🗡",                      // single emoji
  "category": "combat",              // combat | craft | voice | mind | lore | collaboration | cadence
  "description": "One-line description of what this skill means.",
  "prereqs": [],                     // optional — IDs of skills that should be earned first
  "rule": {{"type": "...", ...}},    // see below — flexible
  "grant": "Pliny-voice doctrine, 1–4 sentences. Specific, actionable, mythic.",
  "rationale": "WHY this skill belongs — cite the evidence you found (filenames, counts, dates).",
  "forged_at": "<iso timestamp utc>"
}}
```

### Rule field — DESCRIPTIVE, NOT EVALUATOR-BOUND
Forged skills are auto-unlocked when the operator accepts them. The `rule`
field documents WHY this skill is earned; it doesn't need to fit one of the
five hardcoded evaluator shapes. Use any of these (or invent your own):

- `{{"type": "count_type", "shipping_type": "audit", "n": 5}}` — classic count
- `{{"type": "count_types_all", "requirements": [...]}}` — combo gate
- `{{"type": "count_distinct_types", "n": 7}}` — variety milestone
- `{{"type": "count_canon_lessons", "n": 5}}` — graduations only
- `{{"type": "cadence", "description": "Shipped on N distinct calendar days in last 30"}}`
- `{{"type": "quality", "description": "Average ≥4 evidence files per audit across 5 audits"}}`
- `{{"type": "chain", "description": "Same target appears in audit → canon → tweet"}}`
- `{{"type": "collaboration", "description": "Participated in N flight-of-dragons runs"}}`
- `{{"type": "domain_concentration", "description": "≥5 artifacts mentioning kernel/auth/AI"}}`
- `{{"type": "voice", "description": "Recurring rhetorical move across 5+ dragonfire posts"}}`
- `{{"type": "elder", "description": "Earned after canon lesson published on this topic"}}`

### Category guidance
- **combat** — adversarial, audits, exploits, gauntlets
- **craft** — writing, framework-building, tool-making, refactor
- **voice** — dragonfire / tweets / hooks / signature rhetoric
- **mind** — synthesis, cross-domain reasoning, questions
- **lore** — canon, lineage, philosophy, doctrine-shaping
- **collaboration** — flights, conclaves, specialist orchestration
- **cadence** — streaks, recovery, rhythm, long-haul

## Step 3 — Be ambitious

Don't just count things. The best forged skills **surprise the operator** by
naming a pattern they didn't realize they'd earned:
- "You wrote three audits where the second one cited the first." → *Recursive Hunter*
- "Five tweets in the same week opened with rhetorical questions." → *Hook Discipline*
- "You shipped on 14 distinct days last month with no two days the same type." → *Polymath Cadence*

Reward **patterns** more than **piles**. A skill that rewards "did the same
thing 50 times" is worse than one that rewards "did three different things in
a deliberate sequence."

## Step 4 — Write the drafts file

Write to: `{DRAFTS_FILE}`

```json
{{
  "drafts": [ <3 to 5 skill objects> ],
  "updated_at": "<iso utc>"
}}
```

**If the file already exists, MERGE** — read existing drafts, append yours,
write back. Don't clobber drafts the operator hasn't reviewed yet.

## Quality bar

- **Earned, not given.** Threshold high enough that unlocking is proof, not participation.
- **Doctrine must be specific.** "Be careful" is not doctrine. "Three breaches
  in, patterns emerge before you call them patterns — trust the second look" IS.
- **Cite real evidence** in `rationale`. Filenames, counts, dates. If you can't
  point at evidence, you shouldn't draft the skill.
- **Don't draft a skill the operator wouldn't be proud to unlock.** Aim for
  the moment they read the name and grin — "yeah, I did earn that."
- **No duplicates** of existing catalog skills. Adjacent and complementary is fine.

Ship 3–5 drafts. The operator reviews next.
"""


def get_unlocked_overlay(max_skills: int = 12) -> str:
    """Return a Pliny-voice system-prompt section listing earned skill doctrines.

    Empty string when nothing is unlocked — caller can append unconditionally.
    """
    t = tree()
    earned = [n for n in t["nodes"] if n.get("unlocked") and n.get("grant")]
    if not earned:
        return ""
    # Order: tier descending (elder first), then by unlock recency.
    earned.sort(key=lambda n: (-(n.get("tier") or 0), n.get("unlocked_at") or ""), reverse=False)
    earned = earned[:max_skills]
    lines = ["# MASTERED SKILLS — your earned doctrine", ""]
    lines.append(f"You have unlocked {len(earned)} skill(s). Each is hard-won. Apply them:")
    lines.append("")
    for n in earned:
        icon = n.get("icon", "•")
        lines.append(f"## {icon} {n['name']}")
        lines.append(n["grant"])
        lines.append("")
    lines.append("Your skills are not decoration — they shape how you work. Lean on them.")
    return "\n".join(lines)


if __name__ == "__main__":
    # Smoke test
    newly, _ = evaluate_and_persist()
    print(f"newly unlocked: {newly}")
    t = tree()
    for n in t["nodes"]:
        mark = "✅" if n["unlocked"] else "🔒"
        print(f"  {mark} T{n['tier']} {n['icon']} {n['name']}  "
              f"({n['current']}/{n['needed']}, prereqs={n['prereqs']})")
