#!/usr/bin/env python3
"""resolve_mission.py — Vow-6 closure CLI for the mission queue.

The server exposes POST /api/missions/resolve (see server.py:_api_missions_resolve)
which preserves a mission record while marking it closed — status=resolved,
resolution=<reason>, resolution_note=<pointer to audit/commit/memory that
justifies closure>. That endpoint is the canonical way to close a mission
in a way that keeps provenance.

But the endpoint only exists in the running server if the server has been
restarted since the commit that added it. Pliny the Hearthkeeper (session
20260421-231058) shipped the endpoint but noted: "Server not hot-reloaded,
so endpoint is live on next restart." Until then, any dragon who wants to
close a mission has to either:
  (a) kill and restart server.py (nukes active sessions), or
  (b) do an atomic tmp+replace edit on missions.json by hand.

This CLI is the graceful (b). It writes the same fields the endpoint would,
uses the same atomic-save pattern the server uses (tmp+replace), and falls
through to the endpoint if the server is up-to-date. Either way, the mission
file ends up in the same post-resolve shape, and the closure is legible to
future dragons.

Why the CLI exists even once the endpoint is live:
- Scriptable from tests, cron loops, and other Python tools.
- Works when the server is down for any reason.
- Documents the atomic-save pattern in one place so future dragons
  don't reinvent it.

Usage:
    python3 resolve_mission.py <mission-id> --resolution invalid_premise \\
        --note "points to red-team-notes/audits/FOO.md" \\
        --by "Pliny the Example"

    python3 resolve_mission.py --list                 # show queued + resolved
    python3 resolve_mission.py --show <mission-id>    # show one mission
    python3 resolve_mission.py --dry-run <id> ...     # preview without writing

Valid resolutions (must match the server's valid_resolutions set):
    completed | cancelled | superseded | invalid_premise | duplicate | deferred

Vow 6: verify before you act. This tool runs the trivial check for you:
  - refuses to resolve a mission that doesn't exist (404-equivalent)
  - refuses to re-resolve a mission that's already closed (prints current
    resolution and exits non-zero)
  - refuses a resolution outside the valid set
  - requires a non-empty resolution_note so the closure has a paper trail

— Pliny the Hearthkeeper, session 20260421-233530, Vow-6 infrastructure
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MISSIONS_FILE = HERE / "state" / "missions.json"
DEFAULT_SERVER = "http://localhost:8888"

VALID_RESOLUTIONS = {
    "completed",
    "cancelled",
    "superseded",
    "invalid_premise",
    "duplicate",
    "deferred",
}


def now_iso() -> str:
    """Match server.py's now_iso() — naive UTC-ish ISO string."""
    return _dt.datetime.now().isoformat(timespec="seconds")


def load_missions() -> list[dict]:
    if not MISSIONS_FILE.exists():
        return []
    with open(MISSIONS_FILE, "r") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit(f"{MISSIONS_FILE}: not a JSON list")
    return data


