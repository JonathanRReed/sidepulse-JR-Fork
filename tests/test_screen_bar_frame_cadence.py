"""The Screen Bar presents at the cadence the policy chose, not the panel's.

`choose_render_cadence` computes a real framerate -- 30 fps for the slow
breathe that is the app's resting state, 15 under thermal pressure, 4 when
nothing is moving -- and every driver then discarded it. The display link asks
the panel for its maximum (60-120 Hz) and the fallback timer is a hardcoded 60
Hz, so the number was computed on every schedule change and never used.

The gate that fixes it is phase-free on purpose: it compares against what was
last actually PRESENTED, never against an accumulated schedule, so a late tick
can never cascade into a burst of catch-up frames.

Four things that gate got wrong, each with a test below that fails when the
behaviour is taken back out (verified by reverting the specific lines):

  C1  the interval was started by frames that had nothing to show, so the
      first real frame of a cue was locked out for a whole interval
  C2  a fixed 10% tolerance turned main-thread jitter into dropped frames one
      for one whenever the cadence equalled the driver rate
  C3  a 40 fps policy was delivered as 30 fps, deterministically, at zero
      jitter, and the policy went on reporting 40
  C4  the reset never ran on the cadence-change path the app actually takes,
      and the test that pinned it could not fail
"""

from __future__ import annotations

import math
import random

import pytest

from sidepulse.render_policy import (
    RenderCadence,
    RenderDriverKind,
    RenderEnvironment,
    RenderSchedule,
    choose_render_cadence,
    choose_render_schedule,
    deliverable_fps,
    presentation_hold_seconds,
)
from sidepulse.screen_bar_pipeline import (
    DEFAULT_PRESENTATION_METRICS,
    ColorSample,
    PresentationMetricKind,
    SamplePair,
)

LED_COUNT = 8
_LIT = ((0.0, 0.9, 1.0, 1.0),) * LED_COUNT


class _View:
    """The AppKit boundary redraw_ actually touches."""

    def __init__(self) -> None:
        self.marked = 0
        self.render_fps = None
        self._presentation_colors = None

    def setPresentationColors_(self, colors) -> None:
        self._presentation_colors = colors

    def setNeedsDisplay_(self, flag) -> None:
        self.marked += 1

    def setRenderFps_(self, fps) -> None:
        self.render_fps = fps


def _device(monkeypatch, fps: float | None, *, driver_fps: float = 0.0):
    from sidepulse import virtual_device

    clock = {"now": 1000.0}
    monkeypatch.setattr(virtual_device.time, "monotonic", lambda: clock["now"])

    device = virtual_device.VirtualStatusDevice.alloc().init()
    device.window = None
    device.view = _View()
    device._is_surface_visible = lambda: True
    if fps is not None:
        device._render_schedule = RenderSchedule(
            driver=RenderDriverKind.TIMER,
            cadence=RenderCadence(fps=fps, sample_fps=fps),
            driver_fps=driver_fps,
        )
    return device, clock, virtual_device


def _publish(device, at: float, colors=_LIT) -> None:
    """Put a real sample pair in the buffer for the current generation."""
    generation = device._presentation_generation
    device._sample_buffer.publish(
        SamplePair(
            ColorSample(generation=generation, sampled_at=at - 0.001, colors=colors),
            ColorSample(generation=generation, sampled_at=at, colors=colors),
        )
    )


def _drive(
    device,
    clock,
    virtual_device,
    *,
    callback_hz: float,
    seconds: float,
    jitter: float = 0.0,
    fed: bool = True,
) -> int:
    """Run the real callback for a while; count frames that reached the pipeline."""
    calls = {"n": 0}
    original = virtual_device.display_colors_for_tick

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    virtual_device.display_colors_for_tick = counting
    noise = random.Random(20260814)
    try:
        start = clock["now"]
        ticks = int(callback_hz * seconds)
        for index in range(ticks):
            clock["now"] = start + index / callback_hz + (noise.uniform(0.0, jitter) if jitter else 0.0)
            if fed:
                _publish(device, clock["now"])
            device.redraw_(None)
    finally:
        virtual_device.display_colors_for_tick = original
    return calls["n"]


@pytest.mark.parametrize(
    ("callback_hz", "policy_fps", "expected"),
    [
        (120.0, 30.0, 30),  # ProMotion display link, resting breathe
        (60.0, 30.0, 30),  # timer fallback, resting breathe
        (60.0, 60.0, 60),  # a real transition still gets every frame
        (60.0, 15.0, 15),  # thermal "serious" clamp finally does something
        (120.0, 4.0, 4),  # the static watch rate
    ],
)
def test_the_pipeline_runs_at_the_policy_rate_not_the_callback_rate(
    monkeypatch, callback_hz: float, policy_fps: float, expected: int
) -> None:
    device, clock, virtual_device = _device(monkeypatch, policy_fps, driver_fps=callback_hz)
    frames = _drive(device, clock, virtual_device, callback_hz=callback_hz, seconds=1.0)
    # One frame either way for where the window lands against the tick grid.
    assert abs(frames - expected) <= 1
    assert frames < callback_hz or policy_fps >= callback_hz


