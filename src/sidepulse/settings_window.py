"""The Settings window's construction -- every pane builder, the
window assembly, and their private helpers -- extracted from
status_bar.py (backlog #14: ~2,300 lines of module-level functions
with target/snapshot passed explicitly, so the cut is import-shuffling).

Namespace contract: this module never imports status_bar (so no import
cycle can exist in either direction). Instead status_bar, at the end
of its own module body, calls _install() with its complete namespace
-- the moved code keeps referencing shared helpers, constants, AppKit
symbols and sibling modules exactly as it did in place -- and then
re-imports every public name defined here, so controller methods,
tests, and external callers keep addressing status_bar.<name>.
Do not import this module without importing status_bar first.
"""

from __future__ import annotations

# The only import-time dependencies: module-level constants below use
# these as dict keys, evaluated before _install() runs. They come from
# settings.py, which never imports status_bar -- still no cycle.
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSButtonTypeRadio,
    NSClickGestureRecognizer,
    NSColor,
    NSFont,
    NSImage,
    NSLayoutConstraint,
    NSLayoutConstraintOrientationHorizontal,
    NSScreen,
    NSSplitView,
    NSSplitViewDividerStyleThin,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)

from .colors import ANIMATION_MODE_KEYS, CURATED_PALETTE, matching_preset
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
from .virtual_device import LED_COUNT

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
        if policy.source in active_sources:
            return (
                "Available locally",
                "SidePulse can refresh this exact source without an account API key.",
            )
        return (
            "Setup required",
            "The reviewed source is not active in this build. No provider work runs.",
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
            label = _CAPACITY_PROFILE_LABELS[policy.profile_id]
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
    """Bind status_bar's module namespace as ours -- called exactly
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
    today_inner.addArrangedSubview_(
        native_ui.make_row(
            "Claude consumer plan capacity",
            native_ui.make_label("Not supported", secondary=True),
            help_text=(
                "Claude lifecycle and local transcript activity still work. "
                "This build has no supported Anthropic consumer-plan capacity "
                "source and never reads browser sessions or private endpoints."
            ),
        )
    )
    codex_pct_row, codex_pct_switch = native_ui.make_switch_row(
        "Show Codex rate-limit percent",
        target,
        "toggleCodexPercent:",
        help_text="The current rate-limit window percent on the Codex line.",
    )
    codex_pct_switch.setState_(1 if target.settings.codex_percent_enabled else 0)
    today_inner.addArrangedSubview_(codex_pct_row)
    today_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Costs use Anthropic list rates; cached reads bill at a tenth "
            "of the uncached rate -- \u201csaved with caching\u201d is that "
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
                "appears here by itself -- or start with the on-screen bar:",
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
            # dragging, not just after release -- setDeviceBrightness_
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
        # The slider stretches to whatever width the row gives it -- a
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
                    "A faint ember on every LED, even the unlit ones -- "
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
                    "time crosses your expected length -- a timer, not a "
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

        # Story #16: pin this device to one provider -- "the Dot is
        # Codex's" -- while other devices keep the aggregate.
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
                    "every device -- blocked-on-you is never filtered."
                ),
            )
        )

        # Backlog #21: per-device courtesy muting -- "the Dot only
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
                    "blocked-on-you -- completions, notifications, quota, "
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


def calibration_summary_text(auto_brightness_enabled: bool, red: float, green: float, blue: float) -> str:
    """The at-a-glance summary next to the Calibrate button. Channel
    percentages only appear once they differ from the default -- an
    uncalibrated device just says what Auto-Brightness is doing, not a
    wall of R100% G100% B100% that reads as debug output."""
    parts = ["Auto-Brightness on" if auto_brightness_enabled else "Auto-Brightness off"]
    if any(round(gain * 100) != 100 for gain in (red, green, blue)):
        parts.append(f"R{round(red * 100)}% G{round(green * 100)}% B{round(blue * 100)}%")
    return " · ".join(parts)


CALIBRATION_TEST_PATCHES: tuple[tuple[str, str], ...] = (
    ("White", "#FFFFFF"),
    ("Red", "#FF0000"),
    ("Green", "#00FF00"),
    ("Blue", "#0000FF"),
)


def build_calibration_popover_content(device: StatusBarDevice, target: StatusBarController):
    """The content shown inside the "Calibrate…" popover for one device:
    a guided matching flow, not blind sliders. The reference patches at
    the top are the ground truth -- click one and the device lights with
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
            "Blue until the light looks white to you -- then check\n"
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
# wing_width_for_screen computes -- this preview shows the *shape* of
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
                "moving signal shows -- the relay dot ticking around, "
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
            "The outermost sliver of each wing becomes a standing "
            "micro-gauge: left holds a faint amber ember as your worst "
            "quota window fills past 50%, right glows green while "
            "finished sessions wait unseen -- and goes out the moment "
            "you open the menu. Survives every animation."
        ),
    )
    glow_inner.addArrangedSubview_(gauges_row)
    stack.addArrangedSubview_(glow_outer)
    outer, inner = native_ui.make_card()

    inner.addArrangedSubview_(
        native_ui.make_row("Colors", native_ui.make_button("Customize Colors…", target, "openColorsWindow:"))
    )
    native_ui.add_separator(inner)

    # A live, real miniature of the Screen Bar itself -- the same drawing
    # code the actual on-screen widget uses, not an illustration -- so
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
    follow_row, follow_switch = native_ui.make_switch_row(
        "Match Alcove's width automatically",
        target,
        "toggleScreenBarFollowAlcove:",
        help_text=(
            "The bracket tracks Alcove's visible capsule -- widening "
            "for a timer or now-playing pill and easing back when it "
            "collapses, hugging it within a couple of points. While a "
            "capsule is visible this supersedes the Bar Size gap, so "
            "Automatic stays automatic; a manual wing length still "
            "wins. Needs Screen Recording permission; without it the "
            "bar quietly keeps its classic size."
        ),
    )
    inner.addArrangedSubview_(follow_row)
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
    # whose visual width changes at runtime -- and for whatever notch
    # future Macs ship with. Automatic uses the hardware measurements.
    size_outer, size_inner = native_ui.make_card("Bar Size")
    try:
        auto_gap = slot_width_for_screen(NSScreen.mainScreen())
    except Exception:
        auto_gap = 232.0
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
            help_text="How wide the dark center is -- widen it to clear Alcove's pill.",
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
    }
    buttons = {
        "screen_bar_wraps_menu_bar": wraps_switch,
        "screen_bar_gauges": gauges_switch,
        "screen_bar_follow_alcove": follow_switch,
    }
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


