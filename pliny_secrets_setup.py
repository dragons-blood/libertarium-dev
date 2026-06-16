#!/usr/bin/env python3
"""Stupid-simple secrets setup for Pliny.

Stores API keys in the macOS Keychain (encrypted, login-password derived).
No file ever holds the keys. No shell history. Hidden prompts.

Usage:
    python3 pliny_secrets_setup.py            # interactive
    python3 pliny_secrets_setup.py --list     # show what's configured
    python3 pliny_secrets_setup.py --remove xai
    python3 pliny_secrets_setup.py --provider xai   # set just one

After setup, the sidecar daemon (pliny_secrets_sidecar.py) loads them at its
own startup and holds them in RAM. Agents talk to the sidecar over a Unix
socket — they never see the keys. See pliny_secrets_sidecar.py for the API.
"""
from __future__ import annotations
import argparse
import getpass
import os
import subprocess
import sys

PROVIDERS = [
    ("xai",         "xAI (Grok) — for x_search + Grok models",  "xai-..."),
    ("openrouter",  "OpenRouter — multi-model gateway",          "sk-or-..."),
    ("anthropic",   "Anthropic — Claude API",                    "sk-ant-..."),
    ("openai",      "OpenAI — GPT models",                       "sk-..."),
    ("google",      "Google — Gemini API",                       "AI..."),
    ("mistral",     "Mistral",                                   "..."),
    ("deepseek",    "DeepSeek",                                  "..."),
    ("cohere",      "Cohere",                                    "..."),
    ("github_pat",  "GitHub Personal Access Token",              "ghp_..."),
]

SERVICE_PREFIX = "pliny/"
USER = os.getenv("USER") or os.getenv("LOGNAME") or "user"


def _kc_set(provider: str, value: str) -> bool:
    """Store key in Keychain. -U updates if exists. value never echoed."""
    service = SERVICE_PREFIX + provider
    r = subprocess.run(
        ["security", "add-generic-password",
         "-a", USER, "-s", service, "-w", value, "-U",
         "-T", "/usr/bin/security",  # only `security` CLI may read without prompt
         "-T", sys.executable],       # plus the python that will run sidecar
        capture_output=True, text=True,
    )
    return r.returncode == 0


def _kc_has(provider: str) -> bool:
    """Check if a key is stored — does NOT retrieve the value (no -w)."""
    service = SERVICE_PREFIX + provider
    r = subprocess.run(
        ["security", "find-generic-password", "-a", USER, "-s", service],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def _kc_remove(provider: str) -> bool:
    service = SERVICE_PREFIX + provider
    r = subprocess.run(
        ["security", "delete-generic-password", "-a", USER, "-s", service],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def _prompt_key(provider: str, label: str, placeholder: str) -> str | None:
    """Hidden-input prompt. Returns None if user pressed Enter (skip)."""
    existing = "configured ✓" if _kc_has(provider) else "not set"
    print(f"\n  {label}")
    print(f"  Service: pliny/{provider}    Status: {existing}    Format: {placeholder}")
    try:
        val = getpass.getpass(f"  Paste key (hidden — Enter to skip): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n  ↳ aborted, no change\n")
        return None
    return val or None


def cmd_interactive() -> int:
    print("━" * 72)
    print("  PLINY SECRETS SETUP")
    print("  Keys go straight to macOS Keychain. Nothing is logged or echoed.")
    print("  Press Enter to skip any provider you don't have a key for.")
    print("━" * 72)
    set_count = 0
    skip_count = 0
    for provider, label, placeholder in PROVIDERS:
        val = _prompt_key(provider, label, placeholder)
        if val is None:
            skip_count += 1
            continue
        if _kc_set(provider, val):
            print(f"  ✓ stored in Keychain as pliny/{provider}")
            set_count += 1
        else:
            print(f"  ✗ failed to store pliny/{provider} — try running with -v")
        # Wipe local reference immediately
        del val
    print()
    print("━" * 72)
    print(f"  Done. Stored {set_count} new/updated, skipped {skip_count}.")
    print("  Configured providers:")
    for provider, label, _ in PROVIDERS:
        mark = "✓" if _kc_has(provider) else " "
        print(f"   [{mark}] pliny/{provider}  — {label}")
    print()
    print("  Next: start the sidecar if it's not already running:")
    print("    python3 pliny_secrets_sidecar.py &")
    print("  Or install the launchd plist (see scripts/com.pliny.secrets-sidecar.plist).")
    print("━" * 72)
    return 0


def cmd_list() -> int:
    print("Configured providers (pliny/<name> in Keychain):")
    for provider, label, _ in PROVIDERS:
        mark = "✓" if _kc_has(provider) else " "
        print(f"  [{mark}] {provider}  — {label}")
    return 0


def cmd_remove(provider: str) -> int:
    if _kc_remove(provider):
        print(f"  ✓ removed pliny/{provider}")
        return 0
    print(f"  ✗ no entry for pliny/{provider}")
    return 1


def cmd_single(provider: str) -> int:
    match = next((p for p in PROVIDERS if p[0] == provider), None)
    if not match:
        # Allow custom provider names too
        match = (provider, f"custom provider {provider}", "...")
    val = _prompt_key(*match)
    if val is None:
        return 0
    ok = _kc_set(provider, val)
    del val
    print(f"  {'✓ stored' if ok else '✗ failed'}: pliny/{provider}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Pliny secrets setup (macOS Keychain)")
    ap.add_argument("--list", action="store_true", help="show configured providers")
    ap.add_argument("--remove", metavar="PROVIDER", help="delete a stored key")
    ap.add_argument("--provider", metavar="NAME",
                    help="set just one provider (skips full interactive flow)")
    args = ap.parse_args()

    if sys.platform != "darwin":
        print("This tool requires macOS Keychain. On Linux/Windows, use a different vault.")
        return 1

    if args.list:
        return cmd_list()
    if args.remove:
        return cmd_remove(args.remove)
    if args.provider:
        return cmd_single(args.provider)
    return cmd_interactive()


if __name__ == "__main__":
    sys.exit(main())
