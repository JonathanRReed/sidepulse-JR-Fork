"""Latest-wins background runtime for native provider accounting."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .provider_reconnect import (
    FailureGate,
    credential_fingerprint,
    note_failure,
    repair_grok_credential,
    should_collect,
)
from .provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    most_constrained_lane,
    provider_descriptor,
    select_authoritative_snapshot,
)
from .provider_usage_settings import ProviderUsageSettings

#: States that arm a failure gate. NEEDS_SIGN_IN is terminal (only a
#: fresh credential can fix it); the rest are transient and ride the
#: exponential ladder. RATE_LIMITED matters most in practice: the Claude
#: usage endpoint 429s, and before this gate existed the service kept
#: re-asking every 120 s, which is exactly how one STAYS rate limited.
_TERMINAL_FAILURE_STATES = frozenset({ProviderSourceState.NEEDS_SIGN_IN})
_TRANSIENT_FAILURE_STATES = frozenset(
    {
        ProviderSourceState.RATE_LIMITED,
        ProviderSourceState.UNAVAILABLE,
        ProviderSourceState.ERROR,
    }
)

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
        # Per-provider retry gates (see provider_reconnect): terminal
        # auth failures wait for the credential source to change,
        # transient failures ride an exponential ladder. In-memory only
        # -- a relaunch deliberately retries everything once.
        self._failure_gates: dict[str, FailureGate] = {}
        # Providers the NEXT worker run must collect even through a
        # gate, because a person just clicked something.
        self._forced_providers: set[str] = set()
        self._rerun_requested = False

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
        force: bool = False,
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
                # A disabled provider's old failure gate must not
                # outlive the disable: re-enabling should probe fresh,
                # not serve the pre-disable failure for up to an hour.
                self._failure_gates.pop(provider_id, None)
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
            gate = self._failure_gates.get(provider_id, FailureGate())
            fingerprint = credential_fingerprint(self._home, provider_id)
            previous = previous_by_provider.get(provider_id)
            if previous is not None and not should_collect(
                gate,
                now=observed_at,
                fingerprint=fingerprint,
                forced=force,
            ):
                # The gate is armed and nothing changed: serve the last
                # snapshot instead of re-asking a server that already
                # said no. This is what stops a 429 from becoming a
                # permanent 429.
                snapshots.append(previous)
                continue
            if (
                provider_id == "grok"
                and gate.terminal
                and fingerprint != gate.terminal_fingerprint
            ):
                # The user just ran `grok login`: clear any wedged
                # stored-token copy so the fresh file wins immediately.
                # Background-safe -- file reads only, never a prompt.
                try:
                    repair_grok_credential(
                        self._credentials,
                        home=self._home,
                        now=observed_at,
                    )
                except Exception:
                    pass
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
            if candidate.state in _TERMINAL_FAILURE_STATES:
                self._failure_gates[provider_id] = note_failure(
                    gate,
                    now=observed_at,
                    terminal=True,
                    fingerprint=fingerprint,
                )
            elif candidate.state in _TRANSIENT_FAILURE_STATES:
                self._failure_gates[provider_id] = note_failure(
                    gate,
                    now=observed_at,
                    terminal=False,
                    fingerprint=None,
                )
            else:
                self._failure_gates.pop(provider_id, None)
            previous_good = self._last_known_good.get(provider_id)
            if (
                candidate.state is ProviderSourceState.READY
                and not candidate.lanes
                and previous_good is not None
                and previous_good.lanes
            ):
                # A lane-less READY means "the scan worked and found no
                # quota evidence" -- e.g. Codex transcripts rotated away.
                # That is the ABSENCE of a reading, not a newer reading;
                # letting it overwrite last-known-good silently degraded
                # "48% left" to a bare "ready" card with no number.
                snapshots.append(
                    replace(
                        previous_good,
                        observed_at=candidate.observed_at,
                        state=ProviderSourceState.STALE,
                        reason_code="reading_evidence_missing",
                        action_label=previous_good.action_label or "Retry",
                    )
                )
            elif candidate.state is ProviderSourceState.READY:
                self._last_known_good[provider_id] = candidate
                snapshots.append(candidate)
            elif candidate.state is ProviderSourceState.STALE and candidate.lanes:
                # A stale-but-real reading is NEWER information than the
                # last known good one, and it is the same numbers wearing
                # an honest label. Substituting last_known_good here is
                # what let a Codex quota frozen three days ago keep
                # rendering as a live "ready" reading.
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
        # `force` used to be deleted here, which meant a user-initiated
        # reconnect could not push through a failure gate. Now it can.
        with self._lock:
            if self._closed:
                return self._state
        return self._run_refresh(providers=providers, force=force)

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
                if force:
                    # A person clicked while a background run was in
                    # flight. That run already read the OLD credential;
                    # piggybacking on it is how "Reconnect" used to
                    # report stale results as fresh ones. Run once more
                    # after it finishes.
                    self._rerun_requested = True
                    if providers is not None:
                        self._forced_providers.update(providers)
                return self._state
            self._state = replace(self._state, refreshing=True)
            self._worker = threading.Thread(
                target=self._worker_main,
                kwargs={"providers": providers, "force": force},
                name="SidePulseProviderUsage",
                daemon=True,
            )
            self._worker.start()
            return self._state

    def _worker_main(
        self,
        *,
        providers: tuple[str, ...] | None,
        force: bool = False,
    ) -> None:
        pending_providers = providers
        pending_force = force
        while True:
            state = self._run_refresh(
                providers=pending_providers, force=pending_force
            )
            with self._lock:
                if self._rerun_requested:
                    pending_providers = (
                        tuple(sorted(self._forced_providers)) or None
                    )
                    pending_force = True
                    self._rerun_requested = False
                    self._forced_providers.clear()
                    continue
                callbacks = tuple(self._callbacks)
                self._callbacks.clear()
                # Retire the worker UNDER THE LOCK, in the same critical
                # section as the final rerun check. The exit used to
                # happen while `is_alive()` was still true, so a forced
                # request landing during callback delivery piggybacked
                # on a thread that would never look at its flags again:
                # the click was swallowed and the leaked flags fired a
                # spurious forced run up to five minutes later.
                self._worker = None
                break
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
