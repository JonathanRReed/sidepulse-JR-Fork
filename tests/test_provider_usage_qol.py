from __future__ import annotations

from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from sidepulse.provider_usage_qol import (
    detect_reset_events,
    format_reset_countdown,
    threshold_crossings,
    usage_totals,
)


def lane(remaining, reset, *, lane_id="weekly", label="Weekly"):
    return UsageLane(
        provider_id="claude",
        lane_id=lane_id,
        label=label,
        remaining_percent=remaining,
        reset_at=reset,
        scope="all",
        model=None,
        feature=None,
        bindable=True,
        source_id="claude-oauth",
    )


def snapshot(observed, lanes, *, state=ProviderSourceState.READY):
    return ProviderUsageSnapshot(
        provider_id="claude",
        account_label="account-fixture",
        observed_at=observed,
        state=state,
        reason_code=None if state is ProviderSourceState.READY else "network_unavailable",
        action_label=None if state is ProviderSourceState.READY else "Retry",
        lanes=tuple(lanes),
        input_tokens=100,
        cached_input_tokens=50,
        output_tokens=25,
        model_count=3,
        estimated_cost_usd=2.5,
        cache_savings_usd=0.75,
        credits_remaining=12,
        incident=None,
    )


def test_reset_event_requires_boundary_crossing_and_fresh_replenishment():
    before = snapshot(990, (lane(5, 1000),))
    after = snapshot(1001, (lane(100, 2000),))
    events = detect_reset_events((before,), (after,), seen_event_ids=frozenset())
    assert len(events) == 1
    assert events[0].provider_id == "claude"
    assert events[0].label == "Weekly reset"
    assert events[0].event_id.startswith("claude:weekly:")


def test_reset_event_is_not_reannounced_or_emitted_from_stale_data():
    before = snapshot(990, (lane(5, 1000),))
    after = snapshot(1001, (lane(100, 2000),))
    first = detect_reset_events((before,), (after,), seen_event_ids=frozenset())
    assert detect_reset_events(
        (before,),
        (after,),
        seen_event_ids=frozenset({first[0].event_id}),
    ) == ()
    stale = snapshot(1001, (lane(100, 2000),), state=ProviderSourceState.STALE)
    assert detect_reset_events((before,), (stale,), seen_event_ids=frozenset()) == ()


def test_countdown_copy_is_exact_and_human_readable():
    assert format_reset_countdown(1000, now=1000) == "resetting now"
    assert format_reset_countdown(1061, now=1000) == "resets in 1m"
    assert format_reset_countdown(4700, now=1000) == "resets in 1h 1m"
    assert format_reset_countdown(200000, now=1000) == "resets in 2d 7h"


def test_threshold_crossing_only_fires_downward_across_configured_boundary():
    before = snapshot(900, (lane(25, 2000),))
    after = snapshot(1000, (lane(19, 2000),))
    crossings = threshold_crossings((before,), (after,), {"claude": 20})
    assert len(crossings) == 1
    assert crossings[0].remaining_percent == 19
    assert threshold_crossings((after,), (snapshot(1100, (lane(18, 2000),)),), {"claude": 20}) == ()


def test_usage_totals_add_machine_local_usage_without_summing_quota_lanes():
    first = snapshot(1000, (lane(40, 2000),))
    second = ProviderUsageSnapshot(
        provider_id="codex",
        account_label=None,
        observed_at=1000,
        state=ProviderSourceState.READY,
        reason_code=None,
        action_label=None,
        lanes=(),
        input_tokens=200,
        cached_input_tokens=25,
        output_tokens=100,
        model_count=2,
        estimated_cost_usd=3.0,
        cache_savings_usd=0.25,
        credits_remaining=None,
        incident=None,
    )
    totals = usage_totals((first, second))
    assert totals.input_tokens == 300
    assert totals.cached_input_tokens == 75
    assert totals.output_tokens == 125
    assert totals.providers_with_usage == 2
    assert totals.estimated_cost_usd == 5.5
    assert totals.cache_savings_usd == 1.0
