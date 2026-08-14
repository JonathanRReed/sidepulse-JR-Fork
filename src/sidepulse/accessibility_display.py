from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AccessibilityDisplayPreferences:
    reduce_motion: bool = False
    reduce_transparency: bool = False
    increase_contrast: bool = False
    differentiate_without_color: bool = False


_CONSERVATIVE_PREFERENCES = AccessibilityDisplayPreferences(
    reduce_motion=True,
    reduce_transparency=True,
    increase_contrast=True,
    differentiate_without_color=True,
)

_SELECTORS = (
    "accessibilityDisplayShouldReduceMotion",
    "accessibilityDisplayShouldReduceTransparency",
    "accessibilityDisplayShouldIncreaseContrast",
    "accessibilityDisplayShouldDifferentiateWithoutColor",
)


def _default_workspace() -> object:
    from AppKit import NSWorkspace

    return NSWorkspace.sharedWorkspace()


def read_accessibility_display_preferences(
    workspace: object | None = None,
) -> AccessibilityDisplayPreferences:
    """Read one exact, bounded snapshot from the AppKit workspace."""
    source = _default_workspace() if workspace is None else workspace
    values: list[bool] = []
    for selector in _SELECTORS:
        reader = getattr(source, selector)
        if not callable(reader):
            raise TypeError(f"workspace selector is not callable: {selector}")
        value = reader()
        if type(value) is not bool:
            raise TypeError(f"workspace selector did not return bool: {selector}")
        values.append(value)
    return AccessibilityDisplayPreferences(*values)


def refresh_accessibility_display_preferences(
    previous: AccessibilityDisplayPreferences | None,
    workspace: object | None = None,
) -> AccessibilityDisplayPreferences:
    """Refresh preferences, failing conservatively without losing known truth."""
    try:
        return read_accessibility_display_preferences(workspace)
    except Exception:
        return _CONSERVATIVE_PREFERENCES if previous is None else previous

