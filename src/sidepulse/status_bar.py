"""Stable facade for SidePulse's historical AppKit controller.

The original controller grew into a single, very large module. It remains the
runtime implementation while deterministic decisions are extracted into small,
testable modules. This facade preserves the public ``sidepulse.status_bar``
module contract, including test monkeypatches and source introspection.
"""

from __future__ import annotations

import sys
from types import ModuleType

from . import status_bar_legacy as _legacy
from .device_projection import light_rows_for_provider, projection_for_provider


# PyObjC registers Objective-C methods when a Python subclass is created. Do
# not assign methods onto an existing Cocoa class after creation. The reload
# guard reuses the registered subclass instead of attempting to define a second
# Objective-C class with the same process-global name.
if _legacy.StatusBarController.__name__ == "JRStatusBarController":
    JRStatusBarController = _legacy.StatusBarController
else:

    class JRStatusBarController(_legacy.StatusBarController):
        def projected_rows_for_device(self, projection, device):
            provider_pin = self.settings.device_provider_pin(device.device_id)
            return light_rows_for_provider(projection, provider_pin)

        def projection_for_device(self, projection, device):
            provider_pin = self.settings.device_provider_pin(device.device_id)
            return projection_for_provider(projection, provider_pin)

    _legacy.StatusBarController = JRStatusBarController


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

    def __delattr__(self, name: str) -> None:
        if name in {"__all__", "__class__"} or name.startswith("_facade_"):
            super().__delattr__(name)
            return
        delattr(_legacy, name)

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(dir(_legacy)))


__all__ = tuple(name for name in dir(_legacy) if not name.startswith("_"))
_facade_module = sys.modules[__name__]
_facade_module.__class__ = _StatusBarFacade
# Existing wiring tests and diagnostic tooling inspect status_bar.__file__ to
# verify that controller methods call their janitors and workers. Point source
# introspection at the retained implementation, just as attribute access does.
_facade_module.__file__ = _legacy.__file__


# ``python -m sidepulse.status_bar`` executes this facade, not the retained
# runtime module, so the entrypoint guard must live here.
if __name__ == "__main__":
    raise SystemExit(_legacy.main())
