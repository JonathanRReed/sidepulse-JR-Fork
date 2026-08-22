"""The dropdown's "Today" section: calendar, reminders, weather — visible.

These three features existed only as LIGHT effects (a glow before an
event, an amber pulse for a due reminder, a flash for severe weather)
plus switches buried in Extras. The information itself was never
readable anywhere. This section makes the menu answer the questions the
lights only hint at: what's next on the calendar, what's due, whether a
weather alert is active.

Ground rules: features the user has NOT enabled fetch nothing (their
switches are permission prompts by design); fetching happens on a slow
background thread and the menu only ever reads a cached snapshot; a
fetch failure is a quiet honest row, never an alarm.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

TODAY_REFRESH_SECONDS = 300.0
CALENDAR_WINDOW_MINUTES = 12 * 60.0
REMINDER_LOOKBACK_SECONDS = 24 * 3600.0
MAX_REMINDER_ROWS = 3


@dataclass
class TodaySnapshot:
    calendar_line: str | None = None
    reminder_lines: tuple[str, ...] = ()
    weather_lines: tuple[str, ...] = ()
    fetched_at: float = 0.0
    enabled_count: int = 0


def _relative_start(start: datetime, now: datetime) -> str:
    delta = max(0.0, (start - now).total_seconds())
    if delta < 90.0:
        return "now"
    if delta < 3600.0:
        return f"in {int(delta // 60)}m"
    local = start.astimezone()
    return local.strftime("%-H:%M")


class TodayFeed:
    """Slow background fetch of whatever the user has switched on."""

    def __init__(self) -> None:
        self._snapshot = TodaySnapshot()
        self._lock = threading.Lock()
        self._refreshing = False

    def snapshot(self, settings) -> TodaySnapshot:
        with self._lock:
            current = self._snapshot
            stale = time.monotonic() - current.fetched_at > TODAY_REFRESH_SECONDS
            if stale and not self._refreshing:
                self._refreshing = True
                threading.Thread(
                    target=self._refresh,
                    args=(settings,),
                    name="SidePulseToday",
                    daemon=True,
                ).start()
        return current

    def _refresh(self, settings) -> None:
        fresh = TodaySnapshot(fetched_at=time.monotonic())
        if getattr(settings, "calendar_alerts_enabled", False):
            fresh.enabled_count += 1
            fresh.calendar_line = self._calendar_line()
        if getattr(settings, "reminder_alerts_enabled", False):
            fresh.enabled_count += 1
            fresh.reminder_lines = self._reminder_lines()
        if getattr(settings, "weather_alerts_enabled", False):
            fresh.enabled_count += 1
            fresh.weather_lines = self._weather_lines(settings)
        with self._lock:
            self._snapshot = fresh
            self._refreshing = False

    def _calendar_line(self) -> str | None:
        try:
            from . import calendar_watch

            found = calendar_watch.next_event_start(CALENDAR_WINDOW_MINUTES)
        except Exception:
            return "Calendar unavailable — check access in System Settings"
        if found is None:
            return "No events in the next 12 hours"
        title, start = found
        return f"{title} · {_relative_start(start, datetime.now(timezone.utc))}"

    def _reminder_lines(self) -> tuple[str, ...]:
        collected: list[str] = []
        done = threading.Event()

        def _completion(reminders) -> None:
            try:
                for reminder in list(reminders or [])[:MAX_REMINDER_ROWS]:
                    title = str(getattr(reminder, "title", lambda: "")() or "")
                    if title:
                        collected.append(title)
            except Exception:
                pass
            done.set()

        try:
            from . import reminders_watch

            reminders_watch.fetch_due(REMINDER_LOOKBACK_SECONDS, _completion)
            done.wait(timeout=10.0)
        except Exception:
            return ("Reminders unavailable — check access in System Settings",)
        if not collected:
            return ("Nothing due",)
        return tuple(collected)

    def _weather_lines(self, settings) -> tuple[str, ...]:
        try:
            from . import weather_watch

            latitude = getattr(settings, "weather_latitude", None)
            longitude = getattr(settings, "weather_longitude", None)
            if latitude is None or longitude is None:
                latitude, longitude = weather_watch.ip_location()
            alerts = weather_watch.active_alerts(latitude, longitude)
        except Exception:
            return ("Weather check unavailable right now",)
        if not alerts:
            return ("No active weather alerts",)
        return tuple(
            f"⚠ {alert[0]}" for alert in alerts[:3]
        )


_shared_feed: TodayFeed | None = None
_shared_lock = threading.Lock()


def shared_today_feed() -> TodayFeed:
    global _shared_feed
    with _shared_lock:
        if _shared_feed is None:
            _shared_feed = TodayFeed()
        return _shared_feed


def project_today_rows(snapshot: TodaySnapshot) -> tuple[tuple[str, bool], ...]:
    """(text, is_alert) rows for the submenu, in reading order."""
    rows: list[tuple[str, bool]] = []
    if snapshot.calendar_line is not None:
        rows.append((f"Next event · {snapshot.calendar_line}", False))
    for line in snapshot.reminder_lines:
        prefix = "Reminders · " if line == snapshot.reminder_lines[0] else "     "
        rows.append((f"{prefix}{line}", False))
    for line in snapshot.weather_lines:
        rows.append((line, line.startswith("⚠")))
    return tuple(rows)


def today_menu_title(snapshot: TodaySnapshot) -> str:
    alerts = sum(1 for line in snapshot.weather_lines if line.startswith("⚠"))
    if alerts:
        return f"Today · ⚠ {alerts} weather alert{'s' if alerts != 1 else ''}"
    if snapshot.calendar_line and "No events" not in snapshot.calendar_line:
        return f"Today · {snapshot.calendar_line}"
    return "Today"


def build_today_menu_item(target):
    """The whole section as one NSMenuItem, or None when nothing is on.

    With none of the three switched on, one quiet setup row points at
    the switches instead of pretending there is nothing to say.
    """
    from AppKit import NSMenu, NSMenuItem

    settings = getattr(target, "settings", None)
    if settings is None:
        return None
    enabled = any(
        getattr(settings, flag, False)
        for flag in (
            "calendar_alerts_enabled",
            "reminder_alerts_enabled",
            "weather_alerts_enabled",
        )
    )
    snapshot = shared_today_feed().snapshot(settings) if enabled else TodaySnapshot()
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        today_menu_title(snapshot) if enabled else "Today", None, ""
    )
    submenu = NSMenu.alloc().init()
    submenu.setAutoenablesItems_(False)
    if not enabled:
        setup = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Show calendar, reminders and weather here — Settings → Extras…",
            "openSetup:",
            "",
        )
        setup.setTarget_(target)
        submenu.addItem_(setup)
    else:
        rows = project_today_rows(snapshot)
        if not rows:
            placeholder = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Gathering today's details…", None, ""
            )
            placeholder.setEnabled_(False)
            submenu.addItem_(placeholder)
        for text, _is_alert in rows:
            row_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                text, None, ""
            )
            row_item.setEnabled_(False)
            submenu.addItem_(row_item)
    item.setSubmenu_(submenu)
    return item


__all__ = [
    "TodayFeed",
    "TodaySnapshot",
    "build_today_menu_item",
    "project_today_rows",
    "shared_today_feed",
    "today_menu_title",
]
