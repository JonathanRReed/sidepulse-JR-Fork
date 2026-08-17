from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS_BAR = ROOT / "src" / "sidepulse" / "status_bar.py"


def _source() -> str:
    return STATUS_BAR.read_text(encoding="utf-8")


def test_status_bar_wires_cached_device_identity_without_diskutil_on_menu_thread() -> None:
    source = _source()
    tree = ast.parse(source)
    build_menu = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build_menu"
    )
    calls = {
        node.func.attr
        for node in ast.walk(build_menu)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "device_id_for_root" in source
    assert "DeviceIdentityCache" in source
    assert "diskutil" not in source
    assert "subprocess" not in source
    assert "run" not in calls


def test_root_menu_groups_devices_and_removes_permanent_tip() -> None:
    source = _source()
    assert 'title in {"Profiles", "Timer"}' in source
    assert 'title.startswith("SidePulse")' in source
    assert 'title.startswith("Tip:")' in source
    assert 'item.setTitle_("Diagnostics…")' in source
    assert 'plan_by_key["devices"].title' in source


def test_menu_compaction_does_not_define_another_objc_controller() -> None:
    classes = {
        node.name
        for node in ast.walk(ast.parse(_source()))
        if isinstance(node, ast.ClassDef)
    }
    assert classes == {"_StatusBarFacade"}
