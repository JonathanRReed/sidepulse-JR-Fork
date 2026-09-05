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
    source_instance_id: str = "default"
    reset_boundary: float | None = None


@dataclass(frozen=True, slots=True)
class ThresholdCrossing:
    provider_id: str
    lane_id: str
    label: str
    remaining_percent: float
    threshold_percent: float
    source_instance_id: str = "default"


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
) -> dict[tuple[str, str], ProviderUsageSnapshot]:
    return {snapshot.identity: snapshot for snapshot in snapshots}


def _lane_map(snapshot: ProviderUsageSnapshot) -> dict[str, UsageLane]:
    return {lane.lane_id: lane for lane in snapshot.lanes}


def _event_id(
    provider_id: str,
    source_instance_id: str,
    lane_id: str,
    old_reset_at: float,
) -> str:
    material = (
        f"{provider_id}\0{source_instance_id}\0{lane_id}\0{old_reset_at:.6f}"
    ).encode()
    digest = hashlib.sha256(material).hexdigest()[:24]
    prefix = (
        f"{provider_id}:{lane_id}"
        if source_instance_id == "default"
        else f"{provider_id}:{source_instance_id}:{lane_id}"
    )
    return f"{prefix}:{digest}"


def merged_edge_baseline(previous, current):
    """The next edge comparison's BEFORE: last COMPARABLE reading per
    provider.

    Edge detectors skip a before-snapshot that is not READY/STALE -- so
    a vendor incident's degraded snapshot, published between two good
    readings, used to WIPE the pre-reset baseline and swallow the
    crossing (2026-08-27: the owner's Codex refill went uncelebrated
    behind exactly that interlude). A degraded or missing current
    snapshot keeps the provider's previous baseline instead.
    """
    from dataclasses import replace as dataclass_replace

    comparable = {ProviderSourceState.READY, ProviderSourceState.STALE}
    before = _snapshot_map(previous.snapshots)
    kept = []
    for snapshot in current.snapshots:
        held = before.get(snapshot.identity)
        if snapshot.state in comparable or held is None:
            kept.append(snapshot)
        else:
            kept.append(held)
    current_ids = {snapshot.identity for snapshot in current.snapshots}
    kept.extend(
        snapshot
        for identity, snapshot in before.items()
        if identity not in current_ids
    )
    return dataclass_replace(current, snapshots=tuple(kept))


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
        before = before_by_provider.get(after.identity)
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
                or lane_after.reset_at <= lane_before.reset_at
            ):
                continue
            # Two independent detectors, either fires:
            #   TIMING -- the reset moment passed between our looks. One
            #   failed poll re-stamps observed_at past the boundary
            #   (select_authoritative_snapshot) and blinds this forever,
            #   which is how the owner's live reset went unseen.
            #   JUMP -- remaining leapt >= 50 points while the window
            #   advanced: unmistakably a refill, whatever the clocks
            #   claim (the usage-hook heuristic, promoted).
            crossed = (
                before.observed_at < lane_before.reset_at <= after.observed_at
                and lane_after.remaining_percent
                > lane_before.remaining_percent + 5.0
            )
            jumped = (
                lane_after.remaining_percent
                >= lane_before.remaining_percent + 50.0
            )
            if not crossed and not jumped:
                continue
            event_id = _event_id(
                after.provider_id,
                after.source_instance_id,
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
                    after.source_instance_id,
                    lane_before.reset_at,
                )
            )
    return tuple(events)


def threshold_crossings(
    previous: tuple[ProviderUsageSnapshot, ...],
    current: tuple[ProviderUsageSnapshot, ...],
    thresholds: dict[object, float],
) -> tuple[ThresholdCrossing, ...]:
    before_by_provider = _snapshot_map(previous)
    crossings: list[ThresholdCrossing] = []
    for after in current:
        if after.state is not ProviderSourceState.READY:
            continue
        threshold = thresholds.get(
            after.identity,
            thresholds.get(after.provider_id),
        )
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(float(threshold))
            or not 0.0 <= float(threshold) <= 100.0
        ):
            continue
        before = before_by_provider.get(after.identity)
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
                    after.source_instance_id,
                )
            )
    return tuple(crossings)


#: Eight meter cells, one per LED -- the meter speaks the strip's own
#: language (codebar/t3code-style at-a-glance limits).
METER_CELLS = 8


def format_lane_meter(remaining: float) -> str:
    filled = int(round(max(0.0, min(100.0, float(remaining))) / 100.0 * METER_CELLS))
    if filled == 0 and remaining > 0.0:
        # A nearly-exhausted lane still shows one lit cell: "almost out"
        # and "out" must not render identically.
        filled = 1
    return "▰" * filled + "▱" * (METER_CELLS - filled)


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
        # A reset moment more than a couple of minutes in the past is
        # not "resetting now" -- it means the READING predates the
        # reset. Three stale lanes all chanting "resetting now" forever
        # (live, 2026-08-26) told the user the provider was stuck when
        # it was the number that was old.
        if float(now) - float(reset_at) > 120.0:
            return "reset passed — reading is older"
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
    "format_lane_meter",
    "format_reset_countdown",
    "threshold_crossings",
    "usage_totals",
]
