#!/usr/bin/env bash
# ─── PLINY COMMAND — one-command quickstart ─────────────────────────────────
# Runs install.sh (idempotent), sources .env if present, then launches the
# server and opens the dashboard.
#
# Usage:  ./quickstart.sh
#
set -euo pipefail
cd "$(dirname "$0")"

if [[ -t 1 ]]; then
  GRN=$'\e[1;32m'; CYN=$'\e[1;36m'; YLW=$'\e[1;33m'; DIM=$'\e[2m'; RST=$'\e[0m'
else
  GRN=''; CYN=''; YLW=''; DIM=''; RST=''
fi

# 1. Install (idempotent — ok to re-run)
if [[ ! -f .env ]] || [[ ! -d rt_library/gauntlets/presets ]]; then
  echo "${CYN}First run — installing…${RST}"
  ./install.sh --quiet
fi

# 2. Source .env so the server inherits API keys
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  echo "${GRN}✓${RST} loaded .env"
else
  echo "${YLW}!${RST} no .env — server will start without provider keys"
fi

# 3. Pick the right Python
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

# 4. If a server is already on :8888, kindly ask the user before stomping it
if command -v lsof >/dev/null 2>&1 && lsof -ti :8888 >/dev/null 2>&1; then
  echo "${YLW}⚠${RST}  Port 8888 is already in use."
  echo "    Stop the existing server with: ${CYN}make stop${RST}"
  echo "    Or visit: ${CYN}http://localhost:8888/redteam${RST}"
  exit 1
fi

# 5. Launch
echo
echo "${GRN}🐉  starting Pliny Command on http://localhost:8888${RST}"
echo "${DIM}    (Ctrl-C to stop)${RST}"
echo

# Open the browser shortly after launch (non-blocking)
(
  sleep 1
  if command -v open >/dev/null 2>&1; then open http://localhost:8888/redteam
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open http://localhost:8888/redteam
  fi
) &

exec "$PY" server.py
