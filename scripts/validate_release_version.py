#!/usr/bin/env python3
"""Validate that source, package metadata, changelog, and an optional tag agree."""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_INIT = ROOT / "src" / "sidepulse" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
_VERSION_PATTERN = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def pyproject_version() -> str:
    match = _VERSION_PATTERN.search(PYPROJECT.read_text(encoding="utf-8"))
    if match is None:
        raise RuntimeError("pyproject.toml has no project version")
    return match.group(1)


def package_version() -> str:
    module = ast.parse(PACKAGE_INIT.read_text(encoding="utf-8"), filename=str(PACKAGE_INIT))
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in statement.targets):
            value = ast.literal_eval(statement.value)
            if type(value) is str and value:
                return value
    raise RuntimeError("sidepulse.__version__ is missing or not a string literal")


def validate(tag: str | None = None) -> str:
    project = pyproject_version()
    package = package_version()
    if package != project:
        raise RuntimeError(f"version mismatch: pyproject={project!r}, package={package!r}")
    if tag is not None and tag != f"v{project}":
        raise RuntimeError(f"tag {tag!r} must equal v{project}")
    if not CHANGELOG.is_file() or f"## {project}\n" not in CHANGELOG.read_text(encoding="utf-8"):
        raise RuntimeError(f"CHANGELOG.md has no release section for {project}")
    return project


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Release tag, including the leading v.")
    args = parser.parse_args()
    print(validate(args.tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
