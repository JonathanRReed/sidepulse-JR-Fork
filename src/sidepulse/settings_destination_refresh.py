"""Cached refresh policy for the selected Settings destination."""

from __future__ import annotations

from . import settings_navigation
from .settings_category_runtime import refresh_native_usage_summary


def refresh_settings_destination(controller, page_key: str) -> None:
    """Refresh the selected pane without rebuilding the Settings graph."""
    if page_key == settings_navigation.NATIVE_USAGE_PAGE:
        refresh_native_usage_summary(controller)
    elif page_key == "installed_agents":
        controller.refresh_installed_agents_settings_projection()
        controller.reconcile_installed_agent_inventory()
    elif page_key == "capacity":
        controller.refresh_capacity_settings_projection()
    elif page_key == "color_studio":
        controller.refresh_colors_window()
    elif page_key == settings_navigation.NATIVE_EFFECT_STUDIO_PAGE:
        from .lighting_settings_pane import refresh_brightness_behavior_controls

        refresh_brightness_behavior_controls(controller)
