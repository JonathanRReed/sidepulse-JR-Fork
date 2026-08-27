from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from sidepulse.presentation_policy import MotionClass
from sidepulse.presentation_scheduler import plan_presentation_schedule
from sidepulse.render_policy import (
    RenderDriverKind,
    RenderEnvironment,
    alcove_bracket_corner_radius,
    choose_render_schedule,
)


@pytest.fixture(autouse=True)
def _real_window_presentation(monkeypatch):
    """These tests VERIFY presentation behavior against mock windows --
    nothing real is shown, so the desktop-takeover gate opens for them.
    (The gate exists because the suite once yanked the owner's focus
    for minutes; see tests/test_window_presentation.py.)"""
    monkeypatch.setattr(
        "sidepulse.window_presentation.desktop_takeover_suppressed",
        lambda: False,
    )


def test_alcove_idle_pulse_keeps_a_visible_floor_between_breaths() -> None:
    from sidepulse.virtual_device import VirtualLedView

    view = VirtualLedView.alloc().initWithFrame_(((0, 0), (213.0, 37.0)))
    view.setMinGlow_(0.1)
    view.current_program = "#5A787D 6000ms pulse\nrepeat"

    rendered = view._bracket_colors([(0.0, 0.0, 0.0, 0.0)] * 8)

    assert all(color[3] > 0.0 for color in rendered)


def test_explicit_off_program_remains_invisible_with_a_minimum_glow() -> None:
    from sidepulse.virtual_device import VirtualLedView

    view = VirtualLedView.alloc().initWithFrame_(((0, 0), (213.0, 37.0)))
    view.setMinGlow_(0.1)
    view.current_program = "off"

    assert view._bracket_colors([(0.0, 0.0, 0.0, 0.0)] * 8) == [
        (0.0, 0.0, 0.0, 0.0)
    ] * 8


class _DisplayLink:
    def __init__(
        self,
        *,
        fail_registration: bool = False,
        target_timestamps: tuple[float, ...] = (10.0,),
        supports_target_timestamp: bool = True,
        supports_frame_range: bool = True,
        fail_frame_range: bool = False,
    ) -> None:
        self.fail_registration = fail_registration
        self.fail_frame_range = fail_frame_range
        self.invalidated = 0
        self.run_loop_modes: list[tuple[object, object]] = []
        self.frame_ranges: list[object] = []
        self.target_timestamp_calls = 0
        self._target_timestamps = iter(target_timestamps)
        if not supports_target_timestamp:
            self.targetTimestamp = None
        if not supports_frame_range:
            self.setPreferredFrameRateRange_ = None

    def targetTimestamp(self) -> float:
        self.target_timestamp_calls += 1
        return next(self._target_timestamps)

    def setPreferredFrameRateRange_(self, frame_range: object) -> None:
        if self.fail_frame_range:
            raise RuntimeError("frame range rejected")
        self.frame_ranges.append(frame_range)

    def addToRunLoop_forMode_(self, run_loop: object, mode: object) -> None:
        if self.fail_registration:
            raise RuntimeError("run loop rejected display link")
        self.run_loop_modes.append((run_loop, mode))

    def invalidate(self) -> None:
        self.invalidated += 1


class _Timer:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.invalidated = 0

    def invalidate(self) -> None:
        self.invalidated += 1


class _TimerFactory:
    def __init__(self) -> None:
        self.created: list[_Timer] = []

    def scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        self, interval, _target, _selector, _user_info, _repeats
    ) -> _Timer:
        timer = _Timer(interval)
        self.created.append(timer)
        return timer


class _RunLoop:
    def __init__(self, *, fail_timer_registration: bool = False) -> None:
        self.fail_timer_registration = fail_timer_registration
        self.timers: list[tuple[_Timer, object]] = []

    def addTimer_forMode_(self, timer: _Timer, mode: object) -> None:
        if self.fail_timer_registration:
            raise RuntimeError("run loop rejected timer")
        self.timers.append((timer, mode))


class _RunLoopClass:
    def __init__(self, run_loop: _RunLoop) -> None:
        self.run_loop = run_loop

    def currentRunLoop(self) -> _RunLoop:
        return self.run_loop


class _DisplayLinkView:
    def __init__(
        self,
        *,
        fail_construction: bool = False,
        fail_registration: bool = False,
        supports_target_timestamp: bool = True,
        supports_frame_range: bool = True,
        fail_frame_range: bool = False,
        target_timestamps: tuple[float, ...] = (10.0,),
    ) -> None:
        self.fail_construction = fail_construction
        self.fail_registration = fail_registration
        self.supports_target_timestamp = supports_target_timestamp
        self.supports_frame_range = supports_frame_range
        self.fail_frame_range = fail_frame_range
        self.target_timestamps = target_timestamps
        self.links: list[_DisplayLink] = []
        self.sample_fps: list[float] = []
        self.paint_requests: list[bool] = []
        self.presentation_colors: list[tuple[tuple[float, float, float, float], ...]] = []
        self.current_program = "old"

    def displayLinkWithTarget_selector_(self, _target, _selector: str) -> _DisplayLink:
        if self.fail_construction:
            raise RuntimeError("display link unavailable")
        link = _DisplayLink(
            fail_registration=self.fail_registration,
            target_timestamps=self.target_timestamps,
            supports_target_timestamp=self.supports_target_timestamp,
            supports_frame_range=self.supports_frame_range,
            fail_frame_range=self.fail_frame_range,
        )
        self.links.append(link)
        return link

    def setRenderFps_(self, fps: float) -> None:
        self.sample_fps.append(fps)

    def _colors_for_draw(self):
        return [(0.2, 0.4, 0.6, 1.0)] * 8

    def setNeedsDisplay_(self, needs_display: bool) -> None:
        self.paint_requests.append(needs_display)

    def setPresentationColors_(self, colors) -> None:
        self.presentation_colors.append(tuple(tuple(color) for color in colors))

    def setProgram_startedAt_(self, program: str, _started_at) -> None:
        self.current_program = program

    def setPresentationProgram_startedAt_(self, program: str, _started_at) -> None:
        self.current_program = program


class _TimerOnlyView:
    def __init__(self) -> None:
        self.sample_fps: list[float] = []

    def setRenderFps_(self, fps: float) -> None:
        self.sample_fps.append(fps)


class _Window:
    def __init__(self) -> None:
        self.visible = True
        self.ordered_out = 0
        self.ordered_front = 0

    def isVisible(self) -> bool:
        return self.visible

    def orderOut_(self, _sender) -> None:
        self.visible = False
        self.ordered_out += 1

    def orderFrontRegardless(self) -> None:
        self.visible = True
        self.ordered_front += 1


class _Screen:
    def __init__(self, maximum_fps: int) -> None:
        self.maximum_fps = maximum_fps

    def maximumFramesPerSecond(self) -> int:
        return self.maximum_fps


class _ScreenClass:
    def __init__(self, screen: _Screen) -> None:
        self.screen = screen

    def mainScreen(self) -> _Screen:
        return self.screen


def _active_device(
    monkeypatch,
    *,
    view: _DisplayLinkView | _TimerOnlyView | None = None,
    fail_registration: bool = False,
    fail_timer_registration: bool = False,
    maximum_fps: int = 60,
):
    """Create one visible device with AppKit's driver boundary replaced by doubles."""
    from sidepulse import virtual_device

    run_loop = _RunLoop(fail_timer_registration=fail_timer_registration)
    timer_factory = _TimerFactory()
    monkeypatch.setattr(virtual_device, "NSRunLoop", _RunLoopClass(run_loop))
    monkeypatch.setattr(virtual_device, "NSTimer", timer_factory, raising=False)
    monkeypatch.setattr(virtual_device, "NSScreen", _ScreenClass(_Screen(maximum_fps)))
    monkeypatch.setattr(
        virtual_device,
        "Quartz",
        SimpleNamespace(
            CAFrameRateRangeMake=lambda minimum, maximum, preferred: (
                minimum,
                maximum,
                preferred,
            )
        ),
        raising=False,
    )
    device = virtual_device.VirtualStatusDevice.alloc().init()
    device.window = _Window()
    device.view = view or _DisplayLinkView(fail_registration=fail_registration)
    if isinstance(device.view, _DisplayLinkView):
        device.view.fail_registration = fail_registration
    device._runtime_environment = lambda **_kwargs: RenderEnvironment()
    device._last_reposition_at = float("inf")
    return device, device.view, run_loop, timer_factory, virtual_device


