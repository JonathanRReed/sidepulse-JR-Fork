"""Remaining-percent history for EVERY provider, not just the two local ones.

The token/cost/sessions graph can only show claude and codex because only
they leave local transcript records. Every other provider answers a single
point-in-time question -- "what percent is left right now" -- so the only
way to chart them is to remember those answers. This module is that memory:
an append-only private JSONL of (provider, lane, remaining_percent,
observed_at) samples, deduplicated at the source so an all-day session adds
dozens of points, not thousands, plus the projection that turns the file
into the same graph-model shape the settings chart already draws.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from . import audit
from .private_io import append_private_text
from .providers import default_state_dir

PERCENT_HISTORY_FILE_NAME = "usage-percent-history.jsonl"
#: A lane's sample is worth writing when its value moved at least this
#: much, or this much time passed -- whichever comes first.
PERCENT_RECORD_MIN_DELTA = 1.0
PERCENT_RECORD_MIN_SECONDS = 1800.0


def default_percent_history_path(home: Path | None = None) -> Path:
    return default_state_dir(home) / PERCENT_HISTORY_FILE_NAME


def filter_new_observations(
    last_recorded: dict[tuple[str, str], tuple[float, float]],
    observations: list[tuple[str, str, float]],
    *,
    now_epoch: float,
) -> tuple[list[dict], dict[tuple[str, str], tuple[float, float]]]:
    """(records worth appending, updated last-recorded map).

    ``last_recorded`` maps (provider_id, lane_id) -> (percent, epoch) and
    lives on the controller so dedupe never rereads the file.
    """
    updated = dict(last_recorded)
    fresh: list[dict] = []
    for provider_id, lane_id, remaining_percent in observations:
        if not provider_id or not lane_id:
            continue
        try:
            value = float(remaining_percent)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= value <= 100.0):
            continue
        key = (provider_id, lane_id)
        previous = updated.get(key)
        if previous is not None:
            prior_value, prior_epoch = previous
            if (
                abs(value - prior_value) < PERCENT_RECORD_MIN_DELTA
                and now_epoch - prior_epoch < PERCENT_RECORD_MIN_SECONDS
            ):
                continue
        updated[key] = (value, now_epoch)
        fresh.append(
            {
                "provider_id": provider_id,
                "lane_id": lane_id,
                "remaining_percent": round(value, 2),
                "observed_at_epoch": round(float(now_epoch), 3),
            }
        )
    return fresh, updated


def append_percent_observations(path: Path, records: list[dict]) -> int:
    """Append pre-filtered records; the audit trimmer bounds the file."""
    if not records:
        return 0
    text = "".join(
        json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        for record in records
    )
    append_private_text(path, text)
    audit.compact_jsonl_file(path)
    return len(records)


def _parse_history_lines(text: str) -> list[tuple[str, str, float, float]]:
    parsed: list[tuple[str, str, float, float]] = []
    for line in text.splitlines():
        try:
            payload = json.loads(line)
        except (TypeError, ValueError):
            continue
        if type(payload) is not dict:
            continue
        provider_id = payload.get("provider_id")
        lane_id = payload.get("lane_id")
        percent = payload.get("remaining_percent")
        epoch = payload.get("observed_at_epoch")
        if (
            type(provider_id) is not str
            or not provider_id
            or type(lane_id) is not str
            or type(percent) not in (int, float)
            or type(epoch) not in (int, float)
            or not (0.0 <= float(percent) <= 100.0)
        ):
            continue
        parsed.append((provider_id, lane_id, float(percent), float(epoch)))
    return parsed


def percent_graph_model(
    history_text: str,
    *,
    days: int,
    provider_ids: tuple[str, ...],
    now: datetime | None = None,
    period_label: str | None = None,
) -> dict:
    """The settings chart's model shape, from percent history.

    One value per provider per local calendar day: the day's WORST
    (minimum) remaining percent across that provider's lanes -- the
    number that answers "how squeezed did I get". Days with no sample
    carry the previous known value forward so the line stays readable;
    days before the first sample repeat the first known value.
    """
    current = now or datetime.now()
    day_keys = [
        (current - timedelta(days=offset)).date().isoformat()
        for offset in range(days - 1, -1, -1)
    ]
    label_stride = max(1, days // 6)
    labels = tuple(
        day[5:].replace("-", "/") if index % label_stride == 0 else ""
        for index, day in enumerate(day_keys)
    )
    worst_by_provider_day: dict[str, dict[str, float]] = {}
    for provider_id, _lane_id, percent, epoch in _parse_history_lines(history_text):
        if provider_id not in provider_ids:
            continue
        day = datetime.fromtimestamp(epoch).date().isoformat()
        per_day = worst_by_provider_day.setdefault(provider_id, {})
        existing = per_day.get(day)
        if existing is None or percent < existing:
            per_day[day] = percent
    series = []
    for provider_id in provider_ids:
        per_day = worst_by_provider_day.get(provider_id)
        if not per_day:
            continue
        values: list[float] = []
        carried: float | None = None
        for day in day_keys:
            observed = per_day.get(day)
            if observed is not None:
                carried = observed
            values.append(carried if carried is not None else -1.0)
        if all(value < 0.0 for value in values):
            continue
        # Days BEFORE the first sample stay negative: the chart renders
        # them as a gap. They used to be backfilled with the first known
        # reading, which drew a fabricated flat line across every day
        # before history began (noticeable on 90/365 ranges -- history
        # starts 2026-08-21).
        series.append({"provider_id": provider_id, "values": tuple(values)})
    return {
        "days": days,
        "period_label": period_label or f"Last {days} days",
        "metric": "percent",
        "labels": labels,
        "series": tuple(series),
        "scale_max": 100.0,
    }


def record_state_observations(controller, snapshots) -> None:
    """Record a fresh usage state's lane percents, dedup'd, off-main.

    The controller carries the last-recorded map so dedupe never rereads
    the file; the append itself rides a daemon thread.
    """
    import threading
    import time

    observations = [
        (snapshot.provider_id, lane.lane_id, lane.remaining_percent)
        for snapshot in snapshots
        for lane in snapshot.lanes
        if lane.remaining_percent is not None
    ]
    if not observations:
        return
    fresh, updated = filter_new_observations(
        getattr(controller, "_sidepulse_percent_history_last", {}),
        observations,
        now_epoch=time.time(),
    )
    controller._sidepulse_percent_history_last = updated
    if fresh:
        threading.Thread(
            target=lambda: append_percent_observations(
                default_percent_history_path(), fresh
            ),
            name="SidePulsePercentHistory",
            daemon=True,
        ).start()


def shared_percent_graph_model(
    *,
    days: int,
    period_label: str,
    now: datetime | None = None,
) -> dict:
    """percent_graph_model over the default store, for the full registry.

    The full registry, not the curated token pair: percent is the one
    metric every provider can answer, and the curation default of
    ("claude", "codex") is exactly why the chart looked like a
    two-provider app. Providers with no history contribute no line.
    """
    from .private_io import read_private_text
    from .provider_usage_platform import provider_descriptors

    path = default_percent_history_path()
    history_text = read_private_text(path) if path.exists() else ""
    return percent_graph_model(
        history_text,
        days=days,
        provider_ids=tuple(
            descriptor.provider_id for descriptor in provider_descriptors()
        ),
        now=now,
        period_label=period_label,
    )


__all__ = [
    "append_percent_observations",
    "default_percent_history_path",
    "filter_new_observations",
    "percent_graph_model",
    "shared_percent_graph_model",
]
