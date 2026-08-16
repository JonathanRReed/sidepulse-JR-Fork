from __future__ import annotations

from sidepulse import cli_entry


def test_public_cli_routes_integration_commands_without_changing_legacy_args(
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        cli_entry,
        "integration_main",
        lambda args: calls.append(("integrations", args)) or 17,
    )
    monkeypatch.setattr(
        cli_entry,
        "_legacy_sidepulse_main",
        lambda args: calls.append(("legacy", args)) or 23,
    )

    assert cli_entry.sidepulse_main(["integrations", "status", "--json"]) == 17
    assert cli_entry.sidepulse_main(["doctor", "--json"]) == 23
    assert calls == [
        ("integrations", ["status", "--json"]),
        ("legacy", ["doctor", "--json"]),
    ]


def test_packaged_application_routes_cli_arguments_through_the_public_router() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "packaging"
        / "sidepulse_entry.py"
    ).read_text(encoding="utf-8")

    assert "from sidepulse.cli_entry import sidepulse_main" in source
    assert "from sidepulse.cli import sidepulse_main" not in source
