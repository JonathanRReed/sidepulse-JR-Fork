"""Versioned settings for SidePulse's native provider accounting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .provider_instances import (
    DEFAULT_PROVIDER_INSTANCE_SOURCE_ID,
    OPEN_SESSION_APP,
    REMOTE_SHARING_NEVER,
    ProviderInstanceError,
    ProviderInstanceKey,
    ProviderInstanceProfile,
)
from .provider_usage_platform import provider_descriptor, provider_descriptors

PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION = 3


class ProviderUsageSettingsError(ValueError):
    pass


class ProviderUsageSettingsWriteRefusedError(ProviderUsageSettingsError):
    pass


#: The user-curatable elements of the menu's Usage rows. Field names are
#: the durable keys used by ``with_menu_flag`` and the settings document.
@dataclass(frozen=True, slots=True)
class MenuUsageDisplay:
    show_meters: bool = True
    show_totals: bool = True
    show_cost: bool = True
    show_detail_lanes: bool = True
    #: Codex Bar parity: the tightest visible limit rides next to the
    #: menu-bar icon itself.
    show_menu_bar_percent: bool = True

    def __post_init__(self) -> None:
        if not all(
            type(getattr(self, field)) is bool for field in MENU_USAGE_DISPLAY_FLAGS
        ):
            raise ProviderUsageSettingsError("invalid menu usage display")


MENU_USAGE_DISPLAY_FLAGS = (
    "show_meters",
    "show_totals",
    "show_cost",
    "show_detail_lanes",
    "show_menu_bar_percent",
)


@dataclass(frozen=True, slots=True)
class ProviderPreference:
    provider_id: str
    enabled: bool
    browser_sources: bool
    reset_celebrations: bool = True
    threshold_remaining: float = 20.0
    options: tuple[tuple[str, str], ...] = ()
    #: Collection can stay on while the menu row is hidden -- "I track
    #: Devin but don't want it in my face" is a curation choice, not a
    #: data choice.
    menu_visible: bool = True
    source_instance_id: str = DEFAULT_PROVIDER_INSTANCE_SOURCE_ID
    label: str | None = None
    color_override: str | None = None
    retention_days: int = 7
    remote_sharing_choice: str = REMOTE_SHARING_NEVER
    open_session_action: str = OPEN_SESSION_APP
    consent_reference: str | None = None
    credential_account_reference: str | None = None

    def __post_init__(self) -> None:
        descriptor = provider_descriptor(self.provider_id)
        try:
            instance_key = ProviderInstanceKey(
                self.provider_id,
                self.source_instance_id,
            )
            profile = ProviderInstanceProfile(
                instance_key,
                self.label
                or (
                    descriptor.label
                    if instance_key.source_instance_id.value
                    == DEFAULT_PROVIDER_INSTANCE_SOURCE_ID
                    else f"{descriptor.label} · {instance_key.source_instance_id.value}"
                ),
                color_override=self.color_override,
                retention_days=self.retention_days,
                remote_sharing_choice=self.remote_sharing_choice,
                open_session_action=self.open_session_action,
                consent_reference=self.consent_reference,
                credential_account_reference=self.credential_account_reference,
            )
        except ProviderInstanceError as exc:
            raise ProviderUsageSettingsError("invalid provider preference instance") from exc
        if (
            type(self.enabled) is not bool
            or type(self.browser_sources) is not bool
            or type(self.reset_celebrations) is not bool
            or type(self.menu_visible) is not bool
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
        object.__setattr__(self, "source_instance_id", instance_key.source_instance_id.value)
        object.__setattr__(self, "label", profile.label)
        object.__setattr__(self, "color_override", profile.color_override)
        object.__setattr__(self, "retention_days", profile.retention_days)
        object.__setattr__(
            self,
            "remote_sharing_choice",
            profile.remote_sharing_choice,
        )
        object.__setattr__(self, "open_session_action", profile.open_session_action)
        object.__setattr__(self, "consent_reference", profile.consent_reference)
        object.__setattr__(
            self,
            "credential_account_reference",
            profile.credential_account_reference,
        )

    @property
    def identity(self) -> tuple[str, str]:
        return self.provider_id, self.source_instance_id

    @property
    def profile(self) -> ProviderInstanceProfile:
        return ProviderInstanceProfile(
            ProviderInstanceKey(*self.identity),
            self.label,
            color_override=self.color_override,
            retention_days=self.retention_days,
            remote_sharing_choice=self.remote_sharing_choice,
            open_session_action=self.open_session_action,
            consent_reference=self.consent_reference,
            credential_account_reference=self.credential_account_reference,
        )

    def with_profile(self, profile: ProviderInstanceProfile) -> ProviderPreference:
        if type(profile) is not ProviderInstanceProfile or profile.key.value != self.identity:
            raise ProviderUsageSettingsError("provider profile identity mismatch")
        return replace(
            self,
            label=profile.label,
            color_override=profile.color_override,
            retention_days=profile.retention_days,
            remote_sharing_choice=profile.remote_sharing_choice,
            open_session_action=profile.open_session_action,
            consent_reference=profile.consent_reference,
            credential_account_reference=profile.credential_account_reference,
        )

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
    menu_display: MenuUsageDisplay = MenuUsageDisplay()

    def __post_init__(self) -> None:
        expected = tuple(descriptor.provider_id for descriptor in provider_descriptors())
        identities = tuple(preference.identity for preference in self.providers)
        defaults = tuple(
            preference.provider_id
            for preference in self.providers
            if preference.source_instance_id == DEFAULT_PROVIDER_INSTANCE_SOURCE_ID
        )
        if (
            type(self.schema_version) is not int
            or self.schema_version != PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION
            or type(self.providers) is not tuple
            or not all(type(preference) is ProviderPreference for preference in self.providers)
            or len(identities) != len(set(identities))
            or defaults != expected
            or type(self.menu_display) is not MenuUsageDisplay
        ):
            raise ProviderUsageSettingsError("invalid provider usage settings")

    def preference(
        self,
        provider_id: str,
        source_instance_id: str = DEFAULT_PROVIDER_INSTANCE_SOURCE_ID,
    ) -> ProviderPreference:
        provider_descriptor(provider_id)
        key = ProviderInstanceKey(provider_id, source_instance_id).value
        return next(
            preference
            for preference in self.providers
            if preference.identity == key
        )

    def profile(
        self,
        provider_id: str,
        source_instance_id: str = DEFAULT_PROVIDER_INSTANCE_SOURCE_ID,
    ) -> ProviderInstanceProfile:
        return self.preference(provider_id, source_instance_id).profile

    def _replace(self, preference: ProviderPreference) -> ProviderUsageSettings:
        return replace(
            self,
            providers=tuple(
                preference if current.identity == preference.identity else current
                for current in self.providers
            ),
        )

    def with_instance(self, preference: ProviderPreference) -> ProviderUsageSettings:
        if type(preference) is not ProviderPreference:
            raise ProviderUsageSettingsError("invalid provider preference")
        provider_descriptor(preference.provider_id)
        if any(current.identity == preference.identity for current in self.providers):
            return self._replace(preference)
        return replace(self, providers=(*self.providers, preference))

    def with_profile(
        self,
        profile: ProviderInstanceProfile,
    ) -> ProviderUsageSettings:
        if type(profile) is not ProviderInstanceProfile:
            raise ProviderUsageSettingsError("invalid provider instance profile")
        provider_id, source_instance_id = profile.key.value
        try:
            preference = self.preference(provider_id, source_instance_id)
        except StopIteration:
            preference = replace(
                self.preference(provider_id),
                source_instance_id=source_instance_id,
                label=None,
                color_override=None,
                retention_days=7,
                remote_sharing_choice=REMOTE_SHARING_NEVER,
                open_session_action=OPEN_SESSION_APP,
                consent_reference=None,
                credential_account_reference=None,
            )
        return self.with_instance(preference.with_profile(profile))

    def with_enabled(
        self,
        provider_id: str,
        enabled: bool,
        *,
        source_instance_id: str = DEFAULT_PROVIDER_INSTANCE_SOURCE_ID,
    ) -> ProviderUsageSettings:
        if type(enabled) is not bool:
            raise ProviderUsageSettingsError("enabled must be a boolean")
        return self._replace(
            replace(
                self.preference(provider_id, source_instance_id),
                enabled=enabled,
            )
        )

    def with_browser_sources(
        self,
        provider_id: str,
        enabled: bool,
        *,
        source_instance_id: str = DEFAULT_PROVIDER_INSTANCE_SOURCE_ID,
    ) -> ProviderUsageSettings:
        if type(enabled) is not bool:
            raise ProviderUsageSettingsError("browser_sources must be a boolean")
        descriptor = provider_descriptor(provider_id)
        if enabled and not descriptor.supports_browser_sources:
            raise ProviderUsageSettingsError(
                f"{descriptor.label} does not support browser sources"
            )
        return self._replace(
            replace(
                self.preference(provider_id, source_instance_id),
                browser_sources=enabled,
            )
        )

    def with_reset_celebrations(
        self,
        provider_id: str,
        enabled: bool,
        *,
        source_instance_id: str = DEFAULT_PROVIDER_INSTANCE_SOURCE_ID,
    ) -> ProviderUsageSettings:
        if type(enabled) is not bool:
            raise ProviderUsageSettingsError("reset_celebrations must be a boolean")
        return self._replace(
            replace(
                self.preference(provider_id, source_instance_id),
                reset_celebrations=enabled,
            )
        )

    def with_threshold_remaining(
        self,
        provider_id: str,
        threshold: float,
        *,
        source_instance_id: str = DEFAULT_PROVIDER_INSTANCE_SOURCE_ID,
    ) -> ProviderUsageSettings:
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not 0.0 <= float(threshold) <= 100.0
        ):
            raise ProviderUsageSettingsError(
                "threshold_remaining must be between 0 and 100"
            )
        return self._replace(
            replace(
                self.preference(provider_id, source_instance_id),
                threshold_remaining=float(threshold),
            )
        )

    def with_option(
        self,
        provider_id: str,
        key: str,
        value: str | None,
        *,
        source_instance_id: str = DEFAULT_PROVIDER_INSTANCE_SOURCE_ID,
    ) -> ProviderUsageSettings:
        return self._replace(
            self.preference(provider_id, source_instance_id).with_option(key, value)
        )

    def with_menu_visible(
        self,
        provider_id: str,
        visible: bool,
        *,
        source_instance_id: str = DEFAULT_PROVIDER_INSTANCE_SOURCE_ID,
    ) -> ProviderUsageSettings:
        if type(visible) is not bool:
            raise ProviderUsageSettingsError("menu_visible must be a boolean")
        return self._replace(
            replace(
                self.preference(provider_id, source_instance_id),
                menu_visible=visible,
            )
        )

    def with_menu_flag(self, flag: str, enabled: bool) -> ProviderUsageSettings:
        if flag not in MENU_USAGE_DISPLAY_FLAGS:
            raise ProviderUsageSettingsError(f"unknown menu display flag: {flag!r}")
        if type(enabled) is not bool:
            raise ProviderUsageSettingsError("menu display flag must be a boolean")
        return replace(
            self,
            menu_display=replace(self.menu_display, **{flag: enabled}),
        )

    def hidden_menu_providers(self) -> frozenset[str]:
        provider_ids = {preference.provider_id for preference in self.providers}
        return frozenset(
            provider_id
            for provider_id in provider_ids
            if all(
                not preference.menu_visible
                for preference in self.providers
                if preference.provider_id == provider_id
            )
        )

    def hidden_menu_instances(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            preference.identity
            for preference in self.providers
            if not preference.menu_visible
        )


@dataclass(frozen=True, slots=True)
class LoadedProviderUsageSettings:
    settings: ProviderUsageSettings
    read_only: bool
    unknown_fields: tuple[tuple[str, object], ...]
    source_revision: str | None = None
    source_path: Path | None = None

    @property
    def source_digest(self) -> str | None:
        """Compatibility name for the content-addressed source revision."""
        return self.source_revision


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


_PROFILE_SETTING_FIELDS = (
    "label",
    "color_override",
    "retention_days",
    "remote_sharing_choice",
    "open_session_action",
    "consent_reference",
    "credential_account_reference",
)


def _profile_from_payload(
    payload: dict[str, object],
    fallback: ProviderPreference,
) -> ProviderInstanceProfile:
    """Validate profile choices independently so one bad value stays local."""

    key = ProviderInstanceKey(*fallback.identity)
    values = {
        field: getattr(fallback, field)
        for field in _PROFILE_SETTING_FIELDS
    }
    profile = fallback.profile
    for field in _PROFILE_SETTING_FIELDS:
        if field not in payload:
            continue
        candidate = {**values, field: payload[field]}
        try:
            profile = ProviderInstanceProfile(
                key,
                candidate["label"],
                color_override=candidate["color_override"],
                retention_days=candidate["retention_days"],
                remote_sharing_choice=candidate["remote_sharing_choice"],
                open_session_action=candidate["open_session_action"],
                consent_reference=candidate["consent_reference"],
                credential_account_reference=candidate["credential_account_reference"],
            )
        except (ProviderInstanceError, TypeError, ValueError):
            continue
        values = {
            profile_field: getattr(profile, profile_field)
            for profile_field in _PROFILE_SETTING_FIELDS
        }
    return profile


def _preference_from_payload(
    provider_id: str,
    source_instance_id: str,
    payload: object,
    fallback: ProviderPreference,
) -> ProviderPreference:
    if not isinstance(payload, dict):
        return fallback
    descriptor = provider_descriptor(provider_id)
    profile_fallback = replace(
        fallback,
        source_instance_id=source_instance_id,
        label=fallback.label if fallback.identity[1] == source_instance_id else None,
    )
    profile = _profile_from_payload(payload, profile_fallback)
    enabled = payload.get("enabled", fallback.enabled)
    browser_sources = payload.get("browser_sources", fallback.browser_sources)
    reset_celebrations = payload.get(
        "reset_celebrations",
        fallback.reset_celebrations,
    )
    menu_visible = payload.get("menu_visible", fallback.menu_visible)
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
            menu_visible=(
                menu_visible
                if type(menu_visible) is bool
                else fallback.menu_visible
            ),
            source_instance_id=source_instance_id,
            label=profile.label,
            color_override=profile.color_override,
            retention_days=profile.retention_days,
            remote_sharing_choice=profile.remote_sharing_choice,
            open_session_action=profile.open_session_action,
            consent_reference=profile.consent_reference,
            credential_account_reference=profile.credential_account_reference,
        )
    except (ProviderUsageSettingsError, TypeError, ValueError):
        return fallback


def _menu_display_from_payload(payload: object) -> MenuUsageDisplay:
    defaults = MenuUsageDisplay()
    if not isinstance(payload, dict):
        return defaults
    values = {}
    for flag in MENU_USAGE_DISPLAY_FLAGS:
        value = payload.get(flag, getattr(defaults, flag))
        values[flag] = value if type(value) is bool else getattr(defaults, flag)
    return MenuUsageDisplay(**values)


def _document_digest(document: dict[str, object]) -> str:
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_real_document(target: Path) -> dict[str, object]:
    from .private_io import read_private_text

    document = json.loads(read_private_text(target))
    if not isinstance(document, dict):
        raise ValueError("provider usage settings document must be an object")
    return document


def load_provider_usage_settings(
    path: Path | None = None,
    *,
    reader: Callable[[Path], str] | None = None,
) -> LoadedProviderUsageSettings:
    target = (default_provider_usage_settings_path() if path is None else Path(path)).expanduser().absolute()
    defaults = default_provider_usage_settings()
    read = reader
    real_path = read is None
    if read is None:
        from .private_io import read_private_text

        read = read_private_text
    try:
        document = json.loads(read(target))
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return LoadedProviderUsageSettings(
            defaults,
            False,
            (),
            None,
            target if real_path else None,
        )
    if not isinstance(document, dict):
        return LoadedProviderUsageSettings(
            defaults,
            True,
            (),
            None,
            target if real_path else None,
        )
    source_revision = _document_digest(document)
    version = document.get("settings_schema_version")
    if type(version) is not int:
        return LoadedProviderUsageSettings(
            defaults,
            True,
            tuple(document.items()),
            source_revision,
            target if real_path else None,
        )
    if version > PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION:
        return LoadedProviderUsageSettings(
            defaults,
            True,
            tuple(document.items()),
            source_revision,
            target if real_path else None,
        )

    by_identity: dict[tuple[str, str], dict[str, object]] = {}
    additional_identities: list[tuple[str, str]] = []
    raw_rows = document.get("providers", [])
    if isinstance(raw_rows, list):
        for row in raw_rows:
            if isinstance(row, dict) and isinstance(row.get("provider_id"), str):
                provider_id = row["provider_id"]
                source_instance_id = row.get(
                    "source_instance_id",
                    DEFAULT_PROVIDER_INSTANCE_SOURCE_ID,
                )
                try:
                    identity = ProviderInstanceKey(
                        provider_id,
                        source_instance_id,
                    ).value
                    provider_descriptor(provider_id)
                except (ProviderInstanceError, KeyError, TypeError, ValueError):
                    continue
                if identity not in by_identity:
                    by_identity[identity] = row
                    if identity[1] != DEFAULT_PROVIDER_INSTANCE_SOURCE_ID:
                        additional_identities.append(identity)
    default_preferences = tuple(
        _preference_from_payload(
            default.provider_id,
            DEFAULT_PROVIDER_INSTANCE_SOURCE_ID,
            by_identity.get(default.identity),
            default,
        )
        for default in defaults.providers
    )
    additional_preferences = tuple(
        _preference_from_payload(
            provider_id,
            source_instance_id,
            by_identity[(provider_id, source_instance_id)],
            replace(
                defaults.preference(provider_id),
                source_instance_id=source_instance_id,
                label=None,
            ),
        )
        for provider_id, source_instance_id in additional_identities
    )
    settings = ProviderUsageSettings(
        PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION,
        default_preferences + additional_preferences,
        _menu_display_from_payload(document.get("menu_display")),
    )
    unknown = tuple(
        (key, value)
        for key, value in document.items()
        if key not in {"settings_schema_version", "providers", "menu_display"}
    )
    return LoadedProviderUsageSettings(
        settings,
        False,
        unknown,
        source_revision,
        target if real_path else None,
    )


def _settings_document(
    settings: ProviderUsageSettings,
    unknown_fields: tuple[tuple[str, object], ...],
) -> dict[str, object]:
    document = dict(unknown_fields)
    document["settings_schema_version"] = PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION
    document["providers"] = [
        {
            "provider_id": preference.provider_id,
            "source_instance_id": preference.source_instance_id,
            "enabled": preference.enabled,
            "browser_sources": preference.browser_sources,
            "reset_celebrations": preference.reset_celebrations,
            "threshold_remaining": preference.threshold_remaining,
            "options": dict(preference.options),
            "menu_visible": preference.menu_visible,
            "label": preference.label,
            "color_override": preference.color_override,
            "retention_days": preference.retention_days,
            "remote_sharing_choice": preference.remote_sharing_choice,
            "open_session_action": preference.open_session_action,
            "consent_reference": preference.consent_reference,
            "credential_account_reference": preference.credential_account_reference,
        }
        for preference in settings.providers
    ]
    document["menu_display"] = {
        flag: getattr(settings.menu_display, flag)
        for flag in MENU_USAGE_DISPLAY_FLAGS
    }
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
    target = (default_provider_usage_settings_path() if path is None else Path(path)).expanduser().absolute()
    if loaded is not None and loaded.source_path == target:
        try:
            current_revision = _document_digest(_read_real_document(target))
        except FileNotFoundError:
            current_revision = None
        except (OSError, UnicodeError, ValueError) as exc:
            raise ProviderUsageSettingsWriteRefusedError(
                "provider usage settings could not be verified; reload before saving"
            ) from exc
        if current_revision != loaded.source_revision:
            raise ProviderUsageSettingsWriteRefusedError(
                "provider usage settings changed after they were loaded; reload before saving"
            )
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
    "MENU_USAGE_DISPLAY_FLAGS",
    "PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION",
    "LoadedProviderUsageSettings",
    "MenuUsageDisplay",
    "ProviderPreference",
    "ProviderUsageSettings",
    "ProviderUsageSettingsError",
    "ProviderUsageSettingsWriteRefusedError",
    "default_provider_usage_settings",
    "default_provider_usage_settings_path",
    "load_provider_usage_settings",
    "save_provider_usage_settings",
]
