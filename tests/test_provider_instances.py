from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from sidepulse.provider_instances import (
    OPEN_SESSION_ACTION_CHOICES,
    PROVIDER_INSTANCE_PROFILE_SCHEMA_VERSION,
    REMOTE_SHARING_CHOICES,
    ProviderInstanceError,
    ProviderInstanceFutureSchemaError,
    ProviderInstanceKey,
    ProviderInstanceProfile,
    default_provider_instance_key,
    default_provider_instance_profile,
    deserialize_provider_instance_profile,
    migrate_legacy_provider_document,
    provider_instance_profile_document,
    serialize_provider_instance_profile,
)


def _key(source: str = "personal") -> ProviderInstanceKey:
    return ProviderInstanceKey("codex", source)


def _profile(**changes: object) -> ProviderInstanceProfile:
    values: dict[str, object] = {
        "key": _key(),
        "label": "Personal Codex",
        "color_override": "#5E5CE6",
        "retention_days": 30,
        "remote_sharing_choice": "never",
        "open_session_action": "app",
        "consent_reference": "consent:codex:personal",
        "credential_account_reference": "keychain:codex:personal",
    }
    values.update(changes)
    return ProviderInstanceProfile(**values)


def test_same_provider_instances_are_distinct_routing_keys() -> None:
    personal = _key("personal")
    work = _key("work")

    assert personal != work
    assert {personal: "personal", work: "work"}[personal] == "personal"
    assert personal.provider_id.value == "codex"
    assert personal.source_instance_id.value == "personal"


def test_default_profile_uses_safe_v1_choices_and_explicit_legacy_instance() -> None:
    profile = default_provider_instance_profile("codex")

    assert profile.key == ProviderInstanceKey("codex", "default")
    assert profile.remote_sharing_choice == "never"
    assert profile.open_session_action == "app"


@pytest.mark.parametrize(
    "source_instance_id",
    (
        "person@example.com",
        "/Users/person/.codex",
        "../../credentials",
        "Bearer-secret-token",
        "api_key_123456",
        "refresh_token",
    ),
)
def test_instance_key_rejects_email_path_and_secret_like_identity(
    source_instance_id: str,
) -> None:
    with pytest.raises(ProviderInstanceError):
        ProviderInstanceKey("codex", source_instance_id)


def test_profile_is_immutable_and_validates_bounded_choices() -> None:
    profile = _profile()

    with pytest.raises(FrozenInstanceError):
        profile.label = "Work"  # type: ignore[misc]
    with pytest.raises(ProviderInstanceError):
        replace(profile, retention_days=365)
    with pytest.raises(ProviderInstanceError):
        replace(profile, color_override="not-a-color")


@pytest.mark.parametrize("remote_sharing_choice", REMOTE_SHARING_CHOICES)
@pytest.mark.parametrize("open_session_action", OPEN_SESSION_ACTION_CHOICES)
def test_profile_accepts_only_explicit_remote_and_session_choices(
    remote_sharing_choice: str,
    open_session_action: str,
) -> None:
    profile = _profile(
        remote_sharing_choice=remote_sharing_choice,
        open_session_action=open_session_action,
    )

    assert profile.remote_sharing_choice == remote_sharing_choice
    assert profile.open_session_action == open_session_action


@pytest.mark.parametrize(
    "field, value",
    (
        ("remote_sharing_choice", "sometimes"),
        ("remote_sharing_choice", "status-only"),
        ("open_session_action", "open"),
        ("open_session_action", "browser"),
    ),
)
def test_profile_rejects_unbounded_remote_and_session_choices(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ProviderInstanceError):
        _profile(**{field: value})


