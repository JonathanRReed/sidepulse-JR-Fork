"""Bounded identity and local profile data for multiple provider instances.

This module deliberately has no provider, settings, UI, credential, or network
integration.  An instance key is only a routing identity.  Credential and
consent references are opaque handles owned by their respective subsystems,
never secret values.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from .provider_contracts import ProviderIdentifier, SourceInstanceIdentifier

PROVIDER_INSTANCE_PROFILE_SCHEMA_VERSION: Final = 1
DEFAULT_PROVIDER_INSTANCE_SOURCE_ID: Final = "default"
MAX_PROVIDER_INSTANCE_LABEL_LENGTH: Final = 128
MAX_PROVIDER_INSTANCE_REFERENCE_LENGTH: Final = 128

# Keep these vocabularies local.  They are data-only settings contracts and
# importing a UI/controller module here would make profile decoding AppKit
# dependent.  The session values intentionally match ``session_actions``.
REMOTE_SHARING_NEVER: Final = "never"
REMOTE_SHARING_STATUS_ONLY: Final = "status_only"
REMOTE_SHARING_CHOICES: Final = (REMOTE_SHARING_NEVER, REMOTE_SHARING_STATUS_ONLY)
REMOTE_SHARING_POLICY_CHOICES: Final = REMOTE_SHARING_CHOICES

OPEN_SESSION_APP: Final = "app"
OPEN_SESSION_TERMINAL: Final = "terminal"
OPEN_SESSION_VSCODE: Final = "vscode"
OPEN_SESSION_ACTION_CHOICES: Final = (
    OPEN_SESSION_APP,
    OPEN_SESSION_TERMINAL,
    OPEN_SESSION_VSCODE,
)
# Alternate spelling used by a few settings callers.
SESSION_OPEN_ACTION_CHOICES: Final = OPEN_SESSION_ACTION_CHOICES
SESSION_OPEN_CHOICES: Final = OPEN_SESSION_ACTION_CHOICES

_COLOR = re.compile(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?\Z")
_REFERENCE = re.compile(r"[A-Za-z][A-Za-z0-9._:~-]{0,127}\Z")
_SECRET_WORDS = frozenset(
    {
        "access_token",
        "apikey",
        "api_key",
        "bearer",
        "client_secret",
        "credential",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "secret",
        "token",
    }
)
_VALUE_SECRET_WORDS = _SECRET_WORDS - {"credential"}
_SECRET_PREFIX = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:sk|pk)[-_][a-z0-9]|"
    r"(?:gh[opsu]|github_pat|npm|pypi|hf)[_-][a-z0-9]|"
    r"xox[baprs]-[a-z0-9]|AIza[a-z0-9]"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer[ \t]+[^\s]{8,}")
_JWT_VALUE = re.compile(r"\AeyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.")
_NAMED_SECRET_VALUE = re.compile(
    r"(?i)\b(?:password|passwd|secret|token)(?:[0-9]{3,}|[-_:=][^\s]{3,})\b"
)
_RESERVED_FIELDS = frozenset(
    {
        "schema_version",
        "provider_id",
        "source_instance_id",
        "label",
        "color_override",
        "retention_days",
        "remote_sharing_choice",
        "open_session_action",
        "consent_reference",
        "credential_account_reference",
    }
)


class ProviderInstanceError(ValueError):
    """A provider-instance identity or profile is invalid."""


class ProviderInstanceFutureSchemaError(ProviderInstanceError):
    """A profile was written by a newer schema and must not be overwritten."""


class ProviderInstanceWriteRefusedError(ProviderInstanceFutureSchemaError):
    """Compatibility name for callers that distinguish refused writes."""


@dataclass(frozen=True, slots=True)
class ProviderInstanceKey:
    """The stable, non-secret routing identity of one provider instance."""

    provider_id: ProviderIdentifier | str
    source_instance_id: SourceInstanceIdentifier | str

    def __post_init__(self) -> None:
        provider = self.provider_id
        source = self.source_instance_id
        if isinstance(provider, str):
            try:
                provider = ProviderIdentifier(provider)
            except (TypeError, ValueError) as exc:
                raise ProviderInstanceError("invalid provider instance provider id") from exc
        if isinstance(source, str):
            try:
                source = SourceInstanceIdentifier(source)
            except (TypeError, ValueError) as exc:
                raise ProviderInstanceError("invalid provider instance source id") from exc
        if not isinstance(provider, ProviderIdentifier) or not isinstance(
            source, SourceInstanceIdentifier
        ):
            raise ProviderInstanceError("provider instance key requires typed identifiers")
        _reject_sensitive_identity(provider.value)
        _reject_sensitive_identity(source.value)
        object.__setattr__(self, "provider_id", provider)
        object.__setattr__(self, "source_instance_id", source)

    @property
    def value(self) -> tuple[str, str]:
        """A safe pair suitable for dictionary keys and diagnostics."""
        return self.provider_id.value, self.source_instance_id.value

    def __repr__(self) -> str:
        return (
            "ProviderInstanceKey(provider_id="
            f"{self.provider_id.value!r}, source_instance_id={self.source_instance_id.value!r})"
        )


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ProviderInstanceProfile:
    """Immutable user choices associated with one provider instance."""

    key: ProviderInstanceKey
    label: str
    color_override: str | None = None
    retention_days: int = 7
    remote_sharing_choice: str = "never"
    open_session_action: str = OPEN_SESSION_APP
    consent_reference: str | None = None
    credential_account_reference: str | None = None
    schema_version: int = PROVIDER_INSTANCE_PROFILE_SCHEMA_VERSION
    unknown_fields: tuple[tuple[str, Any], ...] = ()

    def __init__(
        self,
        key: ProviderInstanceKey | None = None,
        label: str | None = None,
        color_override: str | None = None,
        retention_days: int = 7,
        remote_sharing_choice: str = "never",
        open_session_action: str = OPEN_SESSION_APP,
        consent_reference: str | None = None,
        credential_account_reference: str | None = None,
        schema_version: int = PROVIDER_INSTANCE_PROFILE_SCHEMA_VERSION,
        unknown_fields: tuple[tuple[str, Any], ...] = (),
        *,
        instance_key: ProviderInstanceKey | None = None,
        display_label: str | None = None,
        accent_color: str | None = None,
        retention: int | None = None,
        remote_sharing: str | None = None,
        consent: str | None = None,
        credential_account: str | None = None,
    ) -> None:
        """Accept the canonical names and their explicit-domain aliases."""
        resolved_key = key if key is not None else instance_key
        resolved_label = label if label is not None else display_label
        resolved_color = color_override if color_override is not None else accent_color
        resolved_retention = retention_days if retention is None else retention
        resolved_remote = remote_sharing_choice if remote_sharing is None else remote_sharing
        resolved_consent = consent_reference if consent_reference is not None else consent
        resolved_account = (
            credential_account_reference
            if credential_account_reference is not None
            else credential_account
        )
        object.__setattr__(self, "key", resolved_key)
        object.__setattr__(self, "label", resolved_label)
        object.__setattr__(self, "color_override", resolved_color)
        object.__setattr__(self, "retention_days", resolved_retention)
        object.__setattr__(self, "remote_sharing_choice", resolved_remote)
        object.__setattr__(self, "open_session_action", open_session_action)
        object.__setattr__(self, "consent_reference", resolved_consent)
        object.__setattr__(self, "credential_account_reference", resolved_account)
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "unknown_fields", unknown_fields)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.key, ProviderInstanceKey):
            raise ProviderInstanceError("profile requires a provider instance key")
        if (
            not isinstance(self.label, str)
            or not self.label.strip()
            or len(self.label) > MAX_PROVIDER_INSTANCE_LABEL_LENGTH
            or any(ord(char) < 32 for char in self.label)
        ):
            raise ProviderInstanceError("invalid provider instance label")
        if self.color_override is not None and (
            not isinstance(self.color_override, str) or _COLOR.fullmatch(self.color_override) is None
        ):
            raise ProviderInstanceError("invalid provider instance color override")
        if type(self.retention_days) is not int or self.retention_days not in {0, 7, 30, 90}:
            raise ProviderInstanceError("unsupported provider instance retention")
        _validate_bounded_choice(
            self.remote_sharing_choice,
            REMOTE_SHARING_CHOICES,
            "remote sharing choice",
        )
        _validate_bounded_choice(
            self.open_session_action,
            OPEN_SESSION_ACTION_CHOICES,
            "open-session action",
        )
        _validate_reference(self.consent_reference, "consent reference")
        _validate_reference(self.credential_account_reference, "credential account reference")
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ProviderInstanceError("invalid provider instance profile schema")
        if type(self.unknown_fields) is not tuple:
            raise ProviderInstanceError("unknown profile fields must be a tuple")
        for item in self.unknown_fields:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], str)
                or item[0] in _RESERVED_FIELDS
            ):
                raise ProviderInstanceError("invalid unknown profile field")

    @property
    def instance_key(self) -> ProviderInstanceKey:
        """Alias used by callers that name the identity field explicitly."""
        return self.key

    @property
    def display_label(self) -> str:
        return self.label

    @property
    def accent_color(self) -> str | None:
        return self.color_override

    @property
    def retention(self) -> int:
        return self.retention_days

    @property
    def remote_sharing(self) -> str:
        return self.remote_sharing_choice

    @property
    def credential_account(self) -> str | None:
        return self.credential_account_reference

    @property
    def consent(self) -> str | None:
        return self.consent_reference

    def to_document(self) -> dict[str, Any]:
        return provider_instance_profile_document(self)

    def to_dict(self) -> dict[str, Any]:
        return self.to_document()

    def to_json(self) -> str:
        return serialize_provider_instance_profile(self)

    @classmethod
    def from_document(
        cls, value: str | Mapping[str, Any]
    ) -> LoadedProviderInstanceProfile:
        return deserialize_provider_instance_profile(value)

    def __repr__(self) -> str:
        return (
            "ProviderInstanceProfile("
            f"key={self.key!r}, label={self.label!r}, color_override={self.color_override!r}, "
            f"retention_days={self.retention_days!r}, remote_sharing_choice={self.remote_sharing_choice!r}, "
            f"open_session_action={self.open_session_action!r}, consent_reference='<redacted>', "
            "credential_account_reference='<redacted>')"
        )


@dataclass(frozen=True, slots=True)
class LoadedProviderInstanceProfile:
    """A decoded profile plus persistence state needed for safe writes."""

    profile: ProviderInstanceProfile
    read_only: bool = False
    unknown_fields: tuple[tuple[str, Any], ...] = ()


def _reject_sensitive_identity(value: str) -> None:
    lowered = value.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    if (
        "@" in value
        or "/" in value
        or "\\" in value
        or value.startswith((".", "~"))
        or lowered.startswith(("sk-", "ghp_", "xoxb-", "AIza".lower()))
        or _looks_like_secret_value(value)
        or any(
            word in re.split(r"[^a-z0-9]+", lowered)
            or word in normalized
            or word.replace("_", "") in normalized.replace("_", "")
            for word in _SECRET_WORDS
        )
        or lowered.startswith("eyj")
    ):
        raise ProviderInstanceError("instance identity must not contain email, path, or secret material")


def _validate_reference(value: str | None, description: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_PROVIDER_INSTANCE_REFERENCE_LENGTH
        or _REFERENCE.fullmatch(value) is None
        or "@" in value
        or "/" in value
        or "\\" in value
    ):
        raise ProviderInstanceError(f"invalid {description}")
    # References are opaque account/keychain handles, so a field marker such
    # as ``credential-ref`` is allowed.  Credential-shaped values themselves
    # still fail closed, including known token prefixes and bearer/JWT forms.
    if _looks_like_secret_value(value):
        raise ProviderInstanceError(f"invalid {description}")


def _validate_bounded_choice(
    value: str,
    choices: tuple[str, ...],
    description: str,
) -> None:
    if value not in choices:
        raise ProviderInstanceError(f"invalid {description}")


def default_provider_instance_key(provider_id: ProviderIdentifier | str) -> ProviderInstanceKey:
    """Return the explicit instance identity for a legacy provider-only record."""
    return ProviderInstanceKey(provider_id, DEFAULT_PROVIDER_INSTANCE_SOURCE_ID)


legacy_default_instance_key = default_provider_instance_key


def default_provider_instance_profile(
    provider_id: ProviderIdentifier | str,
    *,
    label: str | None = None,
) -> ProviderInstanceProfile:
    key = default_provider_instance_key(provider_id)
    return ProviderInstanceProfile(key, label or key.provider_id.value.title())


def _is_sensitive_field(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return normalized in _SECRET_WORDS or any(
        f"_{word}" in f"_{normalized}" or normalized.endswith(word) for word in _SECRET_WORDS
    )


def _looks_like_secret_value(value: str) -> bool:
    """Identify credential-shaped strings without rejecting opaque handles."""
    lowered = value.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    if (
        _SECRET_PREFIX.search(value)
        or _BEARER_VALUE.search(value)
        or _JWT_VALUE.search(value)
        or _NAMED_SECRET_VALUE.search(value)
    ):
        return True
    return any(
        normalized == word
        or normalized.startswith(f"{word}_")
        or normalized.endswith(f"_{word}")
        or f"_{word}_" in normalized
        for word in _VALUE_SECRET_WORDS
    )


_DROP_UNKNOWN = object()


def _sanitize_unknown(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or _is_sensitive_field(key):
                continue
            sanitized = _sanitize_unknown(item)
            if sanitized is not _DROP_UNKNOWN:
                result[key] = sanitized
        return result
    if isinstance(value, (list, tuple)):
        return [
            sanitized
            for item in value
            if (sanitized := _sanitize_unknown(item)) is not _DROP_UNKNOWN
        ]
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and _looks_like_secret_value(value):
            return _DROP_UNKNOWN
        return value
    raise ProviderInstanceError("unknown profile fields must contain JSON values")


def _safe_unknown_fields(
    unknown_fields: Mapping[str, Any] | tuple[tuple[str, Any], ...] | None,
) -> tuple[tuple[str, Any], ...]:
    if unknown_fields is None:
        return ()
    source = unknown_fields.items() if isinstance(unknown_fields, Mapping) else unknown_fields
    result: list[tuple[str, Any]] = []
    for item in source:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        name, value = item
        if not isinstance(name, str) or name in _RESERVED_FIELDS or _is_sensitive_field(name):
            continue
        sanitized = _sanitize_unknown(value)
        if sanitized is not _DROP_UNKNOWN:
            result.append((name, sanitized))
    return tuple(sorted(result, key=lambda entry: entry[0]))


def provider_instance_profile_document(
    profile: ProviderInstanceProfile,
    *,
    unknown_fields: Mapping[str, Any] | tuple[tuple[str, Any], ...] | None = None,
) -> dict[str, Any]:
    """Build a JSON-compatible profile document with stable field meanings."""
    if profile.schema_version > PROVIDER_INSTANCE_PROFILE_SCHEMA_VERSION:
        raise ProviderInstanceFutureSchemaError("future provider instance profile is read-only")
    fields = _safe_unknown_fields(profile.unknown_fields if unknown_fields is None else unknown_fields)
    document: dict[str, Any] = {name: value for name, value in fields}
    document.update(
        {
            "schema_version": profile.schema_version,
            "provider_id": profile.key.provider_id.value,
            "source_instance_id": profile.key.source_instance_id.value,
            "label": profile.label,
            "color_override": profile.color_override,
            "retention_days": profile.retention_days,
            "remote_sharing_choice": profile.remote_sharing_choice,
            "open_session_action": profile.open_session_action,
            "consent_reference": profile.consent_reference,
            "credential_account_reference": profile.credential_account_reference,
        }
    )
    return document


def serialize_provider_instance_profile(
    profile: ProviderInstanceProfile,
    *,
    unknown_fields: Mapping[str, Any] | tuple[tuple[str, Any], ...] | None = None,
) -> str:
    """Serialize a profile canonically, omitting secret-like extension fields."""
    try:
        return json.dumps(
            provider_instance_profile_document(profile, unknown_fields=unknown_fields),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except ProviderInstanceFutureSchemaError:
        raise
    except (TypeError, ValueError) as exc:
        raise ProviderInstanceError("provider instance profile is not canonical JSON") from exc


def _document_value(value: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ProviderInstanceError("invalid provider instance profile JSON") from exc
    if not isinstance(value, Mapping):
        raise ProviderInstanceError("provider instance profile must be an object")
    return value


def deserialize_provider_instance_profile(
    value: str | Mapping[str, Any],
) -> LoadedProviderInstanceProfile:
    """Decode a profile; newer schemas become read-only instead of being rewritten."""
    document = _document_value(value)
    schema_version = document.get("schema_version", PROVIDER_INSTANCE_PROFILE_SCHEMA_VERSION)
    if type(schema_version) is not int or schema_version < 1:
        raise ProviderInstanceError("invalid provider instance profile schema")
    provider_id = document.get("provider_id")
    source_instance_id = document.get("source_instance_id", DEFAULT_PROVIDER_INSTANCE_SOURCE_ID)
    key = ProviderInstanceKey(provider_id, source_instance_id)
    unknown = tuple(
        (name, item)
        for name, item in document.items()
        if name not in _RESERVED_FIELDS
    )
    if schema_version > PROVIDER_INSTANCE_PROFILE_SCHEMA_VERSION:
        profile = ProviderInstanceProfile(
            key,
            document.get("label") if isinstance(document.get("label"), str) and document.get("label") else "Unknown provider",
            color_override=None,
            retention_days=7,
            remote_sharing_choice="never",
            open_session_action=OPEN_SESSION_APP,
            schema_version=schema_version,
            unknown_fields=unknown,
        )
        return LoadedProviderInstanceProfile(profile, True, unknown)
    try:
        profile = ProviderInstanceProfile(
            key,
            document.get("label"),
            color_override=document.get("color_override"),
            retention_days=document.get("retention_days", 7),
            remote_sharing_choice=document.get("remote_sharing_choice", "never"),
            open_session_action=document.get("open_session_action", OPEN_SESSION_APP),
            consent_reference=document.get("consent_reference"),
            credential_account_reference=document.get("credential_account_reference"),
            schema_version=schema_version,
            unknown_fields=unknown,
        )
    except (TypeError, ProviderInstanceError) as exc:
        raise ProviderInstanceError("invalid provider instance profile document") from exc
    return LoadedProviderInstanceProfile(profile, False, unknown)


load_provider_instance_profile = deserialize_provider_instance_profile
profile_from_document = deserialize_provider_instance_profile


def migrate_legacy_provider_document(
    document: Mapping[str, Any],
    provider_id: ProviderIdentifier | str | None = None,
) -> dict[str, Any]:
    """Attach the explicit ``default`` instance to a provider-only document."""
    if not isinstance(document, Mapping):
        raise ProviderInstanceError("legacy provider document must be an object")
    migrated = dict(document)
    chosen_provider = provider_id if provider_id is not None else migrated.get("provider_id")
    if chosen_provider is None:
        raise ProviderInstanceError("legacy provider document has no provider id")
    key = default_provider_instance_key(chosen_provider)
    existing_source = migrated.get("source_instance_id")
    if existing_source is None:
        migrated["source_instance_id"] = key.source_instance_id.value
    else:
        ProviderInstanceKey(key.provider_id, existing_source)
    migrated["provider_id"] = key.provider_id.value
    return migrated


migrate_legacy_instance_document = migrate_legacy_provider_document


__all__ = [
    "DEFAULT_PROVIDER_INSTANCE_SOURCE_ID",
    "OPEN_SESSION_ACTION_CHOICES",
    "OPEN_SESSION_APP",
    "OPEN_SESSION_TERMINAL",
    "OPEN_SESSION_VSCODE",
    "PROVIDER_INSTANCE_PROFILE_SCHEMA_VERSION",
    "REMOTE_SHARING_CHOICES",
    "REMOTE_SHARING_NEVER",
    "REMOTE_SHARING_POLICY_CHOICES",
    "REMOTE_SHARING_STATUS_ONLY",
    "SESSION_OPEN_ACTION_CHOICES",
    "SESSION_OPEN_CHOICES",
    "LoadedProviderInstanceProfile",
    "ProviderInstanceError",
    "ProviderInstanceFutureSchemaError",
    "ProviderInstanceKey",
    "ProviderInstanceProfile",
    "ProviderInstanceWriteRefusedError",
    "default_provider_instance_key",
    "default_provider_instance_profile",
    "deserialize_provider_instance_profile",
    "legacy_default_instance_key",
    "load_provider_instance_profile",
    "migrate_legacy_instance_document",
    "migrate_legacy_provider_document",
    "profile_from_document",
    "provider_instance_profile_document",
    "serialize_provider_instance_profile",
]
