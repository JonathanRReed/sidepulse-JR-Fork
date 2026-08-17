from __future__ import annotations

import sys
from types import SimpleNamespace

from sidepulse import cli_entry


def test_public_cli_routes_provider_integration_and_foreground_status_commands(
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
        "provider_main",
        lambda args: calls.append(("providers", args)) or 19,
    )
    monkeypatch.setattr(
        cli_entry,
        "_legacy_sidepulse_main",
        lambda args: calls.append(("legacy", args)) or 23,
    )
    monkeypatch.setitem(
        sys.modules,
        "sidepulse.provider_usage_status_bar",
        SimpleNamespace(main=lambda: calls.append(("status-bar", ())) or 29),
    )

    assert cli_entry.sidepulse_main(["integrations", "status", "--json"]) == 17
    assert cli_entry.sidepulse_main(["providers", "status", "--json"]) == 19
    assert cli_entry.sidepulse_main(["status-bar", "--foreground"]) == 29
    assert cli_entry.sidepulse_main(["status-bar", "start", "--foreground"]) == 29
    assert cli_entry.sidepulse_main(["doctor", "--json"]) == 23
    assert calls == [
        ("integrations", ["status", "--json"]),
        ("providers", ["status", "--json"]),
        ("status-bar", ()),
        ("status-bar", ()),
        ("legacy", ["doctor", "--json"]),
    ]


def test_packaged_application_uses_public_router_and_native_usage_host() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "packaging"
        / "sidepulse_entry.py"
    ).read_text(encoding="utf-8")

    assert "from sidepulse.cli_entry import sidepulse_main" in source
    assert "from sidepulse.cli import sidepulse_main" not in source
    assert (
        "from sidepulse.provider_usage_status_bar import main as status_bar_main"
        in source
    )
