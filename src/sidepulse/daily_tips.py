"""The calendar-keyed feature tips shown at the bottom of the dropdown."""

from __future__ import annotations

from datetime import datetime

# One per day, keyed to the calendar: each teaches a feature people
# don't find on their own. (text, settings pane key or None, anchor).
DAILY_TIPS: tuple[tuple[str, str | None, str | None], ...] = (
    ("Each agent gets its own color when several run at once", "color_studio", None),
    ("Give a session a permanent color from its row's Identity Color menu", None, None),
    ("The Screen Bar hugs your notch -- style it under Screen Bar", "colors_screen_bar", None),
    ("Timer fills your lights as working time passes -- try it below", None, None),
    ("Write your own light animation under Lid Animations", "animations", None),
    ("Whites looking off? Calibrate each device under Devices", "devices", None),
    ("Day, Night, and Travel calibration profiles live under Profiles", None, None),
    ("Ignored asks can escalate: light, menu bar, chime, takeover", "led_behavior", None),
    ("Severe-weather warnings can flash your lights", "extras", None),
    ("Calendar events and Reminders can glow before they're due", "extras", None),
    ("Every signal card in Signals has a Test button -- try one", "led_behavior", None),
    ("Agents on your other Macs can show up in this menu", "agents", None),
    ("A cloud code review can post its own status to SidePulse", "agents", None),
    ("Choose how each provider's light moves under Agents & Providers", "agents", None),
    ("A macOS Focus can dim or silence your lights automatically", "focus", None),
    ("A device can show Agent status, Battery, Timer, or your Studio program", "devices", None),
    (
        "Claude, OpenAI, Codex, and Gemini brand colors are the swatches on every Agent color row",
        "color_studio",
        "brand_colors",
    ),
    ("Celebrate when finished sweeps green the moment an agent completes", "color_studio", None),
)


def daily_tip(settings=None) -> tuple[str, str | None, str | None] | None:
    """The day's tip, skipping anything the user dismissed. None when
    every tip is dismissed or tips are off entirely."""
    if settings is not None and not getattr(settings, "tips_enabled", True):
        return None
    dismissed = set(getattr(settings, "dismissed_tips", ()) or ())
    tips = [tip for tip in DAILY_TIPS if tip[0] not in dismissed]
    if not tips:
        return None
    # Local calendar day: the tip changes overnight, like a calendar page.
    day = datetime.now().timetuple().tm_yday
    return tips[day % len(tips)]


__all__ = ["DAILY_TIPS", "daily_tip"]
