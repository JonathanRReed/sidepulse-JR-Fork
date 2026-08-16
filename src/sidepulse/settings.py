"""Versioned, lossless settings facade over the historical settings model.

The original model remains in :mod:`sidepulse._settings_legacy` while the
persistence boundary is hardened here. All callers continue importing
``sidepulse.settings``; the facade patches the durable encoders/decoders once
and preserves the public API.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import _settings_legacy as _legacy

CURRENT_SETTINGS_SCHEMA_VERSION = 2
MIN_READABLE_SETTINGS_SCHEMA_VERSION = 1
MIN_WRITABLE_SETTINGS_SCHEMA_VERSION = 1
SETTINGS_SCHEMA_VERSION = CURRENT_SETTINGS_SCHEMA_VERSION
SETTINGS_DOCUMENT_MAX_BYTES = 4 * 1024 * 1024

DEVICE_SETTING_PERSISTED_FIELDS = frozenset(
    {
        "id",
        "name",
        "path",
        "led_display",
        "brightness",
        "auto_brightness_enabled",
        "red_gain",
        "green_gain",
        "blue_gain",
        "resting_glow",
        "blend_mode",
        "provider_pin",
        "signal_policy",
    }
)


class SettingsWriteRefusedError(RuntimeError):
    """A settings document is newer than this writer can safely preserve."""


@dataclass(frozen=True, slots=True)
class SettingsCompatibility:
    source_version: int
    target_version: int = CURRENT_SETTINGS_SCHEMA_VERSION
    read_only: bool = False
    migrated: bool = False

    def __post_init__(self) -> None:
        if not (
            type(self.source_version) is int
            and self.source_version >= 1
            and type(self.target_version) is int
            and self.target_version >= 1
            and type(self.read_only) is bool
            and type(self.migrated) is bool
        ):
            raise ValueError("invalid settings compatibility")


@dataclass(frozen=True, slots=True)
class LoadedSettings:
    settings: Any
    compatibility: SettingsCompatibility


_STATE_LOCK = threading.RLock()
_COMPATIBILITY_BY_PATH: dict[Path, SettingsCompatibility] = {}
_SOURCE_DOCUMENT_BY_PATH: dict[Path, dict[str, object]] = {}

_ORIGINAL_DEVICE_TO_DICT = _legacy.DeviceDisplaySetting.to_dict
_ORIGINAL_DEVICE_SETTINGS_LOADER = _legacy._device_display_settings
_ORIGINAL_APPLY_CALIBRATION_PROFILE = (
    _legacy.AgentMonitorSettings.with_applied_calibration_profile
)
_ORIGINAL_LOAD_SETTINGS = _legacy.load_settings


def _settings_path(path: Path | None) -> Path:
    return (path or _legacy.default_settings_path()).expanduser().absolute()


def _device_to_dict(self) -> dict[str, object]:
    payload = dict(_ORIGINAL_DEVICE_TO_DICT(self))
    payload["resting_glow"] = max(0.0, min(0.35, float(self.resting_glow)))
    if set(payload) != DEVICE_SETTING_PERSISTED_FIELDS:
        missing = sorted(DEVICE_SETTING_PERSISTED_FIELDS - set(payload))
        extra = sorted(set(payload) - DEVICE_SETTING_PERSISTED_FIELDS)
        raise RuntimeError(
            f"device settings schema drifted (missing={missing}, extra={extra})"
        )
    return payload


def _device_display_settings(
    value: object,
    default_display: str,
) -> tuple[object, ...]:
    devices = _ORIGINAL_DEVICE_SETTINGS_LOADER(value, default_display)
    return tuple(
        replace(
            device,
            resting_glow=max(0.0, min(0.35, float(device.resting_glow))),
        )
        for device in devices
    )


def _with_applied_calibration_profile(self, slot: str):
    updated = _ORIGINAL_APPLY_CALIBRATION_PROFILE(self, slot)
    profile = self.calibration_profiles.get(slot)
    if not isinstance(profile, dict):
        return updated
    devices = []
    for device in updated.devices:
        entry = profile.get(device.device_id)
        if isinstance(entry, dict):
            raw = entry.get("resting_glow", device.resting_glow)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                device = replace(
                    device,
                    resting_glow=max(0.0, min(0.35, float(raw))),
                )
        devices.append(device)
    return replace(updated, devices=tuple(devices))


def _settings_schema_version(data: dict[str, object]) -> int:
    raw = data.get("settings_schema_version", 1)
    if type(raw) is not int or raw < 1:
        raise ValueError("invalid settings schema version")
    return raw


def _migrate_settings_document(
    data: dict[str, object],
    source_version: int,
) -> dict[str, object]:
    migrated = dict(data)
    version = source_version
    while version < CURRENT_SETTINGS_SCHEMA_VERSION:
        if version == 1:
            migrated["settings_schema_version"] = 2
            version = 2
            continue
        raise ValueError("unsupported settings migration")
    return migrated


def _remember_document(
    target: Path,
    compatibility: SettingsCompatibility,
    document: dict[str, object],
) -> None:
    with _STATE_LOCK:
        _COMPATIBILITY_BY_PATH[target] = compatibility
        _SOURCE_DOCUMENT_BY_PATH[target] = dict(document)


def _forget_document(target: Path) -> None:
    with _STATE_LOCK:
        _COMPATIBILITY_BY_PATH.pop(target, None)
        _SOURCE_DOCUMENT_BY_PATH.pop(target, None)


def load_settings_document(path: Path | None = None) -> LoadedSettings:
    target = _settings_path(path)
    try:
        target.lstat()
        _legacy.ensure_private_directory(target.parent)
        data = json.loads(
            _legacy.read_private_text(
                target,
                max_bytes=SETTINGS_DOCUMENT_MAX_BYTES,
            )
        )
    except FileNotFoundError:
        compatibility = SettingsCompatibility(CURRENT_SETTINGS_SCHEMA_VERSION)
        _remember_document(target, compatibility, {})
        return LoadedSettings(_legacy.AgentMonitorSettings(), compatibility)
    except OSError:
        compatibility = SettingsCompatibility(CURRENT_SETTINGS_SCHEMA_VERSION)
        _forget_document(target)
        return LoadedSettings(_legacy.AgentMonitorSettings(), compatibility)
    except Exception:
        _legacy._preserve_corrupt_settings(target)
        compatibility = SettingsCompatibility(CURRENT_SETTINGS_SCHEMA_VERSION)
        _forget_document(target)
        return LoadedSettings(_legacy.AgentMonitorSettings(), compatibility)

    if not isinstance(data, dict):
        _legacy._preserve_corrupt_settings(target)
        compatibility = SettingsCompatibility(CURRENT_SETTINGS_SCHEMA_VERSION)
        _forget_document(target)
        return LoadedSettings(_legacy.AgentMonitorSettings(), compatibility)

    try:
        source_version = _settings_schema_version(data)
    except ValueError:
        _legacy._preserve_corrupt_settings(target)
        compatibility = SettingsCompatibility(CURRENT_SETTINGS_SCHEMA_VERSION)
        _forget_document(target)
        return LoadedSettings(_legacy.AgentMonitorSettings(), compatibility)

    if source_version > CURRENT_SETTINGS_SCHEMA_VERSION:
        compatibility = SettingsCompatibility(
            source_version,
            read_only=True,
            migrated=False,
        )
        settings = _ORIGINAL_LOAD_SETTINGS(target)
        _remember_document(target, compatibility, data)
        return LoadedSettings(settings, compatibility)

    if source_version < MIN_READABLE_SETTINGS_SCHEMA_VERSION:
        _legacy._preserve_corrupt_settings(target)
        compatibility = SettingsCompatibility(CURRENT_SETTINGS_SCHEMA_VERSION)
        _forget_document(target)
        return LoadedSettings(_legacy.AgentMonitorSettings(), compatibility)

    try:
        migrated = _migrate_settings_document(data, source_version)
    except ValueError:
        compatibility = SettingsCompatibility(source_version, read_only=True)
        _remember_document(target, compatibility, data)
        return LoadedSettings(_legacy.AgentMonitorSettings(), compatibility)

    compatibility = SettingsCompatibility(
        source_version,
        read_only=False,
        migrated=source_version != CURRENT_SETTINGS_SCHEMA_VERSION,
    )
    settings = _ORIGINAL_LOAD_SETTINGS(target)
    _remember_document(target, compatibility, migrated)
    return LoadedSettings(settings, compatibility)


def load_settings(path: Path | None = None):
    return load_settings_document(path).settings


def save_settings(
    settings,
    path: Path | None = None,
    *,
    compatibility: SettingsCompatibility | None = None,
) -> Path:
    target = _settings_path(path)
    with _STATE_LOCK:
        remembered_compatibility = _COMPATIBILITY_BY_PATH.get(target)
        source_document = dict(_SOURCE_DOCUMENT_BY_PATH.get(target, {}))
    effective = compatibility or remembered_compatibility
    if effective is not None and effective.read_only:
        raise SettingsWriteRefusedError(
            "settings were written by a newer SidePulse version"
        )
    if effective is not None and (
        effective.source_version < MIN_WRITABLE_SETTINGS_SCHEMA_VERSION
    ):
        raise SettingsWriteRefusedError("settings schema is not writable")

    encoded = settings.to_dict()
    encoded["settings_schema_version"] = CURRENT_SETTINGS_SCHEMA_VERSION
    unknown = {
        key: value
        for key, value in source_document.items()
        if key not in encoded and key != "settings_schema_version"
    }
    document = {**unknown, **encoded}
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    written = _legacy.atomic_private_write(target, payload)
    current = SettingsCompatibility(CURRENT_SETTINGS_SCHEMA_VERSION)
    _remember_document(target, current, document)
    return written


_legacy.CURRENT_SETTINGS_SCHEMA_VERSION = CURRENT_SETTINGS_SCHEMA_VERSION
_legacy.MIN_READABLE_SETTINGS_SCHEMA_VERSION = MIN_READABLE_SETTINGS_SCHEMA_VERSION
_legacy.MIN_WRITABLE_SETTINGS_SCHEMA_VERSION = MIN_WRITABLE_SETTINGS_SCHEMA_VERSION
_legacy.SETTINGS_SCHEMA_VERSION = CURRENT_SETTINGS_SCHEMA_VERSION
_legacy.DEVICE_SETTING_PERSISTED_FIELDS = DEVICE_SETTING_PERSISTED_FIELDS
_legacy.DeviceDisplaySetting.to_dict = _device_to_dict
_legacy._device_display_settings = _device_display_settings
_legacy.AgentMonitorSettings.with_applied_calibration_profile = (
    _with_applied_calibration_profile
)
_legacy.SettingsCompatibility = SettingsCompatibility
_legacy.LoadedSettings = LoadedSettings
_legacy.SettingsWriteRefusedError = SettingsWriteRefusedError
_legacy.load_settings_document = load_settings_document
_legacy.load_settings = load_settings
_legacy.save_settings = save_settings

for _name in dir(_legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_legacy, _name)

__all__ = tuple(
    sorted(
        {
            name
            for name in globals()
            if not name.startswith("_") and name not in {"Any", "Path"}
        }
    )
)
