"""One light language: every state owns a loudness AND a rhythm.

Two defects are pinned here.

LOUDNESS. Red-orange is the dimmest saturated colour there is, so putting Ask
on the same "gentle" fade ceiling as Working made the one state that means "a
human is needed" the quietest lit thing on the strip -- 2.5x dimmer than an
agent quietly working. The ceiling exists to keep ambient breathing from being
harsh; urgency is not ambient.

MOTION. In the default blend mode idle, working and ask rendered as the same
pulse, over the same period, between the same floor and ceiling -- hue was the
only difference between "resting", "busy" and "stop". For a deuteranope an
agent going Ask changed by about dE 8, which is not a signal. About 1 man in 12
is red/green colourblind, so a light language that leans on hue alone is a
light language that does not work for them.

The colourblind simulation below is Vienot, Brettel & Mollon (1999) with
CIEDE2000 in CIELAB/D65 -- the same method used to audit this palette, kept
here so the assertion is a measurement rather than an opinion.
"""

from __future__ import annotations

import itertools
import math

import pytest

from sidepulse.colors import (
    MIN_BEAT_MS,
    MIN_CYCLE_SPEED_SECONDS,
    MIN_FLASH_CYCLE_MS,
    MODE_ASK,
    MODE_DONE,
    MODE_IDLE,
    MODE_WORKING,
    MOTION_BEAT,
    MOTION_BLINK,
    MOTION_BREATHE,
    MOTION_CHASE,
    MOTION_STEADY,
    STATE_MOTION,
    ColorSettings,
    _floor_for_state,
    _motion_segments,
    _peak_for_state,
    luminance_matched_hex,
    relative_luminance,
    state_motion,
)
from sidepulse.led_status import LedDisplayState, srgb_to_linear

STATE_MODE_KEY = {
    LedDisplayState.IDLE: MODE_IDLE,
    LedDisplayState.WORKING: MODE_WORKING,
    LedDisplayState.DONE: MODE_DONE,
    LedDisplayState.ASK: MODE_ASK,
    LedDisplayState.FAILED: MODE_ASK,
}


def _peaks(settings: ColorSettings) -> dict[LedDisplayState, str]:
    return {
        state: _peak_for_state(settings.mode_color(key), state, settings)
        for state, key in STATE_MODE_KEY.items()
    }


# --- colourblind simulation (Vienot/Brettel/Mollon + CIEDE2000) ------------


def _linear(hex_color: str) -> list[float]:
    cleaned = hex_color.lstrip("#")
    return [srgb_to_linear(int(cleaned[i : i + 2], 16) / 255.0) for i in (0, 2, 4)]


def _hex(linear: list[float]) -> str:
    from sidepulse.led_status import linear_to_srgb

    return "#" + "".join(
        f"{round(linear_to_srgb(max(0.0, min(1.0, c))) * 255.0):02X}" for c in linear
    )


def simulate_dichromacy(hex_color: str, kind: str) -> str:
    red, green, blue = _linear(hex_color)
    long_ = 17.8824 * red + 43.5161 * green + 4.11935 * blue
    medium = 3.45565 * red + 27.1554 * green + 3.86714 * blue
    short = 0.0299566 * red + 0.184309 * green + 1.46709 * blue
    if kind == "deuteranopia":
        medium = 0.494207 * long_ + 1.24827 * short
    else:  # protanopia
        long_ = 2.02344 * medium - 2.52581 * short
    return _hex(
        [
            0.080944 * long_ - 0.130504 * medium + 0.116721 * short,
            -0.0102485 * long_ + 0.0540194 * medium - 0.113615 * short,
            -0.000365294 * long_ - 0.00412163 * medium + 0.693513 * short,
        ]
    )


