"""Contracts for the compatibility facade around the AppKit runtime."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FACADE = ROOT / "src" / "sidepulse" / "status_bar.py"


def _tree() -> ast.Module:
    return ast.parse(FACADE.read_text(encoding="utf-8"), filename=str(FACADE))


def test_direct_module_execution_delegates_to_runtime_main() -> None:
    guards = [
        node
        for node in _tree().body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    ]
    assert guards, "status-bar facade lost its python -m entrypoint guard"
    calls = [
        node
        for guard in guards
        for node in ast.walk(guard)
        if isinstance(node, ast.Call)
    ]
    assert any(
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "_legacy"
        and call.func.attr == "main"
        for call in calls
    ), "status-bar facade does not delegate direct execution to runtime main"


def test_facade_forwards_assignment_and_deletion() -> None:
    class_definition = next(
        node
        for node in _tree().body
        if isinstance(node, ast.ClassDef) and node.name == "_StatusBarFacade"
    )
    method_names = {
        node.name
        for node in class_definition.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert {"__getattr__", "__setattr__", "__delattr__", "__dir__"} <= method_names


def test_source_introspection_points_at_the_retained_runtime() -> None:
    source = FACADE.read_text(encoding="utf-8")

    assert "_facade_module.__file__ = _legacy.__file__" in source
