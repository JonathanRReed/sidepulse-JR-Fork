from __future__ import annotations

import subprocess
import threading
import webbrowser
from pathlib import Path
from types import SimpleNamespace

import pytest


def _adaptive_refresh():
    from sidepulse import adaptive_refresh

    return adaptive_refresh


def _refresh_state(
    provider_id: str,
    *,
    enabled: bool = True,
    visible: bool = True,
    last_success_at: float | None = None,
    in_flight: bool = False,
    retry_not_before: float = 0.0,
):
    from sidepulse.capacity_types import SourceKey
    from sidepulse.refresh_policy import ProviderRefreshState

    return ProviderRefreshState(
        SourceKey(provider_id, "quota", "local", "remote_quota_windows"),
        enabled=enabled,
        visible=visible,
        last_success_at=last_success_at,
        in_flight=in_flight,
        retry_not_before=retry_not_before,
    )


def _usage_snapshot(
    *,
    state="ready",
    reset_at: float | None = None,
):
    from sidepulse.provider_usage_platform import (
        ProviderSourceState,
        ProviderUsageSnapshot,
        UsageLane,
    )

    source_state = ProviderSourceState(state)
    lane = UsageLane(
        provider_id="codex",
        lane_id="weekly",
        label="Weekly",
        remaining_percent=50.0,
        reset_at=reset_at,
        scope="all",
        model=None,
        feature=None,
        bindable=True,
        source_id="fixture",
    )
    return ProviderUsageSnapshot(
        provider_id="codex",
        account_label=None,
        observed_at=10_000.0,
        state=source_state,
        reason_code=None if source_state is ProviderSourceState.READY else "fixture",
        action_label=None if source_state is ProviderSourceState.READY else "Retry",
        lanes=(lane,),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
    )


def _menu_controller_with_refresh_states(*, controller=None):
    from sidepulse.status_bar import StatusBarController

    target = controller if controller is not None else SimpleNamespace()
    target._usage_provider_states = {
        "fresh": _refresh_state("fresh", last_success_at=9_900.0),
        "stale": _refresh_state("stale", last_success_at=9_500.0),
        "missing": _refresh_state("missing"),
    }
    target._usage_transcript_states = {}
    target._capacity_row_enabled = lambda _provider_id: True
    target._provider_refresh_calls = []

    def request_usage_refresh(invocations, *, reason=None):
        target._provider_refresh_calls.append(
            {"invocations": tuple(invocations), "reason": reason}
        )

    target.request_usage_refresh = request_usage_refresh
    target.jr_plane_owns_capacity = lambda _provider_id: False
    target.request_jr_usage_refresh = lambda *_args, **_kwargs: None
    target.mark_activity_seen_now = lambda _timestamp: None
    target.note_menu_opened = lambda **_kwargs: None
    target.current_attention_projection = None
    target.mailbox_seen_completion_ids = set()
    target.current_mailbox_projection = None
    target.mailbox_retained_order = {}
    target._runtime_started = False
    target.refresh_capacity_settings_projection = lambda: None
    target._sidepulse_provider_usage_service = None
    target._provider_usage_log = lambda _message: None
    target._sidepulse_provider_usage_window = None
    target._alert_new_critical_pace = lambda *_args: None
    target._alert_connection_loss = lambda *_args: None
    target._report_reconnect_outcome = lambda *_args: None
    target._celebrate_quota_resets = lambda *_args: None
    target._menu_signature = None
    target.current_settings_pane = None
    target.settings_window = None
    target.virtual_status_device = SimpleNamespace(
        set_pointer_interaction_relevant=lambda *_args: None
    )
    target.status_menu_open = False
    target.last_snapshot = None
    return target, StatusBarController


def test_adaptive_cadence_plan_preserves_current_interval_precedence() -> None:
    adaptive_refresh = _adaptive_refresh()

    plan = adaptive_refresh.plan_adaptive_refresh_cadence(
        (_usage_snapshot(reset_at=10_120.0),),
        observed_at=10_000.0,
        menu_last_opened_at=9_990.0,
        constrained=True,
        ambient_usage_visible=True,
    )

    assert isinstance(plan, adaptive_refresh.AdaptiveRefreshPlan)
    assert plan.reason == adaptive_refresh.AdaptiveRefreshReason.CONSTRAINED
    assert plan.interval_seconds == 1800.0
    from sidepulse.provider_usage_runtime import _interval_for

    assert plan.interval_seconds == _interval_for(
        (_usage_snapshot(reset_at=10_120.0),),
        10_000.0,
        menu_last_opened_at=9_990.0,
        constrained=True,
        ambient_usage_visible=True,
    )


