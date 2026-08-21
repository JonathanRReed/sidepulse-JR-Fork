"""The Settings window's construction — every pane builder, the
window assembly, and their private helpers — extracted from
status_bar.py (backlog #14: ~2,300 lines of module-level functions
with target/snapshot passed explicitly, so the cut is import-shuffling).

Namespace contract: this module never imports status_bar (so no import
cycle can exist in either direction). Instead status_bar, at the end
of its own module body, calls _install() with its complete namespace
-- the moved code keeps referencing shared helpers, constants, AppKit
symbols and sibling modules exactly as it did in place — and then
re-imports every public name defined here, so controller methods,
tests, and external callers keep addressing status_bar.<name>.
Do not import this module without importing status_bar first.
"""

from __future__ import annotations

# The only import-time dependencies: module-level constants below use
# these as dict keys, evaluated before _install() runs. They come from
# settings.py, which never imports status_bar — still no cycle.
# objc/Foundation are imported here rather than inherited from
# status_bar's namespace because SidePulseStudioActions is defined at
# module scope, i.e. before _install() has run.
import time
from typing import Final

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSButtonTypeRadio,
    NSClickGestureRecognizer,
    NSColor,
    NSFont,
    NSImage,
    NSImageView,
    NSLayoutAttributeCenterX,
    NSLayoutAttributeTop,
    NSLayoutConstraint,
    NSLayoutConstraintOrientationHorizontal,
    NSScreen,
    NSSegmentedControl,
    NSSegmentSwitchTrackingSelectOne,
    NSSplitView,
    NSSplitViewDividerStyleThin,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject

# calendar_watch/reminders_watch import EventKit lazily, inside their own
# helpers — importing the modules here costs nothing at start-up and is
# what lets the Extras pane read a permission without owning EventKit.
from . import calendar_watch, display_brightness, reminders_watch, remote_peers
from . import colors as colors_module
from .alcove_observation import (
    ALCOVE_STATUS_MAX_AGE_SECONDS,
    ALCOVE_STATUS_MESSAGES,
    AlcoveCaptureStatus,
    alcove_follow_blocker,
    latest_alcove_status,
    request_screen_recording_access,
    reset_alcove_status,
)
from .colors import ANIMATION_MODE_KEYS, MODE_ROW_LABELS, matching_preset
from .led_status import ANIMATION_STYLE_CHOICES, program_for_display_state
from .operator_accessibility import normalize_semantic_text_scale
from .provider_capacity import CapacityPolicyState, provider_capacity_policies
from .session_actions import provider_session_opener_providers
from .settings import (
    LID_ANIMATION_CLOSED,
    LID_ANIMATION_CLOSED_ACTIVE,
    LID_ANIMATION_OPEN,
    LID_ANIMATION_OPEN_ACTIVE,
)

# _install() only fills names this module does not already define, so an
# explicit import here wins over status_bar's namespace injection — and
# these three are new, which status_bar does not re-export.
from .virtual_device import (
    LED_COUNT,
    WINDOW_WIDTH,
    ScreenBarWingState,
    screen_bar_wing_state,
    slot_width_for_screen,
)

# What the Bar Size slider shows when the screen cannot be measured. Read
# from the geometry module so it is the SAME number Automatic would use.
SCREEN_BAR_AUTOMATIC_GAP_FALLBACK = WINDOW_WIDTH

# Where macOS keeps the Screen Recording list. Same shape as the Full Disk
# Access link the Focus pane already offers.
SCREEN_RECORDING_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)

OPERATOR_HISTORY_FIELD_MANIFEST: tuple[str, ...] = (
    "day_key",
    "timezone_offset_minutes",
    "provider_id",
    "started",
    "needs_user",
    "completed",
    "failed",
    "acknowledged",
    "active_duration_bands",
    "attention_wait_bands",
    "primary_count",
    "worker_count",
    "source_recoveries",
    "device_recoveries",
    "coverage",
    "sample_count",
)


def _make_history_radio_group(
    target,
    choices: tuple[tuple[str, int], ...],
    *,
    selector: str,
    selected: int,
    label: str,
    scale: float,
):
    group = native_ui.make_stack(
        orientation="vertical" if scale >= 1.75 else "horizontal",
        spacing=native_ui.SPACE_S,
    )
    group.setAccessibilityLabel_(label)
    group.setAccessibilityRole_("AXRadioGroup")
    controls: dict[int, object] = {}
    for title, value in choices:
        button = NSButton.alloc().init()
        button.setButtonType_(NSButtonTypeRadio)
        button.setTitle_(title)
        button.setTarget_(target)
        button.setAction_(selector)
        button.setRepresentedObject_(value)
        button.setState_(1 if value == selected else 0)
        button.setAccessibilityLabel_(title)
        button.setFont_(NSFont.systemFontOfSize_(13.0 * scale))
        if button.accessibilityRole() == "AXUnknown":
            button.setAccessibilityRole_("AXRadioButton")
        controls[value] = button
        group.addArrangedSubview_(button)
    return group, controls


def _build_history_pane(target: StatusBarController):
    scale = normalize_semantic_text_scale(
        getattr(target, "semantic_text_scale_percent", 100)
    )
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)

    retention_outer, retention_inner = native_ui.make_card("Private History")
    disclosure = native_ui.make_wrapping_label(
        "Before enabling, SidePulse stores derived metadata only for the "
        "selected retention period. It does not store prompts, messages, "
        "responses, commands, titles, paths, raw errors, or navigation targets.",
        secondary=True,
        size=11.0 * scale,
        max_width=560.0,
    )
    disclosure.setAccessibilityLabel_("History retention disclosure")
    retention_inner.addArrangedSubview_(disclosure)
    retention_group, retention_controls = _make_history_radio_group(
        target,
        (("Off", 0), ("7 Days", 7), ("30 Days", 30), ("90 Days", 90)),
        selector="changeOperatorHistoryRetention:",
        selected=target.settings.operator_history_retention_days,
        label="History retention",
        scale=scale,
    )
    retention_inner.addArrangedSubview_(retention_group)
    stack.addArrangedSubview_(retention_outer)

    observation_outer, observation_inner = native_ui.make_card("Observation")
    range_group, range_controls = _make_history_radio_group(
        target,
        (("Day", 1), ("7 Day", 7), ("30 Day", 30)),
        selector="changeOperatorHistoryRange:",
        selected=getattr(target, "operator_history_range_days", 1),
        label="History range",
        scale=scale,
    )
    observation_inner.addArrangedSubview_(range_group)
    health = native_ui.make_wrapping_label(
        "No Observation",
        secondary=False,
        size=13.0 * scale,
        max_width=560.0,
    )
    health.setAccessibilityLabel_("History health")
    summary = native_ui.make_wrapping_label(
        "No operator history was observed in this range.",
        secondary=True,
        size=12.0 * scale,
        max_width=560.0,
    )
    summary.setAccessibilityLabel_("History summary")
    observation_inner.addArrangedSubview_(health)
    observation_inner.addArrangedSubview_(summary)
    stack.addArrangedSubview_(observation_outer)

    fields_outer, fields_inner = native_ui.make_card("Stored Fields")
    manifest = native_ui.make_wrapping_label(
        ", ".join(OPERATOR_HISTORY_FIELD_MANIFEST),
        secondary=True,
        size=11.0 * scale,
        max_width=560.0,
    )
    manifest.setAccessibilityLabel_("Stored history fields")
    fields_inner.addArrangedSubview_(manifest)
    stack.addArrangedSubview_(fields_outer)

    reel_outer, reel_inner = native_ui.make_card("Current Run")
    reel_copy = native_ui.make_wrapping_label(
        "Current-run semantic events only. Up to 50 product-vocabulary "
        "rows are kept in memory and are never persisted or exported.",
        secondary=True,
        size=11.0 * scale,
        max_width=560.0,
    )
    reel = native_ui.make_wrapping_label(
        "No current-run events.",
        secondary=False,
        size=11.0 * scale,
        max_width=560.0,
    )
    reel.setAccessibilityLabel_("Current-run semantic events")
    reel_inner.addArrangedSubview_(reel_copy)
    reel_inner.addArrangedSubview_(reel)
    stack.addArrangedSubview_(reel_outer)

    export_outer, export_inner = native_ui.make_card("Local Export")
    export_preview = native_ui.make_wrapping_label(
        "History: sidepulse-history.json, up to 2 MiB. Diagnostics: "
        "sidepulse-diagnostics.json, up to 512 KiB. Each export is a "
        "separate local file. History contains retention_days and the "
        "stored fields above. Diagnostics contains app_version, build_trust, "
        "provider_health_counts, delivery_disposition_counts, "
        "device_health_counts, and history_health. No upload or sharing "
        "route is used.",
        secondary=True,
        size=11.0 * scale,
        max_width=560.0,
    )
    export_inner.addArrangedSubview_(export_preview)
    export_buttons = native_ui.make_stack(
        orientation="horizontal",
        spacing=native_ui.SPACE_S,
    )
    history_export = native_ui.make_button(
        "Export History",
        target,
        "exportOperatorHistory:",
    )
    diagnostics_export = native_ui.make_button(
        "Export Diagnostics",
        target,
        "exportOperatorDiagnostics:",
    )
    for button in (history_export, diagnostics_export):
        button.setFont_(NSFont.systemFontOfSize_(13.0 * scale))
        if button.accessibilityRole() == "AXUnknown":
            button.setAccessibilityRole_("AXButton")
    export_buttons.addArrangedSubview_(history_export)
    export_buttons.addArrangedSubview_(diagnostics_export)
    export_buttons.addArrangedSubview_(native_ui.make_hspacer())
    export_inner.addArrangedSubview_(export_buttons)
    stack.addArrangedSubview_(export_outer)

    clear_outer, clear_inner = native_ui.make_card("Clear History")
    clear_copy = native_ui.make_wrapping_label(
        "Clear removes only SidePulse operator-history aggregates. It does "
        "not clear mailbox preferences, capacity observations, or settings.",
        secondary=True,
        size=11.0 * scale,
        max_width=560.0,
    )
    clear_button = native_ui.make_button(
        "Clear History",
        target,
        "clearOperatorHistory:",
    )
    clear_button.setFont_(NSFont.systemFontOfSize_(13.0 * scale))
    if clear_button.accessibilityRole() == "AXUnknown":
        clear_button.setAccessibilityRole_("AXButton")
    operation_status = native_ui.make_wrapping_label(
        getattr(target, "_operator_history_operation_status", "") or "No history operation in progress.",
        secondary=True,
        size=11.0 * scale,
        max_width=560.0,
    )
    operation_status.setAccessibilityLabel_("History operation status")
    if operation_status.accessibilityRole() == "AXUnknown":
        operation_status.setAccessibilityRole_("AXStaticText")
    clear_inner.addArrangedSubview_(clear_copy)
    clear_inner.addArrangedSubview_(clear_button)
    clear_inner.addArrangedSubview_(operation_status)
    stack.addArrangedSubview_(clear_outer)

    keyboard_order = (
        *retention_controls.values(),
        *range_controls.values(),
        history_export,
        diagnostics_export,
        clear_button,
    )
    for current, following in zip(
        keyboard_order,
        (*keyboard_order[1:], keyboard_order[0]),
        strict=True,
    ):
        current.setNextKeyView_(following)
    fields = {
        "history_retention_disclosure": disclosure,
        "history_retention_group": retention_group,
        "history_retention_controls": retention_controls,
        "history_range_group": range_group,
        "history_range_controls": range_controls,
        "history_health": health,
        "history_summary": summary,
        "history_field_manifest": manifest,
        "history_semantic_reel": reel,
        "history_export_preview": export_preview,
        "history_operation_status": operation_status,
        "history_keyboard_order": keyboard_order,
    }
    buttons = {
        "export_history": history_export,
        "export_diagnostics": diagnostics_export,
        "clear_history": clear_button,
    }
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


_PROVIDER_FAMILY_LABELS: dict[str, str] = {
    "anthropic": "Anthropic",
    "claude": "Claude",
    "codex": "Codex",
    "cursor": "Cursor",
    "devin": "Devin and Windsurf",
    "github": "GitHub",
    "google": "Google",
    "grok": "Grok",
    "hermes": "Hermes",
    "openai": "OpenAI",
    "openclaw": "OpenClaw",
    "opencode": "OpenCode",
}

_CAPACITY_PROFILE_LABELS: dict[str, str] = {
    "openai-codex-consumer": "ChatGPT and Codex",
    "openai-api-organization": "OpenAI API",
    "anthropic-consumer": "Claude consumer plan",
    "anthropic-team-enterprise": "Claude Team and Enterprise",
    "anthropic-console-api": "Anthropic Console API",
    "google-gemini-code-assist": "Gemini Code Assist",
    "google-antigravity": "Google Antigravity",
    "google-cloud-api": "Google Cloud API",
    "github-copilot": "GitHub Copilot",
    "cursor-team-organization": "Cursor Team and Organization",
    "devin-windsurf-team": "Devin and Windsurf teams",
    "opencode-upstream-delegation": "OpenCode",
}


def _installed_surface_state_copy(registration) -> str:
    if registration.support in {
        SurfaceSupportLevel.FULL,
        SurfaceSupportLevel.LIFECYCLE,
    }:
        return "Not detected · Monitoring available"
    if registration.support is SurfaceSupportLevel.CAPACITY:
        return "Not detected · Capacity available"
    if registration.support is SurfaceSupportLevel.INVENTORY:
        return "Not detected · Detection only"
    return "Not detected · Unsupported"


def _build_installed_agents_pane(target: StatusBarController):
    scale = normalize_semantic_text_scale(
        getattr(target, "semantic_text_scale_percent", 100)
    )
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    overview_outer, overview_inner = native_ui.make_card("Installed Coding Agents")
    intro = native_ui.make_wrapping_label(
        "SidePulse checks a bounded list of reviewed app, extension, and command "
        "markers only while this pane is visible. Detection never starts an agent, "
        "reads its conversations, or adds it to the live status menu.",
        secondary=True,
        size=11.0 * scale,
        max_width=560.0,
    )
    intro.setAccessibilityLabel_("Installed coding agents privacy summary")
    refresh_status = native_ui.make_wrapping_label(
        "Open this pane to check installed coding agents.",
        secondary=True,
        size=11.0 * scale,
        max_width=560.0,
    )
    refresh_status.setAccessibilityLabel_("Installed coding agents refresh status")
    refresh = native_ui.make_button("Refresh", target, "refreshInstalledAgents:")
    refresh.setAccessibilityLabel_("Refresh installed coding agents")
    refresh.setFont_(NSFont.systemFontOfSize_(13.0 * scale))
    refresh_row = native_ui.make_stack(
        orientation="horizontal", spacing=native_ui.SPACE_S
    )
    refresh_row.addArrangedSubview_(refresh_status)
    refresh_row.addArrangedSubview_(native_ui.make_hspacer())
    refresh_row.addArrangedSubview_(refresh)
    overview_inner.addArrangedSubview_(intro)
    overview_inner.addArrangedSubview_(refresh_row)
    stack.addArrangedSubview_(overview_outer)

    status_fields: dict[tuple[str, str], object] = {}
    grouped: dict[str, list[object]] = {}
    for registration in installed_surface_registrations():
        grouped.setdefault(registration.provider_id, []).append(registration)
    for provider_id, registrations in grouped.items():
        outer, inner = native_ui.make_card(
            _PROVIDER_FAMILY_LABELS.get(provider_id, provider_id.title())
        )
        for registration in registrations:
            status = native_ui.make_wrapping_label(
                _installed_surface_state_copy(registration),
                secondary=True,
                size=11.0 * scale,
                max_width=310.0,
            )
            status.setAccessibilityLabel_(f"{registration.label} status")
            status_fields[(registration.provider_id, registration.surface_id)] = status
            inner.addArrangedSubview_(
                native_ui.make_row(
                    registration.label,
                    status,
                    help_text=(
                        "Product-owned inventory state only. Paths, commands, account "
                        "labels, prompts, and provider errors are never shown."
                    ),
                )
            )
        stack.addArrangedSubview_(outer)

    fields = {
        "installed_agents_intro": intro,
        "installed_agents_refresh_status": refresh_status,
        "installed_agent_status_fields": status_fields,
    }
    buttons = {"refresh_installed_agents": refresh}
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


def _capacity_policy_copy(policy, active_sources: frozenset) -> tuple[str, str]:
    if policy.state is CapacityPolicyState.OBSERVABLE:
        if policy.source not in active_sources:
            return (
                "Setup required",
                "The reviewed source is not active in this build. No provider work runs.",
            )
        # An observable source can still require consent. Saying "without an
        # account API key" about a read that presents the user's own
        # subscription credential would be true and still misleading.
        if policy.opt_in_required:
            return (
                "Available after opt-in",
                "Reads this subscription's own usage endpoint with the credential "
                "its app already stores, once you turn it on.",
            )
        return (
            "Available locally",
            "SidePulse can refresh this exact source without an account API key.",
        )
    if policy.state is CapacityPolicyState.DETAIL_ONLY:
        return (
            "Permission required",
            "Available only after an explicit opt-in to an official account API.",
        )
    if policy.state is CapacityPolicyState.LINK_ONLY:
        return (
            "Check provider",
            "SidePulse does not read browser sessions or private provider endpoints.",
        )
    if policy.state is CapacityPolicyState.UPSTREAM_DELEGATED:
        return (
            "Uses model provider",
            "OpenCode capacity belongs to its configured model provider.",
        )
    return ("Not observable", "No trustworthy provider capacity source is available.")


def _build_capacity_pane(target: StatusBarController):
    scale = normalize_semantic_text_scale(
        getattr(target, "semantic_text_scale_percent", 100)
    )
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)

    live_outer, live_inner = native_ui.make_card("Current Capacity")
    intro = native_ui.make_wrapping_label(
        "Capacity is provider-specific. SidePulse shows only exact sources it can "
        "bind safely, keeps API billing separate from subscription limits, and "
        "never reads browser cookies or private provider endpoints.",
        secondary=True,
        size=11.0 * scale,
        max_width=560.0,
    )
    intro.setAccessibilityLabel_("Capacity privacy and authority summary")
    codex = native_ui.make_wrapping_label(
        getattr(target, "codex_summary_text", None) or "Not observed yet",
        secondary=True,
        size=11.0 * scale,
        max_width=340.0,
    )
    claude = native_ui.make_wrapping_label(
        getattr(target, "claude_plan_text", None) or "Not observed yet",
        secondary=True,
        size=11.0 * scale,
        max_width=340.0,
    )
    codex.setAccessibilityLabel_("Codex capacity status")
    claude.setAccessibilityLabel_("Claude capacity status")
    live_inner.addArrangedSubview_(intro)
    live_inner.addArrangedSubview_(native_ui.make_row("Codex", codex))
    live_inner.addArrangedSubview_(native_ui.make_row("Claude", claude))
    refresh = native_ui.make_button("Refresh Capacity", target, "refreshCapacitySources:")
    refresh.setAccessibilityLabel_("Refresh available capacity sources")
    refresh.setFont_(NSFont.systemFontOfSize_(13.0 * scale))
    refresh_row = native_ui.make_stack(
        orientation="horizontal", spacing=native_ui.SPACE_S
    )
    refresh_row.addArrangedSubview_(refresh)
    refresh_row.addArrangedSubview_(native_ui.make_hspacer())
    live_inner.addArrangedSubview_(refresh_row)
    stack.addArrangedSubview_(live_outer)

    active_sources = frozenset(
        source.source_key
        for source in negotiated_provider_sources()
        if source.source_key.capability_id == "remote_quota_windows"
    )
    policy_fields: dict[str, object] = {}
    grouped: dict[str, list[object]] = {}
    for policy in provider_capacity_policies():
        grouped.setdefault(policy.provider_id, []).append(policy)
    for provider_id, policies in grouped.items():
        outer, inner = native_ui.make_card(
            _PROVIDER_FAMILY_LABELS.get(provider_id, provider_id.title())
        )
        for policy in policies:
            state, detail = _capacity_policy_copy(policy, active_sources)
            lane_copy = ", ".join(lane.semantic_name for lane in policy.lanes)
            rendered_detail = (
                f"{state}. {detail} Windows: {lane_copy}."
                if lane_copy
                else f"{state}. {detail}"
            )
            status = native_ui.make_wrapping_label(
                rendered_detail,
                secondary=True,
                size=11.0 * scale,
                max_width=340.0,
            )
            label = _CAPACITY_PROFILE_LABELS.get(
                policy.profile_id, policy.profile_id.replace("_", " ").title()
            )
            status.setAccessibilityLabel_(f"{label} capacity status")
            policy_fields[policy.profile_id] = status
            inner.addArrangedSubview_(
                native_ui.make_row(
                    label,
                    status,
                    help_text=(
                        "This row describes product support only. It never starts "
                        "provider or network work by appearing on screen."
                    ),
                )
            )
        stack.addArrangedSubview_(outer)

    fields = {
        "capacity_intro": intro,
        "capacity_live_fields": {"codex": codex, "claude": claude},
        "capacity_policy_status_fields": policy_fields,
    }
    buttons = {"refresh_capacity": refresh}
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


def _install(status_bar_namespace: dict) -> None:
    """Bind status_bar's module namespace as ours — called exactly
    once, from the bottom of status_bar.py."""
    for key, value in status_bar_namespace.items():
        if not key.startswith("__") and key not in globals():
            globals()[key] = value

