"""Pure, bounded per-source capacity refresh state.

The coordinator owns only typed intent, generation fences, deadlines, cooldown
boundaries, and last-known-good snapshots. It performs no source work, I/O,
threading, timer scheduling, or presentation.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from functools import total_ordering
from typing import Final

from .capacity_types import (
    CapacityAccountBinding,
    CapacitySnapshot,
    SourceKey,
    is_safe_opaque_account_discriminator,
)
from .refresh_policy import RetryScheduleKind, resolve_retry_schedule

MAX_REFRESH_SOURCE_RECORDS: Final = 16
MAX_REFRESH_IDENTITY_LENGTH: Final = 64
MAX_REFRESH_DEADLINE_SECONDS: Final = 300.0

_POOL_IDENTIFIER: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")


class RefreshValidationError(ValueError):
    """A refresh input failed closed at the pure domain boundary."""


class RefreshCause(str, Enum):
    AUTOMATIC = "automatic"
    MENU_OPEN = "menu_open"
    MANUAL = "manual"


class RefreshDecisionKind(str, Enum):
    START = "start"
    COALESCED = "coalesced"
    QUEUED_FOR_COOLDOWN = "queued_for_cooldown"
    DISABLED = "disabled"
    UNSUPPORTED = "unsupported"


class RefreshDecisionReason(str, Enum):
    ELIGIBLE = "eligible"
    IN_FLIGHT = "in_flight"
    COOLDOWN = "cooldown"
    ALREADY_QUEUED = "already_queued"
    NO_QUEUED_REFRESH = "no_queued_refresh"
    DISABLED = "disabled"
    UNSUPPORTED = "unsupported"


class RefreshCommitKind(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMED_OUT = "timed_out"
    STALE_GENERATION = "stale_generation"
    NOT_DUE = "not_due"


class RefreshFailureKind(str, Enum):
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SIGN_IN_REQUIRED = "sign_in_required"
    ACCESS_DENIED = "access_denied"
    SOURCE_UNAVAILABLE = "source_unavailable"


class RefreshStatusKind(str, Enum):
    IDLE = "idle"
    HEALTHY = "healthy"
    REFRESHING = "refreshing"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SIGN_IN_REQUIRED = "sign_in_required"
    ACCESS_DENIED = "access_denied"


def _is_time(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0.0


def _validated_time(value: object) -> float:
    if not _is_time(value):
        raise RefreshValidationError("invalid refresh time")
    return float(value)


def _valid_identity(value: object, pattern: re.Pattern[str]) -> bool:
    return (
        type(value) is str and 1 <= len(value) <= MAX_REFRESH_IDENTITY_LENGTH and pattern.fullmatch(value) is not None
    )


@total_ordering
@dataclass(frozen=True, slots=True)
class RefreshSourceKey:
    source: SourceKey
    pool: str
    account_discriminator: str | None
    auth_mode: str | None = None

    def __post_init__(self) -> None:
        if not (
            type(self.source) is SourceKey
            and _valid_identity(self.pool, _POOL_IDENTIFIER)
            and (
                self.account_discriminator is None
                or (
                    is_safe_opaque_account_discriminator(self.account_discriminator)
                )
            )
            and (self.auth_mode is None or _valid_identity(self.auth_mode, _POOL_IDENTIFIER))
        ):
            raise RefreshValidationError("invalid refresh source key")

    def _ordering_key(self) -> tuple[SourceKey, str, str, str]:
        return (self.source, self.pool, self.account_discriminator or "", self.auth_mode or "")

    def __lt__(self, other: object) -> bool:
        if type(other) is not RefreshSourceKey:
            return NotImplemented
        return self._ordering_key() < other._ordering_key()


@dataclass(frozen=True, slots=True)
class RefreshSourceRegistration:
    key: RefreshSourceKey
    enabled: bool
    supported: bool
    binding: CapacityAccountBinding | None = None

    def __post_init__(self) -> None:
        if not (
            type(self.key) is RefreshSourceKey
            and type(self.enabled) is bool
            and type(self.supported) is bool
            and (self.binding is None or type(self.binding) is CapacityAccountBinding)
        ):
            raise RefreshValidationError("invalid refresh source registration")
        if self.binding is not None and (
            self.binding.source != self.key.source
            or self.binding.pool_id != self.key.pool
            or self.binding.opaque_account_id != self.key.account_discriminator
            or self.binding.auth_mode != self.key.auth_mode
        ):
            raise RefreshValidationError("invalid refresh source registration binding")


@dataclass(frozen=True, slots=True)
class RefreshDecision:
    kind: RefreshDecisionKind
    key: RefreshSourceKey
    cause: RefreshCause
    generation: int | None
    retry_at: float | None
    reason: RefreshDecisionReason

    def __post_init__(self) -> None:
        if not (
            type(self.kind) is RefreshDecisionKind
            and type(self.key) is RefreshSourceKey
            and type(self.cause) is RefreshCause
            and (self.generation is None or (type(self.generation) is int and self.generation > 0))
            and (self.retry_at is None or _is_time(self.retry_at))
            and type(self.reason) is RefreshDecisionReason
        ):
            raise RefreshValidationError("invalid refresh decision")
        if self.kind is RefreshDecisionKind.START and (
            self.generation is None or self.retry_at is not None or self.reason is not RefreshDecisionReason.ELIGIBLE
        ):
            raise RefreshValidationError("invalid start decision")
        if self.kind is RefreshDecisionKind.QUEUED_FOR_COOLDOWN and (
            self.generation is not None or self.retry_at is None or self.reason is not RefreshDecisionReason.COOLDOWN
        ):
            raise RefreshValidationError("invalid queued refresh decision")
        if self.kind is RefreshDecisionKind.DISABLED and (
            self.generation is not None
            or self.retry_at is not None
            or self.reason is not RefreshDecisionReason.DISABLED
        ):
            raise RefreshValidationError("invalid disabled refresh decision")
        if self.kind is RefreshDecisionKind.UNSUPPORTED and (
            self.generation is not None
            or self.retry_at is not None
            or self.reason is not RefreshDecisionReason.UNSUPPORTED
        ):
            raise RefreshValidationError("invalid unsupported refresh decision")
        if self.retry_at is not None:
            object.__setattr__(self, "retry_at", float(self.retry_at))


@dataclass(frozen=True, slots=True)
class RefreshCommit:
    kind: RefreshCommitKind
    key: RefreshSourceKey
    generation: int
    committed_at: float
    retry_at: float | None
    has_last_known_good: bool
    failure_kind: RefreshFailureKind | None

    def __post_init__(self) -> None:
        if not (
            type(self.kind) is RefreshCommitKind
            and type(self.key) is RefreshSourceKey
            and type(self.generation) is int
            and self.generation > 0
            and _is_time(self.committed_at)
            and (self.retry_at is None or _is_time(self.retry_at))
            and type(self.has_last_known_good) is bool
            and (self.failure_kind is None or type(self.failure_kind) is RefreshFailureKind)
        ):
            raise RefreshValidationError("invalid refresh commit")
        object.__setattr__(self, "committed_at", float(self.committed_at))
        if self.retry_at is not None:
            object.__setattr__(self, "retry_at", float(self.retry_at))
        if self.kind is RefreshCommitKind.SUCCESS and (self.retry_at is not None or self.failure_kind is not None):
            raise RefreshValidationError("invalid success commit")
        if self.kind in {RefreshCommitKind.FAILURE, RefreshCommitKind.TIMED_OUT} and (
            self.retry_at is None or self.failure_kind is None
        ):
            raise RefreshValidationError("invalid failure commit")
        if (self.kind is RefreshCommitKind.TIMED_OUT and self.failure_kind is not RefreshFailureKind.TIMED_OUT) or (
            self.kind is RefreshCommitKind.FAILURE and self.failure_kind is RefreshFailureKind.TIMED_OUT
        ):
            raise RefreshValidationError("invalid failure commit")
        if (
            self.kind
            in {
                RefreshCommitKind.STALE_GENERATION,
                RefreshCommitKind.NOT_DUE,
            }
            and self.failure_kind is not None
        ):
            raise RefreshValidationError("invalid refusal commit")


@dataclass(frozen=True, slots=True)
class RefreshSourceState:
    key: RefreshSourceKey
    enabled: bool
    supported: bool
    generation: int
    in_flight: bool
    active_cause: RefreshCause | None
    deadline: float | None
    queued_manual: bool
    status: RefreshStatusKind
    last_attempt_at: float | None
    last_success_at: float | None
    retry_at: float | None
    retry_schedule: RetryScheduleKind | None
    consecutive_failures: int
    last_failure: RefreshFailureKind | None
    last_known_good: CapacitySnapshot | None
    has_last_known_good: bool

    def __post_init__(self) -> None:
        optional_times = (
            self.deadline,
            self.last_attempt_at,
            self.last_success_at,
            self.retry_at,
        )
        if not (
            type(self.key) is RefreshSourceKey
            and type(self.enabled) is bool
            and type(self.supported) is bool
            and type(self.generation) is int
            and self.generation >= 0
            and type(self.in_flight) is bool
            and (self.active_cause is None or type(self.active_cause) is RefreshCause)
            and all(value is None or _is_time(value) for value in optional_times)
            and type(self.queued_manual) is bool
            and type(self.status) is RefreshStatusKind
            and (self.retry_schedule is None or type(self.retry_schedule) is RetryScheduleKind)
            and type(self.consecutive_failures) is int
            and self.consecutive_failures >= 0
            and (self.last_failure is None or type(self.last_failure) is RefreshFailureKind)
            and (self.last_known_good is None or type(self.last_known_good) is CapacitySnapshot)
            and type(self.has_last_known_good) is bool
        ):
            raise RefreshValidationError("invalid refresh source state")
        if self.in_flight != (self.active_cause is not None):
            raise RefreshValidationError("invalid active refresh state")
        if not self.in_flight and self.deadline is not None:
            raise RefreshValidationError("invalid active refresh deadline")
        if self.in_flight and (self.retry_at is not None or self.retry_schedule is not None):
            raise RefreshValidationError("invalid active refresh cooldown")
        if self.status is RefreshStatusKind.REFRESHING and not self.in_flight:
            raise RefreshValidationError("invalid refreshing state")
        if self.in_flight and self.status is not RefreshStatusKind.REFRESHING:
            raise RefreshValidationError("invalid refreshing state")
        if self.queued_manual and self.retry_at is None:
            raise RefreshValidationError("invalid queued refresh state")
        if self.has_last_known_good != (self.last_known_good is not None):
            raise RefreshValidationError("invalid last-known-good state")
        if self.last_known_good is not None and self.last_success_at is None:
            raise RefreshValidationError("invalid last-known-good state")
        if (self.consecutive_failures == 0) != (self.last_failure is None):
            raise RefreshValidationError("invalid refresh failure state")
        for field_name in (
            "deadline",
            "last_attempt_at",
            "last_success_at",
            "retry_at",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, float(value))


@dataclass(frozen=True, slots=True)
class RefreshCoordinatorSnapshot:
    observed_at: float
    sources: tuple[RefreshSourceState, ...]

    def __post_init__(self) -> None:
        if not (
            _is_time(self.observed_at)
            and type(self.sources) is tuple
            and len(self.sources) <= MAX_REFRESH_SOURCE_RECORDS
            and all(type(source) is RefreshSourceState for source in self.sources)
        ):
            raise RefreshValidationError("invalid refresh coordinator snapshot")
        keys = tuple(source.key for source in self.sources)
        if len(keys) != len(set(keys)) or keys != tuple(sorted(keys)):
            raise RefreshValidationError("invalid refresh coordinator source order")
        object.__setattr__(self, "observed_at", float(self.observed_at))


@dataclass(slots=True)
class _RefreshRecord:
    registration: RefreshSourceRegistration
    generation: int = 0
    in_flight: bool = False
    active_cause: RefreshCause | None = None
    deadline: float | None = None
    queued_manual: bool = False
    last_attempt_at: float | None = None
    last_success_at: float | None = None
    retry_at: float | None = None
    retry_schedule: RetryScheduleKind | None = None
    consecutive_failures: int = 0
    last_failure: RefreshFailureKind | None = None
    last_known_good: CapacitySnapshot | None = None


class CapacityRefreshCoordinator:
    """Own bounded refresh intent and publication authority for exact sources."""

    def __init__(
        self,
        registrations: tuple[RefreshSourceRegistration, ...],
    ) -> None:
        if not (
            type(registrations) is tuple
            and len(registrations) <= MAX_REFRESH_SOURCE_RECORDS
            and all(type(item) is RefreshSourceRegistration for item in registrations)
        ):
            raise RefreshValidationError("invalid refresh source registrations")
        if len({item.key for item in registrations}) != len(registrations):
            raise RefreshValidationError("duplicate refresh source registration")
        self._records = {item.key: _RefreshRecord(registration=item) for item in registrations}

    def invalidate_source(
        self,
        key: RefreshSourceKey,
        *,
        now: float,
    ) -> RefreshSourceState:
        """Fence prior work and reset one exact source to registered idle state."""
        reference = _validated_time(now)
        record = self._known_record(key)
        record.generation += 1
        record.in_flight = False
        record.active_cause = None
        record.deadline = None
        record.queued_manual = False
        record.last_attempt_at = None
        record.last_success_at = None
        record.retry_at = None
        record.retry_schedule = None
        record.consecutive_failures = 0
        record.last_failure = None
        record.last_known_good = None
        return self._public_state(record, reference)

    def request_refresh(
        self,
        key: RefreshSourceKey,
        cause: RefreshCause,
        now: float,
    ) -> RefreshDecision:
        reference = _validated_time(now)
        if type(key) is not RefreshSourceKey or type(cause) is not RefreshCause:
            raise RefreshValidationError("invalid refresh request")
        record = self._records.get(key)
        if record is None or not record.registration.supported:
            return RefreshDecision(
                RefreshDecisionKind.UNSUPPORTED,
                key,
                cause,
                None,
                None,
                RefreshDecisionReason.UNSUPPORTED,
            )
        if not record.registration.enabled:
            return RefreshDecision(
                RefreshDecisionKind.DISABLED,
                key,
                cause,
                None,
                None,
                RefreshDecisionReason.DISABLED,
            )
        if record.in_flight:
            return RefreshDecision(
                RefreshDecisionKind.COALESCED,
                key,
                cause,
                record.generation,
                None,
                RefreshDecisionReason.IN_FLIGHT,
            )
        if record.retry_at is not None and reference < record.retry_at:
            if cause is RefreshCause.MANUAL and not record.queued_manual:
                record.queued_manual = True
                return RefreshDecision(
                    RefreshDecisionKind.QUEUED_FOR_COOLDOWN,
                    key,
                    cause,
                    None,
                    record.retry_at,
                    RefreshDecisionReason.COOLDOWN,
                )
            return RefreshDecision(
                RefreshDecisionKind.COALESCED,
                key,
                cause,
                None,
                record.retry_at,
                (
                    RefreshDecisionReason.ALREADY_QUEUED
                    if cause is RefreshCause.MANUAL and record.queued_manual
                    else RefreshDecisionReason.COOLDOWN
                ),
            )
        if record.queued_manual:
            cause = RefreshCause.MANUAL
            record.queued_manual = False
        return self._reserve_start(record, cause, reference)

    def register_started(
        self,
        key: RefreshSourceKey,
        generation: int,
        deadline: float,
    ) -> None:
        record = self._known_record(key)
        deadline_value = _validated_time(deadline)
        if not (
            type(generation) is int
            and generation > 0
            and record.in_flight
            and record.generation == generation
            and record.last_attempt_at is not None
            and deadline_value > record.last_attempt_at
            and deadline_value - record.last_attempt_at <= MAX_REFRESH_DEADLINE_SECONDS
        ):
            raise RefreshValidationError("invalid refresh start")
        if record.deadline is not None and record.deadline != deadline_value:
            raise RefreshValidationError("refresh deadline already registered")
        record.deadline = deadline_value

    def register_success(
        self,
        key: RefreshSourceKey,
        generation: int,
        snapshot: CapacitySnapshot,
        completed_at: float,
    ) -> RefreshCommit:
        record = self._known_record(key)
        completed = _validated_time(completed_at)
        if type(snapshot) is not CapacitySnapshot:
            raise RefreshValidationError("invalid capacity refresh snapshot")
        if not self._is_current(record, generation):
            return self._stale_commit(record, generation, completed)
        self._validate_completion(record, completed)
        self._validate_snapshot(key, snapshot)
        assert record.deadline is not None
        if completed >= record.deadline:
            return self._apply_failure(
                record,
                failure_kind=RefreshFailureKind.TIMED_OUT,
                completed_at=completed,
                retry_at=None,
            )
        record.in_flight = False
        record.active_cause = None
        record.deadline = None
        record.last_success_at = completed
        record.retry_at = None
        record.retry_schedule = None
        record.consecutive_failures = 0
        record.last_failure = None
        record.last_known_good = snapshot if snapshot.lanes else None
        return RefreshCommit(
            RefreshCommitKind.SUCCESS,
            key,
            generation,
            completed,
            None,
            record.last_known_good is not None,
            None,
        )

    def register_failure(
        self,
        key: RefreshSourceKey,
        generation: int,
        failure_kind: RefreshFailureKind,
        completed_at: float,
        retry_at: float | None,
    ) -> RefreshCommit:
        record = self._known_record(key)
        completed = _validated_time(completed_at)
        if type(failure_kind) is not RefreshFailureKind:
            raise RefreshValidationError("invalid refresh failure")
        if retry_at is not None:
            _validated_time(retry_at)
        if not self._is_current(record, generation):
            return self._stale_commit(record, generation, completed)
        self._validate_completion(record, completed)
        assert record.deadline is not None
        if completed >= record.deadline:
            return self._apply_failure(
                record,
                failure_kind=RefreshFailureKind.TIMED_OUT,
                completed_at=completed,
                retry_at=None,
            )
        return self._apply_failure(
            record,
            failure_kind=failure_kind,
            completed_at=completed,
            retry_at=retry_at,
        )

    def expire_deadline(
        self,
        key: RefreshSourceKey,
        generation: int,
        now: float,
    ) -> RefreshCommit:
        record = self._known_record(key)
        reference = _validated_time(now)
        if not self._is_current(record, generation):
            return self._stale_commit(record, generation, reference)
        if record.deadline is None or reference < record.deadline:
            return RefreshCommit(
                RefreshCommitKind.NOT_DUE,
                key,
                generation,
                reference,
                record.deadline,
                record.last_known_good is not None,
                None,
            )
        return self._apply_failure(
            record,
            failure_kind=RefreshFailureKind.TIMED_OUT,
            completed_at=reference,
            retry_at=None,
        )

    def take_due_queued_refresh(
        self,
        key: RefreshSourceKey,
        now: float,
    ) -> RefreshDecision:
        reference = _validated_time(now)
        if type(key) is not RefreshSourceKey:
            raise RefreshValidationError("invalid refresh source key")
        record = self._records.get(key)
        if record is None or not record.registration.supported:
            return RefreshDecision(
                RefreshDecisionKind.UNSUPPORTED,
                key,
                RefreshCause.MANUAL,
                None,
                None,
                RefreshDecisionReason.UNSUPPORTED,
            )
        if not record.registration.enabled:
            return RefreshDecision(
                RefreshDecisionKind.DISABLED,
                key,
                RefreshCause.MANUAL,
                None,
                None,
                RefreshDecisionReason.DISABLED,
            )
        if record.in_flight:
            return RefreshDecision(
                RefreshDecisionKind.COALESCED,
                key,
                RefreshCause.MANUAL,
                record.generation,
                None,
                RefreshDecisionReason.IN_FLIGHT,
            )
        if not record.queued_manual:
            return RefreshDecision(
                RefreshDecisionKind.COALESCED,
                key,
                RefreshCause.MANUAL,
                None,
                record.retry_at,
                RefreshDecisionReason.NO_QUEUED_REFRESH,
            )
        if record.retry_at is not None and reference < record.retry_at:
            return RefreshDecision(
                RefreshDecisionKind.COALESCED,
                key,
                RefreshCause.MANUAL,
                None,
                record.retry_at,
                RefreshDecisionReason.COOLDOWN,
            )
        record.queued_manual = False
        return self._reserve_start(record, RefreshCause.MANUAL, reference)

    def snapshot_state(self, now: float) -> RefreshCoordinatorSnapshot:
        reference = _validated_time(now)
        return RefreshCoordinatorSnapshot(
            observed_at=reference,
            sources=tuple(self._public_state(self._records[key], reference) for key in sorted(self._records)),
        )

    @staticmethod
    def _is_current(record: _RefreshRecord, generation: int) -> bool:
        return type(generation) is int and generation > 0 and record.in_flight and record.generation == generation

    @staticmethod
    def _reserve_start(
        record: _RefreshRecord,
        cause: RefreshCause,
        now: float,
    ) -> RefreshDecision:
        record.generation += 1
        record.in_flight = True
        record.active_cause = cause
        record.deadline = None
        record.last_attempt_at = now
        record.retry_at = None
        record.retry_schedule = None
        return RefreshDecision(
            RefreshDecisionKind.START,
            record.registration.key,
            cause,
            record.generation,
            None,
            RefreshDecisionReason.ELIGIBLE,
        )

    @staticmethod
    def _validate_snapshot(
        key: RefreshSourceKey,
        snapshot: CapacitySnapshot,
    ) -> None:
        if any(
            lane.key.source != key.source
            or lane.key.pool != key.pool
            or lane.account_discriminator != key.account_discriminator
            or lane.auth_mode != key.auth_mode
            for lane in snapshot.lanes
        ) or any(health.source != key.source for health in snapshot.source_health):
            raise RefreshValidationError("cross-scope capacity refresh snapshot")

    @staticmethod
    def _validate_completion(record: _RefreshRecord, completed_at: float) -> None:
        if record.last_attempt_at is None or record.deadline is None or completed_at < record.last_attempt_at:
            raise RefreshValidationError("invalid refresh completion")

    @staticmethod
    def _apply_failure(
        record: _RefreshRecord,
        *,
        failure_kind: RefreshFailureKind,
        completed_at: float,
        retry_at: float | None,
    ) -> RefreshCommit:
        failures = record.consecutive_failures + 1
        try:
            schedule = resolve_retry_schedule(
                completed_at=completed_at,
                consecutive_failures=failures,
                retry_at=retry_at,
            )
        except ValueError as exc:
            raise RefreshValidationError("invalid refresh retry boundary") from exc
        record.in_flight = False
        record.active_cause = None
        record.deadline = None
        record.consecutive_failures = failures
        record.last_failure = failure_kind
        record.retry_at = schedule.retry_not_before
        record.retry_schedule = schedule.kind
        commit_kind = (
            RefreshCommitKind.TIMED_OUT if failure_kind is RefreshFailureKind.TIMED_OUT else RefreshCommitKind.FAILURE
        )
        return RefreshCommit(
            commit_kind,
            record.registration.key,
            record.generation,
            completed_at,
            record.retry_at,
            record.last_known_good is not None,
            failure_kind,
        )

    @staticmethod
    def _stale_commit(
        record: _RefreshRecord,
        generation: int,
        completed_at: float,
    ) -> RefreshCommit:
        if type(generation) is not int or generation <= 0:
            raise RefreshValidationError("invalid refresh generation")
        return RefreshCommit(
            RefreshCommitKind.STALE_GENERATION,
            record.registration.key,
            generation,
            completed_at,
            record.retry_at,
            record.last_known_good is not None,
            None,
        )

    def _known_record(self, key: RefreshSourceKey) -> _RefreshRecord:
        if type(key) is not RefreshSourceKey:
            raise RefreshValidationError("invalid refresh source key")
        record = self._records.get(key)
        if record is None:
            raise RefreshValidationError("unknown refresh source key")
        return record

    @staticmethod
    def _public_state(
        record: _RefreshRecord,
        now: float,
    ) -> RefreshSourceState:
        registration = record.registration
        if not registration.supported:
            status = RefreshStatusKind.UNSUPPORTED
        elif not registration.enabled:
            status = RefreshStatusKind.DISABLED
        elif record.in_flight:
            status = RefreshStatusKind.REFRESHING
        elif record.retry_at is not None and now < record.retry_at:
            status = RefreshStatusKind.COOLDOWN
        elif record.last_failure is RefreshFailureKind.TIMED_OUT:
            status = RefreshStatusKind.TIMED_OUT
        elif record.last_failure is RefreshFailureKind.SIGN_IN_REQUIRED:
            status = RefreshStatusKind.SIGN_IN_REQUIRED
        elif record.last_failure is RefreshFailureKind.ACCESS_DENIED:
            status = RefreshStatusKind.ACCESS_DENIED
        elif record.last_failure is not None:
            status = RefreshStatusKind.FAILED
        elif record.last_success_at is not None:
            status = RefreshStatusKind.HEALTHY
        else:
            status = RefreshStatusKind.IDLE
        return RefreshSourceState(
            key=registration.key,
            enabled=registration.enabled,
            supported=registration.supported,
            generation=record.generation,
            in_flight=record.in_flight,
            active_cause=record.active_cause,
            deadline=record.deadline,
            queued_manual=record.queued_manual,
            status=status,
            last_attempt_at=record.last_attempt_at,
            last_success_at=record.last_success_at,
            retry_at=record.retry_at,
            retry_schedule=record.retry_schedule,
            consecutive_failures=record.consecutive_failures,
            last_failure=record.last_failure,
            last_known_good=record.last_known_good,
            has_last_known_good=record.last_known_good is not None,
        )


__all__ = [
    "MAX_REFRESH_DEADLINE_SECONDS",
    "MAX_REFRESH_IDENTITY_LENGTH",
    "MAX_REFRESH_SOURCE_RECORDS",
    "CapacityRefreshCoordinator",
    "RefreshCause",
    "RefreshCommit",
    "RefreshCommitKind",
    "RefreshCoordinatorSnapshot",
    "RefreshDecision",
    "RefreshDecisionKind",
    "RefreshDecisionReason",
    "RefreshFailureKind",
    "RefreshSourceKey",
    "RefreshSourceRegistration",
    "RefreshSourceState",
    "RefreshStatusKind",
    "RefreshValidationError",
]
