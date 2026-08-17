from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS_BAR = ROOT / "src" / "sidepulse" / "status_bar.py"
BACKGROUND_MODULES = (
    "_integration_settings_legacy.py",
    "integration_compatibility.py",
    "integration_settings.py",
    "t3_compat.py",
)


def _tree() -> ast.Module:
    return ast.parse(STATUS_BAR.read_text(encoding="utf-8"))


def _method(name: str) -> ast.FunctionDef:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing integration method: {name}")


def _calls(node: ast.AST) -> tuple[str, ...]:
    names = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        function = call.func
        if isinstance(function, ast.Name):
            names.append(function.id)
            continue
        if isinstance(function, ast.Attribute):
            parts = [function.attr]
            owner = function.value
            while isinstance(owner, ast.Attribute):
                parts.append(owner.attr)
                owner = owner.value
            if isinstance(owner, ast.Name):
                parts.append(owner.id)
            names.append(".".join(reversed(parts)))
    return tuple(names)


def test_t3_configuration_loads_once_and_refresh_only_schedules_worker() -> None:
    launch = _calls(_method("applicationDidFinishLaunching_"))
    refresh = _calls(_method("refresh_"))

    assert "load_integration_settings" in launch
    assert "_ProductionStatusBarController.applicationDidFinishLaunching_" in launch
    assert "self._request_t3_integration" in refresh
    assert "_ProductionStatusBarController.refresh_" in refresh
    assert "load_integration_settings" not in refresh
    assert "subprocess.run" not in refresh
    assert "sqlite3.connect" not in refresh


def test_t3_results_reach_the_canonical_monitor() -> None:
    calls = _calls(_method("applyT3Observation_"))

    assert "monitor.replace_external_statuses" in calls
    assert "monitor.current_statuses_by_key" in calls
    assert "self.schedule_event_refresh" in calls


def test_status_bar_has_no_codexbar_runtime_path() -> None:
    source = STATUS_BAR.read_text(encoding="utf-8")

    assert "CodexBar" not in source
    assert "codexbar_compat" not in source
    assert "_sidepulse_codexbar" not in source


def test_integration_workers_are_appkit_free() -> None:
    for name in BACKGROUND_MODULES:
        source = (ROOT / "src" / "sidepulse" / name).read_text(encoding="utf-8")
        assert "import AppKit" not in source
        assert "from AppKit" not in source
        assert "import Foundation" not in source
        assert "from Foundation" not in source
        assert "import objc" not in source