def _build_profile_pane(target: StatusBarController):
    """Local usage over one explicit, user-selected period and metric."""
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    fields: dict[str, object] = {}

    today_outer, today_inner = native_ui.make_card("Usage")
    period_label = native_ui.make_label(
        usage_stats.usage_period_label(target.settings.usage_graph_days),
        secondary=True,
        size=11.0,
    )
    today_inner.addArrangedSubview_(period_label)
    fields["profile_usage_period_label"] = period_label
    usage_summary = getattr(target, "usage_summary_text", None)
    usage_label = native_ui.make_label(
        usage_summary
        or (
            "No Claude activity in this period."
            if getattr(target, "_usage_local_scan_complete", False)
            else "Loading local usage history…"
        ),
        size=13.0,
    )
    today_inner.addArrangedSubview_(usage_label)
    fields["profile_usage_label"] = usage_label
    detail_label = native_ui.make_label(
        getattr(target, "usage_detail_text", None) or "", secondary=True, size=11.0
    )
    today_inner.addArrangedSubview_(detail_label)
    fields["profile_usage_detail"] = detail_label
    codex_label = native_ui.make_label(
        getattr(target, "codex_summary_text", None) or "", secondary=False, size=13.0
    )
    today_inner.addArrangedSubview_(codex_label)
    fields["profile_codex_label"] = codex_label
    graph = UsageGraphView.alloc().initWithFrame_(((0, 0), (560.0, 180.0)))
    graph.setTranslatesAutoresizingMaskIntoConstraints_(False)
    native_ui.constrain_width(graph, 560.0)
    native_ui.constrain_height(graph, 180.0)
    today_inner.addArrangedSubview_(graph)
    graph.setModel_(getattr(target, "usage_graph_model", None) or {})
    fields["profile_usage_graph"] = graph
    range_popup = native_ui.make_popup_button(target, "setUsageGraphRange:")
    for range_label, range_days in (
        ("7 days", 7),
        ("30 days", 30),
        ("90 days", 90),
        ("Year", 365),
    ):
        range_popup.addItemWithTitle_(range_label)
        item = range_popup.lastItem()
        item.setRepresentedObject_(range_days)
        if range_days == target.settings.usage_graph_days:
            range_popup.selectItem_(item)
    fields["usage_graph_range_popup"] = range_popup
    range_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    range_row.addArrangedSubview_(range_popup)
    mode_popup = native_ui.make_popup_button(target, "setUsageDisplayMode:")
    for mode_label, mode_key in (
        ("Processed tokens", "tokens"),
        ("Estimated cost", "cost"),
        ("Sessions", "sessions"),
    ):
        mode_popup.addItemWithTitle_(mode_label)
        item = mode_popup.lastItem()
        item.setRepresentedObject_(mode_key)
        if mode_key == target.settings.usage_display_mode:
            mode_popup.selectItem_(item)
    range_row.addArrangedSubview_(mode_popup)
    range_row.addArrangedSubview_(native_ui.make_hspacer())
    today_inner.addArrangedSubview_(range_row)
    selected_providers = target.settings.usage_graph_providers
    legend = native_ui.make_label(
        " · ".join(provider_id.title() for provider_id in selected_providers)
        + " · one shared metric and zero baseline",
        secondary=True,
        size=10.0,
    )
    today_inner.addArrangedSubview_(legend)
    fields["profile_usage_legend"] = legend
    provider_row = native_ui.make_stack(
        orientation="horizontal",
        spacing=native_ui.SPACE_M,
    )
    provider_switches = {}
    for provider_id, provider_label in (("claude", "Claude"), ("codex", "Codex")):
        row, switch = native_ui.make_switch_row(
            provider_label,
            target,
            "toggleUsageGraphProvider:",
            help_text="Show this supported local usage source in the graph.",
        )
        switch.setIdentifier_(provider_id)
        switch.setState_(1 if provider_id in selected_providers else 0)
        provider_row.addArrangedSubview_(row)
        provider_switches[provider_id] = switch
    provider_row.addArrangedSubview_(native_ui.make_hspacer())
    today_inner.addArrangedSubview_(provider_row)
    fields["usage_graph_provider_switches"] = provider_switches
    today_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "All supported local usage sources are selected by default. "
            "Additional installed agents appear here only when SidePulse has "
            "a trustworthy, bounded usage source for them.",
            secondary=True,
            size=10.0,
            max_width=560.0,
        )
    )
    plan_label = native_ui.make_label(
        getattr(target, "claude_plan_text", None) or "", secondary=False, size=13.0
    )
    today_inner.addArrangedSubview_(plan_label)
    fields["profile_plan_label"] = plan_label
    # Was a static "Not supported" row. The consumer policy declares real
    # lanes now, so the honest control is the opt-in itself — and without a
    # switch the toggle action had no way for the user to reach it.
    plan_limits_row, plan_limits_switch = native_ui.make_switch_row(
        "Show Claude plan limits",
        target,
        "toggleClaudePlanLimits:",
        help_text=(
            "Reads your Claude subscription's own usage endpoint using the "
            "credential Claude Code already stores, and shows every window it "
            "reports — the 5-hour and weekly ceilings and the weekly Opus and "
            "Sonnet sub-caps. Off until you turn it on. SidePulse never reads "
            "browser sessions or private provider endpoints."
        ),
    )
    plan_limits_switch.setState_(
        1 if target.settings.claude_plan_limits_enabled else 0
    )
    fields["profile_plan_limits_switch"] = plan_limits_switch
    today_inner.addArrangedSubview_(plan_limits_row)
    codex_pct_row, codex_pct_switch = native_ui.make_switch_row(
        "Show Codex rate-limit percent",
        target,
        "toggleCodexPercent:",
        help_text="The current rate-limit window percent on the Codex line.",
    )
    codex_pct_switch.setState_(1 if target.settings.codex_percent_enabled else 0)
    fields["profile_codex_pct_switch"] = codex_pct_switch
    today_inner.addArrangedSubview_(codex_pct_row)
    today_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Costs use Anthropic list rates; cached reads bill at a tenth "
            "of the uncached rate — \u201csaved with caching\u201d is that "
            "difference. Counted once per message, so resumed sessions "
            "never double-count.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    stack.addArrangedSubview_(today_outer)

    about_outer, about_inner = native_ui.make_card("About")
    try:
        from importlib.metadata import version as _pkg_version

        app_version = _pkg_version("sidepulse")
    except Exception:
        app_version = "dev"
    about_inner.addArrangedSubview_(
        native_ui.make_label(f"SidePulse {app_version}", size=13.0)
    )
    about_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Your agents, at a glance \u2014 as light. Watches Claude Code, "
            "Codex, Devin and friends; shows their state on SidePulse LED "
            "devices and the Screen Bar.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    link_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    link_row.addArrangedSubview_(
        native_ui.make_button("Project Page", target, "openProjectPage:")
    )
    link_row.addArrangedSubview_(native_ui.make_hspacer())
    about_inner.addArrangedSubview_(link_row)
    stack.addArrangedSubview_(about_outer)

    return native_ui.wrap_in_scroll_pane(stack), fields


def _build_devices_pane(target: StatusBarController):
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    devices = target.status_bar_devices(remember=False)
    device_controls: dict[str, dict[str, object]] = {}
    if not devices:
        outer, inner = native_ui.make_card("Devices")
        inner.addArrangedSubview_(
            native_ui.make_wrapping_label(
                "No devices yet. Plug a SidePulse into any USB port and it "
                "appears here by itself — or start with the on-screen bar:",
                secondary=True,
                size=12.0,
                max_width=560.0,
            )
        )
        empty_cta = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
        empty_cta.addArrangedSubview_(
            native_ui.make_button(
                "Add the Screen Bar", target, "toggleVirtualStatusDevice:"
            )
        )
        empty_cta.addArrangedSubview_(native_ui.make_hspacer())
        inner.addArrangedSubview_(empty_cta)
        stack.addArrangedSubview_(outer)
    if devices:
        # One global timer length for the Working-timer display.
        timer_outer, timer_inner = native_ui.make_card("Working Timer")
        timer_field = native_ui.make_field(
            f"{target.settings.timer_expected_minutes:g}",
            target=target,
            action="applyTimerMinutes:",
        )
        native_ui.constrain_width(timer_field, 52.0)
        timer_controls = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_XS)
        timer_controls.addArrangedSubview_(timer_field)
        timer_controls.addArrangedSubview_(native_ui.make_label("minutes expected", secondary=True))
        timer_inner.addArrangedSubview_(
            native_ui.make_row(
                "Fill completes after",
                timer_controls,
                help_text=(
                    "Devices set to Working timer fill light up as the "
                    "oldest working agent's elapsed time crosses this."
                ),
            )
        )
        stack.addArrangedSubview_(timer_outer)
        target.timer_minutes_field = timer_field
    for device in devices:
        outer, inner = native_ui.make_card(device.name)

        brightness_slider = native_ui.make_slider(
            min_value=0.0,
            max_value=255.0,
            value=float(normalize_brightness(device.brightness)),
            target=target,
            action="setDeviceBrightness:",
            identifier=device.device_id,
            # Continuous so the LED preview below tracks the thumb while
            # dragging, not just after release — setDeviceBrightness_
            # itself still only commits (saves + syncs hardware) on the
            # final tick, so this doesn't turn every pixel of drag into a
            # disk write.
            continuous=True,
        )
        brightness_label = native_ui.make_label(f"{brightness_percent(device.brightness)}%", secondary=True)
        native_ui.constrain_width(brightness_label, 44)
        brightness_row_controls = native_ui.make_stack(orientation="horizontal", spacing=10.0)
        brightness_row_controls.addArrangedSubview_(brightness_slider)
        brightness_row_controls.addArrangedSubview_(brightness_label)
        # The slider stretches to whatever width the row gives it — a
        # brightness track is the one control here that gets better the
        # longer it is.
        brightness_slider.setContentHuggingPriority_forOrientation_(
            1, NSLayoutConstraintOrientationHorizontal
        )
        inner.addArrangedSubview_(
            native_ui.make_row("Brightness", brightness_row_controls, fill_control=True)
        )

        led_count = LED_COUNT if device.device_id == VIRTUAL_DEVICE_ID else led_count_for_target(device.target)
        dots_width = led_count * (COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP) - COLOR_SWATCH_GAP
        dots_container = native_ui.make_fixed_area(dots_width, COLOR_SWATCH_SIZE)
        brightness_dots = []
        dot_x = 0.0
        preview_value = int(normalize_brightness(device.brightness))
        for _index in range(led_count):
            dot = add_preview_dot(dots_container, dot_x, 0.0)
            set_preview_dot_rgb(dot, preview_value, preview_value, preview_value)
            brightness_dots.append(dot)
            dot_x += COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP
        inner.addArrangedSubview_(
            native_ui.make_row(
                "Preview",
                dots_container,
                help_text="How bright the LEDs will actually be, at this brightness.",
            )
        )

        auto_checkbox_row = native_ui.make_checkbox(
            "Match screen brightness", target, "toggleDeviceAutoBrightness:"
        )
        auto_checkbox_row.setRepresentedObject_(device.device_id)
        auto_checkbox_row.setState_(1 if device.auto_brightness_enabled else 0)
        inner.addArrangedSubview_(native_ui.make_row("Auto-Brightness", auto_checkbox_row))
        native_ui.add_separator(inner)

        calibrate_button = native_ui.make_button("Calibrate…", target, "openDeviceCalibrationPopover:")
        calibrate_button.setRepresentedObject_(device.device_id)
        red, green, blue = device.channel_gains
        calibration_label = native_ui.make_label(
            calibration_summary_text(device.auto_brightness_enabled, red, green, blue),
            secondary=True,
            size=11.0,
        )
        color_row_controls = native_ui.make_stack(orientation="horizontal", spacing=10.0)
        color_row_controls.addArrangedSubview_(calibrate_button)
        color_row_controls.addArrangedSubview_(calibration_label)
        inner.addArrangedSubview_(native_ui.make_row("Color", color_row_controls))
        native_ui.add_separator(inner)
        resting_slider = native_ui.make_slider(
            min_value=0.0,
            max_value=35.0,
            value=float(device.resting_glow) * 100.0,
            target=target,
            action="setDeviceRestingGlow:",
            identifier=device.device_id,
        )
        inner.addArrangedSubview_(
            native_ui.make_row(
                "Resting glow",
                resting_slider,
                help_text=(
                    "A faint ember on every LED, even the unlit ones — "
                    "the dots read as physical objects instead of "
                    "vanishing. 0% is classic full dark."
                ),
            )
        )
        native_ui.add_separator(inner)

        # Per-device display choice: agent status, battery fill, or the
        # honest working-timer fill.
        display_popup = native_ui.make_popup_button(target, "setDeviceDisplay:")
        display_popup.setIdentifier_(device.device_id)
        for label, display_key in (
            ("Agent status", LED_DISPLAY_AGENT),
            ("Battery fill", LED_DISPLAY_BATTERY),
            ("Working timer fill", LED_DISPLAY_TIMER),
            ("Studio program", LED_DISPLAY_STUDIO),
        ):
            display_popup.addItemWithTitle_(label)
            item = display_popup.lastItem()
            item.setRepresentedObject_(display_key)
            if display_key == device.display:
                display_popup.selectItem_(item)
        inner.addArrangedSubview_(
            native_ui.make_row(
                "Display",
                display_popup,
                help_text=(
                    "Working timer fill lights the strip as elapsed working "
                    "time crosses your expected length — a timer, not a "
                    "claim about task progress. Studio program plays the "
                    "animation you wrote in the Studio tab, all the time."
                ),
            )
        )

        # Per-device blend override: the Pro can run Spatial Split while
        # the Dot relays.
        blend_popup = native_ui.make_popup_button(target, "setDeviceBlendMode:")
        blend_popup.setIdentifier_(device.device_id)
        current_blend = target.settings.device_blend_mode(device.device_id)
        blend_popup.addItemWithTitle_("Global default")
        blend_popup.lastItem().setRepresentedObject_("")
        if current_blend is None:
            blend_popup.selectItem_(blend_popup.lastItem())
        for mode in BLEND_MODE_CHOICES:
            blend_popup.addItemWithTitle_(BLEND_MODE_LABELS[mode])
            item = blend_popup.lastItem()
            item.setRepresentedObject_(mode)
            if mode == current_blend:
                blend_popup.selectItem_(item)
        inner.addArrangedSubview_(native_ui.make_row("Blend Mode", blend_popup))

        # Story #16: pin this device to one provider — "the Dot is
        # Codex's" — while other devices keep the aggregate.
        pin_popup = native_ui.make_popup_button(target, "setDeviceProviderPin:")
        pin_popup.setIdentifier_(device.device_id)
        current_pin = target.settings.device_provider_pin(device.device_id)
        for label, pin_key in (
            ("All sessions", ""),
            ("Claude only", "claude"),
            ("Codex only", "codex"),
        ):
            pin_popup.addItemWithTitle_(label)
            item = pin_popup.lastItem()
            item.setRepresentedObject_(pin_key)
            if (pin_key or None) == current_pin:
                pin_popup.selectItem_(item)
        inner.addArrangedSubview_(
            native_ui.make_row(
                "Sessions",
                pin_popup,
                help_text=(
                    "A pinned device shows only that provider's sessions "
                    "and rests dark when none are live. Asks still light "
                    "every device — blocked-on-you is never filtered."
                ),
            )
        )

        # Backlog #21: per-device courtesy muting — "the Dot only
        # speaks asks" while the Pro carries the full signal chorus.
        policy_popup = native_ui.make_popup_button(target, "setDeviceSignalPolicy:")
        policy_popup.setIdentifier_(device.device_id)
        current_device_policy = target.settings.device_signal_policy(device.device_id)
        for label, policy_key in (
            ("All signals", ""),
            ("Asks only", "asks_only"),
        ):
            policy_popup.addItemWithTitle_(label)
            item = policy_popup.lastItem()
            item.setRepresentedObject_(policy_key)
            if (policy_key or None) == current_device_policy:
                policy_popup.selectItem_(item)
        inner.addArrangedSubview_(
            native_ui.make_row(
                "Signals",
                policy_popup,
                help_text=(
                    "Asks only keeps this device to agent status and "
                    "blocked-on-you — completions, notifications, quota, "
                    "calendar and reminder glows stay off it. Weather and "
                    "low battery always land."
                ),
            )
        )

        stack.addArrangedSubview_(outer)
        device_controls[device.device_id] = {
            "brightness_slider": brightness_slider,
            "brightness_label": brightness_label,
            "brightness_dots": brightness_dots,
            "calibrate_button": calibrate_button,
            "calibration_label": calibration_label,
            "auto_brightness_row_checkbox": auto_checkbox_row,
            "display_popup": display_popup,
            "blend_popup": blend_popup,
            "pin_popup": pin_popup,
            "signal_policy_popup": policy_popup,
        }
    return native_ui.wrap_in_scroll_pane(stack), device_controls


# The screen-brightness reading behind Auto-Brightness is cached for the
# same reason the permission reads are: this text is rebuilt on every
# settings refresh, once per device.
SCREEN_BRIGHTNESS_TTL_SECONDS: Final = 5.0
_screen_brightness_cache: tuple[float, bool] | None = None


def reset_screen_brightness_cache() -> None:
    """Force the next Auto-Brightness summary to ask CoreDisplay again."""
    global _screen_brightness_cache
    _screen_brightness_cache = None


def screen_brightness_readable() -> bool:
    """Can this Mac actually report its screen brightness right now?

    ``CoreDisplay_Display_GetUserBrightness`` is undocumented — see
    display_brightness.py, which says in its own first paragraph that
    Apple may remove it without notice. When it goes, every
    auto-brightness device silently keeps its MANUAL brightness and no
    surface anywhere says so, which is the whole defect class: the
    checkbox reads on, the feature is inert.
    """
    global _screen_brightness_cache
    now = time.monotonic()
    cached = _screen_brightness_cache
    if cached is not None and 0.0 <= now - cached[0] < SCREEN_BRIGHTNESS_TTL_SECONDS:
        return cached[1]
    try:
        display_brightness.current_screen_brightness_fraction()
        readable = True
    except Exception:
        # DisplayBrightnessUnavailableError is the documented answer; a
        # broader failure is the same fact for this row's purposes.
        readable = False
    _screen_brightness_cache = (now, readable)
    return readable


def calibration_summary_text(
    auto_brightness_enabled: bool,
    red: float,
    green: float,
    blue: float,
    *,
    brightness_readable: bool | None = None,
) -> str:
    """The at-a-glance summary next to the Calibrate button. Channel
    percentages only appear once they differ from the default — an
    uncalibrated device just says what Auto-Brightness is doing, not a
    wall of R100% G100% B100% that reads as debug output.

    "Auto-Brightness on" used to be a claim about the CHECKBOX, not a
    reading: with the screen-brightness technique unavailable, the LED
    sync falls back to the manual value on every tick (see
    effective_brightness_for_device) and this line still said "on".
    ``brightness_readable`` is resolved here rather than demanded of the
    caller so the refresh path in status_bar keeps its positional call.
    """
    if not auto_brightness_enabled:
        parts = ["Auto-Brightness off"]
    elif brightness_readable is False or (
        brightness_readable is None and not screen_brightness_readable()
    ):
        parts = ["Auto-Brightness on, but this Mac won't report screen brightness"]
    else:
        parts = ["Auto-Brightness on"]
    if any(round(gain * 100) != 100 for gain in (red, green, blue)):
        parts.append(f"R{round(red * 100)}% G{round(green * 100)}% B{round(blue * 100)}%")
    return " · ".join(parts)


# Every patch is FULL SCALE on the channels it lights, and that is load
# bearing rather than incidental. A stored gain means "the fraction of full
# drive this channel reaches at white", and full scale is the fixed point of
# the surface transfer (led_status.strip_drive_code): decode(1.0) is 1.0, so
# these four patches drive the strip with exactly the bytes they always did.
# A calibration matched against them therefore survives the transfer change
# unchanged, and the slider keeps reading the percentage the owner dialled in.
#
# A mid-tone patch would NOT be safe to add: below full scale the strip's
# drive byte and the screen's code are deliberately different numbers, so a
# grey patch would be matched by eye against one surface and rendered on the
# other. If a mid-tone reference is ever wanted, it has to be generated
# through the same transfer the device write uses, not written as a literal.
CALIBRATION_TEST_PATCHES: tuple[tuple[str, str], ...] = (
    ("White", "#FFFFFF"),
    ("Red", "#FF0000"),
    ("Green", "#00FF00"),
    ("Blue", "#0000FF"),
)


def build_calibration_popover_content(device: StatusBarDevice, target: StatusBarController):
    """The content shown inside the "Calibrate…" popover for one device:
    a guided matching flow, not blind sliders. The reference patches at
    the top are the ground truth — click one and the device lights with
    that exact color (through the current gains), so you hold the device
    beside the screen and adjust each slider until light and patch agree.
    Closing the popover returns the device to live status automatically.
    """
    stack = native_ui.make_stack(orientation="vertical", spacing=14.0)
    native_ui.constrain_width(stack, 300.0)

    stack.addArrangedSubview_(
        native_ui.make_label(
            "Every LED is now TRUE WHITE, and the patch below\n"
            "shows true white on screen. Adjust Red, Green, and\n"
            "Blue until the light looks white to you — then check\n"
            "the other patches if you want to fine-tune.",
            secondary=True,
            size=11.0,
        )
    )
    patch_size = 26
    patch_gap = 10
    patches_width = len(CALIBRATION_TEST_PATCHES) * (patch_size + patch_gap) - patch_gap
    patches = native_ui.make_fixed_area(float(patches_width), float(patch_size))
    x = 0
    for _label, hex_color in CALIBRATION_TEST_PATCHES:
        add_color_swatch(
            patches,
            hex_color,
            x,
            1,
            target,
            "startCalibrationTest:",
            {"device_id": device.device_id, "hex": hex_color},
        )
        x += patch_size + patch_gap
    stack.addArrangedSubview_(patches)
    native_ui.add_separator(stack)

    auto_checkbox = native_ui.make_checkbox(
        "Auto-Brightness (matches screen)", target, "toggleDeviceAutoBrightness:"
    )
    auto_checkbox.setRepresentedObject_(device.device_id)
    auto_checkbox.setState_(1 if device.auto_brightness_enabled else 0)
    stack.addArrangedSubview_(auto_checkbox)

    native_ui.add_separator(stack)
    stack.addArrangedSubview_(native_ui.make_label("Color Calibration", bold=True, size=13.0))

    controls: dict[str, object] = {"auto_brightness_checkbox": auto_checkbox}
    red, green, blue = device.channel_gains
    for label, channel, gain, action, tint in (
        ("Red", "red", red, "setDeviceRedGain:", NSColor.systemRedColor()),
        ("Green", "green", green, "setDeviceGreenGain:", NSColor.systemGreenColor()),
        ("Blue", "blue", blue, "setDeviceBlueGain:", NSColor.systemBlueColor()),
    ):
        slider = native_ui.make_slider(
            min_value=MIN_CHANNEL_GAIN * 100.0,
            max_value=MAX_CHANNEL_GAIN * 100.0,
            value=gain * 100.0,
            target=target,
            action=action,
            identifier=device.device_id,
        )
        native_ui.constrain_width(slider, 200.0)
        try:
            slider.setTrackFillColor_(tint)
        except Exception:
            pass
        stack.addArrangedSubview_(native_ui.make_row(label, slider))
        controls[f"{channel}_slider"] = slider

    reset_button = native_ui.make_button("Reset to Default", target, "resetDeviceColorCalibration:")
    reset_button.setRepresentedObject_(device.device_id)
    stack.addArrangedSubview_(reset_button)
    controls["reset_button"] = reset_button

    return stack, controls


SCREEN_BAR_PREVIEW_NOTCH_WIDTH = 200.0
# A representative amount, not the real per-screen measurement
# wing_width_for_screen computes — this preview shows the *shape* of
# "extend glow along the menu bar" (does the light actually reach the
# edge it's given?) rather than standing in for any one real screen.
SCREEN_BAR_PREVIEW_WING_WIDTH = 12.0


