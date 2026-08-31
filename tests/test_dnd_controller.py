from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from sidepulse.dnd_controller import (
    DndChangeFailure,
    DndController,
)
from sidepulse.dnd_policy import (
    DisplayAdmission,
    DndMode,
    DndOverride,
    DndSchedule,
    DndSource,
    OutboundAdmission,
)
from sidepulse.focus_status import (
    FocusActivity,
    FocusAuthorization,
    FocusStatusObservation,
)
from sidepulse.settings import (
    AgentMonitorSettings,
    SettingsConcurrentWriteError,
    SettingsWriteRefusedError,
)

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 8, 30, 22, 30, tzinfo=UTC).timestamp()


class _Timer:
    def __init__(self, delay: float, callback) -> None:
        self.delay = delay
        self.callback = callback
        self.started = False
        self.cancelled = False
        self.fired = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        self.fired = True
        self.callback()


class _TimerFactory:
    def __init__(self) -> None:
        self.timers: list[_Timer] = []
        self.error: Exception | None = None

    def __call__(self, delay: float, callback) -> _Timer:
        if self.error is not None:
            raise self.error
        timer = _Timer(delay, callback)
        self.timers.append(timer)
        return timer


class _FocusClient:
    def __init__(
        self,
        observation: FocusStatusObservation | None = None,
    ) -> None:
        self.observation = observation or FocusStatusObservation(
            FocusAuthorization.NOT_DETERMINED,
            FocusActivity.UNAVAILABLE,
        )
        self.observe_count = 0
        self.request_count = 0
        self.completions = []
        self.request_started = True

    def observe(self) -> FocusStatusObservation:
        self.observe_count += 1
        return self.observation

    def request_authorization(self, completion) -> bool:
        self.request_count += 1
        self.completions.append(completion)
        return self.request_started


class _Harness:
    def __init__(
        self,
        settings: AgentMonitorSettings | None = None,
        *,
        focus: _FocusClient | None = None,
        named_reader=None,
        save_error: type[Exception] | None = None,
    ) -> None:
        self.settings = settings or AgentMonitorSettings()
        self.focus = focus or _FocusClient()
        self.timer_factory = _TimerFactory()
        self.recovery_timer_factory = _TimerFactory()
        self.now = NOW
        self.zone = UTC
        self.projections = []
        self.saved = []
        self.save_error = save_error

        def save(candidate) -> None:
            self.saved.append(candidate)
            if self.save_error is not None:
                raise self.save_error("injected Settings refusal")

        self.controller = DndController(
            settings_getter=lambda: self.settings,
            settings_setter=lambda candidate: setattr(self, "settings", candidate),
            settings_saver=save,
            focus_client=self.focus,
            named_focus_reader=named_reader,
            wall_clock=lambda: self.now,
            timezone_getter=lambda _now: self.zone,
            timer_factory=self.timer_factory,
            recovery_timer_factory=self.recovery_timer_factory,
            on_projection=self.projections.append,
        )


def _scheduled_settings() -> AgentMonitorSettings:
    return AgentMonitorSettings().with_dnd_schedule(
        enabled=True,
        start_minutes=22 * 60,
        end_minutes=23 * 60,
        mode=DndMode.DARK,
    )


def _active_focus() -> FocusStatusObservation:
    return FocusStatusObservation(
        FocusAuthorization.AUTHORIZED,
        FocusActivity.ACTIVE,
    )


def test_controller_values_are_immutable_and_initially_inactive() -> None:
    harness = _Harness()

    assert harness.controller.projection.summary == "DND: Off"
    assert harness.controller.transition_deadline is None
    assert not harness.controller.started
    with pytest.raises(FrozenInstanceError):
        harness.controller.projection.summary = "changed"  # type: ignore[misc]


def test_start_is_idempotent_and_owns_exactly_one_transition_timer() -> None:
    harness = _Harness(_scheduled_settings())

    first = harness.controller.start()
    second = harness.controller.start()

    assert first.applied and second.applied
    assert harness.controller.started
    assert harness.controller.projection.source is DndSource.SCHEDULE
    assert harness.controller.projection.mode is DndMode.DARK
    assert harness.controller.transition_deadline == datetime(
        2026, 8, 30, 23, 0, tzinfo=UTC
    ).timestamp()
    assert len(harness.timer_factory.timers) == 1
    timer = harness.timer_factory.timers[0]
    assert timer.started and not timer.cancelled
    assert timer.delay == pytest.approx(30 * 60)
    assert harness.focus.observe_count == 1


