"""Native Power settings with four independent owner decisions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

import objc
from Foundation import NSObject

from . import native_ui
from .models import AgentMode
from .settings import save_settings

POWER_CHOICE_LABELS: Final = {
    "agent": "Keep Mac awake while agents work",
    "display": "Keep displays awake during holds",
    "battery": "Continue agent holds on battery",
    "closed_lid": "Closed-lid policy",
}

_AGENT_HELP = (
    "Prevents automatic Mac sleep while a main agent is working. "
    "The display choice below remains independent."
)
_DISPLAY_HELP = (
    "Off by default. The Mac can keep an agent working while its displays "
    "sleep and the screen locks normally."
)
_BATTERY_HELP = (
    "When enabled, agent holds continue while unplugged. The low-battery "
    "threshold still releases the hold so the Mac can conserve power."
)
_CLOSED_LID_HELP = (
    "Closed-lid behavior is a separate, stronger policy. Its optional helper "
    "does not turn on merely because ordinary agent keep-awake is enabled."
)


def _switch_is_on(sender) -> bool:
    try:
        return int(sender.state()) == 1
    except Exception:
        return False


def _set_switch_state(control, enabled: bool) -> None:
    if control is not None:
        control.setState_(1 if enabled else 0)


def _make_accessible_switch(
    label: str,
    target,
    selector: str,
    *,
    help_text: str,
):
    row, control = native_ui.make_switch_row(
        label,
        target,
        selector,
        help_text=help_text,
    )
    control.setAccessibilityLabel_(label)
    control.setAccessibilityHelp_(help_text)
    return row, control


class PowerSettingsActions(NSObject):
    """Retained AppKit actions for settings outside the legacy controller."""

    def initWithController_(self, controller):
        self = objc.super(PowerSettingsActions, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def _save_and_sync(self, candidate, *, setting_name: str, success: str) -> None:
        controller = self.controller
        try:
            save_settings(candidate)
        except Exception as exc:
            refresh_power_settings_controls(controller)
            controller.set_settings_message(
                f"Could not save {setting_name} power setting: {exc}"
            )
            return
        controller.settings = candidate
        current_mode = getattr(getattr(controller, "keep_awake", None), "last_mode", None)
        controller.sync_keep_awake(
            current_mode if type(current_mode) is AgentMode else AgentMode.IDLE_READY
        )
        controller.set_settings_message(success)

    @objc.IBAction
    def toggleAgentKeepAwake_(self, sender):
        enabled = _switch_is_on(sender)
        self._save_and_sync(
            self.controller.settings.with_agent_keep_awake_enabled(enabled),
            setting_name="agent keep-awake",
            success=(
                "Agents will prevent automatic Mac sleep while working."
                if enabled
                else "Agent work will no longer prevent automatic Mac sleep."
            ),
        )

    @objc.IBAction
    def toggleKeepDisplayAwake_(self, sender):
        enabled = _switch_is_on(sender)
        self._save_and_sync(
            self.controller.settings.with_keep_display_awake(enabled),
            setting_name="display",
            success=(
                "Displays will stay awake during active holds."
                if enabled
                else "Displays may sleep while the Mac keeps agents working."
            ),
        )

    @objc.IBAction
    def toggleKeepAwakeOnBattery_(self, sender):
        enabled = _switch_is_on(sender)
        self._save_and_sync(
            self.controller.settings.with_keep_awake_on_battery(enabled),
            setting_name="battery",
            success=(
                "Agent holds will continue on battery until the low-battery limit."
                if enabled
                else "Agent holds will release whenever the Mac is on battery."
            ),
        )


def refresh_power_settings_controls(target) -> None:
    buttons = getattr(target, "settings_buttons", {}) or {}
    settings = target.settings
    _set_switch_state(
        buttons.get("agent_keep_awake_enabled"),
        settings.agent_keep_awake_enabled,
    )
    _set_switch_state(
        buttons.get("keep_display_awake"),
        settings.keep_display_awake,
    )
    _set_switch_state(
        buttons.get("keep_awake_on_battery"),
        settings.keep_awake_on_battery,
    )


def build_power_settings_pane(
    target,
    *,
    make_closed_lid_policy_popup: Callable[[object], object],
):
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    actions = PowerSettingsActions.alloc().initWithController_(target)
    target.power_settings_actions = actions

    agent_outer, agent_inner = native_ui.make_card("Agent Work")
    agent_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            _AGENT_HELP,
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    agent_row, agent_switch = _make_accessible_switch(
        POWER_CHOICE_LABELS["agent"],
        actions,
        "toggleAgentKeepAwake:",
        help_text=_AGENT_HELP,
    )
    _set_switch_state(agent_switch, target.settings.agent_keep_awake_enabled)
    agent_inner.addArrangedSubview_(agent_row)

    grace_field = native_ui.make_field(
        f"{target.settings.closed_lid_grace_minutes:g}",
        target=target,
        action="applyClosedLidGraceMinutes:",
    )
    native_ui.constrain_width(grace_field, 56.0)
    grace_controls = native_ui.make_stack(
        orientation="horizontal",
        spacing=native_ui.SPACE_XS,
    )
    grace_controls.addArrangedSubview_(grace_field)
    grace_controls.addArrangedSubview_(native_ui.make_label("min", secondary=True))
    agent_inner.addArrangedSubview_(
        native_ui.make_row(
            "Release delay",
            grace_controls,
            help_text=(
                "Keeps a short buffer after work appears to stop so a quiet "
                "command does not put the Mac to sleep too early."
            ),
        )
    )
    stack.addArrangedSubview_(agent_outer)

    display_outer, display_inner = native_ui.make_card("Display")
    display_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            _DISPLAY_HELP,
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    display_row, display_switch = _make_accessible_switch(
        POWER_CHOICE_LABELS["display"],
        actions,
        "toggleKeepDisplayAwake:",
        help_text=_DISPLAY_HELP,
    )
    _set_switch_state(display_switch, target.settings.keep_display_awake)
    display_inner.addArrangedSubview_(display_row)
    stack.addArrangedSubview_(display_outer)

    closed_outer, closed_inner = native_ui.make_card("Closed Lid")
    closed_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            _CLOSED_LID_HELP,
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    policy_popup = make_closed_lid_policy_popup(target)
    policy_popup.setAccessibilityLabel_(POWER_CHOICE_LABELS["closed_lid"])
    policy_popup.setAccessibilityHelp_(_CLOSED_LID_HELP)
    closed_inner.addArrangedSubview_(
        native_ui.make_row(
            POWER_CHOICE_LABELS["closed_lid"],
            policy_popup,
            help_text=_CLOSED_LID_HELP,
        )
    )
    stack.addArrangedSubview_(closed_outer)

    battery_outer, battery_inner = native_ui.make_card("Battery")
    battery_inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            _BATTERY_HELP,
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    battery_hold_row, battery_hold_switch = _make_accessible_switch(
        POWER_CHOICE_LABELS["battery"],
        actions,
        "toggleKeepAwakeOnBattery:",
        help_text=_BATTERY_HELP,
    )
    _set_switch_state(battery_hold_switch, target.settings.keep_awake_on_battery)
    battery_inner.addArrangedSubview_(battery_hold_row)
    native_ui.add_separator(battery_inner)

    leds_row, battery_leds = native_ui.make_switch_row(
        "Show battery on LEDs",
        target,
        "setBatteryLedDisplayFromCheckbox:",
    )
    battery_inner.addArrangedSubview_(leds_row)
    preview_row, battery_power_preview = native_ui.make_switch_row(
        "Show battery for 7s on plug/unplug",
        target,
        "setBatteryPowerPreviewFromCheckbox:",
    )
    battery_inner.addArrangedSubview_(preview_row)
    trickle_row, battery_charging_idle = native_ui.make_switch_row(
        "Charging trickle while idle",
        target,
        "setBatteryChargingIdleFromCheckbox:",
        help_text=(
            "When idle and plugged in, the bar fills to the charge level with "
            "a slow pulse. Agent signals still take priority."
        ),
    )
    battery_inner.addArrangedSubview_(trickle_row)
    native_ui.add_separator(battery_inner)
    low_power_row, low_battery_switch = native_ui.make_switch_row(
        "Charge reminder when battery is low",
        target,
        "toggleLowBatteryAlert:",
        help_text=(
            "Below the threshold while unplugged, displays use the configured "
            "low-battery signal."
        ),
    )
    battery_inner.addArrangedSubview_(low_power_row)
    threshold_field = native_ui.make_field(
        f"{target.settings.low_battery_threshold_percent:g}",
        target=target,
        action="applyLowBatteryThreshold:",
    )
    native_ui.constrain_width(threshold_field, 48.0)
    threshold_controls = native_ui.make_stack(
        orientation="horizontal",
        spacing=native_ui.SPACE_XS,
    )
    threshold_controls.addArrangedSubview_(threshold_field)
    threshold_controls.addArrangedSubview_(native_ui.make_label("%", secondary=True))
    battery_inner.addArrangedSubview_(
        native_ui.make_row("Below", threshold_controls)
    )
    stack.addArrangedSubview_(battery_outer)

    fields = {
        "closed_lid_awake_policy_popup": policy_popup,
        "closed_lid_grace_field": grace_field,
        "low_battery_threshold_field": threshold_field,
    }
    buttons = {
        "agent_keep_awake_enabled": agent_switch,
        "keep_display_awake": display_switch,
        "keep_awake_on_battery": battery_hold_switch,
        "battery_leds": battery_leds,
        "battery_power_preview": battery_power_preview,
        "battery_charging_idle": battery_charging_idle,
        "low_battery_alert": low_battery_switch,
    }
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


__all__ = [
    "POWER_CHOICE_LABELS",
    "PowerSettingsActions",
    "build_power_settings_pane",
    "refresh_power_settings_controls",
]
