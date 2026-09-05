from __future__ import annotations

import json
from pathlib import Path

import pytest

from sidepulse.integration_settings import (
    INTEGRATION_SETTINGS_SCHEMA_VERSION,
    IntegrationSettingsConcurrentWriteError,
    IntegrationSettingsError,
    IntegrationSettingsWriteRefusedError,
    load_integration_settings,
    save_integration_settings,
)


def test_integration_settings_round_trip_preserves_unknown_fields(
    tmp_path: Path,
) -> None:
    target = tmp_path / "integrations.json"
    target.write_text(
        json.dumps(
            {
                "settings_schema_version": INTEGRATION_SETTINGS_SCHEMA_VERSION,
                "future_extension": {"preserve": True},
            }
        ),
        encoding="utf-8",
    )
    loaded = load_integration_settings(target)
    updated = loaded.settings.with_enabled("t3code", True).with_t3code(
        base_dir="/tmp/t3",
        environment_id="local",
        activity_statistics_enabled=True,
    )

    save_integration_settings(updated, target, loaded=loaded)
    document = json.loads(target.read_text(encoding="utf-8"))

    assert document["future_extension"] == {"preserve": True}
    assert document["t3code_enabled"] is True
    assert document["t3code_base_dir"] == "/tmp/t3"
    assert document["t3code_environment_id"] == "local"
    assert document["t3code_activity_statistics_enabled"] is True
    assert not any(key.startswith("codexbar_") for key in document)


def test_schema_one_codexbar_settings_migrate_out_on_next_save(tmp_path: Path) -> None:
    target = tmp_path / "integrations.json"
    target.write_text(
        json.dumps(
            {
                "settings_schema_version": 1,
                "t3code_enabled": True,
                "codexbar_enabled": True,
                "codexbar_identity": "full",
                "codexbar_connection_mode": "dashboard",
                "future_extension": "keep",
            }
        ),
        encoding="utf-8",
    )

    loaded = load_integration_settings(target)
    save_integration_settings(loaded.settings, target, loaded=loaded)
    document = json.loads(target.read_text(encoding="utf-8"))

    assert document["settings_schema_version"] == INTEGRATION_SETTINGS_SCHEMA_VERSION
    assert document["t3code_enabled"] is True
    assert document["future_extension"] == "keep"
    assert not any(key.startswith("codexbar_") for key in document)


def test_codexbar_cannot_be_reenabled_through_the_settings_model() -> None:
    settings = load_integration_settings().settings

    with pytest.raises(IntegrationSettingsError):
        settings.with_enabled("codexbar", True)
    assert not hasattr(settings, "codexbar_enabled")
    assert not hasattr(settings, "with_codexbar")


def test_future_integration_settings_are_read_only(tmp_path: Path) -> None:
    target = tmp_path / "integrations.json"
    original = {
        "settings_schema_version": INTEGRATION_SETTINGS_SCHEMA_VERSION + 1,
        "future": "keep",
        "t3code_enabled": True,
    }
    target.write_text(json.dumps(original), encoding="utf-8")

    loaded = load_integration_settings(target)

    assert loaded.compatibility.read_only is True
    with pytest.raises(IntegrationSettingsWriteRefusedError):
        save_integration_settings(loaded.settings, target, loaded=loaded)
    assert json.loads(target.read_text(encoding="utf-8")) == original


def test_external_edit_after_load_is_not_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "integrations.json"
    first = load_integration_settings(target)
    save_integration_settings(first.settings, target, loaded=first)
    loaded = load_integration_settings(target)
    external = json.loads(target.read_text(encoding="utf-8"))
    external["external_owner"] = True
    target.write_text(json.dumps(external), encoding="utf-8")

    with pytest.raises(IntegrationSettingsConcurrentWriteError):
        save_integration_settings(
            loaded.settings.with_enabled("t3code", True),
            target,
            loaded=loaded,
        )

    assert json.loads(target.read_text(encoding="utf-8")) == external


def test_t3_partial_update_does_not_clear_the_other_field() -> None:
    configured = load_integration_settings().settings.with_t3code(
        base_dir="/tmp/t3",
        environment_id="env-a",
        activity_statistics_enabled=True,
    )

    base_updated = configured.with_t3code(base_dir="/tmp/t3-new")
    environment_updated = configured.with_t3code(environment_id="env-b")

    assert base_updated.t3code_base_dir == "/tmp/t3-new"
    assert base_updated.t3code_environment_id == "env-a"
    assert environment_updated.t3code_base_dir == "/tmp/t3"
    assert environment_updated.t3code_environment_id == "env-b"
    assert base_updated.t3code_activity_statistics_enabled is True
    assert environment_updated.t3code_activity_statistics_enabled is True


def test_t3_activity_statistics_are_a_separate_default_off_setting() -> None:
    settings = load_integration_settings().settings

    assert settings.t3code_activity_statistics_enabled is False
    assert settings.with_enabled("t3code", True).t3code_activity_statistics_enabled is False
    assert settings.with_t3code(activity_statistics_enabled=True).t3code_activity_statistics_enabled is True


def test_agent_deck_and_creator_micro_integrations_are_default_off() -> None:
    settings = load_integration_settings().settings

    assert settings.agent_deck_enabled is False
    assert settings.agent_deck_snapshot_path is None
    assert settings.creator_micro_enabled is False
    assert settings.creator_micro_device_serial is None


def test_agent_deck_and_creator_micro_settings_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "integrations.json"
    loaded = load_integration_settings(target)
    configured = loaded.settings.with_agent_deck(
        enabled=True,
        snapshot_path="/tmp/deck-snapshot.json",
    ).with_creator_micro(enabled=True, device_serial="CM2-123")

    save_integration_settings(configured, target, loaded=loaded)
    restored = load_integration_settings(target).settings

    assert restored.agent_deck_enabled is True
    assert restored.agent_deck_snapshot_path == "/tmp/deck-snapshot.json"
    assert restored.creator_micro_enabled is True
    assert restored.creator_micro_device_serial == "CM2-123"


def test_legacy_creator_micro_enablement_without_identity_fails_closed(
    tmp_path: Path,
) -> None:
    target = tmp_path / "integrations.json"
    target.write_text(
        json.dumps(
            {
                "settings_schema_version": 4,
                "creator_micro_enabled": True,
            }
        ),
        encoding="utf-8",
    )

    restored = load_integration_settings(target).settings

    assert restored.creator_micro_enabled is False
    assert restored.creator_micro_device_serial is None


def test_malformed_integration_settings_fail_closed_read_only(
    tmp_path: Path,
) -> None:
    target = tmp_path / "integrations.json"
    target.write_text("{not-json", encoding="utf-8")

    loaded = load_integration_settings(target)

    assert loaded.compatibility.read_only is True
    assert loaded.settings.t3code_enabled is False
    with pytest.raises(IntegrationSettingsWriteRefusedError):
        save_integration_settings(loaded.settings, target, loaded=loaded)


def test_untracked_save_refuses_a_malformed_existing_document(
    tmp_path: Path,
) -> None:
    target = tmp_path / "integrations.json"
    target.write_text("{not-json", encoding="utf-8")

    with pytest.raises(IntegrationSettingsWriteRefusedError):
        save_integration_settings(load_integration_settings().settings, target)

    assert target.read_text(encoding="utf-8") == "{not-json"
