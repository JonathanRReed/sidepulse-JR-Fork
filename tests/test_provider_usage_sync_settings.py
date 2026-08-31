from __future__ import annotations

import json
from pathlib import Path

import pytest

from sidepulse.provider_usage_sync_settings import (
    PROVIDER_SYNC_SETTINGS_SCHEMA_VERSION,
    ProviderSyncSettingsWriteRefusedError,
    default_provider_sync_settings,
    load_provider_sync_settings,
    save_provider_sync_settings,
)


def test_defaults_are_disabled_and_exclude_agent_activity() -> None:
    settings = default_provider_sync_settings()
    assert settings.enabled is False
    assert settings.device_id is None
    assert settings.categories == ("quota", "token_usage")
    assert settings.peers == ()


def test_peer_configuration_is_immutable_and_secret_is_only_a_keychain_reference() -> None:
    original = default_provider_sync_settings().with_device_id("mac-mini")
    updated = original.with_peer(
        peer_id="macbook",
        host="jonathan@macbook.tailnet.example",
        remote_path="~/.local/state/sidepulse/provider-sync/local.packet",
        known_hosts="/Users/jonathan/.ssh/known_hosts",
        identity_file="/Users/jonathan/.ssh/id_ed25519",
        secret_account="pairing-macbook",
    )
    assert original.peers == ()
    assert updated.peers[0].peer_id == "macbook"
    assert updated.peers[0].secret_account == "pairing-macbook"
    assert not hasattr(updated.peers[0], "secret")


def test_agent_activity_is_an_explicit_opt_in_category() -> None:
    settings = default_provider_sync_settings().with_categories(
        ("quota", "token_usage", "agent_activity")
    )
    assert settings.categories[-1] == "agent_activity"


def test_round_trip_preserves_unknown_fields(tmp_path: Path) -> None:
    target = tmp_path / "sync.json"
    target.write_text(
        json.dumps(
            {
                "settings_schema_version": PROVIDER_SYNC_SETTINGS_SCHEMA_VERSION,
                "future_extension": {"keep": True},
                "enabled": False,
                "device_id": None,
                "categories": ["quota", "token_usage"],
                "peers": [],
            }
        )
    )
    loaded = load_provider_sync_settings(target)
    save_provider_sync_settings(
        loaded.settings.with_device_id("mac-mini").with_enabled(True),
        target,
        loaded=loaded,
    )
    document = json.loads(target.read_text())
    assert document["future_extension"] == {"keep": True}
    assert document["enabled"] is True
    assert document["device_id"] == "mac-mini"


def test_future_schema_is_read_only(tmp_path: Path) -> None:
    target = tmp_path / "sync.json"
    target.write_text(
        json.dumps(
            {
                "settings_schema_version": PROVIDER_SYNC_SETTINGS_SCHEMA_VERSION + 1,
                "future": True,
            }
        )
    )
    loaded = load_provider_sync_settings(target)
    assert loaded.read_only is True
    try:
        save_provider_sync_settings(loaded.settings, target, loaded=loaded)
    except ProviderSyncSettingsWriteRefusedError:
        pass
    else:
        raise AssertionError("future sync settings were overwritten")


def test_host_and_remote_path_reject_command_or_batch_injection() -> None:
    settings = default_provider_sync_settings()
    for host, remote_path in (
        ("host\nget /etc/passwd", "~/.local/state/packet"),
        ("host", "~/packet\nput /etc/passwd"),
        ("-oProxyCommand=bad", "~/packet"),
    ):
        try:
            settings.with_peer(
                peer_id="peer",
                host=host,
                remote_path=remote_path,
                known_hosts="/Users/test/.ssh/known_hosts",
                identity_file="/Users/test/.ssh/id_ed25519",
                secret_account="pairing-peer",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe peer configuration accepted")


def test_loaded_sync_settings_exposes_source_digest_and_refuses_external_edit(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sync.json"
    save_provider_sync_settings(default_provider_sync_settings(), target)
    loaded = load_provider_sync_settings(target)

    assert loaded.source_digest
    external = json.loads(target.read_text(encoding="utf-8"))
    external["external_owner"] = {"keep": True}
    target.write_text(json.dumps(external), encoding="utf-8")

    with pytest.raises(ProviderSyncSettingsWriteRefusedError):
        save_provider_sync_settings(loaded.settings, target, loaded=loaded)
    assert json.loads(target.read_text(encoding="utf-8")) == external


def test_loaded_missing_sync_settings_refuses_file_that_appears_before_save(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sync.json"
    loaded = load_provider_sync_settings(target)
    target.write_text('{"owner":"external"}', encoding="utf-8")

    with pytest.raises(ProviderSyncSettingsWriteRefusedError):
        save_provider_sync_settings(loaded.settings, target, loaded=loaded)
