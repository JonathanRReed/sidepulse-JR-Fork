from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "sidepulse"
APPLICATION_COMPOSITION = SRC / "application_composition.py"
STATUS_BAR = SRC / "status_bar.py"
PRODUCTION_STATUS_BAR = SRC / "_status_bar_production.py"
PROVIDER_USAGE_STATUS_BAR = SRC / "provider_usage_status_bar.py"
STATUS_BAR_LEGACY = SRC / "status_bar_legacy.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _top_level_calls(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for candidate in ast.walk(node):
            if isinstance(candidate, ast.Call):
                calls.append(candidate)
    return calls


def _top_level_assignments(tree: ast.Module) -> list[ast.AST]:
    assignments: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            assignments.append(node)
    return assignments


def _call_name(call: ast.AST) -> str | None:
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function: {name}")


def _assigned_attribute_targets(tree: ast.Module) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    for node in _top_level_assignments(tree):
        nodes: list[ast.AST]
        if isinstance(node, ast.Assign):
            nodes = list(node.targets)
        else:
            nodes = [node.target]
        for target in nodes:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
            ):
                targets.append((target.value.id, target.attr))
    return targets


def test_application_composition_module_is_pure_at_import_time() -> None:
    source = APPLICATION_COMPOSITION.read_text(encoding="utf-8")
    tree = _tree(APPLICATION_COMPOSITION)

    assert "import AppKit" not in source
    assert "from AppKit" not in source
    assert "import objc" not in source
    assert "from Foundation" not in source
    assert _top_level_calls(tree) == []


def test_application_composition_declares_a_receipt_contract() -> None:
    tree = _tree(APPLICATION_COMPOSITION)
    compose = _function(tree, "compose_status_bar_application")

    call_names = [
        name
        for name in (_call_name(node) for node in ast.walk(compose))
        if name is not None
    ]
    assert "install_settings_navigation" in call_names
    assert "install_screen_bar_runtime" in call_names
    assert call_names.index("install_settings_navigation") < call_names.index(
        "install_screen_bar_runtime"
    )

    receipt_keywords: set[str] = set()
    for node in ast.walk(compose):
        if not isinstance(node, ast.Call) or _call_name(node) != "ApplicationCompositionReceipt":
            continue
        receipt_keywords.update(
            keyword.arg for keyword in node.keywords if keyword.arg is not None
        )

    assert {"controller", "final_controller", "menu_binding"} <= receipt_keywords


def test_status_bar_modules_stop_bootstrapping_on_import() -> None:
    for path in (
        STATUS_BAR,
        PRODUCTION_STATUS_BAR,
        PROVIDER_USAGE_STATUS_BAR,
        STATUS_BAR_LEGACY,
    ):
        source = path.read_text(encoding="utf-8")
        tree = _tree(path)

        assert "install_screen_bar_runtime()" not in source, f"{path.name} still starts Screen Bar at import time"
        assert "_install(dict(globals()))" not in source, f"{path.name} still performs namespace injection at import time"
        assert "install_settings_navigation(_legacy, _settings_window)" not in source, f"{path.name} still installs settings navigation at import time"

        top_level_calls = {_call_name(call) for call in _top_level_calls(tree)}
        assert "DeviceIdentityCache" not in top_level_calls, f"{path.name} still creates the device identity cache at import time"
        assert "request_refresh" not in top_level_calls, f"{path.name} still starts refresh work at import time"

        assigned_targets = _assigned_attribute_targets(tree)
        assert not any(owner == "_legacy" for owner, _attr in assigned_targets), (
            f"{path.name} still mutates the retained runtime module at import time"
        )


def test_foreground_entrypoints_reach_one_composition_boundary() -> None:
    legacy_main = _function(_tree(STATUS_BAR_LEGACY), "main")
    provider_main = _function(_tree(PROVIDER_USAGE_STATUS_BAR), "main")
    legacy_calls = [
        _call_name(node) for node in ast.walk(legacy_main) if isinstance(node, ast.Call)
    ]
    provider_calls = [
        _call_name(node)
        for node in ast.walk(provider_main)
        if isinstance(node, ast.Call)
    ]

    assert legacy_calls.count("compose_status_bar_application") == 1
    assert legacy_calls.count("run_status_bar") == 1
    assert legacy_calls.index("compose_status_bar_application") < legacy_calls.index(
        "run_status_bar"
    )
    assert "compose_status_bar_application" not in provider_calls
    assert provider_calls == ["main"]


def test_status_bar_composition_is_pure_on_import_and_idempotent_at_boot() -> None:
    script = """
import json
import threading

thread_starts = []
original_start = threading.Thread.start


def guarded_start(self, *args, **kwargs):
    thread_starts.append(self.name)
    return original_start(self, *args, **kwargs)


threading.Thread.start = guarded_start

from sidepulse import status_bar_legacy as legacy

controller_before = legacy.StatusBarController
menu_before = legacy.build_menu

from sidepulse import _status_bar_production as production
from sidepulse import provider_usage_status_bar as provider
from sidepulse import status_bar as public_status_bar

assert legacy.StatusBarController is controller_before
assert legacy.build_menu is menu_before
assert not thread_starts

from sidepulse.application_composition import compose_status_bar_application

receipt = compose_status_bar_application()
second = compose_status_bar_application()

assert receipt is second
assert not thread_starts
assert receipt.controller is production.JRStatusBarController
assert receipt.final_controller is provider.JRProviderUsageStatusBarController
assert receipt.menu_binding is provider.build_menu
assert receipt.steps == (
    "production-controller",
    "status-bar-facade",
    "settings-navigation",
    "screen-bar-runtime",
    "ambient-effects-runtime",
    "provider-usage-controller",
)
assert legacy.StatusBarController is provider.JRProviderUsageStatusBarController
assert legacy.build_menu is provider.build_menu
assert public_status_bar.StatusBarController is production.JRStatusBarController
print(json.dumps({"ok": True}))
"""
    with tempfile.TemporaryDirectory() as tempdir:
        env = os.environ.copy()
        env["SIDEPULSE_TESTING"] = "1"
        env["HOME"] = tempdir
        env["PYTHONPATH"] = str(ROOT / "src")
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr


def test_provider_foreground_main_composes_once_before_appkit() -> None:
    script = """
from sidepulse import application_composition
from sidepulse import provider_usage_status_bar as provider
from sidepulse import status_bar_legacy as legacy

calls = []
application_composition.compose_status_bar_application = (
    lambda: calls.append("compose")
)
legacy.another_instance_alive = lambda: False
legacy.run_status_bar = lambda: calls.append("run")

assert provider.main() == 0
assert calls == ["compose", "run"], calls
"""
    with tempfile.TemporaryDirectory() as tempdir:
        env = os.environ.copy()
        env["SIDEPULSE_TESTING"] = "1"
        env["HOME"] = tempdir
        env["PYTHONPATH"] = str(ROOT / "src")
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr
