from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .device_writer import (
    DEFAULT_FILE_NAME,
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


LED_STATE_LABELS: dict[LedDisplayState, str] = {
    LedDisplayState.IDLE: "Idle",
    LedDisplayState.WORKING: "Working",
    LedDisplayState.DONE: "Done",
    LedDisplayState.ASK: "Ask",
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


MIN_CHANNEL_GAIN = 0.3
MAX_CHANNEL_GAIN = 1.5
DEFAULT_CHANNEL_GAIN = 1.0
NEUTRAL_CHANNEL_GAINS = (DEFAULT_CHANNEL_GAIN, DEFAULT_CHANNEL_GAIN, DEFAULT_CHANNEL_GAIN)

_HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")


def normalize_channel_gain(value: float | int | None) -> float:
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


def _done_celebration_program(done_color: str, led_count: int) -> str:
    """A twinkle sweeps once across the strip, briefly flashes off, then
    blooms smoothly up to the solid done_color and holds there forever --
    there's no "repeat" line, so once played there's nothing left to do
    and the device just keeps showing the final (solid) state, exactly
    like the plain color it replaces. This is why it's safe to dedup
    against (AgentLedController/BatteryLedController compare the fully
    rendered program string) -- the string only changes on the actual
    transition into Done, so the twinkle plays exactly once per
    transition, never on every otherwise-unchanged re-render.
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
        ]
    )


def program_for_display_state(
    state: LedDisplayState,
    *,
    led_count: int = 8,
    brightness: int | float = 255,
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
        return apply_brightness(done_color, brightness)
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


def rolling_program(color: str, *, led_count: int = 8, floor: float = 0.0) -> str:
    count = max(2, min(8, int(led_count)))
    delay_ms = 260 if count == 2 else 95
    duration_ms = 760
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
    brightness: int | float = 255,
    channel_gains: tuple[float, float, float] = NEUTRAL_CHANNEL_GAINS,
) -> LedStatusWrite:
    target = resolve_target_path(device_path=device_path, file_name=file_name)
    state = display_state_for_mode(mode)
    program = program_for_display_state(
        state,
        led_count=led_count_for_target(target),
        brightness=brightness,
    )
    program = apply_channel_gain_to_program(program, channel_gains)
    written_target = write_led_program(
        program,
        device_path=target,
        file_name=file_name,
        dry_run=dry_run,
    )
    return LedStatusWrite(
        state=state,
        target=written_target,
        program=program,
        changed=True,
    )


def led_count_for_target(target: Path) -> int:
    name = normalized_device_name(target.parent.name)
    for hint, led_count in DEVICE_LED_COUNTS.items():
        if hint in name:
            return led_count
    return 8


def normalized_device_name(name: str) -> str:
    return "".join(char for char in name.lower() if char.isalnum())


def normalize_brightness(value: int | float | None) -> int:
    if value is None:
        return 255
    return max(0, min(255, int(round(float(value)))))


def brightness_percent(value: int | float | None) -> int:
    return round(normalize_brightness(value) / 255 * 100)


def low_battery_program(brightness: int | float = 255) -> str:
    """The low-battery signal's DEFAULT style through the one renderer
    (see LOW_BATTERY_RED's comment for why it's deliberately slow)."""
    from .signals import DEFAULT_SIGNAL_STYLES, SIGNAL_LOW_BATTERY

    return style_to_program(DEFAULT_SIGNAL_STYLES[SIGNAL_LOW_BATTERY], brightness)


def style_to_program(
    style,
    brightness: int | float = 255,
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
    elif style.pattern in ("blink", "double-blink"):
        cycles = 3 if style.pattern == "blink" else 2
        flash = max(1, round(speed_ms * 17 / 30))
        gap = max(1, round(speed_ms * 13 / 30))
        body = "\n".join([f"{hex_color} {flash}ms cosine\noff {gap}ms cosine"] * cycles)
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
        # Lub-dub: two quick whole-bar thumps, then a rest.
        thump = max(1, round(speed_ms * 0.18))
        gap = max(1, round(speed_ms * 0.12))
        rest = max(1, round(speed_ms * 0.52))
        body = (
            f"{hex_color} {thump}ms cosine\noff {gap}ms cosine\n"
            f"{hex_color} {thump}ms cosine\noff {rest}ms cosine\nrepeat"
        )
    else:  # pragma: no cover - normalized() forbids this
        body = hex_color
    return apply_brightness(body, effective)


def calendar_glow_program(brightness: int | float = 255) -> str:
    """The calendar signal's DEFAULT style through the one renderer --
    kept as a named helper for call sites and tests."""
    from .signals import DEFAULT_SIGNAL_STYLES, SIGNAL_CALENDAR

    return style_to_program(DEFAULT_SIGNAL_STYLES[SIGNAL_CALENDAR], brightness)


NOTIFICATION_BLINK_SECONDS = 3 * 0.3 + 0.4  # default blink style's hold


def notification_blink_program(color: str, brightness: int | float = 255) -> str:
    """The notification signal's DEFAULT style (app color override)
    through the one renderer."""
    from .signals import DEFAULT_SIGNAL_STYLES, SIGNAL_NOTIFICATION

    return style_to_program(
        DEFAULT_SIGNAL_STYLES[SIGNAL_NOTIFICATION], brightness, color=color
    )


def apply_brightness(program: str, brightness: int | float = 255) -> str:
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
        brightness: int | float = 255,
        channel_gains: tuple[float, float, float] = NEUTRAL_CHANNEL_GAINS,
    ) -> None:
        self.device_path = device_path
        self.file_name = file_name
        self.dry_run = dry_run
        self.error_retry_seconds = error_retry_seconds
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
        self.last_error: str | None = None
        self.last_target: Path | None = None
        self.last_attempt_monotonic = 0.0

    def reset(self) -> None:
        self.last_state = None
        self.last_brightness = None
        self.last_channel_gains = None
        self.last_program = None
        self.last_error = None
        self.last_target = None
        self.last_attempt_monotonic = 0.0

    def sync_mode(self, mode: AgentMode) -> LedStatusWrite:
        state = display_state_for_mode(mode)
        brightness = normalize_brightness(self.brightness)
        gains = self.channel_gains
        now = time.monotonic()
        unchanged = (
            state == self.last_state and brightness == self.last_brightness and gains == self.last_channel_gains
        )
        if unchanged and self.last_error is None:
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

    def sync_snapshot(
        self,
        statuses: tuple[AgentStatus, ...],
        colors: ColorSettings,
        *,
        fallback_mode: AgentMode = AgentMode.IDLE_READY,
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
        )
        # Applied before the dedup check below (not after) so both the
        # comparison and self.last_program reflect the exact bytes actually
        # written -- a gain change alone (with statuses/colors unchanged)
        # still produces a different string here and correctly triggers a
        # rewrite, with no separate "did gains change" tracking needed.
        program = apply_channel_gain_to_program(program, self.channel_gains)
        return self._write_deduped_program(state, program)

    def sync_program(self, program: str, state: LedDisplayState) -> LedStatusWrite:
        """Writes a pre-rendered program through the same gain/dedup/retry
        path sync_snapshot uses -- for displays that aren't derived from
        agent statuses at all (e.g. the low-battery reminder)."""
        program = apply_channel_gain_to_program(program, self.channel_gains)
        return self._write_deduped_program(state, program)

    def _write_deduped_program(self, state: LedDisplayState, program: str) -> LedStatusWrite:
        now = time.monotonic()

        if program == self.last_program and self.last_error is None:
            return LedStatusWrite(state=state, target=self.last_target, program="", changed=False)
        if (
            program == self.last_program
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
            written_target = write_led_program(
                program,
                device_path=self.device_path,
                file_name=self.file_name,
                dry_run=self.dry_run,
            )
        except (DeviceWriteError, OSError) as exc:
            self.last_state = state
            self.last_program = program
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
        self.last_error = None
        self.last_target = written_target
        return LedStatusWrite(state=state, target=written_target, program=program, changed=True)
