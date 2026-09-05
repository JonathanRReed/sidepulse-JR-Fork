#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${SIDEPULSE_DEV_VENV:-${VENV_DIR:-$ROOT_DIR/.venv}}"
CONSTRAINTS="$ROOT_DIR/requirements/release-constraints.txt"
PINNED_PIP="26.1.2"

select_python() {
    local candidates=()
    if [ -n "${PYTHON:-}" ]; then
        candidates=("$PYTHON")
    else
        candidates=(
            python3.12
            /opt/homebrew/bin/python3.12
            /usr/local/bin/python3.12
        )
    fi

    local candidate resolved
    for candidate in "${candidates[@]}"; do
        resolved="$(command -v "$candidate" 2>/dev/null || true)"
        if [ -z "$resolved" ]; then
            continue
        fi
        if "$resolved" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))' 2>/dev/null; then
            printf '%s\n' "$resolved"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(select_python || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "JR-Bar development and release tooling requires Python 3.12." >&2
    echo "Install Homebrew Python 3.12 or set PYTHON to that interpreter." >&2
    exit 2
fi
if [ ! -f "$CONSTRAINTS" ]; then
    echo "Missing reviewed dependency constraints: $CONSTRAINTS" >&2
    exit 2
fi

if [ -x "$VENV_DIR/bin/python" ]; then
    if ! "$VENV_DIR/bin/python" -c \
        'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))' 2>/dev/null; then
        echo "The existing virtual environment at $VENV_DIR does not use Python 3.12." >&2
        echo "Choose a new SIDEPULSE_DEV_VENV or remove and recreate that environment." >&2
        exit 2
    fi
else
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install "pip==$PINNED_PIP"
"$VENV_DIR/bin/python" -m pip install \
    --constraint "$CONSTRAINTS" \
    --editable "$ROOT_DIR[dev]"
"$VENV_DIR/bin/python" -m pip check
printf 'Development environment ready: %s (%s)\n' \
    "$VENV_DIR" "$("$VENV_DIR/bin/python" -V 2>&1)"
