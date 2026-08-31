from __future__ import annotations

import ast
from pathlib import Path

import pytest

TEST_ROOT = Path(__file__).resolve().parent


def _modules(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    modules: dict[str, str] = {}
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return modules, names


def _test_trees():
    for path in sorted(TEST_ROOT.glob("test_*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _call(source: str) -> ast.Call:
    expression = ast.parse(source).body[0]
    assert isinstance(expression, ast.Expr)
    assert isinstance(expression.value, ast.Call)
    return expression.value


def _is_unbounded_join(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "join":
        return False
    timeout = next(
        (keyword for keyword in node.keywords if keyword.arg == "timeout"),
        None,
    )
    if timeout is not None:
        return isinstance(timeout.value, ast.Constant) and timeout.value.value is None
    return not node.args or (
        isinstance(node.args[0], ast.Constant) and node.args[0].value is None
    )


@pytest.mark.parametrize(
    "source",
    ("worker.join()", "worker.join(None)", "worker.join(timeout=None)"),
)
def test_join_detector_rejects_every_unbounded_form(source: str) -> None:
    assert _is_unbounded_join(_call(source)) is True


@pytest.mark.parametrize(
    "source",
    ("worker.join(0.1)", "worker.join(timeout=0.1)"),
)
def test_join_detector_accepts_explicitly_bounded_forms(source: str) -> None:
    assert _is_unbounded_join(_call(source)) is False


def test_tests_do_not_sleep_or_join_without_a_bound() -> None:
    violations: list[str] = []
    for path, tree in _test_trees():
        source = path.read_text(encoding="utf-8")
        javascript_timeout_marker = "set" + "Timeout("
        if javascript_timeout_marker in source:
            violations.append(f"{path.name}: embedded JavaScript timeout")
        modules, names = _modules(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and modules.get(node.func.value.id) == "time"
                and node.func.attr == "sleep"
            ) or (
                isinstance(node.func, ast.Name)
                and names.get(node.func.id) == "time.sleep"
            ):
                violations.append(f"{path.name}:{node.lineno}: wall-clock sleep")
            if _is_unbounded_join(node):
                violations.append(f"{path.name}:{node.lineno}: unbounded join")
    assert violations == []


def test_test_random_generators_always_have_an_explicit_seed() -> None:
    violations: list[str] = []
    for path, tree in _test_trees():
        modules, names = _modules(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            is_random_constructor = (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and modules.get(node.func.value.id) == "random"
                and node.func.attr == "Random"
            ) or (
                isinstance(node.func, ast.Name)
                and names.get(node.func.id) == "random.Random"
            )
            if is_random_constructor and not node.args and not node.keywords:
                violations.append(f"{path.name}:{node.lineno}: unseeded Random")
    assert violations == []
