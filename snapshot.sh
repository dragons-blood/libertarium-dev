#!/bin/bash
# PLINY COMMAND — Snapshot Tool
# Takes a versioned zip snapshot of the codebase (excludes sessions/state/tmp)
# Usage: ./snapshot.sh [label]
# Example: ./snapshot.sh "before-lair-refactor"

SNAP_DIR="$HOME/Desktop/pliny-snapshots"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DATE=$(date +%Y-%m-%d_%H%M)
LABEL="${1:-snapshot}"
FILENAME="pliny-command_${LABEL}_${DATE}.zip"

mkdir -p "$SNAP_DIR"

cd "$SRC_DIR"
zip -r "$SNAP_DIR/$FILENAME" . \
  -x "./sessions/*" \
  -x "./state/*" \
  -x "./.git/*" \
  -x "./__pycache__/*" \
  -x "./.DS_Store" \
  -x "./*.pyc"

echo ""
echo "Snapshot saved: $SNAP_DIR/$FILENAME"
echo "Size: $(du -h "$SNAP_DIR/$FILENAME" | cut -f1)"
echo ""
echo "All snapshots:"
ls -lh "$SNAP_DIR"/pliny-command_*.zip | awk '{print "  " $NF " (" $5 ")"}'
