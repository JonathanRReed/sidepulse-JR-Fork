"""Versioned, owner-private provider configuration and browser consent."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .private_io import atomic_private_write, read_private_text
from .provider_usage_platform import DEFAULT_PROVIDER_DESCRIPTORS

PROVIDER_SETTINGS_SCHEMA_VERSION = 1
MAX_SETTINGS_BYTES = 512 * 1024


def default_provider_usage_settings_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".config"
    return base / "sidepulse" / "providers.json"


def _digest(raw: bytes | None) -> str | None:
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def _safe_atom(value: object, *, maximum: int = 200) -> str:
    if not isinstance(value, str):
        raise ValueError("configuration value must be a string")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or "\x00" in cleaned:
        raise ValueError("invalid configuration value")
    return cleaned


@dataclass(frozen=True, slots=True)
class BrowserConsent:
    provider_id: str
    browser: str
    profile: str
    domains: tuple[str, ...]
    fields: tuple[str, ...]
    background_repair: bool
    granted_at: float

    def __post_init__(self) -> None:
        provider_id = _safe_atom(self.provider_id, maximum=64).lower()
        browser = _safe_atom(self.browser, maximum=64).lower()
        profile = _safe_atom(self.profile, maximum=200)
        if (
            type(self.domains) is not tuple
            or not self.domains
            or len(self.domains) > 32
            or len(self.domains) != len(set(self.domains))
            or not all(isinstance(value, str) and value and len(value) <= 253 for value in self.domains)
            or type(self.fields) is not tuple
            or not self.fields
            or len(self.fields) > 64
            or len(self.fields) != len(set(self.fields))
            or not all(isinstance(value, str) and value and len(value) <= 200 for value in self.fields)
            or type(self.background_repair) is not bool
            or isinstance(self.granted_at, bool)
            or not isinstance(self.granted_at, (int, float))
            or float(self.granted_at) < 0.0
        ):
            raise ValueError("invalid browser consent")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "browser", browser)
        object.__setattr__(self, "profile", profile)
        object.__setattr__(self, "granted_at", float(self.granted_at))

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.provider_id, self.browser, self.profile)

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider_id,
            "browser": self.browser,
            "profile": self.profile,
            "domains": list(self.domains),
            "fields": list(self.fields),
            "background_repair": self.background_repair,
            "granted_at": self.granted_at,
        }


_DEFAULT_ENABLED = tuple(
    row.provider_id for row in DEFAULT_PROVIDER_DESCRIPTORS if row.default_enabled
)


@dataclass(frozen=True, slots=True)
class ProviderUsageSettings:
    enabled: tuple[str, ...] = _DEFAULT_ENABLED
    source_modes: Mapping[str, str] = field(default_factory=dict)
    options: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    browser_consents: tuple[BrowserConsent, ...] = ()

    def __post_init__(self) -> None:
        known = {row.provider_id for row in DEFAULT_PROVIDER_DESCRIPTORS}
        if (
            type(self.enabled) is not tuple
            or len(self.enabled) != len(set(self.enabled))
            or not all(value in known for value in self.enabled)
        ):
            raise ValueError("invalid enabled providers")
        modes: dict[str, str] = {}
        if not isinstance(self.source_modes, Mapping):
            raise ValueError("source modes must be a mapping")
        for provider, mode in self.source_modes.items():
            provider_id = _safe_atom(provider, maximum=64).lower()
            if provider_id not in known:
                raise ValueError("unknown provider source mode")
            modes[provider_id] = _safe_atom(mode, maximum=64).lower()
        options: dict[str, Mapping[str, str]] = {}
        if not isinstance(self.options, Mapping):
            raise ValueError("provider options must be a mapping")
        for provider, values in self.options.items():
            provider_id = _safe_atom(provider, maximum=64).lower()
            if provider_id not in known or not isinstance(values, Mapping):
                raise ValueError("invalid provider options")
            cleaned = {
                _safe_atom(key, maximum=80): _safe_atom(value, maximum=500)
                for key, value in values.items()
            }
            if len(cleaned) > 64:
                raise ValueError("too many provider options")
            options[provider_id] = MappingProxyType(cleaned)
        if (
            type(self.browser_consents) is not tuple
            or len(self.browser_consents) > 64
            or not all(type(row) is BrowserConsent for row in self.browser_consents)
            or len({row.key for row in self.browser_consents}) != len(self.browser_consents)
        ):
            raise ValueError("invalid browser consent collection")
        object.__setattr__(self, "source_modes", MappingProxyType(modes))
        object.__setattr__(self, "options", MappingProxyType(options))

    @classmethod
    def defaults(cls) -> ProviderUsageSettings:
        return cls()

    def is_enabled(self, provider_id: str) -> bool:
        return provider_id in self.enabled

    def with_enabled(self, provider_id: str, enabled: bool) -> ProviderUsageSettings:
        known = {row.provider_id for row in DEFAULT_PROVIDER_DESCRIPTORS}
        if provider_id not in known or type(enabled) is not bool:
            raise ValueError("invalid provider enablement")
        values = set(self.enabled)
        if enabled:
            values.add(provider_id)
        else:
            values.discard(provider_id)
        ordered = tuple(
            row.provider_id for row in DEFAULT_PROVIDER_DESCRIPTORS if row.provider_id in values
        )
        return replace(self, enabled=ordered)

    def with_source_mode(self, provider_id: str, mode: str) -> ProviderUsageSettings:
        values = dict(self.source_modes)
        values[provider_id] = _safe_atom(mode, maximum=64).lower()
        return replace(self, source_modes=values)

    def with_option(self, provider_id: str, key: str, value: str) -> ProviderUsageSettings:
        values = {name: dict(options) for name, options in self.options.items()}
        values.setdefault(provider_id, {})[_safe_atom(key, maximum=80)] = _safe_atom(
            value, maximum=500
        )
        return replace(self, options=values)

    def option(self, provider_id: str, key: str) -> str | None:
        return self.options.get(provider_id, {}).get(key)

    def with_browser_consent(self, consent: BrowserConsent) -> ProviderUsageSettings:
        if type(consent) is not BrowserConsent:
            raise TypeError("consent must be BrowserConsent")
        rows = {row.key: row for row in self.browser_consents}
        rows[consent.key] = consent
        return replace(
            self,
            browser_consents=tuple(sorted(rows.values(), key=lambda row: row.key)),
        )

    def without_browser_consent(
        self, provider_id: str, browser: str, profile: str
    ) -> ProviderUsageSettings:
        key = (provider_id.lower(), browser.lower(), profile)
        return replace(
            self,
            browser_consents=tuple(row for row in self.browser_consents if row.key != key),
        )

    def browser_consent(
        self, provider_id: str, browser: str, profile: str
    ) -> BrowserConsent | None:
        key = (provider_id.lower(), browser.lower(), profile)
        return next((row for row in self.browser_consents if row.key == key), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PROVIDER_SETTINGS_SCHEMA_VERSION,
            "enabled": list(self.enabled),
            "source_modes": dict(self.source_modes),
            "options": {
                provider: dict(values) for provider, values in self.options.items()
            },
            "browser_consents": [row.to_dict() for row in self.browser_consents],
        }


@dataclass(frozen=True, slots=True)
class LoadedProviderUsageSettings:
    settings: ProviderUsageSettings
    path: Path
    digest: str | None
    unknown_fields: Mapping[str, object]
    read_only: bool


def _consent_from_dict(value: object) -> BrowserConsent | None:
    if not isinstance(value, dict):
        return None
    try:
        return BrowserConsent(
            provider_id=value.get("provider"),
            browser=value.get("browser"),
            profile=value.get("profile"),
            domains=tuple(value.get("domains", ())),
            fields=tuple(value.get("fields", ())),
            background_repair=value.get("background_repair", False),
            granted_at=value.get("granted_at", 0.0),
        )
    except (TypeError, ValueError):
        return None


def load_provider_usage_settings(
    path: Path | None = None,
) -> LoadedProviderUsageSettings:
    target = (path or default_provider_usage_settings_path()).expanduser().absolute()
    try:
        raw_text = read_private_text(target, max_bytes=MAX_SETTINGS_BYTES)
        raw = raw_text.encode("utf-8")
        document = json.loads(raw_text)
    except FileNotFoundError:
        return LoadedProviderUsageSettings(
            ProviderUsageSettings.defaults(), target, None, MappingProxyType({}), False
        )
    except (OSError, UnicodeError, ValueError):
        return LoadedProviderUsageSettings(
            ProviderUsageSettings.defaults(), target, None, MappingProxyType({}), True
        )
    if not isinstance(document, dict):
        return LoadedProviderUsageSettings(
            ProviderUsageSettings.defaults(), target, _digest(raw), MappingProxyType({}), True
        )
    version = document.get("schema_version", 1)
    read_only = type(version) is not int or version > PROVIDER_SETTINGS_SCHEMA_VERSION
    known = {"schema_version", "enabled", "source_modes", "options", "browser_consents"}
    unknown = {key: value for key, value in document.items() if key not in known}
    try:
        settings = ProviderUsageSettings(
            enabled=tuple(document.get("enabled", _DEFAULT_ENABLED)),
            source_modes=document.get("source_modes", {}),
            options=document.get("options", {}),
            browser_consents=tuple(
                consent
                for value in document.get("browser_consents", [])
                if (consent := _consent_from_dict(value)) is not None
            ),
        )
    except (TypeError, ValueError):
        settings = ProviderUsageSettings.defaults()
        read_only = True
    return LoadedProviderUsageSettings(
        settings,
        target,
        _digest(raw),
        MappingProxyType(unknown),
        read_only,
    )


def save_provider_usage_settings(
    settings: ProviderUsageSettings,
    path: Path | None = None,
    *,
    loaded: LoadedProviderUsageSettings | None = None,
) -> Path:
    if type(settings) is not ProviderUsageSettings:
        raise TypeError("settings must be ProviderUsageSettings")
    target = (path or (loaded.path if loaded else default_provider_usage_settings_path())).expanduser().absolute()
    if loaded is not None and loaded.read_only:
        raise ValueError("provider settings use a newer or invalid schema")
    try:
        current_raw = target.read_bytes()
    except FileNotFoundError:
        current_raw = None
    if loaded is not None and _digest(current_raw) != loaded.digest:
        raise ValueError("provider settings changed after they were loaded")
    document = dict(loaded.unknown_fields) if loaded is not None else {}
    document.update(settings.to_dict())
    atomic_private_write(
        target,
        json.dumps(document, indent=2, sort_keys=True) + "\n",
    )
    return target


__all__ = [
    "BrowserConsent",
    "LoadedProviderUsageSettings",
    "PROVIDER_SETTINGS_SCHEMA_VERSION",
    "ProviderUsageSettings",
    "default_provider_usage_settings_path",
    "load_provider_usage_settings",
    "save_provider_usage_settings",
]
