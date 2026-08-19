"""Latest-wins background service for cross-Mac provider synchronization."""

from __future__ import annotations

import threading
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


class ProviderSyncService:
    def __init__(self, runtime: ProviderSyncRuntime) -> None:
        self._runtime = runtime
        self._lock = threading.RLock()
        self._state = ProviderSyncServiceState(None, False, False, None)
        self._callbacks: list[Callable[[ProviderSyncServiceState], None]] = []
        self._pending_usage_state: ProviderUsageState | None = None
        self._worker: threading.Thread | None = None

    def snapshot(self) -> ProviderSyncServiceState:
        with self._lock:
            return self._state

    def refresh_now(self, usage_state: ProviderUsageState) -> ProviderSyncServiceState:
        if type(usage_state) is not ProviderUsageState:
            raise ValueError("invalid provider usage state")
        with self._lock:
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
        with self._lock:
            self._state = state
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
        with self._lock:
            if self._state.closed:
                return self._state
            self._pending_usage_state = usage_state
            self._callbacks.append(callback)
            if self._worker is not None and self._worker.is_alive():
                return self._state
            self._state = replace(self._state, refreshing=True, reason=None)
            self._worker = threading.Thread(
                target=self._worker_main,
                name="SidePulseProviderSync",
                daemon=True,
            )
            self._worker.start()
            return self._state

    def _worker_main(self) -> None:
        with self._lock:
            usage_state = self._pending_usage_state
            self._pending_usage_state = None
        if usage_state is None:
            result = ProviderSyncServiceState(
                self._state.refresh,
                False,
                False,
                "sync_refresh_failed",
            )
        else:
            result = self.refresh_now(usage_state)
        with self._lock:
            callbacks = tuple(self._callbacks)
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback(result)
            except Exception:
                continue

    def close(self) -> None:
        with self._lock:
            self._state = replace(self._state, refreshing=False, closed=True)
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=1.0)


__all__ = ["ProviderSyncService", "ProviderSyncServiceState"]
