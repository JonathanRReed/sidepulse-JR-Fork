"""Versioned settings for SidePulse's native provider accounting."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .provider_usage_platform import provider_descriptor, provider_descriptors

PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION = 1


class ProviderUsageSettingsError(ValueError):
    pass


class ProviderUsageSettingsWriteRefusedError(ProviderUsageSettingsError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderPreference:
    provider_id: str
    enabled: bool
    browser_sources: bool
    reset_celebrations: bool = True
    threshold_remaining: float = 20.0
    options: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        descriptor = provider_descriptor(self.provider_id)
        if (
            type(self.enabled) is not bool
            or type(self.browser_sources) is not bool
            or type(self.reset_celebrations) is not bool
            or isinstance(self.threshold_remaining, bool)
            or not isinstance(self.threshold_remaining, (int, float))
            or not 0.0 <= float(self.threshold_remaining) <= 100.0
            or type(self.options) is not tuple
            or len({key for key, _value in self.options}) != len(self.options)
            or not all(
                isinstance(key, str)
                and key
                and isinstance(value, str)
                and len(key) <= 64
                and len(value) <= 4096
                for key, value in self.options
            )
            or (self.browser_sources and not descriptor.supports_browser_sources)
        ):
            raise ProviderUsageSettingsError("invalid provider preference")
        object.__setattr__(self, "threshold_remaining", float(self.threshold_remaining))

    def option(self, key: str) -> str | None:
        return next((value for name, value in self.options if name == key), None)

    def with_option(self, key: str, value: str | None) -> ProviderPreference:
        if not isinstance(key, str) or not key or len(key) > 64:
            raise ProviderUsageSettingsError("invalid provider option key")
        updated = dict(self.options)
        if value is None:
            updated.pop(key, None)
        else:
            if not isinstance(value, str) or len(value) > 4096:
                raise ProviderUsageSettingsError("invalid provider option value")
            updated[key] = value
        return replace(self, options=tuple(sorted(updated.items())))


@dataclass(frozen=True, slots=True)
class ProviderUsageSettings:
    schema_version: int
    providers: tuple[ProviderPreference, ...]

    def __post_init__(self) -> None:
        expected = tuple(descriptor.provider_id for descriptor in provider_descriptors())
        actual = tuple(preference.provider_id for preference in self.providers)
        if (
            type(self.schema_version) is not int
            or self.schema_version != PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION
            or type(self.providers) is not tuple
            or actual != expected
        ):
            raise ProviderUsageSettingsError("invalid provider usage settings")

    def preference(self, provider_id: str) -> ProviderPreference:
        provider_descriptor(provider_id)
        return next(
            preference
            for preference in self.providers
            if preference.provider_id == provider_id
        )

    def _replace(self, preference: ProviderPreference) -> ProviderUsageSettings:
        return replace(
            self,
            providers=tuple(
                preference if current.provider_id == preference.provider_id else current
                for current in self.providers
            ),
        )

    def with_enabled(self, provider_id: str, enabled: bool) -> ProviderUsageSettings:
        if type(enabled) is not bool:
            raise ProviderUsageSettingsError("enabled must be a boolean")
        return self._replace(replace(self.preference(provider_id), enabled=enabled))

    def with_browser_sources(
        self,
        provider_id: str,
        enabled: bool,
    ) -> ProviderUsageSettings:
        if type(enabled) is not bool:
            raise ProviderUsageSettingsError("browser_sources must be a boolean")
        descriptor = provider_descriptor(provider_id)
        if enabled and not descriptor.supports_browser_sources:
            raise ProviderUsageSettingsError(
                f"{descriptor.label} does not support browser sources"
            )
        return self._replace(
            replace(self.preference(provider_id), browser_sources=enabled)
        )

    def with_option(
        self,
        provider_id: str,
        key: str,
        value: str | None,
    ) -> ProviderUsageSettings:
        return self._replace(self.preference(provider_id).with_option(key, value))


@dataclass(frozen=True, slots=True)
class LoadedProviderUsageSettings:
    settings: ProviderUsageSettings
    read_only: bool
    unknown_fields: tuple[tuple[str, object], ...]


def default_provider_usage_settings() -> ProviderUsageSettings:
    return ProviderUsageSettings(
        PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION,
        tuple(
            ProviderPreference(
                descriptor.provider_id,
                enabled=descriptor.provider_id != "openai-api",
                browser_sources=False,
            )
            for descriptor in provider_descriptors()
        ),
    )


def default_provider_usage_settings_path(home: Path | None = None) -> Path:
    base = Path.home() if home is None else Path(home)
    return base / ".config" / "sidepulse" / "provider-usage.json"


def _preference_from_payload(
    provider_id: str,
    payload: object,
    fallback: ProviderPreference,
) -> ProviderPreference:
    if not isinstance(payload, dict):
        return fallback
    descriptor = provider_descriptor(provider_id)
    enabled = payload.get("enabled", fallback.enabled)
    browser_sources = payload.get("browser_sources", fallback.browser_sources)
    reset_celebrations = payload.get(
        "reset_celebrations",
        fallback.reset_celebrations,
    )
    threshold = payload.get("threshold_remaining", fallback.threshold_remaining)
    raw_options = payload.get("options", {})
    options = ()
    if isinstance(raw_options, dict):
        options = tuple(
            sorted(
                (str(key), str(value))
                for key, value in raw_options.items()
                if isinstance(key, str)
                and key
                and isinstance(value, (str, int, float, bool))
            )
        )
    try:
        return ProviderPreference(
            provider_id,
            enabled=enabled if type(enabled) is bool else fallback.enabled,
            browser_sources=(
                browser_sources
                if type(browser_sources) is bool
                and (not browser_sources or descriptor.supports_browser_sources)
                else False
            ),
            reset_celebrations=(
                reset_celebrations
                if type(reset_celebrations) is bool
                else fallback.reset_celebrations
            ),
            threshold_remaining=threshold,
            options=options,
        )
    except (ProviderUsageSettingsError, TypeError, ValueError):
        return fallback


def load_provider_usage_settings(
    path: Path | None = None,
    *,
    reader: Callable[[Path], str] | None = None,
) -> LoadedProviderUsageSettings:
    target = default_provider_usage_settings_path() if path is None else Path(path)
    defaults = default_provider_usage_settings()
    read = reader
    if read is None:
        from .private_io import read_private_text

        read = read_private_text
    try:
        document = json.loads(read(target))
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return LoadedProviderUsageSettings(defaults, False, ())
    if not isinstance(document, dict):
        return LoadedProviderUsageSettings(defaults, True, ())
    version = document.get("settings_schema_version")
    if type(version) is not int:
        return LoadedProviderUsageSettings(defaults, True, tuple(document.items()))
    if version > PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION:
        return LoadedProviderUsageSettings(defaults, True, tuple(document.items()))

    by_id = {}
    raw_rows = document.get("providers", [])
    if isinstance(raw_rows, list):
        for row in raw_rows:
            if isinstance(row, dict) and isinstance(row.get("provider_id"), str):
                by_id[row["provider_id"]] = row
    settings = ProviderUsageSettings(
        PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION,
        tuple(
            _preference_from_payload(
                default.provider_id,
                by_id.get(default.provider_id),
                default,
            )
            for default in defaults.providers
        ),
    )
    unknown = tuple(
        (key, value)
        for key, value in document.items()
        if key not in {"settings_schema_version", "providers"}
    )
    return LoadedProviderUsageSettings(settings, False, unknown)


def _settings_document(
    settings: ProviderUsageSettings,
    unknown_fields: tuple[tuple[str, object], ...],
) -> dict[str, object]:
    document = dict(unknown_fields)
    document["settings_schema_version"] = PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION
    document["providers"] = [
        {
            "provider_id": preference.provider_id,
            "enabled": preference.enabled,
            "browser_sources": preference.browser_sources,
            "reset_celebrations": preference.reset_celebrations,
            "threshold_remaining": preference.threshold_remaining,
            "options": dict(preference.options),
        }
        for preference in settings.providers
    ]
    return document


def save_provider_usage_settings(
    settings: ProviderUsageSettings,
    path: Path | None = None,
    *,
    loaded: LoadedProviderUsageSettings | None = None,
    writer: Callable[[Path, str], object] | None = None,
) -> Path:
    if type(settings) is not ProviderUsageSettings:
        raise ProviderUsageSettingsError("invalid provider usage settings")
    if loaded is not None and loaded.read_only:
        raise ProviderUsageSettingsWriteRefusedError(
            "provider usage settings are read-only"
        )
    target = default_provider_usage_settings_path() if path is None else Path(path)
    unknown = loaded.unknown_fields if loaded is not None else ()
    text = json.dumps(
        _settings_document(settings, unknown),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    write = writer
    if write is None:
        from .private_io import atomic_private_write

        write = atomic_private_write
    write(target, text)
    return target


__all__ = [
    "LoadedProviderUsageSettings",
    "PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION",
    "ProviderPreference",
    "ProviderUsageSettings",
    "ProviderUsageSettingsError",
    "ProviderUsageSettingsWriteRefusedError",
    "default_provider_usage_settings",
    "default_provider_usage_settings_path",
    "load_provider_usage_settings",
    "save_provider_usage_settings",
]
