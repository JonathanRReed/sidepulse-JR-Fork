from __future__ import annotations

from argparse import Namespace

import pytest

from sidepulse import integration_cli
from sidepulse.integration_cli import build_parser
from sidepulse.integration_settings import IntegrationSettings


def test_integration_cli_exposes_t3_configuration_and_probe_commands() -> None:
    parser = build_parser()

    enabled = parser.parse_args(["enable", "t3code"])
    configured = parser.parse_args(
        [
            "configure",
            "t3code",
            "--base-dir",
            "/tmp/t3",
            "--environment-id",
            "local",
            "--activity-statistics",
        ]
    )
    probed = parser.parse_args(["probe", "t3code", "--json"])

    assert enabled.integration == "t3code"
    assert enabled.enabled is True
    assert str(configured.base_dir) == "/tmp/t3"
    assert configured.environment_id == "local"
    assert configured.activity_statistics is True
    assert probed.integration == "t3code"
    assert probed.json is True


def test_integration_cli_exposes_agent_deck_and_creator_micro_settings() -> None:
    parser = build_parser()

    deck = parser.parse_args(["configure", "agent-deck", "--snapshot-path", "/tmp/deck.json"])
    creator = parser.parse_args(["enable", "creator-micro"])

    assert str(deck.snapshot_path) == "/tmp/deck.json"
    assert creator.integration == "creator-micro"


def test_integration_cli_help_describes_the_shared_compatibility_surface() -> None:
    help_text = build_parser().format_help()

    assert "JR-Bar compatibility integrations" in help_text
    assert "Enable an integration" in help_text
    assert "Disable an integration" in help_text
    assert "Configure an integration" in help_text
    assert "Enable T3 Code" not in help_text


@pytest.mark.parametrize(
    ("integration", "field"),
    [
        ("agent-deck", "agent_deck_enabled"),
        ("creator-micro", "creator_micro_enabled"),
        ("t3code", "t3code_enabled"),
    ],
)
def test_disable_routes_to_the_selected_integration(
    monkeypatch,
    integration: str,
    field: str,
) -> None:
    settings = IntegrationSettings(
        t3code_enabled=True,
        agent_deck_enabled=True,
        creator_micro_enabled=True,
        creator_micro_device_serial="CM2-123",
    )
    loaded = type("Loaded", (), {"settings": settings})()
    saved = []
    monkeypatch.setattr(integration_cli, "load_integration_settings", lambda: loaded)
    monkeypatch.setattr(
        integration_cli,
        "_save_updated",
        lambda _loaded, updated: saved.append(updated) or 0,
    )

    assert integration_cli.cmd_enabled(
        Namespace(integration=integration, enabled=False)
    ) == 0

    updated = saved.pop()
    assert getattr(updated, field) is False
    for other in {"t3code_enabled", "agent_deck_enabled", "creator_micro_enabled"} - {
        field
    }:
        assert getattr(updated, other) is True


def test_creator_micro_enable_approves_the_only_connected_stable_identity(
    monkeypatch,
) -> None:
    from sidepulse import creator_micro_hidapi

    settings = IntegrationSettings()
    loaded = type("Loaded", (), {"settings": settings})()
    saved = []

    class Transport:
        def enumerate(self):
            return [{"serial_number": "CM2-123"}]

    monkeypatch.setattr(integration_cli, "load_integration_settings", lambda: loaded)
    monkeypatch.setattr(creator_micro_hidapi, "HidApiTransport", Transport)
    monkeypatch.setattr(
        integration_cli,
        "_save_updated",
        lambda _loaded, updated: saved.append(updated) or 0,
    )

    assert integration_cli.cmd_enabled(
        Namespace(integration="creator-micro", enabled=True)
    ) == 0
    assert saved[0].creator_micro_enabled is True
    assert saved[0].creator_micro_device_serial == "CM2-123"


def test_integration_cli_rejects_codexbar() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["enable", "codexbar"])


def test_status_document_exposes_only_t3_compatibility_window() -> None:
    from sidepulse.integration_cli import _status_document
    from sidepulse.integration_settings import load_integration_settings

    document = _status_document(load_integration_settings())

    assert document["settingsFileReadOnly"] is False
    assert "readOnly" not in document
    assert document["t3code"]["minimumVersion"] == "0.0.33"
    assert document["t3code"]["maximumTestedVersion"] == "0.0.33"
    assert document["t3code"]["connectionMode"] == "sqlite-readonly-v1"
    assert (
        document["creator-micro"]["connectionMode"]
        == "hidapi-explicit-identity-bound-output"
    )
    assert "codexbar" not in document


def test_disabled_t3_probe_does_not_resolve_or_read_the_t3_database(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        integration_cli,
        "load_integration_settings",
        lambda: type(
            "Loaded",
            (),
            {"settings": type("Settings", (), {"t3code_enabled": False})()},
        )(),
    )
    monkeypatch.setattr(
        integration_cli,
        "_t3_probe_document",
        lambda _settings: pytest.fail("disabled probe touched T3"),
    )
    args = build_parser().parse_args(["probe", "t3code", "--json"])

    assert integration_cli.cmd_probe(args) == 1