def _build_colors_screen_bar_pane(target: StatusBarController):
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    glow_outer, glow_inner = native_ui.make_card("Minimum Glow")
    glow_slider = native_ui.make_slider(
        min_value=0.0,
        max_value=100.0,
        value=float(target.settings.screen_bar_min_glow) * 100.0,
        target=target,
        action="setScreenBarMinGlow:",
        continuous=True,
    )
    glow_inner.addArrangedSubview_(
        native_ui.make_row(
            "Dim floor",
            glow_slider,
            help_text=(
                "How dark the bar may get. 0% = pitch black: only the "
                "moving signal shows — the relay dot ticking around, "
                "the timer filling up. Higher keeps a soft outline."
            ),
        )
    )
    native_ui.add_separator(glow_inner)
    gauges_row, gauges_switch = native_ui.make_switch_row(
        "Wing-tip gauges",
        target,
        "toggleScreenBarGauges:",
        help_text=(
            "The outermost sliver of the right wing becomes a standing "
            "micro-gauge: it glows green while finished sessions wait "
            "unseen, and goes out the moment you open the menu. "
            "Survives every animation."
        ),
    )
    glow_inner.addArrangedSubview_(gauges_row)
    native_ui.add_separator(glow_inner)
    link_row, link_switch = native_ui.make_switch_row(
        "Match the hardware's animation",
        target,
        "toggleLinkScreenBarToHardware:",
        help_text=(
            "One light language in two places: the Screen Bar renders "
            "the same animation your SidePulse hardware is running, so "
            "the notch and the LEDs are never two different opinions "
            "about the same moment. Turn this off to give the Screen "
            "Bar its own animation."
        ),
    )
    glow_inner.addArrangedSubview_(link_row)
    stack.addArrangedSubview_(glow_outer)
    outer, inner = native_ui.make_card()

    inner.addArrangedSubview_(
        native_ui.make_row("Colors", native_ui.make_button("Customize Colors…", target, "openColorsWindow:"))
    )
    native_ui.add_separator(inner)

    # A live, real miniature of the Screen Bar itself — the same drawing
    # code the actual on-screen widget uses, not an illustration — so
    # "extend glow along the menu bar" and Alcove Compatibility show
    # their effect immediately instead of asking you to trust a checkbox
    # label and go look at the real menu bar to check.
    preview_max_width = SCREEN_BAR_PREVIEW_NOTCH_WIDTH + 2.0 * SCREEN_BAR_PREVIEW_WING_WIDTH
    preview_container = native_ui.make_fixed_area(preview_max_width, SCREEN_BAR_PREVIEW_HEIGHT)
    preview_view = VirtualLedView.alloc().initWithFrame_(
        ((0.0, 0.0), (SCREEN_BAR_PREVIEW_NOTCH_WIDTH, SCREEN_BAR_PREVIEW_HEIGHT))
    )
    preview_view.setHasNotch_(True)
    preview_view.setMinGlow_(float(target.settings.screen_bar_min_glow))
    preview_container.addSubview_(preview_view)
    inner.addArrangedSubview_(preview_container)

    wraps_row, wraps_switch = native_ui.make_switch_row(
        "Extend glow along the menu bar", target, "toggleScreenBarWrapsMenuBar:"
    )
    inner.addArrangedSubview_(wraps_row)
    # The same defect as the Alcove switch, on a different measurement:
    # every display without a notch reports no area beside one, so the
    # wings are zero points wide and this switch has never done anything
    # on an external monitor. Nothing said so.
    wing_status_label = native_ui.make_wrapping_label(
        screen_bar_wing_status_text(target),
        secondary=True,
        size=11.0,
        max_width=360.0,
    )
    inner.addArrangedSubview_(native_ui.make_row("Menu bar glow", wing_status_label))
    follow_row, follow_switch = native_ui.make_switch_row(
        "Match Alcove's width automatically",
        target,
        "toggleScreenBarFollowAlcove:",
        help_text=(
            "The bracket tracks Alcove's visible capsule — widening "
            "for a timer or now-playing pill and easing back when it "
            "collapses, hugging it within a couple of points. While a "
            "capsule is visible this supersedes the Bar Size gap, so "
            "Automatic stays automatic; a manual wing length still "
            "wins. Needs Screen Recording permission; without it the "
            "bar quietly keeps its classic size."
        ),
    )
    inner.addArrangedSubview_(follow_row)
    fullscreen_row, fullscreen_switch = native_ui.make_switch_row(
        "Show over full-screen apps",
        target,
        "toggleScreenBarFullScreen:",
        help_text=(
            "Off (the default) hides the bar whenever the active space "
            "is full-screen — a movie stays a movie. On keeps the bar "
            "everywhere, including over full-screen video."
        ),
    )
    inner.addArrangedSubview_(fullscreen_row)
    # A switch that reads ON while the feature does nothing is the defect,
    # not the cosmetics. This row says which of the four things is
    # actually happening, and — when the answer is a permission — offers
    # the one click that fixes it, exactly as the dropdown does for a
    # stale hook.
    alcove_actions = alcove_actions_for(target)
    alcove_status_label = native_ui.make_wrapping_label(
        alcove_follow_status_text(target),
        secondary=True,
        size=11.0,
        max_width=360.0,
    )
    alcove_permission_button = native_ui.make_button(
        "Open Screen Recording Settings…",
        alcove_actions,
        "grantScreenRecording:",
    )
    alcove_permission_button.setHidden_(not alcove_follow_needs_permission(target))
    alcove_controls = native_ui.make_stack(
        orientation="horizontal",
        spacing=native_ui.SPACE_S,
    )
    alcove_controls.addArrangedSubview_(alcove_status_label)
    alcove_controls.addArrangedSubview_(native_ui.make_hspacer())
    alcove_controls.addArrangedSubview_(alcove_permission_button)
    alcove_status_row = native_ui.make_row("Alcove following", alcove_controls)
    inner.addArrangedSubview_(alcove_status_row)
    alcove_actions.status_label = alcove_status_label
    alcove_actions.permission_button = alcove_permission_button
    native_ui.add_separator(inner)
    # Bracket coloring: Auto keeps the on-screen bracket in lockstep
    # with the physical LEDs' ripple whenever a crowd is lit.
    bracket_popup = native_ui.make_popup_button(target, "setBracketStyle:")
    for label, style_key in (
        ("Automatic", "auto"),
        ("Match the LEDs (ripple)", "spatial"),
        ("Single identity color", "identity"),
    ):
        bracket_popup.addItemWithTitle_(label)
        item = bracket_popup.lastItem()
        item.setRepresentedObject_(style_key)
        if style_key == target.settings.screen_bar_bracket_style:
            bracket_popup.selectItem_(item)
    inner.addArrangedSubview_(
        native_ui.make_row(
            "Bracket colors",
            bracket_popup,
            help_text=(
                "Automatic mirrors the light bar's own per-LED animation "
                "whenever two or more LEDs are lit, and falls back to one "
                "blended identity color when a lone agent would leave the "
                "bracket mostly dark."
            ),
        )
    )
    stack.addArrangedSubview_(outer)

    # Manual bar geometry (Jonathan's ask): the gap between the risers
    # and the wings' reach, adjustable for notch companions like Alcove
    # whose visual width changes at runtime — and for whatever notch
    # future Macs ship with. Automatic uses the hardware measurements.
    size_outer, size_inner = native_ui.make_card("Bar Size")
    try:
        auto_gap = slot_width_for_screen(NSScreen.mainScreen())
    except Exception:
        # WINDOW_WIDTH, not a literal. This was 232.0 — a number that
        # appears nowhere in the geometry it claims to stand in for, so
        # the slider parked 12pt away from the size Automatic actually
        # uses and the owner was reading a measurement of nothing.
        auto_gap = SCREEN_BAR_AUTOMATIC_GAP_FALLBACK
    gap_value = target.settings.screen_bar_gap_width or auto_gap
    gap_slider = native_ui.make_slider(
        min_value=140.0,
        max_value=900.0,
        value=float(gap_value),
        target=target,
        action="setScreenBarGapWidth:",
        continuous=True,
    )
    size_inner.addArrangedSubview_(
        native_ui.make_row(
            "Gap width",
            gap_slider,
            fill_control=True,
            help_text="How wide the dark center is — widen it to clear Alcove's pill.",
        )
    )
    auto_cluster = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    auto_cluster.addArrangedSubview_(
        native_ui.make_button("Use Automatic Size", target, "resetScreenBarGeometry:")
    )
    auto_cluster.addArrangedSubview_(native_ui.make_hspacer())
    size_inner.addArrangedSubview_(auto_cluster)
    stack.addArrangedSubview_(size_outer)
    fields = {
        "screen_bar_preview_view": preview_view,
        "screen_bar_preview_container": preview_container,
        "screen_bar_gap_slider": gap_slider,
        "bracket_style_popup": bracket_popup,
        "alcove_follow_status": alcove_status_label,
        "screen_bar_wing_status": wing_status_label,
    }
    buttons = {
        "screen_bar_wraps_menu_bar": wraps_switch,
        "screen_bar_gauges": gauges_switch,
        "link_screen_bar_to_hardware": link_switch,
        "screen_bar_follow_alcove": follow_switch,
        "screen_bar_show_in_full_screen": fullscreen_switch,
        "alcove_screen_recording_permission": alcove_permission_button,
    }
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


SCREEN_BAR_WING_MESSAGES: Final[dict[ScreenBarWingState, str]] = {
    # Classic mode paints contained inside the notch (2026-08-20: wing
    # glow on the menu bar's own background read as gray slabs that
    # visibly did not match the notch); wings act while following
    # Alcove, where the bracket wraps the capsule.
    ScreenBarWingState.EXTENDED: (
        "Ready — the bar wraps Alcove's capsule when following is on; "
        "over the bare notch it stays inside the black."
    ),
    ScreenBarWingState.NO_SAFE_AREA: (
        "This display has no notch, so there is no menu-bar room beside "
        "one to glow into. The bar keeps its own size here."
    ),
    ScreenBarWingState.MENU_BAR_FULL: (
        "Your menu bar is too full to lend any room, so the glow stays "
        "inside the notch."
    ),
    ScreenBarWingState.UNREADABLE: (
        "This display would not report its menu-bar areas, so the glow "
        "stays inside the notch."
    ),
    ScreenBarWingState.MANUAL: "Using your saved wing length.",
    # Deliberately does not mention a notch: this same line is shown on
    # displays that have none.
    ScreenBarWingState.NOT_EXTENDING: "Off — the bar keeps its own width.",
}


def screen_bar_wing_status_text(target) -> str:
    """The sentence under "Extend glow along the menu bar".

    While Alcove following is live the wings are sized from the measured
    capsule instead of the screen's auxiliary areas, so a screen that
    reports no room is NOT the reason the bar looks the way it does --
    saying "no notch here" then would be a second wrong answer stacked on
    the first. The Alcove row directly below already owns that case.
    """
    settings = target.settings
    wrap = bool(getattr(settings, "virtual_status_device_wraps_menu_bar", False))
    if wrap and alcove_follow_state(target) is AlcoveCaptureStatus.CAPTURED:
        return "Matching Alcove's capsule — see below."
    try:
        screen = NSScreen.mainScreen()
    except Exception:
        screen = None
    if screen is None:
        return "No display to measure right now."
    gap = getattr(settings, "screen_bar_gap_width", None)
    try:
        notch_width = float(gap) if gap else slot_width_for_screen(screen)
        state = screen_bar_wing_state(
            screen,
            notch_width,
            wrap_menu_bar=wrap,
            wing_length=getattr(settings, "screen_bar_wing_length", None),
        )
    except Exception:
        # A settings row must never be able to take the window down.
        state = ScreenBarWingState.UNREADABLE
    return SCREEN_BAR_WING_MESSAGES.get(
        state, SCREEN_BAR_WING_MESSAGES[ScreenBarWingState.UNREADABLE]
    )


def alcove_follow_state(target) -> AlcoveCaptureStatus | None:
    """What Alcove following is doing right now, or None when unknown.

    Prefers the render path's own live reading, because only a real
    capture may claim success. Falls back to a promptless preflight and
    window probe, which can only ever report a BLOCKER — "nothing is in
    the way" is not evidence that anything worked, and this function
    returns None for it rather than implying otherwise.
    """
    if not bool(getattr(target.settings, "screen_bar_follow_alcove", True)):
        return AlcoveCaptureStatus.NOT_FOLLOWING
    snapshot = latest_alcove_status()
    if snapshot is not None:
        age = time.monotonic() - snapshot.updated_at
        if 0.0 <= age <= ALCOVE_STATUS_MAX_AGE_SECONDS:
            return snapshot.status
    return alcove_follow_blocker(following=True)


def alcove_follow_status_text(target) -> str:
    """The sentence under the "Match Alcove's width" switch.

    Before this row the switch was the only signal, and it read ON in all
    four failure modes — including the one where macOS had never granted
    Screen Recording, which no surface anywhere mentioned.
    """
    status = alcove_follow_state(target)
    if status is None:
        return "No measurement yet."
    return ALCOVE_STATUS_MESSAGES.get(status, "No measurement yet.")


def alcove_follow_needs_permission(target) -> bool:
    """Only a DENIED preflight earns the button. Unknown does not.

    Offering "grant Screen Recording" to someone whose permission is
    already fine sends them to a settings pane to fix nothing, which is
    its own species of dishonesty.
    """
    return alcove_follow_state(target) is AlcoveCaptureStatus.SCREEN_RECORDING_DENIED


def alcove_actions_for(target):
    """The retained action sink for Alcove's permission button.

    Created on demand and kept on the controller, so the dropdown can
    offer the same one click without depending on whether the Screen Bar
    settings pane has ever been opened.
    """
    actions = getattr(target, "alcove_actions", None)
    if actions is None:
        actions = SidePulseAlcoveActions.alloc().initWithController_(target)
        target.alcove_actions = actions
    return actions


def alcove_menu_alert_title(target) -> str:
    """The dropdown's one line about Alcove following, or "".

    Only the permission case earns a row in the menu. It is the one
    failure the user can actually fix, and — unlike a missing Alcove or
    an unmeasurable capsule — the one with no other symptom at all: the
    bar simply keeps its old size forever and nothing says why.
    """
    if not alcove_follow_needs_permission(target):
        return ""
    return (
        "⚠ Alcove following needs Screen Recording — grant it in "
        "Settings…"
    )


def refresh_alcove_follow_controls(target) -> None:
    """Keep the row current while the window is open.

    Panes are built once, lazily, so without a refresh this row would
    freeze at whatever was true the first time the user visited it --
    including a stale "Screen Recording is off" after they granted it.
    """
    fields = getattr(target, "settings_fields", None) or {}
    buttons = getattr(target, "settings_buttons", None) or {}
    label = fields.get("alcove_follow_status")
    if label is not None:
        label.setStringValue_(alcove_follow_status_text(target))
    button = buttons.get("alcove_screen_recording_permission")
    if button is not None:
        button.setHidden_(not alcove_follow_needs_permission(target))
    label = fields.get("screen_bar_wing_status")
    if label is not None:
        # Same pane, same freeze: a display change is exactly when this
        # row stops describing anything, and a display change is exactly
        # what the owner does when they dock the Mac.
        label.setStringValue_(screen_bar_wing_status_text(target))


class SidePulseAlcoveActions(NSObject):
    """Action target for the Alcove permission button.

    Same reason SidePulseStudioActions exists: every other selector in
    this window belongs to StatusBarController, which lives in a file
    this one may not edit, and PyObjC dispatches target/action through
    respondsToSelector: — which a plain Python object cannot satisfy.
    The controller retains this via ``target.alcove_actions``.
    """

    def initWithController_(self, controller):
        self = objc.super(SidePulseAlcoveActions, self).init()
        if self is None:
            return None
        self.controller = controller
        self.status_label = None
        self.permission_button = None
        return self

    @objc.IBAction
    def grantScreenRecording_(self, _sender):
        """Explicit user action — the ONLY place a prompt is allowed.

        Requesting first is what puts SidePulse in the Screen Recording
        list at all; an app that never asked does not appear there, so
        sending someone straight to the pane would show them a list
        without the row they were told to switch on. Then open the pane,
        because a second request after the first denial is a no-op and
        the switch is the real fix.
        """
        granted = request_screen_recording_access()
        if granted is True:
            # The last recorded outcome was taken under the OLD permission
            # and is now a lie with up to two seconds left to live. Drop
            # it rather than let the row keep saying "Screen Recording is
            # off" to the person who just switched it on.
            reset_alcove_status()
        else:
            open_url(SCREEN_RECORDING_SETTINGS_URL)
        refresh_alcove_follow_controls(self.controller)
        message = getattr(self.controller, "set_settings_message", None)
        if callable(message):
            message(
                # Not "following is live": permission is one of four
                # things that have to be true, and the row already knows
                # which of them currently is.
                alcove_follow_status_text(self.controller)
                if granted is True
                else (
                    "Turn SidePulse on under Screen Recording, then "
                    "reopen SidePulse."
                )
            )


def _build_power_pane(target: StatusBarController):
    """Keep-awake policy and battery display together — both are "what
    SidePulse does with the Mac's power state"."""
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)

    awake_outer, awake_inner = native_ui.make_card("Keep Awake With Lid Closed")
    policy_popup = make_closed_lid_awake_policy_popup(target)
    awake_inner.addArrangedSubview_(native_ui.make_row("Policy", policy_popup))

    grace_field = native_ui.make_field(
        f"{target.settings.closed_lid_grace_minutes:g}", target=target, action="applyClosedLidGraceMinutes:"
    )
    native_ui.constrain_width(grace_field, 56.0)
    grace_controls = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_XS)
    grace_controls.addArrangedSubview_(grace_field)
    grace_controls.addArrangedSubview_(native_ui.make_label("min", secondary=True))
    awake_inner.addArrangedSubview_(
        native_ui.make_row(
            "Wait before releasing",
            grace_controls,
            help_text=(
                "A buffer against a false “done” reading — e.g. a command still "
                "running with no events for a stretch — closing the lid into sleep."
            ),
        )
    )
    stack.addArrangedSubview_(awake_outer)

    battery_outer, battery_inner = native_ui.make_card("Battery")
    leds_row, battery_leds = native_ui.make_switch_row(
        "Show battery on LEDs", target, "setBatteryLedDisplayFromCheckbox:"
    )
    battery_inner.addArrangedSubview_(leds_row)
    preview_row, battery_power_preview = native_ui.make_switch_row(
        "Show battery for 7s on plug/unplug", target, "setBatteryPowerPreviewFromCheckbox:"
    )
    battery_inner.addArrangedSubview_(preview_row)
    native_ui.add_separator(battery_inner)
    low_power_row, low_battery_switch = native_ui.make_switch_row(
        "Charge reminder when battery is low",
        target,
        "toggleLowBatteryAlert:",
        help_text=(
            "Below the threshold while unplugged, every display switches to "
            "a calm, slow red breathe until you plug in."
        ),
    )
    battery_inner.addArrangedSubview_(low_power_row)
    threshold_field = native_ui.make_field(
        f"{target.settings.low_battery_threshold_percent:g}",
        target=target,
        action="applyLowBatteryThreshold:",
    )
    native_ui.constrain_width(threshold_field, 48.0)
    threshold_controls = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_XS)
    threshold_controls.addArrangedSubview_(threshold_field)
    threshold_controls.addArrangedSubview_(native_ui.make_label("%", secondary=True))
    battery_inner.addArrangedSubview_(native_ui.make_row("Below", threshold_controls))
    stack.addArrangedSubview_(battery_outer)

    fields = {
        "closed_lid_awake_policy_popup": policy_popup,
        "closed_lid_grace_field": grace_field,
        "low_battery_threshold_field": threshold_field,
    }
    buttons = {
        "battery_leds": battery_leds,
        "battery_power_preview": battery_power_preview,
        "low_battery_alert": low_battery_switch,
    }
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


# Style cards: every signal is edited BY EYE — a color well, pattern
# thumbnails that ANIMATE their pattern live, continuous sliders, and a
# live preview strip rendering exactly what the Screen Bar will show.
SIGNAL_STYLE_CARDS: tuple[tuple[str, str, bool], ...] = (
    ("low_battery", "Low Battery Style", True),
    ("reminders", "Reminder Style", True),
    ("calendar", "Calendar Style", True),
    ("weather", "Weather Alert Style", True),
    ("completion", "Completion Sweep Style", True),
)
SIGNAL_THUMB_SIZE = (52.0, 20.0)
# Quick-pick swatches for signal colors: recognizable brand hues first
# (pick "Claude" and mean it), then the identity palette. The swatch
# row + the classic NSColorPanel replaced NSColorWell entirely: the
# modern well is SwiftUI-backed and SEGFAULTed inside Swift
# Concurrency's executor checks in this Python-hosted process the
# moment its "add a color" picker was touched (crash report
# SidePulse-2026-08-11-202021.ips).
#
# Read out of colors.BRAND_SEED_COLORS, never restated. The literal that
# used to live here still named Codex #FF3A00 — which is this app's own
# ask/blocked signal colour (led_status.ASK_AMBER), not Codex's #2B8FFF --
# so the chip captioned "Codex" painted the alert red. colors.py fixed its
# own copy and left this one behind, which is exactly the failure mode
# that comment warned about.
BRAND_SWATCHES: tuple[tuple[str, str], ...] = colors_module.BRAND_SEED_COLORS
SWATCH_BUTTON_SIZE = 22.0
SIGNAL_PREVIEW_SIZE = (220.0, 22.0)
ESCALATION_TIER_LABELS: tuple[tuple[str, str], ...] = (
    ("Light ramp only", "light"),
    ("Ramp + menu bar flash", "menu_bar"),
    ("Ramp + flash + one chime", "chime"),
    ("Full takeover", "takeover"),
)


def _mini_led_view(width: float, height: float):
    view = VirtualLedView.alloc().initWithFrame_(((0, 0), (width, height)))
    view.setHasNotch_(False)
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    native_ui.constrain_width(view, width)
    native_ui.constrain_height(view, height)
    view.setWantsLayer_(True)
    layer = view.layer()
    if layer is not None:
        layer.setCornerRadius_(5.0)
        layer.setMasksToBounds_(True)
    return view


def _apply_thumb_selection(thumbs: dict, selected_pattern: str) -> None:
    for pattern, thumb in thumbs.items():
        layer = thumb.layer()
        if layer is None:
            continue
        if pattern == selected_pattern:
            layer.setBorderWidth_(2.0)
            layer.setBorderColor_(NSColor.controlAccentColor().CGColor())
        else:
            layer.setBorderWidth_(0.0)


def _solid_swatch_image(hex_color: str, size: float = SWATCH_BUTTON_SIZE):
    image = NSImage.alloc().initWithSize_((size, size))
    image.lockFocus()
    nscolor_from_hex(hex_color).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        ((1.0, 1.0), (size - 2.0, size - 2.0)), 6.0, 6.0
    ).fill()
    NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.25).setStroke()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        ((1.0, 1.0), (size - 2.0, size - 2.0)), 6.0, 6.0
    ).stroke()
    image.unlockFocus()
    return image


def _mode_animation_thumb_program(target: StatusBarController, mode_key: str, style: str) -> str:
    """One mode-animation choice rendered live in that mode's own color
    — the Colors window speaks the Signals pane's visual language."""
    spec = {
        "idle": (LedDisplayState.IDLE, "idle_color"),
        "working": (LedDisplayState.WORKING, "working_color"),
        "ask": (LedDisplayState.ASK, "ask_color"),
        "done": (LedDisplayState.DONE, "done_color"),
    }.get(mode_key)
    if spec is None:
        return "#FFFFFF"
    state, color_kwarg = spec
    mode_hex = target.settings.colors.mode_color(mode_key)
    stripped = mode_hex.lstrip("#")
    try:
        luminance = sum(int(stripped[i : i + 2], 16) for i in (0, 2, 4)) / 3.0
    except ValueError:
        luminance = 255.0
    if luminance < 24.0:
        # A near-black mode color (Idle ships at #030302) makes an
        # invisible thumbnail; preview the SHAPE in neutral gray.
        mode_hex = "#9A9A9A"
    kwargs = {color_kwarg: mode_hex}
    style_kwarg = colors_module._MODE_KEY_TO_STYLE_KWARG.get(mode_key)
    if style_kwarg:
        kwargs[style_kwarg] = style
    try:
        return program_for_display_state(state, led_count=8, **kwargs)
    except (TypeError, ValueError):
        return target.settings.colors.mode_color(mode_key)


def make_signal_color_row(target: StatusBarController, key: str, current_color: str):
    """Brand + palette swatches and a Custom… button (classic
    NSColorPanel). No NSColorWell anywhere — see BRAND_SWATCHES."""
    row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_XS)
    swatches = list(BRAND_SWATCHES) + [
        (f"Palette {index + 1}", hex_color)
        for index, hex_color in enumerate(colors_module.IDENTITY_PALETTE[:4])
    ]
    for label, hex_color in swatches:
        button = NSButton.alloc().init()
        button.setTranslatesAutoresizingMaskIntoConstraints_(False)
        button.setBordered_(False)
        button.setImage_(_solid_swatch_image(hex_color))
        button.setToolTip_(f"{label} — {hex_color}")
        button.setTarget_(target)
        button.setAction_("pickSignalSwatch:")
        button.setIdentifier_(f"{key}|{hex_color}")
        native_ui.constrain_width(button, SWATCH_BUTTON_SIZE)
        native_ui.constrain_height(button, SWATCH_BUTTON_SIZE)
        row.addArrangedSubview_(button)
    custom = native_ui.make_button("Custom…", target, "openSignalColorPanel:")
    custom.setIdentifier_(key)
    row.addArrangedSubview_(custom)
    return row


