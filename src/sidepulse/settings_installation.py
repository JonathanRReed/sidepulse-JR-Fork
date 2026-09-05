"""One-time wiring between Settings navigation and retained module globals."""

from __future__ import annotations

from . import settings_navigation as navigation

_BRACKET_STYLES = ("auto", "spatial", "identity", "bracket")


def install_settings_navigation(legacy, settings_window) -> None:
    items = navigation.sidebar_items()
    icons = navigation.sidebar_icons()
    legacy.SETTINGS_SIDEBAR_ITEMS = items
    legacy.DEFAULT_SETTINGS_PANE = navigation.SETTINGS_CATEGORIES[0].key
    legacy.SIDEBAR_ICONS = {**getattr(legacy, "SIDEBAR_ICONS", {}), **icons}
    settings_window.DEFAULT_SETTINGS_PANE = navigation.SETTINGS_CATEGORIES[0].key

    from . import _settings_legacy, settings

    _settings_legacy.BRACKET_STYLE_CHOICES = _BRACKET_STYLES
    settings.BRACKET_STYLE_CHOICES = _BRACKET_STYLES
    settings_window.BRACKET_STYLE_CHOICES = _BRACKET_STYLES
    legacy.BRACKET_STYLE_CHOICES = _BRACKET_STYLES


__all__ = ["install_settings_navigation"]
