"""Pure information architecture for SidePulse Settings.

The retained AppKit panes are intentionally preserved, but they are no longer
exposed as fifteen unrelated destinations.  This module owns the seven stable
categories, their child pages, and every translation from a legacy pane key to
the category a person sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

NATIVE_USAGE_PAGE: Final = "native_usage"


@dataclass(frozen=True, slots=True)
class SettingsPage:
    key: str
    label: str

    def __post_init__(self) -> None:
        if not self.key or not self.label:
            raise ValueError("settings page identity is required")


@dataclass(frozen=True, slots=True)
class SettingsCategory:
    key: str
    label: str
    icon: str
    pages: tuple[SettingsPage, ...]
    subtitle: str

    def __post_init__(self) -> None:
        if not self.key or not self.label or not self.icon or not self.subtitle:
            raise ValueError("settings category identity is required")
        if not self.pages or len({page.key for page in self.pages}) != len(self.pages):
            raise ValueError("settings category pages must be non-empty and unique")

    @property
    def default_page(self) -> str:
        return self.pages[0].key

    def contains(self, page_key: str) -> bool:
        return any(page.key == page_key for page in self.pages)

    def page_label(self, page_key: str) -> str:
        for page in self.pages:
            if page.key == page_key:
                return page.label
        raise KeyError(page_key)


SETTINGS_CATEGORIES: Final = (
    SettingsCategory(
        "overview",
        "Overview",
        "house",
        (SettingsPage("profile", "Overview"),),
        "Identity, defaults, and this Mac at a glance.",
    ),
    SettingsCategory(
        "agents_providers",
        "Agents & Providers",
        "cpu",
        (
            SettingsPage("agents", "Connected"),
            SettingsPage("installed_agents", "Installed"),
        ),
        "Agent hooks, provider hosts, and source health.",
    ),
    SettingsCategory(
        "usage",
        "Usage",
        "gauge",
        (
            SettingsPage(NATIVE_USAGE_PAGE, "Usage Center"),
            SettingsPage("history", "History"),
            SettingsPage("capacity", "Capacity"),
        ),
        "Quota windows, resets, tokens, estimates, and history.",
    ),
    SettingsCategory(
        "devices_screen_bar",
        "Devices & Screen Bar",
        "display",
        (
            SettingsPage("devices", "Devices"),
            SettingsPage("colors_screen_bar", "Screen Bar"),
            SettingsPage("power", "Power"),
        ),
        "Physical devices, on-screen light, and Mac power behavior.",
    ),
    SettingsCategory(
        "appearance_motion",
        "Appearance & Motion",
        "paintpalette",
        (
            SettingsPage("color_studio", "Studio"),
            SettingsPage("animations", "Lid Animations"),
        ),
        "One studio for color, animation, previews, and saved looks.",
    ),
    SettingsCategory(
        "notifications_focus",
        "Notifications & Focus",
        "bell",
        (
            SettingsPage("notifications", "Notifications"),
            SettingsPage("focus", "Focus"),
            SettingsPage("led_behavior", "Signals"),
        ),
        "What may interrupt you, when, and how strongly.",
    ),
    SettingsCategory(
        # Calendar, Reminders, and weather are the FUN ambient half of
        # the product, and filing them under "Advanced" hid them from
        # the exact person they were built for ("why is all of the
        # system-fun stuff hidden behind advanced menus?", 2026-08-21).
        "today_ambient",
        "Today",
        "sun.max",
        (SettingsPage("extras", "Today"),),
        "Calendar, Reminders, and weather — on your lights and in the menu.",
    ),
    SettingsCategory(
        "advanced_diagnostics",
        "Advanced & Diagnostics",
        "wrench.and.screwdriver",
        (SettingsPage("debug", "Diagnostics"),),
        "Diagnostics and recovery.",
    ),
)

_CATEGORY_BY_KEY = MappingProxyType({category.key: category for category in SETTINGS_CATEGORIES})
_CATEGORY_BY_PAGE = MappingProxyType(
    {
        page.key: category
        for category in SETTINGS_CATEGORIES
        for page in category.pages
    }
)


def category_for_key(key: str) -> SettingsCategory:
    """Return the visible category for a category or retained pane key."""
    category = _CATEGORY_BY_KEY.get(key) or _CATEGORY_BY_PAGE.get(key)
    if category is None:
        raise KeyError(key)
    return category


def page_for_request(category: SettingsCategory, requested: str | None) -> str:
    """Choose a child page without letting stale navigation escape its category."""
    if requested and category.contains(requested):
        return requested
    return category.default_page


def sidebar_items() -> tuple[tuple[str, str], ...]:
    return tuple((category.key, category.label) for category in SETTINGS_CATEGORIES)


def sidebar_icons() -> dict[str, str]:
    return {category.key: category.icon for category in SETTINGS_CATEGORIES}


def legacy_page_keys() -> tuple[str, ...]:
    return tuple(
        page.key
        for category in SETTINGS_CATEGORIES
        for page in category.pages
        if page.key != NATIVE_USAGE_PAGE
    )


__all__ = [
    "NATIVE_USAGE_PAGE",
    "SETTINGS_CATEGORIES",
    "SettingsCategory",
    "SettingsPage",
    "category_for_key",
    "legacy_page_keys",
    "page_for_request",
    "sidebar_icons",
    "sidebar_items",
]
