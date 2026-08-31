from __future__ import annotations

import threading
import time
from dataclasses import replace as dataclass_replace

import pytest

from sidepulse.provider_feature_settings import (
    ProviderCollectionFeature,
    project_presentation_settings,
)
from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from sidepulse.provider_usage_runtime import (
    ProviderUsageApply,
    ProviderUsageService,
    ProviderUsageState,
    RefreshPublicationOutcome,
)
from sidepulse.provider_usage_settings import default_provider_usage_settings


def snapshot(provider, *, state=ProviderSourceState.READY, remaining=50, observed=1000):
    lanes = ()
    reason = None
    action = None
    if state in {ProviderSourceState.READY, ProviderSourceState.STALE}:
        lanes = (
            UsageLane(
                provider_id=provider,
                lane_id="weekly",
                label="Weekly",
                remaining_percent=remaining,
                reset_at=3000,
                scope="all",
                model=None,
                feature=None,
                bindable=True,
                source_id="fixture",
            ),
        )
        if state is ProviderSourceState.STALE:
            reason = "network_unavailable"
            action = "Retry"
    elif state is not ProviderSourceState.DISABLED:
        reason = "network_unavailable"
        action = "Retry"
    return ProviderUsageSnapshot(
        provider_id=provider,
        account_label=None,
        observed_at=observed,
        state=state,
        reason_code=reason,
        action_label=action,
        lanes=lanes,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
    )


def test_refresh_preserves_registry_order_and_disabled_state(tmp_path):
    settings = default_provider_usage_settings().with_enabled("grok", False)
    collectors = {
        preference.provider_id: (
            lambda provider: lambda _pref, _home, observed, _credentials: snapshot(
                provider, observed=observed
            )
        )(preference.provider_id)
        for preference in settings.providers
    }
    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors=collectors,
        credentials=object(),
        home=tmp_path,
        clock=lambda: 1000,
    )

    state = service.refresh_now()

    assert tuple(item.provider_id for item in state.snapshots) == tuple(
        preference.provider_id for preference in settings.providers
    )
    assert state.by_provider("grok").state is ProviderSourceState.DISABLED
    assert state.refreshing is False


def test_last_known_good_is_retained_when_refresh_fails(tmp_path):
    settings = default_provider_usage_settings()
    calls = {"codex": 0}

    def codex(_pref, _home, observed, _credentials):
        calls["codex"] += 1
        if calls["codex"] == 1:
            return snapshot("codex", remaining=20, observed=observed)
        return snapshot(
            "codex",
            state=ProviderSourceState.UNAVAILABLE,
            observed=observed,
        )

    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={"codex": codex},
        credentials=object(),
        home=tmp_path,
        clock=iter((1000, 1100)).__next__,
    )
    first = service.refresh_now(providers=("codex",))
    second = service.refresh_now(providers=("codex",), force=True)

    assert first.by_provider("codex").state is ProviderSourceState.READY
    stale = second.by_provider("codex")
    assert stale.state is ProviderSourceState.STALE
    assert stale.lanes[0].remaining_percent == 20


def test_collector_exception_becomes_actionable_error(tmp_path):
    settings = default_provider_usage_settings()

    def broken(*_args):
        raise RuntimeError("private body must not surface")

    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={"claude": broken},
        credentials=object(),
        home=tmp_path,
        clock=lambda: 1000,
    )
    result = service.refresh_now(providers=("claude",)).by_provider("claude")
    assert result.state is ProviderSourceState.ERROR
    assert result.reason_code == "collector_failed"
    assert result.action_label == "Retry"


def test_a_low_meter_is_not_a_reason_to_poll_harder(tmp_path):
    """Quota level must not drive cadence. A nearly-empty meter is not a
    reason to poll every 30s -- it is a reason the number matters. The
    ladder keys off ATTENTION instead (2026-08-27, mined from CodexBar,
    whose cadence deliberately excludes quota)."""
    settings = default_provider_usage_settings()
    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={
            "codex": lambda _pref, _home, observed, _credentials: snapshot(
                "codex", remaining=8, observed=observed
            )
        },
        credentials=object(),
        home=tmp_path,
        clock=lambda: 1000,
    )
    state = service.refresh_now(providers=("codex",))
    assert state.next_refresh_at == 1000 + 1800.0, "unattended: the idle rung"

    service.note_menu_opened(now=1000)
    state = service.refresh_now(providers=("codex",))
    assert state.next_refresh_at == 1000 + 120.0, "just looked: the fast rung"


