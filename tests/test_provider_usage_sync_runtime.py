from __future__ import annotations

import base64
from pathlib import Path

from sidepulse.provider_feature_settings import (
    ProviderInstanceSharingPolicy,
    ProviderInstanceSharingProjection,
)
from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from sidepulse.provider_usage_runtime import ProviderUsageState
from sidepulse.provider_usage_sync import (
    ProviderSyncPacket,
    decode_signed_packet,
    encode_signed_packet,
)
from sidepulse.provider_usage_sync_runtime import (
    ProviderSyncRuntime,
    build_local_sync_packet,
)
from sidepulse.provider_usage_sync_settings import (
    ProviderSyncPeer,
    ProviderSyncSettings,
)
from sidepulse.provider_usage_sync_transport import SftpFetchResult


class Credentials:
    def __init__(self, secret: bytes):
        self.encoded = base64.b64encode(secret).decode("ascii")

    def get(self, provider, account):
        value = self.encoded if (provider, account) == ("sidepulse-sync", "pairing-macbook") else None
        return type("Read", (), {"available": value is not None, "secret": value, "reason": None})()


def usage_state(*source_instance_ids: str):
    instances = source_instance_ids or ("default",)
    lane = UsageLane(
        provider_id="claude",
        lane_id="weekly",
        label="Weekly",
        remaining_percent=25,
        reset_at=3000,
        scope="all",
        model=None,
        feature=None,
        bindable=True,
        source_id="claude-oauth",
    )
    snapshots = tuple(
        ProviderUsageSnapshot(
            provider_id="claude",
            account_label=f"account-{source_instance_id}",
            observed_at=1000,
            state=ProviderSourceState.READY,
            reason_code=None,
            action_label=None,
            lanes=(lane,),
            input_tokens=100,
            cached_input_tokens=25,
            output_tokens=50,
            model_count=2,
            estimated_cost_usd=1.25,
            cache_savings_usd=0.25,
            credits_remaining=None,
            incident=None,
            source_instance_id=source_instance_id,
        )
        for source_instance_id in instances
    )
    return ProviderUsageState(snapshots, 1000, 1100, False)


def sharing_projection(
    *choices: tuple[str, str],
) -> ProviderInstanceSharingProjection:
    return ProviderInstanceSharingProjection(
        tuple(
            ProviderInstanceSharingPolicy("claude", source_instance_id, choice)
            for source_instance_id, choice in choices
        )
    )


def settings(tmp_path: Path):
    known_hosts = tmp_path / "known_hosts"
    identity = tmp_path / "id_ed25519"
    known_hosts.write_text("fixture")
    identity.write_text("fixture")
    known_hosts.chmod(0o600)
    identity.chmod(0o600)
    return ProviderSyncSettings(
        1,
        True,
        "mac-mini",
        ("quota", "token_usage"),
        (
            ProviderSyncPeer(
                "macbook",
                "user@macbook.tailnet.example",
                "~/.local/state/sidepulse/provider-sync/mac-mini.packet",
                str(known_hosts),
                str(identity),
                "pairing-macbook",
            ),
        ),
    )


def test_local_packet_status_only_contains_quota_without_machine_usage():
    packet = build_local_sync_packet(
        usage_state(),
        settings(Path("/tmp")),
        generated_at=1000,
        sharing=sharing_projection(("default", "status_only")),
    )
    assert packet.device_id == "mac-mini"
    assert len(packet.quota_snapshots) == 1
    shared = packet.quota_snapshots[0]
    assert shared.source_instance_id == "default"
    assert shared.account_label is None
    assert shared.input_tokens == 0
    assert shared.cached_input_tokens == 0
    assert shared.output_tokens == 0
    assert shared.model_count == 0
    assert shared.estimated_cost_usd is None
    assert shared.cache_savings_usd is None
    assert packet.machine_usage == ()


def test_local_packet_filters_each_exact_instance_and_never_shares_usage_totals():
    packet = build_local_sync_packet(
        usage_state("default", "work"),
        settings(Path("/tmp")),
        generated_at=1000,
        sharing=sharing_projection(
            ("default", "never"),
            ("work", "status_only"),
        ),
    )

    assert tuple(item.identity for item in packet.quota_snapshots) == (
        ("claude", "work"),
    )
    assert packet.machine_usage == ()


