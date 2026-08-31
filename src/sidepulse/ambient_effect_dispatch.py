"""Pure compilation of renderer-neutral ambient plans into safe LED DSL.

This boundary selects at most one effect for each shipping light surface. It
does not read a clock, schedule work, retain state, mutate a controller, or
write to a device. Every emitted program passes through the existing
presentation safety compiler and carries a finite refresh/expiry contract.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import Enum
from types import MappingProxyType
from typing import Final

from .ask_heartbeat_sync import AskHeartbeatPlan
from .completion_meniscus import CompletionMeniscusPlan, CompletionMeniscusSurface
from .courtesy_signatures import CourtesySemantic, CourtesySignaturePlan
from .dot_binary_heartbeat import DotBinaryHeartbeatPlan, DotLedInstruction, DotLedMode
from .firefly_completion import FireflyCompletionPlan
from .fleet_arrival_departure import FleetArrivalDepartureCue, FleetEndpointRole
from .glance_light import (
    GlanceDestination,
    GlanceLightPlan,
    GlancePattern,
    GlanceSurfacePlan,
)
from .handoff_baton import HandoffBatonPlan
from .milestone_odometer import MilestoneOdometerPlan
from .presentation_compiler import compile_presentation_program
from .rainstick_idle import RainstickIdlePlan
from .recovery_grace_note import RecoveryGracePlan
from .semantic_effect_router import (
    SEMANTIC_PRIORITY,
    SemanticEffectSelection,
    SemanticEventKind,
)
from .turn_length_ember import TurnLengthEmberPlan

MAX_AMBIENT_OUTPUT_DURATION_MS: Final = 60_000
MAX_AMBIENT_PROGRAM_LINES: Final = 20
MAX_AMBIENT_PROGRAM_BYTES: Final = 512
MAX_AMBIENT_FLASH_HZ: Final = 2.0

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")
_BLACK: Final = "#000000"


class AmbientEffectSurface(str, Enum):
    SCREEN_BAR = "screen_bar"
    SIDEPULSE_PRO = "sidepulse_pro"
    SIDEPULSE_DOT = "sidepulse_dot"


SURFACE_ORDER: Final = (
    AmbientEffectSurface.SCREEN_BAR,
    AmbientEffectSurface.SIDEPULSE_PRO,
    AmbientEffectSurface.SIDEPULSE_DOT,
)


class AmbientEffectFamily(str, Enum):
    SEMANTIC_SELECTION = "semantic_selection"
    GLANCE_LIGHT = "glance_light"
    FIREFLY_COMPLETION = "firefly_completion"
    COMPLETION_MENISCUS = "completion_meniscus"
    HANDOFF_BATON = "handoff_baton"
    RECOVERY_GRACE = "recovery_grace"
    ASK_HEARTBEAT = "ask_heartbeat"
    TURN_LENGTH_EMBER = "turn_length_ember"
    RAINSTICK_IDLE = "rainstick_idle"
    DOT_BINARY_HEARTBEAT = "dot_binary_heartbeat"
    MILESTONE_ODOMETER = "milestone_odometer"
    FLEET_ARRIVAL_DEPARTURE = "fleet_arrival_departure"
    COURTESY_SIGNATURE = "courtesy_signature"


AMBIENT_EFFECT_PRIORITY: Mapping[AmbientEffectFamily, int] = MappingProxyType(
    {
        AmbientEffectFamily.DOT_BINARY_HEARTBEAT: 1_100,
        AmbientEffectFamily.ASK_HEARTBEAT: 1_000,
        AmbientEffectFamily.GLANCE_LIGHT: 900,
        AmbientEffectFamily.SEMANTIC_SELECTION: 800,
        AmbientEffectFamily.HANDOFF_BATON: 780,
        AmbientEffectFamily.FIREFLY_COMPLETION: 740,
        AmbientEffectFamily.COMPLETION_MENISCUS: 730,
        AmbientEffectFamily.MILESTONE_ODOMETER: 720,
        AmbientEffectFamily.COURTESY_SIGNATURE: 660,
        AmbientEffectFamily.RECOVERY_GRACE: 620,
        AmbientEffectFamily.FLEET_ARRIVAL_DEPARTURE: 520,
        AmbientEffectFamily.TURN_LENGTH_EMBER: 320,
        AmbientEffectFamily.RAINSTICK_IDLE: 100,
    }
)


@dataclass(frozen=True, slots=True)
class AmbientSemanticColors:
    """Small explicit palette used by every ambient DSL adaptation."""

    ask: str = "#FF3A00"
    failure: str = "#FF0000"
    notification: str = "#34C759"
    handoff: str = "#A45CFF"
    work: str = "#00E5FF"
    completion: str = "#00FF66"
    recovery: str = "#12E3B0"
    environment: str = "#FFB340"
    idle: str = "#8B93A7"

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not str or _HEX_COLOR.fullmatch(value) is None:
                raise ValueError(f"ambient semantic color {field.name} must be #RRGGBB")
            object.__setattr__(self, field.name, value.upper())

    def for_semantic(self, semantic: SemanticEventKind) -> str:
        if type(semantic) is not SemanticEventKind:
            raise ValueError("ambient color lookup requires a known semantic")
        return getattr(self, semantic.value)


@dataclass(frozen=True, slots=True)
class AmbientEffectSurfaceOutput:
    surface: AmbientEffectSurface
    family: AmbientEffectFamily
    effect_identity: str
    semantic: SemanticEventKind | None
    priority: int
    program: str
    static_fallback_program: str
    accessibility_text: str
    animated: bool
    duration_ms: int
    expires_after_ms: int
    max_flash_hz: float

    def __post_init__(self) -> None:
        if type(self.surface) is not AmbientEffectSurface:
            raise ValueError("ambient output surface must be known")
        if type(self.family) is not AmbientEffectFamily:
            raise ValueError("ambient output family must be known")
        if (
            type(self.effect_identity) is not str
            or not self.effect_identity
            or len(self.effect_identity) > 256
            or not self.effect_identity.isprintable()
        ):
            raise ValueError("ambient effect identity must be bounded printable text")
        if self.semantic is not None and type(self.semantic) is not SemanticEventKind:
            raise ValueError("ambient output semantic must be known")
        if type(self.priority) is not int or self.priority < 0:
            raise ValueError("ambient output priority must be nonnegative")
        if not _bounded_program(self.program) or not _bounded_program(
            self.static_fallback_program
        ):
            raise ValueError("ambient output programs must fit the firmware bounds")
        if (
            type(self.accessibility_text) is not str
            or not self.accessibility_text
            or len(self.accessibility_text) > 1_024
            or not self.accessibility_text.isprintable()
        ):
            raise ValueError("ambient accessibility text must be bounded printable text")
        if type(self.animated) is not bool:
            raise ValueError("ambient animation flag must be a boolean")
        if (
            type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_AMBIENT_OUTPUT_DURATION_MS
            or self.expires_after_ms != self.duration_ms
        ):
            raise ValueError("ambient output duration and expiry must be finite and bounded")
        if (
            type(self.max_flash_hz) not in {int, float}
            or not 0.0 <= float(self.max_flash_hz) <= MAX_AMBIENT_FLASH_HZ
        ):
            raise ValueError("ambient output cadence must not exceed 2 Hz")
        object.__setattr__(self, "max_flash_hz", float(self.max_flash_hz))


@dataclass(frozen=True, slots=True)
class AmbientEffectSuppression:
    surface: AmbientEffectSurface
    family: AmbientEffectFamily
    effect_identity: str
    winning_family: AmbientEffectFamily
    reason: str = "lower_priority"


@dataclass(frozen=True, slots=True)
class AmbientEffectDispatch:
    outputs: tuple[AmbientEffectSurfaceOutput, ...]
    suppressed: tuple[AmbientEffectSuppression, ...] = ()

    def __post_init__(self) -> None:
        if type(self.outputs) is not tuple or not all(
            type(item) is AmbientEffectSurfaceOutput for item in self.outputs
        ):
            raise ValueError("ambient outputs must be an immutable typed tuple")
        surfaces = tuple(item.surface for item in self.outputs)
        if surfaces != tuple(surface for surface in SURFACE_ORDER if surface in surfaces):
            raise ValueError("ambient outputs must use stable surface order")
        if len(set(surfaces)) != len(surfaces):
            raise ValueError("ambient dispatch allows one output per surface")
        if type(self.suppressed) is not tuple or not all(
            type(item) is AmbientEffectSuppression for item in self.suppressed
        ):
            raise ValueError("ambient suppressions must be an immutable typed tuple")

    def for_surface(
        self,
        surface: AmbientEffectSurface,
    ) -> AmbientEffectSurfaceOutput | None:
        if type(surface) is not AmbientEffectSurface:
            raise ValueError("ambient surface lookup requires a known surface")
        return next((item for item in self.outputs if item.surface is surface), None)


def _bounded_program(program: object) -> bool:
    return (
        type(program) is str
        and bool(program)
        and len(program.splitlines()) <= MAX_AMBIENT_PROGRAM_LINES
        and len(program.encode("utf-8")) <= MAX_AMBIENT_PROGRAM_BYTES
    )


def _led_count(surface: AmbientEffectSurface) -> int:
    return 2 if surface is AmbientEffectSurface.SIDEPULSE_DOT else 8


def _duration(value: int | float) -> int:
    return max(1, min(MAX_AMBIENT_OUTPUT_DURATION_MS, int(round(float(value)))))


def _scale_color(color: str, intensity: float) -> str:
    level = max(0.0, min(1.0, float(intensity)))
    channels = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
    return "#" + "".join(f"{round(channel * level):02X}" for channel in channels)


def _color_list(
    led_count: int,
    indices: tuple[int, ...],
    color: str,
) -> str:
    selected = {max(0, min(led_count - 1, index)) for index in indices}
    return " ".join(color if index in selected else _BLACK for index in range(led_count))


def _static_program(led_count: int, indices: tuple[int, ...], color: str) -> str:
    if not indices or color == _BLACK:
        return "off"
    if len(set(indices)) >= led_count:
        return color
    return _color_list(led_count, indices, color)


def _compiled(program: str, fallback: str, *, led_count: int) -> tuple[str, str]:
    fallback_result = compile_presentation_program(
        fallback,
        led_count=led_count,
        fallback="off",
    )
    safe_fallback = fallback_result.program if fallback_result.accepted else "off"
    result = compile_presentation_program(
        program,
        led_count=led_count,
        fallback=safe_fallback,
    )
    safe_program = result.program if result.accepted else safe_fallback
    if not _bounded_program(safe_program) or not _bounded_program(safe_fallback):
        return safe_fallback, safe_fallback
    return safe_program, safe_fallback


def _output(
    *,
    surface: AmbientEffectSurface,
    family: AmbientEffectFamily,
    effect_identity: str,
    semantic: SemanticEventKind | None,
    priority: int,
    program: str,
    fallback: str,
    accessibility_text: str,
    animated: bool,
    duration_ms: int,
    max_flash_hz: float,
) -> AmbientEffectSurfaceOutput:
    safe_program, safe_fallback = _compiled(
        program,
        fallback,
        led_count=_led_count(surface),
    )
    bounded_duration = _duration(duration_ms)
    return AmbientEffectSurfaceOutput(
        surface=surface,
        family=family,
        effect_identity=effect_identity,
        semantic=semantic,
        priority=priority,
        program=safe_program,
        static_fallback_program=safe_fallback,
        accessibility_text=accessibility_text,
        animated=animated and safe_program != safe_fallback,
        duration_ms=bounded_duration,
        expires_after_ms=bounded_duration,
        max_flash_hz=min(MAX_AMBIENT_FLASH_HZ, max(0.0, max_flash_hz)),
    )


def _joined_text(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part.strip())[:1_024]


def _semantic_candidates(
    selection: SemanticEffectSelection,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    if selection.winner is None:
        return ()
    semantic = selection.winner.semantic
    color = colors.for_semantic(semantic)
    identity = selection.registry_effect_identifier
    if identity is None:
        return ()
    effective = selection.reduce_motion_substitution or identity
    animated = effective != "none"
    if effective in {"none", "steady"}:
        dynamic = color
        duration = 30_000
        hz = 0.0
        animated = False
    elif effective == "alert":
        dynamic = f"{color} 250ms none\noff 750ms none\nrepeat 8"
        duration = 8_000
        hz = 1.0
    elif effective == "notification":
        dynamic = f"{color} 250ms none\noff 750ms none\nrepeat 4"
        duration = 4_000
        hz = 1.0
    else:
        dynamic = f"{_scale_color(color, 0.25)} 1s cosine\n{color} 1s cosine\nrepeat 5"
        duration = 10_000
        hz = 0.5
    fallback = color
    allowed = set(selection.destination_surfaces)
    outputs = []
    for surface in SURFACE_ORDER:
        if surface.value not in allowed:
            continue
        outputs.append(
            _output(
                surface=surface,
                family=AmbientEffectFamily.SEMANTIC_SELECTION,
                effect_identity=identity,
                semantic=semantic,
                priority=(
                    AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.SEMANTIC_SELECTION]
                    + SEMANTIC_PRIORITY[semantic]
                ),
                program=fallback if not animated else dynamic,
                fallback=fallback,
                accessibility_text=f"{semantic.value.replace('_', ' ').title()} state. {identity} effect.",
                animated=animated,
                duration_ms=duration,
                max_flash_hz=hz,
            )
        )
    return tuple(outputs)


_GLANCE_SURFACES: Final = {
    GlanceDestination.DOT: AmbientEffectSurface.SIDEPULSE_DOT,
    GlanceDestination.PRO_ENDPOINT: AmbientEffectSurface.SIDEPULSE_PRO,
    GlanceDestination.SCREEN_BAR_ORB: AmbientEffectSurface.SCREEN_BAR,
}


def _glance_semantic(plan: GlanceSurfacePlan) -> SemanticEventKind:
    return {
        GlancePattern.DOUBLE_SOFT_PULSE: SemanticEventKind.ASK,
        GlancePattern.TRIPLE_FAILURE: SemanticEventKind.FAILURE,
        GlancePattern.SHORT_WINK: SemanticEventKind.COMPLETION,
    }.get(plan.pattern, SemanticEventKind.NOTIFICATION)


def _glance_program(plan: GlanceSurfacePlan, color: str) -> tuple[str, bool, int, float]:
    fallback = _scale_color(color, plan.intensity)
    if plan.pulse_count == 0 or plan.pattern in {
        GlancePattern.STEADY_DIM,
        GlancePattern.STATIC_MARKER,
    }:
        return fallback, False, 30_000, 0.0
    on_ms = max(250, round(plan.pulse_on_seconds * 1_000))
    gap_ms = max(250, round(plan.pulse_gap_seconds * 1_000))
    lines = []
    for _index in range(plan.pulse_count):
        lines.extend((f"{fallback} {on_ms}ms none", f"off {gap_ms}ms none"))
    phrase_ms = plan.pulse_count * (on_ms + gap_ms)
    if plan.repeat_interval_seconds is not None:
        repeat_ms = round(plan.repeat_interval_seconds * 1_000)
        rest_ms = max(0, repeat_ms - phrase_ms)
        if rest_ms:
            lines.append(f"off {rest_ms}ms none")
        repeats = max(1, min(2, MAX_AMBIENT_OUTPUT_DURATION_MS // max(1, repeat_ms)))
        lines.append(f"repeat {repeats}")
        phrase_ms = repeat_ms * repeats
    return "\n".join(lines), True, phrase_ms, min(2.0, 1_000.0 / (on_ms + gap_ms))


def _glance_candidates(
    plan: GlanceLightPlan,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    outputs = []
    for surface_plan in plan.surface_plans:
        surface = _GLANCE_SURFACES.get(surface_plan.destination)
        if surface is None or not surface_plan.active:
            continue
        semantic = _glance_semantic(surface_plan)
        color = colors.for_semantic(semantic)
        program, animated, duration, hz = _glance_program(surface_plan, color)
        fallback = _scale_color(color, surface_plan.intensity)
        outputs.append(
            _output(
                surface=surface,
                family=AmbientEffectFamily.GLANCE_LIGHT,
                effect_identity=surface_plan.notification_id or "glance_light",
                semantic=semantic,
                priority=(
                    AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.GLANCE_LIGHT]
                    + SEMANTIC_PRIORITY[semantic]
                ),
                program=program,
                fallback=fallback,
                accessibility_text=_joined_text(
                    plan.count_summary,
                    f"{surface_plan.pattern.value.replace('_', ' ')} glance light.",
                ),
                animated=animated,
                duration_ms=duration,
                max_flash_hz=hz,
            )
        )
    return tuple(outputs)


def _firefly_program(plan: FireflyCompletionPlan, color: str, led_count: int) -> str:
    lines = ["off"]
    previous: int | None = None
    frames = plan.keyframes
    for index, frame in enumerate(frames[:-1]):
        current = max(0, min(led_count - 1, round(frame.led_position)))
        duration = max(1, round((frames[index + 1].elapsed_seconds - frame.elapsed_seconds) * 1_000))
        assignments = []
        if previous is not None and previous != current:
            assignments.append(f"{previous}:{_BLACK}")
        assignments.append(f"{current}:{_scale_color(color, frame.intensity)}")
        lines.append(f"{' '.join(assignments)} {duration}ms none")
        previous = current
    lines.append("off")
    return "\n".join(lines)


def _firefly_candidates(
    plan: FireflyCompletionPlan,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    color = colors.completion
    text = _joined_text(
        plan.accessibility.label,
        plan.accessibility.value,
        plan.accessibility.help,
    )
    static = plan.mode.value == "static_highlight"
    outputs = []
    for surface in (AmbientEffectSurface.SCREEN_BAR, AmbientEffectSurface.SIDEPULSE_PRO):
        led_count = _led_count(surface)
        index = max(0, min(led_count - 1, round(plan.stable_fleet_band.led_center)))
        fallback = _static_program(led_count, (index,), _scale_color(color, 0.62))
        outputs.append(
            _output(
                surface=surface,
                family=AmbientEffectFamily.FIREFLY_COMPLETION,
                effect_identity="firefly_completion",
                semantic=SemanticEventKind.COMPLETION,
                priority=AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.FIREFLY_COMPLETION],
                program=fallback if static else _firefly_program(plan, color, led_count),
                fallback=fallback,
                accessibility_text=text,
                animated=not static,
                duration_ms=plan.duration_seconds * 1_000,
                max_flash_hz=0.0,
            )
        )
    return tuple(outputs)


def _meniscus_program(plan: CompletionMeniscusPlan, color: str, led_count: int) -> str:
    lines = []
    frames = plan.frames
    for index, frame in enumerate(frames[:-1]):
        duration = max(1, frames[index + 1].elapsed_ms - frame.elapsed_ms)
        if frame.intensity <= 0.0 or frame.opacity <= 0.0:
            lines.append(f"off {duration}ms none")
            continue
        fraction = min(1.0, frame.radius / max(1.0, plan.geometry.maximum_radius))
        count = max(1, round(fraction * led_count))
        start = max(0, (led_count - count) // 2)
        indices = tuple(range(start, min(led_count, start + count)))
        frame_color = _scale_color(color, frame.intensity * frame.opacity)
        lines.append(f"{_color_list(led_count, indices, frame_color)} {duration}ms none")
    lines.append("off")
    return "\n".join(lines)


def _meniscus_candidates(
    plan: CompletionMeniscusPlan,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    if plan.geometry.surface is not CompletionMeniscusSurface.SCREEN_BAR:
        return ()
    surface = AmbientEffectSurface.SCREEN_BAR
    led_count = _led_count(surface)
    centers = ((led_count - 1) // 2, led_count // 2)
    fallback = _static_program(led_count, centers, _scale_color(colors.completion, 0.65))
    static = plan.mode.value == "static_center_highlight"
    return (
        _output(
            surface=surface,
            family=AmbientEffectFamily.COMPLETION_MENISCUS,
            effect_identity="completion_meniscus",
            semantic=SemanticEventKind.COMPLETION,
            priority=AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.COMPLETION_MENISCUS],
            program=fallback if static else _meniscus_program(plan, colors.completion, led_count),
            fallback=fallback,
            accessibility_text=_joined_text(
                plan.accessibility.label,
                plan.accessibility.value,
                plan.accessibility.help,
            ),
            animated=not static,
            duration_ms=plan.duration_ms,
            max_flash_hz=0.0,
        ),
    )


def _travel_program(color: str, duration_ms: int, led_count: int) -> str:
    positions = tuple(dict.fromkeys((0, led_count // 2, led_count - 1)))
    phase = max(250, duration_ms // len(positions))
    lines = ["off"]
    previous: int | None = None
    for position in positions:
        assignments = []
        if previous is not None:
            assignments.append(f"{previous}:{_BLACK}")
        assignments.append(f"{position}:{color}")
        lines.append(f"{' '.join(assignments)} {phase}ms none")
        previous = position
    lines.append("off")
    return "\n".join(lines)


def _handoff_candidates(
    plan: HandoffBatonPlan,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    text = _joined_text(
        plan.accessibility.label,
        plan.accessibility.value,
        plan.accessibility.announcement,
        plan.accessibility.motion_description,
    )
    static = not plan.motion.spatial_travel
    outputs = []
    for surface in (AmbientEffectSurface.SCREEN_BAR, AmbientEffectSurface.SIDEPULSE_PRO):
        led_count = _led_count(surface)
        fallback = _static_program(led_count, (led_count - 1,), colors.handoff)
        outputs.append(
            _output(
                surface=surface,
                family=AmbientEffectFamily.HANDOFF_BATON,
                effect_identity=plan.presentation.effect_identifier,
                semantic=SemanticEventKind.HANDOFF,
                priority=AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.HANDOFF_BATON],
                program=(
                    fallback
                    if static
                    else _travel_program(colors.handoff, plan.motion.duration_ms, led_count)
                ),
                fallback=fallback,
                accessibility_text=text,
                animated=not static,
                duration_ms=plan.motion.duration_ms,
                max_flash_hz=0.0,
            )
        )
    return tuple(outputs)


def _recovery_candidates(
    plan: RecoveryGracePlan,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    if not plan.emits:
        return ()
    static = not plan.animated
    outputs = []
    for surface in (AmbientEffectSurface.SCREEN_BAR, AmbientEffectSurface.SIDEPULSE_PRO):
        led_count = _led_count(surface)
        fallback = _static_program(led_count, tuple(range(led_count)), colors.recovery)
        outputs.append(
            _output(
                surface=surface,
                family=AmbientEffectFamily.RECOVERY_GRACE,
                effect_identity="recovery_grace",
                semantic=SemanticEventKind.RECOVERY,
                priority=AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.RECOVERY_GRACE],
                program=(
                    fallback
                    if static
                    else _travel_program(
                        colors.recovery,
                        round(plan.duration_seconds * 1_000),
                        led_count,
                    )
                ),
                fallback=fallback,
                accessibility_text=plan.accessibility_text,
                animated=not static,
                duration_ms=plan.duration_seconds * 1_000,
                max_flash_hz=0.0,
            )
        )
    return tuple(outputs)


def _ask_candidates(
    plan: AskHeartbeatPlan,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    if plan.request_count == 0:
        return ()
    fallback = colors.ask
    cadence = plan.cadence
    static = cadence.cycle_seconds is None
    if static:
        program = fallback
        duration = 10_000
        hz = 0.0
    else:
        on_ms = max(1, round(cadence.pulse_on_seconds * 1_000))
        gap_ms = max(1, round(cadence.inter_pulse_seconds * 1_000))
        rest_ms = max(1, round(cadence.rest_seconds * 1_000))
        program = "\n".join(
            (
                f"{fallback} {on_ms}ms none",
                f"off {gap_ms}ms none",
                f"{fallback} {on_ms}ms none",
                f"off {rest_ms}ms none",
                "repeat 8",
            )
        )
        duration = round(cadence.cycle_seconds * 8_000)
        hz = cadence.pulse_count / cadence.cycle_seconds
    text = _joined_text(
        plan.accessibility_label,
        plan.accessibility_value,
        plan.accessibility_help,
    )
    return tuple(
        _output(
            surface=surface,
            family=AmbientEffectFamily.ASK_HEARTBEAT,
            effect_identity=cadence.signature,
            semantic=SemanticEventKind.ASK,
            priority=AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.ASK_HEARTBEAT],
            program=program,
            fallback=fallback,
            accessibility_text=text,
            animated=not static,
            duration_ms=duration,
            max_flash_hz=hz,
        )
        for surface in SURFACE_ORDER
    )


def _ember_candidates(
    plan: TurnLengthEmberPlan,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    if not plan.visible:
        return ()
    peak = _scale_color(colors.work, plan.luminance)
    floor = _scale_color(peak, 0.35)
    animated = plan.animated and plan.breathe_period_seconds is not None
    if animated:
        half_period = max(250, round(plan.breathe_period_seconds * 500))
        cycles = max(1, min(8, MAX_AMBIENT_OUTPUT_DURATION_MS // (half_period * 2)))
        program = f"{floor} {half_period}ms cosine\n{peak} {half_period}ms cosine\nrepeat {cycles}"
        duration = half_period * 2 * cycles
        hz = 1_000.0 / (half_period * 2)
    else:
        program = peak
        duration = 30_000
        hz = 0.0
    return tuple(
        _output(
            surface=surface,
            family=AmbientEffectFamily.TURN_LENGTH_EMBER,
            effect_identity="turn_length_ember",
            semantic=SemanticEventKind.WORK,
            priority=AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.TURN_LENGTH_EMBER],
            program=program,
            fallback=peak,
            accessibility_text=plan.accessibility_text,
            animated=animated,
            duration_ms=duration,
            max_flash_hz=hz,
        )
        for surface in (AmbientEffectSurface.SCREEN_BAR, AmbientEffectSurface.SIDEPULSE_PRO)
    )


def _rainstick_candidates(
    plan: RainstickIdlePlan,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    if not plan.visible or plan.geometry is None or plan.cadence is None:
        return ()
    color = _scale_color(colors.idle, plan.geometry.relative_luminance)
    outputs = []
    for surface in (AmbientEffectSurface.SCREEN_BAR, AmbientEffectSurface.SIDEPULSE_PRO):
        led_count = _led_count(surface)
        static_index = plan.geometry.static_index if plan.geometry.static_index is not None else 0
        static_index = max(0, min(led_count - 1, static_index))
        fallback = _static_program(led_count, (static_index,), color)
        if plan.animated and plan.cadence.step_interval_seconds is not None:
            interval = round(plan.cadence.step_interval_seconds * 1_000)
            program = "\n".join(
                (
                    f"0:{color} {interval}ms none",
                    f"0:{_BLACK} 1:{color} {interval}ms none",
                )
            )
            duration = interval * 2
        else:
            program = fallback
            duration = 30_000
        outputs.append(
            _output(
                surface=surface,
                family=AmbientEffectFamily.RAINSTICK_IDLE,
                effect_identity="rainstick_idle",
                semantic=SemanticEventKind.IDLE,
                priority=AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.RAINSTICK_IDLE],
                program=program,
                fallback=fallback,
                accessibility_text=plan.accessibility_text,
                animated=plan.animated,
                duration_ms=duration,
                max_flash_hz=plan.cadence.step_frequency_hz,
            )
        )
    return tuple(outputs)


def _dot_instruction_color(
    instruction: DotLedInstruction,
    semantic: SemanticEventKind | None,
    colors: AmbientSemanticColors,
) -> str:
    base = colors.for_semantic(semantic) if semantic is not None else colors.idle
    return _scale_color(base, instruction.intensity)


def _dot_candidates(
    plan: DotBinaryHeartbeatPlan,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    primary_color = _dot_instruction_color(plan.primary, plan.selected_semantic, colors)
    secondary_color = _scale_color(colors.notification, plan.secondary.intensity)
    fallback = _static_program(
        2,
        tuple(
            index
            for index, color in enumerate((primary_color, secondary_color))
            if color != _BLACK
        ),
        primary_color,
    )
    if primary_color != secondary_color and secondary_color != _BLACK:
        fallback = f"0:{primary_color} 1:{secondary_color}"
    elif primary_color == _BLACK and secondary_color != _BLACK:
        fallback = f"0:{_BLACK} 1:{secondary_color}"

    cadence = plan.primary.cadence
    animated = plan.primary.mode is DotLedMode.PULSE and cadence is not None
    if animated and cadence is not None:
        initial = f"0:{primary_color} 1:{secondary_color} {cadence.on_ms}ms none"
        lines = [initial, f"0:{_BLACK} {cadence.off_ms}ms none"]
        for _index in range(1, cadence.pulses):
            lines.extend(
                (
                    f"0:{primary_color} {cadence.on_ms}ms none",
                    f"0:{_BLACK} {cadence.off_ms}ms none",
                )
            )
        if cadence.rest_ms:
            lines.append(f"0:{_BLACK} {cadence.rest_ms}ms none")
        repeats = max(1, min(8, MAX_AMBIENT_OUTPUT_DURATION_MS // cadence.phrase_duration_ms))
        lines.append(f"repeat {repeats}")
        program = "\n".join(lines)
        duration = cadence.phrase_duration_ms * repeats
        hz = cadence.peak_flash_hz
    else:
        program = fallback
        duration = 30_000
        hz = 0.0
    semantic_text = plan.selected_semantic.value if plan.selected_semantic is not None else "none"
    text = _joined_text(
        f"Dot primary state: {semantic_text}, {plan.primary.non_color_cue}.",
        f"Dot secondary state: {plan.secondary.non_color_cue}.",
    )
    return (
        _output(
            surface=AmbientEffectSurface.SIDEPULSE_DOT,
            family=AmbientEffectFamily.DOT_BINARY_HEARTBEAT,
            effect_identity="dot_binary_heartbeat",
            semantic=plan.selected_semantic,
            priority=AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.DOT_BINARY_HEARTBEAT],
            program=program,
            fallback=fallback,
            accessibility_text=text,
            animated=animated,
            duration_ms=duration,
            max_flash_hz=hz,
        ),
    )


def _milestone_candidates(
    plan: MilestoneOdometerPlan,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    cue = plan.cue
    if cue is None:
        return ()
    fallback = colors.completion
    if cue.animated:
        lines = []
        step_count = len(cue.steps)
        for index, step in enumerate(cue.steps, start=1):
            color = _scale_color(colors.completion, 0.35 + 0.65 * index / step_count)
            lines.append(f"{color} {step.duration_ms}ms none")
        program = "\n".join(lines)
    else:
        program = fallback
    text = _joined_text(
        cue.accessibility.label,
        cue.accessibility.value,
        cue.accessibility.announcement,
        cue.accessibility.help,
    )
    return tuple(
        _output(
            surface=surface,
            family=AmbientEffectFamily.MILESTONE_ODOMETER,
            effect_identity="milestone_odometer",
            semantic=SemanticEventKind.COMPLETION,
            priority=AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.MILESTONE_ODOMETER],
            program=program,
            fallback=fallback,
            accessibility_text=text,
            animated=cue.animated,
            duration_ms=cue.duration_ms,
            max_flash_hz=0.0,
        )
        for surface in (AmbientEffectSurface.SCREEN_BAR, AmbientEffectSurface.SIDEPULSE_PRO)
    )


def _fleet_candidates(
    cue: FleetArrivalDepartureCue,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    if not cue.emits:
        return ()
    text = _joined_text(
        cue.accessibility.label,
        cue.accessibility.value,
        cue.accessibility.announcement,
    )
    outputs = []
    for surface in (AmbientEffectSurface.SCREEN_BAR, AmbientEffectSurface.SIDEPULSE_PRO):
        led_count = _led_count(surface)
        endpoint = 0 if cue.endpoint_role is FleetEndpointRole.ARRIVAL_ENDPOINT else led_count - 1
        fallback = _static_program(led_count, (endpoint,), colors.environment)
        if cue.animated:
            on_ms = max(250, cue.duration_ms // 2)
            program = f"{endpoint}:{colors.environment} {on_ms}ms none\n{endpoint}:{_BLACK} {on_ms}ms none"
        else:
            program = fallback
        outputs.append(
            _output(
                surface=surface,
                family=AmbientEffectFamily.FLEET_ARRIVAL_DEPARTURE,
                effect_identity=f"fleet_{cue.identity.transition.value}",
                semantic=SemanticEventKind.ENVIRONMENT,
                priority=AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.FLEET_ARRIVAL_DEPARTURE],
                program=program,
                fallback=fallback,
                accessibility_text=text,
                animated=cue.animated,
                duration_ms=cue.duration_ms,
                max_flash_hz=0.0,
            )
        )
    return tuple(outputs)


_COURTESY_SEMANTICS: Final = {
    CourtesySemantic.FAILURE: SemanticEventKind.FAILURE,
    CourtesySemantic.HANDOFF: SemanticEventKind.HANDOFF,
    CourtesySemantic.COMPLETION: SemanticEventKind.COMPLETION,
    CourtesySemantic.RECOVERY: SemanticEventKind.RECOVERY,
    CourtesySemantic.GENERIC_NOTIFICATION: SemanticEventKind.NOTIFICATION,
}


def _courtesy_candidates(
    plan: CourtesySignaturePlan,
    colors: AmbientSemanticColors,
) -> tuple[AmbientEffectSurfaceOutput, ...]:
    semantic = _COURTESY_SEMANTICS.get(plan.semantic, SemanticEventKind.ENVIRONMENT)
    color = colors.for_semantic(semantic)
    outputs = []
    for surface in SURFACE_ORDER:
        led_count = _led_count(surface)

        def indices_for(slots: tuple[int, ...]) -> tuple[int, ...]:
            return tuple(round(slot * (led_count - 1) / 4) for slot in slots)

        fallback = _static_program(led_count, indices_for(plan.static_slots), color)
        if plan.has_motion and plan.cadence is not None:
            lines = []
            for index, frame in enumerate(plan.frames):
                pulse = plan.cadence.pulses[index % len(plan.cadence.pulses)]
                frame_program = _static_program(led_count, indices_for(frame), color)
                lines.extend(
                    (
                        f"{frame_program} {pulse.active_ms}ms none",
                        f"off {pulse.rest_ms}ms none",
                    )
                )
            program = "\n".join(lines)
            duration = sum(
                plan.cadence.pulses[index % len(plan.cadence.pulses)].cycle_ms
                for index in range(len(plan.frames))
            )
            hz = plan.cadence.peak_hz
        else:
            program = fallback
            duration = 700
            hz = 0.0
        outputs.append(
            _output(
                surface=surface,
                family=AmbientEffectFamily.COURTESY_SIGNATURE,
                effect_identity=plan.identifier,
                semantic=semantic,
                priority=(
                    AMBIENT_EFFECT_PRIORITY[AmbientEffectFamily.COURTESY_SIGNATURE]
                    + SEMANTIC_PRIORITY[semantic]
                ),
                program=program,
                fallback=fallback,
                accessibility_text=_joined_text(
                    plan.accessibility_label,
                    plan.spatial_description,
                ),
                animated=plan.has_motion,
                duration_ms=duration,
                max_flash_hz=hz,
            )
        )
    return tuple(outputs)


def _typed(value: object, expected: type, name: str) -> None:
    if value is not None and type(value) is not expected:
        raise TypeError(f"{name} must be {expected.__name__} or None")


def compile_ambient_effect_dispatch(
    *,
    semantic_selection: SemanticEffectSelection | None = None,
    glance_light: GlanceLightPlan | None = None,
    firefly_completion: FireflyCompletionPlan | None = None,
    completion_meniscus: CompletionMeniscusPlan | None = None,
    handoff_baton: HandoffBatonPlan | None = None,
    recovery_grace: RecoveryGracePlan | None = None,
    ask_heartbeat: AskHeartbeatPlan | None = None,
    turn_length_ember: TurnLengthEmberPlan | None = None,
    rainstick_idle: RainstickIdlePlan | None = None,
    dot_binary_heartbeat: DotBinaryHeartbeatPlan | None = None,
    milestone_odometer: MilestoneOdometerPlan | None = None,
    fleet_arrival_departure: FleetArrivalDepartureCue | None = None,
    courtesy_signature: CourtesySignaturePlan | None = None,
    semantic_colors: AmbientSemanticColors = AmbientSemanticColors(),
) -> AmbientEffectDispatch:
    """Compile optional immutable plans into one safe program per surface."""

    inputs = (
        (semantic_selection, SemanticEffectSelection, "semantic_selection"),
        (glance_light, GlanceLightPlan, "glance_light"),
        (firefly_completion, FireflyCompletionPlan, "firefly_completion"),
        (completion_meniscus, CompletionMeniscusPlan, "completion_meniscus"),
        (handoff_baton, HandoffBatonPlan, "handoff_baton"),
        (recovery_grace, RecoveryGracePlan, "recovery_grace"),
        (ask_heartbeat, AskHeartbeatPlan, "ask_heartbeat"),
        (turn_length_ember, TurnLengthEmberPlan, "turn_length_ember"),
        (rainstick_idle, RainstickIdlePlan, "rainstick_idle"),
        (dot_binary_heartbeat, DotBinaryHeartbeatPlan, "dot_binary_heartbeat"),
        (milestone_odometer, MilestoneOdometerPlan, "milestone_odometer"),
        (fleet_arrival_departure, FleetArrivalDepartureCue, "fleet_arrival_departure"),
        (courtesy_signature, CourtesySignaturePlan, "courtesy_signature"),
    )
    for value, expected, name in inputs:
        _typed(value, expected, name)
    if type(semantic_colors) is not AmbientSemanticColors:
        raise TypeError("semantic_colors must be AmbientSemanticColors")

    generated: list[AmbientEffectSurfaceOutput] = []
    if semantic_selection is not None:
        generated.extend(_semantic_candidates(semantic_selection, semantic_colors))
    if glance_light is not None:
        generated.extend(_glance_candidates(glance_light, semantic_colors))
    if firefly_completion is not None:
        generated.extend(_firefly_candidates(firefly_completion, semantic_colors))
    if completion_meniscus is not None:
        generated.extend(_meniscus_candidates(completion_meniscus, semantic_colors))
    if handoff_baton is not None:
        generated.extend(_handoff_candidates(handoff_baton, semantic_colors))
    if recovery_grace is not None:
        generated.extend(_recovery_candidates(recovery_grace, semantic_colors))
    if ask_heartbeat is not None:
        generated.extend(_ask_candidates(ask_heartbeat, semantic_colors))
    if turn_length_ember is not None:
        generated.extend(_ember_candidates(turn_length_ember, semantic_colors))
    if rainstick_idle is not None:
        generated.extend(_rainstick_candidates(rainstick_idle, semantic_colors))
    if dot_binary_heartbeat is not None:
        generated.extend(_dot_candidates(dot_binary_heartbeat, semantic_colors))
    if milestone_odometer is not None:
        generated.extend(_milestone_candidates(milestone_odometer, semantic_colors))
    if fleet_arrival_departure is not None:
        generated.extend(_fleet_candidates(fleet_arrival_departure, semantic_colors))
    if courtesy_signature is not None:
        generated.extend(_courtesy_candidates(courtesy_signature, semantic_colors))

    outputs = []
    suppressions = []
    for surface in SURFACE_ORDER:
        contenders = [item for item in generated if item.surface is surface]
        if not contenders:
            continue
        winner = max(contenders, key=lambda item: (item.priority, item.family.value))
        outputs.append(winner)
        suppressions.extend(
            AmbientEffectSuppression(
                surface=surface,
                family=item.family,
                effect_identity=item.effect_identity,
                winning_family=winner.family,
            )
            for item in contenders
            if item is not winner
        )
    return AmbientEffectDispatch(tuple(outputs), tuple(suppressions))


__all__ = [
    "AMBIENT_EFFECT_PRIORITY",
    "MAX_AMBIENT_FLASH_HZ",
    "MAX_AMBIENT_OUTPUT_DURATION_MS",
    "MAX_AMBIENT_PROGRAM_BYTES",
    "MAX_AMBIENT_PROGRAM_LINES",
    "AmbientEffectDispatch",
    "AmbientEffectFamily",
    "AmbientEffectSuppression",
    "AmbientEffectSurface",
    "AmbientEffectSurfaceOutput",
    "AmbientSemanticColors",
    "compile_ambient_effect_dispatch",
]
