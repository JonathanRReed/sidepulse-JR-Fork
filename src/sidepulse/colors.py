"""Per-mode and per-agent LED color customization, blending, and rendering.

This module is the home for everything described in
docs/superpowers/specs/2026-08-10-agent-color-customizer-design.md: the
color data model (``ColorSettings``), the curated palette used for both
default assignment and the Colors window's swatch strips, and the
multi-agent-aware LED program renderer that supersedes the single-aggregate
``led_status.program_for_display_state`` for anything beyond Classic mode.

Kept as its own module (rather than growing ``settings.py`` or
``status_bar.py`` further) since both of those files are already large and
this is a self-contained concern: given a set of active agent statuses and a
``ColorSettings``, produce an LED program string.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from .device_writer import MAX_LED_BYTES, MAX_LED_LINES
from .led_status import (
    ANIMATION_STYLE_BLINK,
    ANIMATION_STYLE_CHOICES,
    ANIMATION_STYLE_PULSE,
    ANIMATION_STYLE_ROLL,
    ANIMATION_STYLE_SOLID,
    ASK_AMBER,
    DONE_GREEN,
    IDLE_DIM,
    WORKING_CYAN,
    LedDisplayState,
    apply_brightness,
    display_state_for_mode,
    normalize_brightness,
    program_for_display_state,
    scale_hex_brightness,
    settle_duration_ms,
)
from .models import AgentMode, AgentStatus, MODE_PRIORITY
from .providers import PROVIDER_SPECS


# --- Mode color keys -------------------------------------------------------

MODE_IDLE = "idle"
MODE_WORKING = "working"
MODE_DONE = "done"
MODE_ASK = "ask"
MODE_COLOR_KEYS: tuple[str, ...] = (MODE_IDLE, MODE_WORKING, MODE_DONE, MODE_ASK)

_STATE_TO_MODE_KEY: dict[LedDisplayState, str] = {
    LedDisplayState.IDLE: MODE_IDLE,
    LedDisplayState.WORKING: MODE_WORKING,
    LedDisplayState.DONE: MODE_DONE,
    LedDisplayState.ASK: MODE_ASK,
}

_MODE_KEY_TO_COLOR_KWARG: dict[str, str] = {
    MODE_IDLE: "idle_color",
    MODE_WORKING: "working_color",
    MODE_DONE: "done_color",
    MODE_ASK: "ask_color",
}

# Done is a solid, non-pulsing color -- there's nothing to fade between, so
# it's excluded from fade customization.
FADE_MODE_KEYS: tuple[str, ...] = (MODE_IDLE, MODE_WORKING, MODE_ASK)

# A pulse breathing fully between 0% and 100% brightness reads as harsh/
# jarring. These are the new defaults for every pulsing mode -- gentler than
# led_status.py's own 0.0/1.0 primitives, which stay as the low-level
# building block's neutral defaults.
DEFAULT_FADE_FLOOR = 0.01
DEFAULT_FADE_CEILING = 0.5

_MODE_KEY_TO_FLOOR_KWARG: dict[str, str] = {
    MODE_IDLE: "idle_floor",
    MODE_WORKING: "working_floor",
    MODE_ASK: "ask_floor",
}
_MODE_KEY_TO_CEILING_KWARG: dict[str, str] = {
    MODE_IDLE: "idle_ceiling",
    MODE_WORKING: "working_ceiling",
    MODE_ASK: "ask_ceiling",
}

# Same three modes as FADE_MODE_KEYS -- Done stays a fixed solid color, so
# there's no animation style to pick for it either.
ANIMATION_MODE_KEYS: tuple[str, ...] = FADE_MODE_KEYS

DEFAULT_MODE_ANIMATION: dict[str, str] = {
    MODE_IDLE: ANIMATION_STYLE_PULSE,
    MODE_WORKING: ANIMATION_STYLE_ROLL,
    MODE_ASK: ANIMATION_STYLE_PULSE,
}

_MODE_KEY_TO_STYLE_KWARG: dict[str, str] = {
    MODE_IDLE: "idle_style",
    MODE_WORKING: "working_style",
    MODE_ASK: "ask_style",
}


# --- Curated palette ---------------------------------------------------

# Apple's own system semantic colors (systemRed/Orange/Yellow/Green/Blue/
# Purple/Pink/Gray), not colors invented for this app. Two reasons:
#   1. Distinctness on real hardware: an earlier palette optimized for
#      pairwise blending (colors chosen to average into something legible
#      together) put blue, teal, and violet within ~45-90 degrees of each
#      other -- on a dim, cheap RGB LED that whole blue/teal/violet cluster
#      reads as "some shade of blue-ish," confirmed live (a teal agent color
#      was mistaken for green). Blending is no longer the primary way
#      multiple agents are shown (see BLEND_MODE_ROUND_ROBIN below), so this
#      palette is tuned for maximum at-a-glance distinctness instead --
#      teal/cyan/indigo are deliberately skipped since they sit in that same
#      crowded blue-adjacent region.
#   2. It's the actual native palette -- using Apple's own semantic colors
#      is a more direct way to "feel Apple native" than approximating one.
#
# Ordering matters as much as color choice: default_agent_color() assigns
# by registry position (codex=index 0, claude=index 1, ...), so whichever
# two colors sit at indices 0-1 are what your two most-used agents will
# actually wear day to day. An earlier ordering here put systemRed and
# systemOrange at 0-1 -- adjacent hues, confirmed live to be genuinely hard
# to tell apart at reduced pulse brightness (Codex's red vs Claude's orange
# both alternating in Round-Robin looked like "orange/red, which is which").
# This order is a greedy farthest-point selection instead of hue order, so
# each new provider gets the color most different from every color already
# assigned -- the first four (today's actual provider count) land on
# red/blue/green/purple, a maximally-distinct quadrant split.
CURATED_PALETTE: tuple[str, ...] = (
    "#FF3B30",  # systemRed
    "#007AFF",  # systemBlue
    "#34C759",  # systemGreen
    "#AF52DE",  # systemPurple
    "#FFCC00",  # systemYellow
    "#FF9500",  # systemOrange
    "#FF2D55",  # systemPink
    "#8E8E93",  # systemGray (last resort / overflow slot)
)


# --- Blend modes -------------------------------------------------------

BLEND_MODE_SPATIAL = "spatial_split"
BLEND_MODE_COLOR = "color_blend"
BLEND_MODE_CYCLE = "cycle"
BLEND_MODE_CLASSIC = "classic"
BLEND_MODE_ROUND_ROBIN = "round_robin"
BLEND_MODE_RELAY = "relay"
BLEND_MODE_CHOICES: tuple[str, ...] = (
    BLEND_MODE_ROUND_ROBIN,
    BLEND_MODE_RELAY,
    BLEND_MODE_SPATIAL,
    BLEND_MODE_COLOR,
    BLEND_MODE_CYCLE,
    BLEND_MODE_CLASSIC,
)
DEFAULT_BLEND_MODE = BLEND_MODE_ROUND_ROBIN

BLEND_MODE_LABELS: dict[str, str] = {
    BLEND_MODE_ROUND_ROBIN: "Round-Robin",
    BLEND_MODE_RELAY: "Relay",
    BLEND_MODE_SPATIAL: "Spatial Split",
    BLEND_MODE_COLOR: "Color Blend",
    BLEND_MODE_CYCLE: "Cycle Colors",
    BLEND_MODE_CLASSIC: "Classic",
}

BLEND_MODE_DESCRIPTIONS: dict[str, str] = {
    BLEND_MODE_ROUND_ROBIN: "Every LED breathes together, in sequence.",
    BLEND_MODE_RELAY: "One agent lights up at a time, like a baton.",
    BLEND_MODE_SPATIAL: "Each agent gets a block sized by urgency.",
    BLEND_MODE_COLOR: "Blends all agents into one averaged color.",
    BLEND_MODE_CYCLE: "Whole strip shows one agent, then the next.",
    BLEND_MODE_CLASSIC: "One color for the most urgent state.",
}

# Longer versions for a tooltip, so the detail is available without
# permanently taking up space in the window.
BLEND_MODE_TOOLTIPS: dict[str, str] = {
    BLEND_MODE_ROUND_ROBIN: (
        "Every LED breathes in one agent's own color -- the sequence repeats "
        "across the strip, all at once."
    ),
    BLEND_MODE_RELAY: (
        "One agent's LED flares bright while the rest rest dim -- the "
        "spotlight passes around the ring, agent to agent, like a baton."
    ),
    BLEND_MODE_SPATIAL: "Each agent claims a block of LEDs sized by urgency -- more agents, smaller blocks.",
    BLEND_MODE_COLOR: (
        "Every LED shows one color: the weighted average of all active "
        "agents -- can look muddy with dissimilar hues."
    ),
    BLEND_MODE_CYCLE: "The whole strip shows one agent's color at a time, breathing, then hands off to the next.",
    BLEND_MODE_CLASSIC: "Ignores individual agents -- shows one color for the most urgent mode across everyone.",
}

LAYOUT_DEBOUNCE_SECONDS = 1.5
LAYOUT_CROSSFADE_MS = 450
IDLE_ROLL_SECONDS = 7.0
IDLE_ROLL_MIN_AGENTS = 3

# User-configurable "how long is one breath" for Round-Robin and Cycle --
# both are about seeing distinct agents in turn/at a glance rather than one
# state's own fixed animation cadence. cycle_speed_seconds on ColorSettings
# is the global default; SPEED_OVERRIDE_MODES lists which blend modes can
# each have their own independent speed instead of following the global one.
DEFAULT_CYCLE_SPEED_SECONDS = 1.6
MIN_CYCLE_SPEED_SECONDS = 0.3
MAX_CYCLE_SPEED_SECONDS = 10.0
SPEED_OVERRIDE_MODES: tuple[str, ...] = (BLEND_MODE_ROUND_ROBIN, BLEND_MODE_CYCLE)
# Per-LED stagger within one Round-Robin breath cycle, as a fraction of the
# cycle speed -- gives the repeating pattern a traveling-wave feel instead
# of every LED breathing in flat unison.
ROUND_ROBIN_STAGGER_FRACTION = 0.12

# When on (default), an agent that's Waiting for Input or Blocked/Error
# shows the Ask mode color instead of its own identity color while in
# Round-Robin or Cycle -- otherwise a blocked agent is just "whichever color
# it happens to be," indistinguishable from an agent that's simply idle.
DEFAULT_URGENCY_ALERT_ENABLED = True

# The attention takeover: with the urgency alert on, ANY multi-agent
# program with a Waiting-for-Input/Blocked agent opens each loop with a
# full-bar double hard-flash in the Ask color at full ceiling. A per-slot
# color swap alone is not an alert -- it pulses at the same rhythm and
# brightness as every working neighbor, and the Ask color can literally
# coincide with another agent's identity color (#FF3A00 is both the Ask
# default AND Codex's brand color). No steady pulse can be confused with
# a double flash of the whole bar; the temporal signature IS the alarm.
ATTENTION_FLASH_MS = 240
ATTENTION_FLASH_GAP_MS = 140
ATTENTION_REST_MS = 900

# A quick twinkle-then-bloom when an agent finishes, rather than an
# instant snap to the solid Done color -- a small reward moment. Default
# on; some may find any extra motion distracting, so it's a plain toggle.
DEFAULT_DONE_CELEBRATION_ENABLED = True


# --- Hex helpers ---------------------------------------------------------


def normalize_hex(value: object, fallback: str) -> str:
    """Validate a hex color string, or fall back to a known-good default.

    Never raises: bad input (wrong type, wrong length, non-hex characters)
    always resolves to ``fallback`` rather than propagating an error into a
    settings load or a render pass.
    """
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) != 6:
        return fallback
    try:
        int(text, 16)
    except ValueError:
        return fallback
    return f"#{text.upper()}"


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    red, green, blue = (max(0, min(255, round(channel))) for channel in rgb)
    return f"#{red:02X}{green:02X}{blue:02X}"


def weighted_blend(entries: list[tuple[str, float]]) -> str:
    """Weighted RGB average of one or more ``(hex_color, weight)`` pairs."""
    if not entries:
        return "#000000"
    total_weight = sum(weight for _, weight in entries) or 1.0
    red = green = blue = 0.0
    for color, weight in entries:
        source_red, source_green, source_blue = hex_to_rgb(color)
        red += source_red * weight
        green += source_green * weight
        blue += source_blue * weight
    return rgb_to_hex((red / total_weight, green / total_weight, blue / total_weight))


# --- Default assignment ---------------------------------------------------


# Approximate brand colors for each currently-supported provider, so an
# agent's LED evokes its actual product instead of an arbitrary distinct
# hue. Sourced from each company's own published brand palette where
# findable:
#   - codex: OpenAI's own documented secondary accent ("Azure", #2B8FFF)
#     -- OpenAI's primary brand accent is actually green (#10A37F); Azure
#     is real OpenAI-documented blue, not invented, and matches what was
#     asked for.
#   - claude: Anthropic's documented terracotta/"Claude orange" (#D97757).
#   - devin / grok: Cognition's and xAI's literal brand colors are both
#     near-black monochrome (Cognition's "Swamp" #001423, xAI's "Mine
#     Shaft" #313131) -- accurate, but functionally invisible as a lit LED,
#     especially through the fade ceiling. These two are brightened to the
#     same *character* (deep navy, neutral grey) rather than the literal
#     hex, so they're still recognizable as "Devin" / "Grok" and not just
#     unlit.
# Devin's navy and Codex's blue share a hue family on purpose (both are
# genuinely "blue" brands) -- they're kept apart mainly by lightness/
# saturation (bright azure vs. dark navy) rather than hue, which is a
# real, deliberate trade of the maximal-distinctness goal for brand
# fidelity, per explicit request.
PROVIDER_BRAND_COLORS: dict[str, str] = {
    "codex": "#2B8FFF",
    "claude": "#D97757",
    "devin": "#1D3461",
    "grok": "#8E8E93",
}


def default_agent_color(provider: str) -> str:
    """Default color for a provider: its brand color if known, otherwise a
    deterministic slot from CURATED_PALETTE indexed by its position in
    PROVIDER_SPECS. A future provider added to that registry (without an
    entry in PROVIDER_BRAND_COLORS) gets the next unused palette slot
    automatically; an entirely unknown provider id (e.g. one seen in a
    snapshot before this module knows about it) falls back to a hash-based
    slot so it's still deterministic and collision-free in the common case,
    without requiring a settings migration.
    """
    if provider in PROVIDER_BRAND_COLORS:
        return PROVIDER_BRAND_COLORS[provider]
    provider_ids = [spec.provider for spec in PROVIDER_SPECS]
    if provider in provider_ids:
        index = provider_ids.index(provider)
    else:
        index = len(provider_ids) + (hash(provider) % len(CURATED_PALETTE))
    return CURATED_PALETTE[index % len(CURATED_PALETTE)]


def _default_mode_colors() -> dict[str, str]:
    # Seeded directly from led_status.py's existing constants so Classic
    # mode's output, and a fresh install's defaults, match today's behavior
    # byte-for-byte.
    return {
        MODE_IDLE: IDLE_DIM,
        MODE_WORKING: WORKING_CYAN,
        MODE_DONE: DONE_GREEN,
        MODE_ASK: ASK_AMBER,
    }


def _default_agent_colors() -> dict[str, str]:
    return {spec.provider: default_agent_color(spec.provider) for spec in PROVIDER_SPECS}


def _default_fade_floor() -> dict[str, float]:
    return {key: DEFAULT_FADE_FLOOR for key in FADE_MODE_KEYS}


def _default_fade_ceiling() -> dict[str, float]:
    return {key: DEFAULT_FADE_CEILING for key in FADE_MODE_KEYS}


def normalize_fade_fraction(value: object, fallback: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return fallback
    return max(0.0, min(1.0, float(value)))


def normalize_cycle_speed(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return DEFAULT_CYCLE_SPEED_SECONDS
    return max(MIN_CYCLE_SPEED_SECONDS, min(MAX_CYCLE_SPEED_SECONDS, float(value)))


def _default_mode_animation() -> dict[str, str]:
    return dict(DEFAULT_MODE_ANIMATION)


# --- ColorSettings ---------------------------------------------------------


@dataclass(frozen=True)
class ColorSettings:
    mode_colors: dict[str, str] = field(default_factory=_default_mode_colors)
    agent_colors: dict[str, str] = field(default_factory=_default_agent_colors)
    blend_mode: str = DEFAULT_BLEND_MODE
    fade_floor: dict[str, float] = field(default_factory=_default_fade_floor)
    fade_ceiling: dict[str, float] = field(default_factory=_default_fade_ceiling)
    mode_animation: dict[str, str] = field(default_factory=_default_mode_animation)
    cycle_speed_seconds: float = DEFAULT_CYCLE_SPEED_SECONDS
    # Per-blend-mode overrides for the "one breath" duration -- a mode key
    # present here uses its own speed instead of the global
    # cycle_speed_seconds. Keys are restricted to SPEED_OVERRIDE_MODES.
    speed_overrides: dict[str, float] = field(default_factory=dict)
    round_robin_urgency_alert: bool = DEFAULT_URGENCY_ALERT_ENABLED
    # A one-shot twinkle-then-bloom flourish plays when settling into Done
    # instead of an instant snap to the solid color -- see
    # led_status.program_for_display_state's done_celebrate parameter.
    # Only reaches the single-agent/aggregate rendering path (see
    # _single_agent_program) -- a multi-agent blend mode's own Done
    # segments stay plain, since those live inside a shared repeat loop
    # with other agents' active animations and can't safely host a
    # one-shot effect without replaying it every loop.
    done_celebration_enabled: bool = DEFAULT_DONE_CELEBRATION_ENABLED

    @classmethod
    def defaults(cls) -> "ColorSettings":
        return cls(
            mode_colors=_default_mode_colors(),
            agent_colors=_default_agent_colors(),
            blend_mode=DEFAULT_BLEND_MODE,
            fade_floor=_default_fade_floor(),
            fade_ceiling=_default_fade_ceiling(),
            mode_animation=_default_mode_animation(),
            cycle_speed_seconds=DEFAULT_CYCLE_SPEED_SECONDS,
            speed_overrides={},
            round_robin_urgency_alert=DEFAULT_URGENCY_ALERT_ENABLED,
            done_celebration_enabled=DEFAULT_DONE_CELEBRATION_ENABLED,
        )

    def mode_color(self, key: str) -> str:
        fallback = _default_mode_colors().get(key, IDLE_DIM)
        return normalize_hex(self.mode_colors.get(key), fallback)

    def agent_color(self, provider: str) -> str:
        fallback = default_agent_color(provider)
        return normalize_hex(self.agent_colors.get(provider), fallback)

    def fade_range(self, key: str) -> tuple[float, float]:
        """Returns (floor, ceiling) as fractions 0.0-1.0 for a pulsing mode
        key. Modes outside FADE_MODE_KEYS (i.e. Done) always return (0, 1)
        since they don't pulse."""
        if key not in FADE_MODE_KEYS:
            return (0.0, 1.0)
        floor = normalize_fade_fraction(self.fade_floor.get(key), DEFAULT_FADE_FLOOR)
        ceiling = normalize_fade_fraction(self.fade_ceiling.get(key), DEFAULT_FADE_CEILING)
        if floor > ceiling:
            floor, ceiling = ceiling, floor
        return (floor, ceiling)

    def animation_style(self, key: str) -> str:
        """The animation style for a mode key. Modes outside
        ANIMATION_MODE_KEYS (i.e. Done) always report solid -- it's a fixed
        color, there's no style to pick."""
        if key not in ANIMATION_MODE_KEYS:
            return ANIMATION_STYLE_SOLID
        style = self.mode_animation.get(key)
        if style not in ANIMATION_STYLE_CHOICES:
            return DEFAULT_MODE_ANIMATION[key]
        return style

    def with_mode_color(self, key: str, hex_value: str) -> "ColorSettings":
        if key not in MODE_COLOR_KEYS:
            raise ValueError(f"Unknown mode color key: {key}")
        fallback = _default_mode_colors()[key]
        colors = dict(self.mode_colors)
        colors[key] = normalize_hex(hex_value, fallback)
        return replace(self, mode_colors=colors)

    def with_agent_color(self, provider: str, hex_value: str) -> "ColorSettings":
        fallback = default_agent_color(provider)
        colors = dict(self.agent_colors)
        colors[provider] = normalize_hex(hex_value, fallback)
        return replace(self, agent_colors=colors)

    def with_blend_mode(self, mode: str) -> "ColorSettings":
        if mode not in BLEND_MODE_CHOICES:
            raise ValueError(f"Unknown blend mode: {mode}")
        return replace(self, blend_mode=mode)

    def with_fade_floor(self, key: str, value: float) -> "ColorSettings":
        if key not in FADE_MODE_KEYS:
            raise ValueError(f"Unknown fade mode key: {key}")
        floors = dict(self.fade_floor)
        floors[key] = normalize_fade_fraction(value, DEFAULT_FADE_FLOOR)
        return replace(self, fade_floor=floors)

    def with_fade_ceiling(self, key: str, value: float) -> "ColorSettings":
        if key not in FADE_MODE_KEYS:
            raise ValueError(f"Unknown fade mode key: {key}")
        ceilings = dict(self.fade_ceiling)
        ceilings[key] = normalize_fade_fraction(value, DEFAULT_FADE_CEILING)
        return replace(self, fade_ceiling=ceilings)

    def with_mode_animation(self, key: str, style: str) -> "ColorSettings":
        if key not in ANIMATION_MODE_KEYS:
            raise ValueError(f"Unknown animation mode key: {key}")
        if style not in ANIMATION_STYLE_CHOICES:
            raise ValueError(f"Unknown animation style: {style}")
        animations = dict(self.mode_animation)
        animations[key] = style
        return replace(self, mode_animation=animations)

    def with_cycle_speed(self, seconds: float) -> "ColorSettings":
        """Sets the global speed. Does not affect any mode that has its own
        override via with_speed_override()."""
        return replace(self, cycle_speed_seconds=normalize_cycle_speed(seconds))

    def effective_speed_seconds(self, blend_mode: str) -> float:
        """The speed actually used when rendering `blend_mode`: its own
        override if it has one, otherwise the global cycle_speed_seconds."""
        if blend_mode in self.speed_overrides:
            return normalize_cycle_speed(self.speed_overrides[blend_mode])
        return self.cycle_speed_seconds

    def uses_global_speed(self, blend_mode: str) -> bool:
        return blend_mode not in self.speed_overrides

    def with_speed_override(self, blend_mode: str, seconds: float) -> "ColorSettings":
        if blend_mode not in SPEED_OVERRIDE_MODES:
            raise ValueError(f"Unknown speed-override mode: {blend_mode}")
        overrides = dict(self.speed_overrides)
        overrides[blend_mode] = normalize_cycle_speed(seconds)
        return replace(self, speed_overrides=overrides)

    def with_global_speed_for_mode(self, blend_mode: str) -> "ColorSettings":
        """Reverts `blend_mode` back to following the global speed."""
        if blend_mode not in SPEED_OVERRIDE_MODES:
            raise ValueError(f"Unknown speed-override mode: {blend_mode}")
        if blend_mode not in self.speed_overrides:
            return self
        overrides = dict(self.speed_overrides)
        del overrides[blend_mode]
        return replace(self, speed_overrides=overrides)

    def with_round_robin_urgency_alert(self, enabled: bool) -> "ColorSettings":
        return replace(self, round_robin_urgency_alert=bool(enabled))

    def with_done_celebration_enabled(self, enabled: bool) -> "ColorSettings":
        return replace(self, done_celebration_enabled=bool(enabled))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode_colors": dict(self.mode_colors),
            "agent_colors": dict(self.agent_colors),
            "blend_mode": self.blend_mode,
            "fade_floor": dict(self.fade_floor),
            "fade_ceiling": dict(self.fade_ceiling),
            "mode_animation": dict(self.mode_animation),
            "cycle_speed_seconds": self.cycle_speed_seconds,
            "speed_overrides": dict(self.speed_overrides),
            "round_robin_urgency_alert": self.round_robin_urgency_alert,
            "done_celebration_enabled": self.done_celebration_enabled,
        }

    @classmethod
    def from_dict(cls, data: object) -> "ColorSettings":
        defaults = cls.defaults()
        if not isinstance(data, dict):
            return defaults

        mode_colors = dict(defaults.mode_colors)
        raw_modes = data.get("mode_colors")
        if isinstance(raw_modes, dict):
            for key in MODE_COLOR_KEYS:
                if key in raw_modes:
                    mode_colors[key] = normalize_hex(raw_modes[key], defaults.mode_colors[key])

        agent_colors = dict(defaults.agent_colors)
        raw_agents = data.get("agent_colors")
        if isinstance(raw_agents, dict):
            for provider, value in raw_agents.items():
                if isinstance(provider, str):
                    agent_colors[provider] = normalize_hex(value, default_agent_color(provider))

        blend_mode = data.get("blend_mode")
        if blend_mode not in BLEND_MODE_CHOICES:
            blend_mode = DEFAULT_BLEND_MODE

        fade_floor = dict(defaults.fade_floor)
        raw_floor = data.get("fade_floor")
        if isinstance(raw_floor, dict):
            for key in FADE_MODE_KEYS:
                if key in raw_floor:
                    fade_floor[key] = normalize_fade_fraction(raw_floor[key], defaults.fade_floor[key])

        fade_ceiling = dict(defaults.fade_ceiling)
        raw_ceiling = data.get("fade_ceiling")
        if isinstance(raw_ceiling, dict):
            for key in FADE_MODE_KEYS:
                if key in raw_ceiling:
                    fade_ceiling[key] = normalize_fade_fraction(
                        raw_ceiling[key], defaults.fade_ceiling[key]
                    )

        mode_animation = dict(defaults.mode_animation)
        raw_animation = data.get("mode_animation")
        if isinstance(raw_animation, dict):
            for key in ANIMATION_MODE_KEYS:
                value = raw_animation.get(key)
                if value in ANIMATION_STYLE_CHOICES:
                    mode_animation[key] = value

        cycle_speed_seconds = normalize_cycle_speed(data.get("cycle_speed_seconds"))

        speed_overrides: dict[str, float] = {}
        raw_overrides = data.get("speed_overrides")
        if isinstance(raw_overrides, dict):
            for key in SPEED_OVERRIDE_MODES:
                value = raw_overrides.get(key)
                # A malformed value is dropped entirely (falls back to
                # "uses global"), not coerced to the current default and
                # stored as if it were a real override.
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    speed_overrides[key] = normalize_cycle_speed(value)

        raw_urgency = data.get("round_robin_urgency_alert")
        round_robin_urgency_alert = (
            bool(raw_urgency) if isinstance(raw_urgency, bool) else DEFAULT_URGENCY_ALERT_ENABLED
        )

        raw_celebration = data.get("done_celebration_enabled")
        done_celebration_enabled = (
            bool(raw_celebration) if isinstance(raw_celebration, bool) else DEFAULT_DONE_CELEBRATION_ENABLED
        )

        return cls(
            mode_colors=mode_colors,
            agent_colors=agent_colors,
            blend_mode=blend_mode,
            fade_floor=fade_floor,
            fade_ceiling=fade_ceiling,
            mode_animation=mode_animation,
            cycle_speed_seconds=cycle_speed_seconds,
            speed_overrides=speed_overrides,
            round_robin_urgency_alert=round_robin_urgency_alert,
            done_celebration_enabled=done_celebration_enabled,
        )