@pytest.mark.parametrize(
    ("menu_opened", "ambient", "snapshots", "reason", "interval"),
    (
        (9_990.0, False, (), "recent_menu", 120.0),
        (9_000.0, False, (), "warm_menu", 300.0),
        (6_000.0, False, (), "aging_menu", 900.0),
        (None, False, (), "idle", 1800.0),
        (None, True, (), "ambient_usage", 300.0),
        (None, False, (_usage_snapshot(state="error"),), "degraded_source", 120.0),
        (None, False, (_usage_snapshot(reset_at=10_120.0),), "reset_watch", 120.0),
    ),
)
def test_adaptive_cadence_plan_explains_each_current_rung(
    menu_opened,
    ambient,
    snapshots,
    reason,
    interval,
) -> None:
    adaptive_refresh = _adaptive_refresh()

    plan = adaptive_refresh.plan_adaptive_refresh_cadence(
        snapshots,
        observed_at=10_000.0,
        menu_last_opened_at=menu_opened,
        ambient_usage_visible=ambient,
    )

    assert plan.reason.value == reason
    assert plan.interval_seconds == interval


def test_adaptive_cadence_reason_is_stable_for_mixed_short_interval_causes() -> None:
    adaptive_refresh = _adaptive_refresh()
    degraded = _usage_snapshot(state="error")
    reset_watch = _usage_snapshot(reset_at=10_120.0)

    forward = adaptive_refresh.plan_adaptive_refresh_cadence(
        (degraded, reset_watch),
        observed_at=10_000.0,
    )
    reverse = adaptive_refresh.plan_adaptive_refresh_cadence(
        (reset_watch, degraded),
        observed_at=10_000.0,
    )

    assert forward == reverse
    assert forward.reason is adaptive_refresh.AdaptiveRefreshReason.RESET_WATCH
    assert forward.interval_seconds == 120.0


def test_rate_limiting_keeps_the_idle_cadence_for_failure_gate_backoff() -> None:
    adaptive_refresh = _adaptive_refresh()

    plan = adaptive_refresh.plan_adaptive_refresh_cadence(
        (_usage_snapshot(state="rate_limited"),),
        observed_at=10_000.0,
    )

    assert plan.reason is adaptive_refresh.AdaptiveRefreshReason.IDLE
    assert plan.interval_seconds == 1800.0


def test_menu_open_admission_returns_a_bounded_receipt() -> None:
    adaptive_refresh = _adaptive_refresh()
    notifications = []
    plans = []
    service = SimpleNamespace(
        note_menu_opened=lambda *, now: notifications.append(now)
    )
    controller = SimpleNamespace(
        _sidepulse_provider_usage_service=service,
        maybe_refresh_usage_summary=lambda *, reason: plans.append(reason),
    )

    receipt = adaptive_refresh.admit_menu_open_refresh(controller, wall_clock=lambda: 1_000.0)

    assert isinstance(receipt, adaptive_refresh.MenuOpenAdmissionReceipt)
    assert receipt.reason == "menu-open"
    assert receipt.provider_service_notified is True
    assert receipt.refresh_planned is True
    assert receipt.wall_clock == 1_000.0
    assert notifications == [1_000.0]
    assert plans == ["menu-open"]
    assert set(receipt.__slots__) <= {
        "provider_service_notified",
        "refresh_planned",
        "reason",
        "wall_clock",
    }


def test_menu_open_admission_preserves_planner_failure_semantics() -> None:
    adaptive_refresh = _adaptive_refresh()
    notifications = []

    def fail_planning(*, reason):
        assert reason == "menu-open"
        raise RuntimeError("planner failed")

    controller = SimpleNamespace(
        _sidepulse_provider_usage_service=SimpleNamespace(
            note_menu_opened=lambda *, now: notifications.append(now)
        ),
        maybe_refresh_usage_summary=fail_planning,
    )

    with pytest.raises(RuntimeError, match="planner failed"):
        adaptive_refresh.admit_menu_open_refresh(
            controller,
            wall_clock=lambda: 1_000.0,
        )

    assert notifications == [1_000.0]
    assert not hasattr(controller, "_sidepulse_adaptive_refresh_visit_receipt")


def test_maybe_refresh_usage_summary_does_not_run_io_on_the_caller_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    controller, StatusBarController = _menu_controller_with_refresh_states()
    caller_thread = threading.current_thread().name
    observed_calls: list[str] = []
    forbidden: list[str] = []

    def refuse_io(*_args, **_kwargs):
        forbidden.append(threading.current_thread().name)
        raise AssertionError("caller-thread I/O")

    original_request = controller.request_usage_refresh

    def record_request(*args, **kwargs):
        observed_calls.append(threading.current_thread().name)
        return original_request(*args, **kwargs)

    controller.request_usage_refresh = record_request
    monkeypatch.setattr("sidepulse.status_bar_legacy.time.monotonic", lambda: 10_000.0)
    monkeypatch.setattr(
        "sidepulse.status_bar_legacy.runtime_render_environment",
        lambda **_kwargs: SimpleNamespace(low_power=False),
    )
    monkeypatch.setattr(Path, "read_text", refuse_io)
    monkeypatch.setattr(subprocess, "run", refuse_io)
    monkeypatch.setattr(webbrowser, "open", refuse_io)

    StatusBarController.maybe_refresh_usage_summary(controller, reason="menu-open")

    assert observed_calls == [caller_thread]
    assert not forbidden
    assert controller._provider_refresh_calls == [
        {
            "invocations": tuple(
                state.source_key
                for name, state in controller._usage_provider_states.items()
                if name in {"stale", "missing"}
            ),
            "reason": "menu-open",
        },
    ]


