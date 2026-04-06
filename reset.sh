#!/usr/bin/env bash
# This script has moved to bin/reset.sh.
# It now supports --clean and --template flags and reads from scratchpads/active.*.yaml templates.
exec "$(dirname "$0")/bin/reset.sh" "$@"
