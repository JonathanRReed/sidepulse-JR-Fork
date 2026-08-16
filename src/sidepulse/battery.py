"""Bounded battery API facade.

The historical implementation remains in :mod:`sidepulse._battery_legacy`.
This facade preserves its public model while making every external ``ioreg``
probe terminate within a strict deadline.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from . import _battery_legacy as _legacy

BATTERY_READ_TIMEOUT_SECONDS = 2.0
_ORIGINAL_READ_BATTERY_SNAPSHOT = _legacy.read_battery_snapshot


def read_battery_snapshot(
    *,
    full_charge_watts: float | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
):
    def bounded_runner(*args, **kwargs):
        kwargs.setdefault("timeout", BATTERY_READ_TIMEOUT_SECONDS)
        return runner(*args, **kwargs)

    return _ORIGINAL_READ_BATTERY_SNAPSHOT(
        full_charge_watts=full_charge_watts,
        runner=bounded_runner,
    )


_legacy.BATTERY_READ_TIMEOUT_SECONDS = BATTERY_READ_TIMEOUT_SECONDS
_legacy.read_battery_snapshot = read_battery_snapshot

for _name in dir(_legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_legacy, _name)

__all__ = tuple(sorted(name for name in globals() if not name.startswith("_")))
