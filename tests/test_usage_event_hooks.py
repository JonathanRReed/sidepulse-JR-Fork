"""Edge-triggered usage hooks: transitions fire once, states never do."""

from __future__ import annotations

import os
import stat
import time

from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from sidepulse.usage_event_hooks import (
    detect_usage_hook_events,
    run_usage_hooks,
)


def lane(remaining, *, provider="claude", lane_id="weekly"):
    return UsageLane(
        provider_id=provider,
        lane_id=lane_id,
        label=lane_id.title(),
        remaining_percent=remaining,
        reset_at=2_000,
        scope="all",
        model=None,
        feature=None,
        bindable=True,
        source_id=f"{provider}-oauth",
    )


def snapshot(lanes, *, provider="claude", state=ProviderSourceState.READY):
    return ProviderUsageSnapshot(
        provider_id=provider,
        account_label="fixture",
        observed_at=1_000,
        state=state,
        reason_code=None if state is ProviderSourceState.READY else "network_unavailable",
        action_label=None if state is ProviderSourceState.READY else "Retry",
        lanes=tuple(lanes),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
    )


THRESHOLDS = {"claude": 20.0}


def test_quota_low_fires_on_the_downward_crossing_only() -> None:
    events = detect_usage_hook_events(
        (snapshot((lane(25.0),)),),
        (snapshot((lane(18.0),)),),
        thresholds=THRESHOLDS,
    )
    assert [event.name for event in events] == ["quota_low"]
    assert events[0].detail == "18"

    # Sitting under the threshold: no repeat.
    again = detect_usage_hook_events(
        (snapshot((lane(18.0),)),),
        (snapshot((lane(15.0),)),),
        thresholds=THRESHOLDS,
    )
    assert again == ()


def test_quota_reached_and_reset_edges() -> None:
    reached = detect_usage_hook_events(
        (snapshot((lane(3.0),)),),
        (snapshot((lane(0.0),)),),
        thresholds=THRESHOLDS,
    )
    # 3% was already under the 20% threshold -- no second quota_low.
    assert [event.name for event in reached] == ["quota_reached"]

    reset = detect_usage_hook_events(
        (snapshot((lane(2.0),)),),
        (snapshot((lane(100.0),)),),
        thresholds=THRESHOLDS,
    )
    assert [event.name for event in reset] == ["quota_reset"]


def test_provider_availability_edges() -> None:
    down = detect_usage_hook_events(
        (snapshot((lane(50.0),)),),
        (snapshot((lane(50.0),), state=ProviderSourceState.UNAVAILABLE),),
        thresholds={},
    )
    assert [event.name for event in down] == ["provider_unavailable"]

    up = detect_usage_hook_events(
        (snapshot((lane(50.0),), state=ProviderSourceState.UNAVAILABLE),),
        (snapshot((lane(50.0),)),),
        thresholds={},
    )
    assert [event.name for event in up] == ["provider_recovered"]

    # A lane appearing from nowhere is not a crossing.
    fresh = detect_usage_hook_events(
        (), (snapshot((lane(5.0),)),), thresholds=THRESHOLDS
    )
    assert fresh == ()


def test_runner_invokes_the_executable_with_event_argv(tmp_path) -> None:
    record = tmp_path / "events.txt"
    script = tmp_path / "hook.sh"
    script.write_text(f'#!/bin/sh\necho "$1 $2 $3 $4" >> "{record}"\n')
    os.chmod(script, stat.S_IRWXU)

    events = detect_usage_hook_events(
        (snapshot((lane(25.0),)),),
        (snapshot((lane(18.0),)),),
        thresholds=THRESHOLDS,
    )
    run_usage_hooks(str(script), events)
    deadline = time.time() + 5.0
    while time.time() < deadline and not record.exists():
        time.sleep(0.05)
    assert record.read_text().strip() == "quota_low claude weekly 18"
