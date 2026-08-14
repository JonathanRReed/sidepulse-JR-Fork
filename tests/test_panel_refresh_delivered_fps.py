"""What the Screen Bar actually delivers on a panel that is not a 60 multiple.

C3 fixed a real defect -- the policy named 40 fps and the screen showed 30 --
by quantising the cadence to the rate the DRIVER calls back at. But it left the
older panel-divisor snap in front of it on the TIMER path, so the cadence went
through two different quantisers in series:

    deliverable_fps(driver, refresh_divisor_fps(panel, target))

Composing two quantisers is lossless only when one lattice contains the other.
{driver/n} contains {panel/m} exactly when the panel is a whole multiple of the
driver, which for the 60 Hz fallback timer means 60, 120, 180, 240 -- and
nothing else. On every other panel the first snap moved the target off the
driver's lattice and the second snap could only round it further DOWN:

    144 Hz, resting breathe: 30 -> 28.8 (panel snap) -> 20 (driver snap)

a 33% cut, delivered, to buy a 4% correction in a reported number.

The snap could not have helped on that path in the first place. The fallback
timer is not vsync-locked to anything: it fires on the shared frame-fallback
interval (60 Hz) whatever the panel is doing. Subsampling a free-running 60 Hz
timer by an integer n cannot align it to a 144 Hz panel -- it only makes the
same beat happen less often. So on the TIMER path the panel snap bought no
vsync alignment and cost framerate, which is the definition of a bad trade.

The DISPLAY_LINK path never had the problem: driver_callback_fps already IS the
panel (floored at 60, capped at 120, matching what _install_display_link asks
for), so quantising to the driver there is quantising to the panel.

The tables below assert DELIVERED fps -- measured by driving the real
presentation gate with real callbacks -- not the number the policy prints.
"""

from __future__ import annotations

import itertools

import pytest

from sidepulse import render_policy
from sidepulse.render_policy import (
    RenderDriverKind,
    RenderEnvironment,
    RenderSchedule,
    choose_render_cadence,
    choose_render_schedule,
    refresh_divisor_fps,
)
from sidepulse.screen_bar_pipeline import ColorSample, SamplePair

LED_COUNT = 8
_LIT = ((0.0, 0.9, 1.0, 1.0),) * LED_COUNT

# A realistic panel set. 60 and 120 are whole multiples of the fallback timer
# and were the only two the prior C3 tests exercised -- they are exactly the
# two that cannot show this defect.
PANELS = (
    60.0,  # commodity external, non-ProMotion built-in
    90.0,  # portable/3rd-party panels
    100.0,  # ultrawides
    120.0,  # iPad-class, a 60 multiple
    144.0,  # the workhorse gaming panel -- NOT a 60 multiple
    165.0,  # 144's bigger sibling
    240.0,  # a 60 multiple again
)

# A ProMotion panel reports 120 for maximumFramesPerSecond but the hardware
# walks these rates as content demands, and an external variable-refresh panel
# can report any of them. Four of the seven are not 60 multiples.
PROMOTION_RANGE = (24.0, 30.0, 40.0, 48.0, 60.0, 80.0, 120.0)

ALL_PANELS = tuple(dict.fromkeys(PANELS + PROMOTION_RANGE))


class _View:
    """The AppKit boundary redraw_ actually touches."""

    def setPresentationColors_(self, colors) -> None:
        pass

    def setNeedsDisplay_(self, flag) -> None:
        pass

    def setRenderFps_(self, fps) -> None:
        pass


