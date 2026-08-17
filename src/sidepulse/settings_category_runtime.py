"""AppKit host for the seven-category Settings information architecture.

This module composes the retained, tested pane builders inside stable category
containers.  It does not duplicate their controls or add another window or
Objective-C controller class.
"""

from __future__ import annotations

from AppKit import (
    NSLayoutConstraint,
    NSSegmentedControl,
    NSSegmentSwitchTrackingSelectOne,
    NSView,
)

from . import settings_navigation as navigation

_BRACKET_STYLE_CHOICES = ("auto", "spatial", "identity", "bracket")


def install_settings_navigation(legacy, settings_window) -> None:
    """Make both halves of the extracted Settings implementation agree."""
    items = navigation.sidebar_items()
    icons = navigation.sidebar_icons()
    legacy.SETTINGS_SIDEBAR_ITEMS = items
    legacy.DEFAULT_SETTINGS_PANE = navigation.SETTINGS_CATEGORIES[0].key
    legacy.SIDEBAR_ICONS = {**getattr(legacy, "SIDEBAR_ICONS", {}), **icons}
    # build_settings_window lives in the extracted module and captured its own
    # globals when `_install` ran, so patch its copy as well.
    settings_window.DEFAULT_SETTINGS_PANE = navigation.SETTINGS_CATEGORIES[0].key

    # The explicit geometry style is a fourth option, separate from color
    # projection. Patch every module copy before settings are loaded so a saved
    # `bracket` value survives validation and round-trips normally.
    from . import _settings_legacy, settings

    _settings_legacy.BRACKET_STYLE_CHOICES = _BRACKET_STYLE_CHOICES
    settings.BRACKET_STYLE_CHOICES = _BRACKET_STYLE_CHOICES
    settings_window.BRACKET_STYLE_CHOICES = _BRACKET_STYLE_CHOICES
    legacy.BRACKET_STYLE_CHOICES = _BRACKET_STYLE_CHOICES


def _ensure_storage(target) -> None:
    if not hasattr(target, "_settings_category_children"):
        target._settings_category_children = {}
        target._settings_category_content = {}
        target._settings_category_selectors = {}
        target._settings_category_current = {}
        target._pending_settings_page = None


