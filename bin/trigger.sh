#!/usr/bin/env bash
# trigger.sh — Create the file-watch trigger to fire one agent cycle.
#
# This is only useful when the scheduler is running in file_watch mode.
# The scheduler polls for tmp/msa_trigger every 2 seconds; when found,
# it deletes the file and runs one agent cycle.
#
# Usage:
#   bin/trigger.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TRIGGER="$PROJECT_ROOT/tmp/msa_trigger"

mkdir -p "$(dirname "$TRIGGER")"
touch "$TRIGGER"
echo "Trigger file created: $TRIGGER"
echo "The agent will fire on its next poll (within 2 seconds)."
