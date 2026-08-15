#!/bin/bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_ROOT="${SIDEPULSE_INSTALL_ROOT:-$HOME/.local/share/sidepulse}"
BIN_DIR="${SIDEPULSE_BIN_DIR:-$HOME/.local/bin}"
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$INSTALL_ROOT/venv"

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "SidePulse requires Python 3.10+. Set PYTHON_BIN to a supported interpreter." >&2
    exit 2
fi

mkdir -p "$INSTALL_ROOT" "$BIN_DIR"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install --upgrade "$SOURCE_DIR"

for command in sidepulse agent-monitor agent-status-bar; do
    ln -sfn "$VENV_DIR/bin/$command" "$BIN_DIR/$command"
done

printf '%s\n' "SidePulse installed in $VENV_DIR"
printf '%s\n' "Commands linked in $BIN_DIR"
printf '%s\n' "Run: $BIN_DIR/sidepulse setup"
