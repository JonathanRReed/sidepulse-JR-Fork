"""The display link must be asked for a rate the panel can actually produce.

A display link does not manufacture frames. It hands back scans the panel has
already produced, so on a panel running at R Hz the only rates it can fire at
are R/n -- 144, 72, 48, 36, 28.8 on a 144 Hz panel, and nothing between them.

``_install_display_link`` used to ask for ``CAFrameRateRangeMake(60, m, m)``
with ``m = min(max(60, panel), 120)``, and ``driver_callback_fps`` reported that
same clamp as fact. On the panels where the clamp is not a member of {R/n} the
two parted company and every number downstream was about a rate that does not
exist:

    144 Hz -> asked for 120, could only get 144/2 = 72,  assumed 120
    165 Hz -> asked for 120, could only get 165/2 = 82.5, assumed 120
     24 Hz -> asked for 60,  no rate at or above 60 exists at all, assumed 60

The gate in ``redraw_`` turns callbacks into frames by refusing everything
inside half a DRIVER period of the last presented frame. Feed it a driver period
that is a fiction and it subsamples against a clock nothing is ticking to: on a
144 Hz panel it named 60 fps and put 72 on the screen -- 20% over a ceiling --
while naming 30 for the resting breathe and putting 24 on the screen.

Nothing here can run on a real 144 Hz panel, so the physical rate is MODELLED,
once, in ``_achievable_rates``: the set {panel/n}. Every claim below is a claim
about that model plus real production code -- the real schedule, the real
negotiation, the real gate. The model itself is the one hardware-unverified
step, and it is the same assumption the API documents.
"""

from __future__ import annotations

import time as _time
from types import SimpleNamespace

import pytest

from sidepulse.presentation_policy import MotionClass
from sidepulse.render_policy import (
    DISPLAY_LINK_CEILINGS,
    DISPLAY_LINK_MAX_FPS,
    RenderDriverKind,
    RenderEnvironment,
    choose_render_cadence,
    choose_render_schedule,
    display_link_fps,
)
from sidepulse.screen_bar_pipeline import ColorSample, SamplePair

LED_COUNT = 8
_LIT = ((0.0, 0.9, 1.0, 1.0),) * LED_COUNT

# Every panel the brief names. 24 and 48 are below the old request's floor of
# 60; 144 and 165 are above the cap and are not whole fractions of it; 60, 90,
# 100, 120 and 240 already worked and must keep working byte for byte.
PANELS = (24.0, 48.0, 60.0, 90.0, 100.0, 120.0, 144.0, 165.0, 240.0)

# A ProMotion panel reports 120 for maximumFramesPerSecond whatever it is
# currently running at, and the hardware is free to walk these rates. The
# request is the only thing that pins which one a link sees.
PROMOTION_ADAPTIVE = (24.0, 30.0, 40.0, 48.0, 60.0, 80.0, 120.0)


def _achievable_rates(panel: float) -> tuple[float, ...]:
    """The rates a link on a panel at ``panel`` Hz can physically fire at.

    Deliberately derived here from first principles rather than by calling
    ``achievable_display_rates``, so this file states the physics independently
    of the code it is judging.
    """
    return tuple(panel / divisor for divisor in range(1, 400))


def _rates_inside(frame_range, panel: float) -> tuple[float, ...]:
    """Which of the panel's real rates a requested range actually admits."""
    minimum, maximum, _preferred = frame_range
    return tuple(
        rate
        for rate in _achievable_rates(panel)
        if minimum - 1e-9 <= rate <= maximum + 1e-9
    )


# --- doubles for the AppKit boundary, with the panel rate injectable ---------


