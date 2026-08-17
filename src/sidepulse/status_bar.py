"""Public facade for SidePulse's single production AppKit controller.

All controller behavior lives in ``_status_bar_production``. This module keeps
legacy imports, monkeypatches, direct module execution, and source
introspection compatible without defining or rebinding another Objective-C
subclass.
"""

from __future__ import annotations

import sys
from types import ModuleType

from . import _status_bar_production as _production

_legacy = _production._legacy
JRStatusBarController = _production.JRStatusBarController
StatusBarController = JRStatusBarController


class _StatusBarFacade(ModuleType):
    """Forward reads and monkeypatches to the retained runtime module."""

    def __getattr__(self, name: str):
        if hasattr(_production, name):
            return getattr(_production, name)
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
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in {"__all__", "__class__"} or name.startswith("_facade_"):
            super().__delattr__(name)
            return
        if hasattr(_legacy, name):
            delattr(_legacy, name)
        if name in self.__dict__:
            super().__delattr__(name)

    def __dir__(self) -> list[str]:
        return sorted(
            set(super().__dir__()) | set(dir(_production)) | set(dir(_legacy))
        )


__all__ = tuple(
    sorted(
        {name for name in dir(_legacy) if not name.startswith("_")}
        | {"JRStatusBarController", "StatusBarController"}
    )
)
_facade_module = sys.modules[__name__]
_facade_module.__class__ = _StatusBarFacade
_facade_module.__file__ = _legacy.__file__


if __name__ == "__main__":
    raise SystemExit(_legacy.main())
