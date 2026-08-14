"""Bounded, capability-scoped provider invocation runtime.

Adapters run in independent daemon threads. The common runtime owns only typed
results, deadlines, generation fences, and privacy-safe cooldown identifiers.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .capacity_types import SourceKey
from .provider_facts import ProviderFactBatch, ProviderQuotaWindow
from .refresh_policy import retry_delay_seconds

MAX_PROVIDER_SOURCES: Final = 32
MAX_INVOCATIONS_PER_REQUEST: Final = 32
MAX_DEADLINE_SECONDS: Final = 300.0
MAX_SHUTDOWN_SECONDS: Final = 5.0
MAX_QUOTA_WINDOWS_PER_RESULT: Final = 128
MAX_COOLDOWN_SECONDS: Final = 3_600.0
MAX_COOLDOWNS_PER_SOURCE: Final = 8
_OPAQUE_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:\-]{0,63}")
_PRIVATE_IDENTIFIER_COMPONENT: Final = re.compile(
    r"(?:^|[._~:\-])"
    r"(?:api[_-]?key|authorization|bearer|cookie|credential|password|passwd|"
    r"private[_-]?key|secret|token)"
    r"(?:$|[._~:\-])",
    re.IGNORECASE,
)
_PRODUCT_DIAGNOSTIC_CODES: Final = frozenset(
    {
        "access_denied",
        "adapter_failed",
        "provider_timed_out",
        "rate_limited",
        "sign_in_required",
        "source_unavailable",
        "unsupported_source",
    }
)


class ProviderOutcomeKind(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    COOLDOWN = "cooldown"
    AUTH_REQUIRED = "auth_required"
    ACCESS_DENIED = "access_denied"
    UNSUPPORTED = "unsupported"


class RefreshTrigger(str, Enum):
    AUTOMATIC = "automatic"
    MENU_OPEN = "menu_open"
    MANUAL = "manual"


@dataclass(frozen=True, order=True, slots=True)
class OpaqueScopeIdentifier:
    value: str

    def __post_init__(self) -> None:
        if (
            type(self.value) is not str
            or _OPAQUE_IDENTIFIER.fullmatch(self.value) is None
            or _PRIVATE_IDENTIFIER_COMPONENT.search(self.value) is not None
        ):
            raise ValueError("invalid opaque scope identifier")


@dataclass(frozen=True, order=True, slots=True)
class CooldownKey:
    source_key: SourceKey
    opaque_scope: OpaqueScopeIdentifier

    def __post_init__(self) -> None:
        if type(self.source_key) is not SourceKey or type(self.opaque_scope) is not OpaqueScopeIdentifier:
            raise ValueError("invalid cooldown key")


@dataclass(frozen=True, slots=True)
class ProviderRuntimeDiagnostic:
    code: str

    def __post_init__(self) -> None:
        if type(self.code) is not str or self.code not in _PRODUCT_DIAGNOSTIC_CODES:
            raise ValueError("invalid provider runtime diagnostic")


@dataclass(frozen=True, slots=True)
class ProviderInvocation:
    source_key: SourceKey
    generation: int
    deadline_seconds: float
    trigger: RefreshTrigger
    cooldown_scope: OpaqueScopeIdentifier | None = None

    def __post_init__(self) -> None:
        if not (
            type(self.source_key) is SourceKey
            and type(self.generation) is int
            and self.generation > 0
            and type(self.deadline_seconds) in {int, float}
            and math.isfinite(self.deadline_seconds)
            and 0.0 < self.deadline_seconds <= MAX_DEADLINE_SECONDS
            and type(self.trigger) is RefreshTrigger
            and (self.cooldown_scope is None or type(self.cooldown_scope) is OpaqueScopeIdentifier)
        ):
            raise ValueError("invalid provider invocation")
        object.__setattr__(self, "deadline_seconds", float(self.deadline_seconds))


@dataclass(frozen=True, slots=True)
class ProviderResult:
    invocation: ProviderInvocation
    outcome: ProviderOutcomeKind
    fact_batch: ProviderFactBatch | None
    quota_windows: tuple[ProviderQuotaWindow, ...]
    cooldown_key: CooldownKey | None
    retry_not_before: float | None
    diagnostic: ProviderRuntimeDiagnostic | None

    def __post_init__(self) -> None:
        retry_valid = self.retry_not_before is None or (
            type(self.retry_not_before) in {int, float}
            and math.isfinite(self.retry_not_before)
            and self.retry_not_before >= 0.0
        )
        if not (
            type(self.invocation) is ProviderInvocation
            and type(self.outcome) is ProviderOutcomeKind
            and (self.fact_batch is None or type(self.fact_batch) is ProviderFactBatch)
            and type(self.quota_windows) is tuple
            and len(self.quota_windows) <= MAX_QUOTA_WINDOWS_PER_RESULT
            and all(type(window) is ProviderQuotaWindow for window in self.quota_windows)
            and (self.cooldown_key is None or type(self.cooldown_key) is CooldownKey)
            and retry_valid
            and (self.diagnostic is None or type(self.diagnostic) is ProviderRuntimeDiagnostic)
        ):
            raise ValueError("invalid provider result")
        if self.fact_batch is not None and self.fact_batch.source_key != self.invocation.source_key:
            raise ValueError("cross-source provider result")
        if any(window.lane_key.source != self.invocation.source_key for window in self.quota_windows):
            raise ValueError("cross-source provider result")
        if self.cooldown_key is not None and self.cooldown_key.source_key != self.invocation.source_key:
            raise ValueError("cross-source cooldown")
        if self.outcome is ProviderOutcomeKind.COOLDOWN:
            if self.cooldown_key is None or self.retry_not_before is None:
                raise ValueError("invalid cooldown result")
            if self.invocation.cooldown_scope != self.cooldown_key.opaque_scope:
                raise ValueError("cooldown scope mismatch")
        elif self.cooldown_key is not None or self.retry_not_before is not None:
            raise ValueError("unexpected cooldown fields")
        if self.outcome is ProviderOutcomeKind.EMPTY and (self.fact_batch is not None or self.quota_windows):
            raise ValueError("invalid empty result")
        if self.outcome is ProviderOutcomeKind.SUCCESS and self.fact_batch is None and not self.quota_windows:
            raise ValueError("success result requires observations")
        if self.outcome in {
            ProviderOutcomeKind.FAILED,
            ProviderOutcomeKind.TIMED_OUT,
            ProviderOutcomeKind.COOLDOWN,
            ProviderOutcomeKind.AUTH_REQUIRED,
            ProviderOutcomeKind.ACCESS_DENIED,
            ProviderOutcomeKind.UNSUPPORTED,
        } and (self.fact_batch is not None or self.quota_windows):
            raise ValueError("failure result cannot contain observations")
        if self.retry_not_before is not None:
            object.__setattr__(self, "retry_not_before", float(self.retry_not_before))


@dataclass(frozen=True, slots=True)
class ProviderRuntimeState:
    source_key: SourceKey
    generation: int
    in_flight: bool
    timed_out_generation: int | None
    last_success_at: float | None
    consecutive_failures: int
    retry_not_before: float
    cooldown_until: float
    queued_after_cooldown: bool
    credential_generation: int
    blocked_credential_generation: int | None


ProviderAdapterCallable = Callable[
    [ProviderInvocation, threading.Event],
    ProviderResult,
]


@dataclass(slots=True)
class _SourceState:
    source_key: SourceKey
    generation: int = 0
    thread: threading.Thread | None = None
    invocation: ProviderInvocation | None = None
    cancel_event: threading.Event | None = None
    started_at: float = 0.0
    worker_result: ProviderResult | None = None
    timed_out_generation: int | None = None
    last_success_at: float | None = None
    consecutive_failures: int = 0
    retry_not_before: float = 0.0
    cooldowns: dict[CooldownKey, float] | None = None
    queued_invocation: ProviderInvocation | None = None
    credential_generation: int = 0
    blocked_credential_generation: int | None = None
    last_known_fact_batch: ProviderFactBatch | None = None
    last_known_quota_windows: tuple[ProviderQuotaWindow, ...] = ()

    def __post_init__(self) -> None:
        self.cooldowns = {}

    def public(self) -> ProviderRuntimeState:
        return ProviderRuntimeState(
            source_key=self.source_key,
            generation=self.generation,
            in_flight=self.thread is not None,
            timed_out_generation=self.timed_out_generation,
            last_success_at=self.last_success_at,
            consecutive_failures=self.consecutive_failures,
            retry_not_before=self.retry_not_before,
            cooldown_until=max((self.cooldowns or {}).values(), default=0.0),
            queued_after_cooldown=self.queued_invocation is not None,
            credential_generation=self.credential_generation,
            blocked_credential_generation=self.blocked_credential_generation,
        )


class ProviderRuntime:
    def __init__(
        self,
        adapters: Mapping[SourceKey, ProviderAdapterCallable],
        *,
        max_sources: int = MAX_PROVIDER_SOURCES,
    ) -> None:
        if type(max_sources) is not int or not 1 <= max_sources <= MAX_PROVIDER_SOURCES:
            raise ValueError("invalid provider source limit")
        if len(adapters) > max_sources:
            raise ValueError("too many provider sources")
        copied: dict[SourceKey, ProviderAdapterCallable] = {}
        for source_key, adapter in adapters.items():
            if type(source_key) is not SourceKey or not callable(adapter):
                raise ValueError("invalid provider adapter")
            copied[source_key] = adapter
        self._adapters = copied
        self._states = {source_key: _SourceState(source_key) for source_key in copied}
        self._lock = threading.Lock()
        self._immediate: list[ProviderResult] = []
        self._stopped = False

    def request(
        self,
        invocations: tuple[ProviderInvocation, ...],
    ) -> tuple[ProviderInvocation, ...]:
        if type(invocations) is not tuple or len(invocations) > MAX_INVOCATIONS_PER_REQUEST:
            raise ValueError("invalid provider invocation batch")
        if not all(type(item) is ProviderInvocation for item in invocations):
            raise ValueError("invalid provider invocation batch")
        launched: list[ProviderInvocation] = []
        with self._lock:
            if self._stopped:
                raise RuntimeError("provider runtime stopped")
            monotonic_now = time.monotonic()
            for item in invocations:
                state = self._states.get(item.source_key)
                if state is None:
                    self._immediate.append(self._unsupported(item))
                    continue
                if item.generation <= state.generation:
                    continue
                if state.blocked_credential_generation == state.credential_generation:
                    continue
                if state.thread is not None:
                    continue
                self._prune_cooldowns_locked(state, monotonic_now)
                cooldown_until = self._cooldown_until_locked(state, item)
                if monotonic_now < cooldown_until:
                    if item.trigger is RefreshTrigger.MANUAL and state.queued_invocation is None:
                        state.queued_invocation = item
                        state.generation = item.generation
                    continue
                state.retry_not_before = 0.0
                self._start_locked(state, item, monotonic_now)
                launched.append(item)
        return tuple(launched)

    def poll(self, *, monotonic_now: float) -> tuple[ProviderResult, ...]:
        if type(monotonic_now) not in {int, float} or not math.isfinite(monotonic_now):
            raise ValueError("invalid monotonic time")
        now = float(monotonic_now)
        published: list[ProviderResult] = []
        with self._lock:
            if self._immediate:
                published.extend(self._immediate)
                self._immediate.clear()
            for source_key in sorted(self._states):
                state = self._states[source_key]
                self._prune_cooldowns_locked(state, now)
                thread = state.thread
                active = state.invocation
                if thread is not None and active is not None and not thread.is_alive():
                    returned = state.worker_result
                    timed_out = state.timed_out_generation == active.generation
                    state.thread = None
                    state.invocation = None
                    state.cancel_event = None
                    state.worker_result = None
                    state.started_at = 0.0
                    if not timed_out and returned is not None and active.generation == state.generation:
                        self._apply_result_locked(state, returned, now)
                        published.append(returned)
                elif (
                    thread is not None
                    and active is not None
                    and state.timed_out_generation != active.generation
                    and now >= state.started_at + active.deadline_seconds
                ):
                    state.timed_out_generation = active.generation
                    state.consecutive_failures += 1
                    state.retry_not_before = now + retry_delay_seconds(state.consecutive_failures)
                    if state.cancel_event is not None:
                        state.cancel_event.set()
                    published.append(
                        ProviderResult(
                            invocation=active,
                            outcome=ProviderOutcomeKind.TIMED_OUT,
                            fact_batch=None,
                            quota_windows=(),
                            cooldown_key=None,
                            retry_not_before=None,
                            diagnostic=ProviderRuntimeDiagnostic("provider_timed_out"),
                        )
                    )
                if (
                    state.thread is None
                    and state.queued_invocation is not None
                    and now
                    >= self._cooldown_until_locked(
                        state,
                        state.queued_invocation,
                    )
                    and not self._stopped
                ):
                    queued = state.queued_invocation
                    state.queued_invocation = None
                    state.retry_not_before = 0.0
                    self._start_locked(state, queued, now)
        return tuple(published)

    def stop(self, *, deadline_seconds: float) -> None:
        if type(deadline_seconds) not in {int, float} or not math.isfinite(deadline_seconds):
            raise ValueError("invalid stop deadline")
        if deadline_seconds < 0.0:
            raise ValueError("invalid stop deadline")
        budget = min(float(deadline_seconds), MAX_SHUTDOWN_SECONDS)
        with self._lock:
            self._stopped = True
            workers = tuple(state.thread for state in self._states.values() if state.thread is not None)
            for state in self._states.values():
                state.queued_invocation = None
                if state.cancel_event is not None:
                    state.cancel_event.set()
        deadline = time.monotonic() + budget
        for worker in workers:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            worker.join(remaining)

    def state_for(self, source_key: SourceKey) -> ProviderRuntimeState:
        with self._lock:
            state = self._states.get(source_key)
            if state is None:
                raise KeyError(source_key)
            return state.public()

    def last_known_fact_batch(self, source_key: SourceKey) -> ProviderFactBatch | None:
        with self._lock:
            state = self._states.get(source_key)
            if state is None:
                raise KeyError(source_key)
            return state.last_known_fact_batch

    def set_credential_generation(self, source_key: SourceKey, generation: int) -> None:
        if type(generation) is not int or generation < 0:
            raise ValueError("invalid credential generation")
        with self._lock:
            if self._stopped:
                raise RuntimeError("provider runtime stopped")
            state = self._states.get(source_key)
            if state is None:
                raise KeyError(source_key)
            if generation < state.credential_generation:
                raise ValueError("credential generation cannot decrease")
            if generation != state.credential_generation:
                state.credential_generation = generation
                state.blocked_credential_generation = None

    def invalidate(self, source_key: SourceKey, *, generation: int) -> bool:
        """Fence off older work without replacing a still-owned worker."""
        if type(generation) is not int or generation <= 0:
            raise ValueError("invalid provider generation")
        with self._lock:
            if self._stopped:
                raise RuntimeError("provider runtime stopped")
            state = self._states.get(source_key)
            if state is None:
                raise KeyError(source_key)
            if generation <= state.generation:
                return False
            state.generation = generation
            state.queued_invocation = None
            if state.cancel_event is not None:
                state.cancel_event.set()
            return True

    def _start_locked(
        self,
        state: _SourceState,
        invocation: ProviderInvocation,
        started_at: float,
    ) -> None:
        cancel_event = threading.Event()
        state.generation = invocation.generation
        state.invocation = invocation
        state.cancel_event = cancel_event
        state.started_at = started_at
        state.worker_result = None
        worker = threading.Thread(
            target=self._invoke,
            args=(invocation, cancel_event),
            name=f"sidepulse-provider-{invocation.source_key.adapter_id}",
            daemon=True,
        )
        state.thread = worker
        worker.start()

    def _invoke(
        self,
        invocation: ProviderInvocation,
        cancel_event: threading.Event,
    ) -> None:
        try:
            returned = self._adapters[invocation.source_key](invocation, cancel_event)
            if type(returned) is not ProviderResult or returned.invocation != invocation:
                returned = self._adapter_failed(invocation)
        except Exception:
            returned = self._adapter_failed(invocation)
        with self._lock:
            state = self._states[invocation.source_key]
            if state.invocation == invocation:
                state.worker_result = returned

    @staticmethod
    def _adapter_failed(invocation: ProviderInvocation) -> ProviderResult:
        return ProviderResult(
            invocation=invocation,
            outcome=ProviderOutcomeKind.FAILED,
            fact_batch=None,
            quota_windows=(),
            cooldown_key=None,
            retry_not_before=None,
            diagnostic=ProviderRuntimeDiagnostic("adapter_failed"),
        )

    @staticmethod
    def _unsupported(invocation: ProviderInvocation) -> ProviderResult:
        return ProviderResult(
            invocation=invocation,
            outcome=ProviderOutcomeKind.UNSUPPORTED,
            fact_batch=None,
            quota_windows=(),
            cooldown_key=None,
            retry_not_before=None,
            diagnostic=ProviderRuntimeDiagnostic("unsupported_source"),
        )

    @staticmethod
    def _apply_result_locked(
        state: _SourceState,
        returned: ProviderResult,
        monotonic_now: float,
    ) -> None:
        outcome = returned.outcome
        if outcome in {
            ProviderOutcomeKind.SUCCESS,
            ProviderOutcomeKind.PARTIAL,
            ProviderOutcomeKind.EMPTY,
        }:
            state.last_success_at = monotonic_now
            state.consecutive_failures = 0
            state.retry_not_before = 0.0
            state.blocked_credential_generation = None
            if outcome is ProviderOutcomeKind.EMPTY:
                state.last_known_fact_batch = None
                state.last_known_quota_windows = ()
            else:
                state.last_known_fact_batch = returned.fact_batch
                state.last_known_quota_windows = returned.quota_windows
            return
        if outcome is ProviderOutcomeKind.COOLDOWN:
            state.consecutive_failures += 1
            cooldown_key = returned.cooldown_key
            assert cooldown_key is not None
            requested_until = returned.retry_not_before or monotonic_now
            bounded_until = min(
                max(monotonic_now, requested_until),
                monotonic_now + MAX_COOLDOWN_SECONDS,
            )
            cooldowns = state.cooldowns
            assert cooldowns is not None
            if cooldown_key not in cooldowns and len(cooldowns) >= MAX_COOLDOWNS_PER_SOURCE:
                evicted = min(cooldowns, key=lambda key: (cooldowns[key], key))
                del cooldowns[evicted]
            cooldowns[cooldown_key] = bounded_until
            state.retry_not_before = bounded_until
            return
        if outcome is ProviderOutcomeKind.AUTH_REQUIRED:
            state.blocked_credential_generation = state.credential_generation
        if outcome in {
            ProviderOutcomeKind.FAILED,
            ProviderOutcomeKind.AUTH_REQUIRED,
            ProviderOutcomeKind.ACCESS_DENIED,
        }:
            state.consecutive_failures += 1
            state.retry_not_before = monotonic_now + retry_delay_seconds(state.consecutive_failures)

    @staticmethod
    def _cooldown_until_locked(
        state: _SourceState,
        invocation: ProviderInvocation,
    ) -> float:
        if invocation.cooldown_scope is None:
            return 0.0
        cooldowns = state.cooldowns
        assert cooldowns is not None
        return cooldowns.get(
            CooldownKey(invocation.source_key, invocation.cooldown_scope),
            0.0,
        )

    @staticmethod
    def _prune_cooldowns_locked(state: _SourceState, monotonic_now: float) -> None:
        cooldowns = state.cooldowns
        assert cooldowns is not None
        expired = tuple(key for key, until in cooldowns.items() if until <= monotonic_now)
        for key in expired:
            del cooldowns[key]
