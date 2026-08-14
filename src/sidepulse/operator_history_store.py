"""Private atomic persistence for bounded daily operator facts."""

from __future__ import annotations

import fcntl
import json
import math
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Final

from .operator_history import (
    MAX_OPERATOR_HISTORY_PROVIDERS,
    HistoryCoverage,
    HistoryValidationError,
    OperatorHistoryDay,
    OperatorHistoryProjection,
    merge_operator_history_days,
    project_operator_history,
)
from .private_io import (
    _private_parent,
    _require_private_leaf,
    atomic_private_write,
    read_private_text,
)
from .provider_contracts import ProviderIdentifier

STORE_VERSION: Final = 1
MAX_OPERATOR_HISTORY_STORE_BYTES: Final = 2 * 1024 * 1024
MAX_OPERATOR_HISTORY_ROWS: Final = 90 * MAX_OPERATOR_HISTORY_PROVIDERS
SUPPORTED_OPERATOR_HISTORY_RETENTION_DAYS: Final = frozenset({0, 7, 30, 90})

_DOCUMENT_FIELDS: Final = frozenset({"days", "version"})
_DAY_FIELDS: Final = frozenset(
    {
        "acknowledged",
        "active_duration_bands",
        "attention_wait_bands",
        "completed",
        "coverage",
        "day_key",
        "device_recoveries",
        "failed",
        "needs_user",
        "primary_count",
        "provider_id",
        "sample_count",
        "source_recoveries",
        "started",
        "timezone_offset_minutes",
        "worker_count",
    }
)
_PROCESS_STORE_LOCK: Final = threading.RLock()


class OperatorHistoryRestoreHealth(str, Enum):
    HEALTHY = "healthy"
    MISSING = "missing"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class OperatorHistoryState:
    rows: tuple[OperatorHistoryDay, ...] = ()

    def __post_init__(self) -> None:
        if not (type(self.rows) is tuple and all(type(row) is OperatorHistoryDay for row in self.rows)):
            raise ValueError("invalid operator history state")


@dataclass(frozen=True, slots=True)
class OperatorHistoryRestore:
    state: OperatorHistoryState
    health: OperatorHistoryRestoreHealth

    def __post_init__(self) -> None:
        if not (type(self.state) is OperatorHistoryState and type(self.health) is OperatorHistoryRestoreHealth):
            raise ValueError("invalid operator history restore")


class _CorruptOperatorHistory(ValueError):
    pass


class _UnsupportedOperatorHistory(ValueError):
    pass


