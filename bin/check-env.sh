#!/usr/bin/env bash
# check-env.sh — Verify the MSA environment is correctly configured before running.
#
# Checks:
#   - Python virtualenv exists and is activated
#   - config/config.yaml and config/rules.md are present
#   - Required API keys are set for the configured backend
#   - Slack tokens are set when scheduler mode is slack
#   - Optional: ultralytics is importable (for yolo_detect)
#
# Exit code 0 = all required checks passed (warnings are non-fatal).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

ERRORS=0
WARNINGS=0

pass() { echo "  [OK]   $1"; }
fail() { echo "  [FAIL] $1"; ERRORS=$((ERRORS + 1)); }
warn() { echo "  [WARN] $1"; WARNINGS=$((WARNINGS + 1)); }

echo "=== MSA Environment Check ==="
echo ""

# ── Python environment ──────────────────────────────────────────────────────
echo "Python environment:"
if [[ -d "$PROJECT_ROOT/.venv" ]]; then
    pass ".venv directory exists"
else
    fail ".venv not found — run: bin/install.sh"
fi

if [[ "${VIRTUAL_ENV:-}" == "$PROJECT_ROOT/.venv" ]]; then
    pass "virtualenv is activated"
else
    warn "virtualenv does not appear to be activated — run: source .venv/bin/activate"
fi

# ── Configuration files ─────────────────────────────────────────────────────
echo ""
echo "Configuration:"
if [[ -f "$PROJECT_ROOT/config/config.yaml" ]]; then
    pass "config/config.yaml exists"
else
    fail "config/config.yaml not found"
fi

if [[ -f "$PROJECT_ROOT/config/rules.md" ]]; then
    pass "config/rules.md exists"
else
    fail "config/rules.md not found"
fi

# Parse backend and scheduler mode from config
BACKEND=$(grep -m1 'backend:' "$PROJECT_ROOT/config/config.yaml" 2>/dev/null | awk '{print $2}' | tr -d '"' || true)
BACKEND="${BACKEND:-anthropic}"

# scheduler can be a bare string or under a mode: key
SCHEDULER=$(grep -m1 '^\s*mode:' "$PROJECT_ROOT/config/config.yaml" 2>/dev/null | awk '{print $2}' | tr -d '"' || true)
if [[ -z "$SCHEDULER" ]]; then
    SCHEDULER=$(grep -m1 '^scheduler:' "$PROJECT_ROOT/config/config.yaml" 2>/dev/null | awk '{print $2}' | tr -d '"' || true)
fi
SCHEDULER="${SCHEDULER:-interval}"

# ── API keys ────────────────────────────────────────────────────────────────
echo ""
echo "API keys (backend: $BACKEND):"
case "$BACKEND" in
    anthropic)
        if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
            fail "ANTHROPIC_API_KEY is not set"
        elif [[ "${ANTHROPIC_API_KEY}" == sk-ant-* ]]; then
            pass "ANTHROPIC_API_KEY is set"
        else
            warn "ANTHROPIC_API_KEY is set but does not look like an Anthropic key (expected sk-ant-...)"
        fi
        ;;
    vllm|ollama)
        pass "No API key required for $BACKEND backend"
        ;;
    *)
        warn "Unknown backend '$BACKEND' — cannot verify API key requirement"
        ;;
esac

# ── Scheduler / Slack tokens ────────────────────────────────────────────────
echo ""
echo "Scheduler (mode: $SCHEDULER):"
if [[ "$SCHEDULER" == "slack" ]]; then
    if [[ -n "${SLACK_BOT_TOKEN:-}" ]]; then
        pass "SLACK_BOT_TOKEN is set"
    else
        fail "SLACK_BOT_TOKEN is not set (required for slack scheduler)"
    fi
    if [[ -n "${SLACK_APP_TOKEN:-}" ]]; then
        pass "SLACK_APP_TOKEN is set"
    else
        fail "SLACK_APP_TOKEN is not set (required for slack scheduler)"
    fi
else
    pass "Slack tokens not required for '$SCHEDULER' mode"
fi

# ── Optional dependencies ────────────────────────────────────────────────────
echo ""
echo "Optional dependencies:"
if python3 -c "import ultralytics" 2>/dev/null; then
    pass "ultralytics is installed (yolo_detect tool available)"
else
    warn "ultralytics not installed — yolo_detect will fail if called (pip install ultralytics)"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
if [[ $ERRORS -gt 0 ]]; then
    echo "Result: $ERRORS error(s), $WARNINGS warning(s). Fix errors before running the agent."
    exit 1
else
    echo "Result: OK — $WARNINGS warning(s). Environment looks good."
    exit 0
fi