def test_transition_timer_recomputes_from_current_wall_truth() -> None:
    harness = _Harness(_scheduled_settings())
    harness.controller.start()
    timer = harness.timer_factory.timers[-1]
    harness.now = datetime(2026, 8, 30, 23, 0, tzinfo=UTC).timestamp()

    timer.fire()

    assert harness.controller.projection.summary == "DND: Off"
    assert harness.controller.transition_deadline == datetime(
        2026, 8, 31, 22, 0, tzinfo=UTC
    ).timestamp()
    assert timer.cancelled
    assert len(harness.timer_factory.timers) == 2


def test_replaced_timer_callback_is_generation_fenced() -> None:
    harness = _Harness(_scheduled_settings())
    harness.controller.start()
    stale_timer = harness.timer_factory.timers[-1]
    harness.controller.handle_environment_refresh()
    current_timer = harness.timer_factory.timers[-1]
    before = harness.controller.projection

    stale_timer.fire()

    assert stale_timer.cancelled
    assert not current_timer.cancelled
    assert harness.controller.projection is before
    assert harness.timer_factory.timers[-1] is current_timer


@pytest.mark.parametrize(
    "entrypoint",
    [
        "handle_wake",
        "handle_sleep",
        "handle_screen_wake",
        "handle_screen_sleep",
        "handle_activation",
        "handle_clock_change",
        "handle_timezone_change",
        "handle_environment_refresh",
    ],
)
def test_environment_entrypoints_reobserve_and_rearm(entrypoint: str) -> None:
    harness = _Harness(_scheduled_settings())
    harness.controller.start()
    previous_timer = harness.timer_factory.timers[-1]
    previous_observations = harness.focus.observe_count

    result = getattr(harness.controller, entrypoint)()

    assert result.applied
    assert previous_timer.cancelled
    assert harness.focus.observe_count == previous_observations + 1
    assert len(harness.timer_factory.timers) == 2


def test_clock_and_timezone_entrypoints_recompute_the_real_boundary() -> None:
    settings = AgentMonitorSettings().with_dnd_schedule(
        enabled=True,
        start_minutes=22 * 60,
        end_minutes=7 * 60,
        mode=DndMode.DIM,
    )
    harness = _Harness(settings)
    harness.controller.start()
    assert harness.controller.projection.source is DndSource.SCHEDULE

    harness.zone = ZoneInfo("America/Los_Angeles")
    harness.controller.handle_timezone_change()

    assert harness.controller.projection.summary == "DND: Off"
    assert harness.controller.transition_deadline == datetime(
        2026, 8, 30, 22, 0, tzinfo=harness.zone
    ).timestamp()


def test_close_invalidates_timer_and_all_late_timer_work() -> None:
    harness = _Harness(_scheduled_settings())
    harness.controller.start()
    timer = harness.timer_factory.timers[-1]
    before = harness.controller.projection

    harness.controller.close()
    harness.controller.close()
    timer.fire()

    assert harness.controller.closed
    assert timer.cancelled
    assert harness.controller.projection is before
    assert harness.focus.observe_count == 1
    assert not harness.controller.start().applied
    assert harness.controller.start().failure is DndChangeFailure.CLOSED


def test_failed_initial_start_rolls_back_started_generation_and_projection() -> None:
    harness = _Harness(_scheduled_settings())
    harness.zone = object()

    with pytest.raises(ValueError, match="timezone"):
        harness.controller.start()

    assert not harness.controller.started
    assert harness.controller.generation == 0
    assert harness.controller.projection.summary == "DND: Off"
    assert harness.controller.transition_deadline is None
    assert harness.timer_factory.timers == []


def test_failed_refresh_keeps_the_existing_generation_projection_and_timer() -> None:
    harness = _Harness(_scheduled_settings())
    harness.controller.start()
    before_generation = harness.controller.generation
    before_projection = harness.controller.projection
    before_deadline = harness.controller.transition_deadline
    before_timer = harness.timer_factory.timers[-1]
    harness.zone = object()

    with pytest.raises(ValueError, match="timezone"):
        harness.controller.handle_timezone_change()

    assert harness.controller.started
    assert harness.controller.generation == before_generation
    assert harness.controller.projection is before_projection
    assert harness.controller.transition_deadline == before_deadline
    assert harness.timer_factory.timers == [before_timer]
    assert not before_timer.cancelled


