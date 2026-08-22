from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from itertools import pairwise

from .accessibility_display import AccessibilityDisplayPreferences
from .signals import ATTENTION_ARRIVAL_TAPS
from .temporal_safety import (
    CalibrationState,
    SafeTemporalProgram,
    StaticSemanticFallback,
    TemporalFrame,
    TemporalProgram,
    analyze_temporal_safety,
)

MAX_EPISODE_KEY_BYTES = 128
MAX_PROVIDER_KEY_BYTES = 128
MAX_FINITE_CUE_DURATION_SECONDS = 60.0
RELAY_TRAVERSAL_SECONDS = 1.6
MAX_PROGRAM_BYTES = 512
MAX_PROGRAM_LINES = 20
# An epoch-scale timestamp is not a monotonic presentation anchor. Ten years is
# deliberately far beyond a realistic single boot while remaining far below
# contemporary wall-clock values.
MAX_MONOTONIC_SECONDS = 10.0 * 365.25 * 24.0 * 60.0 * 60.0


class GlanceSemantic(str, Enum):
    ATTENTION = "attention"
    FRESH_FAILURE = "fresh_failure"
    FRESH_COMPLETION = "fresh_completion"
    ACTIVE = "active"
    UNRESOLVED_FAILURE = "unresolved_failure"
    CAPACITY = "capacity"
    REST = "rest"


class GlanceOverrideReason(str, Enum):
    NONE = "none"
    EXPLICIT_DEVICE_MODE = "explicit_device_mode"
    PROVIDER_PIN = "provider_pin"
    SAFETY_SIGNAL = "safety_signal"
    FOCUS = "focus"
    SHARED_SPACE_PRIVACY = "shared_space_privacy"
    UNAVAILABLE = "unavailable"


class MotionClass(str, Enum):
    STATIC = "static"
    FINITE = "finite"
    CONTINUOUS = "continuous"


class SemanticGlyph(str, Enum):
    FULL_ANCHOR = "full_anchor"
    LEFT_ANCHOR = "left_anchor"
    RIGHT_ANCHOR = "right_anchor"
    CENTER_PAIR = "center_pair"
    CAPACITY_FILL = "capacity_fill"
    REST = "rest"


@dataclass(frozen=True, slots=True)
class CapacityGlance:
    provider_key: str
    remaining_fraction: float


@dataclass(frozen=True, slots=True)
class FiniteCue:
    event_key: str
    semantic: GlanceSemantic
    repetitions: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class FiniteCueBudget:
    max_repetitions: int = 2
    max_active: int = 1
    max_pending: int = 1
    max_consumed_keys: int = 256


@dataclass(frozen=True, slots=True)
class FiniteCueState:
    active: FiniteCue | None
    pending: FiniteCue | None
    next_deadline: float | None
    overflowed: bool


@dataclass(frozen=True, slots=True)
class GlanceInputs:
    actionable_episode_key: str | None
    fresh_failure: FiniteCue | None
    fresh_completion: FiniteCue | None
    active: bool
    unresolved_failure: bool
    capacity: CapacityGlance | None
    override_reason: GlanceOverrideReason = GlanceOverrideReason.NONE
    override_semantic: GlanceSemantic | None = None


@dataclass(frozen=True, slots=True)
class ResolvedGlance:
    semantic: GlanceSemantic
    glyph: SemanticGlyph
    cue: FiniteCue | None
    override_reason: GlanceOverrideReason
    relay_epoch: float
    next_visual_change_at: float | None


@dataclass(frozen=True, slots=True)
class PresentationProgram:
    semantic: GlanceSemantic
    glyph: SemanticGlyph
    motion: MotionClass
    dsl: str
    static_fallback_dsl: str
    temporal: TemporalProgram | None
    trusted_period_seconds: float | None
    relay_epoch: float
    next_visual_change_at: float | None
    playback_anchor: float | None = None