def make_signal_style_card(target: StatusBarController, key: str, title: str, *, show_color: bool, fields: dict):
    outer, inner = native_ui.make_card(title)
    style = target.settings.signal_style(key)

    if show_color:
        color_row = make_signal_color_row(target, key, style.color)
        current_swatch = _mini_led_view(SWATCH_BUTTON_SIZE * 1.6, SWATCH_BUTTON_SIZE)
        current_swatch.setProgram_(style.color)
        color_cluster = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
        color_cluster.addArrangedSubview_(current_swatch)
        color_cluster.addArrangedSubview_(color_row)
        inner.addArrangedSubview_(native_ui.make_row("Color", color_cluster))
        native_ui.add_separator(inner)
        fields[f"signal_color:{key}"] = current_swatch

    preview_color = None
    thumbs: dict[str, object] = {}
    thumb_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    # Continuous signals don't offer one-shot patterns: three flashes
    # then hours of darkness isn't a style, it's a bug.
    offered_patterns = tuple(
        pattern
        for pattern in signals_module.SIGNAL_PATTERNS
        if not (
            key in signals_module.CONTINUOUS_SIGNALS
            and pattern in signals_module.ONE_SHOT_PATTERNS
        )
    )
    for pattern in offered_patterns:
        thumb = _mini_led_view(*SIGNAL_THUMB_SIZE)
        thumb.setToolTip_(pattern.replace("-", " ").title())
        preview_style = signals_module.SignalStyle(
            style.color, pattern, style.speed_seconds, style.intensity
        )
        thumb.setProgram_(style_to_program(preview_style, 255, color=preview_color))
        thumb.signal_card_key = key
        thumb.signal_card_pattern = pattern
        recognizer = NSClickGestureRecognizer.alloc().initWithTarget_action_(
            target, "selectSignalPattern:"
        )
        thumb.addGestureRecognizer_(recognizer)
        thumbs[pattern] = thumb
        thumb_row.addArrangedSubview_(thumb)
    _apply_thumb_selection(thumbs, style.pattern)
    inner.addArrangedSubview_(native_ui.make_row("Pattern", thumb_row))
    native_ui.add_separator(inner)
    fields[f"signal_thumbs:{key}"] = thumbs

    speed = native_ui.make_slider(
        min_value=signals_module.MIN_SPEED_SECONDS,
        max_value=signals_module.MAX_SPEED_SECONDS,
        value=style.speed_seconds,
        target=target,
        action="setSignalSpeed:",
        identifier=key,
        continuous=True,
    )
    inner.addArrangedSubview_(native_ui.make_row("Speed", speed, fill_control=True))
    fields[f"signal_speed:{key}"] = speed
    intensity = native_ui.make_slider(
        min_value=signals_module.MIN_INTENSITY,
        max_value=signals_module.MAX_INTENSITY,
        value=style.intensity,
        target=target,
        action="setSignalIntensity:",
        identifier=key,
        continuous=True,
    )
    inner.addArrangedSubview_(native_ui.make_row("Intensity", intensity, fill_control=True))
    fields[f"signal_intensity:{key}"] = intensity
    native_ui.add_separator(inner)

    preview = _mini_led_view(*SIGNAL_PREVIEW_SIZE)
    preview.setProgram_(style_to_program(style, 255, color=preview_color))
    preview_cluster = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    preview_cluster.addArrangedSubview_(preview)
    test_button = native_ui.make_button("Test", target, "testSignal:")
    test_button.setIdentifier_(key)
    test_button.setToolTip_(
        "Play this signal's current style on the Screen Bar and every "
        "connected device for a few seconds."
    )
    preview_cluster.addArrangedSubview_(test_button)
    inner.addArrangedSubview_(native_ui.make_row("Preview", preview_cluster))
    fields[f"signal_preview:{key}"] = preview
    return outer


def _build_focus_pane(target: StatusBarController):
    """What each macOS Focus does to the lights — its own pane because
    this lived at the BOTTOM of Signals and the user who asked for
    per-Focus control had never seen that it already existed."""
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    fields: dict[str, object] = {}
    buttons: dict[str, object] = {}

    now_outer, now_inner = native_ui.make_card("Right Now")
    now_label = native_ui.make_label(
        target.active_focus_summary(), secondary=False, size=13.0
    )
    now_inner.addArrangedSubview_(now_label)
    fields["focus_now_label"] = now_label
    stack.addArrangedSubview_(now_outer)

    enable_outer, enable_inner = native_ui.make_card("Focus Dimming")
    focus_row, focus_switch = native_ui.make_switch_row(
        "React when a macOS Focus turns on",
        target,
        "toggleFocusSync:",
        help_text=(
            "Needs Full Disk Access for SidePulse (granted in the Setup "
            "window) — otherwise this has no effect."
        ),
    )
    enable_inner.addArrangedSubview_(focus_row)
    buttons["focus_sync_enabled"] = focus_switch
    stack.addArrangedSubview_(enable_outer)

    warmth_outer, warmth_inner = native_ui.make_card("Night Warmth")
    warmth_row, warmth_switch = native_ui.make_switch_row(
        "Warm the lights from 7 PM to 7 AM",
        target,
        "toggleNightWarmth:",
        help_text=(
            "Eases green and blue down after dark — like Night Shift, "
            "for your LEDs. Composes with each device's calibration."
        ),
    )
    warmth_inner.addArrangedSubview_(warmth_row)
    buttons["night_warmth_enabled"] = warmth_switch
    stack.addArrangedSubview_(warmth_outer)

    # Story #10: timebox presets can run a named Shortcut when they
    # start and another when they end — Focus on with the drain, Focus
    # off when it finishes.
    handshake_outer, handshake_inner = native_ui.make_card("Timebox Focus Handshake")
    handshake_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Each Timer preset can run a Shortcut when it starts and "
            "another when it ends or you press Stop — name a Shortcut "
            "that turns a Focus on, and its partner that turns it off. "
            "macOS asks permission once per Shortcut the first time it "
            "runs.",
            secondary=True,
            size=12.0,
            max_width=560.0,
        )
    )
    for preset_index, preset_minutes in enumerate(TIMEBOX_PRESET_MINUTES):
        pair = target.settings.timebox_shortcut_pair(str(preset_minutes))
        on_field = native_ui.make_field(
            pair[0], target=target, action="applyTimeboxShortcuts:"
        )
        on_field.setPlaceholderString_("Shortcut at start")
        native_ui.constrain_width(on_field, 150.0)
        off_field = native_ui.make_field(
            pair[1], target=target, action="applyTimeboxShortcuts:"
        )
        off_field.setPlaceholderString_("Shortcut at end")
        native_ui.constrain_width(off_field, 150.0)
        pair_cluster = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_XS)
        pair_cluster.addArrangedSubview_(on_field)
        pair_cluster.addArrangedSubview_(off_field)
        handshake_inner.addArrangedSubview_(
            native_ui.make_row(f"{preset_minutes} minutes", pair_cluster)
        )
        if preset_index < len(TIMEBOX_PRESET_MINUTES) - 1:
            native_ui.add_separator(handshake_inner)
        fields[f"timebox_on_field:{preset_minutes}"] = on_field
        fields[f"timebox_off_field:{preset_minutes}"] = off_field
    stack.addArrangedSubview_(handshake_outer)

    # Per-Focus rules: each configured Focus gets its own dim choice.
    # When the Focus database can't be read (no Full Disk Access), say so
    # plainly and offer the one-click path to fix it, instead of showing
    # a feature that silently does nothing.
    focus_outer, focus_inner = native_ui.make_card("Per-Focus Rules")
    focus_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Each Focus can dim, turn the lights off entirely, or apply a "
            "calibration profile the moment it activates — e.g. School \u2192 "
            "Turn off, Work \u2192 Dim to 50%.",
            secondary=True,
            size=12.0,
            max_width=560.0,
        )
    )
    try:
        focus_modes = focus_sync.configured_focus_modes()
        # Count only: custom Focus names are user content and stay out of
        # logs, same as session titles and transcript text.
        log_status_bar(f"focus roster: {len(focus_modes)} mode(s)")
    except focus_sync.FocusSyncUnavailableError as exc:
        log_status_bar(f"focus roster unavailable: {exc}")
        focus_modes = None
    if not focus_modes:
        if running_inside_bundle():
            grant_target = str(default_app_bundle_path())
            grant_instructions = (
                "In Privacy Settings, click +, and pick SidePulse from your "
                "Applications folder. If SidePulse is already listed, remove "
                "it with − first, then add it again — macOS only "
                "re-checks the app when it's re-added. This pane fills with "
                "your Focus modes once granted."
            )
        else:
            # Not yet running inside the app bundle: macOS attributes file
            # access to the RESOLVED binary, not the venv symlink.
            grant_target = os.path.realpath(sys.executable or "python3")
            grant_instructions = (
                "In Privacy Settings, click +, press ⌘⇧G, and paste that "
                "path. This pane fills with your Focus modes once granted."
            )
        focus_inner.addArrangedSubview_(
            native_ui.make_wrapping_label(
                "Per-Focus rules need Full Disk Access, granted to SidePulse "
                "itself:",
                secondary=True,
                size=12.0,
                max_width=500.0,
            )
        )
        interpreter_label = native_ui.make_label(grant_target, secondary=True, size=11.0)
        interpreter_label.setSelectable_(True)
        focus_inner.addArrangedSubview_(interpreter_label)
        focus_inner.addArrangedSubview_(
            native_ui.make_wrapping_label(
                grant_instructions,
                secondary=True,
                size=11.0,
                max_width=500.0,
            )
        )
        fda_controls = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
        fda_controls.addArrangedSubview_(
            native_ui.make_button("Open Privacy Settings…", target, "openFullDiskAccessSettings:")
        )
        fda_controls.addArrangedSubview_(
            native_ui.make_button(
                "Reveal SidePulse in Finder" if running_inside_bundle() else "Reveal Program in Finder",
                target,
                "revealFocusBinaryInFinder:",
            )
        )
        fda_controls.addArrangedSubview_(native_ui.make_hspacer())
        focus_inner.addArrangedSubview_(fda_controls)
    else:
        for index, (identifier, name) in enumerate(focus_modes):
            popup = make_focus_dim_popup(target, identifier)
            # Focus -> profile automation: this Focus can also apply a
            # calibration/brightness profile the moment it activates.
            profile_popup = native_ui.make_popup_button(target, "setFocusProfileRule:")
            profile_popup.setIdentifier_(identifier)
            current_rule = target.settings.focus_profile_rules.get(identifier)
            profile_popup.addItemWithTitle_("No profile")
            profile_popup.lastItem().setRepresentedObject_("")
            if current_rule is None:
                profile_popup.selectItem_(profile_popup.lastItem())
            for slot in CALIBRATION_PROFILE_SLOTS:
                profile_popup.addItemWithTitle_(f"Apply {slot}")
                item = profile_popup.lastItem()
                item.setRepresentedObject_(slot)
                if slot == current_rule:
                    profile_popup.selectItem_(item)
            # Story #12: what this Focus does to SIGNALS (not just
            # brightness) — hold the courtesy glows, or go fully silent.
            signal_popup = native_ui.make_popup_button(target, "setFocusSignalPolicy:")
            signal_popup.setIdentifier_(identifier)
            current_policy = target.settings.focus_signal_policy.get(identifier, "all")
            for title, policy_value in (
                ("All signals", "all"),
                ("Asks only", "asks_only"),
                ("Silent", "silent"),
            ):
                signal_popup.addItemWithTitle_(title)
                item = signal_popup.lastItem()
                item.setRepresentedObject_(policy_value)
                if policy_value == current_policy:
                    signal_popup.selectItem_(item)
            row_cluster = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
            row_cluster.addArrangedSubview_(popup)
            row_cluster.addArrangedSubview_(profile_popup)
            row_cluster.addArrangedSubview_(signal_popup)
            focus_inner.addArrangedSubview_(native_ui.make_row(name, row_cluster))
            if index < len(focus_modes) - 1:
                native_ui.add_separator(focus_inner)
            fields[f"focus_rule_popup:{identifier}"] = popup
            fields[f"focus_profile_popup:{identifier}"] = profile_popup
            fields[f"focus_signal_popup:{identifier}"] = signal_popup
    stack.addArrangedSubview_(focus_outer)

    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


def _build_led_behavior_pane(target: StatusBarController):
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    outer, inner = native_ui.make_card("Dimming")

    idle_row, idle_switch = native_ui.make_switch_row(
        "Dim further after being idle", target, "toggleIdleDim:"
    )
    inner.addArrangedSubview_(idle_row)

    minutes_field = native_ui.make_field(
        f"{target.settings.idle_dim_after_minutes:g}", target=target, action="applyIdleDimSettings:"
    )
    native_ui.constrain_width(minutes_field, 48.0)
    fraction_field = native_ui.make_field(
        f"{round(target.settings.idle_dim_fraction * 100)}", target=target, action="applyIdleDimSettings:"
    )
    native_ui.constrain_width(fraction_field, 48.0)
    idle_controls = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_XS)
    idle_controls.addArrangedSubview_(minutes_field)
    idle_controls.addArrangedSubview_(native_ui.make_label("min, dim to", secondary=True))
    idle_controls.addArrangedSubview_(fraction_field)
    idle_controls.addArrangedSubview_(native_ui.make_label("%", secondary=True))
    inner.addArrangedSubview_(native_ui.make_row("After", idle_controls))

    stack.addArrangedSubview_(outer)

    fields = {"idle_dim_minutes_field": minutes_field, "idle_dim_fraction_field": fraction_field}


    # Needs-you escalation: how loud an ignored ask may get.
    esc_outer, esc_inner = native_ui.make_card("Needs-You Escalation")
    tier_popup = native_ui.make_popup_button(target, "setEscalationTier:")
    for label, tier_key in ESCALATION_TIER_LABELS:
        tier_popup.addItemWithTitle_(label)
        item = tier_popup.lastItem()
        item.setRepresentedObject_(tier_key)
        if tier_key == target.settings.escalation_tier:
            tier_popup.selectItem_(item)
    esc_inner.addArrangedSubview_(native_ui.make_row("Ceiling", tier_popup))
    fields["escalation_tier_popup"] = tier_popup
    native_ui.add_separator(esc_inner)
    subask_row, subask_switch = native_ui.make_switch_row(
        "Sub-agent asks ring the Ask signal",
        target,
        "toggleSubagentAsksAlert:",
        help_text=(
            "Workers often end with question-shaped text nobody can "
            "answer — off (default) means only MAIN sessions turn the "
            "lights amber."
        ),
    )
    esc_inner.addArrangedSubview_(subask_row)
    native_ui.add_separator(esc_inner)
    threshold_controls = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_XS)
    for field_key, seconds, suffix in (
        ("escalation_ramp_field", target.settings.escalation_ramp_seconds, "s ramp,"),
        ("escalation_menu_bar_field", target.settings.escalation_menu_bar_seconds, "s flash,"),
        ("escalation_final_field", target.settings.escalation_final_seconds, "s finale"),
    ):
        threshold_field = native_ui.make_field(
            f"{seconds:g}", target=target, action="applyEscalationThresholds:"
        )
        native_ui.constrain_width(threshold_field, 52.0)
        threshold_controls.addArrangedSubview_(threshold_field)
        threshold_controls.addArrangedSubview_(native_ui.make_label(suffix, secondary=True))
        fields[field_key] = threshold_field
    esc_inner.addArrangedSubview_(native_ui.make_row("After", threshold_controls))
    webhook_field = native_ui.make_field(
        target.settings.escalation_webhook_url,
        target=target,
        action="applyEscalationWebhook:",
    )
    native_ui.constrain_width(webhook_field, 280.0)
    esc_inner.addArrangedSubview_(
        native_ui.make_row(
            "Stage-3 webhook",
            webhook_field,
            help_text=(
                "Optional: when the chime stage lands, POST one JSON "
                "payload here (an ntfy topic, Home Assistant...) so a "
                "blocked agent can find you away from the desk. Blank "
                "= off; one call per ask episode, never retried."
            ),
        )
    )
    fields["escalation_webhook_field"] = webhook_field
    native_ui.add_separator(esc_inner)
    # Webhook bridge: moment events beyond stage-3, each opt-in. The
    # indicator escapes the device — Home Assistant, ntfy, a Hue
    # scene, anything that takes JSON.
    bridge_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    webhook_event_boxes: dict[str, object] = {}
    for label, event_key in (
        ("Completions", "completion"),
        ("Weather", "weather"),
        ("Timebox", "timebox"),
    ):
        box = native_ui.make_checkbox(label, target, "toggleWebhookEvent:")
        box.setIdentifier_(event_key)
        set_checkbox_state(box, event_key in target.settings.webhook_events)
        bridge_row.addArrangedSubview_(box)
        webhook_event_boxes[f"webhook_event:{event_key}"] = box
    bridge_row.addArrangedSubview_(native_ui.make_hspacer())
    esc_inner.addArrangedSubview_(
        native_ui.make_row(
            "Also send",
            bridge_row,
            help_text=(
                "Each ticked moment POSTs one JSON event to the same "
                "URL: completions, severe-weather onset, or timebox finish."
            ),
        )
    )
    stack.addArrangedSubview_(esc_outer)

    # Style cards: pick every signal's look by eye.
    for signal_key, card_title, show_color in SIGNAL_STYLE_CARDS:
        stack.addArrangedSubview_(
            make_signal_style_card(
                target, signal_key, card_title, show_color=show_color, fields=fields
            )
        )

    buttons = {
        "idle_dim_enabled": idle_switch,
        "subagent_asks_alert": subask_switch,
    }
    buttons.update(webhook_event_boxes)
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


def _build_notifications_pane(target: StatusBarController):
    """Messages: everything SidePulse may say to you in WORDS.

    Split out of Signals, which had grown into a seven-subject pane
    holding dimming, notifications, calendars, weather, quota and
    escalation at once. The lights and the words are different jobs --
    a light is peripheral, a banner is an interruption with text in it --
    and the owner asked for the words to have their own place.
    """
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    fields: dict[str, object] = {}

    # SidePulse-owned agent lifecycle notifications only. Foreign app
    # notifications are never observed or mirrored into the lights.
    notif_outer, notif_inner = native_ui.make_card("Agent Notifications")
    completion_row, completion_switch = native_ui.make_switch_row(
        "Sweep when any agent finishes",
        target,
        "toggleCompletionSweep:",
        help_text=(
            "A brief sweep in the finishing agent's own color — without "
            "it, a completion is invisible whenever another agent is "
            "still working."
        ),
    )
    notif_inner.addArrangedSubview_(completion_row)
    native_ui.add_separator(notif_inner)
    completion_banner_row, completion_banner_switch = native_ui.make_switch_row(
        "Post a macOS notification when a session finishes",
        target,
        "toggleCompletionNotification:",
        help_text=(
            "A content-free banner identifies only the provider and whether "
            "a session finished or needs you. Quiet Hour and Focus policies "
            "hold it."
        ),
    )
    notif_inner.addArrangedSubview_(completion_banner_row)
    native_ui.add_separator(notif_inner)
    notification_status = native_ui.make_label(
        target.notification_authorization_status_text(),
        secondary=True,
    )
    notification_permission = native_ui.make_button(
        "Enable Notifications…",
        target,
        "requestNotificationPermission:",
    )
    notification_permission.setHidden_(
        not target._notification_authorization_checked
        or target.notification_authorization_state.value != "not_determined"
    )
    notification_controls = native_ui.make_stack(
        orientation="horizontal",
        spacing=native_ui.SPACE_S,
    )
    notification_controls.addArrangedSubview_(notification_status)
    notification_controls.addArrangedSubview_(native_ui.make_hspacer())
    notification_controls.addArrangedSubview_(notification_permission)
    notif_inner.addArrangedSubview_(
        native_ui.make_row("macOS permission", notification_controls)
    )
    fields["notification_authorization_status"] = notification_status
    stack.addArrangedSubview_(notif_outer)

    # Who is allowed to interrupt: the ledger shows every machine, the
    # lights are only for the desk you are sitting at.
    interrupt_outer, interrupt_inner = native_ui.make_card("Who May Interrupt You")
    interrupt_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Agents on another Mac and agents in the cloud always appear in "
            "the menu. Whether they may also take a light here is a separate "
            "question, and the answer starts as no.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    remote_interrupt_row, remote_interrupt_switch = native_ui.make_switch_row(
        "Let other Macs' agents take a light here",
        target,
        "toggleRemoteInterrupts:",
        help_text=(
            "Off (default) means a peer's rows are ledger-only. On means "
            "every machine may interrupt unless you mute it below."
        ),
    )
    interrupt_inner.addArrangedSubview_(remote_interrupt_row)
    machine_boxes = _add_remote_machine_rows(target, interrupt_inner)
    stack.addArrangedSubview_(interrupt_outer)

    buttons = {
        "completion_sweep_enabled": completion_switch,
        "completion_notification": completion_banner_switch,
        "notification_permission": notification_permission,
        "remote_interrupts_enabled": remote_interrupt_switch,
    }
    buttons.update(machine_boxes)
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


def known_remote_machines(target) -> tuple[str, ...]:
    """Every peer machine the owner could have an opinion about.

    Union of what was actually seen this session and what the settings
    file already names, so a machine that is asleep right now does not
    silently lose the mute the owner set for it last week.
    """
    names: list[str] = []
    ledger = getattr(target, "current_merged_ledger", None)
    if ledger is not None:
        for item in getattr(ledger, "health", ()):
            if item.machine and item.machine not in names:
                names.append(item.machine)
        for row in getattr(ledger, "rows", ()):
            if row.is_remote and row.machine not in names:
                names.append(row.machine)
    remote = target.settings.remote_peers
    for machine in (*remote.unmuted_machines, *remote.muted_machines):
        if machine not in names:
            names.append(machine)
    return tuple(sorted(names))


def _add_remote_machine_rows(target, inner) -> dict[str, object]:
    """One checkbox per known peer machine, or an honest empty line."""
    machines = known_remote_machines(target)
    if not machines:
        native_ui.add_separator(inner)
        inner.addArrangedSubview_(
            native_ui.make_label(
                "No other Macs seen yet."
                if target.settings.remote_peers.enabled
                else "Turn on Other Macs in Agents & Providers first.",
                secondary=True,
                size=11.0,
            )
        )
        return {}
    policy = target.settings.remote_peers.interrupt_policy()
    boxes: dict[str, object] = {}
    for machine in machines:
        native_ui.add_separator(inner)
        box = native_ui.make_checkbox(
            f"Let {machine} interrupt",
            target,
            "toggleRemoteMachineInterrupt:",
        )
        box.setIdentifier_(machine)
        set_checkbox_state(box, policy.allows_machine(machine))
        inner.addArrangedSubview_(box)
        boxes[f"remote_machine:{machine}"] = box
    return boxes


