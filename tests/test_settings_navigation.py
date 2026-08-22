from __future__ import annotations

import pytest

from sidepulse.settings_navigation import (
    NATIVE_USAGE_PAGE,
    SETTINGS_CATEGORIES,
    category_for_key,
    legacy_page_keys,
    page_for_request,
    sidebar_items,
)


def test_settings_navigation_has_eight_stable_categories() -> None:
    assert [category.label for category in SETTINGS_CATEGORIES] == [
        "Overview",
        "Agents & Providers",
        "Usage",
        "Devices & Screen Bar",
        "Appearance & Motion",
        "Notifications & Focus",
        # The ambient half got its own front door -- filing calendar,
        # Reminders and weather under "Advanced" hid them (2026-08-21).
        "Today",
        "Advanced & Diagnostics",
    ]
    assert len(sidebar_items()) == 8
    assert len({category.key for category in SETTINGS_CATEGORIES}) == 8


def test_every_retained_pane_has_exactly_one_visible_home() -> None:
    pages = [
        page.key
        for category in SETTINGS_CATEGORIES
        for page in category.pages
    ]
    assert len(pages) == len(set(pages))
    assert set(legacy_page_keys()) == {
        "profile",
        "agents",
        "installed_agents",
        "history",
        "capacity",
        "devices",
        "colors_screen_bar",
        "power",
        "color_studio",
        "animations",
        "notifications",
        "focus",
        "led_behavior",
        "extras",
        "debug",
    }
    assert NATIVE_USAGE_PAGE in pages


def test_category_lookup_accepts_category_and_child_keys() -> None:
    usage = category_for_key("usage")
    assert category_for_key(NATIVE_USAGE_PAGE) is usage
    assert category_for_key("history") is usage
    assert page_for_request(usage, "capacity") == "capacity"
    assert page_for_request(usage, "debug") == NATIVE_USAGE_PAGE
    with pytest.raises(KeyError):
        category_for_key("missing")