def test_the_cadence_ladder_is_pure_and_ordered():
    from sidepulse.provider_usage_runtime import _interval_for

    assert _interval_for((), 10_000.0, menu_last_opened_at=9_900.0) == 120.0
    assert _interval_for((), 10_000.0, menu_last_opened_at=9_000.0) == 300.0
    assert _interval_for((), 10_000.0, menu_last_opened_at=6_000.0) == 900.0
    assert _interval_for((), 10_000.0, menu_last_opened_at=None) == 1800.0
    assert (
        _interval_for((), 10_000.0, menu_last_opened_at=9_990.0, constrained=True)
        == 1800.0
    ), "Low Power Mode outranks a fresh visit"


def test_an_imminent_reset_is_still_watched_closely(tmp_path):
    """Our one deliberate divergence: we celebrate resets, so we have to
    see the boundary cross -- 120s, not the old 30s hammer."""
    from sidepulse.provider_usage_runtime import _interval_for

    soon = snapshot("codex", remaining=50, observed=1000)
    lane = soon.lanes[0]
    from dataclasses import replace as dataclass_replace

    soon = dataclass_replace(
        soon, lanes=(dataclass_replace(lane, reset_at=1000 + 120.0),)
    )
    assert _interval_for((soon,), 1000.0, menu_last_opened_at=None) == 120.0


def test_request_runs_off_caller_thread_and_coalesces(tmp_path):
    settings = default_provider_usage_settings()
    gate = threading.Event()
    collector_threads = []
    callback_threads = []

    def collect(_pref, _home, observed, _credentials):
        collector_threads.append(threading.current_thread().name)
        gate.wait(2)
        return snapshot("codex", observed=observed)

    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={"codex": collect},
        credentials=object(),
        home=tmp_path,
        clock=time.time,
    )
    callbacks = []
    callbacks_ready = threading.Event()

    def callback(state):
        callback_threads.append(threading.current_thread().name)
        callbacks.append(state)
        callbacks_ready.set()

    first = service.request(callback=callback, providers=("codex",), force=True)
    # A FORCED request during an in-flight run may not piggyback on it:
    # that run already read the old credential, which is how "Reconnect"
    # used to report pre-click results as fresh ones. It runs once more.
    second = service.request(callback=callback, providers=("codex",), force=True)
    # An UNFORCED request still coalesces onto whatever is in flight.
    third = service.request(callback=callback, providers=("codex",))
    assert first.refreshing is True
    assert second.refreshing is True
    assert third.refreshing is True
    gate.set()
    assert callbacks_ready.wait(3)
    assert len(collector_threads) == 2
    assert all(
        name != threading.current_thread().name for name in collector_threads
    )
    assert callback_threads
    assert all(name != threading.current_thread().name for name in callback_threads)
    assert callbacks and callbacks[0].refreshing is False
    service.close()


def test_request_uses_the_service_clock_for_refresh_gating(tmp_path):
    settings = default_provider_usage_settings()
    clock = {"now": 1000.0}
    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={},
        credentials=object(),
        home=tmp_path,
        clock=lambda: clock["now"],
    )

    service.refresh_now()
    clock["now"] = 1001.0
    result = service.request(
        callback=lambda _state: None,
        providers=("codex",),
        force=False,
    )

    assert result.refreshing is False
    assert service.snapshot().refreshing is False


def test_service_exposes_the_exact_settings_snapshot_used_for_collection(tmp_path):
    settings = default_provider_usage_settings().with_enabled("grok", False)
    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={},
        credentials=object(),
        home=tmp_path,
        clock=lambda: 1000.0,
    )

    assert service.settings_snapshot() is None
    service.refresh_now(force=True)

    assert service.settings_snapshot() is settings


def test_collectors_receive_only_the_typed_collection_projection(tmp_path):
    settings = default_provider_usage_settings()
    observed_preferences = []

    def collect(preference, _home, observed, _credentials):
        observed_preferences.append(preference)
        assert type(preference) is ProviderCollectionFeature
        assert not hasattr(preference, "menu_visible")
        assert not hasattr(preference, "reset_celebrations")
        assert not hasattr(preference, "threshold_remaining")
        return snapshot("devin", observed=observed)

    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={"devin": collect},
        credentials=object(),
        home=tmp_path,
        clock=lambda: 1000.0,
    )

    service.refresh_now(providers=("devin",), force=True)

    assert len(observed_preferences) == 1