def _delivered_fps(monkeypatch, schedule: RenderSchedule, seconds: float = 8.0) -> float:
    """Run the real gate against real driver callbacks; return frames/second.

    The callbacks come at ``schedule.driver_fps`` because that is what the
    driver this schedule chose will really fire at. Zero jitter: this measures
    the deterministic loss, not a jitter effect.
    """
    from sidepulse import virtual_device

    clock = {"now": 1000.0}
    monkeypatch.setattr(virtual_device.time, "monotonic", lambda: clock["now"])

    device = virtual_device.VirtualStatusDevice.alloc().init()
    device.window = None
    device.view = _View()
    device._is_surface_visible = lambda: True
    device._render_schedule = schedule

    frames = {"n": 0}
    original = virtual_device.display_colors_for_tick

    def counting(*args, **kwargs):
        frames["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(virtual_device, "display_colors_for_tick", counting)

    callback_hz = schedule.driver_fps
    assert callback_hz > 0.0, "a schedule with no driver cannot deliver anything"
    start = clock["now"]
    for index in range(int(callback_hz * seconds)):
        clock["now"] = start + index / callback_hz
        generation = device._presentation_generation
        device._sample_buffer.publish(
            SamplePair(
                ColorSample(
                    generation=generation, sampled_at=clock["now"] - 0.001, colors=_LIT
                ),
                ColorSample(generation=generation, sampled_at=clock["now"], colors=_LIT),
            )
        )
        device.redraw_(None)
    return frames["n"] / seconds


def _best_achievable(driver_fps: float, ceiling_fps: float) -> float:
    """The fastest rate at or below the ceiling that this driver can produce.

    Derived from first principles rather than by calling ``deliverable_fps``,
    so this is an independent statement of the right answer: the gate presents
    one callback in every n, so the reachable set is exactly {driver/n}, and
    the correct choice is its largest member that still clears the ceiling.
    """
    for divisor in itertools.count(1):
        if driver_fps / divisor <= ceiling_fps + 1e-9:
            return driver_fps / divisor
    raise AssertionError("unreachable")


# --- the fallback timer: same rate on every panel, because it is not the panel


# (label, environment, gentle_motion, expected delivered fps)
TIMER_TABLE = (
    ("transition", RenderEnvironment(), False, 60.0),
    ("breathe", RenderEnvironment(), True, 30.0),
    ("fair/transition", RenderEnvironment(thermal="fair"), False, 30.0),
    ("fair/breathe", RenderEnvironment(thermal="fair"), True, 30.0),
    ("serious", RenderEnvironment(thermal="serious"), False, 15.0),
    ("serious/breathe", RenderEnvironment(thermal="serious"), True, 15.0),
    ("critical", RenderEnvironment(thermal="critical"), False, 7.5),
    ("lowpower", RenderEnvironment(low_power=True), False, 30.0),
    ("lowpower/breathe", RenderEnvironment(low_power=True), True, 30.0),
    (
        "lowpower+serious",
        RenderEnvironment(low_power=True, thermal="serious"),
        False,
        10.0,
    ),
    (
        "lowpower+critical",
        RenderEnvironment(low_power=True, thermal="critical"),
        False,
        5.0,
    ),
)


@pytest.mark.parametrize("panel", ALL_PANELS, ids=lambda hz: f"{hz:g}Hz")
@pytest.mark.parametrize(
    ("label", "environment", "gentle", "expected"),
    TIMER_TABLE,
    ids=[row[0] for row in TIMER_TABLE],
)
def test_the_fallback_timer_delivers_the_same_rate_on_every_panel(
    monkeypatch,
    panel: float,
    label: str,
    environment: RenderEnvironment,
    gentle: bool,
    expected: float,
) -> None:
    """The timer's delivered rate is a whole fraction of the TIMER, so the panel
    cannot change it. Before the fix these numbers sagged on 90/100/144/165 and
    on the sub-60 ProMotion rates: the resting breathe fell 30 -> 20, thermal
    `serious` fell 15 -> 12, and a full transition fell 60 -> 30."""
    schedule = choose_render_schedule(
        environment,
        True,
        display_link_available=False,
        gentle_motion=gentle,
        refresh_hz=panel,
    )
    assert schedule.driver is RenderDriverKind.TIMER
    assert schedule.driver_fps == render_policy.TIMER_DRIVER_FPS
    assert schedule.cadence.fps == pytest.approx(expected)
    assert _delivered_fps(monkeypatch, schedule) == pytest.approx(expected, abs=0.2)


# --- the display link: a whole fraction of the rate it negotiated ------------


# The link is only chosen at thermal nominal on mains power, so one row per
# panel. driver_fps is min(max(60, panel), 120) -- what _install_display_link
# passes to CAFrameRateRangeMake.
DISPLAY_LINK_TABLE = (
    # panel, negotiated driver, transition fps, resting breathe fps
    (60.0, 60.0, 60.0, 30.0),
    (90.0, 90.0, 45.0, 30.0),
    (100.0, 100.0, 50.0, 25.0),
    (120.0, 120.0, 60.0, 30.0),
    (144.0, 120.0, 60.0, 30.0),
    (165.0, 120.0, 60.0, 30.0),
    (240.0, 120.0, 60.0, 30.0),
)


@pytest.mark.parametrize(
    ("panel", "driver_fps", "transition_fps", "breathe_fps"),
    DISPLAY_LINK_TABLE,
    ids=[f"{row[0]:g}Hz" for row in DISPLAY_LINK_TABLE],
)
def test_the_display_link_delivers_a_whole_fraction_of_what_it_negotiated(
    monkeypatch,
    panel: float,
    driver_fps: float,
    transition_fps: float,
    breathe_fps: float,
) -> None:
    """Unchanged by the timer fix, and it must stay that way. The link's loss on
    a 90/100 Hz panel is real physics -- that driver genuinely fires at 90/100
    Hz -- not the composition defect the timer had."""
    for gentle, expected in ((False, transition_fps), (True, breathe_fps)):
        schedule = choose_render_schedule(
            RenderEnvironment(),
            True,
            display_link_available=True,
            gentle_motion=gentle,
            refresh_hz=panel,
        )
        assert schedule.driver is RenderDriverKind.DISPLAY_LINK
        assert schedule.driver_fps == pytest.approx(driver_fps)
        assert schedule.cadence.fps == pytest.approx(expected)
        assert _delivered_fps(monkeypatch, schedule) == pytest.approx(expected, abs=0.2)


# --- the general invariant, over the whole cross product ---------------------


@pytest.mark.parametrize("panel", ALL_PANELS, ids=lambda hz: f"{hz:g}Hz")
@pytest.mark.parametrize("link", (True, False), ids=("link", "timer"))
@pytest.mark.parametrize(
    ("label", "environment", "gentle", "_expected"),
    TIMER_TABLE,
    ids=[row[0] for row in TIMER_TABLE],
)
def test_no_panel_costs_a_frame_the_driver_could_have_delivered(
    panel: float,
    link: bool,
    label: str,
    environment: RenderEnvironment,
    gentle: bool,
    _expected: float,
) -> None:
    """Three properties, stated over every panel and environment at once.

    1. The named rate is the BEST the chosen driver can do under the ceiling --
       no panel may cost a frame the driver was willing to deliver.
    2. It never exceeds the ceiling; these are thermal and low-power limits and
       overshooting one is the failure the limit exists to prevent.
    3. It is an exact integer subdivision of the driver's callback rate, which
       is the only lattice that exists once the gate subsamples callbacks.
       Anything off that lattice is a frame the gate drops irregularly.
    """
    ceiling = choose_render_cadence(environment, True, gentle_motion=gentle).fps
    schedule = choose_render_schedule(
        environment,
        True,
        display_link_available=link,
        gentle_motion=gentle,
        refresh_hz=panel,
    )
    best = _best_achievable(schedule.driver_fps, ceiling)
    assert schedule.cadence.fps == pytest.approx(best), (
        f"{panel:g} Hz / {label}: driver runs at {schedule.driver_fps:g} and could "
        f"have delivered {best:g} under a {ceiling:g} ceiling, but the policy "
        f"named {schedule.cadence.fps:g}"
    )
    assert schedule.cadence.fps <= ceiling + 1e-9
    ratio = schedule.driver_fps / schedule.cadence.fps
    assert ratio == pytest.approx(round(ratio)), (
        f"{panel:g} Hz / {label}: {schedule.cadence.fps:g} is not a whole "
        f"fraction of the {schedule.driver_fps:g} Hz driver, so the gate has to "
        f"drop frames unevenly to produce it"
    )


# --- the exact case from the challenge report --------------------------------


def test_a_144hz_panel_gets_the_thirty_fps_breathe_it_asks_for(monkeypatch) -> None:
    """MacBook driving a 144 Hz external display, an agent working, the resting
    breathe. The policy asks for 30. The panel snap made it 28.8, and the driver
    snap then floored 28.8 to 20 -- measured 20.0 delivered."""
    schedule = choose_render_schedule(
        RenderEnvironment(),
        True,
        display_link_available=False,
        gentle_motion=True,
        refresh_hz=144.0,
    )
    assert schedule.cadence.fps == 30.0
    assert _delivered_fps(monkeypatch, schedule) == pytest.approx(30.0, abs=0.2)
    # The intermediate value that used to be handed to the driver snap. It is
    # off the timer's lattice (60/28.8 = 2.083), which is the whole defect.
    assert refresh_divisor_fps(144.0, 30.0) == pytest.approx(28.8)


# --- the guard against re-adoption -------------------------------------------


def test_the_schedule_never_snaps_a_cadence_to_the_panel(monkeypatch) -> None:
    """`refresh_divisor_fps` is correct about panels and must not be composed
    with the driver snap in front of it. Reinstating the call fails here."""
    calls: list[tuple] = []

    def spy(refresh_hz, target_fps):
        calls.append((refresh_hz, target_fps))
        return refresh_divisor_fps(refresh_hz, target_fps)

    monkeypatch.setattr(render_policy, "refresh_divisor_fps", spy)
    for panel in ALL_PANELS:
        for link in (True, False):
            for gentle in (True, False):
                choose_render_schedule(
                    RenderEnvironment(thermal="fair"),
                    True,
                    display_link_available=link,
                    gentle_motion=gentle,
                    refresh_hz=panel,
                )
    assert calls == [], (
        "choose_render_schedule consulted the panel divisor snap; composing it "
        "with deliverable_fps is what cut 30 fps to 20 on a 144 Hz panel"
    )


def test_the_panel_divisor_snap_itself_is_still_correct() -> None:
    """It is kept, unwired, because it is the right answer for a driver that is
    genuinely vsync-locked to the panel -- which neither driver here is."""
    assert refresh_divisor_fps(120.0, 20.0) == pytest.approx(20.0)  # exactly 1/6
    assert refresh_divisor_fps(90.0, 20.0) == pytest.approx(18.0)  # 1/5, not 1/4.5
    assert refresh_divisor_fps(144.0, 30.0) == pytest.approx(28.8)  # 1/5
    assert refresh_divisor_fps(60.0, 90.0) == 60.0  # never above the panel
    assert refresh_divisor_fps(None, 30.0) == 30.0  # no panel, no opinion
    assert refresh_divisor_fps(0.0, 30.0) == 30.0
