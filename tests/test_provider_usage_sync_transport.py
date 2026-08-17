from __future__ import annotations

from pathlib import Path

from sidepulse.provider_usage_sync_settings import ProviderSyncPeer
from sidepulse.provider_usage_sync_transport import (
    SftpFetchResult,
    build_sftp_fetch_command,
    fetch_peer_packet,
    publish_local_packet,
)


def peer(tmp_path: Path) -> ProviderSyncPeer:
    known_hosts = tmp_path / "known_hosts"
    identity = tmp_path / "id_ed25519"
    known_hosts.write_text("fixture-host-key")
    identity.write_text("fixture-private-key")
    known_hosts.chmod(0o600)
    identity.chmod(0o600)
    return ProviderSyncPeer(
        peer_id="macbook",
        host="jonathan@macbook.tailnet.example",
        remote_path="~/.local/state/sidepulse/provider-sync/local.packet",
        known_hosts=str(known_hosts),
        identity_file=str(identity),
        secret_account="pairing-macbook",
    )


def test_sftp_command_is_batch_only_strict_and_never_uses_shell(tmp_path: Path):
    command = build_sftp_fetch_command(peer(tmp_path), Path("/tmp/output.packet"), Path("/tmp/batch"))
    assert command[0] == "/usr/bin/sftp"
    assert "-b" in command
    assert "-oBatchMode=yes" in command
    assert "-oStrictHostKeyChecking=yes" in command
    assert any(value.startswith("-oUserKnownHostsFile=") for value in command)
    assert any(value.startswith("-oIdentityFile=") for value in command)
    assert command[-1] == "jonathan@macbook.tailnet.example"
    assert all(";" not in value and "\n" not in value for value in command)


def test_publish_local_packet_is_owner_private_and_atomic(tmp_path: Path):
    target = tmp_path / "state" / "local.packet"
    publish_local_packet(b"fixture-packet", target)
    assert target.read_bytes() == b"fixture-packet"
    assert target.stat().st_mode & 0o777 == 0o600


def test_fetch_peer_packet_uses_bounded_temp_and_returns_bytes(tmp_path: Path):
    observed = {}

    def runner(command, *, timeout, check, capture_output, text):
        assert check is False
        assert capture_output is True
        assert text is True
        assert timeout == 20.0
        batch_path = Path(command[command.index("-b") + 1])
        batch = batch_path.read_text()
        assert batch.startswith("get ")
        output_path = Path(batch.split('"')[-2])
        output_path.write_bytes(b"remote-packet")
        observed["command"] = command
        return type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    result = fetch_peer_packet(peer(tmp_path), runner=runner)
    assert result == SftpFetchResult("macbook", True, b"remote-packet", None)
    assert observed["command"][0] == "/usr/bin/sftp"


def test_fetch_failure_is_bounded_and_does_not_return_stderr(tmp_path: Path):
    def runner(*_args, **_kwargs):
        return type(
            "Completed",
            (),
            {
                "returncode": 255,
                "stderr": "sensitive remote detail",
                "stdout": "",
            },
        )()

    result = fetch_peer_packet(peer(tmp_path), runner=runner)
    assert result.reachable is False
    assert result.packet is None
    assert result.reason == "sftp_failed"
    assert "sensitive" not in repr(result)


def test_fetch_rejects_unsafe_known_hosts_or_identity_permissions(tmp_path: Path):
    configured = peer(tmp_path)
    Path(configured.identity_file).chmod(0o644)
    result = fetch_peer_packet(configured, runner=lambda *_args, **_kwargs: None)
    assert result.reachable is False
    assert result.reason == "unsafe_identity_file"
