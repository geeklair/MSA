#!/usr/bin/env bash
# status.sh — Show the current agent scratchpad state and storage summary.
#
# Usage:
#   bin/status.sh       Print active.yaml and a summary of logs/snapshots on disk

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ACTIVE="$PROJECT_ROOT/scratchpads/active.yaml"

if [[ ! -f "$ACTIVE" ]]; then
    echo "No active scratchpad found at $ACTIVE"
    echo "Run 'bin/reset.sh' to create one."
    exit 1
fi

echo "=== MSA Scratchpad Status ==="
echo ""
cat "$ACTIVE"
echo ""

LOG_COUNT=$(ls "$PROJECT_ROOT/logs"/cycle_*.log 2>/dev/null | wc -l | tr -d ' ')
SNAP_COUNT=$(ls "$PROJECT_ROOT/scratchpads"/*_before.yaml 2>/dev/null | wc -l | tr -d ' ')

if [[ "$LOG_COUNT" -gt 0 ]]; then
    LATEST_LOG=$(ls -t "$PROJECT_ROOT/logs"/cycle_*.log | head -1)
    echo "--- $LOG_COUNT cycle log(s), $SNAP_COUNT snapshot pair(s) on disk ---"
    echo "    Latest log: $(basename "$LATEST_LOG")"
else
    echo "--- No cycle logs on disk yet ---"
fi
