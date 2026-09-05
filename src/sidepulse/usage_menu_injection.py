"""Menu-surgery helpers for the JR usage row's injection into the dropdown.

Extracted verbatim from provider_usage_status_bar (2026-08-26) for its
facade size ratchet. These are pure NSMenu manipulations: find the anchor
row, remove a leftover legacy usage card, and tidy separators after the
insert. The facade's build_menu wrapper is their only caller.
"""

from __future__ import annotations


def _fail_closed_menu_projection_settings(state):
    """Keep an unknown settings state from revealing cached account rows."""

    hidden_instances = frozenset(
        (snapshot.provider_id, snapshot.source_instance_id)
        for snapshot in getattr(state, "snapshots", ())
    )
    return None, frozenset(), hidden_instances, None, True


def menu_index(menu, title_prefix: str) -> int:
    for position in range(menu.numberOfItems()):
        if str(menu.itemAtIndex_(position).title() or "").startswith(title_prefix):
            return position
    return -1


def remove_legacy_usage_item(menu, target) -> None:
    """Remove a stale legacy usage card if one is present in this menu.

    Mostly a no-op since the legacy build stopped constructing the card
    when the JR plane owns the row (jr_plane_owns_usage_menu_item); this
    still catches an item carried over from an older build of the menu.
    """
    item = getattr(target, "_usage_menu_item", None)
    if item is None:
        return
    try:
        index = menu.indexOfItem_(item)
    except Exception:
        index = -1
    if index >= 0:
        menu.removeItemAtIndex_(index)
    else:
        # The compact facade may have grouped the legacy view under its own
        # "Usage · …" parent row; removing only the nested item would leave
        # a second, empty usage row behind. Remove the whole parent.
        for parent_index in range(menu.numberOfItems()):
            parent = menu.itemAtIndex_(parent_index)
            submenu = parent.submenu()
            if submenu is None:
                continue
            try:
                nested = submenu.indexOfItem_(item)
            except Exception:
                nested = -1
            if nested >= 0:
                menu.removeItemAtIndex_(parent_index)
                break
    target._usage_menu_item = None
    target._usage_menu_view = None


def remove_redundant_separators(menu) -> None:
    index = menu.numberOfItems() - 1
    while index >= 0:
        item = menu.itemAtIndex_(index)
        previous = menu.itemAtIndex_(index - 1) if index > 0 else None
        if item.isSeparatorItem() and (
            index == 0
            or index == menu.numberOfItems() - 1
            or (previous is not None and previous.isSeparatorItem())
        ):
            menu.removeItemAtIndex_(index)
        index -= 1


def _apply_meter_attributed_title(item, title: str, *, color=None):
    try:
        from AppKit import (
            NSFont,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSMutableAttributedString,
        )
        base_font = NSFont.menuFontOfSize_(13.0)
        symbols_font = NSFont.fontWithName_size_("Apple Symbols", 13.0) or NSFont.fontWithName_size_("Menlo", 12.0)
        attr_str = NSMutableAttributedString.alloc().initWithString_(title)
        if base_font is not None:
            attr_str.addAttribute_value_range_(NSFontAttributeName, base_font, (0, len(title)))
        if color is not None:
            attr_str.addAttribute_value_range_(NSForegroundColorAttributeName, color, (0, len(title)))
        if symbols_font is not None and ("▰" in title or "▱" in title):
            for idx, ch in enumerate(title):
                if ch in ("▰", "▱"):
                    attr_str.addAttribute_value_range_(NSFontAttributeName, symbols_font, (idx, 1))
        item.setAttributedTitle_(attr_str)
    except Exception:
        pass


def disabled_usage_item(title: str, *, alert: bool = False):
    from AppKit import NSColor, NSMenuItem

    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title,
        None,
        "",
    )
    item.setEnabled_(False)
    color = NSColor.systemOrangeColor() if alert else None
    if alert or "▰" in title or "▱" in title:
        _apply_meter_attributed_title(item, title, color=color)
    return item


