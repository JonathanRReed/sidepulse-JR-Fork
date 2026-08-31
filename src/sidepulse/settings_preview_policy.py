"""Accessibility and motion policy shared by native settings previews."""

from __future__ import annotations

from AppKit import NSColor, NSOffState, NSOnState

from . import colors as colors_module
from .led_status import LedDisplayState, program_for_display_state, style_to_program


def reduce_motion_active(target) -> bool:
    preferences = getattr(target, "_accessibility_display_preferences", None)
    return bool(getattr(preferences, "reduce_motion", False))


def apply_thumb_selection(thumbs: dict, selected_pattern: str) -> None:
    for pattern, thumb in thumbs.items():
        layer = thumb.layer()
        selected = pattern == selected_pattern
        if layer is not None and selected:
            layer.setBorderWidth_(2.0)
            layer.setBorderColor_(NSColor.controlAccentColor().CGColor())
        elif layer is not None:
            layer.setBorderWidth_(0.0)
        choice = getattr(thumb, "accessibility_choice_control", None)
        if choice is not None:
            choice.setState_(NSOnState if selected else NSOffState)


def static_preview_color(program: str, fallback: str = "#FFFFFF") -> str:
    for token in str(program or "").replace("\n", " ").split():
        candidate = token.strip()
        if len(candidate) != 7 or not candidate.startswith("#"):
            continue
        try:
            int(candidate[1:], 16)
        except ValueError:
            continue
        return candidate.upper()
    return fallback


def lid_preset_preview_program(target, program: str) -> str:
    if reduce_motion_active(target):
        return static_preview_color(program)
    return f"{program}\nrepeat"


def signal_preview_program(target, style, *, color=None) -> str:
    if reduce_motion_active(target):
        return style.color
    return style_to_program(style, 255, color=color)


def mode_animation_thumb_program(target, mode_key: str, style: str) -> str:
    """Render one animation choice in its mode color or a static equivalent."""
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
        luminance = sum(int(stripped[index : index + 2], 16) for index in (0, 2, 4)) / 3.0
    except ValueError:
        luminance = 255.0
    if luminance < 24.0:
        mode_hex = "#9A9A9A"
    if reduce_motion_active(target):
        return mode_hex
    kwargs = {color_kwarg: mode_hex}
    style_kwarg = colors_module._MODE_KEY_TO_STYLE_KWARG.get(mode_key)
    if style_kwarg:
        kwargs[style_kwarg] = style
    try:
        return program_for_display_state(state, led_count=8, **kwargs)
    except (TypeError, ValueError):
        return target.settings.colors.mode_color(mode_key)