# --- Urgency weighting -----------------------------------------------------


PRESET_CUSTOM = "custom"
PRESET_CALM = "calm"
PRESET_INFORMATIVE = "informative"
PRESET_EVERYTHING = "everything"
PRESET_CHOICES: tuple[str, ...] = (PRESET_CALM, PRESET_INFORMATIVE, PRESET_EVERYTHING)
PRESET_LABELS: dict[str, str] = {
    PRESET_CUSTOM: "Custom",
    PRESET_CALM: "Calm",
    PRESET_INFORMATIVE: "Informative",
    PRESET_EVERYTHING: "Everything",
}
PRESET_DESCRIPTIONS: dict[str, str] = {
    PRESET_CALM: "One quiet aggregate light. Peripheral, never busy.",
    PRESET_INFORMATIVE: "Every agent visible, breathing together.",
    PRESET_EVERYTHING: "Full show: spotlight relay, motion, celebrations.",
}


def apply_preset(colors: "ColorSettings", preset: str) -> "ColorSettings":
    """One-click personalities for the whole display engine -- each sets
    blend mode, animation styles, speed, fade range, and event toggles as
    a coherent package. Agent identity colors are deliberately untouched:
    a preset changes how the light BEHAVES, never whose color it is.
    """
    if preset == PRESET_CALM:
        result = colors.with_blend_mode(BLEND_MODE_CLASSIC).with_cycle_speed(2.4)
        for key in ANIMATION_MODE_KEYS:
            result = result.with_mode_animation(key, ANIMATION_STYLE_PULSE)
        for key in FADE_MODE_KEYS:
            result = result.with_fade_floor(key, 0.01).with_fade_ceiling(key, 0.35)
        return result.with_round_robin_urgency_alert(True).with_done_celebration_enabled(False)
    if preset == PRESET_INFORMATIVE:
        result = colors.with_blend_mode(BLEND_MODE_ROUND_ROBIN).with_cycle_speed(1.6)
        result = (
            result.with_mode_animation(MODE_IDLE, ANIMATION_STYLE_PULSE)
            .with_mode_animation(MODE_WORKING, ANIMATION_STYLE_PULSE)
            .with_mode_animation(MODE_ASK, ANIMATION_STYLE_BLINK)
        )
        for key in FADE_MODE_KEYS:
            result = result.with_fade_floor(key, 0.01).with_fade_ceiling(key, 0.5)
        return result.with_round_robin_urgency_alert(True).with_done_celebration_enabled(True)
    if preset == PRESET_EVERYTHING:
        result = colors.with_blend_mode(BLEND_MODE_RELAY).with_cycle_speed(1.2)
        result = (
            result.with_mode_animation(MODE_IDLE, ANIMATION_STYLE_PULSE)
            .with_mode_animation(MODE_WORKING, ANIMATION_STYLE_ROLL)
            .with_mode_animation(MODE_ASK, ANIMATION_STYLE_BLINK)
        )
        for key in FADE_MODE_KEYS:
            result = result.with_fade_floor(key, 0.02).with_fade_ceiling(key, 0.7)
        return result.with_round_robin_urgency_alert(True).with_done_celebration_enabled(True)
    raise ValueError(f"Unknown preset: {preset}")


