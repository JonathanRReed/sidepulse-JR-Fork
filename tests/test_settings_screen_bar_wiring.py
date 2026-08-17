from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "sidepulse"


def test_native_provider_host_installs_settings_and_screen_bar_before_launch() -> None:
    source = (SRC / "provider_usage_status_bar.py").read_text(encoding="utf-8")

    assert "install_settings_navigation(_legacy, _settings_window)" in source
    assert "install_screen_bar_runtime()" in source
    assert "class JRProviderUsageStatusBarController" in source
    assert source.count("class JRProviderUsageStatusBarController") == 1
    assert "selectSettingsCategoryPage_" in source
    assert "openProviderUsageCenter_" in source


def test_screen_bar_installer_does_not_rebind_objective_c_classes() -> None:
    source = (SRC / "screen_bar_runtime.py").read_text(encoding="utf-8")

    assert "VirtualLedView._draw_compact_accent = _draw_compact_accent" in source
    assert "VirtualLedView._draw_wings_only = _draw_wings_only" in source
    assert "StatusBarController" not in source
    assert "objc.super" not in source
    assert "rounded_band_bounds" in source
    assert '== "bracket"' in source


def test_settings_runtime_reuses_retained_pane_builders() -> None:
    source = (SRC / "settings_category_runtime.py").read_text(encoding="utf-8")

    assert "settings_window._build_settings_pane(target, page_key)" in source
    assert "NATIVE_USAGE_PAGE" in source
    assert "Open Usage Center…" in source
    assert "class " not in source
