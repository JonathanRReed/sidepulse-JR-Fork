from __future__ import annotations

import io
import json
from pathlib import Path

from sidepulse import provider_usage_sync_cli
from sidepulse.provider_usage_runtime import ProviderUsageState
from sidepulse.provider_usage_sync_runtime import ProviderSyncRefresh
from sidepulse.provider_usage_sync_service import ProviderSyncServiceState
from sidepulse.provider_usage_sync_settings import load_provider_sync_settings


class Credentials:
    def __init__(self):
        self.values = {}

    def set(self, provider, account, secret):
        self.values[(provider, account)] = secret

    def get(self, provider, account):
        value = self.values.get((provider, account))
        return type(
            "Read",
            (),
            {"available": value is not None, "secret": value, "reason": None},
        )()


def test_set_device_categories_and_enable_round_trip(tmp_path: Path):
    target = tmp_path / "sync.json"
    output = io.StringIO()
    assert provider_usage_sync_cli.main(
        ["set-device", "mac-mini"], stdout=output, home=tmp_path, settings_path=target
    ) == 0
    assert provider_usage_sync_cli.main(
        ["set-categories", "quota,token_usage,agent_activity"],
        stdout=output,
        home=tmp_path,
        settings_path=target,
    ) == 0
    assert provider_usage_sync_cli.main(
        ["enable"], stdout=output, home=tmp_path, settings_path=target
    ) == 0
    settings = load_provider_sync_settings(target).settings
    assert settings.device_id == "mac-mini"
    assert settings.enabled is True
    assert settings.categories == ("quota", "token_usage", "agent_activity")


def test_add_peer_never_writes_shared_secret_into_settings(tmp_path: Path):
    target = tmp_path / "sync.json"
    known_hosts = tmp_path / "known_hosts"
    identity = tmp_path / "id_ed25519"
    known_hosts.write_text("fixture")
    identity.write_text("fixture")
    known_hosts.chmod(0o600)
    identity.chmod(0o600)
    output = io.StringIO()
    code = provider_usage_sync_cli.main(
        [
            "add-peer",
            "macbook",
            "--host",
            "user@macbook.tailnet.example",
            "--remote-path",
            "~/.local/state/sidepulse/provider-sync/mac-mini.packet",
            "--known-hosts",
            str(known_hosts),
            "--identity-file",
            str(identity),
            "--secret-account",
            "pairing-macbook",
        ],
        stdout=output,
        home=tmp_path,
        settings_path=target,
    )
    assert code == 0
    document = target.read_text()
    assert "pairing-macbook" in document
    assert "shared_secret" not in document
    assert "fixture-secret" not in document


def test_export_pairing_stores_local_secret_and_import_configures_peer(tmp_path: Path):
    source_settings = tmp_path / "source-sync.json"
    target_settings = tmp_path / "target-sync.json"
    pairing = tmp_path / "pairing.json"
    credentials = Credentials()
    output = io.StringIO()
    provider_usage_sync_cli.main(
        ["set-device", "mac-mini"],
        stdout=output,
        home=tmp_path,
        settings_path=source_settings,
    )
    code = provider_usage_sync_cli.main(
        [
            "export-pairing",
            "macbook",
            "--output",
            str(pairing),
            "--secret-account",
            "pairing-macbook",
        ],
        stdout=output,
        home=tmp_path,
        settings_path=source_settings,
        credentials=credentials,
        random_bytes=lambda size: b"x" * size,
    )
    assert code == 0
    assert credentials.values[("sidepulse-sync", "pairing-macbook")]

    provider_usage_sync_cli.main(
        ["set-device", "macbook"],
        stdout=output,
        home=tmp_path,
        settings_path=target_settings,
    )
    known_hosts = tmp_path / "known_hosts"
    identity = tmp_path / "id_ed25519"
    known_hosts.write_text("fixture")
    identity.write_text("fixture")
    known_hosts.chmod(0o600)
    identity.chmod(0o600)
    code = provider_usage_sync_cli.main(
        [
            "import-pairing",
            "--input",
            str(pairing),
            "--host",
            "user@mac-mini.tailnet.example",
            "--remote-path",
            "~/.local/state/sidepulse/provider-sync/macbook.packet",
            "--known-hosts",
            str(known_hosts),
            "--identity-file",
            str(identity),
        ],
        stdout=output,
        home=tmp_path,
        settings_path=target_settings,
        credentials=credentials,
    )
    assert code == 0
    target = load_provider_sync_settings(target_settings).settings
    assert target.device_id == "macbook"
    assert target.peers[0].peer_id == "mac-mini"


def test_status_json_is_specific_about_disabled_and_peer_count(tmp_path: Path):
    output = io.StringIO()
    code = provider_usage_sync_cli.main(
        ["status", "--json"],
        stdout=output,
        home=tmp_path,
        settings_path=tmp_path / "sync.json",
    )
    document = json.loads(output.getvalue())
    assert code == 0
    assert document["enabled"] is False
    assert document["peer_count"] == 0
    assert document["categories"] == ["quota", "token_usage"]


def test_refresh_uses_background_sync_service_result(tmp_path: Path):
    output = io.StringIO()
    usage_state = ProviderUsageState((), None, None, False)
    refresh = ProviderSyncRefresh(False, None, (), None, (), 1000)

    class Service:
        def refresh_now(self, state):
            assert state == usage_state
            return ProviderSyncServiceState(refresh, False, False, None)

        def close(self):
            pass

    code = provider_usage_sync_cli.main(
        ["refresh", "--json"],
        stdout=output,
        home=tmp_path,
        usage_state_loader=lambda: usage_state,
        service_factory=lambda **_kwargs: Service(),
    )
    assert code == 0
    document = json.loads(output.getvalue())
    assert document["enabled"] is False
    assert document["health"] == []