def _build_power_pane(target: StatusBarController):
    """Keep-awake policy and battery display together -- both are "what
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
                "A buffer against a false “done” reading -- e.g. a command still "
                "running with no events for a stretch -- closing the lid into sleep."
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


# Style cards: every signal is edited BY EYE -- a color well, pattern
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
BRAND_SWATCHES: tuple[tuple[str, str], ...] = (
    ("Claude", "#D97757"),
    ("OpenAI", "#10A37F"),
    ("Codex", "#FF3A00"),
    ("Gemini", "#4796E3"),
)
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
    -- the Colors window speaks the Signals pane's visual language."""
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
    NSColorPanel). No NSColorWell anywhere -- see BRAND_SWATCHES."""
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
    """What each macOS Focus does to the lights -- its own pane because
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
            "window) -- otherwise this has no effect."
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
            "Eases green and blue down after dark -- like Night Shift, "
            "for your LEDs. Composes with each device's calibration."
        ),
    )
    warmth_inner.addArrangedSubview_(warmth_row)
    buttons["night_warmth_enabled"] = warmth_switch
    stack.addArrangedSubview_(warmth_outer)

    # Story #10: timebox presets can run a named Shortcut when they
    # start and another when they end -- Focus on with the drain, Focus
    # off when it finishes.
    handshake_outer, handshake_inner = native_ui.make_card("Timebox Focus Handshake")
    handshake_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Each Timer preset can run a Shortcut when it starts and "
            "another when it ends or you press Stop -- name a Shortcut "
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
            "calibration profile the moment it activates -- e.g. School \u2192 "
            "Turn off, Work \u2192 Dim to 50%.",
            secondary=True,
            size=12.0,
            max_width=560.0,
        )
    )
    try:
        focus_modes = focus_sync.configured_focus_modes()
        log_status_bar(
            "focus roster: "
            + (", ".join(name for _id, name in focus_modes) or "EMPTY")
        )
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
            # brightness) -- hold the courtesy glows, or go fully silent.
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


    # SidePulse-owned agent lifecycle notifications only. Foreign app
    # notifications are never observed or mirrored into the lights.
    notif_outer, notif_inner = native_ui.make_card("Agent Notifications")
    completion_row, completion_switch = native_ui.make_switch_row(
        "Sweep when any agent finishes",
        target,
        "toggleCompletionSweep:",
        help_text=(
            "A brief sweep in the finishing agent's own color -- without "
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

    # Calendar & Reminders: warning lights, not a calendar app.
    # (This card used to be a five-feature grab-bag titled "Calendar",
    # with weather's location fields orphaned three rows from the
    # weather switch. One card per subject now.)
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
            "from your network address -- no Location permission "
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

    # Capacity effects remain withheld until a separately reviewed release.
    quota_outer, quota_inner = native_ui.make_card("Quota")
    quota_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Capacity alerts, outbound events, queue advice, and hardware "
            "runway are unavailable until a supported source and explicit "
            "forecast release authority exist.",
            secondary=True,
        )
    )
    stack.addArrangedSubview_(quota_outer)

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
            "answer -- off (default) means only MAIN sessions turn the "
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
    # indicator escapes the device -- Home Assistant, ntfy, a Hue
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
        "completion_sweep_enabled": completion_switch,
        "completion_notification": completion_banner_switch,
        "notification_permission": notification_permission,
        "calendar_alerts_enabled": cal_switch,
        "reminder_alerts_enabled": rem_switch,
        "weather_alerts_enabled": weather_switch,
        "subagent_asks_alert": subask_switch,
    }
    buttons.update(webhook_event_boxes)
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