def test_failed_timer_construction_keeps_the_existing_committed_state() -> None:
    harness = _Harness(_scheduled_settings())
    harness.controller.start()
    before_generation = harness.controller.generation
    before_projection = harness.controller.projection
    before_deadline = harness.controller.transition_deadline
    before_timer = harness.timer_factory.timers[-1]
    harness.timer_factory.error = RuntimeError("injected timer construction failure")

    with pytest.raises(RuntimeError, match="timer construction"):
        harness.controller.handle_clock_change()

    assert harness.controller.generation == before_generation
    assert harness.controller.projection is before_projection
    assert harness.controller.transition_deadline == before_deadline
    assert harness.timer_factory.timers == [before_timer]
    assert not before_timer.cancelled


def test_public_focus_truth_uses_the_configured_mode_only_when_follow_is_on() -> None:
    settings = (
        AgentMonitorSettings()
        .with_focus_sync_enabled(True)
        .with_dnd_focus_mode(DndMode.MUTE)
    )
    harness = _Harness(settings, focus=_FocusClient(_active_focus()))

    harness.controller.start()

    assert harness.controller.focus_observation == _active_focus()
    assert harness.controller.projection.source is DndSource.MACOS_FOCUS
    assert harness.controller.projection.mode is DndMode.MUTE


def test_follow_focus_off_observes_public_status_but_does_not_read_private_detail() -> None:
    named_reads: list[str] = []
    harness = _Harness(
        AgentMonitorSettings(),
        focus=_FocusClient(_active_focus()),
        named_reader=lambda: named_reads.append("read") or ("work",),
    )

    harness.controller.start()

    assert harness.controller.focus_observation.activity is FocusActivity.ACTIVE
    assert harness.controller.projection.summary == "DND: Off"
    assert named_reads == []


def test_named_focus_detail_tightens_but_cannot_replace_public_active_truth() -> None:
    settings = (
        AgentMonitorSettings()
        .with_focus_sync_enabled(True)
        .with_dnd_focus_mode(DndMode.DIM)
        .with_focus_dim_rule("work", 0.4)
        .with_focus_signal_policy("work", "asks_only")
        .with_focus_dim_rule("sleep", None)
        .with_focus_signal_policy("sleep", "silent")
    )
    harness = _Harness(
        settings,
        focus=_FocusClient(_active_focus()),
        named_reader=lambda: ("work", "sleep"),
    )

    harness.controller.start()

    projection = harness.controller.projection
    assert projection.active_sources == (
        DndSource.MACOS_FOCUS,
        DndSource.NAMED_FOCUS,
    )
    assert projection.brightness_factor == pytest.approx(0.05)
    assert projection.display_admission is DisplayAdmission.ASKS
    assert projection.outbound_admission is OutboundAdmission.ASKS
    assert not projection.audible_allowed
    assert harness.controller.named_focus_identifiers == ("sleep", "work")


@pytest.mark.parametrize(
    "named_reader",
    [lambda: (), lambda: (_ for _ in ()).throw(PermissionError("no detail"))],
)
def test_missing_named_detail_preserves_public_active_focus(named_reader) -> None:
    settings = (
        AgentMonitorSettings()
        .with_focus_sync_enabled(True)
        .with_dnd_focus_mode(DndMode.PAUSE)
    )
    harness = _Harness(
        settings,
        focus=_FocusClient(_active_focus()),
        named_reader=named_reader,
    )

    harness.controller.start()

    assert harness.controller.projection.source is DndSource.MACOS_FOCUS
    assert harness.controller.projection.mode is DndMode.PAUSE
    assert harness.controller.named_focus_identifiers == ()


def test_named_detail_never_activates_dnd_when_public_focus_is_inactive() -> None:
    settings = AgentMonitorSettings().with_focus_sync_enabled(True)
    inactive = FocusStatusObservation(
        FocusAuthorization.AUTHORIZED,
        FocusActivity.INACTIVE,
    )
    named_reads: list[str] = []
    harness = _Harness(
        settings,
        focus=_FocusClient(inactive),
        named_reader=lambda: named_reads.append("read") or ("sleep",),
    )

    harness.controller.start()

    assert harness.controller.projection.summary == "DND: Off"
    assert named_reads == []


