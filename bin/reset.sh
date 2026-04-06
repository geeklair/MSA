#!/usr/bin/env bash
# reset.sh — Reset the agent scratchpad and optionally clean old logs/snapshots.
#
# Usage:
#   bin/reset.sh                        Copy active.reset.yaml → active.yaml
#   bin/reset.sh --template yolo        Use active.yolo.yaml instead
#   bin/reset.sh --clean                Reset + remove old logs and snapshots
#   bin/reset.sh --clean --template yolo
#
# Available templates: any scratchpads/active.NAME.yaml file.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

CLEAN=false
TEMPLATE="reset"

usage() {
    echo "Usage: $(basename "$0") [--clean] [--template NAME]"
    echo ""
    echo "  --clean           Remove old cycle logs and scratchpad snapshots"
    echo "  --template NAME   Use scratchpads/active.NAME.yaml as the source (default: reset)"
    echo ""
    echo "Available templates:"
    for f in "$PROJECT_ROOT"/scratchpads/active.*.yaml; do
        [[ -f "$f" ]] || continue
        name=$(basename "$f" .yaml)
        name="${name#active.}"
        echo "    $name  ($f)"
    done
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --clean)      CLEAN=true; shift ;;
        --template)   TEMPLATE="$2"; shift 2 ;;
        --help|-h)    usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

TEMPLATE_FILE="$PROJECT_ROOT/scratchpads/active.${TEMPLATE}.yaml"
if [[ ! -f "$TEMPLATE_FILE" ]]; then
    echo "ERROR: Template not found: $TEMPLATE_FILE"
    echo "Run '$(basename "$0") --help' to list available templates."
    exit 1
fi

cp "$TEMPLATE_FILE" "$PROJECT_ROOT/scratchpads/active.yaml"
echo "Scratchpad reset from template: active.${TEMPLATE}.yaml"

if $CLEAN; then
    removed=0
    for f in "$PROJECT_ROOT"/logs/cycle_*.log \
             "$PROJECT_ROOT"/scratchpads/*_before.yaml \
             "$PROJECT_ROOT"/scratchpads/*_after.yaml; do
        if [[ -f "$f" ]]; then
            rm "$f"
            removed=$((removed + 1))
        fi
    done
    echo "Cleaned $removed old log/snapshot file(s)."
fi
