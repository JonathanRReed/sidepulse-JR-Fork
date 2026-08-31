from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.clear_agents import CompletionPresentationKey
from sidepulse.milestone_odometer import (
    MAX_RETAINED_OUTCOMES,
    MOTION_STEP_DURATION_MS,
    STATIC_HIGHLIGHT_DURATION_MS,
    MilestoneCueMode,
    MilestoneOdometerError,
    MilestoneOdometerPreferences,
    MilestoneOdometerState,
    plan_milestone_odometer,
)

SOURCE = SourceKey("codex", "hooks", "local:test", "live_agent_events")


def _outcome(
    index: int,
    *,
    completed_at: float | None = None,
) -> CompletionPresentationKey:
    return CompletionPresentationKey(
        SOURCE,
        f"agent:{index}",
        "Stop",
        float(index if completed_at is None else completed_at),
    )


def _preferences(*steps: int, enabled: bool = True) -> MilestoneOdometerPreferences:
    return MilestoneOdometerPreferences(enabled=enabled, milestone_steps=steps)


def test_disabled_odometer_does_not_count_or_retain_supplied_outcomes() -> None:
    state = MilestoneOdometerState(reset_epoch=10.0)

    plan = plan_milestone_odometer(
        _preferences(5, 10, enabled=False),
        state,
        (_outcome(1, completed_at=11.0),),
    )

    assert plan.enabled is False
    assert plan.state is state
    assert plan.completed_count == 0
    assert plan.newly_counted == 0
    assert plan.reached_milestones == ()
    assert plan.next_milestone is None
    assert plan.cue is None


def test_exact_completion_keys_advance_once_and_report_next_milestone() -> None:
    first = _outcome(1)
    second = _outcome(2)
    initial = plan_milestone_odometer(
        _preferences(2, 5, 10),
        MilestoneOdometerState(),
        (second, first, first),
    )

    assert initial.previous_count == 0
    assert initial.completed_count == 2
    assert initial.newly_counted == 2
    assert initial.duplicates_ignored == 1
    assert initial.reached_milestones == (2,)
    assert initial.next_milestone == 5
    assert initial.cue is not None
    assert initial.cue.mode is MilestoneCueMode.FINITE_STEPS
    assert initial.cue.finite is True
    assert initial.cue.loops == 0
    assert initial.cue.duration_ms == MOTION_STEP_DURATION_MS

    repeated = plan_milestone_odometer(
        _preferences(2, 5, 10),
        initial.state,
        (first, second),
    )

    assert repeated.completed_count == 2
    assert repeated.newly_counted == 0
    assert repeated.duplicates_ignored == 2
    assert repeated.reached_milestones == ()
    assert repeated.next_milestone == 5
    assert repeated.cue is None


def test_batch_crossing_multiple_steps_emits_one_bounded_step_per_milestone() -> None:
    plan = plan_milestone_odometer(
        _preferences(1, 3, 5, 8),
        MilestoneOdometerState(),
        tuple(_outcome(index) for index in range(1, 7)),
    )

    assert plan.reached_milestones == (1, 3, 5)
    assert plan.next_milestone == 8
    assert plan.cue is not None
    assert plan.cue.mode is MilestoneCueMode.FINITE_STEPS
    assert [step.milestone_count for step in plan.cue.steps] == [1, 3, 5]
    assert [step.ordinal for step in plan.cue.steps] == [1, 2, 3]
    assert plan.cue.duration_ms == 3 * MOTION_STEP_DURATION_MS
    assert plan.cue.finite is True
    assert plan.cue.loops == 0


def test_reduce_motion_collapses_crossed_steps_to_one_static_highlight() -> None:
    plan = plan_milestone_odometer(
        _preferences(1, 2, 3),
        MilestoneOdometerState(),
        (_outcome(1), _outcome(2), _outcome(3)),
        reduce_motion=True,
    )

    assert plan.reached_milestones == (1, 2, 3)
    assert plan.cue is not None
    assert plan.cue.mode is MilestoneCueMode.STATIC_HIGHLIGHT
    assert plan.cue.animated is False
    assert len(plan.cue.steps) == 1
    assert plan.cue.steps[0].milestone_count == 3
    assert plan.cue.duration_ms == STATIC_HIGHLIGHT_DURATION_MS
    assert plan.cue.finite is True
    assert plan.cue.loops == 0