def matching_preset(colors: "ColorSettings") -> str:
    """Which preset the current settings exactly correspond to, or
    PRESET_CUSTOM -- so the preset picker can honestly show "Custom" the
    moment any manual tweak diverges from a package."""
    for preset in PRESET_CHOICES:
        if apply_preset(colors, preset) == colors:
            return preset
    return PRESET_CUSTOM


def urgency_weight(mode: AgentMode) -> int:
    """Higher weight = more urgent = larger LED share / more blend influence.

    ``MODE_PRIORITY`` uses 1 (most urgent, Blocked/Error) through 7 (least
    urgent, Idle/Ready), plus 99 for Unknown. Weight is the inverse of that,
    clamped so nothing ever gets a non-positive share.
    """
    priority = MODE_PRIORITY.get(mode, MODE_PRIORITY[AgentMode.UNKNOWN])
    if priority > 7:
        return 1
    return max(1, 8 - priority)


# --- Per-agent single-block program ----------------------------------------


def _color_kwargs_for_state(state: LedDisplayState, color: str) -> dict[str, str]:
    kwarg = _MODE_KEY_TO_COLOR_KWARG[_STATE_TO_MODE_KEY[state]]
    return {kwarg: color}


def _fade_kwargs_for_state(state: LedDisplayState, settings: ColorSettings) -> dict[str, float | str]:
    mode_key = _STATE_TO_MODE_KEY[state]
    if mode_key not in FADE_MODE_KEYS:
        return {}
    floor, ceiling = settings.fade_range(mode_key)
    return {
        _MODE_KEY_TO_FLOOR_KWARG[mode_key]: floor,
        _MODE_KEY_TO_CEILING_KWARG[mode_key]: ceiling,
        _MODE_KEY_TO_STYLE_KWARG[mode_key]: settings.animation_style(mode_key),
    }