def _valid_now(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0.0


def _validate_retention(value: object) -> int:
    if type(value) is not int or value not in SUPPORTED_OPERATOR_HISTORY_RETENTION_DAYS:
        raise ValueError("invalid operator history retention")
    return value


def default_operator_history_path(home: Path | None = None) -> Path:
    base = Path.home() if home is None else Path(home).expanduser()
    return base / "Library" / "Application Support" / "SidePulse" / "operator-history.json"


def _strict_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _CorruptOperatorHistory
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _CorruptOperatorHistory


def _exact_fields(value: object, fields: frozenset[str]) -> bool:
    return type(value) is dict and frozenset(value) == fields


def _bands_from_payload(value: object) -> tuple[int, int, int, int]:
    if not (type(value) is list and len(value) == 4 and all(type(item) is int for item in value)):
        raise _CorruptOperatorHistory
    return value[0], value[1], value[2], value[3]


def _day_from_payload(payload: object) -> OperatorHistoryDay:
    if not _exact_fields(payload, _DAY_FIELDS):
        raise _CorruptOperatorHistory
    try:
        return OperatorHistoryDay(
            payload["day_key"],
            payload["timezone_offset_minutes"],
            ProviderIdentifier(payload["provider_id"]),
            payload["started"],
            payload["needs_user"],
            payload["completed"],
            payload["failed"],
            payload["acknowledged"],
            _bands_from_payload(payload["active_duration_bands"]),
            _bands_from_payload(payload["attention_wait_bands"]),
            payload["primary_count"],
            payload["worker_count"],
            payload["source_recoveries"],
            payload["device_recoveries"],
            HistoryCoverage(payload["coverage"]),
            payload["sample_count"],
        )
    except (HistoryValidationError, TypeError, ValueError) as error:
        raise _CorruptOperatorHistory from error


def _state_from_document(document: object) -> OperatorHistoryState:
    if not _exact_fields(document, _DOCUMENT_FIELDS):
        raise _CorruptOperatorHistory
    version = document["version"]
    if type(version) is not int:
        raise _CorruptOperatorHistory
    if version != STORE_VERSION:
        raise _UnsupportedOperatorHistory
    rows = document["days"]
    if type(rows) is not list or len(rows) > MAX_OPERATOR_HISTORY_ROWS:
        raise _CorruptOperatorHistory
    state = OperatorHistoryState(tuple(_day_from_payload(row) for row in rows))
    if len({row.provider_id for row in state.rows}) > MAX_OPERATOR_HISTORY_PROVIDERS:
        raise _CorruptOperatorHistory
    if merge_operator_history_days((), state.rows) != state.rows:
        raise _CorruptOperatorHistory
    return state


def _day_to_payload(row: OperatorHistoryDay) -> dict[str, object]:
    if type(row) is not OperatorHistoryDay:
        raise ValueError("invalid operator history row")
    return {
        "day_key": row.day_key,
        "timezone_offset_minutes": row.timezone_offset_minutes,
        "provider_id": row.provider_id.value,
        "started": row.started,
        "needs_user": row.needs_user,
        "completed": row.completed,
        "failed": row.failed,
        "acknowledged": row.acknowledged,
        "active_duration_bands": list(row.active_duration_bands),
        "attention_wait_bands": list(row.attention_wait_bands),
        "primary_count": row.primary_count,
        "worker_count": row.worker_count,
        "source_recoveries": row.source_recoveries,
        "device_recoveries": row.device_recoveries,
        "coverage": row.coverage.value,
        "sample_count": row.sample_count,
    }


def _encode_state(state: OperatorHistoryState) -> str:
    if type(state) is not OperatorHistoryState:
        raise ValueError("invalid operator history state")
    document = {
        "version": STORE_VERSION,
        "days": [_day_to_payload(row) for row in state.rows],
    }
    return json.dumps(document, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n"


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
    payload = json.dumps(
        {"diagnostic": "operator_history_corrupt", "version": 1},
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        atomic_private_write(path.with_name(path.name + ".corrupt"), payload + "\n")
    except OSError:
        pass


def load_operator_history(path: Path) -> OperatorHistoryRestore:
    """Load an exact metadata envelope with visible degraded health."""
    target = Path(path).expanduser()
    try:
        raw = read_private_text(target, max_bytes=MAX_OPERATOR_HISTORY_STORE_BYTES)
        document = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
        state = _state_from_document(document)
    except FileNotFoundError:
        return OperatorHistoryRestore(
            OperatorHistoryState(),
            OperatorHistoryRestoreHealth.MISSING,
        )
    except _UnsupportedOperatorHistory:
        return OperatorHistoryRestore(
            OperatorHistoryState(),
            OperatorHistoryRestoreHealth.UNSUPPORTED,
        )
    except OSError as error:
        if "exceeds maximum size" in str(error):
            _discard_and_diagnose(target)
        return OperatorHistoryRestore(
            OperatorHistoryState(),
            OperatorHistoryRestoreHealth.UNAVAILABLE,
        )
    except (RecursionError, TypeError, UnicodeError, ValueError):
        _discard_and_diagnose(target)
        return OperatorHistoryRestore(
            OperatorHistoryState(),
            OperatorHistoryRestoreHealth.CORRUPT,
        )
    return OperatorHistoryRestore(state, OperatorHistoryRestoreHealth.HEALTHY)


def _row_sort_key(row: OperatorHistoryDay) -> tuple[str, int, str]:
    return row.day_key, row.timezone_offset_minutes, row.provider_id.value


def _local_today(now: float, offset_minutes: int) -> date:
    offset = timezone(timedelta(minutes=offset_minutes))
    try:
        return datetime.fromtimestamp(now, offset).date()
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("operator history time is out of range") from error


def _prune_state(
    state: OperatorHistoryState,
    *,
    retention_days: int,
    now: float,
) -> OperatorHistoryState:
    merged = merge_operator_history_days((), state.rows)
    retained = tuple(
        row
        for row in merged
        if (
            date.fromordinal(_local_today(now, row.timezone_offset_minutes).toordinal() - retention_days + 1)
            <= date.fromisoformat(row.day_key)
            <= _local_today(now, row.timezone_offset_minutes)
        )
    )
    latest_by_provider: dict[ProviderIdentifier, str] = {}
    for row in retained:
        latest_by_provider[row.provider_id] = max(
            row.day_key,
            latest_by_provider.get(row.provider_id, row.day_key),
        )
    providers = tuple(
        provider
        for provider, _latest in sorted(
            latest_by_provider.items(),
            key=lambda item: (-date.fromisoformat(item[1]).toordinal(), item[0].value),
        )[:MAX_OPERATOR_HISTORY_PROVIDERS]
    )
    allowed = frozenset(providers)
    bounded = tuple(row for row in retained if row.provider_id in allowed)
    if len(bounded) > MAX_OPERATOR_HISTORY_ROWS:
        bounded = bounded[-MAX_OPERATOR_HISTORY_ROWS:]
    return OperatorHistoryState(tuple(sorted(bounded, key=_row_sort_key)))


def save_operator_history(
    path: Path,
    state: OperatorHistoryState,
    *,
    retention_days: int,
    now: float,
) -> OperatorHistoryState:
    """Prune and atomically save daily facts, or clear exactly when disabled."""
    retention = _validate_retention(retention_days)
    if type(state) is not OperatorHistoryState or not _valid_now(now):
        raise ValueError("invalid operator history save input")
    target = Path(path).expanduser()
    if retention == 0:
        with _merge_lock(target):
            try:
                _secure_unlink(target)
            except FileNotFoundError:
                pass
        return OperatorHistoryState()

    candidate = _prune_state(state, retention_days=retention, now=float(now))
    rows = candidate.rows

    def after_dropping(count: int) -> tuple[OperatorHistoryState, str]:
        selected = OperatorHistoryState(rows[count:])
        return selected, _encode_state(selected)

    candidate, encoded = after_dropping(0)
    if len(encoded.encode("utf-8")) > MAX_OPERATOR_HISTORY_STORE_BYTES:
        low = 1
        high = len(rows)
        while low < high:
            midpoint = (low + high) // 2
            _, probe = after_dropping(midpoint)
            if len(probe.encode("utf-8")) <= MAX_OPERATOR_HISTORY_STORE_BYTES:
                high = midpoint
            else:
                low = midpoint + 1
        candidate, encoded = after_dropping(low)
    if len(encoded.encode("utf-8")) > MAX_OPERATOR_HISTORY_STORE_BYTES:
        raise ValueError("operator history store exceeds maximum size")
    atomic_private_write(target, encoded)
    return candidate


@contextmanager
def _merge_lock(path: Path) -> Iterator[None]:
    """Serialize cooperating read-merge-write cycles across threads and processes."""
    with _PROCESS_STORE_LOCK:
        with _private_parent(path) as (_target, parent_descriptor, _name):
            fcntl.flock(parent_descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(parent_descriptor, fcntl.LOCK_UN)


class OperatorHistoryStore:
    """Opt-in owner of pending daily deltas and restart-safe merged state."""

    def __init__(self, path: Path, *, retention_days: int = 0) -> None:
        self.path = Path(path).expanduser()
        self.retention_days = _validate_retention(retention_days)
        self.state = OperatorHistoryState()
        self._pending_rows: tuple[OperatorHistoryDay, ...] = ()
        self._dirty = False
        self._restore_health = OperatorHistoryRestoreHealth.MISSING

    @property
    def dirty(self) -> bool:
        return self._dirty

    def restore(self) -> OperatorHistoryRestore:
        if self.retention_days == 0:
            restored = OperatorHistoryRestore(
                OperatorHistoryState(),
                OperatorHistoryRestoreHealth.MISSING,
            )
        else:
            restored = load_operator_history(self.path)
        self.state = restored.state
        self._pending_rows = ()
        self._dirty = False
        self._restore_health = restored.health
        return restored

    def add_rows(self, rows: tuple[OperatorHistoryDay, ...]) -> bool:
        if not (type(rows) is tuple and all(type(row) is OperatorHistoryDay for row in rows)):
            raise ValueError("invalid operator history rows")
        if self.retention_days == 0 or not rows:
            return False
        try:
            pending = merge_operator_history_days(self._pending_rows, rows)
            combined = merge_operator_history_days(self.state.rows, rows)
        except HistoryValidationError as error:
            raise ValueError("invalid operator history rows") from error
        self._pending_rows = pending
        self.state = OperatorHistoryState(combined)
        self._dirty = True
        return True

    def flush(self, *, now: float) -> bool:
        if not _valid_now(now):
            raise ValueError("invalid operator history flush time")
        if self.retention_days == 0:
            return False
        with _merge_lock(self.path):
            if not self._dirty:
                return False
            restored = load_operator_history(self.path)
            if restored.health not in {
                OperatorHistoryRestoreHealth.HEALTHY,
                OperatorHistoryRestoreHealth.MISSING,
            }:
                self._restore_health = restored.health
                raise OSError("operator history store is not safely mergeable")
            merged = merge_operator_history_days(
                restored.state.rows,
                self._pending_rows,
            )
            saved = save_operator_history(
                self.path,
                OperatorHistoryState(merged),
                retention_days=self.retention_days,
                now=float(now),
            )
        self.state = saved
        self._pending_rows = ()
        self._dirty = False
        self._restore_health = OperatorHistoryRestoreHealth.HEALTHY
        return True

    def project(
        self,
        *,
        range_days: int,
        now: float,
        timezone_offset_minutes: int = 0,
    ) -> OperatorHistoryProjection:
        projection = project_operator_history(
            self.state.rows,
            range_days=range_days,
            now=now,
            timezone_offset_minutes=timezone_offset_minutes,
        )
        if self._restore_health in {
            OperatorHistoryRestoreHealth.CORRUPT,
            OperatorHistoryRestoreHealth.UNSUPPORTED,
            OperatorHistoryRestoreHealth.UNAVAILABLE,
        }:
            return OperatorHistoryProjection(
                range_days,
                0,
                range_days,
                (),
                ("Operator history could not be restored.",),
                "History unavailable",
            )
        return projection

    def clear(self) -> bool:
        with _merge_lock(self.path):
            had_memory = self.state != OperatorHistoryState() or self._dirty
            try:
                removed = _secure_unlink(self.path)
            except FileNotFoundError:
                removed = False
            self.state = OperatorHistoryState()
            self._pending_rows = ()
            self._dirty = False
            self._restore_health = OperatorHistoryRestoreHealth.MISSING
            return had_memory or removed