def test_reset_epoch_clears_prior_count_and_rejects_pre_reset_outcomes() -> None:
    prior = plan_milestone_odometer(
        _preferences(1, 2, 3),
        MilestoneOdometerState(),
        (_outcome(1, completed_at=10.0), _outcome(2, completed_at=20.0)),
    )

    reset = plan_milestone_odometer(
        _preferences(1, 2, 3),
        prior.state,
        (
            _outcome(1, completed_at=10.0),
            _outcome(3, completed_at=31.0),
        ),
        requested_reset_epoch=30.0,
    )

    assert reset.reset_applied is True
    assert reset.previous_count == 0
    assert reset.completed_count == 1
    assert reset.newly_counted == 1
    assert reset.before_reset_ignored == 1
    assert reset.state.reset_epoch == 30.0
    assert reset.state.retained_outcomes == (_outcome(3, completed_at=31.0),)
    assert reset.reached_milestones == (1,)

    with pytest.raises(MilestoneOdometerError, match="cannot move backward"):
        plan_milestone_odometer(
            _preferences(1),
            reset.state,
            (),
            requested_reset_epoch=29.0,
        )


def test_retention_is_bounded_without_decrementing_durable_count() -> None:
    outcomes = tuple(_outcome(index) for index in range(1, MAX_RETAINED_OUTCOMES + 2))

    plan = plan_milestone_odometer(
        _preferences(MAX_RETAINED_OUTCOMES + 1),
        MilestoneOdometerState(),
        outcomes,
    )

    assert plan.completed_count == MAX_RETAINED_OUTCOMES + 1
    assert len(plan.state.retained_outcomes) == MAX_RETAINED_OUTCOMES
    assert plan.retention_pruned == 1
    assert plan.state.retained_outcomes[0] == _outcome(2)
    assert plan.reached_milestones == (MAX_RETAINED_OUTCOMES + 1,)


def test_accessibility_is_content_free_and_explains_exact_counting() -> None:
    private_identity = "agent:private-session-identifier"
    outcome = CompletionPresentationKey(SOURCE, private_identity, "Stop", 10.0)

    plan = plan_milestone_odometer(
        _preferences(1),
        MilestoneOdometerState(),
        (outcome,),
    )

    assert plan.cue is not None
    accessibility = plan.cue.accessibility
    text = " ".join(
        (
            accessibility.label,
            accessibility.value,
            accessibility.announcement,
            accessibility.help,
        )
    )
    assert private_identity not in text
    assert "1 completed outcome" in accessibility.value
    assert "exact completed outcomes once" in accessibility.help


@pytest.mark.parametrize(
    "steps",
    (
        (0,),
        (-1,),
        (2, 1),
        (1, 1),
        (True,),
    ),
)
def test_milestone_steps_must_be_strictly_increasing_positive_counts(
    steps: tuple[int, ...],
) -> None:
    with pytest.raises(MilestoneOdometerError):
        MilestoneOdometerPreferences(enabled=True, milestone_steps=steps)


def test_raw_hook_volume_and_loose_identifiers_fail_closed() -> None:
    preferences = _preferences(1)
    state = MilestoneOdometerState()

    for untrusted in (1, ("hook:event",), [_outcome(1)]):
        with pytest.raises(
            MilestoneOdometerError,
            match="exact completion presentation keys",
        ):
            plan_milestone_odometer(
                preferences,
                state,
                untrusted,  # type: ignore[arg-type]
            )


def test_state_and_plans_are_immutable() -> None:
    plan = plan_milestone_odometer(
        _preferences(1),
        MilestoneOdometerState(),
        (_outcome(1),),
    )

    with pytest.raises(FrozenInstanceError):
        plan.state.completed_count = 9  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.next_milestone = 9  # type: ignore[misc]
    assert plan.cue is not None
    with pytest.raises(FrozenInstanceError):
        plan.cue.duration_ms = 9  # type: ignore[misc]