def test_same_provider_instances_collect_and_remain_exactly_addressable(tmp_path):
    settings = default_provider_usage_settings()
    settings = settings.with_instance(
        dataclass_replace(
            settings.preference("claude"),
            source_instance_id="work",
            options=(("account", "work"),),
        )
    )

    def collect(preference, _home, observed, _credentials):
        return snapshot(
            "claude",
            remaining=20 if preference.source_instance_id == "work" else 80,
            observed=observed,
        )

    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={"claude": collect},
        credentials=object(),
        home=tmp_path,
        clock=lambda: 1000.0,
    )

    state = service.refresh_now(providers=("claude",), force=True)

    assert state.by_instance("claude", "default").lanes[0].remaining_percent == 80
    assert state.by_instance("claude", "work").lanes[0].remaining_percent == 20
    with pytest.raises(ValueError, match="ambiguous"):
        state.by_provider("claude")


def test_explicit_settings_update_outlives_an_older_worker_load(tmp_path):
    initial = default_provider_usage_settings()
    updated = initial.with_enabled("grok", False)
    load_started = threading.Event()
    release_load = threading.Event()

    def load_settings():
        load_started.set()
        release_load.wait(2.0)
        return initial

    service = ProviderUsageService(
        settings_loader=load_settings,
        collectors={},
        credentials=object(),
        home=tmp_path,
        clock=time.time,
    )
    callbacks = []
    callbacks_ready = threading.Event()

    def callback(state):
        callbacks.append(state)
        callbacks_ready.set()

    service.request(callback=callback, force=True)
    assert load_started.wait(1.0)

    service.note_settings_updated(updated)
    release_load.set()
    assert callbacks_ready.wait(3.0)

    assert callbacks
    assert service.settings_snapshot() is updated
    service.close()


def test_provider_usage_apply_rejects_mixed_or_untyped_payloads():
    state = ProviderUsageState((), None, None, False)
    settings = project_presentation_settings(default_provider_usage_settings())

    assert ProviderUsageApply(state, settings).state is state
    with pytest.raises(ValueError, match="invalid provider usage state"):
        ProviderUsageApply(object(), settings)
    with pytest.raises(ValueError, match="invalid provider usage settings"):
        ProviderUsageApply(state, object())


def test_service_restores_and_persists_last_known_good(tmp_path):
    settings = default_provider_usage_settings()
    initial = ProviderUsageState(
        (snapshot("codex", remaining=17, observed=900),),
        900,
        960,
        False,
    )
    saved = []
    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={
            "codex": lambda _pref, _home, observed, _credentials: snapshot(
                "codex",
                state=ProviderSourceState.UNAVAILABLE,
                observed=observed,
            )
        },
        credentials=object(),
        home=tmp_path,
        clock=lambda: 1000,
        state_loader=lambda: initial,
        state_saver=saved.append,
    )
    assert service.snapshot() == initial
    refreshed = service.refresh_now(providers=("codex",), force=True)
    assert refreshed.by_provider("codex").state is ProviderSourceState.STALE
    assert refreshed.by_provider("codex").lanes[0].remaining_percent == 17
    assert saved == [refreshed]


def test_a_stale_but_real_reading_is_not_replaced_by_the_last_known_good(tmp_path):
    """Reported as "why does it say codex ... 48 percent, it should be
    around 96". The collector correctly marked a three-day-old Codex
    quota STALE, and this substitution handed the OLDER ready snapshot
    back instead -- so a frozen number kept rendering as live. A stale
    reading carrying real lanes is newer information than last_known_good
    and must win; only a reading with nothing in it falls back."""
    settings = default_provider_usage_settings().with_enabled("grok", False)
    current = {"state": ProviderSourceState.READY}

    def collector(_pref, _home, observed, _credentials):
        return snapshot("codex", state=current["state"], remaining=48, observed=observed)

    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={"codex": collector},
        credentials=object(),
        home=tmp_path,
        clock=lambda: 1000,
    )

    first = service.refresh_now()
    codex = next(s for s in first.snapshots if s.provider_id == "codex")
    assert codex.state is ProviderSourceState.READY

    current["state"] = ProviderSourceState.STALE
    second = service.refresh_now()
    codex = next(s for s in second.snapshots if s.provider_id == "codex")
    assert codex.state is ProviderSourceState.STALE, "last_known_good masked a stale reading"
    assert codex.lanes[0].remaining_percent == 48


