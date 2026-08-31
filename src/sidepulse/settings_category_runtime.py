"""AppKit host for the seven-category Settings information architecture.

This module composes the retained, tested pane builders inside stable category
containers.  It does not duplicate their controls or add another window or
Objective-C controller class.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

from AppKit import (
    NSLayoutConstraint,
    NSSegmentedControl,
    NSSegmentSwitchTrackingSelectOne,
    NSView,
)

from . import settings_navigation as navigation
from .product_identity import PRODUCT_DISPLAY_NAME

_BRACKET_STYLE_CHOICES = ("auto", "spatial", "identity", "bracket")

MAX_PROVIDER_PROFILE_SETTINGS_ROWS = 128

# This composition facade deliberately declares no classes. Functional named
# tuples keep the render boundary immutable and typed without becoming another
# controller or weakening the existing no-class architecture ratchet.
ProviderProfileSettingsChoice = NamedTuple(  # noqa: UP014
    "ProviderProfileSettingsChoice",
    [("value", str | int | None), ("label", str)],
)
ProviderProfileSettingsField = NamedTuple(  # noqa: UP014
    "ProviderProfileSettingsField",
    [
        (
            "key",
            Literal[
                "label",
                "color_override",
                "retention_days",
                "remote_sharing_choice",
                "open_session_action",
            ],
        ),
        ("label", str),
        ("control_kind", Literal["text", "color", "choice"]),
        ("value", str | int | None),
        ("options", tuple[ProviderProfileSettingsChoice, ...]),
        ("help_text", str),
    ],
)
ProviderInstanceProfileSettingsRow = NamedTuple(  # noqa: UP014
    "ProviderInstanceProfileSettingsRow",
    [
        ("provider_id", str),
        ("source_instance_id", str),
        ("heading", str),
        ("fields", tuple[ProviderProfileSettingsField, ...]),
    ],
)
ProviderInstanceProfileSettingsModel = NamedTuple(  # noqa: UP014
    "ProviderInstanceProfileSettingsModel",
    [("rows", tuple[ProviderInstanceProfileSettingsRow, ...])],
)

_RETENTION_SETTINGS_CHOICES = (
    ProviderProfileSettingsChoice(0, "Don't keep history"),
    ProviderProfileSettingsChoice(7, "7 days"),
    ProviderProfileSettingsChoice(30, "30 days"),
    ProviderProfileSettingsChoice(90, "90 days"),
)
_REMOTE_SHARING_SETTINGS_CHOICES = (
    ProviderProfileSettingsChoice("never", "This Mac only"),
    ProviderProfileSettingsChoice("status_only", "Quota status only"),
)
_OPEN_SESSION_SETTINGS_CHOICES = (
    ProviderProfileSettingsChoice("app", "Provider app"),
    ProviderProfileSettingsChoice("terminal", "Terminal"),
    ProviderProfileSettingsChoice("vscode", "Visual Studio Code"),
)


def build_provider_instance_profile_settings_model(
    policies,
) -> ProviderInstanceProfileSettingsModel:
    """Build the privacy-safe, AppKit-independent instance settings model."""
    from .provider_feature_settings import ProviderInstancePolicyProjection

    if type(policies) is not ProviderInstancePolicyProjection:
        raise TypeError("expected ProviderInstancePolicyProjection")
    visual_items = policies.visual.providers
    if len(visual_items) > MAX_PROVIDER_PROFILE_SETTINGS_ROWS:
        raise ValueError("too many provider profiles for Settings")
    identities = {item.identity for item in visual_items}
    if any(
        {item.identity for item in projection.providers} != identities
        for projection in (
            policies.retention,
            policies.sharing,
            policies.session_action,
        )
    ):
        raise ValueError("profile policy domains must contain the same exact provider instances")

    rows = []
    for visual in visual_items:
        provider_id, source_instance_id = visual.identity
        retention = policies.retention.provider(provider_id, source_instance_id)
        sharing = policies.sharing.provider(provider_id, source_instance_id)
        session_action = policies.session_action.provider(provider_id, source_instance_id)
        rows.append(
            ProviderInstanceProfileSettingsRow(
                provider_id,
                source_instance_id,
                visual.label,
                (
                    ProviderProfileSettingsField(
                        "label",
                        "Name",
                        "text",
                        visual.label,
                        (),
                        "A local name for this exact provider account or profile.",
                    ),
                    ProviderProfileSettingsField(
                        "color_override",
                        "Accent",
                        "color",
                        visual.color_override,
                        (),
                        "Use a custom accent, or keep the provider's default color.",
                    ),
                    ProviderProfileSettingsField(
                        "retention_days",
                        "Usage history",
                        "choice",
                        retention.retention_days,
                        _RETENTION_SETTINGS_CHOICES,
                        "Choose how long this Mac keeps percentage history for this instance.",
                    ),
                    ProviderProfileSettingsField(
                        "remote_sharing_choice",
                        "Remote sharing",
                        "choice",
                        sharing.remote_sharing_choice,
                        _REMOTE_SHARING_SETTINGS_CHOICES,
                        "Choose whether other configured Macs can receive quota status.",
                    ),
                    ProviderProfileSettingsField(
                        "open_session_action",
                        "Open session with",
                        "choice",
                        session_action.open_session_action,
                        _OPEN_SESSION_SETTINGS_CHOICES,
                        "Choose how JR Bar opens work from this exact instance.",
                    ),
                ),
            )
        )
    return ProviderInstanceProfileSettingsModel(tuple(rows))


def provider_instance_profile_settings_row(
    model: ProviderInstanceProfileSettingsModel,
    provider_id: str,
    source_instance_id: str = "default",
) -> ProviderInstanceProfileSettingsRow:
    """Return one exact row, never a provider-level fallback."""
    if type(model) is not ProviderInstanceProfileSettingsModel:
        raise TypeError("expected ProviderInstanceProfileSettingsModel")
    return next(
        row
        for row in model.rows
        if (row.provider_id, row.source_instance_id)
        == (provider_id, source_instance_id)
    )


def _provider_profile_control_payload(row, field, value):
    return {
        "provider_id": row.provider_id,
        "source_instance_id": row.source_instance_id,
        "field_key": field.key,
        "value": value,
    }


def _provider_profile_control(target, row, field, ui):
    selector = "updateProviderInstanceProfile:"
    payload = _provider_profile_control_payload(row, field, field.value)
    if field.control_kind in {"text", "color"}:
        control = ui.make_field(
            "" if field.value is None else str(field.value),
            target=target,
            action=selector,
        )
        if field.control_kind == "color":
            control.setPlaceholderString_("Provider default or #RRGGBB")
        ui.constrain_width(control, 230.0)
    else:
        control = ui.make_popup_button(target, selector)
        for option in field.options:
            control.addItemWithTitle_(option.label)
            item = control.lastItem()
            option_payload = _provider_profile_control_payload(
                row,
                field,
                option.value,
            )
            item.setRepresentedObject_(option_payload)
            if option.value == field.value:
                control.selectItem_(item)
    control.setIdentifier_(
        f"provider-profile:{row.provider_id}:{row.source_instance_id}:{field.key}"
    )
    control.setRepresentedObject_(payload)
    return control


def _render_provider_instance_profile_cards(target, stack, model, ui) -> None:
    from .provider_usage_platform import provider_descriptor

    cards = {}
    controls = {}
    for row in model.rows:
        descriptor = provider_descriptor(row.provider_id)
        outer, inner = ui.make_card(row.heading)
        outer.setAccessibilityElement_(True)
        ui.set_accessibility_metadata(
            outer,
            label=(
                f"{row.heading} provider profile, "
                f"{descriptor.label}, {row.source_instance_id}"
            ),
            help_text=(
                "Settings for the exact "
                f"{descriptor.label} source {row.source_instance_id}."
            ),
            role="AXGroup",
        )
        inner.addArrangedSubview_(
            ui.make_label(
                f"{descriptor.label} · {row.source_instance_id}",
                secondary=True,
                size=11.0,
            )
        )
        for field in row.fields:
            control = _provider_profile_control(target, row, field, ui)
            control_row = ui.make_row(
                field.label,
                control,
                help_text=field.help_text,
            )
            ui.set_accessibility_metadata(
                control,
                label=f"{row.heading}, {field.label}",
                help_text=field.help_text,
            )
            inner.addArrangedSubview_(control_row)
            controls[(row.provider_id, row.source_instance_id, field.key)] = control
        cards[(row.provider_id, row.source_instance_id)] = outer
        stack.addArrangedSubview_(outer)
    target._sidepulse_provider_profile_settings_cards = cards
    target._sidepulse_provider_profile_settings_controls = controls


def _sync_provider_instance_profile_settings(target, settings) -> None:
    """Refresh cached profile cards from one committed durable snapshot."""
    from .provider_feature_settings import project_instance_policies
    from .provider_usage_platform import provider_descriptor
    from .provider_usage_settings import ProviderUsageSettings

    if type(settings) is not ProviderUsageSettings:
        raise TypeError("expected ProviderUsageSettings")
    model = build_provider_instance_profile_settings_model(
        project_instance_policies(settings)
    )
    target._sidepulse_provider_profile_settings_model = model
    cards = getattr(target, "_sidepulse_provider_profile_settings_cards", {})
    controls = getattr(target, "_sidepulse_provider_profile_settings_controls", {})
    for row in model.rows:
        identity = (row.provider_id, row.source_instance_id)
        card = cards.get(identity)
        if card is not None:
            arranged = card.arrangedSubviews()
            if arranged:
                arranged[0].setStringValue_(row.heading)
            descriptor = provider_descriptor(row.provider_id)
            card.setAccessibilityLabel_(
                f"{row.heading} provider profile, "
                f"{descriptor.label}, {row.source_instance_id}"
            )
        for field in row.fields:
            control = controls.get((*identity, field.key))
            if control is None:
                continue
            payload = _provider_profile_control_payload(row, field, field.value)
            control.setRepresentedObject_(payload)
            control.setAccessibilityLabel_(f"{row.heading}, {field.label}")
            if field.control_kind in {"text", "color"}:
                control.setStringValue_(
                    "" if field.value is None else str(field.value)
                )
                continue
            for index in range(control.numberOfItems()):
                item = control.itemAtIndex_(index)
                option_payload = item.representedObject()
                if (
                    isinstance(option_payload, dict)
                    and option_payload.get("value") == field.value
                ):
                    control.selectItem_(item)
                    break


def save_provider_instance_profile_setting(
    target,
    sender,
    *,
    updater=None,
    log,
) -> bool:
    """Persist one profile choice and reconcile the cached AppKit controls."""
    if updater is None:
        from .provider_usage_controller_actions import (
            update_provider_instance_profile as updater,
        )

    try:
        updated = updater(target, sender)
    except Exception as exc:
        log(f"provider profile settings: {exc}")
        committed = getattr(
            target,
            "_sidepulse_provider_usage_settings_snapshot",
            None,
        )
        try:
            _sync_provider_instance_profile_settings(target, committed)
        except (TypeError, ValueError, StopIteration) as restore_exc:
            log(f"provider profile settings restore: {restore_exc}")
        message = getattr(target, "set_settings_message", None)
        if callable(message):
            message(f"Could not save provider profile: {exc}")
        return False

    _sync_provider_instance_profile_settings(target, updated)
    message = getattr(target, "set_settings_message", None)
    if callable(message):
        message("Provider profile saved.")
    window = getattr(target, "_sidepulse_provider_usage_window", None)
    if window is not None:
        window.refresh(target.provider_usage_state)
    return True


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
    overview_outer, overview_inner = ui.make_card("Usage Center")
    summary = ui.make_wrapping_label(
        f"{PRODUCT_DISPLAY_NAME} collects quota, reset, token, model, credit, incident, and "
        "estimated-cost facts directly. Sources that need permission say so "
        "and offer the next action instead of showing “no reading”.",
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

    # Curation: what the menu's Usage rows show, and which providers get
    # a row at all. Prefer the worker-produced immutable snapshot. A pane
    # opened before the first provider refresh may pay one bounded initial
    # load; repaint and apply paths never reopen the settings document.
    from .provider_feature_settings import (
        project_instance_policies,
        project_presentation_settings,
    )
    from .provider_usage_platform import provider_descriptor
    from .provider_usage_settings import (
        ProviderUsageSettings,
        load_provider_usage_settings,
    )

    usage_settings = getattr(
        target,
        "_sidepulse_provider_usage_settings_snapshot",
        None,
    )
    if type(usage_settings) is not ProviderUsageSettings:
        usage_settings = load_provider_usage_settings().settings
        target._sidepulse_provider_usage_settings_snapshot = usage_settings
    target._sidepulse_provider_presentation_settings = (
        project_presentation_settings(usage_settings)
    )
    profile_settings_model = build_provider_instance_profile_settings_model(
        project_instance_policies(usage_settings)
    )
    target._sidepulse_provider_profile_settings_model = profile_settings_model
    display_outer, display_inner = ui.make_card("In the Usage Menu")
    display_inner.addArrangedSubview_(
        ui.make_wrapping_label(
            "Choose what each provider's row shows. The Usage Center window "
            "always shows everything.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    element_boxes = []
    for flag, label in (
        ("show_meters", "Meter per limit lane"),
        ("show_totals", "Token and model totals"),
        ("show_cost", "Estimated cost"),
        ("show_detail_lanes", "Model-scoped lanes (e.g. “Fable only”)"),
        ("show_menu_bar_percent", "Tightest limit next to the menu-bar icon"),
    ):
        box = ui.make_checkbox(label, target, "toggleUsageMenuElement:")
        box.setIdentifier_(flag)
        box.setState_(1 if getattr(usage_settings.menu_display, flag) else 0)
        display_inner.addArrangedSubview_(box)
        element_boxes.append(box)
    stack.addArrangedSubview_(display_outer)

    providers_outer, providers_inner = ui.make_card("Providers in the Menu")
    providers_inner.addArrangedSubview_(
        ui.make_wrapping_label(
            "Hidden providers keep collecting; they just stay out of the "
            "menu (and the Usage Center still lists them).",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    provider_boxes = []
    provider_counts = {}
    for preference in usage_settings.providers:
        provider_counts[preference.provider_id] = (
            provider_counts.get(preference.provider_id, 0) + 1
        )
    for preference in usage_settings.providers:
        provider_label = provider_descriptor(preference.provider_id).label
        if provider_counts[preference.provider_id] > 1:
            provider_label = (
                f"{provider_label} · {preference.source_instance_id}"
            )
        box = ui.make_checkbox(
            provider_label,
            target,
            "toggleUsageMenuProvider:",
        )
        box.setIdentifier_(preference.provider_id)
        box.setRepresentedObject_(
            {
                "provider_id": preference.provider_id,
                "source_instance_id": preference.source_instance_id,
            }
        )
        box.setState_(1 if preference.menu_visible else 0)
        providers_inner.addArrangedSubview_(box)
        provider_boxes.append(box)
    stack.addArrangedSubview_(providers_outer)

    _render_provider_instance_profile_cards(
        target,
        stack,
        profile_settings_model,
        ui,
    )

    # The pane is cached across reopens. Keeping the box references lets
    # refresh_native_usage_summary apply worker-observed external changes
    # without touching disk on the AppKit path.
    target._sidepulse_usage_menu_boxes = (tuple(element_boxes), tuple(provider_boxes))

    return ui.wrap_in_scroll_pane(stack), {
        "native_usage_summary": summary,
        "native_usage_source_status": source_status,
    }, {}


def refresh_native_usage_summary(target) -> None:
    _sync_usage_menu_checkboxes(target)
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


def _sync_usage_menu_checkboxes(target) -> None:
    """Apply the current immutable settings snapshot to cached checkboxes."""
    boxes = getattr(target, "_sidepulse_usage_menu_boxes", None)
    if not boxes:
        return
    try:
        from .provider_usage_settings import ProviderUsageSettings

        settings = getattr(
            target,
            "_sidepulse_provider_usage_settings_snapshot",
            None,
        )
        if type(settings) is not ProviderUsageSettings:
            return
        element_boxes, provider_boxes = boxes
        for box in element_boxes:
            flag = str(box.identifier() or "")
            box.setState_(1 if getattr(settings.menu_display, flag, True) else 0)
        visible = {
            preference.identity: preference.menu_visible
            for preference in settings.providers
        }
        for box in provider_boxes:
            represented = getattr(box, "representedObject", None)
            payload = represented() if callable(represented) else None
            identity = (
                (
                    str(payload.get("provider_id") or ""),
                    str(payload.get("source_instance_id") or "default"),
                )
                if isinstance(payload, dict)
                else (str(box.identifier() or ""), "default")
            )
            box.setState_(
                1 if visible.get(identity, True) else 0
            )
    except Exception:
        return


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
        selector.setTag_(navigation.SETTINGS_CATEGORIES.index(category))
        selector.setTarget_(target)
        selector.setAction_("selectSettingsCategoryPage:")
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
    "MAX_PROVIDER_PROFILE_SETTINGS_ROWS",
    "ProviderInstanceProfileSettingsModel",
    "ProviderInstanceProfileSettingsRow",
    "ProviderProfileSettingsChoice",
    "ProviderProfileSettingsField",
    "build_provider_instance_profile_settings_model",
    "ensure_category",
    "install_settings_navigation",
    "provider_instance_profile_settings_row",
    "refresh_native_usage_summary",
    "requested_page_for_category",
    "save_provider_instance_profile_setting",
    "select_page",
    "show_category",
]
