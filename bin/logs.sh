#!/usr/bin/env bash
# logs.sh — Show the most recent agent cycle log.
#
# Usage:
#   bin/logs.sh        Print the most recent cycle log
#   bin/logs.sh -f     Follow (tail -f) the most recent cycle log
#   bin/logs.sh -n 3   List the 3 most recent logs and print the latest

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOGS_DIR="$PROJECT_ROOT/logs"

FOLLOW=false
LIST_N=1

usage() {
    echo "Usage: $(basename "$0") [-f] [-n N]"
    echo "  -f      Follow the latest log (tail -f)"
    echo "  -n N    Show the N most recent log filenames before printing the latest"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--follow) FOLLOW=true; shift ;;
        -n) LIST_N="$2"; shift 2 ;;
        --help|-h) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

mapfile -t LOGS < <(ls -t "$LOGS_DIR"/cycle_*.log 2>/dev/null)
if [[ ${#LOGS[@]} -eq 0 ]]; then
    echo "No cycle logs found in $LOGS_DIR"
    exit 1
fi

if [[ $LIST_N -gt 1 ]]; then
    echo "Recent logs:"
    for ((i=0; i<LIST_N && i<${#LOGS[@]}; i++)); do
        echo "  ${LOGS[$i]}"
    done
    echo ""
fi

LATEST="${LOGS[0]}"
echo "==> $LATEST"
echo ""
if $FOLLOW; then
    tail -f "$LATEST"
else
    cat "$LATEST"
fi
