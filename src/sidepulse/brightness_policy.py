"""Pure brightness-policy decisions for the retained JR Bar runtime."""

from __future__ import annotations

from dataclasses import dataclass

MIN_ESCALATION_VISIBLE_BRIGHTNESS = 12


@dataclass(frozen=True, slots=True)
class BrightnessTraceStep:
    name: str
    before: float
    after: float
    factor: float | None = None
    floor: float | None = None


@dataclass(frozen=True, slots=True)
class BrightnessPolicyResult:
    brightness: int
    trace: tuple[BrightnessTraceStep, ...]


def _normalize_brightness(value: float | None) -> int:
    if value is None:
        return 255
    return max(0, min(255, int(round(float(value)))))


def _scaled_step(name: str, current: float, factor: float) -> tuple[float, BrightnessTraceStep]:
    before = float(current)
    applied = float(factor)
    after = before * applied
    return after, BrightnessTraceStep(name, before=before, factor=applied, after=after)


def _floor_step(name: str, current: float, floor: float) -> tuple[float, BrightnessTraceStep]:
    before = float(current)
    minimum = float(floor)
    after = max(before, minimum)
    return after, BrightnessTraceStep(name, before=before, floor=minimum, after=after)


def _normalized_result(
    current: float,
    trace: list[BrightnessTraceStep],
) -> BrightnessPolicyResult:
    before = float(current)
    brightness = _normalize_brightness(before)
    trace.append(
        BrightnessTraceStep(
            "normalize",
            before=before,
            after=float(brightness),
        )
    )
    return BrightnessPolicyResult(brightness=brightness, trace=tuple(trace))


def plan_ambient_brightness(
    *,
    base_brightness: float,
    idle_factor: float,
    focus_factor: float,
    night_factor: float,
    global_factor: float,
    escalation_boost: float,
    is_screen_bar: bool,
    screen_bar_min_glow: float,
    dnd_factor: float | None = None,
) -> BrightnessPolicyResult:
    scaled = float(base_brightness)
    trace: list[BrightnessTraceStep] = [
        BrightnessTraceStep("base", before=scaled, after=scaled)
    ]
    for name, factor in (
        ("idle_dim", idle_factor),
        ("focus_sync", focus_factor),
        ("night_dim", night_factor),
        ("global_brightness", global_factor),
    ):
        scaled, step = _scaled_step(name, scaled, factor)
        trace.append(step)
    if dnd_factor is not None:
        scaled, step = _scaled_step("dnd_dim", scaled, dnd_factor)
        trace.append(step)
        if float(dnd_factor) <= 0.0:
            return _normalized_result(0.0, trace)
    scaled, step = _scaled_step("escalation_boost", scaled, escalation_boost)
    trace.append(step)
    if float(escalation_boost) > 1.0:
        scaled, step = _floor_step(
            "escalation_floor",
            scaled,
            float(MIN_ESCALATION_VISIBLE_BRIGHTNESS),
        )
        trace.append(step)
    if is_screen_bar and scaled > 0.0:
        scaled, step = _floor_step(
            "screen_bar_min_glow",
            scaled,
            255.0 * float(screen_bar_min_glow),
        )
        trace.append(step)
    return _normalized_result(scaled, trace)


def plan_signal_brightness(
    *,
    configured_brightness: float,
    global_factor: float,
    escalation_boost: float,
    focus_scale: float,
    dnd_factor: float | None = None,
) -> BrightnessPolicyResult:
    if float(focus_scale) <= 0.0:
        current = float(configured_brightness)
        return _normalized_result(
            0.0,
            [
                BrightnessTraceStep(
                    "focus_turn_off",
                    before=current,
                    floor=0.0,
                    after=0.0,
                ),
            ],
        )
    scaled = float(configured_brightness)
    trace: list[BrightnessTraceStep] = [
        BrightnessTraceStep("configured_brightness", before=scaled, after=scaled)
    ]
    scaled, step = _scaled_step("global_brightness", scaled, global_factor)
    trace.append(step)
    if dnd_factor is not None:
        scaled, step = _scaled_step("dnd_dim", scaled, dnd_factor)
        trace.append(step)
        if float(dnd_factor) <= 0.0:
            return _normalized_result(0.0, trace)
    scaled, step = _scaled_step("escalation_boost", scaled, escalation_boost)
    trace.append(step)
    scaled, step = _floor_step("minimum_signal_visibility", scaled, 1.0)
    trace.append(step)
    return _normalized_result(scaled, trace)


__all__ = [
    "MIN_ESCALATION_VISIBLE_BRIGHTNESS",
    "BrightnessPolicyResult",
    "BrightnessTraceStep",
    "plan_ambient_brightness",
    "plan_signal_brightness",
]