def test_a_callback_with_no_schedule_still_presents_every_tick(monkeypatch) -> None:
    """No policy, no gate -- anything driving redraw_ directly is unaffected."""
    device, clock, virtual_device = _device(monkeypatch, None)
    assert _drive(device, clock, virtual_device, callback_hz=60.0, seconds=1.0) == 60


def test_runtime_metrics_distinguish_presented_and_suppressed_callbacks(
    monkeypatch,
) -> None:
    device, clock, virtual_device = _device(
        monkeypatch,
        30.0,
        driver_fps=60.0,
    )
    before = DEFAULT_PRESENTATION_METRICS.snapshot()

    delivered = _drive(
        device,
        clock,
        virtual_device,
        callback_hz=60.0,
        seconds=1.0,
    )

    after = DEFAULT_PRESENTATION_METRICS.snapshot()
    assert (
        after.counter(PresentationMetricKind.PROCESSED_CALLBACK)
        - before.counter(PresentationMetricKind.PROCESSED_CALLBACK)
        == delivered
    )
    assert (
        after.counter(PresentationMetricKind.PRESENTED_FRAME) - before.counter(PresentationMetricKind.PRESENTED_FRAME)
        == delivered
    )
    assert (
        after.counter(PresentationMetricKind.SUPPRESSED_CALLBACK)
        - before.counter(PresentationMetricKind.SUPPRESSED_CALLBACK)
        == 60 - delivered
    )


def test_a_stall_never_cascades_into_a_burst_of_catch_up_frames(monkeypatch) -> None:
    """Phase-free: the gate measures from the last frame PRESENTED, so a
    ten-second gap owes exactly one frame, not three hundred."""
    device, clock, _virtual_device = _device(monkeypatch, 30.0, driver_fps=60.0)
    assert device._due_for_presentation(clock["now"]) is True
    device._mark_presented(clock["now"])
    clock["now"] += 10.0
    assert device._due_for_presentation(clock["now"]) is True
    device._mark_presented(clock["now"])
    assert device._due_for_presentation(clock["now"]) is False
    assert device._due_for_presentation(clock["now"] + 1 / 30.0) is True


# --- C1: the interval belongs to frames that had something to show ---------


def test_a_frame_with_no_sampler_output_does_not_spend_the_interval(
    monkeypatch,
) -> None:
    """The dark blink at the head of a cue must last one driver tick.

    set_program advances the generation and hands the program to the sampler
    thread. If the next callback beats the WASM parse, display_colors_for_tick
    finds no pair for this generation and presents _static_fallback_colors --
    OFF, in production, because no caller ever passes anything else. Letting
    that frame start the cadence clock locked the cue's real first frame out
    for a whole interval: 33 ms at the resting cadence, 67 ms under thermal
    `serious`, up to 183 ms in low power.
    """
    device, clock, virtual_device = _device(monkeypatch, 30.0, driver_fps=60.0)

    # The sampler has not published yet: this frame is the OFF fallback.
    device.redraw_(None)
    assert device._last_presented_at is None
    assert device.view._presentation_colors == virtual_device._OFF_COLORS

    # One driver tick later the sampler lands. The gate must not be holding.
    clock["now"] += 1 / 60.0
    _publish(device, clock["now"])
    device.redraw_(None)
    assert device.view._presentation_colors == _LIT
    assert device._last_presented_at == clock["now"]

    # And a real frame DOES start the clock -- this is a correctness fix, not
    # the gate being switched off.
    clock["now"] += 1 / 60.0
    _publish(device, clock["now"])
    device.redraw_(None)
    assert device._last_presented_at == pytest.approx(clock["now"] - 1 / 60.0)


def test_the_gate_still_rate_limits_once_real_frames_are_flowing(
    monkeypatch,
) -> None:
    """The C1 fix must not become 'never gate'. With the sampler feeding
    every tick, a 30 fps cadence still costs 30 pipeline runs, not 60."""
    device, clock, virtual_device = _device(monkeypatch, 30.0, driver_fps=60.0)
    assert _drive(device, clock, virtual_device, callback_hz=60.0, seconds=1.0) == 30


# --- C2: jitter must not become dropped frames -----------------------------


