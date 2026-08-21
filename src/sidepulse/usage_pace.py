"""Burn-rate pace for rate-limit lanes: the CodexBar at-a-glance answer.

A percentage alone answers "how much is left"; the question that
actually matters mid-session is "am I going to MAKE it to the reset."
Pace compares what you've used against how far into the window you are,
and projects when the lane runs dry if the current rate holds.

Window durations aren't carried on lanes -- they're inferred from the
canonical lane ids every provider parser normalizes to. A lane whose
duration is unknown gets no pace reading rather than a guessed one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Canonical lane ids -> window minutes. Only lanes whose duration is
#: KNOWN get a pace reading.
WINDOW_MINUTES_BY_LANE_ID: Final = {
    "five-hour": 5 * 60,
    "weekly": 7 * 24 * 60,
    "weekly-opus": 7 * 24 * 60,
    "weekly-sonnet": 7 * 24 * 60,
    "fable-only": 7 * 24 * 60,
    "monthly": 30 * 24 * 60,
}

#: Below this much of the window elapsed, usage says nothing about pace.
MIN_ELAPSED_FRACTION: Final = 0.04
#: Ratio bounds for the verdicts. Between them is "on pace".
SURPLUS_RATIO: Final = 0.6
FAST_RATIO: Final = 1.25

PACE_OUT: Final = "out"
PACE_CRITICAL: Final = "critical"  # runs dry BEFORE the reset at this rate
PACE_FAST: Final = "fast"  # spending fast, but the reset arrives first
PACE_ON_PACE: Final = "on_pace"
PACE_SURPLUS: Final = "surplus"


@dataclass(frozen=True, slots=True)
class PaceReading:
    verdict: str
    #: used-so-far divided by uniform-spend-so-far; 1.0 is exactly on pace.
    ratio: float
    #: Epoch when the lane runs dry at the current rate; None when the
    #: rate is zero or the lane already ran out.
    exhaustion_epoch: float | None


def lane_pace(
    *,
    remaining_percent: float | None,
    reset_at: float | None,
    lane_id: str,
    now: float,
) -> PaceReading | None:
    """Pace for one lane, or None when there isn't enough to judge."""
    window_minutes = WINDOW_MINUTES_BY_LANE_ID.get(lane_id)
    if (
        remaining_percent is None
        or reset_at is None
        or window_minutes is None
        or reset_at <= now
    ):
        return None
    window_seconds = window_minutes * 60.0
    elapsed = window_seconds - (reset_at - now)
    if elapsed <= 0.0:
        return None
    elapsed_fraction = min(1.0, elapsed / window_seconds)
    used = max(0.0, min(100.0, 100.0 - remaining_percent))
    if used >= 99.5:
        return PaceReading(PACE_OUT, float("inf"), None)
    if elapsed_fraction < MIN_ELAPSED_FRACTION:
        return None
    expected_used = elapsed_fraction * 100.0
    ratio = used / expected_used if expected_used > 0.0 else 0.0
    rate = used / elapsed  # percent per second
    exhaustion = now + (100.0 - used) / rate if rate > 0.0 else None
    if exhaustion is not None and exhaustion < reset_at:
        return PaceReading(PACE_CRITICAL, ratio, exhaustion)
    if ratio >= FAST_RATIO:
        return PaceReading(PACE_FAST, ratio, exhaustion)
    if ratio <= SURPLUS_RATIO:
        return PaceReading(PACE_SURPLUS, ratio, exhaustion)
    return PaceReading(PACE_ON_PACE, ratio, exhaustion)


def pace_phrase(reading: PaceReading | None, *, now: float) -> str | None:
    """The at-a-glance words for a lane line, or None for no tag."""
    if reading is None:
        return None
    if reading.verdict == PACE_OUT:
        return "out"
    if reading.verdict == PACE_CRITICAL and reading.exhaustion_epoch is not None:
        minutes = max(1, int((reading.exhaustion_epoch - now) // 60))
        if minutes < 60:
            return f"runs out in ~{minutes}m at this rate"
        hours, rest = divmod(minutes, 60)
        return f"runs out in ~{hours}h {rest:02d}m at this rate"
    return {
        PACE_FAST: "spending fast",
        PACE_ON_PACE: "on pace",
        PACE_SURPLUS: "surplus",
    }.get(reading.verdict)


__all__ = [
    "FAST_RATIO",
    "MIN_ELAPSED_FRACTION",
    "PACE_CRITICAL",
    "PACE_FAST",
    "PACE_ON_PACE",
    "PACE_OUT",
    "PACE_SURPLUS",
    "SURPLUS_RATIO",
    "WINDOW_MINUTES_BY_LANE_ID",
    "PaceReading",
    "critical_pace_transitions",
    "lane_pace",
    "pace_phrase",
]


def critical_pace_transitions(
    previous_snapshots,
    current_snapshots,
    *,
    now: float,
    seen_keys: frozenset[str],
) -> tuple[tuple[str, str, str], ...]:
    """Lanes that JUST became critical: (key, provider_id, label).

    A lane is critical when its pace projects it dry before its reset
    (or already out). The key binds provider, lane, and reset window,
    so one window alerts at most once -- a re-render of the same
    critical state is not news, and neither is the same window after a
    restart when the caller persists ``seen_keys``.
    """

    def verdicts(snapshots) -> dict[str, str]:
        found: dict[str, str] = {}
        for snapshot in snapshots:
            for lane in getattr(snapshot, "lanes", ()):
                pace = lane_pace(
                    remaining_percent=lane.remaining_percent,
                    reset_at=lane.reset_at,
                    lane_id=lane.lane_id,
                    now=now,
                )
                if pace is None:
                    continue
                key = (
                    f"{snapshot.provider_id}:{lane.lane_id}:"
                    f"{int(lane.reset_at or 0)}"
                )
                found[key] = pace.verdict
        return found

    before = verdicts(previous_snapshots)
    alerts: list[tuple[str, str, str]] = []
    for snapshot in current_snapshots:
        for lane in getattr(snapshot, "lanes", ()):
            pace = lane_pace(
                remaining_percent=lane.remaining_percent,
                reset_at=lane.reset_at,
                lane_id=lane.lane_id,
                now=now,
            )
            if pace is None or pace.verdict not in {PACE_CRITICAL, PACE_OUT}:
                continue
            key = (
                f"{snapshot.provider_id}:{lane.lane_id}:"
                f"{int(lane.reset_at or 0)}"
            )
            if key in seen_keys:
                continue
            if before.get(key) in {PACE_CRITICAL, PACE_OUT}:
                continue
            alerts.append((key, snapshot.provider_id, lane.label))
    return tuple(alerts)
