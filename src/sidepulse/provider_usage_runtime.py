"""Latest-wins background runtime for native provider accounting."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from .adaptive_refresh import (
    AdaptiveRefreshPlan,
    plan_adaptive_refresh_cadence,
)
from .provider_feature_settings import (
    ProviderPresentationSettings,
    project_collection_settings,
)
from .provider_instances import ProviderInstanceKey
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
    provider_descriptor,
    select_authoritative_snapshot,
)
from .provider_usage_settings import ProviderUsageSettings

ProviderRefreshScope = str | tuple[str, str]

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
class _UnavailableCredentialRead:
    available: bool = False
    secret: None = None
    reason: str = "instance_credential_unavailable"


class _InstanceCredentialView:
    """Collector-facing credential lookup fixed to one provider instance."""

    __slots__ = ("_key", "_store")

    def __init__(self, store: object, key: ProviderInstanceKey) -> None:
        self._store = store
        self._key = key

    def get(self, provider_id: str, account: str):
        expected_provider, source_instance_id = self._key.value
        if provider_id != expected_provider:
            return _UnavailableCredentialRead(reason="provider_identity_mismatch")
        exact = getattr(self._store, "get_for_instance", None)
        if callable(exact):
            return exact(self._key, account)
        if source_instance_id == "default":
            legacy = getattr(self._store, "get", None)
            if callable(legacy):
                return legacy(provider_id, account)
        return _UnavailableCredentialRead()

    def set(self, provider_id: str, account: str, secret: str) -> None:
        """Keep repair helpers instance-scoped when they receive this view."""
        expected_provider, source_instance_id = self._key.value
        if provider_id != expected_provider:
            raise ValueError("provider identity mismatch")
        if source_instance_id == "default":
            setter = getattr(self._store, "set", None)
            if callable(setter):
                setter(provider_id, account, secret)
                return
        setter = getattr(self._store, "set_for_instance", None)
        if not callable(setter):
            raise ValueError("instance credential storage unavailable")
        setter(self._key, account, secret)


class RefreshPublicationOutcome(str, Enum):
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    REFUSED = "refused"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RefreshPublicationReceipt:
    sequence: int
    settings_revision: int
    outcome: RefreshPublicationOutcome
    error_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("invalid refresh receipt sequence")
        if type(self.settings_revision) is not int or self.settings_revision < 0:
            raise ValueError("invalid refresh receipt revision")
        if type(self.outcome) is not RefreshPublicationOutcome:
            raise ValueError("invalid refresh receipt outcome")
        if self.outcome is RefreshPublicationOutcome.FAILED:
            if self.error_code != "state_persistence_failed":
                raise ValueError("invalid refresh failure code")
        elif self.error_code is not None:
            raise ValueError("unexpected refresh receipt error code")


@dataclass(frozen=True, slots=True)
class ProviderUsageState:
    snapshots: tuple[ProviderUsageSnapshot, ...]
    refreshed_at: float | None
    next_refresh_at: float | None
    refreshing: bool

    def by_provider(self, provider_id: str) -> ProviderUsageSnapshot:
        matches = tuple(
            snapshot for snapshot in self.snapshots if snapshot.provider_id == provider_id
        )
        if len(matches) > 1:
            raise ValueError("provider usage lookup is ambiguous; choose a source instance")
        if not matches:
            raise KeyError(provider_id)
        return matches[0]

    def by_instance(
        self,
        provider_id: str,
        source_instance_id: str = "default",
    ) -> ProviderUsageSnapshot:
        try:
            return next(
                snapshot
                for snapshot in self.snapshots
                if snapshot.identity == (provider_id, source_instance_id)
            )
        except StopIteration as exc:
            raise KeyError((provider_id, source_instance_id)) from exc


@dataclass(frozen=True, slots=True)
class ProviderUsageApply:
    state: ProviderUsageState
    settings: ProviderPresentationSettings
    #: The durable document used by collection and Settings checkboxes. Keep
    #: this beside, rather than inside, the presentation projection so a
    #: worker apply cannot erase the identity-bearing source of truth.
    usage_settings: ProviderUsageSettings | None = None

    def __post_init__(self) -> None:
        if type(self.state) is not ProviderUsageState:
            raise ValueError("invalid provider usage state")
        if type(self.settings) is not ProviderPresentationSettings:
            raise ValueError("invalid provider usage settings")
        if self.usage_settings is not None and type(self.usage_settings) is not ProviderUsageSettings:
            raise ValueError("invalid durable provider usage settings")


def _empty_snapshot(
    provider_id: str,
    *,
    observed_at: float,
    state: ProviderSourceState,
    reason: str | None = None,
    action: str | None = None,
    source_instance_id: str = "default",
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
        source_instance_id=source_instance_id,
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


def _interval_for(
    snapshots: tuple[ProviderUsageSnapshot, ...],
    observed_at: float,
    *,
    menu_last_opened_at: float | None = None,
    constrained: bool = False,
    ambient_usage_visible: bool = False,
) -> float:
    """Compatibility projection of the observable adaptive cadence plan."""
    return plan_adaptive_refresh_cadence(
        snapshots,
        observed_at=observed_at,
        menu_last_opened_at=menu_last_opened_at,
        constrained=constrained,
        ambient_usage_visible=ambient_usage_visible,
    ).interval_seconds


def _machine_is_constrained() -> bool:
    """Low Power Mode or serious thermal pressure, best effort."""
    try:
        from Foundation import NSProcessInfo

        info = NSProcessInfo.processInfo()
        if bool(info.isLowPowerModeEnabled()):
            return True
        # NSProcessInfoThermalStateSerious == 2
        return int(info.thermalState()) >= 2
    except Exception:
        return False


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
        receipt_handler: Callable[[RefreshPublicationReceipt], object] | None = None,
    ) -> None:
        self._settings_loader = settings_loader
        self._credentials = credentials
        self._home = Path(home)
        self._collectors = dict(_default_collectors() if collectors is None else collectors)
        self._clock = clock
        self._state_saver = state_saver
        if receipt_handler is not None and not callable(receipt_handler):
            raise ValueError("invalid refresh receipt handler")
        self._receipt_handler = receipt_handler
        self._lock = threading.RLock()
        self._closed = False
        self._settings_snapshot: ProviderUsageSettings | None = None
        self._settings_revision = 0
        self._explicit_settings_revision: int | None = None
        loaded_state = (
            state_loader()
            if state_loader is not None
            else ProviderUsageState((), None, None, False)
        )
        if type(loaded_state) is not ProviderUsageState or loaded_state.refreshing:
            loaded_state = ProviderUsageState((), None, None, False)
        self._last_known_good: dict[tuple[str, str], ProviderUsageSnapshot] = {
            snapshot.identity: snapshot
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
        self._failure_gates: dict[tuple[str, str], FailureGate] = {}
        # Providers the NEXT worker run must collect even through a
        # gate, because a person just clicked something.
        self._forced_providers: set[str] = set()
        self._rerun_requested = False
        self._refresh_receipts: deque[RefreshPublicationReceipt] = deque(maxlen=32)
        self._refresh_sequence = 0
        self._last_publication_outcome: RefreshPublicationOutcome | None = None
        self._last_publication_revision: int | None = None
        #: When the owner last opened the menu -- the cadence ladder's
        #: only attention signal. None means 'not since launch'.
        self._menu_last_opened_at: float | None = None
        #: True while the LED bar renders Quota Runway.
        self._ambient_usage_visible = False
        self._last_cadence_plan = plan_adaptive_refresh_cadence(
            (),
            observed_at=0.0,
        )

    def note_ambient_usage_visible(self, visible: bool) -> None:
        """Tell the cadence whether a usage number is on screen already."""
        with self._lock:
            self._ambient_usage_visible = bool(visible)
            self._replan_cached_cadence_locked(float(self._clock()))

    def note_menu_opened(self, *, now: float | None = None) -> None:
        """Record a visit; the cadence ladder keys off how long ago."""
        with self._lock:
            observed_at = float(self._clock()) if now is None else float(now)
            self._menu_last_opened_at = observed_at
            self._replan_cached_cadence_locked(observed_at)

    def _replan_cached_cadence_locked(self, observed_at: float) -> None:
        """Shorten an accepted schedule when a local attention signal changes."""
        plan = plan_adaptive_refresh_cadence(
            self._state.snapshots,
            observed_at=observed_at,
            menu_last_opened_at=self._menu_last_opened_at,
            constrained=self._last_cadence_plan.constrained,
            ambient_usage_visible=self._ambient_usage_visible,
        )
        next_refresh_at = self._state.next_refresh_at
        if next_refresh_at is not None:
            next_refresh_at = min(
                next_refresh_at,
                observed_at + plan.interval_seconds,
            )
            self._state = replace(self._state, next_refresh_at=next_refresh_at)
        self._last_cadence_plan = plan

    def snapshot(self) -> ProviderUsageState:
        with self._lock:
            return self._state

    def settings_snapshot(self) -> ProviderUsageSettings | None:
        with self._lock:
            return self._settings_snapshot

    def cadence_plan(self) -> AdaptiveRefreshPlan:
        """Return the current scheduled cadence without reading system state."""
        with self._lock:
            return self._last_cadence_plan

    def refresh_receipts(self) -> tuple[RefreshPublicationReceipt, ...]:
        with self._lock:
            return tuple(self._refresh_receipts)

    def _record_receipt(
        self,
        outcome: RefreshPublicationOutcome,
        settings_revision: int,
        *,
        error_code: str | None = None,
    ) -> RefreshPublicationReceipt:
        with self._lock:
            self._refresh_sequence += 1
            receipt = RefreshPublicationReceipt(
                self._refresh_sequence,
                settings_revision,
                outcome,
                error_code,
            )
            self._refresh_receipts.append(receipt)
            self._last_publication_outcome = outcome
            handler = self._receipt_handler
        if handler is not None:
            try:
                handler(receipt)
            except Exception:
                pass
        return receipt

    def note_settings_updated(self, settings: ProviderUsageSettings) -> None:
        if type(settings) is not ProviderUsageSettings:
            raise ValueError("invalid provider usage settings")
        with self._lock:
            self._settings_revision += 1
            self._settings_snapshot = settings
            self._explicit_settings_revision = self._settings_revision

    def _settings(self) -> ProviderUsageSettings:
        settings, _revision = self._settings_with_revision()
        return settings

    def _settings_with_revision(self) -> tuple[ProviderUsageSettings, int]:
        with self._lock:
            starting_revision = self._settings_revision
        loaded = self._settings_loader()
        settings = getattr(loaded, "settings", loaded)
        if type(settings) is not ProviderUsageSettings:
            raise ValueError("invalid provider usage settings")
        with self._lock:
            # A user may save a menu preference while this load is in
            # flight. The worker can finish its already-started collection
            # with the version it read, but it must not overwrite the newer
            # explicit-action snapshot that AppKit should project.
            if self._settings_revision == starting_revision:
                if self._explicit_settings_revision == starting_revision:
                    # A bounded rerun can read a lagging source again. Keep
                    # the explicit edit as the projected snapshot for that
                    # rerun, then allow later ordinary loads to refresh it.
                    self._explicit_settings_revision = None
                else:
                    self._settings_snapshot = settings
        return settings, starting_revision

    def _run_refresh(
        self,
        *,
        providers: tuple[ProviderRefreshScope, ...] | None,
        force: bool = False,
    ) -> ProviderUsageState:
        observed_at = float(self._clock())
        settings, settings_revision = self._settings_with_revision()
        collection_settings = project_collection_settings(settings)
        selected = None if providers is None else frozenset(providers)
        selected_provider_ids = frozenset(
            item for item in selected or () if isinstance(item, str)
        )
        selected_instances = frozenset(
            item for item in selected or ()
            if isinstance(item, tuple) and len(item) == 2
        )
        previous_by_provider = {
            snapshot.identity: snapshot for snapshot in self.snapshot().snapshots
        }
        snapshots: list[ProviderUsageSnapshot] = []
        for preference in collection_settings.providers:
            provider_id = preference.provider_id
            identity = preference.identity
            if selected is not None and (
                provider_id not in selected_provider_ids
                and identity not in selected_instances
            ):
                previous = previous_by_provider.get(identity)
                if previous is not None:
                    snapshots.append(previous)
                continue
            if not preference.enabled:
                # A disabled provider's old failure gate must not
                # outlive the disable: re-enabling should probe fresh,
                # not serve the pre-disable failure for up to an hour.
                self._failure_gates.pop(identity, None)
                snapshots.append(
                    _empty_snapshot(
                        provider_id,
                        observed_at=observed_at,
                        state=ProviderSourceState.DISABLED,
                        source_instance_id=preference.source_instance_id,
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
                        source_instance_id=preference.source_instance_id,
                    )
                )
                continue
            gate = self._failure_gates.get(identity, FailureGate())
            fingerprint = credential_fingerprint(self._home, provider_id)
            previous = previous_by_provider.get(identity)
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
                and preference.source_instance_id == "default"
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
                    _InstanceCredentialView(
                        self._credentials,
                        ProviderInstanceKey(
                            provider_id,
                            preference.source_instance_id,
                        ),
                    ),
                )
                if type(candidate) is not ProviderUsageSnapshot:
                    raise ValueError("collector returned invalid snapshot")
                if candidate.identity != identity:
                    candidate = replace(
                        candidate,
                        source_instance_id=preference.source_instance_id,
                    )
            except Exception:
                candidate = _empty_snapshot(
                    provider_id,
                    observed_at=observed_at,
                    state=ProviderSourceState.ERROR,
                    reason="collector_failed",
                    action="Retry",
                    source_instance_id=preference.source_instance_id,
                )
            if candidate.state in _TERMINAL_FAILURE_STATES:
                self._failure_gates[identity] = note_failure(
                    gate,
                    now=observed_at,
                    terminal=True,
                    fingerprint=fingerprint,
                )
            elif candidate.state in _TRANSIENT_FAILURE_STATES:
                self._failure_gates[identity] = note_failure(
                    gate,
                    now=observed_at,
                    terminal=False,
                    fingerprint=None,
                )
            else:
                self._failure_gates.pop(identity, None)
            previous_good = self._last_known_good.get(identity)
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
                self._last_known_good[identity] = candidate
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
        cadence_plan = plan_adaptive_refresh_cadence(
            ordered,
            observed_at=observed_at,
            menu_last_opened_at=getattr(self, "_menu_last_opened_at", None),
            constrained=_machine_is_constrained(),
            ambient_usage_visible=bool(
                getattr(self, "_ambient_usage_visible", False)
            ),
        )
        state = ProviderUsageState(
            snapshots=ordered,
            refreshed_at=observed_at,
            next_refresh_at=observed_at + cadence_plan.interval_seconds,
            refreshing=False,
        )
        # State publication and its durable save are one revision-fenced
        # critical section. An explicit settings edit cannot land between
        # the check and the save, and an older worker therefore cannot leak
        # either state or persistence past the edit.
        publication_outcome: RefreshPublicationOutcome
        publication_error: str | None = None
        with self._lock:
            if self._closed:
                publication_outcome = RefreshPublicationOutcome.REFUSED
                result = self._state
            elif settings_revision != self._settings_revision:
                publication_outcome = RefreshPublicationOutcome.SUPERSEDED
                result = self._state
            else:
                self._state = state
                self._last_cadence_plan = cadence_plan
                self._last_publication_revision = settings_revision
                result = state
                publication_outcome = RefreshPublicationOutcome.ACCEPTED
                if self._state_saver is not None:
                    try:
                        self._state_saver(state)
                    except Exception:
                        publication_outcome = RefreshPublicationOutcome.FAILED
                        publication_error = "state_persistence_failed"
        self._record_receipt(
            publication_outcome,
            settings_revision,
            error_code=publication_error,
        )
        return result

    def refresh_now(
        self,
        *,
        providers: tuple[ProviderRefreshScope, ...] | None = None,
        force: bool = False,
    ) -> ProviderUsageState:
        # `force` used to be deleted here, which meant a user-initiated
        # reconnect could not push through a failure gate. Now it can.
        with self._lock:
            if self._closed:
                self._record_receipt(
                    RefreshPublicationOutcome.REFUSED,
                    self._settings_revision,
                )
                return self._state
        return self._run_refresh(providers=providers, force=force)

    def request(
        self,
        *,
        callback: Callable[[ProviderUsageState], None],
        providers: tuple[ProviderRefreshScope, ...] | None = None,
        force: bool = False,
    ) -> ProviderUsageState:
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            if self._closed:
                self._record_receipt(
                    RefreshPublicationOutcome.REFUSED,
                    self._settings_revision,
                )
                return self._state
            now = float(self._clock())
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
        providers: tuple[ProviderRefreshScope, ...] | None,
        force: bool = False,
    ) -> None:
        pending_providers = providers
        pending_force = force
        while True:
            state = self._run_refresh(
                providers=pending_providers, force=pending_force
            )
            with self._lock:
                if (
                    self._rerun_requested
                    or self._last_publication_outcome
                    is RefreshPublicationOutcome.SUPERSEDED
                    or (
                        self._last_publication_outcome
                        is RefreshPublicationOutcome.ACCEPTED
                        and self._last_publication_revision
                        != self._settings_revision
                    )
                ):
                    pending_providers = (
                        tuple(sorted(self._forced_providers, key=repr)) or None
                    )
                    if pending_providers is None:
                        pending_providers = providers
                    pending_force = True
                    self._rerun_requested = False
                    self._forced_providers.clear()
                    continue
                callbacks = tuple(self._callbacks)
                self._callbacks.clear()
                publication_revision = self._last_publication_revision
                # Retire the worker UNDER THE LOCK, in the same critical
                # section as the final rerun check. The exit used to
                # happen while `is_alive()` was still true, so a forced
                # request landing during callback delivery piggybacked
                # on a thread that would never look at its flags again:
                # the click was swallowed and the leaked flags fired a
                # spurious forced run up to five minutes later.
                self._worker = None
            superseded_callbacks = False
            for index, callback in enumerate(callbacks):
                # Keep the revision check and callback invocation in one
                # critical section. A settings update from another thread
                # therefore either precedes this callback and suppresses it,
                # or waits until the callback has begun and is ordered after
                # the publication it observes.
                with self._lock:
                    if publication_revision != self._settings_revision:
                        self._callbacks = list(callbacks[index:]) + self._callbacks
                        self._rerun_requested = True
                        self._worker = threading.current_thread()
                        superseded_callbacks = True
                        break
                    try:
                        callback(state)
                    except Exception:
                        continue
            if superseded_callbacks:
                pending_providers = providers
                pending_force = True
                continue
            break

    def close(self) -> None:
        with self._lock:
            self._closed = True
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=1.0)


__all__ = [
    "ProviderRefreshScope",
    "ProviderUsageApply",
    "ProviderUsageService",
    "ProviderUsageState",
    "RefreshPublicationOutcome",
    "RefreshPublicationReceipt",
]