def test_local_packet_missing_or_invalid_sharing_policy_fails_closed():
    missing = build_local_sync_packet(
        usage_state("work"),
        settings(Path("/tmp")),
        generated_at=1000,
        sharing=sharing_projection(("default", "status_only")),
    )
    invalid = build_local_sync_packet(
        usage_state(),
        settings(Path("/tmp")),
        generated_at=1000,
        sharing=object(),
    )

    assert missing.quota_snapshots == ()
    assert missing.machine_usage == ()
    assert invalid.quota_snapshots == ()
    assert invalid.machine_usage == ()


def test_runtime_sharing_loader_failure_fails_closed_without_aborting_sync(
    tmp_path: Path,
):
    class InvalidProjection:
        @property
        def sharing(self):
            raise ValueError("invalid profile projection")

    configured = settings(tmp_path)
    local_only = ProviderSyncSettings(
        configured.schema_version,
        configured.enabled,
        configured.device_id,
        configured.categories,
        (),
    )
    loaders = (
        lambda: (_ for _ in ()).throw(ValueError("invalid profile")),
        lambda: InvalidProjection(),
    )

    for sharing_loader in loaders:
        runtime = ProviderSyncRuntime(
            settings_loader=lambda: local_only,
            sharing_loader=sharing_loader,
            credentials=Credentials(b"x" * 32),
            local_directory=tmp_path,
            clock=lambda: 1000,
        )

        result = runtime.refresh(usage_state())

        assert result.enabled is True
        assert result.local_packet.quota_snapshots == ()
        assert result.local_packet.machine_usage == ()


def test_runtime_publishes_peer_specific_signed_packet_and_merges_remote(tmp_path: Path):
    secret = b"x" * 32
    remote_packet = ProviderSyncPacket(
        1,
        "macbook",
        1100,
        (),
        (),
        ("quota", "token_usage"),
    )
    published = {}

    def publish(packet, target):
        published[Path(target).name] = packet
        return target

    runtime = ProviderSyncRuntime(
        settings_loader=lambda: settings(tmp_path),
        sharing_loader=lambda: sharing_projection(("default", "status_only")),
        credentials=Credentials(secret),
        local_directory=tmp_path / "published",
        fetcher=lambda peer: SftpFetchResult(
            peer.peer_id,
            True,
            encode_signed_packet(remote_packet, secret),
            None,
        ),
        publisher=publish,
        clock=lambda: 1000,
    )
    result = runtime.refresh(usage_state())

    assert result.enabled is True
    assert result.health[0].reachable is True
    assert result.remote_packets == (remote_packet,)
    assert "macbook.packet" in published
    decoded = decode_signed_packet(published["macbook.packet"], secret, now=1000.0)
    assert decoded.device_id == "mac-mini"
    assert len(decoded.quota_snapshots) == 1
    assert decoded.machine_usage == ()
    # The verified remote envelope is cached beside the published packet
    # so the Usage Center can merge without fetching (2026-08-26).
    assert "macbook.remote.packet" in published
    cached = decode_signed_packet(published["macbook.remote.packet"], secret, now=1100.0)
    assert cached == remote_packet


def test_stale_remote_packet_is_reported_as_stale_not_merged(tmp_path: Path):
    from sidepulse.provider_usage_sync import SYNC_PACKET_MAX_AGE_SECONDS

    secret = b"x" * 32
    stale_now = 1100.0 + SYNC_PACKET_MAX_AGE_SECONDS + 1.0
    remote_packet = ProviderSyncPacket(
        1,
        "macbook",
        1100,
        (),
        (),
        ("quota", "token_usage"),
    )
    runtime = ProviderSyncRuntime(
        settings_loader=lambda: settings(tmp_path),
        sharing_loader=lambda: sharing_projection(("default", "status_only")),
        credentials=Credentials(secret),
        local_directory=tmp_path / "published",
        fetcher=lambda peer: SftpFetchResult(
            peer.peer_id,
            True,
            encode_signed_packet(remote_packet, secret),
            None,
        ),
        publisher=lambda _packet, target: target,
        clock=lambda: stale_now,
    )
    result = runtime.refresh(usage_state())
    assert result.remote_packets == ()
    assert result.health[0].reachable is False
    assert result.health[0].reason == "packet_stale"


