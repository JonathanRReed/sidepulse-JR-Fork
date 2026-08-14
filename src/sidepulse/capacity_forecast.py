"""Pure, bounded internal capacity pace and exhaustion diagnostics.

The records in this module are not presentation authority. They consume only
already-normalized capacity facts and metadata history, refuse ambiguous
identity or reset truth, and never produce a point exhaustion estimate.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise
from typing import Final

from .capacity_history import (
    MAX_CAPACITY_HISTORY_SAMPLES,
    MAX_WINDOW_MINUTES,
    CapacityHistorySample,
)
from .capacity_types import (
    ForecastConfidence,
    ObservationState,
    QuotaHorizon,
    QuotaLaneObservation,
    ResetState,
    SampleDisposition,
    SourceHealthKind,
)

MINIMUM_CYCLE_ELAPSED_FRACTION: Final = 0.03
MINIMUM_SLOPE_COVERAGE_FRACTION: Final = 0.03
MINIMUM_SLOPE_INTERVAL_SECONDS: Final = 60.0
MAX_SLOPE_INTERVAL_SECONDS: Final = 7.0 * 24.0 * 60.0 * 60.0
MAX_CURRENT_OBSERVATION_AGE_SECONDS: Final = 300.0
MAX_FORECAST_EPOCH_SECONDS: Final = 32_503_680_000.0
MINIMUM_VALID_SLOPES: Final = 3
MINIMUM_COMPLETE_CYCLES: Final = 5
BOUNDARY_COVERAGE_FRACTION: Final = 0.10
MAX_BURN_RATE_PER_SECOND: Final = 100.0 / MINIMUM_SLOPE_INTERVAL_SECONDS
_COMPARISON_EPSILON: Final = 1e-9


class ForecastRefusalCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    INVALID_CLOCK = "invalid_clock"
    HISTORY_CONSENT_REQUIRED = "history_consent_required"
    HISTORY_TOO_LARGE = "history_too_large"
    NO_ACCOUNT_DISCRIMINATOR = "no_account_discriminator"
    IDENTITY_CHANGED = "identity_changed"
    HISTORY_OUT_OF_ORDER = "history_out_of_order"
    DUPLICATE_TIMESTAMP_CONFLICT = "duplicate_timestamp_conflict"
    CROSS_LANE_HISTORY = "cross_lane_history"
    CROSS_ACCOUNT_HISTORY = "cross_account_history"
    NONMONOTONIC_USAGE = "nonmonotonic_usage"
    INTERVAL_UNBOUNDED = "interval_unbounded"
    INSUFFICIENT_CYCLE_ELAPSED = "insufficient_cycle_elapsed"
    INSUFFICIENT_SLOPES = "insufficient_slopes"
    INSUFFICIENT_SLOPE_COVERAGE = "insufficient_slope_coverage"
    SOURCE_PARTIAL = "source_partial"
    SOURCE_STALE = "source_stale"
    SOURCE_UNAVAILABLE = "source_unavailable"
    RESET_UNKNOWN = "reset_unknown"
    RESET_DISPUTED = "reset_disputed"
    RESET_NOT_FUTURE = "reset_not_future"
    RESET_UNSTABLE = "reset_unstable"
    NO_POSITIVE_BURN = "no_positive_burn"
    RUNWAY_UNBOUNDED = "runway_unbounded"
    EXHAUSTION_NOT_BEFORE_RESET = "exhaustion_not_before_reset"
    AUTHORITY_MISSING = "authority_missing"
    AUTHORITY_WITHHELD = "authority_withheld"
    RELEASE_AUTHORITY_REVOKED = "release_authority_revoked"
    AUTHORITY_EXPIRED = "authority_expired"
    AUTHORITY_NOT_YET_VALID = "authority_not_yet_valid"
    AUTHORITY_MISMATCHED = "authority_mismatched"
    CALIBRATION_SAMPLE_MISMATCH = "calibration_sample_mismatch"
    CALIBRATION_INSUFFICIENT = "calibration_insufficient"
    BASELINE_NOT_BEATEN = "baseline_not_beaten"
    FALSE_WARNING_REGRESSED = "false_warning_regressed"
    MISS_RATE_REGRESSED = "miss_rate_regressed"
    FORECAST_UNAVAILABLE = "forecast_unavailable"


class PaceSignal(str, Enum):
    IDLE = "idle"
    DECLINING = "declining"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class ExhaustionRunway:
    earliest_exhaustion_epoch: float
    latest_exhaustion_epoch: float
    reset_epoch: float

    def __post_init__(self) -> None:
        values = (
            self.earliest_exhaustion_epoch,
            self.latest_exhaustion_epoch,
            self.reset_epoch,
        )
        if not all(_valid_timestamp(value) for value in values):
            raise ValueError("invalid exhaustion runway")
        if not (self.earliest_exhaustion_epoch <= self.latest_exhaustion_epoch <= self.reset_epoch):
            raise ValueError("invalid exhaustion runway")
        for name in (
            "earliest_exhaustion_epoch",
            "latest_exhaustion_epoch",
            "reset_epoch",
        ):
            object.__setattr__(self, name, float(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class ForecastDiagnostic:
    confidence: ForecastConfidence
    refusal_code: ForecastRefusalCode | None
    pace_signal: PaceSignal | None
    runway: ExhaustionRunway | None
    horizon: QuotaHorizon | None

    def __post_init__(self) -> None:
        if not (
            type(self.confidence) is ForecastConfidence
            and (self.refusal_code is None or type(self.refusal_code) is ForecastRefusalCode)
            and (self.pace_signal is None or type(self.pace_signal) is PaceSignal)
            and (self.runway is None or type(self.runway) is ExhaustionRunway)
            and (self.horizon is None or type(self.horizon) is QuotaHorizon)
        ):
            raise ValueError("invalid forecast diagnostic")
        if self.confidence is ForecastConfidence.UNAVAILABLE and (
            self.refusal_code is None
            or self.pace_signal is not None
            or self.runway is not None
            or self.horizon is not None
        ):
            raise ValueError("unavailable forecast cannot carry diagnostics")
        if self.confidence is not ForecastConfidence.UNAVAILABLE:
            if self.pace_signal is None or self.horizon is None:
                raise ValueError("available diagnostic lacks typed scope")
            if self.runway is not None and (
                self.refusal_code is not None or self.confidence is ForecastConfidence.LOW_LINEAR
            ):
                raise ValueError("invalid numeric forecast authority")


@dataclass(frozen=True, slots=True)
class _Point:
    observed_at: float
    remaining: float
    reset_epoch: float


def _finite_number(value: object) -> float | None:
    if type(value) not in {int, float}:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _valid_timestamp(value: object) -> bool:
    number = _finite_number(value)
    return number is not None and 0.0 <= number <= MAX_FORECAST_EPOCH_SECONDS


def _unavailable(code: ForecastRefusalCode) -> ForecastDiagnostic:
    return ForecastDiagnostic(ForecastConfidence.UNAVAILABLE, code, None, None, None)


def _pace_signal(remaining: float, slopes: tuple[float, ...] = ()) -> PaceSignal:
    if remaining == 0.0:
        return PaceSignal.EXHAUSTED
    if not slopes or max(slopes, default=0.0) <= _COMPARISON_EPSILON:
        return PaceSignal.IDLE if remaining >= 100.0 else PaceSignal.DECLINING
    return PaceSignal.DECLINING


def _eligible_history_point(sample: CapacityHistorySample) -> _Point | None:
    if (
        sample.disposition is not SampleDisposition.ACCEPTED
        or sample.source_health is not SourceHealthKind.HEALTHY
        or sample.reset_epoch is None
    ):
        return None
    return _Point(sample.observed_at, sample.remaining, sample.reset_epoch)


def _validated_history(
    current: QuotaLaneObservation,
    history: tuple[CapacityHistorySample, ...],
) -> ForecastRefusalCode | None:
    previous_at: float | None = None
    for sample in history:
        if not _valid_timestamp(sample.observed_at):
            return ForecastRefusalCode.INVALID_INPUT
        if sample.lane_key != current.key:
            return ForecastRefusalCode.CROSS_LANE_HISTORY
        if sample.account_discriminator != current.account_discriminator:
            return ForecastRefusalCode.CROSS_ACCOUNT_HISTORY
        if previous_at is not None and sample.observed_at < previous_at:
            return ForecastRefusalCode.HISTORY_OUT_OF_ORDER
        if (
            sample.disposition is SampleDisposition.ACCEPTED
            and sample.source_health is SourceHealthKind.HEALTHY
            and sample.reset_epoch == current.reset.reset_epoch
            and sample.window_minutes != current.reset.window_minutes
        ):
            return ForecastRefusalCode.RESET_UNSTABLE
        previous_at = sample.observed_at
    if previous_at is not None and current.observed_at < previous_at:
        return ForecastRefusalCode.HISTORY_OUT_OF_ORDER
    return None


def _current_cycle_points(
    current: QuotaLaneObservation,
    history: tuple[CapacityHistorySample, ...],
) -> tuple[tuple[_Point, ...] | None, ForecastRefusalCode | None]:
    reset_epoch = current.reset.reset_epoch
    remaining = current.value.remaining
    if reset_epoch is None or remaining is None:
        return None, ForecastRefusalCode.INVALID_INPUT

    points = [
        point
        for sample in history
        if (point := _eligible_history_point(sample)) is not None and point.reset_epoch == reset_epoch
    ]
    points.append(_Point(current.observed_at, remaining, reset_epoch))

    unique: list[_Point] = []
    for point in points:
        if unique and point.observed_at == unique[-1].observed_at:
            if abs(point.remaining - unique[-1].remaining) > _COMPARISON_EPSILON:
                return None, ForecastRefusalCode.DUPLICATE_TIMESTAMP_CONFLICT
            continue
        unique.append(point)
    return tuple(unique), None


def _valid_slopes(
    points: tuple[_Point, ...],
) -> tuple[tuple[float, ...] | None, ForecastRefusalCode | None]:
    slopes: list[float] = []
    for previous, current in pairwise(points):
        interval = current.observed_at - previous.observed_at
        if interval > MAX_SLOPE_INTERVAL_SECONDS:
            return None, ForecastRefusalCode.INTERVAL_UNBOUNDED
        if interval < MINIMUM_SLOPE_INTERVAL_SECONDS:
            continue
        if current.remaining > previous.remaining + _COMPARISON_EPSILON:
            return None, ForecastRefusalCode.NONMONOTONIC_USAGE
        burn_rate = (previous.remaining - current.remaining) / interval
        if not math.isfinite(burn_rate) or not 0.0 <= burn_rate <= MAX_BURN_RATE_PER_SECOND:
            return None, ForecastRefusalCode.INVALID_INPUT
        slopes.append(burn_rate)
    return tuple(slopes), None


def _robust_rate_bounds(slopes: tuple[float, ...]) -> tuple[float, float]:
    center = statistics.median(slopes)
    absolute_deviations = tuple(abs(slope - center) for slope in slopes)
    deviation = statistics.median(absolute_deviations)
    lower = max(0.0, center - 2.0 * deviation)
    upper = min(MAX_BURN_RATE_PER_SECOND, center + 2.0 * deviation)
    return lower, upper


def _complete_cycle_count(
    history: tuple[CapacityHistorySample, ...],
    *,
    current_reset_epoch: float,
    current_window_minutes: float,
) -> int:
    grouped: dict[tuple[float, float], list[CapacityHistorySample]] = {}
    for sample in history:
        if (
            sample.disposition is SampleDisposition.ACCEPTED
            and sample.source_health is SourceHealthKind.HEALTHY
            and sample.reset_epoch is not None
            and sample.window_minutes is not None
            and sample.reset_epoch < current_reset_epoch
            and sample.window_minutes == current_window_minutes
        ):
            grouped.setdefault((sample.reset_epoch, sample.window_minutes), []).append(sample)

    complete = 0
    for (reset_epoch, window_minutes), samples in grouped.items():
        duration = window_minutes * 60.0
        start = reset_epoch - duration
        if start < 0.0:
            continue
        ordered = sorted(samples, key=lambda sample: sample.observed_at)
        boundary = duration * BOUNDARY_COVERAGE_FRACTION
        if ordered[0].observed_at <= start + boundary and ordered[-1].observed_at >= reset_epoch - boundary:
            complete += 1
    return complete


def analyze_capacity_forecast(
    current: QuotaLaneObservation,
    history: tuple[CapacityHistorySample, ...],
    *,
    now: object,
    history_consent: bool,
    continuity_disposition: SampleDisposition,
) -> ForecastDiagnostic:
    """Evaluate one internal diagnostic without I/O or release authority."""
    current_time = _finite_number(now)
    if current_time is None or current_time < 0.0 or current_time > MAX_FORECAST_EPOCH_SECONDS:
        return _unavailable(ForecastRefusalCode.INVALID_CLOCK)
    if not (
        type(current) is QuotaLaneObservation
        and type(history) is tuple
        and all(type(sample) is CapacityHistorySample for sample in history)
        and type(history_consent) is bool
        and type(continuity_disposition) is SampleDisposition
    ):
        return _unavailable(ForecastRefusalCode.INVALID_INPUT)
    if len(history) > MAX_CAPACITY_HISTORY_SAMPLES:
        return _unavailable(ForecastRefusalCode.HISTORY_TOO_LARGE)
    if not history_consent:
        return _unavailable(ForecastRefusalCode.HISTORY_CONSENT_REQUIRED)
    if current.observed_at > current_time:
        return _unavailable(ForecastRefusalCode.INVALID_CLOCK)
    if current_time - current.observed_at > MAX_CURRENT_OBSERVATION_AGE_SECONDS:
        return _unavailable(ForecastRefusalCode.SOURCE_STALE)
    if current.account_discriminator is None:
        return _unavailable(ForecastRefusalCode.NO_ACCOUNT_DISCRIMINATOR)
    if continuity_disposition is not SampleDisposition.ACCEPTED:
        if continuity_disposition is SampleDisposition.RESET_DISPUTED:
            return _unavailable(ForecastRefusalCode.RESET_DISPUTED)
        if continuity_disposition is SampleDisposition.SOURCE_STALE:
            return _unavailable(ForecastRefusalCode.SOURCE_STALE)
        if continuity_disposition is SampleDisposition.OUT_OF_ORDER:
            return _unavailable(ForecastRefusalCode.HISTORY_OUT_OF_ORDER)
        return _unavailable(ForecastRefusalCode.IDENTITY_CHANGED)

    value = current.value
    health = current.source_health.kind
    if value.state is ObservationState.PARTIAL or health is SourceHealthKind.PARTIAL:
        return _unavailable(ForecastRefusalCode.SOURCE_PARTIAL)
    if (
        value.state
        in {
            ObservationState.STALE,
            ObservationState.LAST_KNOWN_GOOD,
        }
        or health is SourceHealthKind.STALE
    ):
        return _unavailable(ForecastRefusalCode.SOURCE_STALE)
    if value.state not in {ObservationState.OBSERVED, ObservationState.OBSERVED_ZERO}:
        return _unavailable(ForecastRefusalCode.SOURCE_UNAVAILABLE)
    if health is not SourceHealthKind.HEALTHY or value.remaining is None:
        return _unavailable(ForecastRefusalCode.SOURCE_UNAVAILABLE)
    if current.source_health.observed_at != current.observed_at:
        return _unavailable(ForecastRefusalCode.SOURCE_STALE)

    reset = current.reset
    if reset.state is ResetState.DISPUTED:
        return _unavailable(ForecastRefusalCode.RESET_DISPUTED)
    if reset.state in {ResetState.UNKNOWN, ResetState.UNAVAILABLE, ResetState.STALE}:
        return _unavailable(ForecastRefusalCode.RESET_UNKNOWN)
    if reset.state is not ResetState.FUTURE or reset.reset_epoch is None:
        return _unavailable(ForecastRefusalCode.RESET_NOT_FUTURE)
    if reset.reset_epoch <= current_time:
        return _unavailable(ForecastRefusalCode.RESET_NOT_FUTURE)
    if (
        reset.window_minutes is None
        or reset.window_minutes > MAX_WINDOW_MINUTES
        or reset.observed_at != current.observed_at
        or not _valid_timestamp(reset.reset_epoch)
    ):
        return _unavailable(ForecastRefusalCode.RESET_UNSTABLE)
    duration = reset.window_minutes * 60.0
    cycle_start = reset.reset_epoch - duration
    elapsed = current.observed_at - cycle_start
    if cycle_start < 0.0 or elapsed < duration * MINIMUM_CYCLE_ELAPSED_FRACTION:
        return _unavailable(ForecastRefusalCode.INSUFFICIENT_CYCLE_ELAPSED)

    history_refusal = _validated_history(current, history)
    if history_refusal is not None:
        return _unavailable(history_refusal)
    points, point_refusal = _current_cycle_points(current, history)
    if point_refusal is not None or points is None:
        return _unavailable(point_refusal or ForecastRefusalCode.INVALID_INPUT)
    slopes, slope_refusal = _valid_slopes(points)
    if slope_refusal is not None or slopes is None:
        return _unavailable(slope_refusal or ForecastRefusalCode.INVALID_INPUT)

    pace = _pace_signal(value.remaining, slopes)
    if len(slopes) < MINIMUM_VALID_SLOPES:
        return ForecastDiagnostic(
            ForecastConfidence.LOW_LINEAR,
            ForecastRefusalCode.INSUFFICIENT_SLOPES,
            pace,
            None,
            current.horizon,
        )
    if points[-1].observed_at - points[0].observed_at < (duration * MINIMUM_SLOPE_COVERAGE_FRACTION):
        return ForecastDiagnostic(
            ForecastConfidence.LOW_LINEAR,
            ForecastRefusalCode.INSUFFICIENT_SLOPE_COVERAGE,
            pace,
            None,
            current.horizon,
        )

    confidence = (
        ForecastConfidence.HIGH_HISTORICAL
        if _complete_cycle_count(
            history,
            current_reset_epoch=reset.reset_epoch,
            current_window_minutes=reset.window_minutes,
        )
        >= MINIMUM_COMPLETE_CYCLES
        else ForecastConfidence.MEDIUM_OBSERVED
    )
    lower_rate, upper_rate = _robust_rate_bounds(slopes)
    if upper_rate <= _COMPARISON_EPSILON:
        return ForecastDiagnostic(
            confidence,
            ForecastRefusalCode.NO_POSITIVE_BURN,
            pace,
            None,
            current.horizon,
        )
    if lower_rate <= _COMPARISON_EPSILON:
        return ForecastDiagnostic(
            confidence,
            ForecastRefusalCode.RUNWAY_UNBOUNDED,
            pace,
            None,
            current.horizon,
        )

    if value.remaining == 0.0:
        earliest = latest = current_time
    else:
        earliest = current_time + value.remaining / upper_rate
        latest = current_time + value.remaining / lower_rate
    if not all(math.isfinite(value) for value in (earliest, latest)):
        return ForecastDiagnostic(
            confidence,
            ForecastRefusalCode.RUNWAY_UNBOUNDED,
            pace,
            None,
            current.horizon,
        )
    if earliest > reset.reset_epoch or latest > reset.reset_epoch:
        return ForecastDiagnostic(
            confidence,
            ForecastRefusalCode.EXHAUSTION_NOT_BEFORE_RESET,
            pace,
            None,
            current.horizon,
        )
    return ForecastDiagnostic(
        confidence,
        None,
        pace,
        ExhaustionRunway(earliest, latest, reset.reset_epoch),
        current.horizon,
    )


__all__ = [
    "BOUNDARY_COVERAGE_FRACTION",
    "MAX_CURRENT_OBSERVATION_AGE_SECONDS",
    "MAX_FORECAST_EPOCH_SECONDS",
    "MAX_SLOPE_INTERVAL_SECONDS",
    "MINIMUM_COMPLETE_CYCLES",
    "MINIMUM_CYCLE_ELAPSED_FRACTION",
    "MINIMUM_SLOPE_INTERVAL_SECONDS",
    "MINIMUM_VALID_SLOPES",
    "ExhaustionRunway",
    "ForecastDiagnostic",
    "ForecastRefusalCode",
    "PaceSignal",
    "analyze_capacity_forecast",
]
