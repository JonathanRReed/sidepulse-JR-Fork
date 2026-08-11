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
SIGNAL_PATTERNS = (
    PATTERN_BREATHE,
    PATTERN_BLINK,
    PATTERN_DOUBLE_BLINK,
    PATTERN_SOLID,
    PATTERN_SWEEP,
)

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

    def normalized(self) -> "SignalStyle":
        return replace(
            self,
            color=_normalize_color(self.color, "#FFFFFF"),
            pattern=self.pattern if self.pattern in SIGNAL_PATTERNS else PATTERN_BREATHE,
            speed_seconds=max(MIN_SPEED_SECONDS, min(MAX_SPEED_SECONDS, float(self.speed_seconds))),
            intensity=max(MIN_INTENSITY, min(MAX_INTENSITY, float(self.intensity))),
        )

    def to_dict(self) -> dict:
        return {
            "color": self.color,
            "pattern": self.pattern,
            "speed_seconds": self.speed_seconds,
            "intensity": self.intensity,
        }

    @classmethod
    def from_dict(cls, raw: object, fallback: "SignalStyle") -> "SignalStyle":
        if not isinstance(raw, dict):
            return fallback
        try:
            return cls(
                color=str(raw.get("color", fallback.color)),
                pattern=str(raw.get("pattern", fallback.pattern)),
                speed_seconds=float(raw.get("speed_seconds", fallback.speed_seconds)),
                intensity=float(raw.get("intensity", fallback.intensity)),
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
}


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