FOCUS_DIM_CHOICES: tuple[tuple[str, str], ...] = (
    ("Shared dim (default)", "default"),
    ("Don't dim", "1.0"),
    ("Dim to 50%", "0.5"),
    ("Dim to 25%", "0.25"),
    ("Turn off", "0.0"),
)


def select_focus_dim_choice(popup, fraction: float | None) -> None:
    """Selects the popup item matching a saved rule (None = shared
    default) -- refresh_settings_window's counterpart to
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
        "available -- a fallback, not the primary detection path."
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

    buttons = {"codex_transcripts": codex_switch, "claude_transcripts": claude_switch}
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


# Curated one-shot lid looks. Built only from grammar primitives already
# proven against the firmware (colors, durations, pulse/cosine/linear,
# off) -- a test parses every preset through the real sdled.wasm.
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
        ("Heartbeat Out", 1.3, "#FF2D55 150ms pulse\noff 120ms linear\n#FF2D55 150ms pulse\noff 880ms cosine"),
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
    it. The raw editor below stays for hand-tuning -- the picker is how
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
    library_popup.addItemWithTitle_("Saved looks\u2026")
    library_popup.lastItem().setRepresentedObject_("")
    for look_name, _program in target.settings.studio_library:
        library_popup.addItemWithTitle_(look_name)
        library_popup.lastItem().setRepresentedObject_(look_name)
    library_row.addArrangedSubview_(library_popup)
    library_row.addArrangedSubview_(
        native_ui.make_button("Delete", target, "deleteStudioLook:")
    )
    library_row.addArrangedSubview_(native_ui.make_hspacer())
    studio_inner.addArrangedSubview_(library_row)
    target.studio_save_name_field = save_name
    target.studio_library_popup = library_popup
    stack.addArrangedSubview_(studio_outer)
    target.studio_editor = studio_editor




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
    # instant-apply contract as every field -- no Save button.
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
    # detail -- not as a permanent footer under every pane, where it read
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
    # content's computed fitting width -- which for a sidebar + a column
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
    # Bar now extends...") -- diagnostic paths live in the Debug pane.
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

    # Audit #5: panes build LAZILY, on first visit -- the gear click
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
    """(pane, fields, buttons) for one sidebar key -- fields and
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


