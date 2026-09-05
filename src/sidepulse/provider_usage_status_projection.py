"""Cached provider-usage projections for compact status surfaces."""

from __future__ import annotations

import time


def screen_bar_quota_ember_level(controller) -> float:
    try:
        from .provider_usage_platform import ProviderSourceState, most_constrained_lane

        settings = controller._usage_menu_settings()
        if settings is None:
            return 0.0
        hidden = settings.hidden_menu_providers()
        hidden_instances = settings.hidden_menu_instances()
        thresholds = {preference.identity: preference.threshold_remaining for preference in settings.providers}
        worst = 0.0
        for snapshot in controller.provider_usage_state.snapshots:
            if snapshot.provider_id in hidden or snapshot.identity in hidden_instances:
                continue
            if snapshot.state not in {ProviderSourceState.READY, ProviderSourceState.STALE}:
                continue
            lane = most_constrained_lane(snapshot)
            if lane is None or lane.remaining_percent is None:
                continue
            threshold = thresholds.get(snapshot.identity, 20.0)
            if threshold > 0.0 and lane.remaining_percent <= threshold:
                worst = max(worst, 1.0 - lane.remaining_percent / threshold)
        return max(0.0, min(1.0, worst))
    except Exception:
        return 0.0


def capacity_settings_text(controller, provider_id, *, wall_clock=time.time):
    state = getattr(controller, "_sidepulse_provider_usage_state", None)
    snapshot = next(
        (row for row in getattr(state, "snapshots", ()) if row.provider_id == provider_id),
        None,
    )
    if snapshot is None or not snapshot.lanes:
        return None
    from .provider_usage_qol import format_reset_countdown

    now = float(wall_clock())
    parts = []
    for lane in snapshot.lanes[:3]:
        part = lane.label if lane.remaining_percent is None else f"{lane.label} {lane.remaining_percent:.0f}% left"
        if lane.reset_at:
            part += f" · {format_reset_countdown(lane.reset_at, now=now)}"
        parts.append(part)
    if not parts:
        return None
    age_minutes = max(0, int((now - snapshot.observed_at) // 60))
    checked = "just checked" if age_minutes < 1 else f"checked {age_minutes}m ago"
    state_note = "" if snapshot.state.value == "ready" else f" · {snapshot.state.value}"
    return " · ".join(parts) + f" · {checked}{state_note}"


def active_usage_providers(controller, legacy) -> frozenset[str]:
    snapshot = getattr(controller, "last_snapshot", None)
    if snapshot is None:
        return frozenset()
    busy = {
        legacy.AgentMode.WORKING,
        legacy.AgentMode.TOOL_RUNNING,
        legacy.AgentMode.LONG_TASK_PROGRESS,
    }
    return frozenset(status.provider for status in snapshot.statuses if not status.is_subagent and status.mode in busy)


def active_usage_instances(controller, legacy) -> frozenset[tuple[str, str]]:
    snapshot = getattr(controller, "last_snapshot", None)
    if snapshot is None:
        return frozenset()
    busy = {
        legacy.AgentMode.WORKING,
        legacy.AgentMode.TOOL_RUNNING,
        legacy.AgentMode.LONG_TASK_PROGRESS,
    }
    available = {item.identity for item in controller.provider_usage_state.snapshots}
    active = set()
    for status in snapshot.statuses:
        if status.is_subagent or status.mode not in busy:
            continue
        work_key = getattr(status, "work_key", None)
        source_key = getattr(work_key, "source_key", None)
        identity = (
            status.provider,
            str(getattr(source_key, "source_instance_id", "default")),
        )
        if identity in available:
            active.add(identity)
    return frozenset(active)


def append_quota_to_status_title(controller, *, wall_clock=time.time) -> None:
    from .provider_usage_menu import menu_bar_quota_glance

    settings = controller._usage_menu_settings()
    if settings is None or not settings.menu_display.show_menu_bar_percent:
        return
    item = getattr(controller, "status_item", None)
    button = item.button() if item is not None else None
    if button is None:
        return
    glance = menu_bar_quota_glance(
        controller.provider_usage_state,
        hidden_providers=settings.hidden_menu_providers(),
        hidden_instances=settings.hidden_menu_instances(),
        active_providers=controller._active_usage_providers(),
        now=float(wall_clock()),
    )
    if glance is None:
        return
    title = str(button.title() or "")
    if glance.text in title:
        return
    prefix = f"{title} · " if title.strip() else " "
    full = f"{prefix}{glance.text}"
    button.setTitle_(full)
    color_name = {
        "fast": "systemOrangeColor",
        "critical": "systemRedColor",
        "out": "systemRedColor",
    }.get(glance.verdict or "")
    if color_name is None:
        return
    try:
        from AppKit import (
            NSColor,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSMutableAttributedString,
        )

        styled = NSMutableAttributedString.alloc().initWithString_attributes_(
            full,
            {
                NSForegroundColorAttributeName: NSColor.labelColor(),
                NSFontAttributeName: button.font(),
            },
        )
        styled.addAttribute_value_range_(
            NSForegroundColorAttributeName,
            getattr(NSColor, color_name)(),
            (len(prefix), len(glance.text)),
        )
        button.setAttributedTitle_(styled)
    except Exception:
        pass


def provider_usage_why_panel_body(controller, body: str, *, wall_clock=time.time) -> str:
    from .provider_usage_menu import project_usage_menu

    settings = controller._usage_menu_settings()
    privacy_mode = False if settings is None else settings.menu_display.privacy_mode
    policies = getattr(controller, "_sidepulse_provider_instance_policies", None)
    projection = project_usage_menu(
        controller.provider_usage_state,
        now=float(wall_clock()),
        visual=getattr(policies, "visual", None),
        privacy_mode=privacy_mode,
    )
    lines = ["Native provider usage", projection.title]
    for row in projection.rows:
        lines.append(f"  {row.title}")
        if row.action_label:
            lines.append(f"    Action: {row.action_label}")
    return f"{body}\n\n" + "\n".join(lines)


__all__ = [
    "active_usage_instances",
    "active_usage_providers",
    "append_quota_to_status_title",
    "capacity_settings_text",
    "provider_usage_why_panel_body",
    "screen_bar_quota_ember_level",
]
