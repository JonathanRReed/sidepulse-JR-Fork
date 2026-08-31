"""Pure evidence and presentation policy for a finite Handoff Baton.

A close completion and start are only a timing correlation. This module emits
a baton when the two observations also share exact task or project identity,
and when distinct agents and segments make a directional handoff meaningful.
It performs no rendering, controller access, clock reads, or device I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Final

DEFAULT_HANDOFF_WINDOW_SECONDS: Final = 8.0
MAX_HANDOFF_WINDOW_SECONDS: Final = 30.0
TRAVEL_DURATION_MS: Final = 900
STATIC_HIGHLIGHT_DURATION_MS: Final = 700
MAX_IDENTITY_LENGTH: Final = 128
MAX_ACCESSIBILITY_NAME_LENGTH: Final = 80
MAX_ACCESSIBILITY_TEXT_LENGTH: Final = 256


class HandoffLinkKind(str, Enum):
    """The exact evidence that connects both endpoint observations."""

    TASK = "task"
    PROJECT = "project"


class HandoffBatonMotionVariant(str, Enum):
    """Finite motion choices, including the Reduce Motion substitution."""

    TRAVEL_ONCE = "travel_once"
    STATIC_HIGHLIGHT = "static_highlight"


class HandoffBatonRefusal(str, Enum):
    """Why two observations did not form a Handoff Baton."""

    SAME_EVENT = "same_event"
    SAME_AGENT = "same_agent"
    SAME_SEGMENT = "same_segment"
    MISSING_LINKAGE = "missing_linkage"
    DESTINATION_PRECEDES_SOURCE = "destination_precedes_source"
    OUTSIDE_WINDOW = "outside_window"


def _bounded_text(value: object, *, field: str, limit: int) -> str:
    if (
        type(value) is not str
        or not value
        or value.strip() != value
        or len(value) > limit
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError(f"{field} must be bounded single-line text")
    return value


def _optional_identity(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, field=field, limit=MAX_IDENTITY_LENGTH)


def _finite_nonnegative(value: object, *, field: str) -> float:
    if (
        type(value) not in {int, float}
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise ValueError(f"{field} must be a finite nonnegative number")
    return float(value)


def _handoff_window(value: object) -> float:
    if (
        type(value) not in {int, float}
        or not math.isfinite(float(value))
        or not 0.0 < float(value) <= MAX_HANDOFF_WINDOW_SECONDS
    ):
        raise ValueError(
            "handoff window must be finite, positive, and no greater than the cap"
        )
    return float(value)


@dataclass(frozen=True, slots=True)
class HandoffEndpoint:
    """One content-free endpoint observation supplied by a runtime owner.

    The first endpoint passed to :func:`plan_handoff_baton` is a completion.
    The second is a start. Project and task identities are optional because
    unlinked observations remain useful to callers, but they cannot produce a
    baton.
    """

    event_identity: str
    agent_identity: str
    segment_identity: str
    accessibility_name: str
    observed_at: float
    project_identity: str | None = None
    task_identity: str | None = None

    def __post_init__(self) -> None:
        _bounded_text(
            self.event_identity,
            field="event identity",
            limit=MAX_IDENTITY_LENGTH,
        )
        _bounded_text(
            self.agent_identity,
            field="agent identity",
            limit=MAX_IDENTITY_LENGTH,
        )
        _bounded_text(
            self.segment_identity,
            field="segment identity",
            limit=MAX_IDENTITY_LENGTH,
        )
        _bounded_text(
            self.accessibility_name,
            field="accessibility name",
            limit=MAX_ACCESSIBILITY_NAME_LENGTH,
        )
        object.__setattr__(
            self,
            "observed_at",
            _finite_nonnegative(self.observed_at, field="endpoint observed time"),
        )
        _optional_identity(self.project_identity, field="project identity")
        _optional_identity(self.task_identity, field="task identity")


@dataclass(frozen=True, slots=True)
class HandoffLinkageEvidence:
    """The shared task or project identity that makes the handoff explicit."""

    kind: HandoffLinkKind
    identity: str

    def __post_init__(self) -> None:
        if type(self.kind) is not HandoffLinkKind:
            raise ValueError("handoff linkage kind must be known")
        _bounded_text(
            self.identity,
            field="handoff linkage identity",
            limit=MAX_IDENTITY_LENGTH,
        )


@dataclass(frozen=True, slots=True)
class HandoffBatonPresentationPlan:
    """Content-free surface roles for a source-to-destination presentation."""

    effect_identifier: str
    source_agent_identity: str
    destination_agent_identity: str
    source_segment_identity: str
    destination_segment_identity: str
    settle_at_destination: bool

    def __post_init__(self) -> None:
        for field, value in (
            ("effect identity", self.effect_identifier),
            ("source agent identity", self.source_agent_identity),
            ("destination agent identity", self.destination_agent_identity),
            ("source segment identity", self.source_segment_identity),
            ("destination segment identity", self.destination_segment_identity),
        ):
            _bounded_text(value, field=field, limit=MAX_IDENTITY_LENGTH)
        if self.source_agent_identity == self.destination_agent_identity:
            raise ValueError("handoff presentation agents must be distinct")
        if self.source_segment_identity == self.destination_segment_identity:
            raise ValueError("handoff presentation segments must be distinct")
        if type(self.settle_at_destination) is not bool:
            raise ValueError("handoff settle policy must be a boolean")
        if not self.settle_at_destination:
            raise ValueError("handoff presentation must settle at the destination")


@dataclass(frozen=True, slots=True)
class HandoffBatonAccessibilityPlan:
    """A non-color, non-motion account of the same directional event."""

    label: str
    value: str
    announcement: str
    motion_description: str

    def __post_init__(self) -> None:
        for field, value in (
            ("accessibility label", self.label),
            ("accessibility value", self.value),
            ("accessibility announcement", self.announcement),
            ("accessibility motion description", self.motion_description),
        ):
            _bounded_text(value, field=field, limit=MAX_ACCESSIBILITY_TEXT_LENGTH)


@dataclass(frozen=True, slots=True)
class HandoffBatonMotionPlan:
    """One bounded animation or its finite static accessibility substitute."""

    variant: HandoffBatonMotionVariant
    duration_ms: int
    finite: bool
    spatial_travel: bool
    passes: int
    loops: int

    def __post_init__(self) -> None:
        if type(self.variant) is not HandoffBatonMotionVariant:
            raise ValueError("handoff motion variant must be known")
        if type(self.duration_ms) is not int or not 1 <= self.duration_ms <= 5_000:
            raise ValueError("handoff motion duration must be bounded milliseconds")
        if type(self.finite) is not bool or not self.finite:
            raise ValueError("handoff motion must be finite")
        if type(self.spatial_travel) is not bool:
            raise ValueError("handoff spatial travel policy must be a boolean")
        if type(self.passes) is not int or type(self.loops) is not int:
            raise ValueError("handoff repetition counts must be integers")
        if self.loops != 0:
            raise ValueError("handoff motion cannot loop")
        if self.variant is HandoffBatonMotionVariant.TRAVEL_ONCE:
            if not self.spatial_travel or self.passes != 1:
                raise ValueError("traveling baton must make exactly one spatial pass")
        elif self.spatial_travel or self.passes != 0:
            raise ValueError("static handoff highlight cannot travel")


@dataclass(frozen=True, slots=True)
class HandoffBatonPlan:
    """The admitted evidence plus immutable surface and accessibility plans."""

    source_event_identity: str
    destination_event_identity: str
    linkage: HandoffLinkageEvidence
    elapsed_seconds: float
    window_seconds: float
    presentation: HandoffBatonPresentationPlan
    accessibility: HandoffBatonAccessibilityPlan
    motion: HandoffBatonMotionPlan

    def __post_init__(self) -> None:
        _bounded_text(
            self.source_event_identity,
            field="source event identity",
            limit=MAX_IDENTITY_LENGTH,
        )
        _bounded_text(
            self.destination_event_identity,
            field="destination event identity",
            limit=MAX_IDENTITY_LENGTH,
        )
        if self.source_event_identity == self.destination_event_identity:
            raise ValueError("handoff plan event identities must be distinct")
        if type(self.linkage) is not HandoffLinkageEvidence:
            raise ValueError("handoff plan linkage must be typed")
        elapsed = _finite_nonnegative(
            self.elapsed_seconds,
            field="handoff elapsed time",
        )
        window = _handoff_window(self.window_seconds)
        if elapsed > window:
            raise ValueError("handoff elapsed time must fit the handoff window")
        object.__setattr__(self, "elapsed_seconds", elapsed)
        object.__setattr__(self, "window_seconds", window)
        if type(self.presentation) is not HandoffBatonPresentationPlan:
            raise ValueError("handoff presentation plan must be typed")
        if type(self.accessibility) is not HandoffBatonAccessibilityPlan:
            raise ValueError("handoff accessibility plan must be typed")
        if type(self.motion) is not HandoffBatonMotionPlan:
            raise ValueError("handoff motion plan must be typed")


@dataclass(frozen=True, slots=True)
class HandoffBatonDecision:
    """Exactly one admitted plan or one explicit refusal."""

    plan: HandoffBatonPlan | None
    refusal: HandoffBatonRefusal | None

    def __post_init__(self) -> None:
        if self.plan is not None and type(self.plan) is not HandoffBatonPlan:
            raise ValueError("handoff decision plan must be typed")
        if self.refusal is not None and type(self.refusal) is not HandoffBatonRefusal:
            raise ValueError("handoff decision refusal must be known")
        if (self.plan is None) == (self.refusal is None):
            raise ValueError("handoff decision requires exactly one plan or refusal")

    @property
    def admitted(self) -> bool:
        return self.plan is not None


def _refuse(reason: HandoffBatonRefusal) -> HandoffBatonDecision:
    return HandoffBatonDecision(plan=None, refusal=reason)


def _linkage(
    source: HandoffEndpoint,
    destination: HandoffEndpoint,
) -> HandoffLinkageEvidence | None:
    if (
        source.task_identity is not None
        and source.task_identity == destination.task_identity
    ):
        return HandoffLinkageEvidence(HandoffLinkKind.TASK, source.task_identity)
    if (
        source.project_identity is not None
        and source.project_identity == destination.project_identity
    ):
        return HandoffLinkageEvidence(HandoffLinkKind.PROJECT, source.project_identity)
    return None


def plan_handoff_baton(
    source_completion: HandoffEndpoint,
    destination_start: HandoffEndpoint,
    *,
    window_seconds: float = DEFAULT_HANDOFF_WINDOW_SECONDS,
    reduce_motion: bool = False,
) -> HandoffBatonDecision:
    """Plan one evidence-backed baton or explain why it was withheld.

    Caller-supplied observation times must share a timebase. This function
    only compares them and never reads a clock. Exact task linkage takes
    precedence over exact project linkage when both are available.
    """

    if type(source_completion) is not HandoffEndpoint:
        raise ValueError("handoff source completion must be a typed endpoint")
    if type(destination_start) is not HandoffEndpoint:
        raise ValueError("handoff destination start must be a typed endpoint")
    window = _handoff_window(window_seconds)
    if type(reduce_motion) is not bool:
        raise ValueError("handoff Reduce Motion policy must be a boolean")

    if source_completion.event_identity == destination_start.event_identity:
        return _refuse(HandoffBatonRefusal.SAME_EVENT)
    if source_completion.agent_identity == destination_start.agent_identity:
        return _refuse(HandoffBatonRefusal.SAME_AGENT)
    if source_completion.segment_identity == destination_start.segment_identity:
        return _refuse(HandoffBatonRefusal.SAME_SEGMENT)

    linkage = _linkage(source_completion, destination_start)
    if linkage is None:
        return _refuse(HandoffBatonRefusal.MISSING_LINKAGE)

    elapsed = destination_start.observed_at - source_completion.observed_at
    if elapsed < 0.0:
        return _refuse(HandoffBatonRefusal.DESTINATION_PRECEDES_SOURCE)
    if elapsed > window:
        return _refuse(HandoffBatonRefusal.OUTSIDE_WINDOW)

    if reduce_motion:
        variant = HandoffBatonMotionVariant.STATIC_HIGHLIGHT
        duration_ms = STATIC_HIGHLIGHT_DURATION_MS
        spatial_travel = False
        passes = 0
        motion_description = (
            "A finite static highlight marks the destination without spatial motion."
        )
    else:
        variant = HandoffBatonMotionVariant.TRAVEL_ONCE
        duration_ms = TRAVEL_DURATION_MS
        spatial_travel = True
        passes = 1
        motion_description = (
            "One finite baton travels from the source to the destination."
        )

    presentation = HandoffBatonPresentationPlan(
        effect_identifier="handoff-baton",
        source_agent_identity=source_completion.agent_identity,
        destination_agent_identity=destination_start.agent_identity,
        source_segment_identity=source_completion.segment_identity,
        destination_segment_identity=destination_start.segment_identity,
        settle_at_destination=True,
    )
    accessibility = HandoffBatonAccessibilityPlan(
        label="Work handoff",
        value=(
            f"{source_completion.accessibility_name} to "
            f"{destination_start.accessibility_name}"
        ),
        announcement=(
            f"Handoff from {source_completion.accessibility_name} to "
            f"{destination_start.accessibility_name}."
        ),
        motion_description=motion_description,
    )
    motion = HandoffBatonMotionPlan(
        variant=variant,
        duration_ms=duration_ms,
        finite=True,
        spatial_travel=spatial_travel,
        passes=passes,
        loops=0,
    )
    return HandoffBatonDecision(
        plan=HandoffBatonPlan(
            source_event_identity=source_completion.event_identity,
            destination_event_identity=destination_start.event_identity,
            linkage=linkage,
            elapsed_seconds=elapsed,
            window_seconds=window,
            presentation=presentation,
            accessibility=accessibility,
            motion=motion,
        ),
        refusal=None,
    )


__all__ = [
    "DEFAULT_HANDOFF_WINDOW_SECONDS",
    "MAX_HANDOFF_WINDOW_SECONDS",
    "STATIC_HIGHLIGHT_DURATION_MS",
    "TRAVEL_DURATION_MS",
    "HandoffBatonAccessibilityPlan",
    "HandoffBatonDecision",
    "HandoffBatonMotionPlan",
    "HandoffBatonMotionVariant",
    "HandoffBatonPlan",
    "HandoffBatonPresentationPlan",
    "HandoffBatonRefusal",
    "HandoffEndpoint",
    "HandoffLinkKind",
    "HandoffLinkageEvidence",
    "plan_handoff_baton",
]
