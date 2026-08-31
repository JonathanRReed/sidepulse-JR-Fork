"""Pure planning for the localized Firefly Completion cue.

The planner binds one exact, source-backed completion event to one explicit
fleet identity.  It snapshots the identity's active segment, finds that same
identity's stable fleet band, and returns renderer-neutral keyframes for a
single finite cue.  It never samples a clock, mutates fleet assignment, writes
to a device, or imports a renderer.

The snapshot is intentional.  A live fleet may rearrange while a completion
is being shown, but the firefly continues from the segment that actually
completed and releases to live assignment only after the finite cue ends.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .clear_agents import CompletionPresentationKey
from .fleet_bands import FleetBand, FleetPlan

FIREFLY_DURATION_SECONDS: Final = 2.0
REDUCE_MOTION_HOLD_SECONDS: Final = 0.75
MAX_LED_COUNT: Final = 4_096
MAX_IDENTITY_LENGTH: Final = 512


class FireflyCompletionMode(str, Enum):
    """How an accepted completion cue should be presented."""

    TRAVELLING_SPARK = "travelling_spark"
    STATIC_HIGHLIGHT = "static_highlight"


class FireflyCompletionRefusal(str, Enum):
    """Content-free reasons a completion cue was not planned."""

    INVALID_EVIDENCE = "invalid_evidence"
    INVALID_PREFERENCE = "invalid_preference"
    INVALID_FLEET = "invalid_fleet"
    INVALID_ACTIVE_SEGMENT = "invalid_active_segment"
    IDENTITY_NOT_IN_FLEET = "identity_not_in_fleet"
    AMBIGUOUS_IDENTITY = "ambiguous_identity"
    INVALID_STABLE_BAND = "invalid_stable_band"


def _finite_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _valid_identity(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_IDENTITY_LENGTH
        and value.strip() == value
        and value.isprintable()
        and "\x00" not in value
        and "\n" not in value
        and "\r" not in value
    )


@dataclass(frozen=True, slots=True)
class FireflySegment:
    """A frozen half-open segment in LED and Screen Bar coordinates."""

    identity: str
    led_start: int
    led_end: int
    screen_start: float
    screen_end: float

    def __post_init__(self) -> None:
        if not (
            _valid_identity(self.identity)
            and type(self.led_start) is int
            and type(self.led_end) is int
            and 0 <= self.led_start < self.led_end <= MAX_LED_COUNT
            and _finite_number(self.screen_start)
            and _finite_number(self.screen_end)
            and 0.0 <= float(self.screen_start) < float(self.screen_end)
        ):
            raise ValueError("invalid Firefly Completion segment")
        object.__setattr__(self, "screen_start", float(self.screen_start))
        object.__setattr__(self, "screen_end", float(self.screen_end))

    @property
    def led_center(self) -> float:
        """Center of the occupied zero-based LED indices."""
        return (self.led_start + self.led_end - 1) / 2.0

    @property
    def screen_center(self) -> float:
        return (self.screen_start + self.screen_end) / 2.0

    def led_position(self, fraction: float) -> float:
        first_index = float(self.led_start)
        last_index = float(self.led_end - 1)
        return first_index + (last_index - first_index) * fraction

    def screen_position(self, fraction: float) -> float:
        return self.screen_start + (self.screen_end - self.screen_start) * fraction


@dataclass(frozen=True, slots=True)
class FireflyCompletionEvidence:
    """Exact completion evidence bound to the segment visible at completion.

    ``active_fraction`` is the renderer-neutral position of the active marker
    inside the frozen segment, from 0.0 at its leading edge to 1.0 at its
    trailing edge.  The same fraction keeps physical and screen surfaces in
    phase without retaining renderer objects.
    """

    completion_key: CompletionPresentationKey
    fleet_identity: str
    active_band: FleetBand
    active_fraction: float = 0.5

    def __post_init__(self) -> None:
        if not (
            type(self.completion_key) is CompletionPresentationKey
            and _valid_identity(self.fleet_identity)
            and type(self.active_band) is FleetBand
            and self.active_band.identity == self.fleet_identity
            and self.active_band.shared is False
            and _finite_number(self.active_fraction)
            and 0.0 <= float(self.active_fraction) <= 1.0
        ):
            raise ValueError("invalid Firefly Completion evidence")
        object.__setattr__(self, "active_fraction", float(self.active_fraction))


@dataclass(frozen=True, slots=True)
class FireflyKeyframe:
    """One renderer-neutral sample of the travelling light."""

    elapsed_seconds: float
    led_position: float
    screen_position: float
    intensity: float

    def __post_init__(self) -> None:
        if not (
            _finite_number(self.elapsed_seconds)
            and float(self.elapsed_seconds) >= 0.0
            and _finite_number(self.led_position)
            and _finite_number(self.screen_position)
            and _finite_number(self.intensity)
            and 0.0 <= float(self.intensity) <= 1.0
        ):
            raise ValueError("invalid Firefly Completion keyframe")
        object.__setattr__(self, "elapsed_seconds", float(self.elapsed_seconds))
        object.__setattr__(self, "led_position", float(self.led_position))
        object.__setattr__(self, "screen_position", float(self.screen_position))
        object.__setattr__(self, "intensity", float(self.intensity))


@dataclass(frozen=True, slots=True)
class FireflyAccessibilityText:
    """Content-free text shared by all surfaces presenting the cue."""

    label: str
    value: str
    help: str

    def __post_init__(self) -> None:
        if not all(type(value) is str and value for value in (self.label, self.value, self.help)):
            raise ValueError("invalid Firefly Completion accessibility text")


@dataclass(frozen=True, slots=True)
class FireflyCompletionPlan:
    """An accepted finite cue, including its explicit release contract."""

    evidence: FireflyCompletionEvidence
    mode: FireflyCompletionMode
    frozen_active_segment: FireflySegment
    stable_fleet_band: FireflySegment
    duration_seconds: float
    keyframes: tuple[FireflyKeyframe, ...]
    accessibility: FireflyAccessibilityText
    release_to_live_assignment: bool = True

    def __post_init__(self) -> None:
        if not (
            type(self.evidence) is FireflyCompletionEvidence
            and type(self.mode) is FireflyCompletionMode
            and type(self.frozen_active_segment) is FireflySegment
            and type(self.stable_fleet_band) is FireflySegment
            and self.frozen_active_segment.identity == self.evidence.fleet_identity
            and self.stable_fleet_band.identity == self.evidence.fleet_identity
            and _finite_number(self.duration_seconds)
            and float(self.duration_seconds) > 0.0
            and type(self.keyframes) is tuple
            and len(self.keyframes) >= 2
            and all(type(frame) is FireflyKeyframe for frame in self.keyframes)
            and self.keyframes[0].elapsed_seconds == 0.0
            and self.keyframes[-1].elapsed_seconds == float(self.duration_seconds)
            and all(
                left.elapsed_seconds < right.elapsed_seconds
                for left, right in zip(self.keyframes, self.keyframes[1:])
            )
            and type(self.accessibility) is FireflyAccessibilityText
            and self.release_to_live_assignment is True
        ):
            raise ValueError("invalid Firefly Completion plan")
        object.__setattr__(self, "duration_seconds", float(self.duration_seconds))

    @property
    def completion_key(self) -> CompletionPresentationKey:
        return self.evidence.completion_key

    @property
    def fleet_identity(self) -> str:
        return self.evidence.fleet_identity


@dataclass(frozen=True, slots=True)
class FireflyCompletionDecision:
    """Exactly one accepted plan or one explicit refusal."""

    plan: FireflyCompletionPlan | None
    refusal: FireflyCompletionRefusal | None

    def __post_init__(self) -> None:
        accepted = type(self.plan) is FireflyCompletionPlan and self.refusal is None
        refused = self.plan is None and type(self.refusal) is FireflyCompletionRefusal
        if not (accepted or refused):
            raise ValueError("invalid Firefly Completion decision")

    @property
    def accepted(self) -> bool:
        return self.plan is not None


def _segment_from_band(band: object, *, identity: str) -> FireflySegment | None:
    if (
        type(band) is not FleetBand
        or band.identity != identity
        or band.shared is not False
    ):
        return None
    try:
        return FireflySegment(
            identity=identity,
            led_start=band.led_start,
            led_end=band.led_end,
            screen_start=band.screen_start,
            screen_end=band.screen_end,
        )
    except ValueError:
        return None


def _fleet_geometry_is_valid(fleet: FleetPlan) -> bool:
    return (
        fleet.mode in {"segmented", "shared"}
        and fleet.refusal is None
        and type(fleet.led_count) is int
        and 0 < fleet.led_count <= MAX_LED_COUNT
        and _finite_number(fleet.screen_bar_width)
        and float(fleet.screen_bar_width) > 0.0
        and type(fleet.bands) is tuple
        and type(fleet.member_slots) is tuple
        and all(type(band) is FleetBand for band in fleet.bands)
        and all(
            type(slot) is tuple
            and len(slot) == 3
            and _valid_identity(slot[0])
            and type(slot[1]) is int
            and type(slot[2]) is int
            for slot in fleet.member_slots
        )
    )


def _stable_segment(
    fleet: FleetPlan,
    identity: str,
) -> tuple[FireflySegment | None, FireflyCompletionRefusal | None]:
    matching_bands = tuple(band for band in fleet.bands if band.identity == identity)
    matching_slots = tuple(slot for slot in fleet.member_slots if slot[0] == identity)
    if len(matching_bands) > 1 or len(matching_slots) > 1:
        return None, FireflyCompletionRefusal.AMBIGUOUS_IDENTITY

    if matching_bands:
        segment = _segment_from_band(matching_bands[0], identity=identity)
        if segment is None:
            return None, FireflyCompletionRefusal.INVALID_STABLE_BAND
        if (
            segment.led_end > fleet.led_count
            or segment.screen_end > float(fleet.screen_bar_width)
        ):
            return None, FireflyCompletionRefusal.INVALID_STABLE_BAND
        return segment, None

    if not matching_slots:
        return None, FireflyCompletionRefusal.IDENTITY_NOT_IN_FLEET
    slot = matching_slots[0]
    if not (
        type(slot) is tuple
        and len(slot) == 3
        and type(slot[1]) is int
        and type(slot[2]) is int
        and 0 <= slot[1] < slot[2] <= fleet.led_count
    ):
        return None, FireflyCompletionRefusal.INVALID_STABLE_BAND
    screen_scale = float(fleet.screen_bar_width) / fleet.led_count
    try:
        return (
            FireflySegment(
                identity=identity,
                led_start=slot[1],
                led_end=slot[2],
                screen_start=slot[1] * screen_scale,
                screen_end=slot[2] * screen_scale,
            ),
            None,
        )
    except ValueError:
        return None, FireflyCompletionRefusal.INVALID_STABLE_BAND


def _smoothstep(value: float) -> float:
    return value * value * (3.0 - 2.0 * value)


def _travelling_keyframes(
    *,
    source_led: float,
    source_screen: float,
    target_led: float,
    target_screen: float,
) -> tuple[FireflyKeyframe, ...]:
    fractions = (0.0, 0.25, 0.5, 0.75, 1.0)
    intensities = (0.18, 0.68, 1.0, 0.56, 0.0)
    frames: list[FireflyKeyframe] = []
    for fraction, intensity in zip(fractions, intensities, strict=True):
        progress = _smoothstep(fraction)
        frames.append(
            FireflyKeyframe(
                elapsed_seconds=FIREFLY_DURATION_SECONDS * fraction,
                led_position=source_led + (target_led - source_led) * progress,
                screen_position=(
                    source_screen + (target_screen - source_screen) * progress
                ),
                intensity=intensity,
            )
        )
    return tuple(frames)


def _static_keyframes(
    *,
    target_led: float,
    target_screen: float,
) -> tuple[FireflyKeyframe, ...]:
    return (
        FireflyKeyframe(0.0, target_led, target_screen, 0.62),
        FireflyKeyframe(
            REDUCE_MOTION_HOLD_SECONDS,
            target_led,
            target_screen,
            0.62,
        ),
    )


def plan_firefly_completion(
    evidence: object,
    stable_fleet: object,
    *,
    reduce_motion: object = False,
) -> FireflyCompletionDecision:
    """Plan one truthful cue or return a content-free refusal.

    A completion event is not inferred from timing or aggregate state.  The
    caller must provide an exact :class:`CompletionPresentationKey`, an
    explicit fleet identity, and the active segment that identity occupied at
    completion.  The stable fleet plan must independently retain the same
    identity.  This makes a completion cue impossible to attach to whichever
    segment happens to occupy the old position later.
    """

    if type(evidence) is not FireflyCompletionEvidence:
        return FireflyCompletionDecision(
            None,
            FireflyCompletionRefusal.INVALID_EVIDENCE,
        )
    if type(reduce_motion) is not bool:
        return FireflyCompletionDecision(
            None,
            FireflyCompletionRefusal.INVALID_PREFERENCE,
        )
    if type(stable_fleet) is not FleetPlan or not _fleet_geometry_is_valid(
        stable_fleet
    ):
        return FireflyCompletionDecision(None, FireflyCompletionRefusal.INVALID_FLEET)

    active = _segment_from_band(
        evidence.active_band,
        identity=evidence.fleet_identity,
    )
    if active is None:
        return FireflyCompletionDecision(
            None,
            FireflyCompletionRefusal.INVALID_ACTIVE_SEGMENT,
        )
    if (
        active.led_end > stable_fleet.led_count
        or active.screen_end > float(stable_fleet.screen_bar_width)
    ):
        return FireflyCompletionDecision(
            None,
            FireflyCompletionRefusal.INVALID_ACTIVE_SEGMENT,
        )

    stable, refusal = _stable_segment(stable_fleet, evidence.fleet_identity)
    if stable is None:
        return FireflyCompletionDecision(None, refusal)

    target_led = stable.led_center
    target_screen = stable.screen_center
    if reduce_motion:
        mode = FireflyCompletionMode.STATIC_HIGHLIGHT
        duration = REDUCE_MOTION_HOLD_SECONDS
        keyframes = _static_keyframes(
            target_led=target_led,
            target_screen=target_screen,
        )
        accessibility = FireflyAccessibilityText(
            label="Completion highlight",
            value="A session completed in its fleet band.",
            help=(
                "A brief static highlight marks the stable fleet band because "
                "Reduce Motion is on."
            ),
        )
    else:
        mode = FireflyCompletionMode.TRAVELLING_SPARK
        duration = FIREFLY_DURATION_SECONDS
        keyframes = _travelling_keyframes(
            source_led=active.led_position(evidence.active_fraction),
            source_screen=active.screen_position(evidence.active_fraction),
            target_led=target_led,
            target_screen=target_screen,
        )
        accessibility = FireflyAccessibilityText(
            label="Firefly completion",
            value="A session completed in its fleet band.",
            help=(
                "A two-second firefly travels from the completing session's "
                "frozen segment to its stable fleet band."
            ),
        )

    return FireflyCompletionDecision(
        FireflyCompletionPlan(
            evidence=evidence,
            mode=mode,
            frozen_active_segment=active,
            stable_fleet_band=stable,
            duration_seconds=duration,
            keyframes=keyframes,
            accessibility=accessibility,
        ),
        None,
    )


__all__ = [
    "FIREFLY_DURATION_SECONDS",
    "REDUCE_MOTION_HOLD_SECONDS",
    "FireflyAccessibilityText",
    "FireflyCompletionDecision",
    "FireflyCompletionEvidence",
    "FireflyCompletionMode",
    "FireflyCompletionPlan",
    "FireflyCompletionRefusal",
    "FireflyKeyframe",
    "FireflySegment",
    "plan_firefly_completion",
]
