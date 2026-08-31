"""Latest-wins background service for cross-Mac provider synchronization."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from .provider_usage_runtime import ProviderUsageState
from .provider_usage_sync_runtime import ProviderSyncRefresh, ProviderSyncRuntime


@dataclass(frozen=True, slots=True)
class ProviderSyncServiceState:
    refresh: ProviderSyncRefresh | None
    refreshing: bool
    closed: bool
    reason: str | None


def _valid_timeout(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


class ProviderSyncService:
    def __init__(self, runtime: ProviderSyncRuntime) -> None:
        self._runtime = runtime
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._state = ProviderSyncServiceState(None, False, False, None)
        self._callbacks: list[Callable[[ProviderSyncServiceState], None]] = []
        self._pending_usage_state: ProviderUsageState | None = None
        self._inflight_usage_state: ProviderUsageState | None = None
        self._worker: threading.Thread | None = None

    def snapshot(self) -> ProviderSyncServiceState:
        with self._lock:
            return self._state

    def refresh_now(self, usage_state: ProviderUsageState) -> ProviderSyncServiceState:
        if type(usage_state) is not ProviderUsageState:
            raise ValueError("invalid provider usage state")
        with self._condition:
            if self._state.closed:
                return self._state
        try:
            refresh = self._runtime.refresh(usage_state)
            state = ProviderSyncServiceState(refresh, False, False, None)
        except Exception:
            state = ProviderSyncServiceState(
                self._state.refresh,
                False,
                False,
                "sync_refresh_failed",
            )
        with self._condition:
            if self._state.closed:
                return self._state
            self._state = state
            self._condition.notify_all()
        return state

    def request(
        self,
        usage_state: ProviderUsageState,
        *,
        callback: Callable[[ProviderSyncServiceState], None],
        force: bool = False,
    ) -> ProviderSyncServiceState:
        del force
        if type(usage_state) is not ProviderUsageState:
            raise ValueError("invalid provider usage state")
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._condition:
            if self._state.closed:
                return self._state
            self._callbacks.append(callback)
            if self._worker is not None and self._worker.is_alive():
                self._pending_usage_state = (
                    None
                    if usage_state == self._inflight_usage_state
                    else usage_state
                )
                self._state = replace(self._state, refreshing=True, reason=None)
                self._condition.notify_all()
                return self._state
            self._pending_usage_state = usage_state
            self._state = replace(self._state, refreshing=True, reason=None)
            self._worker = threading.Thread(
                target=self._worker_main,
                name="SidePulseProviderSync",
                daemon=True,
            )
            self._worker.start()
            self._condition.notify_all()
            return self._state

    def _worker_main(self) -> None:
        while True:
            with self._condition:
                usage_state = self._pending_usage_state
                self._pending_usage_state = None
                self._inflight_usage_state = usage_state
            if usage_state is None:
                result = ProviderSyncServiceState(
                    self._state.refresh,
                    False,
                    False,
                    "sync_refresh_failed",
                )
            else:
                result = self.refresh_now(usage_state)

            with self._condition:
                self._inflight_usage_state = None
                if self._state.closed:
                    self._pending_usage_state = None
                    self._callbacks.clear()
                    self._condition.notify_all()
                    return
                if self._pending_usage_state is not None:
                    # Keep all callbacks coalesced onto the newest refresh.
                    continue
                callbacks = tuple(self._callbacks)
                self._callbacks.clear()
            for callback in callbacks:
                try:
                    callback(result)
                except Exception:
                    continue
            with self._condition:
                if self._state.closed:
                    self._condition.notify_all()
                    return
                if self._pending_usage_state is None:
                    self._worker = None
                    self._state = replace(self._state, refreshing=False)
                    self._condition.notify_all()
                    return

    def wait_idle(self, *, timeout_seconds: float) -> bool:
        if not _valid_timeout(timeout_seconds):
            raise ValueError("invalid provider sync wait timeout")
        deadline = time.monotonic() + float(timeout_seconds)
        with self._condition:
            while (
                self._pending_usage_state is not None
                or self._state.refreshing
                or (self._worker is not None and self._worker.is_alive())
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self, *, timeout_seconds: float = 1.0) -> bool:
        if not _valid_timeout(timeout_seconds):
            raise ValueError("invalid provider sync close timeout")
        deadline = time.monotonic() + float(timeout_seconds)
        with self._condition:
            self._state = replace(self._state, refreshing=False, closed=True)
            self._pending_usage_state = None
            self._inflight_usage_state = None
            self._condition.notify_all()
            worker = self._worker
        if worker is None or not worker.is_alive():
            return True
        if worker.ident == threading.get_ident():
            return False
        worker.join(max(0.0, deadline - time.monotonic()))
        return not worker.is_alive()


__all__ = ["ProviderSyncService", "ProviderSyncServiceState"]
