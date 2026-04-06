#!/usr/bin/env bash
# run.sh — Activate the virtualenv and run the agent.
#
# Handles venv activation automatically so the caller does not need to
# remember to source .venv/bin/activate before running.
#
# Usage:
#   bin/run.sh              Run one agent cycle and exit  (--once)
#   bin/run.sh --schedule   Run continuously on the configured schedule

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

SCHEDULE=false

usage() {
    echo "Usage: $(basename "$0") [--schedule]"
    echo "  (no flag)    Run one agent cycle and exit"
    echo "  --schedule   Run continuously on the configured schedule"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --schedule|-s) SCHEDULE=true; shift ;;
        --help|-h) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

# Activate venv if not already active
if [[ "${VIRTUAL_ENV:-}" != "$PROJECT_ROOT/.venv" ]]; then
    if [[ ! -f "$PROJECT_ROOT/.venv/bin/activate" ]]; then
        echo "ERROR: .venv not found."
        echo "Run: bin/install.sh"
        exit 1
    fi
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

cd "$PROJECT_ROOT"

if $SCHEDULE; then
    exec python3 -m msa.agent --schedule
else
    exec python3 -m msa.agent --once
fi
