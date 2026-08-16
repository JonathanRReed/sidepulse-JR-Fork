"""Hardened facade for versioned optional-integration settings."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

from . import _integration_settings_legacy as _legacy

_ORIGINAL_SAVE_INTEGRATION_SETTINGS = _legacy.save_integration_settings


def save_integration_settings(
    settings,
    path: Path | None = None,
    *,
    loaded=None,
) -> Path:
    """Refuse malformed existing state instead of replacing it implicitly."""
    if type(settings) is not _legacy.IntegrationSettings:
        raise _legacy.IntegrationSettingsError("invalid integration settings")
    try:
        return _ORIGINAL_SAVE_INTEGRATION_SETTINGS(
            settings,
            path,
            loaded=loaded,
        )
    except _legacy.IntegrationSettingsWriteRefusedError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise _legacy.IntegrationSettingsWriteRefusedError(
            "existing integration settings cannot be preserved safely"
        ) from exc


_legacy.save_integration_settings = save_integration_settings

for _name in dir(_legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_legacy, _name)


class _IntegrationSettingsFacade(ModuleType):
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
        return sorted(set(super().__dir__()) | set(dir(_legacy)))


__all__ = tuple(sorted(name for name in globals() if not name.startswith("_")))
_facade_module = sys.modules[__name__]
_facade_module.__class__ = _IntegrationSettingsFacade
