#!/usr/bin/env bash
# ─── PLINY COMMAND — installer ───────────────────────────────────────────────
# Idempotent. Safe to run multiple times. Does:
#   1. Verify Python 3.9+
#   2. Install pyyaml (only hard dep) into the active Python
#   3. Create runtime dirs (state/, sessions/, logs/, rt_library/)
#   4. Seed rt_library/ with starter gauntlet configs from setup/ if empty
#   5. Drop a .env from the template if missing
#   6. Print next steps
#
# Usage:  ./install.sh         (interactive — prompts for missing keys)
#         ./install.sh --quiet (no prompts, just install)
#
set -euo pipefail

# colors that work on dark and light terminals
if [[ -t 1 ]]; then
  RED=$'\e[1;31m'; GRN=$'\e[1;32m'; YLW=$'\e[1;33m'; CYN=$'\e[1;36m'; DIM=$'\e[2m'; RST=$'\e[0m'
else
  RED=''; GRN=''; YLW=''; CYN=''; DIM=''; RST=''
fi

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

cd "$(dirname "$0")"
ROOT="$(pwd)"

banner() { echo "${CYN}${1}${RST}"; }
ok()     { echo "  ${GRN}✓${RST} $1"; }
warn()   { echo "  ${YLW}!${RST} $1"; }
fail()   { echo "  ${RED}✗${RST} $1"; exit 1; }
step()   { echo; echo "${CYN}▸${RST} ${1}"; }

banner "
╭─────────────────────────────────────────────╮
│  🐉  PLINY COMMAND — installer              │
│      Mission Control for sovereign agents   │
╰─────────────────────────────────────────────╯
"

# ─── 1. Python ───────────────────────────────────────────────────────────────
step "Checking Python"
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    if [[ -n "$ver" ]]; then
      major="${ver%%.*}"; minor="${ver##*.}"
      if [[ "$major" -ge 3 && ( "$major" -gt 3 || "$minor" -ge 9 ) ]]; then
        PY="$cand"
        ok "found $cand (v$ver)"
        break
      fi
    fi
  fi
done
[[ -z "$PY" ]] && fail "Python 3.9+ not found. Install from https://www.python.org/downloads/"

# ─── 2. pyyaml ───────────────────────────────────────────────────────────────
step "Installing pyyaml (the only hard dependency)"
if "$PY" -c 'import yaml' 2>/dev/null; then
  ok "pyyaml already present"
else
  if "$PY" -m pip install --quiet pyyaml 2>/dev/null; then
    ok "pyyaml installed"
  elif "$PY" -m pip install --user --quiet pyyaml 2>/dev/null; then
    ok "pyyaml installed (--user)"
  else
    warn "pyyaml install failed — gauntlet presets will be disabled"
    warn "you can fix this later with:  $PY -m pip install pyyaml"
  fi
fi

# ─── 3. Runtime directories ──────────────────────────────────────────────────
step "Creating runtime directories"
for d in state sessions logs rt_library rt_library/gauntlets/runs rt_library/artifacts; do
  if [[ ! -d "$d" ]]; then
    mkdir -p "$d"
    ok "created $d/"
  else
    ok "$d/ exists"
  fi
done

# ─── 4. Seed gauntlet configs ────────────────────────────────────────────────
step "Seeding gauntlet starter configs"
SEED="setup/starter_rt_library"
if [[ ! -d "$SEED" ]]; then
  warn "$SEED not found — skipping seed"
else
  for src in "$SEED"/gauntlets/targets/*.yml \
             "$SEED"/gauntlets/behaviors/*.yml \
             "$SEED"/gauntlets/presets/*.yml; do
    [[ -f "$src" ]] || continue
    rel="${src#$SEED/}"
    dst="rt_library/$rel"
    if [[ -e "$dst" ]]; then
      ok "kept existing $rel"
    else
      mkdir -p "$(dirname "$dst")"
      cp "$src" "$dst"
      ok "seeded $rel"
    fi
  done
fi

# ─── 5. .env from template ───────────────────────────────────────────────────
step "Setting up .env"
if [[ -f .env ]]; then
  ok ".env already exists — leaving it alone"
else
  if [[ -f setup/.env.example ]]; then
    cp setup/.env.example .env
    ok "wrote .env from template — fill in your API keys"
  else
    warn "setup/.env.example missing"
  fi
fi

# ─── 6. Optional: claude CLI for the anthropic target ────────────────────────
step "Optional dependencies"
if command -v claude >/dev/null 2>&1; then
  ok "claude CLI present (anthropic target enabled)"
else
  warn "claude CLI not found — anthropic target via local sandbox disabled"
  warn "install from: https://docs.claude.com/en/docs/claude-code"
fi

# ─── Done ────────────────────────────────────────────────────────────────────
echo
banner "
╭─────────────────────────────────────────────╮
│  ✅  Install complete                       │
╰─────────────────────────────────────────────╯
"
echo "${DIM}Next steps:${RST}"
echo "  1. Edit ${CYN}.env${RST} and set at least ${CYN}OPENROUTER_API_KEY${RST}"
echo "  2. Launch:   ${GRN}./quickstart.sh${RST}   or   ${GRN}make run${RST}"
echo "  3. Open:     ${CYN}http://localhost:8888/redteam${RST}"
echo