def _build_extras_pane(target: StatusBarController):
    """Extras: the non-agent things the bar can also tell you about.

    Calendar, Reminders, weather and the (still withheld) quota effects
    used to live inside Signals, three cards below a dimming slider, with
    nothing tying them together except "we had nowhere else to put them".
    They are a coherent group — ambient facts that are not agents — and
    the owner asked for them to have their own area.
    """
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    fields: dict[str, object] = {}

    # Calendar & Reminders: warning lights, not a calendar app.
    cal_outer, cal_inner = native_ui.make_card("Calendar & Reminders")
    cal_row, cal_switch = native_ui.make_switch_row(
        "Glow before events start",
        target,
        "toggleCalendarAlerts:",
        help_text=(
            "A calm purple breathe on every surface while an event is "
            "about to begin. Turning this on asks macOS for Calendar "
            "access."
        ),
    )
    cal_inner.addArrangedSubview_(cal_row)
    # Same defect as the Alcove switch, one pane over. Turning this on
    # asks macOS once; if the answer is no — or was no a year ago, in
    # which case macOS never asks again — the switch stays ON forever
    # and nothing ever glows. The toggle handler says so in the status
    # line, but that line is gone by the next time anyone looks.
    calendar_access_label = native_ui.make_wrapping_label(
        calendar_access_status_text(target),
        secondary=True,
        size=11.0,
        max_width=360.0,
    )
    cal_inner.addArrangedSubview_(
        native_ui.make_row("Calendar access", calendar_access_label)
    )
    fields["calendar_access_status"] = calendar_access_label
    native_ui.add_separator(cal_inner)
    lead_field = native_ui.make_field(
        f"{target.settings.calendar_lead_minutes:g}",
        target=target,
        action="applyCalendarLead:",
    )
    native_ui.constrain_width(lead_field, 48.0)
    lead_controls = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_XS)
    lead_controls.addArrangedSubview_(lead_field)
    lead_controls.addArrangedSubview_(native_ui.make_label("minutes before", secondary=True))
    cal_inner.addArrangedSubview_(native_ui.make_row("Start glowing", lead_controls))
    native_ui.add_separator(cal_inner)
    rem_row, rem_switch = native_ui.make_switch_row(
        "Glow when a Reminder comes due",
        target,
        "toggleReminderAlerts:",
        help_text=(
            "A short amber glow the moment a Reminder's due time "
            "arrives. Turning this on asks macOS for Reminders access."
        ),
    )
    cal_inner.addArrangedSubview_(rem_row)
    reminders_access_label = native_ui.make_wrapping_label(
        reminders_access_status_text(target),
        secondary=True,
        size=11.0,
        max_width=360.0,
    )
    cal_inner.addArrangedSubview_(
        native_ui.make_row("Reminders access", reminders_access_label)
    )
    fields["reminders_access_status"] = reminders_access_label
    stack.addArrangedSubview_(cal_outer)
    fields["calendar_lead_field"] = lead_field

    # Weather: the switch and ITS location fields, together.
    weather_outer, weather_inner = native_ui.make_card("Weather")
    weather_row, weather_switch = native_ui.make_switch_row(
        "Flash on severe weather warnings",
        target,
        "toggleWeatherAlerts:",
        help_text=(
            "An urgent heartbeat while a Severe or Extreme National "
            "Weather Service warning covers your area. Location comes "
            "from your network address — no Location permission "
            "needed. A live agent ask still takes the bar first."
        ),
    )
    weather_inner.addArrangedSubview_(weather_row)
    native_ui.add_separator(weather_inner)
    lat_field = native_ui.make_field(
        ""
        if target.settings.weather_latitude is None
        else f"{target.settings.weather_latitude:g}",
        target=target,
        action="applyWeatherLocation:",
    )
    lon_field = native_ui.make_field(
        ""
        if target.settings.weather_longitude is None
        else f"{target.settings.weather_longitude:g}",
        target=target,
        action="applyWeatherLocation:",
    )
    native_ui.constrain_width(lat_field, 72.0)
    native_ui.constrain_width(lon_field, 72.0)
    location_controls = native_ui.make_stack(
        orientation="horizontal", spacing=native_ui.SPACE_XS
    )
    location_controls.addArrangedSubview_(lat_field)
    location_controls.addArrangedSubview_(native_ui.make_label("lat", secondary=True))
    location_controls.addArrangedSubview_(lon_field)
    location_controls.addArrangedSubview_(native_ui.make_label("lon", secondary=True))
    weather_inner.addArrangedSubview_(
        native_ui.make_row(
            "Location override",
            location_controls,
            help_text=(
                "Leave both blank to locate automatically from your "
                "network address. NWS alerts cover the United States "
                "and its territories."
            ),
        )
    )
    fields["weather_latitude_field"] = lat_field
    fields["weather_longitude_field"] = lon_field
    stack.addArrangedSubview_(weather_outer)

    # Capacity EFFECTS remain withheld. Capacity HISTORY is a different
    # decision — it is a record kept on this Mac, not an outbound event --
    # and it is the owner's to make, which is why it has a switch and the
    # effects have a sentence.
    quota_outer, quota_inner = native_ui.make_card("Quota")
    history_row, history_switch = native_ui.make_switch_row(
        "Remember how capacity moved",
        target,
        "toggleCapacityHistory:",
        help_text=(
            "Keeps the percentage left and the reset time for each "
            "window, on this Mac only, so “Why Is It Doing That?” "
            "can show the last day and week instead of only this "
            "moment. No prompts, no session names, no paths. Turning "
            "it off deletes the file."
        ),
    )
    quota_inner.addArrangedSubview_(history_row)
    native_ui.add_separator(quota_inner)
    retention_popup = native_ui.make_popup_button(target, "setCapacityHistoryRetention:")
    current_days = getattr(target.settings, "capacity_history_retention_days", 7)
    for days in (7, 30, 90):
        retention_popup.addItemWithTitle_(f"{days} days")
        item = retention_popup.lastItem()
        item.setRepresentedObject_(str(days))
        if days == current_days:
            retention_popup.selectItem_(item)
    quota_inner.addArrangedSubview_(native_ui.make_row("Keep for", retention_popup))
    native_ui.add_separator(quota_inner)
    quota_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Capacity alerts, outbound events, queue advice, and hardware "
            "runway are unavailable until a supported source and explicit "
            "forecast release authority exist.",
            secondary=True,
        )
    )
    stack.addArrangedSubview_(quota_outer)
    fields["capacity_history_retention_popup"] = retention_popup

    buttons = {
        "calendar_alerts_enabled": cal_switch,
        "reminder_alerts_enabled": rem_switch,
        "weather_alerts_enabled": weather_switch,
        "capacity_history_enabled": history_switch,
    }
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


# EventKit's authorization status is a TCC cache read, but the Extras
# pane can be rebuilt and refreshed repeatedly, so it is asked at most
# this often. Same reason — and the same shape — as the Screen
# Recording preflight cache in alcove_observation.
EVENT_ACCESS_TTL_SECONDS: Final = 5.0
_event_access_cache: dict[str, tuple[float, str]] = {}


def reset_event_access_cache() -> None:
    """Force the next read to ask EventKit again."""
    _event_access_cache.clear()


def _event_access_status(key: str, watch) -> str:
    """``watch.authorization_status()``, or "unavailable", cached briefly.

    The module raises rather than returning a falsy value, which is why
    "EventKit cannot be used on this Mac" survives as its own answer
    instead of arriving as the same "denied" a real denial produces.
    """
    now = time.monotonic()
    cached = _event_access_cache.get(key)
    if cached is not None and 0.0 <= now - cached[0] < EVENT_ACCESS_TTL_SECONDS:
        return cached[1]
    try:
        status = str(watch.authorization_status())
    except Exception:
        # Every entry point of calendar_watch/reminders_watch raises its
        # own Unavailable error for "EventKit missing" and for API drift.
        # That is a fourth state, not a denial, and it is named as one.
        status = "unavailable"
    _event_access_cache[key] = (now, status)
    return status


def _access_status_text(enabled: bool, status: str, *, subject: str, pane: str) -> str:
    """One sentence for a switch whose feature needs a macOS permission.

    The switch alone said the same thing — ON — whether the glow was
    working, whether macOS had refused a year ago and would never ask
    again, or whether EventKit was not usable at all. The toggle handler
    does say which, once, in a status line that is gone by the next time
    anyone opens this window.
    """
    if not enabled:
        return "Not used while this is off."
    if status == "authorized":
        return f"Granted — {subject} can start a glow."
    if status == "not_determined":
        return "macOS has not been asked yet. Switch this off and on again to ask."
    if status == "unavailable":
        return f"{pane} access is unavailable on this Mac, so nothing here can glow."
    return (
        f"Denied — no {subject} will glow until you turn SidePulse on "
        f"under Privacy & Security → {pane}."
    )


def calendar_access_status_text(target) -> str:
    # Probed only when the switch is on: `authorization_status` imports
    # EventKit, and a pane read has no business pulling a framework in
    # for a feature the owner turned off.
    enabled = bool(getattr(target.settings, "calendar_alerts_enabled", False))
    status = _event_access_status("calendar", calendar_watch) if enabled else ""
    return _access_status_text(enabled, status, subject="events", pane="Calendars")


def reminders_access_status_text(target) -> str:
    enabled = bool(getattr(target.settings, "reminder_alerts_enabled", False))
    status = _event_access_status("reminders", reminders_watch) if enabled else ""
    return _access_status_text(enabled, status, subject="reminders", pane="Reminders")


def refresh_event_access_controls(target) -> None:
    """Keep both rows current while the window is open.

    Panes are built once, lazily, so without this the row freezes at
    whatever was true the first time the pane was visited — including a
    stale "Denied" after the owner granted access in System Settings.
    The cache is NOT dropped here: its five seconds are shorter than any
    trip to System Settings, and clearing it would turn every unrelated
    settings action into two more EventKit reads.
    """
    fields = getattr(target, "settings_fields", None) or {}
    label = fields.get("calendar_access_status")
    if label is not None:
        label.setStringValue_(calendar_access_status_text(target))
    label = fields.get("reminders_access_status")
    if label is not None:
        label.setStringValue_(reminders_access_status_text(target))


FOCUS_DIM_CHOICES: tuple[tuple[str, str], ...] = (
    ("Shared dim (default)", "default"),
    ("Don't dim", "1.0"),
    ("Dim to 50%", "0.5"),
    ("Dim to 25%", "0.25"),
    ("Turn off", "0.0"),
)


def select_focus_dim_choice(popup, fraction: float | None) -> None:
    """Selects the popup item matching a saved rule (None = shared
    default) — refresh_settings_window's counterpart to
    make_focus_dim_popup's construction-time selection."""
    wanted = "default" if fraction is None else f"{float(fraction):g}"
    for index in range(popup.numberOfItems()):
        item = popup.itemAtIndex_(index)
        if str(item.representedObject() or "") == wanted:
            popup.selectItem_(item)
            return


def make_focus_dim_popup(target: StatusBarController, mode_identifier: str):
    popup = native_ui.make_popup_button(target, "setFocusDimRule:")
    popup.setIdentifier_(mode_identifier)
    current = target.settings.focus_dim_rules.get(mode_identifier)
    current_key = "default" if current is None else f"{current:g}"
    for label, key in FOCUS_DIM_CHOICES:
        popup.addItemWithTitle_(label)
        item = popup.lastItem()
        item.setRepresentedObject_(key)
        if key == current_key:
            popup.selectItem_(item)
    return popup


def _build_agents_pane(target: StatusBarController):
    """Everything about how SidePulse talks to coding agents: hook
    installs, the transcript-watching fallback, and which app opens a
    provider's sessions."""
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)

    bar_outer, bar_inner = native_ui.make_card("Menu Bar")
    label_row, label_switch = native_ui.make_switch_row(
        "Show status text next to the icon",
        target,
        "toggleMenuBarLabel:",
        help_text=(
            "Off keeps SidePulse icon-only like native menu extras; a "
            "count or check still appears when something needs you."
        ),
    )
    bar_inner.addArrangedSubview_(label_row)
    stack.addArrangedSubview_(bar_outer)
    hooks_outer, hooks_inner = native_ui.make_card("Hooks")
    fields: dict[str, object] = {}
    for index, provider in enumerate(HOOK_PROVIDERS):
        selector = provider.title()
        status_label = native_ui.make_label("", secondary=True, size=12.0)
        install_button = native_ui.make_button("Install", target, f"install{selector}Hooks:")
        uninstall_button = native_ui.make_button("Uninstall", target, f"uninstall{selector}Hooks:")
        controls = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
        controls.addArrangedSubview_(status_label)
        # One state-appropriate action per provider, not both at once --
        # refresh_settings_window hides whichever doesn't apply.
        controls.addArrangedSubview_(install_button)
        controls.addArrangedSubview_(uninstall_button)
        hooks_inner.addArrangedSubview_(native_ui.make_row(provider_spec(provider).label, controls))

        if index < len(HOOK_PROVIDERS) - 1:
            native_ui.add_separator(hooks_inner)
        fields[f"{provider}_hook_status"] = status_label
        fields[f"{provider}_hook_install"] = install_button
        fields[f"{provider}_hook_uninstall"] = uninstall_button
    stack.addArrangedSubview_(hooks_outer)

    fallback_outer, fallback_inner = native_ui.make_card("Transcript Fallback")
    fallback_help = (
        "Reads the CLI's transcript files directly when hook events aren't "
        "available — a fallback, not the primary detection path."
    )
    codex_row, codex_switch = native_ui.make_switch_row(
        "Watch Codex CLI transcripts", target, "toggleCodexTranscripts:", help_text=fallback_help
    )
    fallback_inner.addArrangedSubview_(codex_row)
    claude_row, claude_switch = native_ui.make_switch_row(
        "Watch Claude CLI transcripts", target, "toggleClaudeTranscripts:", help_text=fallback_help
    )
    fallback_inner.addArrangedSubview_(claude_row)
    stack.addArrangedSubview_(fallback_outer)

    openers_outer, openers_inner = native_ui.make_card("Open Sessions With")
    for provider in provider_session_opener_providers():
        popup = make_provider_opener_popup(provider, target)
        openers_inner.addArrangedSubview_(native_ui.make_row(provider_spec(provider).label, popup))
        fields[f"{provider}_session_opener"] = popup
    stack.addArrangedSubview_(openers_outer)

    # Pick a provider, choose how it moves. The same setting the Studio's
    # Animations section writes — one fact, two doors, and both of them
    # read `colors.provider_animation` rather than keeping a copy.
    anim_outer, anim_inner = native_ui.make_card("Agent Animation")
    anim_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Pick a provider, choose how it moves. Automatic follows the "
            "state (breathe when idle, chase while working). Whatever you "
            "choose, an agent that needs you keeps its urgent beat — that "
            "one is not up for negotiation.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    for index, row in enumerate(colors_module.provider_color_rows(target.settings.colors)):
        popup = make_agent_animation_popup(row, target)
        anim_inner.addArrangedSubview_(
            native_ui.make_row(
                _provider_display_label(row.provider),
                popup,
                help_text=row.animation_description,
            )
        )
        fields[f"{row.provider}_agent_animation"] = popup
        if index < len(PROVIDER_SPECS) - 1:
            native_ui.add_separator(anim_inner)
    stack.addArrangedSubview_(anim_outer)

    peers_outer, peers_inner, peer_buttons, peer_fields = _build_remote_peers_card(target)
    stack.addArrangedSubview_(peers_outer)
    fields.update(peer_fields)

    cloud_outer, cloud_inner, cloud_buttons, cloud_fields = _build_cloud_agents_card(target)
    stack.addArrangedSubview_(cloud_outer)
    fields.update(cloud_fields)

    buttons = {"codex_transcripts": codex_switch, "claude_transcripts": claude_switch}
    buttons.update(peer_buttons)
    buttons.update(cloud_buttons)
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


def _provider_display_label(provider: str) -> str:
    for spec in PROVIDER_SPECS:
        if spec.provider == provider:
            return spec.label
    return provider.title() or provider


def make_agent_animation_popup(row, target):
    """One provider's motion picker, bound straight to the controller.

    The Studio's own version of this row talks to SidePulseStudioActions,
    which only exists while the Studio pane is built. This one targets the
    controller, so the two panes can hold the same choice without either
    needing the other to be on screen.
    """
    popup = native_ui.make_popup_button(target, "setAgentAnimation:")
    popup.setIdentifier_(row.provider)
    for motion in colors_module.PROVIDER_ANIMATION_CHOICES:
        popup.addItemWithTitle_(colors_module.PROVIDER_ANIMATION_LABELS[motion])
        item = popup.lastItem()
        item.setRepresentedObject_({"provider": row.provider, "motion": motion})
        item.setToolTip_(colors_module.PROVIDER_ANIMATION_DESCRIPTIONS[motion])
        if motion == row.animation:
            popup.selectItem_(item)
    return popup


def _build_remote_peers_card(target: StatusBarController):
    """The second Mac. Every switch here starts OFF.

    Three separate consents, deliberately not one: reading peers, being
    readable BY peers, and letting the words of a session travel. A Mac
    that can see the other one is not automatically a Mac that publishes
    itself, and neither of those is permission to copy prompt text off
    the machine that produced it.
    """
    outer, inner = native_ui.make_card("Other Macs")
    inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Show agents running on your other Macs in this menu. Reads one "
            "small file per peer over Tailscale + SSH; nothing is ever run "
            "on the other machine, and no capacity number crosses the wire.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    enabled_row, enabled_switch = native_ui.make_switch_row(
        "Show agents from my other Macs",
        target,
        "toggleRemotePeers:",
        help_text=(
            "Discovers peers with the Tailscale CLI and fetches each one's "
            "published ledger once a minute, bounded at eight seconds total."
        ),
    )
    inner.addArrangedSubview_(enabled_row)
    native_ui.add_separator(inner)
    publish_row, publish_switch = native_ui.make_switch_row(
        "Let my other Macs see this one",
        target,
        "toggleRemotePublish:",
        help_text=(
            "Writes this Mac's own rows to a private file peers can read. "
            "Sub-agents are never published, and the working directory and "
            "launch origin never leave this machine."
        ),
    )
    inner.addArrangedSubview_(publish_row)
    native_ui.add_separator(inner)
    messages_row, messages_switch = native_ui.make_switch_row(
        "Include what each agent is asking",
        target,
        "toggleRemoteMessages:",
        help_text=(
            "Off (default) publishes the session name and its state, not "
            "its words. On sends the question text too — turn it on only "
            "for machines you would read those questions on."
        ),
    )
    inner.addArrangedSubview_(messages_row)
    native_ui.add_separator(inner)
    status_label = native_ui.make_label(
        remote_peer_status_text(target),
        secondary=True,
        size=11.0,
    )
    controls = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    controls.addArrangedSubview_(status_label)
    controls.addArrangedSubview_(native_ui.make_hspacer())
    controls.addArrangedSubview_(
        native_ui.make_button("Check Now", target, "refreshRemotePeersNow:")
    )
    inner.addArrangedSubview_(native_ui.make_row("Peers", controls))
    buttons = {
        "remote_peers_enabled": enabled_switch,
        "remote_publish_enabled": publish_switch,
        "remote_messages_enabled": messages_switch,
    }
    return outer, inner, buttons, {"remote_peers_status": status_label}


def remote_peer_status_text(target) -> str:
    """What the peer transport can honestly claim right now.

    "No peers found yet." was three different facts wearing one coat:
    Tailscale is not installed on this Mac at all, the refresh has not
    run yet, and the refresh ran and genuinely found nobody. The first
    of those is the only one the owner can act on, and it was the one
    the sentence actively argued against — "yet" promises a peer is
    still coming when there is no CLI to discover one with.
    """
    remote = target.settings.remote_peers
    if not remote.enabled:
        return "Off."
    if not remote_peers.tailscale_available():
        return (
            "Tailscale is not installed, so there is nothing to discover "
            "your other Macs with."
        )
    result = getattr(target, "_remote_refresh", None)
    health = tuple(getattr(result, "health", ()) or ())
    if not health:
        # `attempted` is the refresh's own count of peers it reached for.
        # Zero means no round trip has happened, which is not the same
        # answer as one that came back empty.
        if not int(getattr(result, "attempted", 0) or 0):
            return "No peers checked yet."
        return "No other Macs are running SidePulse right now."
    reachable = [item.machine for item in health if item.reachable]
    failed = [
        f"{item.machine} ({item.failure or 'unreachable'})"
        for item in health
        if not item.reachable
    ]
    parts = []
    if reachable:
        parts.append("Reading " + ", ".join(sorted(reachable)))
    if failed:
        parts.append("Cannot reach " + ", ".join(sorted(failed)))
    return ". ".join(parts) + "."


def _build_cloud_agents_card(target: StatusBarController):
    """The loopback door for agents that do not run on this Mac at all."""
    outer, inner = native_ui.make_card("Cloud Agents")
    inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Let a cloud agent — a hosted code review, say — post its own "
            "lifecycle to a loopback port so it appears here like any other "
            "session. Binds 127.0.0.1 only, and every request needs a token "
            "kept in your private state directory.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    enabled_row, enabled_switch = native_ui.make_switch_row(
        "Accept events from cloud agents",
        target,
        "toggleCloudIngest:",
        help_text=(
            "Off by default: this opens a listening socket, and any process "
            "running as you can reach a loopback port — which is why the "
            "token exists."
        ),
    )
    inner.addArrangedSubview_(enabled_row)
    native_ui.add_separator(inner)
    address_label = native_ui.make_label(
        cloud_ingest_status_text(target),
        secondary=True,
        size=11.0,
    )
    inner.addArrangedSubview_(native_ui.make_row("Listening", address_label))
    return (
        outer,
        inner,
        {"cloud_ingest_enabled": enabled_switch},
        {"cloud_ingest_status": address_label},
    )


def cloud_ingest_status_text(target) -> str:
    """The listening address, or why there is not one.

    "Enabled — starts with the app." was a PROMISE, and it was printed
    in the one case where the promise had already been broken:
    ``start_cloud_ingest_server`` sets ``cloud_ingest`` back to None and
    logs when the bind raises (a port already in use is the ordinary
    way), so a switch reading ON with no server is a failure that had
    happened minutes ago and this row was still saying it was coming.
    """
    if not target.settings.cloud_ingest_enabled:
        return "Off."
    server = getattr(target, "cloud_ingest", None)
    address = getattr(server, "address", None) if server is not None else None
    if address is None:
        # A reading, not a promise: whatever the reason, nothing on this
        # Mac is accepting cloud events right now.
        return "Nothing is listening — the loopback port could not be opened."
    return f"http://{address[0]}:{address[1]}/v1/agent-event"


# Curated one-shot lid looks. Built only from grammar primitives already
# proven against the firmware (colors, durations, pulse/cosine/linear,
# off) — a test parses every preset through the real sdled.wasm.
LID_ANIMATION_PRESETS: dict[str, tuple[tuple[str, float, str], ...]] = {
    LID_ANIMATION_CLOSED: (
        ("Fade Out", 1.0, "#8A7CFF 300ms pulse\noff 700ms cosine"),
        ("Blink Out", 0.9, "#FF4F79 150ms pulse\noff 150ms linear\n#FF4F79 150ms pulse\noff 450ms cosine"),
        ("Ember", 1.6, "#FF9F0A 500ms pulse\n#5A3A00 400ms cosine\noff 700ms cosine"),
        ("Cool Down", 1.4, "#00E5FF 350ms pulse\n#0044AA 450ms cosine\noff 600ms cosine"),
    ),
    LID_ANIMATION_OPEN: (
        ("Rise", 1.0, "off 100ms linear\n#00E5FF 400ms cosine\n#00E5FF 500ms pulse"),
        ("Hello", 1.4, "#12E3B0 300ms pulse\n#0FA07C 300ms cosine\n#12E3B0 800ms pulse"),
        ("Sunrise", 1.6, "#331A00 300ms cosine\n#FF9F0A 600ms cosine\n#FFD60A 700ms pulse"),
        ("Quick Blink", 0.8, "#FFFFFF 150ms pulse\noff 150ms linear\n#FFFFFF 500ms pulse"),
    ),
    # Agents-running variants: unmistakably different rhythms so the
    # lid itself tells you work is still cooking.
    LID_ANIMATION_CLOSED_ACTIVE: (
        ("Still Cooking", 1.5, "#FF9F0A 300ms pulse\n#FF9F0A 250ms cosine\n#5A3A00 350ms cosine\n#1A1200 600ms cosine"),
        ("Baton Pass", 1.2, "#00E5FF 250ms pulse\n#8A7CFF 250ms cosine\n#12E3B0 250ms pulse\noff 450ms cosine"),
        ("Ember Watch", 1.8, "#FF6A3D 400ms pulse\n#802000 500ms cosine\n#331000 900ms cosine"),
        # Named for the shape it actually has. Two equal hard-ish thumps
        # and a long rest is a KNOCK; a heartbeat's second thump is dimmer
        # than its first, which is the whole difference between the two in
        # the signal vocabulary. Calling this one "Heartbeat" left the
        # window using one word for two motions.
        ("Knock Out", 1.3, "#FF2D55 150ms pulse\noff 120ms linear\n#FF2D55 150ms pulse\noff 880ms cosine"),
    ),
    LID_ANIMATION_OPEN_ACTIVE: (
        ("Back On It", 1.2, "#12E3B0 200ms pulse\n#00E5FF 300ms cosine\n#00E5FF 700ms pulse"),
        ("Status Sweep", 1.4, "#8A7CFF 250ms pulse\n#00E5FF 250ms cosine\n#12E3B0 250ms pulse\n#12E3B0 650ms pulse"),
        ("Rekindle", 1.6, "#331000 300ms cosine\n#FF6A3D 500ms cosine\n#FFD60A 800ms pulse"),
        ("Double Take", 1.0, "#FFFFFF 120ms pulse\noff 100ms linear\n#00E5FF 180ms pulse\n#00E5FF 600ms pulse"),
    ),
}


