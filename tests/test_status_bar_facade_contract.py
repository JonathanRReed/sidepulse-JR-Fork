"""Contracts for the compatibility facades around the AppKit runtime."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FACADE = ROOT / "src" / "sidepulse" / "status_bar.py"
PRODUCTION_FACADE = ROOT / "src" / "sidepulse" / "_status_bar_production.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_direct_module_execution_delegates_to_runtime_main() -> None:
    guards = [
        node
        for node in _tree(FACADE).body
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


def test_controller_overrides_are_real_subclasses() -> None:
    production_tree = _tree(PRODUCTION_FACADE)
    production_controller = next(
        node
        for node in ast.walk(production_tree)
        if isinstance(node, ast.ClassDef) and node.name == "JRStatusBarController"
    )
    method_names = {
        node.name
        for node in production_controller.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {
        "projected_rows_for_device",
        "projection_for_device",
    } <= method_names

    final_tree = _tree(FACADE)
    final_controller = next(
        node
        for node in ast.walk(final_tree)
        if isinstance(node, ast.ClassDef)
        and node.name == "JRFinalStatusBarController"
    )
    assert any(
        isinstance(base, ast.Name)
        and base.id == "_ProductionStatusBarController"
        for base in final_controller.bases
    )

    forbidden_assignments = [
        node
        for tree in (production_tree, final_tree)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        and any(
            isinstance(candidate, ast.Attribute)
            and candidate.attr in {
                "projected_rows_for_device",
                "projection_for_device",
            }
            for candidate in ast.walk(node)
        )
    ]
    assert forbidden_assignments == [], "do not mutate Cocoa methods after class creation"


def test_facade_forwards_assignment_and_deletion() -> None:
    class_definition = next(
        node
        for node in _tree(FACADE).body
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
