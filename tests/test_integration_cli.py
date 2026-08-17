from __future__ import annotations

import pytest

from sidepulse.integration_cli import build_parser


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
        ]
    )
    probed = parser.parse_args(["probe", "t3code", "--json"])

    assert enabled.integration == "t3code"
    assert enabled.enabled is True
    assert str(configured.base_dir) == "/tmp/t3"
    assert configured.environment_id == "local"
    assert probed.integration == "t3code"
    assert probed.json is True


def test_integration_cli_rejects_codexbar() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["enable", "codexbar"])


def test_status_document_exposes_only_t3_compatibility_window() -> None:
    from sidepulse.integration_cli import _status_document
    from sidepulse.integration_settings import load_integration_settings

    document = _status_document(load_integration_settings())

    assert document["t3code"]["minimumVersion"] == "0.0.33"
    assert document["t3code"]["maximumTestedVersion"] == "0.0.33"
    assert document["t3code"]["connectionMode"] == "sqlite-readonly-v1"
    assert "codexbar" not in document