def _lab(hex_color: str) -> tuple[float, float, float]:
    red, green, blue = _linear(hex_color)
    x = (0.4124 * red + 0.3576 * green + 0.1805 * blue) / 0.95047
    y = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    z = (0.0193 * red + 0.1192 * green + 0.9505 * blue) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (841 / 108) * t + 4 / 29

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e_2000(first: str, second: str) -> float:
    l1, a1, b1 = _lab(first)
    l2, a2, b2 = _lab(second)
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    c_bar = (c1 + c2) / 2
    g = 0.5 * (1 - math.sqrt(c_bar**7 / (c_bar**7 + 25**7))) if c_bar else 0.5
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (a1p or b1) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (a2p or b2) else 0.0
    dlp, dcp = l2 - l1, c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    else:
        dhp = h2p - h1p - 360 if h2p - h1p > 180 else h2p - h1p + 360
    dhp_big = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp) / 2)
    l_bar, c_bar_p = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        h_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        h_bar = (h1p + h2p) / 2
    else:
        h_bar = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    t = (
        1
        - 0.17 * math.cos(math.radians(h_bar - 30))
        + 0.24 * math.cos(math.radians(2 * h_bar))
        + 0.32 * math.cos(math.radians(3 * h_bar + 6))
        - 0.20 * math.cos(math.radians(4 * h_bar - 63))
    )
    s_l = 1 + (0.015 * (l_bar - 50) ** 2) / math.sqrt(20 + (l_bar - 50) ** 2)
    s_c = 1 + 0.045 * c_bar_p
    s_h = 1 + 0.015 * c_bar_p * t
    r_t = -math.sin(math.radians(2 * 30 * math.exp(-(((h_bar - 275) / 25) ** 2)))) * (
        2 * math.sqrt(c_bar_p**7 / (c_bar_p**7 + 25**7)) if c_bar_p else 0.0
    )
    return math.sqrt(
        (dlp / s_l) ** 2
        + (dcp / s_c) ** 2
        + (dhp_big / s_h) ** 2
        + r_t * (dcp / s_c) * (dhp_big / s_h)
    )


def worst_case_separation(first: str, second: str) -> float:
    """The smallest dE any of normal, deuteranopic and protanopic vision sees."""
    return min(
        delta_e_2000(first, second),
        delta_e_2000(
            simulate_dichromacy(first, "deuteranopia"),
            simulate_dichromacy(second, "deuteranopia"),
        ),
        delta_e_2000(
            simulate_dichromacy(first, "protanopia"),
            simulate_dichromacy(second, "protanopia"),
        ),
    )


# Below this, two lights are not reliably separable at a glance on a small dim
# emitter, so hue alone may not carry a distinction across them.
CONFUSABLE_DELTA_E = 20.0


# --- loudness --------------------------------------------------------------


def test_blocked_reads_louder_than_working() -> None:
    """The locked priority, in luminance. Before this it was inverted."""
    settings = ColorSettings.defaults()
    peaks = _peaks(settings)
    working = relative_luminance(peaks[LedDisplayState.WORKING])
    for urgent in (LedDisplayState.ASK, LedDisplayState.FAILED):
        assert relative_luminance(peaks[urgent]) > working * 1.5
    # The exact numbers, so a future palette tweak has to face them.
    assert relative_luminance(peaks[LedDisplayState.ASK]) == pytest.approx(0.2429, abs=1e-3)
    assert working == pytest.approx(0.1359, abs=1e-3)


def test_loudness_follows_intent_rather_than_hue_accident() -> None:
    settings = ColorSettings.defaults()
    peaks = _peaks(settings)
    order = sorted(peaks, key=lambda state: relative_luminance(peaks[state]))
    assert order.index(LedDisplayState.IDLE) == 0
    assert order.index(LedDisplayState.WORKING) < order.index(LedDisplayState.ASK)
    assert order.index(LedDisplayState.WORKING) < order.index(LedDisplayState.FAILED)


def test_an_urgent_light_rests_where_an_ambient_one_peaks() -> None:
    """A light meaning "a human is needed" must not spend most of its cycle
    dark: the gentleness ceiling becomes the urgent state's FLOOR."""
    settings = ColorSettings.defaults()
    ask = settings.mode_color(MODE_ASK)
    resting = _floor_for_state(ask, LedDisplayState.ASK, settings)
    working_floor = _floor_for_state(
        settings.mode_color(MODE_WORKING), LedDisplayState.WORKING, settings
    )
    assert relative_luminance(resting) > relative_luminance(working_floor) * 50
    # Specifically: Ask now RESTS at the brightness it used to peak at.
    from sidepulse.colors import scale_hex_brightness

    _floor, ceiling = settings.fade_range(MODE_ASK)
    assert resting == scale_hex_brightness(ask, ceiling)


def test_an_explicit_zero_floor_still_goes_all_the_way_dark() -> None:
    """The lift raises a resting glow; it must not invent one the user
    switched off -- and an indexed `N:off` is a firmware parse error, so the
    zero floor has to stay the literal #000000."""
    settings = ColorSettings.defaults().with_fade_floor(MODE_ASK, 0.0)
    assert (
        _floor_for_state(settings.mode_color(MODE_ASK), LedDisplayState.ASK, settings)
        == "#000000"
    )