_GLYPHS = {
    GlanceSemantic.ATTENTION: SemanticGlyph.FULL_ANCHOR,
    GlanceSemantic.FRESH_FAILURE: SemanticGlyph.LEFT_ANCHOR,
    GlanceSemantic.FRESH_COMPLETION: SemanticGlyph.RIGHT_ANCHOR,
    GlanceSemantic.ACTIVE: SemanticGlyph.CENTER_PAIR,
    GlanceSemantic.UNRESOLVED_FAILURE: SemanticGlyph.LEFT_ANCHOR,
    GlanceSemantic.CAPACITY: SemanticGlyph.CAPACITY_FILL,
    GlanceSemantic.REST: SemanticGlyph.REST,
}


def resolve_glance(
    inputs: GlanceInputs,
    *,
    presentation_time: float,
    relay_epoch: float,
    preferences: AccessibilityDisplayPreferences,
) -> ResolvedGlance:
    """Resolve canonical inputs through the exact shared glance ladder."""
    if not _valid_clock_pair(presentation_time, relay_epoch):
        return _rest_result()
    if not isinstance(inputs, GlanceInputs) or not _valid_preferences(preferences):
        return _rest_result()
    if type(inputs.active) is not bool or type(inputs.unresolved_failure) is not bool:
        return _rest_result()

    semantic, cue = _automatic_glance(inputs)
    override_reason = GlanceOverrideReason.NONE
    if (
        isinstance(inputs.override_reason, GlanceOverrideReason)
        and inputs.override_reason is not GlanceOverrideReason.NONE
        and isinstance(inputs.override_semantic, GlanceSemantic)
    ):
        semantic = inputs.override_semantic
        cue = None
        override_reason = inputs.override_reason

    if preferences.reduce_motion:
        cue = None
    deadline = (
        presentation_time + cue.repetitions * cue.duration_seconds
        if cue is not None
        else None
    )
    return ResolvedGlance(
        semantic=semantic,
        glyph=_GLYPHS[semantic],
        cue=cue,
        override_reason=override_reason,
        relay_epoch=relay_epoch,
        next_visual_change_at=deadline,
    )