class _PresentationInputRecorder:
    def __init__(self) -> None:
        self.inputs: list[object] = []

    def __call__(self, inputs: object) -> None:
        self.inputs.append(inputs)


def test_screen_bar_production_lifecycle_publishes_no_inactive_work(
    monkeypatch,
) -> None:
    """Catches hidden, disabled, sleeping, or terminating device work escaping the plan."""
    for lifecycle in ("hide", "disable", "sleep", "terminate"):
        device, _view, _run_loop, timers, _virtual_device = _active_device(
            monkeypatch,
            view=_TimerOnlyView(),
        )
        recorder = _PresentationInputRecorder()
        device.set_presentation_schedule_reconciler(recorder)
        device._sampler_command = SimpleNamespace(
            motion=MotionClass.CONTINUOUS,
            next_visual_change_at=None,
        )
        device._animation_active = True
        device._frame_fallback_relevant = True
        device._alcove_relevant = True
        device.set_pointer_interaction_relevant(True)
        sampler_creations: list[object] = []
        alcove_creations: list[object] = []
        device._sampler_factory = lambda _buffer: sampler_creations.append(
            object()
        )
        device._alcove_request = object()
        device._alcove_observer_factory = lambda _buffer: alcove_creations.append(
            object()
        )

        if lifecycle == "hide":
            device.hide()
        elif lifecycle == "disable":
            device.set_enabled(False)
        elif lifecycle == "sleep":
            device.screenDidSleep_(None)
        else:
            device.terminate()

        device._resume_sampler()
        device._resume_alcove_observer()

        final_inputs = recorder.inputs[-1]
        final_plan = plan_presentation_schedule(final_inputs, now=time.monotonic())
        assert final_plan.intents == ()
        assert final_plan.reconcile_immediately is False
        assert timers.created == []
        assert sampler_creations == []
        assert alcove_creations == []


def test_screen_bar_resolves_native_and_fallback_drivers_before_planning(
    monkeypatch,
) -> None:
    """Catches a native display link and registry fallback running together."""
    native, _view, _run_loop, native_timers, _virtual_device = _active_device(monkeypatch)
    native_recorder = _PresentationInputRecorder()
    native.set_presentation_schedule_reconciler(native_recorder)

    native._refresh_render_cadence(True, force=True)

    assert native.display_link is not None
    assert native_recorder.inputs[-1].animation_active is False
    assert native_timers.created == []

    fallback, _view, _run_loop, fallback_timers, _virtual_device = _active_device(
        monkeypatch,
        view=_TimerOnlyView(),
    )
    fallback_recorder = _PresentationInputRecorder()
    fallback.set_presentation_schedule_reconciler(fallback_recorder)

    fallback._refresh_render_cadence(True, force=True)

    assert fallback.display_link is None
    assert fallback.timer is None
    assert fallback_recorder.inputs[-1].animation_active is True
    assert fallback_timers.created == []


def test_screen_bar_static_and_finite_programs_publish_only_exact_deadline_work(
    monkeypatch,
) -> None:
    """Catches static output retaining a watcher or finite output creating a local timer."""
    clock = [500.0]
    device, _view, _run_loop, timers, virtual_device = _active_device(
        monkeypatch,
        view=_TimerOnlyView(),
    )
    monkeypatch.setattr(virtual_device.time, "monotonic", lambda: clock[0])
    device.reposition = lambda: None
    device._install_power_observers = lambda: None
    device._sampler = _Sampler()
    recorder = _PresentationInputRecorder()
    device.set_presentation_schedule_reconciler(recorder)

    device.set_program("steady", motion=MotionClass.STATIC)

    static_plan = plan_presentation_schedule(recorder.inputs[-1], now=clock[0])
    assert static_plan.intents == ()
    assert device.timer is None
    assert timers.created == []

    deadline = clock[0] + 0.375
    device.set_program(
        "finite",
        motion=MotionClass.FINITE,
        static_fallback_program="steady",
        next_visual_change_at=deadline,
    )

    finite_plan = plan_presentation_schedule(recorder.inputs[-1], now=clock[0])
    assert len(finite_plan.intents) == 2
    assert finite_plan.intents[-1].fire_at == deadline
    assert finite_plan.intents[-1].interval is None
    assert finite_plan.intents[-1].tolerance == 0.0
    assert device.timer is None
    assert timers.created == []


def test_screen_bar_fifty_lifecycle_cycles_keep_one_worker_and_no_local_timer(
    monkeypatch,
) -> None:
    """Catches lifecycle churn leaking sampler workers or rebuilding local timers."""

    class LifecycleSampler(_Sampler):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        def close(self, *, timeout_seconds: float) -> bool:
            self.closed = True
            return super().close(timeout_seconds=timeout_seconds)

    device, _view, _run_loop, timers, _virtual_device = _active_device(
        monkeypatch,
        view=_TimerOnlyView(),
    )
    device.reposition = lambda: None
    device._install_power_observers = lambda: None
    device._remove_power_observers = lambda: None
    device._sampler_command = SimpleNamespace(generation=0)
    created: list[LifecycleSampler] = []
    device._sampler_factory = lambda _buffer: created.append(
        LifecycleSampler()
    ) or created[-1]
    recorder = _PresentationInputRecorder()
    device.set_presentation_schedule_reconciler(recorder)
    max_live = 0

    for _ in range(50):
        device.show()
        max_live = max(max_live, sum(not sampler.closed for sampler in created))
        device.screenDidChange_(None)
        max_live = max(max_live, sum(not sampler.closed for sampler in created))
        device.screenDidSleep_(None)
        assert sum(not sampler.closed for sampler in created) == 0
        device.screenDidWake_(None)
        max_live = max(max_live, sum(not sampler.closed for sampler in created))
        device.hide()
        assert sum(not sampler.closed for sampler in created) == 0

    device.terminate()
    final = plan_presentation_schedule(recorder.inputs[-1], now=time.monotonic())

    assert max_live == 1
    assert all(len(sampler.close_timeouts) == 1 for sampler in created)
    assert final.intents == ()
    assert timers.created == []


def test_screen_bar_fallback_never_creates_a_second_local_timer(monkeypatch) -> None:
    """Catches the device bypassing the shared registry with a local fallback timer."""
    device, _view, _run_loop, timers, _virtual_device = _active_device(
        monkeypatch,
        view=_TimerOnlyView(),
        fail_timer_registration=True,
    )
    error = None
    try:
        device._refresh_render_cadence(True, force=True)
    except Exception as exc:
        error = exc

    assert error is None
    assert timers.created == []
    assert device.timer is None
    assert device.display_link is None
    assert device.presentation_scheduler_inputs().animation_active is True


def test_screen_bar_partial_workspace_observer_install_rolls_back(monkeypatch) -> None:
    """Catches a wake-registration failure leaking the already registered sleep observer."""
    from sidepulse import virtual_device

    class Center:
        def __init__(self) -> None:
            self.registered: list[object] = []
            self.removed: list[object] = []

        def addObserver_selector_name_object_(self, _observer, _selector, name, _object) -> None:
            if len(self.registered) == 1:
                raise RuntimeError("wake observer rejected")
            self.registered.append(name)

        def removeObserver_name_object_(self, _observer, name, _object) -> None:
            self.removed.append(name)
            self.registered.remove(name)

    center = Center()

    class Workspace:
        @staticmethod
        def sharedWorkspace():
            return type("SharedWorkspace", (), {"notificationCenter": lambda self: center})()

    device = virtual_device.VirtualStatusDevice.alloc().init()
    monkeypatch.setattr(virtual_device, "NSWorkspace", Workspace)

    device._install_power_observers()

    assert center.registered == []
    assert center.removed == [virtual_device.NSWorkspaceScreensDidSleepNotification]
    assert not device._power_observers_installed


