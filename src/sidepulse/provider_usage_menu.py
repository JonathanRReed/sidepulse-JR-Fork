"""Pure projection for the compact native provider usage menu."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .provider_account_identity import (
    configured_user_alias,
    project_provider_account_identity,
)
from .provider_feature_settings import ProviderInstanceVisualProjection
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
    source_instance_id: str = field(default="default", repr=False)
    tooltip: str | None = None
    accessibility_label: str | None = None
    compact: bool = False
    collapsed_count: int = 0
    collapsed_rows: tuple[ProviderUsageMenuRow, ...] = ()


def _display_identity(
    snapshot: ProviderUsageSnapshot,
    visual: ProviderInstanceVisualProjection | None,
    *,
    privacy_mode: bool,
) -> tuple[str, str | None, bool, str]:
    visual_label = None
    if visual is not None:
        try:
            policy = visual.provider(
                snapshot.provider_id,
                snapshot.source_instance_id,
            )
        except StopIteration:
            pass
        else:
            visual_label = policy.label
    alias = configured_user_alias(
        provider_id=snapshot.provider_id,
        source_instance_id=snapshot.source_instance_id,
        visual_label=visual_label,
    )
    identity = project_provider_account_identity(
        provider_id=snapshot.provider_id,
        source_instance_id=snapshot.source_instance_id,
        account_label=snapshot.account_label,
        user_alias=alias,
        privacy_mode=privacy_mode,
    )
    return identity.primary_label, identity.account_detail, alias is not None, identity.full_label


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

    @property
    def account_rows(self) -> tuple[ProviderUsageMenuRow, ...]:
        rows = []
        for row in self.rows:
            rows.extend(row.collapsed_rows or (row,))
        return tuple(rows)


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


def _staleness_marker(snapshot: ProviderUsageSnapshot) -> str:
    """Why this number is not current, in one word.

    A blanket "stale" is true but useless when the cause is a sign-in
    that stopped working: the owner can act on "reconnect" and cannot
    act on "stale". Both mean the figure beside it is a LAST-KNOWN
    reading, not a live one.
    """
    if snapshot.reason_code == "authentication_required":
        return "reconnect"
    return "stale"


def _row(
    snapshot: ProviderUsageSnapshot,
    *,
    now: float,
    display: MenuUsageDisplay,
    visible_account_count: int = 1,
    account_position: int = 1,
    threshold: float | None = None,
    visual: ProviderInstanceVisualProjection | None = None,
    privacy_mode: bool = False,
) -> ProviderUsageMenuRow:
    identity_primary, _identity_detail, custom_label, _full_label = _display_identity(
        snapshot,
        visual,
        privacy_mode=privacy_mode,
    )
    provider_label = provider_descriptor(snapshot.provider_id).label
    identity_label = None
    if visible_account_count > 1:
        if custom_label and not privacy_mode and "@" not in identity_primary:
            if provider_label.casefold() in identity_primary.casefold():
                provider_label = identity_primary
            else:
                identity_label = identity_primary
        else:
            identity_label = f"Account {account_position}"
    full_label = (
        provider_label
        if identity_label is None
        else f"{provider_label} · {identity_label}"
    )
    lane = most_constrained_lane(snapshot)
    if lane is None:
        # No usable number. "Grok · stale" is true and useless -- the row
        # is the whole glance, so it carries the thing that would FIX it
        # when there is one (2026-08-27 owner report: "so much of it says
        # Grok stale"). The state label stays the fallback.
        title = f"{provider_label} · "
        if identity_label:
            title += f"{identity_label} · "
        title += snapshot.action_label or _state_label(snapshot)
        detail = (
            f"{_state_label(snapshot)} · last reading unavailable"
            if snapshot.action_label
            else None
        )
    else:
        remaining = (
            "unknown"
            if lane.remaining_percent is None
            else f"{lane.remaining_percent:.0f}% left"
        )
        title = f"{provider_label} · "
        if identity_label:
            title += f"{identity_label} · "
        title += remaining
        detail = f"{lane.label} {remaining} · {format_reset_countdown(lane.reset_at, now=now)}"
        if snapshot.state is ProviderSourceState.STALE:
            # The TITLE is the line that gets read -- this row is the whole
            # glance for most people, and "Codex · 48% left" is a claim
            # about right now. Marking only the detail hid staleness one
            # level down: reported as "am i on the latest version its out
            # of date for codex" against a reading three days old that
            # looked perfectly current here. Applies to every provider,
            # since any of them can go stale.
            marker = _staleness_marker(snapshot)
            title += f" · {marker}"
            detail += f" · {marker}"
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
    if display.show_totals and snapshot.credits_remaining:
        # Banked credits are headroom the meters don't show.
        usage_parts.append(f"{snapshot.credits_remaining:g} credits banked")
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
        snapshot.source_instance_id,
        full_label,
        full_label,
    )


def _snapshot_headroom(snapshot: ProviderUsageSnapshot) -> float | None:
    lane = most_constrained_lane(snapshot)
    return None if lane is None else lane.remaining_percent


def _compact_account_rows(
    snapshots: tuple[ProviderUsageSnapshot, ...],
    rows: tuple[ProviderUsageMenuRow, ...],
    *,
    thresholds: dict[object, float],
    active_instances: frozenset[tuple[str, str]],
) -> tuple[ProviderUsageMenuRow, ...]:
    by_provider: dict[str, list[tuple[ProviderUsageSnapshot, ProviderUsageMenuRow]]] = {}
    provider_order = []
    for snapshot, row in zip(snapshots, rows, strict=True):
        if snapshot.provider_id not in by_provider:
            provider_order.append(snapshot.provider_id)
        by_provider.setdefault(snapshot.provider_id, []).append((snapshot, row))

    result = []
    for provider_id in provider_order:
        group = by_provider[provider_id]
        if len(group) < 4:
            result.extend(row for _snapshot, row in group)
            continue
        ordered = sorted(
            group,
            key=lambda item: (
                _snapshot_headroom(item[0]) is None,
                _snapshot_headroom(item[0])
                if _snapshot_headroom(item[0]) is not None
                else 101.0,
                item[0].source_instance_id,
            ),
        )
        numeric = [item for item in ordered if _snapshot_headroom(item[0]) is not None]
        most_constrained = numeric[0][0].identity if numeric else None
        full = []
        healthy = []
        for snapshot, row in ordered:
            threshold = thresholds.get(snapshot.identity, thresholds.get(provider_id))
            headroom = _snapshot_headroom(snapshot)
            keep_full = (
                snapshot.identity in active_instances
                or snapshot.identity == most_constrained
                or snapshot.action_label is not None
                or snapshot.state not in {ProviderSourceState.READY, ProviderSourceState.STALE}
                or (threshold is not None and headroom is not None and headroom <= threshold)
            )
            (full if keep_full else healthy).append(row)
        result.extend(full)
        if len(healthy) < 2:
            result.extend(healthy)
            continue
        compact_rows = tuple(replace(row, compact=True) for row in healthy)
        provider_label = provider_descriptor(provider_id).label
        count = len(compact_rows)
        full_labels = "; ".join(row.tooltip or row.title for row in compact_rows)
        result.append(
            ProviderUsageMenuRow(
                provider_id=provider_id,
                title=f"{provider_label} · {count} healthy accounts",
                detail="; ".join(row.title for row in compact_rows),
                usage_detail=None,
                action_label=None,
                stale=False,
                source_instance_id=f"collapsed:{provider_id}",
                tooltip=full_labels,
                accessibility_label=(
                    f"{provider_label}, {count} healthy accounts. {full_labels}"
                ),
                compact=True,
                collapsed_count=count,
                collapsed_rows=compact_rows,
            )
        )
    return tuple(result)


def project_usage_menu(
    state: ProviderUsageState,
    *,
    now: float,
    display: MenuUsageDisplay | None = None,
    hidden_providers: frozenset[str] = frozenset(),
    hidden_instances: frozenset[tuple[str, str]] = frozenset(),
    thresholds: dict[object, float] | None = None,
    visual: ProviderInstanceVisualProjection | None = None,
    privacy_mode: bool = False,
    active_instances: frozenset[tuple[str, str]] = frozenset(),
) -> ProviderUsageMenuProjection:
    display = MenuUsageDisplay() if display is None else display
    thresholds = {} if thresholds is None else thresholds
    snapshots = tuple(
        snapshot
        for snapshot in state.snapshots
        if snapshot.provider_id not in hidden_providers
        and snapshot.identity not in hidden_instances
    )
    visible_account_counts: dict[str, int] = {}
    for snapshot in snapshots:
        visible_account_counts[snapshot.provider_id] = (
            visible_account_counts.get(snapshot.provider_id, 0) + 1
        )
    account_positions: dict[str, int] = {}
    positioned_snapshots = []
    for snapshot in snapshots:
        position = account_positions.get(snapshot.provider_id, 0) + 1
        account_positions[snapshot.provider_id] = position
        positioned_snapshots.append((snapshot, position))
    rows = tuple(
        _row(
            snapshot,
            now=now,
            display=display,
            visible_account_count=visible_account_counts[snapshot.provider_id],
            account_position=position,
            threshold=thresholds.get(
                snapshot.identity,
                thresholds.get(snapshot.provider_id),
            ),
            visual=visual,
            privacy_mode=privacy_mode,
        )
        for snapshot, position in positioned_snapshots
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
            constrained.append(
                (
                    lane.remaining_percent,
                    snapshot.provider_id,
                    snapshot,
                    lane,
                )
            )
    constrained.sort(key=lambda item: (item[0], item[1], item[2].source_instance_id))
    if state.refreshing and not state.snapshots:
        title = "Usage · refreshing…"
    elif constrained:
        # The root menu is a command-center summary, not a second Usage
        # Center. Show one decision-worthy value and its reset; detailed
        # provider/account rows remain in the submenu and dedicated window.
        remaining, provider_id, snapshot, lane = constrained[0]
        title = (
            f"Usage · {provider_descriptor(provider_id).label} {remaining:.0f}% · "
            f"{format_reset_countdown(lane.reset_at, now=now)}"
        )
        if snapshot.state is ProviderSourceState.STALE:
            title += f" · {_staleness_marker(snapshot)}"
    elif actionable:
        title = "Usage · setup needed"
    elif state.snapshots:
        title = "Usage"
    else:
        title = "Usage · not collected"
    return ProviderUsageMenuProjection(
        title,
        _compact_account_rows(
            snapshots,
            rows,
            thresholds=thresholds,
            active_instances=active_instances,
        ),
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
    hidden_instances: frozenset[tuple[str, str]] = frozenset(),
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
        if (
            snapshot.provider_id in hidden_providers
            or snapshot.identity in hidden_instances
        ):
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


def glance_summary(state, *, now: float | None = None) -> str:
    """One honest sentence about the provider sources, for the Usage
    settings pane's status line. That line imported this function for
    weeks while it did not exist, so the except-arm's "temporarily
    unavailable" fallback rendered permanently (audit, 2026-08-26)."""
    import time as _time

    projection = project_usage_menu(
        state, now=_time.time() if now is None else float(now)
    )
    counts: dict[str, int] = {}
    for snapshot in getattr(state, "snapshots", ()):
        value = getattr(snapshot.state, "value", str(snapshot.state))
        counts[value] = counts.get(value, 0) + 1
    interesting = {
        key: count
        for key, count in counts.items()
        if key not in ("disabled",)
    }
    if not interesting:
        return projection.title
    parts = ", ".join(
        f"{count} {key.replace('_', ' ')}"
        for key, count in sorted(interesting.items())
    )
    return f"{projection.title} — {parts}"


__all__ = [
    "ProviderUsageMenuProjection",
    "ProviderUsageMenuRow",
    "QuotaGlance",
    "glance_summary",
    "menu_bar_quota_glance",
    "project_usage_menu",
]
