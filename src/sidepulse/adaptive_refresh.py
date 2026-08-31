"""Observable, AppKit-free acceptance contract for adaptive refresh cadence."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .provider_usage_platform import ProviderSourceState, most_constrained_lane

MENU_AGE_LADDER: tuple[tuple[float, float, str], ...] = (
    (300.0, 120.0, "recent_menu"),
    (3600.0, 300.0, "warm_menu"),
    (14400.0, 900.0, "aging_menu"),
)
IDLE_INTERVAL_SECONDS = 1800.0
CONSTRAINED_INTERVAL_SECONDS = 1800.0
RESET_WATCH_WINDOW_SECONDS = 600.0
RESET_WATCH_INTERVAL_SECONDS = 120.0
AMBIENT_VISIBLE_CEILING_SECONDS = 300.0
DEGRADED_INTERVAL_SECONDS = 120.0

_DEGRADED_STATES = frozenset(
    {
        ProviderSourceState.NEEDS_CONSENT,
        ProviderSourceState.NEEDS_SIGN_IN,
        ProviderSourceState.SOURCE_NOT_FOUND,
        ProviderSourceState.ERROR,
        ProviderSourceState.UNAVAILABLE,
    }
)


class AdaptiveRefreshReason(str, Enum):
    CONSTRAINED = "constrained"
    RECENT_MENU = "recent_menu"
    WARM_MENU = "warm_menu"
    AGING_MENU = "aging_menu"
    IDLE = "idle"
    AMBIENT_USAGE = "ambient_usage"
    DEGRADED_SOURCE = "degraded_source"
    RESET_WATCH = "reset_watch"


@dataclass(frozen=True, slots=True)
class AdaptiveRefreshPlan:
    interval_seconds: float
    reason: AdaptiveRefreshReason
    menu_age_seconds: float | None
    constrained: bool
    ambient_usage_visible: bool

    def __post_init__(self) -> None:
        if (
            type(self.interval_seconds) not in {int, float}
            or not math.isfinite(self.interval_seconds)
            or self.interval_seconds <= 0.0
            or type(self.reason) is not AdaptiveRefreshReason
            or type(self.constrained) is not bool
            or type(self.ambient_usage_visible) is not bool
        ):
            raise ValueError("invalid adaptive refresh plan")
        if self.menu_age_seconds is not None and (
            type(self.menu_age_seconds) not in {int, float}
            or not math.isfinite(self.menu_age_seconds)
            or self.menu_age_seconds < 0.0
        ):
            raise ValueError("invalid adaptive refresh menu age")
        object.__setattr__(self, "interval_seconds", float(self.interval_seconds))
        if self.menu_age_seconds is not None:
            object.__setattr__(self, "menu_age_seconds", float(self.menu_age_seconds))


@dataclass(frozen=True, slots=True)
class MenuOpenAdmissionReceipt:
    provider_service_notified: bool
    refresh_planned: bool
    reason: str
    wall_clock: float

    def __post_init__(self) -> None:
        if (
            type(self.provider_service_notified) is not bool
            or type(self.refresh_planned) is not bool
            or self.reason != "menu-open"
            or type(self.wall_clock) not in {int, float}
            or not math.isfinite(self.wall_clock)
            or self.wall_clock < 0.0
        ):
            raise ValueError("invalid menu-open admission receipt")
        object.__setattr__(self, "wall_clock", float(self.wall_clock))


def plan_adaptive_refresh_cadence(
    snapshots,
    *,
    observed_at: float,
    menu_last_opened_at: float | None = None,
    constrained: bool = False,
    ambient_usage_visible: bool = False,
) -> AdaptiveRefreshPlan:
    """Explain the existing cadence without reading clocks or system state."""
    observed = float(observed_at)
    if not math.isfinite(observed):
        raise ValueError("invalid adaptive refresh observation time")
    if constrained:
        return AdaptiveRefreshPlan(
            CONSTRAINED_INTERVAL_SECONDS,
            AdaptiveRefreshReason.CONSTRAINED,
            None,
            True,
            bool(ambient_usage_visible),
        )

    interval = IDLE_INTERVAL_SECONDS
    reason = AdaptiveRefreshReason.IDLE
    menu_age = None
    if menu_last_opened_at is not None:
        opened = float(menu_last_opened_at)
        if not math.isfinite(opened):
            raise ValueError("invalid adaptive refresh menu time")
        menu_age = max(0.0, observed - opened)
        for ceiling, candidate, reason_value in MENU_AGE_LADDER:
            if menu_age <= ceiling:
                interval = candidate
                reason = AdaptiveRefreshReason(reason_value)
                break

    if ambient_usage_visible and interval > AMBIENT_VISIBLE_CEILING_SECONDS:
        interval = AMBIENT_VISIBLE_CEILING_SECONDS
        reason = AdaptiveRefreshReason.AMBIENT_USAGE

    degraded_source = False
    reset_watch = False
    for snapshot in snapshots:
        degraded_source = degraded_source or (
            getattr(snapshot, "state", None) in _DEGRADED_STATES
        )
        lane = most_constrained_lane(snapshot)
        if lane is None or lane.reset_at is None:
            continue
        until_reset = lane.reset_at - observed
        reset_watch = reset_watch or (
            0.0 <= until_reset <= RESET_WATCH_WINDOW_SECONDS
        )

    if interval > RESET_WATCH_INTERVAL_SECONDS:
        if reset_watch:
            interval = RESET_WATCH_INTERVAL_SECONDS
            reason = AdaptiveRefreshReason.RESET_WATCH
        elif degraded_source:
            interval = DEGRADED_INTERVAL_SECONDS
            reason = AdaptiveRefreshReason.DEGRADED_SOURCE

    return AdaptiveRefreshPlan(
        interval,
        reason,
        menu_age,
        False,
        bool(ambient_usage_visible),
    )


def admit_menu_open_refresh(
    controller,
    wall_clock: Callable[[], float] = time.time,
) -> MenuOpenAdmissionReceipt:
    """Notify the usage service and invoke only the refresh admission planner."""
    observed = float(wall_clock())
    service_notified = False
    service = getattr(controller, "_sidepulse_provider_usage_service", None)
    note_menu_opened = getattr(service, "note_menu_opened", None)
    if callable(note_menu_opened):
        try:
            note_menu_opened(now=observed)
            service_notified = True
        except Exception:
            service_notified = False

    refresh_planned = False
    planner = getattr(controller, "maybe_refresh_usage_summary", None)
    if callable(planner):
        planner(reason="menu-open")
        refresh_planned = True
    receipt = MenuOpenAdmissionReceipt(
        service_notified,
        refresh_planned,
        "menu-open",
        observed,
    )
    try:
        controller._sidepulse_adaptive_refresh_visit_receipt = receipt
    except Exception:
        pass
    return receipt


__all__ = [
    "AdaptiveRefreshPlan",
    "AdaptiveRefreshReason",
    "MenuOpenAdmissionReceipt",
    "admit_menu_open_refresh",
    "plan_adaptive_refresh_cadence",
]