def _build_lid_preset_row(target: StatusBarController, kind: str, current_program: str):
    """A strip of live-playing preset thumbnails; clicking one applies
    it. The raw editor below stays for hand-tuning — the picker is how
    normal people choose."""
    row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_M)
    thumbs = getattr(target, "lid_animation_thumbs", None)
    if thumbs is None:
        thumbs = {}
        target.lid_animation_thumbs = thumbs
    current_normalized = (current_program or "").strip()
    for name, duration, program in LID_ANIMATION_PRESETS[kind]:
        cell = native_ui.make_stack(orientation="vertical", spacing=2.0)
        view = _mini_led_view(96.0, 20.0)
        view.setProgram_startedAt_(program + "\nrepeat", time.monotonic())
        view.lid_preset_kind = kind
        view.lid_preset_name = name
        recognizer = NSClickGestureRecognizer.alloc().initWithTarget_action_(
            target, "selectLidPresetThumb:"
        )
        view.addGestureRecognizer_(recognizer)
        cell.addArrangedSubview_(view)
        label = native_ui.make_label(name, secondary=True, size=10.0)
        cell.addArrangedSubview_(label)
        row.addArrangedSubview_(cell)
        thumbs[(kind, name)] = view
        view.setToolTip_(f"{name} \u00b7 {duration:g}s")
        if program.strip() == current_normalized:
            layer = view.layer()
            if layer is not None:
                layer.setBorderWidth_(2.0)
                layer.setBorderColor_(NSColor.controlAccentColor().CGColor())
    row.addArrangedSubview_(native_ui.make_hspacer())
    return row


def _add_studio_card(target: StatusBarController, stack) -> None:

    # The Studio: write any LED program by hand, preview it on every
    # surface, and it's saved as yours. The DSL reference lives in
    # LEDS_FORMAT.md; the lid animations below use the same language.
    studio_outer, studio_inner = native_ui.make_card("Studio")
    studio_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Write your own light. Lines are steps — a color, a duration, "
            "an easing — and `repeat` loops them. Preview plays your "
            "program on the Screen Bar and every connected device.",
            secondary=True,
            size=11.0,
            max_width=520.0,
        )
    )
    studio_scroll, studio_editor = native_ui.make_text_editor(
        target.settings.studio_program or "#00E5FF 800ms pulse\noff 300ms cosine\nrepeat",
        height=110.0,
    )
    studio_inner.addArrangedSubview_(studio_scroll)
    target.studio_editor = studio_editor
    # Live validation, in sentences. What this replaces was a single
    # message that appeared only when you pressed a button and said
    # "syntax at line 2, column 7" — the firmware parser's vocabulary,
    # not a person's. These name the STEP and the fix, and they update
    # as you type because the checker is pure and never touches hardware.
    problem_label = native_ui.make_wrapping_label(
        "",
        secondary=True,
        size=11.0,
        max_width=520.0,
    )
    studio_inner.addArrangedSubview_(problem_label)
    target.studio_problem_label = problem_label
    try:
        studio_editor.setDelegate_(target)
    except Exception:
        pass
    target.refresh_studio_problem_label()
    studio_buttons = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    studio_buttons.addArrangedSubview_(
        native_ui.make_button("Preview on Everything", target, "previewStudioProgram:")
    )
    studio_buttons.addArrangedSubview_(native_ui.make_button("Stop", target, "stopStudioProgram:"))
    studio_buttons.addArrangedSubview_(
        native_ui.make_button("Set as Power-Up Look", target, "applyStudioAsPowerUp:")
    )
    studio_buttons.addArrangedSubview_(
        native_ui.make_button("Capture What's Playing", target, "captureStudioProgram:")
    )
    studio_buttons.addArrangedSubview_(native_ui.make_hspacer())
    studio_inner.addArrangedSubview_(studio_buttons)
    library_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    save_name = native_ui.make_field("", target=target, action="saveStudioLook:")
    native_ui.constrain_width(save_name, 140.0)
    library_row.addArrangedSubview_(save_name)
    library_row.addArrangedSubview_(
        native_ui.make_button("Save Look", target, "saveStudioLook:")
    )
    library_popup = native_ui.make_popup_button(target, "loadStudioLook:")
    library_row.addArrangedSubview_(library_popup)
    library_row.addArrangedSubview_(
        native_ui.make_button("Rename", target, "renameStudioLook:")
    )
    library_row.addArrangedSubview_(
        native_ui.make_button("Delete", target, "deleteStudioLook:")
    )
    library_row.addArrangedSubview_(
        native_ui.make_button("Burn as Power-Up", target, "burnStudioLookAsPowerUp:")
    )
    library_row.addArrangedSubview_(native_ui.make_hspacer())
    studio_inner.addArrangedSubview_(library_row)
    target.studio_save_name_field = save_name
    target.studio_library_popup = library_popup
    # Fill from the bounded private library, not from settings.json --
    # the popup is the one place a saved look is named, so it has to name
    # what is actually stored.
    target._refresh_studio_library_popup()
    stack.addArrangedSubview_(studio_outer)




def _build_lid_animations_pane(target: StatusBarController):
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    _add_studio_card(target, stack)


    closed_outer, closed_inner = native_ui.make_card("Lid Closed")
    closed_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "What the lights do the moment you close the lid. Pick a "
            "look, or edit the program below it.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    closed_inner.addArrangedSubview_(
        _build_lid_preset_row(
            target,
            LID_ANIMATION_CLOSED,
            target.settings.lid_closed_animation.program,
        )
    )
    native_ui.add_separator(closed_inner)
    closed_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "When agents are RUNNING as you close it, this plays instead "
            "-- so the lid itself says \u201cstill cooking\u201d:",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    closed_inner.addArrangedSubview_(
        _build_lid_preset_row(
            target,
            LID_ANIMATION_CLOSED_ACTIVE,
            target.settings.lid_closed_active_animation.program,
        )
    )
    native_ui.add_separator(closed_inner)
    closed_duration = native_ui.make_field("", target=target, action="saveLidAnimations:")
    native_ui.constrain_width(closed_duration, 60.0)
    closed_inner.addArrangedSubview_(native_ui.make_row("Duration (sec)", closed_duration))
    closed_scroll, closed_program = native_ui.make_text_editor("")
    # Programs commit when editing ends (see textDidEndEditing_), same
    # instant-apply contract as every field — no Save button.
    closed_program.setDelegate_(target)
    closed_inner.addArrangedSubview_(closed_scroll)
    closed_buttons = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    closed_buttons.addArrangedSubview_(native_ui.make_button("Preview", target, "previewLidClosedAnimation:"))
    closed_buttons.addArrangedSubview_(native_ui.make_button("Reset", target, "resetLidClosedAnimation:"))
    closed_buttons.addArrangedSubview_(native_ui.make_hspacer())
    closed_inner.addArrangedSubview_(closed_buttons)
    stack.addArrangedSubview_(closed_outer)

    open_outer, open_inner = native_ui.make_card("Lid Open")
    open_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "The greeting when you open it back up.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    open_inner.addArrangedSubview_(
        _build_lid_preset_row(
            target,
            LID_ANIMATION_OPEN,
            target.settings.lid_open_animation.program,
        )
    )
    native_ui.add_separator(open_inner)
    open_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "And the greeting when you come back to live agents:",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    open_inner.addArrangedSubview_(
        _build_lid_preset_row(
            target,
            LID_ANIMATION_OPEN_ACTIVE,
            target.settings.lid_open_active_animation.program,
        )
    )
    native_ui.add_separator(open_inner)
    open_duration = native_ui.make_field("", target=target, action="saveLidAnimations:")
    native_ui.constrain_width(open_duration, 60.0)
    open_inner.addArrangedSubview_(native_ui.make_row("Duration (sec)", open_duration))
    open_scroll, open_program = native_ui.make_text_editor("")
    open_program.setDelegate_(target)
    open_inner.addArrangedSubview_(open_scroll)
    open_buttons = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    open_buttons.addArrangedSubview_(native_ui.make_button("Preview", target, "previewLidOpenAnimation:"))
    open_buttons.addArrangedSubview_(native_ui.make_button("Reset", target, "resetLidOpenAnimation:"))
    open_buttons.addArrangedSubview_(native_ui.make_hspacer())
    open_inner.addArrangedSubview_(open_buttons)
    stack.addArrangedSubview_(open_outer)

    fields = {
        "closed_animation_program": closed_program,
        "closed_animation_duration": closed_duration,
        "open_animation_program": open_program,
        "open_animation_duration": open_duration,
    }
    return native_ui.wrap_in_scroll_pane(stack), fields


def _build_debug_pane(target: StatusBarController):
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    outer, inner = native_ui.make_card()

    status_label = native_ui.make_label("", secondary=True, size=12.0)
    inner.addArrangedSubview_(status_label)
    # The settings-file path lives here with the rest of the diagnostic
    # detail — not as a permanent footer under every pane, where it read
    # as debug output leaking into a settings window.
    settings_path_label = native_ui.make_label("", secondary=True, size=12.0)
    inner.addArrangedSubview_(settings_path_label)
    inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Safe diagnostic export is available in History. Content-bearing "
            "audit CSV and HTML are not exposed in Settings.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )

    stack.addArrangedSubview_(outer)
    fields = {"debug_log_status": status_label, "settings_path": settings_path_label}
    return native_ui.wrap_in_scroll_pane(stack), fields


def build_settings_window(target: StatusBarController) -> NSWindow:
    width, height = 900, 640
    # Fixed-size, like a Mac settings window should be (System Settings
    # itself is fixed-width): the panes are designed at this size, and a
    # user-resizable frame combined with a pure-Auto-Layout content
    # hierarchy is exactly the combination that produced both the
    # shrink-to-fitting-width bug and a dead band above the sidebar on
    # manual resize.
    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (width, height)), style, NSBackingStoreBuffered, False,
    )
    window.setTitle_("SidePulse Settings: Profile")
    window.setDelegate_(target)
    window.setReleasedWhenClosed_(False)
    window.center()

    root = NSView.alloc().init()
    window.setContentView_(root)
    # Same treatment build_colors_window documents for its own root: with
    # a pure-Auto-Layout content hierarchy, the window pulls itself to the
    # content's computed fitting width — which for a sidebar + a column
    # of natural-width form rows is well under the size this window is
    # designed at. Pin the design size explicitly; panes still lay out
    # freely inside it (and the window stays user-resizable above it).
    # Equality, not >=: a floor without a ceiling lets any
    # width-preferring content (wrap_in_scroll_pane's centered column)
    # inflate the content view PAST the fixed window frame, which then
    # clips it at the right edge instead of resizing.
    root.setTranslatesAutoresizingMaskIntoConstraints_(False)
    root.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    root.heightAnchor().constraintEqualToConstant_(height).setActive_(True)

    split = NSSplitView.alloc().init()
    split.setVertical_(True)
    split.setDividerStyle_(NSSplitViewDividerStyleThin)
    split.setTranslatesAutoresizingMaskIntoConstraints_(False)
    root.addSubview_(split)

    sidebar_bg = native_ui.make_sidebar_background()
    sidebar_scroll, sidebar_table = native_ui.build_sidebar_table(width=220.0)
    sidebar_bg.addSubview_(sidebar_scroll)
    NSLayoutConstraint.activateConstraints_(
        [
            sidebar_scroll.topAnchor().constraintEqualToAnchor_(sidebar_bg.topAnchor()),
            sidebar_scroll.leadingAnchor().constraintEqualToAnchor_(sidebar_bg.leadingAnchor()),
            sidebar_scroll.trailingAnchor().constraintEqualToAnchor_(sidebar_bg.trailingAnchor()),
            sidebar_scroll.bottomAnchor().constraintEqualToAnchor_(sidebar_bg.bottomAnchor()),
        ]
    )
    sidebar_table.setDataSource_(target)
    sidebar_table.setDelegate_(target)

    content_container = NSView.alloc().init()
    content_container.setTranslatesAutoresizingMaskIntoConstraints_(False)

    split.addSubview_(sidebar_bg)
    split.addSubview_(content_container)
    sidebar_bg.widthAnchor().constraintEqualToConstant_(220.0).setActive_(True)
    sidebar_bg.widthAnchor().constraintGreaterThanOrEqualToConstant_(160.0).setActive_(True)
    split.setHoldingPriority_forSubviewAtIndex_(250, 0)

    # The footer holds only the transient confirmation message ("Screen
    # Bar now extends...") — diagnostic paths live in the Debug pane.
    footer = native_ui.make_stack(orientation="vertical", spacing=2.0)
    message = native_ui.make_label("", secondary=True, size=12.0)
    footer.addArrangedSubview_(message)
    root.addSubview_(footer)

    NSLayoutConstraint.activateConstraints_(
        [
            split.topAnchor().constraintEqualToAnchor_(root.topAnchor()),
            split.leadingAnchor().constraintEqualToAnchor_(root.leadingAnchor()),
            split.trailingAnchor().constraintEqualToAnchor_(root.trailingAnchor()),
            split.bottomAnchor().constraintEqualToAnchor_constant_(footer.topAnchor(), -8.0),
            footer.leadingAnchor().constraintEqualToAnchor_constant_(root.leadingAnchor(), 16.0),
            footer.trailingAnchor().constraintEqualToAnchor_constant_(root.trailingAnchor(), -16.0),
            footer.bottomAnchor().constraintEqualToAnchor_constant_(root.bottomAnchor(), -10.0),
        ]
    )

    # Audit #5: panes build LAZILY, on first visit — the gear click
    # used to construct all ten panes (and ~98 WASM preview engines,
    # one JSContext each) before the window could even appear. Only the
    # default pane exists now; ensure_settings_pane() installs the rest
    # as the sidebar reaches them, merging their fields/buttons in.
    target.settings_sidebar_table = sidebar_table
    target._settings_pane_container = content_container
    target.settings_panes = {}
    target.settings_fields = {"message": message}
    target.settings_buttons = {}
    target.device_settings_controls = {}
    target.ensure_settings_pane(DEFAULT_SETTINGS_PANE)
    default_pane = target.settings_panes.get(DEFAULT_SETTINGS_PANE)
    if default_pane is not None:
        default_pane.setHidden_(False)
    return window


def _build_settings_pane(target: StatusBarController, key: str):
    """(pane, fields, buttons) for one sidebar key — fields and
    buttons may be empty. The Devices builder also installs its own
    control map (it owns per-device rows)."""
    if key == "profile":
        pane, fields = _build_profile_pane(target)
        return pane, fields, {}
    if key == "history":
        return _build_history_pane(target)
    if key == "capacity":
        return _build_capacity_pane(target)
    if key == "devices":
        pane, device_controls = _build_devices_pane(target)
        target.device_settings_controls = device_controls
        return pane, {}, {}
    if key == "color_studio":
        return _build_color_studio_pane(target), {}, {}
    if key == "colors_screen_bar":
        return _build_colors_screen_bar_pane(target)
    if key == "agents":
        return _build_agents_pane(target)
    if key == "installed_agents":
        return _build_installed_agents_pane(target)
    if key == "led_behavior":
        return _build_led_behavior_pane(target)
    if key == "notifications":
        return _build_notifications_pane(target)
    if key == "extras":
        return _build_extras_pane(target)
    if key == "focus":
        return _build_focus_pane(target)
    if key == "power":
        return _build_power_pane(target)
    if key == "animations":
        pane, fields = _build_lid_animations_pane(target)
        return pane, fields, {}
    if key == "debug":
        pane, fields = _build_debug_pane(target)
        return pane, fields, {}
    raise KeyError(key)


# One source for the state row's words: the model names the row, the view
# renders it. status_bar imports this name, so it stays a dict.
MODE_COLOR_DISPLAY_LABELS: dict[str, str] = dict(MODE_ROW_LABELS)

COLOR_SWATCH_SIZE = 22
COLOR_SWATCH_GAP = 8
COLOR_ROW_HEIGHT = 40


def _build_agent_or_mode_color_row(row, target, actions, swatches, hex_labels):
    """One state's colour row, rendered straight from
    ``colors.mode_color_row()`` — identity, then a labelled Default group,
    then Palette, then the picker.

    Three things this replaces, all reproduced live before the rewrite:

    1. It drew ``CURATED_PALETTE[:6]`` and nothing else. Not one of the four
       shipped state colours is in that strip, so on a fresh install every
       state row rendered with ZERO chips ringed — the row could not show
       you what it was currently set to.
    2. Its picker chip was hardcoded ``name="Pick…"`` with ``selected``
       never set, so a hand-picked colour could not become the named,
       ringed "Custom" chip that provider rows get.
    3. The picker button was a throwaway local: built, added to the row, and
       dropped. Nothing could repaint it, so after a palette button or Reset
       the chip kept whatever colour it was born with.
    """
    stack = native_ui.make_stack(orientation="vertical", spacing=native_ui.SPACE_XS)
    row_key = ("mode", row.key)
    header, name_label, hex_label = _identity_view(row)
    stack.addArrangedSubview_(header)

    sync = _row_sync(
        target,
        actions,
        row_key,
        lambda colors, key=row.key: colors_module.mode_color_row(key, colors),
        picker_payload={"key": row.key},
        hex_labels=hex_labels,
    )
    sync.add_identity(name_label, hex_label)

    def register(swatch, button):
        if not swatch.opens_picker:
            swatches[(row_key, swatch.hex)] = button

    for group in row.groups:
        if group.key == colors_module.SWATCH_GROUP_CUSTOM:
            group_row, picker = _build_picker_group_row(
                group, target, "openCustomModeColor:", {"key": row.key}
            )
            sync.add_picker(picker)
            stack.addArrangedSubview_(group_row)
            continue
        group_row, _buttons = _build_swatch_group_row(
            group,
            target,
            actions,
            "selectModeColorSwatch:",
            {"key": row.key},
            hover=(actions.hover_mode_color if actions is not None else None),
            register=register,
            action_target=target,
        )
        stack.addArrangedSubview_(group_row)

    return stack


# --- Colour / Animation Studio ---------------------------------------------
#
# The Studio replaces the old scattered-cards Colors pane. Three things were
# wrong with what it replaces, and all three are structural rather than
# cosmetic:
#
#   1. The "Agent Colors" card said in body text that its first four swatches
#      were the Claude/OpenAI/Codex/Gemini brand colours. They were not — the
#      strip it drew was CURATED_PALETTE, whose first four are system red,
#      blue, green and purple. A source comment in this very file already
#      admitted the anonymous swatches made the tip "a lie".
#   2. Rows carried no provider identity, so which row was which depended on
#      remembering PROVIDER_SPECS order.
#   3. Colour, animation and preview were separate cards in one long column
#      with no home, and nothing let you see a change before committing it.
#
# So: identity leads every row, groups are LABELLED, every chip is NAMED, and
# Colours / Animations / Preview are peer sections of one surface. What each
# row contains is decided in colors.provider_color_rows(); everything below is
# a renderer over that model, which is what makes the interesting half
# testable without an NSView.

STUDIO_SWATCH_SIZE = 20.0
STUDIO_SWATCH_COLUMN_WIDTH = 50.0
STUDIO_GROUP_LABEL_WIDTH = 58.0
STUDIO_COMPARE_LED_COUNT = 8
STUDIO_IDLE_COMPARE_CAPTION = "Hover any color or animation to try it here first."


def hardware_preview_enabled(target) -> bool:
    """Does a COMMITTED colour change reach connected hardware right now?

    One reader, one default. This flag was read with ``default=False`` where
    the answer decided whether to push to a device and ``default=True`` where
    it decided how to draw the switch, so a controller that had not set the
    attribute would have shown a switch that was ON while behaving as OFF.
    False is the right default for both: never touch hardware on the strength
    of an attribute nobody set.
    """
    return bool(getattr(target, "color_preview_enabled", False))


