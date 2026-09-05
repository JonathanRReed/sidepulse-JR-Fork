"""Immutable heatmaps derived from canonical local usage records."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from types import MappingProxyType

_COLORS = ("#E5E7EB", "#DDD6FE", "#C4B5FD", "#A78BFA", "#7C3AED")
_VALID_DAYS = (7, 30, 90, 365)
_MAX_PROVIDERS = 32
_INTENSITY_REFERENCE_TOKENS = 100_000


@dataclass(frozen=True, slots=True)
class HeatmapTotals:
    tokens: int = 0
    sessions: int = 0


@dataclass(frozen=True, slots=True)
class HeatmapCell:
    day: date
    tokens: int
    sessions: int
    intensity: int
    color: str
    accessibility_label: str


@dataclass(frozen=True, slots=True)
class ProviderHeatmap:
    provider_id: str
    cells: tuple[HeatmapCell, ...]
    totals: HeatmapTotals
    data_status: str


@dataclass(frozen=True, slots=True)
class UsageHeatmap:
    days: tuple[date, ...]
    providers: Mapping[str, ProviderHeatmap]
    aggregate: ProviderHeatmap
    timezone: str


def _valid_record(record: object) -> tuple[str, str, float, tuple[int, ...], str] | None:
    if not isinstance(record, (tuple, list)) or len(record) != 9:
        return None
    provider, session, _model, raw_epoch, *raw_counts, dedupe = record
    if not all(type(value) is str and value for value in (provider, session, dedupe)):
        return None
    if isinstance(raw_epoch, bool) or not isinstance(raw_epoch, (int, float)):
        return None
    epoch = float(raw_epoch)
    if not math.isfinite(epoch):
        return None
    if not all(type(value) is int and value >= 0 for value in raw_counts):
        return None
    return provider, session, epoch, tuple(raw_counts), dedupe


def _local_day(epoch: float, zone: tzinfo | None) -> date | None:
    try:
        if zone is None:
            return datetime.fromtimestamp(epoch).astimezone().date()
        return datetime.fromtimestamp(epoch, zone).date()
    except (OverflowError, OSError, ValueError):
        return None


def _timezone_name(zone: tzinfo | None, current: datetime) -> str:
    if zone is not None:
        return str(getattr(zone, "key", None) or zone)
    return os.environ.get("TZ") or current.astimezone().tzname() or "local"


def build_usage_heatmap(
    records: Sequence[tuple],
    *,
    provider_ids: tuple[str, ...],
    days: int = 7,
    now: datetime | None = None,
    timezone: tzinfo | None = None,
) -> UsageHeatmap:
    """Build a bounded local-calendar grid from ``usage_stats`` records.

    Token fields are the canonical, non-overlapping input, cached-input,
    cache-creation/write, and output counts. They are summed once, exactly as
    ``usage_stats.daily_buckets`` does. Source admission belongs to the scanner;
    consume its complete finite history without imposing a second record cap.
    The output remains bounded by the selected calendar and providers.
    """
    if days not in _VALID_DAYS:
        raise ValueError("days must be one of 7, 30, 90, or 365")
    if (
        type(provider_ids) is not tuple
        or not provider_ids
        or len(provider_ids) > _MAX_PROVIDERS
        or len(provider_ids) != len(set(provider_ids))
        or not all(type(value) is str and value for value in provider_ids)
    ):
        raise ValueError("providers must be a nonempty unique tuple of at most 32 IDs")
    if not isinstance(records, Sequence):
        raise ValueError("records must be a finite sequence")

    current = now or (datetime.now(timezone) if timezone else datetime.now().astimezone())
    if timezone is not None:
        local_now = current.astimezone(timezone) if current.tzinfo else current.replace(tzinfo=timezone)
    elif current.tzinfo is None:
        local_now = current.astimezone()
    else:
        local_now = current.astimezone()
    end = local_now.date()
    calendar_days = tuple(end - timedelta(days=days - 1 - index) for index in range(days))
    included_days = frozenset(calendar_days)
    selected = frozenset(provider_ids)
    observed: set[str] = set()
    seen: set[str] = set()
    values: dict[str, dict[date, tuple[int, set[str]]]] = {provider: {} for provider in provider_ids}

    for raw_record in records:
        parsed = _valid_record(raw_record)
        if parsed is None:
            continue
        provider, session, epoch, counts, dedupe = parsed
        if provider not in selected:
            continue
        local_day = _local_day(epoch, timezone)
        if local_day is None:
            continue
        observed.add(provider)
        if local_day not in included_days or dedupe in seen:
            continue
        seen.add(dedupe)
        tokens = sum(counts)
        old_tokens, old_sessions = values[provider].get(local_day, (0, set()))
        old_sessions.add(session)
        values[provider][local_day] = (old_tokens + tokens, old_sessions)

    aggregate_source: dict[date, tuple[int, set[tuple[str, str]]]] = {}
    for provider, source in values.items():
        for day, (tokens, sessions) in source.items():
            old_tokens, old_sessions = aggregate_source.get(day, (0, set()))
            scoped_sessions = {(provider, session) for session in sessions}
            aggregate_source[day] = (old_tokens + tokens, old_sessions | scoped_sessions)
    intensity_scale = max(
        _INTENSITY_REFERENCE_TOKENS,
        max((tokens for tokens, _sessions in aggregate_source.values()), default=0),
    )

    def make(provider: str, source: Mapping[date, tuple[int, set]], available: bool) -> ProviderHeatmap:
        cells = []
        for day in calendar_days:
            tokens, sessions = source.get(day, (0, set()))
            intensity = 0 if tokens == 0 else min(4, max(1, math.ceil(tokens * 4 / intensity_scale)))
            state = "data unavailable" if not available else ("zero activity" if tokens == 0 else "local activity")
            label = f"{day.isoformat()}: {tokens:,} tokens, {len(sessions)} sessions, {state}"
            cells.append(HeatmapCell(day, tokens, len(sessions), intensity, _COLORS[intensity], label))
        all_sessions = {session for _tokens, sessions in source.values() for session in sessions}
        return ProviderHeatmap(
            provider,
            tuple(cells),
            HeatmapTotals(sum(tokens for tokens, _sessions in source.values()), len(all_sessions)),
            "available" if available else "unavailable",
        )

    providers = MappingProxyType(
        {provider: make(provider, values[provider], provider in observed) for provider in provider_ids}
    )
    aggregate = make("all", aggregate_source, bool(observed))
    return UsageHeatmap(calendar_days, providers, aggregate, _timezone_name(timezone, current))


__all__ = ["HeatmapCell", "HeatmapTotals", "ProviderHeatmap", "UsageHeatmap", "build_usage_heatmap"]
