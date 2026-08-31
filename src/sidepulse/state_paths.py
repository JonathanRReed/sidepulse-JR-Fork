"""Stdlib-only compatibility paths shared by hook clients and the app."""

from __future__ import annotations

import os
from pathlib import Path


def default_state_dir(home: Path | None = None) -> Path:
    if home is None:
        xdg_state_home = os.environ.get("XDG_STATE_HOME")
        if xdg_state_home:
            return Path(xdg_state_home).expanduser() / "sidepulse" / "agent-monitor"

    base = home or Path.home()
    return base / ".local" / "state" / "sidepulse" / "agent-monitor"


def candidate_state_dirs(home: Path | None = None) -> tuple[Path, ...]:
    if home is not None:
        return (default_state_dir(home).expanduser(),)

    candidates = (
        default_state_dir().expanduser(),
        default_state_dir(Path.home()).expanduser(),
    )
    return tuple(dict.fromkeys(candidates))


__all__ = ["candidate_state_dirs", "default_state_dir"]
