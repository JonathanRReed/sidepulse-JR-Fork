#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${SIDEPULSE_VENV:-$ROOT/.venv}"
BOOTSTRAP=1
RUN_BUILD=1
RUN_FULL_TESTS=1

usage() {
  cat <<'EOF'
Usage: scripts/verify.sh [--no-bootstrap] [--no-build] [--targeted]

  --no-bootstrap  Use an existing virtual environment without installing.
  --no-build      Skip wheel and source-distribution validation.
  --targeted      Run the rescue regression tests instead of the complete suite.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-bootstrap) BOOTSTRAP=0 ;;
    --no-build) RUN_BUILD=0 ;;
    --targeted) RUN_FULL_TESTS=0 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "$BOOTSTRAP" -eq 1 ]]; then
  "$ROOT/scripts/bootstrap-dev.sh"
fi

PYTHON="$VENV/bin/python"
RUFF="$VENV/bin/ruff"

if [[ ! -x "$PYTHON" || ! -x "$RUFF" ]]; then
  printf 'Development environment missing. Run scripts/bootstrap-dev.sh first.\n' >&2
  exit 2
fi

cd "$ROOT"
"$RUFF" check src tests

if [[ "$RUN_FULL_TESTS" -eq 1 ]]; then
  if [[ "$(uname -s)" != "Darwin" ]]; then
    printf 'The complete SidePulse suite requires macOS/PyObjC. Use --targeted off-macOS.\n' >&2
    exit 2
  fi
  "$PYTHON" -m pytest tests/ -q
else
  "$PYTHON" -m pytest tests/test_device_projection.py tests/test_packaging_contract.py -q
fi

if [[ "$RUN_BUILD" -eq 1 ]]; then
  "$PYTHON" -m pip install --quiet build twine
  rm -rf build dist
  "$PYTHON" -m build
  "$PYTHON" -m twine check dist/*
fi
