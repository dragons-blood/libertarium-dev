#!/usr/bin/env bash
# Install + start the Pliny secrets sidecar as a launchd agent.
# Idempotent: safe to re-run.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="$REPO/scripts/com.pliny.secrets-sidecar.plist"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
PLIST_DST="$LAUNCH_DIR/com.pliny.secrets-sidecar.plist"
LABEL="com.pliny.secrets-sidecar"

mkdir -p "$LAUNCH_DIR"
cp "$PLIST_SRC" "$PLIST_DST"

# Unload if already loaded (ignore failure on first run)
launchctl unload "$PLIST_DST" 2>/dev/null || true

launchctl load -w "$PLIST_DST"

# Quick health check
sleep 2
if [ -S /tmp/.pliny_secrets.sock ]; then
  echo "✓ sidecar running, socket at /tmp/.pliny_secrets.sock"
else
  echo "✗ socket not present after 2s — check /tmp/pliny_secrets_sidecar.log"
  tail -20 /tmp/pliny_secrets_sidecar.log 2>/dev/null || true
  exit 1
fi

# Ping
echo "Pinging sidecar..."
python3 -c "from pliny_secrets_client import sidecar_ping; import json; print(json.dumps(sidecar_ping(), indent=2))"

echo ""
echo "Installed: $PLIST_DST"
echo "Label: $LABEL"
echo ""
echo "To stop:    launchctl unload $PLIST_DST"
echo "To restart: launchctl kickstart -k gui/\$UID/$LABEL"
echo "Logs:       tail -f /tmp/pliny_secrets_sidecar.log"
