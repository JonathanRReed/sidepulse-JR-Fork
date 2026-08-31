from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "sidepulse" / "provider_usage_status_bar.py"
# The usage-row builder was extracted here for the facade's size ratchet
# (2026-08-27); the menu-composition contract spans both files.
MENU_MODULE = ROOT / "src" / "sidepulse" / "usage_menu_injection.py"
SETTINGS_CATEGORY_MODULE = ROOT / "src" / "sidepulse" / "settings_category_runtime.py"


def _tree():
    return ast.parse(MODULE.read_text(encoding="utf-8"))


def _method(name: str):
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing method: {name}")


def _function(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function: {name}")


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
    assert "apply_provider_usage_settings_snapshot" in apply_calls
    assert "submit" in apply_calls
    assert "threading.Thread" not in MODULE.read_text(encoding="utf-8")
    assert "_request_provider_usage" in refresh_calls
    assert "refresh_now" not in refresh_calls


def test_usage_apply_and_menu_projection_do_not_reload_settings_on_the_ui_thread():
    ready = _method("_provider_usage_ready")
    ready_calls = _calls(ready)
    apply_calls = _calls(_method("applyProviderUsageState_"))
    menu_settings_calls = _calls(_method("_usage_menu_settings"))
    native_menu_calls = _calls(_function(MENU_MODULE, "native_usage_menu_item"))

    assert "settings_snapshot" in ready_calls
    assert "refresh_cached_merged_sync" in ready_calls
    assert "ProviderUsageApply" in ready_calls
    refresh = next(
        node
        for node in ast.walk(ready)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "refresh_cached_merged_sync"
    )
    dispatch = next(
        node
        for node in ast.walk(ready)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr
        == "performSelectorOnMainThread_withObject_waitUntilDone_"
    )
    assert refresh.lineno < dispatch.lineno
    assert "load_provider_usage_settings" not in apply_calls
    assert "load_provider_usage_settings" not in menu_settings_calls
    assert "_usage_menu_settings" in native_menu_calls
    assert "load_provider_usage_settings" not in native_menu_calls
    menu_source = MENU_MODULE.read_text(encoding="utf-8")
    assert "ProviderInstancePolicyProjection" in menu_source
    assert "visual=visual" in menu_source


def test_usage_summary_and_checkbox_repaint_do_not_reload_settings():
    summary_calls = _calls(
        _function(SETTINGS_CATEGORY_MODULE, "refresh_native_usage_summary")
    )
    checkbox_calls = _calls(
        _function(SETTINGS_CATEGORY_MODULE, "_sync_usage_menu_checkboxes")
    )

    assert "_sync_usage_menu_checkboxes" in summary_calls
    assert "load_provider_usage_settings" not in summary_calls
    assert "load_provider_usage_settings" not in checkbox_calls


def test_local_usage_settings_mutations_refresh_all_cached_projections():
    assert "apply_provider_usage_settings_snapshot" in _calls(
        _method("toggleUsageMenuElement_")
    )
    provider_calls = _calls(_method("toggleUsageMenuProvider_"))
    assert "toggle_provider_menu_visibility" in provider_calls
    assert "load_provider_usage_settings" not in provider_calls


def test_profile_settings_selector_delegates_save_and_ui_freshness_as_one_action():
    calls = _calls(_method("updateProviderInstanceProfile_"))

    assert "save_provider_instance_profile_setting" in calls
    assert "update_provider_instance_profile" not in calls


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


def test_session_opening_consults_exact_instance_policy_before_legacy_router():
    calls = _calls(_method("open_session"))

    assert "profile_session_action" in calls
    assert "open_session" in calls


def test_why_panel_override_preserves_the_base_context_keyword_contract():
    method = _method("why_panel_body")
    kwonly = [argument.arg for argument in method.args.kwonlyargs]
    assert kwonly == ["why_context", "wall_clock"]

    base_call = next(
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "why_panel_body"
    )
    keywords = {keyword.arg for keyword in base_call.keywords}
    assert "why_context" in keywords


_STATUS_BAR_PROBE_PREAMBLE = """
from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from sidepulse.provider_usage_runtime import ProviderUsageState
from sidepulse.provider_usage_status_bar import JRProviderUsageStatusBarController

class FakeController:
    def __init__(self, state=None):
        self._sidepulse_provider_usage_state = state
        self.refreshes = []

    def _request_provider_usage(self, **kwargs):
        self.refreshes.append(kwargs)

def capacity_state():
    lane = UsageLane(
        provider_id="claude",
        lane_id="weekly",
        label="Weekly",
        remaining_percent=74,
        reset_at=1240,
        scope="all",
        model=None,
        feature=None,
        bindable=True,
        source_id="fixture",
    )
    snapshot = ProviderUsageSnapshot(
        provider_id="claude",
        account_label=None,
        observed_at=1000,
        state=ProviderSourceState.READY,
        reason_code=None,
        action_label=None,
        lanes=(lane,),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
    )
    return ProviderUsageState((snapshot,), 1000, None, False)
"""


def _run_status_bar_probe(body: str) -> None:
    """Exercise the production wrapper without leaking its import installers."""
    completed = subprocess.run(
        [sys.executable, "-c", _STATUS_BAR_PROBE_PREAMBLE + body],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_refresh_gate_is_fresh_before_two_minutes_and_due_at_two_minutes():
    _run_status_bar_probe(
        """
controller = FakeController()
clock = [1000.0]
JRProviderUsageStatusBarController.request_jr_usage_refresh(
    controller, ("claude",), monotonic=lambda: clock[0]
)
clock[0] = 1119.999
JRProviderUsageStatusBarController.request_jr_usage_refresh(
    controller, ("claude",), monotonic=lambda: clock[0]
)
assert len(controller.refreshes) == 1
clock[0] = 1120.0
JRProviderUsageStatusBarController.request_jr_usage_refresh(
    controller, ("claude",), monotonic=lambda: clock[0]
)
assert len(controller.refreshes) == 2
"""
    )


def test_capacity_projection_uses_injected_wall_clock():
    _run_status_bar_probe(
        """
controller = FakeController(capacity_state())
fresh = JRProviderUsageStatusBarController.jr_capacity_settings_text(
    controller, "claude", wall_clock=lambda: 1059.0
)
due = JRProviderUsageStatusBarController.jr_capacity_settings_text(
    controller, "claude", wall_clock=lambda: 1060.0
)
assert "just checked" in fresh
assert "checked 1m ago" in due
"""
    )
