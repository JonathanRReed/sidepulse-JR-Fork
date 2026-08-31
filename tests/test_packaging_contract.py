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


def _relative_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.level != 1:
            continue
        if node.module is not None:
            imported.add(node.module.split(".", 1)[0])
        else:
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
    return imported


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
        if module_name == "sidepulse.status_bar":
            # The final facade delegates through _status_bar_production, which
            # in turn owns the direct status_bar_legacy import. Validate the
            # complete chain instead of requiring the historical implementation
            # to be imported directly from the public entrypoint.
            public_imports = _relative_imports(modules[module_name])
            production_module = modules.get("sidepulse._status_bar_production")
            assert production_module is not None
            production_imports = _relative_imports(production_module)
            assert "_status_bar_production" in public_imports, (
                f"{script}: public status-bar facade is not delegated"
            )
            assert "status_bar_legacy" in production_imports, (
                f"{script}: retained status-bar runtime is not delegated"
            )
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


def test_package_builder_declares_the_reviewed_focus_status_usage() -> None:
    source = (REPO_ROOT / "packaging" / "build_macos_pkg.sh").read_text(
        encoding="utf-8"
    )

    description = (
        "JR Bar uses Focus Status only when you choose Allow Focus Status, "
        "so Do Not Disturb can follow whether a macOS Focus is active."
    )
    assert f'FOCUS_STATUS_USAGE_DESCRIPTION="{description}"' in source
    assert (
        'Add :NSFocusStatusUsageDescription string $FOCUS_STATUS_USAGE_DESCRIPTION'
        in source
    )
    assert (
        'Set :NSFocusStatusUsageDescription $FOCUS_STATUS_USAGE_DESCRIPTION'
        in source
    )