MODE_COLOR_DISPLAY_LABELS: dict[str, str] = {
    "idle": "Idle",
    "working": "Working",
    "done": "Done",
    "ask": "Ask (waiting / blocked)",
}

COLOR_SWATCH_SIZE = 22
COLOR_SWATCH_GAP = 8
COLOR_ROW_HEIGHT = 40


def _build_agent_or_mode_color_row(
    row_key: tuple[str, str],
    row_label: str,
    current: str,
    palette: tuple[str, ...],
    target,
    swatch_selector: str,
    swatch_represented: dict,
    custom_selector: str,
    custom_represented: dict,
    swatches: dict,
    hex_labels: dict,
):
    """One "label ... [swatch swatch swatch ... + hex] " row shared by the
    Agent Colors and Mode Colors cards. The swatch strip is a fixed-size
    area (see native_ui.make_fixed_area) holding frame-positioned swatch
    buttons -- a palette grid is inherently a custom-drawn strip, not a
    column of stock controls, so this is the sanctioned escape hatch from
    Auto Layout rather than a rewrite of add_color_swatch/
    add_custom_color_swatch themselves."""
    width = len(palette) * (COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP) + COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP + 80
    container = native_ui.make_fixed_area(width, 24.0)
    x = 0
    brand_names = {hex_value.upper(): name for name, hex_value in BRAND_SWATCHES}
    for palette_hex in palette:
        button = add_color_swatch(
            container, palette_hex, x, 1, target, swatch_selector, {**swatch_represented, "hex": palette_hex}
        )
        brand = brand_names.get(palette_hex.upper())
        # Anonymous colored squares made the brand-colors tip a lie --
        # hovering now says exactly what each swatch is.
        button.setToolTip_(
            f"{brand} brand color \u00b7 {palette_hex}" if brand else palette_hex
        )
        set_swatch_selected(button, palette_hex.upper() == current.upper())
        swatches[(row_key, palette_hex)] = button
        x += COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP
    add_custom_color_swatch(container, x, 1, target, custom_selector, custom_represented)
    x += COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP
    hex_labels[row_key] = add_label(container, current, x, 3, 80, 18)
    return native_ui.make_row(row_label, container)