def test_screen_bar_failed_observer_rollback_remains_owned_for_teardown_retry(monkeypatch) -> None:
    """Catches a failed rollback being forgotten, duplicated, or skipped by later teardown."""
    from sidepulse import virtual_device

    class Center:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.registered: list[object] = []
            self.removal_attempts: list[object] = []
            self.allow_removal = False

        def addObserver_selector_name_object_(self, _observer, _selector, name, _object) -> None:
            self.added.append(name)
            if name == virtual_device.NSWorkspaceScreensDidWakeNotification:
                raise RuntimeError("wake observer rejected")
            self.registered.append(name)

        def removeObserver_name_object_(self, _observer, name, _object) -> None:
            self.removal_attempts.append(name)
            if not self.allow_removal:
                raise RuntimeError("rollback removal rejected")
            self.registered.remove(name)

    center = Center()

    class Workspace:
        @staticmethod
        def sharedWorkspace():
            return type("SharedWorkspace", (), {"notificationCenter": lambda self: center})()

    device = virtual_device.VirtualStatusDevice.alloc().init()
    monkeypatch.setattr(virtual_device, "NSWorkspace", Workspace)

    device._install_power_observers()
    device._install_power_observers()
    center.allow_removal = True
    device.hide()

    sleep = virtual_device.NSWorkspaceScreensDidSleepNotification
    wake = virtual_device.NSWorkspaceScreensDidWakeNotification
    assert center.added == [sleep, wake, wake]
    assert center.removal_attempts == [sleep, sleep, sleep]
    assert center.registered == []
    assert not device._power_observers_installed


@pytest.mark.parametrize(
    ("maximum_fps", "negotiated", "cadence"),
    # The rate asked for is one the panel can produce, so it is also the rate
    # assumed afterwards. 144 used to ask for 120: there is no 144/n equal to
    # 120, so the link settled at 72 and every number downstream was a fiction.
    # 144/3 = 48 is a real scan rate and clears the 60 fps ceiling whole.
    [(60, 60.0, 60.0), (120, 120.0, 60.0), (144, 48.0, 48.0)],
)
def test_screen_bar_driver_negotiates_target_clock_in_common_modes(
    monkeypatch, maximum_fps: int, negotiated: float, cadence: float
) -> None:
    """Catches active nominal output falling back to a timer despite display-link support."""
    device, view, run_loop, timers, virtual_device = _active_device(
        monkeypatch, maximum_fps=maximum_fps
    )

    device._refresh_render_cadence(True, force=True)

    assert device.display_link is view.links[0]
    assert view.links[0].run_loop_modes == [(run_loop, virtual_device.NSRunLoopCommonModes)]
    assert view.links[0].frame_ranges == [(negotiated, negotiated, negotiated)]
    assert device._render_schedule.driver_fps == negotiated
    assert device.timer is None
    assert timers.created == []
    assert view.sample_fps == [cadence]


def test_screen_bar_display_link_callback_samples_and_invalidates_once(monkeypatch) -> None:
    """Catches one display callback requesting multiple paints or re-registering its driver."""
    device, view, run_loop, _timers, _virtual_device = _active_device(monkeypatch)
    device._refresh_render_cadence(True, force=True)

    device.redraw_(None)

    assert view.paint_requests == [True]
    assert len(view.links) == 1
    assert len(view.links[0].run_loop_modes) == 1
    assert run_loop.timers == []


@pytest.mark.parametrize(
    ("display_interval", "middle_frame"),
    [
        (1.0 / 60.0, ((1.0, 0.5, 0.25, 1.0),)),
        (1.0 / 120.0, ((0.5, 0.25, 0.125, 1.0),)),
    ],
)
def test_screen_bar_display_samples_follow_target_timestamp_not_arrival(
    display_interval: float, middle_frame: tuple[tuple[float, float, float, float], ...]
) -> None:
    """Catches 60 or 120 Hz motion advancing by callback count or callback arrival."""
    from sidepulse.screen_bar_pipeline import (
        ColorSample,
        PresentationTick,
        SamplePair,
        TwoSampleBuffer,
        display_colors_for_tick,
    )

    pair = SamplePair(
        ColorSample(7, 30.0, ((0.0, 0.0, 0.0, 1.0),)),
        ColorSample(7, 30.0 + 1.0 / 60.0, ((1.0, 0.5, 0.25, 1.0),)),
    )
    buffer = TwoSampleBuffer()
    assert buffer.publish(pair)
    schedules = (
        (29.998, 30.000),
        (30.010, 30.000 + display_interval),
        (30.013, 30.000 + display_interval),
        (30.012, 30.000 + 1.0 / 60.0),
    )

    frames = [
        display_colors_for_tick(
            buffer,
            PresentationTick(arrival, target, 7),
            last_safe_colors=None,
            static_fallback_colors=((0.0, 0.0, 0.0, 1.0),),
        )
        for arrival, target in schedules
    ]

    assert frames == [
        ((0.0, 0.0, 0.0, 1.0),),
        middle_frame,
        middle_frame,
        ((1.0, 0.5, 0.25, 1.0),),
    ]


@pytest.mark.parametrize(
    ("environment", "expected_fps"),
    [
        (RenderEnvironment(low_power=True), 30.0),
        (RenderEnvironment(thermal="serious"), 15.0),
    ],
)
def test_screen_bar_constrained_output_replaces_display_link_with_capped_timer(
    monkeypatch, environment: RenderEnvironment, expected_fps: float
) -> None:
    """Catches low-power or thermal changes leaving the uncapped native driver alive."""
    device, view, _run_loop, timers, _virtual_device = _active_device(monkeypatch)
    device._refresh_render_cadence(True, force=True)
    native_link = view.links[0]
    device._runtime_environment = lambda **_kwargs: environment

    device._refresh_render_cadence(True, force=True)

    assert native_link.invalidated == 1
    assert device.display_link is None
    assert device.timer is None
    assert timers.created == []
    assert device._frame_interval_current == 1.0 / expected_fps
    assert device.presentation_scheduler_inputs().animation_active is True


@pytest.mark.parametrize("failure", ["construction", "registration"])
def test_screen_bar_display_link_failure_uses_60_hz_timer_fallback(monkeypatch, failure: str) -> None:
    """Catches an AppKit display-link boundary failure that leaves the bar without a driver."""
    view = _DisplayLinkView(fail_construction=failure == "construction")
    device, view, _run_loop, timers, _virtual_device = _active_device(
        monkeypatch, view=view, fail_registration=failure == "registration"
    )

    device._refresh_render_cadence(True, force=True)

    assert device.display_link is None
    assert device.timer is None
    assert timers.created == []
    assert device.presentation_scheduler_inputs().animation_active is True
    if failure == "registration":
        assert view.links[0].invalidated == 1


@pytest.mark.parametrize(
    "view",
    [
        _DisplayLinkView(supports_target_timestamp=False),
        _DisplayLinkView(supports_frame_range=False),
        _DisplayLinkView(fail_frame_range=True),
    ],
)
def test_screen_bar_unsupported_target_clock_uses_one_timer_without_retry_churn(
    monkeypatch, view: _DisplayLinkView
) -> None:
    """Catches an unsupported display-link lifecycle replacing its fallback every refresh."""
    device, view, _run_loop, timers, _virtual_device = _active_device(
        monkeypatch, view=view
    )

    device._refresh_render_cadence(True, force=True)
    device._refresh_render_cadence(True, force=True)

    assert device.display_link is None
    assert device.timer is None
    assert timers.created == []
    assert device.presentation_scheduler_inputs().animation_active is True
    assert len(view.links) == 1
    assert view.links[0].invalidated == 1