def compose_presentation_program(
    resolved: ResolvedGlance,
    *,
    presentation_time: float,
    led_count: int,
    color: str,
    preferences: AccessibilityDisplayPreferences,
    capacity_remaining_fraction: float | None = None,
    calibration: CalibrationState = CalibrationState(),
    motion_style: str | None = None,
    provider: str | None = None,
    color_settings=None,
) -> PresentationProgram:
    """Compose one hue-independent semantic glyph for a bounded surface.

    ``motion_style`` is the provider's own chosen rhythm ("breathe",
    "blink", "steady") and applies ONLY to the ACTIVE semantic: this was
    the dead half of the per-provider animation setting -- the
    multi-agent renderers honored it, but a SOLO agent renders through
    this composer, which always chased. Urgent semantics ignore it,
    exactly as agent_motion does.
    """
    if (
        not isinstance(resolved, ResolvedGlance)
        or not valid_presentation_time(presentation_time)
        or not _valid_preferences(preferences)
        or type(led_count) is not int
        or led_count <= 0
    ):
        return _static_program(
            _rest_result(),
            dsl="off",
        )

    from .colors import (
        normalize_hex,
        relay_led_order,
        relay_phase_index,
        relay_step_ms,
    )
    from .led_status import ASK_AMBER, settle_duration_ms

    if motion_style is None and provider and color_settings is not None:
        from .colors import PROVIDER_ANIMATION_AUTO

        try:
            chosen = color_settings.agent_animation(provider)
        except Exception:
            chosen = PROVIDER_ANIMATION_AUTO
        if chosen != PROVIDER_ANIMATION_AUTO:
            motion_style = chosen

    normalized_color = normalize_hex(color, ASK_AMBER)
    if resolved.semantic is GlanceSemantic.ACTIVE:
        # ACTIVE is the one semantic painted in an agent's IDENTITY color
        # (color_for_resolved_glance) -- floor it so a dark brand or custom
        # pick stays a lit LED. REST keeps its deliberate idle dim.
        from .colors import readable_identity_hex

        normalized_color = readable_identity_hex(normalized_color)
    intensities = _glyph_intensities(
        resolved.semantic,
        led_count=led_count,
        capacity_remaining_fraction=capacity_remaining_fraction,
        differentiate_without_color=preferences.differentiate_without_color,
        increase_contrast=preferences.increase_contrast,
    )
    fallback = _static_glyph_dsl(normalized_color, intensities)
    motion = _motion_for_resolved(resolved, preferences=preferences)

    if motion is MotionClass.STATIC:
        return _static_program(resolved, dsl=fallback)

    if motion is MotionClass.FINITE:
        assert resolved.cue is not None
        cue = resolved.cue
        half_duration = cue.duration_seconds / 2.0
        half_ms = max(1, round(half_duration * 1000.0))
        lowered_intensities = _lowered_glyph_intensities(intensities)
        frames_intensities = tuple(
            vector
            for _ in range(cue.repetitions)
            for vector in (intensities, lowered_intensities)
        )
        lines = [
            _duration_glyph_dsl(
                normalized_color,
                frames_intensities[0],
                duration_ms=half_ms,
            )
        ]
        for previous, current in pairwise(frames_intensities):
            lines.append(
                _duration_glyph_delta_dsl(
                    normalized_color,
                    previous,
                    current,
                    duration_ms=half_ms,
                )
            )
        lines.append(fallback)
        temporal = TemporalProgram(
            frames=tuple(
                TemporalFrame(_mean_intensity(vector), half_duration)
                for vector in frames_intensities
            ),
            repeat_count=1,
            static_fallback=StaticSemanticFallback(
                resolved.semantic.value,
                _mean_intensity(intensities),
            ),
        )
        candidate = PresentationProgram(
            semantic=resolved.semantic,
            glyph=resolved.glyph,
            motion=motion,
            dsl="\n".join(lines),
            static_fallback_dsl=fallback,
            temporal=temporal,
            trusted_period_seconds=None,
            relay_epoch=resolved.relay_epoch,
            next_visual_change_at=resolved.next_visual_change_at,
        )
        return _bounded_and_safe(candidate, calibration=calibration)

    if resolved.semantic is GlanceSemantic.ACTIVE and motion_style in (
        "breathe",
        "blink",
        "steady",
        "heartbeat",
        "scanner",
        "comet",
        "flicker",
        "stack",
        "twinkle",
        "drift",
        "converge",
        "aurora",
        "tide",
    ):
        if motion_style == "steady":
            return _static_program(resolved, dsl=fallback)
        floor_color = _scaled_color(normalized_color, 0.05)
        peak_color = _scaled_color(normalized_color, 1.0)
        settle_text = f"{floor_color} 160ms cosine"
        if motion_style == "breathe":
            cycle_ms = round(RELAY_TRAVERSAL_SECONDS * 2000.0)
            lines = (
                settle_text,
                f"{peak_color} {cycle_ms}ms pulse",
                "repeat",
            )
            cycle_seconds = 0.16 + cycle_ms / 1000.0
        elif motion_style == "blink":
            half_ms = round(RELAY_TRAVERSAL_SECONDS * 500.0)
            lines = (
                f"{peak_color} {half_ms}ms none",
                f"{floor_color} {half_ms}ms none",
                "repeat",
            )
            cycle_seconds = half_ms / 500.0
        elif motion_style == "heartbeat":
            # Lub-dub then a long rest: the rhythm most separable from a
            # sinusoid in peripheral vision (Particle/WLED survey).
            lines = (
                settle_text,
                f"{peak_color} 300ms pulse; {peak_color} 300ms pulse 500ms",
                f"{floor_color} 1400ms none",
                "repeat",
            )
            cycle_seconds = 0.16 + 0.8 + 1.4
        elif motion_style == "scanner":
            # A dot bounces 0..7..0 across 2.8s -- born on 8 elements.
            step_ms, width_ms = 200, 400
            segments = [
                f"{index}:{peak_color} {width_ms}ms pulse {index * step_ms}ms"
                for index in range(led_count)
            ] + [
                f"{index}:{peak_color} {width_ms}ms pulse {(2 * led_count - 2 - index) * step_ms}ms"
                for index in range(1, led_count - 1)
            ]
            lines = (settle_text, "; ".join(segments), "repeat")
            cycle_seconds = 0.16 + ((2 * led_count - 3) * step_ms + width_ms) / 1000.0
        elif motion_style == "comet":
            # One-way sweep with an eased tail, then a dark beat.
            step_ms, width_ms = 180, 420
            segments = [
                f"{index}:{peak_color} {width_ms}ms pulse {index * step_ms}ms"
                for index in range(led_count)
            ]
            lines = (
                settle_text,
                "; ".join(segments),
                f"{floor_color} 600ms none",
                "repeat",
            )
            cycle_seconds = 0.16 + ((led_count - 1) * step_ms + width_ms + 600) / 1000.0
        elif motion_style == "stack":
            # LEDs pile on hard, one by one, hold the full bar, then the
            # whole strip eases away together -- "adding on top of each
            # other until it disappears".
            step_ms, hold_ms = 250, 600
            segments = [
                f"{index}:{peak_color} "
                f"{(led_count - index) * step_ms + hold_ms}ms none {index * step_ms}ms"
                for index in range(led_count)
            ]
            lines = (
                settle_text,
                "; ".join(segments),
                f"{floor_color} 900ms cosine",
                "repeat",
            )
            cycle_seconds = 0.16 + (led_count * step_ms + hold_ms + 900) / 1000.0
        elif motion_style == "twinkle":
            # Scattered single sparks over a dim base; frozen offsets.
            offsets = (0, 1300, 2700, 700, 3400, 2100, 500, 1800)
            segments = [
                f"{index}:{peak_color} 450ms pulse "
                f"{offsets[index % len(offsets)]}ms"
                for index in range(led_count)
            ]
            lines = (settle_text, "; ".join(segments), "repeat")
            cycle_seconds = 0.16 + (3400 + 450) / 1000.0
        elif motion_style == "drift":
            # Glacial detuned swells -- slow water. Periods per LED are
            # slightly different so the interference never visibly loops.
            segments = [
                f"{index}:{peak_color} "
                f"{2600 + index * 140}ms pulse {(index * 530) % 1300}ms"
                for index in range(led_count)
            ]
            lines = (settle_text, "; ".join(segments), "repeat")
            cycle_seconds = 0.16 + (2600 + (led_count - 1) * 140 + 1299) / 1000.0
        elif motion_style == "converge":
            # Two dots leave the ends and meet at the center pair.
            step_ms, width_ms = 240, 420
            segments = [
                f"{index}:{peak_color} {width_ms}ms pulse "
                f"{min(index, led_count - 1 - index) * step_ms}ms"
                for index in range(led_count)
            ]
            lines = (
                settle_text,
                "; ".join(segments),
                f"{floor_color} 500ms none",
                "repeat",
            )
            cycle_seconds = 0.16 + (
                (led_count // 2 - 1) * step_ms + width_ms + 500
            ) / 1000.0
        elif motion_style == "aurora":
            # Rolling waves over a LUMINOUS base: like drift, but resting
            # on a visible quarter-bright bed instead of near-dark, with
            # wider detune -- light moving on water at night.
            bed_color = _scaled_color(normalized_color, 0.22)
            segments = [
                f"{index}:{peak_color} "
                f"{2400 + index * 220}ms pulse {(index * 617) % 1600}ms"
                for index in range(led_count)
            ]
            lines = (
                f"{bed_color} 160ms cosine",
                "; ".join(segments),
                "repeat",
            )
            cycle_seconds = 0.16 + (2400 + (led_count - 1) * 220 + 1599) / 1000.0
        elif motion_style == "tide":
            # The bar rises to full and the water pulls back: LED 0 rises
            # first and falls last, LED 7 crests briefly at the top.
            step_ms = 200
            segments = [
                f"{index}:{peak_color} "
                f"{2 * (led_count - index) * step_ms}ms pulse {index * step_ms}ms"
                for index in range(led_count)
            ]
            lines = (settle_text, "; ".join(segments), "repeat")
            cycle_seconds = 0.16 + (2 * led_count * step_ms) / 1000.0
        else:
            # Flicker: frozen per-LED detune -- deterministic shimmer.
            base_ms = 1800
            segments = [
                f"{index}:{peak_color} "
                f"{base_ms + (index * 137) % 331}ms pulse {(index * 271) % 600}ms"
                for index in range(led_count)
            ]
            lines = (settle_text, "; ".join(segments), "repeat")
            cycle_seconds = 0.16 + (base_ms + 330 + 599) / 1000.0
        cycle_ms = max(1, round(cycle_seconds * 1000.0))
        elapsed = max(0.0, float(presentation_time) - resolved.relay_epoch)
        elapsed_ms = round(elapsed * 1000.0)
        anchor = float(presentation_time) - (elapsed_ms % cycle_ms) / 1000.0
        temporal = TemporalProgram(
            # Two full periods: the safety pass requires the analyzed
            # envelope to cover trusted_period + 1s.
            frames=tuple(
                TemporalFrame(_mean_intensity(intensities), cycle_seconds / 2.0)
                for _ in range(4)
            ),
            repeat_count=1,
            static_fallback=StaticSemanticFallback(
                resolved.semantic.value,
                _mean_intensity(intensities),
            ),
        )
        candidate = PresentationProgram(
            semantic=resolved.semantic,
            glyph=resolved.glyph,
            motion=motion,
            dsl="\n".join(lines),
            static_fallback_dsl=fallback,
            temporal=temporal,
            trusted_period_seconds=cycle_seconds,
            relay_epoch=resolved.relay_epoch,
            next_visual_change_at=resolved.next_visual_change_at,
            playback_anchor=anchor,
        )
        return _bounded_and_safe(candidate, calibration=calibration)

    elapsed = max(0.0, float(presentation_time) - resolved.relay_epoch)
    step_ms = relay_step_ms(RELAY_TRAVERSAL_SECONDS, led_count)
    start_index = relay_phase_index(
        elapsed,
        RELAY_TRAVERSAL_SECONDS,
        led_count,
    )
    elapsed_ms = round(elapsed * 1000.0)
    playback_anchor = float(presentation_time) - (elapsed_ms % step_ms) / 1000.0
    order = relay_led_order(led_count, start_index)
    settle_ms = settle_duration_ms(step_ms)
    floor_color = _scaled_color(normalized_color, 0.05)
    peak_color = _scaled_color(normalized_color, 1.0)
    resets = f"{floor_color} {settle_ms}ms cosine"
    pulses = "; ".join(
        f"{index}:{peak_color} {step_ms}ms pulse {turn * step_ms}ms"
        for turn, index in enumerate(order)
    )
    temporal = TemporalProgram(
        frames=tuple(
            TemporalFrame(_mean_intensity(intensities), RELAY_TRAVERSAL_SECONDS / led_count)
            for _ in range(led_count * 2)
        ),
        repeat_count=1,
        static_fallback=StaticSemanticFallback(
            resolved.semantic.value,
            _mean_intensity(intensities),
        ),
    )
    candidate = PresentationProgram(
        semantic=resolved.semantic,
        glyph=resolved.glyph,
        motion=motion,
        dsl="\n".join((resets, pulses, "repeat")),
        static_fallback_dsl=fallback,
        temporal=temporal,
        trusted_period_seconds=RELAY_TRAVERSAL_SECONDS,
        relay_epoch=resolved.relay_epoch,
        next_visual_change_at=resolved.next_visual_change_at,
        playback_anchor=playback_anchor,
    )
    return _bounded_and_safe(candidate, calibration=calibration)


def enforce_temporal_safety(
    program: PresentationProgram,
    *,
    calibration: CalibrationState,
) -> PresentationProgram:
    """Fail closed to typed steady truth for any untrusted or unsafe motion."""
    if not isinstance(program, PresentationProgram):
        return _static_program(_rest_result(), dsl="off")
    if program.motion is MotionClass.STATIC:
        return program
    if program.motion is MotionClass.FINITE and any(
        line.strip() == "repeat" for line in program.dsl.splitlines()
    ):
        return _fallback_program(program)
    if not isinstance(program.temporal, TemporalProgram):
        return _fallback_program(program)
    if program.motion is MotionClass.CONTINUOUS:
        period = program.trusted_period_seconds
        if (
            not _finite_number(period)
            or float(period) <= 0.0
            or _temporal_duration(program.temporal) < float(period) + 1.0
        ):
            return _fallback_program(program)
    outcome = analyze_temporal_safety(program.temporal, calibration=calibration)
    if not isinstance(outcome, SafeTemporalProgram):
        return _fallback_program(program)
    return program


def _bounded_and_safe(
    program: PresentationProgram,
    *,
    calibration: CalibrationState,
) -> PresentationProgram:
    if (
        len(program.dsl.encode("utf-8")) > MAX_PROGRAM_BYTES
        or len(program.dsl.splitlines()) > MAX_PROGRAM_LINES
    ):
        return _fallback_program(program)
    return enforce_temporal_safety(program, calibration=calibration)


def _motion_for_resolved(
    resolved: ResolvedGlance,
    *,
    preferences: AccessibilityDisplayPreferences,
) -> MotionClass:
    if preferences.reduce_motion:
        return MotionClass.STATIC
    if resolved.cue is not None:
        return MotionClass.FINITE
    if resolved.semantic is GlanceSemantic.ACTIVE:
        return MotionClass.CONTINUOUS
    return MotionClass.STATIC


def _glyph_intensities(
    semantic: GlanceSemantic,
    *,
    led_count: int,
    capacity_remaining_fraction: float | None,
    differentiate_without_color: bool,
    increase_contrast: bool,
) -> tuple[float, ...]:
    if led_count == 2:
        anchors = {
            GlanceSemantic.ATTENTION: (1.0, 1.0),
            GlanceSemantic.FRESH_FAILURE: (1.0, 0.2),
            GlanceSemantic.FRESH_COMPLETION: (0.2, 1.0),
            GlanceSemantic.ACTIVE: (0.65, 0.65),
            GlanceSemantic.UNRESOLVED_FAILURE: (0.55, 0.1),
            GlanceSemantic.CAPACITY: (0.05, 0.05),
            GlanceSemantic.REST: (0.05, 0.05),
        }
        return anchors[semantic]

    floor = 0.0 if differentiate_without_color else 0.05
    values = [floor] * led_count
    if semantic is GlanceSemantic.ATTENTION:
        values = [1.0] * led_count
    elif semantic is GlanceSemantic.FRESH_FAILURE:
        values[0 : min(2, led_count)] = [1.0] * min(2, led_count)
    elif semantic is GlanceSemantic.FRESH_COMPLETION:
        values[max(0, led_count - 2) :] = [1.0] * min(2, led_count)
    elif semantic is GlanceSemantic.ACTIVE:
        left = max(0, (led_count - 1) // 2)
        right = min(led_count - 1, led_count // 2)
        values[left] = values[right] = 0.65
    elif semantic is GlanceSemantic.UNRESOLVED_FAILURE:
        values[0] = 0.55
    elif semantic is GlanceSemantic.CAPACITY:
        fraction = _valid_fraction_or_zero(capacity_remaining_fraction)
        filled = fraction * led_count
        for index in range(led_count):
            values[index] = max(floor, min(1.0, filled - index))
    else:
        values = [0.05] * led_count

    if increase_contrast:
        values = [0.0 if value <= floor else min(1.0, value * 1.15) for value in values]
    return tuple(values)


def _static_glyph_dsl(color: str, intensities: tuple[float, ...]) -> str:
    return "; ".join(
        f"{index}:{_scaled_color(color, intensity)}"
        for index, intensity in enumerate(intensities)
    )


def _duration_glyph_dsl(
    color: str,
    intensities: tuple[float, ...],
    *,
    duration_ms: int,
) -> str:
    if intensities and all(
        math.isclose(value, intensities[0], rel_tol=0.0, abs_tol=1e-12)
        for value in intensities[1:]
    ):
        return f"{_scaled_color(color, intensities[0])} {duration_ms}ms cosine"
    return "; ".join(
        f"{index}:{_scaled_color(color, intensity)} {duration_ms}ms cosine"
        for index, intensity in enumerate(intensities)
    )


def _duration_glyph_delta_dsl(
    color: str,
    previous: tuple[float, ...],
    current: tuple[float, ...],
    *,
    duration_ms: int,
) -> str:
    if current and all(
        math.isclose(value, current[0], rel_tol=0.0, abs_tol=1e-12)
        for value in current[1:]
    ):
        return f"{_scaled_color(color, current[0])} {duration_ms}ms cosine"
    return "; ".join(
        f"{index}:{_scaled_color(color, value)} {duration_ms}ms cosine"
        for index, (prior, value) in enumerate(zip(previous, current))
        if not math.isclose(prior, value, rel_tol=0.0, abs_tol=1e-12)
    )


def _lowered_glyph_intensities(
    intensities: tuple[float, ...],
) -> tuple[float, ...]:
    if not intensities:
        return ()
    floor = min(intensities)
    if all(
        math.isclose(value, floor, rel_tol=0.0, abs_tol=1e-12)
        for value in intensities
    ):
        return tuple(max(0.05, value * 0.45) for value in intensities)
    return tuple(
        value if math.isclose(value, floor, rel_tol=0.0, abs_tol=1e-12)
        else max(floor + 0.1, value * 0.55)
        for value in intensities
    )


def _scaled_color(color: str, intensity: float) -> str:
    from .led_status import scale_hex_brightness

    return scale_hex_brightness(color, max(0.0, min(1.0, intensity)))


def _mean_intensity(intensities: tuple[float, ...]) -> float:
    if not intensities:
        return 0.0
    return max(0.0, min(1.0, math.fsum(intensities) / len(intensities)))


def _valid_fraction_or_zero(value: object) -> float:
    if not _finite_number(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _temporal_duration(program: TemporalProgram) -> float:
    try:
        return math.fsum(frame.duration_seconds for frame in program.frames) * int(
            program.repeat_count or 0
        )
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _static_program(
    resolved: ResolvedGlance,
    *,
    dsl: str,
    playback_anchor: float | None = None,
) -> PresentationProgram:
    return PresentationProgram(
        semantic=resolved.semantic,
        glyph=resolved.glyph,
        motion=MotionClass.STATIC,
        dsl=dsl,
        static_fallback_dsl=dsl,
        temporal=None,
        trusted_period_seconds=None,
        relay_epoch=resolved.relay_epoch,
        next_visual_change_at=None,
        playback_anchor=playback_anchor,
    )


def _fallback_program(program: PresentationProgram) -> PresentationProgram:
    return replace(
        program,
        motion=MotionClass.STATIC,
        dsl=program.static_fallback_dsl or "off",
        static_fallback_dsl=program.static_fallback_dsl or "off",
        temporal=None,
        trusted_period_seconds=None,
        next_visual_change_at=None,
    )


def continuous_presentation_identity(program: object) -> tuple[object, ...] | None:
    """Return phase-independent identity for one continuous presentation."""
    if not isinstance(program, PresentationProgram):
        return None
    if program.motion is not MotionClass.CONTINUOUS:
        return None
    return (
        program.semantic,
        program.glyph,
        program.motion,
        program.static_fallback_dsl,
        program.trusted_period_seconds,
        program.relay_epoch,
    )


def valid_finite_cue(
    cue: object,
    *,
    expected_semantic: GlanceSemantic | None = None,
) -> bool:
    if not isinstance(cue, FiniteCue):
        return False
    if not isinstance(cue.semantic, GlanceSemantic):
        return False
    if expected_semantic is not None and cue.semantic is not expected_semantic:
        return False
    if cue.semantic not in {
        GlanceSemantic.ATTENTION,
        GlanceSemantic.FRESH_FAILURE,
        GlanceSemantic.FRESH_COMPLETION,
    }:
        return False
    if not valid_opaque_key(cue.event_key, max_bytes=MAX_EPISODE_KEY_BYTES):
        return False
    if type(cue.repetitions) is not int or not 1 <= cue.repetitions <= 2:
        return False
    return _finite_number(cue.duration_seconds) and (
        0.0 < float(cue.duration_seconds) <= MAX_FINITE_CUE_DURATION_SECONDS
    )


def valid_opaque_key(value: object, *, max_bytes: int) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        return False
    return 0 < len(encoded) <= max_bytes


def valid_presentation_time(value: object) -> bool:
    return _finite_number(value) and 0.0 <= float(value) <= MAX_MONOTONIC_SECONDS


def _automatic_glance(inputs: GlanceInputs) -> tuple[GlanceSemantic, FiniteCue | None]:
    if inputs.actionable_episode_key is not None:
        cue = FiniteCue(
            event_key=inputs.actionable_episode_key,
            semantic=GlanceSemantic.ATTENTION,
            repetitions=ATTENTION_ARRIVAL_TAPS,
            duration_seconds=0.24,
        )
        return (
            GlanceSemantic.ATTENTION,
            cue if valid_finite_cue(cue) else None,
        )
    if inputs.fresh_failure is not None:
        return (
            GlanceSemantic.FRESH_FAILURE,
            inputs.fresh_failure
            if valid_finite_cue(
                inputs.fresh_failure,
                expected_semantic=GlanceSemantic.FRESH_FAILURE,
            )
            else None,
        )
    if inputs.fresh_completion is not None:
        return (
            GlanceSemantic.FRESH_COMPLETION,
            inputs.fresh_completion
            if valid_finite_cue(
                inputs.fresh_completion,
                expected_semantic=GlanceSemantic.FRESH_COMPLETION,
            )
            else None,
        )
    if inputs.active:
        return GlanceSemantic.ACTIVE, None
    if inputs.unresolved_failure:
        return GlanceSemantic.UNRESOLVED_FAILURE, None
    if _valid_capacity(inputs.capacity):
        return GlanceSemantic.CAPACITY, None
    return GlanceSemantic.REST, None


def _valid_capacity(value: object) -> bool:
    return (
        isinstance(value, CapacityGlance)
        and valid_opaque_key(value.provider_key, max_bytes=MAX_PROVIDER_KEY_BYTES)
        and _finite_number(value.remaining_fraction)
        and 0.0 <= float(value.remaining_fraction) <= 1.0
    )


def _valid_preferences(value: object) -> bool:
    return isinstance(value, AccessibilityDisplayPreferences) and all(
        type(preference) is bool
        for preference in (
            value.reduce_motion,
            value.reduce_transparency,
            value.increase_contrast,
            value.differentiate_without_color,
        )
    )


def _valid_clock_pair(presentation_time: object, relay_epoch: object) -> bool:
    return (
        valid_presentation_time(presentation_time)
        and valid_presentation_time(relay_epoch)
        and float(relay_epoch) <= float(presentation_time)
    )


def _finite_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(float(value))


def _rest_result() -> ResolvedGlance:
    return ResolvedGlance(
        semantic=GlanceSemantic.REST,
        glyph=SemanticGlyph.REST,
        cue=None,
        override_reason=GlanceOverrideReason.NONE,
        relay_epoch=0.0,
        next_visual_change_at=None,
    )
