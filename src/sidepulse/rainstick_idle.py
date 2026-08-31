"""Pure planning for the opt-in Rainstick Idle liveness effect.

Rainstick Idle is an ambient, content-free indication that JR Bar is alive and
watching.  When admitted, one dim pixel advances along a bounded path at a low
frequency.  This module describes that geometry and cadence only.  It owns no
clock, scheduler, AppKit object, renderer, or device writer.

The effect fails dark whenever a higher-priority signal or an environmental
policy should own the surface.  Reduce Motion retains the liveness meaning as
one stationary dim pixel instead of removing the indication entirely.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Final

DEFAULT_SURFACE_PIXEL_COUNT: Final = 8
MAX_SURFACE_PIXEL_COUNT: Final = 1_024
RAINSTICK_LIT_PIXEL_COUNT: Final = 1
RAINSTICK_RELATIVE_LUMINANCE: Final = 0.04
RAINSTICK_STEP_INTERVAL_SECONDS: Final = 30.0
RAINSTICK_STEP_FREQUENCY_HZ: Final = 1.0 / RAINSTICK_STEP_INTERVAL_SECONDS
RAINSTICK_ACCESSIBILITY_DISCLOSURE: Final = (
    "Ambient liveness only; it does not indicate progress, health, or attention."
)


class RainstickIdleError(ValueError):
    """Raised when a caller supplies a malformed planner input."""


class RainstickDisposition(str, Enum):
    """The complete renderer-facing outcome vocabulary."""

    MOVE = "move"
    STATIC = "static"
    SUPPRESS = "suppress"


class RainstickThermalState(str, Enum):
    """The bounded thermal vocabulary accepted by the pure planner."""

    NOMINAL = "nominal"
    FAIR = "fair"
    SERIOUS = "serious"
    CRITICAL = "critical"


class RainstickSuppressionReason(str, Enum):
    """Content-free reasons Rainstick Idle must yield the surface."""

    DISABLED_PREFERENCE = "disabled_preference"
    HIGHER_PRIORITY_SIGNAL = "higher_priority_signal"
    DND = "dnd"
    NIGHT_POLICY = "night_policy"
    DISPLAY_ASLEEP = "display_asleep"
    SURFACE_HIDDEN = "surface_hidden"
    LOW_POWER = "low_power"
    THERMAL_SERIOUS = "thermal_serious"
    THERMAL_CRITICAL = "thermal_critical"


@dataclass(frozen=True, slots=True)
class RainstickCadence:
    """A clock-independent instruction for advancing the active pixel.

    A moving cadence carries a positive interval and its reciprocal frequency.
    The Reduce Motion substitute is represented by a non-moving zero-frequency
    cadence so a renderer never needs to invent motion policy.
    """

    moves: bool
    step_interval_seconds: float | None
    step_frequency_hz: float

    def __post_init__(self) -> None:
        if type(self.moves) is not bool:
            raise RainstickIdleError("cadence moves must be bool")
        if type(self.step_frequency_hz) is not float or not math.isfinite(
            self.step_frequency_hz
        ):
            raise RainstickIdleError("cadence frequency must be finite")
        if self.moves:
            interval = self.step_interval_seconds
            if (
                type(interval) is not float
                or not math.isfinite(interval)
                or interval <= 0.0
                or self.step_frequency_hz <= 0.0
                or not math.isclose(
                    self.step_frequency_hz,
                    1.0 / interval,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            ):
                raise RainstickIdleError(
                    "moving cadence must have a reciprocal interval"
                )
        elif (
            self.step_interval_seconds is not None
            or self.step_frequency_hz != 0.0
        ):
            raise RainstickIdleError("static cadence cannot advance")


@dataclass(frozen=True, slots=True)
class RainstickGeometry:
    """A one-pixel path expressed without sampling a current position."""

    surface_pixel_count: int
    lit_pixel_count: int
    path_start_index: int
    path_end_index: int
    step_delta: int
    wraps: bool
    relative_luminance: float
    static_index: int | None

    def __post_init__(self) -> None:
        if (
            type(self.surface_pixel_count) is not int
            or not 2 <= self.surface_pixel_count <= MAX_SURFACE_PIXEL_COUNT
        ):
            raise RainstickIdleError("geometry surface pixel count is out of range")
        if self.lit_pixel_count != RAINSTICK_LIT_PIXEL_COUNT:
            raise RainstickIdleError("Rainstick Idle must light exactly one pixel")
        if (
            self.path_start_index != 0
            or self.path_end_index != self.surface_pixel_count - 1
            or self.step_delta != 1
            or type(self.wraps) is not bool
            or not self.wraps
        ):
            raise RainstickIdleError("Rainstick Idle requires one forward wrapping path")
        if (
            type(self.relative_luminance) is not float
            or not math.isfinite(self.relative_luminance)
            or not 0.0 < self.relative_luminance <= RAINSTICK_RELATIVE_LUMINANCE
        ):
            raise RainstickIdleError("Rainstick Idle luminance exceeds its dim ceiling")
        if self.static_index is not None and (
            type(self.static_index) is not int
            or not 0 <= self.static_index < self.surface_pixel_count
        ):
            raise RainstickIdleError("static Rainstick index is outside the surface")

    @property
    def position_count(self) -> int:
        """Return the number of positions on the inclusive travel path."""

        return self.path_end_index - self.path_start_index + 1


@dataclass(frozen=True, slots=True)
class RainstickIdlePlan:
    """One internally consistent cadence, geometry, and accessibility plan."""

    disposition: RainstickDisposition
    cadence: RainstickCadence | None
    geometry: RainstickGeometry | None
    suppression_reasons: tuple[RainstickSuppressionReason, ...]
    accessibility_text: str

    def __post_init__(self) -> None:
        if type(self.disposition) is not RainstickDisposition:
            raise RainstickIdleError("Rainstick disposition must be known")
        if type(self.suppression_reasons) is not tuple or not all(
            type(reason) is RainstickSuppressionReason
            for reason in self.suppression_reasons
        ):
            raise RainstickIdleError("Rainstick suppression reasons must be typed")
        if len(set(self.suppression_reasons)) != len(self.suppression_reasons):
            raise RainstickIdleError("Rainstick suppression reasons must be unique")
        if (
            type(self.accessibility_text) is not str
            or not 1 <= len(self.accessibility_text) <= 512
            or not self.accessibility_text.isprintable()
        ):
            raise RainstickIdleError("Rainstick accessibility text is invalid")

        suppressed = self.disposition is RainstickDisposition.SUPPRESS
        if suppressed != bool(self.suppression_reasons):
            raise RainstickIdleError("suppressed Rainstick plans require reasons")
        if suppressed:
            if self.cadence is not None or self.geometry is not None:
                raise RainstickIdleError("suppressed Rainstick plans cannot retain output")
            return

        if type(self.cadence) is not RainstickCadence or type(
            self.geometry
        ) is not RainstickGeometry:
            raise RainstickIdleError(
                "visible Rainstick plans require cadence and geometry"
            )
        if self.disposition is RainstickDisposition.MOVE:
            if not self.cadence.moves or self.geometry.static_index is not None:
                raise RainstickIdleError("moving Rainstick plan is inconsistent")
        elif self.cadence.moves or self.geometry.static_index is None:
            raise RainstickIdleError("static Rainstick plan is inconsistent")

    @property
    def visible(self) -> bool:
        return self.disposition is not RainstickDisposition.SUPPRESS

    @property
    def animated(self) -> bool:
        return self.disposition is RainstickDisposition.MOVE

    @property
    def suppression_reason(self) -> RainstickSuppressionReason | None:
        """Return the first stable reason for compact UI projections."""

        return self.suppression_reasons[0] if self.suppression_reasons else None


_SUPPRESSION_LABELS: Final = {
    RainstickSuppressionReason.DISABLED_PREFERENCE: "the preference is disabled",
    RainstickSuppressionReason.HIGHER_PRIORITY_SIGNAL: (
        "a higher-priority signal owns the surface"
    ),
    RainstickSuppressionReason.DND: "Do Not Disturb is active",
    RainstickSuppressionReason.NIGHT_POLICY: "the current night policy withholds it",
    RainstickSuppressionReason.DISPLAY_ASLEEP: "the display is asleep",
    RainstickSuppressionReason.SURFACE_HIDDEN: "the surface is hidden",
    RainstickSuppressionReason.LOW_POWER: "Low Power Mode is active",
    RainstickSuppressionReason.THERMAL_SERIOUS: "thermal pressure is serious",
    RainstickSuppressionReason.THERMAL_CRITICAL: "thermal pressure is critical",
}


def plan_rainstick_idle(
    *,
    preference_enabled: object = False,
    higher_priority_signal_active: object = False,
    dnd_active: object = False,
    night_policy_allows_idle: object = True,
    surface_visible: object = True,
    display_asleep: object = False,
    low_power: object = False,
    thermal: object = RainstickThermalState.NOMINAL,
    reduce_motion: object = False,
    surface_pixel_count: object = DEFAULT_SURFACE_PIXEL_COUNT,
) -> RainstickIdlePlan:
    """Plan Rainstick Idle from already-observed policy and machine facts.

    The disabled default preserves the opt-in product contract.  Night policy
    is an admission fact rather than an inference from local time, allowing an
    overnight scene to opt in while a stricter sleep-oriented scene remains
    dark.  All suppressing facts are retained in stable priority order.
    """

    enabled = _boolean(preference_enabled, "preference_enabled")
    higher_priority = _boolean(
        higher_priority_signal_active,
        "higher_priority_signal_active",
    )
    dnd = _boolean(dnd_active, "dnd_active")
    night_allowed = _boolean(
        night_policy_allows_idle,
        "night_policy_allows_idle",
    )
    visible = _boolean(surface_visible, "surface_visible")
    asleep = _boolean(display_asleep, "display_asleep")
    power_constrained = _boolean(low_power, "low_power")
    reduced = _boolean(reduce_motion, "reduce_motion")
    thermal_state = _thermal_state(thermal)
    pixel_count = _surface_pixel_count(surface_pixel_count)

    reasons: list[RainstickSuppressionReason] = []
    if not enabled:
        reasons.append(RainstickSuppressionReason.DISABLED_PREFERENCE)
    if higher_priority:
        reasons.append(RainstickSuppressionReason.HIGHER_PRIORITY_SIGNAL)
    if dnd:
        reasons.append(RainstickSuppressionReason.DND)
    if not night_allowed:
        reasons.append(RainstickSuppressionReason.NIGHT_POLICY)
    if asleep:
        reasons.append(RainstickSuppressionReason.DISPLAY_ASLEEP)
    if not visible:
        reasons.append(RainstickSuppressionReason.SURFACE_HIDDEN)
    if power_constrained:
        reasons.append(RainstickSuppressionReason.LOW_POWER)
    thermal_reason = {
        RainstickThermalState.SERIOUS: RainstickSuppressionReason.THERMAL_SERIOUS,
        RainstickThermalState.CRITICAL: RainstickSuppressionReason.THERMAL_CRITICAL,
    }.get(thermal_state)
    if thermal_reason is not None:
        reasons.append(thermal_reason)

    if reasons:
        explanation = "; ".join(_SUPPRESSION_LABELS[reason] for reason in reasons)
        return RainstickIdlePlan(
            disposition=RainstickDisposition.SUPPRESS,
            cadence=None,
            geometry=None,
            suppression_reasons=tuple(reasons),
            accessibility_text=(
                f"Rainstick Idle is off because {explanation}. "
                f"{RAINSTICK_ACCESSIBILITY_DISCLOSURE}"
            ),
        )

    geometry = RainstickGeometry(
        surface_pixel_count=pixel_count,
        lit_pixel_count=RAINSTICK_LIT_PIXEL_COUNT,
        path_start_index=0,
        path_end_index=pixel_count - 1,
        step_delta=1,
        wraps=True,
        relative_luminance=RAINSTICK_RELATIVE_LUMINANCE,
        static_index=pixel_count // 2 if reduced else None,
    )
    if reduced:
        return RainstickIdlePlan(
            disposition=RainstickDisposition.STATIC,
            cadence=RainstickCadence(
                moves=False,
                step_interval_seconds=None,
                step_frequency_hz=0.0,
            ),
            geometry=geometry,
            suppression_reasons=(),
            accessibility_text=(
                "Rainstick Idle is on as one dim stationary pixel because "
                f"Reduce Motion is enabled. {RAINSTICK_ACCESSIBILITY_DISCLOSURE}"
            ),
        )

    return RainstickIdlePlan(
        disposition=RainstickDisposition.MOVE,
        cadence=RainstickCadence(
            moves=True,
            step_interval_seconds=RAINSTICK_STEP_INTERVAL_SECONDS,
            step_frequency_hz=RAINSTICK_STEP_FREQUENCY_HZ,
        ),
        geometry=geometry,
        suppression_reasons=(),
        accessibility_text=(
            "Rainstick Idle is on. One dim pixel advances every 30 seconds to "
            f"show that JR Bar is alive and watching. {RAINSTICK_ACCESSIBILITY_DISCLOSURE}"
        ),
    )


def _boolean(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise RainstickIdleError(f"{field} must be bool")
    return value


def _thermal_state(value: object) -> RainstickThermalState:
    if isinstance(value, RainstickThermalState):
        return value
    if type(value) is not str:
        raise RainstickIdleError("thermal must be a RainstickThermalState or name")
    try:
        return RainstickThermalState(value.strip().lower())
    except ValueError as error:
        raise RainstickIdleError(
            "thermal must be nominal, fair, serious, or critical"
        ) from error


def _surface_pixel_count(value: object) -> int:
    if type(value) is not int or not 2 <= value <= MAX_SURFACE_PIXEL_COUNT:
        raise RainstickIdleError(
            "surface_pixel_count must be an integer from 2 through "
            f"{MAX_SURFACE_PIXEL_COUNT}"
        )
    return value


__all__ = [
    "DEFAULT_SURFACE_PIXEL_COUNT",
    "MAX_SURFACE_PIXEL_COUNT",
    "RAINSTICK_ACCESSIBILITY_DISCLOSURE",
    "RAINSTICK_LIT_PIXEL_COUNT",
    "RAINSTICK_RELATIVE_LUMINANCE",
    "RAINSTICK_STEP_FREQUENCY_HZ",
    "RAINSTICK_STEP_INTERVAL_SECONDS",
    "RainstickCadence",
    "RainstickDisposition",
    "RainstickGeometry",
    "RainstickIdleError",
    "RainstickIdlePlan",
    "RainstickSuppressionReason",
    "RainstickThermalState",
    "plan_rainstick_idle",
]
