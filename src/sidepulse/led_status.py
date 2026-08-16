"""Compatibility facade for the historical LED renderer."""

from __future__ import annotations

from . import _led_status_legacy as _legacy

for _name in dir(_legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_legacy, _name)

__all__ = tuple(sorted(name for name in globals() if not name.startswith("_")))