def test_cached_merged_sync_reads_local_documents_without_fetching(tmp_path: Path):
    from sidepulse.provider_usage_sync_runtime import load_cached_merged_sync
    from sidepulse.provider_usage_sync_transport import publish_local_packet

    secret = b"x" * 32
    remote_packet = ProviderSyncPacket(
        1,
        "macbook",
        1100,
        (),
        (),
        ("quota", "token_usage"),
    )
    directory = tmp_path / "published"
    publish_local_packet(
        encode_signed_packet(remote_packet, secret),
        directory / "macbook.remote.packet",
    )
    merged = load_cached_merged_sync(
        usage_state(),
        settings_loader=lambda: settings(tmp_path),
        sharing_loader=lambda: sharing_projection(("default", "status_only")),
        credentials=Credentials(secret),
        local_directory=directory,
        now=1200.0,
    )
    assert merged is not None
    # Status-only profiles never contribute token or cost totals.
    assert merged.total_input_tokens == 0


def test_cached_merge_keeps_verified_remote_when_local_policy_loader_fails(
    tmp_path: Path,
):
    from sidepulse.provider_usage_sync_runtime import load_cached_merged_sync
    from sidepulse.provider_usage_sync_transport import publish_local_packet

    secret = b"x" * 32
    remote_packet = ProviderSyncPacket(
        1,
        "macbook",
        1100,
        usage_state("work").snapshots,
        (),
        ("quota", "token_usage"),
    )
    directory = tmp_path / "published"
    publish_local_packet(
        encode_signed_packet(remote_packet, secret),
        directory / "macbook.remote.packet",
    )

    merged = load_cached_merged_sync(
        usage_state(),
        settings_loader=lambda: settings(tmp_path),
        sharing_loader=lambda: (_ for _ in ()).throw(ValueError("invalid profile")),
        credentials=Credentials(secret),
        local_directory=directory,
        now=1200.0,
    )

    assert merged is not None
    assert tuple(item.identity for item in merged.quota_snapshots) == (
        ("claude", "work"),
    )


def test_cached_merged_sync_is_none_when_sync_is_disabled(tmp_path: Path):
    from sidepulse.provider_usage_sync_runtime import load_cached_merged_sync

    configured = settings(tmp_path)
    disabled = ProviderSyncSettings(
        configured.schema_version,
        False,
        configured.device_id,
        configured.categories,
        configured.peers,
    )
    merged = load_cached_merged_sync(
        usage_state(),
        settings_loader=lambda: disabled,
        sharing_loader=lambda: sharing_projection(("default", "status_only")),
        credentials=Credentials(b"x" * 32),
        local_directory=tmp_path,
        now=1200.0,
    )
    assert merged is None


def test_missing_pairing_secret_is_actionable_and_does_not_fetch(tmp_path: Path):
    class Missing:
        def get(self, *_args):
            return type("Read", (), {"available": False, "secret": None, "reason": "credential_not_found"})()

    calls = []
    runtime = ProviderSyncRuntime(
        settings_loader=lambda: settings(tmp_path),
        sharing_loader=lambda: sharing_projection(("default", "status_only")),
        credentials=Missing(),
        local_directory=tmp_path / "published",
        fetcher=lambda peer: calls.append(peer) or None,
        clock=lambda: 1000,
    )
    result = runtime.refresh(usage_state())
    assert result.health[0].reason == "pairing_secret_missing"
    assert calls == []


def test_disabled_runtime_does_not_publish_or_fetch(tmp_path: Path):
    configured = settings(tmp_path)
    disabled = ProviderSyncSettings(
        configured.schema_version,
        False,
        configured.device_id,
        configured.categories,
        configured.peers,
    )
    runtime = ProviderSyncRuntime(
        settings_loader=lambda: disabled,
        sharing_loader=lambda: sharing_projection(("default", "status_only")),
        credentials=Credentials(b"x" * 32),
        local_directory=tmp_path,
        fetcher=lambda _peer: (_ for _ in ()).throw(AssertionError("fetched")),
        publisher=lambda _packet, _target: (_ for _ in ()).throw(AssertionError("published")),
        clock=lambda: 1000,
    )
    result = runtime.refresh(usage_state())
    assert result.enabled is False
    assert result.remote_packets == ()