def _fade_kwargs_for_all_modes(settings: ColorSettings) -> dict[str, float | str]:
    """All *_floor/*_ceiling/*_style kwargs program_for_display_state()
    accepts, for call sites that don't know the state up front (it branches
    internally)."""
    kwargs: dict[str, float | str] = {}
    for mode_key in FADE_MODE_KEYS:
        floor, ceiling = settings.fade_range(mode_key)
        kwargs[_MODE_KEY_TO_FLOOR_KWARG[mode_key]] = floor
        kwargs[_MODE_KEY_TO_CEILING_KWARG[mode_key]] = ceiling
        kwargs[_MODE_KEY_TO_STYLE_KWARG[mode_key]] = settings.animation_style(mode_key)
    return kwargs


def _single_agent_program(
    color: str,
    state: LedDisplayState,
    *,
    led_count: int,
    brightness: int | float,
    settings: ColorSettings,
) -> str:
    return program_for_display_state(
        state,
        led_count=led_count,
        brightness=brightness,
        done_celebrate=settings.done_celebration_enabled,
        **_color_kwargs_for_state(state, color),
        **_fade_kwargs_for_state(state, settings),
    )


# --- Snapshot rendering -----------------------------------------------------


@dataclass(frozen=True)
class _ActiveAgent:
    provider: str
    color: str
    state: LedDisplayState
    weight: int