def test_screen_bar_redraw_reads_target_time_and_only_dirties_quantized_changes(
    monkeypatch,
) -> None:
    """Catches a frame callback parsing, sampling, rescheduling, or repainting equal output."""
    from sidepulse.screen_bar_pipeline import ColorSample, SamplePair, TwoSampleBuffer

    link = _DisplayLink(target_timestamps=(10.5, 10.5001))
    view = _DisplayLinkView()
    device, view, _run_loop, _timers, _virtual_device = _active_device(
        monkeypatch, view=view
    )
    buffer = TwoSampleBuffer()
    assert buffer.publish(
        SamplePair(
            ColorSample(9, 10.0, ((0.25, 0.5, 0.75, 1.0),) * 8),
            ColorSample(9, 11.0, ((0.25, 0.5, 0.75, 1.0),) * 8),
        )
    )
    device._sample_buffer = buffer
    device._presentation_generation = 9
    device._static_fallback_colors = ((0.0, 0.0, 0.0, 0.0),) * 8
    device._last_safe_colors = None
    device._previous_target_timestamp = None
    device._refresh_render_cadence = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("frame callback selected a render schedule")
    )
    device.reposition = lambda: (_ for _ in ()).throw(
        AssertionError("frame callback queried geometry")
    )
    view._colors_for_draw = lambda: (_ for _ in ()).throw(
        AssertionError("frame callback sampled the view")
    )

    device.redraw_(link)
    device.redraw_(link)

    expected = ((0.25, 0.5, 0.75, 1.0),) * 8
    assert link.target_timestamp_calls == 2
    assert view.presentation_colors == [expected, expected]
    assert view.paint_requests == [True]


def test_screen_bar_frame_callback_never_observes_or_scans_alcove(monkeypatch) -> None:
    """Catches Alcove capture or geometry reduction moving into a display callback."""
    device, _view, _run_loop, _timers, _virtual_device = _active_device(monkeypatch)

    class Observer:
        def reconcile(self, _request) -> None:
            raise AssertionError("frame callback requested Alcove capture")

        def take(self):
            raise AssertionError("frame callback scanned Alcove pixels")

    device._alcove_observer = Observer()
    device._apply_latest_alcove_observation = lambda *_args: (_ for _ in ()).throw(
        AssertionError("frame callback reduced Alcove geometry")
    )

    device.redraw_(_DisplayLink(target_timestamps=(10.5,)))


def test_screen_bar_draw_rect_never_captures_or_scans_alcove(monkeypatch) -> None:
    """Catches pixel observation being hidden inside the AppKit paint callback."""
    from sidepulse import alcove_observation, virtual_device

    monkeypatch.setattr(
        alcove_observation,
        "capture_alcove_observation",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("drawRect_ captured an Alcove window")
        ),
    )
    monkeypatch.setattr(
        alcove_observation,
        "scan_alpha_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("drawRect_ scanned Alcove pixels")
        ),
    )
    view = virtual_device.VirtualLedView.alloc().initWithFrame_(
        ((0.0, 0.0), (220.0, 37.0))
    )
    view.has_notch = False
    view._presentation_colors = ((0.2, 0.4, 0.6, 1.0),) * 8

    view.drawRect_(None)


def test_reposition_submits_plain_alcove_request_and_applies_validated_center(
    monkeypatch,
) -> None:
    """Catches main-thread identity loss or a worker result being recentered by guess."""
    from sidepulse import virtual_device
    from sidepulse.alcove_observation import AlcoveObservation

    class Rect:
        def __init__(self, x: float, y: float, width: float, height: float) -> None:
            self.origin = SimpleNamespace(x=x, y=y)
            self.size = SimpleNamespace(width=width, height=height)

    class Screen:
        def frame(self):
            return Rect(0.0, 0.0, 1512.0, 982.0)

        def deviceDescription(self):
            return {"NSScreenNumber": 1}

        def backingScaleFactor(self) -> float:
            return 2.0

        def safeAreaInsets(self):
            return SimpleNamespace(top=32.0)

        def auxiliaryTopLeftArea(self):
            return Rect(0.0, 950.0, 646.0, 32.0)

        def auxiliaryTopRightArea(self):
            return Rect(866.0, 950.0, 646.0, 32.0)

    class ScreenClass:
        @staticmethod
        def mainScreen():
            return Screen()

    class Window:
        def __init__(self) -> None:
            self.current = Rect(646.0, 945.0, 220.0, 37.0)
            self.levels: list[int] = []

        def isVisible(self) -> bool:
            return True

        def frame(self):
            return self.current

        def windowNumber(self) -> int:
            return 500

        def setFrame_display_(self, frame, _display) -> None:
            self.current = Rect(frame[0][0], frame[0][1], frame[1][0], frame[1][1])

        def setLevel_(self, level: int) -> None:
            self.levels.append(level)

    class View:
        def __init__(self) -> None:
            self.silhouettes: list[object] = []

        def setRenderGeometryIdentity_(self, _identity) -> None:
            pass

        def setHasNotch_(self, _value) -> None:
            pass

        def setCompactMode_(self, _value) -> None:
            pass

        def setWingsOnlyMode_(self, _value) -> None:
            pass

        def setAlcoveSilhouette_(self, value) -> None:
            self.silhouettes.append(value)

        def setFrame_(self, _frame) -> None:
            pass

        def setNotchWidth_(self, _width) -> None:
            pass

    class Observer:
        def __init__(self) -> None:
            self.requests: list[object] = []
            self.closes = 0

        def reconcile(self, request) -> None:
            self.requests.append(request)

        def close(self, *, timeout_seconds: float) -> bool:
            assert timeout_seconds <= 0.25
            self.closes += 1
            return True

    observer = Observer()
    device = virtual_device.VirtualStatusDevice.alloc().init()
    device.window = Window()
    device.view = View()
    device.wraps_menu_bar = True
    device.follow_alcove_width = True
    device._alcove_observer_factory = lambda _buffer: observer
    monkeypatch.setattr(virtual_device, "NSScreen", ScreenClass)
    monkeypatch.setattr(virtual_device, "is_alcove_running", lambda: True)
    # Deterministic screen values: the real resolver reads WindowServer
    # state, which vanishes on locked/headless sessions (hosted CI).
    monkeypatch.setattr(
        virtual_device,
        "_screen_capture_values",
        lambda _screen: ("1:0.000:0.000:1512.000:982.000", 1, 0.0, 0.0, 1512.0, 982.0, 2.0),
    )
    monkeypatch.setattr(virtual_device, "measured_notch_silhouette", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        virtual_device,
        "_alcove_window_values",
        lambda *_args: (99, 444.0, 0.0, 624.0),
    )
    # Following now preflights Screen Recording, and a denied preflight
    # correctly refuses to start a capture. Pin it: whether the machine
    # running the suite happens to have granted the permission is not
    # what this test is about.
    monkeypatch.setattr(virtual_device, "screen_recording_granted", lambda **_kwargs: True)

    device.reposition()
    request = observer.requests[-1]
    assert request.screen_id == "1:0.000:0.000:1512.000:982.000"
    assert request.display_id == 1
    assert request.window_number == 99
    assert request.scale == 2.0
    assert all(
        isinstance(getattr(request, field), (str, int, float))
        for field in request.__slots__
    )

    contour = (
        (464.0, 8.0),
        (464.0, 32.0),
        (736.0, 32.0),
        (736.0, 8.0),
        (464.0, 8.0),
    )
    device._alcove_buffer.publish(
        AlcoveObservation(
            request_id=request.request_id,
            generation=request.generation,
            screen_id=request.screen_id,
            window_number=request.window_number,
            center_x=600.0,
            width=272.0,
            height=32.0,
            contour=contour,
            captured_at=time.monotonic(),
            confidence=0.95,
        )
    )
    device.reposition()

    # The follow window is one stroke inset wider on EACH side, so the
    # drawn bracket lands on Alcove's real corners instead of 6pt inside
    # them: origin moves out by the inset, width grows by twice it.
    inset = virtual_device.ALCOVE_ACCENT_EDGE_INSET
    assert device.window.current.origin.x == pytest.approx(464.0 - inset)
    assert device.window.current.size.width == pytest.approx(272.0 + 2 * inset)
    assert device.view.silhouettes[-1] == (600.0, 272.0, 32.0, contour)

    previous_request_count = len(observer.requests)
    monkeypatch.setattr(virtual_device, "_alcove_window_values", lambda *_args: None)
    held_at = time.monotonic()
    monkeypatch.setattr(virtual_device.time, "monotonic", lambda: held_at)
    device.reposition()

    assert device._alcove_request is None
    assert observer.closes == 1
    assert len(observer.requests) == previous_request_count
    assert device.window.current.origin.x == pytest.approx(464.0 - inset)
    assert device.view.silhouettes[-1] == (600.0, 272.0, 32.0, contour)

    monkeypatch.setattr(
        virtual_device.time,
        "monotonic",
        lambda: held_at + virtual_device.ALCOVE_HOLD_SECONDS + 0.1,
    )
    device.reposition()

    assert device._alcove_request is None
    assert len(observer.requests) == previous_request_count
    assert device.window.current.origin.x == pytest.approx(632.0)
    assert device.view.silhouettes[-1] is None


