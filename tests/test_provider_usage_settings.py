from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from sidepulse.provider_instances import ProviderInstanceKey, ProviderInstanceProfile
from sidepulse.provider_usage_settings import (
    PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION,
    ProviderUsageSettingsWriteRefusedError,
    default_provider_usage_settings,
    load_provider_usage_settings,
    save_provider_usage_settings,
)


def test_defaults_enable_consumer_providers_but_not_openai_admin() -> None:
    settings = default_provider_usage_settings()
    assert settings.preference("codex").enabled is True
    assert settings.preference("claude").enabled is True
    assert settings.preference("grok").enabled is True
    assert settings.preference("openai-api").enabled is False
    assert all(not item.browser_sources for item in settings.providers)


def test_configuration_updates_are_immutable_and_provider_scoped() -> None:
    original = default_provider_usage_settings()
    updated = (
        original.with_enabled("cursor", False)
        .with_browser_sources("devin", True)
        .with_option("devin", "organization", "org_example")
    )
    assert original.preference("cursor").enabled is True
    assert updated.preference("cursor").enabled is False
    assert updated.preference("devin").browser_sources is True
    assert updated.preference("devin").option("organization") == "org_example"


def test_round_trip_preserves_unknown_fields() -> None:
    original = {
        "settings_schema_version": PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION,
        "future_extension": {"keep": True},
        "providers": [
            {"provider_id": "codex", "enabled": False, "browser_sources": False}
        ],
    }
    loaded = load_provider_usage_settings(reader=lambda _path: json.dumps(original))
    writes = []
    save_provider_usage_settings(
        loaded.settings.with_enabled("claude", False),
        loaded=loaded,
        writer=lambda _path, text: writes.append(json.loads(text)),
    )
    document = writes[0]
    assert document["future_extension"] == {"keep": True}
    assert next(
        row for row in document["providers"] if row["provider_id"] == "codex"
    )["enabled"] is False
    assert next(
        row for row in document["providers"] if row["provider_id"] == "claude"
    )["enabled"] is False


def test_future_schema_is_read_only() -> None:
    loaded = load_provider_usage_settings(
        reader=lambda _path: json.dumps(
            {
                "settings_schema_version": PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION + 1,
                "future": True,
            }
        )
    )
    assert loaded.read_only is True
    try:
        save_provider_usage_settings(
            loaded.settings,
            loaded=loaded,
            writer=lambda _path, _text: None,
        )
    except ProviderUsageSettingsWriteRefusedError:
        pass
    else:
        raise AssertionError("future settings were overwritten")


def test_browser_sources_are_rejected_for_providers_without_browser_support() -> None:
    settings = default_provider_usage_settings()
    try:
        settings.with_browser_sources("antigravity", True)
    except ValueError as exc:
        assert "browser" in str(exc)
    else:
        raise AssertionError("unsupported browser source was enabled")


def test_menu_display_and_visibility_round_trip(tmp_path) -> None:
    path = tmp_path / "provider-usage.json"
    updated = (
        default_provider_usage_settings()
        .with_menu_flag("show_cost", False)
        .with_menu_flag("show_detail_lanes", False)
        .with_menu_flag("privacy_mode", True)
        .with_menu_visible("devin", False)
    )
    save_provider_usage_settings(updated, path)
    loaded = load_provider_usage_settings(path).settings
    assert loaded.menu_display.show_cost is False
    assert loaded.menu_display.show_detail_lanes is False
    assert loaded.menu_display.privacy_mode is True
    assert loaded.menu_display.show_meters is True
    assert loaded.preference("devin").menu_visible is False
    assert loaded.preference("claude").menu_visible is True
    assert loaded.hidden_menu_providers() == frozenset({"devin"})


def test_menu_display_tolerates_old_documents_and_junk(tmp_path) -> None:
    # A pre-menu_display document (and garbage values) must load as the
    # defaults: everything visible, everything shown.
    path = tmp_path / "provider-usage.json"
    path.write_text(
        json.dumps(
            {
                "settings_schema_version": PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION,
                "providers": [
                    {"provider_id": "claude", "menu_visible": "yes-please"},
                ],
                "menu_display": {
                    "show_meters": "definitely",
                    "privacy_mode": "definitely",
                    "surprise": 1,
                },
            }
        )
    )
    loaded = load_provider_usage_settings(path).settings
    assert loaded.menu_display.show_meters is True
    assert loaded.menu_display.privacy_mode is False
    assert loaded.preference("claude").menu_visible is True
    assert loaded.hidden_menu_providers() == frozenset()


def test_menu_flag_rejects_unknown_names() -> None:
    import pytest

    from sidepulse.provider_usage_settings import ProviderUsageSettingsError

    with pytest.raises(ProviderUsageSettingsError):
        default_provider_usage_settings().with_menu_flag("show_everything", True)


