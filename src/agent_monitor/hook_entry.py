"""Compatibility launcher for installations that predate the sidepulse rename."""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

try:
    from sidepulse.hook import hook_log_main
except ImportError:
    from sidepulse.hook import hook_log_main


if __name__ == "__main__":
    raise SystemExit(hook_log_main())
