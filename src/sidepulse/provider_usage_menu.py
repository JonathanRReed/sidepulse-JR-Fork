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
from .usage_pace import PACE_CRITICAL, PACE_OUT, lane_pace, pace_phrase


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
    #: Indexes into lane_lines whose lane has crossed the provider's
    #: low-remaining threshold -- renderers paint these as a warning.
    alert_lane_indexes: tuple[int, ...] = ()


def _lane_lines(
    snapshot: ProviderUsageSnapshot,
    *,
    now: float,
    display: MenuUsageDisplay,
    threshold: float | None,
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    lines = []
    alerts = []
    lanes = tuple(
        lane
        for lane in snapshot.lanes
        if display.show_detail_lanes or lane.bindable
    )[:6]
    for index, lane in enumerate(lanes):
        countdown = format_reset_countdown(lane.reset_at, now=now)
        if lane.remaining_percent is None:
            lines.append(f"{lane.label} · {countdown}")
            continue
        pace = lane_pace(
            remaining_percent=lane.remaining_percent,
            reset_at=lane.reset_at,
            lane_id=lane.lane_id,
            now=now,
        )
        if (threshold is not None and lane.remaining_percent <= threshold) or (
            pace is not None and pace.verdict in {PACE_CRITICAL, PACE_OUT}
        ):
            alerts.append(index)
        meter = (
            f"{format_lane_meter(lane.remaining_percent)}  "
            if display.show_meters
            else ""
        )
        phrase = pace_phrase(pace, now=now)
        pace_tag = f" · {phrase}" if phrase else ""
        lines.append(
            f"{meter}{lane.label} · {lane.remaining_percent:.0f}% left"
            f" · {countdown}{pace_tag}"
        )
    return tuple(lines), tuple(alerts)


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
    threshold: float | None = None,
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
    lane_lines, alert_indexes = _lane_lines(
        snapshot, now=now, display=display, threshold=threshold
    )
    return ProviderUsageMenuRow(
        snapshot.provider_id,
        title,
        detail,
        " · ".join(usage_parts) if usage_parts else None,
        snapshot.action_label,
        snapshot.state is ProviderSourceState.STALE,
        lane_lines,
        alert_indexes,
    )


def project_usage_menu(
    state: ProviderUsageState,
    *,
    now: float,
    display: MenuUsageDisplay | None = None,
    hidden_providers: frozenset[str] = frozenset(),
    thresholds: dict[str, float] | None = None,
) -> ProviderUsageMenuProjection:
    display = MenuUsageDisplay() if display is None else display
    thresholds = {} if thresholds is None else thresholds
    snapshots = tuple(
        snapshot
        for snapshot in state.snapshots
        if snapshot.provider_id not in hidden_providers
    )
    rows = tuple(
        _row(
            snapshot,
            now=now,
            display=display,
            threshold=thresholds.get(snapshot.provider_id),
        )
        for snapshot in snapshots
    )
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
        # The tightest lane's meter rides in the row itself: the
        # at-a-glance answer with zero hovering. Curated off with the
        # same switch as the per-lane meters.
        meter = (
            f"{format_lane_meter(constrained[0][0])}  "
            if display.show_meters
            else ""
        )
        title = f"Usage · {meter}" + " · ".join(labels)
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


@dataclass(frozen=True, slots=True)
class QuotaGlance:
    text: str
    #: A pace verdict from usage_pace (PACE_*), or None when the shown
    #: lane has no pace reading. Renderers color fast/critical/out.
    verdict: str | None


def _display_lane(snapshot: ProviderUsageSnapshot, *, now: float):
    """The lane worth showing for one provider: a lane that runs dry
    before its reset beats everything (earliest exhaustion first);
    otherwise the most constrained bindable lane."""
    critical = []
    for lane in snapshot.lanes:
        if not lane.bindable or lane.remaining_percent is None:
            continue
        pace = lane_pace(
            remaining_percent=lane.remaining_percent,
            reset_at=lane.reset_at,
            lane_id=lane.lane_id,
            now=now,
        )
        if pace is not None and pace.verdict in {PACE_CRITICAL, PACE_OUT}:
            critical.append((pace.exhaustion_epoch or now, lane, pace))
    if critical:
        _, lane, pace = min(critical, key=lambda item: item[0])
        return lane, pace
    lane = most_constrained_lane(snapshot)
    if lane is None or lane.remaining_percent is None:
        return None, None
    return lane, lane_pace(
        remaining_percent=lane.remaining_percent,
        reset_at=lane.reset_at,
        lane_id=lane.lane_id,
        now=now,
    )


def menu_bar_quota_glance(
    state: ProviderUsageState,
    *,
    hidden_providers: frozenset[str] = frozenset(),
    active_providers: frozenset[str] = frozenset(),
    now: float,
) -> QuotaGlance | None:
    """What rides next to the menu-bar icon.

    The providers RUNNING right now own the glance: while an agent
    works, the number that matters is that provider's own runway, on
    whichever window is most at risk (a lane projected to run dry
    before its reset outranks a merely-low one). With several active,
    the lowest wins. With none active, the tightest visible provider
    speaks. None when nothing has a number -- never "unknown%"."""
    candidates = []
    for snapshot in state.snapshots:
        if snapshot.provider_id in hidden_providers:
            continue
        if snapshot.state not in {
            ProviderSourceState.READY,
            ProviderSourceState.STALE,
        }:
            continue
        lane, pace = _display_lane(snapshot, now=now)
        if lane is None:
            continue
        candidates.append((snapshot.provider_id, lane, pace))
    if not candidates:
        return None
    active = [item for item in candidates if item[0] in active_providers]
    pool = active or candidates
    urgent = [
        item
        for item in pool
        if item[2] is not None and item[2].verdict in {PACE_CRITICAL, PACE_OUT}
    ]
    if urgent:
        _, lane, pace = min(
            urgent, key=lambda item: item[2].exhaustion_epoch or now
        )
    else:
        _, lane, pace = min(pool, key=lambda item: item[1].remaining_percent)
    return QuotaGlance(
        f"{lane.remaining_percent:.0f}%",
        pace.verdict if pace is not None else None,
    )


__all__ = [
    "ProviderUsageMenuProjection",
    "ProviderUsageMenuRow",
    "QuotaGlance",
    "menu_bar_quota_glance",
    "project_usage_menu",
]
