from __future__ import annotations

from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from sidepulse.provider_usage_runtime import ProviderUsageState
from sidepulse.provider_usage_sync import MergedProviderSync
from sidepulse.provider_usage_sync_projection import apply_merged_sync_to_state


def snapshot(observed, remaining, *, input_tokens):
    lane = UsageLane(
        provider_id="claude",
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
        provider_id="claude",
        account_label="account-fixture",
        observed_at=observed,
        state=ProviderSourceState.READY,
        reason_code=None,
        action_label=None,
        lanes=(lane,),
        input_tokens=input_tokens,
        cached_input_tokens=0,
        output_tokens=10,
        model_count=1,
        estimated_cost_usd=1.0,
        cache_savings_usd=0.0,
        credits_remaining=None,
        incident=None,
    )


def test_fresher_remote_quota_replaces_lanes_but_preserves_local_usage_fields():
    local = snapshot(1000, 40, input_tokens=100)
    remote = snapshot(1100, 25, input_tokens=999)
    merged = MergedProviderSync(
        quota_snapshots=(remote,),
        machine_usage=(),
        total_input_tokens=150,
        total_cached_input_tokens=0,
        total_output_tokens=20,
        total_estimated_cost_usd=2.0,
        total_cache_savings_usd=0.0,
    )
    state = apply_merged_sync_to_state(
        ProviderUsageState((local,), 1000, 1100, False),
        merged,
    )
    result = state.by_provider("claude")
    assert result.lanes[0].remaining_percent == 25
    assert result.observed_at == 1100
    assert result.input_tokens == 100
    assert result.output_tokens == 10


def test_older_remote_quota_cannot_replace_fresher_local_quota():
    local = snapshot(1100, 25, input_tokens=100)
    remote = snapshot(1000, 40, input_tokens=999)
    merged = MergedProviderSync(
        (remote,),
        (),
        100,
        0,
        10,
        1.0,
        0.0,
    )
    state = apply_merged_sync_to_state(
        ProviderUsageState((local,), 1100, 1200, False),
        merged,
    )
    assert state.by_provider("claude").lanes[0].remaining_percent == 25
