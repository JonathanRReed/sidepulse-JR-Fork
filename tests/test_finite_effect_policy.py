from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sidepulse.animation import (
    Animation,
    PaintStep,
    RepeatStep,
    Timing,
    WholeBar,
)
from sidepulse.finite_effect_policy import (
    MAX_FINITE_REPETITIONS,
    MAX_FINITE_TOTAL_DURATION_MS,
    MAX_SAFE_CADENCE_HZ,
    MIN_SAFE_CYCLE_MS,
    FiniteEffectDecision,
    StaticSubstitutionReason,
    plan_finite_effect,
    plan_finite_loop,
    plan_one_shot_intro,
)


def _paint(duration_ms: int, color: str = "#3366FF") -> PaintStep:
    return PaintStep(
        segments=(
            WholeBar(
                color=color,
                timing=Timing(duration_ms=duration_ms),
            ),
        )
    )


def _animation(
    duration_ms: int,
    *,
    repeat: int | object | None = "absent",
    tail_duration_ms: int = 0,
) -> Animation:
    steps: list[object] = [_paint(duration_ms)]
    if repeat != "absent":
        steps.append(RepeatStep(repeat if repeat is None else int(repeat)))
    if tail_duration_ms:
        steps.append(_paint(tail_duration_ms, "#22CC88"))
    return Animation(name="finite cue", steps=tuple(steps))  # type: ignore[arg-type]


def test_one_shot_intro_is_never_repeated() -> None:
    animation = _animation(900)

    plan = plan_one_shot_intro("completion-intro", animation)

    assert plan.decision is FiniteEffectDecision.PLAY
    assert plan.intro is not None
    assert plan.intro.animation is animation
    assert plan.intro.duration_ms == 900
    assert plan.intro.repetitions == 1
    assert plan.loop is None
    assert plan.total_duration_ms == 900
    assert plan.substitution_reason is None


def test_intro_and_loop_form_one_bounded_plan() -> None:
    intro = _animation(300)
    loop = _animation(700, repeat=2)

    plan = plan_finite_effect(
        "handoff-baton",
        intro_animation=intro,
        loop_animation=loop,
    )

    assert plan.intro is not None
    assert plan.intro.duration_ms == 300
    assert plan.loop is not None
    assert plan.loop.repetitions == 2
    assert plan.loop.total_duration_ms == 1400
    assert plan.total_duration_ms == 1700


def test_unbounded_firmware_repeat_becomes_a_finite_product_loop() -> None:
    animation = _animation(800, repeat=None)

    plan = plan_finite_loop("work-loop", animation)

    assert plan.loop is not None
    assert plan.loop.source_animation is animation
    assert plan.loop.animation is not animation
    assert isinstance(plan.loop.animation.steps[1], RepeatStep)
    assert plan.loop.animation.steps[1].count == MAX_FINITE_REPETITIONS
    assert isinstance(animation.steps[1], RepeatStep)
    assert animation.steps[1].count is None
    assert plan.loop.source_repetitions is None
    assert plan.loop.source_was_unbounded is True
    assert plan.loop.repetitions == MAX_FINITE_REPETITIONS
    assert plan.loop.repetitions_clamped is True
    assert plan.total_duration_ms == 800 * MAX_FINITE_REPETITIONS


def test_explicit_repeat_request_cannot_weaken_the_hard_bound() -> None:
    animation = _animation(600)

    plan = plan_finite_loop(
        "notification-loop",
        animation,
        requested_repetitions=999,
    )

    assert plan.loop is not None
    assert plan.loop.source_repetitions == 1
    assert plan.loop.repetitions == MAX_FINITE_REPETITIONS
    assert plan.loop.repetitions_clamped is True
    assert isinstance(plan.loop.animation.steps[-1], RepeatStep)
    assert plan.loop.animation.steps[-1].count == MAX_FINITE_REPETITIONS


