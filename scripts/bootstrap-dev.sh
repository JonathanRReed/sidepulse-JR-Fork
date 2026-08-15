#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${SIDEPULSE_VENV:-$ROOT/.venv}"
PYTHON="${PYTHON:-python3}"

if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install --editable "$ROOT[test]"

printf 'SidePulse development environment: %s\n' "$VENV"
printf 'Python: '
"$VENV/bin/python" --version