_PROVIDER_ORDER_INDEX: dict[str, int] = {spec.provider: index for index, spec in enumerate(PROVIDER_SPECS)}


def _stable_agent_sort_key(status: AgentStatus) -> tuple[int, str]:
    """A fixed, content-independent ordering (registry order, then provider
    id alphabetically for anything unregistered) -- deliberately ignoring
    recency/priority. ``statuses`` arrives already sorted most-actionable-
    first for the human-facing status list, but reusing that order here
    would reshuffle which LED each agent occupies every time any agent's
    ``updated_at`` ticks forward (the common case with two-plus agents
    active at once), forcing a full strip rewrite -- and therefore a visible
    restart -- on nearly every poll. Sorting by identity instead means an
    agent keeps the same LED slot(s) for as long as it stays active."""
    return (_PROVIDER_ORDER_INDEX.get(status.provider, len(_PROVIDER_ORDER_INDEX)), status.provider)


def _active_agents(statuses: tuple[AgentStatus, ...], colors: ColorSettings) -> list[_ActiveAgent]:
    agents: list[_ActiveAgent] = []
    for status in sorted(statuses, key=_stable_agent_sort_key):
        agents.append(
            _ActiveAgent(
                provider=status.provider,
                color=colors.agent_color(status.provider),
                state=display_state_for_mode(status.mode),
                weight=urgency_weight(status.mode),
            )
        )
    return agents


def _representative_state(agents: list[_ActiveAgent]) -> LedDisplayState:
    if not agents:
        return LedDisplayState.IDLE
    # Highest weight (most urgent) wins; ties keep the first (statuses are
    # already sorted most-actionable-first by the collector).
    return max(agents, key=lambda agent: agent.weight).state


def _color_blend_program(
    agents: list[_ActiveAgent],
    *,
    led_count: int,
    brightness: int | float,
    settings: ColorSettings,
) -> str:
    blended = weighted_blend([(agent.color, float(agent.weight)) for agent in agents])
    state = _representative_state(agents)
    return _single_agent_program(blended, state, led_count=led_count, brightness=brightness, settings=settings)


def _display_color_for_agent(agent: _ActiveAgent, settings: ColorSettings) -> str:
    """The color to render for this agent -- normally its own identity
    color, but the Ask mode color when urgency alerting is on and this
    agent is Waiting for Input / Blocked. Without this, a blocked agent in
    Round-Robin/Cycle is "whichever color it happens to be," indistinguishable
    from simply idle -- there's no other urgency signal in those two modes
    the way block-size does the job in Spatial Split. Scoped to Round-Robin
    and Cycle only, since Spatial Split/Color Blend/Classic already
    communicate urgency some other way.
    """
    if settings.round_robin_urgency_alert and agent.state == LedDisplayState.ASK:
        return settings.mode_color(MODE_ASK)
    return agent.color


def _cycle_program(
    agents: list[_ActiveAgent],
    *,
    led_count: int,
    brightness: int | float,
    settings: ColorSettings,
) -> str:
    # Show each active agent's own color in turn across the whole strip --
    # a real breath (dim -> bright -> dim) per agent, not a flat fade-and-
    # hold, using the user-configurable cycle speed.
    if len(agents) == 1:
        agent = agents[0]
        return _single_agent_program(
            agent.color, agent.state, led_count=led_count, brightness=brightness, settings=settings
        )

    duration_ms = max(1, int(settings.effective_speed_seconds(BLEND_MODE_CYCLE) * 1000))
    settle_ms = settle_duration_ms(duration_ms)
    # Eased rather than a bare "off" -- a bare assignment is an instant,
    # un-eased snap (see settle_duration_ms()), which is only ever invisible
    # once steady-state; the moment a real status change interrupts this
    # loop mid-breath, a bare snap reads as the animation abruptly stopping
    # instead of easing into the next cycle.
    lines: list[str] = [f"off {settle_ms}ms cosine"]
    for agent in agents:
        color = _display_color_for_agent(agent, settings)
        if agent.state == LedDisplayState.DONE:
            lines.append(f"{color} {duration_ms}ms cosine")
            continue
        _floor, ceiling = settings.fade_range(_STATE_TO_MODE_KEY[agent.state])
        peak = color if ceiling >= 1.0 else scale_hex_brightness(color, ceiling)
        lines.append(f"{peak} {duration_ms}ms pulse")
    lines.append("repeat")
    return apply_brightness("\n".join(lines), brightness)


def _round_robin_program(
    agents: list[_ActiveAgent],
    *,
    led_count: int,
    brightness: int | float,
    settings: ColorSettings,
) -> str:
    """Repeats the active-agent sequence across every LED (3 agents on 8
    LEDs -> A,B,C,A,B,C,A,B), each LED breathing independently and
    simultaneously in its own agent's pure color -- every active agent is
    visible at a glance, nothing is averaged into mud. A small per-LED
    stagger gives the repeating pattern a traveling-wave feel instead of
    every LED breathing in flat unison."""
    if len(agents) == 1:
        agent = agents[0]
        return _single_agent_program(
            agent.color, agent.state, led_count=led_count, brightness=brightness, settings=settings
        )

    duration_ms = max(1, int(settings.effective_speed_seconds(BLEND_MODE_ROUND_ROBIN) * 1000))
    stagger_ms = max(0, int(duration_ms * ROUND_ROBIN_STAGGER_FRACTION))
    # Scaled to this program's own speed rather than a flat 160ms -- at the
    # fast end of the user's speed range (down to 300ms/cycle) a fixed
    # 160ms reset line would eat over half of every cycle as dead time.
    settle_ms = settle_duration_ms(duration_ms)

    reset_segments: list[str] = []
    pulse_segments: list[str] = []
    for index in range(led_count):
        agent = agents[index % len(agents)]
        color = _display_color_for_agent(agent, settings)
        if agent.state == LedDisplayState.DONE:
            reset_segments.append(f"{index}:{color} {settle_ms}ms cosine")
            pulse_segments.append(f"{index}:{color} {settle_ms}ms cosine")
            continue
        floor, ceiling = settings.fade_range(_STATE_TO_MODE_KEY[agent.state])
        floor_color = "off" if floor <= 0.0 else scale_hex_brightness(color, floor)
        peak_color = color if ceiling >= 1.0 else scale_hex_brightness(color, ceiling)
        delay = (index * stagger_ms) % duration_ms
        reset_segments.append(f"{index}:{floor_color} {settle_ms}ms cosine")
        pulse_segments.append(f"{index}:{peak_color} {duration_ms}ms pulse {delay}ms")

    program_lines = ["; ".join(reset_segments), "; ".join(pulse_segments), "repeat"]
    return apply_brightness("\n".join(program_lines), brightness)