def _native_usage_pane(target):
    from . import native_ui as ui

    stack = ui.make_fill_stack(spacing=ui.SPACE_L)
    overview_outer, overview_inner = ui.make_card("Native Usage Center")
    summary = ui.make_wrapping_label(
        "SidePulse collects quota, reset, token, model, credit, incident, and "
        "estimated-cost facts directly. Sources that need permission say so "
        "and offer the next action instead of showing ‘no reading’.",
        secondary=True,
        size=12.0,
        max_width=560.0,
    )
    overview_inner.addArrangedSubview_(summary)
    controls = ui.make_stack(orientation="horizontal", spacing=ui.SPACE_S)
    controls.addArrangedSubview_(
        ui.make_button("Open Usage Center…", target, "openProviderUsageCenter:")
    )
    controls.addArrangedSubview_(
        ui.make_button("Refresh Now", target, "refreshNativeProviderUsage:")
    )
    controls.addArrangedSubview_(ui.make_hspacer())
    overview_inner.addArrangedSubview_(controls)
    stack.addArrangedSubview_(overview_outer)

    source_outer, source_inner = ui.make_card("Source Health")
    source_status = ui.make_wrapping_label(
        "Provider sources are starting.",
        secondary=False,
        size=12.0,
        max_width=560.0,
    )
    source_inner.addArrangedSubview_(source_status)
    source_inner.addArrangedSubview_(
        ui.make_wrapping_label(
            "Connect or repair a provider from the Usage Center. Browser-backed "
            "sources remain off until consent is granted for that exact provider, "
            "browser, profile, domain, and field set.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    stack.addArrangedSubview_(source_outer)
    return ui.wrap_in_scroll_pane(stack), {
        "native_usage_summary": summary,
        "native_usage_source_status": source_status,
    }, {}


def refresh_native_usage_summary(target) -> None:
    field = getattr(target, "settings_fields", {}).get("native_usage_source_status")
    if field is None:
        return
    state = getattr(target, "_sidepulse_provider_usage_state", None)
    try:
        from .provider_usage_menu import glance_summary

        text = glance_summary(state) if state is not None else "Provider sources are starting."
    except Exception:
        text = "Provider source status is temporarily unavailable."
    field.setStringValue_(text)


def _build_child(target, page_key: str):
    if page_key == navigation.NATIVE_USAGE_PAGE:
        return _native_usage_pane(target)
    from . import settings_window

    return settings_window._build_settings_pane(target, page_key)


def _install_explicit_bracket_style(target) -> None:
    popup = getattr(target, "settings_fields", {}).get("bracket_style_popup")
    if popup is None:
        return
    for index in range(popup.numberOfItems()):
        if popup.itemAtIndex_(index).representedObject() == "bracket":
            return
    popup.addItemWithTitle_("Rounded band with corner brackets")
    item = popup.lastItem()
    item.setRepresentedObject_("bracket")
    if target.settings.screen_bar_bracket_style == "bracket":
        popup.selectItem_(item)


def _after_child_built(target, page_key: str) -> None:
    if page_key == "notifications":
        target.refresh_notification_authorization_controls()
        target.start_notification_authorization_refresh()
    elif page_key == "history":
        target.start_operator_history_restore()
        target.refresh_operator_history_projection()
    elif page_key == "installed_agents":
        target.refresh_installed_agents_settings_projection()
        target.reconcile_installed_agent_inventory()
    elif page_key == "capacity":
        target.refresh_capacity_settings_projection()
    elif page_key == "colors_screen_bar":
        _install_explicit_bracket_style(target)
    elif page_key == navigation.NATIVE_USAGE_PAGE:
        refresh_native_usage_summary(target)


def _build_category_container(target, category: navigation.SettingsCategory):
    from . import native_ui as ui

    root = NSView.alloc().init()
    root.setTranslatesAutoresizingMaskIntoConstraints_(False)

    header = ui.make_stack(orientation="vertical", spacing=6.0)
    header.addArrangedSubview_(ui.make_label(category.label, size=21.0))
    header.addArrangedSubview_(
        ui.make_wrapping_label(
            category.subtitle,
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    selector = None
    if len(category.pages) > 1:
        selector = NSSegmentedControl.alloc().init()
        selector.setSegmentCount_(len(category.pages))
        selector.setTrackingMode_(NSSegmentSwitchTrackingSelectOne)
        for index, page in enumerate(category.pages):
            selector.setLabel_forSegment_(page.label, index)
            selector.setWidth_forSegment_(
                max(92.0, min(150.0, 30.0 + len(page.label) * 7.0)),
                index,
            )
        selector.setSelectedSegment_(0)
        selector.setTarget_(target)
        selector.setAction_("selectSettingsCategoryPage:")
        selector.sidepulse_category_key = category.key
        header.addArrangedSubview_(selector)

    content = NSView.alloc().init()
    content.setTranslatesAutoresizingMaskIntoConstraints_(False)
    root.addSubview_(header)
    root.addSubview_(content)
    NSLayoutConstraint.activateConstraints_(
        [
            header.topAnchor().constraintEqualToAnchor_constant_(root.topAnchor(), 18.0),
            header.leadingAnchor().constraintEqualToAnchor_constant_(root.leadingAnchor(), 24.0),
            header.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(
                root.trailingAnchor(), -24.0
            ),
            content.topAnchor().constraintEqualToAnchor_constant_(header.bottomAnchor(), 12.0),
            content.leadingAnchor().constraintEqualToAnchor_(root.leadingAnchor()),
            content.trailingAnchor().constraintEqualToAnchor_(root.trailingAnchor()),
            content.bottomAnchor().constraintEqualToAnchor_(root.bottomAnchor()),
        ]
    )
    return root, content, selector


def ensure_category(target, category_key: str, requested_page: str | None = None):
    _ensure_storage(target)
    category = navigation.category_for_key(category_key)
    panes = getattr(target, "settings_panes", None)
    host = getattr(target, "_settings_pane_container", None)
    if panes is None or host is None:
        return None

    container = panes.get(category.key)
    if container is None:
        container, content, selector = _build_category_container(target, category)
        host.addSubview_(container)
        NSLayoutConstraint.activateConstraints_(
            [
                container.topAnchor().constraintEqualToAnchor_(host.topAnchor()),
                container.leadingAnchor().constraintEqualToAnchor_(host.leadingAnchor()),
                container.trailingAnchor().constraintEqualToAnchor_(host.trailingAnchor()),
                container.bottomAnchor().constraintEqualToAnchor_(host.bottomAnchor()),
            ]
        )
        container.setHidden_(True)
        panes[category.key] = container
        target._settings_category_content[category.key] = content
        target._settings_category_selectors[category.key] = selector
        target._settings_category_children[category.key] = {}

    select_page(target, category.key, requested_page)
    return panes[category.key]


def _ensure_child(target, category: navigation.SettingsCategory, page_key: str):
    children = target._settings_category_children[category.key]
    pane = children.get(page_key)
    if pane is not None:
        return pane
    content = target._settings_category_content[category.key]
    try:
        pane, fields, buttons = _build_child(target, page_key)
    except KeyError:
        return None
    content.addSubview_(pane)
    NSLayoutConstraint.activateConstraints_(
        [
            pane.topAnchor().constraintEqualToAnchor_(content.topAnchor()),
            pane.leadingAnchor().constraintEqualToAnchor_(content.leadingAnchor()),
            pane.trailingAnchor().constraintEqualToAnchor_(content.trailingAnchor()),
            pane.bottomAnchor().constraintEqualToAnchor_(content.bottomAnchor()),
        ]
    )
    pane.setHidden_(True)
    children[page_key] = pane
    target.settings_fields.update(fields)
    target.settings_buttons.update(buttons)
    _after_child_built(target, page_key)
    return pane


def select_page(target, category_key: str, requested_page: str | None = None) -> str:
    _ensure_storage(target)
    category = navigation.category_for_key(category_key)
    page_key = navigation.page_for_request(category, requested_page)
    pane = _ensure_child(target, category, page_key)
    if pane is None:
        page_key = category.default_page
        pane = _ensure_child(target, category, page_key)
    if pane is None:
        return page_key
    for key, child in target._settings_category_children[category.key].items():
        child.setHidden_(key != page_key)
    selector = target._settings_category_selectors.get(category.key)
    if selector is not None:
        index = next(
            index
            for index, page in enumerate(category.pages)
            if page.key == page_key
        )
        selector.setSelectedSegment_(index)
    target._settings_category_current[category.key] = page_key
    target.current_settings_pane = page_key
    refresh_native_usage_summary(target)
    return page_key


def show_category(target, category_key: str, requested_page: str | None = None) -> str:
    category = navigation.category_for_key(category_key)
    ensure_category(target, category.key, requested_page)
    for key, pane in getattr(target, "settings_panes", {}).items():
        pane.setHidden_(key != category.key)
    return select_page(target, category.key, requested_page)


def requested_page_for_category(target, category: navigation.SettingsCategory) -> str:
    pending = getattr(target, "_pending_settings_page", None)
    if pending and category.contains(pending):
        target._pending_settings_page = None
        return pending
    current = getattr(target, "_settings_category_current", {}).get(category.key)
    return navigation.page_for_request(category, current)


__all__ = [
    "ensure_category",
    "install_settings_navigation",
    "refresh_native_usage_summary",
    "requested_page_for_category",
    "select_page",
    "show_category",
]