def test_provider_usage_service_exposes_the_current_cadence_plan() -> None:
    adaptive_refresh = _adaptive_refresh()

    from sidepulse.provider_usage_runtime import ProviderUsageService, ProviderUsageState

    service = ProviderUsageService(
        settings_loader=lambda: SimpleNamespace(),
        credentials=object(),
        home=Path.cwd(),
        state_loader=lambda: ProviderUsageState((), None, None, False),
    )

    plan = service.cadence_plan()

    assert isinstance(plan, adaptive_refresh.AdaptiveRefreshPlan)


def test_provider_usage_service_cadence_receipt_tracks_the_last_accepted_refresh(
    tmp_path,
) -> None:
    adaptive_refresh = _adaptive_refresh()
    from sidepulse.provider_usage_runtime import ProviderUsageService
    from sidepulse.provider_usage_settings import default_provider_usage_settings

    clock = [10_000.0]
    service = ProviderUsageService(
        settings_loader=default_provider_usage_settings,
        credentials=object(),
        home=tmp_path,
        clock=lambda: clock[0],
        collectors={
            "codex": lambda _preference, _home, _now, _credentials: _usage_snapshot()
        },
    )

    service.refresh_now(providers=("codex",))
    assert service.cadence_plan().reason is adaptive_refresh.AdaptiveRefreshReason.IDLE
    assert service.cadence_plan().interval_seconds == 1800.0

    service.note_menu_opened(now=clock[0])
    service.refresh_now(providers=("codex",))
    assert (
        service.cadence_plan().reason
        is adaptive_refresh.AdaptiveRefreshReason.RECENT_MENU
    )
    assert service.cadence_plan().interval_seconds == 120.0


def test_menu_attention_pulls_the_cached_due_time_forward_without_collection(
    tmp_path,
) -> None:
    adaptive_refresh = _adaptive_refresh()
    from sidepulse.provider_usage_runtime import ProviderUsageService
    from sidepulse.provider_usage_settings import default_provider_usage_settings

    clock = [10_000.0]
    collection_times: list[float] = []

    def collect(_preference, _home, observed_at, _credentials):
        collection_times.append(observed_at)
        return _usage_snapshot()

    service = ProviderUsageService(
        settings_loader=default_provider_usage_settings,
        credentials=object(),
        home=tmp_path,
        clock=lambda: clock[0],
        collectors={"codex": collect},
    )
    idle = service.refresh_now(providers=("codex",))
    assert idle.next_refresh_at == 11_800.0
    assert collection_times == [10_000.0]

    service.note_menu_opened(now=clock[0])

    recent = service.snapshot()
    assert recent.next_refresh_at == 10_120.0
    assert (
        service.cadence_plan().reason
        is adaptive_refresh.AdaptiveRefreshReason.RECENT_MENU
    )
    assert collection_times == [10_000.0]

    clock[0] = 10_119.0
    not_due = service.request(
        callback=lambda _state: pytest.fail("early refresh callback"),
        providers=("codex",),
    )
    assert not_due.refreshing is False
    assert collection_times == [10_000.0]

    refreshed = threading.Event()
    clock[0] = 10_120.0
    due = service.request(
        callback=lambda _state: refreshed.set(),
        providers=("codex",),
    )
    assert due.refreshing is True
    assert refreshed.wait(2.0)
    service.close()
    assert collection_times == [10_000.0, 10_120.0]


def test_ambient_visibility_pulls_the_cached_due_time_forward_without_collection(
    tmp_path,
) -> None:
    adaptive_refresh = _adaptive_refresh()
    from sidepulse.provider_usage_runtime import ProviderUsageService
    from sidepulse.provider_usage_settings import default_provider_usage_settings

    clock = [10_000.0]
    collection_times: list[float] = []

    def collect(_preference, _home, observed_at, _credentials):
        collection_times.append(observed_at)
        return _usage_snapshot()

    service = ProviderUsageService(
        settings_loader=default_provider_usage_settings,
        credentials=object(),
        home=tmp_path,
        clock=lambda: clock[0],
        collectors={"codex": collect},
    )
    idle = service.refresh_now(providers=("codex",))
    assert idle.next_refresh_at == 11_800.0

    service.note_ambient_usage_visible(True)

    ambient = service.snapshot()
    assert ambient.next_refresh_at == 10_300.0
    assert (
        service.cadence_plan().reason
        is adaptive_refresh.AdaptiveRefreshReason.AMBIENT_USAGE
    )
    assert collection_times == [10_000.0]