def _relay_program(
    agents: list[_ActiveAgent],
    *,
    led_count: int,
    brightness: int | float,
    settings: ColorSettings,
) -> str:
    """A baton pass: exactly one LED flares to its agent's peak color at a
    time, in a chase around the ring, while every other LED holds a soft
    resting glow in its own agent's color. Same per-LED agent assignment as
    Round-Robin (the sequence repeats across the strip), but with a full
    stagger instead of a small one -- Round-Robin reads as one synchronized
    wave breathing together; Relay reads as a single spotlight visiting each
    agent in turn before moving on."""
    if len(agents) == 1:
        agent = agents[0]
        return _single_agent_program(
            agent.color, agent.state, led_count=led_count, brightness=brightness, settings=settings
        )

    duration_ms = max(1, int(settings.effective_speed_seconds(BLEND_MODE_RELAY) * 1000))
    # The firmware caps every duration/delay at 65535 ms and a parse
    # error blinks the whole device red -- the full stagger below peaks
    # at (led_count - 1) * duration_ms, so cap the per-turn duration to
    # keep the largest delay legal even at the slowest user speed.
    duration_ms = min(duration_ms, 65535 // max(1, led_count - 1))
    settle_ms = settle_duration_ms(duration_ms)

    reset_segments: list[str] = []
    pulse_segments: list[str] = []
    for index in range(led_count):
        agent = agents[index % len(agents)]
        color = _display_color_for_agent(agent, settings)
        if agent.state == LedDisplayState.DONE:
            reset_segments.append(f"{index}:{color} {settle_ms}ms cosine")
            pulse_segments.append(f"{index}:{color} {settle_ms}ms cosine")
            continue
        floor, ceiling = settings.fade_range(_STATE_TO_MODE_KEY[agent.state])
        floor_color = "off" if floor <= 0.0 else scale_hex_brightness(color, floor)
        peak_color = color if ceiling >= 1.0 else scale_hex_brightness(color, ceiling)
        # Full stagger -- one LED's entire turn elapses before the next
        # one's delay expires, so only one LED is ever mid-flare.
        delay = index * duration_ms
        reset_segments.append(f"{index}:{floor_color} {settle_ms}ms cosine")
        pulse_segments.append(f"{index}:{peak_color} {duration_ms}ms pulse {delay}ms")

    program_lines = ["; ".join(reset_segments), "; ".join(pulse_segments), "repeat"]
    return apply_brightness("\n".join(program_lines), brightness)


def _spatial_split_blocks(agents: list[_ActiveAgent], led_count: int) -> list[tuple[_ActiveAgent, int]]:
    total_weight = sum(agent.weight for agent in agents) or 1
    blocks: list[tuple[_ActiveAgent, int]] = []
    assigned = 0
    for agent in agents:
        count = max(1, round(agent.weight / total_weight * led_count))
        blocks.append((agent, count))
        assigned += count

    # Reconcile rounding drift against led_count, adjusting the
    # highest-weight (most urgent) agent's block so the total is exact.
    drift = led_count - assigned
    if drift != 0 and blocks:
        top_index = max(range(len(blocks)), key=lambda i: blocks[i][0].weight)
        top_agent, top_count = blocks[top_index]
        blocks[top_index] = (top_agent, max(1, top_count + drift))

    # If adjustment pushed the total over led_count (e.g. many agents each
    # with a forced minimum of 1), trim from the lowest-weight blocks first.
    total = sum(count for _, count in blocks)
    order = sorted(range(len(blocks)), key=lambda i: blocks[i][0].weight)
    index = 0
    while total > led_count and index < len(order):
        pos = order[index]
        agent, count = blocks[pos]
        if count > 1:
            blocks[pos] = (agent, count - 1)
            total -= 1
        else:
            index += 1
    return blocks


def _spatial_split_program(
    agents: list[_ActiveAgent],
    *,
    led_count: int,
    brightness: int | float,
    settings: ColorSettings,
) -> str:
    if len(agents) > led_count:
        return _color_blend_program(agents, led_count=led_count, brightness=brightness, settings=settings)

    blocks = _spatial_split_blocks(agents, led_count)

    segments: list[str] = []
    reset_segments: list[str] = []
    index = 0
    for agent, count in blocks:
        for offset in range(count):
            led_index = index + offset
            segments.append(_segment_for_agent(led_index, agent, settings))
            reset_segments.append(_reset_segment_for_agent(led_index, agent, settings))
        index += count

    # Per-index reset line (rather than one blanket "off") so each LED
    # settles to its own agent's configured fade floor -- different active
    # agents can have different modes, and therefore different floors.
    program_lines = ["; ".join(reset_segments), "; ".join(segments), "repeat"]

    living_roll = len(agents) >= IDLE_ROLL_MIN_AGENTS and led_count >= IDLE_ROLL_MIN_AGENTS
    if living_roll:
        # A slow, purely cosmetic background rotation so a busy strip keeps
        # some motion even between reshuffles. Inserted before "repeat" so
        # it loops with everything else.
        program_lines = program_lines[:-1] + [f"roll {IDLE_ROLL_SECONDS:g}s linear"] + program_lines[-1:]

    return apply_brightness("\n".join(program_lines), brightness)


_SEGMENT_DURATION_BY_STATE: dict[LedDisplayState, str] = {
    LedDisplayState.IDLE: "6s",
    LedDisplayState.ASK: "1.6s",
    LedDisplayState.WORKING: "760ms",
}
_SEGMENT_DURATION_MS_BY_STATE: dict[LedDisplayState, int] = {
    LedDisplayState.IDLE: 6000,
    LedDisplayState.ASK: 1600,
    LedDisplayState.WORKING: 760,
}
# DONE has no natural duration of its own to scale a settle time against
# (it's a static hold, not a breathing cycle) -- use the cheapest settle
# the adaptive helper allows.
_DONE_SETTLE_MS = settle_duration_ms(0)


def _segment_for_agent(led_index: int, agent: _ActiveAgent, settings: ColorSettings) -> str:
    if agent.state == LedDisplayState.DONE:
        return f"{led_index}:{agent.color} {_DONE_SETTLE_MS}ms cosine"
    mode_key = _STATE_TO_MODE_KEY[agent.state]
    _floor, ceiling = settings.fade_range(mode_key)
    peak_color = agent.color if ceiling >= 1.0 else scale_hex_brightness(agent.color, ceiling)
    style = settings.animation_style(mode_key)
    if style == ANIMATION_STYLE_SOLID:
        settle_ms = settle_duration_ms(_SEGMENT_DURATION_MS_BY_STATE[agent.state])
        return f"{led_index}:{peak_color} {settle_ms}ms cosine"
    # Roll and Blink don't have a clean per-block equivalent within a
    # spatial-split block (Roll assumes ownership of the whole strip's
    # stagger pattern; Blink needs its own independent on/off phase rather
    # than sharing one repeat cycle with every other active agent's block)
    # -- both fall back to a plain per-LED pulse here, same as Roll already
    # did before per-mode animation styles existed.
    duration = _SEGMENT_DURATION_BY_STATE[agent.state]
    return f"{led_index}:{peak_color} {duration} pulse"


def _reset_segment_for_agent(led_index: int, agent: _ActiveAgent, settings: ColorSettings) -> str:
    mode_key = _STATE_TO_MODE_KEY[agent.state]
    if agent.state == LedDisplayState.DONE or settings.animation_style(mode_key) == ANIMATION_STYLE_SOLID:
        # Not pulsing -- assign directly, no floor to settle to first.
        settle_ms = (
            _DONE_SETTLE_MS
            if agent.state == LedDisplayState.DONE
            else settle_duration_ms(_SEGMENT_DURATION_MS_BY_STATE[agent.state])
        )
        return f"{led_index}:{agent.color} {settle_ms}ms cosine"
    floor, _ceiling = settings.fade_range(mode_key)
    floor_color = "off" if floor <= 0.0 else scale_hex_brightness(agent.color, floor)
    settle_ms = settle_duration_ms(_SEGMENT_DURATION_MS_BY_STATE[agent.state])
    return f"{led_index}:{floor_color} {settle_ms}ms cosine"


def program_for_snapshot(
    statuses: tuple[AgentStatus, ...],
    *,
    led_count: int = 8,
    colors: ColorSettings | None = None,
    brightness: int | float = 255,
    fallback_mode: AgentMode = AgentMode.IDLE_READY,
) -> tuple[LedDisplayState, str]:
    """Render an LED program for the full set of active agent statuses.

    Returns ``(representative_state, program)`` — the representative state
    is used by ``AgentLedController`` purely for change-detection bookkeeping
    parity with the single-mode path, not to select the rendered colors.

    When ``statuses`` is empty (no per-agent breakdown available, e.g. the
    monitor itself failed and the caller only knows a single fallback mode),
    this renders exactly what ``program_for_display_state`` would for
    ``fallback_mode`` -- this makes this function a strict superset of the
    single-mode path rather than silently downgrading an error/blocked
    fallback to Idle.
    """
    settings = colors or ColorSettings.defaults()
    brightness = normalize_brightness(brightness)

    if not statuses:
        state = display_state_for_mode(fallback_mode)
        program = program_for_display_state(
            state,
            led_count=led_count,
            brightness=brightness,
            idle_color=settings.mode_color(MODE_IDLE),
            working_color=settings.mode_color(MODE_WORKING),
            done_color=settings.mode_color(MODE_DONE),
            ask_color=settings.mode_color(MODE_ASK),
            done_celebrate=settings.done_celebration_enabled,
            **_fade_kwargs_for_all_modes(settings),
        )
        return state, program

    if settings.blend_mode == BLEND_MODE_CLASSIC:
        aggregate_mode = max(statuses, key=lambda status: -status.priority).mode
        state = display_state_for_mode(aggregate_mode)
        program = program_for_display_state(
            state,
            led_count=led_count,
            brightness=brightness,
            idle_color=settings.mode_color(MODE_IDLE),
            working_color=settings.mode_color(MODE_WORKING),
            done_color=settings.mode_color(MODE_DONE),
            ask_color=settings.mode_color(MODE_ASK),
            done_celebrate=settings.done_celebration_enabled,
            **_fade_kwargs_for_all_modes(settings),
        )
        return state, program

    agents = _active_agents(statuses, settings)

    if len(agents) == 1:
        agent = agents[0]
        program = _single_agent_program(
            agent.color, agent.state, led_count=led_count, brightness=brightness, settings=settings
        )
        return agent.state, program

    state = _representative_state(agents)
    if settings.blend_mode == BLEND_MODE_COLOR:
        program = _color_blend_program(agents, led_count=led_count, brightness=brightness, settings=settings)
    elif settings.blend_mode == BLEND_MODE_CYCLE:
        program = _cycle_program(agents, led_count=led_count, brightness=brightness, settings=settings)
    elif settings.blend_mode == BLEND_MODE_SPATIAL:
        program = _spatial_split_program(agents, led_count=led_count, brightness=brightness, settings=settings)
    elif settings.blend_mode == BLEND_MODE_RELAY:
        program = _relay_program(agents, led_count=led_count, brightness=brightness, settings=settings)
    else:  # Default / BLEND_MODE_ROUND_ROBIN
        program = _round_robin_program(agents, led_count=led_count, brightness=brightness, settings=settings)
    return state, _with_attention_takeover(program, agents, settings=settings, brightness=brightness)


def _with_attention_takeover(
    program: str,
    agents: "list[_ActiveAgent]",
    *,
    settings: ColorSettings,
    brightness: int | float,
) -> str:
    """Prepends the full-bar double-flash preamble (see ATTENTION_FLASH_MS)
    when the urgency alert is on and any agent is Waiting/Blocked. Applied
    to every multi-agent blend -- an urgent agent must interrupt whatever
    show is playing, not wait its turn in it. Single-agent and Classic
    paths already hard-blink on Ask and need no takeover.

    Programs also drive real SidePulse hardware, so the result must stay
    inside the controller's 20-line / 512-byte limits: if the full
    preamble doesn't fit, a single flash is tried; if even that doesn't
    fit, the program is returned unchanged rather than truncated."""
    if not settings.round_robin_urgency_alert:
        return program
    if not any(agent.state == LedDisplayState.ASK for agent in agents):
        return program
    ask = settings.mode_color(MODE_ASK)
    flash = apply_brightness(f"{ask} {ATTENTION_FLASH_MS}ms", brightness)
    double = [flash, f"off {ATTENTION_FLASH_GAP_MS}ms", flash, f"off {ATTENTION_REST_MS}ms"]
    single = [flash, f"off {ATTENTION_REST_MS}ms"]
    for preamble in (double, single):
        candidate = "\n".join([*preamble, program])
        if len(candidate.splitlines()) <= MAX_LED_LINES and len(candidate.encode("utf-8")) <= MAX_LED_BYTES:
            return candidate
    return program


def _peak_color_for_agent(agent: "_ActiveAgent", settings: ColorSettings) -> str:
    if agent.state == LedDisplayState.DONE:
        return agent.color
    _floor, ceiling = settings.fade_range(_STATE_TO_MODE_KEY[agent.state])
    return agent.color if ceiling >= 1.0 else scale_hex_brightness(agent.color, ceiling)


def _peak_color_for_agent_with_alert(agent: "_ActiveAgent", settings: ColorSettings) -> str:
    """Like _peak_color_for_agent, but applies the Round-Robin/Cycle urgency
    alert color swap first -- used by preview_led_colors() for those two
    modes so the static preview matches what program_for_snapshot() would
    actually render."""
    color = _display_color_for_agent(agent, settings)
    if agent.state == LedDisplayState.DONE:
        return color
    _floor, ceiling = settings.fade_range(_STATE_TO_MODE_KEY[agent.state])
    return color if ceiling >= 1.0 else scale_hex_brightness(color, ceiling)


def preview_led_colors(
    statuses: tuple[AgentStatus, ...],
    *,
    led_count: int = 8,
    colors: ColorSettings | None = None,
    fallback_mode: AgentMode = AgentMode.IDLE_READY,
) -> list[str]:
    """Returns one hex color per LED index -- the *peak* (fade-ceiling)
    color each LED would pulse up to. Mirrors ``program_for_snapshot``'s
    color assignment exactly (same blend mode, same weighting, same fade
    scaling) but returns plain colors instead of a DSL program, for use in
    a real visual preview (e.g. the Colors window's live-preview dots)
    rather than re-parsing generated DSL text back into colors.
    """
    settings = colors or ColorSettings.defaults()
    led_count = max(1, int(led_count))

    if not statuses:
        state = display_state_for_mode(fallback_mode)
        mode_key = _STATE_TO_MODE_KEY[state]
        base = settings.mode_color(mode_key)
        if mode_key in FADE_MODE_KEYS:
            _floor, ceiling = settings.fade_range(mode_key)
            base = base if ceiling >= 1.0 else scale_hex_brightness(base, ceiling)
        return [base] * led_count

    if settings.blend_mode == BLEND_MODE_CLASSIC:
        aggregate_mode = max(statuses, key=lambda status: -status.priority).mode
        state = display_state_for_mode(aggregate_mode)
        mode_key = _STATE_TO_MODE_KEY[state]
        base = settings.mode_color(mode_key)
        if mode_key in FADE_MODE_KEYS:
            _floor, ceiling = settings.fade_range(mode_key)
            base = base if ceiling >= 1.0 else scale_hex_brightness(base, ceiling)
        return [base] * led_count

    agents = _active_agents(statuses, settings)

    if len(agents) == 1:
        return [_peak_color_for_agent(agents[0], settings)] * led_count

    spatial_needs_fallback = settings.blend_mode == BLEND_MODE_SPATIAL and len(agents) > led_count
    if settings.blend_mode == BLEND_MODE_COLOR or spatial_needs_fallback:
        blended = weighted_blend([(agent.color, float(agent.weight)) for agent in agents])
        state = _representative_state(agents)
        mode_key = _STATE_TO_MODE_KEY[state]
        if mode_key in FADE_MODE_KEYS:
            _floor, ceiling = settings.fade_range(mode_key)
            blended = blended if ceiling >= 1.0 else scale_hex_brightness(blended, ceiling)
        return [blended] * led_count

    if settings.blend_mode == BLEND_MODE_CYCLE:
        top = max(agents, key=lambda agent: agent.weight)
        return [_peak_color_for_agent_with_alert(top, settings)] * led_count

    if settings.blend_mode == BLEND_MODE_SPATIAL:
        blocks = _spatial_split_blocks(agents, led_count)
        colors_out: list[str] = []
        for agent, count in blocks:
            colors_out.extend([_peak_color_for_agent(agent, settings)] * count)
        return colors_out

    # Default / BLEND_MODE_ROUND_ROBIN and BLEND_MODE_RELAY -- both assign
    # LEDs to agents the same way (agents[index % len(agents)]); they only
    # differ in animation timing (synchronized wave vs. one-at-a-time
    # chase), which a static peak-color preview can't show anyway.
    return [
        _peak_color_for_agent_with_alert(agents[index % len(agents)], settings) for index in range(led_count)
    ]


def demo_statuses_for_preview() -> tuple[AgentStatus, ...]:
    """A fixed, deterministic multi-agent scenario for the Colors window's
    live preview when no real agents are active -- so blend mode and color
    choices are always visible, not just when something happens to be
    running. Uses real timestamps at call time only for the required field;
    the modes/providers themselves are fixed.
    """
    now = datetime.now(timezone.utc)
    provider_ids = [spec.provider for spec in PROVIDER_SPECS]
    demo_modes = (AgentMode.BLOCKED_ERROR, AgentMode.WORKING, AgentMode.IDLE_READY)
    statuses = []
    for provider, mode in zip(provider_ids, demo_modes):
        statuses.append(
            AgentStatus(
                provider=provider,
                agent_id=f"{provider}:preview",
                display_name=provider.title(),
                mode=mode,
                updated_at=now,
                event_name="Preview",
            )
        )
    return tuple(statuses)


# --- Preview scenarios -------------------------------------------------------
#
# A brand-new user who just plugged the device in and hasn't run a single
# agent yet has no way to see what any of this actually looks like -- the
# Colors window's live preview otherwise only ever shows either real
# activity or the one fixed demo_statuses_for_preview() scenario above. The
# picker below lets them step through a deliberately varied set of
# situations (quiet, one agent, several sessions of the same provider,
# a full mixed team, more agents than LEDs) so every blend mode and color
# choice can be evaluated before anything is actually running.


def _preview_status(provider: str, mode: AgentMode, *, agent_id: str, now: datetime) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=agent_id,
        display_name=provider.title(),
        mode=mode,
        updated_at=now,
        event_name="Preview",
    )


