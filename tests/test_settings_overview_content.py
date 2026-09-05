"""Settings destinations must contain the controls their names promise."""

from types import SimpleNamespace

import pytest
from Foundation import NSObject

from sidepulse import settings_category_runtime as runtime
from sidepulse import settings_navigation as navigation
from sidepulse.settings import AgentMonitorSettings


class _OverviewContentTarget(NSObject):
    pass


@pytest.fixture
def target(monkeypatch):
    controller = _OverviewContentTarget.alloc().init()
    controller.settings = AgentMonitorSettings()
    controller.settings_fields = {}
    controller.global_action_lifecycle = SimpleNamespace(registry=None)
    # The chart builder normally starts a local transcript scan. This test
    # checks real native controls without reading the user's transcripts.
    monkeypatch.setattr("sidepulse.usage_graph_worker.refresh_usage_graph", lambda _: None)
    return controller


def _views(root):
    yield root
    for child in root.subviews():
        yield from _views(child)


def _text(root):
    return [str(view.stringValue()) for view in _views(root) if hasattr(view, "stringValue")]


def test_overview_keeps_global_actions_without_usage_chart_or_event_hook(target):
    category = navigation.category_for_key("overview")
    pane, fields, _ = runtime._build_child(target, category.default_page)

    assert "global_action_settings_pane" in fields
    assert "profile_usage_graph" not in fields
    assert "Event hook" not in _text(pane)
    assert "About" in _text(pane)


def test_usage_activity_is_reachable_with_chart_heatmap_and_no_global_recorder(target):
    category = navigation.category_for_key("usage")
    activity = next((page for page in category.pages if page.key == "usage_activity"), None)
    assert activity is not None, "Usage statistics must be reachable from Usage"
    pane, fields, _ = runtime._build_child(target, activity.key)

    assert fields["profile_usage_graph"] in tuple(_views(pane))
    assert fields["profile_usage_heatmap"] in tuple(_views(pane))
    assert "Event hook" in _text(pane)
    assert "global_action_settings_pane" not in fields


@pytest.mark.parametrize("mode", ["sessions", "percent"])
def test_activity_legend_names_only_selected_providers(mode):
    from sidepulse.settings_window import usage_graph_legend_text

    settings = AgentMonitorSettings().with_usage_graph_providers(("grok",)).with_usage_display_mode(mode)
    legend = usage_graph_legend_text(settings)

    assert "Grok" in legend
    assert "Claude" not in legend
    assert "Codex" not in legend


def test_reopened_activity_seeds_summary_from_its_cached_model(target):
    target.usage_graph_model = {"summary": "Selected local history", "series": ()}
    target.usage_summary_text = "Unrelated provider summary"

    _pane, fields, _ = runtime._build_child(target, "usage_activity")

    assert str(fields["profile_usage_label"].stringValue()) == "Selected local history"


def test_activity_does_not_mix_unfiltered_provider_status_into_local_history(target):
    target.usage_graph_model = {"summary": "Selected local history", "series": ()}
    target.codex_summary_text = "Capacity source unavailable"
    target.usage_detail_text = "Unrelated Claude quota detail"

    pane, _fields, _ = runtime._build_child(target, "usage_activity")

    assert "Selected local history" in _text(pane)
    assert "Capacity source unavailable" not in _text(pane)
    assert "Unrelated Claude quota detail" not in _text(pane)
