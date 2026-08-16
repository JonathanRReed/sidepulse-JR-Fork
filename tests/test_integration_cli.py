from __future__ import annotations

from sidepulse.integration_cli import build_parser


def test_integration_cli_exposes_configuration_and_probe_commands() -> None:
    parser = build_parser()

    enabled = parser.parse_args(["enable", "t3code"])
    configured = parser.parse_args(
        [
            "configure",
            "codexbar",
            "--identity",
            "redacted",
            "--connection-mode",
            "auto",
        ]
    )
    probed = parser.parse_args(["probe", "codexbar", "--json"])

    assert enabled.integration == "t3code"
    assert enabled.enabled is True
    assert configured.identity == "redacted"
    assert configured.connection_mode == "auto"
    assert probed.integration == "codexbar"
    assert probed.json is True


def test_status_document_exposes_the_packaged_compatibility_window() -> None:
    from sidepulse.integration_cli import _status_document
    from sidepulse.integration_settings import load_integration_settings

    document = _status_document(load_integration_settings())

    assert document["t3code"]["minimumVersion"] == "0.0.33"
    assert document["t3code"]["maximumTestedVersion"] == "0.0.33"
    assert document["t3code"]["connectionMode"] == "sqlite-readonly-v1"
    assert document["codexbar"]["minimumVersion"] == "0.37.2"
    assert document["codexbar"]["maximumTestedVersion"] == "0.50.0"
    assert document["codexbar"]["protocol"] == "dashboard-v1"