def _scenario_quiet(now: datetime) -> tuple[AgentStatus, ...]:
    return ()


def _scenario_one_agent_working(now: datetime) -> tuple[AgentStatus, ...]:
    return (_preview_status("codex", AgentMode.WORKING, agent_id="codex:preview", now=now),)


def _scenario_one_agent_needs_you(now: datetime) -> tuple[AgentStatus, ...]:
    return (_preview_status("claude", AgentMode.WAITING_FOR_INPUT, agent_id="claude:preview", now=now),)


def _scenario_same_provider_duo(now: datetime) -> tuple[AgentStatus, ...]:
    # Two separate sessions of the *same* provider -- agents are keyed by
    # session, not provider, so this is a real situation (two Codex windows
    # open at once), not a hypothetical.
    return (
        _preview_status("codex", AgentMode.WORKING, agent_id="codex:preview-a", now=now),
        _preview_status("codex", AgentMode.WAITING_FOR_INPUT, agent_id="codex:preview-b", now=now),
    )


def _scenario_pair(now: datetime) -> tuple[AgentStatus, ...]:
    return (
        _preview_status("codex", AgentMode.WORKING, agent_id="codex:preview", now=now),
        _preview_status("claude", AgentMode.WORKING, agent_id="claude:preview", now=now),
    )


