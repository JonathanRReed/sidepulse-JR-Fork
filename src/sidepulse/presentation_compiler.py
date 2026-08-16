"""Mandatory temporal-safety compiler for every SidePulse light surface."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .animation import (
    Animation,
    AnimationValidationError,
    ColorList,
    IndexedPaint,
    PaintStep,
    RepeatStep,
    RollStep,
    Timing,
    WholeBar,
    errors_only,
    read_program,
    render_animation,
    validate_animation,
)

MAX_PRESENTATION_HZ = 2.0
MIN_PRESENTATION_CYCLE_MS = 500
MIN_PRESENTATION_PHASE_MS = 250
MAX_SATURATED_RED_HZ = 1.0
MIN_SATURATED_RED_CYCLE_MS = 1000
MIN_SATURATED_RED_PHASE_MS = 500
SAFE_FALLBACK_PROGRAM = "off"
MAX_TIME_MS = 65535


@dataclass(frozen=True, slots=True)
class PresentationCompileResult:
    program: str
    accepted: bool
    transformed: bool
    reasons: tuple[str, ...]


class PresentationSafetyError(ValueError):
    pass


def _is_saturated_red(color: str) -> bool:
    if not isinstance(color, str) or not color.startswith("#") or len(color) != 7:
        return False
    try:
        red, green, blue = (
            int(color[index : index + 2], 16) for index in (1, 3, 5)
        )
    except ValueError:
        return False
    return red >= 192 and green <= 64 and blue <= 64


def _segment_colors(segment) -> tuple[str, ...]:
    if type(segment) is WholeBar:
        return (segment.color,)
    if type(segment) is ColorList:
        return segment.colors
    if type(segment) is IndexedPaint:
        return tuple(color for _index, color in segment.assignments)
    return ()


def _step_has_saturated_red(step) -> bool:
    return type(step) is PaintStep and any(
        _is_saturated_red(color)
        for segment in step.segments
        for color in _segment_colors(segment)
    )


def _safe_timing(timing: Timing, *, saturated_red: bool) -> tuple[Timing, bool]:
    minimum = (
        MIN_SATURATED_RED_PHASE_MS if saturated_red else MIN_PRESENTATION_PHASE_MS
    )
    duration = timing.duration_ms
    if duration is None:
        duration = minimum
        if timing.easing == "pulse":
            duration = (
                MIN_SATURATED_RED_CYCLE_MS
                if saturated_red
                else MIN_PRESENTATION_CYCLE_MS
            )
    else:
        duration = max(minimum, duration)
        if timing.easing == "pulse":
            duration = max(
                MIN_SATURATED_RED_CYCLE_MS
                if saturated_red
                else MIN_PRESENTATION_CYCLE_MS,
                duration,
            )
    delay = timing.delay_ms
    if delay is not None:
        delay = max(minimum, delay)
    if (duration is not None and duration > MAX_TIME_MS) or (
        delay is not None and delay > MAX_TIME_MS
    ):
        raise PresentationSafetyError("safe timing exceeds firmware limit")
    updated = replace(timing, duration_ms=duration, delay_ms=delay)
    return updated, updated != timing


def _safe_segment(segment, *, saturated_red: bool):
    timing, changed = _safe_timing(segment.timing, saturated_red=saturated_red)
    return replace(segment, timing=timing), changed


def _safe_animation(animation: Animation) -> tuple[Animation, tuple[str, ...]]:
    reasons: list[str] = []
    transformed_steps = []
    saw_red = False
    repeat_index = next(
        (
            index
            for index, step in enumerate(animation.steps)
            if type(step) is RepeatStep
        ),
        None,
    )
    for step in animation.steps:
        if type(step) is PaintStep:
            red = _step_has_saturated_red(step)
            saw_red = saw_red or red
            segments = []
            changed = False
            for segment in step.segments:
                safe, segment_changed = _safe_segment(segment, saturated_red=red)
                segments.append(safe)
                changed = changed or segment_changed
            transformed_steps.append(replace(step, segments=tuple(segments)))
            if changed:
                reasons.append("phase_cadence_clamped")
        elif type(step) is RollStep:
            duration = max(MIN_PRESENTATION_PHASE_MS, step.duration_ms)
            if duration > MAX_TIME_MS:
                raise PresentationSafetyError("safe roll exceeds firmware limit")
            transformed_steps.append(replace(step, duration_ms=duration))
            if duration != step.duration_ms:
                reasons.append("roll_cadence_clamped")
        else:
            transformed_steps.append(step)

    transformed = Animation(animation.name, tuple(transformed_steps))
    if repeat_index is not None:
        from .animation import loop_duration_ms

        loop_ms = loop_duration_ms(transformed)
        required = (
            MIN_SATURATED_RED_CYCLE_MS if saw_red else MIN_PRESENTATION_CYCLE_MS
        )
        if loop_ms is not None and 0 < loop_ms < required:
            factor = math.ceil(required / loop_ms)
            scaled = []
            for index, step in enumerate(transformed.steps):
                if index >= repeat_index:
                    scaled.append(step)
                    continue
                if type(step) is PaintStep:
                    segments = []
                    for segment in step.segments:
                        timing = segment.timing
                        duration = (
                            None
                            if timing.duration_ms is None
                            else timing.duration_ms * factor
                        )
                        delay = (
                            None
                            if timing.delay_ms is None
                            else timing.delay_ms * factor
                        )
                        if (duration is not None and duration > MAX_TIME_MS) or (
                            delay is not None and delay > MAX_TIME_MS
                        ):
                            raise PresentationSafetyError(
                                "safe loop timing exceeds firmware limit"
                            )
                        segments.append(
                            replace(
                                segment,
                                timing=replace(
                                    timing,
                                    duration_ms=duration,
                                    delay_ms=delay,
                                ),
                            )
                        )
                    scaled.append(replace(step, segments=tuple(segments)))
                elif type(step) is RollStep:
                    duration = step.duration_ms * factor
                    if duration > MAX_TIME_MS:
                        raise PresentationSafetyError(
                            "safe loop timing exceeds firmware limit"
                        )
                    scaled.append(replace(step, duration_ms=duration))
                else:
                    scaled.append(step)
            transformed = Animation(animation.name, tuple(scaled))
            reasons.append("loop_cadence_clamped")

    return transformed, tuple(dict.fromkeys(reasons))


def compile_presentation_program(
    program: str,
    *,
    led_count: int = 8,
    fallback: str = SAFE_FALLBACK_PROGRAM,
) -> PresentationCompileResult:
    animation, problems = read_program(program, led_count=led_count)
    if errors_only(problems):
        return PresentationCompileResult(
            fallback,
            False,
            program != fallback,
            ("invalid_program",),
        )
    try:
        safe_animation, reasons = _safe_animation(animation)
    except (PresentationSafetyError, AnimationValidationError, ValueError):
        return PresentationCompileResult(
            fallback,
            False,
            program != fallback,
            ("unsafe_program",),
        )
    safe_problems = validate_animation(safe_animation, led_count=led_count)
    if errors_only(safe_problems):
        return PresentationCompileResult(
            fallback,
            False,
            program != fallback,
            ("unsafe_program",),
        )
    safe_program = render_animation(safe_animation)
    return PresentationCompileResult(
        safe_program,
        True,
        safe_program != program,
        reasons,
    )
