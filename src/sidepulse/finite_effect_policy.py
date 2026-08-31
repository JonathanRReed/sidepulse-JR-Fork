"""Pure policy for finite effect introductions and bounded loops.

The firmware animation model can describe a repeat marker, including an
unbounded repeat.  Product surfaces need a stricter contract: introductions
play once, loops have a hard end, fast cycles receive a safe pacing interval,
and Reduce Motion replaces the entire moving presentation with a static
effect.  This module turns immutable animation values into an immutable plan.

It may compose a new animation value with a finite repeat marker and a dark
cadence-rest step.  It deliberately performs no rendering, scheduling, clock
access, or device writes.  The eventual runtime owner remains responsible for
executing the plan and for passing every device-bound program through the
existing writer and validation gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from .animation import (
    MAX_REPEAT,
    MIN_REPEAT,
    OFF,
    Animation,
    PaintStep,
    RepeatStep,
    Timing,
    WholeBar,
    animation_duration_ms,
    loop_duration_ms,
)
from .presentation_policy import (
    MAX_FINITE_CUE_DURATION_SECONDS,
    FiniteCueBudget,
)

MAX_EFFECT_KEY_BYTES: Final = 128
MAX_SAFE_CADENCE_HZ: Final = 2.0
MIN_SAFE_CYCLE_MS: Final = int(1000 / MAX_SAFE_CADENCE_HZ)
MAX_FINITE_REPETITIONS: Final = FiniteCueBudget().max_repetitions
MAX_FINITE_TOTAL_DURATION_MS: Final = int(
    MAX_FINITE_CUE_DURATION_SECONDS * 1000
)


class FiniteEffectDecision(str, Enum):
    """The only outcomes a runtime may execute from this policy."""

    PLAY = "play"
    STATIC_SUBSTITUTE = "static_substitute"


class StaticSubstitutionReason(str, Enum):
    """Content-free reasons why a moving effect became static."""

    REDUCE_MOTION = "reduce_motion"
    NO_TIMED_MOTION = "no_timed_motion"
    DURATION_BUDGET = "duration_budget"


@dataclass(frozen=True, slots=True)
class CadencePlan:
    """Safe pacing for one repeated animation cycle.

    ``inserted_rest_ms`` is represented by a dark rest in the bounded output
    animation.  The source animation remains untouched.
    """

    source_cycle_ms: int
    effective_cycle_ms: int
    inserted_rest_ms: int
    source_hz: float
    effective_hz: float
    maximum_hz: float = MAX_SAFE_CADENCE_HZ

    @property
    def adjusted(self) -> bool:
        return self.inserted_rest_ms > 0

    @property
    def safe(self) -> bool:
        return self.effective_hz <= self.maximum_hz


@dataclass(frozen=True, slots=True)
class OneShotIntroPlan:
    """One non-repeating phase that precedes a finite loop."""

    animation: Animation
    duration_ms: int
    repetitions: int = 1


@dataclass(frozen=True, slots=True)
class FiniteLoopPlan:
    """One finite repeated phase plus an optional one-shot source tail."""

    source_animation: Animation
    animation: Animation
    source_repetitions: int | None
    repetitions: int
    cadence: CadencePlan
    post_loop_duration_ms: int
    total_duration_ms: int
    source_was_unbounded: bool
    repetitions_clamped: bool


@dataclass(frozen=True, slots=True)
class FiniteEffectPlan:
    """Complete bounded plan for one semantic effect presentation."""

    effect_key: str
    decision: FiniteEffectDecision
    static_fallback_key: str
    intro: OneShotIntroPlan | None
    loop: FiniteLoopPlan | None
    total_duration_ms: int
    substitution_reason: StaticSubstitutionReason | None = None

    @property
    def reduce_motion_substituted(self) -> bool:
        return self.substitution_reason is StaticSubstitutionReason.REDUCE_MOTION

    @property
    def has_motion(self) -> bool:
        return self.decision is FiniteEffectDecision.PLAY


def plan_one_shot_intro(
    effect_key: str,
    animation: Animation,
    *,
    reduce_motion: bool = False,
    static_fallback_key: str = "rest",
    max_total_duration_ms: int = MAX_FINITE_TOTAL_DURATION_MS,
) -> FiniteEffectPlan:
    """Plan one animation as an introduction that is never repeated."""

    return plan_finite_effect(
        effect_key,
        intro_animation=animation,
        reduce_motion=reduce_motion,
        static_fallback_key=static_fallback_key,
        max_total_duration_ms=max_total_duration_ms,
    )


def plan_finite_loop(
    effect_key: str,
    animation: Animation,
    *,
    requested_repetitions: int | None = None,
    reduce_motion: bool = False,
    static_fallback_key: str = "rest",
    max_repetitions: int = MAX_FINITE_REPETITIONS,
    max_total_duration_ms: int = MAX_FINITE_TOTAL_DURATION_MS,
) -> FiniteEffectPlan:
    """Plan one animation as a bounded loop with no introduction."""

    return plan_finite_effect(
        effect_key,
        loop_animation=animation,
        requested_repetitions=requested_repetitions,
        reduce_motion=reduce_motion,
        static_fallback_key=static_fallback_key,
        max_repetitions=max_repetitions,
        max_total_duration_ms=max_total_duration_ms,
    )


def plan_finite_effect(
    effect_key: str,
    *,
    intro_animation: Animation | None = None,
    loop_animation: Animation | None = None,
    requested_repetitions: int | None = None,
    reduce_motion: bool = False,
    static_fallback_key: str = "rest",
    max_repetitions: int = MAX_FINITE_REPETITIONS,
    max_total_duration_ms: int = MAX_FINITE_TOTAL_DURATION_MS,
) -> FiniteEffectPlan:
    """Return one immutable finite plan from immutable animation values.

    A source ``RepeatStep(count=None)`` is never preserved as unbounded.  With
    no explicit request it receives ``max_repetitions``.  A finite source
    count or explicit request is clamped to the same hard bound and again to
    the total-duration budget.  Fast loop cycles receive an inserted rest so
    the effective cadence never exceeds 2 Hz.
    """

    _validate_key(effect_key, field="effect key")
    _validate_key(static_fallback_key, field="static fallback key")
    _validate_bool(reduce_motion, field="reduce motion")
    _validate_positive_int(max_repetitions, field="maximum repetitions")
    _validate_positive_int(
        max_total_duration_ms,
        field="maximum total duration",
    )
    if max_repetitions > MAX_FINITE_REPETITIONS:
        raise ValueError(
            f"maximum repetitions cannot exceed {MAX_FINITE_REPETITIONS}"
        )
    if max_total_duration_ms > MAX_FINITE_TOTAL_DURATION_MS:
        raise ValueError(
            "maximum total duration cannot exceed "
            f"{MAX_FINITE_TOTAL_DURATION_MS} ms"
        )
    if intro_animation is None and loop_animation is None:
        raise ValueError("an introduction or loop animation is required")
    if intro_animation is not None and type(intro_animation) is not Animation:
        raise ValueError("introduction must be an Animation")
    if loop_animation is not None and type(loop_animation) is not Animation:
        raise ValueError("loop must be an Animation")
    if requested_repetitions is not None:
        _validate_positive_int(
            requested_repetitions,
            field="requested repetitions",
        )
    if loop_animation is None and requested_repetitions is not None:
        raise ValueError("requested repetitions require a loop animation")

    if reduce_motion:
        return _static_plan(
            effect_key,
            static_fallback_key,
            StaticSubstitutionReason.REDUCE_MOTION,
        )

    intro = _plan_intro(intro_animation)
    intro_duration_ms = intro.duration_ms if intro is not None else 0
    if intro is not None and intro.duration_ms <= 0:
        return _static_plan(
            effect_key,
            static_fallback_key,
            StaticSubstitutionReason.NO_TIMED_MOTION,
        )
    if intro_duration_ms > max_total_duration_ms:
        return _static_plan(
            effect_key,
            static_fallback_key,
            StaticSubstitutionReason.DURATION_BUDGET,
        )

    if loop_animation is None:
        return FiniteEffectPlan(
            effect_key=effect_key,
            decision=FiniteEffectDecision.PLAY,
            static_fallback_key=static_fallback_key,
            intro=intro,
            loop=None,
            total_duration_ms=intro_duration_ms,
        )

    loop = _plan_loop(
        loop_animation,
        requested_repetitions=requested_repetitions,
        max_repetitions=max_repetitions,
        remaining_duration_ms=max_total_duration_ms - intro_duration_ms,
    )
    if loop is None:
        reason = (
            StaticSubstitutionReason.NO_TIMED_MOTION
            if _source_cycle_duration_ms(loop_animation) <= 0
            else StaticSubstitutionReason.DURATION_BUDGET
        )
        return _static_plan(effect_key, static_fallback_key, reason)

    return FiniteEffectPlan(
        effect_key=effect_key,
        decision=FiniteEffectDecision.PLAY,
        static_fallback_key=static_fallback_key,
        intro=intro,
        loop=loop,
        total_duration_ms=intro_duration_ms + loop.total_duration_ms,
    )


def _plan_intro(animation: Animation | None) -> OneShotIntroPlan | None:
    if animation is None:
        return None
    if _source_repeat(animation) is not None:
        raise ValueError("one-shot introduction cannot contain a repeat step")
    return OneShotIntroPlan(
        animation=animation,
        duration_ms=animation_duration_ms(animation),
    )


def _plan_loop(
    animation: Animation,
    *,
    requested_repetitions: int | None,
    max_repetitions: int,
    remaining_duration_ms: int,
) -> FiniteLoopPlan | None:
    source_cycle_ms = _source_cycle_duration_ms(animation)
    if source_cycle_ms <= 0:
        return None

    source_repeat = _source_repeat(animation)
    source_repetitions = source_repeat.count if source_repeat is not None else 1
    source_was_unbounded = source_repeat is not None and source_repeat.count is None
    if requested_repetitions is not None:
        desired_repetitions = requested_repetitions
    elif source_was_unbounded:
        desired_repetitions = max_repetitions
    else:
        desired_repetitions = source_repetitions
    assert desired_repetitions is not None

    bounded_repetitions = min(desired_repetitions, max_repetitions)
    effective_cycle_ms = max(source_cycle_ms, MIN_SAFE_CYCLE_MS)
    full_duration_ms = animation_duration_ms(animation)
    post_loop_duration_ms = (
        max(0, full_duration_ms - source_cycle_ms)
        if source_repeat is not None
        else 0
    )
    available_for_cycles_ms = remaining_duration_ms - post_loop_duration_ms
    repetitions_by_duration = available_for_cycles_ms // effective_cycle_ms
    repetitions = min(bounded_repetitions, repetitions_by_duration)
    if repetitions < 1:
        return None

    cadence = CadencePlan(
        source_cycle_ms=source_cycle_ms,
        effective_cycle_ms=effective_cycle_ms,
        inserted_rest_ms=effective_cycle_ms - source_cycle_ms,
        source_hz=1000.0 / source_cycle_ms,
        effective_hz=1000.0 / effective_cycle_ms,
    )
    total_duration_ms = repetitions * effective_cycle_ms + post_loop_duration_ms
    return FiniteLoopPlan(
        source_animation=animation,
        animation=_bounded_loop_animation(
            animation,
            repetitions=repetitions,
            inserted_rest_ms=cadence.inserted_rest_ms,
        ),
        source_repetitions=source_repetitions,
        repetitions=repetitions,
        cadence=cadence,
        post_loop_duration_ms=post_loop_duration_ms,
        total_duration_ms=total_duration_ms,
        source_was_unbounded=source_was_unbounded,
        repetitions_clamped=(
            source_was_unbounded or repetitions != desired_repetitions
        ),
    )


def _source_repeat(animation: Animation) -> RepeatStep | None:
    repeats = tuple(step for step in animation.steps if type(step) is RepeatStep)
    if len(repeats) > 1:
        raise ValueError("finite effect source may contain at most one repeat step")
    if not repeats:
        return None
    repeat = repeats[0]
    if repeat.count is not None and (
        type(repeat.count) is not int
        or not MIN_REPEAT <= repeat.count <= MAX_REPEAT
    ):
        raise ValueError("finite effect source has an invalid repeat count")
    return repeat


def _source_cycle_duration_ms(animation: Animation) -> int:
    repeated_duration_ms = loop_duration_ms(animation)
    return (
        repeated_duration_ms
        if repeated_duration_ms is not None
        else animation_duration_ms(animation)
    )


def _bounded_loop_animation(
    animation: Animation,
    *,
    repetitions: int,
    inserted_rest_ms: int,
) -> Animation:
    """Return a finite, cadence-paced animation without mutating the source."""

    steps = list(animation.steps)
    repeat_index = next(
        (index for index, step in enumerate(steps) if type(step) is RepeatStep),
        None,
    )
    if repeat_index is not None:
        steps[repeat_index] = RepeatStep(repetitions)
        if inserted_rest_ms:
            steps.insert(repeat_index, _cadence_rest(inserted_rest_ms))
    elif repetitions > 1:
        if inserted_rest_ms:
            steps.append(_cadence_rest(inserted_rest_ms))
        steps.append(RepeatStep(repetitions))
    return Animation(name=animation.name, steps=tuple(steps))


def _cadence_rest(duration_ms: int) -> PaintStep:
    return PaintStep(
        segments=(
            WholeBar(
                color=OFF,
                timing=Timing(duration_ms=duration_ms),
            ),
        )
    )


def _static_plan(
    effect_key: str,
    static_fallback_key: str,
    reason: StaticSubstitutionReason,
) -> FiniteEffectPlan:
    return FiniteEffectPlan(
        effect_key=effect_key,
        decision=FiniteEffectDecision.STATIC_SUBSTITUTE,
        static_fallback_key=static_fallback_key,
        intro=None,
        loop=None,
        total_duration_ms=0,
        substitution_reason=reason,
    )


def _validate_key(value: object, *, field: str) -> None:
    if (
        type(value) is not str
        or not value
        or value.strip() != value
        or len(value.encode("utf-8")) > MAX_EFFECT_KEY_BYTES
    ):
        raise ValueError(f"{field} must be bounded non-empty text")


def _validate_bool(value: object, *, field: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{field} must be a boolean")


def _validate_positive_int(value: object, *, field: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a positive integer")


__all__ = [
    "MAX_FINITE_REPETITIONS",
    "MAX_FINITE_TOTAL_DURATION_MS",
    "MAX_SAFE_CADENCE_HZ",
    "MIN_SAFE_CYCLE_MS",
    "CadencePlan",
    "FiniteEffectDecision",
    "FiniteEffectPlan",
    "FiniteLoopPlan",
    "OneShotIntroPlan",
    "StaticSubstitutionReason",
    "plan_finite_effect",
    "plan_finite_loop",
    "plan_one_shot_intro",
]
