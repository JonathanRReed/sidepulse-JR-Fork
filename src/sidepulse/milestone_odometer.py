"""Pure planning for an opt-in completion Milestone Odometer.

The odometer accepts only :class:`CompletionPresentationKey` values emitted by
the existing exact completion boundary.  It never accepts a raw hook count,
event volume, or inferred work signal.  Exact keys are counted once within a
bounded reset period, then projected into user-selected positive milestones.

This module owns no clock, persistence, controller, renderer, or device I/O.
It returns immutable state and a finite renderer-neutral cue.  Callers remain
responsible for persisting the bounded state and compiling an accepted cue
through the normal presentation and hardware safety gates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .clear_agents import CompletionPresentationKey

MAX_MILESTONE_STEPS: Final = 16
MAX_COMPLETION_COUNT: Final = 2**63 - 1
MAX_RETAINED_OUTCOMES: Final = 256
MOTION_STEP_DURATION_MS: Final = 300
STATIC_HIGHLIGHT_DURATION_MS: Final = 700
MAX_CUE_DURATION_MS: Final = MAX_MILESTONE_STEPS * MOTION_STEP_DURATION_MS


class MilestoneOdometerError(ValueError):
    """A milestone configuration, state, or planner input is invalid."""


class MilestoneCueMode(str, Enum):
    """The only presentation variants emitted by the pure planner."""

    FINITE_STEPS = "finite_steps"
    STATIC_HIGHLIGHT = "static_highlight"


def _finite_nonnegative_epoch(value: object, *, field: str) -> float:
    if (
        type(value) not in {int, float}
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise MilestoneOdometerError(
            f"{field} must be a finite nonnegative epoch"
        )
    return float(value)


def _bounded_count(value: object, *, field: str, positive: bool = False) -> int:
    minimum = 1 if positive else 0
    if type(value) is not int or not minimum <= value <= MAX_COMPLETION_COUNT:
        qualifier = "positive" if positive else "nonnegative"
        raise MilestoneOdometerError(f"{field} must be a bounded {qualifier} integer")
    return value


@dataclass(frozen=True, slots=True)
class MilestoneOdometerPreferences:
    """Opt-in milestone counts selected by the user.

    Milestones are exact positive completion counts, not an open-ended repeat
    interval.  The bounded, strictly increasing tuple makes the whole cue
    finite even if a caller supplies a batch that crosses several milestones.
    """

    enabled: bool = False
    milestone_steps: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise MilestoneOdometerError("enabled must be a boolean")
        if (
            type(self.milestone_steps) is not tuple
            or len(self.milestone_steps) > MAX_MILESTONE_STEPS
            or any(
                type(step) is not int or not 1 <= step <= MAX_COMPLETION_COUNT
                for step in self.milestone_steps
            )
            or tuple(sorted(set(self.milestone_steps))) != self.milestone_steps
        ):
            raise MilestoneOdometerError(
                "milestone steps must be a bounded, strictly increasing tuple "
                "of positive completion counts"
            )
        if self.enabled and not self.milestone_steps:
            raise MilestoneOdometerError(
                "an enabled Milestone Odometer requires at least one step"
            )


@dataclass(frozen=True, slots=True)
class MilestoneOdometerState:
    """Bounded exact-deduplication state for one reset period.

    ``completed_count`` is the durable count.  ``retained_outcomes`` is only
    the bounded recent exact-key window used for idempotence.  Pruning that
    window never decrements the durable count.
    """

    reset_epoch: float = 0.0
    completed_count: int = 0
    retained_outcomes: tuple[CompletionPresentationKey, ...] = ()

    def __post_init__(self) -> None:
        reset = _finite_nonnegative_epoch(self.reset_epoch, field="reset epoch")
        count = _bounded_count(self.completed_count, field="completed count")
        outcomes = self.retained_outcomes
        if (
            type(outcomes) is not tuple
            or len(outcomes) > MAX_RETAINED_OUTCOMES
            or any(
                type(outcome) is not CompletionPresentationKey
                for outcome in outcomes
            )
            or len(set(outcomes)) != len(outcomes)
            or any(outcome.completed_at_epoch < reset for outcome in outcomes)
            or count < len(outcomes)
        ):
            raise MilestoneOdometerError("invalid retained completion outcomes")
        object.__setattr__(self, "reset_epoch", reset)


@dataclass(frozen=True, slots=True)
class MilestoneCueStep:
    """One finite odometer advance or one static replacement."""

    milestone_count: int
    ordinal: int
    duration_ms: int

    def __post_init__(self) -> None:
        _bounded_count(self.milestone_count, field="milestone count", positive=True)
        if (
            type(self.ordinal) is not int
            or not 1 <= self.ordinal <= MAX_MILESTONE_STEPS
        ):
            raise MilestoneOdometerError("cue ordinal must be bounded and positive")
        if (
            type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_CUE_DURATION_MS
        ):
            raise MilestoneOdometerError("cue step duration must be bounded")


@dataclass(frozen=True, slots=True)
class MilestoneAccessibilityPlan:
    """Content-free non-motion meaning shared by every presentation surface."""

    label: str
    value: str
    announcement: str
    help: str

    def __post_init__(self) -> None:
        values = (self.label, self.value, self.announcement, self.help)
        if not all(
            type(value) is str
            and 1 <= len(value) <= 256
            and value.strip() == value
            and "\n" not in value
            and "\r" not in value
            for value in values
        ):
            raise MilestoneOdometerError(
                "accessibility text must be bounded single-line text"
            )


@dataclass(frozen=True, slots=True)
class MilestoneCuePlan:
    """A finite celebration for one or more newly reached milestones."""

    mode: MilestoneCueMode
    reached_milestones: tuple[int, ...]
    steps: tuple[MilestoneCueStep, ...]
    duration_ms: int
    accessibility: MilestoneAccessibilityPlan
    finite: bool = True
    loops: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.mode) is not MilestoneCueMode
            or type(self.reached_milestones) is not tuple
            or not self.reached_milestones
            or len(self.reached_milestones) > MAX_MILESTONE_STEPS
            or tuple(sorted(set(self.reached_milestones)))
            != self.reached_milestones
            or any(
                type(value) is not int or value <= 0
                for value in self.reached_milestones
            )
            or type(self.steps) is not tuple
            or not self.steps
            or any(type(step) is not MilestoneCueStep for step in self.steps)
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_CUE_DURATION_MS
            or sum(step.duration_ms for step in self.steps) != self.duration_ms
            or type(self.accessibility) is not MilestoneAccessibilityPlan
            or self.finite is not True
            or type(self.loops) is not int
            or self.loops != 0
        ):
            raise MilestoneOdometerError("invalid finite milestone cue")
        if self.mode is MilestoneCueMode.FINITE_STEPS:
            if (
                tuple(step.milestone_count for step in self.steps)
                != self.reached_milestones
            ):
                raise MilestoneOdometerError(
                    "moving cue steps must match every reached milestone"
                )
        elif not (
            len(self.steps) == 1
            and self.steps[0].milestone_count == self.reached_milestones[-1]
        ):
            raise MilestoneOdometerError(
                "static cue must collapse to the latest reached milestone"
            )

    @property
    def animated(self) -> bool:
        return self.mode is MilestoneCueMode.FINITE_STEPS


@dataclass(frozen=True, slots=True)
class MilestoneOdometerPlan:
    """One immutable state transition and its optional finite cue."""

    enabled: bool
    previous_count: int
    completed_count: int
    newly_counted: int
    duplicates_ignored: int
    before_reset_ignored: int
    retention_pruned: int
    reset_applied: bool
    reached_milestones: tuple[int, ...]
    next_milestone: int | None
    cue: MilestoneCuePlan | None
    state: MilestoneOdometerState

    def __post_init__(self) -> None:
        for field, value in (
            ("previous count", self.previous_count),
            ("completed count", self.completed_count),
            ("newly counted", self.newly_counted),
            ("duplicates ignored", self.duplicates_ignored),
            ("before-reset ignored", self.before_reset_ignored),
            ("retention pruned", self.retention_pruned),
        ):
            _bounded_count(value, field=field)
        if (
            type(self.enabled) is not bool
            or type(self.reset_applied) is not bool
            or type(self.reached_milestones) is not tuple
            or any(
                type(value) is not int or value <= 0
                for value in self.reached_milestones
            )
            or (
                self.next_milestone is not None
                and (
                    type(self.next_milestone) is not int
                    or self.next_milestone <= self.completed_count
                )
            )
            or (self.cue is not None and type(self.cue) is not MilestoneCuePlan)
            or type(self.state) is not MilestoneOdometerState
            or self.completed_count != self.state.completed_count
            or self.completed_count != self.previous_count + self.newly_counted
            or (not self.reached_milestones) != (self.cue is None)
        ):
            raise MilestoneOdometerError("invalid Milestone Odometer plan")
        if not self.enabled and (
            self.newly_counted
            or self.reached_milestones
            or self.next_milestone is not None
        ):
            raise MilestoneOdometerError("disabled odometer cannot advance")


def _canonical_outcome_key(
    outcome: CompletionPresentationKey,
) -> tuple[object, ...]:
    return (
        outcome.completed_at_epoch,
        outcome.source_key,
        outcome.agent_id,
        outcome.event_name,
    )


def _reset_state(
    state: MilestoneOdometerState,
    requested_reset_epoch: object | None,
) -> tuple[MilestoneOdometerState, bool]:
    if requested_reset_epoch is None:
        return state, False
    requested = _finite_nonnegative_epoch(
        requested_reset_epoch,
        field="requested reset epoch",
    )
    if requested < state.reset_epoch:
        raise MilestoneOdometerError("reset epoch cannot move backward")
    if requested == state.reset_epoch:
        return state, False
    return MilestoneOdometerState(reset_epoch=requested), True


def _accessibility_plan(
    reached: tuple[int, ...],
) -> MilestoneAccessibilityPlan:
    latest = reached[-1]
    count_label = "outcome" if latest == 1 else "outcomes"
    if len(reached) == 1:
        announcement = f"Completion milestone reached: {latest}."
    else:
        announcement = (
            f"{len(reached)} completion milestones reached; latest is {latest}."
        )
    return MilestoneAccessibilityPlan(
        label="Completion milestone",
        value=f"{latest} completed {count_label}",
        announcement=announcement,
        help=(
            "Counts exact completed outcomes once within the current reset "
            "period."
        ),
    )


def _cue_plan(
    reached: tuple[int, ...],
    *,
    reduce_motion: bool,
) -> MilestoneCuePlan:
    if reduce_motion:
        steps = (
            MilestoneCueStep(
                milestone_count=reached[-1],
                ordinal=1,
                duration_ms=STATIC_HIGHLIGHT_DURATION_MS,
            ),
        )
        mode = MilestoneCueMode.STATIC_HIGHLIGHT
    else:
        steps = tuple(
            MilestoneCueStep(
                milestone_count=milestone,
                ordinal=index,
                duration_ms=MOTION_STEP_DURATION_MS,
            )
            for index, milestone in enumerate(reached, start=1)
        )
        mode = MilestoneCueMode.FINITE_STEPS
    return MilestoneCuePlan(
        mode=mode,
        reached_milestones=reached,
        steps=steps,
        duration_ms=sum(step.duration_ms for step in steps),
        accessibility=_accessibility_plan(reached),
    )


def plan_milestone_odometer(
    preferences: MilestoneOdometerPreferences,
    state: MilestoneOdometerState,
    completed_outcomes: tuple[CompletionPresentationKey, ...],
    *,
    requested_reset_epoch: object | None = None,
    reduce_motion: object = False,
) -> MilestoneOdometerPlan:
    """Count exact new outcomes and return the next/reached milestone plan.

    The input deliberately requires a tuple of exact completion keys.  Raw
    identifiers, numeric hook counts, and loosely typed iterables fail closed.
    A newer reset epoch clears both the durable count and deduplication window
    before eligible outcomes are considered.  Outcomes older than that epoch
    cannot repopulate the reset period.
    """

    if type(preferences) is not MilestoneOdometerPreferences:
        raise MilestoneOdometerError(
            "preferences must be Milestone Odometer preferences"
        )
    if type(state) is not MilestoneOdometerState:
        raise MilestoneOdometerError("state must be Milestone Odometer state")
    if (
        type(completed_outcomes) is not tuple
        or any(
            type(outcome) is not CompletionPresentationKey
            for outcome in completed_outcomes
        )
    ):
        raise MilestoneOdometerError(
            "completed outcomes must be exact completion presentation keys"
        )
    if type(reduce_motion) is not bool:
        raise MilestoneOdometerError("reduce motion must be a boolean")

    active_state, reset_applied = _reset_state(state, requested_reset_epoch)
    previous_count = active_state.completed_count
    if not preferences.enabled:
        return MilestoneOdometerPlan(
            enabled=False,
            previous_count=previous_count,
            completed_count=previous_count,
            newly_counted=0,
            duplicates_ignored=0,
            before_reset_ignored=0,
            retention_pruned=0,
            reset_applied=reset_applied,
            reached_milestones=(),
            next_milestone=None,
            cue=None,
            state=active_state,
        )

    retained = set(active_state.retained_outcomes)
    new_outcomes: list[CompletionPresentationKey] = []
    duplicates_ignored = 0
    before_reset_ignored = 0
    for outcome in sorted(completed_outcomes, key=_canonical_outcome_key):
        if outcome.completed_at_epoch < active_state.reset_epoch:
            before_reset_ignored += 1
            continue
        if outcome in retained:
            duplicates_ignored += 1
            continue
        retained.add(outcome)
        new_outcomes.append(outcome)

    newly_counted = len(new_outcomes)
    if previous_count > MAX_COMPLETION_COUNT - newly_counted:
        raise MilestoneOdometerError("completed count exceeds the bounded maximum")
    completed_count = previous_count + newly_counted

    combined = tuple(
        sorted(
            (*active_state.retained_outcomes, *new_outcomes),
            key=_canonical_outcome_key,
        )
    )
    retention_pruned = max(0, len(combined) - MAX_RETAINED_OUTCOMES)
    retained_outcomes = combined[retention_pruned:]
    next_state = MilestoneOdometerState(
        reset_epoch=active_state.reset_epoch,
        completed_count=completed_count,
        retained_outcomes=retained_outcomes,
    )

    reached = tuple(
        milestone
        for milestone in preferences.milestone_steps
        if previous_count < milestone <= completed_count
    )
    next_milestone = next(
        (
            milestone
            for milestone in preferences.milestone_steps
            if milestone > completed_count
        ),
        None,
    )
    cue = _cue_plan(reached, reduce_motion=reduce_motion) if reached else None
    return MilestoneOdometerPlan(
        enabled=True,
        previous_count=previous_count,
        completed_count=completed_count,
        newly_counted=newly_counted,
        duplicates_ignored=duplicates_ignored,
        before_reset_ignored=before_reset_ignored,
        retention_pruned=retention_pruned,
        reset_applied=reset_applied,
        reached_milestones=reached,
        next_milestone=next_milestone,
        cue=cue,
        state=next_state,
    )


__all__ = [
    "MAX_COMPLETION_COUNT",
    "MAX_CUE_DURATION_MS",
    "MAX_MILESTONE_STEPS",
    "MAX_RETAINED_OUTCOMES",
    "MOTION_STEP_DURATION_MS",
    "STATIC_HIGHLIGHT_DURATION_MS",
    "MilestoneAccessibilityPlan",
    "MilestoneCueMode",
    "MilestoneCuePlan",
    "MilestoneCueStep",
    "MilestoneOdometerError",
    "MilestoneOdometerPlan",
    "MilestoneOdometerPreferences",
    "MilestoneOdometerState",
    "plan_milestone_odometer",
]
