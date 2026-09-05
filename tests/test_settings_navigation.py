from __future__ import annotations

from pathlib import Path

import pytest

from sidepulse.settings_navigation import (
    NATIVE_EFFECT_STUDIO_PAGE,
    NATIVE_USAGE_PAGE,
    SETTINGS_CATEGORIES,
    category_for_key,
    legacy_page_keys,
    page_for_request,
    sidebar_items,
)

ROOT = Path(__file__).resolve().parents[1]


def test_weather_settings_disclose_separate_ip_location_consent() -> None:
    source = (ROOT / "src/sidepulse/settings_window.py").read_text(encoding="utf-8")

    assert '"Use network address for weather location"' in source
    assert '"weather_ip_geolocation_enabled"' in source
    assert "ipapi.co" in source
    assert "Weather alerts stay off until you enter coordinates" in source


def test_settings_navigation_has_eight_stable_categories() -> None:
    assert [category.label for category in SETTINGS_CATEGORIES] == [
        "Overview",
        "Agents & Providers",
        "Usage",
        "Devices & Screen Bar",
        "Lighting",
        "Notifications & Focus",
        # The ambient half got its own front door -- filing calendar,
        # Reminders and weather under "Advanced" hid them (2026-08-21).
        "Today",
        "Advanced",
    ]
    assert len(sidebar_items()) == 8
    assert len({category.key for category in SETTINGS_CATEGORIES}) == 8


def test_global_action_recorder_stays_inside_existing_overview_page() -> None:
    overview = category_for_key("profile")

    assert overview.label == "Overview"
    assert [(page.key, page.label) for page in overview.pages] == [
        ("profile", "Overview")
    ]
    assert "global_actions" not in legacy_page_keys()


def test_every_retained_pane_has_exactly_one_visible_home() -> None:
    pages = [
        page.key
        for category in SETTINGS_CATEGORIES
        for page in category.pages
    ]
    assert len(pages) == len(set(pages))
    assert set(legacy_page_keys()) == {
        "profile",
        "usage_activity",
        "agents",
        "installed_agents",
        "history",
        "capacity",
        "devices",
        "colors_screen_bar",
        "power",
        "color_studio",
        NATIVE_EFFECT_STUDIO_PAGE,
        "animations",
        "notifications",
        "focus",
        "led_behavior",
        "extras",
        "debug",
    }
    assert NATIVE_USAGE_PAGE in pages


def test_lighting_is_one_workspace_with_three_clear_subpages() -> None:
    lighting = category_for_key("color_studio")

    assert lighting.label == "Lighting"
    assert [(page.key, page.label) for page in lighting.pages] == [
        ("color_studio", "Colors"),
        (NATIVE_EFFECT_STUDIO_PAGE, "Effects"),
        ("animations", "Lid Programs"),
    ]
    assert category_for_key(NATIVE_EFFECT_STUDIO_PAGE) is lighting


def test_category_lookup_accepts_category_and_child_keys() -> None:
    usage = category_for_key("usage")
    assert category_for_key(NATIVE_USAGE_PAGE) is usage
    assert category_for_key("history") is usage
    assert page_for_request(usage, "capacity") == "capacity"
    assert page_for_request(usage, "debug") == NATIVE_USAGE_PAGE
    with pytest.raises(KeyError):
        category_for_key("missing")
