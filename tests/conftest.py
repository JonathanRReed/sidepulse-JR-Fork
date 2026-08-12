"""Repo root on sys.path so `examples.*` imports resolve no matter how
pytest is invoked (`pytest tests/` from the venv script does not add the
current directory the way `python -m pytest` does)."""

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