def test_loaded_settings_exposes_source_digest_and_refuses_external_edit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider-usage.json"
    save_provider_usage_settings(default_provider_usage_settings(), path)
    loaded = load_provider_usage_settings(path)

    assert loaded.source_digest
    external = json.loads(path.read_text(encoding="utf-8"))
    external["external_owner"] = {"keep": True}
    path.write_text(json.dumps(external), encoding="utf-8")

    with pytest.raises(ProviderUsageSettingsWriteRefusedError):
        save_provider_usage_settings(
            loaded.settings.with_enabled("claude", False),
            path,
            loaded=loaded,
        )
    assert json.loads(path.read_text(encoding="utf-8")) == external


def test_loaded_missing_settings_refuses_file_that_appears_before_save(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider-usage.json"
    loaded = load_provider_usage_settings(path)
    path.write_text('{"owner":"external"}', encoding="utf-8")

    with pytest.raises(ProviderUsageSettingsWriteRefusedError):
        save_provider_usage_settings(loaded.settings, path, loaded=loaded)


def test_loaded_settings_refuses_stale_write_after_sibling_instance_changes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider-usage.json"
    settings = default_provider_usage_settings()
    work = replace(
        settings.preference("claude"),
        source_instance_id="work",
        options=(("account", "work"),),
    )
    settings = settings.with_instance(work)
    save_provider_usage_settings(settings, path)

    loaded = load_provider_usage_settings(path)
    external = json.loads(path.read_text(encoding="utf-8"))
    for row in external["providers"]:
        if row["provider_id"] == "claude" and row["source_instance_id"] == "work":
            row["enabled"] = False
    path.write_text(json.dumps(external), encoding="utf-8")

    with pytest.raises(ProviderUsageSettingsWriteRefusedError):
        save_provider_usage_settings(
            loaded.settings.with_enabled("claude", False, source_instance_id="work"),
            path,
            loaded=loaded,
        )

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert any(
        row["provider_id"] == "claude"
        and row["source_instance_id"] == "work"
        and row["enabled"] is False
        for row in persisted["providers"]
    )


def test_two_same_provider_instances_round_trip_without_collapsing(tmp_path: Path) -> None:
    path = tmp_path / "provider-usage.json"
    settings = default_provider_usage_settings()
    work = replace(
        settings.preference("claude"),
        source_instance_id="work",
        options=(("account", "work"),),
    )
    settings = settings.with_instance(work)

    save_provider_usage_settings(settings, path)
    loaded = load_provider_usage_settings(path).settings

    assert loaded.preference("claude", "default").source_instance_id == "default"
    assert loaded.preference("claude", "work").option("account") == "work"
    assert [row.identity for row in loaded.providers if row.provider_id == "claude"] == [
        ("claude", "default"),
        ("claude", "work"),
    ]


def test_legacy_provider_rows_migrate_to_default_source_instance() -> None:
    loaded = load_provider_usage_settings(
        reader=lambda _path: json.dumps(
            {
                "settings_schema_version": 1,
                "providers": [
                    {
                        "provider_id": "claude",
                        "enabled": False,
                        "browser_sources": False,
                    }
                ],
            }
        )
    ).settings

    assert loaded.preference("claude").source_instance_id == "default"
    assert loaded.preference("claude").enabled is False


def test_instance_mutation_is_exact_and_provider_only_lookup_uses_default() -> None:
    settings = default_provider_usage_settings()
    work = replace(settings.preference("claude"), source_instance_id="work")
    settings = settings.with_instance(work).with_enabled(
        "claude",
        False,
        source_instance_id="work",
    )

    assert settings.preference("claude").enabled is True
    assert settings.preference("claude", "work").enabled is False


def test_two_same_provider_instances_persist_distinct_profiles(tmp_path: Path) -> None:
    path = tmp_path / "provider-usage.json"
    settings = default_provider_usage_settings()
    personal = ProviderInstanceProfile(
        ProviderInstanceKey("claude", "default"),
        label="Claude Personal",
        color_override="#112233",
        retention_days=7,
        remote_sharing_choice="never",
        open_session_action="app",
    )
    work = ProviderInstanceProfile(
        ProviderInstanceKey("claude", "work"),
        label="Claude Work",
        color_override="#445566",
        retention_days=30,
        remote_sharing_choice="status_only",
        open_session_action="terminal",
    )

    updated = settings.with_profile(personal).with_profile(work)
    save_provider_usage_settings(updated, path)
    loaded = load_provider_usage_settings(path).settings

    assert loaded.preference("claude").profile.label == "Claude Personal"
    assert loaded.preference("claude", "work").profile.label == "Claude Work"
    assert loaded.preference("claude").profile.color_override == "#112233"
    assert loaded.preference("claude", "work").profile.color_override == "#445566"
    assert loaded.preference("claude").profile.retention_days == 7
    assert loaded.preference("claude", "work").profile.retention_days == 30
    assert loaded.preference("claude").profile.remote_sharing_choice == "never"
    assert loaded.preference("claude", "work").profile.remote_sharing_choice == "status_only"
    assert loaded.preference("claude").profile.open_session_action == "app"
    assert loaded.preference("claude", "work").profile.open_session_action == "terminal"


def test_profile_references_round_trip_without_exposing_secret_material(tmp_path: Path) -> None:
    path = tmp_path / "provider-usage.json"
    profile = ProviderInstanceProfile(
        ProviderInstanceKey("claude", "work"),
        label="Claude Work",
        consent_reference="consent:claude:work",
        credential_account_reference="keychain:claude:work",
    )

    save_provider_usage_settings(
        default_provider_usage_settings().with_profile(profile),
        path,
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    row = next(
        row
        for row in document["providers"]
        if row["provider_id"] == "claude" and row["source_instance_id"] == "work"
    )
    assert row["consent_reference"] == "consent:claude:work"
    assert row["credential_account_reference"] == "keychain:claude:work"

    loaded = load_provider_usage_settings(path).settings.profile("claude", "work")
    assert loaded.consent_reference == "consent:claude:work"
    assert loaded.credential_account_reference == "keychain:claude:work"
    assert "secret" not in repr(loaded).lower()


def test_legacy_schema_two_rows_migrate_profile_defaults_and_refuse_mismatch() -> None:
    loaded = load_provider_usage_settings(
        reader=lambda _path: json.dumps(
            {
                "settings_schema_version": 2,
                "providers": [
                    {
                        "provider_id": "claude",
                        "enabled": False,
                        "browser_sources": False,
                    }
                ],
            }
        )
    )

    assert loaded.settings.profile("claude").label == "Claude"
    assert loaded.settings.profile("claude").color_override is None
    assert loaded.settings.profile("claude").retention_days == 7
    assert loaded.settings.profile("claude").remote_sharing_choice == "never"
    assert loaded.settings.profile("claude").open_session_action == "app"

    with pytest.raises(ValueError, match="identity"):
        loaded.settings.preference("claude").with_profile(
            ProviderInstanceProfile(
                ProviderInstanceKey("claude", "work"),
                label="Claude Work",
            )
        )


@pytest.mark.parametrize(
    ("field", "invalid", "fallback"),
    (
        ("label", "", "Claude"),
        ("color_override", "not-a-color", None),
        ("retention_days", 8, 7),
        ("remote_sharing_choice", "everything", "never"),
        ("open_session_action", "browser", "app"),
    ),
)
def test_invalid_profile_field_does_not_reset_unrelated_provider_choices(
    field: str,
    invalid: object,
    fallback: object,
) -> None:
    profile_values = {
        "label": "Claude Work",
        "color_override": "#112233",
        "retention_days": 30,
        "remote_sharing_choice": "status_only",
        "open_session_action": "terminal",
    }
    profile_values[field] = invalid
    loaded = load_provider_usage_settings(
        reader=lambda _path: json.dumps(
            {
                "settings_schema_version": PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION,
                "providers": [
                    {
                        "provider_id": "claude",
                        "enabled": False,
                        "browser_sources": False,
                        "menu_visible": False,
                        "threshold_remaining": 33,
                        "options": {"account": "work"},
                        **profile_values,
                    }
                ],
            }
        )
    )

    preference = loaded.settings.preference("claude")
    assert preference.enabled is False
    assert preference.menu_visible is False
    assert preference.threshold_remaining == 33
    assert preference.option("account") == "work"
    assert getattr(preference, field) == fallback
    for valid_field, value in profile_values.items():
        if valid_field != field:
            assert getattr(preference, valid_field) == value


def test_malformed_instance_identity_does_not_discard_valid_rows() -> None:
    loaded = load_provider_usage_settings(
        reader=lambda _path: json.dumps(
            {
                "settings_schema_version": PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION,
                "providers": [
                    {
                        "provider_id": "claude",
                        "source_instance_id": "work",
                        "label": "Claude Work",
                        "menu_visible": False,
                    },
                    {
                        "provider_id": "not-a-provider",
                        "source_instance_id": "work",
                        "label": "Unknown",
                    },
                    {
                        "provider_id": "claude",
                        "source_instance_id": "",
                        "label": "Invalid source",
                    },
                ],
            }
        )
    )

    assert loaded.settings.profile("claude", "work").label == "Claude Work"
    assert loaded.settings.preference("claude", "work").menu_visible is False
    assert tuple(
        preference.identity
        for preference in loaded.settings.providers
        if preference.source_instance_id != "default"
    ) == (("claude", "work"),)
