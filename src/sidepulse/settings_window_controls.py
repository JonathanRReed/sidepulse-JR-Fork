"""Shared, explicitly imported controls for the native Settings window."""

from __future__ import annotations

from datetime import datetime

from AppKit import NSColor, NSOffState, NSOnState, NSView, NSWorkspace
from Foundation import NSURL

from . import colors as colors_module
from . import native_ui
from .effect_selection import (
    BLEND_MODE_OPTIONS,
    COLOR_PRESET_OPTIONS,
    PREVIEW_SCENARIO_OPTIONS,
    EffectOption,
    selected_option_index,
)
from .session_actions import (
    SESSION_OPEN_APP,
    SESSION_OPEN_TERMINAL,
    SESSION_OPEN_VSCODE,
)
from .settings import (
    CLOSED_LID_AWAKE_AGENTS,
    CLOSED_LID_AWAKE_ALWAYS,
    CLOSED_LID_AWAKE_CHOICES,
    CLOSED_LID_AWAKE_NEVER,
)

PREVIEW_DOT_SIZE = 22.0
CLOSED_LID_AWAKE_LABELS: dict[str, str] = {
    CLOSED_LID_AWAKE_NEVER: "Never",
    CLOSED_LID_AWAKE_AGENTS: "When Agents Work",
    CLOSED_LID_AWAKE_ALWAYS: "Always",
}


def nscolor_from_hex(hex_value: str) -> NSColor:
    red, green, blue = colors_module.hex_to_rgb(
        colors_module.normalize_hex(hex_value, "#000000")
    )
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(
        red / 255.0,
        green / 255.0,
        blue / 255.0,
        1.0,
    )


def make_blend_mode_popup(target):
    popup = native_ui.make_popup_button(target, "setBlendMode:")
    for option in BLEND_MODE_OPTIONS:
        popup.addItemWithTitle_(option.label)
        popup.lastItem().setRepresentedObject_({"blend_mode": option.value})
    return popup


def make_color_preset_popup(target):
    popup = native_ui.make_popup_button(target, "setColorPreset:")
    for option in COLOR_PRESET_OPTIONS:
        popup.addItemWithTitle_(option.label)
        item = popup.lastItem()
        item.setRepresentedObject_({"preset": option.value})
        if option.description:
            item.setToolTip_(option.description)
    return popup


def select_popup_item(popup, key: str, value) -> None:
    for index in range(popup.numberOfItems()):
        payload = popup.itemAtIndex_(index).representedObject()
        if isinstance(payload, dict) and payload.get(key) == value:
            popup.selectItemAtIndex_(index)
            return


def select_effect_popup_item(
    popup, options: tuple[EffectOption, ...], value: str
) -> None:
    index = selected_option_index(options, value)
    if index is not None and index < popup.numberOfItems():
        popup.selectItemAtIndex_(index)


def select_color_preset(popup, preset: str) -> None:
    select_effect_popup_item(popup, COLOR_PRESET_OPTIONS, preset)


def select_blend_mode(popup, blend_mode: str) -> None:
    select_effect_popup_item(popup, BLEND_MODE_OPTIONS, blend_mode)


def make_preview_scenario_popup(target):
    popup = native_ui.make_popup_button(target, "setPreviewScenario:")
    for option in PREVIEW_SCENARIO_OPTIONS:
        popup.addItemWithTitle_(option.label)
        popup.lastItem().setRepresentedObject_({"scenario": option.value})
    return popup


def select_preview_scenario(popup, scenario: str) -> None:
    select_effect_popup_item(popup, PREVIEW_SCENARIO_OPTIONS, scenario)


def make_closed_lid_awake_policy_popup(target):
    popup = native_ui.make_popup_button(target, "setClosedLidAwakePolicyFromPopup:")
    for policy in CLOSED_LID_AWAKE_CHOICES:
        popup.addItemWithTitle_(CLOSED_LID_AWAKE_LABELS[policy])
        popup.lastItem().setRepresentedObject_({"policy": policy})
    return popup


def add_preview_dot(parent, x: float, y: float):
    dot = NSView.alloc().initWithFrame_(
        ((x, y), (PREVIEW_DOT_SIZE, PREVIEW_DOT_SIZE))
    )
    dot.setWantsLayer_(True)
    layer = dot.layer()
    layer.setBackgroundColor_(NSColor.blackColor().CGColor())
    layer.setCornerRadius_(PREVIEW_DOT_SIZE / 2.0)
    layer.setBorderWidth_(1.0)
    layer.setBorderColor_(NSColor.separatorColor().CGColor())
    parent.addSubview_(dot)
    return dot


def set_preview_dot_color(dot, hex_color: str) -> None:
    try:
        dot.setWantsLayer_(True)
        dot.layer().setBackgroundColor_(nscolor_from_hex(hex_color).CGColor())
    except Exception:
        pass


def set_preview_dot_rgb(dot, red: int, green: int, blue: int) -> None:
    try:
        dot.setWantsLayer_(True)
        color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
            max(0, min(255, red)) / 255.0,
            max(0, min(255, green)) / 255.0,
            max(0, min(255, blue)) / 255.0,
            1.0,
        )
        dot.layer().setBackgroundColor_(color.CGColor())
    except Exception:
        pass


def _provider_open_actions(provider: str) -> tuple[str, ...]:
    if provider == "claude":
        return (SESSION_OPEN_VSCODE, SESSION_OPEN_APP, SESSION_OPEN_TERMINAL)
    if provider == "codex":
        return (SESSION_OPEN_APP, SESSION_OPEN_TERMINAL)
    return (SESSION_OPEN_TERMINAL,)


def _provider_open_action_label(provider: str, action: str) -> str:
    if action == SESSION_OPEN_VSCODE:
        return "VS Code"
    if action == SESSION_OPEN_TERMINAL:
        return "Terminal"
    return {"codex": "Codex", "claude": "Claude"}.get(provider, "App")


def make_provider_opener_popup(provider: str, target):
    popup = native_ui.make_popup_button(target, "setProviderOpenPreference:")
    for action in _provider_open_actions(provider):
        popup.addItemWithTitle_(_provider_open_action_label(provider, action))
        popup.lastItem().setRepresentedObject_(
            {"provider": provider, "action": action}
        )
    return popup


def set_field_value(field, value: str) -> None:
    if field is not None:
        field.setStringValue_(value)


def set_checkbox_state(button, enabled: bool) -> None:
    if button is not None:
        button.setState_(NSOnState if enabled else NSOffState)


def log_status_bar(message: str) -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"{timestamp} {message}", flush=True)


def open_url(url: str) -> None:
    ns_url = NSURL.URLWithString_(url)
    if ns_url is not None:
        NSWorkspace.sharedWorkspace().openURL_(ns_url)


__all__ = [
    "add_preview_dot",
    "log_status_bar",
    "make_blend_mode_popup",
    "make_closed_lid_awake_policy_popup",
    "make_color_preset_popup",
    "make_preview_scenario_popup",
    "make_provider_opener_popup",
    "nscolor_from_hex",
    "open_url",
    "select_blend_mode",
    "select_color_preset",
    "select_effect_popup_item",
    "select_popup_item",
    "select_preview_scenario",
    "set_checkbox_state",
    "set_field_value",
    "set_preview_dot_color",
    "set_preview_dot_rgb",
]
