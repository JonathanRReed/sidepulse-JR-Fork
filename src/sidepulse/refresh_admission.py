"""Pure admission policy for the retained full-refresh compatibility path."""

from __future__ import annotations

from dataclasses import dataclass

from .core_state import CoreDomain, StateDelta


@dataclass(frozen=True, slots=True)
class RefreshAdmission:
    admitted: bool
    reason: str
    update_menu: bool
    update_diagnostics: bool

    def __post_init__(self) -> None:
        if not (
            type(self.admitted) is bool
            and type(self.reason) is str
            and self.reason in {
                "first-observation",
                "urgent",
                "changed",
                "heartbeat",
                "dynamic-display",
                "forced",
                "noop",
            }
            and type(self.update_menu) is bool
            and type(self.update_diagnostics) is bool
        ):
            raise ValueError("invalid refresh admission")


def admit_refresh(
    delta: StateDelta,
    *,
    first_observation: bool,
    heartbeat_due: bool,
    dynamic_display: bool,
    forced: bool,
) -> RefreshAdmission:
    if type(delta) is not StateDelta:
        raise ValueError("invalid refresh delta")
    if forced:
        reason = "forced"
    elif first_observation:
        reason = "first-observation"
    elif delta.urgent:
        reason = "urgent"
    elif delta.changed:
        reason = "changed"
    elif dynamic_display:
        reason = "dynamic-display"
    elif heartbeat_due:
        reason = "heartbeat"
    else:
        reason = "noop"
    admitted = reason != "noop"
    menu_domains = {
        CoreDomain.AGENTS,
        CoreDomain.OPERATOR,
        CoreDomain.ATTENTION,
        CoreDomain.BATTERY,
        CoreDomain.SETTINGS,
        CoreDomain.REMOTE,
        CoreDomain.USAGE,
        CoreDomain.MENU,
        CoreDomain.DEVICES,
    }
    update_menu = bool(
        first_observation
        or forced
        or heartbeat_due
        or delta.changed_domains.intersection(menu_domains)
    )
    return RefreshAdmission(
        admitted=admitted,
        reason=reason,
        update_menu=update_menu,
        update_diagnostics=bool(first_observation or forced or delta.changed),
    )
