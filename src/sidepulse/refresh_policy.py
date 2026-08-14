"""Pure provider refresh planning and immutable state transitions."""

from __future__ import annotations

import math
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from enum import Enum

from .capacity_types import SourceKey
from .freshness import FUTURE_CLOCK_SKEW_SECONDS

DEFAULT_FRESH_SECONDS = 300.0
LOW_POWER_FRESH_SECONDS = 900.0
RETRY_BASE_SECONDS = 15.0
RETRY_CAP_SECONDS = 300.0
MAX_ATTEMPTED_BOUNDARY_KEYS = 64
MAX_RETRY_AFTER_SECONDS = 3_600.0


class RetryScheduleKind(str, Enum):
    EXPONENTIAL_BACKOFF = "exponential_backoff"
    RETRY_AFTER = "retry_after"


@dataclass(frozen=True, slots=True)
class RetrySchedule:
    kind: RetryScheduleKind
    retry_not_before: float

    def __post_init__(self) -> None:
        if not (
            type(self.kind) is RetryScheduleKind
            and type(self.retry_not_before) in {int, float}
            and math.isfinite(self.retry_not_before)
            and self.retry_not_before >= 0.0
        ):
            raise ValueError("invalid retry schedule")
        object.__setattr__(self, "retry_not_before", float(self.retry_not_before))


@dataclass(frozen=True, slots=True)
class ProviderRefreshState:
    source_key: SourceKey
    enabled: bool = True
    visible: bool = True
    last_success_at: float | None = None
    in_flight: bool = False
    consecutive_failures: int = 0
    retry_not_before: float = 0.0
    error_text: str | None = None
    generation: int = 0

    def __post_init__(self) -> None:
        if type(self.source_key) is not SourceKey:
            raise ValueError("invalid refresh source key")

    @property
    def provider_id(self) -> str:
        """Temporary compatibility projection for provider-only consumers."""
        return self.source_key.provider_id


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    invocations: tuple[SourceKey, ...]


def _deterministic_keys(values) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    elif isinstance(values, AbstractSet):
        values = sorted(str(value) for value in values)
    return tuple(str(value) for value in values if str(value))


def retain_attempted_boundary_keys(existing, additions) -> tuple[str, ...]:
    """Append unique reset attempts and retain the newest 64 caller-ordered keys."""
    ordered: list[str] = []
    seen: set[str] = set()
    for key in (*_deterministic_keys(existing), *_deterministic_keys(additions)):
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return tuple(ordered[-MAX_ATTEMPTED_BOUNDARY_KEYS:])


def retry_delay_seconds(consecutive_failures: int) -> float:
    """Return deterministic bounded exponential delay for a failure count."""
    if consecutive_failures <= 0:
        return 0.0
    exponent = min(int(consecutive_failures) - 1, 30)
    return min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * (2**exponent))


def resolve_retry_schedule(
    *,
    completed_at: float,
    consecutive_failures: int,
    retry_at: float | None,
) -> RetrySchedule:
    """Resolve an explicit bounded retry boundary for one failed attempt."""
    if not (
        type(completed_at) in {int, float}
        and math.isfinite(completed_at)
        and completed_at >= 0.0
        and type(consecutive_failures) is int
        and consecutive_failures > 0
    ):
        raise ValueError("invalid retry schedule input")
    completed = float(completed_at)
    if retry_at is None:
        return RetrySchedule(
            kind=RetryScheduleKind.EXPONENTIAL_BACKOFF,
            retry_not_before=completed + retry_delay_seconds(consecutive_failures),
        )
    if not (type(retry_at) in {int, float} and math.isfinite(retry_at) and retry_at >= 0.0):
        raise ValueError("invalid retry schedule input")
    return RetrySchedule(
        kind=RetryScheduleKind.RETRY_AFTER,
        retry_not_before=min(
            max(completed, float(retry_at)),
            completed + MAX_RETRY_AFTER_SECONDS,
        ),
    )


def _elapsed(now: float, timestamp: float) -> float:
    age = float(now) - float(timestamp)
    if age >= 0.0:
        return age
    if -age <= FUTURE_CLOCK_SKEW_SECONDS:
        return 0.0
    return float("inf")


def plan_menu_open_refresh(
    provider_states,
    now: float,
    low_power: bool,
) -> RefreshPlan:
    """Select due capability sources once, retaining caller-supplied order."""
    if not math.isfinite(now):
        return RefreshPlan(invocations=())
    fresh_seconds = LOW_POWER_FRESH_SECONDS if low_power else DEFAULT_FRESH_SECONDS
    selected: list[SourceKey] = []
    seen: set[SourceKey] = set()
    for state in provider_states:
        source_key = state.source_key
        if source_key in seen:
            continue
        seen.add(source_key)
        if not state.enabled or not state.visible:
            continue
        retry_not_before = float(state.retry_not_before)
        if not math.isfinite(retry_not_before):
            continue
        if state.in_flight or retry_not_before > float(now):
            continue
        missing = state.last_success_at is None
        stale = not missing and _elapsed(now, state.last_success_at) >= fresh_seconds
        if missing or stale:
            selected.append(source_key)
    return RefreshPlan(invocations=tuple(selected))


def mark_refresh_started(
    state: ProviderRefreshState,
    *,
    generation: int | None = None,
) -> ProviderRefreshState:
    return replace(
        state,
        in_flight=True,
        generation=state.generation + 1 if generation is None else int(generation),
    )


def mark_refresh_failed(
    state: ProviderRefreshState,
    *,
    now: float,
    error_text: str,
) -> ProviderRefreshState:
    failures = max(0, int(state.consecutive_failures)) + 1
    return replace(
        state,
        in_flight=False,
        consecutive_failures=failures,
        retry_not_before=float(now) + retry_delay_seconds(failures),
        error_text=str(error_text).strip() or "unavailable",
    )


def mark_refresh_succeeded(
    state: ProviderRefreshState,
    *,
    now: float,
) -> ProviderRefreshState:
    return replace(
        state,
        last_success_at=float(now),
        in_flight=False,
        consecutive_failures=0,
        retry_not_before=0.0,
        error_text=None,
    )