@pytest.mark.parametrize(
    "lifecycle",
    ["hide", "disable", "sleep", "terminate", "follow_disabled"],
)
def test_screen_bar_inactive_lifecycle_stops_alcove_observation(
    monkeypatch, lifecycle: str
) -> None:
    """Catches capture work surviving a hidden, disabled, asleep, or torn-down bar."""
    device, _view, _run_loop, _timers, _virtual_device = _active_device(monkeypatch)

    class Observer:
        def __init__(self) -> None:
            self.closes = 0

        def close(self, *, timeout_seconds: float) -> bool:
            assert timeout_seconds <= 0.25
            self.closes += 1
            return True

    observer = Observer()
    device._alcove_observer = observer
    if lifecycle == "hide":
        device.hide()
    elif lifecycle == "disable":
        device.set_enabled(False)
    elif lifecycle == "sleep":
        device._set_display_asleep(True)
    elif lifecycle == "terminate":
        device.terminate()
    elif lifecycle == "follow_disabled":
        device.set_follow_alcove(False)
    else:
        device.set_wraps_menu_bar(False)

    assert observer.closes == 1
    assert device._alcove_observer is None


def test_screen_bar_late_callback_clamps_last_sample_without_waking_sampler(
    monkeypatch,
) -> None:
    """Catches a late display tick advancing WASM or submitting work from the callback."""
    from sidepulse.screen_bar_pipeline import ColorSample, SamplePair, TwoSampleBuffer

    link = _DisplayLink(target_timestamps=(15.0,))
    device, view, _run_loop, _timers, _virtual_device = _active_device(monkeypatch)
    buffer = TwoSampleBuffer()
    assert buffer.publish(
        SamplePair(
            ColorSample(4, 10.0, ((0.0, 0.0, 0.0, 1.0),) * 8),
            ColorSample(4, 11.0, ((1.0, 0.5, 0.25, 1.0),) * 8),
        )
    )
    sampler = _Sampler()
    device._sample_buffer = buffer
    device._presentation_generation = 4
    device._sampler = sampler

    device.redraw_(link)

    assert view.presentation_colors[-1] == ((1.0, 0.5, 0.25, 1.0),) * 8
    assert sampler.commands == []


def test_screen_bar_screen_change_rebinds_one_driver_at_new_refresh_rate(
    monkeypatch,
) -> None:
    """Catches a 60-to-120 Hz screen transition retaining the old display clock.

    Both halves of the clock: the rate the link is registered at AND the rate
    the schedule quantises against. The second half used to lag by up to five
    seconds -- `_install_display_link` re-read NSScreen while the policy used
    `_panel_refresh_hz`'s cache -- so the driver ran at 120 while the gate held
    against a 60 Hz period and let every callback through.
    """
    device, view, _run_loop, timers, virtual_device = _active_device(
        monkeypatch,
        maximum_fps=60,
    )
    device._refresh_render_cadence(True, force=True)
    first = view.links[0]
    virtual_device.NSScreen.screen.maximum_fps = 120

    device.screenDidChange_(None)

    assert first.invalidated == 1
    assert device.display_link is view.links[1]
    assert view.links[1].frame_ranges == [(120.0, 120.0, 120.0)]
    assert device._render_schedule.driver_fps == 120.0
    assert device.timer is None
    assert timers.created == []


class _Sampler:
    def __init__(self, *, closes: bool = True) -> None:
        self.commands: list[object] = []
        self.close_timeouts: list[float] = []
        self.closes = closes

    def reconcile(self, command: object) -> None:
        self.commands.append(command)

    def close(self, *, timeout_seconds: float) -> bool:
        self.close_timeouts.append(timeout_seconds)
        return self.closes


def test_screen_bar_set_program_advances_generation_and_only_enqueues_worker_work(
    monkeypatch,
) -> None:
    """Catches set_program constructing or stepping the WASM engine on AppKit's thread."""
    device, view, _run_loop, _timers, virtual_device = _active_device(monkeypatch)
    monkeypatch.setattr(virtual_device.time, "monotonic", lambda: 12.5)
    sampler = _Sampler()
    device._sampler = sampler
    device.show = lambda: None
    view.setProgram_startedAt_ = lambda *_args: (_ for _ in ()).throw(
        AssertionError("legacy view parser ran on the main thread")
    )

    device.set_program(
        "#34C759 200ms pulse",
        started_at=12.5,
        motion=MotionClass.FINITE,
        static_fallback_program="#34C759",
        next_visual_change_at=13.5,
    )

    assert device._presentation_generation == 1
    assert len(sampler.commands) == 1
    command = sampler.commands[0]
    assert command.generation == 1
    assert command.program == "#34C759 200ms pulse"
    assert command.parse_anchor == 12.5
    assert command.static_fallback_program == "#34C759"
    assert command.sample_interval == pytest.approx(1.0 / 60.0)
    assert command.motion is MotionClass.FINITE
    assert command.next_visual_change_at == 13.5


def test_screen_bar_missing_sample_paints_typed_static_semantic_fallback(
    monkeypatch,
) -> None:
    """Catches first-frame or failed-sampler fallback collapsing semantic truth to black."""
    fallback = ((0.1, 0.2, 0.3, 1.0),) * 8
    link = _DisplayLink(target_timestamps=(50.0,))
    device, view, _run_loop, _timers, _virtual_device = _active_device(monkeypatch)
    device.show = lambda: None
    device._sampler = _Sampler()

    device.set_program(
        "animated",
        started_at=49.0,
        static_fallback_program="steady",
        static_fallback_colors=fallback,
    )
    device.redraw_(link)

    assert view.presentation_colors[-1] == fallback


def test_screen_bar_finite_deadline_demotes_to_static_without_a_watcher(
    monkeypatch,
) -> None:
    """Catches an expired finite cue retaining a frame driver or static watcher."""
    clock = [100.0]
    device, view, run_loop, timers, virtual_device = _active_device(monkeypatch)
    monkeypatch.setattr(virtual_device.time, "monotonic", lambda: clock[0])
    device.reposition = lambda: None
    sampler = _Sampler()
    device._sampler = sampler
    fallback = ((0.2, 0.3, 0.4, 1.0),) * 8

    device.set_program(
        "finite",
        started_at=100.0,
        motion=MotionClass.FINITE,
        static_fallback_program="steady",
        static_fallback_colors=fallback,
        next_visual_change_at=101.0,
    )
    finite_link = device.display_link
    clock[0] = 101.0
    device.presentationStaticDeadline()

    assert finite_link.invalidated == 1
    assert device._animation_active is False
    assert device.display_link is None
    assert device.timer is None
    assert timers.created == []
    assert run_loop.timers == []
    assert sampler.commands[-1].program == "steady"
    assert sampler.commands[-1].motion is MotionClass.STATIC
    assert device._static_fallback_colors == fallback


