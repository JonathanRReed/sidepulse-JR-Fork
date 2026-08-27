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

import hashlib
import math
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
    display_state_for_projection,
    failure_signal_program,
    linear_to_srgb,
    normalize_brightness,
    program_for_display_state,
    scale_hex_brightness,
    settle_duration_ms,
    srgb_to_linear,
)
from .models import MODE_PRIORITY, AgentMode, AgentStatus
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
    # Reuse the existing configurable blocked/error color slot while
    # keeping failure distinct from actionable Ask semantics.
    LedDisplayState.FAILED: MODE_ASK,
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


# --- Loudness and motion: the light language's two channels ----------------
#
# LOUDNESS. How loud a state reads is its rendered peak's relative luminance,
# and that had been an accident of hue rather than a decision. Red-orange is
# intrinsically the dimmest saturated colour there is (the red primary carries
# 21% of the luminance, cyan 85%), so putting Ask on the same "gentle" fade
# ceiling as Working made the one state that means "a human is needed" the
# QUIETEST lit thing on the strip -- 2.5x dimmer than an agent quietly working,
# 13x dimmer than one that had finished. The gentleness ceiling exists to keep
# ambient breathing from being harsh; urgency is not ambient, so it does not
# get the ambient ceiling.
#
# Measured (relative luminance of the rendered peak, default settings):
#   before   done 0.725  >  working 0.136  >  ask 0.055  >  idle 0.0003
#   after    done 0.725  >  ask     0.243  >  working 0.136  >  idle 0.0003
#
# Done deliberately stays where it is. Equalising its luminance against Ask is
# the obvious next move and it is a trap: red and green are the pair a
# red/green colourblind viewer can ONLY separate by lightness, and simulating
# it (Vienot/Brettel/Mollon + CIEDE2000) shows dimming Done to match Ask
# collapses their protanopic separation from dE 39 to dE 6 -- i.e. it would buy
# a tidier luminance ladder by making "finished" and "blocked" the same light
# for 1 man in 12. Done stays bright and stays STILL; Ask is what moves.
URGENT_STATES: frozenset[LedDisplayState] = frozenset(
    {LedDisplayState.ASK, LedDisplayState.FAILED}
)

# MOTION. Hue was carrying the whole state distinction on its own: in the
# default blend mode idle, working and ask rendered as the same pulse, at the
# same period, between the same floor and ceiling, differing only in colour --
# and an Ask is signalled to a deuteranope by about dE 8, which is not a
# signal. Each state now owns a rhythm as well, built from the same DSL
# primitives (pulse / none / delay) rather than any new device capability:
#
#   breathe  one slow swell per cycle, every LED in unison   (idle)
#   chase    the same swell, staggered into a traveling wave (working)
#   beat     one short sharp swell, then dark for the rest   (ask)
#   blink    hard-edged square, no easing at all             (failed)
#   steady   holds, does not move                            (done)
MOTION_BREATHE = "breathe"
MOTION_CHASE = "chase"
MOTION_BEAT = "beat"
MOTION_BLINK = "blink"
MOTION_STEADY = "steady"

# The four rhythms a PERSON is offered (beat is reserved for urgency and
# is never a choice -- see agent_motion). "Automatic" is the default and
# means "whatever the state says", i.e. today's behaviour exactly.
PROVIDER_ANIMATION_AUTO = "auto"
# The expanded vocabulary (2026-08-21, from the WLED/Particle/Adafruit
# pattern survey): rhythm CLASS is the strongest peripheral cue for
# telling two providers apart -- a sinusoid, a lub-dub, and a travelling
# dot are distinguishable without looking, where two tempos are not.
MOTION_HEARTBEAT = "heartbeat"
MOTION_SCANNER = "scanner"
MOTION_COMET = "comet"
MOTION_FLICKER = "flicker"
MOTION_STACK = "stack"
MOTION_TWINKLE = "twinkle"
MOTION_DRIFT = "drift"
MOTION_CONVERGE = "converge"
MOTION_AURORA = "aurora"
MOTION_TIDE = "tide"
# The 2026-08-26 expansion, mined from the upstream ecosystem (PR #29's
# KITT scanner, tlip's gradient wave, the iOS pattern library's two-tone
# working breathe) plus the roll-based marquee the DSL gives us nearly
# for free.
MOTION_KITT = "kitt"
MOTION_GRADIENT = "gradient"
MOTION_MARQUEE = "marquee"
MOTION_DUOTONE = "duotone"
PROVIDER_ANIMATION_CHOICES: tuple[str, ...] = (
    PROVIDER_ANIMATION_AUTO,
    MOTION_BREATHE,
    MOTION_DUOTONE,
    MOTION_CHASE,
    MOTION_GRADIENT,
    MOTION_HEARTBEAT,
    MOTION_SCANNER,
    MOTION_KITT,
    MOTION_COMET,
    MOTION_FLICKER,
    MOTION_STACK,
    MOTION_TWINKLE,
    MOTION_DRIFT,
    MOTION_CONVERGE,
    MOTION_AURORA,
    MOTION_TIDE,
    MOTION_MARQUEE,
    MOTION_STEADY,
    MOTION_BLINK,
)
PROVIDER_ANIMATION_LABELS: dict[str, str] = {
    PROVIDER_ANIMATION_AUTO: "Automatic",
    MOTION_BREATHE: "Breathe",
    MOTION_CHASE: "Chase",
    MOTION_HEARTBEAT: "Heartbeat",
    MOTION_SCANNER: "Scanner",
    MOTION_COMET: "Comet",
    MOTION_FLICKER: "Flicker",
    MOTION_STACK: "Stack",
    MOTION_TWINKLE: "Twinkle",
    MOTION_DRIFT: "Drift",
    MOTION_CONVERGE: "Converge",
    MOTION_AURORA: "Aurora",
    MOTION_TIDE: "Tide",
    MOTION_KITT: "Knight Rider",
    MOTION_GRADIENT: "Gradient",
    MOTION_MARQUEE: "Marquee",
    MOTION_DUOTONE: "Duotone",
    MOTION_STEADY: "Steady",
    MOTION_BLINK: "Blink",
}
PROVIDER_ANIMATION_DESCRIPTIONS: dict[str, str] = {
    PROVIDER_ANIMATION_AUTO: "Follows the state: breathe when idle, chase while working.",
    MOTION_BREATHE: "One slow swell, every LED together.",
    MOTION_CHASE: "The same swell, staggered into a travelling wave.",
    MOTION_HEARTBEAT: "Two quick swells, then a long rest — a lub-dub.",
    MOTION_SCANNER: "A bright dot sweeps end to end and bounces back. Shared strips ride it as a narrow travelling flare.",
    MOTION_COMET: "A bright head sweeps one way, trailing off behind. Shared strips ride it as a narrow travelling flare.",
    MOTION_FLICKER: "A warm candle-like shimmer that never quite repeats.",
    MOTION_STACK: "LEDs pile on one by one until full, then it all lets go. Keeps its hard pile-on in shared strips.",
    MOTION_TWINKLE: "A dim base with single LEDs briefly sparking, scattered.",
    MOTION_DRIFT: "Glacial detuned swells, like light on slow water.",
    MOTION_CONVERGE: "Two dots leave the ends and meet in the middle. In a Split block the fronts meet mid-block.",
    MOTION_AURORA: "Rolling waves of light over a luminous base.",
    MOTION_TIDE: "The bar rises to full, then the water pulls back. Shared strips ride it as the full swell.",
    MOTION_KITT: "The classic scanner: a wide bright eye sweeps end to end and back, overlapping as it goes. Shared strips ride it as a narrow travelling flare.",
    MOTION_GRADIENT: "The travelling wave, but each LED carries its own shade of the color — a gradient rolling by. Shared strips keep the flare; Cycle turns keep the shades.",
    MOTION_MARQUEE: "A palette seeded from the color, endlessly rotating around the bar. Shared strips ride it as a narrow travelling flare.",
    MOTION_DUOTONE: "A slow swell that alternates between two tones of the color. Cycle turns keep both tones; interleaved strips breathe.",
    MOTION_STEADY: "Holds its color. Never moves.",
    MOTION_BLINK: "Hard-edged on/off, no easing.",
}

STATE_MOTION: dict[LedDisplayState, str] = {
    LedDisplayState.IDLE: MOTION_BREATHE,
    LedDisplayState.WORKING: MOTION_CHASE,
    LedDisplayState.ASK: MOTION_BEAT,
    LedDisplayState.DONE: MOTION_STEADY,
    LedDisplayState.FAILED: MOTION_BLINK,
}

# "Nothing above 2Hz." A beat is a third of a cycle and a blink is a hard
# square, so at the fastest cycle speed the user can dial (300ms) either would
# strobe -- 10Hz and 3.3Hz. Below this cycle length both degrade to the gentler
# motion they are built from rather than becoming a hazard.
MIN_FLASH_CYCLE_MS = 500
# A beat is a third of the cycle, but never so short it reads as a strobe on a
# slow cycle either -- floored, then capped at the cycle it lives inside.
MIN_BEAT_MS = 480


def relative_luminance(hex_color: str) -> float:
    """Y of an sRGB hex, 0.0-1.0 -- how much LIGHT a colour actually is.

    The one number that says whether two states read as equally loud, and the
    reason a saturated red at half brightness disappears next to a cyan at the
    same half brightness: they share a fraction, not a luminance.
    """
    cleaned = normalize_hex(hex_color, "#000000").lstrip("#")
    red, green, blue = (
        srgb_to_linear(int(cleaned[index : index + 2], 16) / 255.0)
        for index in (0, 2, 4)
    )
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def luminance_matched_hex(hex_color: str, target_luminance: float) -> str:
    """Scale a colour in LINEAR light until its luminance hits ``target``.

    A uniform scale of the three linear channels leaves their ratios -- and
    therefore the hue and the saturation -- exactly where they were, unlike
    scale_hex_brightness, which multiplies the gamma-encoded bytes. Requests
    the colour cannot reach clamp at its own full-gamut maximum rather than
    clipping a channel and sliding the hue somewhere else.
    """
    cleaned = normalize_hex(hex_color, "#000000").lstrip("#")
    linear = [
        srgb_to_linear(int(cleaned[index : index + 2], 16) / 255.0)
        for index in (0, 2, 4)
    ]
    luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    if luminance <= 0.0 or target_luminance <= 0.0:
        return "#000000"
    scale = target_luminance / luminance
    brightest = max(linear)
    if brightest * scale > 1.0:
        scale = 1.0 / brightest
    return "#" + "".join(
        f"{round(linear_to_srgb(channel * scale) * 255.0):02X}" for channel in linear
    )


# The dimmest an agent's IDENTITY color may render on the strip. Below this
# a color is functionally unlit through the transfer's fade ceiling (devin's
# former navy sat at 0.036 and read as "not interacting with the light bulbs
# at all"), so a custom near-black identity would silently disable the very
# feature it configures. 0.08 lifts those into visibility while leaving
# every shipped brand and palette color untouched.
IDENTITY_LUMINANCE_FLOOR = 0.08


def readable_identity_hex(hex_color: str) -> str:
    """An identity color the strip can actually show: hue and saturation
    kept, luminance lifted to IDENTITY_LUMINANCE_FLOOR when it's darker
    than that. Bright colors pass through byte-for-byte. Render-time only
    -- the stored setting and its swatch keep the user's literal pick."""
    if relative_luminance(hex_color) >= IDENTITY_LUMINANCE_FLOOR:
        return normalize_hex(hex_color, "#000000")
    return luminance_matched_hex(hex_color, IDENTITY_LUMINANCE_FLOOR)


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

