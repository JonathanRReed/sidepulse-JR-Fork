"""The Today section: readable calendar/reminders/weather, honest gaps."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sidepulse.today_menu import (
    TodaySnapshot,
    _relative_start,
    project_today_rows,
    today_menu_title,
)


def test_relative_start_phrasing() -> None:
    now = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)
    assert _relative_start(now + timedelta(seconds=30), now) == "now"
    assert _relative_start(now + timedelta(minutes=42), now) == "in 42m"
    later = _relative_start(now + timedelta(hours=3), now)
    assert ":" in later  # clock time past the hour horizon


def test_title_leads_with_weather_alerts_then_calendar() -> None:
    quiet = TodaySnapshot(calendar_line="No events in the next 12 hours")
    assert today_menu_title(quiet) == "Today"

    busy = TodaySnapshot(calendar_line="Standup · in 12m")
    assert today_menu_title(busy) == "Today · Standup · in 12m"

    storm = TodaySnapshot(
        calendar_line="Standup · in 12m",
        weather_lines=("⚠ Severe Thunderstorm Warning",),
    )
    assert today_menu_title(storm) == "Today · ⚠ 1 weather alert"


def test_rows_read_in_order_and_flag_alerts() -> None:
    snapshot = TodaySnapshot(
        calendar_line="Standup · in 12m",
        reminder_lines=("Pay rent", "Call back"),
        weather_lines=("No active weather alerts",),
    )
    rows = project_today_rows(snapshot)
    assert rows[0] == ("Next event · Standup · in 12m", False, "calendar")
    assert rows[1][0].startswith("Reminders · Pay rent")
    assert rows[1][2] == "reminders"
    assert rows[-1] == ("No active weather alerts", False, "weather")
    assert not any(alert for _text, alert, _kind in rows)

    alerting = project_today_rows(
        TodaySnapshot(weather_lines=("⚠ Flood Watch",))
    )
    assert alerting == (("⚠ Flood Watch", True, "weather"),)
