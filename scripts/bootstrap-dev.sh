#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${SIDEPULSE_DEV_VENV:-${VENV_DIR:-$ROOT_DIR/.venv}}"

select_python() {
    local candidates=()
    if [ -n "${PYTHON:-}" ]; then
        candidates=("$PYTHON")
    else
        candidates=(
            python3.13
            python3.12
            python3.11
            python3.10
            /opt/homebrew/bin/python3
            /usr/local/bin/python3
            python3
        )
    fi

    local candidate resolved
    for candidate in "${candidates[@]}"; do
        resolved="$(command -v "$candidate" 2>/dev/null || true)"
        if [ -z "$resolved" ]; then
            continue
        fi
        if "$resolved" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>/dev/null; then
            printf '%s\n' "$resolved"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(select_python || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "SidePulse requires Python 3.10+. Install Homebrew Python 3.13 or set PYTHON." >&2
    exit 2
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -e "$ROOT_DIR[dev]"
printf 'Development environment ready: %s (%s)\n' \
    "$VENV_DIR" "$("$VENV_DIR/bin/python" -V 2>&1)"