def test_screen_bar_same_finite_dsl_rearms_for_a_new_episode(monkeypatch) -> None:
    """Catches the DSL-only change gate swallowing a later distinct finite cue episode."""
    clock = [200.0]
    device, _view, _run_loop, _timers, virtual_device = _active_device(monkeypatch)
    monkeypatch.setattr(virtual_device.time, "monotonic", lambda: clock[0])
    device.show = lambda: None
    sampler = _Sampler()
    device._sampler = sampler

    device.set_program(
        "finite",
        started_at=200.0,
        motion=MotionClass.FINITE,
        static_fallback_program="steady",
        next_visual_change_at=201.0,
    )
    first_generation = device._presentation_generation
    clock[0] = 201.0
    device.presentationStaticDeadline()
    clock[0] = 202.0
    device.set_program(
        "finite",
        started_at=202.0,
        motion=MotionClass.FINITE,
        static_fallback_program="steady",
        next_visual_change_at=203.0,
    )

    assert device._presentation_generation > first_generation
    assert sampler.commands[-1].program == "finite"
    assert sampler.commands[-1].motion is MotionClass.FINITE
    assert sampler.commands[-1].parse_anchor == 202.0


def test_screen_bar_continuous_phase_only_refresh_keeps_one_episode(monkeypatch) -> None:
    """Catches phase-only relay refreshes restarting local playback generation."""
    device, view, _run_loop, _timers, _virtual_device = _active_device(monkeypatch)
    device.show = lambda: None
    sampler = _Sampler()
    device._sampler = sampler

    device.set_program(
        "relay early",
        started_at=100.2,
        motion=MotionClass.CONTINUOUS,
        static_fallback_program="steady",
        dedupe_token=("relay", 100.0, 1.6),
    )
    first_generation = device._presentation_generation
    first_program = view.current_program
    first_anchor = sampler.commands[-1].parse_anchor

    device.set_program(
        "relay later",
        started_at=100.8,
        motion=MotionClass.CONTINUOUS,
        static_fallback_program="steady",
        dedupe_token=("relay", 100.0, 1.6),
    )

    assert device._presentation_generation == first_generation
    assert view.current_program == first_program
    assert len(sampler.commands) == 1
    assert sampler.commands[-1].parse_anchor == first_anchor


def test_screen_bar_hide_and_show_replace_at_most_one_sampler(monkeypatch) -> None:
    """Catches a hidden sampler surviving or repeated show spawning duplicate workers."""
    device, _view, _run_loop, _timers, virtual_device = _active_device(monkeypatch)
    first = _Sampler()
    replacements: list[_Sampler] = []
    device._sampler = first
    device._sampler_command = SimpleNamespace(generation=1)
    device._sampler_factory = lambda _buffer: replacements.append(_Sampler()) or replacements[-1]
    device._promote_animation = lambda: None
    device._install_power_observers = lambda: None
    device.reposition = lambda: None

    device.hide()
    device.show()
    device.show()

    assert first.close_timeouts == [virtual_device.SAMPLER_CLOSE_TIMEOUT_SECONDS]
    assert len(replacements) == 1
    assert replacements[0].commands == [device._sampler_command]


def test_screen_bar_show_and_wake_preserve_a_typed_static_schedule(monkeypatch) -> None:
    """Catches lifecycle resume turning a static command into repeating frame work."""
    device, _view, _run_loop, timers, _virtual_device = _active_device(monkeypatch)
    device.reposition = lambda: None
    device._install_power_observers = lambda: None
    device._sampler = _Sampler()
    device._sampler_factory = lambda _buffer: _Sampler()
    device.set_program("#112233", motion=MotionClass.STATIC)
    device.hide()
    device.show()
    device.screenDidSleep_(None)
    device.screenDidWake_(None)

    assert device._animation_active is False
    assert device.display_link is None
    assert device.timer is None
    assert timers.created == []
    assert plan_presentation_schedule(
        device.presentation_scheduler_inputs(),
        now=time.monotonic(),
    ).intents == ()


def test_screen_bar_timed_out_sampler_teardown_blocks_a_second_worker(
    monkeypatch,
) -> None:
    """Catches bounded close timing out and a new lifecycle creating a second worker."""
    device, _view, _run_loop, _timers, _virtual_device = _active_device(monkeypatch)
    first = _Sampler(closes=False)
    replacements: list[_Sampler] = []
    device._sampler = first
    device._sampler_command = SimpleNamespace(generation=1)
    device._sampler_factory = lambda _buffer: replacements.append(_Sampler()) or replacements[-1]
    device._promote_animation = lambda: None
    device._install_power_observers = lambda: None
    device.reposition = lambda: None

    device.hide()
    device.show()

    assert first.close_timeouts
    assert replacements == []
    assert device._sampler_shutdown_incomplete is True


def test_screen_bar_disable_sleep_wake_and_terminate_bound_sampler_lifecycle(
    monkeypatch,
) -> None:
    """Catches lifecycle edges retaining a worker or resurrecting work after termination."""
    device, _view, _run_loop, _timers, virtual_device = _active_device(monkeypatch)
    created: list[_Sampler] = []
    device._sampler_command = SimpleNamespace(generation=1)
    device._sampler_factory = lambda _buffer: created.append(_Sampler()) or created[-1]
    device._promote_animation = lambda: None
    device._install_power_observers = lambda: None
    device.reposition = lambda: None
    device._resume_sampler()
    first = created[0]

    device.screenDidSleep_(None)
    device.screenDidWake_(None)
    device.screenDidWake_(None)
    second = created[1]
    device.set_enabled(False)
    device.set_enabled(True)
    device.show()
    third = created[2]
    device.terminate()
    device.show()

    assert first.close_timeouts == [virtual_device.SAMPLER_CLOSE_TIMEOUT_SECONDS]
    assert second.close_timeouts == [virtual_device.SAMPLER_CLOSE_TIMEOUT_SECONDS]
    assert third.close_timeouts == [virtual_device.SAMPLER_CLOSE_TIMEOUT_SECONDS]
    assert len(created) == 3
    assert device._terminating is True
    assert device._sampler is None
    assert device.display_link is None
    assert device.timer is None
    assert device.window is None
    assert device.view is None


def test_screen_bar_termination_releases_appkit_state_when_order_out_fails(
    monkeypatch,
) -> None:
    """Catches an AppKit teardown exception retaining the window and view graph."""
    device, _view, _run_loop, _timers, _virtual_device = _active_device(monkeypatch)
    device.window.orderOut_ = lambda _sender: (_ for _ in ()).throw(
        RuntimeError("window teardown failed")
    )

    error = None
    try:
        device.terminate()
    except Exception as exc:
        error = exc

    assert error is None
    assert device.window is None
    assert device.view is None


def test_screen_bar_stale_program_or_screen_publication_never_reaches_paint(
    monkeypatch,
) -> None:
    """Catches an old worker publication crossing either generation fence."""
    from sidepulse.screen_bar_pipeline import ColorSample, SamplePair, TwoSampleBuffer

    link = _DisplayLink(target_timestamps=(20.5,))
    device, view, _run_loop, _timers, _virtual_device = _active_device(monkeypatch)
    device.show = lambda: None
    device._sampler = _Sampler()
    buffer = TwoSampleBuffer()
    device._sample_buffer = buffer
    assert buffer.publish(
        SamplePair(
            ColorSample(0, 20.0, ((1.0, 0.0, 0.0, 1.0),) * 8),
            ColorSample(0, 21.0, ((1.0, 0.0, 0.0, 1.0),) * 8),
        )
    )

    device.set_program("#0000FF")
    device.screenDidChange_(None)
    assert buffer.publish(
        SamplePair(
            ColorSample(1, 20.0, ((0.0, 1.0, 0.0, 1.0),) * 8),
            ColorSample(1, 21.0, ((0.0, 1.0, 0.0, 1.0),) * 8),
        )
    )
    device.redraw_(link)

    assert device._presentation_generation == 2
    assert view.presentation_colors[-1] == ((0.0, 0.0, 0.0, 0.0),) * 8


