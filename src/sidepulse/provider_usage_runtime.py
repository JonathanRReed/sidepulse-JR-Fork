"""Latest-wins background runtime for native provider accounting."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    most_constrained_lane,
    provider_descriptor,
    select_authoritative_snapshot,
)
from .provider_usage_settings import ProviderUsageSettings

Collector = Callable[[object, Path, float, object], ProviderUsageSnapshot]


@dataclass(frozen=True, slots=True)
class ProviderUsageState:
    snapshots: tuple[ProviderUsageSnapshot, ...]
    refreshed_at: float | None
    next_refresh_at: float | None
    refreshing: bool

    def by_provider(self, provider_id: str) -> ProviderUsageSnapshot:
        return next(
            snapshot for snapshot in self.snapshots if snapshot.provider_id == provider_id
        )


def _empty_snapshot(
    provider_id: str,
    *,
    observed_at: float,
    state: ProviderSourceState,
    reason: str | None = None,
    action: str | None = None,
) -> ProviderUsageSnapshot:
    return ProviderUsageSnapshot(
        provider_id=provider_id,
        account_label=None,
        observed_at=observed_at,
        state=state,
        reason_code=reason,
        action_label=action,
        lanes=(),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
    )


def _default_collectors() -> dict[str, Collector]:
    from .provider_usage_codex_claude import collect_claude, collect_codex
    from .provider_usage_collectors import (
        collect_antigravity,
        collect_cursor,
        collect_devin,
        collect_grok,
        collect_openai_api,
    )

    return {
        "codex": lambda preference, home, observed, credentials: collect_codex(
            preference,
            home=home,
            observed_at=observed,
        ),
        "claude": lambda preference, home, observed, credentials: collect_claude(
            preference,
            home=home,
            observed_at=observed,
            credentials=credentials,
        ),
        "cursor": lambda preference, home, observed, credentials: collect_cursor(
            preference,
            home=home,
            observed_at=observed,
            credentials=credentials,
        ),
        "devin": lambda preference, home, observed, credentials: collect_devin(
            preference,
            observed_at=observed,
            credentials=credentials,
        ),
        "grok": lambda preference, home, observed, credentials: collect_grok(
            preference,
            home=home,
            observed_at=observed,
            credentials=credentials,
        ),
        "antigravity": lambda preference, home, observed, credentials: collect_antigravity(
            preference,
            observed_at=observed,
        ),
        "openai-api": lambda preference, home, observed, credentials: collect_openai_api(
            preference,
            observed_at=observed,
            credentials=credentials,
        ),
    }


def _interval_for(snapshots: tuple[ProviderUsageSnapshot, ...], observed_at: float) -> float:
    interval = 300.0
    for snapshot in snapshots:
        if snapshot.state in {
            ProviderSourceState.NEEDS_CONSENT,
            ProviderSourceState.NEEDS_SIGN_IN,
            ProviderSourceState.SOURCE_NOT_FOUND,
            ProviderSourceState.ERROR,
            ProviderSourceState.UNAVAILABLE,
        }:
            interval = min(interval, 120.0)
        lane = most_constrained_lane(snapshot)
        if lane is None:
            continue
        if lane.remaining_percent is not None and lane.remaining_percent <= 10.0:
            interval = min(interval, 30.0)
        elif lane.remaining_percent is not None and lane.remaining_percent <= 25.0:
            interval = min(interval, 60.0)
        if lane.reset_at is not None:
            until_reset = lane.reset_at - observed_at
            if 0.0 <= until_reset <= 10 * 60:
                interval = min(interval, 30.0)
    return interval


class ProviderUsageService:
    def __init__(
        self,
        *,
        settings_loader: Callable[[], ProviderUsageSettings | object],
        credentials: object,
        home: Path,
        collectors: dict[str, Collector] | None = None,
        clock: Callable[[], float] = time.time,
        state_loader: Callable[[], ProviderUsageState] | None = None,
        state_saver: Callable[[ProviderUsageState], object] | None = None,
    ) -> None:
        self._settings_loader = settings_loader
        self._credentials = credentials
        self._home = Path(home)
        self._collectors = dict(_default_collectors() if collectors is None else collectors)
        self._clock = clock
        self._state_saver = state_saver
        self._lock = threading.RLock()
        self._closed = False
        loaded_state = (
            state_loader()
            if state_loader is not None
            else ProviderUsageState((), None, None, False)
        )
        if type(loaded_state) is not ProviderUsageState or loaded_state.refreshing:
            loaded_state = ProviderUsageState((), None, None, False)
        self._last_known_good: dict[str, ProviderUsageSnapshot] = {
            snapshot.provider_id: snapshot
            for snapshot in loaded_state.snapshots
            if snapshot.state in {ProviderSourceState.READY, ProviderSourceState.STALE}
        }
        self._state = loaded_state
        self._callbacks: list[Callable[[ProviderUsageState], None]] = []
        self._worker: threading.Thread | None = None

    def snapshot(self) -> ProviderUsageState:
        with self._lock:
            return self._state

    def _settings(self) -> ProviderUsageSettings:
        loaded = self._settings_loader()
        settings = getattr(loaded, "settings", loaded)
        if type(settings) is not ProviderUsageSettings:
            raise ValueError("invalid provider usage settings")
        return settings

    def _run_refresh(
        self,
        *,
        providers: tuple[str, ...] | None,
    ) -> ProviderUsageState:
        observed_at = float(self._clock())
        settings = self._settings()
        selected = None if providers is None else frozenset(providers)
        previous_by_provider = {
            snapshot.provider_id: snapshot for snapshot in self.snapshot().snapshots
        }
        snapshots: list[ProviderUsageSnapshot] = []
        for preference in settings.providers:
            provider_id = preference.provider_id
            if selected is not None and provider_id not in selected:
                previous = previous_by_provider.get(provider_id)
                if previous is not None:
                    snapshots.append(previous)
                continue
            if not preference.enabled:
                snapshots.append(
                    _empty_snapshot(
                        provider_id,
                        observed_at=observed_at,
                        state=ProviderSourceState.DISABLED,
                    )
                )
                continue
            collector = self._collectors.get(provider_id)
            if collector is None:
                snapshots.append(
                    _empty_snapshot(
                        provider_id,
                        observed_at=observed_at,
                        state=ProviderSourceState.SOURCE_NOT_FOUND,
                        reason="collector_not_configured",
                        action=f"Configure {provider_descriptor(provider_id).label}",
                    )
                )
                continue
            try:
                candidate = collector(
                    preference,
                    self._home,
                    observed_at,
                    self._credentials,
                )
                if type(candidate) is not ProviderUsageSnapshot:
                    raise ValueError("collector returned invalid snapshot")
            except Exception:
                candidate = _empty_snapshot(
                    provider_id,
                    observed_at=observed_at,
                    state=ProviderSourceState.ERROR,
                    reason="collector_failed",
                    action="Retry",
                )
            previous_good = self._last_known_good.get(provider_id)
            if candidate.state is ProviderSourceState.READY:
                self._last_known_good[provider_id] = candidate
                snapshots.append(candidate)
            elif previous_good is not None:
                snapshots.append(
                    select_authoritative_snapshot(
                        (candidate,),
                        last_known_good=previous_good,
                    )
                )
            else:
                snapshots.append(candidate)
        ordered = tuple(snapshots)
        state = ProviderUsageState(
            snapshots=ordered,
            refreshed_at=observed_at,
            next_refresh_at=observed_at + _interval_for(ordered, observed_at),
            refreshing=False,
        )
        with self._lock:
            self._state = state
        if self._state_saver is not None:
            try:
                self._state_saver(state)
            except Exception:
                pass
        return state

    def refresh_now(
        self,
        *,
        providers: tuple[str, ...] | None = None,
        force: bool = False,
    ) -> ProviderUsageState:
        del force
        with self._lock:
            if self._closed:
                return self._state
        return self._run_refresh(providers=providers)

    def request(
        self,
        *,
        callback: Callable[[ProviderUsageState], None],
        providers: tuple[str, ...] | None = None,
        force: bool = False,
    ) -> ProviderUsageState:
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            if self._closed:
                return self._state
            now = time.time()
            if (
                not force
                and self._state.next_refresh_at is not None
                and now < self._state.next_refresh_at
            ):
                return self._state
            self._callbacks.append(callback)
            if self._worker is not None and self._worker.is_alive():
                return self._state
            self._state = replace(self._state, refreshing=True)
            self._worker = threading.Thread(
                target=self._worker_main,
                kwargs={"providers": providers},
                name="SidePulseProviderUsage",
                daemon=True,
            )
            self._worker.start()
            return self._state

    def _worker_main(self, *, providers: tuple[str, ...] | None) -> None:
        state = self._run_refresh(providers=providers)
        with self._lock:
            callbacks = tuple(self._callbacks)
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback(state)
            except Exception:
                continue

    def close(self) -> None:
        with self._lock:
            self._closed = True
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=1.0)


__all__ = ["ProviderUsageService", "ProviderUsageState"]
