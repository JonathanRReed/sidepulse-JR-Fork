"""Fail-closed contract for the Settings window's dependency graph.

The Settings window must declare every dependency through Python imports.
Ambient ``globals()`` copying makes the import graph invisible to static
analysis and lets Settings panes fail only when a user opens them.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "sidepulse"


def _settings_window_tree() -> ast.Module:
    return ast.parse((SRC / "settings_window.py").read_text())


def _ambient_names() -> set[str]:
    tree = _settings_window_tree()
    defined = set(dir(builtins)) | {"__name__", "__file__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for name_node in ast.walk(target):
                    if isinstance(name_node, ast.Name):
                        defined.add(name_node.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(node.target, ast.Name):
                defined.add(node.target.id)

    loads: set[str] = set()

    def visit_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        local = {
            arg.arg
            for arg in (
                node.args.args + node.args.kwonlyargs + node.args.posonlyargs
            )
        }
        if node.args.vararg:
            local.add(node.args.vararg.arg)
        if node.args.kwarg:
            local.add(node.args.kwarg.arg)
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Store):
                local.add(inner.id)
            elif isinstance(
                inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                local.add(inner.name)
            elif isinstance(inner, ast.ExceptHandler) and inner.name:
                local.add(inner.name)
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Name)
                and isinstance(inner.ctx, ast.Load)
                and inner.id not in local
            ):
                loads.add(inner.id)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            visit_function(node)

    return {name for name in loads if name not in defined}


def test_settings_window_has_no_namespace_injection() -> None:
    tree = _settings_window_tree()
    functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_install" not in functions
    global_namespace_reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "globals"
    ]
    assert global_namespace_reads == []
    assert _ambient_names() == set()


def test_settings_window_declares_extracted_settings_pane_dependencies() -> None:
    imports = {
        node.module
        for node in ast.walk(_settings_window_tree())
        if isinstance(node, ast.ImportFrom)
    }

    assert "global_action_settings_pane" in imports
    assert "dnd_settings_pane" in imports
