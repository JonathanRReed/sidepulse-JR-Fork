"""The Signal Engine's data model: what may claim the light, and how it
looks while it does.

Everything that lights the bar -- low battery, notification blinks, the
calendar/reminders glows, and (via its own richer renderer) agent
status -- is a *signal* with a `SignalStyle`: color, pattern, speed,
intensity. One renderer (`led_status.style_to_program`) turns any style
into the device DSL, one precedence order decides which signal wins,
and the Signals pane's style cards edit these values with live
previews. See docs/superpowers/specs/2026-08-11-signal-engine-design.md.

The DEFAULT_SIGNAL_STYLES here are chosen so that rendering them
reproduces the pre-engine bespoke programs BYTE-FOR-BYTE -- the
migration is invisible, and a snapshot test holds it that way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

PATTERN_BREATHE = "breathe"
PATTERN_BLINK = "blink"
PATTERN_DOUBLE_BLINK = "double-blink"
PATTERN_SOLID = "solid"
PATTERN_SWEEP = "sweep"
PATTERN_RIPPLE = "ripple"
PATTERN_COMET = "comet"
PATTERN_SPARKLE = "sparkle"
PATTERN_HEARTBEAT = "heartbeat"
SIGNAL_PATTERNS = (
    PATTERN_BREATHE,
    PATTERN_BLINK,
    PATTERN_DOUBLE_BLINK,
    PATTERN_SOLID,
    PATTERN_SWEEP,
    PATTERN_RIPPLE,
    PATTERN_COMET,
    PATTERN_SPARKLE,
    PATTERN_HEARTBEAT,
)
# Patterns that play once and end (the rest loop with `repeat`).
ONE_SHOT_PATTERNS = (PATTERN_BLINK, PATTERN_DOUBLE_BLINK)
# Signals whose claim HOLDS while a condition lasts (vs. moment
# signals that fire and expire). A one-shot pattern on one of these
# would flash three times and leave the bar dark for the rest of a
# multi-hour condition -- settings.signal_style falls such choices
# back to breathe, and the style cards don't offer them.
CONTINUOUS_SIGNALS = ("low_battery", "calendar", "weather")

MIN_SPEED_SECONDS = 0.1
MAX_SPEED_SECONDS = 10.0
MIN_INTENSITY = 0.05
MAX_INTENSITY = 1.0

# Signal keys, in precedence order (first active claim wins). "agent"
# is the default and always active; battery sits just above it as a
# data display rather than a styled signal.
SIGNAL_LOW_BATTERY = "low_battery"
SIGNAL_NOTIFICATION = "notification"
SIGNAL_REMINDERS = "reminders"
SIGNAL_CALENDAR = "calendar"
SIGNAL_WEATHER = "weather"
SIGNAL_QUOTA = "quota"
SIGNAL_COMPLETION = "completion"

_HEX_RE = re.compile(r"#?[0-9a-fA-F]{6}")


def _normalize_color(raw: object, fallback: str) -> str:
    if isinstance(raw, str) and _HEX_RE.fullmatch(raw.strip()):
        return "#" + raw.strip().lstrip("#").upper()
    return fallback


@dataclass(frozen=True)
class SignalStyle:
    color: str
    pattern: str
    speed_seconds: float
    intensity: float
    finite_repetitions: int | None = None

    def normalized(self) -> SignalStyle:
        return replace(
            self,
            color=_normalize_color(self.color, "#FFFFFF"),
            pattern=self.pattern if self.pattern in SIGNAL_PATTERNS else PATTERN_BREATHE,
            speed_seconds=max(MIN_SPEED_SECONDS, min(MAX_SPEED_SECONDS, float(self.speed_seconds))),
            intensity=max(MIN_INTENSITY, min(MAX_INTENSITY, float(self.intensity))),
            finite_repetitions=(
                self.finite_repetitions
                if type(self.finite_repetitions) is int
                and 1 <= self.finite_repetitions <= 2
                else None
            ),
        )

    def to_dict(self) -> dict:
        return {
            "color": self.color,
            "pattern": self.pattern,
            "speed_seconds": self.speed_seconds,
            "intensity": self.intensity,
        }

    @classmethod
    def from_dict(cls, raw: object, fallback: SignalStyle) -> SignalStyle:
        if not isinstance(raw, dict):
            return fallback
        try:
            return cls(
                # An invalid color falls back to the SIGNAL's default,
                # not normalized()'s generic white -- {"color": "red"}
                # must yield the documented default, not a white glow.
                color=_normalize_color(raw.get("color", fallback.color), fallback.color),
                pattern=str(raw.get("pattern", fallback.pattern)),
                speed_seconds=float(raw.get("speed_seconds", fallback.speed_seconds)),
                intensity=float(raw.get("intensity", fallback.intensity)),
                finite_repetitions=fallback.finite_repetitions,
            ).normalized()
        except (TypeError, ValueError):
            return fallback


# Byte-identical to the pre-engine bespoke programs (snapshot-tested):
#   low_battery: "off 400ms cosine\n#E01010 3600ms pulse\nrepeat"
#   calendar:    "off 400ms cosine\n#A45CFF 2600ms pulse\nrepeat"
#   notification (blink, 0.3s cycle): 170ms flash / 130ms gap x3
DEFAULT_SIGNAL_STYLES: dict[str, SignalStyle] = {
    SIGNAL_LOW_BATTERY: SignalStyle("#E01010", PATTERN_BREATHE, 3.6, 1.0),
    SIGNAL_NOTIFICATION: SignalStyle("#34C759", PATTERN_BLINK, 0.3, 1.0),
    SIGNAL_REMINDERS: SignalStyle("#FFB340", PATTERN_BREATHE, 1.5, 1.0),
    SIGNAL_CALENDAR: SignalStyle("#A45CFF", PATTERN_BREATHE, 2.6, 1.0),
    # Emergency weather: an urgent heartbeat in warning pink-red --
    # unmistakable, and unlike any agent state's rhythm or hue.
    SIGNAL_WEATHER: SignalStyle("#FF2D55", PATTERN_HEARTBEAT, 1.4, 1.0),
    # Completion sweep: ANY agent finishing claims the bar briefly, in
    # that agent's identity color when several are running -- without
    # this, a completion was invisible whenever another agent's
    # WORKING state outranked it in the aggregate.
    SIGNAL_COMPLETION: SignalStyle(
        "#00FF66",
        PATTERN_SWEEP,
        0.8,
        1.0,
        finite_repetitions=2,
    ),
    # Quota threshold crossed: a double-tap in caution amber (or the
    # provider's own color at fire time) -- a nudge, not an alarm.
    SIGNAL_QUOTA: SignalStyle("#FFB020", PATTERN_DOUBLE_BLINK, 0.9, 1.0),
}


# --- Ask escalation ---------------------------------------------------
# How loud "an agent needs you" may get as it's ignored. Stages:
#   0  fresh ask, normal rendering
#   1  ramp: brightness boost on the light surfaces
#   2  + menu-bar icon flash (catches full-screen apps)
#   3  + the user's opt-in finale: a single chime, or a full takeover
# The tier is the user's chosen CEILING; stages above it never fire.
ESCALATION_TIER_LIGHT = "light"
ESCALATION_TIER_MENU_BAR = "menu_bar"
ESCALATION_TIER_CHIME = "chime"
ESCALATION_TIER_TAKEOVER = "takeover"
ESCALATION_TIERS = (
    ESCALATION_TIER_LIGHT,
    ESCALATION_TIER_MENU_BAR,
    ESCALATION_TIER_CHIME,
    ESCALATION_TIER_TAKEOVER,
)
_TIER_CEILING = {
    ESCALATION_TIER_LIGHT: 1,
    ESCALATION_TIER_MENU_BAR: 2,
    ESCALATION_TIER_CHIME: 3,
    ESCALATION_TIER_TAKEOVER: 3,
}
ESCALATION_RAMP_BRIGHTNESS_BOOST = 1.15


def escalation_stage(
    blocked_elapsed_seconds: float | None,
    *,
    ramp_seconds: float,
    menu_bar_seconds: float,
    final_seconds: float,
    tier: str,
) -> int:
    """Pure: how escalated an ignored ask currently is (0-3)."""
    if blocked_elapsed_seconds is None or blocked_elapsed_seconds < ramp_seconds:
        return 0
    stage = 1
    if blocked_elapsed_seconds >= menu_bar_seconds:
        stage = 2
    if blocked_elapsed_seconds >= final_seconds:
        stage = 3
    return min(stage, _TIER_CEILING.get(tier, 2))


def quota_crossings(
    previous: dict[str, float],
    current: dict[str, float],
    thresholds: tuple[float, ...],
) -> list[tuple[str, float]]:
    """(window key, threshold) pairs newly crossed UPWARD since the last
    observation. A key seen for the first time never fires -- the T3
    rule: transitions alert, repaints and restarts never do."""
    fired: list[tuple[str, float]] = []
    for key, percent in current.items():
        prior = previous.get(key)
        if prior is None:
            continue
        for threshold in thresholds:
            if prior < threshold <= percent:
                fired.append((key, float(threshold)))
    return fired


def quota_resets(
    previous: dict[str, float],
    current: dict[str, float],
) -> list[str]:
    """Window keys whose usage just RESET: a large downward transition
    (>=50% before, near zero now). The mirror image of quota_crossings,
    same first-observation silence -- restarts never celebrate."""
    reset = []
    for key, percent in current.items():
        prior = previous.get(key)
        if prior is None:
            continue
        if prior >= 50.0 and percent <= 10.0:
            reset.append(key)
    return reset


def signal_hold_seconds(style: SignalStyle) -> float:
    """How long a MOMENT signal (notification/reminders) claims the bar
    for one firing: the pattern's full play time plus a settle beat."""
    style = style.normalized()
    if style.pattern == PATTERN_BLINK:
        return 3.0 * style.speed_seconds + 0.4
    if style.pattern == PATTERN_DOUBLE_BLINK:
        return 2.0 * style.speed_seconds + 0.4
    # Continuous patterns hold for a few breaths.
    return max(2.0, 2.0 * style.speed_seconds) + 0.4