def _build_color_studio_pane(target: StatusBarController) -> NSView:
    """The Color Studio: the old standalone Colors window merged into
    Settings as its own pane -- the pinned Live Preview on top, the
    color/blend/fade/animation cards scrolling beneath, footer pinned.
    One app, one window, one visual language."""
    root = NSView.alloc().init()
    root.setTranslatesAutoresizingMaskIntoConstraints_(False)

    # Live Preview stays pinned at the top, outside the scroll area -- it's
    # a continuous creative-feedback tool you check while adjusting colors
    # below, not a "setting" you visit once and move past.
    preview_outer, preview_inner = native_ui.make_card("Live Preview")
    root.addSubview_(preview_outer)

    preview_scenario_popup = make_preview_scenario_popup(target)
    preview_inner.addArrangedSubview_(native_ui.make_row("Scenario", preview_scenario_popup))

    # One shared legend under both device previews -- the roster of
    # agents is the same for both, and repeating it verbatim twice was
    # pure noise.
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

    # Everything else scrolls independently below the pinned preview.
    scroll_stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)

    behavior_outer, behavior_inner = native_ui.make_card("Blend Mode & Behavior")
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
            "Sessions in the same repo share one hue family -- providers "
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
    palette_outer, palette_inner = native_ui.make_card("Palettes")
    palette_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "A complete look in one click -- mode colors and agent colors "
            "together, derived so every set stays legible. Individual "
            "colors below still override anything.",
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
            f"working {modes['working']} \u00b7 done {modes['done']} \u00b7 "
            f"ask {modes['ask']}"
        )
        palette_row.addArrangedSubview_(palette_button)
    palette_row.addArrangedSubview_(native_ui.make_hspacer())
    palette_inner.addArrangedSubview_(palette_row)
    provider_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    provider_row.addArrangedSubview_(
        native_ui.make_label("Provider looks:", secondary=True, size=11.0)
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
    scroll_stack.addArrangedSubview_(palette_outer)

    scroll_stack.addArrangedSubview_(behavior_outer)

    swatches: dict[tuple[tuple[str, str], str], object] = {}
    hex_labels: dict[tuple[str, str], object] = {}

    agent_outer, agent_inner = native_ui.make_card("Agent Colors")
    agent_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "The first four swatches on every row are Claude, OpenAI, "
            "Codex, and Gemini's official brand colors \u2014 click one to "
            "use it. Hover any swatch to see its name.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    if not hasattr(target, "tip_anchor_views"):
        target.tip_anchor_views = {}
    target.tip_anchor_views["brand_colors"] = agent_outer
    for spec in PROVIDER_SPECS:
        current = target.settings.colors.agent_color(spec.provider)
        agent_inner.addArrangedSubview_(
            _build_agent_or_mode_color_row(
                ("agent", spec.provider),
                spec.label,
                current,
                CURATED_PALETTE,
                target,
                "selectAgentColorSwatch:",
                {"provider": spec.provider},
                "openCustomAgentColor:",
                {"provider": spec.provider},
                swatches,
                hex_labels,
            )
        )
    scroll_stack.addArrangedSubview_(agent_outer)

    mode_outer, mode_inner = native_ui.make_card("Mode Colors")
    for key in MODE_COLOR_KEYS:
        current = target.settings.colors.mode_color(key)
        mode_inner.addArrangedSubview_(
            _build_agent_or_mode_color_row(
                ("mode", key),
                MODE_COLOR_DISPLAY_LABELS[key],
                current,
                CURATED_PALETTE[:6],
                target,
                "selectModeColorSwatch:",
                {"key": key},
                "openCustomModeColor:",
                {"key": key},
                swatches,
                hex_labels,
            )
        )
    scroll_stack.addArrangedSubview_(mode_outer)

    anim_outer, anim_inner = native_ui.make_card("Animation Style")
    animation_thumbs: dict[str, dict[str, object]] = {}
    for index, key in enumerate(ANIMATION_MODE_KEYS):
        thumb_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
        thumbs: dict[str, object] = {}
        for style in ANIMATION_STYLE_CHOICES:
            thumb = _mini_led_view(*SIGNAL_THUMB_SIZE)
            thumb.setToolTip_(ANIMATION_STYLE_DISPLAY_LABELS.get(style, style.title()))
            thumb.setProgram_(_mode_animation_thumb_program(target, key, style))
            thumb.mode_anim_key = key
            thumb.mode_anim_style = style
            recognizer = NSClickGestureRecognizer.alloc().initWithTarget_action_(
                target, "selectModeAnimationThumb:"
            )
            thumb.addGestureRecognizer_(recognizer)
            thumbs[style] = thumb
            thumb_row.addArrangedSubview_(thumb)
        _apply_thumb_selection(thumbs, target.settings.colors.animation_style(key))
        animation_thumbs[key] = thumbs
        anim_inner.addArrangedSubview_(
            native_ui.make_row(MODE_COLOR_DISPLAY_LABELS[key], thumb_row)
        )
        if index < len(ANIMATION_MODE_KEYS) - 1:
            native_ui.add_separator(anim_inner)
    target.colors_animation_thumbs = animation_thumbs
    scroll_stack.addArrangedSubview_(anim_outer)

    fade_outer, fade_inner = native_ui.make_card("Fade Intensity")
    fade_inner.addArrangedSubview_(
        native_ui.make_label(
            "How far each pulsing mode dims down and brightens up, as % of its color",
            secondary=True,
            size=11.0,
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
        controls.addArrangedSubview_(native_ui.make_label("Floor", secondary=True, size=11.0))
        floor_field = native_ui.make_field(
            f"{round(floor * 100)}", target=target, action="applyFadeIntensity:"
        )
        native_ui.constrain_width(floor_field, 48.0)
        controls.addArrangedSubview_(floor_field)
        controls.addArrangedSubview_(native_ui.make_label("%", secondary=True, size=11.0))
        controls.addArrangedSubview_(native_ui.make_label("Ceiling", secondary=True, size=11.0))
        ceiling_field = native_ui.make_field(
            f"{round(ceiling * 100)}", target=target, action="applyFadeIntensity:"
        )
        native_ui.constrain_width(ceiling_field, 48.0)
        controls.addArrangedSubview_(ceiling_field)
        controls.addArrangedSubview_(native_ui.make_label("%", secondary=True, size=11.0))
        fade_fields[key] = {"floor": floor_field, "ceiling": ceiling_field}
        fade_inner.addArrangedSubview_(native_ui.make_row(MODE_COLOR_DISPLAY_LABELS[key], controls))
    scroll_stack.addArrangedSubview_(fade_outer)

    scroll_pane = native_ui.wrap_in_scroll_pane(scroll_stack)
    # BELOW the pinned preview card in z -- scrolled rows must slide
    # under the card's glass (which blurs them, toolbar-style), never
    # render on top of it.
    root.addSubview_positioned_relativeTo_(scroll_pane, -1, preview_outer)

    # Reset/Preview-live/Done stay pinned at the bottom too, matching every
    # native macOS dialog's own convention of action buttons that don't
    # scroll away with the content above them.
    footer = NSView.alloc().init()
    footer.setTranslatesAutoresizingMaskIntoConstraints_(False)
    footer_left = native_ui.make_stack(orientation="horizontal", spacing=12.0)
    footer_left.addArrangedSubview_(native_ui.make_button("Reset to Defaults", target, "resetColorsToDefaults:"))
    live_toggle = native_ui.make_checkbox("Preview live on device", target, "toggleColorPreviewLive:")
    footer_left.addArrangedSubview_(live_toggle)
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
            preview_outer.topAnchor().constraintEqualToAnchor_constant_(root.topAnchor(), 20.0),
            preview_outer.centerXAnchor().constraintEqualToAnchor_(root.centerXAnchor()),
            preview_outer.widthAnchor().constraintLessThanOrEqualToConstant_(native_ui.CONTENT_MAX_WIDTH),
            preview_outer.leadingAnchor().constraintGreaterThanOrEqualToAnchor_constant_(root.leadingAnchor(), 20.0),
            scroll_pane.topAnchor().constraintEqualToAnchor_constant_(preview_outer.bottomAnchor(), 16.0),
            scroll_pane.leadingAnchor().constraintEqualToAnchor_(root.leadingAnchor()),
            scroll_pane.trailingAnchor().constraintEqualToAnchor_(root.trailingAnchor()),
            scroll_pane.bottomAnchor().constraintEqualToAnchor_constant_(footer.topAnchor(), -12.0),
            footer.leadingAnchor().constraintEqualToAnchor_constant_(root.leadingAnchor(), 20.0),
            footer.trailingAnchor().constraintEqualToAnchor_constant_(root.trailingAnchor(), -20.0),
            footer.bottomAnchor().constraintEqualToAnchor_constant_(root.bottomAnchor(), -14.0),
        ]
    )
    # Prefer the full column width (see wrap_in_scroll_pane's identical
    # 749 preference) -- without this the pinned card would shrink to its
    # own fitting width instead of matching the cards scrolling under it.
    preview_width_preference = preview_outer.widthAnchor().constraintEqualToConstant_(
        native_ui.CONTENT_MAX_WIDTH
    )
    preview_width_preference.setPriority_(749)
    preview_width_preference.setActive_(True)

    target.color_swatches = swatches
    target.color_hex_labels = hex_labels
    target.color_fields = {
        "preview_scenario_popup": preview_scenario_popup,
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
        "live_toggle": live_toggle,
        "fade_fields": fade_fields,
    }
    target.color_preview_rows = preview_rows
    refresh_blend_and_speed_fields(target)
    return root


def refresh_blend_and_speed_fields(target: StatusBarController) -> None:
    colors = target.settings.colors
    fields = target.color_fields
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
