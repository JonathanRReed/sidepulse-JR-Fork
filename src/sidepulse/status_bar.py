"""Stable facade for SidePulse's historical AppKit controller.

The original controller grew into a single, very large module. It remains the
runtime implementation while deterministic decisions are extracted into small,
testable modules. This facade preserves the public ``sidepulse.status_bar``
module contract, including test monkeypatches of module globals, and replaces
only the per-device projection methods with the repaired implementation.
"""

from __future__ import annotations

import sys
from types import ModuleType

from . import status_bar_legacy as _legacy
from .device_projection import light_rows_for_provider, projection_for_provider


def _projected_rows_for_device(self, projection, device):
    provider_pin = self.settings.device_provider_pin(device.device_id)
    return light_rows_for_provider(projection, provider_pin)


def _projection_for_device(self, projection, device):
    provider_pin = self.settings.device_provider_pin(device.device_id)
    return projection_for_provider(projection, provider_pin)


# Repair the live controller at the narrowest boundary. Existing callers keep
# the same class object and all AppKit state remains owned by the legacy module.
_legacy.StatusBarController.projected_rows_for_device = _projected_rows_for_device
_legacy.StatusBarController.projection_for_device = _projection_for_device


class _StatusBarFacade(ModuleType):
    """Forward reads and test monkeypatches to the retained runtime module."""

    def __getattr__(self, name: str):
        return getattr(_legacy, name)

    def __setattr__(self, name: str, value) -> None:
        if name in {
            "__all__",
            "__class__",
            "__doc__",
            "__file__",
            "__loader__",
            "__name__",
            "__package__",
            "__path__",
            "__spec__",
        } or name.startswith("_facade_"):
            super().__setattr__(name, value)
            return
        setattr(_legacy, name, value)

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(dir(_legacy)))


__all__ = tuple(name for name in dir(_legacy) if not name.startswith("_"))
sys.modules[__name__].__class__ = _StatusBarFacade
