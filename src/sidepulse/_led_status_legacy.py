from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .device_writer import (
    DEFAULT_FILE_NAME,
    POWER_UP_FILE_NAME,
    DeviceWriteError,
    resolve_target_path,
    write_led_program,
)
from .models import AgentMode, AgentStatus

if TYPE_CHECKING:
    # Deferred to avoid a circular import at runtime -- colors.py imports
    # from this module. Only used for the sync_snapshot() type hint below.
    from .colors import ColorSettings


class LedDisplayState(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    DONE = "done"
    ASK = "ask"
    FAILED = "failed"


LED_STATE_LABELS: dict[LedDisplayState, str] = {
    LedDisplayState.IDLE: "Idle",
    LedDisplayState.WORKING: "Working",
    LedDisplayState.DONE: "Done",
    LedDisplayState.ASK: "Ask",
    LedDisplayState.FAILED: "Failed",
}


ASK_AMBER = "#FF3A00"
WORKING_CYAN = "#00E5FF"
DONE_GREEN = "#00FF66"
IDLE_DIM = "#020204"
# The low-battery reminder: a deliberately CALM slow red breathe -- "plug
# me in sometime soon", not an alarm. A charge reminder that strobes is a
# nagging light; one long 3.6s breath reads as patient.
LOW_BATTERY_RED = "#E01010"
LOW_BATTERY_BREATH_MS = 3600
# 240, not 60: every reassert physically rewrites LEDS.LED and the
# firmware restarts its loop from line 1 -- a visible mid-breath hitch.
# Remounts are caught by device discovery immediately anyway; this
# timer is only the backstop for a firmware that lost its file quietly.
LED_REASSERT_SECONDS = 240.0
DEVICE_LED_COUNTS = {
    "sidepulsedot": 2,
    "sidepulsepro": 8,
}


@dataclass(frozen=True)
class LedStatusWrite:
    state: LedDisplayState
    target: Path | None
    program: str
    changed: bool
    error: str | None = None

    @property
    def label(self) -> str:
        return LED_STATE_LABELS[self.state]


def display_state_for_mode(mode: AgentMode) -> LedDisplayState:
    if mode in {AgentMode.WAITING_FOR_INPUT, AgentMode.BLOCKED_ERROR}:
        return LedDisplayState.ASK
    if mode in {
        AgentMode.WORKING,
        AgentMode.TOOL_RUNNING,
        AgentMode.LONG_TASK_PROGRESS,
    }:
        return LedDisplayState.WORKING
    if mode == AgentMode.COMPLETED:
        return LedDisplayState.DONE
    return LedDisplayState.IDLE


def scale_hex_brightness(hex_color: str, fraction: float) -> str:
    """Scale a hex color's RGB channels by ``fraction`` (0.0-1.0), preserving
    hue. Used to compute a pulse's floor/ceiling brightness relative to its
    own configured color -- independent of the separate device-wide
    ``brightness`` dimmer applied by ``apply_brightness()``.
    """
    fraction = max(0.0, min(1.0, float(fraction)))
    cleaned = hex_color.lstrip("#")
    try:
        red = int(cleaned[0:2], 16)
        green = int(cleaned[2:4], 16)
        blue = int(cleaned[4:6], 16)
    except (ValueError, IndexError):
        return hex_color
    return f"#{round(red * fraction):02X}{round(green * fraction):02X}{round(blue * fraction):02X}"


# --- What a palette hex MEANS ----------------------------------------------
# Every hex in this app -- the mode colours, the identity palette, the signal
# styles, the swatch in the Colors window -- is a gamma-encoded sRGB code.
# That is not a convention we chose; it is what NSColorWell hands back and what
# colors.oklch_hex writes out. So the *meaning* of "#00E5FF" is fixed: it is
# the light an sRGB display emits for that code, and nothing else.
#
# The bug this pair of functions exists to close is that the two surfaces did
# not agree on that. The same code produced two different amounts of light:
#
#   LED strip   light proportional to (code/255)**1.0   (linear PWM)
#   Screen Bar  light proportional to (code/255)**1.89  (a per-channel tone
#                                                        map, then the panel)
#
# so idle emitted 12.9x more light on the strip than on screen relative to
# full, #FF3A00 read red on one and orange on the other, and a "50% fade" was
# a 50% breath on the strip and a 21% breath on screen. These two functions are
# the only place the app crosses between code and light. They are the exact IEC
# 61966-2-1 piecewise curve, not the 2.2 power approximation -- the
# approximation is off by enough in the shadows to move a drive code, and drive
# codes are what the owner calibrated against.


def srgb_to_linear(value: float) -> float:
    """Gamma-encoded sRGB (0.0-1.0) -> relative linear light (0.0-1.0)."""
    value = float(value)
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def linear_to_srgb(value: float) -> float:
    """Relative linear light (0.0-1.0) -> gamma-encoded sRGB (0.0-1.0)."""
    value = max(0.0, min(1.0, float(value)))
    if value <= 0.0031308:
        return value * 12.92
    return 1.055 * (value ** (1 / 2.4)) - 0.055


# --- The strip's own transfer, and the one number to correct ---------------
# ASSUMPTION, NOT MEASUREMENT. LEDS_FORMAT.md documents the brightness command
# as "Brightness scales the RGB values" and never mentions gamma anywhere in
# the DSL spec, which is the signature of a controller that PWMs the byte
# directly: light proportional to (code/255)**1.0. The owner's own calibration
# datum agrees -- codes (255, 97, 255) look neutral white on his strip, which
# under linear PWM means a green die about 2.6x more efficient than red/blue
# (ordinary for a cheap RGB LED) and under an sRGB-decoding firmware would mean
# 8.4x (implausible for any real emitter).
#
# One physical test settles it: drive #808080 and hold the strip beside a
# #808080 screen patch. If the strip is much brighter than the patch, PWM is
# linear and 1.0 is right. If they match, the firmware decodes sRGB itself and
# this becomes 2.4 -- at which point every transform below collapses to
# identity on its own, with no other edit anywhere.
STRIP_CODE_TO_LIGHT_EXPONENT = 1.0
# 8-bit linear PWM cannot represent an sRGB shadow: nominal #020204 is 0.06% of
# full light, which rounds to drive code 0. "Lit" is a state the user reads off
# this surface, so a lit LED never goes dark -- it bottoms out one code above
# off. The cost is that the strip's dimmest ember stays a few times brighter
# than the screen's; the alternative is idle silently going black on hardware.
STRIP_MIN_LIT_DRIVE = 1
# ...but a floor is only honest if it can hold its HUE. At drive 1-2 the
# physical dies dominate the math: green emits several times more light
# per code than red or blue, so "barely-visible white" #010101 renders as
# a clearly GREEN glow ("why is the SidePulse green when it should be
# off", 2026-08-20, photographed). A whole LED whose brightest computed
# drive lands below this threshold cannot say its own color -- it goes
# honestly dark instead of lying in green. Colors with at least one
# channel at or above it keep the classic floor behavior.
STRIP_HUE_HOLDING_DRIVE = 3
# The fidelity floor for colors that are MEANT to be seen. Between
# drives 3 and ~13 the strip has so few PWM steps that a ratio like
# teal's collapses into whatever the rounding leaves (measured live:
# nominal #085240 -- clearly green-dominant -- landed at drives
# (1, 6, 11) and read blue beside the Screen Bar's faithful render;
# "why is the color on the sidepulse different from the screenbar",
# 2026-08-21). A color whose NOMINAL intent carries real chroma is
# lifted, in the light domain so the calibrated ratio holds exactly,
# until its peak drive reaches this level. Whisper intents (nominal
# peak below STRIP_CHROMA_INTENT_CODE) keep the honest-black crush --
# the fix for the green-glow arc stays intact.
STRIP_HUE_READABLE_DRIVE = 14
STRIP_CHROMA_INTENT_CODE = 24

MIN_CHANNEL_GAIN = 0.3
MAX_CHANNEL_GAIN = 1.5
DEFAULT_CHANNEL_GAIN = 1.0
NEUTRAL_CHANNEL_GAINS = (DEFAULT_CHANNEL_GAIN, DEFAULT_CHANNEL_GAIN, DEFAULT_CHANNEL_GAIN)

_HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
_BRIGHTNESS_RE = re.compile(r"(?i)\bbrightness[ \t]+(\d{1,3})\b")


def strip_drive_code(code: int, gain: float = 1.0) -> int:
    """One nominal sRGB code -> the byte that makes the strip emit that light.

    This is the whole reconciliation in four lines. Decode the code to the
    light it means, apply the channel's calibration gain to that LIGHT (which
    is what a white balance is a statement about), then encode for the strip's
    own transfer rather than the screen's.

    The gain arithmetic is deliberately unchanged from what the owner tuned
    against: under linear PWM the drive byte *is* the light, so ``light *
    gain`` and the old ``code * gain`` are the same multiply, and at the
    calibration reference (white, every channel at full) this still writes the
    exact byte he matched by eye -- 255 * 1.0 * 0.380417 = 97. What is new is
    that the ratio now holds at *every* level instead of only at full scale:
    drive_green/drive_red is exactly the gain all the way down the fade, which
    is the promise a white balance actually makes.
    """
    try:
        code = int(code)
    except (TypeError, ValueError):
        return 0
    code = max(0, min(255, code))
    if code <= 0:
        return 0
    light = srgb_to_linear(code / 255.0) * max(0.0, float(gain))
    if light <= 0.0:
        return 0
    drive = 255.0 * (light ** (1.0 / STRIP_CODE_TO_LIGHT_EXPONENT))
    return max(STRIP_MIN_LIT_DRIVE, min(255, round(drive)))


def _strip_drive_float(code: int, gain: float) -> float:
    """strip_drive_code before rounding/clamping -- the true ratio."""
    code = max(0, min(255, int(code)))
    if code <= 0:
        return 0.0
    light = srgb_to_linear(code / 255.0) * max(0.0, float(gain))
    if light <= 0.0:
        return 0.0
    return 255.0 * (light ** (1.0 / STRIP_CODE_TO_LIGHT_EXPONENT))


def apply_strip_transfer_to_hex(hex_color: str, gains: tuple[float, float, float]) -> str:
    """``strip_drive_code`` over one hex literal, with the fidelity floor."""
    cleaned = hex_color.lstrip("#")
    try:
        channels = (
            int(cleaned[0:2], 16),
            int(cleaned[2:4], 16),
            int(cleaned[4:6], 16),
        )
    except (ValueError, IndexError):
        return hex_color
    floats = [
        _strip_drive_float(value, gain) for value, gain in zip(channels, gains)
    ]
    peak = max(floats)
    if peak <= 0.0:
        return "#000000"
    nominal_peak = max(channels)
    saturated = (
        nominal_peak > 0
        and (nominal_peak - min(channels)) >= nominal_peak * 0.25
    )
    if nominal_peak >= STRIP_CHROMA_INTENT_CODE and saturated:
        # A COLOR meant to be seen: lift the whole triplet, ratio intact,
        # until its hue survives 8-bit PWM quantization. Grays are
        # exempt -- they have no hue to protect, and the white-balance
        # ratio is finest at the unlifted drive.
        if peak < STRIP_HUE_READABLE_DRIVE:
            scale = STRIP_HUE_READABLE_DRIVE / peak
            floats = [value * scale for value in floats]
    elif peak < STRIP_HUE_HOLDING_DRIVE:
        # A whisper: too dim to hold a hue, and not worth lifting -- the
        # die imbalance would paint it green.
        return "#000000"
    red, green, blue = (
        max(STRIP_MIN_LIT_DRIVE, min(255, round(value))) if value > 0.0 else 0
        for value in floats
    )
    return f"#{red:02X}{green:02X}{blue:02X}"


def apply_strip_transfer_to_program(
    program: str, gains: tuple[float, float, float] = NEUTRAL_CHANNEL_GAINS
) -> str:
    """The strip's last step: nominal sRGB program text -> drive bytes.

    Applied once, at the write boundary, on the finished program -- deliberately
    the same shape and the same place as the channel gain it replaces, so
    everything upstream (fade floors and ceilings, resting glow, idle dimming,
    night warmth, relay phase) keeps operating on nominal colours that mean
    what the Colors window says they mean.

    The ``brightness`` command is rewritten too, and it has to be: the firmware
    multiplies the drive bytes by ``N/255``, so on this surface brightness is a
    scale on LIGHT, while the Screen Bar's engine multiplies the encoded code.
    Decoding N as well is what makes "half brightness" the same amount of dim
    on both surfaces instead of 50% on one and 21% on the other. (sRGB is very
    nearly a power law, so decoding the colour and the scalar separately gives
    the same product as decoding once at the end, to within a rounding step.)
    """
    program = _HEX_COLOR_RE.sub(
        lambda match: apply_strip_transfer_to_hex(match.group(0), gains), program
    )
    return _BRIGHTNESS_RE.sub(
        lambda match: f"brightness {strip_drive_code(int(match.group(1)))}", program
    )


def normalize_channel_gain(value: float | None) -> float:
    if value is None:
        return DEFAULT_CHANNEL_GAIN
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT_CHANNEL_GAIN
    return max(MIN_CHANNEL_GAIN, min(MAX_CHANNEL_GAIN, value))


def apply_channel_gain_to_hex(hex_color: str, gains: tuple[float, float, float]) -> str:
    """Scale a hex color's channels independently by ``(red, green, blue)``
    gain factors -- a per-*channel* correction, unlike scale_hex_brightness's
    single uniform fraction. Used to compensate for a specific physical
    LED's own die imbalance (commonly an over-bright green die) so a color
    reads perceptually closer to its intended hue on that hardware, without
    changing the "true" hex value shown anywhere in the UI.

    A plain multiply on the code, exactly as the owner tuned it. Under the
    strip's linear PWM the drive byte IS the light, so this multiply already
    lands in the right domain and the stored 0.38 is a correct linear-light
    white balance -- see strip_drive_code, which keeps this same arithmetic and
    only fixes what the code means before it gets here.

    Kept encoded-domain and unchanged on purpose. This function is still the
    Screen Bar's and the battery reminder's write boundary (status_bar.py and
    battery.py call it directly), and a surface that has no die imbalance to
    correct must not have this applied at all -- see the Screen Bar note in
    strip_drive_code's module comment.
    """
    cleaned = hex_color.lstrip("#")
    try:
        red = int(cleaned[0:2], 16)
        green = int(cleaned[2:4], 16)
        blue = int(cleaned[4:6], 16)
    except (ValueError, IndexError):
        return hex_color
    red_gain, green_gain, blue_gain = gains

    def channel(value: int, gain: float) -> int:
        return max(0, min(255, round(value * gain)))

    return f"#{channel(red, red_gain):02X}{channel(green, green_gain):02X}{channel(blue, blue_gain):02X}"


def apply_resting_glow_to_program(program: str, fraction: float) -> str:
    """Replaces every fully-dark token (the word `off`, the literal
    #000000) with a faint gray ember so unlit LEDs stay barely visible
    -- "the dots appear more physical". Applied per device, BEFORE
    channel gains so calibration shapes the ember too. fraction <= 0 is
    a no-op (classic full dark)."""
    if fraction <= 0.004:
        return program
    level = max(1, min(90, round(255 * fraction)))
    glow_hex = f"#{level:02X}{level:02X}{level:02X}"
    import re as _re

    program = program.replace("#000000", glow_hex)
    return _re.sub(r"\boff\b", glow_hex, program)


def apply_channel_gain_to_program(program: str, gains: tuple[float, float, float]) -> str:
    """Rewrites every literal ``#RRGGBB`` color in a rendered LED DSL
    program through apply_channel_gain_to_hex, leaving everything else
    (durations, easings, delays, ``off``, ``repeat``, ``brightness N``)
    untouched. This is intentionally a post-processing pass over the final
    program text rather than a change to how colors.py/led_status.py
    compute colors -- it applies uniformly to every blend mode and
    animation style without needing to touch any of that rendering logic,
    and callers that don't want it (the Screen Bar's on-screen preview,
    which has no hardware LED die to compensate for) simply never call it.
    """
    if gains == NEUTRAL_CHANNEL_GAINS:
        # Fast path: skip the regex pass entirely when there's nothing to
        # correct, so the common (uncalibrated) case is byte-identical to
        # before this existed -- no risk to the write-dedup logic that
        # compares against the exact previously-written string.
        return program
    return _HEX_COLOR_RE.sub(lambda match: apply_channel_gain_to_hex(match.group(0), gains), program)


def _pulse_floor_color(color: str, floor: float) -> str:
    if floor <= 0.0:
        return "off"
    return scale_hex_brightness(color, floor)


def _pulse_ceiling_color(color: str, ceiling: float) -> str:
    if ceiling >= 1.0:
        return color
    return scale_hex_brightness(color, ceiling)


# Animation styles selectable per mode (Idle/Working/Ask -- Done stays a
# fixed solid color, there's nothing to animate there). Each is built from
# the same LEDS.LED DSL primitives (pulse/roll/none easing), just recombined
# differently, rather than adding new device-side capability.
ANIMATION_STYLE_PULSE = "pulse"
ANIMATION_STYLE_ROLL = "roll"
ANIMATION_STYLE_SOLID = "solid"
ANIMATION_STYLE_BLINK = "blink"
ANIMATION_STYLE_CHOICES = (
    ANIMATION_STYLE_PULSE,
    ANIMATION_STYLE_ROLL,
    ANIMATION_STYLE_SOLID,
    ANIMATION_STYLE_BLINK,
)

_STATE_DURATION_TEXT: dict[LedDisplayState, str] = {
    LedDisplayState.IDLE: "6s",
    LedDisplayState.ASK: "1.6s",
    LedDisplayState.WORKING: "760ms",
}

# Numeric (ms) companion to _STATE_DURATION_TEXT, used only to scale the
# settle transition below -- kept separate so the DSL text above stays
# hand-readable.
_STATE_DURATION_MS: dict[LedDisplayState, int] = {
    LedDisplayState.IDLE: 6000,
    LedDisplayState.ASK: 1600,
    LedDisplayState.WORKING: 760,
}

# Every loop needs exactly one "return to the resting color" line so a fresh
# write (the device always starts a new program from the *current* visible
# color) has a known baseline to pulse from -- see settle_duration_ms()'s
# docstring for why this must never be a bare, un-eased color assignment.
SETTLE_MIN_MS = 40
SETTLE_MAX_MS = 160
SETTLE_FRACTION = 0.12


def settle_duration_ms(reference_ms: int) -> int:
    """Duration for a loop's one-time-per-cycle "return to rest" line.

    Two failure modes to avoid, both of which read as the animation
    "stopping" rather than breathing smoothly:

    1. A bare, no-duration color assignment (the DSL's implicit ``none``,
       one 60Hz frame) is an instant, un-eased snap. Once steady-state, it's
       invisible (already at rest, no color change) -- but the very moment
       a real status change interrupts a pulse mid-breath, the device's
       "resume from current visible color" behavior means this line makes
       that interruption an abrupt jump-cut instead of a smooth ease.
    2. A *fixed* settle duration (e.g. a flat 160ms) is fine for a lazy 6s
       idle breathe, but becomes a large fraction of dead time on a fast,
       user-configured Round-Robin/Cycle speed (down to 300ms per cycle) --
       every loop pauses flat at the floor for nearly half the cycle.

    Scaling the settle time to the animation's own duration (capped to a
    sensible range) keeps it long enough to read as a soft cosine ease, but
    never long enough to dominate a fast cycle.
    """
    return max(SETTLE_MIN_MS, min(SETTLE_MAX_MS, round(reference_ms * SETTLE_FRACTION)))


def _render_full_strip(
    style: str,
    state: LedDisplayState,
    color: str,
    *,
    led_count: int,
    floor: float,
    ceiling: float,
) -> str:
    """Renders one mode's full-strip animation in the requested style.
    ``color`` is the mode's own configured color (unscaled) -- floor/ceiling
    scaling is applied here, per style, not by the caller."""
    peak = _pulse_ceiling_color(color, ceiling)
    duration = _STATE_DURATION_TEXT[state]

    if style == ANIMATION_STYLE_SOLID:
        return peak
    if style == ANIMATION_STYLE_BLINK:
        floor_color = _pulse_floor_color(color, floor)
        return "\n".join([f"{peak} {duration} none", f"{floor_color} {duration} none", "repeat"])
    if style == ANIMATION_STYLE_ROLL:
        return rolling_program(peak, led_count=led_count, floor=floor)
    # Default: pulse. The settle line eases to the floor rather than
    # snapping to it -- see settle_duration_ms().
    settle_ms = settle_duration_ms(_STATE_DURATION_MS[state])
    settle_line = f"{_pulse_floor_color(color, floor)} {settle_ms}ms cosine"
    return "\n".join([settle_line, f"{peak} {duration} pulse", "repeat"])


# A brief one-shot twinkle-then-bloom flourish for settling into Done,
# instead of an instant snap to the solid color -- see
# _done_celebration_program. Timings are fixed (not user/speed-scaled like
# the breathing loops) since this only ever plays once, not repeatedly.
DONE_CELEBRATION_SETTLE_MS = 90
DONE_CELEBRATION_STEP_MS = 45
DONE_CELEBRATION_FLASH_MS = 70
DONE_CELEBRATION_PAUSE_MS = 70
DONE_CELEBRATION_BLOOM_MS = 280
# The celebration is the completion signal; holding the strip lit afterwards
# read as an agent still wanting something. The bloom now basks briefly at
# the done colour and then fades out -- the ledger, not the periphery,
# carries "ready for review".
DONE_CELEBRATION_BASK_MS = 1400
DONE_CELEBRATION_FADE_MS = 900


def _done_celebration_program(done_color: str, led_count: int) -> str:
    """A twinkle sweeps once across the strip, briefly flashes off, blooms
    up to the done colour, basks for a moment, then fades to dark and rests
    there -- no "repeat" line, so once played the device simply holds the
    final (dark) state. Safe to dedup against (the controllers compare the
    fully rendered program string), so the twinkle plays exactly once per
    transition into Done, never on an unchanged re-render.
    """
    count = max(1, int(led_count))
    segments = [
        f"{index}:{done_color} {DONE_CELEBRATION_FLASH_MS}ms none {index * DONE_CELEBRATION_STEP_MS}ms"
        for index in range(count)
    ]
    return "\n".join(
        [
            f"off {DONE_CELEBRATION_SETTLE_MS}ms cosine",
            "; ".join(segments),
            f"off {DONE_CELEBRATION_PAUSE_MS}ms none",
            f"{done_color} {DONE_CELEBRATION_BLOOM_MS}ms cosine",
            f"{done_color} {DONE_CELEBRATION_BASK_MS}ms none",
            f"off {DONE_CELEBRATION_FADE_MS}ms cosine",
        ]
    )


def program_for_display_state(
    state: LedDisplayState,
    *,
    led_count: int = 8,
    brightness: float = 255,
    idle_color: str = IDLE_DIM,
    ask_color: str = ASK_AMBER,
    done_color: str = DONE_GREEN,
    working_color: str = WORKING_CYAN,
    idle_floor: float = 0.0,
    idle_ceiling: float = 1.0,
    ask_floor: float = 0.0,
    ask_ceiling: float = 1.0,
    working_floor: float = 0.0,
    working_ceiling: float = 1.0,
    idle_style: str = ANIMATION_STYLE_PULSE,
    ask_style: str = ANIMATION_STYLE_PULSE,
    working_style: str = ANIMATION_STYLE_ROLL,
    done_celebrate: bool = False,
) -> str:
    """Render the LED program for one display state.

    The four ``*_color`` keyword arguments default to this module's original
    hardcoded constants, so every existing caller (and Classic blend mode in
    colors.py) keeps producing byte-identical output. Passing a different
    color lets colors.py reuse this same animation shape (pulse/roll/solid)
    for customized mode colors and per-agent single-block rendering, instead
    of duplicating the animation logic.

    The ``*_floor``/``*_ceiling`` arguments (0.0-1.0, default 0.0/1.0) let a
    pulse breathe between two brightness fractions of its own color instead
    of always swinging fully off-to-on -- e.g. floor=0.01, ceiling=0.5 pulses
    gently between 1% and 50% instead of 0% and 100%. Defaults reproduce
    today's exact off-to-full pulse.

    The ``*_style`` arguments (default idle/ask=pulse, working=roll --
    today's exact behavior) select the animation shape itself: pulse
    (breathe between floor and ceiling), roll (per-LED staggered chase, the
    original Working behavior), solid (steady, no animation), or blink
    (hard on/off snap between floor and ceiling, no easing). Done has no
    style parameter of its own (there's nothing to fade between -- it's
    always the solid done_color at rest) but does take done_celebrate: when
    True, a one-shot twinkle-then-bloom flourish plays on the way to that
    same solid color instead of an instant snap (see
    _done_celebration_program). Defaults to False, reproducing today's
    exact plain-solid-color output.
    """
    if state == LedDisplayState.IDLE:
        return apply_brightness(
            _render_full_strip(
                idle_style, state, idle_color, led_count=led_count, floor=idle_floor, ceiling=idle_ceiling
            ),
            brightness,
        )
    if state == LedDisplayState.ASK:
        return apply_brightness(
            _render_full_strip(
                ask_style, state, ask_color, led_count=led_count, floor=ask_floor, ceiling=ask_ceiling
            ),
            brightness,
        )
    if state == LedDisplayState.DONE:
        if done_celebrate:
            return apply_brightness(_done_celebration_program(done_color, led_count), brightness)
        # Without the flourish there is still no held light: completion is a
        # finite cue, and a lit strip after it read as a phantom ask.
        return "off"
    if state == LedDisplayState.FAILED:
        # Failure stays visible after its finite two-pulse cue without
        # becoming the persistent Ask animation. The existing configurable
        # blocked/error color is the base; only the temporal signature differs.
        return apply_brightness(ask_color, brightness)
    if state == LedDisplayState.WORKING:
        return apply_brightness(
            _render_full_strip(
                working_style,
                state,
                working_color,
                led_count=led_count,
                floor=working_floor,
                ceiling=working_ceiling,
            ),
            brightness,
        )
    raise ValueError(f"Unknown LED display state: {state}")


def display_state_for_projection(projection, active_signal=None) -> LedDisplayState:
    """Map shared projection semantics to one renderer state."""
    from .attention import LifecycleMode, SignalKind

    if active_signal is not None and active_signal.signal.kind is SignalKind.FAILURE:
        return LedDisplayState.FAILED
    return {
        LifecycleMode.IDLE: LedDisplayState.IDLE,
        LifecycleMode.ACTIVE: LedDisplayState.WORKING,
        LifecycleMode.WAITING: LedDisplayState.ASK,
        LifecycleMode.COMPLETED_RECENTLY: LedDisplayState.DONE,
        LifecycleMode.FAILED_VISIBLE: LedDisplayState.FAILED,
        LifecycleMode.UNKNOWN: LedDisplayState.IDLE,
    }[projection.lifecycle_mode]


def failure_signal_program(
    color: str,
    active_signal,
    *,
    brightness: float = 255,
    led_count: int = 8,
) -> str:
    """Render the active failure's exact finite cycles, then steady failure."""
    from .signals import PATTERN_DOUBLE_BLINK, SignalStyle

    repetitions = min(2, max(1, int(active_signal.signal.repetitions)))
    cycle_seconds = max(
        0.001,
        (active_signal.ends_at - active_signal.started_at) / repetitions,
    )
    style = SignalStyle(
        color,
        PATTERN_DOUBLE_BLINK,
        cycle_seconds,
        1.0,
        finite_repetitions=repetitions,
    ).normalized()
    cycle_ms = max(1, round(style.speed_seconds * 1000.0))
    flash_ms = max(1, round(cycle_ms * 17 / 30))
    gap_ms = max(1, cycle_ms - flash_ms)
    cycle = (
        f"{style.color} {flash_ms}ms cosine\n"
        f"off {gap_ms}ms cosine"
    )
    body = "\n".join([cycle] * repetitions + [style.color])
    return apply_brightness(body, normalize_brightness(brightness) * style.intensity)


def rolling_program(color: str, *, led_count: int = 8, floor: float = 0.0) -> str:
    """The Working relay: a breath travelling down the strip.

    Retuned 2026-08-20 from 760ms/95ms ("skittish, almost glitchy") to a
    slow travelling swell -- these lights are meant to read as small art
    pieces breathing beside the work, not as activity spinners.
    """
    count = max(2, min(8, int(led_count)))
    delay_ms = 480 if count == 2 else 170
    duration_ms = 1400
    settle_ms = settle_duration_ms(duration_ms)
    reset_line = f"{_pulse_floor_color(color, floor)} {settle_ms}ms cosine"
    segments: list[str] = []
    for active_index in range(count):
        delay = active_index * delay_ms
        segments.append(f"{active_index}:{color} {duration_ms}ms pulse {delay}ms")
    return "\n".join(
        [
            reset_line,
            "; ".join(segments),
            "repeat",
        ]
    )


def write_mode_to_leds(
    mode: AgentMode,
    *,
    device_path: Path | None = None,
    file_name: str = DEFAULT_FILE_NAME,
    dry_run: bool = False,
    brightness: float = 255,
    channel_gains: tuple[float, float, float] = NEUTRAL_CHANNEL_GAINS,
) -> LedStatusWrite:
    target = resolve_target_path(device_path=device_path, file_name=file_name)
    state = display_state_for_mode(mode)
    program = program_for_display_state(
        state,
        led_count=led_count_for_target(target),
        brightness=brightness,
    )
    program = apply_strip_transfer_to_program(program, channel_gains)
    written_target = write_led_program(
        program,
        device_path=target,
        file_name=file_name,
        dry_run=dry_run,
        preserve_existing_inode=not dry_run,
    )
    return LedStatusWrite(
        state=state,
        target=written_target,
        program=program,
        changed=True,
    )


def burn_saved_animation_to_power_up(
    name: str,
    *,
    library_path: Path | None = None,
    device_path: Path | None = None,
    led_count: int | None = None,
    dry_run: bool = True,
    allow_warnings: bool = False,
):
    """Burn one of the owner's saved animations into INIT.LED.

    The device-facing half of the animation editor, kept here with the rest
    of the writing so there is ONE module that talks to LED files. Returns
    an ``animation.BurnPlan``: on a dry run (the default) that is the exact
    bytes and the target path with nothing written, and a caller that wants
    the write has to say ``dry_run=False`` out loud.

    ``led_count`` defaults to whatever the resolved device actually is, so a
    2-LED Dot is validated as a 2-LED Dot -- an 8-LED program burned onto a
    Dot is accepted by the parser and then silently paints six LEDs that do
    not exist.
    """
    from .animation import burn_power_up_animation, parse_animation
    from .animation_store import (
        default_animation_library_path,
        load_animation_library,
    )

    path = Path(library_path) if library_path else default_animation_library_path()
    saved = load_animation_library(path).library.get(str(name))
    if led_count is None:
        try:
            target = resolve_target_path(
                device_path=device_path, file_name=POWER_UP_FILE_NAME
            )
        except DeviceWriteError:
            led_count = 8
        else:
            led_count = led_count_for_target(target)
    animation = parse_animation(saved.program, name=saved.name, led_count=led_count)
    return burn_power_up_animation(
        animation,
        device_path=device_path,
        led_count=led_count,
        dry_run=dry_run,
        allow_warnings=allow_warnings,
    )


def led_count_for_target(target: Path) -> int:
    name = normalized_device_name(target.parent.name)
    for hint, led_count in DEVICE_LED_COUNTS.items():
        if hint in name:
            return led_count
    return 8


def normalized_device_name(name: str) -> str:
    return "".join(char for char in name.lower() if char.isalnum())


def normalize_brightness(value: float | None) -> int:
    if value is None:
        return 255
    return max(0, min(255, int(round(float(value)))))


def brightness_percent(value: float | None) -> int:
    return round(normalize_brightness(value) / 255 * 100)


def low_battery_program(brightness: float = 255) -> str:
    """The low-battery signal's DEFAULT style through the one renderer
    (see LOW_BATTERY_RED's comment for why it's deliberately slow)."""
    from .signals import DEFAULT_SIGNAL_STYLES, SIGNAL_LOW_BATTERY

    return style_to_program(DEFAULT_SIGNAL_STYLES[SIGNAL_LOW_BATTERY], brightness)


def quota_runway_program(
    fraction_left: float,
    *,
    led_count: int = 8,
    brightness: float = 255,
    color: str = "#10A37F",
) -> str:
    """Remaining quota headroom as a static left-anchored fill -- the
    honest shape for a slow-moving number (no trickle, no motion). Same
    indexed-fill body as timer_fill_program including the invariant that
    unlit segments are #000000, never `off` (firmware parse law)."""
    fraction_left = max(0.0, min(1.0, float(fraction_left)))
    filled = fraction_left * max(1, led_count)
    stripped = color.lstrip("#")
    red, green, blue = (int(stripped[i : i + 2], 16) for i in (0, 2, 4))
    segments = []
    for index in range(led_count):
        level = max(0.0, min(1.0, filled - index))
        if level <= 0.0:
            segments.append(f"{index}:#000000 60000ms linear")
            continue
        lit = scale_hex_brightness(
            f"#{red:02X}{green:02X}{blue:02X}", level * (brightness / 255.0)
        )
        segments.append(f"{index}:{lit} 60000ms linear")
    return "\n".join(["; ".join(segments), "repeat"])


def timer_fill_program(
    fraction: float,
    *,
    led_count: int = 8,
    brightness: float = 255,
    color: str = "#00E5FF",
) -> str:
    """LED fill from the left as elapsed working time crosses the
    user's expected window -- deliberately a TIMER, never a claim about
    task progress (hooks deliver no truthful progress fraction). The
    partially-elapsed LED scales its own brightness for a smooth edge;
    static program, rewritten by the ordinary sync cadence."""
    fraction = max(0.0, min(1.0, float(fraction)))
    filled = fraction * max(1, led_count)
    stripped = color.lstrip("#")
    red, green, blue = (int(stripped[i : i + 2], 16) for i in (0, 2, 4))
    segments = []
    frontier = min(led_count - 1, int(filled)) if filled > 0 else 0
    for index in range(led_count):
        amount = max(0.0, min(1.0, filled - index))
        if amount <= 0.0:
            # "#000000", never "off": the firmware's indexed-segment
            # parser rejects `N:off` (bad-index) and a failed parse
            # renders the solid-red error state.
            segments.append(f"{index}:#000000")
        else:
            scaled = "#" + "".join(
                f"{round(channel * amount):02X}" for channel in (red, green, blue)
            )
            segments.append(f"{index}:{scaled}")
    body = "; ".join(segments)
    if 0.0 < fraction < 1.0:
        # The plink: the frontier LED breathes -- a grain landing --
        # and faint grains trickle across the unfilled section toward
        # it, so a slow timer reads as alive, not frozen.
        lines = [body, f"{frontier}:{color} 1200ms pulse"]
        unfilled = [index for index in range(led_count) if index > frontier]
        if unfilled:
            dim = "#" + "".join(
                f"{round(channel * 0.28):02X}" for channel in (red, green, blue)
            )
            span = max(1, len(unfilled))
            trickle = "; ".join(
                f"{index}:{dim} 260ms pulse "
                f"{min(65535, round((len(unfilled) - 1 - position) * 2400 / span))}ms"
                for position, index in enumerate(unfilled)
            )
            lines.append(trickle)
        lines.append("repeat")
        body = "\n".join(lines)
    return apply_brightness(body, brightness)


def style_to_program(
    style,
    brightness: float = 255,
    *,
    color: str | None = None,
    led_count: int = 8,
) -> str:
    """The Signal Engine's ONE renderer: any SignalStyle -> device DSL.
    ``color`` overrides the style's own (the notification signal passes
    the notifying app's color). Every pattern emits whole-bar lines
    except sweep (indexed chase); all durations/delays stay within the
    firmware's 65535ms cap and the 512B/20-line program limits at every
    legal speed (asserted by tests)."""
    style = style.normalized()
    hex_color = color or style.color
    speed_ms = max(1, round(style.speed_seconds * 1000.0))
    effective = normalize_brightness(brightness) * style.intensity

    if style.pattern == "breathe":
        body = f"off 400ms cosine\n{hex_color} {speed_ms}ms pulse\nrepeat"
    elif style.pattern == "blink":
        # A SQUARE, and the only shape here with no easing at all. It used
        # to be `cosine` on both halves, which is a triangle -- measured
        # through the firmware, a cosine blink and a breathe are the same
        # smooth bump at different ratios, so "blink" was a second name for
        # "breathe" rather than its own signal. Hard edges are the whole
        # point of the word, and they are what a dichromat can still read
        # when the hue is gone.
        flash = max(1, round(speed_ms * 17 / 30))
        gap = max(1, round(speed_ms * 13 / 30))
        body = "\n".join([f"{hex_color} {flash}ms none\noff {gap}ms none"] * 3)
    elif style.pattern == "double-blink":
        # A KNOCK: two hard taps close together, then a rest that is half
        # the cycle. It used to be literally `blink` truncated to two
        # cycles -- byte-for-byte the same two lines, so a knock and a
        # blink were indistinguishable in a still frame AND in motion until
        # the third flash that never came. The rest is what makes it a
        # knock: rap-rap, pause.
        tap = max(1, round(speed_ms / 6))
        rest = max(1, speed_ms - 3 * tap)
        body = (
            f"{hex_color} {tap}ms none\noff {tap}ms none\n"
            f"{hex_color} {tap}ms none\noff {rest}ms none"
        )
    elif style.pattern == "solid":
        body = hex_color
    elif style.pattern == "sweep":
        duration = min(65535, max(1, round(speed_ms / 2)))
        segments = "; ".join(
            f"{index}:{hex_color} {duration}ms pulse "
            f"{min(65535, round(index * speed_ms / max(1, led_count)))}ms"
            for index in range(led_count)
        )
        body = f"off 300ms cosine\n{segments}\nrepeat"
    elif style.pattern == "ripple":
        # Overlapping full-length pulses, one LED-step apart: a wave
        # that travels the strip -- the on-screen twin of Relay's chase.
        duration = min(65535, max(1, speed_ms))
        segments = "; ".join(
            f"{index}:{hex_color} {duration}ms pulse "
            f"{min(65535, round(index * speed_ms / max(1, led_count)))}ms"
            for index in range(led_count)
        )
        body = f"off 300ms cosine\n{segments}\nrepeat"
    elif style.pattern == "comet":
        # A short bright head racing the strip, cosine decay as its tail.
        duration = min(65535, max(1, round(speed_ms / 4)))
        segments = "; ".join(
            f"{index}:{hex_color} {duration}ms pulse "
            f"{min(65535, round(index * speed_ms / max(1, led_count)))}ms"
            for index in range(led_count)
        )
        body = f"off 300ms cosine\n{segments}\nrepeat"
    elif style.pattern == "sparkle":
        # Deterministic scatter (a fixed co-prime permutation, so it
        # renders identically everywhere) of short twinkles.
        flash = min(65535, max(1, round(speed_ms / 6)))
        segments = "; ".join(
            f"{index}:{hex_color} {flash}ms pulse "
            f"{min(65535, round(((index * 5 + 3) % max(1, led_count)) * speed_ms / max(1, led_count)))}ms"
            for index in range(led_count)
        )
        body = f"off 300ms cosine\n{segments}\nrepeat"
    elif style.pattern == "heartbeat":
        # LUB-dub. Three things make this a heartbeat rather than a third
        # spelling of blink, and the old version had none of them:
        #
        #   * each beat is a `pulse`, so it rises AND falls inside its own
        #     duration -- a thump. The old one ramped up with `cosine` and
        #     then held until the line ended, so beat two was a 180ms rise
        #     followed by a 520ms fade: a long sigh, not a beat.
        #   * the rest is a flat `none` hold at dark. A cosine rest is a
        #     decay, and a decay is indistinguishable from the beat it is
        #     decaying from.
        #   * the second beat is dimmer than the first. Two equal taps is a
        #     knock; unequal ones are a pulse you can feel.
        thump = max(1, round(speed_ms * 0.14))
        gap = max(1, round(speed_ms * 0.10))
        rest = max(1, speed_ms - 2 * thump - gap)
        dub = scale_hex_brightness(hex_color, 0.55)
        body = (
            f"{hex_color} {thump}ms pulse\noff {gap}ms none\n"
            f"{dub} {thump}ms pulse\noff {rest}ms none\nrepeat"
        )
    else:  # pragma: no cover - normalized() forbids this
        body = hex_color
    if style.finite_repetitions is not None and any(
        line.strip() == "repeat" for line in body.splitlines()
    ):
        body = "\n".join(
            [
                *(line for line in body.splitlines() if line.strip() != "repeat"),
                hex_color,
            ]
        )
    return apply_brightness(body, effective)


def calendar_glow_program(brightness: float = 255) -> str:
    """The calendar signal's DEFAULT style through the one renderer --
    kept as a named helper for call sites and tests."""
    from .signals import DEFAULT_SIGNAL_STYLES, SIGNAL_CALENDAR

    return style_to_program(DEFAULT_SIGNAL_STYLES[SIGNAL_CALENDAR], brightness)


NOTIFICATION_BLINK_SECONDS = 3 * 0.3 + 0.4  # default blink style's hold


def notification_blink_program(color: str, brightness: float = 255) -> str:
    """The notification signal's DEFAULT style (app color override)
    through the one renderer."""
    from .signals import DEFAULT_SIGNAL_STYLES, SIGNAL_NOTIFICATION

    return style_to_program(
        DEFAULT_SIGNAL_STYLES[SIGNAL_NOTIFICATION], brightness, color=color
    )


def _steady_state_variant(program: str) -> str:
    """The program without its one-shot approach frame, for reasserts.

    A reassert refreshes device state without any visual change intended,
    but the firmware restarts a program from its first line -- and the
    first paint line is a bright transition snapshot, so every reassert
    flashed one LED like an ask. The repeating body carries its own phase
    delays and is the steady state the device is already showing.
    """
    lines = program.splitlines()
    body_start = 1 if lines and lines[0].startswith("brightness ") else 0
    content = lines[body_start:]
    if len(content) >= 3 and content[-1].strip().startswith("repeat"):
        trimmed = lines[:body_start] + content[1:]
        return "\n".join(trimmed)
    return program


def apply_brightness(program: str, brightness: float = 255) -> str:
    value = normalize_brightness(brightness)
    if value >= 255:
        return program
    return f"brightness {value}\n{program}"


class AgentLedController:
    def __init__(
        self,
        *,
        device_path: Path | None = None,
        file_name: str = DEFAULT_FILE_NAME,
        dry_run: bool = False,
        error_retry_seconds: float = 10.0,
        reassert_after_seconds: float = LED_REASSERT_SECONDS,
        brightness: float = 255,
        channel_gains: tuple[float, float, float] = NEUTRAL_CHANNEL_GAINS,
    ) -> None:
        self.device_path = device_path
        self.file_name = file_name
        self.dry_run = dry_run
        self.error_retry_seconds = error_retry_seconds
        self.reassert_after_seconds = max(1.0, float(reassert_after_seconds))
        self.brightness = normalize_brightness(brightness)
        # Per-channel gain correction for this physical device's own LED
        # die response -- applied only to what actually gets written here,
        # never to the "true" hex shown in the Colors window or the Screen
        # Bar's on-screen preview. See apply_channel_gain_to_program.
        self.channel_gains = channel_gains
        self.last_state: LedDisplayState | None = None
        self.last_brightness: int | None = None
        self.last_channel_gains: tuple[float, float, float] | None = None
        self.last_program: str | None = None
        self.last_program_identity: object | None = None
        self.last_error: str | None = None
        self.last_target: Path | None = None
        self.last_attempt_monotonic = 0.0
        self.last_device_uptime_ms: float | None = None
        self.last_uptime_check_monotonic = 0.0
        self.pending_reboot_repaint = False

    def reset(self) -> None:
        self.last_state = None
        self.last_brightness = None
        self.last_channel_gains = None
        self.last_program = None
        self.last_program_identity = None
        self.last_error = None
        self.last_target = None
        self.last_attempt_monotonic = 0.0

    def _for_strip(self, program: str) -> str:
        """The strip's last step, in the one place every write goes through.

        Resting glow, then the surface transfer (which carries the channel
        gain with it -- see strip_drive_code). Deliberately the very end of the
        pipeline: everything before this point is nominal sRGB, the same
        numbers the Colors window shows and the same numbers the Screen Bar
        receives, so the two surfaces differ only in the final translation into
        their own units rather than diverging somewhere upstream.
        """
        program = apply_resting_glow_to_program(program, getattr(self, "resting_glow", 0.0))
        return apply_strip_transfer_to_program(program, self.channel_gains)

    def sync_mode(self, mode: AgentMode) -> LedStatusWrite:
        state = display_state_for_mode(mode)
        brightness = normalize_brightness(self.brightness)
        gains = self.channel_gains
        now = time.monotonic()
        unchanged = (
            state == self.last_state and brightness == self.last_brightness and gains == self.last_channel_gains
        )
        if (
            unchanged
            and self.last_error is None
            and now - self.last_attempt_monotonic < self.reassert_after_seconds
        ):
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
            )
        if (
            unchanged
            and self.last_error is not None
            and now - self.last_attempt_monotonic < self.error_retry_seconds
        ):
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error,
            )

        self.last_attempt_monotonic = now
        try:
            result = write_mode_to_leds(
                mode,
                device_path=self.device_path,
                file_name=self.file_name,
                dry_run=self.dry_run,
                brightness=brightness,
                channel_gains=gains,
            )
        except (DeviceWriteError, OSError) as exc:
            self.last_state = state
            self.last_brightness = brightness
            self.last_channel_gains = gains
            self.last_error = str(exc)
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error,
            )

        self.last_state = state
        self.last_brightness = brightness
        self.last_channel_gains = gains
        self.last_error = None
        self.last_target = result.target
        return result

    def _phase_free_identity(self, render, relay_elapsed_seconds: float):
        """Identity = what the strip WOULD look like at a fixed phase.

        Dedupe must compare the visual RESULT, not the inputs and not the
        raw text. Relay bakes a wall-clock phase into the program, so a
        raw text compare differed on essentially every call and the 60s
        reassert window never engaged -- the device was rewritten every
        refresh at ~25-40 syscalls plus an fsync and a readback each. But
        comparing inputs is wrong in the other direction: a 2-LED Dot
        genuinely cannot show a third agent changing, and that write
        should still be skipped. Re-rendering at phase zero gets both:
        motion alone never writes, and anything that would actually look
        different does.
        """
        # Always render at phase zero, including when the live phase is
        # already zero: an identity that sometimes falls back to the raw
        # program compares unequal against a token-shaped one, which
        # costs an extra device write every time the phase leaves zero.
        try:
            identity = render(0.0)
        except Exception:
            return None
        # Same post-processing as the live program: calibration gains and
        # resting glow change what reaches the device, so they must be
        # able to invalidate the identity too.
        return self._for_strip(identity)

    def sync_snapshot(
        self,
        statuses: tuple[AgentStatus, ...],
        colors: ColorSettings,
        *,
        fallback_mode: AgentMode = AgentMode.IDLE_READY,
        relay_elapsed_seconds: float = 0.0,
    ) -> LedStatusWrite:
        """Multi-agent-aware sibling of ``sync_mode``.

        Renders through ``colors.program_for_snapshot`` (imported locally to
        avoid a circular import -- colors.py imports from this module) and
        dedups on the fully rendered program string rather than just the
        display state, since two different agent layouts/blend outputs can
        share the same representative state (e.g. two different Working
        splits are both "Working" but render different LEDs).
        """
        from .colors import program_for_snapshot

        target = resolve_target_path(device_path=self.device_path, file_name=self.file_name)
        led_count = led_count_for_target(target)
        brightness = normalize_brightness(self.brightness)
        state, program = program_for_snapshot(
            statuses,
            led_count=led_count,
            colors=colors,
            brightness=brightness,
            fallback_mode=fallback_mode,
            relay_elapsed_seconds=relay_elapsed_seconds,
        )
        # Applied before the dedup check below (not after) so both the
        # comparison and self.last_program reflect the exact bytes actually
        # written -- a gain change alone (with statuses/colors unchanged)
        # still produces a different string here and correctly triggers a
        # rewrite, with no separate "did gains change" tracking needed.
        program = self._for_strip(program)
        # Dedupe on the INPUTS, never the rendered text. Relay bakes a
        # wall-clock phase into the program, so a text compare differs on
        # essentially every call -- the 60s reassert window never engaged
        # and the device was rewritten every refresh, each write costing
        # ~25-40 syscalls plus an fsync and a full readback.
        identity = self._phase_free_identity(
            lambda phase: program_for_snapshot(
                statuses,
                colors=colors,
                fallback_mode=fallback_mode,
                led_count=led_count,
                brightness=brightness,
                relay_elapsed_seconds=phase,
            )[1],
            relay_elapsed_seconds,
        )
        return self._write_deduped_program(
            state,
            program,
            dedupe_token=("snapshot", identity) if identity is not None else None,
        )

    def sync_projection(
        self,
        projection,
        colors: ColorSettings,
        *,
        active_signal=None,
        relay_elapsed_seconds: float = 0.0,
    ) -> LedStatusWrite:
        """Projection-aware renderer used by every live controller surface."""
        from .colors import program_for_projection

        target = resolve_target_path(device_path=self.device_path, file_name=self.file_name)
        state, program = program_for_projection(
            projection,
            active_signal=active_signal,
            led_count=led_count_for_target(target),
            colors=colors,
            brightness=normalize_brightness(self.brightness),
            relay_elapsed_seconds=relay_elapsed_seconds,
        )
        program = self._for_strip(program)
        identity = self._phase_free_identity(
            lambda phase: program_for_projection(
                projection,
                active_signal=active_signal,
                led_count=led_count_for_target(target),
                colors=colors,
                brightness=normalize_brightness(self.brightness),
                relay_elapsed_seconds=phase,
            )[1],
            relay_elapsed_seconds,
        )
        return self._write_deduped_program(
            state,
            program,
            dedupe_token=("projection", identity) if identity is not None else None,
        )

    def sync_program(
        self,
        program: str,
        state: LedDisplayState,
        *,
        dedupe_token: object | None = None,
    ) -> LedStatusWrite:
        """Writes a pre-rendered program through the same gain/dedup/retry
        path sync_snapshot uses -- for displays that aren't derived from
        agent statuses at all (e.g. the low-battery reminder)."""
        program = self._for_strip(program)
        return self._write_deduped_program(
            state,
            program,
            dedupe_token=dedupe_token,
        )

    UPTIME_CHECK_SECONDS = 60.0

    def _device_rebooted_since_last_write(self, now: float) -> bool:
        """True when the firmware's uptime went BACKWARDS -- the strip
        rebooted (wake-time USB re-enumeration, replug) and whatever it
        is displaying no longer corresponds to what this writer believes
        it last delivered. Live incident 2026-08-20: the device rebooted
        on lid-open mid-flourish and looped the lid greens for two hours
        while every dedupe-skipped tick assumed the steady program was
        still showing. Read at most once a minute; unreadable STATUS.TXT
        is not evidence of a reboot."""
        if self.device_path is None or self.dry_run:
            return False
        if now - self.last_uptime_check_monotonic < self.UPTIME_CHECK_SECONDS:
            return False
        self.last_uptime_check_monotonic = now
        try:
            root = Path(self.device_path)
            # Production controllers carry the LEDS.LED FILE path, not
            # the volume root -- appending STATUS.TXT to that yields
            # <volume>/LEDS.LED/STATUS.TXT, NotADirectoryError, and a
            # silent False: reboot detection had never fired in the
            # shipped app (audit, 2026-08-26). Mirror the keepalive
            # helper's file-vs-directory handling.
            from .device_writer import KNOWN_LED_FILE_NAMES

            if root.name.upper() in KNOWN_LED_FILE_NAMES:
                root = root.parent
            status_path = root / "STATUS.TXT"
            text = status_path.read_text(errors="replace")[:4096]
        except OSError:
            return False
        uptime_ms: float | None = None
        for line in text.splitlines():
            if line.startswith("uptime_ms"):
                parts = line.split()
                if len(parts) == 2:
                    try:
                        uptime_ms = float(parts[1])
                    except ValueError:
                        uptime_ms = None
                break
        if uptime_ms is None:
            return False
        previous = self.last_device_uptime_ms
        self.last_device_uptime_ms = uptime_ms
        if previous is not None and uptime_ms < previous:
            self.pending_reboot_repaint = True
            return True
        return False

    def _write_deduped_program(
        self,
        state: LedDisplayState,
        program: str,
        *,
        dedupe_token: object | None = None,
    ) -> LedStatusWrite:
        now = time.monotonic()
        identity = (
            ("token", dedupe_token)
            if dedupe_token is not None
            else ("program", program)
        )

        if getattr(self, "pending_reboot_repaint", False):
            # Set by the background keepalive poke after it saw the
            # firmware's uptime go backwards. The 2026-08-21 flight
            # recorder caught the ORIGINAL inline STATUS.TXT read
            # blocking the main thread for up to 13.7s -- a slow SD FAT
            # read is exactly the settings lag it caused. The write path
            # now only consumes this memory flag.
            self.pending_reboot_repaint = False
            self.last_program_identity = None
            self.last_attempt_monotonic = 0.0

        if (
            identity == self.last_program_identity
            and self.last_error is None
            and now - self.last_attempt_monotonic < self.reassert_after_seconds
        ):
            return LedStatusWrite(state=state, target=self.last_target, program="", changed=False)
        if (
            identity == self.last_program_identity
            and self.last_error is not None
            and now - self.last_attempt_monotonic < self.error_retry_seconds
        ):
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error,
            )

        self.last_attempt_monotonic = now
        reassert = identity == self.last_program_identity and self.last_error is None
        to_write = (
            _steady_state_variant(program) if reassert else program
        )
        try:
            written_target = write_led_program(
                to_write,
                device_path=self.device_path,
                file_name=self.file_name,
                dry_run=self.dry_run,
                preserve_existing_inode=not self.dry_run,
            )
        except (DeviceWriteError, OSError) as exc:
            self.last_state = state
            self.last_program = program
            self.last_program_identity = identity
            self.last_error = str(exc)
            return LedStatusWrite(
                state=state,
                target=self.last_target,
                program="",
                changed=False,
                error=self.last_error,
            )

        self.last_state = state
        self.last_program = program
        self.last_program_identity = identity
        self.last_error = None
        self.last_target = written_target
        return LedStatusWrite(state=state, target=written_target, program=program, changed=True)
