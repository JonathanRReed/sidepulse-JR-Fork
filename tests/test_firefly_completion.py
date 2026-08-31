from __future__ import annotations

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.clear_agents import CompletionPresentationKey
from sidepulse.firefly_completion import (
    FIREFLY_DURATION_SECONDS,
    REDUCE_MOTION_HOLD_SECONDS,
    FireflyCompletionEvidence,
    FireflyCompletionMode,
    FireflyCompletionRefusal,
    plan_firefly_completion,
)
from sidepulse.fleet_bands import FleetBand, FleetMember, FleetPlan, plan_fleet_bands


def _completion(*, completed_at: float = 100.0) -> CompletionPresentationKey:
    return CompletionPresentationKey(
        SourceKey("codex", "hooks", "local:test", "live_agent_events"),
        "codex:session:alpha",
        "Stop",
        completed_at,
    )


def _band(
    identity: str,
    *,
    led_start: int,
    led_end: int,
    screen_start: float,
    screen_end: float,
) -> FleetBand:
    return FleetBand(
        identity=identity,
        semantic="working",
        led_start=led_start,
        led_end=led_end,
        screen_start=screen_start,
        screen_end=screen_end,
    )


def _evidence(
    *,
    identity: str = "project:alpha",
    active_band: FleetBand | None = None,
    active_fraction: float = 0.25,
    completed_at: float = 100.0,
) -> FireflyCompletionEvidence:
    return FireflyCompletionEvidence(
        completion_key=_completion(completed_at=completed_at),
        fleet_identity=identity,
        active_band=active_band
        or _band(
            identity,
            led_start=0,
            led_end=2,
            screen_start=0.0,
            screen_end=20.0,
        ),
        active_fraction=active_fraction,
    )


def _stable_fleet() -> FleetPlan:
    alpha = _band(
        "project:alpha",
        led_start=4,
        led_end=8,
        screen_start=40.0,
        screen_end=80.0,
    )
    beta = _band(
        "project:beta",
        led_start=0,
        led_end=4,
        screen_start=0.0,
        screen_end=40.0,
    )
    return FleetPlan(
        mode="segmented",
        bands=(beta, alpha),
        member_slots=(
            ("project:beta", 0, 4),
            ("project:alpha", 4, 8),
        ),
        led_count=8,
        screen_bar_width=80.0,
    )


def test_travelling_firefly_freezes_source_and_releases_at_stable_band() -> None:
    decision = plan_firefly_completion(_evidence(), _stable_fleet())

    assert decision.accepted is True
    assert decision.refusal is None
    plan = decision.plan
    assert plan is not None
    assert plan.mode is FireflyCompletionMode.TRAVELLING_SPARK
    assert plan.duration_seconds == FIREFLY_DURATION_SECONDS == 2.0
    assert plan.frozen_active_segment.led_start == 0
    assert plan.frozen_active_segment.led_end == 2
    assert plan.stable_fleet_band.led_start == 4
    assert plan.stable_fleet_band.led_end == 8
    assert plan.release_to_live_assignment is True

    assert plan.keyframes[0].led_position == pytest.approx(0.25)
    assert plan.keyframes[0].screen_position == pytest.approx(5.0)
    assert plan.keyframes[-1].led_position == pytest.approx(5.5)
    assert plan.keyframes[-1].screen_position == pytest.approx(60.0)
    assert [frame.elapsed_seconds for frame in plan.keyframes] == [
        0.0,
        0.5,
        1.0,
        1.5,
        2.0,
    ]
    assert max(frame.intensity for frame in plan.keyframes) == 1.0
    assert plan.keyframes[-1].intensity == 0.0


