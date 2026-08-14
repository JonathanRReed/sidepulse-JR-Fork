"""Hardened persistence and exact lifecycle for private capacity metadata."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from .capacity_history import (
    MAX_ACTIVITY_HISTORY_SAMPLES,
    MAX_CAPACITY_HISTORY_SAMPLES,
    ActivityHistorySample,
    CapacityHistorySample,
    CapacityHistorySummary,
    HistoryContinuity,
    HistoryInterval,
    HistoryRetentionPolicy,
    HistoryValidationError,
    SampleAdmission,
    admit_capacity_sample,
    prune_activity_history,
    prune_capacity_history,
    summarize_capacity_history,
)
from .capacity_types import (
    QuotaEffect,
    QuotaLaneKey,
    SampleDisposition,
    SourceHealthKind,
    SourceKey,
)
from .private_io import (
    _private_parent,
    _require_private_leaf,
    atomic_private_write,
    read_private_text,
)

STORE_VERSION: Final = 1
MAX_HISTORY_STORE_BYTES: Final = 2 * 1024 * 1024
MIN_FLUSH_INTERVAL_SECONDS: Final = 60.0
_DOCUMENT_FIELDS: Final = frozenset({"activity_samples", "capacity_samples", "version"})
_CAPACITY_FIELDS: Final = frozenset(
    {
        "account_discriminator",
        "disposition",
        "lane_key",
        "observed_at",
        "refusal_code",
        "remaining",
        "reset_epoch",
        "schema_version",
        "source_health",
        "window_minutes",
    }
)
_ACTIVITY_FIELDS: Final = frozenset(
    {
        "coverage",
        "estimated_cost",
        "event_count",
        "observed_at",
        "priced_coverage",
        "schema_version",
        "session_count",
        "source_key",
    }
)
_SOURCE_FIELDS: Final = frozenset({"adapter_id", "capability_id", "provider_id", "source_instance_id"})
_LANE_FIELDS: Final = frozenset({"effect", "model", "opaque_scope", "pool", "source", "window"})


class CapacityHistoryRestoreHealth(str, Enum):
    HEALTHY = "healthy"
    MISSING = "missing"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CapacityHistoryState:
    capacity_samples: tuple[CapacityHistorySample, ...] = ()
    activity_samples: tuple[ActivityHistorySample, ...] = ()


@dataclass(frozen=True, slots=True)
class CapacityHistoryRestore:
    state: CapacityHistoryState
    health: CapacityHistoryRestoreHealth


class _CorruptHistoryStore(ValueError):
    pass


class _UnsupportedHistoryStore(ValueError):
    pass


def default_capacity_history_path(home: Path | None = None) -> Path:
    base = Path.home() if home is None else Path(home).expanduser()
    return base / "Library" / "Application Support" / "SidePulse" / "capacity-history.json"


def load_capacity_history(path: Path) -> CapacityHistoryRestore:
    """Load strict metadata or return typed degraded health without content."""
    target = Path(path).expanduser()
    try:
        raw = read_private_text(target, max_bytes=MAX_HISTORY_STORE_BYTES)
        document = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
        state = _state_from_document(document)
    except FileNotFoundError:
        return CapacityHistoryRestore(CapacityHistoryState(), CapacityHistoryRestoreHealth.MISSING)
    except _UnsupportedHistoryStore:
        return CapacityHistoryRestore(CapacityHistoryState(), CapacityHistoryRestoreHealth.UNSUPPORTED)
    except OSError as error:
        if "exceeds maximum size" in str(error):
            _discard_and_diagnose(target)
        return CapacityHistoryRestore(CapacityHistoryState(), CapacityHistoryRestoreHealth.UNAVAILABLE)
    except (RecursionError, TypeError, UnicodeError, ValueError):
        _discard_and_diagnose(target)
        return CapacityHistoryRestore(CapacityHistoryState(), CapacityHistoryRestoreHealth.CORRUPT)
    return CapacityHistoryRestore(state, CapacityHistoryRestoreHealth.HEALTHY)


def save_capacity_history(
    path: Path,
    state: CapacityHistoryState,
    *,
    retention_days: int,
    now: float,
) -> CapacityHistoryState:
    """Prune and atomically save one exact versioned metadata envelope."""
    if type(state) is not CapacityHistoryState:
        raise ValueError("invalid capacity history state")
    policy = HistoryRetentionPolicy(retention_days)
    try:
        capacity = prune_capacity_history(state.capacity_samples, policy, now)
        activity = prune_activity_history(state.activity_samples, policy, now)
    except HistoryValidationError as error:
        raise ValueError("invalid capacity history state") from error
    combined = sorted(
        [
            *((sample.observed_at, "capacity") for sample in capacity),
            *((sample.observed_at, "activity") for sample in activity),
        ],
        key=lambda item: (item[0], item[1]),
    )

    def after_dropping(oldest_count: int) -> tuple[CapacityHistoryState, str]:
        removed = combined[:oldest_count]
        capacity_removed = sum(kind == "capacity" for _, kind in removed)
        activity_removed = oldest_count - capacity_removed
        candidate = CapacityHistoryState(capacity[capacity_removed:], activity[activity_removed:])
        return candidate, _encode_state(candidate)

    candidate, encoded = after_dropping(0)
    if len(encoded.encode("utf-8")) > MAX_HISTORY_STORE_BYTES:
        low = 1
        high = len(combined)
        while low < high:
            midpoint = (low + high) // 2
            _, probe = after_dropping(midpoint)
            if len(probe.encode("utf-8")) <= MAX_HISTORY_STORE_BYTES:
                high = midpoint
            else:
                low = midpoint + 1
        candidate, encoded = after_dropping(low)
    if len(encoded.encode("utf-8")) > MAX_HISTORY_STORE_BYTES:
        raise ValueError("capacity history store exceeds maximum size")
    atomic_private_write(Path(path), encoded)
    return candidate


def _strict_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _CorruptHistoryStore
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _CorruptHistoryStore


def _exact_fields(value: object, fields: frozenset[str]) -> bool:
    return type(value) is dict and frozenset(value) == fields


def _state_from_document(document: object) -> CapacityHistoryState:
    if not _exact_fields(document, _DOCUMENT_FIELDS):
        raise _CorruptHistoryStore
    version = document["version"]
    if type(version) is not int:
        raise _CorruptHistoryStore
    if version != STORE_VERSION:
        raise _UnsupportedHistoryStore
    capacity = document["capacity_samples"]
    activity = document["activity_samples"]
    if not (
        type(capacity) is list
        and len(capacity) <= MAX_CAPACITY_HISTORY_SAMPLES
        and type(activity) is list
        and len(activity) <= MAX_ACTIVITY_HISTORY_SAMPLES
    ):
        raise _CorruptHistoryStore
    try:
        return CapacityHistoryState(
            tuple(_capacity_from_payload(item) for item in capacity),
            tuple(_activity_from_payload(item) for item in activity),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise _CorruptHistoryStore from error


def _encode_state(state: CapacityHistoryState) -> str:
    if not (
        type(state.capacity_samples) is tuple
        and type(state.activity_samples) is tuple
        and all(type(item) is CapacityHistorySample for item in state.capacity_samples)
        and all(type(item) is ActivityHistorySample for item in state.activity_samples)
    ):
        raise ValueError("invalid capacity history state")
    document = {
        "version": STORE_VERSION,
        "capacity_samples": [_capacity_to_payload(sample) for sample in state.capacity_samples],
        "activity_samples": [_activity_to_payload(sample) for sample in state.activity_samples],
    }
    return json.dumps(document, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n"


def _source_to_payload(source: SourceKey) -> dict[str, object]:
    return {
        "provider_id": source.provider_id,
        "adapter_id": source.adapter_id,
        "source_instance_id": source.source_instance_id,
        "capability_id": source.capability_id,
    }


def _source_from_payload(payload: object) -> SourceKey:
    if not _exact_fields(payload, _SOURCE_FIELDS):
        raise _CorruptHistoryStore
    return SourceKey(
        payload["provider_id"],
        payload["adapter_id"],
        payload["source_instance_id"],
        payload["capability_id"],
    )


def _lane_to_payload(lane: QuotaLaneKey) -> dict[str, object]:
    return {
        "source": _source_to_payload(lane.source),
        "opaque_scope": lane.opaque_scope,
        "pool": lane.pool,
        "model": lane.model,
        "window": lane.window,
        "effect": lane.effect.value,
    }


def _lane_from_payload(payload: object) -> QuotaLaneKey:
    if not _exact_fields(payload, _LANE_FIELDS):
        raise _CorruptHistoryStore
    return QuotaLaneKey(
        _source_from_payload(payload["source"]),
        payload["opaque_scope"],
        payload["pool"],
        payload["model"],
        payload["window"],
        QuotaEffect(payload["effect"]),
    )


def _capacity_to_payload(sample: CapacityHistorySample) -> dict[str, object]:
    if type(sample) is not CapacityHistorySample:
        raise ValueError("invalid capacity history sample")
    return {
        "schema_version": sample.schema_version,
        "lane_key": _lane_to_payload(sample.lane_key),
        "account_discriminator": sample.account_discriminator,
        "observed_at": sample.observed_at,
        "remaining": sample.remaining,
        "reset_epoch": sample.reset_epoch,
        "window_minutes": sample.window_minutes,
        "source_health": sample.source_health.value,
        "disposition": sample.disposition.value,
        "refusal_code": sample.refusal_code,
    }


def _capacity_from_payload(payload: object) -> CapacityHistorySample:
    if not _exact_fields(payload, _CAPACITY_FIELDS):
        raise _CorruptHistoryStore
    return CapacityHistorySample(
        payload["schema_version"],
        _lane_from_payload(payload["lane_key"]),
        payload["account_discriminator"],
        payload["observed_at"],
        payload["remaining"],
        payload["reset_epoch"],
        payload["window_minutes"],
        SourceHealthKind(payload["source_health"]),
        SampleDisposition(payload["disposition"]),
        payload["refusal_code"],
    )


def _activity_to_payload(sample: ActivityHistorySample) -> dict[str, object]:
    if type(sample) is not ActivityHistorySample:
        raise ValueError("invalid activity history sample")
    return {
        "schema_version": sample.schema_version,
        "source_key": _source_to_payload(sample.source_key),
        "observed_at": sample.observed_at,
        "event_count": sample.event_count,
        "session_count": sample.session_count,
        "coverage": sample.coverage,
        "priced_coverage": sample.priced_coverage,
        "estimated_cost": sample.estimated_cost,
    }


def _activity_from_payload(payload: object) -> ActivityHistorySample:
    if not _exact_fields(payload, _ACTIVITY_FIELDS):
        raise _CorruptHistoryStore
    return ActivityHistorySample(
        payload["schema_version"],
        _source_from_payload(payload["source_key"]),
        payload["observed_at"],
        payload["event_count"],
        payload["session_count"],
        payload["coverage"],
        payload["priced_coverage"],
        payload["estimated_cost"],
    )


def _secure_unlink(path: Path) -> bool:
    with _private_parent(path, tighten=False) as (
        target,
        parent_descriptor,
        name,
    ):
        info = _require_private_leaf(target, parent_descriptor, name)
        if info is None:
            return False
        os.unlink(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        return True


def _discard_and_diagnose(path: Path) -> None:
    try:
        _secure_unlink(path)
    except OSError:
        return
    diagnostic = path.with_name(path.name + ".corrupt")
    payload = json.dumps(
        {"diagnostic": "capacity_history_corrupt", "version": 1},
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        atomic_private_write(diagnostic, payload + "\n")
    except OSError:
        pass


class CapacityHistoryStore:
    """In-memory batch owner with bounded flush and exact consent deletion."""

    def __init__(self, path: Path, *, retention_days: int) -> None:
        self.path = Path(path).expanduser()
        self.retention_days = HistoryRetentionPolicy(retention_days).days
        self.state = CapacityHistoryState()
        self.cached_summaries: dict[tuple[HistoryInterval, float], CapacityHistorySummary] = {}
        self._dirty = False
        self._last_flush_at: float | None = None

    @property
    def calibration_inputs(self) -> tuple[CapacityHistorySample, ...]:
        return self.state.capacity_samples

    def restore(self) -> CapacityHistoryRestore:
        restored = load_capacity_history(self.path)
        self.state = restored.state
        self._dirty = False
        self.cached_summaries.clear()
        return restored

    def admit_capacity(
        self,
        candidate: CapacityHistorySample,
        continuity: HistoryContinuity,
    ) -> SampleAdmission:
        previous = next(
            (sample for sample in reversed(self.state.capacity_samples) if sample.lane_key == candidate.lane_key),
            None,
        )
        result = admit_capacity_sample(previous, candidate, continuity)
        if result.sample is not None:
            self.state = CapacityHistoryState(
                (*self.state.capacity_samples, result.sample),
                self.state.activity_samples,
            )
            self._dirty = True
            self.cached_summaries.clear()
        return result

    def admit_activity(self, candidate: ActivityHistorySample) -> bool:
        if type(candidate) is not ActivityHistorySample:
            raise HistoryValidationError("invalid activity history sample")
        previous = next(
            (sample for sample in reversed(self.state.activity_samples) if sample.source_key == candidate.source_key),
            None,
        )
        if previous is not None and (
            candidate.observed_at <= previous.observed_at or _same_activity(previous, candidate)
        ):
            return False
        self.state = CapacityHistoryState(
            self.state.capacity_samples,
            (*self.state.activity_samples, candidate),
        )
        self._dirty = True
        return True

    def summarize(
        self,
        interval: HistoryInterval,
        *,
        now: float,
    ) -> CapacityHistorySummary:
        key = (interval, float(now))
        if key not in self.cached_summaries:
            self.cached_summaries[key] = summarize_capacity_history(self.state.capacity_samples, interval, now)
        return self.cached_summaries[key]

    def flush(self, *, now: float | None = None, force: bool = False) -> bool:
        current = time.time() if now is None else float(now)
        if not self._dirty:
            return False
        if not force and self._last_flush_at is not None and current - self._last_flush_at < MIN_FLUSH_INTERVAL_SECONDS:
            return False
        self.state = save_capacity_history(
            self.path,
            self.state,
            retention_days=self.retention_days,
            now=current,
        )
        self._dirty = False
        self._last_flush_at = current
        return True

    def shutdown(self, *, now: float | None = None) -> bool:
        return self.flush(now=now, force=True)

    def delete_capacity_history(self) -> bool:
        had_memory = self.state != CapacityHistoryState() or bool(self.cached_summaries)
        self.state = CapacityHistoryState()
        self.cached_summaries.clear()
        self._dirty = False
        try:
            removed = _secure_unlink(self.path)
        except FileNotFoundError:
            removed = False
        return had_memory or removed


def _same_activity(
    previous: ActivityHistorySample,
    candidate: ActivityHistorySample,
) -> bool:
    return (
        previous.source_key == candidate.source_key
        and previous.event_count == candidate.event_count
        and previous.session_count == candidate.session_count
        and previous.coverage == candidate.coverage
        and previous.priced_coverage == candidate.priced_coverage
        and previous.estimated_cost == candidate.estimated_cost
    )


def delete_capacity_history(store: CapacityHistoryStore) -> bool:
    """Delete all retained and derived history owned by one store."""
    if type(store) is not CapacityHistoryStore:
        raise ValueError("invalid capacity history store")
    return store.delete_capacity_history()
