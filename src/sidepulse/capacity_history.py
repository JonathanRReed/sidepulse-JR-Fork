"""Pure, bounded, metadata-only capacity history models."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise
from typing import Final

from .capacity_types import (
    QuotaLaneKey,
    SampleDisposition,
    SourceHealthKind,
    SourceKey,
    is_safe_opaque_account_discriminator,
)

CAPACITY_HISTORY_SCHEMA_VERSION: Final = 1
ACTIVITY_HISTORY_SCHEMA_VERSION: Final = 1
MAX_CAPACITY_HISTORY_SAMPLES: Final = 4_096
MAX_ACTIVITY_HISTORY_SAMPLES: Final = 4_096
MAX_ACTIVITY_COUNT: Final = 1_000_000
MAX_ESTIMATED_COST: Final = 1_000_000.0
MAX_WINDOW_MINUTES: Final = 525_600.0
SUPPORTED_RETENTION_DAYS: Final = (7, 30, 90)
_DAY_SECONDS: Final = 86_400.0
_REASON_CODE: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")


class HistoryValidationError(ValueError):
    """Retained history failed its metadata-only domain boundary."""


class HistoryContinuity(str, Enum):
    CONTINUOUS = "continuous"
    MISSING = "missing"
    CHANGED = "changed"


class HistoryInterval(str, Enum):
    DAY = "day"
    SEVEN_DAYS = "7d"
    THIRTY_DAYS = "30d"

    @property
    def days(self) -> int:
        return {
            HistoryInterval.DAY: 1,
            HistoryInterval.SEVEN_DAYS: 7,
            HistoryInterval.THIRTY_DAYS: 30,
        }[self]


class _NoObservation(str, Enum):
    VALUE = "no_observation"


NO_OBSERVATION: Final = _NoObservation.VALUE


def _finite_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _timestamp(value: object) -> bool:
    return _finite_number(value) and float(value) >= 0.0


def _reason_code(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= 64 and _REASON_CODE.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class CapacityHistorySample:
    schema_version: int
    lane_key: QuotaLaneKey
    account_discriminator: str
    observed_at: float
    remaining: float
    reset_epoch: float | None
    window_minutes: float | None
    source_health: SourceHealthKind
    disposition: SampleDisposition
    refusal_code: str | None

    def __post_init__(self) -> None:
        valid = (
            type(self.schema_version) is int
            and self.schema_version == CAPACITY_HISTORY_SCHEMA_VERSION
            and type(self.lane_key) is QuotaLaneKey
            and is_safe_opaque_account_discriminator(self.account_discriminator)
            and _timestamp(self.observed_at)
            and _finite_number(self.remaining)
            and 0.0 <= float(self.remaining) <= 100.0
            and (self.reset_epoch is None or _timestamp(self.reset_epoch))
            and (
                self.window_minutes is None
                or (_finite_number(self.window_minutes) and 0.0 < float(self.window_minutes) <= MAX_WINDOW_MINUTES)
            )
            and type(self.source_health) is SourceHealthKind
            and type(self.disposition) is SampleDisposition
            and (self.refusal_code is None or _reason_code(self.refusal_code))
        )
        if not valid:
            raise HistoryValidationError("invalid capacity history sample")
        if self.disposition is SampleDisposition.ACCEPTED:
            if self.refusal_code is not None:
                raise HistoryValidationError("accepted history sample has refusal")
        elif self.refusal_code is None:
            raise HistoryValidationError("rejected history sample lacks refusal")
        object.__setattr__(self, "observed_at", float(self.observed_at))
        object.__setattr__(self, "remaining", float(self.remaining))
        if self.reset_epoch is not None:
            object.__setattr__(self, "reset_epoch", float(self.reset_epoch))
        if self.window_minutes is not None:
            object.__setattr__(self, "window_minutes", float(self.window_minutes))


@dataclass(frozen=True, slots=True)
class ActivityHistorySample:
    schema_version: int
    source_key: SourceKey
    observed_at: float
    event_count: int
    session_count: int
    coverage: float
    priced_coverage: float
    estimated_cost: float | None

    def __post_init__(self) -> None:
        valid = (
            type(self.schema_version) is int
            and self.schema_version == ACTIVITY_HISTORY_SCHEMA_VERSION
            and type(self.source_key) is SourceKey
            and _timestamp(self.observed_at)
            and type(self.event_count) is int
            and 0 <= self.event_count <= MAX_ACTIVITY_COUNT
            and type(self.session_count) is int
            and 0 <= self.session_count <= MAX_ACTIVITY_COUNT
            and self.session_count <= self.event_count
            and _finite_number(self.coverage)
            and 0.0 <= float(self.coverage) <= 1.0
            and _finite_number(self.priced_coverage)
            and 0.0 <= float(self.priced_coverage) <= 1.0
            and (
                self.estimated_cost is None
                or (_finite_number(self.estimated_cost) and 0.0 <= float(self.estimated_cost) <= MAX_ESTIMATED_COST)
            )
        )
        if not valid:
            raise HistoryValidationError("invalid activity history sample")
        object.__setattr__(self, "observed_at", float(self.observed_at))
        object.__setattr__(self, "coverage", float(self.coverage))
        object.__setattr__(self, "priced_coverage", float(self.priced_coverage))
        if self.estimated_cost is not None:
            object.__setattr__(self, "estimated_cost", float(self.estimated_cost))


@dataclass(frozen=True, slots=True)
class SampleAdmission:
    sample: CapacityHistorySample | None
    disposition: SampleDisposition
    refusal_code: str | None

    def __post_init__(self) -> None:
        if not (
            (self.sample is None or type(self.sample) is CapacityHistorySample)
            and type(self.disposition) is SampleDisposition
            and (self.refusal_code is None or _reason_code(self.refusal_code))
        ):
            raise HistoryValidationError("invalid sample admission")


@dataclass(frozen=True, slots=True)
class NoObservationInterval:
    start_epoch: float
    end_epoch: float

    def __post_init__(self) -> None:
        if not (
            _timestamp(self.start_epoch)
            and _timestamp(self.end_epoch)
            and float(self.start_epoch) < float(self.end_epoch)
        ):
            raise HistoryValidationError("invalid no-observation interval")
        object.__setattr__(self, "start_epoch", float(self.start_epoch))
        object.__setattr__(self, "end_epoch", float(self.end_epoch))


@dataclass(frozen=True, slots=True)
class CapacityHistorySummary:
    observed_sample_count: int
    confirmed_reset_cycle_count: int
    minimum_remaining: float | _NoObservation
    maximum_remaining: float | _NoObservation
    no_observation_intervals: tuple[NoObservationInterval, ...]


@dataclass(frozen=True, slots=True)
class HistoryRetentionPolicy:
    days: int
    max_capacity_samples: int = MAX_CAPACITY_HISTORY_SAMPLES
    max_activity_samples: int = MAX_ACTIVITY_HISTORY_SAMPLES

    def __post_init__(self) -> None:
        if not (
            type(self.days) is int
            and self.days in SUPPORTED_RETENTION_DAYS
            and type(self.max_capacity_samples) is int
            and 0 <= self.max_capacity_samples <= MAX_CAPACITY_HISTORY_SAMPLES
            and type(self.max_activity_samples) is int
            and 0 <= self.max_activity_samples <= MAX_ACTIVITY_HISTORY_SAMPLES
        ):
            raise HistoryValidationError("invalid history retention policy")


def admit_capacity_sample(
    previous: CapacityHistorySample | None,
    candidate: CapacityHistorySample,
    continuity: HistoryContinuity,
) -> SampleAdmission:
    """Admit one normalized, identity-continuous, nonduplicate sample."""
    if (
        not (previous is None or type(previous) is CapacityHistorySample)
        or type(candidate) is not CapacityHistorySample
    ):
        raise HistoryValidationError("invalid history admission input")
    if type(continuity) is not HistoryContinuity:
        raise HistoryValidationError("invalid history continuity")
    if continuity is not HistoryContinuity.CONTINUOUS:
        code = "identity_missing" if continuity is HistoryContinuity.MISSING else "identity_changed"
        return SampleAdmission(None, SampleDisposition.IDENTITY_AMBIGUOUS, code)
    if candidate.disposition is not SampleDisposition.ACCEPTED:
        return SampleAdmission(None, candidate.disposition, candidate.refusal_code)
    if previous is not None:
        if previous.lane_key != candidate.lane_key or previous.account_discriminator != candidate.account_discriminator:
            return SampleAdmission(
                None,
                SampleDisposition.IDENTITY_AMBIGUOUS,
                "identity_changed",
            )
        if candidate.observed_at <= previous.observed_at:
            return SampleAdmission(None, SampleDisposition.OUT_OF_ORDER, "out_of_order")
        if _same_observation(previous, candidate):
            return SampleAdmission(None, SampleDisposition.DUPLICATE, "duplicate")
    return SampleAdmission(candidate, SampleDisposition.ACCEPTED, None)


def _same_observation(
    previous: CapacityHistorySample,
    candidate: CapacityHistorySample,
) -> bool:
    return (
        previous.lane_key == candidate.lane_key
        and previous.account_discriminator == candidate.account_discriminator
        and previous.remaining == candidate.remaining
        and previous.reset_epoch == candidate.reset_epoch
        and previous.window_minutes == candidate.window_minutes
        and previous.source_health is candidate.source_health
        and previous.disposition is candidate.disposition
        and previous.refusal_code == candidate.refusal_code
    )


def summarize_capacity_history(
    samples: tuple[CapacityHistorySample, ...],
    interval: HistoryInterval,
    now: float,
) -> CapacityHistorySummary:
    """Summarize only observations inside one bounded day-based interval."""
    if type(samples) is not tuple or not all(type(sample) is CapacityHistorySample for sample in samples):
        raise HistoryValidationError("invalid history samples")
    if type(interval) is not HistoryInterval or not _timestamp(now):
        raise HistoryValidationError("invalid history summary interval")
    end = float(now)
    start = max(0.0, end - interval.days * _DAY_SECONDS)
    selected = tuple(
        sample
        for sample in samples
        if start <= sample.observed_at <= end and sample.disposition is SampleDisposition.ACCEPTED
    )
    missing = _missing_day_intervals(selected, start, end)
    if not selected:
        return CapacityHistorySummary(
            0,
            0,
            NO_OBSERVATION,
            NO_OBSERVATION,
            missing,
        )
    remaining = tuple(sample.remaining for sample in selected)
    return CapacityHistorySummary(
        len(selected),
        _confirmed_reset_cycles(selected),
        min(remaining),
        max(remaining),
        missing,
    )


def _confirmed_reset_cycles(samples: tuple[CapacityHistorySample, ...]) -> int:
    grouped: dict[tuple[QuotaLaneKey, str], list[CapacityHistorySample]] = {}
    for sample in samples:
        grouped.setdefault((sample.lane_key, sample.account_discriminator), []).append(sample)
    confirmed = 0
    for lane_samples in grouped.values():
        ordered = sorted(lane_samples, key=lambda sample: sample.observed_at)
        for previous, current in pairwise(ordered):
            if (
                previous.reset_epoch is not None
                and current.reset_epoch is not None
                and current.observed_at >= previous.reset_epoch
                and current.reset_epoch != previous.reset_epoch
            ):
                confirmed += 1
    return confirmed


def _missing_day_intervals(
    samples: tuple[CapacityHistorySample, ...],
    start: float,
    end: float,
) -> tuple[NoObservationInterval, ...]:
    boundaries: list[tuple[float, float]] = []
    cursor = start
    while cursor < end:
        boundary = min(end, cursor + _DAY_SECONDS)
        if not any(
            cursor <= sample.observed_at < boundary or (boundary == end and sample.observed_at == end)
            for sample in samples
        ):
            if boundaries and boundaries[-1][1] == cursor:
                boundaries[-1] = (boundaries[-1][0], boundary)
            else:
                boundaries.append((cursor, boundary))
        cursor = boundary
    return tuple(NoObservationInterval(first, last) for first, last in boundaries)


def prune_capacity_history(
    samples: tuple[CapacityHistorySample, ...],
    policy: HistoryRetentionPolicy,
    now: float,
) -> tuple[CapacityHistorySample, ...]:
    """Prune capacity samples by age, then retain the newest count bound."""
    if type(samples) is not tuple or not all(type(sample) is CapacityHistorySample for sample in samples):
        raise HistoryValidationError("invalid history samples")
    if type(policy) is not HistoryRetentionPolicy or not _timestamp(now):
        raise HistoryValidationError("invalid history pruning input")
    cutoff = max(0.0, float(now) - policy.days * _DAY_SECONDS)
    eligible = sorted(
        (sample for sample in samples if cutoff <= sample.observed_at <= float(now)),
        key=lambda sample: sample.observed_at,
    )
    if policy.max_capacity_samples == 0:
        return ()
    return tuple(eligible[-policy.max_capacity_samples :])


def prune_activity_history(
    samples: tuple[ActivityHistorySample, ...],
    policy: HistoryRetentionPolicy,
    now: float,
) -> tuple[ActivityHistorySample, ...]:
    """Prune local activity aggregates independently by age and count."""
    if type(samples) is not tuple or not all(type(sample) is ActivityHistorySample for sample in samples):
        raise HistoryValidationError("invalid activity history samples")
    if type(policy) is not HistoryRetentionPolicy or not _timestamp(now):
        raise HistoryValidationError("invalid history pruning input")
    cutoff = max(0.0, float(now) - policy.days * _DAY_SECONDS)
    eligible = sorted(
        (sample for sample in samples if cutoff <= sample.observed_at <= float(now)),
        key=lambda sample: sample.observed_at,
    )
    if policy.max_activity_samples == 0:
        return ()
    return tuple(eligible[-policy.max_activity_samples :])