def test_screen_bar_hide_and_display_sleep_clear_every_owned_driver(monkeypatch) -> None:
    """Catches lifecycle pauses that retain either native or timer callbacks."""
    device, _view, _run_loop, _timers, _virtual_device = _active_device(monkeypatch)
    first_link = _DisplayLink()
    first_timer = _Timer(1.0 / 60.0)
    device.display_link = first_link
    device.timer = first_timer

    device.hide()

    assert (first_link.invalidated, first_timer.invalidated) == (1, 1)
    assert device.display_link is None
    assert device.timer is None

    device.window.visible = True
    device._runtime_environment = lambda **_kwargs: RenderEnvironment(
        visible=True, display_asleep=device._display_asleep
    )
    sleeping_link = _DisplayLink()
    sleeping_timer = _Timer(1.0 / 60.0)
    device.display_link = sleeping_link
    device.timer = sleeping_timer

    device.screenDidSleep_(None)

    assert (sleeping_link.invalidated, sleeping_timer.invalidated) == (1, 1)
    assert device.display_link is None
    assert device.timer is None


def test_screen_bar_rebuild_invalidates_old_display_link_before_binding_view(monkeypatch) -> None:
    """Catches replacing the content view while its display link still targets the old view."""
    device, _view, _run_loop, _timers, virtual_device = _active_device(monkeypatch)
    old_link = _DisplayLink()
    device.display_link = old_link

    class ReplacementWindow:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithContentRect_styleMask_backing_defer_(self, *_args):
            return self

        def setOpaque_(self, _value):
            pass

        def setBackgroundColor_(self, _value):
            pass

        def setHasShadow_(self, _value):
            pass

        def setIgnoresMouseEvents_(self, _value):
            pass

        def setLevel_(self, _value):
            pass

        def setCollectionBehavior_(self, _value):
            pass

        def setContentView_(self, view):
            self.content_view = view

    class ReplacementView:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithFrame_(self, _frame):
            return self

    monkeypatch.setattr(virtual_device, "NSWindow", ReplacementWindow)
    monkeypatch.setattr(virtual_device, "VirtualLedView", ReplacementView)

    device._build_window()

    assert old_link.invalidated == 1
    assert device.display_link is None
    assert isinstance(device.view, ReplacementView)


def test_screen_bar_typed_static_and_program_promotion_swap_drivers(monkeypatch) -> None:
    """Catches typed static output retaining a driver or new motion remaining static."""
    device, view, _run_loop, timers, _virtual_device = _active_device(monkeypatch)
    device.reposition = lambda: None
    device._refresh_render_cadence(True, force=True)
    native_link = view.links[0]
    device._sampler = _Sampler()

    device.set_program("static", motion=MotionClass.STATIC)

    assert native_link.invalidated == 1
    assert device.display_link is None
    assert device.timer is None
    assert timers.created == []

    device.set_program("new", motion=MotionClass.CONTINUOUS)

    assert device.timer is None
    assert device.display_link is view.links[1]
    assert timers.created == []


def test_screen_bar_wake_and_show_install_only_one_driver(monkeypatch) -> None:
    """Catches wake or repeated show installing duplicate display callbacks."""
    device, view, _run_loop, _timers, _virtual_device = _active_device(monkeypatch)
    device.reposition = lambda: None
    device._set_display_asleep(True)

    device.screenDidWake_(None)
    device.show()

    assert len(view.links) == 1
    assert device.display_link is view.links[0]
    assert device.timer is None


@pytest.mark.parametrize(
    ("environment", "animation_active", "display_link_available", "driver", "fps", "sample_fps"),
    [
        (RenderEnvironment(visible=False), True, True, RenderDriverKind.PAUSED, 0.0, 0.0),
        (
            RenderEnvironment(visible=True, display_asleep=True),
            True,
            True,
            RenderDriverKind.PAUSED,
            0.0,
            0.0,
        ),
        (RenderEnvironment(), True, True, RenderDriverKind.DISPLAY_LINK, 60.0, 60.0),
        (RenderEnvironment(), True, False, RenderDriverKind.TIMER, 60.0, 60.0),
        (RenderEnvironment(low_power=True), True, True, RenderDriverKind.TIMER, 30.0, 30.0),
        # 30, not the 45 the cap allows: the 60 Hz fallback timer can only
        # deliver 60/n, and naming a rate it cannot produce is what made a
        # "40 fps" policy arrive as 30 with the policy still reporting 40.
        (RenderEnvironment(thermal="fair"), True, True, RenderDriverKind.TIMER, 30.0, 30.0),
        (RenderEnvironment(thermal="serious"), True, True, RenderDriverKind.TIMER, 15.0, 15.0),
        (RenderEnvironment(thermal="critical"), True, True, RenderDriverKind.TIMER, 7.5, 7.5),
        (RenderEnvironment(), False, True, RenderDriverKind.TIMER, 4.0, 4.0),
        (RenderEnvironment(low_power=True), False, True, RenderDriverKind.TIMER, 1.0, 1.0),
    ],
)
def test_render_schedule_selects_driver_without_changing_cadence(
    environment: RenderEnvironment,
    animation_active: bool,
    display_link_available: bool,
    driver: RenderDriverKind,
    fps: float,
    sample_fps: float,
) -> None:
    """Catches a driver choice that bypasses the established cadence caps.

    The cadence is now also rounded DOWN to something the chosen driver can
    actually produce, never up -- these are thermal and low-power ceilings and
    overshooting one is the failure the ceiling exists to prevent.
    """
    schedule = choose_render_schedule(
        environment,
        animation_active,
        display_link_available=display_link_available,
    )

    assert schedule.driver is driver
    assert schedule.cadence.fps == fps
    assert schedule.cadence.sample_fps == sample_fps


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (220.0, 40.0, 8.0),
        (15.0, 40.0, 7.5),
        (40.0, 6.0, 3.0),
        (-1.0, 40.0, 0.0),
    ],
)
def test_alcove_bracket_corner_radius_stays_inside_the_bracket(
    width: float, height: float, expected: float
) -> None:
    """Catches rounded corners extending beyond a narrow Alcove bracket."""
    assert alcove_bracket_corner_radius(width, height) == expected


def test_reanchor_program_snaps_phase_to_the_hardware_write_moment() -> None:
    """Linked means SYNCED: the strip restarts its cycle when the firmware
    picks up a changed LEDS.LED; the bar snaps its clock to that moment so
    the same pulse loops together on both surfaces instead of a few
    hundred milliseconds apart."""
    from sidepulse import virtual_device
    from sidepulse.screen_bar_pipeline import SamplerCommand

    device = virtual_device.VirtualStatusDevice.alloc().init()

    class _View:
        def __init__(self) -> None:
            self.recorded: list[tuple[str, float]] = []

        def setPresentationProgram_startedAt_(self, program, anchor) -> None:
            self.recorded.append((program, anchor))

    class _Sampler:
        def __init__(self) -> None:
            self.commands: list[object] = []

        def reconcile(self, command) -> None:
            self.commands.append(command)

    device.view = _View()
    sampler = _Sampler()
    device._sampler = sampler
    command = SamplerCommand(
        generation=int(device._presentation_generation),
        program="#00FF00 1600ms pulse\nrepeat",
        parse_anchor=100.0,
        static_fallback_program="off",
        sample_interval=1.0 / 60.0,
        motion=MotionClass.CONTINUOUS,
        next_visual_change_at=None,
    )
    device._sampler_command = command

    # Sub-50ms nudges are noise, not a handshake.
    assert device.reanchor_program(100.02) is False
    # The real handshake: phase snaps and the sampler gets a fresh command.
    assert device.reanchor_program(100.4) is True
    assert device._sampler_command.parse_anchor == 100.4
    assert device._sampler_command.program == command.program
    assert sampler.commands and sampler.commands[-1].parse_anchor == 100.4
    assert device.view.recorded[-1] == (command.program, 100.4)

    # A program that JUST changed must not be snapped again -- that is
    # the double restart that read as flashing during rapid state flips.
    device._program_applied_at = time.monotonic()
    assert device.reanchor_program(300.0) is False
    device._program_applied_at = float("-inf")

    # A static program has no phase to snap.
    device._sampler_command = SamplerCommand(
        generation=device._sampler_command.generation,
        program="off",
        parse_anchor=100.4,
        static_fallback_program="off",
        sample_interval=1.0 / 60.0,
        motion=MotionClass.STATIC,
        next_visual_change_at=None,
    )
    assert device.reanchor_program(200.0) is False


