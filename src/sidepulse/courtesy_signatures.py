"""Pure semantic courtesy signatures for JR Bar.

Courtesy signatures communicate a small set of event meanings through finite
spatial geometry and safe cadence, never through color alone.  The registry is
deliberately not a cosmetic effect picker.  Each semantic owns exactly one
stable signature and one static Reduce Motion substitute.

This module contains metadata and planning only.  It performs no rendering,
scheduling, controller mutation, or device I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

MAX_CADENCE_HZ: Final = 2.0
MIN_CADENCE_CYCLE_MS: Final = int(1000 / MAX_CADENCE_HZ)
MAX_CADENCE_PULSES: Final = 3
GEOMETRY_SLOT_COUNT: Final = 5

_IDENTIFIER = re.compile(
    r"jrbar\.courtesy\.[a-z0-9]+(?:-[a-z0-9]+)*\.v[1-9][0-9]*\Z"
)
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class CourtesySignatureError(ValueError):
    """Raised when semantic signature metadata is unsafe or ambiguous."""


class CourtesySemantic(str, Enum):
    """Meanings that may receive a finite courtesy signature."""

    COMPLETION = "completion"
    RECOVERY = "recovery"
    HANDOFF = "handoff"
    INTERRUPTION = "interruption"
    FAILURE = "failure"
    QUOTA_RESET = "quota_reset"
    CALENDAR = "calendar"
    REMINDER = "reminder"
    BATTERY = "battery"
    WEATHER = "weather"
    GENERIC_NOTIFICATION = "generic_notification"


class CourtesyPresentation(str, Enum):
    """The two presentations a runtime may compile from this foundation."""

    FINITE = "finite"
    STATIC = "static"


@dataclass(frozen=True, slots=True)
class GeometrySignature:
    """Surface-independent five-slot spatial identity.

    A frame is an ordered tuple of occupied slot indices from left to right.
    Renderers may adapt those slots to their physical geometry, but must keep
    the topology and order.  ``static_slots`` is a separate, unique shape so
    Reduce Motion and monochrome presentations preserve the same meaning.
    """

    frames: tuple[tuple[int, ...], ...]
    static_slots: tuple[int, ...]
    spatial_description: str

    def __post_init__(self) -> None:
        frames = tuple(tuple(frame) for frame in self.frames)
        static_slots = tuple(self.static_slots)
        object.__setattr__(self, "frames", frames)
        object.__setattr__(self, "static_slots", static_slots)

        if not frames:
            raise CourtesySignatureError("geometry needs at least one frame")
        if len(frames) > GEOMETRY_SLOT_COUNT:
            raise CourtesySignatureError("geometry allows at most five frames")
        for frame in frames:
            _validate_slots(frame, field="geometry frame")
        if not static_slots:
            raise CourtesySignatureError("static geometry is required")
        _validate_slots(static_slots, field="static geometry")
        if (
            type(self.spatial_description) is not str
            or not self.spatial_description
            or self.spatial_description != self.spatial_description.strip()
        ):
            raise CourtesySignatureError("spatial description is required")

    @property
    def fingerprint(self) -> tuple[tuple[int, ...], ...]:
        """Return the non-color moving geometry identity."""

        return self.frames


@dataclass(frozen=True, slots=True)
class CadencePulse:
    """One visible interval followed by one quiet interval."""

    active_ms: int
    rest_ms: int

    def __post_init__(self) -> None:
        if type(self.active_ms) is not int or type(self.rest_ms) is not int:
            raise CourtesySignatureError("cadence phases must be integer milliseconds")
        if self.active_ms <= 0 or self.rest_ms <= 0:
            raise CourtesySignatureError("cadence phases must be positive")
        if self.cycle_ms < MIN_CADENCE_CYCLE_MS:
            raise CourtesySignatureError("courtesy cadence exceeds the 2 Hz limit")

    @property
    def cycle_ms(self) -> int:
        return self.active_ms + self.rest_ms

    @property
    def peak_hz(self) -> float:
        return 1000.0 / self.cycle_ms


@dataclass(frozen=True, slots=True)
class CadenceSignature:
    """A named finite pulse sequence with no arbitrary frequency control."""

    name: str
    pulses: tuple[CadencePulse, ...]

    def __post_init__(self) -> None:
        pulses = tuple(self.pulses)
        object.__setattr__(self, "pulses", pulses)
        if type(self.name) is not str or _NAME.fullmatch(self.name) is None:
            raise CourtesySignatureError("cadence name must be a stable slug")
        if not pulses:
            raise CourtesySignatureError("cadence needs at least one pulse")
        if len(pulses) > MAX_CADENCE_PULSES:
            raise CourtesySignatureError("cadence allows at most three pulses")
        if any(type(pulse) is not CadencePulse for pulse in pulses):
            raise CourtesySignatureError("cadence pulses must be CadencePulse values")

    @property
    def pulse_count(self) -> int:
        return len(self.pulses)

    @property
    def duration_ms(self) -> int:
        return sum(pulse.cycle_ms for pulse in self.pulses)

    @property
    def peak_hz(self) -> float:
        return max(pulse.peak_hz for pulse in self.pulses)

    @property
    def fingerprint(self) -> tuple[tuple[int, int], ...]:
        """Return the timing identity without its cosmetic name."""

        return tuple((pulse.active_ms, pulse.rest_ms) for pulse in self.pulses)


@dataclass(frozen=True, slots=True)
class CourtesySignature:
    """One canonical, accessible signature for one semantic meaning."""

    identifier: str
    semantic: CourtesySemantic
    label: str
    meaning: str
    geometry: GeometrySignature
    cadence: CadenceSignature
    accessibility_label: str

    def __post_init__(self) -> None:
        if (
            type(self.identifier) is not str
            or _IDENTIFIER.fullmatch(self.identifier) is None
        ):
            raise CourtesySignatureError(
                "signature identifier must be a stable JR Bar identifier"
            )
        if type(self.semantic) is not CourtesySemantic:
            raise CourtesySignatureError("signature semantic must be CourtesySemantic")
        if type(self.geometry) is not GeometrySignature:
            raise CourtesySignatureError("signature geometry must be GeometrySignature")
        if type(self.cadence) is not CadenceSignature:
            raise CourtesySignatureError("signature cadence must be CadenceSignature")
        for value, field in (
            (self.label, "label"),
            (self.meaning, "meaning"),
            (self.accessibility_label, "accessibility label"),
        ):
            if type(value) is not str or not value or value != value.strip():
                raise CourtesySignatureError(f"signature {field} is required")


@dataclass(frozen=True, slots=True)
class CourtesySignaturePlan:
    """Immutable renderer-neutral projection of one courtesy signature."""

    identifier: str
    semantic: CourtesySemantic
    presentation: CourtesyPresentation
    frames: tuple[tuple[int, ...], ...]
    cadence: CadenceSignature | None
    static_slots: tuple[int, ...]
    spatial_description: str
    accessibility_label: str

    @property
    def reduce_motion_substituted(self) -> bool:
        return self.presentation is CourtesyPresentation.STATIC

    @property
    def has_motion(self) -> bool:
        return self.presentation is CourtesyPresentation.FINITE


class CourtesySignatureRegistry:
    """Strict registry that prevents semantic and non-color collisions."""

    def __init__(self, signatures: Iterable[CourtesySignature]) -> None:
        by_identifier: dict[str, CourtesySignature] = {}
        by_semantic: dict[CourtesySemantic, CourtesySignature] = {}
        geometry_owners: dict[tuple[tuple[int, ...], ...], str] = {}
        cadence_owners: dict[tuple[tuple[int, int], ...], str] = {}
        static_owners: dict[tuple[int, ...], str] = {}
        description_owners: dict[str, str] = {}
        accessibility_owners: dict[str, str] = {}

        ordered = tuple(signatures)
        for signature in ordered:
            if type(signature) is not CourtesySignature:
                raise CourtesySignatureError(
                    "registry entries must be CourtesySignature values"
                )
            if signature.identifier in by_identifier:
                raise CourtesySignatureError(
                    f"signature identifier collision: {signature.identifier}"
                )
            if signature.semantic in by_semantic:
                raise CourtesySignatureError(
                    f"signature semantic collision: {signature.semantic.value}"
                )
            _claim_unique(
                geometry_owners,
                signature.geometry.fingerprint,
                signature.identifier,
                "geometry collision",
            )
            _claim_unique(
                cadence_owners,
                signature.cadence.fingerprint,
                signature.identifier,
                "cadence collision",
            )
            _claim_unique(
                static_owners,
                signature.geometry.static_slots,
                signature.identifier,
                "static geometry collision",
            )
            _claim_unique(
                description_owners,
                signature.geometry.spatial_description,
                signature.identifier,
                "spatial description collision",
            )
            _claim_unique(
                accessibility_owners,
                signature.accessibility_label,
                signature.identifier,
                "accessibility label collision",
            )
            by_identifier[signature.identifier] = signature
            by_semantic[signature.semantic] = signature

        self._ordered = ordered
        self._by_identifier: Mapping[str, CourtesySignature] = MappingProxyType(
            by_identifier
        )
        self._by_semantic: Mapping[CourtesySemantic, CourtesySignature] = (
            MappingProxyType(by_semantic)
        )

    def list(self) -> tuple[CourtesySignature, ...]:
        return self._ordered

    def get(self, identifier: str) -> CourtesySignature | None:
        return self._by_identifier.get(identifier)

    def require(self, identifier: str) -> CourtesySignature:
        signature = self.get(identifier)
        if signature is None:
            raise KeyError(identifier)
        return signature

    def for_semantic(self, semantic: CourtesySemantic) -> CourtesySignature:
        if type(semantic) is not CourtesySemantic:
            raise CourtesySignatureError("semantic must be CourtesySemantic")
        try:
            return self._by_semantic[semantic]
        except KeyError:
            raise KeyError(semantic.value) from None


def _validate_slots(slots: tuple[int, ...], *, field: str) -> None:
    if not slots:
        raise CourtesySignatureError(f"{field} is required")
    if any(type(slot) is not int for slot in slots):
        raise CourtesySignatureError(f"{field} slots must be integers")
    if any(slot < 0 or slot >= GEOMETRY_SLOT_COUNT for slot in slots):
        raise CourtesySignatureError(f"{field} slot range is 0 through 4")
    if tuple(sorted(set(slots))) != slots:
        raise CourtesySignatureError(f"{field} needs ordered unique slots")


def _claim_unique(
    owners: dict[object, str],
    key: object,
    identifier: str,
    collision: str,
) -> None:
    owner = owners.get(key)
    if owner is not None:
        raise CourtesySignatureError(f"{collision}: {owner} and {identifier}")
    owners[key] = identifier


def _geometry(
    frames: tuple[tuple[int, ...], ...],
    static_slots: tuple[int, ...],
    description: str,
) -> GeometrySignature:
    return GeometrySignature(frames, static_slots, description)


def _cadence(
    name: str,
    *phases: tuple[int, int],
) -> CadenceSignature:
    return CadenceSignature(
        name,
        tuple(CadencePulse(active_ms, rest_ms) for active_ms, rest_ms in phases),
    )


COURTESY_SIGNATURES: Final[tuple[CourtesySignature, ...]] = (
    CourtesySignature(
        "jrbar.courtesy.completion.v1",
        CourtesySemantic.COMPLETION,
        "Completion",
        "A task completed successfully.",
        _geometry(
            ((2,), (1, 3), (0, 4)),
            (0, 4),
            "A center mark opens into two outer endpoints.",
        ),
        _cadence("completion-open", (250, 250)),
        "Task completed",
    ),
    CourtesySignature(
        "jrbar.courtesy.recovery.v1",
        CourtesySemantic.RECOVERY,
        "Recovery",
        "A previously unhealthy source recovered.",
        _geometry(
            ((0, 4), (1, 3), (2,)),
            (2,),
            "Two outer endpoints settle into one center mark.",
        ),
        _cadence("recovery-settle", (300, 300)),
        "Source recovered",
    ),
    CourtesySignature(
        "jrbar.courtesy.handoff.v1",
        CourtesySemantic.HANDOFF,
        "Handoff",
        "Work has an evidenced handoff to another owner.",
        _geometry(
            ((0,), (1,), (2,), (3,), (4,)),
            (4,),
            "A single mark travels from the first slot to the last.",
        ),
        _cadence("handoff-double", (200, 300), (200, 300)),
        "Handoff ready",
    ),
    CourtesySignature(
        "jrbar.courtesy.interruption.v1",
        CourtesySemantic.INTERRUPTION,
        "Interruption",
        "Active work was interrupted and may need review.",
        _geometry(
            ((0, 4), (2,), (0, 4)),
            (0, 2, 4),
            "Two endpoints close on the center and reopen.",
        ),
        _cadence("interruption-split", (150, 350), (150, 350)),
        "Work interrupted",
    ),
    CourtesySignature(
        "jrbar.courtesy.failure.v1",
        CourtesySemantic.FAILURE,
        "Failure",
        "A task or source failed and remains unresolved.",
        _geometry(
            ((0, 2, 4), (1, 3)),
            (1, 3),
            "Three separated marks alternate with two inset marks.",
        ),
        _cadence("failure-triple", (250, 250), (250, 250), (250, 500)),
        "Failure needs attention",
    ),
    CourtesySignature(
        "jrbar.courtesy.quota-reset.v1",
        CourtesySemantic.QUOTA_RESET,
        "Quota reset",
        "A provider capacity window reset.",
        _geometry(
            ((4,), (3, 4), (2, 3, 4), (1, 2, 3, 4), (0, 1, 2, 3, 4)),
            (0, 1, 2, 3, 4),
            "A bar refills from the final slot toward the first.",
        ),
        _cadence("quota-refill", (400, 400)),
        "Quota reset available",
    ),
    CourtesySignature(
        "jrbar.courtesy.calendar.v1",
        CourtesySemantic.CALENDAR,
        "Calendar",
        "A scheduled calendar event reached its courtesy window.",
        _geometry(
            ((0, 4), (0, 2, 4), (0, 1, 3, 4)),
            (0, 1, 3, 4),
            "Two endpoints gain a center mark and settle as a framed gap.",
        ),
        _cadence("calendar-window", (300, 500)),
        "Calendar event approaching",
    ),
    CourtesySignature(
        "jrbar.courtesy.reminder.v1",
        CourtesySemantic.REMINDER,
        "Reminder",
        "A user-created reminder became due.",
        _geometry(
            ((2,), (0, 2, 4), (2, 3)),
            (0, 2, 3),
            "A center mark expands to three points and settles to the right.",
        ),
        _cadence("reminder-pair", (200, 400), (200, 600)),
        "Reminder due",
    ),
    CourtesySignature(
        "jrbar.courtesy.battery.v1",
        CourtesySemantic.BATTERY,
        "Battery",
        "Battery state crossed a disclosed threshold.",
        _geometry(
            ((0,), (0, 1), (0, 1, 2), (0, 1, 2, 3)),
            (0, 1, 2, 3),
            "A bar grows from the first slot and leaves the endpoint open.",
        ),
        _cadence("battery-step", (500, 500)),
        "Battery threshold reached",
    ),
    CourtesySignature(
        "jrbar.courtesy.weather.v1",
        CourtesySemantic.WEATHER,
        "Weather",
        "A configured weather condition became relevant.",
        _geometry(
            ((0, 4), (1, 3), (2,), (1, 3), (0, 4)),
            (1, 2, 3),
            "A symmetric wave travels inward and returns outward.",
        ),
        _cadence("weather-wave", (600, 600)),
        "Weather condition changed",
    ),
    CourtesySignature(
        "jrbar.courtesy.generic-notification.v1",
        CourtesySemantic.GENERIC_NOTIFICATION,
        "Notification",
        "A generic notification has no more specific semantic signature.",
        _geometry(
            ((1, 2, 3),),
            (2, 3),
            "A short centered line ends as two adjacent marks.",
        ),
        _cadence("notification-single", (350, 350)),
        "New notification",
    ),
)

DEFAULT_COURTESY_SIGNATURE_REGISTRY: Final = CourtesySignatureRegistry(
    COURTESY_SIGNATURES
)


def signature_for_semantic(semantic: CourtesySemantic) -> CourtesySignature:
    """Return the single canonical signature for ``semantic``."""

    return DEFAULT_COURTESY_SIGNATURE_REGISTRY.for_semantic(semantic)


def plan_courtesy_signature(
    semantic: CourtesySemantic,
    *,
    reduce_motion: bool = False,
) -> CourtesySignaturePlan:
    """Project one finite cue or its static Reduce Motion substitute."""

    if type(reduce_motion) is not bool:
        raise CourtesySignatureError("reduce motion must be a boolean")
    signature = signature_for_semantic(semantic)
    static_slots = signature.geometry.static_slots
    if reduce_motion:
        return CourtesySignaturePlan(
            identifier=signature.identifier,
            semantic=signature.semantic,
            presentation=CourtesyPresentation.STATIC,
            frames=(static_slots,),
            cadence=None,
            static_slots=static_slots,
            spatial_description=signature.geometry.spatial_description,
            accessibility_label=signature.accessibility_label,
        )
    return CourtesySignaturePlan(
        identifier=signature.identifier,
        semantic=signature.semantic,
        presentation=CourtesyPresentation.FINITE,
        frames=signature.geometry.frames,
        cadence=signature.cadence,
        static_slots=static_slots,
        spatial_description=signature.geometry.spatial_description,
        accessibility_label=signature.accessibility_label,
    )


__all__ = [
    "COURTESY_SIGNATURES",
    "DEFAULT_COURTESY_SIGNATURE_REGISTRY",
    "GEOMETRY_SLOT_COUNT",
    "MAX_CADENCE_HZ",
    "MAX_CADENCE_PULSES",
    "MIN_CADENCE_CYCLE_MS",
    "CadencePulse",
    "CadenceSignature",
    "CourtesyPresentation",
    "CourtesySemantic",
    "CourtesySignature",
    "CourtesySignatureError",
    "CourtesySignaturePlan",
    "CourtesySignatureRegistry",
    "GeometrySignature",
    "plan_courtesy_signature",
    "signature_for_semantic",
]
