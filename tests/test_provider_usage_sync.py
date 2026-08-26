from __future__ import annotations

from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from sidepulse.provider_usage_sync import (
    SYNC_PACKET_MAX_AGE_SECONDS,
    MachineUsageObservation,
    ProviderSyncPacket,
    StaleSyncPacketError,
    decode_signed_packet,
    encode_signed_packet,
    merge_provider_sync,
)


def snapshot(provider, observed, remaining, *, account="account-fixture"):
    lane = UsageLane(
        provider_id=provider,
        lane_id="weekly",
        label="Weekly",
        remaining_percent=remaining,
        reset_at=3000,
        scope="all",
        model=None,
        feature=None,
        bindable=True,
        source_id="official",
    )
    return ProviderUsageSnapshot(
        provider_id=provider,
        account_label=account,
        observed_at=observed,
        state=ProviderSourceState.READY,
        reason_code=None,
        action_label=None,
        lanes=(lane,),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
    )


def observation(device, provider, observed, input_tokens):
    return MachineUsageObservation(
        device_id=device,
        provider_id=provider,
        observed_at=observed,
        input_tokens=input_tokens,
        cached_input_tokens=0,
        output_tokens=10,
        model_count=1,
        estimated_cost_usd=1.0,
        cache_savings_usd=0.0,
    )


def test_signed_packet_round_trip_and_tamper_rejection():
    packet = ProviderSyncPacket(
        schema_version=1,
        device_id="mac-mini",
        generated_at=1000,
        quota_snapshots=(snapshot("claude", 1000, 25),),
        machine_usage=(observation("mac-mini", "claude", 1000, 100),),
        categories=("quota", "token_usage"),
    )
    secret = b"fixture-shared-secret-32-bytes!!"
    encoded = encode_signed_packet(packet, secret)
    # now= pins the freshness clock beside the fixture stamps
    # (2026-08-26: decode also enforces the replay window).
    assert decode_signed_packet(encoded, secret, now=1000.0) == packet
    tampered = encoded.replace(b'"input_tokens":100', b'"input_tokens":101')
    try:
        decode_signed_packet(tampered, secret, now=1000.0)
    except ValueError as exc:
        assert "signature" in str(exc)
    else:
        raise AssertionError("tampered sync packet accepted")


def test_authentic_but_stale_packet_is_rejected_on_decode():
    packet = ProviderSyncPacket(
        schema_version=1,
        device_id="mac-mini",
        generated_at=1000,
        quota_snapshots=(snapshot("claude", 1000, 25),),
        machine_usage=(),
        categories=("quota", "token_usage"),
    )
    secret = b"fixture-shared-secret-32-bytes!!"
    encoded = encode_signed_packet(packet, secret)
    # At the boundary the packet still decodes...
    boundary = 1000.0 + SYNC_PACKET_MAX_AGE_SECONDS
    assert decode_signed_packet(encoded, secret, now=boundary) == packet
    # ...one second past it, an authentic replay is refused.
    try:
        decode_signed_packet(encoded, secret, now=boundary + 1.0)
    except StaleSyncPacketError as exc:
        assert "freshness" in str(exc)
    else:
        raise AssertionError("stale sync packet accepted")


def test_account_quota_uses_freshest_observation_and_is_never_summed():
    local = ProviderSyncPacket(
        1,
        "mac-mini",
        1000,
        (snapshot("claude", 1000, 25),),
        (observation("mac-mini", "claude", 1000, 100),),
        ("quota", "token_usage"),
    )
    remote = ProviderSyncPacket(
        1,
        "macbook",
        1100,
        (snapshot("claude", 1100, 20),),
        (observation("macbook", "claude", 1100, 50),),
        ("quota", "token_usage"),
    )
    merged = merge_provider_sync(local, (remote,))
    assert len(merged.quota_snapshots) == 1
    assert merged.quota_snapshots[0].lanes[0].remaining_percent == 20
    assert merged.total_input_tokens == 150


def test_replayed_packet_does_not_double_count_machine_usage():
    local = ProviderSyncPacket(
        1,
        "mac-mini",
        1000,
        (),
        (observation("mac-mini", "codex", 1000, 100),),
        ("token_usage",),
    )
    replay = ProviderSyncPacket(
        1,
        "mac-mini",
        1000,
        (),
        (observation("mac-mini", "codex", 1000, 100),),
        ("token_usage",),
    )
    merged = merge_provider_sync(local, (replay, replay))
    assert len(merged.machine_usage) == 1
    assert merged.total_input_tokens == 100


def test_newer_machine_observation_replaces_older_cumulative_total():
    older = ProviderSyncPacket(
        1,
        "macbook",
        900,
        (),
        (observation("macbook", "grok", 900, 10),),
        ("token_usage",),
    )
    newer = ProviderSyncPacket(
        1,
        "macbook",
        1000,
        (),
        (observation("macbook", "grok", 1000, 30),),
        ("token_usage",),
    )
    merged = merge_provider_sync(newer, (older,))
    assert len(merged.machine_usage) == 1
    assert merged.total_input_tokens == 30


def test_agent_activity_is_not_part_of_default_packet_categories():
    packet = ProviderSyncPacket(
        1,
        "mac-mini",
        1000,
        (),
        (),
        ("quota", "token_usage"),
    )
    assert "agent_activity" not in packet.categories