def _scenario_full_team(now: datetime) -> tuple[AgentStatus, ...]:
    # Modes cycle so EVERY registered provider appears no matter how many
    # there are -- zip() against a fixed mode tuple silently dropped any
    # provider past the fourth.
    modes = (AgentMode.WORKING, AgentMode.WAITING_FOR_INPUT, AgentMode.COMPLETED, AgentMode.IDLE_READY)
    return tuple(
        _preview_status(spec.provider, modes[index % len(modes)], agent_id=f"{spec.provider}:preview", now=now)
        for index, spec in enumerate(PROVIDER_SPECS)
    )


def _scenario_busy_team(now: datetime) -> tuple[AgentStatus, ...]:
    # More active sessions than either device has LEDs (2 or 8) -- shows
    # how each blend mode degrades gracefully when it has to wrap or share.
    return (
        _preview_status("codex", AgentMode.WORKING, agent_id="codex:preview-a", now=now),
        _preview_status("codex", AgentMode.WORKING, agent_id="codex:preview-b", now=now),
        _preview_status("claude", AgentMode.WAITING_FOR_INPUT, agent_id="claude:preview-a", now=now),
        _preview_status("claude", AgentMode.WORKING, agent_id="claude:preview-b", now=now),
        _preview_status("devin", AgentMode.WORKING, agent_id="devin:preview", now=now),
        _preview_status("grok", AgentMode.COMPLETED, agent_id="grok:preview", now=now),
    )


PREVIEW_SCENARIO_LIVE = "live"
PREVIEW_SCENARIO_QUIET = "quiet"
PREVIEW_SCENARIO_ONE_WORKING = "one_working"
PREVIEW_SCENARIO_ONE_NEEDS_YOU = "one_needs_you"
PREVIEW_SCENARIO_SAME_PROVIDER_DUO = "same_provider_duo"
PREVIEW_SCENARIO_PAIR = "pair"
PREVIEW_SCENARIO_FULL_TEAM = "full_team"
PREVIEW_SCENARIO_BUSY_TEAM = "busy_team"

# PREVIEW_SCENARIO_LIVE is handled by the caller (use the real snapshot when
# one exists) and isn't in this table -- everything else maps straight to a
# builder.
_PREVIEW_SCENARIO_BUILDERS: dict[str, Any] = {
    PREVIEW_SCENARIO_QUIET: _scenario_quiet,
    PREVIEW_SCENARIO_ONE_WORKING: _scenario_one_agent_working,
    PREVIEW_SCENARIO_ONE_NEEDS_YOU: _scenario_one_agent_needs_you,
    PREVIEW_SCENARIO_SAME_PROVIDER_DUO: _scenario_same_provider_duo,
    PREVIEW_SCENARIO_PAIR: _scenario_pair,
    PREVIEW_SCENARIO_FULL_TEAM: _scenario_full_team,
    PREVIEW_SCENARIO_BUSY_TEAM: _scenario_busy_team,
}

PREVIEW_SCENARIO_CHOICES: tuple[str, ...] = (
    PREVIEW_SCENARIO_LIVE,
    PREVIEW_SCENARIO_QUIET,
    PREVIEW_SCENARIO_ONE_WORKING,
    PREVIEW_SCENARIO_ONE_NEEDS_YOU,
    PREVIEW_SCENARIO_SAME_PROVIDER_DUO,
    PREVIEW_SCENARIO_PAIR,
    PREVIEW_SCENARIO_FULL_TEAM,
    PREVIEW_SCENARIO_BUSY_TEAM,
)

PREVIEW_SCENARIO_LABELS: dict[str, str] = {
    PREVIEW_SCENARIO_LIVE: "Live Activity",
    PREVIEW_SCENARIO_QUIET: "Quiet (nothing running)",
    PREVIEW_SCENARIO_ONE_WORKING: "One Agent Working",
    PREVIEW_SCENARIO_ONE_NEEDS_YOU: "One Agent Needs You",
    PREVIEW_SCENARIO_SAME_PROVIDER_DUO: "Two Sessions, Same Agent",
    PREVIEW_SCENARIO_PAIR: "Two Different Agents",
    PREVIEW_SCENARIO_FULL_TEAM: "Full Team, Mixed States",
    PREVIEW_SCENARIO_BUSY_TEAM: "Busy Team (more agents than LEDs)",
}


def preview_statuses_for_scenario(scenario: str) -> tuple[AgentStatus, ...]:
    """The canned status tuple for a preview scenario key (see
    PREVIEW_SCENARIO_CHOICES). PREVIEW_SCENARIO_LIVE has no builder here --
    callers handle it themselves by preferring the real snapshot -- so it
    resolves to demo_statuses_for_preview()'s fixed scenario, the same thing
    shown today when nothing is selected and nothing is live.
    """
    builder = _PREVIEW_SCENARIO_BUILDERS.get(scenario)
    if builder is None:
        return demo_statuses_for_preview()
    return builder(datetime.now(timezone.utc))


# --- Layout stability (debounce + crossfade bookkeeping) --------------------


def _layout_signature(statuses: tuple[AgentStatus, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((status.provider, display_state_for_mode(status.mode).value) for status in statuses)


class AgentLayoutStabilizer:
    """Debounces spatial reshuffles so a momentary priority blip doesn't
    reshuffle LED positions immediately; a new ranking must hold for
    ``LAYOUT_DEBOUNCE_SECONDS`` before it's committed. Colors/animations for
    already-assigned agents are never delayed by this -- only the
    *positions/sizes* are, via the caller re-rendering with the last
    committed ordering until the debounce window elapses.
    """

    def __init__(self, *, debounce_seconds: float = LAYOUT_DEBOUNCE_SECONDS, clock=time.monotonic) -> None:
        self.debounce_seconds = debounce_seconds
        self._clock = clock
        self._committed_signature: tuple[tuple[str, str], ...] | None = None
        self._committed_order: tuple[AgentStatus, ...] = ()
        self._pending_signature: tuple[tuple[str, str], ...] | None = None
        self._pending_since: float | None = None

    def reset(self) -> None:
        self._committed_signature = None
        self._committed_order = ()
        self._pending_signature = None
        self._pending_since = None

    def stabilize(self, statuses: tuple[AgentStatus, ...]) -> tuple[AgentStatus, ...]:
        """Returns the ordering to render this tick: either the newly
        committed one, or the previously committed one while a new
        candidate is still within its debounce window."""
        signature = _layout_signature(statuses)

        if signature == self._committed_signature:
            self._pending_signature = None
            self._pending_since = None
            return self._committed_order

        now = self._clock()
        if signature != self._pending_signature:
            self._pending_signature = signature
            self._pending_since = now
            # No committed layout yet at all (first tick ever) -- commit
            # immediately rather than showing nothing for the debounce window.
            if self._committed_signature is None:
                self._commit(statuses, signature)
            return self._committed_order

        assert self._pending_since is not None
        if now - self._pending_since >= self.debounce_seconds:
            self._commit(statuses, signature)
        return self._committed_order

    def _commit(self, statuses: tuple[AgentStatus, ...], signature: tuple[tuple[str, str], ...]) -> None:
        self._committed_signature = signature
        self._committed_order = statuses
        self._pending_signature = None
        self._pending_since = None
