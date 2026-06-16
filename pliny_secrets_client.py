"""Thin client for talking to the Pliny secrets sidecar.

The sidecar (pliny_secrets_sidecar.py) holds keys in RAM and exposes a narrow
Unix-socket API. This module is what server.py / the drafter calls into.

Public functions:
  sidecar_ping()                                — is the sidecar alive?
  sidecar_providers()                           — which providers are loaded?
  sidecar_oauth_status()                        — which Hermes OAuth providers ready
  sidecar_draft_tweets(context, n, use_xai)     — generate candidates
  sidecar_research_posts(topic, hours, max_results)  — pull live X posts

These return whatever the sidecar returns. Callers never see keys.
"""
from __future__ import annotations
import json
import os
import socket as sock_mod
from pathlib import Path

STATE_DIR = Path.home() / ".local" / "state" / "pliny"
SOCKET_PATH = str(STATE_DIR / "secrets.sock")
TOKEN_PATH = str(STATE_DIR / "session.token")
DEFAULT_TIMEOUT = 240.0  # generous — research_posts can take a while

# Legacy fallback location (older sidecar version put the socket in /tmp).
_LEGACY_SOCKET = "/tmp/.pliny_secrets.sock"


def _load_token() -> str:
    """Read the per-sidecar-lifetime session token. Caller readability is
    enforced by the token file's 0600 mode + 0700 parent dir."""
    try:
        with open(TOKEN_PATH, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def _connect(timeout: float):
    """Try the canonical socket path first, then the legacy /tmp path."""
    last_err: Exception | None = None
    for path in (SOCKET_PATH, _LEGACY_SOCKET):
        try:
            s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(path)
            return s, None
        except FileNotFoundError as e:
            last_err = e
            continue
        except Exception as e:
            return None, e
    return None, last_err


def _call(req: dict, *, timeout: float = DEFAULT_TIMEOUT) -> dict:
    # Auto-attach token for everything except ping (which is public).
    if req.get("action") != "ping" and "token" not in req:
        tok = _load_token()
        if tok:
            req = {**req, "token": tok}

    s, err = _connect(timeout)
    if s is None:
        if isinstance(err, FileNotFoundError):
            return {"ok": False, "error": (
                "sidecar not running. start: python3 pliny_secrets_sidecar.py & "
                "(or install the launchd plist)"
            )}
        return {"ok": False, "error": f"sidecar connect: {err}"}

    try:
        payload = json.dumps(req).encode("utf-8")
        s.sendall(len(payload).to_bytes(4, "big") + payload)
        hdr = _recv_exact(s, 4)
        if not hdr:
            return {"ok": False, "error": "sidecar closed connection"}
        length = int.from_bytes(hdr, "big")
        body = _recv_exact(s, length)
        return json.loads(body.decode("utf-8"))
    except sock_mod.timeout:
        return {"ok": False, "error": f"sidecar timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": f"sidecar io: {e}"}
    finally:
        try:
            s.close()
        except Exception:
            pass


def _recv_exact(s, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            return b""
        buf += chunk
    return buf


def sidecar_ping() -> dict:
    return _call({"action": "ping"}, timeout=5)


def sidecar_providers() -> dict:
    return _call({"action": "providers"}, timeout=5)


def sidecar_oauth_status() -> dict:
    """Report which Hermes OAuth providers are logged in. No tokens returned."""
    return _call({"action": "oauth_status"}, timeout=15)


def sidecar_draft_tweets(context: str, *, n: int = 5,
                         use_xai: bool = False) -> dict:
    return _call({
        "action": "draft_tweets",
        "context": context, "n": n, "use_xai": use_xai,
    })


def sidecar_research_posts(topic: str, *, hours: int = 24,
                           max_results: int = 10) -> dict:
    return _call({
        "action": "research_posts",
        "topic": topic, "hours": hours, "max_results": max_results,
    })
