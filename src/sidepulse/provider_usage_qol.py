"""Reset celebrations, threshold notices, countdowns, and usage totals."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from .provider_usage_platform import ProviderSourceState, ProviderUsageSnapshot, UsageLane


@dataclass(frozen=True, slots=True)
class ResetEvent:
    event_id: str
    provider_id: str
    lane_id: str
    label: str
    occurred_at: float


@dataclass(frozen=True, slots=True)
class ThresholdCrossing:
    provider_id: str
    lane_id: str
    label: str
    remaining_percent: float
    threshold_percent: float


@dataclass(frozen=True, slots=True)
class UsageTotals:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    providers_with_usage: int
    model_observations: int
    estimated_cost_usd: float | None
    cache_savings_usd: float | None


def _snapshot_map(
    snapshots: tuple[ProviderUsageSnapshot, ...],
) -> dict[str, ProviderUsageSnapshot]:
    return {snapshot.provider_id: snapshot for snapshot in snapshots}


def _lane_map(snapshot: ProviderUsageSnapshot) -> dict[str, UsageLane]:
    return {lane.lane_id: lane for lane in snapshot.lanes}


def _event_id(provider_id: str, lane_id: str, old_reset_at: float) -> str:
    material = f"{provider_id}\0{lane_id}\0{old_reset_at:.6f}".encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()[:24]
    return f"{provider_id}:{lane_id}:{digest}"


def detect_reset_events(
    previous: tuple[ProviderUsageSnapshot, ...],
    current: tuple[ProviderUsageSnapshot, ...],
    *,
    seen_event_ids: frozenset[str],
) -> tuple[ResetEvent, ...]:
    before_by_provider = _snapshot_map(previous)
    events: list[ResetEvent] = []
    for after in current:
        if after.state is not ProviderSourceState.READY:
            continue
        before = before_by_provider.get(after.provider_id)
        if before is None or before.state not in {
            ProviderSourceState.READY,
            ProviderSourceState.STALE,
        }:
            continue
        before_lanes = _lane_map(before)
        for lane_after in after.lanes:
            lane_before = before_lanes.get(lane_after.lane_id)
            if (
                lane_before is None
                or lane_before.reset_at is None
                or lane_after.reset_at is None
                or lane_before.remaining_percent is None
                or lane_after.remaining_percent is None
                or not (before.observed_at < lane_before.reset_at <= after.observed_at)
                or lane_after.reset_at <= lane_before.reset_at
                or lane_after.remaining_percent <= lane_before.remaining_percent + 5.0
            ):
                continue
            event_id = _event_id(
                after.provider_id,
                lane_after.lane_id,
                lane_before.reset_at,
            )
            if event_id in seen_event_ids:
                continue
            events.append(
                ResetEvent(
                    event_id,
                    after.provider_id,
                    lane_after.lane_id,
                    f"{lane_after.label} reset",
                    after.observed_at,
                )
            )
    return tuple(events)


def threshold_crossings(
    previous: tuple[ProviderUsageSnapshot, ...],
    current: tuple[ProviderUsageSnapshot, ...],
    thresholds: dict[str, float],
) -> tuple[ThresholdCrossing, ...]:
    before_by_provider = _snapshot_map(previous)
    crossings: list[ThresholdCrossing] = []
    for after in current:
        if after.state is not ProviderSourceState.READY:
            continue
        threshold = thresholds.get(after.provider_id)
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(float(threshold))
            or not 0.0 <= float(threshold) <= 100.0
        ):
            continue
        before = before_by_provider.get(after.provider_id)
        if before is None:
            continue
        before_lanes = _lane_map(before)
        for lane_after in after.lanes:
            lane_before = before_lanes.get(lane_after.lane_id)
            if (
                lane_before is None
                or lane_before.remaining_percent is None
                or lane_after.remaining_percent is None
                or not (
                    lane_before.remaining_percent > float(threshold)
                    >= lane_after.remaining_percent
                )
            ):
                continue
            crossings.append(
                ThresholdCrossing(
                    after.provider_id,
                    lane_after.lane_id,
                    lane_after.label,
                    lane_after.remaining_percent,
                    float(threshold),
                )
            )
    return tuple(crossings)


def format_reset_countdown(reset_at: float | None, *, now: float) -> str:
    if (
        reset_at is None
        or isinstance(reset_at, bool)
        or not isinstance(reset_at, (int, float))
        or not math.isfinite(float(reset_at))
    ):
        return "reset unknown"
    seconds = max(0, int(math.ceil(float(reset_at) - float(now))))
    if seconds <= 0:
        return "resetting now"
    minutes = max(1, seconds // 60)
    if minutes < 60:
        return f"resets in {minutes}m"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"resets in {hours}h {remaining_minutes}m"
    days, remaining_hours = divmod(hours, 24)
    return f"resets in {days}d {remaining_hours}h"


def usage_totals(
    snapshots: tuple[ProviderUsageSnapshot, ...],
) -> UsageTotals:
    input_tokens = sum(snapshot.input_tokens for snapshot in snapshots)
    cached_input_tokens = sum(snapshot.cached_input_tokens for snapshot in snapshots)
    output_tokens = sum(snapshot.output_tokens for snapshot in snapshots)
    providers_with_usage = sum(
        1
        for snapshot in snapshots
        if snapshot.input_tokens
        or snapshot.cached_input_tokens
        or snapshot.output_tokens
        or snapshot.estimated_cost_usd is not None
    )
    model_observations = sum(snapshot.model_count for snapshot in snapshots)
    cost_values = tuple(
        snapshot.estimated_cost_usd
        for snapshot in snapshots
        if snapshot.estimated_cost_usd is not None
    )
    saving_values = tuple(
        snapshot.cache_savings_usd
        for snapshot in snapshots
        if snapshot.cache_savings_usd is not None
    )
    return UsageTotals(
        input_tokens,
        cached_input_tokens,
        output_tokens,
        providers_with_usage,
        model_observations,
        sum(cost_values) if cost_values else None,
        sum(saving_values) if saving_values else None,
    )


__all__ = [
    "ResetEvent",
    "ThresholdCrossing",
    "UsageTotals",
    "detect_reset_events",
    "format_reset_countdown",
    "threshold_crossings",
    "usage_totals",
]
