from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from sidepulse import status_bar
from sidepulse.attention import AttentionProjection, LifecycleMode
from sidepulse.dnd_controller import DndController
from sidepulse.dnd_policy import (
    DndMode,
    DndOverride,
    DndSource,
    compose_dnd_contributions,
    contribution_for_mode,
)
from sidepulse.focus_status import (
    FocusActivity,
    FocusAuthorization,
    FocusStatusObservation,
)
from sidepulse.models import AgentMode
from sidepulse.presentation_policy import FiniteCue, GlanceSemantic

DND_DARK_DISPLAY = "dnd_dark"


def _projection(mode: DndMode):
    return compose_dnd_contributions(
        (contribution_for_mode(DndSource.MANUAL, mode, dim_fraction=0.25),),
        next_transition_epoch=1_800_000_000.0,
    )


@pytest.fixture
def controller(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(
        status_bar,
        "default_settings_path",
        lambda: tmp_path / "settings.json",
    )
    monkeypatch.setattr(
        status_bar,
        "default_latest_state_path",
        lambda: tmp_path / "latest.json",
    )
    monkeypatch.setattr(
        status_bar,
        "default_activity_ledger_path",
        lambda: tmp_path / "activity-ledger.json",
    )
    monkeypatch.setattr(status_bar, "discover_devices", lambda: [])
    return status_bar.StatusBarController.alloc().init()


def _device(*, brightness: int = 200):
    return status_bar.StatusBarDevice(
        device_id="test-device",
        name="Test Device",
        root=Path("/Volumes/Test"),
        target=Path("/Volumes/Test/LEDS.LED"),
        connected=True,
        display=status_bar.LED_DISPLAY_AGENT,
        brightness=brightness,
    )


def _attention(mode: LifecycleMode) -> AttentionProjection:
    return AttentionProjection(mode, (), (), (), None, None)


def test_retained_controller_owns_one_fail_closed_dnd_controller(controller) -> None:
    assert type(controller.dnd_controller) is DndController
    assert controller.dnd_controller.started is False
    assert controller.current_dnd_projection().summary == "DND: Off"
    assert controller.current_dnd_projection() is controller.dnd_controller.projection


@pytest.mark.parametrize(
    ("observation", "expected"),
    (
        (
            FocusStatusObservation(
                FocusAuthorization.AUTHORIZED,
                FocusActivity.INACTIVE,
            ),
            "No Focus is active.",
        ),
        (
            FocusStatusObservation(
                FocusAuthorization.UNAVAILABLE,
                FocusActivity.UNAVAILABLE,
            ),
            "Focus Status is unavailable.",
        ),
        (
            FocusStatusObservation(
                FocusAuthorization.NOT_DETERMINED,
                FocusActivity.UNAVAILABLE,
            ),
            "Focus Status permission has not been requested.",
        ),
        (
            FocusStatusObservation(
                FocusAuthorization.RESTRICTED,
                FocusActivity.UNAVAILABLE,
            ),
            "Focus Status access is restricted.",
        ),
        (
            FocusStatusObservation(
                FocusAuthorization.DENIED,
                FocusActivity.UNAVAILABLE,
            ),
            "Focus Status access is denied.",
        ),
        (
            FocusStatusObservation(
                FocusAuthorization.AUTHORIZED,
                FocusActivity.UNAVAILABLE,
            ),
            "Focus activity is unavailable.",
        ),
    ),
)
def test_focus_summary_withholds_private_detail_without_public_active_truth(
    controller,
    monkeypatch: pytest.MonkeyPatch,
    observation: FocusStatusObservation,
    expected: str,
) -> None:
    secret = "private-focus-must-not-appear"
    controller.settings = controller.settings.with_focus_sync_enabled(True)
    controller.dnd_controller = SimpleNamespace(
        projection=compose_dnd_contributions(()),
        focus_observation=observation,
        named_focus_identifiers=(secret,),
    )
    monkeypatch.setattr(
        status_bar.focus_sync,
        "active_focus_mode_identifiers",
        lambda: pytest.fail("summary reread private Focus activity"),
    )
    monkeypatch.setattr(
        status_bar.focus_sync,
        "configured_focus_modes",
        lambda: pytest.fail("summary reread private Focus names"),
    )

    summary = controller.active_focus_summary()

    assert summary == expected
    assert secret not in summary


def test_settings_and_menu_summary_share_the_gated_focus_view(
    controller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sidepulse.settings_window import refresh_dnd_settings_controls

    secret = "private-focus-must-not-reach-surfaces"
    controller.settings = controller.settings.with_focus_sync_enabled(True)
    controller.dnd_controller = SimpleNamespace(
        projection=compose_dnd_contributions(()),
        focus_observation=FocusStatusObservation(
            FocusAuthorization.AUTHORIZED,
            FocusActivity.INACTIVE,
        ),
        named_focus_identifiers=(secret,),
    )
    monkeypatch.setattr(
        status_bar.focus_sync,
        "active_focus_mode_identifiers",
        lambda: pytest.fail("surface summary reread private Focus activity"),
    )
    monkeypatch.setattr(
        status_bar.focus_sync,
        "configured_focus_modes",
        lambda: pytest.fail("surface summary reread private Focus names"),
    )
    settings_values: list[str] = []
    label = SimpleNamespace(
        setStringValue_=settings_values.append,
        setAccessibilityValue_=lambda value: settings_values.append(value),
    )
    controller.settings_fields = {"focus_now_label": label}
    controller.settings_buttons = {}

    refresh_dnd_settings_controls(controller)
    snapshot = SimpleNamespace(
        statuses=(),
        stale_statuses=(),
        collected_at=datetime.now(timezone.utc),
    )
    controller.status_bar_devices = lambda *args, **kwargs: []
    signature = status_bar.menu_content_signature(
        snapshot,
        status_bar.STATE_IDLE,
        controller,
    )

    assert settings_values == ["No Focus is active.", "No Focus is active."]
    assert secret not in repr(signature)


def test_focus_summary_uses_retained_named_detail_only_while_public_active(
    controller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller.settings = controller.settings.with_focus_sync_enabled(True)
    controller.dnd_controller = SimpleNamespace(
        projection=compose_dnd_contributions(()),
        focus_observation=FocusStatusObservation(
            FocusAuthorization.AUTHORIZED,
            FocusActivity.ACTIVE,
        ),
        named_focus_identifiers=("focus-work",),
    )
    monkeypatch.setattr(
        status_bar.focus_sync,
        "active_focus_mode_identifiers",
        lambda: pytest.fail("summary reread private Focus activity"),
    )
    monkeypatch.setattr(
        status_bar.focus_sync,
        "configured_focus_modes",
        lambda: pytest.fail("summary reread private Focus names"),
    )

    assert controller.active_focus_summary() == "focus-work \u2014 shared dim"


def test_mute_keeps_visual_grant_and_refuses_each_outbound_axis(controller) -> None:
    controller.dnd_controller = SimpleNamespace(projection=_projection(DndMode.MUTE))

    grant = controller.interrupt_grant(status_bar.signals_module.SIGNAL_COMPLETION)

    assert grant.allowed is True
    assert controller.may_interrupt(status_bar.signals_module.SIGNAL_COMPLETION)
    assert grant.banner_allowed is False
    assert grant.audible is False
    assert grant.webhook_allowed is False


@pytest.mark.parametrize(
    ("mode", "event", "expected"),
    (
        (DndMode.MUTE, "sidepulse.completion", False),
        (DndMode.DIM, "sidepulse.completion", True),
        (DndMode.PAUSE, "sidepulse.completion", False),
        (DndMode.PAUSE, "sidepulse.escalation", True),
        (DndMode.ASKS_ONLY, "sidepulse.completion", False),
        (DndMode.ASKS_ONLY, "sidepulse.escalation", True),
        (DndMode.DARK, "sidepulse.escalation", False),
    ),
)
def test_webhook_effect_site_consumes_the_exact_outbound_axis(
    controller,
    mode: DndMode,
    event: str,
    expected: bool,
) -> None:
    controller.dnd_controller = SimpleNamespace(projection=_projection(mode))

    assert controller.webhook_effect_allowed({"event": event}) is expected
    assert controller.webhook_effect_allowed({"event": "unknown"}) is False


@pytest.mark.parametrize(
    ("dnd_mode", "lifecycle", "expected"),
    (
        (DndMode.PAUSE, LifecycleMode.ACTIVE, DND_DARK_DISPLAY),
        (DndMode.PAUSE, LifecycleMode.FAILED_VISIBLE, status_bar.LED_DISPLAY_AGENT),
        (DndMode.ASKS_ONLY, LifecycleMode.WAITING, status_bar.LED_DISPLAY_AGENT),
        (DndMode.ASKS_ONLY, LifecycleMode.FAILED_VISIBLE, DND_DARK_DISPLAY),
        (DndMode.DARK, LifecycleMode.WAITING, DND_DARK_DISPLAY),
    ),
)
def test_standing_display_claim_uses_dnd_admission(
    controller,
    dnd_mode: DndMode,
    lifecycle: LifecycleMode,
    expected: str,
) -> None:
    controller.dnd_controller = SimpleNamespace(projection=_projection(dnd_mode))
    controller.current_attention_projection = _attention(lifecycle)

    assert controller.active_led_display_kind_for_device(_device(), None) == expected


@pytest.mark.parametrize("mode", (DndMode.PAUSE, DndMode.ASKS_ONLY, DndMode.DARK))
def test_restrictive_dnd_transition_consumes_prearmed_finite_cues_without_replay(
    controller,
    mode: DndMode,
) -> None:
    deadline = time.monotonic() + 60.0
    for field in (
        "completion_sweep_until",
        "all_clear_until",
        "connection_notice_until",
        "reminders_glow_until",
        "calendar_glow_until",
        "quota_blink_until",
        "quota_reset_celebration_until",
        "battery_preview_until",
        "test_signal_until",
        "peek_until",
    ):
        setattr(controller, field, deadline)
    controller.test_signal_key = "completion"
    cue = FiniteCue(
        "pre-dnd-completion",
        GlanceSemantic.FRESH_COMPLETION,
        2,
        0.5,
    )
    controller._status_cue_candidates = (cue,)
    controller._status_finite_cues = controller.status_cue_coordinator.observe(
        (cue,),
        now=time.monotonic(),
        play_motion=True,
    )
    controller._status_cue_deadline = controller._status_finite_cues.next_deadline

    restrictive = _projection(mode)
    controller.dnd_controller = SimpleNamespace(projection=restrictive)
    controller._dnd_projection_changed(restrictive)

    for field in (
        "completion_sweep_until",
        "all_clear_until",
        "connection_notice_until",
        "reminders_glow_until",
        "calendar_glow_until",
        "quota_blink_until",
        "quota_reset_celebration_until",
        "battery_preview_until",
        "test_signal_until",
        "peek_until",
    ):
        assert getattr(controller, field) == 0.0
    assert controller.test_signal_key is None
    assert controller._status_finite_cues.active is None
    assert controller._status_finite_cues.pending is None
    assert controller._status_cue_deadline is None

    lifted = compose_dnd_contributions(())
    controller.dnd_controller.projection = lifted
    controller._dnd_projection_changed(lifted)

    assert (
        controller.active_led_display_kind_for_device(_device(), None)
        == status_bar.LED_DISPLAY_AGENT
    )
    after = controller.status_cue_coordinator.observe(
        (cue,),
        now=time.monotonic(),
        play_motion=True,
    )
    assert after.active is None
    assert after.pending is None


@pytest.mark.parametrize("mode", (DndMode.MUTE, DndMode.DIM))
def test_visual_dnd_transition_preserves_prearmed_finite_cues(
    controller,
    mode: DndMode,
) -> None:
    deadline = time.monotonic() + 60.0
    controller.completion_sweep_until = deadline
    visual = _projection(mode)
    controller.dnd_controller = SimpleNamespace(projection=visual)

    controller._dnd_projection_changed(visual)

    assert controller.completion_sweep_until == deadline


def test_async_calendar_cue_armed_during_pause_is_consumed_without_replay(
    controller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = 650.0
    now = time.time()
    controller.settings = (
        controller.settings.with_calendar_alerts_enabled(True).with_dnd_override(
            DndOverride.for_mode(
                DndMode.PAUSE,
                created_epoch=now,
                until_epoch=now + 3600.0,
            )
        )
    )
    controller._calendar_observation_active = True
    controller._os_poll_generation = 11
    controller._runtime_started = True
    controller._runtime_worker_monotonic = lambda: clock
    controller._presentation_monotonic = lambda: clock
    controller._presentation_scheduler_inputs = status_bar.PresentationSchedulerInputs(
        screen_bar_enabled=False,
        visible=False,
        display_asleep=False,
        app_terminating=False,
        animation_active=False,
        next_visual_change_at=None,
        alcove_enabled=False,
        alcove_relevant=False,
        pointer_interaction_relevant=False,
    )
    monkeypatch.setattr(controller, "refresh_", lambda _sender: None)
    controller.dnd_controller.start()
    command = status_bar.RuntimeWorkCommand(
        status_bar.RuntimeWorkerDomain.OS_POLL,
        "calendar-observation",
        11,
        clock + 30.0,
        status_bar.CalendarObservationRequest(lead_minutes=10.0),
    )

    controller._apply_os_poll_result(
        command,
        status_bar.CalendarObservationResult(
            available=True,
            starts_in_seconds=90.0,
        ),
    )

    assert controller.calendar_glow_until == clock + 90.0
    assert (
        controller.active_led_display_kind_for_device(_device(), None)
        != status_bar.LED_DISPLAY_CALENDAR
    )
    assert controller.calendar_glow_until == 0.0

    controller.settings = controller.settings.with_dnd_override(None)
    controller.dnd_controller.refresh()

    assert (
        controller.active_led_display_kind_for_device(_device(), None)
        == status_bar.LED_DISPLAY_AGENT
    )


@pytest.mark.parametrize(
    "lifecycle",
    (LifecycleMode.WAITING, LifecycleMode.FAILED_VISIBLE),
)
def test_standing_critical_truth_returns_after_fully_dark_without_finite_replay(
    controller,
    lifecycle: LifecycleMode,
) -> None:
    standing = _attention(lifecycle)
    controller.current_attention_projection = standing
    controller.completion_sweep_until = time.monotonic() + 60.0
    dark = _projection(DndMode.DARK)
    controller.dnd_controller = SimpleNamespace(projection=dark)

    controller._dnd_projection_changed(dark)

    assert (
        controller.active_led_display_kind_for_device(_device(), None)
        == DND_DARK_DISPLAY
    )
    assert controller.current_attention_projection is standing

    lifted = compose_dnd_contributions(())
    controller.dnd_controller.projection = lifted
    controller._dnd_projection_changed(lifted)

    assert controller.completion_sweep_until == 0.0
    assert controller.current_attention_projection is standing
    assert (
        controller.active_led_display_kind_for_device(_device(), None)
        == status_bar.LED_DISPLAY_AGENT
    )


def test_dnd_dim_and_dark_scale_both_ambient_and_signal_brightness(controller) -> None:
    device = _device(brightness=200)
    controller.dnd_controller = SimpleNamespace(projection=_projection(DndMode.DIM))

    assert controller.effective_brightness_for_device(device) == 61
    assert controller.effective_signal_brightness_for_device(device) == 50

    controller.dnd_controller = SimpleNamespace(projection=_projection(DndMode.DARK))

    assert controller.effective_brightness_for_device(device) == 0
    assert controller.effective_signal_brightness_for_device(device) == 0


class _DndActions:
    def __init__(self, projection) -> None:
        self.projection = projection
        self.overrides: list[DndOverride | None] = []

    def set_override(self, override: DndOverride | None):
        self.overrides.append(override)
        return SimpleNamespace(applied=True, projection=self.projection, failure=None)


def test_legacy_quiet_action_delegates_to_durable_mute_override(
    controller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=timezone.utc).timestamp()
    actions = _DndActions(compose_dnd_contributions(()))
    controller.dnd_controller = actions
    monkeypatch.setattr(status_bar.time, "time", lambda: now)
    controller.refresh_ = lambda _sender: None

    controller.toggleQuietHour_(None)

    override = actions.overrides[-1]
    assert type(override) is DndOverride
    assert override.mode is DndMode.MUTE
    assert override.created_epoch == now
    assert override.until_epoch == now + 3_600.0


def test_legacy_quiet_duration_action_uses_the_same_durable_mute_override(
    controller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_800_000_000.0
    actions = _DndActions(compose_dnd_contributions(()))
    controller.dnd_controller = actions
    monkeypatch.setattr(status_bar.time, "time", lambda: now)

    controller.startQuiet_(SimpleNamespace(representedObject=lambda: 7_200.0))

    override = actions.overrides[-1]
    assert type(override) is DndOverride
    assert override.mode is DndMode.MUTE
    assert override.until_epoch == now + 7_200.0


def test_exact_menu_mode_selectors_delegate_to_one_hour_override(
    controller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_800_000_000.0
    actions = _DndActions(compose_dnd_contributions(()))
    controller.dnd_controller = actions
    monkeypatch.setattr(status_bar.time, "time", lambda: now)
    controller.refresh_ = lambda _sender: None

    for selector, mode in (
        (controller.setDndMuteForHour_, DndMode.MUTE),
        (controller.setDndDimForHour_, DndMode.DIM),
        (controller.setDndPauseForHour_, DndMode.PAUSE),
        (controller.setDndAsksOnlyForHour_, DndMode.ASKS_ONLY),
        (controller.setDndDarkForHour_, DndMode.DARK),
    ):
        selector(None)
        override = actions.overrides[-1]
        assert type(override) is DndOverride
        assert override.mode is mode
        assert override.until_epoch == now + 3_600.0


def test_dnd_environment_selectors_refresh_the_existing_controller(controller) -> None:
    calls: list[str] = []
    preview_releases = []
    controller._effect_studio_physical_preview_adapter = SimpleNamespace(
        release=lambda reason: preview_releases.append(reason)
    )
    controller.dnd_controller = SimpleNamespace(
        projection=compose_dnd_contributions(()),
        handle_wake=lambda: calls.append("wake"),
        handle_sleep=lambda: calls.append("sleep"),
        handle_screen_wake=lambda: calls.append("screen_wake"),
        handle_screen_sleep=lambda: calls.append("screen_sleep"),
        handle_activation=lambda: calls.append("activation"),
        handle_clock_change=lambda: calls.append("clock"),
        handle_timezone_change=lambda: calls.append("timezone"),
    )

    controller.dndWorkspaceDidWake_(None)
    controller.dndWorkspaceWillSleep_(None)
    controller.dndScreensDidWake_(None)
    controller.dndScreensDidSleep_(None)
    controller.applicationDidBecomeActive_(None)
    controller.dndSystemClockDidChange_(None)
    controller.dndSystemTimeZoneDidChange_(None)

    assert calls == [
        "wake",
        "sleep",
        "screen_wake",
        "screen_sleep",
        "activation",
        "clock",
        "timezone",
    ]
    assert [reason.value for reason in preview_releases] == ["sleep", "sleep"]


def test_dark_display_entry_is_an_explicit_off_program(controller) -> None:
    factory, state, label = controller.signal_display_entries()[
        DND_DARK_DISPLAY
    ]

    assert factory(255, 8) == "off"
    assert state is status_bar.LedDisplayState.IDLE
    assert label(_device(), None) == "Test Device DND presentation held"


def test_fully_dark_physical_write_keeps_zero_after_resting_glow_boundary(
    controller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = _device()
    programs: list[str] = []

    def sync_program(program, state):
        programs.append(program)
        return status_bar.LedStatusWrite(
            state=state,
            target=device.target,
            program=program,
            changed=True,
        )

    physical = SimpleNamespace(brightness=255, sync_program=sync_program)
    monkeypatch.setattr(
        controller,
        "agent_controller_for_device",
        lambda _device: physical,
    )
    monkeypatch.setattr(
        controller,
        "reset_led_controllers_for_device",
        lambda _device_id: None,
    )
    request = status_bar.HardwareWriteRequest(
        device=device,
        mode=AgentMode.WORKING,
        battery_snapshot=None,
        statuses=(),
        projection=None,
        relay_elapsed_seconds=0.0,
        display_kind=DND_DARK_DISPLAY,
    )

    controller._sync_hardware_device(request)

    assert programs == ["brightness 0\noff"]


def test_dnd_changes_do_not_stop_canonical_power_or_ingestion(controller) -> None:
    controller.dnd_controller = SimpleNamespace(projection=_projection(DndMode.DARK))
    controller.current_attention_projection = _attention(LifecycleMode.ACTIVE)

    assert controller.display_aggregate_mode(controller.current_attention_projection) is AgentMode.WORKING
    assert controller.current_dnd_projection().mode is DndMode.DARK