def test_finite_source_repeat_count_is_preserved_when_within_bounds() -> None:
    animation = _animation(750, repeat=2)

    plan = plan_finite_loop("failure-loop", animation)

    assert plan.loop is not None
    assert plan.loop.source_repetitions == 2
    assert plan.loop.repetitions == 2
    assert plan.loop.repetitions_clamped is False


def test_short_loop_receives_rest_to_enforce_safe_cadence() -> None:
    animation = _animation(100, repeat=2)

    plan = plan_finite_loop("ask-heartbeat", animation)

    assert plan.loop is not None
    cadence = plan.loop.cadence
    assert cadence.source_cycle_ms == 100
    assert cadence.effective_cycle_ms == MIN_SAFE_CYCLE_MS == 500
    assert cadence.inserted_rest_ms == 400
    assert cadence.source_hz == 10.0
    assert cadence.effective_hz == MAX_SAFE_CADENCE_HZ == 2.0
    assert cadence.adjusted is True
    assert cadence.safe is True
    assert plan.loop.total_duration_ms == 1000
    assert isinstance(plan.loop.animation.steps[1], PaintStep)
    assert plan.loop.animation.steps[1].segments[0].color == "off"
    assert plan.loop.animation.steps[1].segments[0].timing.duration_ms == 400
    assert isinstance(plan.loop.animation.steps[2], RepeatStep)
    assert plan.loop.animation.steps[2].count == 2


def test_cycle_at_safety_boundary_is_not_modified() -> None:
    animation = _animation(MIN_SAFE_CYCLE_MS, repeat=2)

    plan = plan_finite_loop("boundary-loop", animation)

    assert plan.loop is not None
    assert plan.loop.cadence.inserted_rest_ms == 0
    assert plan.loop.cadence.adjusted is False
    assert plan.loop.cadence.safe is True


def test_steps_after_firmware_repeat_are_a_one_shot_tail() -> None:
    animation = _animation(700, repeat=2, tail_duration_ms=250)

    plan = plan_finite_loop("loop-with-tail", animation)

    assert plan.loop is not None
    assert plan.loop.cadence.source_cycle_ms == 700
    assert plan.loop.post_loop_duration_ms == 250
    assert plan.loop.total_duration_ms == 1650


def test_duration_budget_can_reduce_repetitions_without_truncating_a_cycle() -> None:
    animation = _animation(800, repeat=2, tail_duration_ms=200)

    plan = plan_finite_loop(
        "budgeted-loop",
        animation,
        max_total_duration_ms=1100,
    )

    assert plan.loop is not None
    assert plan.loop.repetitions == 1
    assert plan.loop.repetitions_clamped is True
    assert plan.loop.total_duration_ms == 1000
    assert plan.total_duration_ms == 1000


def test_effect_that_cannot_fit_one_whole_cycle_uses_static_fallback() -> None:
    animation = _animation(900, repeat=2, tail_duration_ms=300)

    plan = plan_finite_loop(
        "oversized-loop",
        animation,
        static_fallback_key="failure-static",
        max_total_duration_ms=1000,
    )

    assert plan.decision is FiniteEffectDecision.STATIC_SUBSTITUTE
    assert plan.static_fallback_key == "failure-static"
    assert plan.substitution_reason is StaticSubstitutionReason.DURATION_BUDGET
    assert plan.intro is None
    assert plan.loop is None
    assert plan.total_duration_ms == 0


def test_intro_that_exceeds_duration_budget_uses_static_fallback() -> None:
    plan = plan_one_shot_intro(
        "oversized-intro",
        _animation(1200),
        max_total_duration_ms=1000,
    )

    assert plan.decision is FiniteEffectDecision.STATIC_SUBSTITUTE
    assert plan.substitution_reason is StaticSubstitutionReason.DURATION_BUDGET


