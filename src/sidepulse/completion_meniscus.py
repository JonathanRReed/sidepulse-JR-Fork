"""Pure planning for the finite Completion Meniscus surface cue.

The planner accepts one exact completion that a caller has already selected as
unseen.  It binds that evidence to a frozen Alcove or Screen Bar rectangle and
returns renderer-neutral samples for one center-out liquid ripple.  Reduce
Motion replaces the ripple with a brief, static center highlight.

This module does not select completions, read accessibility settings, sample a
clock, render, schedule, or write to a device.  Accessibility text intentionally
contains no provider, project, agent, event, path, prompt, or transcript data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .accessibility_display import AccessibilityDisplayPreferences
from .clear_agents import CompletionPresentationKey

RIPPLE_DURATION_MS: Final = 900
STATIC_HIGHLIGHT_DURATION_MS: Final = 650
MAX_SURFACE_DIMENSION: Final = 16_384.0


class CompletionMeniscusSurface(str, Enum):
    """The two screen surfaces that can present a meniscus."""

    ALCOVE = "alcove"
    SCREEN_BAR = "screen_bar"


class CompletionMeniscusMode(str, Enum):
    """One moving cue or its Reduce Motion substitute."""

    CENTER_OUT_RIPPLE = "center_out_ripple"
    STATIC_CENTER_HIGHLIGHT = "static_center_highlight"


def _finite_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(float(value))


@dataclass(frozen=True, slots=True)
class SelectedUnseenCompletionEvidence:
    """One exact completion selected by the existing unseen-completion policy.

    The exact presentation key preserves source instance, agent, event, and
    completion time identity.  The wrapper prevents this planner from accepting
    an aggregate count or inferring a completion from ambient runtime state.
    """

    completion_key: CompletionPresentationKey

    def __post_init__(self) -> None:
        if type(self.completion_key) is not CompletionPresentationKey:
            raise ValueError("completion evidence must contain one exact key")


@dataclass(frozen=True, slots=True)
class CompletionMeniscusGeometry:
    """A frozen surface rectangle in caller-supplied display coordinates."""

    surface: CompletionMeniscusSurface
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if type(self.surface) is not CompletionMeniscusSurface:
            raise ValueError("completion meniscus surface must be known")
        if not all(
            _finite_number(value) for value in (self.x, self.y, self.width, self.height)
        ):
            raise ValueError("completion meniscus geometry must be finite")
        if not (
            0.0 < float(self.width) <= MAX_SURFACE_DIMENSION
            and 0.0 < float(self.height) <= MAX_SURFACE_DIMENSION
        ):
            raise ValueError("completion meniscus dimensions are out of bounds")
        object.__setattr__(self, "x", float(self.x))
        object.__setattr__(self, "y", float(self.y))
        object.__setattr__(self, "width", float(self.width))
        object.__setattr__(self, "height", float(self.height))

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0

    @property
    def maximum_radius(self) -> float:
        """Horizontal radius that reaches both surface edges exactly once."""

        return self.width / 2.0


@dataclass(frozen=True, slots=True)
class CompletionMeniscusFrame:
    """One renderer-neutral sample of the center-out presentation."""

    elapsed_ms: int
    center_x: float
    center_y: float
    radius: float
    crest_height: float
    intensity: float
    opacity: float

    def __post_init__(self) -> None:
        if type(self.elapsed_ms) is not int or self.elapsed_ms < 0:
            raise ValueError("completion meniscus elapsed time must be nonnegative")
        if not all(
            _finite_number(value)
            for value in (
                self.center_x,
                self.center_y,
                self.radius,
                self.crest_height,
                self.intensity,
                self.opacity,
            )
        ):
            raise ValueError("completion meniscus frame values must be finite")
        if not (
            float(self.radius) >= 0.0
            and float(self.crest_height) >= 0.0
            and 0.0 <= float(self.intensity) <= 1.0
            and 0.0 <= float(self.opacity) <= 1.0
        ):
            raise ValueError("completion meniscus frame values are out of bounds")
        for field in (
            "center_x",
            "center_y",
            "radius",
            "crest_height",
            "intensity",
            "opacity",
        ):
            object.__setattr__(self, field, float(getattr(self, field)))


@dataclass(frozen=True, slots=True)
class CompletionMeniscusAppearance:
    """Accessibility-derived rendering facts with no user or work content."""

    opaque_fill: bool
    high_contrast: bool
    center_outline: bool

    def __post_init__(self) -> None:
        if not all(
            type(value) is bool
            for value in (
                self.opaque_fill,
                self.high_contrast,
                self.center_outline,
            )
        ):
            raise ValueError("completion meniscus appearance must use booleans")


@dataclass(frozen=True, slots=True)
class CompletionMeniscusAccessibility:
    """Content-free text for the same completion cue."""

    label: str
    value: str
    help: str

    def __post_init__(self) -> None:
        if not all(
            type(value) is str and value and "\n" not in value and "\r" not in value
            for value in (self.label, self.value, self.help)
        ):
            raise ValueError("completion meniscus accessibility text is invalid")


@dataclass(frozen=True, slots=True)
class CompletionMeniscusPlan:
    """One exact, finite surface cue with an explicit release contract."""

    evidence: SelectedUnseenCompletionEvidence
    geometry: CompletionMeniscusGeometry
    mode: CompletionMeniscusMode
    duration_ms: int
    frames: tuple[CompletionMeniscusFrame, ...]
    appearance: CompletionMeniscusAppearance
    accessibility: CompletionMeniscusAccessibility
    passes: int
    loops: int = 0
    release_to_live_surface: bool = True

    def __post_init__(self) -> None:
        if not (
            type(self.evidence) is SelectedUnseenCompletionEvidence
            and type(self.geometry) is CompletionMeniscusGeometry
            and type(self.mode) is CompletionMeniscusMode
            and type(self.duration_ms) is int
            and 1 <= self.duration_ms <= 5_000
            and type(self.frames) is tuple
            and len(self.frames) >= 2
            and all(type(frame) is CompletionMeniscusFrame for frame in self.frames)
            and self.frames[0].elapsed_ms == 0
            and self.frames[-1].elapsed_ms == self.duration_ms
            and all(
                left.elapsed_ms < right.elapsed_ms
                for left, right in zip(self.frames, self.frames[1:])
            )
            and type(self.appearance) is CompletionMeniscusAppearance
            and type(self.accessibility) is CompletionMeniscusAccessibility
            and type(self.passes) is int
            and type(self.loops) is int
            and self.loops == 0
            and self.release_to_live_surface is True
        ):
            raise ValueError("invalid completion meniscus plan")
        if self.mode is CompletionMeniscusMode.CENTER_OUT_RIPPLE:
            if self.passes != 1:
                raise ValueError("completion ripple must make exactly one pass")
        elif self.passes != 0:
            raise ValueError("static completion highlight cannot make a pass")

    @property
    def completion_key(self) -> CompletionPresentationKey:
        return self.evidence.completion_key


def _ripple_frames(
    geometry: CompletionMeniscusGeometry,
    *,
    high_contrast: bool,
    opaque_fill: bool,
) -> tuple[CompletionMeniscusFrame, ...]:
    fractions = (0.0, 0.2, 0.5, 0.78, 1.0)
    radius_fractions = (0.0, 0.14, 0.5, 0.82, 1.0)
    crest_fractions = (0.18, 0.3, 0.22, 0.1, 0.0)
    base_intensities = (0.22, 0.68, 0.9, 0.52, 0.0)
    base_opacities = (0.2, 0.72, 0.88, 0.48, 0.0)
    frames: list[CompletionMeniscusFrame] = []
    for fraction, radius, crest, intensity, opacity in zip(
        fractions,
        radius_fractions,
        crest_fractions,
        base_intensities,
        base_opacities,
        strict=True,
    ):
        visible = opacity > 0.0
        frames.append(
            CompletionMeniscusFrame(
                elapsed_ms=round(RIPPLE_DURATION_MS * fraction),
                center_x=geometry.center_x,
                center_y=geometry.center_y,
                radius=geometry.maximum_radius * radius,
                crest_height=geometry.height * crest,
                intensity=min(
                    1.0,
                    intensity + (0.1 if high_contrast and visible else 0.0),
                ),
                opacity=1.0 if opaque_fill and visible else opacity,
            )
        )
    return tuple(frames)


def _static_frames(
    geometry: CompletionMeniscusGeometry,
    *,
    high_contrast: bool,
    opaque_fill: bool,
) -> tuple[CompletionMeniscusFrame, ...]:
    intensity = 1.0 if high_contrast else 0.78
    opacity = 1.0 if opaque_fill else 0.86
    radius = min(geometry.width * 0.08, geometry.height * 0.75)
    crest_height = min(geometry.height * 0.16, radius)
    frame = dict(
        center_x=geometry.center_x,
        center_y=geometry.center_y,
        radius=radius,
        crest_height=crest_height,
        intensity=intensity,
        opacity=opacity,
    )
    return (
        CompletionMeniscusFrame(elapsed_ms=0, **frame),
        CompletionMeniscusFrame(
            elapsed_ms=STATIC_HIGHLIGHT_DURATION_MS,
            **frame,
        ),
    )


def plan_completion_meniscus(
    evidence: SelectedUnseenCompletionEvidence,
    geometry: CompletionMeniscusGeometry,
    preferences: AccessibilityDisplayPreferences,
) -> CompletionMeniscusPlan:
    """Plan one center-out completion cue or its static substitution.

    Selection remains the responsibility of ``select_unseen_completions`` and
    key construction remains the responsibility of the completion identity
    layer.  This planner therefore cannot infer or broaden the selected event.
    """

    if type(evidence) is not SelectedUnseenCompletionEvidence:
        raise ValueError("completion meniscus evidence must be exact and selected")
    if type(geometry) is not CompletionMeniscusGeometry:
        raise ValueError("completion meniscus geometry must be typed")
    if type(preferences) is not AccessibilityDisplayPreferences:
        raise ValueError("completion meniscus accessibility preferences must be typed")

    appearance = CompletionMeniscusAppearance(
        opaque_fill=preferences.reduce_transparency,
        high_contrast=preferences.increase_contrast,
        center_outline=preferences.differentiate_without_color,
    )
    if preferences.reduce_motion:
        mode = CompletionMeniscusMode.STATIC_CENTER_HIGHLIGHT
        duration_ms = STATIC_HIGHLIGHT_DURATION_MS
        frames = _static_frames(
            geometry,
            high_contrast=appearance.high_contrast,
            opaque_fill=appearance.opaque_fill,
        )
        passes = 0
        help_text = (
            "A brief static center highlight replaces the liquid ripple because "
            "Reduce Motion is on."
        )
    else:
        mode = CompletionMeniscusMode.CENTER_OUT_RIPPLE
        duration_ms = RIPPLE_DURATION_MS
        frames = _ripple_frames(
            geometry,
            high_contrast=appearance.high_contrast,
            opaque_fill=appearance.opaque_fill,
        )
        passes = 1
        help_text = (
            "One finite liquid ripple travels from the center to both surface edges."
        )

    return CompletionMeniscusPlan(
        evidence=evidence,
        geometry=geometry,
        mode=mode,
        duration_ms=duration_ms,
        frames=frames,
        appearance=appearance,
        accessibility=CompletionMeniscusAccessibility(
            label="Completion Meniscus",
            value="A previously unseen completion is ready.",
            help=help_text,
        ),
        passes=passes,
    )


__all__ = [
    "MAX_SURFACE_DIMENSION",
    "RIPPLE_DURATION_MS",
    "STATIC_HIGHLIGHT_DURATION_MS",
    "CompletionMeniscusAccessibility",
    "CompletionMeniscusAppearance",
    "CompletionMeniscusFrame",
    "CompletionMeniscusGeometry",
    "CompletionMeniscusMode",
    "CompletionMeniscusPlan",
    "CompletionMeniscusSurface",
    "SelectedUnseenCompletionEvidence",
    "plan_completion_meniscus",
]
