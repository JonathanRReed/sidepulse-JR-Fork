"""Packaging contracts that run without importing the macOS UI."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
PYPROJECT = REPO_ROOT / "pyproject.toml"

FRAMEWORK_REQUIREMENTS = {
    "AppKit": "pyobjc-framework-cocoa",
    "Foundation": "pyobjc-framework-cocoa",
    "Quartz": "pyobjc-framework-quartz",
    "WebKit": "pyobjc-framework-webkit",
    "ScriptingBridge": "pyobjc-framework-scriptingbridge",
    "EventKit": "pyobjc-framework-eventkit",
}


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _normalized_requirement(spec: str) -> str:
    head = spec.split(";", 1)[0].strip()
    name = re.split(r"[<>=!~\[\s]", head, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def _source_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(SRC_ROOT)
        parts = list(relative.parts)
        parts[-1] = relative.stem
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            modules[".".join(parts)] = path
    return modules


def test_source_tree_is_parseable() -> None:
    for path in sorted(SRC_ROOT.rglob("*.py")):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_pyobjc_framework_imports_are_declared() -> None:
    declared = {
        _normalized_requirement(spec)
        for spec in _pyproject()["project"].get("dependencies", ())
    }
    imported_frameworks: set[str] = set()
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_frameworks.update(
                    alias.name.split(".", 1)[0]
                    for alias in node.names
                    if alias.name.split(".", 1)[0] in FRAMEWORK_REQUIREMENTS
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".", 1)[0]
                if top in FRAMEWORK_REQUIREMENTS:
                    imported_frameworks.add(top)

    missing = {
        framework: FRAMEWORK_REQUIREMENTS[framework]
        for framework in imported_frameworks
        if FRAMEWORK_REQUIREMENTS[framework] not in declared
    }
    assert not missing, f"undeclared PyObjC frameworks: {missing}"


def test_console_script_targets_exist() -> None:
    modules = _source_modules()
    scripts = _pyproject()["project"].get("scripts", {})
    assert scripts
    for script, target in scripts.items():
        module_name, separator, attribute = target.partition(":")
        assert separator and module_name in modules, f"{script}: missing module {module_name}"
        tree = ast.parse(
            modules[module_name].read_text(encoding="utf-8"),
            filename=str(modules[module_name]),
        )
        functions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        # status_bar is a facade whose callable is delegated to the retained
        # runtime module; the target is verified by its explicit import.
        if module_name == "sidepulse.status_bar":
            imported_runtime = any(
                isinstance(node, ast.ImportFrom)
                and node.level == 1
                and node.module is None
                and any(alias.name == "status_bar_legacy" for alias in node.names)
                for node in tree.body
            )
            assert imported_runtime, f"{script}: status-bar runtime is not delegated"
        else:
            assert attribute in functions, f"{script}: missing callable {target}"


def test_version_declarations_agree() -> None:
    declared = _pyproject()["project"]["version"]
    init_path = SRC_ROOT / "sidepulse" / "__init__.py"
    tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    version = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            version = node.value.value
            break
    assert version == declared, f"pyproject={declared!r}, sidepulse.__version__={version!r}"


def test_declared_package_data_exists() -> None:
    package_data = (
        _pyproject()
        .get("tool", {})
        .get("setuptools", {})
        .get("package-data", {})
    )
    for package, names in package_data.items():
        package_dir = SRC_ROOT.joinpath(*package.split("."))
        assert package_dir.is_dir(), f"missing package-data package: {package}"
        for name in names:
            assert (package_dir / name).is_file(), f"missing package data: {package}/{name}"