def test_reduce_motion_substitutes_static_before_any_motion_planning() -> None:
    plan = plan_finite_effect(
        "ambient-motion",
        intro_animation=_animation(300),
        loop_animation=_animation(100, repeat=None),
        requested_repetitions=200,
        reduce_motion=True,
        static_fallback_key="ambient-rest",
    )

    assert plan.decision is FiniteEffectDecision.STATIC_SUBSTITUTE
    assert plan.static_fallback_key == "ambient-rest"
    assert plan.substitution_reason is StaticSubstitutionReason.REDUCE_MOTION
    assert plan.reduce_motion_substituted is True
    assert plan.has_motion is False
    assert plan.intro is None
    assert plan.loop is None
    assert plan.total_duration_ms == 0


def test_zero_duration_motion_fails_closed_to_static() -> None:
    plan = plan_finite_loop(
        "zero-loop",
        Animation(name="zero", steps=()),
        static_fallback_key="rest",
    )

    assert plan.decision is FiniteEffectDecision.STATIC_SUBSTITUTE
    assert plan.substitution_reason is StaticSubstitutionReason.NO_TIMED_MOTION


def test_plans_and_nested_phases_are_immutable() -> None:
    plan = plan_finite_effect(
        "immutable",
        intro_animation=_animation(200),
        loop_animation=_animation(600, repeat=2),
    )
    assert plan.intro is not None
    assert plan.loop is not None

    with pytest.raises(FrozenInstanceError):
        plan.total_duration_ms = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.intro.duration_ms = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.loop.repetitions = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.loop.cadence.inserted_rest_ms = 0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({}, "an introduction or loop animation is required"),
        ({"requested_repetitions": 2}, "require a loop animation"),
        ({"reduce_motion": 1}, "reduce motion must be a boolean"),
        ({"max_repetitions": 0}, "maximum repetitions must be a positive"),
        (
            {"max_repetitions": MAX_FINITE_REPETITIONS + 1},
            "maximum repetitions cannot exceed",
        ),
        ({"max_total_duration_ms": 0}, "maximum total duration must be a positive"),
        (
            {"max_total_duration_ms": MAX_FINITE_TOTAL_DURATION_MS + 1},
            "maximum total duration cannot exceed",
        ),
    ),
)
def test_invalid_policy_inputs_are_rejected(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        plan_finite_effect("effect", **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ("", " padded", "padded ", "x" * 129, 7))
def test_effect_and_fallback_keys_are_bounded_opaque_text(value: object) -> None:
    with pytest.raises(ValueError, match="effect key"):
        plan_finite_effect(value, intro_animation=_animation(100))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="static fallback key"):
        plan_finite_effect(
            "effect",
            intro_animation=_animation(100),
            static_fallback_key=value,  # type: ignore[arg-type]
        )


def test_multiple_source_repeat_markers_are_rejected() -> None:
    animation = Animation(
        name="invalid",
        steps=(_paint(600), RepeatStep(1), RepeatStep(1)),
    )

    with pytest.raises(ValueError, match="at most one repeat"):
        plan_finite_loop("invalid-loop", animation)


def test_one_shot_intro_rejects_a_source_repeat_marker() -> None:
    with pytest.raises(ValueError, match="one-shot introduction"):
        plan_one_shot_intro("invalid-intro", _animation(600, repeat=1))


@pytest.mark.parametrize("count", (0, -1, True, 65536))
def test_invalid_source_repeat_count_is_rejected(count: object) -> None:
    animation = Animation(
        name="invalid repeat",
        steps=(_paint(600), RepeatStep(count)),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="invalid repeat count"):
        plan_finite_loop("invalid-loop", animation)


def test_explicit_repetition_override_must_be_a_positive_integer() -> None:
    animation = _animation(600)

    for value in (0, -1, True, 1.5, "2"):
        with pytest.raises(ValueError, match="requested repetitions"):
            plan_finite_loop(
                "invalid-repeat",
                animation,
                requested_repetitions=value,  # type: ignore[arg-type]
            )