# Index-aligned with CURATED_PALETTE. A colour square with no name is not a
# choice, it is a guess -- every swatch this app draws gets a word.
CURATED_PALETTE_NAMES: tuple[str, ...] = (
    "Red",
    "Blue",
    "Green",
    "Purple",
    "Yellow",
    "Orange",
    "Pink",
    "Gray",
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

# Names describe what you SEE, not how it is built. The old set was
# scheduler jargon ("Round-Robin"), a mechanism ("Color Blend"), or a
# changelog entry ("Classic") -- a user hunting for a calm, seamless
# light went looking for "Smooth" and could not find it. Persisted
# values never change; these are labels only.
BLEND_MODE_LABELS: dict[str, str] = {
    BLEND_MODE_ROUND_ROBIN: "Everyone",
    BLEND_MODE_RELAY: "Spotlight",
    BLEND_MODE_SPATIAL: "Split",
    BLEND_MODE_COLOR: "Smooth",
    BLEND_MODE_CYCLE: "One at a Time",
    BLEND_MODE_CLASSIC: "Status Only",
}

BLEND_MODE_DESCRIPTIONS: dict[str, str] = {
    BLEND_MODE_ROUND_ROBIN: "Every agent is lit at once, each in its own color.",
    BLEND_MODE_RELAY: "One agent flares bright at a time; the rest stay dim.",
    BLEND_MODE_SPATIAL: "Each agent gets its own section, sized by how much it needs you.",
    BLEND_MODE_COLOR: "One seamless light. Everyone's colors blend across the strip.",
    BLEND_MODE_CYCLE: "The whole strip shows one agent, then the next.",
    BLEND_MODE_CLASSIC: "One color for whatever needs you most. Agents aren't shown.",
}

# Longer versions for a tooltip, so the detail is available without
# permanently taking up space in the window.
BLEND_MODE_TOOLTIPS: dict[str, str] = {
    BLEND_MODE_ROUND_ROBIN: (
        "Agent colors repeat across the strip and breathe together, so every "
        "active agent stays visible at a glance. Nothing waits its turn."
    ),
    BLEND_MODE_RELAY: (
        "A single bright point travels the strip and stops on each agent in "
        "turn while the others hold a soft resting glow. Easiest to follow "
        "when several agents are busy at once."
    ),
    BLEND_MODE_SPATIAL: (
        "The strip divides into blocks, one per agent. The more urgent an "
        "agent is, the more LEDs it claims -- the shape tells you who needs "
        "you before you have read the color."
    ),
    BLEND_MODE_COLOR: (
        "No blocks, no blinking, no marching: one continuous light made from "
        "your active agents' colors. The quietest way to keep everyone on "
        "screen -- pick this when the light should be furniture, not an alarm."
    ),
    BLEND_MODE_CYCLE: (
        "Each agent takes over the entire strip for a moment, breathes once "
        "in its own color, then hands off. Reads correctly from across the "
        "room."
    ),
    BLEND_MODE_CLASSIC: (
        "Ignores who is who. One color for the most urgent thing happening "
        "anywhere -- the calmest option, and the only one whose meaning "
        "never changes as agents come and go."
    ),
}

# NOTE on layout crossfades: every program swap already eases instead of
# jump-cutting -- the firmware resumes from the current visible color and
# each program leads with an eased settle/approach line (see
# led_status.settle_duration_ms). A dedicated longer bridge for roster
# layout changes was designed as LAYOUT_CROSSFADE_MS = 450 but never
# wired; the constant is gone until the bridge is real.
IDLE_ROLL_SECONDS = 7.0
IDLE_ROLL_MIN_AGENTS = 3

# User-configurable "how long is one breath" for Round-Robin and Cycle --
# both are about seeing distinct agents in turn/at a glance rather than one
# state's own fixed animation cadence. cycle_speed_seconds on ColorSettings
# is the global default; SPEED_OVERRIDE_MODES lists which blend modes can
# each have their own independent speed instead of following the global one.
# 2.2, not 1.6: the default breath deepened in the 2026-08-20 motion
# polish -- ambient light should sit BELOW conversation pace.
DEFAULT_CYCLE_SPEED_SECONDS = 2.2
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

# Attention keeps two separate programs: a persistent static spatial anchor
# and an optional finite arrival that precedes it with at most two full-bar
# taps. The pure renderer defaults to the base so refresh or reconnection
# cannot invent an arrival episode; the later episode owner must request it.
# The arrival is ONE overshoot-and-settle crest (Dynamic Island
# grammar: alerts expand past their final size and settle), not
# repeated flashes: the strip swells to the attention color and
# breathes down INTO the anchor's own level. 0.85, not the first
# cut's 0.55: the anchor holds urgent at >=0.75, so settling to 55%
# left a 40ms hop back up -- a stutter where the settle should land
# ("the magic isn't feeling right", tuned 2026-08-27).
ATTENTION_CREST_MS = 300
ATTENTION_CREST_SETTLE_MS = 450
ATTENTION_CREST_HOLD_FRACTION = 0.85
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
    text = text.removeprefix("#")
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
#     unlit. Devin's first brightening (#1D3461) landed at luminance 0.036
#     -- one eighth of claude/codex (0.27-0.29) -- and drove a peak strip
#     byte of 30/255: confirmed live as "Devin isn't interacting with the
#     light bulbs at all" while it was in fact on the strip. A saturated
#     navy capped out near Y 0.09 while grok held the mid-gray #8E8E93 --
#     anything brighter fell under dE 13 from that gray for a dichromat
#     -- so grok moved DARKER (see below) and devin now sits at #5C84B0:
#     same hue family, Y 0.219, finally in the same loudness class as
#     claude/codex. Grid-searched JOINTLY with grok's move under the
#     test_provider_colour_dichromacy metric (worst-case dE 21.1). This
#     also FIXED the recorded claude<->devin dichromat collapse.
# Devin's navy and Codex's blue share a hue family on purpose (both are
# genuinely "blue" brands) -- they're kept apart mainly by lightness/
# saturation (bright azure vs. dark navy) rather than hue, which is a
# real, deliberate trade of the maximal-distinctness goal for brand
# fidelity, per explicit request.
PROVIDER_BRAND_COLORS: dict[str, str] = {
    "codex": "#2B8FFF",
    "claude": "#D97757",
    "devin": "#5C84B0",
    # Darker than the old systemGray stand-in, and closer to xAI's literal
    # "Mine Shaft" #313131. Moved deliberately: the mid-gray sat exactly in
    # the lightness band a visible saturated navy needs under dichromacy,
    # capping devin at an unlit Y 0.09 forever. Worst-case dE 12.7; the
    # identity luminance floor keeps this renderable (Y 0.126).
    "grok": "#636366",
    # Not Antigravity's real brand colour, and deliberately so. Its icon's
    # dominant blue (~#3D8AFF) is 4.5 degrees from codex's #2B8FFF -- two
    # near-identical blues on a strip is the grok/opencode systemGray clash
    # again. CURATED_PALETTE is also structurally exhausted at eight
    # providers, and the positional fallback handed antigravity #FF3B30,
    # ten degrees from the Ask/blocked seed: a provider that looks
    # permanently blocked.
    #
    # This colour was chosen by dE2000 under NORMAL, DEUTERANOPIA and
    # PROTANOPIA simulation against all twelve spoken-for colours, not by
    # hue degrees -- raw hue distance is what let the grok clash through.
    # It wins at minimum dE 28.3; the runner-up hues in the same band score
    # 19-25 because they collapse toward the Done seed for a dichromat.
    # What separates this one is LIGHTNESS, which survives dichromacy when
    # hue does not. See test_provider_colour_dichromacy.py.
    "antigravity": "#ABE17E",
    # Kiro brand purple collapses onto Codex blue under deuteranopia, and
    # warm tones crowd Hermes orange in hue. The first mulberry (#4B1E3C)
    # cleared every dE gap but sat at luminance 0.0275 -- dimmer than the
    # navy that was confirmed live as an unlit LED. Lifting it in place
    # (#96437B) collapsed onto devin's own lifted navy for a deuteranope
    # (dE 2.4 -- purple and blue are the same light without an M cone
    # once they share lightness), so the hue walks to this deep raspberry
    # instead: Y 0.081, worst-case dE 17.8 vs claude, grid-searched
    # against the FINAL table including the other two lifts.
    "kiro": "#A00848",
    # 2026-08-20: every registered provider now has a DELIBERATE entry --
    # the positional fallback is a mechanism, not a palette. cursor,
    # hermes, and opencode keep the exact colours the fallback had been
    # assigning (hermes' is the literal Hermes brand orange), promoted so
    # they can never shift when the registry grows.
    "cursor": "#FFCC00",
    "hermes": "#FF9500",
    "opencode": "#AF52DE",
    # openclaw's fallback #FF2D55 was a KNOWN_COLLAPSES defect (dE 4.9
    # from devin, 10.5 from claude for a dichromat). This deep rust --
    # a claw, as it happens -- was grid-searched under all three vision
    # models against every provider and state seed. The first pick
    # (#601800, dE 40.2) was another unlit LED (luminance 0.031); this is
    # the same rust luminance-matched to 0.12 (worst-case dE 24.0).
    "openclaw": "#B23400",
}


def _hue_gap(left: str, right: str) -> float:
    """Degrees between two hues, 0-180. Only ever used to rank candidate
    colours against colours already spoken for."""
    delta = abs(_hex_to_hls_hue(left) - _hex_to_hls_hue(right)) % 360.0
    return min(delta, 360.0 - delta)


# The CURATED_PALETTE slot each SHIPPED brandless provider wears, pinned
# as data rather than derived from its position in PROVIDER_SPECS.
#
# It used to be derived. `_palette_defaults_by_provider` keyed the
# positional slot on the ABSOLUTE registry index -- including branded
# providers, which consume no slot -- so registering one new provider
# ANYWHERE except the end silently repainted every brandless provider
# after it. Simulated by inserting a single spec at index 4:
#
#     cursor    #FFCC00 -> #FF9500
#     hermes    #FF9500 -> #FF2D55
#     openclaw  #FF2D55 -> #AF52DE
#     opencode  #AF52DE -> #FF3B30
#
# Four providers change colour, on an install where the user has learned
# which light is which, because an unrelated tenth provider was added.
# Identity that moves is not identity.
#
# These hexes are exactly what the derivation produced for the shipped
# nine, so no existing install changes colour. New providers still get an
# automatic slot from the algorithm below -- but they get it from the
# PINNED set plus the brands, never from a position, so adding one can
# never move one of these.
PROVIDER_PALETTE_SLOTS: dict[str, str] = {
    "cursor": "#FFCC00",
    "hermes": "#FF9500",
    "openclaw": "#FF2D55",
    "opencode": "#AF52DE",
}


def _palette_defaults_by_provider() -> dict[str, str]:
    """The CURATED_PALETTE slot each brandless registered provider gets.

    Pinned first (see PROVIDER_PALETTE_SLOTS), then derived for anything
    the table does not yet name.

    Derivation is what shipped a real collision: ``opencode`` sat at index
    7 in PROVIDER_SPECS, CURATED_PALETTE[7] is systemGray #8E8E93, and
    #8E8E93 is also ``grok``'s brand colour -- so out of the box those two
    agents were the same colour on the strip and genuinely
    indistinguishable. An unpinned provider is therefore given the free
    slot whose hue sits FARTHEST from everything already spoken for --
    brands, pinned providers, and the four state colours. Taking merely
    the next free index instead would have handed opencode systemRed
    #FF3B30, ten degrees of hue from the ask/blocked signal #FF3A00:
    distinct as a string, the same light on the strip.

    Unpinned providers are resolved in SORTED id order, never registry
    order, so their result does not depend on where in the registry they
    were declared either.
    """
    registered = {spec.provider for spec in PROVIDER_SPECS}
    assigned: dict[str, str] = {
        provider: hex_value
        for provider, hex_value in PROVIDER_PALETTE_SLOTS.items()
        if provider in registered and provider not in PROVIDER_BRAND_COLORS
    }
    taken = {hex_value.upper() for hex_value in PROVIDER_BRAND_COLORS.values()}
    taken |= {hex_value.upper() for hex_value in assigned.values()}
    reserved = (
        list(PROVIDER_BRAND_COLORS.values())
        + list(assigned.values())
        + [hex_value for _name, hex_value in STATE_SEED_COLORS]
    )
    unpinned = sorted(
        provider
        for provider in registered
        if provider not in PROVIDER_BRAND_COLORS and provider not in assigned
    )
    for provider in unpinned:
        free = [hex_value for hex_value in CURATED_PALETTE if hex_value.upper() not in taken]
        if free:
            choice = max(free, key=lambda candidate: min(_hue_gap(candidate, other) for other in reserved))
        else:
            # More providers than palette slots: everything distinct is
            # already spent, so fall back to a deterministic slot rather
            # than refusing to colour the row at all.
            choice = CURATED_PALETTE[
                int(hashlib.md5(provider.encode("utf-8")).hexdigest(), 16)
                % len(CURATED_PALETTE)
            ]
        assigned[provider] = choice
        taken.add(choice.upper())
        reserved.append(choice)
    return assigned


_PALETTE_DEFAULTS_BY_PROVIDER: dict[str, str] | None = None


def palette_defaults_by_provider() -> dict[str, str]:
    """Memoized because it is pure over module constants, and lazy because
    it needs _hex_to_hls_hue, which is defined further down this file."""
    global _PALETTE_DEFAULTS_BY_PROVIDER
    if _PALETTE_DEFAULTS_BY_PROVIDER is None:
        _PALETTE_DEFAULTS_BY_PROVIDER = _palette_defaults_by_provider()
    return _PALETTE_DEFAULTS_BY_PROVIDER


def default_agent_color(provider: str) -> str:
    """Default color for a provider: its brand color if known, otherwise a
    deterministic slot from CURATED_PALETTE that no other provider is
    already wearing. A future provider added to that registry (without an
    entry in PROVIDER_BRAND_COLORS) gets the next unused palette slot
    automatically; an entirely unknown provider id (e.g. one seen in a
    snapshot before this module knows about it) falls back to a hash-based
    slot so it's still deterministic and collision-free in the common case,
    without requiring a settings migration.
    """
    if provider in PROVIDER_BRAND_COLORS:
        return PROVIDER_BRAND_COLORS[provider]
    assigned = palette_defaults_by_provider()
    if provider in assigned:
        return assigned[provider]
    # Unknown id: prefer a slot no registered provider has claimed, so a
    # provider seen in a snapshot before this module knows about it does
    # not arrive already impersonating one that shipped.
    claimed = {hex_value.upper() for hex_value in PROVIDER_BRAND_COLORS.values()}
    claimed |= {hex_value.upper() for hex_value in assigned.values()}
    spare = [
        hex_value for hex_value in CURATED_PALETTE if hex_value.upper() not in claimed
    ] or list(CURATED_PALETTE)
    return spare[hash(provider) % len(spare)]


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


# Session identity palette ("color = agent"): eight hues chosen to stay
# clear of the STATE hues (working cyan #00E5FF, done green #00FF66,
# ask red-orange #FF3A00, calendar purple #A45CFF) so an identity can
# never be misread as a state. Identity kicks in when two or more
# sessions are active at once -- a lone session keeps its provider's
# brand color, since "which one is which" needs a which.
IDENTITY_PALETTE: tuple[str, ...] = (
    "#4C8DFF",  # blue
    "#FF6FA9",  # pink
    "#FFD60A",  # yellow
    "#FF9F0A",  # amber
    "#A8E34D",  # lime
    "#00B3A4",  # deep teal
    "#E44CFF",  # magenta
    "#E6E6E6",  # silver
)

# Index-aligned with IDENTITY_PALETTE. Distinct words from
# CURATED_PALETTE_NAMES wherever the hues are close, so a name always
# identifies exactly one swatch.
IDENTITY_PALETTE_NAMES: tuple[str, ...] = (
    "Azure",
    "Rose",
    "Sun",
    "Amber",
    "Lime",
    "Teal",
    "Magenta",
    "Silver",
)


def identity_colors_for_agents(
    agent_ids: list[str],
    groups: dict[str, str] | None = None,
) -> dict[str, str]:
    """Deterministic palette assignment: each id hashes to a preferred
    slot and probes forward past slots already taken this round, so the
    same set of sessions always maps to the same colors, and two
    sessions never share a hue while free slots remain.

    Assignment iterates ids in SORTED order, never caller order: the
    LED render, the dropdown dots, and any preview pass differently
    ordered lists, and collision probing is order-dependent -- without
    the sort, two hash-colliding sessions swapped hues whenever their
    urgency order flipped, and the dropdown dot could disagree with
    the LEDs. Sorted, the result is a pure function of the ID SET.

    Story #13 (project hue families): ``groups`` maps agent_id -> a
    project key (the session's origin). A key claimed by TWO OR MORE
    ids becomes a hue family -- one hue per project, members told apart
    by perceptually even OKLCH lightness steps -- so the bar answers
    "which project needs me?", not "which harness?". Singletons and
    ungrouped ids keep the classic per-session slots, and groups=None
    is byte-identical to the pre-#13 behavior."""
    ids = sorted(set(agent_ids))
    assignment: dict[str, str] = {}
    family_member_ids: set[str] = set()
    if groups:
        members_by_group: dict[str, list[str]] = {}
        for agent_id in ids:
            group_key = groups.get(agent_id)
            if group_key:
                members_by_group.setdefault(str(group_key), []).append(agent_id)
        taken_buckets: set[int] = set()
        bucket_count = 10  # 36-degree hue neighborhoods
        for group_key in sorted(
            key for key, members in members_by_group.items() if len(members) >= 2
        ):
            members = members_by_group[group_key]
            start = (
                int(hashlib.md5(group_key.encode("utf-8")).hexdigest(), 16)
                % bucket_count
            )
            bucket = start
            for offset in range(bucket_count):
                candidate = (start + offset) % bucket_count
                if candidate not in taken_buckets:
                    bucket = candidate
                    break
            taken_buckets.add(bucket)
            hue = bucket * (360.0 / bucket_count)
            span = len(members) - 1
            for index, agent_id in enumerate(members):
                lightness = 0.80 - (0.22 * index / span if span else 0.0)
                assignment[agent_id] = oklch_hex(lightness, 0.15, hue)
                family_member_ids.add(agent_id)
    taken: set[int] = set()
    for agent_id in ids:
        if agent_id in family_member_ids:
            continue
        start = int(hashlib.md5(agent_id.encode("utf-8")).hexdigest(), 16) % len(IDENTITY_PALETTE)
        slot = start
        for offset in range(len(IDENTITY_PALETTE)):
            candidate = (start + offset) % len(IDENTITY_PALETTE)
            if candidate not in taken:
                slot = candidate
                break
        taken.add(slot)
        assignment[agent_id] = IDENTITY_PALETTE[slot]
    return assignment


def identity_groups_for_statuses(statuses, colors) -> dict[str, str] | None:
    """The groups argument for identity_colors_for_agents, from live
    statuses: origin (the per-session project detection) keys the
    family. None whenever the toggle is off, so every call site remains
    byte-identical with Color by Project disabled."""
    if not getattr(colors, "color_by_project", False):
        return None
    return {
        status.agent_id: status.origin
        for status in statuses
        if getattr(status, "origin", None)
    }


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


def _normalized_relay_traversal_ms(traversal_seconds: float) -> int:
    candidate = traversal_seconds
    if (
        not isinstance(candidate, (int, float))
        or isinstance(candidate, bool)
        or not math.isfinite(float(candidate))
        or float(candidate) <= 0.0
    ):
        candidate = MIN_CYCLE_SPEED_SECONDS
    return int(normalize_cycle_speed(candidate) * 1000)


def relay_step_ms(traversal_seconds: float, led_count: int) -> int:
    """Return one relay step from a full-line traversal duration."""
    traversal_ms = _normalized_relay_traversal_ms(traversal_seconds)
    return max(1, traversal_ms // max(1, int(led_count)))


def relay_phase_index(
    elapsed_seconds: float,
    traversal_seconds: float,
    led_count: int,
) -> int:
    """Map controller-owned elapsed time to this surface's relay index."""
    count = max(0, int(led_count))
    if count == 0:
        return 0
    elapsed = float(elapsed_seconds)
    if not math.isfinite(elapsed) or elapsed <= 0.0:
        elapsed = 0.0
    traversal_ms = _normalized_relay_traversal_ms(traversal_seconds)
    # Controller callers subtract two monotonic timestamps. A mathematically
    # exact boundary such as 100.8 - 100.0 may arrive as 799.999999ms, so
    # quantize to the DSL's millisecond clock before selecting the phase.
    elapsed_within_traversal = round(elapsed * 1000) % traversal_ms
    return (elapsed_within_traversal * count) // traversal_ms


def relay_led_order(led_count: int, start_index: int) -> tuple[int, ...]:
    """Rotate physical LED indices so delay zero starts at ``start_index``."""
    count = max(0, int(led_count))
    if count == 0:
        return ()
    start = int(start_index) % count
    return tuple((start + offset) % count for offset in range(count))


def _default_mode_animation() -> dict[str, str]:
    return dict(DEFAULT_MODE_ANIMATION)


# --- ColorSettings ---------------------------------------------------------


@dataclass(frozen=True)
class ColorSettings:
    mode_colors: dict[str, str] = field(default_factory=_default_mode_colors)
    agent_colors: dict[str, str] = field(default_factory=_default_agent_colors)
    # Per-SESSION identity overrides (agent_id -> hex), on top of the
    # auto-assigned IDENTITY_PALETTE. Empty by default.
    session_colors: dict[str, str] = field(default_factory=dict)
    blend_mode: str = DEFAULT_BLEND_MODE
    fade_floor: dict[str, float] = field(default_factory=_default_fade_floor)
    fade_ceiling: dict[str, float] = field(default_factory=_default_fade_ceiling)
    mode_animation: dict[str, str] = field(default_factory=_default_mode_animation)
    # Per-PROVIDER animation override (provider id -> a
    # PROVIDER_ANIMATION_CHOICES motion). Absent/"auto" means the state
    # picks the rhythm, which is the shipped behaviour -- so an empty dict
    # is a faithful default and no settings migration exists.
    provider_animation: dict[str, str] = field(default_factory=dict)
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
    # Story #13: color sessions by PROJECT (origin) -- one hue family
    # per repo, lightness steps within it. Off = classic per-session.
    color_by_project: bool = False

    @classmethod
    def defaults(cls) -> ColorSettings:
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

    def session_color(self, agent_id: str | None) -> str | None:
        """The user's explicit identity override for one session, or
        None (auto palette / provider color applies)."""
        if not agent_id:
            return None
        raw = self.session_colors.get(agent_id)
        if raw is None:
            return None
        return normalize_hex(raw, IDENTITY_PALETTE[0])

    def with_session_color(self, agent_id: str, hex_value: str | None) -> ColorSettings:
        """hex_value=None removes the override (back to auto)."""
        session_colors = dict(self.session_colors)
        if hex_value is None:
            session_colors.pop(agent_id, None)
        else:
            session_colors[agent_id] = normalize_hex(hex_value, IDENTITY_PALETTE[0])
        return replace(self, session_colors=session_colors)

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

    def with_mode_color(self, key: str, hex_value: str) -> ColorSettings:
        if key not in MODE_COLOR_KEYS:
            raise ValueError(f"Unknown mode color key: {key}")
        fallback = _default_mode_colors()[key]
        colors = dict(self.mode_colors)
        colors[key] = normalize_hex(hex_value, fallback)
        return replace(self, mode_colors=colors)

    def with_agent_color(self, provider: str, hex_value: str) -> ColorSettings:
        fallback = default_agent_color(provider)
        colors = dict(self.agent_colors)
        colors[provider] = normalize_hex(hex_value, fallback)
        return replace(self, agent_colors=colors)

    def agent_animation(self, provider: str) -> str:
        """The motion this provider was given, or PROVIDER_ANIMATION_AUTO.

        Anything unrecognized reads as Automatic rather than raising: a
        settings file written by a newer build must never brick the render.
        """
        motion = self.provider_animation.get(provider)
        if motion not in PROVIDER_ANIMATION_CHOICES:
            return PROVIDER_ANIMATION_AUTO
        return motion

    def with_agent_animation(self, provider: str, motion: str) -> ColorSettings:
        """Automatic REMOVES the entry rather than storing "auto", so a
        settings file only ever carries the choices actually made."""
        if motion not in PROVIDER_ANIMATION_CHOICES:
            raise ValueError(f"Unknown provider animation: {motion}")
        animations = dict(self.provider_animation)
        if motion == PROVIDER_ANIMATION_AUTO:
            animations.pop(provider, None)
        else:
            animations[provider] = motion
        return replace(self, provider_animation=animations)

    def with_blend_mode(self, mode: str) -> ColorSettings:
        if mode not in BLEND_MODE_CHOICES:
            raise ValueError(f"Unknown blend mode: {mode}")
        return replace(self, blend_mode=mode)

    def with_fade_floor(self, key: str, value: float) -> ColorSettings:
        if key not in FADE_MODE_KEYS:
            raise ValueError(f"Unknown fade mode key: {key}")
        floors = dict(self.fade_floor)
        floors[key] = normalize_fade_fraction(value, DEFAULT_FADE_FLOOR)
        return replace(self, fade_floor=floors)

    def with_fade_ceiling(self, key: str, value: float) -> ColorSettings:
        if key not in FADE_MODE_KEYS:
            raise ValueError(f"Unknown fade mode key: {key}")
        ceilings = dict(self.fade_ceiling)
        ceilings[key] = normalize_fade_fraction(value, DEFAULT_FADE_CEILING)
        return replace(self, fade_ceiling=ceilings)

    def with_mode_animation(self, key: str, style: str) -> ColorSettings:
        if key not in ANIMATION_MODE_KEYS:
            raise ValueError(f"Unknown animation mode key: {key}")
        if style not in ANIMATION_STYLE_CHOICES:
            raise ValueError(f"Unknown animation style: {style}")
        animations = dict(self.mode_animation)
        animations[key] = style
        return replace(self, mode_animation=animations)

    def with_cycle_speed(self, seconds: float) -> ColorSettings:
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

    def with_speed_override(self, blend_mode: str, seconds: float) -> ColorSettings:
        if blend_mode not in SPEED_OVERRIDE_MODES:
            raise ValueError(f"Unknown speed-override mode: {blend_mode}")
        overrides = dict(self.speed_overrides)
        overrides[blend_mode] = normalize_cycle_speed(seconds)
        return replace(self, speed_overrides=overrides)

    def with_global_speed_for_mode(self, blend_mode: str) -> ColorSettings:
        """Reverts `blend_mode` back to following the global speed."""
        if blend_mode not in SPEED_OVERRIDE_MODES:
            raise ValueError(f"Unknown speed-override mode: {blend_mode}")
        if blend_mode not in self.speed_overrides:
            return self
        overrides = dict(self.speed_overrides)
        del overrides[blend_mode]
        return replace(self, speed_overrides=overrides)

    def with_round_robin_urgency_alert(self, enabled: bool) -> ColorSettings:
        return replace(self, round_robin_urgency_alert=bool(enabled))

    def with_done_celebration_enabled(self, enabled: bool) -> ColorSettings:
        return replace(self, done_celebration_enabled=bool(enabled))

    def with_color_by_project(self, enabled: bool) -> ColorSettings:
        return replace(self, color_by_project=bool(enabled))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode_colors": dict(self.mode_colors),
            "agent_colors": dict(self.agent_colors),
            "session_colors": dict(sorted(self.session_colors.items())),
            "blend_mode": self.blend_mode,
            "fade_floor": dict(self.fade_floor),
            "fade_ceiling": dict(self.fade_ceiling),
            "mode_animation": dict(self.mode_animation),
            "provider_animation": dict(sorted(self.provider_animation.items())),
            "cycle_speed_seconds": self.cycle_speed_seconds,
            "speed_overrides": dict(self.speed_overrides),
            "round_robin_urgency_alert": self.round_robin_urgency_alert,
            "done_celebration_enabled": self.done_celebration_enabled,
            "color_by_project": self.color_by_project,
        }

    @classmethod
    def from_dict(cls, data: object) -> ColorSettings:
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
        agent_colors = _brand_colors_repainted_by_a_palette(agent_colors)
        agent_colors = _retired_default_agent_colors_dropped(agent_colors)

        session_colors: dict[str, str] = {}
        raw_sessions = data.get("session_colors")
        if isinstance(raw_sessions, dict):
            for agent_id, value in raw_sessions.items():
                if isinstance(agent_id, str) and agent_id:
                    session_colors[agent_id] = normalize_hex(value, IDENTITY_PALETTE[0])

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

        provider_animation: dict[str, str] = {}
        raw_provider_animation = data.get("provider_animation")
        if isinstance(raw_provider_animation, dict):
            for provider, value in raw_provider_animation.items():
                # "auto" is the absence of a choice, so it is never stored --
                # round-tripping it back in would make an empty default and a
                # fully-explicit "everything automatic" file compare unequal.
                if (
                    isinstance(provider, str)
                    and provider
                    and value in PROVIDER_ANIMATION_CHOICES
                    and value != PROVIDER_ANIMATION_AUTO
                ):
                    provider_animation[provider] = value

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

        raw_by_project = data.get("color_by_project")
        color_by_project = bool(raw_by_project) if isinstance(raw_by_project, bool) else False

        return cls(
            mode_colors=mode_colors,
            agent_colors=agent_colors,
            session_colors=session_colors,
            blend_mode=blend_mode,
            fade_floor=fade_floor,
            fade_ceiling=fade_ceiling,
            mode_animation=mode_animation,
            provider_animation=provider_animation,
            cycle_speed_seconds=cycle_speed_seconds,
            speed_overrides=speed_overrides,
            round_robin_urgency_alert=round_robin_urgency_alert,
            done_celebration_enabled=done_celebration_enabled,
            color_by_project=color_by_project,
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
    PRESET_INFORMATIVE: "Balanced",
    PRESET_EVERYTHING: "Lively",
}
PRESET_DESCRIPTIONS: dict[str, str] = {
    PRESET_CALM: "Slow, dim, no celebrations. Light you can work next to.",
    PRESET_INFORMATIVE: "Clear motion at a normal brightness. The default.",
    PRESET_EVERYTHING: "Fast, bright, and it celebrates. Full show.",
}


def _palette_written_agent_colors() -> dict[str, frozenset[str]]:
    """Every hex a palette click could ever have written per provider."""
    written: dict[str, set[str]] = {}
    for palette in (*CURATED_PALETTES.values(), *PROVIDER_PALETTES.values()):
        for provider, hex_value in palette["agents"].items():
            written.setdefault(provider, set()).add(normalize_hex(hex_value, "#000000"))
    # The four brand SEEDS too: they are the chips every provider row
    # leads with, and clicking the wrong one is the same accident.
    for _name, seed in BRAND_SEED_COLORS:
        for provider in PROVIDER_BRAND_COLORS:
            written.setdefault(provider, set()).add(normalize_hex(seed, "#000000"))
    # Palettes before 2026-08-19 fanned BRANDED providers too (claude at
    # the seed hue, codex +90, devin +270, at oklch 0.72/0.15). Current
    # palettes no longer emit those entries, but installs damaged back
    # then must still repair byte-exactly.
    for _name, seed in (*CURATED_PALETTE_SEEDS, *BRAND_SEED_COLORS):
        hue = _hex_to_hls_hue(normalize_hex(seed, "#00E5FF"))
        for provider, offset in (("claude", 0.0), ("codex", 90.0), ("devin", 270.0)):
            written.setdefault(provider, set()).add(
                normalize_hex(
                    oklch_hex(0.72, 0.15, (hue + offset) % 360.0), "#000000"
                )
            )
        # Between 2026-08-19 and 2026-08-20, palettes fanned exactly the
        # four then-brandless providers at 90-degree steps (sorted id
        # order). They are branded now, so installs carrying that
        # window's fan-out must repair byte-exactly too.
        for index, provider in enumerate(("cursor", "hermes", "openclaw", "opencode")):
            written.setdefault(provider, set()).add(
                normalize_hex(
                    oklch_hex(0.72, 0.15, (hue + index * 90.0) % 360.0),
                    "#000000",
                )
            )
    return {provider: frozenset(values) for provider, values in written.items()}


def _brand_colors_repainted_by_a_palette(agent_colors: dict[str, str]) -> dict[str, str]:
    """Undo a palette that overwrote a provider's declared brand colour.

    The live install held ``claude: #10A37F`` -- OpenAI's own green -- and
    ``codex: #007AFF`` -- systemBlue, not Codex's #2B8FFF. Both were
    written by ``applyPalette_``, which used to repaint every provider
    with the palette's own fan-out. From then on Claude could not be
    rendered as Claude ANYWHERE, because every surface resolves through
    ``agent_color`` and ``agent_color`` reads settings before brands.

    Only a BRANDED provider is repaired, and only when its stored hex is
    exactly a value some shipped palette would have written for it. A
    colour the user picked by hand is not in that set and is left alone.
    """
    written = _palette_written_agent_colors()
    repaired = dict(agent_colors)
    for provider, brand in PROVIDER_BRAND_COLORS.items():
        stored = repaired.get(provider)
        if stored is None:
            continue
        normalized = normalize_hex(stored, brand)
        if normalized.upper() == normalize_hex(brand, brand).upper():
            continue
        if normalized in written.get(provider, frozenset()):
            repaired[provider] = brand
    return repaired


# Hexes that WERE a provider's shipped default in some earlier build and
# have since been replaced. A stored entry exactly equal to one of these
# is a snapshot of a default the user never chose (or a pick of a colour
# that is objectively broken -- the first three drove an unlit LED, and
# cursor's #FF2D55 / hermes' #FFCC00 are the pre-pinning positional
# fallbacks, cursor's being a recorded dichromacy collapse). Dropping it
# lets the install track the repaired palette; a hand-picked colour that
# never shipped as a default is not in this table and is left alone.
#
# MAINTENANCE: to_dict serializes the FULL merged agent_colors dict, so
# every save snapshots the current defaults into settings. Whenever a
# provider's default changes, its old hex MUST be added here or every
# existing install silently keeps the old colour forever.
RETIRED_DEFAULT_AGENT_COLORS: dict[str, frozenset[str]] = {
    "devin": frozenset({"#1D3461", "#395FAA", "#3C5480"}),
    "grok": frozenset({"#8E8E93"}),
    "kiro": frozenset({"#4B1E3C", "#96437B"}),
    "openclaw": frozenset({"#601800", "#FF2D55"}),
    "cursor": frozenset({"#FF2D55"}),
    "hermes": frozenset({"#FFCC00"}),
}


def _retired_default_agent_colors_dropped(agent_colors: dict[str, str]) -> dict[str, str]:
    """Migrate stored snapshots of defaults that no longer exist.

    The live install held ``devin: #1D3461`` -- the navy confirmed as an
    unlit LED -- persisted by an earlier default-snapshotting path, so
    the brand-table fix was invisible there: ``agent_color`` reads
    settings before brands, exactly the shadowing _brand_colors_
    repainted_by_a_palette repairs for palettes."""
    repaired = dict(agent_colors)
    for provider, retired in RETIRED_DEFAULT_AGENT_COLORS.items():
        stored = repaired.get(provider)
        if stored is None:
            continue
        if normalize_hex(stored, "#000000").upper() in retired:
            repaired[provider] = default_agent_color(provider)
    return repaired


def apply_palette(colors: ColorSettings, palette: dict) -> ColorSettings:
    """Dress every STATE in one look, without touching brand identity.

    A palette owns the four mode colours and any provider that has no
    declared brand hue. It does NOT own the brands: applying the "OpenAI"
    look used to write ``claude = #10A37F`` -- OpenAI's own green -- into
    the live settings, and from then on nothing anywhere could render
    Claude as Claude, because every surface resolves through
    ``agent_color`` and ``agent_color`` reads settings first. Per-provider
    brand colour is changed one row at a time, deliberately, or not at
    all.
    """
    for mode_key, hex_value in palette["modes"].items():
        colors = colors.with_mode_color(mode_key, hex_value)
    for provider, hex_value in palette["agents"].items():
        if provider in PROVIDER_BRAND_COLORS:
            continue
        colors = colors.with_agent_color(provider, hex_value)
    return colors


def apply_preset(colors: ColorSettings, preset: str) -> ColorSettings:
    """One-click personalities for how loud the light is.

    Three independent axes: LAYOUT (which agent gets which LEDs -- the
    blend mode), COLORS (whose color is whose), and FEEL (animation
    styles, speed, fade range, celebration toggles). A preset owns FEEL
    and nothing else.

    Layout and colors are the two things a user picks deliberately, and
    a preset used to silently overwrite the layout: choosing "Everything"
    forced Spotlight, so an explicitly chosen mode kept reverting with no
    warning and no undo. Feel is a dozen fiddly numbers nobody wants to
    tune by hand -- that asymmetry is the entire reason presets exist,
    and the reason they must keep their hands off the other two axes.
    """
    if preset == PRESET_CALM:
        result = colors.with_cycle_speed(2.4)
        for key in ANIMATION_MODE_KEYS:
            result = result.with_mode_animation(key, ANIMATION_STYLE_PULSE)
        for key in FADE_MODE_KEYS:
            result = result.with_fade_floor(key, 0.01).with_fade_ceiling(key, 0.35)
        return result.with_round_robin_urgency_alert(True).with_done_celebration_enabled(False)
    if preset == PRESET_INFORMATIVE:
        result = colors.with_cycle_speed(1.6)
        result = (
            result.with_mode_animation(MODE_IDLE, ANIMATION_STYLE_PULSE)
            .with_mode_animation(MODE_WORKING, ANIMATION_STYLE_PULSE)
            .with_mode_animation(MODE_ASK, ANIMATION_STYLE_BLINK)
        )
        for key in FADE_MODE_KEYS:
            result = result.with_fade_floor(key, 0.01).with_fade_ceiling(key, 0.5)
        return result.with_round_robin_urgency_alert(True).with_done_celebration_enabled(True)
    if preset == PRESET_EVERYTHING:
        result = colors.with_cycle_speed(1.2)
        result = (
            result.with_mode_animation(MODE_IDLE, ANIMATION_STYLE_PULSE)
            .with_mode_animation(MODE_WORKING, ANIMATION_STYLE_ROLL)
            .with_mode_animation(MODE_ASK, ANIMATION_STYLE_BLINK)
        )
        for key in FADE_MODE_KEYS:
            result = result.with_fade_floor(key, 0.02).with_fade_ceiling(key, 0.7)
        return result.with_round_robin_urgency_alert(True).with_done_celebration_enabled(True)
    raise ValueError(f"Unknown preset: {preset}")


# The FEEL axis: exactly the fields a preset owns. Everything else --
# blend mode, per-device overrides, agent and mode colors -- belongs to
# the user, so a preset chip must not go dark just because they chose a
# layout or recolored an agent.
PRESET_FEEL_FIELDS: tuple[str, ...] = (
    "cycle_speed_seconds",
    "mode_animation",
    "fade_floor",
    "fade_ceiling",
    "round_robin_urgency_alert",
    "done_celebration_enabled",
)


def _feel_projection(colors: ColorSettings) -> tuple:
    return tuple(getattr(colors, field) for field in PRESET_FEEL_FIELDS)


def matching_preset(colors: ColorSettings) -> str:
    """Which preset the current FEEL corresponds to, or PRESET_CUSTOM.

    Compares the feel projection rather than whole-object equality: a
    preset no longer writes layout or colors, so comparing everything
    would report "Custom" for any user who picked a mode or recolored
    an agent -- which is every user. The chip now means something true
    and stable: "the light is currently tuned Calm."
    """
    current = _feel_projection(colors)
    for preset in PRESET_CHOICES:
        if _feel_projection(apply_preset(colors, preset)) == current:
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


# The single-agent/aggregate renderer speaks in ANIMATION_STYLE_* rather
# than the motion vocabulary; this is the bridge, so "Claude chases" means
# the same thing whether Claude is alone on the strip or sharing it.
# EVERY motion needs a row: an unmapped motion silently fell back to the
# state's own style, which is why scanner/comet previewed (and aggregated)
# as a plain pulse. Travelling shapes map to the roll, shimmer-family
# shapes to the pulse.
PROVIDER_ANIMATION_STYLES: dict[str, str] = {
    MOTION_BREATHE: ANIMATION_STYLE_PULSE,
    MOTION_DUOTONE: ANIMATION_STYLE_PULSE,
    MOTION_CHASE: ANIMATION_STYLE_ROLL,
    MOTION_GRADIENT: ANIMATION_STYLE_ROLL,
    MOTION_HEARTBEAT: ANIMATION_STYLE_PULSE,
    MOTION_SCANNER: ANIMATION_STYLE_ROLL,
    MOTION_KITT: ANIMATION_STYLE_ROLL,
    MOTION_COMET: ANIMATION_STYLE_ROLL,
    MOTION_FLICKER: ANIMATION_STYLE_PULSE,
    MOTION_STACK: ANIMATION_STYLE_ROLL,
    MOTION_TWINKLE: ANIMATION_STYLE_PULSE,
    MOTION_DRIFT: ANIMATION_STYLE_PULSE,
    MOTION_CONVERGE: ANIMATION_STYLE_ROLL,
    MOTION_AURORA: ANIMATION_STYLE_PULSE,
    MOTION_TIDE: ANIMATION_STYLE_ROLL,
    MOTION_MARQUEE: ANIMATION_STYLE_ROLL,
    MOTION_STEADY: ANIMATION_STYLE_SOLID,
    MOTION_BLINK: ANIMATION_STYLE_BLINK,
}


def _provider_style_override(
    state: LedDisplayState,
    settings: ColorSettings,
    provider: str | None,
) -> str | None:
    """The ANIMATION_STYLE_* this provider's chosen animation implies for
    this state, or None when the state's own style stands."""
    if provider is None:
        return None
    mode_key = _STATE_TO_MODE_KEY.get(state)
    if mode_key not in ANIMATION_MODE_KEYS:
        return None
    # Same rule as agent_motion: urgency keeps the state vocabulary.
    if state in URGENT_STATES:
        return None
    chosen = settings.agent_animation(provider)
    if chosen == PROVIDER_ANIMATION_AUTO:
        return None
    return PROVIDER_ANIMATION_STYLES.get(chosen)


def _single_agent_program(
    color: str,
    state: LedDisplayState,
    *,
    led_count: int,
    brightness: float,
    settings: ColorSettings,
    provider: str | None = None,
) -> str:
    # A chosen motion renders its REAL whole-strip shape here (owner
    # decision, 2026-08-26): this function feeds the hover try-outs,
    # the hardware preview push, and Cycle's lone-agent case, and all
    # of them used to collapse 15 of 18 motions through the 4-bucket
    # style bridge -- what you previewed was not what the live solo
    # render played. Automatic keeps the classic state pipeline exactly.
    if (
        provider is not None
        and state not in URGENT_STATES
        and not _is_static_agent_state(state)
        and settings.agent_animation(provider) != PROVIDER_ANIMATION_AUTO
    ):
        duration_ms = max(
            1, int(settings.effective_speed_seconds(BLEND_MODE_CYCLE) * 1000)
        )
        settle_ms = settle_duration_ms(duration_ms)
        floor_color = _floor_for_state(color, state, settings)
        lines = [
            f"{floor_color} {settle_ms}ms cosine",
            *_motion_turn_lines(
                color,
                state,
                settings,
                provider=provider,
                led_count=led_count,
                duration_ms=duration_ms,
            ),
            "repeat",
        ]
        return apply_brightness("\n".join(lines), brightness)
    fade_kwargs = _fade_kwargs_for_state(state, settings)
    style = _provider_style_override(state, settings, provider)
    if style is not None:
        fade_kwargs = dict(fade_kwargs)
        fade_kwargs[_MODE_KEY_TO_STYLE_KWARG[_STATE_TO_MODE_KEY[state]]] = style
    return program_for_display_state(
        state,
        led_count=led_count,
        brightness=brightness,
        done_celebrate=settings.done_celebration_enabled,
        **_color_kwargs_for_state(state, color),
        **fade_kwargs,
    )


def provider_motion_preview_program(
    provider: str,
    color: str,
    settings: ColorSettings,
    *,
    led_count: int = 8,
    brightness: float = 255,
) -> str:
    """The real Working-state shape for one provider's chosen motion --
    the settings thumbnails' program source, so a thumb shows the thing
    the strip will actually play."""
    return _single_agent_program(
        color,
        LedDisplayState.WORKING,
        led_count=led_count,
        brightness=brightness,
        settings=settings,
        provider=provider,
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
    ordered = sorted(statuses, key=_stable_agent_sort_key)
    # Identity colors ("color = agent") apply only when there's a crowd
    # to tell apart; a lone session keeps its provider's brand color.
    identity: dict[str, str] = {}
    if len(ordered) > 1:
        groups = identity_groups_for_statuses(ordered, colors)
        identity = (
            identity_colors_for_agents(
                [status.agent_id for status in ordered], groups=groups
            )
            if groups
            else provider_identity_colors_for_agents(
                [(status.agent_id, status.provider) for status in ordered],
                colors=colors,
            )
        )
    agents: list[_ActiveAgent] = []
    for status in ordered:
        override = colors.session_color(status.agent_id)
        color = override or identity.get(status.agent_id) or colors.agent_color(status.provider)
        agents.append(
            _ActiveAgent(
                provider=status.provider,
                color=readable_identity_hex(color),
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
    brightness: float,
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


def _is_static_agent_state(state: LedDisplayState) -> bool:
    return state in {LedDisplayState.DONE, LedDisplayState.FAILED}


def _speed_safe_motion(motion: str, *, cycle_ms: int) -> str:
    """Degrade a rhythm rather than let it strobe.

    The beat and the hard blink both need room: at the fastest cycle the user
    can dial they would run at 10Hz and 3.3Hz respectively. Under
    MIN_FLASH_CYCLE_MS each falls back to the gentler motion it is built from,
    so "nothing above 2Hz" holds however the motion was chosen -- by the state
    or by the person who picked it for a provider.
    """
    if cycle_ms >= MIN_FLASH_CYCLE_MS:
        return motion
    if motion in (
        MOTION_BEAT,
        MOTION_HEARTBEAT,
        MOTION_FLICKER,
        MOTION_TWINKLE,
        MOTION_DRIFT,
        MOTION_AURORA,
        MOTION_DUOTONE,
    ):
        return MOTION_BREATHE
    if motion == MOTION_BLINK:
        return MOTION_STEADY
    if motion in (
        MOTION_SCANNER,
        MOTION_KITT,
        MOTION_COMET,
        MOTION_STACK,
        MOTION_CONVERGE,
        MOTION_TIDE,
        MOTION_MARQUEE,
        MOTION_GRADIENT,
    ):
        return MOTION_CHASE
    return motion


def state_motion(state: LedDisplayState, *, cycle_ms: int) -> str:
    """The rhythm this state carries at a given cycle length."""
    return _speed_safe_motion(STATE_MOTION.get(state, MOTION_BREATHE), cycle_ms=cycle_ms)


def agent_motion(
    state: LedDisplayState,
    *,
    cycle_ms: int,
    provider: str | None = None,
    settings: ColorSettings | None = None,
) -> str:
    """The rhythm ONE agent's LEDs carry -- the provider's chosen animation
    when it has one, otherwise the state's own.

    Urgency is not negotiable: ASK and FAILED keep the state vocabulary no
    matter what the provider was set to. The per-provider choice exists so a
    person can say "Claude breathes, Codex chases" and tell two working
    agents apart by rhythm; it is not a switch for turning "a human is
    needed" into something calmer.
    """
    if state in URGENT_STATES:
        return state_motion(state, cycle_ms=cycle_ms)
    chosen = PROVIDER_ANIMATION_AUTO
    if settings is not None and provider:
        chosen = settings.agent_animation(provider)
    if chosen == PROVIDER_ANIMATION_AUTO:
        return state_motion(state, cycle_ms=cycle_ms)
    return _speed_safe_motion(chosen, cycle_ms=cycle_ms)


def _holds_still(
    state: LedDisplayState,
    *,
    cycle_ms: int,
    provider: str | None = None,
    settings: ColorSettings | None = None,
) -> bool:
    """Whether this agent owns no share of the traveling stagger.

    Steady holds and hard blinks both sit on one LED and do their own thing,
    so a strip made only of them renders identically at every phase -- which
    is what keeps Relay's program byte-stable (and therefore un-rewritten)
    while nothing is actually moving. A provider parked on Steady counts as
    still for exactly the same reason its state would have.
    """
    return agent_motion(
        state, cycle_ms=cycle_ms, provider=provider, settings=settings
    ) in {MOTION_STEADY, MOTION_BLINK}


def _peak_for_state(color: str, state: LedDisplayState, settings: ColorSettings) -> str:
    """The colour one LED swells UP to for this state.

    Ordinary states peak at the user's gentleness ceiling. Urgent ones peak at
    full: the ceiling exists to stop ambient breathing being harsh, and an Ask
    is not ambient. On the default palette this is the whole loudness fix --
    ask goes from Y 0.055 (2.5x dimmer than working) to Y 0.243 (1.8x louder).
    """
    if state in URGENT_STATES:
        return color
    mode_key = _STATE_TO_MODE_KEY[state]
    _floor, ceiling = settings.fade_range(mode_key)
    return color if ceiling >= 1.0 else scale_hex_brightness(color, ceiling)


def _floor_for_state(color: str, state: LedDisplayState, settings: ColorSettings) -> str:
    """The colour one LED rests DOWN to between swells.

    For an urgent state the gentleness ceiling becomes the FLOOR: a light that
    means "a human is needed" must never go dark between beats, or the whole
    signal is only present for the fraction of a cycle it happens to be lit.
    So Ask now RESTS at exactly the level it used to peak at, and beats to full
    from there -- it is lit at every instant of the cycle, not only at the top
    of a swell an ambient breathe was already brighter than.
    """
    mode_key = _STATE_TO_MODE_KEY[state]
    floor, ceiling = settings.fade_range(mode_key)
    if state in URGENT_STATES and floor > 0.0:
        # A floor of exactly zero is the user saying "go all the way dark".
        # The lift raises a resting glow; it does not invent one.
        # MINIMUM SWING (owner decision, 2026-08-26): an urgent state
        # must always keep at least a quarter of the range between rest
        # and peak -- at ceiling 1.0 the old lift made floor == peak and
        # an Ask rendered as an unblinking steady light.
        return scale_hex_brightness(color, min(ceiling, 0.75))
    return "#000000" if floor <= 0.0 else scale_hex_brightness(color, floor)


def _motion_segments(
    led_index: int,
    color: str,
    state: LedDisplayState,
    settings: ColorSettings,
    *,
    cycle_ms: int,
    settle_ms: int,
    delay_ms: int = 0,
    chase_delay_ms: int = 0,
    provider: str | None = None,
) -> tuple[str, str]:
    """One LED's (reset line segment, motion line segment) for this state.

    Every shape is two segments on the same two lines, so the caller's program
    keeps its exact two-line-plus-repeat structure no matter which states are
    on screen -- LED assignment, firmware line/byte budgets and the write
    dedup all stay where they were. Only the rhythm differs.

    ``delay_ms`` is the offset this LED gets whatever it is doing (Relay's
    baton, where the layout itself is a chase); ``chase_delay_ms`` is the
    extra traveling-wave offset that ONLY the chase motion takes, which is
    what separates a wave from a strip breathing in unison.

    A note on the DSL: a line ends at its longest ``delay + duration``, and
    ``pulse`` returns to the line's start colour, so a segment shorter than
    the line simply finishes early and rests. That is what turns a short pulse
    into a beat with dark after it, for free.

    ``provider`` opts this LED into that provider's own chosen animation (see
    agent_motion); omitted, the state picks the rhythm exactly as before.
    """
    motion = agent_motion(state, cycle_ms=cycle_ms, provider=provider, settings=settings)
    peak = _peak_for_state(color, state, settings)
    tail = f" {delay_ms}ms" if delay_ms else ""

    if motion == MOTION_STEADY:
        # A done agent's steadiness is REST, not a held peak: the completion
        # sweep already celebrated, and a bright LED that nobody needs to
        # answer reads as a phantom ask for its whole 20-minute window.
        resting = (
            _floor_for_state(color, state, settings)
            if state is LedDisplayState.DONE
            else peak
        )
        held = f"{led_index}:{resting} {settle_ms}ms cosine"
        return held, held

    if motion == MOTION_BLINK:
        # Hard edges, no easing anywhere: the one shape in the vocabulary that
        # never eases, so it cannot be mistaken for a breath even in a colour
        # a dichromat reads the same as Done's green.
        half = max(1, cycle_ms // 2)
        floor_color = _floor_for_state(color, state, settings)
        return (
            f"{led_index}:{peak} {settle_ms}ms none",
            f"{led_index}:{floor_color} {half}ms none {delay_ms + half}ms",
        )

    floor_segment = f"{led_index}:{_floor_for_state(color, state, settings)} {settle_ms}ms cosine"
    if motion == MOTION_BEAT:
        beat_ms = max(1, min(cycle_ms, max(cycle_ms // 3, MIN_BEAT_MS)))
        # No traveling wave: every asking LED beats together. A wave says
        # "busy", a unison beat says "stop".
        return floor_segment, f"{led_index}:{peak} {beat_ms}ms pulse{tail}"
    if motion == MOTION_HEARTBEAT:
        # Lub-dub: two quick swells, then the rest of the cycle dark.
        beat_ms = max(1, cycle_ms // 6)
        gap_ms = max(1, cycle_ms // 10)
        return floor_segment, (
            f"{led_index}:{peak} {beat_ms}ms pulse{tail}; "
            f"{led_index}:{peak} {beat_ms}ms pulse {delay_ms + beat_ms + gap_ms}ms"
        )
    if motion == MOTION_TWINKLE:
        # One brief spark per cycle at a frozen scattered offset.
        spark_ms = max(1, cycle_ms // 5)
        offset = (led_index * 613) % max(1, cycle_ms - spark_ms)
        return (
            floor_segment,
            f"{led_index}:{peak} {spark_ms}ms pulse {delay_ms + offset}ms",
        )
    if motion in (MOTION_FLICKER, MOTION_DRIFT, MOTION_AURORA):
        # Frozen per-LED detune: each LED swells on its own duration and
        # offset inside the shared cycle, so the strip shimmers instead
        # of breathing in lockstep. Deterministic -- same program bytes
        # every render, so the write dedupe still holds.
        stretch = 2 if motion in (MOTION_DRIFT, MOTION_AURORA) else 1
        duration = max(1, stretch * ((2 * cycle_ms) // 3) + (led_index * 137) % 331)
        offset = (led_index * 271) % max(1, cycle_ms // 3)
        if motion == MOTION_AURORA:
            # Aurora rests on a LUMINOUS bed (its whole identity: light
            # moving on water at night), where drift rests near-dark.
            # In shared strips the two rendered byte-identical until
            # 2026-08-26 -- a dead pair in an 18-choice picker.
            bed = scale_hex_brightness(peak, 0.22)
            floor_segment = f"{led_index}:{bed} {settle_ms}ms cosine"
            offset = (led_index * 617) % max(1, cycle_ms // 2)
        return (
            floor_segment,
            f"{led_index}:{peak} {duration}ms pulse {delay_ms + offset}ms",
        )
    if motion in (
        MOTION_CHASE,
        MOTION_SCANNER,
        MOTION_KITT,
        MOTION_COMET,
        MOTION_STACK,
        MOTION_CONVERGE,
        MOTION_TIDE,
        MOTION_MARQUEE,
        MOTION_GRADIENT,
    ):
        # In a MULTI-agent layout a provider's positional sweep rides
        # the travelling-wave conversion (delay = its share of the
        # stagger, contiguous in Spatial blocks, interleaved elsewhere)
        # -- but the CLASSES stay distinguishable (owner decision,
        # 2026-08-26; they used to collapse into one identical wave):
        # scanner/kitt/comet/marquee/gradient travel as a NARROW flare,
        # stack piles on hard with no easing, and chase/tide/converge
        # keep the full swell. Real shapes remain the solo render's.
        if motion in (
            MOTION_SCANNER,
            MOTION_KITT,
            MOTION_COMET,
            MOTION_MARQUEE,
            MOTION_GRADIENT,
        ):
            width_ms = max(MIN_FLASH_CYCLE_MS, cycle_ms // 2)
            return (
                floor_segment,
                f"{led_index}:{peak} {width_ms}ms pulse {delay_ms + chase_delay_ms}ms",
            )
        if motion == MOTION_STACK:
            return (
                floor_segment,
                f"{led_index}:{peak} {cycle_ms}ms none {delay_ms + chase_delay_ms}ms",
            )
        return (
            floor_segment,
            f"{led_index}:{peak} {cycle_ms}ms pulse {delay_ms + chase_delay_ms}ms",
        )
    # Breathe: the full cycle, in unison.
    return floor_segment, f"{led_index}:{peak} {cycle_ms}ms pulse{tail}"


def _cycle_turn_lines(
    agent: _ActiveAgent,
    settings: ColorSettings,
    *,
    led_count: int,
    duration_ms: int,
) -> list[str]:
    """One agent's TURN in the Cycle layout, in its chosen rhythm class."""
    return _motion_turn_lines(
        _display_color_for_agent(agent, settings),
        agent.state,
        settings,
        provider=agent.provider,
        led_count=led_count,
        duration_ms=duration_ms,
    )


def _motion_turn_lines(
    color: str,
    state: LedDisplayState,
    settings: ColorSettings,
    *,
    provider: str | None,
    led_count: int,
    duration_ms: int,
) -> list[str]:
    """A whole-strip rendering of this color/state's chosen rhythm.

    The owner of these lines has the full strip for `duration_ms`, so
    positional shapes get a real line to travel. Every shape stays
    inside the safety compiler's floors (the compiler still has the
    last word). Deterministic bytes -- write dedupe holds. Shared by
    the Cycle layout's turns and the solo/preview render."""
    if _is_static_agent_state(state):
        return [
            f"{_peak_for_state(color, state, settings)} {duration_ms}ms cosine"
        ]
    peak = _peak_for_state(color, state, settings)
    floor_color = _floor_for_state(color, state, settings)
    motion = agent_motion(
        state,
        cycle_ms=duration_ms,
        provider=provider,
        settings=settings,
    )
    if motion == MOTION_STEADY:
        return [f"{peak} {duration_ms}ms cosine"]
    if motion == MOTION_BLINK:
        half = max(1, duration_ms // 2)
        return [f"{peak} {half}ms none", f"{floor_color} {half}ms none"]
    if motion == MOTION_HEARTBEAT:
        beat = max(250, duration_ms // 6)
        rest = max(250, duration_ms - 3 * beat)
        return [
            f"{peak} {beat}ms pulse",
            f"{peak} {beat}ms pulse {max(0, beat // 2)}ms",
            f"{floor_color} {rest}ms none",
        ]
    if motion == MOTION_DUOTONE:
        from .presentation_policy import _hue_shifted_color

        half = max(250, duration_ms // 2)
        return [
            f"{peak} {half}ms pulse",
            f"{_hue_shifted_color(peak, 40.0)} {half}ms pulse",
        ]
    if motion in (MOTION_FLICKER, MOTION_TWINKLE, MOTION_DRIFT, MOTION_AURORA):
        seed = 617 if motion == MOTION_AURORA else 271
        segments = [
            f"{index}:{peak} "
            f"{max(250, (2 * duration_ms) // 3 + (index * 137) % 331)}ms pulse "
            f"{(index * seed) % max(1, duration_ms // 3)}ms"
            for index in range(led_count)
        ]
        return ["; ".join(segments)]
    if motion in (
        MOTION_CHASE,
        MOTION_SCANNER,
        MOTION_KITT,
        MOTION_COMET,
        MOTION_STACK,
        MOTION_CONVERGE,
        MOTION_TIDE,
        MOTION_GRADIENT,
        MOTION_MARQUEE,
    ):
        from .presentation_policy import _hue_shifted_color

        step = max(60, duration_ms // max(1, 2 * led_count))
        width = max(250, min(duration_ms // 2, 3 * step))
        segments = []
        for index in range(led_count):
            shade = peak
            if motion in (MOTION_GRADIENT, MOTION_MARQUEE):
                fraction = index / max(1, led_count - 1)
                shade = _hue_shifted_color(peak, (fraction - 0.5) * 48.0)
            if motion == MOTION_CONVERGE:
                delay = min(index, led_count - 1 - index) * step
            else:
                delay = index * step
            if motion == MOTION_TIDE:
                segments.append(
                    f"{index}:{shade} "
                    f"{max(250, 2 * (led_count - index) * step)}ms pulse {delay}ms"
                )
            elif motion == MOTION_STACK:
                segments.append(
                    f"{index}:{shade} "
                    f"{max(250, (led_count - index) * step + 300)}ms none {delay}ms"
                )
            else:
                segments.append(f"{index}:{shade} {width}ms pulse {delay}ms")
        lines = ["; ".join(segments)]
        if motion == MOTION_STACK:
            lines.append(f"{floor_color} 400ms cosine")
        return lines
    return [f"{peak} {duration_ms}ms pulse"]


def _cycle_program(
    agents: list[_ActiveAgent],
    *,
    led_count: int,
    brightness: float,
    settings: ColorSettings,
) -> str:
    # Show each active agent's own color in turn across the whole strip --
    # a real breath (dim -> bright -> dim) per agent, not a flat fade-and-
    # hold, using the user-configurable cycle speed.
    if len(agents) == 1:
        agent = agents[0]
        return _single_agent_program(
            agent.color,
            agent.state,
            led_count=led_count,
            brightness=brightness,
            settings=settings,
            provider=agent.provider,
        )

    duration_ms = max(1, int(settings.effective_speed_seconds(BLEND_MODE_CYCLE) * 1000))
    settle_ms = settle_duration_ms(duration_ms)
    # Eased rather than a bare "off" -- a bare assignment is an instant,
    # un-eased snap (see settle_duration_ms()), which is only ever invisible
    # once steady-state; the moment a real status change interrupts this
    # loop mid-breath, a bare snap reads as the animation abruptly stopping
    # instead of easing into the next cycle.
    #
    # Each agent's TURN owns the whole strip, so its chosen motion can
    # actually render (owner decision 2026-08-26 -- Cycle used to ignore
    # the motion picker entirely). Rich turns are built first; if the
    # firmware's 512-byte / 20-line budget would overflow, agents
    # degrade from the END back to the plain breathe until it fits.
    lead = [f"off {settle_ms}ms cosine"]

    def plain_turn(agent) -> list[str]:
        color = _display_color_for_agent(agent, settings)
        if _is_static_agent_state(agent.state):
            return [
                f"{_peak_for_state(color, agent.state, settings)} {duration_ms}ms cosine"
            ]
        return [f"{_peak_for_state(color, agent.state, settings)} {duration_ms}ms pulse"]

    # Rich turns only for an EXPLICIT motion choice -- Automatic keeps
    # Cycle's classic whole-strip breath per agent exactly (same guard
    # the solo render uses), so the speed dial's rendered duration and
    # the layout's identity are unchanged for everyone who never opened
    # the motion picker.
    turns = [
        (
            _cycle_turn_lines(
                agent,
                settings,
                led_count=led_count,
                duration_ms=duration_ms,
            )
            if settings.agent_animation(agent.provider) != PROVIDER_ANIMATION_AUTO
            else plain_turn(agent)
        )
        for agent in agents
    ]

    def assembled(current_turns) -> str:
        return "\n".join([*lead, *(line for turn in current_turns for line in turn), "repeat"])

    program = assembled(turns)
    degrade_index = len(turns) - 1
    while degrade_index >= 0 and (
        len(program.encode("utf-8")) > MAX_LED_BYTES - 24
        or program.count("\n") + 1 > MAX_LED_LINES - 1
    ):
        turns[degrade_index] = plain_turn(agents[degrade_index])
        program = assembled(turns)
        degrade_index -= 1
    return apply_brightness(program, brightness)


def _agents_that_fit(agents: list[_ActiveAgent], led_count: int) -> list[_ActiveAgent]:
    """Everyone, or the most urgent: with more agents than LEDs the
    modulo layouts silently dropped whoever sorted LAST in registry
    order -- an ASKING agent could be invisible while an idle one kept
    its slot. Past the strip's capacity, keep the led_count highest-
    weight agents, preserving stable order among the kept."""
    if len(agents) <= led_count:
        return agents
    kept_indexes = sorted(
        sorted(range(len(agents)), key=lambda index: -agents[index].weight)[:led_count]
    )
    return [agents[index] for index in kept_indexes]


def _round_robin_program(
    agents: list[_ActiveAgent],
    *,
    led_count: int,
    brightness: float,
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
            agent.color,
            agent.state,
            led_count=led_count,
            brightness=brightness,
            settings=settings,
            provider=agent.provider,
        )

    agents = _agents_that_fit(agents, led_count)
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
        reset, pulse = _motion_segments(
            index,
            color,
            agent.state,
            settings,
            cycle_ms=duration_ms,
            settle_ms=settle_ms,
            chase_delay_ms=(index * stagger_ms) % duration_ms,
            provider=agent.provider,
        )
        reset_segments.append(reset)
        pulse_segments.append(pulse)

    program_lines = ["; ".join(reset_segments), "; ".join(pulse_segments), "repeat"]
    return apply_brightness("\n".join(program_lines), brightness)


def _relay_program(
    agents: list[_ActiveAgent],
    *,
    led_count: int,
    brightness: float,
    settings: ColorSettings,
    relay_elapsed_seconds: float = 0.0,
) -> str:
    """A baton pass: exactly one LED flares to its agent's peak color at a
    time, in a chase around the ring, while every other LED holds a soft
    resting glow in its own agent's color. Same per-LED agent assignment as
    Round-Robin (the sequence repeats across the strip), but with a full
    stagger instead of a small one -- Round-Robin reads as one synchronized
    wave breathing together; Relay reads as a single spotlight visiting each
    agent in turn before moving on."""
    if led_count <= 0:
        return ""
    if len(agents) == 1:
        agent = agents[0]
        return _single_agent_program(
            agent.color,
            agent.state,
            led_count=led_count,
            brightness=brightness,
            settings=settings,
            provider=agent.provider,
        )

    traversal_seconds = settings.effective_speed_seconds(BLEND_MODE_RELAY)
    agents = _agents_that_fit(agents, led_count)
    step_ms = relay_step_ms(traversal_seconds, led_count)
    start_index = relay_phase_index(
        relay_elapsed_seconds,
        traversal_seconds,
        led_count,
    )
    settle_ms = settle_duration_ms(step_ms)
    # "Nothing is travelling" is now a property of the MOTION, not of a fixed
    # state list: a strip of steady holds and hard blinks renders the same at
    # every phase, so the program stays byte-stable and the device is not
    # rewritten for motion that does not exist.
    all_static = all(
        _holds_still(
            agent.state, cycle_ms=step_ms, provider=agent.provider, settings=settings
        )
        for agent in agents
    )
    led_order = (
        tuple(range(led_count))
        if all_static
        else relay_led_order(led_count, start_index)
    )

    reset_segments: list[str] = []
    pulse_segments: list[str] = []
    for turn, index in enumerate(led_order):
        agent = agents[index % len(agents)]
        color = _display_color_for_agent(agent, settings)
        # Full stagger -- one LED's entire turn elapses before the next
        # one's delay expires, so only one LED is ever mid-flare.
        reset, pulse = _motion_segments(
            index,
            color,
            agent.state,
            settings,
            cycle_ms=step_ms,
            settle_ms=settle_ms,
            delay_ms=turn * step_ms,
            provider=agent.provider,
        )
        reset_segments.append(reset)
        pulse_segments.append(pulse)

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
    brightness: float,
    settings: ColorSettings,
) -> str:
    if len(agents) > led_count:
        return _color_blend_program(agents, led_count=led_count, brightness=brightness, settings=settings)

    blocks = _spatial_split_blocks(agents, led_count)

    segments: list[str] = []
    reset_segments: list[str] = []
    index = 0
    for agent, count in blocks:
        duration_ms = _SEGMENT_DURATION_MS_BY_STATE.get(agent.state, 760)
        settle_ms = settle_duration_ms(duration_ms)
        # Intra-block stagger: a spatial block is CONTIGUOUS, so the
        # positional-degrades-to-wave conversion actually reads as
        # travel here -- a chase runs through the block, a heartbeat
        # lub-dubs it, a twinkle sparks it. Before 2026-08-26 this
        # layout honored only Steady and rendered everything else as a
        # plain unison pulse (owner decision: motions real everywhere).
        stagger_ms = duration_ms // max(2, count) if count > 1 else 0
        block_motion = agent_motion(
            agent.state,
            cycle_ms=duration_ms,
            provider=agent.provider,
            settings=settings,
        )
        for offset in range(count):
            led_index = index + offset
            if _is_static_agent_state(agent.state):
                segments.append(_segment_for_agent(led_index, agent, settings))
                reset_segments.append(
                    _reset_segment_for_agent(led_index, agent, settings)
                )
                continue
            if block_motion == MOTION_CONVERGE:
                # Two fronts leaving the block's ends and meeting in
                # its middle -- the block is contiguous, so this reads.
                position = min(offset, count - 1 - offset)
            else:
                position = offset
            reset, motion = _motion_segments(
                led_index,
                _display_color_for_agent(agent, settings),
                agent.state,
                settings,
                cycle_ms=duration_ms,
                settle_ms=settle_ms,
                chase_delay_ms=(position * stagger_ms) % max(1, duration_ms),
                provider=agent.provider,
            )
            segments.append(motion)
            reset_segments.append(reset)
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
        program_lines = [
            *program_lines[:-1],
            f"roll {IDLE_ROLL_SECONDS:g}s linear",
            *program_lines[-1:],
        ]

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
# DONE and FAILED have no natural duration of their own to scale a settle
# time against. Both are static holds, not breathing cycles, so use the
# cheapest settle the adaptive helper allows.
_STATIC_SETTLE_MS = settle_duration_ms(0)


def _segment_for_agent(led_index: int, agent: _ActiveAgent, settings: ColorSettings) -> str:
    if _is_static_agent_state(agent.state):
        return f"{led_index}:{agent.color} {_STATIC_SETTLE_MS}ms cosine"
    mode_key = _STATE_TO_MODE_KEY[agent.state]
    # Spatial Split already tells states apart by PERIOD (6s idle vs 1.6s ask
    # vs 760ms working) and by block size, so it keeps the user's chosen
    # animation style -- the only thing it was missing is that an urgent block
    # was rendered at the ambient gentleness ceiling.
    peak_color = _peak_for_state(agent.color, agent.state, settings)
    style = _provider_style_override(agent.state, settings, agent.provider) or settings.animation_style(
        mode_key
    )
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
    style = _provider_style_override(agent.state, settings, agent.provider) or settings.animation_style(
        mode_key
    )
    # Must agree with _segment_for_agent's own style choice, provider
    # override included: a reset line that thinks the block is pulsing while
    # the motion line holds it solid leaves the block parked at its floor.
    if _is_static_agent_state(agent.state) or style == ANIMATION_STYLE_SOLID:
        # Not pulsing -- assign directly, no floor to settle to first.
        settle_ms = (
            _STATIC_SETTLE_MS
            if _is_static_agent_state(agent.state)
            else settle_duration_ms(_SEGMENT_DURATION_MS_BY_STATE[agent.state])
        )
        return f"{led_index}:{agent.color} {settle_ms}ms cosine"
    floor, _ceiling = settings.fade_range(mode_key)
    floor_color = "#000000" if floor <= 0.0 else scale_hex_brightness(agent.color, floor)
    settle_ms = settle_duration_ms(_SEGMENT_DURATION_MS_BY_STATE[agent.state])
    return f"{led_index}:{floor_color} {settle_ms}ms cosine"


def program_for_snapshot(
    statuses: tuple[AgentStatus, ...],
    *,
    led_count: int = 8,
    colors: ColorSettings | None = None,
    brightness: float = 255,
    fallback_mode: AgentMode = AgentMode.IDLE_READY,
    relay_elapsed_seconds: float = 0.0,
    include_attention_arrival: bool = False,
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

    agents = _active_agents(statuses, settings)

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
        attention = _attention_motion_programs(
            program,
            agents,
            led_count=led_count,
            settings=settings,
            brightness=brightness,
        )
        return state, _selected_attention_program(
            attention,
            include_arrival=include_attention_arrival,
        )

    if settings.blend_mode == BLEND_MODE_RELAY and led_count <= 0:
        return _representative_state(agents), ""

    if len(agents) == 1:
        agent = agents[0]
        program = _single_agent_program(
            agent.color,
            agent.state,
            led_count=led_count,
            brightness=brightness,
            settings=settings,
            provider=agent.provider,
        )
        attention = _attention_motion_programs(
            program,
            agents,
            led_count=led_count,
            settings=settings,
            brightness=brightness,
        )
        return agent.state, _selected_attention_program(
            attention,
            include_arrival=include_attention_arrival,
        )

    state = _representative_state(agents)
    if settings.blend_mode == BLEND_MODE_COLOR:
        program = _color_blend_program(agents, led_count=led_count, brightness=brightness, settings=settings)
    elif settings.blend_mode == BLEND_MODE_CYCLE:
        program = _cycle_program(agents, led_count=led_count, brightness=brightness, settings=settings)
    elif settings.blend_mode == BLEND_MODE_SPATIAL:
        program = _spatial_split_program(agents, led_count=led_count, brightness=brightness, settings=settings)
    elif settings.blend_mode == BLEND_MODE_RELAY:
        program = _relay_program(
            agents,
            led_count=led_count,
            brightness=brightness,
            settings=settings,
            relay_elapsed_seconds=relay_elapsed_seconds,
        )
    else:  # Default / BLEND_MODE_ROUND_ROBIN
        program = _round_robin_program(agents, led_count=led_count, brightness=brightness, settings=settings)
    attention = _attention_motion_programs(
        program,
        agents,
        led_count=led_count,
        settings=settings,
        brightness=brightness,
    )
    return state, _selected_attention_program(
        attention,
        include_arrival=include_attention_arrival,
    )


def program_for_projection(
    projection,
    *,
    active_signal=None,
    led_count: int = 8,
    colors: ColorSettings | None = None,
    brightness: float = 255,
    relay_elapsed_seconds: float = 0.0,
    include_attention_arrival: bool = False,
) -> tuple[LedDisplayState, str]:
    """Render only semantics already decided by ``AttentionProjection``."""
    from .attention import LifecycleMode

    settings = colors or ColorSettings.defaults()
    brightness = normalize_brightness(brightness)
    state = display_state_for_projection(projection, active_signal)
    if active_signal is not None:
        return state, failure_signal_program(
            settings.mode_color(MODE_ASK),
            active_signal,
            brightness=brightness,
            led_count=led_count,
        )

    # light_rows: main agents only, or one stand-in for an orphaned
    # background crowd. Colouring visible_rows here fed 114 rows -- 87 of
    # them Task workers -- into identity assignment, which is why the
    # multi-agent renderer never switched off and Claude's brand hue
    # never reached the strip.
    rows = tuple(projection.light_rows)
    if not rows:
        return state, program_for_display_state(
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

    ordered = sorted(rows, key=lambda row: _stable_agent_sort_key(row.source_status))
    identity: dict[str, str] = {}
    if len(ordered) > 1:
        groups = identity_groups_for_statuses(
            [row.source_status for row in ordered], settings
        )
        identity = (
            # Color by Project is an explicit override: the bar answers
            # "which project needs me", so project hue replaces brand.
            identity_colors_for_agents([row.agent_id for row in ordered], groups=groups)
            if groups
            # Otherwise a crowd is still told apart INSIDE each brand.
            else provider_identity_colors_for_agents(
                [(row.agent_id, row.provider) for row in ordered],
                colors=settings,
            )
        )
    state_by_lifecycle = {
        LifecycleMode.IDLE: LedDisplayState.IDLE,
        LifecycleMode.ACTIVE: LedDisplayState.WORKING,
        LifecycleMode.WAITING: LedDisplayState.ASK,
        LifecycleMode.COMPLETED_RECENTLY: LedDisplayState.DONE,
        LifecycleMode.FAILED_VISIBLE: LedDisplayState.FAILED,
        LifecycleMode.UNKNOWN: LedDisplayState.IDLE,
    }
    weight_by_lifecycle = {
        LifecycleMode.WAITING: 7,
        LifecycleMode.ACTIVE: 6,
        LifecycleMode.COMPLETED_RECENTLY: 5,
        LifecycleMode.FAILED_VISIBLE: 4,
        LifecycleMode.IDLE: 1,
        LifecycleMode.UNKNOWN: 1,
    }
    agents = [
        _ActiveAgent(
            provider=row.provider,
            color=readable_identity_hex(
                settings.session_color(row.agent_id)
                or identity.get(row.agent_id)
                or settings.agent_color(row.provider)
            ),
            state=state_by_lifecycle[row.lifecycle_mode],
            weight=weight_by_lifecycle[row.lifecycle_mode],
        )
        for row in ordered
    ]
    if settings.blend_mode == BLEND_MODE_CLASSIC:
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
        attention = _attention_motion_programs(
            program,
            agents,
            led_count=led_count,
            settings=settings,
            brightness=brightness,
        )
        return state, _selected_attention_program(
            attention,
            include_arrival=include_attention_arrival,
        )
    if settings.blend_mode == BLEND_MODE_RELAY and led_count <= 0:
        return _representative_state(agents), ""
    if len(agents) == 1:
        agent = agents[0]
        program = _single_agent_program(
            agent.color,
            agent.state,
            led_count=led_count,
            brightness=brightness,
            settings=settings,
            provider=agent.provider,
        )
        attention = _attention_motion_programs(
            program,
            agents,
            led_count=led_count,
            settings=settings,
            brightness=brightness,
        )
        return agent.state, _selected_attention_program(
            attention,
            include_arrival=include_attention_arrival,
        )
    if settings.blend_mode == BLEND_MODE_COLOR:
        program = _color_blend_program(
            agents, led_count=led_count, brightness=brightness, settings=settings
        )
    elif settings.blend_mode == BLEND_MODE_CYCLE:
        program = _cycle_program(
            agents, led_count=led_count, brightness=brightness, settings=settings
        )
    elif settings.blend_mode == BLEND_MODE_SPATIAL:
        program = _spatial_split_program(
            agents, led_count=led_count, brightness=brightness, settings=settings
        )
    elif settings.blend_mode == BLEND_MODE_RELAY:
        program = _relay_program(
            agents,
            led_count=led_count,
            brightness=brightness,
            settings=settings,
            relay_elapsed_seconds=relay_elapsed_seconds,
        )
    else:
        program = _round_robin_program(
            agents, led_count=led_count, brightness=brightness, settings=settings
        )
    attention = _attention_motion_programs(
        program,
        agents,
        led_count=led_count,
        settings=settings,
        brightness=brightness,
    )
    return _representative_state(agents), _selected_attention_program(
        attention,
        include_arrival=include_attention_arrival,
    )


@dataclass(frozen=True)
class AttentionMotionPrograms:
    """Persistent attention truth and its optional one-shot arrival cue."""

    base_program: str
    arrival_program: str


def _selected_attention_program(
    programs: AttentionMotionPrograms,
    *,
    include_arrival: bool,
) -> str:
    return programs.arrival_program if include_arrival else programs.base_program


def compose_attention_arrival(
    base_program: str,
    *,
    attention_color: str,
    brightness: float = 255,
) -> AttentionMotionPrograms:
    """Compose at most two finite taps before an already-static anchor."""
    if any(line.strip() == "repeat" for line in base_program.splitlines()):
        raise ValueError("attention base program must be finite")
    if not base_program:
        return AttentionMotionPrograms(base_program="", arrival_program="")

    color = normalize_hex(attention_color, ASK_AMBER)
    crest = apply_brightness(f"{color} {ATTENTION_CREST_MS}ms cosine", brightness)
    settle = apply_brightness(
        f"{scale_hex_brightness(color, ATTENTION_CREST_HOLD_FRACTION)} "
        f"{ATTENTION_CREST_SETTLE_MS}ms cosine",
        brightness,
    )
    full = [crest, settle]
    single = [crest]
    for preamble in (full, single):
        candidate = "\n".join([*preamble, base_program])
        if len(candidate.splitlines()) <= MAX_LED_LINES and len(candidate.encode("utf-8")) <= MAX_LED_BYTES:
            return AttentionMotionPrograms(
                base_program=base_program,
                arrival_program=candidate,
            )
    return AttentionMotionPrograms(
        base_program=base_program,
        arrival_program=base_program,
    )


def _attention_spatial_anchor_program(
    agents: list[_ActiveAgent],
    *,
    led_count: int,
    settings: ColorSettings,
    brightness: float,
) -> str:
    count = max(0, int(led_count))
    if count == 0:
        return ""

    visible = agents
    if len(visible) > count:
        ranked_indices = sorted(
            range(len(visible)),
            key=lambda index: (-visible[index].weight, index),
        )[:count]
        selected = set(ranked_indices)
        visible = [agent for index, agent in enumerate(visible) if index in selected]

    # Two lines, not one: the anchor now RISES into place instead of snapping.
    # The urgent block holds at full rather than at the ambient gentleness
    # ceiling (that is the loudness fix -- an anchor that means "a human is
    # needed" should not be dimmer than an agent quietly working), and an
    # un-eased jump straight to full reads as a glitch rather than as the
    # light standing up. The approach line is where every LED already was, so
    # for calm agents the two lines are identical and nothing moves.
    approach: list[str] = []
    hold: list[str] = []
    led_index = 0
    for agent, block_count in _spatial_split_blocks(visible, count):
        ambient = _ambient_level_for_agent(agent, settings)
        color = _peak_color_for_agent_with_alert(agent, settings)
        for _ in range(block_count):
            approach.append(f"{led_index}:{ambient} {_STATIC_SETTLE_MS}ms cosine")
            hold.append(f"{led_index}:{color} {_STATIC_SETTLE_MS}ms cosine")
            led_index += 1
    if approach == hold:
        return apply_brightness("; ".join(hold), brightness)
    return apply_brightness("; ".join(approach) + "\n" + "; ".join(hold), brightness)


def _attention_motion_programs(
    program: str,
    agents: list[_ActiveAgent],
    *,
    led_count: int,
    settings: ColorSettings,
    brightness: float,
) -> AttentionMotionPrograms:
    if not settings.round_robin_urgency_alert:
        return AttentionMotionPrograms(program, program)
    if not any(agent.state == LedDisplayState.ASK for agent in agents):
        return AttentionMotionPrograms(program, program)

    base_program = _attention_spatial_anchor_program(
        agents,
        led_count=led_count,
        settings=settings,
        brightness=brightness,
    )
    return compose_attention_arrival(
        base_program,
        attention_color=settings.mode_color(MODE_ASK),
        brightness=brightness,
    )


def _ambient_level_for_agent(agent: _ActiveAgent, settings: ColorSettings) -> str:
    """Where this LED already sits under the ordinary gentleness ceiling --
    the level an urgency takeover rises FROM, with no urgency lift applied."""
    color = _display_color_for_agent(agent, settings)
    if _is_static_agent_state(agent.state):
        return color
    _floor, ceiling = settings.fade_range(_STATE_TO_MODE_KEY[agent.state])
    return color if ceiling >= 1.0 else scale_hex_brightness(color, ceiling)


def _peak_color_for_agent(agent: _ActiveAgent, settings: ColorSettings) -> str:
    return _peak_for_state(agent.color, agent.state, settings)


def _peak_color_for_agent_with_alert(agent: _ActiveAgent, settings: ColorSettings) -> str:
    """Like _peak_color_for_agent, but applies the Round-Robin/Cycle urgency
    alert color swap first -- used by preview_led_colors() for those two
    modes so the static preview matches what program_for_snapshot() would
    actually render."""
    return _peak_for_state(_display_color_for_agent(agent, settings), agent.state, settings)


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


def _oklch_to_linear_srgb(lightness: float, chroma: float, hue_degrees: float):
    hue = math.radians(hue_degrees)
    a = chroma * math.cos(hue)
    b = chroma * math.sin(hue)
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_**3, m_**3, s_**3
    return (
        +4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3,
        -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3,
        -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3,
    )


def oklch_hex(lightness: float, chroma: float, hue_degrees: float) -> str:
    """sRGB hex for an OKLCH color, walking chroma down until the color
    fits the gamut -- out-of-gamut requests desaturate, never clip to a
    different hue."""

    def _encode(channel: float) -> int:
        channel = max(0.0, min(1.0, channel))
        if channel <= 0.0031308:
            value = channel * 12.92
        else:
            value = 1.055 * (channel ** (1 / 2.4)) - 0.055
        return round(value * 255)

    current = max(0.0, chroma)
    for _ in range(24):
        red, green, blue = _oklch_to_linear_srgb(lightness, current, hue_degrees)
        if -0.0005 <= min(red, green, blue) and max(red, green, blue) <= 1.0005:
            return f"#{_encode(red):02X}{_encode(green):02X}{_encode(blue):02X}"
        current *= 0.86
    red, green, blue = _oklch_to_linear_srgb(lightness, 0.0, hue_degrees)
    return f"#{_encode(red):02X}{_encode(green):02X}{_encode(blue):02X}"


def _hex_to_hls_hue(hex_value: str) -> float:
    """HLS wheel hue (NOT OKLCH -- see hex_to_oklch): cheap and only ever
    used to rank hue gaps and fan palette slots around the wheel."""
    import colorsys

    red, green, blue = (int(hex_value[i : i + 2], 16) / 255.0 for i in (1, 3, 5))
    hue, _light, _sat = colorsys.rgb_to_hls(red, green, blue)
    return (hue * 360.0) % 360.0


def hex_to_oklch(hex_value: str) -> tuple[float, float, float]:
    """The exact inverse of ``oklch_hex``'s forward transform.

    Needed to shade a colour without leaving it: two sessions of the same
    provider are told apart by LIGHTNESS, which means reading the brand's
    real lightness first rather than guessing one.
    """
    import math

    def _decode(channel: float) -> float:
        if channel <= 0.04045:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    normalized = normalize_hex(hex_value, "#000000")
    red, green, blue = (
        _decode(int(normalized[index : index + 2], 16) / 255.0) for index in (1, 3, 5)
    )
    long_ = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
    medium = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
    short = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue
    l_ = long_ ** (1 / 3) if long_ >= 0 else -((-long_) ** (1 / 3))
    m_ = medium ** (1 / 3) if medium >= 0 else -((-medium) ** (1 / 3))
    s_ = short ** (1 / 3) if short >= 0 else -((-short) ** (1 / 3))
    lightness = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return (
        lightness,
        math.hypot(a, b),
        math.degrees(math.atan2(b, a)) % 360.0,
    )


#: Perceptual lightness offsets that tell two sessions of the SAME
#: provider apart WITHOUT leaving that provider's hue. Offset 0 is the
#: brand colour itself, so a lone session of a provider always wears the
#: exact declared brand hex.
IDENTITY_SHADE_OFFSETS: tuple[float, ...] = (0.0, 0.17, -0.16, 0.32, -0.28, 0.46)


def shade_of(hex_value: str, lightness_offset: float) -> str:
    """The same hue and chroma at a different perceptual lightness."""
    lightness, chroma, hue = hex_to_oklch(hex_value)
    shifted = min(0.97, max(0.16, lightness + lightness_offset))
    return oklch_hex(shifted, chroma, hue)


def provider_identity_colors_for_agents(
    agents,
    *,
    colors: ColorSettings,
) -> dict[str, str]:
    """Per-session identity that still says WHICH PRODUCT is working.

    ``agents`` is an iterable of ``(agent_id, provider)``.

    The old identity assignment hashed each agent_id into
    IDENTITY_PALETTE -- eight hues chosen for mutual distinctness and
    nothing else. That is why the owner reported "it's purple for some
    reason when Claude's running": a live snapshot put Claude main
    sessions on #E44CFF, #4C8DFF, #FFD60A, #FF6FA9, #00B3A4, #E6E6E6 and
    #FF9F0A, and Claude's declared brand terracotta #D97757 appeared
    nowhere on the strip at all. The provider colour was reachable ONLY
    when exactly one row was visible, and 87 leaked sub-agents meant that
    never happened.

    Identity and brand are not in conflict: hue answers "which product",
    lightness answers "which of my Claude sessions". Sorted-order
    assignment inside each provider keeps the result a pure function of
    the id SET, exactly as ``identity_colors_for_agents`` documents for
    its own palette.
    """
    by_provider: dict[str, list[str]] = {}
    for agent_id, provider in agents:
        by_provider.setdefault(provider, []).append(agent_id)
    assignment: dict[str, str] = {}
    for provider, agent_ids in by_provider.items():
        base = colors.agent_color(provider)
        for index, agent_id in enumerate(sorted(set(agent_ids))):
            offset = IDENTITY_SHADE_OFFSETS[index % len(IDENTITY_SHADE_OFFSETS)]
            assignment[agent_id] = base if offset == 0.0 else shade_of(base, offset)
    return assignment


def derive_palette(accent_hex: str) -> dict[str, dict[str, str]]:
    """A full mode+agent color set from ONE accent: working takes the
    accent hue at a vivid weight, done sits opposite-ish so "finished"
    can never be confused with "busy", ask leans warm (attention), and
    idle is the accent at whisper chroma. Agents fan out around the
    wheel from the accent so a crowd stays tellable-apart."""
    hue = _hex_to_hls_hue(normalize_hex(accent_hex, "#00E5FF"))
    return {
        "modes": {
            "working": oklch_hex(0.72, 0.16, hue),
            "done": oklch_hex(0.78, 0.15, (hue + 140.0) % 360.0),
            "ask": oklch_hex(0.74, 0.17, (hue + 300.0) % 360.0),
            "idle": oklch_hex(0.55, 0.035, hue),
        },
        "agents": {
            # A palette owns exactly the providers with no declared brand
            # hue (apply_palette skips the branded ones anyway); fanning
            # branded names -- or a "gemini" that is not a provider at
            # all -- wrote junk entries and recolored nothing. Sorted so
            # the assignment never depends on registry order.
            provider: oklch_hex(
                0.72,
                0.15,
                (hue + index * (360.0 / max(1, _brandless_count()))) % 360.0,
            )
            for index, provider in enumerate(_brandless_providers())
        },
    }


def _brandless_providers() -> tuple[str, ...]:
    return tuple(
        sorted(
            spec.provider
            for spec in PROVIDER_SPECS
            if spec.provider not in PROVIDER_BRAND_COLORS
        )
    )


def _brandless_count() -> int:
    return len(_brandless_providers())


# One-click looks, each derived through the same engine so every set is
# gamut-safe, then seeded with a deliberately different personality.
# Seeds live in their own table because the palette-damage repair needs
# them to reconstruct what OLDER fan-outs once wrote.
CURATED_PALETTE_SEEDS: tuple[tuple[str, str], ...] = (
    ("Neon", "#00E5FF"),
    ("Sunset", "#FF6A3D"),
    ("Forest", "#2FBF71"),
    ("Orchid", "#B26EFF"),
    ("Ember", "#FF9F0A"),
)
CURATED_PALETTES: dict[str, dict[str, dict[str, str]]] = {
    name: derive_palette(seed) for name, seed in CURATED_PALETTE_SEEDS
}

# The four official brand colours, in one place, as (name, hex) -- the
# single source of truth for BOTH the brand-seeded palettes below and the
# named "Brand" swatch group every provider row leads with. They used to be
# restated as an anonymous literal in settings_window.BRAND_SWATCHES, which
# is how the Agent Colors card came to claim "the first four swatches are
# the brand colours" while actually rendering systemRed/Blue/Green/Purple.
#
# Where a brand is ALSO a provider this app ships a row for, the hex is read
# out of PROVIDER_BRAND_COLORS rather than restated -- one table cannot then
# disagree with the other. It did: this tuple named Codex #FF3A00 while
# PROVIDER_BRAND_COLORS gave codex #2B8FFF, so the Codex row drew two chips
# both claiming Codex, and #FF3A00 -- which is this app's own ask/blocked
# signal colour (led_status.ASK_AMBER) -- was globally named "Codex" and
# reported by is_brand_color() as a brand. Clicking the chip captioned
# "Codex" painted Codex the alert red.
BRAND_SEED_COLORS: tuple[tuple[str, str], ...] = (
    ("Claude", PROVIDER_BRAND_COLORS["claude"]),
    ("OpenAI", "#10A37F"),
    ("Codex", PROVIDER_BRAND_COLORS["codex"]),
    ("Gemini", "#4796E3"),
)

# The app's own signal colours, named. These are not brands and not palette
# hues -- they are what SidePulse ships for each state -- but they are the
# colours a State Colors row is actually wearing out of the box, so they need
# words too. Without them every State Colors row rendered with no selected
# chip at all on a fresh install and the row's only truth was its hex text.
STATE_SEED_COLORS: tuple[tuple[str, str], ...] = (
    ("Working", WORKING_CYAN),
    ("Done", DONE_GREEN),
    ("Ask", ASK_AMBER),
    ("Idle", IDLE_DIM),
)

# Brand-seeded looks: one per provider, mix-and-match with anything --
# apply "Claude" then hand-pick a different done color, etc.
PROVIDER_PALETTES: dict[str, dict[str, dict[str, str]]] = {
    name: derive_palette(seed) for name, seed in BRAND_SEED_COLORS
}


# --- Colour / Animation Studio model ---------------------------------------
#
# Everything the Studio pane draws is decided HERE, in plain data, and the
# AppKit code in settings_window.py is a renderer over it. That split exists
# because the bug this replaced was a MODEL bug wearing a view costume: the
# Agent Colors card told the user "the first four swatches are the brand
# colours" while the strip it actually drew was CURATED_PALETTE, whose first
# four are red/blue/green/purple. A sentence of body copy and a hover tooltip
# were carrying the entire mapping between a colour square and its meaning,
# and neither was testable. Now the mapping is a value: every swatch arrives
# with a name, a group, and whether it is a brand colour, and a test can
# assert all three without instantiating a single NSView.

SWATCH_GROUP_BRAND = "brand"
SWATCH_GROUP_PALETTE = "palette"
SWATCH_GROUP_CUSTOM = "custom"
# What SidePulse itself ships for this row -- a state's signal colour. Not a
# brand (nobody outside this app owns it) and not a palette hue, but it is
# the colour the row wears out of the box, so it needs its own named group
# or the row starts life with nothing selected.
SWATCH_GROUP_DEFAULT = "default"
SWATCH_GROUP_CHOICES: tuple[str, ...] = (
    SWATCH_GROUP_DEFAULT,
    SWATCH_GROUP_BRAND,
    SWATCH_GROUP_PALETTE,
    SWATCH_GROUP_CUSTOM,
)
SWATCH_GROUP_LABELS: dict[str, str] = {
    SWATCH_GROUP_DEFAULT: "Default",
    SWATCH_GROUP_BRAND: "Brand",
    SWATCH_GROUP_PALETTE: "Palette",
    SWATCH_GROUP_CUSTOM: "Custom",
}
SWATCH_GROUP_HINTS: dict[str, str] = {
    SWATCH_GROUP_DEFAULT: "What SidePulse ships for this one.",
    SWATCH_GROUP_BRAND: "Official colors, straight from the makers.",
    SWATCH_GROUP_PALETTE: "System hues, chosen to stay far apart from each other.",
    SWATCH_GROUP_CUSTOM: "Anything else you pick.",
}

# The name shown when a hex belongs to no named set. Kept as a constant so
# the "is this swatch anonymous?" test has something to compare against.
CUSTOM_SWATCH_NAME = "Custom"

_BRAND_NAME_BY_HEX: dict[str, str] = {
    hex_value.upper(): name for name, hex_value in BRAND_SEED_COLORS
}
_CURATED_NAME_BY_HEX: dict[str, str] = {
    hex_value.upper(): name
    for hex_value, name in zip(CURATED_PALETTE, CURATED_PALETTE_NAMES)
}
_IDENTITY_NAME_BY_HEX: dict[str, str] = {
    hex_value.upper(): name
    for hex_value, name in zip(IDENTITY_PALETTE, IDENTITY_PALETTE_NAMES)
}
_STATE_NAME_BY_HEX: dict[str, str] = {
    hex_value.upper(): name for name, hex_value in STATE_SEED_COLORS
}


def swatch_name(hex_value: str) -> str:
    """The human name for a colour: its brand name if it is one, then its
    palette name, then its identity name, then this app's own signal name,
    then "Custom".

    Brand wins ties on purpose -- if a system hue ever collided with a brand
    hex, the brand meaning is the one a person is actually choosing. The
    signal names come last for the same reason they exist at all: they are a
    fallback so that this app's own shipped colours are never anonymous, not
    a claim on a hex anyone else has a better name for.
    """
    key = normalize_hex(hex_value, "#000000").upper()
    return (
        _BRAND_NAME_BY_HEX.get(key)
        or _CURATED_NAME_BY_HEX.get(key)
        or _IDENTITY_NAME_BY_HEX.get(key)
        or _STATE_NAME_BY_HEX.get(key)
        or CUSTOM_SWATCH_NAME
    )


@dataclass(frozen=True)
class Swatch:
    """One pickable colour, and everything the view needs to draw it."""

    name: str
    hex: str
    group: str
    selected: bool = False
    # True for the "Custom..." opener, which has no colour of its own until
    # the picker returns one.
    opens_picker: bool = False

    @property
    def is_brand(self) -> bool:
        return self.group == SWATCH_GROUP_BRAND

    @property
    def is_control(self) -> bool:
        """True when this chip is a BUTTON rather than a colour on offer.

        The picker wears the row's current colour only while that colour is
        its own; the rest of the time it must render neutral, or the row
        shows the same colour twice and reads as a duplicate.
        """
        return self.opens_picker and not self.selected

    @property
    def tooltip(self) -> str:
        if self.opens_picker:
            if self.selected:
                return f"{self.name} · {self.hex} — click to pick another"
            return "Pick any color…"
        if self.is_brand:
            return f"{self.name} brand color · {self.hex}"
        return f"{self.name} · {self.hex}"


@dataclass(frozen=True)
class SwatchGroup:
    """A LABELLED run of swatches. The label is not decoration: it is the
    only thing that tells a person the four colours in front of them are
    brands rather than an arbitrary strip."""

    key: str
    label: str
    hint: str
    swatches: tuple[Swatch, ...]


@dataclass(frozen=True)
class SwatchRow:
    """The shared shape of every row the Studio draws: what it is called,
    what colour it is wearing, what that colour is NAMED, and the labelled
    groups of chips on offer.

    Provider rows and state rows are the same object with different group
    lists on purpose -- the view is one renderer, and every string it can
    show is decided here where a test can read it without an NSView.
    """

    key: str
    label: str
    current_hex: str
    current_name: str
    groups: tuple[SwatchGroup, ...]

    @property
    def all_swatches(self) -> tuple[Swatch, ...]:
        return tuple(swatch for group in self.groups for swatch in group.swatches)

    def group(self, key: str) -> SwatchGroup | None:
        for group in self.groups:
            if group.key == key:
                return group
        return None

    @property
    def picker_swatch(self) -> Swatch:
        """The one chip that opens the colour panel. Always present."""
        group = self.group(SWATCH_GROUP_CUSTOM)
        return group.swatches[0]


@dataclass(frozen=True)
class ProviderColorRow(SwatchRow):
    """One provider's whole row: identity first, palette second."""

    provider: str = ""
    animation: str = PROVIDER_ANIMATION_AUTO
    animation_label: str = ""
    animation_description: str = ""


def _provider_label(provider: str) -> str:
    for spec in PROVIDER_SPECS:
        if spec.provider == provider:
            return spec.label
    return provider.title() or provider


def brand_swatches_for_provider(provider: str, current_hex: str) -> tuple[Swatch, ...]:
    """The named Brand group for one provider.

    Leads with that provider's OWN shipped colour when it is not already one
    of the four brand seeds and not in the system palette either (Devin's
    navy, say) -- identity first. Named "Default", never a second swatch
    confusingly wearing the provider's own name next to a different hex.
    """
    swatches: list[Swatch] = []
    own = normalize_hex(default_agent_color(provider), CURATED_PALETTE[0])
    known = set(_BRAND_NAME_BY_HEX) | set(_CURATED_NAME_BY_HEX)
    if own.upper() not in known:
        swatches.append(
            Swatch(
                name="Default",
                hex=own,
                group=SWATCH_GROUP_BRAND,
                selected=own.upper() == current_hex.upper(),
            )
        )
    for name, hex_value in BRAND_SEED_COLORS:
        swatches.append(
            Swatch(
                name=name,
                hex=hex_value,
                group=SWATCH_GROUP_BRAND,
                selected=hex_value.upper() == current_hex.upper(),
            )
        )
    return tuple(swatches)


def palette_swatches(current_hex: str) -> tuple[Swatch, ...]:
    return tuple(
        Swatch(
            name=name,
            hex=hex_value,
            group=SWATCH_GROUP_PALETTE,
            selected=hex_value.upper() == current_hex.upper(),
        )
        for hex_value, name in zip(CURATED_PALETTE, CURATED_PALETTE_NAMES)
    )


def custom_swatches(
    current_hex: str, already_shown: tuple[str, ...] = ()
) -> tuple[Swatch, ...]:
    """The row's own-colour slot: ONE chip that opens the picker, wearing
    whatever colour the row currently has and named for it.

    When the current colour belongs to no named group this chip *is* the
    selection, so it is named "Custom" and rings -- a hand-picked hex stays
    a visible, named, re-selectable swatch instead of vanishing from the
    row. Otherwise it is the neutral "Pick…" opener.

    ``already_shown`` are hexes the earlier groups drew: a colour that is
    already named on the row (a provider's own "Default", say) is not
    custom, whatever the global name table says about it.
    """
    current = normalize_hex(current_hex, CURATED_PALETTE[0])
    shown = {normalize_hex(value, "#000000").upper() for value in already_shown}
    unnamed = swatch_name(current) == CUSTOM_SWATCH_NAME and current.upper() not in shown
    return (
        Swatch(
            name=CUSTOM_SWATCH_NAME if unnamed else "Pick…",
            hex=current,
            group=SWATCH_GROUP_CUSTOM,
            selected=unnamed,
            opens_picker=True,
        ),
    )


def _make_group(key: str, swatches: tuple[Swatch, ...]) -> SwatchGroup:
    return SwatchGroup(
        key=key,
        label=SWATCH_GROUP_LABELS[key],
        hint=SWATCH_GROUP_HINTS[key],
        swatches=swatches,
    )


def _selected_name(groups: tuple[SwatchGroup, ...], current: str) -> str:
    """The name of the chip that is actually ringed.

    A row sitting on its own shipped colour reads "Default", not the global
    fallback name for an unrecognized hex -- the caption has to agree with
    the ring, or the row is telling you two different things at once.
    """
    return next(
        (
            swatch.name
            for group in groups
            for swatch in group.swatches
            if swatch.selected and not swatch.opens_picker
        ),
        None,
    ) or swatch_name(current)


def provider_color_row(provider: str, colors: ColorSettings) -> ProviderColorRow:
    current = colors.agent_color(provider)
    animation = colors.agent_animation(provider)
    brand = brand_swatches_for_provider(provider, current)
    palette = palette_swatches(current)
    custom = custom_swatches(
        current, tuple(swatch.hex for swatch in brand + palette)
    )
    groups = (
        _make_group(SWATCH_GROUP_BRAND, brand),
        _make_group(SWATCH_GROUP_PALETTE, palette),
        _make_group(SWATCH_GROUP_CUSTOM, custom),
    )
    return ProviderColorRow(
        key=provider,
        provider=provider,
        label=_provider_label(provider),
        current_hex=current,
        current_name=_selected_name(groups, current),
        animation=animation,
        animation_label=PROVIDER_ANIMATION_LABELS.get(animation, animation.title()),
        animation_description=PROVIDER_ANIMATION_DESCRIPTIONS.get(animation, ""),
        groups=groups,
    )


# The words the State Colors rows lead with. Kept here beside the model
# rather than in the view, so a test can assert the row is named without
# building an NSView.
MODE_ROW_LABELS: dict[str, str] = {
    MODE_IDLE: "Idle",
    MODE_WORKING: "Working",
    MODE_DONE: "Done",
    MODE_ASK: "Ask (waiting / blocked)",
}


def default_mode_color(key: str) -> str:
    return _default_mode_colors().get(key, WORKING_CYAN)


def mode_color_row(key: str, colors: ColorSettings) -> SwatchRow:
    """One state's colour row, built exactly like a provider's.

    The shipped signal colour leads the row as a named "Default" chip. Before
    that existed the State Colors rows drew CURATED_PALETTE[:6] and nothing
    else -- and not one of the four shipped state colours is in that strip,
    so on a fresh install every state row rendered with ZERO chips ringed and
    the row's only truth was the hex printed at its end. The picker chip was
    hardcoded "Pick…" with ``selected`` never set, so unlike a provider row a
    hand-picked colour could not become a named, ringed "Custom" either.
    """
    current = normalize_hex(colors.mode_color(key), CURATED_PALETTE[0])
    shipped = normalize_hex(default_mode_color(key), CURATED_PALETTE[0])
    default = (
        Swatch(
            name=swatch_name(shipped),
            hex=shipped,
            group=SWATCH_GROUP_DEFAULT,
            selected=shipped.upper() == current.upper(),
        ),
    )
    palette = palette_swatches(current)
    custom = custom_swatches(
        current, tuple(swatch.hex for swatch in default + palette)
    )
    groups = (
        _make_group(SWATCH_GROUP_DEFAULT, default),
        _make_group(SWATCH_GROUP_PALETTE, palette),
        _make_group(SWATCH_GROUP_CUSTOM, custom),
    )
    return SwatchRow(
        key=key,
        label=MODE_ROW_LABELS.get(key, key.title()),
        current_hex=current,
        current_name=_selected_name(groups, current),
        groups=groups,
    )


def mode_color_rows(colors: ColorSettings) -> tuple[SwatchRow, ...]:
    return tuple(mode_color_row(key, colors) for key in MODE_COLOR_KEYS)


def provider_preview_statuses(
    provider: str, mode: AgentMode = AgentMode.WORKING
) -> tuple[AgentStatus, ...]:
    """One synthetic session for one provider.

    What a person hovering Claude's swatch is asking is "what does THIS
    colour look like", and the honest answer needs Claude alone on the strip:
    with two or more sessions active the renderer switches to session
    identity colours, so a provider colour change would preview as no change
    at all. Editing a colour is the one moment the strip should be about the
    provider rather than about the crowd.
    """
    return (
        AgentStatus(
            provider=provider,
            agent_id=f"{provider}:studio-preview",
            display_name=_provider_label(provider),
            mode=mode,
            updated_at=datetime.now(timezone.utc),
            event_name="Studio preview",
        ),
    )


def provider_color_rows(colors: ColorSettings) -> tuple[ProviderColorRow, ...]:
    """One row per registered provider, in PROVIDER_SPECS order -- the same
    order the renderer assigns LEDs in, so the window and the strip agree
    about who comes first."""
    return tuple(provider_color_row(spec.provider, colors) for spec in PROVIDER_SPECS)


# --- Studio sections -------------------------------------------------------

STUDIO_SECTION_COLORS = "colors"
STUDIO_SECTION_ANIMATIONS = "animations"
STUDIO_SECTION_PREVIEW = "preview"
STUDIO_SECTION_CHOICES: tuple[str, ...] = (
    STUDIO_SECTION_COLORS,
    STUDIO_SECTION_ANIMATIONS,
    STUDIO_SECTION_PREVIEW,
)
STUDIO_SECTION_LABELS: dict[str, str] = {
    STUDIO_SECTION_COLORS: "Colors",
    STUDIO_SECTION_ANIMATIONS: "Animations",
    STUDIO_SECTION_PREVIEW: "Preview",
}
STUDIO_SECTION_SUBTITLES: dict[str, str] = {
    STUDIO_SECTION_COLORS: "Which color each agent and each state gets.",
    STUDIO_SECTION_ANIMATIONS: "How each agent and each state moves.",
    STUDIO_SECTION_PREVIEW: "See it before you keep it.",
}
DEFAULT_STUDIO_SECTION = STUDIO_SECTION_COLORS


def normalize_studio_section(value: object) -> str:
    return value if value in STUDIO_SECTION_CHOICES else DEFAULT_STUDIO_SECTION


# --- Uncommitted preview ---------------------------------------------------


@dataclass
class StudioPreviewSession:
    """The "try it before you keep it" rule, as a value.

    Hovering a swatch must show you the light and then take it back; only a
    click keeps it. Doing that with a saved-settings round trip is how a
    half-finished hover ends up persisted, so the candidate lives here and
    NOTHING writes it: ``effective`` is what to render, ``committed`` is what
    to save, and the only way the two converge is ``commit``.
    """

    committed: ColorSettings
    candidate: ColorSettings | None = None

    @property
    def previewing(self) -> bool:
        return self.candidate is not None

    @property
    def effective(self) -> ColorSettings:
        return self.candidate if self.candidate is not None else self.committed

    def preview(self, colors: ColorSettings) -> ColorSettings:
        self.candidate = colors
        return self.candidate

    def preview_agent_color(self, provider: str, hex_value: str) -> ColorSettings:
        return self.preview(self.committed.with_agent_color(provider, hex_value))

    def preview_agent_animation(self, provider: str, motion: str) -> ColorSettings:
        return self.preview(self.committed.with_agent_animation(provider, motion))

    def revert(self) -> ColorSettings:
        """Pointer left without clicking: the candidate never existed."""
        self.candidate = None
        return self.committed

    def commit(self, colors: ColorSettings | None = None) -> ColorSettings:
        """Keep it. With no argument, keeps whatever is being previewed."""
        if colors is None:
            colors = self.effective
        self.committed = colors
        self.candidate = None
        return self.committed

    def rebase(self, colors: ColorSettings) -> ColorSettings:
        """The saved settings changed somewhere else (a palette button, a
        reset). Drop any in-flight hover rather than letting it repaint over
        the new baseline."""
        self.committed = colors
        self.candidate = None
        return self.committed


def studio_preview_program(
    colors: ColorSettings,
    *,
    statuses: tuple[AgentStatus, ...] = (),
    scenario: str = PREVIEW_SCENARIO_LIVE,
    led_count: int = 8,
    brightness: float = 1.0,
) -> str:
    """The LED program a candidate ColorSettings would produce -- what the
    Screen Bar shows while you hover. Live scenario with nothing running
    falls back to the demo roster so a hover is never a blank bar."""
    if scenario != PREVIEW_SCENARIO_LIVE:
        statuses = preview_statuses_for_scenario(scenario)
    elif not statuses:
        statuses = demo_statuses_for_preview()
    _state, program = program_for_snapshot(
        statuses,
        led_count=led_count,
        colors=colors,
        brightness=brightness,
    )
    return program
