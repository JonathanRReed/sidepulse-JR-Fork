"""Bounded, AppKit-free facts for explaining the current light."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import Enum

from .dnd_policy import DndMode, DndSource

MAX_SOURCE_AGE_SECONDS = 30.0 * 24.0 * 60.0 * 60.0
MAX_SUPPRESSION_COUNT = 99
MAX_RENDERER_SAMPLE_COUNT = 1_000_000
MAX_RENDERER_TIMING_MS = 60_000.0
MAX_DND_RETURN_EPOCH = 4_102_444_800.0


class ValueAvailability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class LightSemantic(str, Enum):
    ATTENTION = "attention"
    FRESH_FAILURE = "fresh_failure"
    FRESH_COMPLETION = "fresh_completion"
    ACTIVE = "active"
    UNRESOLVED_FAILURE = "unresolved_failure"
    CAPACITY = "capacity"
    REST = "rest"
    UNAVAILABLE = "unavailable"


class WinningPriority(int, Enum):
    UNAVAILABLE = 0
    P1 = 1
    P2 = 2
    P3 = 3
    P4 = 4
    P5 = 5
    P6 = 6
    P7 = 7


class SceneAvailability(str, Enum):
    UNAVAILABLE = "unavailable"


class GlobalSurfaceRole(str, Enum):
    NONE = "none"
    SCREEN_BAR = "screen_bar"
    PHYSICAL = "physical"
    SCREEN_BAR_AND_PHYSICAL = "screen_bar_and_physical"
    UNAVAILABLE = "unavailable"


class FocusObservation(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNAVAILABLE = "unavailable"


class FocusPolicy(str, Enum):
    ALLOW = "allow"
    SUPPRESS = "suppress"
    UNAVAILABLE = "unavailable"


class FocusOutcome(str, Enum):
    ALLOWED = "allowed"
    SUPPRESSED = "suppressed"
    UNAVAILABLE = "unavailable"


class ReduceMotionDecision(str, Enum):
    UNAVAILABLE = "unavailable"
    NO_MOTION_REQUESTED = "no_motion_requested"
    MOTION_UNCHANGED = "motion_unchanged"
    STATIC_SUBSTITUTED = "static_substituted"


class OutputTimingSource(str, Enum):
    SCREEN_BAR_RENDERER = "screen_bar_renderer"
    PHYSICAL_HARDWARE_WRITE = "physical_hardware_write"
    UNAVAILABLE = "unavailable"


def _require_enum(value: object, expected_type: type[Enum], field_name: str) -> None:
    if type(value) is not expected_type:
        raise TypeError(f"{field_name} must be {expected_type.__name__}")


def _bounded_number(value: object, *, field_name: str, maximum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    if not math.isfinite(value) or value < 0.0 or value > maximum:
        raise ValueError(f"{field_name} is outside the bounded range")


@dataclass(frozen=True, slots=True)
class SourceAge:
    availability: ValueAvailability
    seconds: float = 0.0

    def __post_init__(self) -> None:
        _require_enum(self.availability, ValueAvailability, "availability")
        _bounded_number(self.seconds, field_name="seconds", maximum=MAX_SOURCE_AGE_SECONDS)
        if self.availability is ValueAvailability.UNAVAILABLE and self.seconds != 0.0:
            raise ValueError("unavailable source age cannot contain a duration")

    @classmethod
    def available(cls, seconds: float) -> SourceAge:
        return cls(ValueAvailability.AVAILABLE, seconds)

    @classmethod
    def unavailable(cls) -> SourceAge:
        return cls(ValueAvailability.UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class SuppressionCounts:
    attention: int = 0
    fresh_failure: int = 0
    fresh_completion: int = 0
    active: int = 0
    unresolved_failure: int = 0
    capacity: int = 0

    def __post_init__(self) -> None:
        for field in fields(self):
            count = getattr(self, field.name)
            if type(count) is not int:
                raise TypeError(f"{field.name} must be an integer count")
            if count < 0 or count > MAX_SUPPRESSION_COUNT:
                raise ValueError(f"{field.name} is outside the bounded range")

    @property
    def total(self) -> int:
        return sum(getattr(self, field.name) for field in fields(self))


@dataclass(frozen=True, slots=True)
class FocusDNDDecision:
    observation: FocusObservation
    policy: FocusPolicy
    outcome: FocusOutcome
    dnd_modes: tuple[DndMode, ...] = ()
    dnd_sources: tuple[DndSource, ...] = ()
    dnd_return_epoch: float | None = None

    def __post_init__(self) -> None:
        _require_enum(self.observation, FocusObservation, "observation")
        _require_enum(self.policy, FocusPolicy, "policy")
        _require_enum(self.outcome, FocusOutcome, "outcome")
        if type(self.dnd_modes) is not tuple or not all(
            type(mode) is DndMode for mode in self.dnd_modes
        ):
            raise TypeError("DND modes must be immutable typed values")
        if type(self.dnd_sources) is not tuple or not all(
            type(source) is DndSource for source in self.dnd_sources
        ):
            raise TypeError("DND sources must be immutable typed values")
        if len(self.dnd_modes) > 4 or len(set(self.dnd_modes)) != len(self.dnd_modes):
            raise ValueError("DND modes must remain bounded and unique")
        if len(self.dnd_sources) > 4 or len(set(self.dnd_sources)) != len(
            self.dnd_sources
        ):
            raise ValueError("DND sources must remain bounded and unique")
        if self.dnd_return_epoch is not None:
            _bounded_number(
                self.dnd_return_epoch,
                field_name="dnd_return_epoch",
                maximum=MAX_DND_RETURN_EPOCH,
            )

        unavailable = self.observation is FocusObservation.UNAVAILABLE or self.policy is FocusPolicy.UNAVAILABLE
        if unavailable and self.outcome is not FocusOutcome.UNAVAILABLE:
            raise ValueError("unavailable Focus facts require an unavailable outcome")
        if not unavailable and self.outcome is FocusOutcome.UNAVAILABLE:
            raise ValueError("observed Focus facts require an explicit outcome")
        if self.observation is FocusObservation.INACTIVE and self.outcome is FocusOutcome.SUPPRESSED:
            raise ValueError("inactive Focus cannot suppress the signal")
        if self.policy is FocusPolicy.ALLOW and self.outcome is FocusOutcome.SUPPRESSED:
            raise ValueError("allow policy cannot suppress the signal")


@dataclass(frozen=True, slots=True)
class RendererTiming:
    availability: ValueAvailability
    source: OutputTimingSource = OutputTimingSource.UNAVAILABLE
    sample_count: int = 0
    latest_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0

    def __post_init__(self) -> None:
        _require_enum(self.availability, ValueAvailability, "availability")
        _require_enum(self.source, OutputTimingSource, "source")
        if type(self.sample_count) is not int:
            raise TypeError("sample_count must be an integer count")
        if self.sample_count < 0 or self.sample_count > MAX_RENDERER_SAMPLE_COUNT:
            raise ValueError("sample_count is outside the bounded range")
        for name in ("latest_ms", "p50_ms", "p95_ms"):
            _bounded_number(getattr(self, name), field_name=name, maximum=MAX_RENDERER_TIMING_MS)

        if self.availability is ValueAvailability.UNAVAILABLE:
            if self.source is not OutputTimingSource.UNAVAILABLE:
                raise ValueError("unavailable renderer timing cannot name a source")
            if self.sample_count != 0 or self.latest_ms != 0.0 or self.p50_ms != 0.0 or self.p95_ms != 0.0:
                raise ValueError("unavailable renderer timing cannot contain measurements")
            return
        if self.source is OutputTimingSource.UNAVAILABLE:
            raise ValueError("available renderer timing requires a source")
        if self.sample_count == 0:
            raise ValueError("available renderer timing requires a sample")
        if self.p50_ms > self.p95_ms:
            raise ValueError("p50_ms cannot exceed p95_ms")

    @classmethod
    def available(
        cls,
        sample_count: int,
        latest_ms: float,
        p50_ms: float,
        p95_ms: float,
        *,
        source: OutputTimingSource,
    ) -> RendererTiming:
        return cls(
            ValueAvailability.AVAILABLE,
            source,
            sample_count,
            latest_ms,
            p50_ms,
            p95_ms,
        )

    @classmethod
    def unavailable(cls) -> RendererTiming:
        return cls(ValueAvailability.UNAVAILABLE, OutputTimingSource.UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class WhyLightContext:
    selected_semantic: LightSemantic
    winning_priority: WinningPriority
    source_age: SourceAge
    suppressions: SuppressionCounts
    scene_availability: SceneAvailability
    surface_role: GlobalSurfaceRole
    focus_dnd: FocusDNDDecision
    reduce_motion: ReduceMotionDecision
    renderer_timing: RendererTiming

    def __post_init__(self) -> None:
        expected_types = {
            "selected_semantic": LightSemantic,
            "winning_priority": WinningPriority,
            "source_age": SourceAge,
            "suppressions": SuppressionCounts,
            "scene_availability": SceneAvailability,
            "surface_role": GlobalSurfaceRole,
            "focus_dnd": FocusDNDDecision,
            "reduce_motion": ReduceMotionDecision,
            "renderer_timing": RendererTiming,
        }
        for name, expected_type in expected_types.items():
            if type(getattr(self, name)) is not expected_type:
                raise TypeError(f"{name} must be {expected_type.__name__}")


_SEMANTIC_LABELS = {
    LightSemantic.ATTENTION: "Attention needed",
    LightSemantic.FRESH_FAILURE: "Fresh failure",
    LightSemantic.FRESH_COMPLETION: "Fresh completion",
    LightSemantic.ACTIVE: "Active work",
    LightSemantic.UNRESOLVED_FAILURE: "Unresolved failure",
    LightSemantic.CAPACITY: "Capacity",
    LightSemantic.REST: "Rest",
    LightSemantic.UNAVAILABLE: "Unavailable",
}

_SURFACE_LABELS = {
    GlobalSurfaceRole.NONE: "No active surfaces",
    GlobalSurfaceRole.SCREEN_BAR: "Screen Bar",
    GlobalSurfaceRole.PHYSICAL: "Physical devices",
    GlobalSurfaceRole.SCREEN_BAR_AND_PHYSICAL: "Screen Bar and physical devices",
    GlobalSurfaceRole.UNAVAILABLE: "Unavailable",
}

_FOCUS_OBSERVATION_LABELS = {
    FocusObservation.ACTIVE: "Active",
    FocusObservation.INACTIVE: "Inactive",
    FocusObservation.UNAVAILABLE: "Unavailable",
}

_FOCUS_POLICY_LABELS = {
    FocusPolicy.ALLOW: "Allow",
    FocusPolicy.SUPPRESS: "Suppress",
    FocusPolicy.UNAVAILABLE: "Unavailable",
}

_FOCUS_OUTCOME_LABELS = {
    FocusOutcome.ALLOWED: "Allowed",
    FocusOutcome.SUPPRESSED: "Suppressed",
    FocusOutcome.UNAVAILABLE: "Unavailable",
}

_DND_MODE_LABELS = {
    DndMode.MUTE: "Mute",
    DndMode.DIM: "Dim",
    DndMode.PAUSE: "Pause",
    DndMode.ASKS_ONLY: "Asks Only",
    DndMode.DARK: "Fully Dark",
}

_DND_SOURCE_LABELS = {
    DndSource.MANUAL: "Manual",
    DndSource.SCHEDULE: "Scheduled",
    DndSource.MACOS_FOCUS: "macOS Focus",
    DndSource.NAMED_FOCUS: "Named Focus",
}

_REDUCE_MOTION_LABELS = {
    ReduceMotionDecision.UNAVAILABLE: "Unavailable",
    ReduceMotionDecision.NO_MOTION_REQUESTED: "No motion requested",
    ReduceMotionDecision.MOTION_UNCHANGED: "Motion requested; no substitution",
    ReduceMotionDecision.STATIC_SUBSTITUTED: "Static signal substituted",
}

_TIMING_LABELS = {
    OutputTimingSource.SCREEN_BAR_RENDERER: "Screen Bar renderer timing",
    OutputTimingSource.PHYSICAL_HARDWARE_WRITE: "Hardware write latency",
    OutputTimingSource.UNAVAILABLE: "Output timing",
}


def format_why_light_context(context: WhyLightContext) -> str:
    """Format one fixed-shape explanation section from bounded local facts."""
    if type(context) is not WhyLightContext:
        raise TypeError("context must be WhyLightContext")

    source_age = (
        f"{context.source_age.seconds:.1f} seconds"
        if context.source_age.availability is ValueAvailability.AVAILABLE
        else "Unavailable"
    )
    priority = (
        f"P{context.winning_priority.value}"
        if context.winning_priority is not WinningPriority.UNAVAILABLE
        else "Unavailable"
    )
    suppressions = context.suppressions
    focus = context.focus_dnd
    timing = context.renderer_timing
    timing_label = (
        f"latest {timing.latest_ms:.1f} ms; p50 {timing.p50_ms:.1f} ms; "
        f"p95 {timing.p95_ms:.1f} ms; samples {timing.sample_count}"
        if timing.availability is ValueAvailability.AVAILABLE
        else "Unavailable"
    )
    dnd_suffix = ""
    if focus.dnd_sources or focus.dnd_return_epoch is not None:
        mode = (
            "+".join(_DND_MODE_LABELS[value] for value in focus.dnd_modes)
            or ("Named Rule" if focus.dnd_sources else "Off")
        )
        sources = (
            "+".join(_DND_SOURCE_LABELS[value] for value in focus.dnd_sources)
            or "None"
        )
        source_label = "source" if len(focus.dnd_sources) == 1 else "sources"
        returns = (
            datetime.fromtimestamp(
                focus.dnd_return_epoch,
                tz=timezone.utc,
            ).strftime("%Y-%m-%d %H:%MZ")
            if focus.dnd_return_epoch is not None
            else "Unscheduled"
        )
        dnd_suffix = f"; DND {mode}; {source_label} {sources}; returns {returns}"

    return "\n".join(
        (
            "Current light context",
            f"Semantic: {_SEMANTIC_LABELS[context.selected_semantic]}",
            f"Winning priority: {priority}",
            f"Source age: {source_age}",
            f"Current suppressions: total {suppressions.total}",
            (
                f"  Attention {suppressions.attention}; "
                f"fresh failure {suppressions.fresh_failure}; "
                f"fresh completion {suppressions.fresh_completion}"
            ),
            (
                f"  Active {suppressions.active}; "
                f"unresolved failure {suppressions.unresolved_failure}; "
                f"capacity {suppressions.capacity}"
            ),
            "Scene: Unavailable",
            f"Global surface role: {_SURFACE_LABELS[context.surface_role]}",
            (
                f"Focus/DND: {_FOCUS_OBSERVATION_LABELS[focus.observation]}; "
                f"policy {_FOCUS_POLICY_LABELS[focus.policy]}; "
                f"decision {_FOCUS_OUTCOME_LABELS[focus.outcome]}{dnd_suffix}"
            ),
            f"Reduce Motion: {_REDUCE_MOTION_LABELS[context.reduce_motion]}",
            f"{_TIMING_LABELS[timing.source]}: {timing_label}",
        )
    )


__all__ = [
    "MAX_DND_RETURN_EPOCH",
    "MAX_RENDERER_SAMPLE_COUNT",
    "MAX_RENDERER_TIMING_MS",
    "MAX_SOURCE_AGE_SECONDS",
    "MAX_SUPPRESSION_COUNT",
    "FocusDNDDecision",
    "FocusObservation",
    "FocusOutcome",
    "FocusPolicy",
    "GlobalSurfaceRole",
    "LightSemantic",
    "OutputTimingSource",
    "ReduceMotionDecision",
    "RendererTiming",
    "SceneAvailability",
    "SourceAge",
    "SuppressionCounts",
    "ValueAvailability",
    "WhyLightContext",
    "WinningPriority",
    "format_why_light_context",
]