def test_luminance_matched_hex_moves_light_without_moving_hue() -> None:
    for source in ("#00E5FF", "#FF3A00", "#00FF66", "#A45CFF"):
        # Only targets the colour can actually reach; the rest is the clamp's
        # job and has its own test.
        for fraction in (0.05, 0.4, 0.95):
            target = relative_luminance(source) * fraction
            matched = luminance_matched_hex(source, target)
            assert relative_luminance(matched) == pytest.approx(target, rel=0.02)
            # A uniform LINEAR scale leaves the channel ratios -- and so the
            # hue and saturation -- exactly where they were.
            source_linear = _linear(source)
            matched_linear = _linear(matched)
            brightest = source_linear.index(max(source_linear))
            for index, channel in enumerate(source_linear):
                expected = matched_linear[brightest] * channel / source_linear[brightest]
                assert matched_linear[index] == pytest.approx(expected, abs=0.004)


def test_luminance_matched_hex_clamps_instead_of_clipping_a_channel() -> None:
    """Asking a colour for more light than it has must desaturate nothing and
    shift nothing -- it just stops at that colour's own maximum."""
    assert luminance_matched_hex("#FF3A00", 0.9) == "#FF3A00"
    assert luminance_matched_hex("#020204", 0.5) != "#FFFFFF"
    assert luminance_matched_hex("#00FF66", 0.0) == "#000000"


def test_equalising_done_against_blocked_would_break_colourblind_safety() -> None:
    """Why Done is NOT dimmed to match Ask, kept as an executable warning.

    Red and green are the pair a red/green colourblind viewer can only tell
    apart by lightness. Levelling their luminance buys a tidy ladder by making
    "finished" and "blocked" the same light for about 1 man in 12.
    """
    settings = ColorSettings.defaults()
    peaks = _peaks(settings)
    ask = peaks[LedDisplayState.ASK]
    done = peaks[LedDisplayState.DONE]
    assert worst_case_separation(done, ask) >= 15.0

    levelled = luminance_matched_hex(done, relative_luminance(ask))
    # Measured: dE 39 -> dE 16 protanopic, and worst-case 18 -> 11 -- most of
    # the way to "the same light", bought for nothing.
    assert worst_case_separation(levelled, ask) < worst_case_separation(done, ask) * 0.7


# --- motion ----------------------------------------------------------------


def test_no_two_states_share_a_confusable_hue_and_the_same_motion() -> None:
    """The core accessibility guarantee: hue is never the only channel."""
    settings = ColorSettings.defaults()
    peaks = _peaks(settings)
    cycle_ms = int(settings.effective_speed_seconds(settings.blend_mode) * 1000)
    motions = {state: state_motion(state, cycle_ms=cycle_ms) for state in peaks}

    failures = []
    for first, second in itertools.combinations(sorted(peaks, key=lambda s: s.value), 2):
        separation = worst_case_separation(peaks[first], peaks[second])
        if separation < CONFUSABLE_DELTA_E and motions[first] == motions[second]:
            failures.append(
                f"{first.value}/{second.value}: dE {separation:.1f} and both {motions[first]}"
            )
    assert not failures, "states separable by neither hue nor motion: " + "; ".join(failures)


def test_every_state_owns_a_distinct_rhythm() -> None:
    assert len(set(STATE_MOTION.values())) == len(LedDisplayState)
    assert STATE_MOTION[LedDisplayState.ASK] == MOTION_BEAT
    assert STATE_MOTION[LedDisplayState.FAILED] == MOTION_BLINK
    assert STATE_MOTION[LedDisplayState.DONE] == MOTION_STEADY
    assert STATE_MOTION[LedDisplayState.WORKING] == MOTION_CHASE
    assert STATE_MOTION[LedDisplayState.IDLE] == MOTION_BREATHE


def test_the_rendered_segments_actually_differ_per_state() -> None:
    """Not just the table -- the DSL each state produces has to differ too.

    Compared with the index and colour stripped out, so this is purely about
    shape: duration, easing and delay.
    """
    settings = ColorSettings.defaults()
    shapes = {}
    for state in LedDisplayState:
        reset, motion = _motion_segments(
            0,
            settings.mode_color(STATE_MODE_KEY[state]),
            state,
            settings,
            cycle_ms=1600,
            settle_ms=160,
            chase_delay_ms=192,
        )
        shapes[state] = (
            reset.split(" ", 1)[1] if " " in reset else "",
            motion.split(" ", 1)[1] if " " in motion else "",
        )
    assert len(set(shapes.values())) == len(LedDisplayState), shapes
    # And the two that matter most: a chase travels, a beat does not.
    assert shapes[LedDisplayState.WORKING][1].endswith("192ms")
    assert not shapes[LedDisplayState.ASK][1].endswith("192ms")
    # A beat is a fraction of the cycle, so there is real dark after it.
    assert shapes[LedDisplayState.ASK][1].startswith(f"{max(1600 // 3, MIN_BEAT_MS)}ms pulse")
    # A blink never eases.
    assert "cosine" not in "".join(shapes[LedDisplayState.FAILED])
    assert "none" in shapes[LedDisplayState.FAILED][1]


