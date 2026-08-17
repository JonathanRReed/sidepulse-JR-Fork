from __future__ import annotations

from sidepulse import provider_usage_cli_router


def test_sync_subcommand_routes_to_sync_cli(monkeypatch):
    calls = []
    monkeypatch.setattr(
        provider_usage_cli_router,
        "provider_main",
        lambda args: calls.append(("providers", args)) or 17,
    )
    monkeypatch.setattr(
        provider_usage_cli_router,
        "sync_main",
        lambda args: calls.append(("sync", args)) or 19,
    )
    assert provider_usage_cli_router.main(["sync", "status", "--json"]) == 19
    assert provider_usage_cli_router.main(["status", "--json"]) == 17
    assert calls == [
        ("sync", ["status", "--json"]),
        ("providers", ["status", "--json"]),
    ]
