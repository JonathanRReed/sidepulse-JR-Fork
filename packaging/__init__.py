"""Local packaging helpers used by SidePulse verification.

Keep this directory importable as ``packaging.*`` for repo-local tests while
also exposing the third-party ``packaging`` dependency submodules used by build
tools.
"""

from __future__ import annotations

from pathlib import Path
from pkgutil import extend_path
from typing import Final

__path__ = extend_path(__path__, __name__)  # type: ignore[var-annotated]
LOCAL_PATH: Final = Path(__file__).resolve().parent
