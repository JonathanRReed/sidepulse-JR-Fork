"""Pure local-calendar boundary resolution shared by schedules and presets."""

from __future__ import annotations

import math
import os
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def resolve_local_epoch(
    target_date: date,
    target_time: time,
    zone: tzinfo,
    *,
    not_before_epoch: float | None = None,
) -> float | None:
    """Resolve one wall-clock boundary without inventing fixed-day arithmetic.

    A nonexistent local time advances to the first valid local second. An
    ambiguous time selects the earliest occurrence at or after the optional
    epoch lower bound.
    """
    if type(target_date) is not date or type(target_time) is not time:
        return None
    if target_time.tzinfo is not None or not isinstance(zone, tzinfo):
        return None
    lower = _finite_epoch(not_before_epoch)
    if not_before_epoch is not None and lower is None:
        return None
    try:
        target = datetime.combine(target_date, target_time)
    except (OverflowError, TypeError, ValueError):
        return None
    valid = valid_local_epochs(target, zone)
    if not valid:
        valid = _first_valid_epochs_after_gap(target, zone)
    if lower is not None:
        valid = tuple(epoch for epoch in valid if epoch >= lower)
    return min(valid) if valid else None


def valid_local_epochs(local: datetime, zone: tzinfo) -> tuple[float, ...]:
    """Return every real epoch represented by one naive local datetime."""
    if (
        type(local) is not datetime
        or local.tzinfo is not None
        or not isinstance(zone, tzinfo)
    ):
        return ()
    epochs: set[float] = set()
    for fold in (0, 1):
        try:
            aware = local.replace(tzinfo=zone, fold=fold)
            epoch = aware.timestamp()
            if not math.isfinite(epoch):
                continue
            round_trip = datetime.fromtimestamp(epoch, zone).replace(tzinfo=None)
        except (OSError, OverflowError, TypeError, ValueError):
            continue
        if round_trip == local:
            epochs.add(epoch)
    return tuple(sorted(epochs))


def system_local_timezone(now: float) -> tzinfo:
    """Resolve the current local zone afresh so timezone changes take effect."""
    epoch = _finite_epoch(now)
    if epoch is None:
        return timezone.utc
    environment_zone = os.environ.get("TZ", "").lstrip(":")
    if environment_zone:
        try:
            return ZoneInfo(environment_zone)
        except (ValueError, ZoneInfoNotFoundError):
            pass
    try:
        with Path("/etc/localtime").open("rb") as stream:
            return ZoneInfo.from_file(stream)
    except (OSError, ValueError):
        pass
    try:
        return datetime.fromtimestamp(epoch).astimezone().tzinfo or timezone.utc
    except (OSError, OverflowError, ValueError):
        return timezone.utc


def _first_valid_epochs_after_gap(
    target: datetime,
    zone: tzinfo,
) -> tuple[float, ...]:
    probe = target
    for _minute in range(3 * 24 * 60):
        try:
            probe += timedelta(minutes=1)
        except OverflowError:
            return ()
        valid = valid_local_epochs(probe, zone)
        if not valid:
            continue
        previous_minute = probe - timedelta(minutes=1)
        for second in range(60):
            exact = valid_local_epochs(
                previous_minute + timedelta(seconds=second),
                zone,
            )
            if exact:
                return exact
        return valid
    return ()


def _finite_epoch(value: object) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        epoch = float(value)
    except OverflowError:
        return None
    return epoch if math.isfinite(epoch) else None
