from __future__ import annotations

import threading
import time

from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from sidepulse.provider_usage_runtime import ProviderUsageService, ProviderUsageState
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


def test_adaptive_refresh_accelerates_for_low_capacity(tmp_path):
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
    assert state.next_refresh_at == 1030


def test_request_runs_off_caller_thread_and_coalesces(tmp_path):
    settings = default_provider_usage_settings()
    gate = threading.Event()
    collector_threads = []

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
    first = service.request(callback=callbacks.append, providers=("codex",), force=True)
    second = service.request(callback=callbacks.append, providers=("codex",), force=True)
    assert first.refreshing is True
    assert second.refreshing is True
    gate.set()
    deadline = time.time() + 3
    while time.time() < deadline and not callbacks:
        time.sleep(0.01)
    assert len(collector_threads) == 1
    assert collector_threads[0] != threading.current_thread().name
    assert callbacks and callbacks[0].refreshing is False
    service.close()


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