class _Clock:
    """A monotonic clock that can be stepped, delegating the rest of `time`."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, name: str):
        return getattr(_time, name)


class _Link:
    def __init__(self) -> None:
        self.frame_ranges: list[tuple[float, float, float]] = []
        self.run_loops: list[tuple[object, object]] = []
        self.invalidated = 0

    def targetTimestamp(self) -> float:
        return 10.0

    def setPreferredFrameRateRange_(self, frame_range) -> None:
        self.frame_ranges.append(frame_range)

    def addToRunLoop_forMode_(self, run_loop, mode) -> None:
        self.run_loops.append((run_loop, mode))

    def invalidate(self) -> None:
        self.invalidated += 1


class _View:
    def __init__(self) -> None:
        self.links: list[_Link] = []
        self.sample_fps: list[float] = []

    def displayLinkWithTarget_selector_(self, _target, _selector) -> _Link:
        link = _Link()
        self.links.append(link)
        return link

    def setRenderFps_(self, fps: float) -> None:
        self.sample_fps.append(float(fps))

    def setPresentationColors_(self, _colors) -> None:
        pass

    def setNeedsDisplay_(self, _flag) -> None:
        pass


class _Screen:
    """The injectable panel. Mutate `hz` to simulate a screen change."""

    def __init__(self, hz: float) -> None:
        self.hz = hz

    def maximumFramesPerSecond(self) -> float:
        return self.hz


class _ScreenClass:
    def __init__(self, screen: _Screen) -> None:
        self.screen = screen

    def mainScreen(self) -> _Screen:
        return self.screen


class _RunLoop:
    def addTimer_forMode_(self, _timer, _mode) -> None:
        pass


class _RunLoopClass:
    def currentRunLoop(self) -> _RunLoop:
        return _RunLoop()


def _install(monkeypatch, panel: float, clock: _Clock):
    from sidepulse import virtual_device

    screen = _Screen(panel)
    monkeypatch.setattr(virtual_device, "NSScreen", _ScreenClass(screen))
    monkeypatch.setattr(virtual_device, "NSRunLoop", _RunLoopClass())
    monkeypatch.setattr(virtual_device, "time", clock)
    monkeypatch.setattr(
        virtual_device,
        "Quartz",
        SimpleNamespace(
            CAFrameRateRangeMake=lambda minimum, maximum, preferred: (
                float(minimum),
                float(maximum),
                float(preferred),
            )
        ),
        raising=False,
    )
    return virtual_device, screen


def _linked_device(monkeypatch, panel: float, *, gentle: bool, clock: _Clock | None = None):
    """One device on a `panel` Hz screen with the display link really installed."""
    clock = clock or _Clock()
    virtual_device, screen = _install(monkeypatch, panel, clock)
    device = virtual_device.VirtualStatusDevice.alloc().init()
    device.window = None
    device.view = _View()
    device._is_surface_visible = lambda: True
    device._runtime_environment = lambda **_kwargs: RenderEnvironment()
    device._sampler_command = SimpleNamespace(
        motion=MotionClass.CONTINUOUS if gentle else MotionClass.FINITE,
        next_visual_change_at=None,
    )
    device._animation_active = True
    device._refresh_render_cadence(True, force=True)
    assert device._render_schedule.driver is RenderDriverKind.DISPLAY_LINK
    return device, screen, virtual_device, clock


def _delivered_fps(monkeypatch, device, virtual_device, clock, callback_hz, seconds=16.0):
    """Frames the real gate lets through, given callbacks at `callback_hz`.

    `callback_hz` is the rate the PANEL will really deliver, not the rate the
    policy hoped for. Zero jitter, so this measures deterministic loss.
    """
    frames = {"n": 0}
    original = virtual_device.display_colors_for_tick

    def counting(*args, **kwargs):
        frames["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(virtual_device, "display_colors_for_tick", counting)
    start = clock.now
    for index in range(int(callback_hz * seconds)):
        clock.now = start + index / callback_hz
        generation = device._presentation_generation
        device._sample_buffer.publish(
            SamplePair(
                ColorSample(generation, clock.now - 0.001, _LIT),
                ColorSample(generation, clock.now, _LIT),
            )
        )
        device.redraw_(None)
    return frames["n"] / seconds


# --- 1. the rate asked for is a rate that exists -----------------------------


@pytest.mark.parametrize("panel", PANELS, ids=lambda hz: f"{hz:g}Hz")
def test_the_negotiated_rate_is_one_the_panel_can_actually_produce(panel: float) -> None:
    """The whole defect in one assertion.

    Before: 144 Hz negotiated 120, and 144/120 = 1.2 -- there is no such scan.
    A 24 Hz panel negotiated 60, which is 2.5x more frames than the hardware
    emits. Both numbers then travelled into the gate as facts.
    """
    rate = display_link_fps(panel)
    divisor = panel / rate
    assert divisor == pytest.approx(round(divisor)), (
        f"{panel:g} Hz panel: negotiated {rate:g}, which is panel/{divisor:g} -- "
        "a display link can only be handed whole scans"
    )
    assert rate <= DISPLAY_LINK_MAX_FPS + 1e-9
    assert rate > 0.0


def test_an_unreadable_panel_still_gets_a_usable_rate() -> None:
    """No screen to ask is not a reason to install no driver."""
    assert display_link_fps(None) == 60.0
    assert display_link_fps(0.0) == 60.0


# --- 2. the request and the assumption are one number ------------------------


@pytest.mark.parametrize("panel", PANELS, ids=lambda hz: f"{hz:g}Hz")
@pytest.mark.parametrize("gentle", (False, True), ids=("transition", "breathe"))
def test_the_requested_range_admits_exactly_the_rate_the_policy_assumed(
    monkeypatch, panel: float, gentle: bool
) -> None:
    """A range is only useful if you can predict what it yields.

    ``CAFrameRateRangeMake(60, 120, 120)`` admits 120 AND 60 on a 120 Hz panel,
    admits only 72 on a 144 Hz panel, and admits nothing at all on a 48 Hz one.
    Three different failure modes, one of which is a request the hardware cannot
    honour. Asking for a single achievable number removes all three.
    """
    device, _screen, _virtual_device, _clock = _linked_device(
        monkeypatch, panel, gentle=gentle
    )
    schedule = device._render_schedule
    link = device.view.links[0]

    assert len(device.view.links) == 1
    assert link.frame_ranges == [
        (schedule.driver_fps, schedule.driver_fps, schedule.driver_fps)
    ], "the link must be asked for exactly the rate the schedule assumed"
    assert _rates_inside(link.frame_ranges[0], panel) == pytest.approx(
        (schedule.driver_fps,)
    ), (
        f"{panel:g} Hz: the requested range admits "
        f"{[f'{r:g}' for r in _rates_inside(link.frame_ranges[0], panel)]}, so the "
        "rate the gate quantises against is a guess"
    )


# --- 3. what actually reaches the screen -------------------------------------


# panel, negotiated link rate, transition fps, resting-breathe fps.
# Named AND delivered -- the point of the fix is that those are one column.
DELIVERED_TABLE = (
    (24.0, 24.0, 24.0, 24.0),
    (48.0, 48.0, 48.0, 24.0),
    (60.0, 60.0, 60.0, 30.0),
    (90.0, 90.0, 45.0, 30.0),
    (100.0, 100.0, 50.0, 25.0),
    (120.0, 120.0, 60.0, 30.0),
    (144.0, 48.0, 48.0, 24.0),
    (165.0, 55.0, 55.0, 27.5),
    (240.0, 120.0, 60.0, 30.0),
)


@pytest.mark.parametrize(
    ("panel", "link_fps", "transition_fps", "breathe_fps"),
    DELIVERED_TABLE,
    ids=[f"{row[0]:g}Hz" for row in DELIVERED_TABLE],
)
def test_the_screen_gets_the_rate_the_policy_names(
    monkeypatch,
    panel: float,
    link_fps: float,
    transition_fps: float,
    breathe_fps: float,
) -> None:
    """Named == delivered, on every panel, at both ceilings the link is used at.

    Before, on a 144 Hz panel: named 60, delivered 72 -- 20% over a ceiling that
    exists to be a ceiling -- and named 30, delivered 24 for the resting
    breathe. On 165 Hz: named 60, delivered 41.25. On 24 Hz: named 60, delivered
    24.
    """
    for gentle, expected in ((False, transition_fps), (True, breathe_fps)):
        clock = _Clock()
        device, _screen, virtual_device, clock = _linked_device(
            monkeypatch, panel, gentle=gentle, clock=clock
        )
        schedule = device._render_schedule
        assert schedule.driver_fps == pytest.approx(link_fps)
        assert schedule.cadence.fps == pytest.approx(expected)
        # The callbacks arrive at the rate the PANEL produces, which is the one
        # rate the requested range admits -- not at the rate anyone hoped for.
        physical = _rates_inside(device.view.links[0].frame_ranges[0], panel)
        assert len(physical) == 1
        delivered = _delivered_fps(
            monkeypatch, device, virtual_device, clock, physical[0]
        )
        assert delivered == pytest.approx(expected, abs=0.2), (
            f"{panel:g} Hz / {'breathe' if gentle else 'transition'}: policy names "
            f"{schedule.cadence.fps:g}, screen gets {delivered:g}"
        )


@pytest.mark.parametrize(
    ("panel", "_link", "transition_fps", "breathe_fps"),
    DELIVERED_TABLE,
    ids=[f"{row[0]:g}Hz" for row in DELIVERED_TABLE],
)
def test_no_panel_is_painted_faster_than_the_policy_allowed(
    panel: float, _link: float, transition_fps: float, breathe_fps: float
) -> None:
    """Ceilings are the thing the ceiling is for.

    A 144 Hz panel used to be painted at 72 under a 60 ceiling because the
    driver period the gate held against was a period nothing was ticking at.
    """
    for gentle, expected in ((False, transition_fps), (True, breathe_fps)):
        ceiling = choose_render_cadence(
            RenderEnvironment(), True, gentle_motion=gentle
        ).fps
        assert expected <= ceiling + 1e-9


# --- 4. a stale panel reading is a wrong panel reading -----------------------


def test_a_screen_change_negotiates_for_the_new_panel_not_the_cached_one(
    monkeypatch,
) -> None:
    """`_panel_refresh_hz` caches for five seconds; a screen change ends that.

    Without the cache drop in `screenDidChange_` the rebuilt link is negotiated
    and quantised for the display we just left. Dragging from the 60 Hz built-in
    to a 120 Hz external kept 60 as the assumed driver rate for up to five
    seconds, so the gate held for half a 60 Hz period while callbacks arrived at
    120 -- every one of them presented, 120 fps under a 60 fps ceiling.
    """
    clock = _Clock()
    device, screen, virtual_device, clock = _linked_device(
        monkeypatch, 60.0, gentle=False, clock=clock
    )
    assert device.view.links[0].frame_ranges == [(60.0, 60.0, 60.0)]

    screen.hz = 120.0
    clock.now += 0.25  # well inside the five-second cache window
    device.screenDidChange_(None)

    assert len(device.view.links) == 2
    assert device.view.links[0].invalidated == 1
    assert device._render_schedule.driver_fps == pytest.approx(120.0)
    assert device.view.links[1].frame_ranges == [(120.0, 120.0, 120.0)]
    delivered = _delivered_fps(monkeypatch, device, virtual_device, clock, 120.0)
    assert delivered == pytest.approx(60.0, abs=0.2)


def test_a_link_registered_for_the_old_panel_is_replaced_not_reused(
    monkeypatch,
) -> None:
    """`screenDidChange_` is not the only way a panel rate moves.

    `_panel_refresh_hz` re-reads every five seconds, so the policy can pick up a
    new rate with no notification at all. The cadence refresh then decided the
    driver was "already installed" on identity alone -- link present, no timer
    -- and kept a link registered for the display we were no longer on, with
    the gate holding against the new rate's period.
    """
    clock = _Clock()
    device, screen, virtual_device, clock = _linked_device(
        monkeypatch, 60.0, gentle=False, clock=clock
    )
    assert device.view.links[0].frame_ranges == [(60.0, 60.0, 60.0)]

    screen.hz = 144.0
    clock.now += 6.0  # past the cache window, and no screen-change notification
    device._refresh_render_cadence(True, force=True)

    assert len(device.view.links) == 2, (
        "the cadence refresh kept a link registered for the 60 Hz panel while "
        "quantising for the new one"
    )
    assert device.view.links[0].invalidated == 1
    assert device.view.links[1].frame_ranges == [(48.0, 48.0, 48.0)]
    assert device._render_schedule.driver_fps == pytest.approx(48.0)


def test_a_motion_class_flip_keeps_the_link_it_has(monkeypatch) -> None:
    """The negotiated rate depends on the panel, never on the cadence.

    So the resting breathe and a transition share one link. Deriving the
    negotiated rate from the ceiling instead would tear down and rebuild the
    driver on every CONTINUOUS/FINITE flip, on every panel including 60 Hz.
    """
    clock = _Clock()
    device, _screen, _virtual_device, clock = _linked_device(
        monkeypatch, 144.0, gentle=False, clock=clock
    )
    assert device._render_schedule.cadence.fps == pytest.approx(48.0)

    device._sampler_command = SimpleNamespace(
        motion=MotionClass.CONTINUOUS, next_visual_change_at=None
    )
    clock.now += 6.0
    device._refresh_render_cadence(True, force=True)

    assert len(device.view.links) == 1
    assert device.view.links[0].invalidated == 0
    assert device._render_schedule.cadence.fps == pytest.approx(24.0)
    assert device._render_schedule.driver_fps == pytest.approx(48.0)


# --- 5. the assumption the selection rule rests on ---------------------------


def test_the_display_link_is_only_ever_asked_for_these_two_ceilings() -> None:
    """`display_link_fps` picks the panel rate that serves DISPLAY_LINK_CEILINGS
    best, which is only meaningful if that really is the whole set.

    A guard, not a regression test: it pins the premise rather than the change.
    If a thermal or low-power tier ever becomes reachable on this driver, the
    negotiated rate stops being the right one and this fails first.
    """
    reachable = set()
    for thermal in ("nominal", "fair", "serious", "critical"):
        for low_power in (False, True):
            for visible in (False, True):
                for asleep in (False, True):
                    for gentle in (False, True):
                        for active in (False, True):
                            environment = RenderEnvironment(
                                visible=visible,
                                display_asleep=asleep,
                                low_power=low_power,
                                thermal=thermal,
                            )
                            schedule = choose_render_schedule(
                                environment,
                                active,
                                display_link_available=True,
                                gentle_motion=gentle,
                                refresh_hz=1000.0,  # never itself a ceiling
                            )
                            if schedule.driver is RenderDriverKind.DISPLAY_LINK:
                                reachable.add(
                                    choose_render_cadence(
                                        environment, active, gentle_motion=gentle
                                    ).fps
                                )
    assert reachable == set(DISPLAY_LINK_CEILINGS)


# --- 6. adaptive panels ------------------------------------------------------


def test_an_adaptive_panel_cannot_wander_out_from_under_the_policy(
    monkeypatch,
) -> None:
    """ProMotion reports 120 whatever it is currently running at.

    So the reported number says nothing about the rate a link will see, and a
    range that admits several of the panel's rates is a rate nobody knows. The
    old request admitted 60, 80 and 120 out of the adaptive set and the policy
    assumed 120; a pinned request admits one.
    """
    for gentle in (False, True):
        device, _screen, _virtual_device, _clock = _linked_device(
            monkeypatch, 120.0, gentle=gentle
        )
        requested = device.view.links[0].frame_ranges[0]
        admitted = [
            rate
            for rate in PROMOTION_ADAPTIVE
            if requested[0] - 1e-9 <= rate <= requested[1] + 1e-9
        ]
        assert admitted == [device._render_schedule.driver_fps], (
            f"the request {requested} lets a ProMotion panel deliver any of "
            f"{admitted} while the policy quantises against "
            f"{device._render_schedule.driver_fps:g}"
        )


@pytest.mark.parametrize("rate", PROMOTION_ADAPTIVE, ids=lambda hz: f"{hz:g}Hz")
def test_every_adaptive_rate_is_itself_negotiable_if_it_is_ever_reported(
    rate: float,
) -> None:
    """If a panel ever reports one of these as its maximum -- an external
    variable-refresh display does -- the negotiated rate must be that rate, not
    a floor of 60 invented on its behalf."""
    negotiated = display_link_fps(rate)
    assert negotiated == pytest.approx(rate)
    divisor = rate / negotiated
    assert divisor == pytest.approx(round(divisor))