def test_rate_limited_provider_backs_off_instead_of_hammering(tmp_path):
    """The Claude usage endpoint 429s; before the failure gate the
    service asked again every refresh, which is how one STAYS rate
    limited. A gated provider serves its previous snapshot; a forced
    (user-initiated) refresh still pushes through."""
    settings = default_provider_usage_settings().with_enabled("grok", False)
    calls = []
    clock = {"now": 1000.0}

    def collector(_pref, _home, observed, _credentials):
        calls.append(observed)
        return snapshot(
            "claude", state=ProviderSourceState.RATE_LIMITED, observed=observed
        )

    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={"claude": collector},
        credentials=object(),
        home=tmp_path,
        clock=lambda: clock["now"],
    )

    service.refresh_now(providers=("claude",))
    assert len(calls) == 1
    clock["now"] = 1120.0  # inside the 300 s first rung
    service.refresh_now(providers=("claude",))
    assert len(calls) == 1, "a gated provider was re-collected"
    service.refresh_now(providers=("claude",), force=True)
    assert len(calls) == 2, "force must bypass the gate"
    clock["now"] = 1000.0 + 10_000.0  # past every rung
    service.refresh_now(providers=("claude",))
    assert len(calls) == 3


def test_terminal_gate_lifts_when_the_credential_file_changes(tmp_path):
    """A signed-out provider is not worth re-asking every two minutes;
    it IS worth re-asking the moment the user signs in somewhere. The
    gate watches the provider's own credential file for that."""
    import json as _json

    settings = default_provider_usage_settings()
    calls = []
    clock = {"now": 1000.0}

    def collector(_pref, _home, observed, _credentials):
        calls.append(observed)
        return snapshot(
            "grok", state=ProviderSourceState.NEEDS_SIGN_IN, observed=observed
        )

    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={"grok": collector},
        credentials=object(),
        home=tmp_path,
        clock=lambda: clock["now"],
    )

    service.refresh_now(providers=("grok",))
    assert len(calls) == 1
    clock["now"] = 1200.0
    service.refresh_now(providers=("grok",))
    assert len(calls) == 1, "terminal failure was re-collected with no change"

    grok_dir = tmp_path / ".grok"
    grok_dir.mkdir()
    (grok_dir / "auth.json").write_text(
        _json.dumps({"https://auth.x.ai::a": {"key": "k" * 24}}),
        encoding="utf-8",
    )
    clock["now"] = 1300.0
    service.refresh_now(providers=("grok",))
    assert len(calls) == 2, "a credential change must lift the gate"


def test_ready_without_lanes_does_not_clobber_a_real_reading(tmp_path):
    """A lane-less READY says "the scan found no quota evidence" -- the
    absence of a reading, not a newer one. It must neither replace the
    last known good numbers nor render as a bare card with no number."""
    settings = default_provider_usage_settings().with_enabled("grok", False)
    current = {"lanes": True}

    def collector(_pref, _home, observed, _credentials):
        if current["lanes"]:
            return snapshot("codex", remaining=48, observed=observed)
        base = snapshot("codex", observed=observed)
        import dataclasses as _dataclasses

        return _dataclasses.replace(base, lanes=())

    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={"codex": collector},
        credentials=object(),
        home=tmp_path,
        clock=lambda: 1000,
    )

    first = service.refresh_now(providers=("codex",))
    assert first.by_provider("codex").lanes

    current["lanes"] = False
    second = service.refresh_now(providers=("codex",))
    codex = second.by_provider("codex")
    assert codex.state is ProviderSourceState.STALE
    assert codex.lanes and codex.lanes[0].remaining_percent == 48

    current["lanes"] = True
    third = service.refresh_now(providers=("codex",))
    assert third.by_provider("codex").state is ProviderSourceState.READY


def test_forced_request_during_callback_delivery_is_not_swallowed(tmp_path):
    """Hostile-review regression: the worker used to exit its rerun
    loop and only THEN deliver callbacks, still alive -- a forced
    request landing in that window piggybacked on a thread that would
    never look at its flags again. The click was swallowed and the
    leaked flags fired a spurious forced run minutes later. The worker
    now retires under the lock, so a request during delivery starts a
    fresh worker."""
    settings = default_provider_usage_settings()
    collects = []

    def collect(_pref, _home, observed, _credentials):
        collects.append(observed)
        return snapshot("codex", observed=observed)

    service = ProviderUsageService(
        settings_loader=lambda: settings,
        collectors={"codex": collect},
        credentials=object(),
        home=tmp_path,
        clock=time.time,
    )

    states = []
    second_round = threading.Event()

    def first_callback(state):
        states.append(("first", state))
        # We are INSIDE delivery: the worker has already made its exit
        # decision. A forced request from here must not be lost.
        service.request(
            callback=lambda s: (states.append(("second", s)), second_round.set()),
            providers=("codex",),
            force=True,
        )

    service.request(callback=first_callback, providers=("codex",), force=True)
    assert second_round.wait(3), "the mid-delivery forced request was swallowed"
    assert len(collects) == 2
    assert [name for name, _s in states] == ["first", "second"]
    with service._lock:
        assert service._rerun_requested is False
        assert not service._forced_providers
    service.close()


