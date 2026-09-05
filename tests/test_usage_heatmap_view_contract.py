from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "src" / "sidepulse" / "usage_heatmap_view.py"
SETTINGS = ROOT / "src" / "sidepulse" / "settings_window.py"


def test_profile_pane_installs_one_compact_heatmap_below_the_line_chart():
    source = SETTINGS.read_text(encoding="utf-8")
    assert "UsageHeatmapView" in source
    assert 'fields["profile_usage_heatmap"] = heatmap' in source
    assert source.index("profile_usage_graph") < source.index("profile_usage_heatmap")


def test_heatmap_view_supports_aggregate_and_provider_selection():
    source = VIEW.read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert {"setHeatmap_", "selectProvider_", "_render_cells"} <= methods
    assert "All providers" in source
    assert "provider_id" in source


def test_heatmap_cells_publish_the_model_accessibility_copy():
    source = VIEW.read_text(encoding="utf-8")
    assert "cell.accessibility_label" in source
    assert "setAccessibilityLabel_" in source
    assert 'setAccessibilityRole_("AXStaticText")' in source
    assert "setToolTip_" in source


def test_unavailable_cells_are_visually_distinct_from_zero_activity():
    source = VIEW.read_text(encoding="utf-8")
    assert 'provider.data_status == "unavailable"' in source
    assert "setBorderWidth_(1.0)" in source
    assert "NSColor.clearColor()" in source


def test_heatmap_view_is_a_content_free_projection_host():
    source = VIEW.read_text(encoding="utf-8")
    for forbidden in (
        "scan_usage",
        "sqlite3",
        "subprocess",
        "urlopen",
        "read_text(",
        "read_bytes(",
        "message_id",
        "dedupe",
        "transcript",
    ):
        assert forbidden not in source
