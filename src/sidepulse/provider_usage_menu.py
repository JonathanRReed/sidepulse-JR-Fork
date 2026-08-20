"""Pure projection for the compact native provider usage menu."""

from __future__ import annotations

from dataclasses import dataclass

from .provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    most_constrained_lane,
    provider_descriptor,
)
from .provider_usage_qol import format_lane_meter, format_reset_countdown
from .provider_usage_runtime import ProviderUsageState
from .provider_usage_settings import MenuUsageDisplay


@dataclass(frozen=True, slots=True)
class ProviderUsageMenuRow:
    provider_id: str
    title: str
    detail: str | None
    usage_detail: str | None
    action_label: str | None
    stale: bool
    #: One meter line per rate-limit lane ("▰▰▰▰▰▰▱▱  5-hour · 74% left ·
    #: resets in 2h 10m") -- the codebar/t3code-style at-a-glance limits.
    #: Renderers show these INSTEAD of `detail` when non-empty.
    lane_lines: tuple[str, ...] = ()


def _lane_lines(
    snapshot: ProviderUsageSnapshot,
    *,
    now: float,
    display: MenuUsageDisplay,
) -> tuple[str, ...]:
    lines = []
    lanes = tuple(
        lane
        for lane in snapshot.lanes
        if display.show_detail_lanes or lane.bindable
    )[:6]
    for lane in lanes:
        countdown = format_reset_countdown(lane.reset_at, now=now)
        if lane.remaining_percent is None:
            lines.append(f"{lane.label} · {countdown}")
            continue
        meter = (
            f"{format_lane_meter(lane.remaining_percent)}  "
            if display.show_meters
            else ""
        )
        lines.append(
            f"{meter}{lane.label} · {lane.remaining_percent:.0f}% left · {countdown}"
        )
    return tuple(lines)


@dataclass(frozen=True, slots=True)
class ProviderUsageMenuProjection:
    title: str
    rows: tuple[ProviderUsageMenuRow, ...]
    refreshing: bool
    needs_setup: bool


def _state_label(snapshot: ProviderUsageSnapshot) -> str:
    return {
        ProviderSourceState.DISABLED: "off",
        ProviderSourceState.READY: "ready",
        ProviderSourceState.NEEDS_CONSENT: "permission required",
        ProviderSourceState.NEEDS_SIGN_IN: "sign-in required",
        ProviderSourceState.SOURCE_NOT_FOUND: "source not found",
        ProviderSourceState.UNAVAILABLE: "unavailable",
        ProviderSourceState.RATE_LIMITED: "rate limited",
        ProviderSourceState.STALE: "stale",
        ProviderSourceState.ERROR: "error",
        ProviderSourceState.UNSUPPORTED: "unsupported",
    }[snapshot.state]


def _row(
    snapshot: ProviderUsageSnapshot,
    *,
    now: float,
    display: MenuUsageDisplay,
) -> ProviderUsageMenuRow:
    provider_label = provider_descriptor(snapshot.provider_id).label
    lane = most_constrained_lane(snapshot)
    if lane is None:
        title = f"{provider_label} · {_state_label(snapshot)}"
        detail = None
    else:
        remaining = (
            "unknown"
            if lane.remaining_percent is None
            else f"{lane.remaining_percent:.0f}% left"
        )
        title = f"{provider_label} · {remaining}"
        detail = f"{lane.label} {remaining} · {format_reset_countdown(lane.reset_at, now=now)}"
        if snapshot.state is ProviderSourceState.STALE:
            detail += " · stale"
    token_total = (
        snapshot.input_tokens
        + snapshot.cached_input_tokens
        + snapshot.output_tokens
    )
    usage_parts = []
    if display.show_totals and token_total:
        usage_parts.append(f"{token_total:,} tokens")
    if display.show_totals and snapshot.model_count:
        usage_parts.append(
            f"{snapshot.model_count} model"
            f"{'s' if snapshot.model_count != 1 else ''}"
        )
    if display.show_cost and snapshot.estimated_cost_usd is not None:
        usage_parts.append(f"est. ${snapshot.estimated_cost_usd:.2f}")
    return ProviderUsageMenuRow(
        snapshot.provider_id,
        title,
        detail,
        " · ".join(usage_parts) if usage_parts else None,
        snapshot.action_label,
        snapshot.state is ProviderSourceState.STALE,
        _lane_lines(snapshot, now=now, display=display),
    )


def project_usage_menu(
    state: ProviderUsageState,
    *,
    now: float,
    display: MenuUsageDisplay | None = None,
    hidden_providers: frozenset[str] = frozenset(),
) -> ProviderUsageMenuProjection:
    display = MenuUsageDisplay() if display is None else display
    snapshots = tuple(
        snapshot
        for snapshot in state.snapshots
        if snapshot.provider_id not in hidden_providers
    )
    rows = tuple(_row(snapshot, now=now, display=display) for snapshot in snapshots)
    actionable = tuple(
        snapshot
        for snapshot in snapshots
        if snapshot.action_label is not None
        and snapshot.state is not ProviderSourceState.DISABLED
    )
    constrained = []
    for snapshot in snapshots:
        if snapshot.state not in {ProviderSourceState.READY, ProviderSourceState.STALE}:
            continue
        lane = most_constrained_lane(snapshot)
        if lane is not None and lane.remaining_percent is not None:
            constrained.append((lane.remaining_percent, snapshot.provider_id))
    constrained.sort(key=lambda item: (item[0], item[1]))
    if state.refreshing and not state.snapshots:
        title = "Usage · refreshing…"
    elif constrained:
        labels = [
            f"{provider_descriptor(provider_id).label} {remaining:.0f}%"
            for remaining, provider_id in constrained[:2]
        ]
        title = "Usage · " + " · ".join(labels)
    elif actionable:
        title = "Usage · setup needed"
    elif state.snapshots:
        title = "Usage"
    else:
        title = "Usage · not collected"
    return ProviderUsageMenuProjection(
        title,
        rows,
        state.refreshing,
        bool(actionable),
    )


__all__ = [
    "ProviderUsageMenuProjection",
    "ProviderUsageMenuRow",
    "project_usage_menu",
]