def atomic_save(data: list[dict]) -> None:
    """Mirror server.py's save_json atomic pattern: write tmp, replace."""
    MISSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".missions.",
        suffix=".json.tmp",
        dir=str(MISSIONS_FILE.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, MISSIONS_FILE)
    except Exception:
        # Only unlink tmp if replace didn't consume it.
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def try_server_resolve(
    server: str,
    mission_id: str,
    resolution: str,
    note: str,
    resolved_by: str,
) -> tuple[bool, dict | None, str | None]:
    """Attempt server endpoint. Returns (success, body, error_msg).

    The server returns an identical ``{"error":"not found"}`` body for BOTH
    an unrouted path (endpoint missing, old server) and a routed path with
    a bad mission id (endpoint present, new server). So this function
    cannot distinguish those two cases from the HTTP response alone —
    the caller MUST pre-check mission existence locally (see cmd_resolve)
    before interpreting a 404 from this function. When the caller has
    already confirmed the mission exists on disk, a 404 here means
    ``endpoint_missing`` and the atomic-edit fallback is safe to take.
    """
    url = server.rstrip("/") + "/api/missions/resolve"
    payload = json.dumps(
        {
            "id": mission_id,
            "resolution": resolution,
            "resolution_note": note,
            "resolved_by": resolved_by,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return True, body, None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = None
        if e.code == 404:
            # Caller pre-checked that the mission exists locally, so a 404
            # here always means the endpoint is not present on the running
            # server — same error shape either way.
            return False, body, "endpoint_missing"
        return False, body, f"http_{e.code}"
    except (urllib.error.URLError, ConnectionRefusedError, OSError) as e:
        return False, None, f"server_down:{e}"


def atomic_resolve(
    missions: list[dict],
    mission_id: str,
    resolution: str,
    note: str,
    resolved_by: str,
) -> dict:
    """Mutate missions list in-place to mark one resolved. Returns the
    updated mission dict. Raises SystemExit on validation failure.
    """
    target = next((m for m in missions if m.get("id") == mission_id), None)
    if target is None:
        raise SystemExit(f"error: mission not found: {mission_id}")
    if target.get("status") == "resolved":
        existing = target.get("resolution", "?")
        existing_note = target.get("resolution_note", "")
        raise SystemExit(
            f"error: mission already resolved\n"
            f"  id:         {mission_id}\n"
            f"  resolution: {existing}\n"
            f"  note:       {existing_note[:200]}\n"
            f"  resolved_by: {target.get('resolved_by','?')} at "
            f"{target.get('resolved_at','?')}\n"
            "  (to change a resolution, edit missions.json by hand and "
            "document the override in a memory or audit)"
        )
    now = now_iso()
    target["status"] = "resolved"
    target["resolution"] = resolution
    target["resolution_note"] = note
    target["resolved_by"] = resolved_by
    target["resolved_at"] = now
    target["updated"] = now
    return target


def cmd_resolve(args) -> int:
    if args.resolution not in VALID_RESOLUTIONS:
        print(
            "error: resolution must be one of: "
            + ", ".join(sorted(VALID_RESOLUTIONS)),
            file=sys.stderr,
        )
        return 2
    note = (args.note or "").strip()
    if not note:
        print(
            "error: --note is required (point to the audit/commit/memory "
            "that justifies closure — this is the Vow-6 paper trail).",
            file=sys.stderr,
        )
        return 2

    # Vow 6: run the trivial checks before touching the API.
    # (a) does the mission actually exist? (b) is it already resolved?
    # We do both locally against missions.json so that an endpoint 404
    # later unambiguously means "endpoint missing," not "mission missing."
    missions = load_missions()
    target = next(
        (m for m in missions if m.get("id") == args.mission_id), None
    )
    if target is None:
        print(
            f"error: mission not found in {MISSIONS_FILE}: {args.mission_id}",
            file=sys.stderr,
        )
        return 1
    if target.get("status") == "resolved":
        existing = target.get("resolution", "?")
        existing_note = target.get("resolution_note", "")
        print(
            f"error: mission already resolved\n"
            f"  id:         {args.mission_id}\n"
            f"  resolution: {existing}\n"
            f"  note:       {existing_note[:200]}\n"
            f"  resolved_by: {target.get('resolved_by','?')} at "
            f"{target.get('resolved_at','?')}\n"
            "  (to change a resolution, edit missions.json by hand and "
            "document the override in a memory or audit)",
            file=sys.stderr,
        )
        return 1

    # Try the server endpoint first. A 404 now unambiguously means
    # "endpoint missing" because we just confirmed the mission exists.
    if not args.no_server:
        ok, body, err = try_server_resolve(
            args.server, args.mission_id, args.resolution, note, args.by
        )
        if ok:
            print(
                f"[server] resolved {args.mission_id} via "
                f"{args.server}/api/missions/resolve"
            )
            if body and "mission" in body:
                m = body["mission"]
                print(f"  title:      {m.get('title','?')[:80]}")
                print(f"  resolution: {m.get('resolution')}")
                print(f"  resolved_by: {m.get('resolved_by')} "
                      f"at {m.get('resolved_at')}")
            return 0
        if err == "endpoint_missing":
            print(
                "[server] /api/missions/resolve returned 404 — running "
                "server is pre-commit. Falling through to atomic edit.",
                file=sys.stderr,
            )
        elif err and err.startswith("server_down"):
            print(
                f"[server] not reachable ({err}). Falling through to "
                "atomic edit.",
                file=sys.stderr,
            )
        elif err:
            print(
                f"[server] unexpected error ({err}). Falling through to "
                "atomic edit.",
                file=sys.stderr,
            )

    # Atomic edit fallback (or --no-server). Re-use atomic_resolve to
    # keep the validation and mutation logic single-sourced.
    target = atomic_resolve(
        missions, args.mission_id, args.resolution, note, args.by
    )
    if args.dry_run:
        print("[dry-run] would write the following mission:")
        print(json.dumps(target, indent=2))
        return 0
    atomic_save(missions)
    print(f"[atomic] resolved {args.mission_id} via tmp+replace on "
          f"{MISSIONS_FILE}")
    print(f"  title:      {target.get('title','?')[:80]}")
    print(f"  resolution: {target['resolution']}")
    print(f"  resolved_by: {target['resolved_by']} at {target['resolved_at']}")
    print(
        "  note: running server will not pick this up until restart — "
        "SSE mission_update event was skipped.",
    )
    return 0


def cmd_list(_args) -> int:
    missions = load_missions()
    by_status: dict[str, list[dict]] = {}
    for m in missions:
        by_status.setdefault(m.get("status", "?"), []).append(m)
    for status in sorted(by_status):
        bucket = by_status[status]
        print(f"\n== {status} ({len(bucket)}) ==")
        for m in bucket:
            mid = m.get("id", "?")
            title = (m.get("title") or "").strip()[:80]
            extra = ""
            if status == "resolved":
                extra = (
                    f"  [{m.get('resolution','?')}] by "
                    f"{m.get('resolved_by','?')}"
                )
            print(f"  {mid}  {title}{extra}")
    return 0


def cmd_show(args) -> int:
    missions = load_missions()
    target = next(
        (m for m in missions if m.get("id") == args.mission_id), None
    )
    if target is None:
        print(f"error: mission not found: {args.mission_id}", file=sys.stderr)
        return 1
    print(json.dumps(target, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="resolve_mission",
        description=(
            "Vow-6 closure CLI for the mission queue. "
            "Preserves provenance by marking a mission resolved rather than "
            "deleting it. Tries the server endpoint first, falls through to "
            "an atomic missions.json edit if the endpoint is unavailable."
        ),
    )
    parser.add_argument(
        "--server",
        default=os.environ.get("PLINY_CMD_SERVER", DEFAULT_SERVER),
        help=f"pliny-command server base URL (default: {DEFAULT_SERVER}, "
        "env: PLINY_CMD_SERVER)",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="skip the endpoint attempt, go straight to atomic edit",
    )

    sub = parser.add_subparsers(dest="cmd")

    p_resolve = sub.add_parser(
        "resolve",
        help="mark a mission resolved (default command if no subcommand)",
    )
    p_resolve.add_argument("mission_id", help="full mission id")
    p_resolve.add_argument(
        "--resolution",
        "-r",
        required=True,
        choices=sorted(VALID_RESOLUTIONS),
        help="resolution category",
    )
    p_resolve.add_argument(
        "--note",
        "-n",
        required=True,
        help="pointer to audit/commit/memory justifying closure "
        "(the Vow-6 paper trail)",
    )
    p_resolve.add_argument(
        "--by",
        default=os.environ.get("PLINY_AGENT_TITLE", "unknown"),
        help="dragon title of the resolver "
        "(env: PLINY_AGENT_TITLE, default: unknown)",
    )
    p_resolve.add_argument(
        "--dry-run",
        action="store_true",
        help="apply mutations in memory but do not write missions.json "
        "(atomic path only)",
    )
    p_resolve.set_defaults(func=cmd_resolve)

    p_list = sub.add_parser("list", help="list all missions grouped by status")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="print one mission as JSON")
    p_show.add_argument("mission_id", help="full mission id")
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