def test_a_visible_quota_strip_counts_as_attention():
    """Our one adaptation of CodexBar's ladder: they only have a menu,
    we can be showing the number on the LED bar the whole time."""
    from sidepulse.provider_usage_runtime import _interval_for

    assert _interval_for((), 10_000.0, menu_last_opened_at=None) == 1800.0
    assert (
        _interval_for((), 10_000.0, menu_last_opened_at=None, ambient_usage_visible=True)
        == 300.0
    )
    # It only ever tightens; a fresh visit still wins.
    assert (
        _interval_for(
            (), 10_000.0, menu_last_opened_at=9_990.0, ambient_usage_visible=True
        )
        == 120.0
    )
    # Low Power Mode outranks the ambient surface.
    assert (
        _interval_for(
            (),
            10_000.0,
            menu_last_opened_at=9_990.0,
            constrained=True,
            ambient_usage_visible=True,
        )
        == 1800.0
    )


def test_stale_refresh_is_superseded_then_worker_reruns_latest_settings(tmp_path):
    initial = default_provider_usage_settings().with_enabled("grok", True)
    updated = initial.with_enabled("grok", False)
    load_started = threading.Event()
    release_load = threading.Event()
    calls = []
    receipts = []

    def load_settings():
        load_started.set()
        release_load.wait(2.0)
        return initial if len(calls) == 0 else updated

    def collect(preference, _home, observed, _credentials):
        calls.append(preference.provider_id)
        return snapshot(preference.provider_id, observed=observed)

    service = ProviderUsageService(
        settings_loader=load_settings,
        collectors={"grok": collect},
        credentials=object(),
        home=tmp_path,
        clock=lambda: 1000.0,
        receipt_handler=receipts.append,
    )
    callbacks = []
    done = threading.Event()

    service.request(
        callback=lambda state: (callbacks.append(state), done.set()), force=True
    )
    assert load_started.wait(1.0)
    service.note_settings_updated(updated)
    release_load.set()

    assert done.wait(3.0)
    assert service.settings_snapshot() is updated
    assert callbacks and callbacks[-1].by_provider("grok").state is ProviderSourceState.DISABLED
    assert len(callbacks) == 1
    assert any(item.outcome is RefreshPublicationOutcome.SUPERSEDED for item in receipts)
    assert any(item.outcome is RefreshPublicationOutcome.ACCEPTED for item in receipts)
    service.close()


def test_settings_update_after_persistence_suppresses_old_callback(tmp_path):
    initial = default_provider_usage_settings().with_enabled("grok", True)
    updated = initial.with_enabled("grok", False)
    current = {"settings": initial}
    saved = []
    callbacks = []
    persisted = threading.Event()
    allow_receipt = threading.Event()
    update_done = threading.Event()

    def receipt_handler(receipt):
        if receipt.outcome is RefreshPublicationOutcome.ACCEPTED and not persisted.is_set():
            # The receipt is emitted after state persistence. Hold the
            # worker in the callback-delivery gap while another thread edits
            # settings, after the durable save but before callback delivery.
            persisted.set()
            allow_receipt.wait(2.0)

    def update_settings() -> None:
        assert persisted.wait(2.0)
        current["settings"] = updated
        service.note_settings_updated(updated)
        update_done.set()

    service = ProviderUsageService(
        settings_loader=lambda: current["settings"],
        collectors={
            "grok": lambda preference, _home, observed, _credentials: snapshot(
                preference.provider_id, observed=observed
            )
        },
        credentials=object(),
        home=tmp_path,
        clock=lambda: 1000.0,
        state_saver=saved.append,
        receipt_handler=receipt_handler,
    )
    done = threading.Event()
    updater = threading.Thread(target=update_settings, daemon=True)
    updater.start()

    service.request(
        callback=lambda state: (callbacks.append(state), done.set()),
        providers=("grok",),
        force=True,
    )

    assert persisted.wait(2.0)
    assert update_done.wait(2.0)
    allow_receipt.set()
    assert done.wait(3.0)
    assert len(saved) == 2
    assert callbacks[-1].by_provider("grok").state is ProviderSourceState.DISABLED
    assert len(callbacks) == 1
    service.close()
