"""One deterministic timestamp-freshness policy for SidePulse.

Naive datetimes are interpreted as UTC. A timestamp up to five seconds in
the future is treated as age zero to tolerate small clock skew. Anything
farther in the future has infinite age and is never recent. Age windows are
inclusive at their exact boundary; negative windows are never recent.
"""

from __future__ import annotations

from datetime import datetime, timezone

FUTURE_CLOCK_SKEW_SECONDS = 5.0


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def bounded_age_seconds(
    now: datetime,
    timestamp: datetime,
    *,
    future_skew_seconds: float = FUTURE_CLOCK_SKEW_SECONDS,
) -> float:
    """Return nonnegative age, or infinity for implausible future time."""
    current = _as_utc(now)
    observed = _as_utc(timestamp)
    age = (current - observed).total_seconds()
    if age >= 0.0:
        return age
    if future_skew_seconds >= 0.0 and -age <= future_skew_seconds:
        return 0.0
    return float("inf")


def is_recent(
    now: datetime,
    timestamp: datetime,
    window_seconds: float,
    *,
    future_skew_seconds: float = FUTURE_CLOCK_SKEW_SECONDS,
) -> bool:
    """Return whether ``timestamp`` is inside the inclusive age window."""
    if window_seconds < 0.0:
        return False
    return bounded_age_seconds(
        now,
        timestamp,
        future_skew_seconds=future_skew_seconds,
    ) <= window_seconds