def test_a_full_screen_space_hides_the_bar_unless_opted_in():
    """'In full screen videos it is still there' (2026-08-21): the bar's
    window level rides above full-screen video by necessity, so a
    full-screen space (menu bar gone -- the visible frame reaches the
    screen top) hides the bar unless the owner flipped the switch."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from sidepulse.virtual_device import VirtualStatusDevice, space_hides_menu_bar

    def screen(top_inset: float):
        return SimpleNamespace(
            frame=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=1512.0, height=982.0),
            ),
            visibleFrame=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=1512.0, height=982.0 - top_inset),
            ),
        )

    assert space_hides_menu_bar(screen(0.0)) is True  # full-screen space
    assert space_hides_menu_bar(screen(24.0)) is False  # normal menu bar

    device = VirtualStatusDevice.alloc().init()
    window = MagicMock()
    window.screen.return_value = screen(0.0)
    device.window = window
    device._enabled = True

    device._reconcile_fullscreen_visibility()
    window.orderOut_.assert_called_once()

    # Space returns to normal: the bar comes back.
    window.screen.return_value = screen(24.0)
    device._reconcile_fullscreen_visibility()
    window.orderFrontRegardless.assert_called_once()

    # Opted in: full-screen no longer hides it.
    device.set_show_in_full_screen(True)
    window.screen.return_value = screen(0.0)
    window.orderOut_.reset_mock()
    device._reconcile_fullscreen_visibility()
    window.orderOut_.assert_not_called()


def test_show_never_fronts_the_bar_over_a_full_screen_space():
    """The regression behind 'still there inside full-screen video':
    show() fronted the window BEFORE reconciling, so every program
    reassert popped the bar back over the movie. The verdict now comes
    first; a hidden space never sees orderFrontRegardless from show()."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from sidepulse.virtual_device import VirtualStatusDevice

    def screen(top_inset: float):
        return SimpleNamespace(
            frame=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=1512.0, height=982.0),
            ),
            visibleFrame=lambda: SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=1512.0, height=982.0 - top_inset),
            ),
        )

    device = VirtualStatusDevice.alloc().init()
    window = MagicMock()
    window.screen.return_value = screen(0.0)  # full-screen space
    window.isVisible.return_value = False
    device.window = window
    device._enabled = True
    # Geometry, cadence and samplers are not under test.
    device.reposition = lambda: None
    device._install_space_observer = lambda: None
    device._install_power_observers = lambda: None
    device._refresh_render_cadence = lambda *_a, **_k: None
    device._resume_sampler = lambda: None
    device._resume_alcove_observer = lambda: None
    device._publish_presentation_schedule = lambda: None

    device.show()

    window.orderFrontRegardless.assert_not_called()
    window.orderOut_.assert_called()

    # Back on a normal space, the same show() fronts it again.
    window.screen.return_value = screen(24.0)
    window.orderOut_.reset_mock()
    device.show()
    window.orderFrontRegardless.assert_called()
    window.orderOut_.assert_not_called()


def test_announcer_pill_entrance_springs_from_its_top_anchor(monkeypatch) -> None:
    """The pill used to teleport: frame set, orderFrontRegardless, done.
    Its entrance is now a top-anchored spring + fade (wired 2026-08-26,
    Dynamic Island grammar), and Reduce Motion keeps the instant show."""
    from sidepulse.virtual_device import AnnouncerPill

    pill = AnnouncerPill()
    pill._ensure_window()
    pill._animate_entrance()

    layer = pill.view.layer()
    assert layer is not None
    assert layer.animationForKey_("sidepulse.pill.entrance") is not None
    assert layer.animationForKey_("sidepulse.pill.fade") is not None
    assert tuple(layer.anchorPoint()) == (0.5, 1.0)

    # Reduce Motion: no animation is queued at all.

    layer.removeAllAnimations()
    monkeypatch.setattr(
        "sidepulse.accessibility_display.read_accessibility_display_preferences",
        lambda: type("P", (), {"reduce_motion": True})(),
    )
    pill._animate_entrance()
    assert layer.animationForKey_("sidepulse.pill.entrance") is None
    pill.close()


def test_follow_window_height_tracks_the_capsules_measured_depth() -> None:
    """An expanded Alcove capsule runs far taller than the hardware
    notch; keeping hardware-notch height rendered the band mid-capsule
    as a detached smear (fixed 2026-08-27)."""
    from sidepulse import virtual_device
    from sidepulse.screen_bar_runtime import install_screen_bar_runtime

    install_screen_bar_runtime()
    screen = SimpleNamespace(
        frame=lambda: SimpleNamespace(
            origin=SimpleNamespace(x=0.0, y=0.0),
            size=SimpleNamespace(width=1512.0, height=982.0),
        ),
        safeAreaInsets=lambda: SimpleNamespace(top=32.0),
        auxiliaryTopLeftArea=lambda: SimpleNamespace(
            origin=SimpleNamespace(x=0.0, y=0.0),
            size=SimpleNamespace(width=640.0, height=24.0),
        ),
        auxiliaryTopRightArea=lambda: SimpleNamespace(
            origin=SimpleNamespace(x=872.0, y=0.0),
            size=SimpleNamespace(width=640.0, height=24.0),
        ),
    )
    baseline = virtual_device.virtual_window_frame_for_screen(
        screen, wrap_menu_bar=True, alcove_total_width=286.0, alcove_center_x=756.0
    )
    expanded = virtual_device.virtual_window_frame_for_screen(
        screen,
        wrap_menu_bar=True,
        alcove_total_width=286.0,
        alcove_center_x=756.0,
        alcove_total_height=78.0,
    )
    assert expanded[1][1] == 78.0 + virtual_device.LED_BAND_HEIGHT
    assert expanded[1][1] > baseline[1][1]
    # A collapsed capsule can never lift the band above the hardware notch.
    collapsed = virtual_device.virtual_window_frame_for_screen(
        screen,
        wrap_menu_bar=True,
        alcove_total_width=200.0,
        alcove_center_x=756.0,
        alcove_total_height=10.0,
    )
    assert collapsed[1][1] == baseline[1][1]


def test_alcove_relevance_wakes_from_a_cached_presence_read() -> None:
    """A launched Alcove against an idle bar starts its own follow
    cadence: the schedule inputs refresh relevance from the probe's
    cached answer instead of waiting for an unrelated reposition
    (fixed 2026-08-27)."""
    from sidepulse.virtual_device import VirtualStatusDevice

    device = VirtualStatusDevice.alloc().init()
    device.wraps_menu_bar = True
    device.follow_alcove_width = True
    device.wing_length_override = None
    device._alcove_relevant = False
    device._alcove_presence_probe = SimpleNamespace(running=lambda now: True)

    assert device._alcove_follow_relevant() is True
    assert device._alcove_relevant is True

    quiet = VirtualStatusDevice.alloc().init()
    quiet.wraps_menu_bar = True
    quiet.follow_alcove_width = True
    quiet.wing_length_override = None
    quiet._alcove_relevant = False
    quiet._alcove_presence_probe = SimpleNamespace(running=lambda now: False)
    assert quiet._alcove_follow_relevant() is False