class SidePulseStudioActions(NSObject):
    """Action target for the Studio's own controls.

    StatusBarController owns every other selector in this window, but it
    lives in status_bar.py and this file may not edit it. An NSObject
    subclass is the requirement rather than a preference: PyObjC dispatches
    target/action through respondsToSelector:, which a plain Python object
    cannot satisfy. The controller retains this via ``target.studio_actions``.
    """

    def initWithController_(self, controller):
        self = objc.super(SidePulseStudioActions, self).init()
        if self is None:
            return None
        self.controller = controller
        self.section = colors_module.normalize_studio_section(
            getattr(controller, "studio_section", None)
        )
        self.section_views = {}
        self.section_segmented = None
        self.section_subtitle = None
        self.animation_popups = {}
        self.animation_thumbs = {}
        self.animation_hover_areas = {}
        self.mode_animation_hover_areas = {}
        self.preview_session = colors_module.StudioPreviewSession(
            controller.settings.colors
        )
        return self

    # --- sections ---

    @objc.IBAction
    def showStudioSection_(self, sender):
        try:
            index = int(sender.selectedSegment())
        except Exception:
            return
        choices = colors_module.STUDIO_SECTION_CHOICES
        if not 0 <= index < len(choices):
            return
        self.select_section(choices[index])

    @objc.python_method
    def select_section(self, section: str) -> str:
        section = colors_module.normalize_studio_section(section)
        self.section = section
        self.controller.studio_section = section
        for key, view in self.section_views.items():
            view.setHidden_(key != section)
        if self.section_subtitle is not None:
            self.section_subtitle.setStringValue_(
                colors_module.STUDIO_SECTION_SUBTITLES[section]
            )
        if self.section_segmented is not None:
            self.section_segmented.setSelectedSegment_(
                colors_module.STUDIO_SECTION_CHOICES.index(section)
            )
        # Leaving the section under the pointer must not leave its preview
        # holding the Screen Bar.
        self.end_preview()
        return section

    # --- per-provider animation ---

    @objc.IBAction
    def selectProviderAnimation_(self, sender):
        item = sender.selectedItem() if sender is not None else None
        payload = item.representedObject() if item is not None else None
        if not payload:
            return
        self.apply_provider_animation(payload.get("provider"), payload.get("motion"))

    @objc.python_method
    def apply_provider_animation(self, provider: str, motion: str) -> bool:
        if not provider or motion not in colors_module.PROVIDER_ANIMATION_CHOICES:
            return False
        controller = self.controller
        colors = controller.settings.colors.with_agent_animation(provider, motion)
        controller.settings = controller.settings.with_colors(colors)
        save_settings(controller.settings)
        self.preview_session.commit(colors)
        self.release_screen_bar()
        controller.refresh_colors_window()
        if hardware_preview_enabled(controller):
            controller.push_colors_preview_to_device()
        controller.refresh_(None)
        controller.set_settings_message(
            f"{colors_module.provider_color_row(provider, colors).label}: "
            f"{colors_module.PROVIDER_ANIMATION_LABELS.get(motion, motion)}."
        )
        return True

    # --- committing a colour ---

    @objc.IBAction
    def commitProviderColor_(self, sender):
        payload = sender.representedObject() or {}
        self.apply_provider_color(payload.get("provider"), payload.get("hex"))

    @objc.python_method
    def apply_provider_color(self, provider: str, hex_value: str) -> bool:
        if not provider or not hex_value:
            return False
        controller = self.controller
        colors = controller.settings.colors.with_agent_color(provider, hex_value)
        controller.settings = controller.settings.with_colors(colors)
        save_settings(controller.settings)
        self.preview_session.commit(colors)
        # Drop the hover hold BEFORE the repaint, so the Screen Bar lands on
        # the committed colour rather than replaying the pre-hover frame.
        self.release_screen_bar()
        controller.refresh_colors_window()
        if hardware_preview_enabled(controller):
            controller.push_colors_preview_to_device()
        controller.refresh_(None)
        row = colors_module.provider_color_row(provider, colors)
        controller.set_settings_message(f"{row.label}: {row.current_name}.")
        return True

    # --- uncommitted preview ---

    @objc.python_method
    def _preview_statuses(self):
        snapshot = getattr(self.controller, "last_snapshot", None)
        return snapshot.statuses if snapshot is not None else ()

    @objc.python_method
    def preview_colors(self, colors, caption: str, *, statuses=None) -> None:
        self.preview_session.preview(colors)
        refresh_studio_compare(self.controller, colors, caption, statuses=statuses)
        device = getattr(self.controller, "virtual_status_device", None)
        holder = getattr(device, "hold_preview_program", None)
        if not callable(holder):
            return
        try:
            holder(
                colors_module.studio_preview_program(
                    colors,
                    statuses=self._preview_statuses() if statuses is None else statuses,
                    scenario=(
                        colors_module.PREVIEW_SCENARIO_LIVE
                        if statuses is not None
                        else getattr(
                            self.controller,
                            "color_preview_scenario",
                            colors_module.PREVIEW_SCENARIO_LIVE,
                        )
                    ),
                )
            )
        except Exception:
            # A preview must never be able to take the window down with it.
            pass

    @objc.python_method
    def release_screen_bar(self) -> bool:
        device = getattr(self.controller, "virtual_status_device", None)
        release = getattr(device, "release_preview_program", None)
        if not callable(release):
            return False
        try:
            return bool(release())
        except Exception:
            return False

    @objc.python_method
    def end_preview(self) -> None:
        """Pointer left, or the ground moved. Idempotent by construction so
        every path that *might* have started a preview can end one."""
        self.preview_session.revert()
        self.release_screen_bar()
        refresh_studio_compare(self.controller, None, STUDIO_IDLE_COMPARE_CAPTION)

    @objc.python_method
    def hover_color(self, button) -> None:
        payload = button.representedObject() or {}
        provider = payload.get("provider")
        hex_value = payload.get("hex")
        if not provider or not hex_value:
            return
        colors = self.controller.settings.colors.with_agent_color(provider, hex_value)
        row = colors_module.provider_color_row(provider, colors)
        self.preview_colors(
            colors,
            f"Trying {row.label}: {row.current_name}",
            statuses=colors_module.provider_preview_statuses(provider),
        )

    @objc.python_method
    def hover_end(self, _button) -> None:
        self.end_preview()

    @objc.python_method
    def hover_mode_color(self, button) -> None:
        payload = button.representedObject() or {}
        key = payload.get("key")
        hex_value = payload.get("hex")
        if not key or not hex_value:
            return
        colors = self.controller.settings.colors.with_mode_color(key, hex_value)
        self.preview_colors(
            colors,
            f"Trying {MODE_COLOR_DISPLAY_LABELS.get(key, key)}: "
            f"{colors_module.swatch_name(hex_value)}",
        )

    @objc.python_method
    def hover_provider_animation(self, view) -> None:
        """Hovering a provider's rhythm thumb plays that rhythm, in that
        provider's colour, alone on the Screen Bar — the same bargain
        hovering a colour makes. Without this the pane's own body copy
        ("hover any color or animation") was simply untrue."""
        provider = (getattr(view, "hover_payload", None) or {}).get("provider")
        if not provider:
            return
        colors = self.controller.settings.colors
        row = colors_module.provider_color_row(provider, colors)
        self.preview_colors(
            colors,
            f"Trying {row.label}: {row.animation_label}",
            statuses=colors_module.provider_preview_statuses(provider),
        )

    @objc.python_method
    def hover_mode_animation(self, view) -> None:
        payload = getattr(view, "hover_payload", None) or {}
        key = payload.get("key")
        style = payload.get("style")
        if not key or not style:
            return
        try:
            colors = self.controller.settings.colors.with_mode_animation(key, style)
        except ValueError:
            return
        self.preview_colors(
            colors,
            f"Trying {MODE_COLOR_DISPLAY_LABELS.get(key, key)}: "
            f"{ANIMATION_STYLE_DISPLAY_LABELS.get(style, style.title())}",
        )


class _StudioRowSync:
    """Stands in for one row's hex label in ``color_hex_labels``.

    StatusBarController.refresh_color_row re-rings the row's swatches and
    then calls ``setStringValue_(hex)`` on whatever it finds there. Putting
    this object in that slot is how the Studio's extra per-row chrome — the
    colour's NAME, the picker chip, the animation popup, the sentence
    explaining the animation — stays in sync with settings changed from
    anywhere else (a palette button, Reset to Defaults) without adding a
    hook inside a file this module may not edit.

    THE RULE THIS CLASS EXISTS TO ENFORCE: if a view can go stale, this
    object holds a live reference to it. The shipped bug was the opposite
    — views built as throwaway locals, added to a stack, and dropped:

      * the per-provider animation DESCRIPTION (settings_window.py:3155)
        was a local, so choosing Chase left the popup reading "Chase" above
        a sentence still reading "Follows the state...";
      * the animation thumb's TOOLTIP was set once at build time and never
        re-set, stale the same way;
      * the Animations section's own identity line (provider name + hex)
        was discarded as ``_name_label, _hex_label``, so recolouring a
        provider in the Colors section left the Animations section showing
        the old hex;
      * the state rows' picker chip was dropped entirely.

    Nothing rebuilds this pane — it is cached for the window's lifetime --
    so a dropped reference is a permanently wrong string, not a flicker.
    Every registration below is therefore a list: one row can be drawn in
    more than one section, and all of its copies have to agree.
    """

    def __init__(self, target, actions, row_key, row_for_colors, *, picker_payload):
        self.target = target
        self.actions = actions
        self.row_key = row_key
        self._row_for_colors = row_for_colors
        self.picker_payload = dict(picker_payload)
        self.identity_labels: list[tuple[object, object]] = []
        self.description_labels: list[object] = []
        self.pickers: list[object] = []

    # --- registration (every one of these is a reference kept alive) ---

    def add_identity(self, name_label, hex_label) -> None:
        self.identity_labels.append((name_label, hex_label))

    def add_description(self, label) -> None:
        self.description_labels.append(label)

    def add_picker(self, button) -> None:
        if button is not None:
            self.pickers.append(button)

    # --- the model this row renders ---

    def row(self):
        return self._row_for_colors(self.target.settings.colors)

    @property
    def provider(self) -> str:
        return getattr(self.row(), "provider", "")

    @property
    def name_label(self):
        return self.identity_labels[0][0] if self.identity_labels else None

    @property
    def hex_label(self):
        return self.identity_labels[0][1] if self.identity_labels else None

    def setStringValue_(self, _value) -> None:
        # The MODEL is the source, not the string passed in: a label that
        # renders its argument can disagree with settings; one that renders
        # the row cannot.
        row = self.row()
        for name_label, hex_label in self.identity_labels:
            hex_label.setStringValue_(row.current_hex)
            name_label.setStringValue_(row.current_name)
        description = getattr(row, "animation_description", "")
        for label in self.description_labels:
            label.setStringValue_(description)
        picker_swatch = row.picker_swatch
        for picker in self.pickers:
            _paint_swatch(picker, picker_swatch)
            picker.setRepresentedObject_(dict(self.picker_payload))
            caption = getattr(picker, "studio_caption", None)
            if caption is not None:
                caption.setStringValue_(picker_swatch.name)
        provider = getattr(row, "provider", "")
        if not provider:
            return
        popup = self.actions.animation_popups.get(provider)
        if popup is not None:
            select_popup_item(popup, "motion", row.animation)
        thumb = self.actions.animation_thumbs.get(provider)
        if thumb is not None:
            thumb.setProgram_(_provider_animation_thumb_program(self.target, row))
            thumb.setToolTip_(description)

    def stringValue(self) -> str:
        label = self.hex_label
        return label.stringValue() if label is not None else ""


def _paint_swatch(button, swatch) -> None:
    """Colour, ring and tooltip for one chip, from the model.

    A picker that is not itself the selection renders neutral (the hollow
    "+" chip this window has always used) rather than wearing the row's
    current colour — otherwise the same colour appears twice on one row and
    reads as a duplicate swatch.
    """
    try:
        button.setWantsLayer_(True)
        layer = button.layer()
        if swatch.is_control:
            button.setTitle_("+")
            layer.setBackgroundColor_(NSColor.controlColor().CGColor())
            layer.setBorderWidth_(1.0)
            layer.setBorderColor_(NSColor.separatorColor().CGColor())
        else:
            button.setTitle_("")
            layer.setBackgroundColor_(nscolor_from_hex(swatch.hex).CGColor())
            layer.setBorderWidth_(2.5 if swatch.selected else 0.0)
            layer.setBorderColor_(NSColor.controlAccentColor().CGColor())
        layer.setCornerRadius_(STUDIO_SWATCH_SIZE / 2.0)
    except Exception:
        pass
    button.setToolTip_(swatch.tooltip)


def _studio_swatch_column(swatch, target, selector: str, represented: dict, actions=None):
    """One chip with its NAME underneath. Returns (column, button)."""
    button = native_ui.make_swatch_button(
        swatch.hex,
        size=STUDIO_SWATCH_SIZE,
        target=actions if actions is not None else target,
        selector=selector,
        represented=represented,
        color_for_hex=nscolor_from_hex,
    )
    _paint_swatch(button, swatch)
    caption = native_ui.make_caption(swatch.name)
    column = native_ui.make_stack(orientation="vertical", spacing=1.0)
    column.setAlignment_(NSLayoutAttributeCenterX)
    column.addArrangedSubview_(button)
    column.addArrangedSubview_(caption)
    native_ui.constrain_width(column, STUDIO_SWATCH_COLUMN_WIDTH)
    button.studio_caption = caption
    return column, button


def _build_swatch_group_row(
    group, target, actions, selector, represented, *, hover, register, action_target=None
):
    """A LABELLED run of named chips: "Brand  [Claude][OpenAI]...".

    The label is the whole point — it is the only thing on screen that says
    these four colours are brands rather than an arbitrary strip.

    ``action_target`` is which object the chips send their selector to, and
    it is NOT always ``actions``: the state rows' ``selectModeColorSwatch:``
    lives on StatusBarController, only the Studio's own selectors live on
    SidePulseStudioActions. Defaults to ``actions`` when given, else target.
    """
    label = native_ui.make_label(group.label, secondary=True, size=11.0)
    native_ui.constrain_width(label, STUDIO_GROUP_LABEL_WIDTH)
    label.setToolTip_(group.hint)
    row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_XS)
    row.setAlignment_(NSLayoutAttributeTop)
    row.addArrangedSubview_(label)
    buttons = []
    for swatch in group.swatches:
        column, button = _studio_swatch_column(
            swatch,
            target,
            selector,
            {**represented, "hex": swatch.hex},
            actions=action_target if action_target is not None else actions,
        )
        if hover is not None and actions is not None:
            button.hover_enter = hover
            button.hover_exit = actions.hover_end
        register(swatch, button)
        buttons.append(button)
        row.addArrangedSubview_(column)
    row.addArrangedSubview_(native_ui.make_hspacer())
    return row, buttons


def _identity_view(row):
    """Identity first: the row's own icon when the machine has one, its name
    always, and the colour it is currently wearing spelled out in words as
    well as hex.

    Returns (header, name_label, hex_label). BOTH labels must be handed to
    the row's _StudioRowSync by every caller — a section that draws this
    header and drops the labels is a section that will keep showing a colour
    the row no longer has.
    """
    header = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_XS)
    icon_image = None
    provider = getattr(row, "provider", "")
    if provider:
        try:
            icon_image = provider_icon_for_provider(provider)
        except Exception:
            icon_image = None
    if icon_image is not None:
        icon = NSImageView.imageViewWithImage_(icon_image)
        icon.setTranslatesAutoresizingMaskIntoConstraints_(False)
        native_ui.constrain_width(icon, 16.0)
        native_ui.constrain_height(icon, 16.0)
        header.addArrangedSubview_(icon)
    header.addArrangedSubview_(native_ui.make_label(row.label, size=13.0, bold=True))
    name_label = native_ui.make_label(row.current_name, secondary=True, size=11.0)
    header.addArrangedSubview_(name_label)
    hex_label = native_ui.make_label(row.current_hex, secondary=True, size=11.0)
    header.addArrangedSubview_(hex_label)
    header.addArrangedSubview_(native_ui.make_hspacer())
    return header, name_label, hex_label


def _build_picker_group_row(group, target, selector: str, represented: dict):
    """The labelled "Custom [chip]" run. The picker keeps the controller's
    own colour-panel selector: NSColorPanel plumbing already lives there and
    is not worth duplicating for one chip. Returns (row, button) — the
    button is a REFERENCE the caller must register, never a local to drop.
    """
    column, picker = _studio_swatch_column(
        group.swatches[0], target, selector, dict(represented)
    )
    custom_row = native_ui.make_stack(
        orientation="horizontal", spacing=native_ui.SPACE_XS
    )
    custom_row.setAlignment_(NSLayoutAttributeTop)
    label = native_ui.make_label(group.label, secondary=True, size=11.0)
    native_ui.constrain_width(label, STUDIO_GROUP_LABEL_WIDTH)
    label.setToolTip_(group.hint)
    custom_row.addArrangedSubview_(label)
    custom_row.addArrangedSubview_(column)
    custom_row.addArrangedSubview_(native_ui.make_hspacer())
    return custom_row, picker


def _row_sync(target, actions, row_key, row_for_colors, *, picker_payload, hex_labels):
    """One sync object per row, shared by every section that draws the row.

    Created on first use rather than at the end of the Colors section: the
    Animations section draws the same providers and has to register ITS
    identity labels and description with the SAME object, or half the pane
    goes stale the first time anything changes.
    """
    existing = hex_labels.get(row_key)
    if existing is None:
        existing = _StudioRowSync(
            target, actions, row_key, row_for_colors, picker_payload=picker_payload
        )
        hex_labels[row_key] = existing
    return existing


def _build_provider_color_row(row, target, actions, swatches, hex_labels):
    """Identity, then a labelled Brand group, then Palette, then the row's
    own colour. Rendered straight from colors.provider_color_row()."""
    stack = native_ui.make_stack(orientation="vertical", spacing=native_ui.SPACE_XS)
    header, name_label, hex_label = _identity_view(row)
    stack.addArrangedSubview_(header)

    row_key = ("agent", row.provider)
    sync = _row_sync(
        target,
        actions,
        row_key,
        lambda colors, provider=row.provider: colors_module.provider_color_row(
            provider, colors
        ),
        picker_payload={"provider": row.provider},
        hex_labels=hex_labels,
    )
    sync.add_identity(name_label, hex_label)

    def register(swatch, button):
        if not swatch.opens_picker:
            swatches[(row_key, swatch.hex)] = button

    for group in row.groups:
        if group.key == colors_module.SWATCH_GROUP_CUSTOM:
            group_row, picker = _build_picker_group_row(
                group, target, "openCustomAgentColor:", {"provider": row.provider}
            )
            sync.add_picker(picker)
            stack.addArrangedSubview_(group_row)
            continue
        group_row, _buttons = _build_swatch_group_row(
            group,
            target,
            actions,
            "commitProviderColor:",
            {"provider": row.provider},
            hover=actions.hover_color,
            register=register,
        )
        stack.addArrangedSubview_(group_row)

    return stack


def _provider_animation_thumb_program(target: StatusBarController, row) -> str:
    """This provider's chosen rhythm, played in this provider's own colour --
    so "Chase" is a thing you watch, not a word you have to trust."""
    colors = target.settings.colors
    motion = row.animation
    if motion == colors_module.PROVIDER_ANIMATION_AUTO:
        # Automatic means "whatever Working already does", so show that.
        style = colors.animation_style(colors_module.MODE_WORKING)
    else:
        style = colors_module.PROVIDER_ANIMATION_STYLES.get(motion, ANIMATION_STYLE_CHOICES[0])
    try:
        return program_for_display_state(
            LedDisplayState.WORKING,
            led_count=8,
            working_color=row.current_hex,
            working_style=style,
            working_floor=0.05,
            working_ceiling=1.0,
        )
    except (TypeError, ValueError):
        return row.current_hex


def _build_provider_animation_row(row, target, actions, hex_labels):
    """One provider, one rhythm. The whole point of the Animations section:
    "pick a provider and choose what animation it gets".

    Every string this row draws is registered with the row's sync object.
    The version this replaces registered only the popup and the thumb, so
    picking Chase left a popup reading "Chase" directly beside a sentence
    still reading "Follows the state: breathe when idle, chase while
    working" — and left the identity line showing whatever hex the provider
    had when the window opened.
    """
    stack = native_ui.make_stack(orientation="vertical", spacing=native_ui.SPACE_XS)
    header, name_label, hex_label = _identity_view(row)
    stack.addArrangedSubview_(header)

    sync = _row_sync(
        target,
        actions,
        ("agent", row.provider),
        lambda colors, provider=row.provider: colors_module.provider_color_row(
            provider, colors
        ),
        picker_payload={"provider": row.provider},
        hex_labels=hex_labels,
    )
    sync.add_identity(name_label, hex_label)

    controls = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    popup = native_ui.make_popup_button(actions, "selectProviderAnimation:")
    for motion in colors_module.PROVIDER_ANIMATION_CHOICES:
        popup.addItemWithTitle_(colors_module.PROVIDER_ANIMATION_LABELS[motion])
        item = popup.lastItem()
        item.setRepresentedObject_({"provider": row.provider, "motion": motion})
        item.setToolTip_(colors_module.PROVIDER_ANIMATION_DESCRIPTIONS[motion])
    select_popup_item(popup, "motion", row.animation)
    actions.animation_popups[row.provider] = popup
    controls.addArrangedSubview_(popup)

    thumb = _mini_led_view(*SIGNAL_THUMB_SIZE)
    thumb.setProgram_(_provider_animation_thumb_program(target, row))
    thumb.setToolTip_(row.animation_description)
    actions.animation_thumbs[row.provider] = thumb
    # Hovering the rhythm plays it on the Screen Bar, exactly as hovering a
    # colour does. Two body strings in this pane promised "hover any color
    # or animation" while no animation control had any hover wiring at all.
    hover_thumb = native_ui.make_hover_area(thumb, {"provider": row.provider})
    hover_thumb.hover_enter = actions.hover_provider_animation
    hover_thumb.hover_exit = actions.hover_end
    actions.animation_hover_areas[row.provider] = hover_thumb
    controls.addArrangedSubview_(hover_thumb)

    description = native_ui.make_label(
        row.animation_description,
        secondary=True,
        size=11.0,
    )
    sync.add_description(description)
    controls.addArrangedSubview_(description)
    controls.addArrangedSubview_(native_ui.make_hspacer())
    stack.addArrangedSubview_(controls)
    return stack


def _studio_preview_statuses(target: StatusBarController):
    scenario = getattr(
        target, "color_preview_scenario", colors_module.PREVIEW_SCENARIO_LIVE
    )
    if scenario != colors_module.PREVIEW_SCENARIO_LIVE:
        return colors_module.preview_statuses_for_scenario(scenario)
    snapshot = getattr(target, "last_snapshot", None)
    statuses = snapshot.statuses if snapshot is not None else ()
    return statuses or colors_module.demo_statuses_for_preview()


def refresh_studio_compare(
    target: StatusBarController, colors=None, caption=None, *, statuses=None
) -> None:
    """The before/after strip: what the lights do now, and what they would do
    with the thing under the pointer. Static peak colours on purpose — this
    is a comparison, and two strips breathing out of phase compare badly."""
    compare = getattr(target, "studio_compare", None)
    if not compare:
        return
    committed = target.settings.colors
    if statuses is None:
        statuses = _studio_preview_statuses(target)
    for key, candidate in (("before", committed), ("after", colors or committed)):
        try:
            led_colors = colors_module.preview_led_colors(
                statuses, led_count=STUDIO_COMPARE_LED_COUNT, colors=candidate
            )
        except Exception:
            continue
        for dot, hex_color in zip(compare[key], led_colors):
            set_preview_dot_color(dot, hex_color)
    label = compare.get("caption")
    if label is not None:
        label.setStringValue_(caption or STUDIO_IDLE_COMPARE_CAPTION)


def _build_studio_compare_strip(target: StatusBarController):
    """Two eight-LED strips, labelled Now and Trying, plus a caption naming
    what is being tried. Lives in the pinned header so it is visible from
    every section, including while the pointer is on a swatch."""
    holder = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_M)
    dots: dict[str, list] = {}
    for key, title in (("before", "Now"), ("after", "Trying")):
        column = native_ui.make_stack(orientation="vertical", spacing=2.0)
        column.addArrangedSubview_(native_ui.make_label(title, secondary=True, size=10.0))
        width = STUDIO_COMPARE_LED_COUNT * (COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP) - COLOR_SWATCH_GAP
        strip = native_ui.make_fixed_area(width, COLOR_SWATCH_SIZE)
        row_dots = []
        x = 0
        for _index in range(STUDIO_COMPARE_LED_COUNT):
            row_dots.append(add_preview_dot(strip, x, 0))
            x += COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP
        column.addArrangedSubview_(strip)
        dots[key] = row_dots
        holder.addArrangedSubview_(column)
    holder.addArrangedSubview_(native_ui.make_hspacer())
    caption = native_ui.make_label(STUDIO_IDLE_COMPARE_CAPTION, secondary=True, size=11.0)
    outer = native_ui.make_stack(orientation="vertical", spacing=4.0)
    outer.addArrangedSubview_(holder)
    outer.addArrangedSubview_(caption)
    target.studio_compare = {"before": dots["before"], "after": dots["after"], "caption": caption}
    return outer


