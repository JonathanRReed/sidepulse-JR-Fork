"""Pure planning for a content-free turn-age ember.

The ember answers one narrow question: how long the current turn has been
open.  Its four public age bands are deliberately broad and disclosed in the
label presented to the user.  Increasing warmth is not a completion meter and
must never be described as progress, productivity, effort, or difficulty.

This module owns no clock and performs no AppKit or device work.  Callers pass
an elapsed duration and the current render constraints, then compile the
immutable plan for a status item, Screen Bar, or physical light surface.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Final


class TurnLengthEmberError(ValueError):
    """Raised when a caller supplies a malformed planner input."""


class TurnAgeBucket(str, Enum):
    """The complete, public bucket vocabulary for elapsed turn age."""

    UNDER_TWO_MINUTES = "under_two_minutes"
    TWO_TO_TEN_MINUTES = "two_to_ten_minutes"
    TEN_TO_THIRTY_MINUTES = "ten_to_thirty_minutes"
    THIRTY_MINUTES_OR_MORE = "thirty_minutes_or_more"


class EmberMotion(str, Enum):
    """Renderer motion requested by a turn-length plan."""

    HIDDEN = "hidden"
    STEADY = "steady"
    BREATHE = "breathe"


class EmberDegradation(str, Enum):
    """A machine-readable explanation for constrained output."""

    REDUCE_MOTION = "reduce_motion"
    LOW_POWER = "low_power"
    THERMAL_FAIR = "thermal_fair"
    THERMAL_SERIOUS = "thermal_serious"
    THERMAL_CRITICAL = "thermal_critical"


class ThermalState(str, Enum):
    """The bounded thermal vocabulary accepted by the pure planner."""

    NOMINAL = "nominal"
    FAIR = "fair"
    SERIOUS = "serious"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class TurnAgeBand:
    """One disclosed half-open elapsed-time band and its base appearance."""

    bucket: TurnAgeBucket
    minimum_seconds: float
    maximum_seconds: float | None
    label: str
    saturation: float
    luminance: float

    def contains(self, elapsed_seconds: float) -> bool:
        return elapsed_seconds >= self.minimum_seconds and (
            self.maximum_seconds is None or elapsed_seconds < self.maximum_seconds
        )


TURN_AGE_BANDS: Final[tuple[TurnAgeBand, ...]] = (
    TurnAgeBand(
        bucket=TurnAgeBucket.UNDER_TWO_MINUTES,
        minimum_seconds=0.0,
        maximum_seconds=120.0,
        label="Under 2 minutes",
        saturation=0.50,
        luminance=0.28,
    ),
    TurnAgeBand(
        bucket=TurnAgeBucket.TWO_TO_TEN_MINUTES,
        minimum_seconds=120.0,
        maximum_seconds=600.0,
        label="2 to under 10 minutes",
        saturation=0.58,
        luminance=0.34,
    ),
    TurnAgeBand(
        bucket=TurnAgeBucket.TEN_TO_THIRTY_MINUTES,
        minimum_seconds=600.0,
        maximum_seconds=1_800.0,
        label="10 to under 30 minutes",
        saturation=0.68,
        luminance=0.41,
    ),
    TurnAgeBand(
        bucket=TurnAgeBucket.THIRTY_MINUTES_OR_MORE,
        minimum_seconds=1_800.0,
        maximum_seconds=None,
        label="30 minutes or more",
        saturation=0.78,
        luminance=0.48,
    ),
)

DEFAULT_BREATHE_PERIOD_SECONDS: Final = 6.0
FAIR_THERMAL_BREATHE_PERIOD_SECONDS: Final = 10.0
SEMANTIC_DISCLOSURE: Final = (
    "Elapsed time only; it does not indicate progress, productivity, or difficulty."
)


@dataclass(frozen=True, slots=True)
class TurnLengthEmberPlan:
    """A bounded, renderer-shaped projection for one current turn.

    ``saturation`` and ``luminance`` are relative values in ``[0, 1]``.
    ``breathe_period_seconds`` is present only when ``motion`` is ``BREATHE``.
    A hidden plan uses zero light values so a renderer cannot accidentally
    preserve a stale ember while ignoring ``visible``.
    """

    visible: bool
    bucket: TurnAgeBucket | None
    minimum_seconds: float | None
    maximum_seconds: float | None
    age_label: str
    accessibility_text: str
    saturation: float
    luminance: float
    motion: EmberMotion
    breathe_period_seconds: float | None
    degradation: tuple[EmberDegradation, ...] = ()
    semantic_disclosure: str = SEMANTIC_DISCLOSURE

    @property
    def animated(self) -> bool:
        return self.visible and self.motion is EmberMotion.BREATHE


def age_band_for_elapsed(elapsed_seconds: object) -> TurnAgeBand:
    """Return the disclosed band for a finite, non-negative duration."""

    elapsed = _elapsed_seconds(elapsed_seconds)
    for band in TURN_AGE_BANDS:
        if band.contains(elapsed):
            return band
    raise AssertionError("TURN_AGE_BANDS must cover every non-negative duration")


def plan_turn_length_ember(
    *,
    elapsed_seconds: object,
    turn_active: object = True,
    surface_visible: object = True,
    reduce_motion: object = False,
    low_power: object = False,
    thermal: object = ThermalState.NOMINAL,
) -> TurnLengthEmberPlan:
    """Plan a truthful ember without reading time or machine state.

    The caller remains the authority for whether a turn is active and whether
    its surface is visible.  Critical thermal pressure suppresses the ember.
    Serious pressure and Low Power Mode retain the age label as a steady,
    lower-energy light.  Reduce Motion removes the breathe without changing
    the bucket or semantic disclosure.
    """

    elapsed = _elapsed_seconds(elapsed_seconds)
    active = _boolean(turn_active, "turn_active")
    surface = _boolean(surface_visible, "surface_visible")
    reduced = _boolean(reduce_motion, "reduce_motion")
    low_energy = _boolean(low_power, "low_power")
    thermal_state = _thermal_state(thermal)
    band = age_band_for_elapsed(elapsed)

    degradation: list[EmberDegradation] = []
    if reduced:
        degradation.append(EmberDegradation.REDUCE_MOTION)
    if low_energy:
        degradation.append(EmberDegradation.LOW_POWER)
    thermal_degradation = {
        ThermalState.FAIR: EmberDegradation.THERMAL_FAIR,
        ThermalState.SERIOUS: EmberDegradation.THERMAL_SERIOUS,
        ThermalState.CRITICAL: EmberDegradation.THERMAL_CRITICAL,
    }.get(thermal_state)
    if thermal_degradation is not None:
        degradation.append(thermal_degradation)

    if not active:
        return _hidden_plan(
            band,
            "Turn-length ember hidden because no turn is active.",
            degradation,
        )
    if not surface:
        return _hidden_plan(
            band,
            "Turn-length ember hidden because its surface is not visible.",
            degradation,
        )
    if thermal_state is ThermalState.CRITICAL:
        return _hidden_plan(
            band,
            "Turn-length ember hidden during critical thermal pressure.",
            degradation,
        )

    saturation = band.saturation
    luminance = band.luminance
    motion = EmberMotion.BREATHE
    period = DEFAULT_BREATHE_PERIOD_SECONDS

    if thermal_state is ThermalState.FAIR:
        saturation = min(saturation, 0.70)
        luminance = min(luminance, 0.38)
        period = FAIR_THERMAL_BREATHE_PERIOD_SECONDS
    if low_energy:
        saturation = min(saturation, 0.64)
        luminance = min(luminance, 0.30)
        motion = EmberMotion.STEADY
        period = None
    if thermal_state is ThermalState.SERIOUS:
        saturation = min(saturation, 0.55)
        luminance = min(luminance, 0.22)
        motion = EmberMotion.STEADY
        period = None
    if reduced:
        motion = EmberMotion.STEADY
        period = None

    return TurnLengthEmberPlan(
        visible=True,
        bucket=band.bucket,
        minimum_seconds=band.minimum_seconds,
        maximum_seconds=band.maximum_seconds,
        age_label=band.label,
        accessibility_text=(
            f"Turn age: {band.label}. Ambient ember. {SEMANTIC_DISCLOSURE}"
        ),
        saturation=saturation,
        luminance=luminance,
        motion=motion,
        breathe_period_seconds=period,
        degradation=tuple(degradation),
    )


def _hidden_plan(
    band: TurnAgeBand,
    accessibility_text: str,
    degradation: list[EmberDegradation],
) -> TurnLengthEmberPlan:
    return TurnLengthEmberPlan(
        visible=False,
        bucket=band.bucket,
        minimum_seconds=band.minimum_seconds,
        maximum_seconds=band.maximum_seconds,
        age_label=band.label,
        accessibility_text=f"{accessibility_text} {SEMANTIC_DISCLOSURE}",
        saturation=0.0,
        luminance=0.0,
        motion=EmberMotion.HIDDEN,
        breathe_period_seconds=None,
        degradation=tuple(degradation),
    )


def _elapsed_seconds(value: object) -> float:
    if type(value) not in {int, float}:
        raise TurnLengthEmberError("elapsed_seconds must be a finite number")
    elapsed = float(value)
    if not math.isfinite(elapsed) or elapsed < 0.0:
        raise TurnLengthEmberError(
            "elapsed_seconds must be a finite, non-negative number"
        )
    return elapsed


def _boolean(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise TurnLengthEmberError(f"{field} must be bool")
    return value


def _thermal_state(value: object) -> ThermalState:
    if isinstance(value, ThermalState):
        return value
    if type(value) is not str:
        raise TurnLengthEmberError("thermal must be a ThermalState or thermal name")
    try:
        return ThermalState(value.strip().lower())
    except ValueError as error:
        raise TurnLengthEmberError("thermal must be nominal, fair, serious, or critical") from error


__all__ = [
    "DEFAULT_BREATHE_PERIOD_SECONDS",
    "FAIR_THERMAL_BREATHE_PERIOD_SECONDS",
    "SEMANTIC_DISCLOSURE",
    "TURN_AGE_BANDS",
    "EmberDegradation",
    "EmberMotion",
    "ThermalState",
    "TurnAgeBand",
    "TurnAgeBucket",
    "TurnLengthEmberError",
    "TurnLengthEmberPlan",
    "age_band_for_elapsed",
    "plan_turn_length_ember",
]