def test_the_light_language_never_flashes_faster_than_two_hertz() -> None:
    """The beat and the hard blink both need room. At the fastest cycle the
    user can dial they would run at 10Hz and 3.3Hz, so under
    MIN_FLASH_CYCLE_MS each degrades to the gentler motion it is built from.
    """
    fastest_ms = int(MIN_CYCLE_SPEED_SECONDS * 1000)
    assert fastest_ms < MIN_FLASH_CYCLE_MS
    assert state_motion(LedDisplayState.ASK, cycle_ms=fastest_ms) == MOTION_BREATHE
    assert state_motion(LedDisplayState.FAILED, cycle_ms=fastest_ms) == MOTION_STEADY
    # And at the slowest cycle the beat is still a beat, not a whole breath.
    assert state_motion(LedDisplayState.ASK, cycle_ms=10_000) == MOTION_BEAT
    # Every motion that survives repeats no faster than 2Hz.
    for cycle_ms in (300, 500, 800, 1600, 10_000):
        for state in LedDisplayState:
            motion = state_motion(state, cycle_ms=cycle_ms)
            if motion in {MOTION_BEAT, MOTION_BLINK}:
                assert cycle_ms >= MIN_FLASH_CYCLE_MS


# --- end to end ------------------------------------------------------------


def test_a_blocked_agent_beats_while_a_working_one_chases() -> None:
    from datetime import datetime, timezone

    from sidepulse.colors import program_for_snapshot
    from sidepulse.models import AgentMode, AgentStatus

    def status(provider: str, mode: AgentMode) -> AgentStatus:
        return AgentStatus(
            provider=provider,
            agent_id=provider,
            display_name=provider.title(),
            mode=mode,
            updated_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            event_name="Test",
        )

    settings = ColorSettings.defaults().with_round_robin_urgency_alert(False)
    statuses = (status("codex", AgentMode.WORKING), status("claude", AgentMode.BLOCKED_ERROR))
    _state, program = program_for_snapshot(statuses, led_count=4, colors=settings)
    motion_line = [line for line in program.splitlines() if line.startswith("0:")][-1]
    segments = motion_line.split("; ")

    working_segments = [segments[0], segments[2]]
    blocked_segments = [segments[1], segments[3]]
    # The working pair travels (a per-LED delay), the blocked pair does not.
    assert any(segment.endswith("ms") and "pulse " in segment for segment in working_segments)
    assert all(segment.rstrip().endswith("pulse") for segment in blocked_segments)
    # ...and the blocked pair beats over a fraction of the working cycle.
    blocked_ms = int(blocked_segments[0].split()[1].removesuffix("ms"))
    working_ms = int(working_segments[0].split()[1].removesuffix("ms"))
    assert blocked_ms < working_ms


def test_completion_green_is_a_sweep_not_a_resting_state() -> None:
    """2026-08-20 evening: 'a normal animation will happen but it is
    just all the LEDs return to green' -- every interactive turn ends in
    a Stop, and the strip painted the done color for two minutes after
    each one. The LIGHTS settle after COMPLETED_GLOW_SECONDS; rows,
    badge, and gauge keep the longer memory."""
    from datetime import datetime, timedelta, timezone

    from sidepulse.collector import MonitorSnapshot, aggregate_status
    from sidepulse.models import AgentMode, AgentStatus
    from sidepulse.operator_state import COMPLETED_GLOW_SECONDS
    from sidepulse.status_bar_legacy import settled_completion_display_mode

    finished = datetime(2026, 8, 20, 18, 0, 0, tzinfo=timezone.utc)
    done = AgentStatus(
        provider="claude",
        agent_id="claude:session:x",
        display_name="Claude x",
        mode=AgentMode.COMPLETED,
        updated_at=finished,
        event_name="Stop",
        session_id="x",
    )

    def snapshot(seconds_later: float) -> MonitorSnapshot:
        return MonitorSnapshot(
            aggregate=aggregate_status((done,)),
            statuses=(done,),
            stale_statuses=(),
            sources=(),
            collected_at=finished + timedelta(seconds=seconds_later),
        )

    fresh = snapshot(COMPLETED_GLOW_SECONDS - 5.0)
    assert (
        settled_completion_display_mode(AgentMode.COMPLETED, fresh)
        == AgentMode.COMPLETED
    )
    settled = snapshot(COMPLETED_GLOW_SECONDS + 5.0)
    assert (
        settled_completion_display_mode(AgentMode.COMPLETED, settled)
        == AgentMode.IDLE_READY
    )
    # Non-completed aggregates pass through untouched.
    assert (
        settled_completion_display_mode(AgentMode.WORKING, settled)
        == AgentMode.WORKING
    )