def _build_studio_colors_section(target, actions, swatches, hex_labels):
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)

    agent_outer, agent_inner = native_ui.make_card("Agent Colors", revealing=True)
    agent_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "One row per agent, each led by its own name. The Brand group is "
            "the official color of Claude, OpenAI, Codex and Gemini — an "
            "agent whose own color is none of those leads its Brand group "
            "with it, named Default. Palette is the system set. Hover any "
            "color to see it on the Screen Bar before you keep it.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    if not hasattr(target, "tip_anchor_views"):
        target.tip_anchor_views = {}
    # A tip anchored here is reached from the menu bar while the Studio may
    # be sitting on any of its three sections; the card un-hides its own
    # section before the window scrolls to it. See _RevealingStackView.
    agent_outer.on_reveal = lambda: actions.select_section(
        colors_module.STUDIO_SECTION_COLORS
    )
    target.tip_anchor_views["brand_colors"] = agent_outer
    rows = colors_module.provider_color_rows(target.settings.colors)
    for index, row in enumerate(rows):
        agent_inner.addArrangedSubview_(
            _build_provider_color_row(row, target, actions, swatches, hex_labels)
        )
        if index < len(rows) - 1:
            native_ui.add_separator(agent_inner)
    stack.addArrangedSubview_(agent_outer)

    mode_outer, mode_inner = native_ui.make_card("State Colors")
    mode_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "What each state looks like, whoever is in it. Default is the "
            "color SidePulse ships for that state.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    mode_rows = colors_module.mode_color_rows(target.settings.colors)
    for index, mode_row in enumerate(mode_rows):
        mode_inner.addArrangedSubview_(
            _build_agent_or_mode_color_row(
                mode_row, target, actions, swatches, hex_labels
            )
        )
        if index < len(mode_rows) - 1:
            native_ui.add_separator(mode_inner)
    stack.addArrangedSubview_(mode_outer)

    palette_outer, palette_inner = native_ui.make_card("Palettes")
    palette_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "A complete look in one click — state colors and agent colors "
            "together, derived so every set stays legible. Individual "
            "colors above still override anything.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    palette_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    for palette_name, palette in colors_module.CURATED_PALETTES.items():
        palette_button = native_ui.make_button(palette_name, target, "applyPalette:")
        palette_button.setIdentifier_(palette_name)
        modes = palette["modes"]
        palette_button.setToolTip_(
            f"working {modes['working']} · done {modes['done']} · "
            f"ask {modes['ask']}"
        )
        palette_row.addArrangedSubview_(palette_button)
    palette_row.addArrangedSubview_(native_ui.make_hspacer())
    palette_inner.addArrangedSubview_(palette_row)
    provider_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    provider_row.addArrangedSubview_(
        native_ui.make_label("Brand looks:", secondary=True, size=11.0)
    )
    for palette_name in colors_module.PROVIDER_PALETTES:
        provider_button = native_ui.make_button(palette_name, target, "applyPalette:")
        provider_button.setIdentifier_(palette_name)
        provider_button.setToolTip_(
            f"A full look seeded from {palette_name}'s brand color"
        )
        provider_row.addArrangedSubview_(provider_button)
    provider_row.addArrangedSubview_(native_ui.make_hspacer())
    palette_inner.addArrangedSubview_(provider_row)
    stack.addArrangedSubview_(palette_outer)
    return stack


def _build_studio_animations_section(target, actions, hex_labels):
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)

    agent_outer, agent_inner = native_ui.make_card("Agent Animation")
    agent_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Pick a provider, choose how it moves. Automatic follows the "
            "state (breathe when idle, chase while working). Whatever you "
            "choose, an agent that needs you keeps its urgent beat — that "
            "one is not up for negotiation.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    rows = colors_module.provider_color_rows(target.settings.colors)
    for index, row in enumerate(rows):
        agent_inner.addArrangedSubview_(
            _build_provider_animation_row(row, target, actions, hex_labels)
        )
        if index < len(rows) - 1:
            native_ui.add_separator(agent_inner)
    stack.addArrangedSubview_(agent_outer)

    anim_outer, anim_inner = native_ui.make_card("State Animation")
    anim_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "How each state moves, whoever is in it. Every choice is named "
            "under its thumbnail; hover one to try it on the Screen Bar.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    animation_thumbs: dict[str, dict[str, object]] = {}
    for index, key in enumerate(ANIMATION_MODE_KEYS):
        thumb_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
        thumb_row.setAlignment_(NSLayoutAttributeTop)
        thumbs: dict[str, object] = {}
        for style in ANIMATION_STYLE_CHOICES:
            style_name = ANIMATION_STYLE_DISPLAY_LABELS.get(style, style.title())
            thumb = _mini_led_view(*SIGNAL_THUMB_SIZE)
            thumb.setToolTip_(style_name)
            thumb.setProgram_(_mode_animation_thumb_program(target, key, style))
            thumb.mode_anim_key = key
            thumb.mode_anim_style = style
            recognizer = NSClickGestureRecognizer.alloc().initWithTarget_action_(
                target, "selectModeAnimationThumb:"
            )
            thumb.addGestureRecognizer_(recognizer)
            thumbs[style] = thumb
            # A NAME under every one, and a hover on every one. These four
            # thumbnails were identified by setToolTip_ alone and told apart
            # only by position — the exact failure ("a colour square with no
            # word attached is a guess") that the Studio's own header comment
            # claims to have ended, three cards above this one.
            hover_area = native_ui.make_hover_area(
                thumb, {"key": key, "style": style}
            )
            hover_area.hover_enter = actions.hover_mode_animation
            hover_area.hover_exit = actions.hover_end
            actions.mode_animation_hover_areas[(key, style)] = hover_area
            column = native_ui.make_stack(orientation="vertical", spacing=1.0)
            column.setAlignment_(NSLayoutAttributeCenterX)
            column.addArrangedSubview_(hover_area)
            caption = native_ui.make_caption(style_name)
            column.addArrangedSubview_(caption)
            thumb.studio_caption = caption
            thumb_row.addArrangedSubview_(column)
        _apply_thumb_selection(thumbs, target.settings.colors.animation_style(key))
        animation_thumbs[key] = thumbs
        anim_inner.addArrangedSubview_(
            native_ui.make_row(MODE_COLOR_DISPLAY_LABELS[key], thumb_row)
        )
        if index < len(ANIMATION_MODE_KEYS) - 1:
            native_ui.add_separator(anim_inner)
    target.colors_animation_thumbs = animation_thumbs
    stack.addArrangedSubview_(anim_outer)

    fade_outer, fade_inner = native_ui.make_card("Fade Intensity")
    fade_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Each animation breathes between these two levels: Floor is its "
            "dimmest moment, Ceiling its brightest, as % of the mode's color. "
            "Final light = this range x the device's Brightness x the menu-bar "
            "Global Brightness.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    fade_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "On the LED strip, levels too dim to hold their color show as OFF "
            "(very low drive turns every color green, so the strip goes "
            "honestly dark instead). A floor under ~10% means the breath's "
            "low point is darkness — that's the intended look, not a fault.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    fade_preset_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    for label, floor_pct, ceiling_pct in (
        ("Subtle", 35, 70),
        ("Balanced", 12, 85),
        ("Full Range", 0, 100),
    ):
        preset_button = native_ui.make_button(label, target, "applyFadePreset:")
        preset_button.setIdentifier_(f"{floor_pct}|{ceiling_pct}")
        fade_preset_row.addArrangedSubview_(preset_button)
    fade_preset_row.addArrangedSubview_(native_ui.make_hspacer())
    fade_inner.addArrangedSubview_(fade_preset_row)
    fade_fields: dict[str, dict[str, object]] = {}
    for key in FADE_MODE_KEYS:
        floor, ceiling = target.settings.colors.fade_range(key)
        controls = native_ui.make_stack(orientation="horizontal", spacing=6.0)
        # Sliders, not number fields: dragging a range you can SEE is
        # how a breath's dimmest/brightest moments should be tuned. The
        # existing plumbing survives — NSSlider speaks stringValue, so
        # applyFadeIntensity_'s parse and refresh's set_field_value work
        # unchanged.
        controls.addArrangedSubview_(
            native_ui.make_label("Floor", secondary=True, size=11.0)
        )
        floor_field = native_ui.make_slider(
            min_value=0.0,
            max_value=100.0,
            value=round(floor * 100),
            target=target,
            action="applyFadeIntensity:",
        )
        native_ui.constrain_width(floor_field, 110.0)
        controls.addArrangedSubview_(floor_field)
        floor_value = native_ui.make_label(
            f"{round(floor * 100)}%", secondary=True, size=11.0
        )
        controls.addArrangedSubview_(floor_value)
        controls.addArrangedSubview_(
            native_ui.make_label("Ceiling", secondary=True, size=11.0)
        )
        ceiling_field = native_ui.make_slider(
            min_value=0.0,
            max_value=100.0,
            value=round(ceiling * 100),
            target=target,
            action="applyFadeIntensity:",
        )
        native_ui.constrain_width(ceiling_field, 110.0)
        controls.addArrangedSubview_(ceiling_field)
        ceiling_value = native_ui.make_label(
            f"{round(ceiling * 100)}%", secondary=True, size=11.0
        )
        controls.addArrangedSubview_(ceiling_value)
        fade_fields[key] = {
            "floor": floor_field,
            "ceiling": ceiling_field,
            "floor_label": floor_value,
            "ceiling_label": ceiling_value,
        }
        fade_inner.addArrangedSubview_(native_ui.make_row(MODE_COLOR_DISPLAY_LABELS[key], controls))
    stack.addArrangedSubview_(fade_outer)

    behavior_outer, behavior_inner = native_ui.make_card("How Several Agents Share the Strip")
    preset_popup = make_color_preset_popup(target)
    behavior_inner.addArrangedSubview_(
        native_ui.make_row(
            "Preset",
            preset_popup,
            help_text=(
                "One-click personality for the whole display: blend mode, "
                "animation, speed, and fade set together. Any manual tweak "
                "below switches this back to Custom."
            ),
        )
    )
    blend_popup = make_blend_mode_popup(target)
    behavior_inner.addArrangedSubview_(native_ui.make_row("Blend Mode", blend_popup))
    blend_description = native_ui.make_label("", secondary=True, size=12.0)
    behavior_inner.addArrangedSubview_(blend_description)
    urgency_row, urgency_alert_checkbox = native_ui.make_switch_row(
        "Alert when blocked or waiting",
        target,
        "toggleUrgencyAlert:",
        help_text=(
            "In Round-Robin/Cycle, a blocked or waiting agent shows the Ask "
            "color instead of its own, so it stands out."
        ),
    )
    behavior_inner.addArrangedSubview_(urgency_row)
    done_row, done_celebration_checkbox = native_ui.make_switch_row(
        "Celebrate when finished",
        target,
        "toggleDoneCelebration:",
        help_text="A brief twinkle plays before settling into the Done color.",
    )
    behavior_inner.addArrangedSubview_(done_row)
    project_row, color_by_project_checkbox = native_ui.make_switch_row(
        "Color by project",
        target,
        "toggleColorByProject:",
        help_text=(
            "Sessions in the same repo share one hue family — providers "
            "are told apart by lightness within it. Off: every session "
            "gets its own hue."
        ),
    )
    behavior_inner.addArrangedSubview_(project_row)
    native_ui.add_separator(behavior_inner)

    speed_field = native_ui.make_field("", target=target, action="applyCycleSpeed:")
    native_ui.constrain_width(speed_field, 56.0)
    speed_controls = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_XS)
    speed_controls.addArrangedSubview_(speed_field)
    speed_controls.addArrangedSubview_(native_ui.make_label("sec/breath", secondary=True))
    behavior_inner.addArrangedSubview_(native_ui.make_row("Global Speed", speed_controls))

    round_robin_use_global = native_ui.make_checkbox(
        "Round-Robin: use global", target, "toggleRoundRobinUseGlobalSpeed:"
    )
    round_robin_speed_field = native_ui.make_field("", target=target, action="applyRoundRobinSpeed:")
    native_ui.constrain_width(round_robin_speed_field, 56.0)
    round_robin_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    round_robin_row.addArrangedSubview_(round_robin_use_global)
    round_robin_row.addArrangedSubview_(native_ui.make_hspacer())
    round_robin_row.addArrangedSubview_(round_robin_speed_field)
    behavior_inner.addArrangedSubview_(round_robin_row)

    cycle_use_global = native_ui.make_checkbox("Cycle: use global", target, "toggleCycleUseGlobalSpeed:")
    cycle_speed_field = native_ui.make_field("", target=target, action="applyCycleModeSpeed:")
    native_ui.constrain_width(cycle_speed_field, 56.0)
    cycle_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    cycle_row.addArrangedSubview_(cycle_use_global)
    cycle_row.addArrangedSubview_(native_ui.make_hspacer())
    cycle_row.addArrangedSubview_(cycle_speed_field)
    behavior_inner.addArrangedSubview_(cycle_row)
    stack.addArrangedSubview_(behavior_outer)

    fields = {
        "preset_popup": preset_popup,
        "blend_mode_popup": blend_popup,
        "blend_description": blend_description,
        "urgency_alert_checkbox": urgency_alert_checkbox,
        "done_celebration_checkbox": done_celebration_checkbox,
        "color_by_project_checkbox": color_by_project_checkbox,
        "speed_field": speed_field,
        "round_robin_use_global": round_robin_use_global,
        "round_robin_speed_field": round_robin_speed_field,
        "cycle_use_global": cycle_use_global,
        "cycle_speed_field": cycle_speed_field,
        "fade_fields": fade_fields,
    }
    return stack, fields


def _build_studio_preview_section(target, actions):
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)

    preview_outer, preview_inner = native_ui.make_card("Live Preview")
    preview_scenario_popup = make_preview_scenario_popup(target)
    preview_inner.addArrangedSubview_(native_ui.make_row("Scenario", preview_scenario_popup))
    shared_legend = native_ui.make_label("", secondary=True, size=11.0)
    preview_rows: list[dict[str, object]] = []
    for led_count, device_label in ((2, "SidePulse Dot (2 LEDs)"), (8, "SidePulse Pro (8 LEDs)")):
        device_stack = native_ui.make_stack(orientation="vertical", spacing=6.0)
        device_stack.addArrangedSubview_(native_ui.make_label(device_label, size=13.0))
        dots_width = led_count * (COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP) - COLOR_SWATCH_GAP
        dots_container = native_ui.make_fixed_area(dots_width, COLOR_SWATCH_SIZE)
        dots = []
        dot_x = 0
        for _index in range(led_count):
            dots.append(add_preview_dot(dots_container, dot_x, 0))
            dot_x += COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP
        device_stack.addArrangedSubview_(dots_container)
        preview_inner.addArrangedSubview_(device_stack)
        preview_rows.append({"led_count": led_count, "dots": dots, "legend": shared_legend})
    legend_holder = native_ui.make_stack(orientation="vertical", spacing=0.0)
    legend_holder.addArrangedSubview_(shared_legend)
    preview_inner.addArrangedSubview_(legend_holder)
    stack.addArrangedSubview_(preview_outer)

    surfaces_outer, surfaces_inner = native_ui.make_card("Where Previews Play")
    surfaces_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Hovering a color or an animation plays it on the Screen Bar "
            "only, and the Screen Bar goes straight back to the truth the "
            "moment you move away. Hovering never reaches connected "
            "hardware; a change you actually keep reaches it only while the "
            "switch below is on.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    live_row, live_toggle = native_ui.make_switch_row(
        "Also preview on connected hardware",
        target,
        "toggleColorPreviewLive:",
        help_text=(
            "Off: the lights keep showing what your agents are actually "
            "doing while you experiment."
        ),
    )
    # NSSwitch builds OFF; the controller's flag is the truth.
    set_checkbox_state(live_toggle, hardware_preview_enabled(target))
    surfaces_inner.addArrangedSubview_(live_row)
    stack.addArrangedSubview_(surfaces_outer)

    target.color_preview_rows = preview_rows
    return stack, {"preview_scenario_popup": preview_scenario_popup, "live_toggle": live_toggle}


def _build_color_studio_pane(target: StatusBarController) -> NSView:
    """The Colour / Animation Studio: one surface where Colours, Animations
    and Preview are peers, with a pinned before/after strip on top and the
    hardware opt-in in the footer."""
    root = NSView.alloc().init()
    root.setTranslatesAutoresizingMaskIntoConstraints_(False)

    actions = SidePulseStudioActions.alloc().initWithController_(target)
    target.studio_actions = actions

    swatches: dict[tuple[tuple[str, str], str], object] = {}
    hex_labels: dict[tuple[str, str], object] = {}

    # --- pinned header: what this is, which section, and before/after ---
    #
    # The title goes through make_card, which places it ABOVE the panel at
    # 13pt semibold like every other group header in this window. It used to
    # be a 15pt bold label INSIDE the glass panel — the only display-size
    # headline in the app, against the rule make_card's own docstring states.
    header_outer, header_inner = native_ui.make_card("Color & Animation Studio")
    root.addSubview_(header_outer)
    segmented = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
        [colors_module.STUDIO_SECTION_LABELS[key] for key in colors_module.STUDIO_SECTION_CHOICES],
        NSSegmentSwitchTrackingSelectOne,
        actions,
        "showStudioSection:",
    )
    segmented.setTranslatesAutoresizingMaskIntoConstraints_(False)
    segmented_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    segmented_row.addArrangedSubview_(segmented)
    segmented_row.addArrangedSubview_(native_ui.make_hspacer())
    header_inner.addArrangedSubview_(segmented_row)
    subtitle = native_ui.make_label("", secondary=True, size=11.0)
    header_inner.addArrangedSubview_(subtitle)
    header_inner.addArrangedSubview_(_build_studio_compare_strip(target))
    actions.section_segmented = segmented
    actions.section_subtitle = subtitle

    # --- the three peer sections, stacked and cross-faded by visibility ---
    scroll_stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    colors_section = _build_studio_colors_section(target, actions, swatches, hex_labels)
    animations_section, behavior_fields = _build_studio_animations_section(target, actions, hex_labels)
    preview_section, preview_fields = _build_studio_preview_section(target, actions)
    actions.section_views = {
        colors_module.STUDIO_SECTION_COLORS: colors_section,
        colors_module.STUDIO_SECTION_ANIMATIONS: animations_section,
        colors_module.STUDIO_SECTION_PREVIEW: preview_section,
    }
    for section in colors_module.STUDIO_SECTION_CHOICES:
        scroll_stack.addArrangedSubview_(actions.section_views[section])

    scroll_pane = native_ui.wrap_in_scroll_pane(scroll_stack)
    root.addSubview_positioned_relativeTo_(scroll_pane, -1, header_outer)

    footer = NSView.alloc().init()
    footer.setTranslatesAutoresizingMaskIntoConstraints_(False)
    footer_left = native_ui.make_stack(orientation="horizontal", spacing=12.0)
    footer_left.addArrangedSubview_(
        native_ui.make_button("Reset to Defaults", target, "resetColorsToDefaults:")
    )
    footer.addSubview_(footer_left)
    NSLayoutConstraint.activateConstraints_(
        [
            footer_left.leadingAnchor().constraintEqualToAnchor_(footer.leadingAnchor()),
            footer_left.centerYAnchor().constraintEqualToAnchor_(footer.centerYAnchor()),
            # EQUALITY, not a floor: with only >=28 and no internal
            # bottom pin, the solver dumped the pane's vertical slack
            # into the footer and squeezed the scroll view to zero
            # height (an empty-looking Color Studio).
            footer.heightAnchor().constraintEqualToConstant_(28.0),
        ]
    )
    root.addSubview_(footer)

    NSLayoutConstraint.activateConstraints_(
        [
            # The pinned card follows wrap_in_scroll_pane's own centered
            # max-width column exactly, so it and the scrolling cards
            # below read as one aligned column at any window size.
            header_outer.topAnchor().constraintEqualToAnchor_constant_(root.topAnchor(), 20.0),
            header_outer.centerXAnchor().constraintEqualToAnchor_(root.centerXAnchor()),
            header_outer.widthAnchor().constraintLessThanOrEqualToConstant_(native_ui.CONTENT_MAX_WIDTH),
            header_outer.leadingAnchor().constraintGreaterThanOrEqualToAnchor_constant_(root.leadingAnchor(), 20.0),
            scroll_pane.topAnchor().constraintEqualToAnchor_constant_(header_outer.bottomAnchor(), 16.0),
            scroll_pane.leadingAnchor().constraintEqualToAnchor_(root.leadingAnchor()),
            scroll_pane.trailingAnchor().constraintEqualToAnchor_(root.trailingAnchor()),
            scroll_pane.bottomAnchor().constraintEqualToAnchor_constant_(footer.topAnchor(), -12.0),
            footer.leadingAnchor().constraintEqualToAnchor_constant_(root.leadingAnchor(), 20.0),
            footer.trailingAnchor().constraintEqualToAnchor_constant_(root.trailingAnchor(), -20.0),
            footer.bottomAnchor().constraintEqualToAnchor_constant_(root.bottomAnchor(), -14.0),
        ]
    )
    header_width_preference = header_outer.widthAnchor().constraintEqualToConstant_(
        native_ui.CONTENT_MAX_WIDTH
    )
    header_width_preference.setPriority_(749)
    header_width_preference.setActive_(True)

    target.color_swatches = swatches
    target.color_hex_labels = hex_labels
    target.color_fields = {**behavior_fields, **preview_fields}
    actions.select_section(actions.section)
    refresh_studio_compare(target)
    refresh_blend_and_speed_fields(target)
    return root


def refresh_blend_and_speed_fields(target: StatusBarController) -> None:
    colors = target.settings.colors
    fields = target.color_fields
    # Called once per refresh_colors_window, which is the one hook this
    # module has into "the saved colours just changed" — so the Studio's
    # before/after strip re-bakes here rather than needing its own, and an
    # in-flight hover is rebased onto the new baseline rather than left
    # holding a candidate derived from settings that no longer exist
    # (Reset to Defaults and the palette buttons both land here).
    actions = getattr(target, "studio_actions", None)
    if actions is not None:
        actions.preview_session.rebase(colors)
    refresh_studio_compare(target)
    live_toggle = fields.get("live_toggle")
    if live_toggle is not None:
        set_checkbox_state(live_toggle, hardware_preview_enabled(target))
    preset_popup = fields.get("preset_popup")
    if preset_popup is not None:
        select_color_preset(preset_popup, matching_preset(colors))
    popup = fields.get("blend_mode_popup")
    if popup is not None:
        select_blend_mode(popup, colors.blend_mode)
    description = fields.get("blend_description")
    if description is not None:
        description.setStringValue_(BLEND_MODE_DESCRIPTIONS.get(colors.blend_mode, ""))
        description.setToolTip_(colors_module.BLEND_MODE_TOOLTIPS.get(colors.blend_mode, ""))
    checkbox = fields.get("urgency_alert_checkbox")
    if checkbox is not None:
        set_checkbox_state(checkbox, colors.round_robin_urgency_alert)
    by_project_checkbox = fields.get("color_by_project_checkbox")
    if by_project_checkbox is not None:
        set_checkbox_state(by_project_checkbox, colors.color_by_project)
    celebration_checkbox = fields.get("done_celebration_checkbox")
    if celebration_checkbox is not None:
        set_checkbox_state(celebration_checkbox, colors.done_celebration_enabled)
    speed_field = fields.get("speed_field")
    if speed_field is not None:
        set_field_value(speed_field, f"{colors.cycle_speed_seconds:g}")
    for mode_key, use_global_key, field_key in (
        (BLEND_MODE_ROUND_ROBIN, "round_robin_use_global", "round_robin_speed_field"),
        (BLEND_MODE_CYCLE, "cycle_use_global", "cycle_speed_field"),
    ):
        uses_global = colors.uses_global_speed(mode_key)
        use_global_checkbox = fields.get(use_global_key)
        mode_field = fields.get(field_key)
        if use_global_checkbox is not None:
            set_checkbox_state(use_global_checkbox, uses_global)
        if mode_field is not None:
            set_field_value(mode_field, f"{colors.effective_speed_seconds(mode_key):g}")
            mode_field.setEnabled_(not uses_global)