@pytest.mark.parametrize("jitter_ms", [1.0, 2.0, 4.0, 8.0])
def test_main_thread_jitter_does_not_drop_frames_at_the_driver_rate(monkeypatch, jitter_ms: float) -> None:
    """A FINITE cue asks for 60 fps and the driver runs at 60 Hz. The old
    fixed 10% tolerance left 1.67 ms of headroom, and because the stamp came
    from the jittered callback time, one late tick made the next measurement
    short and it was dropped -- 4 ms of jitter measured 50.2 fps, 8 ms
    measured 44.2, and the losses were irregular, which is judder. Half a
    driver period of headroom is 8.3 ms at 60 Hz.
    """
    device, clock, virtual_device = _device(monkeypatch, 60.0, driver_fps=60.0)
    frames = _drive(
        device,
        clock,
        virtual_device,
        callback_hz=60.0,
        seconds=5.0,
        jitter=jitter_ms / 1000.0,
    )
    assert frames == 300


def test_jitter_does_not_break_the_two_to_one_case_either(monkeypatch) -> None:
    device, clock, virtual_device = _device(monkeypatch, 30.0, driver_fps=60.0)
    frames = _drive(
        device,
        clock,
        virtual_device,
        callback_hz=60.0,
        seconds=5.0,
        jitter=0.004,
    )
    assert abs(frames - 150) <= 1


def test_the_hold_is_half_a_driver_period_short_of_the_interval() -> None:
    """The arithmetic the two tests above depend on, stated once."""
    for driver_fps, cadence_fps, ticks in ((60.0, 60.0, 1), (60.0, 30.0, 2), (120.0, 30.0, 4)):
        schedule = RenderSchedule(
            driver=RenderDriverKind.TIMER,
            cadence=RenderCadence(fps=cadence_fps, sample_fps=cadence_fps),
            driver_fps=driver_fps,
        )
        assert presentation_hold_seconds(schedule) == pytest.approx((ticks - 0.5) / driver_fps)
    # An unstated driver keeps the old fixed tolerance rather than inventing a
    # period from nothing.
    unstated = RenderSchedule(driver=RenderDriverKind.TIMER, cadence=RenderCadence(fps=30.0, sample_fps=30.0))
    assert presentation_hold_seconds(unstated) == pytest.approx(0.9 / 30.0)


# --- C3: the policy may only promise what the driver can deliver -----------


def test_the_policy_never_names_a_rate_its_driver_cannot_produce() -> None:
    """On a 120 Hz panel under thermal `fair` the old policy snapped 45 to the
    PANEL and named 40 fps -- but the driver is the 60 Hz fallback timer, and
    60 Hz ticks against a 25 ms threshold present every second tick: 30 fps, a
    25% shortfall at zero jitter, with the policy still reporting 40."""
    schedule = choose_render_schedule(
        RenderEnvironment(thermal="fair"),
        True,
        display_link_available=False,
        refresh_hz=120.0,
    )
    assert schedule.driver is RenderDriverKind.TIMER
    assert schedule.driver_fps == 60.0
    assert schedule.cadence.fps == 30.0  # not 40
    # ...and the named rate is a whole fraction of the driver, which is the
    # property that makes "named" and "delivered" the same number.
    assert schedule.driver_fps / schedule.cadence.fps == pytest.approx(
        round(schedule.driver_fps / schedule.cadence.fps)
    )


@pytest.mark.parametrize(
    ("thermal", "low_power", "refresh"),
    [
        ("nominal", False, 120.0),
        ("fair", False, 120.0),
        ("serious", False, 60.0),
        ("critical", False, 120.0),
        ("nominal", True, 60.0),
        ("serious", True, 120.0),
        ("critical", True, 60.0),
    ],
)
def test_every_reachable_schedule_delivers_exactly_what_it_names(
    monkeypatch, thermal: str, low_power: bool, refresh: float
) -> None:
    """Drive the real policy, then measure the real callback. The two numbers
    have to agree for every environment the app can actually be in."""
    from sidepulse import virtual_device

    schedule = choose_render_schedule(
        RenderEnvironment(thermal=thermal, low_power=low_power),
        True,
        display_link_available=True,
        refresh_hz=refresh,
    )
    device, clock, _module = _device(monkeypatch, None)
    device._render_schedule = schedule
    delivered = _drive(
        device,
        clock,
        virtual_device,
        callback_hz=schedule.driver_fps,
        seconds=4.0,
    )
    assert abs(delivered / 4.0 - schedule.cadence.fps) <= 0.5
    # And the ceiling the environment asked for is still respected.
    ceiling = choose_render_cadence(RenderEnvironment(thermal=thermal, low_power=low_power), True)
    assert schedule.cadence.fps <= ceiling.fps + 1e-9