def test_reduce_motion_substitutes_one_static_finite_highlight() -> None:
    decision = plan_firefly_completion(
        _evidence(),
        _stable_fleet(),
        reduce_motion=True,
    )

    plan = decision.plan
    assert plan is not None
    assert plan.mode is FireflyCompletionMode.STATIC_HIGHLIGHT
    assert plan.duration_seconds == REDUCE_MOTION_HOLD_SECONDS == 0.75
    assert {frame.led_position for frame in plan.keyframes} == {5.5}
    assert {frame.screen_position for frame in plan.keyframes} == {60.0}
    assert {frame.intensity for frame in plan.keyframes} == {0.62}
    assert "Reduce Motion" in plan.accessibility.help
    assert "travels" not in plan.accessibility.help


def test_shared_fleet_uses_private_sticky_slot_not_the_shared_full_width_band() -> None:
    shared = plan_fleet_bands(
        (
            FleetMember(identity="project:alpha", semantic="working"),
            FleetMember(identity="project:beta", semantic="working"),
        ),
        screen_bar_width=80.0,
    )
    active = _band(
        "project:beta",
        led_start=4,
        led_end=8,
        screen_start=40.0,
        screen_end=80.0,
    )

    decision = plan_firefly_completion(
        _evidence(identity="project:beta", active_band=active),
        shared,
    )

    assert shared.mode == "shared"
    assert shared.bands[0].identity is None
    assert decision.plan is not None
    assert decision.plan.stable_fleet_band.identity == "project:beta"
    assert decision.plan.stable_fleet_band.led_start == 4
    assert decision.plan.stable_fleet_band.led_end == 8


def test_completion_is_refused_when_explicit_identity_is_not_in_stable_fleet() -> None:
    fleet = plan_fleet_bands(
        (FleetMember(identity="project:beta", semantic="working"),),
        screen_bar_width=80.0,
    )

    decision = plan_firefly_completion(_evidence(), fleet)

    assert decision.plan is None
    assert decision.refusal is FireflyCompletionRefusal.IDENTITY_NOT_IN_FLEET


def test_refused_or_malformed_fleet_never_produces_a_cue() -> None:
    refused = FleetPlan(mode="refused", refusal="fleet_member_overflow")

    decision = plan_firefly_completion(_evidence(), refused)

    assert decision.plan is None
    assert decision.refusal is FireflyCompletionRefusal.INVALID_FLEET


def test_invalid_reduce_motion_input_fails_closed() -> None:
    decision = plan_firefly_completion(
        _evidence(),
        _stable_fleet(),
        reduce_motion=1,
    )

    assert decision.plan is None
    assert decision.refusal is FireflyCompletionRefusal.INVALID_PREFERENCE


def test_evidence_requires_an_exact_identity_bound_nonshared_active_segment() -> None:
    with pytest.raises(ValueError, match="invalid Firefly Completion evidence"):
        FireflyCompletionEvidence(
            completion_key=_completion(),
            fleet_identity="project:alpha",
            active_band=_band(
                "project:beta",
                led_start=0,
                led_end=4,
                screen_start=0.0,
                screen_end=40.0,
            ),
        )


def test_exact_completion_events_remain_distinct_even_for_same_fleet_identity() -> None:
    first = plan_firefly_completion(
        _evidence(completed_at=100.0),
        _stable_fleet(),
    ).plan
    second = plan_firefly_completion(
        _evidence(completed_at=101.0),
        _stable_fleet(),
    ).plan

    assert first is not None and second is not None
    assert first.fleet_identity == second.fleet_identity
    assert first.completion_key != second.completion_key
    assert first.evidence != second.evidence


def test_accessibility_copy_is_content_free_and_explains_the_release() -> None:
    plan = plan_firefly_completion(_evidence(), _stable_fleet()).plan

    assert plan is not None
    assert plan.accessibility.label == "Firefly completion"
    assert plan.accessibility.value == "A session completed in its fleet band."
    assert "two-second" in plan.accessibility.help
    assert "frozen segment" in plan.accessibility.help
    assert "project:alpha" not in (
        plan.accessibility.label + plan.accessibility.value + plan.accessibility.help
    )
