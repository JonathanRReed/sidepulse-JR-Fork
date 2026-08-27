from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "sidepulse" / "provider_usage_status_bar.py"
# The usage-row builder was extracted here for the facade's size ratchet
# (2026-08-27); the menu-composition contract spans both files.
MENU_MODULE = ROOT / "src" / "sidepulse" / "usage_menu_injection.py"


def _tree():
    return ast.parse(MODULE.read_text(encoding="utf-8"))


def _method(name: str):
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing method: {name}")


def _calls(node):
    result = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        function = call.func
        if isinstance(function, ast.Name):
            result.append(function.id)
        elif isinstance(function, ast.Attribute):
            result.append(function.attr)
    return tuple(result)


def test_provider_usage_runs_through_background_service_and_main_thread_apply():
    request_calls = _calls(_method("_request_provider_usage"))
    apply_calls = _calls(_method("applyProviderUsageState_"))
    refresh_calls = _calls(_method("refresh_"))

    assert "request" in request_calls
    assert "performSelectorOnMainThread_withObject_waitUntilDone_" not in request_calls
    assert "detect_reset_events" in apply_calls
    assert "save_seen_reset_events" in apply_calls
    assert "_request_provider_usage" in refresh_calls
    assert "refresh_now" not in refresh_calls


def test_menu_replaces_legacy_capacity_card_with_compact_native_usage():
    source = MODULE.read_text(encoding="utf-8")
    menu_source = MENU_MODULE.read_text(encoding="utf-8")
    assert "_original_build_menu" in source
    assert "_usage_menu_item" in menu_source
    assert "project_usage_menu" in menu_source
    assert "Open Usage Center…" in menu_source
    assert "No reading" not in source
    assert "no reading" not in source


def test_wrapper_does_not_rebind_objc_super_through_a_mutable_global():
    source = MODULE.read_text(encoding="utf-8")
    assert "def init(" not in source
    assert "objc.super(" not in source


def test_termination_closes_provider_service():
    calls = _calls(_method("applicationWillTerminate_"))
    assert "close" in calls
