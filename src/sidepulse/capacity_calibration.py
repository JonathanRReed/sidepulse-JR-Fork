"""Bounded rolling-origin calibration and explicit forecast release refusal."""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise
from typing import Final

from .capacity_forecast import (
    MAX_FORECAST_EPOCH_SECONDS,
    MAX_SLOPE_INTERVAL_SECONDS,
    MINIMUM_SLOPE_INTERVAL_SECONDS,
    ForecastDiagnostic,
    ForecastRefusalCode,
    analyze_capacity_forecast,
)
from .capacity_history import (
    MAX_WINDOW_MINUTES,
    CapacityHistorySample,
)
from .capacity_types import (
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ForecastConfidence,
    ForecastReleaseState,
    ObservationState,
    QuotaHorizon,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    ResetState,
    SampleDisposition,
    SourceHealthKind,
)

CALIBRATION_SCHEMA_VERSION: Final = 1
ROBUST_METHOD_VERSION: Final = "robust-runway-v1"
FORECAST_UNAVAILABLE_TEXT: Final = "Forecast unavailable"
MAX_CALIBRATION_CYCLES: Final = 64
MAX_SAMPLES_PER_CALIBRATION_CYCLE: Final = 512
MAX_CALIBRATION_ORIGINS: Final = 8_192
MAX_AUTHORITY_CLAIM_CLASSES: Final = 8
MAX_METHOD_VERSION_LENGTH: Final = 64
MAX_FALSE_WARNING_RATE: Final = 0.15
MAX_MISS_RATE: Final = 0.20
MAX_TIMING_ERROR_SECONDS: Final = 366.0 * 24.0 * 60.0 * 60.0
_METHOD_VERSION: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_OPAQUE_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]*\Z")


class ForecastIdentityClass(str, Enum):
    OPAQUE_ACCOUNT = "opaque_account"


class ForecastClaimClass(str, Enum):
    PACE_DIAGNOSTIC = "pace_diagnostic"
    EXHAUSTION_ENVELOPE = "exhaustion_envelope"
    EXHAUSTION_PROBABILITY = "exhaustion_probability"


@dataclass(frozen=True, slots=True)
class ForecastReleaseAuthority:
    method_version: str
    schema_version: int
    identity_class: ForecastIdentityClass
    horizon: QuotaHorizon
    permitted_claim_classes: tuple[ForecastClaimClass, ...]
    calibration_sample_min: int
    calibration_sample_max: int
    issued_at: float
    expires_at: float
    release_state: ForecastReleaseState

    def __post_init__(self) -> None:
        if not (
            _valid_method_version(self.method_version)
            and type(self.schema_version) is int
            and self.schema_version == CALIBRATION_SCHEMA_VERSION
            and type(self.identity_class) is ForecastIdentityClass
            and type(self.horizon) is QuotaHorizon
            and type(self.permitted_claim_classes) is tuple
            and len(self.permitted_claim_classes) <= MAX_AUTHORITY_CLAIM_CLASSES
            and all(type(claim_class) is ForecastClaimClass for claim_class in self.permitted_claim_classes)
            and len(self.permitted_claim_classes) == len(set(self.permitted_claim_classes))
            and type(self.calibration_sample_min) is int
            and type(self.calibration_sample_max) is int
            and 0 <= self.calibration_sample_min <= self.calibration_sample_max <= MAX_CALIBRATION_ORIGINS
            and _valid_timestamp(self.issued_at)
            and _valid_timestamp(self.expires_at)
            and type(self.release_state) is ForecastReleaseState
        ):
            raise ValueError("invalid forecast release authority")
        if self.release_state is ForecastReleaseState.WITHHELD:
            if not (
                self.method_version == "withheld"
                and self.horizon is QuotaHorizon.OTHER
                and not self.permitted_claim_classes
                and self.calibration_sample_min == 0
                and self.calibration_sample_max == 0
                and self.issued_at == 0.0
                and self.expires_at == 0.0
            ):
                raise ValueError("invalid withheld forecast authority")
        elif not (
            self.method_version != "withheld" and self.issued_at < self.expires_at and self.permitted_claim_classes
        ):
            raise ValueError("invalid active forecast authority")
        object.__setattr__(self, "issued_at", float(self.issued_at))
        object.__setattr__(self, "expires_at", float(self.expires_at))

    @classmethod
    def withheld(cls) -> ForecastReleaseAuthority:
        return cls(
            method_version="withheld",
            schema_version=CALIBRATION_SCHEMA_VERSION,
            identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
            horizon=QuotaHorizon.OTHER,
            permitted_claim_classes=(),
            calibration_sample_min=0,
            calibration_sample_max=0,
            issued_at=0.0,
            expires_at=0.0,
            release_state=ForecastReleaseState.WITHHELD,
        )


