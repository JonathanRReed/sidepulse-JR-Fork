"""Quota Runway LED producer: the tightest lane across visible providers.

The base controller's ``quota_runway_state`` withholds every
capacity-derived LED because the legacy capacity plane never earned
presentation authority. The JR usage plane has: its lanes are gated
(``bindable`` only, READY/STALE sources only) and are the exact numbers
the menu meters, the Usage Center bars, and the Screen Bar quota ember
already trust. This module selects the single WORST ``remaining_percent``
lane over the visible providers -- runway is "how much is left before the
nearest wall", so unlike the ember it does not wait for a lane to sink
below its provider's threshold.

Pure selection lives in :func:`tightest_runway_lane`; the one
controller-shaped seam is :func:`quota_runway_state_for_controller`,
which the provider-usage facade wires in as a thin override.
"""

from __future__ import annotations

from typing import NamedTuple

from .provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
    most_constrained_lane,
    provider_descriptor,
)

#: The renderer's own default fill color, used only when the identity
#: color lookup fails (see quota_runway_program in led_status).
RUNWAY_FALLBACK_COLOR = "#10A37F"

_RUNWAY_STATES = frozenset({ProviderSourceState.READY, ProviderSourceState.STALE})


class QuotaRunwayState(NamedTuple):
    """Indexes 0 and 1 are the exact ``(fraction_left, color)`` pair the
    ``quota_runway_program`` renderer and the LED display claim consume;
    the remaining fields name the lane the fraction came from."""

    fraction_left: float
    color: str
    provider_id: str
    provider_label: str
    lane_label: str
    remaining_percent: float
    reset_at: float | None


def tightest_runway_lane(
    snapshots,
    *,
    hidden_providers: frozenset[str] = frozenset(),
) -> UsageLane | None:
    """The worst remaining_percent lane across visible, gated providers.

    Mirrors the Screen Bar ember's provider gating (hidden providers and
    non-READY/STALE sources are skipped, detail-only lanes never bind)
    but takes the worst lane OVERALL rather than only lanes below their
    threshold. Returns None when no visible lane carries a percent.
    """
    worst_key = None
    worst_lane = None
    for snapshot in snapshots:
        if type(snapshot) is not ProviderUsageSnapshot:
            continue
        if snapshot.provider_id in hidden_providers:
            continue
        if snapshot.state not in _RUNWAY_STATES:
            continue
        lane = most_constrained_lane(snapshot)
        if lane is None or lane.remaining_percent is None:
            continue
        key = (lane.remaining_percent, lane.provider_id, lane.lane_id)
        if worst_key is None or key < worst_key:
            worst_key = key
            worst_lane = lane
    return worst_lane


def runway_state_for_lane(lane: UsageLane | None, *, color: str) -> QuotaRunwayState | None:
    if lane is None or lane.remaining_percent is None:
        return None
    return QuotaRunwayState(
        fraction_left=max(0.0, min(1.0, float(lane.remaining_percent) / 100.0)),
        color=color,
        provider_id=lane.provider_id,
        provider_label=provider_descriptor(lane.provider_id).label,
        lane_label=lane.label,
        remaining_percent=float(lane.remaining_percent),
        reset_at=lane.reset_at,
    )


def quota_runway_state_for_controller(controller) -> QuotaRunwayState | None:
    """The facade seam: JR usage state in, renderer-shaped tuple out.

    Best-effort on the controller reads -- a settings or colors failure
    must degrade to defaults, never take the LED sync down.
    """
    try:
        settings = controller._usage_menu_settings()
        hidden = settings.hidden_menu_providers() if settings is not None else frozenset()
    except Exception:
        hidden = frozenset()
    lane = tightest_runway_lane(
        getattr(controller.provider_usage_state, "snapshots", ()),
        hidden_providers=hidden,
    )
    if lane is None:
        return None
    try:
        color = controller.settings.colors.agent_color(lane.provider_id)
    except Exception:
        color = RUNWAY_FALLBACK_COLOR
    return runway_state_for_lane(lane, color=color)


__all__ = [
    "RUNWAY_FALLBACK_COLOR",
    "QuotaRunwayState",
    "quota_runway_state_for_controller",
    "runway_state_for_lane",
    "tightest_runway_lane",
]