def test_profile_document_is_deterministic_and_preserves_unknown_fields() -> None:
    profile = _profile(
        unknown_fields=(("future_extension", {"enabled": True}),),
    )

    first = serialize_provider_instance_profile(profile)
    second = serialize_provider_instance_profile(profile)

    assert first == second
    assert json.loads(first) == {
        "color_override": "#5E5CE6",
        "consent_reference": "consent:codex:personal",
        "credential_account_reference": "keychain:codex:personal",
        "future_extension": {"enabled": True},
        "label": "Personal Codex",
        "open_session_action": "app",
        "provider_id": "codex",
        "remote_sharing_choice": "never",
        "retention_days": 30,
        "schema_version": PROVIDER_INSTANCE_PROFILE_SCHEMA_VERSION,
        "source_instance_id": "personal",
    }

    loaded = deserialize_provider_instance_profile(first)
    assert loaded.profile == profile
    assert loaded.read_only is False
    assert provider_instance_profile_document(loaded.profile)["future_extension"] == {
        "enabled": True
    }


def test_secret_like_references_and_unknown_fields_never_serialize_or_repr() -> None:
    with pytest.raises(ProviderInstanceError):
        _profile(credential_account_reference="sk-live-secret-token")

    profile = _profile(
        unknown_fields=(
            ("future_extension", {"safe": True}),
            ("access_token", "do-not-persist"),
        )
    )
    encoded = serialize_provider_instance_profile(profile)

    assert "do-not-persist" not in encoded
    assert "access_token" not in encoded
    assert "do-not-persist" not in repr(profile)


def test_opaque_credential_reference_is_allowed_but_raw_credential_is_not() -> None:
    profile = _profile(credential_account_reference="credential-ref:work-account")

    assert profile.credential_account_reference == "credential-ref:work-account"
    assert "credential-ref:work-account" in serialize_provider_instance_profile(profile)


def test_nested_secret_like_values_are_removed_from_unknown_fields() -> None:
    profile = _profile(
        unknown_fields=(
            (
                "future_extension",
                {
                    "safe": True,
                    "nested": {
                        "display": "Bearer opaque-secret-value",
                        "password_value": "password123",
                        "still_safe": "status-only",
                    },
                    "items": ["sk-live-not-for-persistence", "token123", "kept"],
                },
            ),
        )
    )

    document = provider_instance_profile_document(profile)

    assert document["future_extension"] == {
        "safe": True,
        "nested": {"still_safe": "status-only"},
        "items": ["kept"],
    }
    encoded = serialize_provider_instance_profile(profile)
    assert "opaque-secret-value" not in encoded
    assert "sk-live-not-for-persistence" not in encoded
    assert "password123" not in encoded
    assert "token123" not in encoded


def test_unknown_extension_values_must_be_json_compatible_for_determinism() -> None:
    profile = _profile(unknown_fields=(("future_extension", object()),))

    with pytest.raises(ProviderInstanceError):
        serialize_provider_instance_profile(profile)


def test_future_schema_is_read_only_and_cannot_be_serialized() -> None:
    future = {
        "schema_version": PROVIDER_INSTANCE_PROFILE_SCHEMA_VERSION + 1,
        "provider_id": "codex",
        "source_instance_id": "personal",
        "label": "Personal Codex",
        "future_only": {"value": True},
    }

    loaded = deserialize_provider_instance_profile(future)
    assert loaded.read_only is True
    assert loaded.unknown_fields[-1] == ("future_only", {"value": True})
    with pytest.raises(ProviderInstanceFutureSchemaError):
        serialize_provider_instance_profile(loaded.profile)


def test_legacy_provider_document_gets_explicit_default_instance_without_losing_fields() -> None:
    migrated = migrate_legacy_provider_document(
        {
            "provider_id": "codex",
            "label": "Codex",
            "legacy_setting": {"keep": True},
        }
    )

    assert migrated["provider_id"] == "codex"
    assert migrated["source_instance_id"] == "default"
    assert migrated["legacy_setting"] == {"keep": True}
    assert default_provider_instance_key("codex") == ProviderInstanceKey(
        "codex", "default"
    )
