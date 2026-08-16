from __future__ import annotations

import json
from pathlib import Path

import pytest

from sidepulse.integration_settings import (
    INTEGRATION_SETTINGS_SCHEMA_VERSION,
    IntegrationSettingsConcurrentWriteError,
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
    updated = (
        loaded.settings.with_enabled("t3code", True)
        .with_t3code(base_dir="/tmp/t3", environment_id="local")
        .with_enabled("codexbar", True)
        .with_codexbar(identity="full", connection_mode="dashboard")
    )

    save_integration_settings(updated, target, loaded=loaded)
    document = json.loads(target.read_text(encoding="utf-8"))

    assert document["future_extension"] == {"preserve": True}
    assert document["t3code_enabled"] is True
    assert document["t3code_base_dir"] == "/tmp/t3"
    assert document["t3code_environment_id"] == "local"
    assert document["codexbar_enabled"] is True
    assert document["codexbar_identity"] == "full"
    assert document["codexbar_connection_mode"] == "dashboard"


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
            loaded.settings.with_enabled("codexbar", True),
            target,
            loaded=loaded,
        )

    assert json.loads(target.read_text(encoding="utf-8")) == external


def test_t3_partial_update_does_not_clear_the_other_field() -> None:
    configured = load_integration_settings().settings.with_t3code(
        base_dir="/tmp/t3",
        environment_id="env-a",
    )

    base_updated = configured.with_t3code(base_dir="/tmp/t3-new")
    environment_updated = configured.with_t3code(environment_id="env-b")

    assert base_updated.t3code_base_dir == "/tmp/t3-new"
    assert base_updated.t3code_environment_id == "env-a"
    assert environment_updated.t3code_base_dir == "/tmp/t3"
    assert environment_updated.t3code_environment_id == "env-b"


def test_malformed_integration_settings_fail_closed_read_only(
    tmp_path: Path,
) -> None:
    target = tmp_path / "integrations.json"
    target.write_text("{not-json", encoding="utf-8")

    loaded = load_integration_settings(target)

    assert loaded.compatibility.read_only is True
    assert loaded.settings.t3code_enabled is False
    assert loaded.settings.codexbar_enabled is False
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