@dataclass(frozen=True, slots=True)
class CalibrationCycle:
    identity_class: ForecastIdentityClass
    horizon: QuotaHorizon
    lane_key: QuotaLaneKey
    account_discriminator: str
    reset_epoch: float
    window_minutes: float
    samples: tuple[CapacityHistorySample, ...]
    actual_exhaustion_epoch: float | None

    def __post_init__(self) -> None:
        if not (
            type(self.identity_class) is ForecastIdentityClass
            and type(self.horizon) is QuotaHorizon
            and type(self.lane_key) is QuotaLaneKey
            and _valid_opaque_identifier(self.account_discriminator)
            and _valid_timestamp(self.reset_epoch)
            and _valid_positive_number(self.window_minutes)
            and self.window_minutes <= MAX_WINDOW_MINUTES
            and type(self.samples) is tuple
            and len(self.samples) <= MAX_SAMPLES_PER_CALIBRATION_CYCLE
            and all(type(sample) is CapacityHistorySample for sample in self.samples)
            and (self.actual_exhaustion_epoch is None or _valid_timestamp(self.actual_exhaustion_epoch))
        ):
            raise ValueError("invalid calibration cycle")
        previous_at: float | None = None
        for sample in self.samples:
            if not (
                sample.lane_key == self.lane_key
                and sample.account_discriminator == self.account_discriminator
                and sample.reset_epoch == self.reset_epoch
                and sample.window_minutes == self.window_minutes
            ):
                raise ValueError("calibration cycle identity changed")
            if previous_at is not None and sample.observed_at < previous_at:
                raise ValueError("calibration samples out of order")
            previous_at = sample.observed_at
        observed_zero_epochs = tuple(
            sample.observed_at
            for sample in self.samples
            if sample.disposition is SampleDisposition.ACCEPTED
            and sample.source_health is SourceHealthKind.HEALTHY
            and sample.remaining == 0.0
        )
        if self.actual_exhaustion_epoch is not None:
            if not (
                self.actual_exhaustion_epoch <= self.reset_epoch
                and (not self.samples or self.actual_exhaustion_epoch >= self.samples[0].observed_at)
            ):
                raise ValueError("invalid actual exhaustion boundary")
            if self.actual_exhaustion_epoch not in observed_zero_epochs:
                raise ValueError("actual exhaustion lacks observed zero")
            object.__setattr__(
                self,
                "actual_exhaustion_epoch",
                float(self.actual_exhaustion_epoch),
            )
        elif observed_zero_epochs:
            raise ValueError("observed zero lacks actual exhaustion boundary")
        object.__setattr__(self, "reset_epoch", float(self.reset_epoch))
        object.__setattr__(self, "window_minutes", float(self.window_minutes))


@dataclass(frozen=True, slots=True)
class CalibrationScore:
    sample_count: int
    eligible_cycle_count: int
    mean_absolute_timing_error: float | None
    interval_coverage: float | None
    false_warning_rate: float
    miss_rate: float
    abstention_rate: float
    in_sample_mean_absolute_timing_error: float | None

    def __post_init__(self) -> None:
        if not (
            type(self.sample_count) is int
            and 0 <= self.sample_count <= MAX_CALIBRATION_ORIGINS
            and type(self.eligible_cycle_count) is int
            and 0
            <= self.eligible_cycle_count
            <= min(
                MAX_CALIBRATION_CYCLES,
                self.sample_count,
            )
            and _valid_optional_error(self.mean_absolute_timing_error)
            and _valid_optional_fraction(self.interval_coverage)
            and _valid_fraction(self.false_warning_rate)
            and _valid_fraction(self.miss_rate)
            and _valid_fraction(self.abstention_rate)
            and _valid_optional_error(self.in_sample_mean_absolute_timing_error)
        ):
            raise ValueError("invalid calibration score")
        for field_name in (
            "mean_absolute_timing_error",
            "interval_coverage",
            "false_warning_rate",
            "miss_rate",
            "abstention_rate",
            "in_sample_mean_absolute_timing_error",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, float(value))