def native_usage_menu_item(target):
    """Build the JR usage row (moved from the facade 2026-08-27 for its
    size ratchet; function-level imports keep the load order safe)."""
    import time

    from . import status_bar as _host
    from .provider_feature_settings import (
        ProviderInstancePolicyProjection,
        ProviderPresentationSettings,
    )
    from .provider_usage_menu import project_usage_menu
    from .provider_usage_runtime import ProviderUsageState
    from .status_feeds import incident_row_title, shared_status_feed_poller

    _legacy = getattr(_host, "_legacy", _host)

    state = getattr(
        target,
        "_sidepulse_provider_usage_state",
        ProviderUsageState((), None, None, False),
    )
    try:
        settings = target._usage_menu_settings()
        if type(settings) is not ProviderPresentationSettings:
            raise ValueError("provider usage settings are not ready")
        display = settings.menu_display
        privacy_mode = settings.menu_display.privacy_mode
        hidden = settings.hidden_menu_providers()
        hidden_instances = settings.hidden_menu_instances()
        thresholds = {
            preference.identity: preference.threshold_remaining
            for preference in settings.providers
        }
    except Exception:
        display, hidden, hidden_instances, thresholds, privacy_mode = (
            _fail_closed_menu_projection_settings(state)
        )
    policies = getattr(target, "_sidepulse_provider_instance_policies", None)
    visual = (
        policies.visual
        if type(policies) is ProviderInstancePolicyProjection
        else None
    )
    try:
        active_instances = frozenset(target._active_usage_instances())
    except Exception:
        active_instances = frozenset()
    projection = project_usage_menu(
        state,
        now=time.time(),
        display=display,
        hidden_providers=hidden,
        hidden_instances=hidden_instances,
        thresholds=thresholds,
        visual=visual,
        privacy_mode=privacy_mode,
        active_instances=active_instances,
    )
    item = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        projection.title,
        None,
        "",
    )
    if "▰" in projection.title or "▱" in projection.title:
        _apply_meter_attributed_title(item, projection.title)
    submenu = _legacy.NSMenu.alloc().init()
    submenu.setAutoenablesItems_(False)
    # Vendor incidents outrank everything below: "the provider is down"
    # must never read as "your quota fetch broke".
    poller = shared_status_feed_poller()
    poller.start()
    incidents = poller.current()
    for provider_id in sorted(incidents):
        incident = incidents[provider_id]
        row = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            incident_row_title(incident), "openProviderStatusPage:", ""
        )
        row.setTarget_(target)
        row.setRepresentedObject_(incident.page_url)
        submenu.addItem_(row)
    if incidents:
        submenu.addItem_(_legacy.NSMenuItem.separatorItem())
    if not projection.rows:
        # An empty submenu has two very different causes: nothing is
        # connected, or everything is curated out. Diagnosing the wrong
        # one ("connect sources" when sources ARE collecting) sends the
        # user to the wrong fix.
        if state.snapshots and hidden:
            submenu.addItem_(
                disabled_usage_item(
                    "All providers hidden — choose some in Settings → Usage"
                )
            )
        else:
            submenu.addItem_(
                disabled_usage_item("Open Usage Center to connect provider sources")
            )
    for row in projection.rows:
        row_title = row.title
        if row.provider_id in incidents:
            row_title = f"{row_title} · ⚠ vendor incident"
        provider_item = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            row_title,
            None,
            "",
        )
        if row.tooltip:
            provider_item.setToolTip_(row.tooltip)
        if row.accessibility_label:
            provider_item.setAccessibilityLabel_(row.accessibility_label)
        provider_menu = _legacy.NSMenu.alloc().init()
        provider_menu.setAutoenablesItems_(False)
        lane_lines = getattr(row, "lane_lines", ())
        alert_indexes = set(getattr(row, "alert_lane_indexes", ()))
        if lane_lines:
            for index, line in enumerate(lane_lines):
                provider_menu.addItem_(
                    disabled_usage_item(line, alert=index in alert_indexes)
                )
        elif row.detail:
            provider_menu.addItem_(disabled_usage_item(row.detail))
        if row.usage_detail:
            if lane_lines or row.detail:
                provider_menu.addItem_(_legacy.NSMenuItem.separatorItem())
            provider_menu.addItem_(disabled_usage_item(row.usage_detail))
        if row.action_label:
            provider_menu.addItem_(_legacy.NSMenuItem.separatorItem())
            action = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                row.action_label,
                "performProviderUsageAction:",
                "",
            )
            action.setTarget_(target)
            action.setRepresentedObject_(
                {
                    "provider_id": row.provider_id,
                    "source_instance_id": row.source_instance_id,
                    "action": row.action_label,
                }
            )
            provider_menu.addItem_(action)
        provider_item.setSubmenu_(provider_menu)
        submenu.addItem_(provider_item)
    submenu.addItem_(_legacy.NSMenuItem.separatorItem())
    open_center = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Open Usage Center…",
        "openProviderUsageCenter:",
        "",
    )
    open_center.setTarget_(target)
    submenu.addItem_(open_center)
    refresh = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Refresh Usage",
        "refreshProviderUsage:",
        "",
    )
    refresh.setTarget_(target)
    submenu.addItem_(refresh)
    item.setSubmenu_(submenu)
    target._sidepulse_provider_usage_menu_item = item
    return item


__all__ = [
    "disabled_usage_item",
    "menu_index",
    "native_usage_menu_item",
    "remove_legacy_usage_item",
    "remove_redundant_separators",
]