@pytest.mark.parametrize(
    ("method", "argument", "expected"),
    [
        (
            "set_schedule",
            DndSchedule(True, 60, 120, DndMode.ASKS_ONLY),
            lambda settings: settings.dnd_schedule_mode == "asks_only",
        ),
        (
            "set_dim_fraction",
            0.25,
            lambda settings: settings.dnd_dim_fraction == 0.25,
        ),
        (
            "set_follow_focus",
            True,
            lambda settings: settings.focus_sync_enabled,
        ),
        (
            "set_focus_mode",
            DndMode.MUTE,
            lambda settings: settings.dnd_focus_mode == "mute",
        ),
        (
            "set_override",
            DndOverride.for_mode(
                DndMode.DARK,
                created_epoch=NOW,
                until_epoch=NOW + 600,
            ),
            lambda settings: settings.dnd_override_mode == "dark",
        ),
    ],
)
def test_settings_edits_save_then_adopt_one_coherent_candidate(
    method: str,
    argument: object,
    expected,
) -> None:
    harness = _Harness()
    harness.controller.start()

    result = getattr(harness.controller, method)(argument)

    assert result.applied
    assert len(harness.saved) == 1
    assert harness.saved[0] is harness.settings
    assert expected(harness.settings)
    assert harness.controller.projection is result.projection


def test_clear_override_is_a_durable_transaction() -> None:
    override = DndOverride.for_mode(
        DndMode.MUTE,
        created_epoch=NOW,
        until_epoch=NOW + 600,
    )
    harness = _Harness(AgentMonitorSettings().with_dnd_override(override))
    harness.controller.start()
    assert harness.controller.projection.source is DndSource.MANUAL

    result = harness.controller.set_override(None)

    assert result.applied
    assert harness.settings.dnd_override_mode is None
    assert harness.controller.projection.summary == "DND: Off"


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (SettingsConcurrentWriteError, DndChangeFailure.CONCURRENT_WRITE),
        (SettingsWriteRefusedError, DndChangeFailure.WRITE_REFUSED),
    ],
)
def test_save_refusal_preserves_live_settings_projection_and_timer(
    error: type[Exception],
    failure: DndChangeFailure,
) -> None:
    harness = _Harness(_scheduled_settings(), save_error=error)
    harness.controller.start()
    before_settings = harness.settings
    before_projection = harness.controller.projection
    before_timer = harness.timer_factory.timers[-1]
    before_deadline = harness.controller.transition_deadline

    result = harness.controller.set_dim_fraction(0.25)

    assert not result.applied
    assert result.failure is failure
    assert harness.settings is before_settings
    assert harness.controller.projection is before_projection
    assert harness.controller.transition_deadline == before_deadline
    assert harness.timer_factory.timers == [before_timer]
    assert not before_timer.cancelled


def test_timer_firing_inside_a_refused_save_is_rearmed_from_durable_truth() -> None:
    harness = _Harness(_scheduled_settings())
    harness.controller.start()
    before_settings = harness.settings
    before_projection = harness.controller.projection
    before_deadline = harness.controller.transition_deadline
    fired_timer = harness.timer_factory.timers[-1]

    def save(_candidate) -> None:
        fired_timer.fire()
        raise SettingsConcurrentWriteError("injected concurrent write")

    harness.controller._settings_saver = save

    result = harness.controller.set_dim_fraction(0.25)

    assert not result.applied
    assert result.failure is DndChangeFailure.CONCURRENT_WRITE
    assert harness.settings is before_settings
    assert harness.controller.projection == before_projection
    assert harness.controller.transition_deadline == before_deadline
    assert fired_timer.cancelled
    assert len(harness.timer_factory.timers) == 2
    replacement = harness.timer_factory.timers[-1]
    assert replacement.started and not replacement.cancelled


def test_refused_save_survives_invalid_timezone_during_deferred_refresh() -> None:
    harness = _Harness(_scheduled_settings())
    harness.controller.start()
    fired_timer = harness.timer_factory.timers[-1]
    before_settings = harness.settings

    def save(_candidate) -> None:
        fired_timer.fire()
        harness.zone = object()
        raise SettingsConcurrentWriteError("injected concurrent write")

    harness.controller._settings_saver = save

    result = harness.controller.set_dim_fraction(0.25)

    assert not result.applied
    assert result.failure is DndChangeFailure.CONCURRENT_WRITE
    assert harness.settings is before_settings
    assert fired_timer.fired and fired_timer.cancelled
    assert harness.controller.transition_deadline is not None
    assert harness.controller.projection.next_transition_epoch == (
        harness.controller.transition_deadline
    )
    assert len(harness.recovery_timer_factory.timers) == 1
    replacement = harness.recovery_timer_factory.timers[0]
    assert replacement.started and not replacement.fired and not replacement.cancelled