@dataclass(frozen=True, slots=True)
class CalibrationComparison:
    identity_class: ForecastIdentityClass
    horizon: QuotaHorizon
    candidate: CalibrationScore
    baseline: CalibrationScore

    def __post_init__(self) -> None:
        if not (
            type(self.identity_class) is ForecastIdentityClass
            and type(self.horizon) is QuotaHorizon
            and type(self.candidate) is CalibrationScore
            and type(self.baseline) is CalibrationScore
            and self.candidate.sample_count == self.baseline.sample_count
            and self.candidate.eligible_cycle_count == self.baseline.eligible_cycle_count
        ):
            raise ValueError("invalid calibration comparison")


@dataclass(frozen=True, slots=True)
class CalibrationOriginAudit:
    identity_class: ForecastIdentityClass
    horizon: QuotaHorizon
    origin_at: float
    target_at: float

    def __post_init__(self) -> None:
        if not (
            type(self.identity_class) is ForecastIdentityClass
            and type(self.horizon) is QuotaHorizon
            and _valid_timestamp(self.origin_at)
            and _valid_timestamp(self.target_at)
            and self.origin_at < self.target_at
        ):
            raise ValueError("invalid rolling-origin audit")
        object.__setattr__(self, "origin_at", float(self.origin_at))
        object.__setattr__(self, "target_at", float(self.target_at))


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    schema_version: int
    method_version: str
    comparisons: tuple[CalibrationComparison, ...]
    origin_audits: tuple[CalibrationOriginAudit, ...]

    def __post_init__(self) -> None:
        if not (
            type(self.schema_version) is int
            and self.schema_version == CALIBRATION_SCHEMA_VERSION
            and _valid_method_version(self.method_version)
            and type(self.comparisons) is tuple
            and len(self.comparisons) <= len(ForecastIdentityClass) * len(QuotaHorizon)
            and all(type(item) is CalibrationComparison for item in self.comparisons)
            and type(self.origin_audits) is tuple
            and len(self.origin_audits) <= MAX_CALIBRATION_ORIGINS
            and all(type(item) is CalibrationOriginAudit for item in self.origin_audits)
        ):
            raise ValueError("invalid calibration report")
        keys = tuple((item.identity_class, item.horizon) for item in self.comparisons)
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate calibration comparison")


@dataclass(frozen=True, slots=True)
class ReleasedForecast:
    status_text: str | None
    refusal_code: ForecastRefusalCode | None
    earliest_exhaustion_epoch: float | None
    latest_exhaustion_epoch: float | None

    def __post_init__(self) -> None:
        unavailable = self.status_text == FORECAST_UNAVAILABLE_TEXT
        if unavailable:
            if not (
                type(self.refusal_code) is ForecastRefusalCode
                and self.earliest_exhaustion_epoch is None
                and self.latest_exhaustion_epoch is None
            ):
                raise ValueError("invalid unavailable forecast release")
            return
        if not (
            self.status_text is None
            and self.refusal_code is None
            and _valid_timestamp(self.earliest_exhaustion_epoch)
            and _valid_timestamp(self.latest_exhaustion_epoch)
            and self.earliest_exhaustion_epoch <= self.latest_exhaustion_epoch
        ):
            raise ValueError("invalid forecast release")
        object.__setattr__(
            self,
            "earliest_exhaustion_epoch",
            float(self.earliest_exhaustion_epoch),
        )
        object.__setattr__(
            self,
            "latest_exhaustion_epoch",
            float(self.latest_exhaustion_epoch),
        )


@dataclass(frozen=True, slots=True)
class _MethodOutcome:
    cycle_index: int
    actual_exhaustion: bool
    warning: bool
    timing_error: float | None
    covered: bool | None


def _finite_number(value: object) -> float | None:
    if type(value) not in {int, float}:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _valid_timestamp(value: object) -> bool:
    number = _finite_number(value)
    return number is not None and 0.0 <= number <= MAX_FORECAST_EPOCH_SECONDS


