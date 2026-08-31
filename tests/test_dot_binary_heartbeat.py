"""Dot Binary Heartbeat is a pure, documented two-LED code."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sidepulse.dot_binary_heartbeat import (
    DOT_BINARY_HEARTBEAT_ACCESSIBILITY,
    DOT_BINARY_HEARTBEAT_LEGEND,
    MAX_ACTIVE_SEMANTICS,
    MAX_FLASH_HZ,
    MAX_FLEET_SIZE,
    DotBinaryHeartbeatError,
    DotLedMode,
    DotPulseCadence,
    DotSecondaryPolicy,
    FleetSizeBand,
    fleet_size_band,
    plan_dot_binary_heartbeat,
)
from sidepulse.semantic_effect_router import SemanticEventKind


def test_led_one_uses_the_existing_highest_priority_semantic_order() -> None:
    plan = plan_dot_binary_heartbeat(
        (
            SemanticEventKind.IDLE,
            SemanticEventKind.FAILURE,
            SemanticEventKind.WORK,
            SemanticEventKind.ASK,
        ),
        secondary_policy=DotSecondaryPolicy.FLEET_SIZE,
        fleet_size=2,
    )

    assert plan.selected_semantic is SemanticEventKind.ASK
    assert plan.primary.led_index == 1
    assert plan.primary.mode is DotLedMode.PULSE
    assert plan.primary.cadence is not None
    assert plan.primary.cadence.pulses == 2
    assert plan.primary.non_color_cue == "two short pulses"


def test_asking_and_failure_are_distinguishable_without_color() -> None:
    ask = plan_dot_binary_heartbeat(
        (SemanticEventKind.ASK,),
        secondary_policy=DotSecondaryPolicy.UNSEEN_NOTIFICATIONS,
    ).primary
    failure = plan_dot_binary_heartbeat(
        (SemanticEventKind.FAILURE,),
        secondary_policy=DotSecondaryPolicy.UNSEEN_NOTIFICATIONS,
    ).primary

    assert ask.mode is DotLedMode.PULSE
    assert failure.mode is DotLedMode.STEADY
    assert ask.non_color_cue == "two short pulses"
    assert failure.non_color_cue == "steady high"
    assert not ask.relies_on_color
    assert not failure.relies_on_color


def test_reduce_motion_makes_primary_output_static_and_preserves_urgent_distinction() -> None:
    ask = plan_dot_binary_heartbeat(
        (SemanticEventKind.ASK,),
        secondary_policy=DotSecondaryPolicy.FLEET_SIZE,
        fleet_size=1,
        reduce_motion=True,
    ).primary
    failure = plan_dot_binary_heartbeat(
        (SemanticEventKind.FAILURE,),
        secondary_policy=DotSecondaryPolicy.FLEET_SIZE,
        fleet_size=1,
        reduce_motion=True,
    ).primary

    assert ask.mode is failure.mode is DotLedMode.STEADY
    assert ask.cadence is failure.cadence is None
    assert ask.motion_suppressed and failure.motion_suppressed
    assert ask.intensity == 0.70
    assert failure.intensity == 1.00
    assert ask.non_color_cue != failure.non_color_cue


@pytest.mark.parametrize(
    ("fleet_size", "expected_band", "expected_mode", "expected_intensity"),
    (
        (0, FleetSizeBand.NONE, DotLedMode.DARK, 0.0),
        (1, FleetSizeBand.SOLO, DotLedMode.STEADY, 0.28),
        (2, FleetSizeBand.SMALL, DotLedMode.STEADY, 0.56),
        (3, FleetSizeBand.SMALL, DotLedMode.STEADY, 0.56),
        (4, FleetSizeBand.LARGE, DotLedMode.STEADY, 0.85),
        (MAX_FLEET_SIZE, FleetSizeBand.LARGE, DotLedMode.STEADY, 0.85),
    ),
)
def test_led_two_fleet_policy_uses_broad_bounded_static_bands(
    fleet_size: int,
    expected_band: FleetSizeBand,
    expected_mode: DotLedMode,
    expected_intensity: float,
) -> None:
    plan = plan_dot_binary_heartbeat(
        (),
        secondary_policy=DotSecondaryPolicy.FLEET_SIZE,
        fleet_size=fleet_size,
        unseen_notification_present=True,
    )

    assert plan.secondary.led_index == 2
    assert plan.fleet_size_band is expected_band
    assert plan.unseen_notification_present is None
    assert plan.secondary.mode is expected_mode
    assert plan.secondary.intensity == expected_intensity
    assert plan.secondary.cadence is None


def test_led_two_unseen_policy_is_explicit_and_does_not_claim_a_fleet_band() -> None:
    absent = plan_dot_binary_heartbeat(
        (SemanticEventKind.WORK,),
        secondary_policy=DotSecondaryPolicy.UNSEEN_NOTIFICATIONS,
        fleet_size=99,
        unseen_notification_present=False,
    )
    present = plan_dot_binary_heartbeat(
        (SemanticEventKind.WORK,),
        secondary_policy=DotSecondaryPolicy.UNSEEN_NOTIFICATIONS,
        fleet_size=99,
        unseen_notification_present=True,
    )

    assert absent.fleet_size_band is present.fleet_size_band is None
    assert absent.unseen_notification_present is False
    assert present.unseen_notification_present is True
    assert absent.secondary.mode is DotLedMode.DARK
    assert present.secondary.mode is DotLedMode.STEADY
    assert present.secondary.non_color_cue == "steady high"


def test_empty_primary_state_is_dark_but_idle_has_a_faint_alive_marker() -> None:
    empty = plan_dot_binary_heartbeat(
        (),
        secondary_policy=DotSecondaryPolicy.FLEET_SIZE,
    )
    idle = plan_dot_binary_heartbeat(
        (SemanticEventKind.IDLE,),
        secondary_policy=DotSecondaryPolicy.FLEET_SIZE,
    )

    assert empty.selected_semantic is None
    assert empty.primary.mode is DotLedMode.DARK
    assert idle.selected_semantic is SemanticEventKind.IDLE
    assert idle.primary.mode is DotLedMode.STEADY
    assert idle.primary.non_color_cue == "steady faint"


def test_every_pulsed_legend_state_is_capped_at_two_hertz() -> None:
    pulse_rates = []
    for semantic in SemanticEventKind:
        instruction = plan_dot_binary_heartbeat(
            (semantic,),
            secondary_policy=DotSecondaryPolicy.FLEET_SIZE,
        ).primary
        if instruction.cadence is not None:
            pulse_rates.append(instruction.cadence.peak_flash_hz)

    assert pulse_rates
    assert max(pulse_rates) <= MAX_FLASH_HZ
    assert DOT_BINARY_HEARTBEAT_ACCESSIBILITY.max_flash_hz == MAX_FLASH_HZ
    with pytest.raises(DotBinaryHeartbeatError, match="2 Hz"):
        DotPulseCadence(100, 100, 1, 0)


def test_public_legend_and_accessibility_contract_are_immutable_and_complete() -> None:
    codes = {entry.code for entry in DOT_BINARY_HEARTBEAT_LEGEND}

    assert {semantic.value for semantic in SemanticEventKind} <= codes
    assert {
        "none",
        "fleet:none",
        "fleet:solo",
        "fleet:small",
        "fleet:large",
        "unseen:none",
        "unseen:present",
    } <= codes
    assert all(entry.normal_cue and entry.reduce_motion_cue for entry in DOT_BINARY_HEARTBEAT_LEGEND)
    assert DOT_BINARY_HEARTBEAT_ACCESSIBILITY.color_is_supplemental
    assert DOT_BINARY_HEARTBEAT_ACCESSIBILITY.reduce_motion_is_static
    assert "asking" in DOT_BINARY_HEARTBEAT_ACCESSIBILITY.asking_failure_distinction.lower()
    assert "failure" in DOT_BINARY_HEARTBEAT_ACCESSIBILITY.asking_failure_distinction.lower()
    with pytest.raises(FrozenInstanceError):
        DOT_BINARY_HEARTBEAT_LEGEND[0].label = "Changed"  # type: ignore[misc]


@pytest.mark.parametrize("value", (-1, MAX_FLEET_SIZE + 1, 1.5, True, "2"))
def test_fleet_size_is_strictly_bounded(value: object) -> None:
    with pytest.raises(DotBinaryHeartbeatError, match="fleet size"):
        fleet_size_band(value)  # type: ignore[arg-type]


def test_planner_rejects_mutable_unknown_or_unbounded_inputs() -> None:
    with pytest.raises(DotBinaryHeartbeatError, match="immutable tuple"):
        plan_dot_binary_heartbeat(  # type: ignore[arg-type]
            [SemanticEventKind.ASK],
            secondary_policy=DotSecondaryPolicy.FLEET_SIZE,
        )
    with pytest.raises(DotBinaryHeartbeatError, match="must be known"):
        plan_dot_binary_heartbeat(  # type: ignore[arg-type]
            ("ask",),
            secondary_policy=DotSecondaryPolicy.FLEET_SIZE,
        )
    with pytest.raises(DotBinaryHeartbeatError, match="remain bounded"):
        plan_dot_binary_heartbeat(
            (SemanticEventKind.IDLE,) * (MAX_ACTIVE_SEMANTICS + 1),
            secondary_policy=DotSecondaryPolicy.FLEET_SIZE,
        )
    with pytest.raises(DotBinaryHeartbeatError, match="must be explicit"):
        plan_dot_binary_heartbeat(  # type: ignore[arg-type]
            (),
            secondary_policy="fleet_size",
        )


def test_plan_is_a_two_instruction_content_free_value() -> None:
    plan = plan_dot_binary_heartbeat(
        (SemanticEventKind.NOTIFICATION,),
        secondary_policy=DotSecondaryPolicy.UNSEEN_NOTIFICATIONS,
        unseen_notification_present=True,
    )

    assert tuple(instruction.led_index for instruction in plan.instructions) == (1, 2)
    assert all(not instruction.relies_on_color for instruction in plan.instructions)
    assert not hasattr(plan, "notification_content")
    assert not hasattr(plan, "controller")
