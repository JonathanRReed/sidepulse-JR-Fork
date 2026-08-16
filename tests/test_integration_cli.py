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