def _valid_positive_number(value: object) -> bool:
    number = _finite_number(value)
    return number is not None and number > 0.0


def _valid_fraction(value: object) -> bool:
    number = _finite_number(value)
    return number is not None and 0.0 <= number <= 1.0


def _valid_optional_fraction(value: object) -> bool:
    return value is None or _valid_fraction(value)


def _valid_optional_error(value: object) -> bool:
    number = _finite_number(value)
    return value is None or (number is not None and 0.0 <= number <= MAX_TIMING_ERROR_SECONDS)


def _valid_method_version(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_METHOD_VERSION_LENGTH
        and _METHOD_VERSION.fullmatch(value) is not None
    )


def _valid_opaque_identifier(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= 64 and _OPAQUE_IDENTIFIER.fullmatch(value) is not None


def forecast_release_authority_to_payload(
    authority: ForecastReleaseAuthority,
) -> dict[str, object]:
    if type(authority) is not ForecastReleaseAuthority:
        authority = ForecastReleaseAuthority.withheld()
    return {
        "method_version": authority.method_version,
        "schema_version": authority.schema_version,
        "identity_class": authority.identity_class.value,
        "horizon": authority.horizon.value,
        "permitted_claim_classes": [claim_class.value for claim_class in authority.permitted_claim_classes],
        "calibration_sample_min": authority.calibration_sample_min,
        "calibration_sample_max": authority.calibration_sample_max,
        "issued_at": authority.issued_at,
        "expires_at": authority.expires_at,
        "release_state": authority.release_state.value,
    }


def forecast_release_authority_from_payload(
    payload: object,
) -> ForecastReleaseAuthority:
    fields = {
        "calibration_sample_max",
        "calibration_sample_min",
        "expires_at",
        "horizon",
        "identity_class",
        "issued_at",
        "method_version",
        "permitted_claim_classes",
        "release_state",
        "schema_version",
    }
    if type(payload) is not dict or set(payload) != fields:
        return ForecastReleaseAuthority.withheld()
    claims = payload.get("permitted_claim_classes")
    if type(claims) is not list:
        return ForecastReleaseAuthority.withheld()
    try:
        return ForecastReleaseAuthority(
            method_version=payload["method_version"],
            schema_version=payload["schema_version"],
            identity_class=ForecastIdentityClass(payload["identity_class"]),
            horizon=QuotaHorizon(payload["horizon"]),
            permitted_claim_classes=tuple(ForecastClaimClass(value) for value in claims),
            calibration_sample_min=payload["calibration_sample_min"],
            calibration_sample_max=payload["calibration_sample_max"],
            issued_at=payload["issued_at"],
            expires_at=payload["expires_at"],
            release_state=ForecastReleaseState(payload["release_state"]),
        )
    except (TypeError, ValueError):
        return ForecastReleaseAuthority.withheld()


def naive_baseline_exhaustion(
    samples: tuple[CapacityHistorySample, ...],
    *,
    origin_at: float,
    remaining: float,
    reset_epoch: float,
) -> float | None:
    """Carry the most recent valid observed slope to the reset boundary."""
    remaining_value = _finite_number(remaining)
    if not (
        type(samples) is tuple
        and len(samples) <= MAX_SAMPLES_PER_CALIBRATION_CYCLE
        and all(type(sample) is CapacityHistorySample for sample in samples)
        and _valid_timestamp(origin_at)
        and remaining_value is not None
        and 0.0 <= remaining_value <= 100.0
        and _valid_timestamp(reset_epoch)
        and origin_at < reset_epoch
    ):
        return None
    if samples:
        lane_key = samples[0].lane_key
        account_discriminator = samples[0].account_discriminator
        previous_at: float | None = None
        for sample in samples:
            if not (
                sample.lane_key == lane_key
                and sample.account_discriminator == account_discriminator
                and sample.reset_epoch == reset_epoch
                and (previous_at is None or sample.observed_at >= previous_at)
            ):
                return None
            previous_at = sample.observed_at
    eligible = tuple(
        sample
        for sample in samples
        if sample.disposition is SampleDisposition.ACCEPTED
        and sample.source_health is SourceHealthKind.HEALTHY
        and sample.observed_at <= origin_at
    )
    for previous, current in reversed(tuple(pairwise(eligible))):
        if previous.reset_epoch != current.reset_epoch:
            continue
        interval = current.observed_at - previous.observed_at
        if not MINIMUM_SLOPE_INTERVAL_SECONDS <= interval <= MAX_SLOPE_INTERVAL_SECONDS:
            continue
        if current.remaining > previous.remaining:
            return None
        burn_rate = (previous.remaining - current.remaining) / interval
        if burn_rate <= 0.0 or not math.isfinite(burn_rate):
            return None
        prediction = float(origin_at) + remaining_value / burn_rate
        return prediction if math.isfinite(prediction) and prediction <= reset_epoch else None
    return None


def _current_observation(
    cycle: CalibrationCycle,
    sample: CapacityHistorySample,
) -> QuotaLaneObservation:
    state = ObservationState.OBSERVED_ZERO if sample.remaining == 0.0 else ObservationState.OBSERVED
    health = CapacitySourceHealth(
        source=cycle.lane_key.source,
        kind=SourceHealthKind.HEALTHY,
        observed_at=sample.observed_at,
        last_attempt_at=sample.observed_at,
        retry_at=None,
        reason_code=None,
        has_last_known_good=False,
    )
    return QuotaLaneObservation(
        key=cycle.lane_key,
        semantic_name="Calibration window",
        horizon=cycle.horizon,
        value=CapacityValue(
            CapacityUnit.PERCENT_REMAINING,
            sample.remaining,
            state,
        ),
        reset=ResetFact(
            ResetState.FUTURE,
            cycle.reset_epoch,
            cycle.window_minutes,
            sample.observed_at,
        ),
        observed_at=sample.observed_at,
        source_health=health,
        account_discriminator=cycle.account_discriminator,
    )


def _timing_error_for_interval(
    target: float,
    earliest: float,
    latest: float,
) -> float:
    if earliest <= target <= latest:
        return 0.0
    return earliest - target if target < earliest else target - latest


def _outcomes_for_cycle(
    cycle: CalibrationCycle,
    cycle_index: int,
) -> tuple[
    tuple[_MethodOutcome, ...],
    tuple[_MethodOutcome, ...],
    tuple[CalibrationOriginAudit, ...],
]:
    candidate_outcomes: list[_MethodOutcome] = []
    baseline_outcomes: list[_MethodOutcome] = []
    audits: list[CalibrationOriginAudit] = []
    target = cycle.actual_exhaustion_epoch or cycle.reset_epoch
    for index in range(3, len(cycle.samples)):
        current_sample = cycle.samples[index]
        origin = current_sample.observed_at
        if origin >= target:
            continue
        prefix = cycle.samples[: index + 1]
        diagnostic = analyze_capacity_forecast(
            _current_observation(cycle, current_sample),
            prefix[:-1],
            now=origin,
            history_consent=True,
            continuity_disposition=SampleDisposition.ACCEPTED,
        )
        runway = diagnostic.runway
        actual = cycle.actual_exhaustion_epoch is not None
        if runway is None:
            candidate_outcomes.append(_MethodOutcome(cycle_index, actual, False, None, None))
        elif actual:
            candidate_outcomes.append(
                _MethodOutcome(
                    cycle_index,
                    True,
                    True,
                    _timing_error_for_interval(
                        target,
                        runway.earliest_exhaustion_epoch,
                        runway.latest_exhaustion_epoch,
                    ),
                    runway.earliest_exhaustion_epoch <= target <= runway.latest_exhaustion_epoch,
                )
            )
        else:
            candidate_outcomes.append(_MethodOutcome(cycle_index, False, True, None, None))

        baseline_prediction = naive_baseline_exhaustion(
            prefix,
            origin_at=origin,
            remaining=current_sample.remaining,
            reset_epoch=cycle.reset_epoch,
        )
        if baseline_prediction is None:
            baseline_outcomes.append(_MethodOutcome(cycle_index, actual, False, None, None))
        elif actual:
            baseline_outcomes.append(
                _MethodOutcome(
                    cycle_index,
                    True,
                    True,
                    abs(baseline_prediction - target),
                    baseline_prediction == target,
                )
            )
        else:
            baseline_outcomes.append(_MethodOutcome(cycle_index, False, True, None, None))
        audits.append(
            CalibrationOriginAudit(
                cycle.identity_class,
                cycle.horizon,
                origin,
                target,
            )
        )
    return tuple(candidate_outcomes), tuple(baseline_outcomes), tuple(audits)


def _score_outcomes(outcomes: tuple[_MethodOutcome, ...]) -> CalibrationScore:
    sample_count = len(outcomes)
    eligible_cycles = len({outcome.cycle_index for outcome in outcomes})
    errors = tuple(outcome.timing_error for outcome in outcomes if outcome.timing_error is not None)
    covered = tuple(outcome.covered for outcome in outcomes if outcome.covered is not None)
    non_exhausting = tuple(outcome for outcome in outcomes if not outcome.actual_exhaustion)
    exhausting = tuple(outcome for outcome in outcomes if outcome.actual_exhaustion)
    return CalibrationScore(
        sample_count=sample_count,
        eligible_cycle_count=eligible_cycles,
        mean_absolute_timing_error=(statistics.fmean(errors) if errors else None),
        interval_coverage=(sum(bool(value) for value in covered) / len(covered) if covered else None),
        false_warning_rate=(
            sum(outcome.warning for outcome in non_exhausting) / len(non_exhausting) if non_exhausting else 0.0
        ),
        miss_rate=(sum(not outcome.warning for outcome in exhausting) / len(exhausting) if exhausting else 0.0),
        abstention_rate=(sum(not outcome.warning for outcome in outcomes) / sample_count if sample_count else 1.0),
        in_sample_mean_absolute_timing_error=None,
    )


def _horizon_order(horizon: QuotaHorizon) -> int:
    return {
        QuotaHorizon.SHORT: 0,
        QuotaHorizon.LONG: 1,
        QuotaHorizon.OTHER: 2,
    }[horizon]


def evaluate_forecast_calibration(
    cycles: tuple[CalibrationCycle, ...],
) -> CalibrationReport:
    """Evaluate the candidate and frozen baseline at strictly earlier origins."""
    if not (
        type(cycles) is tuple
        and len(cycles) <= MAX_CALIBRATION_CYCLES
        and all(type(cycle) is CalibrationCycle for cycle in cycles)
    ):
        raise ValueError("invalid calibration cycles")
    groups: dict[
        tuple[ForecastIdentityClass, QuotaHorizon],
        tuple[list[_MethodOutcome], list[_MethodOutcome]],
    ] = {}
    audits: list[CalibrationOriginAudit] = []
    for cycle_index, cycle in enumerate(cycles):
        candidate, baseline, cycle_audits = _outcomes_for_cycle(cycle, cycle_index)
        if len(audits) + len(cycle_audits) > MAX_CALIBRATION_ORIGINS:
            raise ValueError("too many calibration origins")
        group = groups.setdefault(
            (cycle.identity_class, cycle.horizon),
            ([], []),
        )
        group[0].extend(candidate)
        group[1].extend(baseline)
        audits.extend(cycle_audits)
    comparisons = tuple(
        CalibrationComparison(
            identity_class=identity_class,
            horizon=horizon,
            candidate=_score_outcomes(tuple(group[0])),
            baseline=_score_outcomes(tuple(group[1])),
        )
        for (identity_class, horizon), group in sorted(
            groups.items(),
            key=lambda item: (item[0][0].value, _horizon_order(item[0][1])),
        )
    )
    return CalibrationReport(
        schema_version=CALIBRATION_SCHEMA_VERSION,
        method_version=ROBUST_METHOD_VERSION,
        comparisons=comparisons,
        origin_audits=tuple(audits),
    )


def _unreleased(code: ForecastRefusalCode) -> ReleasedForecast:
    return ReleasedForecast(FORECAST_UNAVAILABLE_TEXT, code, None, None)


def apply_forecast_release(
    diagnostic: ForecastDiagnostic,
    authority: ForecastReleaseAuthority | None,
    report: CalibrationReport,
    *,
    identity_class: ForecastIdentityClass,
    horizon: QuotaHorizon,
    claim_class: ForecastClaimClass,
    now: object,
) -> ReleasedForecast:
    """Release only an explicitly scoped interval that beats the baseline."""
    current_time = _finite_number(now)
    if current_time is None or current_time < 0.0:
        return _unreleased(ForecastRefusalCode.INVALID_CLOCK)
    if authority is None:
        return _unreleased(ForecastRefusalCode.AUTHORITY_MISSING)
    if not (
        type(diagnostic) is ForecastDiagnostic
        and type(authority) is ForecastReleaseAuthority
        and type(report) is CalibrationReport
        and type(identity_class) is ForecastIdentityClass
        and type(horizon) is QuotaHorizon
        and type(claim_class) is ForecastClaimClass
    ):
        return _unreleased(ForecastRefusalCode.AUTHORITY_MISMATCHED)
    if authority.release_state is ForecastReleaseState.WITHHELD:
        return _unreleased(ForecastRefusalCode.AUTHORITY_WITHHELD)
    if authority.release_state is ForecastReleaseState.REVOKED:
        return _unreleased(ForecastRefusalCode.RELEASE_AUTHORITY_REVOKED)
    if current_time < authority.issued_at:
        return _unreleased(ForecastRefusalCode.AUTHORITY_NOT_YET_VALID)
    if current_time >= authority.expires_at:
        return _unreleased(ForecastRefusalCode.AUTHORITY_EXPIRED)
    if not (
        authority.method_version == report.method_version == ROBUST_METHOD_VERSION
        and authority.schema_version == report.schema_version == CALIBRATION_SCHEMA_VERSION
        and authority.identity_class is identity_class
        and authority.horizon is horizon
        and diagnostic.horizon is horizon
        and claim_class is ForecastClaimClass.EXHAUSTION_ENVELOPE
        and claim_class in authority.permitted_claim_classes
    ):
        return _unreleased(ForecastRefusalCode.AUTHORITY_MISMATCHED)
    comparison = next(
        (item for item in report.comparisons if item.identity_class is identity_class and item.horizon is horizon),
        None,
    )
    if comparison is None:
        return _unreleased(ForecastRefusalCode.CALIBRATION_INSUFFICIENT)
    candidate = comparison.candidate
    baseline = comparison.baseline
    if not (authority.calibration_sample_min <= candidate.sample_count <= authority.calibration_sample_max):
        return _unreleased(ForecastRefusalCode.CALIBRATION_SAMPLE_MISMATCH)
    if candidate.mean_absolute_timing_error is None or baseline.mean_absolute_timing_error is None:
        return _unreleased(ForecastRefusalCode.CALIBRATION_INSUFFICIENT)
    if candidate.mean_absolute_timing_error >= baseline.mean_absolute_timing_error:
        return _unreleased(ForecastRefusalCode.BASELINE_NOT_BEATEN)
    if (
        candidate.false_warning_rate > baseline.false_warning_rate
        or candidate.false_warning_rate > MAX_FALSE_WARNING_RATE
    ):
        return _unreleased(ForecastRefusalCode.FALSE_WARNING_REGRESSED)
    if candidate.miss_rate > baseline.miss_rate or candidate.miss_rate > MAX_MISS_RATE:
        return _unreleased(ForecastRefusalCode.MISS_RATE_REGRESSED)
    if (
        diagnostic.confidence not in {ForecastConfidence.MEDIUM_OBSERVED, ForecastConfidence.HIGH_HISTORICAL}
        or diagnostic.runway is None
    ):
        return _unreleased(diagnostic.refusal_code or ForecastRefusalCode.FORECAST_UNAVAILABLE)
    return ReleasedForecast(
        None,
        None,
        diagnostic.runway.earliest_exhaustion_epoch,
        diagnostic.runway.latest_exhaustion_epoch,
    )


__all__ = [
    "CALIBRATION_SCHEMA_VERSION",
    "FORECAST_UNAVAILABLE_TEXT",
    "MAX_CALIBRATION_CYCLES",
    "MAX_CALIBRATION_ORIGINS",
    "MAX_SAMPLES_PER_CALIBRATION_CYCLE",
    "ROBUST_METHOD_VERSION",
    "CalibrationComparison",
    "CalibrationCycle",
    "CalibrationOriginAudit",
    "CalibrationReport",
    "CalibrationScore",
    "ForecastClaimClass",
    "ForecastIdentityClass",
    "ForecastReleaseAuthority",
    "ReleasedForecast",
    "apply_forecast_release",
    "evaluate_forecast_calibration",
    "forecast_release_authority_from_payload",
    "forecast_release_authority_to_payload",
    "naive_baseline_exhaustion",
]