def test_deliverable_rates_round_down_never_up() -> None:
    """Overshooting is the dangerous direction: these targets are thermal and
    low-power ceilings, and the whole point of a ceiling is not exceeding it."""
    assert deliverable_fps(60.0, 40.0) == 30.0
    assert deliverable_fps(60.0, 60.0) == 60.0
    assert deliverable_fps(60.0, 8.0) == 7.5
    assert deliverable_fps(120.0, 30.0) == 30.0
    assert deliverable_fps(60.0, 90.0) == 60.0  # cannot exceed the driver
    assert deliverable_fps(0.0, 30.0) == 30.0  # no driver stated, no opinion


# --- C4: the reset, on the path the app actually takes ---------------------


def _thermal_transition(monkeypatch, device, thermal: str, *, low_power: bool = False):
    """Drive the REAL cadence-refresh path for one environment change."""
    monkeypatch.setattr(
        type(device),
        "_runtime_environment",
        lambda self, force=False: RenderEnvironment(thermal=thermal, low_power=low_power),
        raising=False,
    )
    monkeypatch.setattr(type(device), "_panel_refresh_hz", lambda self: 60.0, raising=False)
    monkeypatch.setattr(type(device), "_display_link_available", lambda self: False, raising=False)
    return device._refresh_render_cadence(True)


def test_a_cadence_change_presents_on_the_very_next_callback(monkeypatch) -> None:
    """Through _refresh_render_cadence, which is the ONLY way the running app
    ever changes cadence -- not through _apply_render_schedule.

    This is the fix for a test that could not fail. The old version called
    _apply_render_schedule by hand with a PAUSED schedule and then assigned
    _render_schedule directly, a transition _refresh_render_cadence cannot
    produce, and justified itself with "a jump from 4 fps to 60 waits out a
    quarter second first" -- which was never true, because the gate always
    compares against the CURRENT interval. Every real transition that keeps
    the timer driver (serious -> critical, critical -> fair) reports itself
    already installed and never reaches the reset at all.
    """
    device, clock, _virtual_device = _device(monkeypatch, None)
    device._animation_active = True

    slow = _thermal_transition(monkeypatch, device, "critical")
    assert device._render_schedule.driver is RenderDriverKind.TIMER
    assert slow.fps == pytest.approx(7.5)

    assert device._due_for_presentation(clock["now"]) is True
    device._mark_presented(clock["now"])
    clock["now"] += 0.001
    assert device._due_for_presentation(clock["now"]) is False

    fast = _thermal_transition(monkeypatch, device, "nominal")
    assert device._render_schedule.driver is RenderDriverKind.TIMER  # same driver
    assert fast.fps > slow.fps
    assert device._due_for_presentation(clock["now"]) is True


def test_an_unchanged_cadence_does_not_reset_the_gate(monkeypatch) -> None:
    """The other half: re-deciding the same schedule must not hand out a free
    frame, or a 2 s environment poll becomes a second cadence."""
    device, clock, _virtual_device = _device(monkeypatch, None)
    device._animation_active = True
    _thermal_transition(monkeypatch, device, "serious")
    device._mark_presented(clock["now"])
    clock["now"] += 0.001
    _thermal_transition(monkeypatch, device, "serious")
    assert device._due_for_presentation(clock["now"]) is False


def test_a_new_program_presents_on_the_very_next_callback(monkeypatch) -> None:
    device, clock, _virtual_device = _device(monkeypatch, 4.0, driver_fps=60.0)
    assert device._due_for_presentation(clock["now"]) is True
    device._mark_presented(clock["now"])
    clock["now"] += 0.001
    assert device._due_for_presentation(clock["now"]) is False
    device._advance_presentation_generation(enqueue=False)
    assert device._due_for_presentation(clock["now"]) is True


def test_the_clamps_the_policy_already_computes_now_reach_the_screen(
    monkeypatch,
) -> None:
    """A regression guard on the whole point: thermal and low-power clamps
    were computed and dropped, so a hot machine rendered the notch at 60-120
    Hz regardless. Drive the real policy, not a hand-made schedule."""
    from sidepulse import virtual_device

    counts = []
    for thermal in ("nominal", "serious"):
        schedule = choose_render_schedule(
            RenderEnvironment(thermal=thermal),
            True,
            display_link_available=False,
            gentle_motion=True,
            refresh_hz=60.0,
        )
        device, clock, _module = _device(monkeypatch, None)
        device._render_schedule = schedule
        counts.append(_drive(device, clock, virtual_device, callback_hz=60.0, seconds=1.0))
    assert counts == [30, 15]
    assert math.isclose(counts[0] / counts[1], 2.0)
