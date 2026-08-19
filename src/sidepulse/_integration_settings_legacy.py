"""Versioned, explicit settings for the T3 Code integration."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from .private_io import atomic_private_write, read_private_text

INTEGRATION_SETTINGS_SCHEMA_VERSION = 2
INTEGRATION_SETTINGS_MAX_BYTES = 64 * 1024
INTEGRATION_NAMES = frozenset({"t3code"})
_RETIRED_CODEXBAR_KEYS = frozenset(
    {
        "codexbar_enabled",
        "codexbar_identity",
        "codexbar_connection_mode",
    }
)
_UNSET = object()


class IntegrationSettingsError(ValueError):
    """An integration settings document failed validation."""


class IntegrationSettingsWriteRefusedError(RuntimeError):
    """The current writer cannot safely replace the settings document."""


class IntegrationSettingsConcurrentWriteError(IntegrationSettingsWriteRefusedError):
    """The settings file changed after this process loaded it."""


@dataclass(frozen=True, slots=True)
class IntegrationSettingsCompatibility:
    source_version: int
    read_only: bool = False

    def __post_init__(self) -> None:
        if not (
            type(self.source_version) is int
            and self.source_version >= 1
            and type(self.read_only) is bool
        ):
            raise IntegrationSettingsError("invalid integration settings compatibility")


@dataclass(frozen=True, slots=True)
class IntegrationSettings:
    t3code_enabled: bool = False
    t3code_base_dir: str | None = None
    t3code_environment_id: str | None = None

    def __post_init__(self) -> None:
        if not (
            type(self.t3code_enabled) is bool
            and _optional_bounded_text(self.t3code_base_dir, 4096)
            and _optional_bounded_text(self.t3code_environment_id, 256)
        ):
            raise IntegrationSettingsError("invalid integration settings")

    def with_enabled(self, integration: str, enabled: bool) -> IntegrationSettings:
        if integration not in INTEGRATION_NAMES or type(enabled) is not bool:
            raise IntegrationSettingsError("invalid integration selection")
        return replace(self, t3code_enabled=enabled)

    def with_t3code(
        self,
        *,
        base_dir: str | object | None = _UNSET,
        environment_id: str | object | None = _UNSET,
    ) -> IntegrationSettings:
        return replace(
            self,
            t3code_base_dir=(
                self.t3code_base_dir
                if base_dir is _UNSET
                else _clean_optional_text(base_dir)
            ),
            t3code_environment_id=(
                self.t3code_environment_id
                if environment_id is _UNSET
                else _clean_optional_text(environment_id)
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "settings_schema_version": INTEGRATION_SETTINGS_SCHEMA_VERSION,
            "t3code_enabled": self.t3code_enabled,
            "t3code_base_dir": self.t3code_base_dir,
            "t3code_environment_id": self.t3code_environment_id,
        }


@dataclass(frozen=True, slots=True)
class LoadedIntegrationSettings:
    settings: IntegrationSettings
    compatibility: IntegrationSettingsCompatibility
    source_digest: str | None


def _optional_bounded_text(value: object, maximum: int) -> bool:
    return value is None or (
        type(value) is str
        and 1 <= len(value) <= maximum
        and value == value.strip()
        and value.isprintable()
        and "\x00" not in value
    )


def _clean_optional_text(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def default_integration_settings_path() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".config"
    return root / "sidepulse" / "integrations.json"


def _digest(document: dict[str, object]) -> str:
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_document(path: Path) -> dict[str, object]:
    raw = read_private_text(path, max_bytes=INTEGRATION_SETTINGS_MAX_BYTES)
    value = json.loads(raw)
    if type(value) is not dict:
        raise IntegrationSettingsError("integration settings must be an object")
    return value


def _schema_version(document: dict[str, object]) -> int:
    raw = document.get("settings_schema_version", 1)
    if type(raw) is not int or raw < 1:
        raise IntegrationSettingsError("invalid integration settings schema")
    return raw


def _bool(document: dict[str, object], key: str, default: bool) -> bool:
    value = document.get(key, default)
    return value if type(value) is bool else default


def _optional_text(
    document: dict[str, object],
    key: str,
    maximum: int,
) -> str | None:
    value = document.get(key)
    return value if _optional_bounded_text(value, maximum) else None


def _settings_from_document(document: dict[str, object]) -> IntegrationSettings:
    return IntegrationSettings(
        t3code_enabled=_bool(document, "t3code_enabled", False),
        t3code_base_dir=_optional_text(document, "t3code_base_dir", 4096),
        t3code_environment_id=_optional_text(
            document,
            "t3code_environment_id",
            256,
        ),
    )


def load_integration_settings(
    path: Path | None = None,
) -> LoadedIntegrationSettings:
    target = (path or default_integration_settings_path()).expanduser().absolute()
    try:
        document = _read_document(target)
    except FileNotFoundError:
        return LoadedIntegrationSettings(
            IntegrationSettings(),
            IntegrationSettingsCompatibility(INTEGRATION_SETTINGS_SCHEMA_VERSION),
            None,
        )
    except (OSError, UnicodeError, ValueError):
        return LoadedIntegrationSettings(
            IntegrationSettings(),
            IntegrationSettingsCompatibility(
                INTEGRATION_SETTINGS_SCHEMA_VERSION,
                read_only=True,
            ),
            None,
        )

    try:
        version = _schema_version(document)
    except IntegrationSettingsError:
        return LoadedIntegrationSettings(
            IntegrationSettings(),
            IntegrationSettingsCompatibility(
                INTEGRATION_SETTINGS_SCHEMA_VERSION,
                read_only=True,
            ),
            _digest(document),
        )
    compatibility = IntegrationSettingsCompatibility(
        version,
        read_only=version > INTEGRATION_SETTINGS_SCHEMA_VERSION,
    )
    return LoadedIntegrationSettings(
        _settings_from_document(document),
        compatibility,
        _digest(document),
    )


def save_integration_settings(
    settings: IntegrationSettings,
    path: Path | None = None,
    *,
    loaded: LoadedIntegrationSettings | None = None,
) -> Path:
    if type(settings) is not IntegrationSettings:
        raise IntegrationSettingsError("invalid integration settings")
    target = (path or default_integration_settings_path()).expanduser().absolute()
    if loaded is not None and loaded.compatibility.read_only:
        raise IntegrationSettingsWriteRefusedError(
            "integration settings were written by a newer SidePulse version"
        )

    current: dict[str, object] = {}
    current_digest = None
    try:
        current = _read_document(target)
        current_digest = _digest(current)
        if _schema_version(current) > INTEGRATION_SETTINGS_SCHEMA_VERSION:
            raise IntegrationSettingsWriteRefusedError(
                "integration settings were written by a newer SidePulse version"
            )
    except FileNotFoundError:
        pass
    if loaded is not None and current_digest != loaded.source_digest:
        raise IntegrationSettingsConcurrentWriteError(
            "integration settings changed after they were loaded"
        )

    encoded = settings.to_dict()
    document = {
        key: value
        for key, value in current.items()
        if key not in encoded and key not in _RETIRED_CODEXBAR_KEYS
    }
    document.update(encoded)
    payload = json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
    return atomic_private_write(target, payload)


__all__ = [
    "INTEGRATION_NAMES",
    "INTEGRATION_SETTINGS_SCHEMA_VERSION",
    "IntegrationSettings",
    "IntegrationSettingsCompatibility",
    "IntegrationSettingsConcurrentWriteError",
    "IntegrationSettingsError",
    "IntegrationSettingsWriteRefusedError",
    "LoadedIntegrationSettings",
    "default_integration_settings_path",
    "load_integration_settings",
    "save_integration_settings",
]
