"""Pure, bounded, content-free daily operator history."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Final

from .operator_state import SemanticEventKey
from .provider_contracts import ProviderIdentifier
from .provider_facts import RequestKey, WorkKey

MAX_OPERATOR_HISTORY_PROVIDERS: Final = 32
MAX_RUNTIME_HISTORY_EVENTS: Final = 10_000
MAX_RUNTIME_FAMILY_COUNT: Final = 1_000
MAX_DAILY_HISTORY_COUNT: Final = 1_000_000
MAX_DURATION_SECONDS: Final = 31_536_000.0
SUPPORTED_HISTORY_RANGES: Final = frozenset({1, 7, 30, 90})
MIN_TIMEZONE_OFFSET_MINUTES: Final = -14 * 60
MAX_TIMEZONE_OFFSET_MINUTES: Final = 14 * 60

_FORBIDDEN_PROVIDER_PARTS: Final = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "privatekey",
    "secret",
    "token",
)
_HEALTH_LABELS: Final = frozenset(
    {
        "Complete observation",
        "Partial observation",
        "History unavailable",
        "No Observation",
    }
)


class HistoryValidationError(ValueError):
    """Operator history input failed closed at the metadata boundary."""


class HistoryCoverage(str, Enum):
    NO_OBSERVATION = "no-observation"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class HistoryEventKind(str, Enum):
    STARTED = "started"
    NEEDS_USER = "needs-user"
    COMPLETED = "completed"
    FAILED = "failed"
    REQUEST_ACKNOWLEDGED = "request-acknowledged"
    REQUEST_RESUMED = "request-resumed"
    SOURCE_DEGRADED = "source-degraded"
    SOURCE_RECOVERED = "source-recovered"
    DEVICE_DEGRADED = "device-degraded"
    DEVICE_RECOVERED = "device-recovered"


def _finite_nonnegative(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0.0


def _valid_count(value: object, *, maximum: int = MAX_DAILY_HISTORY_COUNT) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _valid_offset(value: object) -> bool:
    return type(value) is int and MIN_TIMEZONE_OFFSET_MINUTES <= value <= MAX_TIMEZONE_OFFSET_MINUTES


def _validate_provider(value: object) -> None:
    if type(value) is not ProviderIdentifier:
        raise HistoryValidationError("invalid history provider")
    normalized = "".join(character for character in value.value.casefold() if character.isalnum())
    if any(part in normalized for part in _FORBIDDEN_PROVIDER_PARTS):
        raise HistoryValidationError("private-shaped history provider")


def _subject_provider(key: SemanticEventKey) -> str:
    subject = key.subject_key
    if type(subject) is WorkKey:
        return subject.source_key.provider_id
    if type(subject) is RequestKey:
        return subject.work_key.source_key.provider_id
    raise HistoryValidationError("invalid history semantic subject")


@dataclass(frozen=True, slots=True)
class RuntimeHistoryEvent:
    """Transient semantic identity plus already derived numeric metadata."""

    semantic_event_key: SemanticEventKey
    provider_id: ProviderIdentifier
    kind: HistoryEventKind
    occurred_at: float
    active_seconds: float | None
    attention_wait_seconds: float | None
    primary_count: int
    worker_count: int

    def __post_init__(self) -> None:
        _validate_provider(self.provider_id)
        if not (
            type(self.semantic_event_key) is SemanticEventKey
            and _subject_provider(self.semantic_event_key) == self.provider_id.value
            and type(self.kind) is HistoryEventKind
            and _finite_nonnegative(self.occurred_at)
            and (
                self.active_seconds is None
                or (_finite_nonnegative(self.active_seconds) and float(self.active_seconds) <= MAX_DURATION_SECONDS)
            )
            and (
                self.attention_wait_seconds is None
                or (
                    _finite_nonnegative(self.attention_wait_seconds)
                    and float(self.attention_wait_seconds) <= MAX_DURATION_SECONDS
                )
            )
            and _valid_count(
                self.primary_count,
                maximum=MAX_RUNTIME_FAMILY_COUNT,
            )
            and _valid_count(
                self.worker_count,
                maximum=MAX_RUNTIME_FAMILY_COUNT,
            )
        ):
            raise HistoryValidationError("invalid runtime history event")
        object.__setattr__(self, "occurred_at", float(self.occurred_at))
        if self.active_seconds is not None:
            object.__setattr__(self, "active_seconds", float(self.active_seconds))
        if self.attention_wait_seconds is not None:
            object.__setattr__(
                self,
                "attention_wait_seconds",
                float(self.attention_wait_seconds),
            )


def _valid_bands(value: object) -> bool:
    return type(value) is tuple and len(value) == 4 and all(_valid_count(item) for item in value)


def _valid_day_key(value: object) -> bool:
    if type(value) is not str or len(value) != 10:
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value


@dataclass(frozen=True, slots=True)
class OperatorHistoryDay:
    day_key: str
    timezone_offset_minutes: int
    provider_id: ProviderIdentifier
    started: int
    needs_user: int
    completed: int
    failed: int
    acknowledged: int
    active_duration_bands: tuple[int, int, int, int]
    attention_wait_bands: tuple[int, int, int, int]
    primary_count: int
    worker_count: int
    source_recoveries: int
    device_recoveries: int
    coverage: HistoryCoverage
    sample_count: int

    def __post_init__(self) -> None:
        _validate_provider(self.provider_id)
        scalar_counts = (
            self.started,
            self.needs_user,
            self.completed,
            self.failed,
            self.acknowledged,
            self.primary_count,
            self.worker_count,
            self.source_recoveries,
            self.device_recoveries,
            self.sample_count,
        )
        if not (
            _valid_day_key(self.day_key)
            and _valid_offset(self.timezone_offset_minutes)
            and all(_valid_count(item) for item in scalar_counts)
            and _valid_bands(self.active_duration_bands)
            and _valid_bands(self.attention_wait_bands)
            and type(self.coverage) is HistoryCoverage
        ):
            raise HistoryValidationError("invalid operator history day")
        observed_counts = (
            *scalar_counts[:-1],
            *self.active_duration_bands,
            *self.attention_wait_bands,
        )
        if self.coverage is HistoryCoverage.NO_OBSERVATION:
            if self.sample_count != 0 or any(observed_counts):
                raise HistoryValidationError("no-observation day contains facts")
        elif self.sample_count == 0:
            raise HistoryValidationError("observed day lacks samples")
        elif (
            any(
                count > self.sample_count
                for count in (
                    self.started,
                    self.needs_user,
                    self.completed,
                    self.failed,
                    self.acknowledged,
                    self.source_recoveries,
                    self.device_recoveries,
                )
            )
            or sum(self.active_duration_bands) > self.sample_count
            or sum(self.attention_wait_bands) > self.sample_count
        ):
            raise HistoryValidationError("history counters exceed samples")


@dataclass(frozen=True, slots=True)
class OperatorHistoryProjection:
    range_days: int
    observed_days: int
    missing_days: int
    rows: tuple[OperatorHistoryDay, ...]
    summary_sentences: tuple[str, ...]
    health_label: str

    def __post_init__(self) -> None:
        if not (
            type(self.range_days) is int
            and self.range_days in SUPPORTED_HISTORY_RANGES
            and _valid_count(self.observed_days, maximum=self.range_days)
            and _valid_count(self.missing_days, maximum=self.range_days)
            and self.observed_days + self.missing_days == self.range_days
            and type(self.rows) is tuple
            and all(type(row) is OperatorHistoryDay for row in self.rows)
            and type(self.summary_sentences) is tuple
            and 1 <= len(self.summary_sentences) <= 3
            and all(
                type(sentence) is str and 1 <= len(sentence) <= 160 and sentence.isprintable()
                for sentence in self.summary_sentences
            )
            and type(self.health_label) is str
            and self.health_label in _HEALTH_LABELS
        ):
            raise HistoryValidationError("invalid operator history projection")


def _resolve_offset(
    occurred_at: float,
    resolver: Callable[[float], int] | None,
) -> int:
    if resolver is None:
        return 0
    try:
        offset = resolver(occurred_at)
    except Exception as error:
        raise HistoryValidationError("timezone offset resolution failed") from error
    if not _valid_offset(offset):
        raise HistoryValidationError("invalid timezone offset")
    return offset


def _local_day_key(epoch: float, offset_minutes: int) -> str:
    offset = timezone(timedelta(minutes=offset_minutes))
    try:
        return datetime.fromtimestamp(epoch, offset).date().isoformat()
    except (OverflowError, OSError, ValueError) as error:
        raise HistoryValidationError("history timestamp is out of range") from error


def _duration_band(value: float) -> int:
    if value < 300.0:
        return 0
    if value < 1_800.0:
        return 1
    if value < 7_200.0:
        return 2
    return 3


def _bounded_add(left: int, right: int) -> int:
    return min(MAX_DAILY_HISTORY_COUNT, left + right)


def _bands_for(events: tuple[RuntimeHistoryEvent, ...], field: str) -> tuple[int, int, int, int]:
    values = [0, 0, 0, 0]
    for event in events:
        duration = getattr(event, field)
        if duration is not None:
            band = _duration_band(duration)
            values[band] = _bounded_add(values[band], 1)
    return values[0], values[1], values[2], values[3]


def _coverage(events: tuple[RuntimeHistoryEvent, ...]) -> HistoryCoverage:
    kinds = frozenset(event.kind for event in events)
    if HistoryEventKind.SOURCE_DEGRADED in kinds:
        if kinds == {HistoryEventKind.SOURCE_DEGRADED}:
            return HistoryCoverage.FAILED
        return HistoryCoverage.PARTIAL
    if HistoryEventKind.DEVICE_DEGRADED in kinds:
        return HistoryCoverage.PARTIAL
    return HistoryCoverage.COMPLETE


def _event_sort_key(event: RuntimeHistoryEvent) -> tuple[object, ...]:
    return (
        event.occurred_at,
        event.provider_id.value,
        event.kind.value,
        event.semantic_event_key,
    )


def aggregate_operator_history(
    events: tuple[RuntimeHistoryEvent, ...],
    *,
    timezone_offset_at: Callable[[float], int] | None = None,
) -> tuple[OperatorHistoryDay, ...]:
    """Deduplicate transient semantic edges and return content-free local-day rows."""
    if not (
        type(events) is tuple
        and len(events) <= MAX_RUNTIME_HISTORY_EVENTS
        and all(type(event) is RuntimeHistoryEvent for event in events)
        and (timezone_offset_at is None or callable(timezone_offset_at))
    ):
        raise HistoryValidationError("invalid operator history events")

    deduplicated: dict[SemanticEventKey, RuntimeHistoryEvent] = {}
    for event in sorted(events, key=_event_sort_key):
        previous = deduplicated.get(event.semantic_event_key)
        if previous is not None and previous != event:
            raise HistoryValidationError("conflicting duplicate history event")
        deduplicated[event.semantic_event_key] = event
    if len({event.provider_id for event in deduplicated.values()}) > MAX_OPERATOR_HISTORY_PROVIDERS:
        raise HistoryValidationError("too many history providers")

    grouped: dict[tuple[str, int, str], list[RuntimeHistoryEvent]] = {}
    for event in deduplicated.values():
        offset = _resolve_offset(event.occurred_at, timezone_offset_at)
        day_key = _local_day_key(event.occurred_at, offset)
        grouped.setdefault((day_key, offset, event.provider_id.value), []).append(event)

    rows: list[OperatorHistoryDay] = []
    for (day_key, offset, _provider_value), values in sorted(grouped.items()):
        selected = tuple(sorted(values, key=_event_sort_key))
        provider = selected[0].provider_id
        rows.append(
            OperatorHistoryDay(
                day_key,
                offset,
                provider,
                sum(event.kind is HistoryEventKind.STARTED for event in selected),
                sum(event.kind is HistoryEventKind.NEEDS_USER for event in selected),
                sum(event.kind is HistoryEventKind.COMPLETED for event in selected),
                sum(event.kind is HistoryEventKind.FAILED for event in selected),
                sum(event.kind is HistoryEventKind.REQUEST_ACKNOWLEDGED for event in selected),
                _bands_for(selected, "active_seconds"),
                _bands_for(selected, "attention_wait_seconds"),
                max((event.primary_count for event in selected), default=0),
                max((event.worker_count for event in selected), default=0),
                sum(event.kind is HistoryEventKind.SOURCE_RECOVERED for event in selected),
                sum(event.kind is HistoryEventKind.DEVICE_RECOVERED for event in selected),
                _coverage(selected),
                len(selected),
            )
        )
    return tuple(rows)


def _row_key(row: OperatorHistoryDay) -> tuple[str, int, str]:
    return row.day_key, row.timezone_offset_minutes, row.provider_id.value


def _merge_coverage(
    left: HistoryCoverage,
    right: HistoryCoverage,
) -> HistoryCoverage:
    if left is HistoryCoverage.NO_OBSERVATION:
        return right
    if right is HistoryCoverage.NO_OBSERVATION:
        return left
    if left is HistoryCoverage.PARTIAL or right is HistoryCoverage.PARTIAL:
        return HistoryCoverage.PARTIAL
    if left is right:
        return left
    return HistoryCoverage.PARTIAL


def _sum_bands(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    return tuple(_bounded_add(a, b) for a, b in zip(left, right, strict=True))  # type: ignore[return-value]


def _merge_day(left: OperatorHistoryDay, right: OperatorHistoryDay) -> OperatorHistoryDay:
    if _row_key(left) != _row_key(right):
        raise HistoryValidationError("cannot merge different history rows")
    return OperatorHistoryDay(
        left.day_key,
        left.timezone_offset_minutes,
        left.provider_id,
        _bounded_add(left.started, right.started),
        _bounded_add(left.needs_user, right.needs_user),
        _bounded_add(left.completed, right.completed),
        _bounded_add(left.failed, right.failed),
        _bounded_add(left.acknowledged, right.acknowledged),
        _sum_bands(left.active_duration_bands, right.active_duration_bands),
        _sum_bands(left.attention_wait_bands, right.attention_wait_bands),
        max(left.primary_count, right.primary_count),
        max(left.worker_count, right.worker_count),
        _bounded_add(left.source_recoveries, right.source_recoveries),
        _bounded_add(left.device_recoveries, right.device_recoveries),
        _merge_coverage(left.coverage, right.coverage),
        _bounded_add(left.sample_count, right.sample_count),
    )


def merge_operator_history_days(
    first: tuple[OperatorHistoryDay, ...],
    second: tuple[OperatorHistoryDay, ...],
) -> tuple[OperatorHistoryDay, ...]:
    """Commutatively merge content-free row batches by local day, offset, and provider."""
    if not (
        type(first) is tuple
        and type(second) is tuple
        and all(type(row) is OperatorHistoryDay for row in (*first, *second))
    ):
        raise HistoryValidationError("invalid operator history rows")
    merged: dict[tuple[str, int, str], OperatorHistoryDay] = {}
    for row in sorted((*first, *second), key=_row_key):
        key = _row_key(row)
        previous = merged.get(key)
        merged[key] = row if previous is None else _merge_day(previous, row)
    return tuple(merged[key] for key in sorted(merged))


def _summary(rows: tuple[OperatorHistoryDay, ...], observed_days: int) -> tuple[str, ...]:
    if not rows or observed_days == 0:
        return ("No operator history was observed in this range.",)
    provider_days = len(
        {(row.day_key, row.provider_id) for row in rows if row.coverage is not HistoryCoverage.NO_OBSERVATION}
    )
    sentences = [f"Observed {provider_days} provider-days across {observed_days} local days."]
    completed = sum(row.completed for row in rows)
    failed = sum(row.failed for row in rows)
    if completed or failed:
        sentences.append(f"Observed {completed} completions and {failed} failures.")
    needs_user = sum(row.needs_user for row in rows)
    acknowledged = sum(row.acknowledged for row in rows)
    if needs_user or acknowledged:
        sentences.append(f"Observed {needs_user} attention episodes and {acknowledged} local acknowledgements.")
    return tuple(sentences[:3])


def project_operator_history(
    rows: tuple[OperatorHistoryDay, ...],
    *,
    range_days: int,
    now: float,
    timezone_offset_minutes: int = 0,
) -> OperatorHistoryProjection:
    """Project retained daily rows without converting missing evidence into zero."""
    if not (
        type(rows) is tuple
        and all(type(row) is OperatorHistoryDay for row in rows)
        and type(range_days) is int
        and range_days in SUPPORTED_HISTORY_RANGES
        and _finite_nonnegative(now)
        and _valid_offset(timezone_offset_minutes)
    ):
        raise HistoryValidationError("invalid operator history projection input")
    today = date.fromisoformat(_local_day_key(float(now), timezone_offset_minutes))
    first_day = date.fromordinal(today.toordinal() - range_days + 1)
    merged = merge_operator_history_days((), rows)
    selected = tuple(row for row in merged if first_day <= date.fromisoformat(row.day_key) <= today)
    observed_day_keys = {row.day_key for row in selected if row.coverage is not HistoryCoverage.NO_OBSERVATION}
    observed_days = min(range_days, len(observed_day_keys))
    missing_days = range_days - observed_days
    if not selected or observed_days == 0:
        health = "No Observation"
    elif any(row.coverage is HistoryCoverage.FAILED for row in selected):
        health = "Partial observation"
    elif any(row.coverage is HistoryCoverage.PARTIAL for row in selected) or missing_days:
        health = "Partial observation"
    else:
        health = "Complete observation"
    return OperatorHistoryProjection(
        range_days,
        observed_days,
        missing_days,
        selected,
        _summary(selected, observed_days),
        health,
    )