def test_refused_save_survives_primary_timer_failure_during_deferred_refresh() -> None:
    harness = _Harness(_scheduled_settings())
    harness.controller.start()
    fired_timer = harness.timer_factory.timers[-1]
    before_settings = harness.settings

    def save(_candidate) -> None:
        fired_timer.fire()
        harness.timer_factory.error = RuntimeError("injected timer construction failure")
        raise SettingsWriteRefusedError("injected write refusal")

    harness.controller._settings_saver = save

    result = harness.controller.set_dim_fraction(0.25)

    assert not result.applied
    assert result.failure is DndChangeFailure.WRITE_REFUSED
    assert harness.settings is before_settings
    assert fired_timer.fired and fired_timer.cancelled
    assert harness.controller.transition_deadline is not None
    assert harness.controller.projection.next_transition_epoch == (
        harness.controller.transition_deadline
    )
    assert len(harness.recovery_timer_factory.timers) == 1
    replacement = harness.recovery_timer_factory.timers[0]
    assert replacement.started and not replacement.fired and not replacement.cancelled


def test_refused_save_degrades_coherently_if_both_timer_factories_fail() -> None:
    harness = _Harness(_scheduled_settings())
    harness.controller.start()
    fired_timer = harness.timer_factory.timers[-1]
    before_settings = harness.settings

    def save(_candidate) -> None:
        fired_timer.fire()
        harness.timer_factory.error = RuntimeError("primary timer unavailable")
        harness.recovery_timer_factory.error = RuntimeError(
            "recovery timer unavailable"
        )
        raise SettingsConcurrentWriteError("injected concurrent write")

    harness.controller._settings_saver = save

    result = harness.controller.set_dim_fraction(0.25)

    assert not result.applied
    assert result.failure is DndChangeFailure.CONCURRENT_WRITE
    assert harness.settings is before_settings
    assert fired_timer.fired and fired_timer.cancelled
    assert harness.controller.transition_deadline is None
    assert harness.controller.projection.next_transition_epoch is None
    assert harness.recovery_timer_factory.timers == []


def test_save_reentrancy_is_refused_without_displacing_the_outer_transaction() -> None:
    harness = _Harness()
    inner_results = []

    def save(candidate) -> None:
        harness.saved.append(candidate)
        inner_results.append(harness.controller.set_focus_mode(DndMode.MUTE))

    harness.controller._settings_saver = save
    harness.controller.start()

    result = harness.controller.set_dim_fraction(0.25)

    assert result.applied
    assert harness.settings.dnd_dim_fraction == 0.25
    assert harness.settings.dnd_focus_mode == "pause"
    assert inner_results[0].failure is DndChangeFailure.SAVE_IN_PROGRESS


def test_stale_focus_result_cannot_replace_a_newer_projection() -> None:
    settings = AgentMonitorSettings().with_focus_sync_enabled(True)
    harness = _Harness(settings)
    harness.controller.start()
    stale_generation = harness.controller.generation
    harness.controller.handle_environment_refresh()
    current = harness.controller.projection

    applied = harness.controller._accept_focus_observation(
        stale_generation,
        _active_focus(),
        ("sleep",),
    )

    assert not applied
    assert harness.controller.projection is current


def test_authorization_is_requested_only_by_the_explicit_method() -> None:
    focus = _FocusClient()
    harness = _Harness(focus=focus)
    harness.controller.start()

    assert focus.request_count == 0
    assert harness.controller.request_focus_authorization()
    assert focus.request_count == 1


def test_current_authorization_callback_refreshes_public_truth() -> None:
    focus = _FocusClient()
    harness = _Harness(focus=focus)
    harness.controller.start()
    harness.controller.request_focus_authorization()
    focus.observation = _active_focus()

    focus.completions[-1](FocusAuthorization.AUTHORIZED)

    assert harness.controller.focus_observation == _active_focus()
    assert focus.observe_count == 2


def test_late_or_superseded_authorization_callbacks_are_fenced() -> None:
    focus = _FocusClient()
    harness = _Harness(focus=focus)
    harness.controller.start()
    harness.controller.request_focus_authorization()
    old_completion = focus.completions[-1]
    harness.controller.request_focus_authorization()

    old_completion(FocusAuthorization.AUTHORIZED)
    assert harness.focus.observe_count == 1

    harness.controller.close()
    focus.completions[-1](FocusAuthorization.AUTHORIZED)
    assert harness.focus.observe_count == 1


def test_invalid_inputs_are_refused_before_save() -> None:
    harness = _Harness()
    harness.controller.start()

    with pytest.raises(ValueError):
        harness.controller.set_schedule("night")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        harness.controller.set_focus_mode("pause")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        harness.controller.set_override("mute")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        harness.controller.set_follow_focus(1)  # type: ignore[arg-type]
    assert harness.saved == []
