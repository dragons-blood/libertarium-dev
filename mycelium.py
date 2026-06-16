#!/usr/bin/env python3
"""
mycelium.py — the shared substrate for organic agent collaboration.

A living, decaying, self-reinforcing nervous system underneath the empire.
Inspired by mycelial networks, slime mold routing (Tero 2010), quorum
sensing (Vibrio fischeri), Voyager skill libraries (NeurIPS 2023), and
generative agent memory streams (Park et al. 2023).

Implements the substrate layer for the top-10 collaboration patterns:

  1. spores         — stigmergic pheromones with decay + reinforcement
  2. skills         — Voyager-style reusable code skills with semantic search
  3. streams        — per-agent memory streams (JSONL)
  4. reflection     — distilled summary of an agent's recent stream
  5. quorum         — N-of-M signal counting within a time window
  6. mobbing        — threat spores that broadcast to all listeners
  7. review         — peer-review tickets
  8. routing        — slime-mold success-weighted topic→agent recommendation
  9. challenge      — constitutional debate tickets
 10. ledger         — append-only akashic records

Storage lives at  $PLINY_WORKSHOP/.mycelium/
                    spores/      one JSON per spore
                    skills/      one Python file + manifest entry
                    streams/     one JSONL per agent_id
                    ledger.jsonl append-only akashic ledger
                    skills.json  skill registry manifest
                    quorum.json  open quorum signals + thresholds
                    routing.json topic→agent affinity weights

Singleton entrypoint:  Mycelium.get()

No external deps. Stdlib only. Thread-safe (one RLock guards mutation).
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


# ─── paths ──────────────────────────────────────────────────────────────────

WORKSHOP_DIR = Path(
    os.environ.get("PLINY_WORKSHOP", str(Path.home() / "pliny-workshop"))
).expanduser().resolve()

MYCELIUM_ROOT = WORKSHOP_DIR / ".mycelium"
SPORE_DIR = MYCELIUM_ROOT / "spores"
SKILL_DIR = MYCELIUM_ROOT / "skills"
STREAM_DIR = MYCELIUM_ROOT / "streams"
LEDGER_FILE = MYCELIUM_ROOT / "ledger.jsonl"
SKILL_MANIFEST = MYCELIUM_ROOT / "skills.json"
QUORUM_FILE = MYCELIUM_ROOT / "quorum.json"
ROUTING_FILE = MYCELIUM_ROOT / "routing.json"


# ─── tuning constants ───────────────────────────────────────────────────────

# Spore decay: signal *= exp(-age_seconds / TAU)
SPORE_TAU = 60 * 60 * 6           # half-life ≈ 4.16h
SPORE_PRUNE_BELOW = 0.05          # drop spores below this signal
SPORE_PRUNE_AGE = 60 * 60 * 24 * 7  # hard cap: 7 days
DECAY_TICK_SECONDS = 300          # daemon decay every 5 min
QUORUM_DEFAULT_WINDOW = 60 * 30   # 30 min
ROUTING_LEARNING_RATE = 0.25      # how fast affinities update


# ─── utilities ──────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_epoch() -> float:
    return time.time()


def _safe_id() -> str:
    return uuid.uuid4().hex[:12]


def _safe_name(name: str) -> str:
    """Sanitize an arbitrary string into a filesystem-safe slug."""
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return s[:80] or "anon"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content)
    tmp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _append_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path, limit: Optional[int] = None) -> List[dict]:
    if not path.exists():
        return []
    out: List[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    if limit:
        out = out[-limit:]
    return out


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9_]{3,}", text or "")]


def _overlap_score(a: Iterable[str], b: Iterable[str]) -> float:
    aset, bset = set(a), set(b)
    if not aset or not bset:
        return 0.0
    return len(aset & bset) / math.sqrt(len(aset) * len(bset))


# ─── data classes ───────────────────────────────────────────────────────────

@dataclass
class Spore:
    id: str
    originator: str
    tags: List[str]
    summary: str
    payload: Dict[str, Any]
    created_at: float
    signal: float = 1.0
    refs: List[str] = field(default_factory=list)   # session_ids, mission_ids, etc.
    threat: bool = False                            # if True → mobbing target
    consumed_by: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at_iso"] = datetime.fromtimestamp(self.created_at, timezone.utc).isoformat()
        return d


# ─── singleton ──────────────────────────────────────────────────────────────

class Mycelium:
    _instance: Optional["Mycelium"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get(cls) -> "Mycelium":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = Mycelium()
        return cls._instance

    def __init__(self):
        self._lock = threading.RLock()
        self._daemon_started = False
        self._broadcast_hook: Optional[Callable[[str, dict], None]] = None
        for p in (MYCELIUM_ROOT, SPORE_DIR, SKILL_DIR, STREAM_DIR):
            p.mkdir(parents=True, exist_ok=True)
        if not LEDGER_FILE.exists():
            LEDGER_FILE.touch()

    # ── broadcast hook (so server.py can wire SSE) ──────────────────────────

    def set_broadcast_hook(self, fn: Callable[[str, dict], None]) -> None:
        self._broadcast_hook = fn

    def _broadcast(self, event: str, payload: dict) -> None:
        if self._broadcast_hook is None:
            return
        try:
            self._broadcast_hook(event, payload)
        except Exception:
            pass

    # ── 1. spores ───────────────────────────────────────────────────────────

    def drop_spore(
        self,
        originator: str,
        tags: List[str],
        summary: str,
        payload: Optional[dict] = None,
        signal: float = 1.0,
        refs: Optional[List[str]] = None,
        threat: bool = False,
    ) -> Spore:
        spore = Spore(
            id=_safe_id(),
            originator=originator or "anon",
            tags=[t.lower().strip() for t in (tags or []) if t.strip()],
            summary=(summary or "")[:500],
            payload=payload or {},
            created_at=_now_epoch(),
            signal=float(signal),
            refs=refs or [],
            threat=bool(threat),
        )
        with self._lock:
            _atomic_write(SPORE_DIR / f"{spore.id}.json", json.dumps(spore.to_dict(), indent=2))
            self.record({
                "kind": "spore_drop",
                "spore_id": spore.id,
                "originator": spore.originator,
                "tags": spore.tags,
                "threat": spore.threat,
            })
        ev = "mycelium_threat" if threat else "mycelium_spore"
        self._broadcast(ev, {"event": "drop", "spore": spore.to_dict()})
        return spore

    def _iter_spore_files(self) -> List[Path]:
        return sorted(SPORE_DIR.glob("*.json"))

    def _load_spore(self, path: Path) -> Optional[Spore]:
        try:
            data = json.loads(path.read_text())
        except Exception:
            return None
        try:
            return Spore(
                id=data["id"],
                originator=data.get("originator", "anon"),
                tags=data.get("tags", []),
                summary=data.get("summary", ""),
                payload=data.get("payload", {}),
                created_at=float(data.get("created_at", _now_epoch())),
                signal=float(data.get("signal", 1.0)),
                refs=data.get("refs", []),
                threat=bool(data.get("threat", False)),
                consumed_by=data.get("consumed_by", []),
            )
        except Exception:
            return None

    def _save_spore(self, spore: Spore) -> None:
        _atomic_write(SPORE_DIR / f"{spore.id}.json", json.dumps(spore.to_dict(), indent=2))

    def read_intray(
        self,
        agent: str,
        tags: Optional[List[str]] = None,
        k: int = 10,
        include_consumed: bool = False,
        only_threats: bool = False,
    ) -> List[dict]:
        """Top-k spores for an agent, ranked by relevance × recency × signal."""
        now = _now_epoch()
        q_tags = [t.lower().strip() for t in (tags or []) if t.strip()]
        scored: List[Tuple[float, Spore]] = []
        with self._lock:
            for path in self._iter_spore_files():
                s = self._load_spore(path)
                if s is None:
                    continue
                if only_threats and not s.threat:
                    continue
                if not include_consumed and agent in s.consumed_by:
                    continue
                age = now - s.created_at
                if age > SPORE_PRUNE_AGE:
                    continue
                recency = math.exp(-age / SPORE_TAU)
                if q_tags:
                    rel = _overlap_score(q_tags, s.tags) or 0.1
                else:
                    rel = 1.0
                score = rel * recency * max(s.signal, 0.0)
                if score <= 0:
                    continue
                scored.append((score, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: List[dict] = []
        for score, s in scored[:k]:
            d = s.to_dict()
            d["_score"] = round(score, 4)
            out.append(d)
        return out

    def reinforce(self, spore_id: str, agent: str, delta: float = 0.5) -> Optional[dict]:
        with self._lock:
            path = SPORE_DIR / f"{spore_id}.json"
            if not path.exists():
                return None
            s = self._load_spore(path)
            if s is None:
                return None
            s.signal = min(s.signal + delta, 10.0)
            if agent and agent not in s.consumed_by:
                s.consumed_by.append(agent)
            self._save_spore(s)
            self.record({
                "kind": "spore_reinforce",
                "spore_id": spore_id,
                "agent": agent,
                "new_signal": s.signal,
            })
        self._broadcast("mycelium_spore", {"event": "reinforce", "spore": s.to_dict()})
        return s.to_dict()

    def list_spores(self, limit: int = 200, threats_only: bool = False) -> List[dict]:
        now = _now_epoch()
        out: List[dict] = []
        with self._lock:
            for path in self._iter_spore_files():
                s = self._load_spore(path)
                if s is None:
                    continue
                if threats_only and not s.threat:
                    continue
                d = s.to_dict()
                d["age_seconds"] = int(now - s.created_at)
                d["effective_signal"] = round(
                    s.signal * math.exp(-(now - s.created_at) / SPORE_TAU), 4
                )
                out.append(d)
        out.sort(key=lambda x: x["effective_signal"], reverse=True)
        return out[:limit]

    def decay_tick(self) -> dict:
        """Apply decay, prune dead spores. Returns stats."""
        now = _now_epoch()
        pruned = 0
        survived = 0
        with self._lock:
            for path in self._iter_spore_files():
                s = self._load_spore(path)
                if s is None:
                    try:
                        path.unlink()
                    except Exception:
                        pass
                    continue
                age = now - s.created_at
                effective = s.signal * math.exp(-age / SPORE_TAU)
                if effective < SPORE_PRUNE_BELOW or age > SPORE_PRUNE_AGE:
                    try:
                        path.unlink()
                        pruned += 1
                    except Exception:
                        pass
                else:
                    survived += 1
        if pruned:
            self.record({"kind": "decay_tick", "pruned": pruned, "survived": survived})
        return {"pruned": pruned, "survived": survived, "at": _now_iso()}

    # ── 2. skills (Voyager library) ─────────────────────────────────────────

    def _load_skill_manifest(self) -> dict:
        return _read_json(SKILL_MANIFEST, {"skills": {}})

    def _save_skill_manifest(self, m: dict) -> None:
        _atomic_write(SKILL_MANIFEST, json.dumps(m, indent=2))

    def register_skill(
        self,
        name: str,
        code: str,
        description: str,
        originator: str,
        tags: Optional[List[str]] = None,
        verified: bool = False,
    ) -> dict:
        slug = _safe_name(name)
        skill_id = f"{slug}-{_safe_id()[:6]}"
        path = SKILL_DIR / f"{skill_id}.py"
        with self._lock:
            _atomic_write(path, code)
            manifest = self._load_skill_manifest()
            entry = {
                "id": skill_id,
                "name": name,
                "slug": slug,
                "description": description,
                "originator": originator,
                "tags": [t.lower().strip() for t in (tags or [])],
                "verified": bool(verified),
                "uses": 0,
                "success": 0,
                "created_at": _now_iso(),
                "path": str(path),
            }
            manifest["skills"][skill_id] = entry
            self._save_skill_manifest(manifest)
            self.record({"kind": "skill_register", "skill_id": skill_id, "name": name, "originator": originator})
        self._broadcast("mycelium_skill", {"event": "register", "skill": entry})
        return entry

    def list_skills(self, query: Optional[str] = None, tags: Optional[List[str]] = None, limit: int = 50) -> List[dict]:
        m = self._load_skill_manifest()
        skills = list(m.get("skills", {}).values())
        if query:
            qt = _tokens(query)
            scored = []
            for s in skills:
                hay = _tokens(" ".join([s.get("name", ""), s.get("description", ""), " ".join(s.get("tags", []))]))
                score = _overlap_score(qt, hay)
                if tags:
                    if any(t.lower() in s.get("tags", []) for t in tags):
                        score += 0.5
                if score > 0:
                    sc = dict(s)
                    sc["_score"] = round(score, 4)
                    scored.append(sc)
            scored.sort(key=lambda x: x["_score"], reverse=True)
            return scored[:limit]
        if tags:
            tagset = {t.lower() for t in tags}
            skills = [s for s in skills if tagset & set(s.get("tags", []))]
        skills.sort(key=lambda s: s.get("success", 0), reverse=True)
        return skills[:limit]

    def use_skill(self, skill_id: str, succeeded: bool = True) -> Optional[dict]:
        with self._lock:
            m = self._load_skill_manifest()
            entry = m.get("skills", {}).get(skill_id)
            if entry is None:
                return None
            entry["uses"] = entry.get("uses", 0) + 1
            if succeeded:
                entry["success"] = entry.get("success", 0) + 1
            self._save_skill_manifest(m)
        self._broadcast("mycelium_skill", {"event": "use", "skill": entry, "succeeded": succeeded})
        return entry

    def read_skill_code(self, skill_id: str) -> Optional[str]:
        m = self._load_skill_manifest()
        entry = m.get("skills", {}).get(skill_id)
        if entry is None:
            return None
        try:
            return Path(entry["path"]).read_text()
        except Exception:
            return None

    # ── 3+4. streams + reflection ───────────────────────────────────────────

    def _stream_path(self, agent: str) -> Path:
        return STREAM_DIR / f"{_safe_name(agent)}.jsonl"

    def append_stream(self, agent: str, kind: str, content: str, importance: float = 0.5, refs: Optional[List[str]] = None) -> dict:
        entry = {
            "ts": _now_iso(),
            "epoch": _now_epoch(),
            "agent": agent,
            "kind": kind,
            "content": (content or "")[:2000],
            "importance": max(0.0, min(1.0, float(importance))),
            "refs": refs or [],
        }
        with self._lock:
            _append_jsonl(self._stream_path(agent), entry)
        self._broadcast("mycelium_stream", {"agent": agent, "entry": entry})
        return entry

    def read_stream(self, agent: str, limit: int = 50) -> List[dict]:
        return _read_jsonl(self._stream_path(agent), limit=limit)

    def reflect(self, agent: str, k: int = 20) -> dict:
        """Surface the top-k memory entries by importance × recency.

        Park-et-al-style scoring without an LLM call — gives the consuming
        agent a packet of high-signal memories it can then summarize itself.
        """
        entries = _read_jsonl(self._stream_path(agent), limit=500)
        if not entries:
            return {"agent": agent, "memories": [], "summary_seed": ""}
        now = _now_epoch()
        scored = []
        for e in entries:
            age = max(0.0, now - float(e.get("epoch", now)))
            recency = math.exp(-age / (60 * 60 * 24))  # 1-day TAU
            imp = float(e.get("importance", 0.5))
            score = (recency * 0.5) + (imp * 0.5)
            scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [e for _, e in scored[:k]]
        seed_lines = [f"- ({e.get('kind')}) {e.get('content','')[:160]}" for e in top]
        return {
            "agent": agent,
            "memories": top,
            "summary_seed": "\n".join(seed_lines),
        }

    # ── 5. quorum sensing ───────────────────────────────────────────────────

    def _load_quorum(self) -> dict:
        return _read_json(QUORUM_FILE, {"topics": {}})

    def _save_quorum(self, q: dict) -> None:
        _atomic_write(QUORUM_FILE, json.dumps(q, indent=2))

    def quorum_signal(
        self,
        topic: str,
        originator: str,
        threshold: int = 3,
        window_seconds: int = QUORUM_DEFAULT_WINDOW,
        payload: Optional[dict] = None,
    ) -> dict:
        """Emit one signal toward a topic. When N distinct originators emit
        within `window_seconds`, the quorum fires and is reset.
        """
        topic = topic.strip().lower()
        now = _now_epoch()
        with self._lock:
            q = self._load_quorum()
            t = q["topics"].setdefault(topic, {"signals": [], "fires": []})
            t["signals"] = [s for s in t["signals"] if (now - s["at"]) <= window_seconds]
            t["signals"].append({"originator": originator, "at": now, "payload": payload or {}})
            distinct = {s["originator"] for s in t["signals"]}
            fired = False
            cascade_event = None
            if len(distinct) >= threshold:
                cascade_event = {
                    "topic": topic,
                    "at": _now_iso(),
                    "distinct": list(distinct),
                    "signals": list(t["signals"]),
                    "threshold": threshold,
                }
                t["fires"].append(cascade_event)
                t["signals"] = []  # reset
                fired = True
            self._save_quorum(q)
            if fired:
                self.record({"kind": "quorum_fire", **cascade_event})
        if fired:
            self._broadcast("mycelium_quorum", {"event": "fire", "cascade": cascade_event})
            # When a quorum fires, drop a spore so dependent agents notice it.
            self.drop_spore(
                originator="quorum",
                tags=["quorum", topic],
                summary=f"quorum fired on '{topic}' ({len(distinct)} agents)",
                payload=cascade_event,
                signal=2.0,
            )
        return {
            "topic": topic,
            "fired": fired,
            "distinct": list(distinct),
            "needed": threshold,
            "cascade": cascade_event,
        }

    def quorum_state(self) -> dict:
        with self._lock:
            return self._load_quorum()

    # ── 6. mobbing ──────────────────────────────────────────────────────────

    def mob(self, ref: str, threat: str, originator: str, tags: Optional[List[str]] = None) -> dict:
        """Spawn a threat spore + emit a high-priority broadcast event."""
        spore = self.drop_spore(
            originator=originator,
            tags=["mob", "threat"] + (tags or []),
            summary=f"MOB: {threat}",
            payload={"threat": threat, "ref": ref},
            signal=3.0,
            refs=[ref] if ref else [],
            threat=True,
        )
        self._broadcast("mycelium_mob", {"spore_id": spore.id, "ref": ref, "threat": threat, "originator": originator})
        self.record({"kind": "mob_call", "ref": ref, "threat": threat, "originator": originator, "spore_id": spore.id})
        return {"spore_id": spore.id, "ref": ref, "threat": threat}

    # ── 7. peer review ──────────────────────────────────────────────────────

    def request_review(self, ref: str, summary: str, originator: str, tags: Optional[List[str]] = None) -> dict:
        spore = self.drop_spore(
            originator=originator,
            tags=["review", "request"] + (tags or []),
            summary=f"REVIEW REQ: {summary}",
            payload={"ref": ref, "request": "peer_review"},
            signal=1.5,
            refs=[ref] if ref else [],
        )
        self.record({"kind": "review_request", "ref": ref, "originator": originator, "spore_id": spore.id})
        return {"spore_id": spore.id, "ref": ref}

    def post_review(self, ref: str, reviewer: str, verdict: str, notes: str, severity: float = 0.5) -> dict:
        entry = {
            "kind": "review",
            "ref": ref,
            "reviewer": reviewer,
            "verdict": verdict,
            "notes": notes,
            "severity": severity,
            "at": _now_iso(),
        }
        self.record(entry)
        spore = self.drop_spore(
            originator=reviewer,
            tags=["review", "result", verdict.lower()],
            summary=f"REVIEW ({verdict}): {notes[:120]}",
            payload=entry,
            signal=1.0 + severity,
            refs=[ref] if ref else [],
        )
        self._broadcast("mycelium_review", {"spore_id": spore.id, **entry})
        return {"spore_id": spore.id, **entry}

    # ── 8. slime-mold routing ───────────────────────────────────────────────

    def _load_routing(self) -> dict:
        return _read_json(ROUTING_FILE, {"topics": {}})

    def _save_routing(self, r: dict) -> None:
        _atomic_write(ROUTING_FILE, json.dumps(r, indent=2))

    def routing_record(self, topic: str, agent: str, succeeded: bool, magnitude: float = 1.0) -> dict:
        """Reinforce the topic→agent path. Successful runs thicken it; failures attenuate."""
        topic = topic.strip().lower()
        agent = agent.strip()
        with self._lock:
            r = self._load_routing()
            t = r["topics"].setdefault(topic, {})
            cur = float(t.get(agent, 1.0))
            target = 2.0 if succeeded else 0.3
            new = cur + (target - cur) * ROUTING_LEARNING_RATE * magnitude
            new = max(0.05, min(5.0, new))
            t[agent] = round(new, 4)
            self._save_routing(r)
            self.record({"kind": "routing_update", "topic": topic, "agent": agent, "weight": new, "succeeded": succeeded})
        return {"topic": topic, "agent": agent, "weight": new}

    def routing_for(self, topic: str, k: int = 5) -> List[dict]:
        topic = topic.strip().lower()
        with self._lock:
            r = self._load_routing()
        t = r.get("topics", {}).get(topic, {})
        ranked = sorted(t.items(), key=lambda kv: kv[1], reverse=True)
        return [{"agent": a, "weight": w} for a, w in ranked[:k]]

    # ── 9. constitutional debate ────────────────────────────────────────────

    def challenge(self, ref: str, claim: str, challenger: str, principle: Optional[str] = None) -> dict:
        spore = self.drop_spore(
            originator=challenger,
            tags=["challenge", "constitution"],
            summary=f"CHALLENGE: {claim[:120]}",
            payload={"ref": ref, "claim": claim, "principle": principle},
            signal=1.8,
            refs=[ref] if ref else [],
        )
        self.record({"kind": "challenge", "ref": ref, "claim": claim, "challenger": challenger, "principle": principle, "spore_id": spore.id})
        self._broadcast("mycelium_challenge", {"spore_id": spore.id, "ref": ref, "challenger": challenger})
        return {"spore_id": spore.id, "ref": ref}

    # ── 10. akashic ledger ──────────────────────────────────────────────────

    def record(self, event: dict) -> dict:
        entry = dict(event)
        entry.setdefault("ts", _now_iso())
        entry.setdefault("epoch", _now_epoch())
        _append_jsonl(LEDGER_FILE, entry)
        return entry

    def read_ledger(
        self,
        limit: int = 100,
        kind: Optional[str] = None,
        since_epoch: Optional[float] = None,
    ) -> List[dict]:
        rows = _read_jsonl(LEDGER_FILE)
        if kind:
            rows = [r for r in rows if r.get("kind") == kind]
        if since_epoch is not None:
            rows = [r for r in rows if float(r.get("epoch", 0)) >= since_epoch]
        return rows[-limit:]

    # ── housekeeping daemon ─────────────────────────────────────────────────

    def start_daemon(self) -> None:
        with self._lock:
            if self._daemon_started:
                return
            self._daemon_started = True
        t = threading.Thread(target=self._daemon_loop, daemon=True, name="mycelium-decay")
        t.start()

    def _daemon_loop(self) -> None:
        while True:
            try:
                self.decay_tick()
            except Exception:
                pass
            time.sleep(DECAY_TICK_SECONDS)

    # ── overview snapshot ───────────────────────────────────────────────────

    def overview(self) -> dict:
        with self._lock:
            spores = self.list_spores(limit=50)
            skills = self._load_skill_manifest().get("skills", {})
            quorum = self._load_quorum()
            routing = self._load_routing()
            streams: Dict[str, int] = {}
            for p in STREAM_DIR.glob("*.jsonl"):
                try:
                    n = sum(1 for _ in p.open())
                except Exception:
                    n = 0
                streams[p.stem] = n
            ledger_tail = _read_jsonl(LEDGER_FILE, limit=20)
        return {
            "spores_top": spores[:20],
            "spore_count": len(spores),
            "threat_count": sum(1 for s in spores if s.get("threat")),
            "skill_count": len(skills),
            "skills_top": sorted(skills.values(), key=lambda s: s.get("success", 0), reverse=True)[:10],
            "quorum_topics": list(quorum.get("topics", {}).keys()),
            "routing_topics": list(routing.get("topics", {}).keys()),
            "streams": streams,
            "ledger_tail": ledger_tail,
            "at": _now_iso(),
        }


# ─── convenience module-level proxies ───────────────────────────────────────

def get() -> Mycelium:
    return Mycelium.get()


if __name__ == "__main__":
    m = Mycelium.get()
    s = m.drop_spore(
        originator="cli-smoke",
        tags=["smoke", "test"],
        summary="hello from the mycelium",
        signal=1.5,
    )
    print("dropped:", s.id)
    print("intray:", json.dumps(m.read_intray("anon", tags=["smoke"], k=3), indent=2))
    print("overview:", json.dumps(m.overview(), indent=2)[:600])
