"""Cohesive Lighting workspace pages that wrap retained editors."""

from __future__ import annotations


def build_effects_page(target):
    from . import native_ui as ui

    stack = ui.make_fill_stack(spacing=ui.SPACE_L)
    outer, inner = ui.make_card("Effect Studio")
    inner.addArrangedSubview_(
        ui.make_wrapping_label(
            "Build and preview live lighting effects in the same Lighting workspace as colors and lid programs.",
            secondary=True,
            size=12.0,
            max_width=560.0,
        )
    )
    inner.addArrangedSubview_(ui.make_button("Open Effect Studio…", target, "openEffectStudio:"))
    stack.addArrangedSubview_(outer)

    behavior_outer, behavior_inner = ui.make_card("Brightness Behavior")
    sleep_row, sleep_switch = ui.make_switch_row(
        "Dim when the display sleeps",
        target,
        "toggleSleepDim:",
        help_text=("Keeps lighting visible at the configured dim level instead of turning it off."),
    )
    sleep_switch.setState_(1 if target.settings.sleep_dim_enabled else 0)
    behavior_inner.addArrangedSubview_(sleep_row)
    sleep_level = ui.make_field(
        f"{round(target.settings.sleep_dim_fraction * 100):g}",
        target=target,
        action="applySleepDimPercentage:",
    )
    ui.constrain_width(sleep_level, 56.0)
    sleep_level.setEnabled_(target.settings.sleep_dim_enabled)
    sleep_controls = ui.make_stack(orientation="horizontal", spacing=ui.SPACE_XS)
    sleep_controls.addArrangedSubview_(sleep_level)
    sleep_controls.addArrangedSubview_(ui.make_label("% brightness", secondary=True))
    behavior_inner.addArrangedSubview_(
        ui.make_row(
            "Sleep level",
            sleep_controls,
            help_text="Between 5% and 100% of the normal lighting level.",
        )
    )
    idle_row, idle_switch = ui.make_switch_row(
        "Turn lighting off after a long idle period",
        target,
        "toggleIdleAutoOff:",
        help_text="Separate from ordinary idle dimming; defaults to 60 minutes.",
    )
    idle_switch.setState_(1 if target.settings.idle_auto_off_enabled else 0)
    behavior_inner.addArrangedSubview_(idle_row)
    idle_timeout = ui.make_field(
        f"{target.settings.idle_auto_off_after_minutes:g}",
        target=target,
        action="applyIdleAutoOffTimeout:",
    )
    ui.constrain_width(idle_timeout, 56.0)
    idle_timeout.setEnabled_(target.settings.idle_auto_off_enabled)
    idle_controls = ui.make_stack(orientation="horizontal", spacing=ui.SPACE_XS)
    idle_controls.addArrangedSubview_(idle_timeout)
    idle_controls.addArrangedSubview_(ui.make_label("minutes", secondary=True))
    behavior_inner.addArrangedSubview_(
        ui.make_row(
            "Turn off after",
            idle_controls,
            help_text="Between 5 minutes and 24 hours of continuous idle time.",
        )
    )
    stack.addArrangedSubview_(behavior_outer)
    return (
        ui.wrap_in_scroll_pane(stack),
        {
            "sleep_dim_percentage": sleep_level,
            "idle_auto_off_timeout": idle_timeout,
        },
        {
            "sleep_dim_enabled": sleep_switch,
            "idle_auto_off_enabled": idle_switch,
        },
    )


def refresh_brightness_behavior_controls(target) -> None:
    """Project persisted brightness behavior into an already-built page."""
    buttons = getattr(target, "settings_buttons", {})
    fields = getattr(target, "settings_fields", {})
    sleep_enabled = bool(target.settings.sleep_dim_enabled)
    idle_enabled = bool(target.settings.idle_auto_off_enabled)
    sleep_switch = buttons.get("sleep_dim_enabled")
    idle_switch = buttons.get("idle_auto_off_enabled")
    sleep_level = fields.get("sleep_dim_percentage")
    idle_timeout = fields.get("idle_auto_off_timeout")
    if sleep_switch is not None:
        sleep_switch.setState_(1 if sleep_enabled else 0)
    if idle_switch is not None:
        idle_switch.setState_(1 if idle_enabled else 0)
    if sleep_level is not None:
        sleep_level.setStringValue_(f"{round(target.settings.sleep_dim_fraction * 100):g}")
        sleep_level.setEnabled_(sleep_enabled)
    if idle_timeout is not None:
        idle_timeout.setStringValue_(f"{target.settings.idle_auto_off_after_minutes:g}")
        idle_timeout.setEnabled_(idle_enabled)


def install_explicit_bracket_style(target) -> None:
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


__all__ = [
    "build_effects_page",
    "install_explicit_bracket_style",
    "refresh_brightness_behavior_controls",
]
