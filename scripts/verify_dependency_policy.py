#!/usr/bin/env python3
"""Verify SidePulse's reviewed direct-dependency and build-tool policy."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 only
    import tomli as tomllib

_EXACT_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*\[[A-Za-z0-9_,.-]+\]==[^;\s]+(?:\s*;.+)?$"
    r"|^[A-Za-z0-9][A-Za-z0-9_.-]*==[^;\s]+(?:\s*;.+)?$"
)
_CONSTRAINT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*==[^;\s]+(?:\s*;.+)?$"
)
_HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}(?:\s*\\)?$")
_REQUIRED_CONSTRAINTS = frozenset(
    {
        "pip",
        "setuptools",
        "wheel",
        "pyinstaller",
        "pyinstaller-hooks-contrib",
        "packaging",
        "altgraph",
        "macholib",
        "pyobjc-core",
        "pyobjc-framework-cocoa",
        "pyobjc-framework-quartz",
        "pyobjc-framework-webkit",
        "pyobjc-framework-eventkit",
        "ruamel-yaml",
        "pytest",
        "ruff",
        "build",
        "twine",
    }
)


def _name(requirement: str) -> str:
    head = requirement.split(";", 1)[0].split("==", 1)[0].strip()
    head = head.split("[", 1)[0]
    return re.sub(r"[-_.]+", "-", head).casefold()


def _requirements(document: dict) -> tuple[str, ...]:
    build = document.get("build-system") or {}
    project = document.get("project") or {}
    optional = project.get("optional-dependencies") or {}
    values = [
        *(build.get("requires") or ()),
        *(project.get("dependencies") or ()),
    ]
    for group in optional.values():
        values.extend(group or ())
    return tuple(str(value) for value in values)


def validate_dependency_policy(root: Path) -> tuple[str, ...]:
    failures = []
    pyproject_path = root / "pyproject.toml"
    constraints_path = root / "requirements" / "release-constraints.txt"
    lock_path = root / "requirements" / "release-lock.txt"
    document = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = document.get("project") or {}
    optional = project.get("optional-dependencies") or {}
    requirements = _requirements(document)
    for requirement in requirements:
        if not _EXACT_REQUIREMENT.fullmatch(requirement):
            failures.append(f"non-exact direct requirement: {requirement}")

    constraints = []
    for raw in constraints_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not _CONSTRAINT.fullmatch(line):
            failures.append(f"non-exact release constraint: {line}")
            continue
        constraints.append(line)
    constrained_names = {_name(line) for line in constraints}
    missing = sorted(_REQUIRED_CONSTRAINTS - constrained_names)
    if missing:
        failures.append(f"missing release constraints: {', '.join(missing)}")

    lock_lines = lock_path.read_text(encoding="utf-8").splitlines()
    locked_names: set[str] = set()
    current_requirement: str | None = None
    current_has_hash = False
    for raw in lock_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not raw.startswith((" ", "\t")):
            if current_requirement is not None and not current_has_hash:
                failures.append(f"release lock entry has no SHA-256 hash: {current_requirement}")
            current_requirement = line.removesuffix("\\").strip()
            current_has_hash = "--hash=sha256:" in current_requirement
            if "==" not in current_requirement:
                failures.append(f"non-exact release lock entry: {current_requirement}")
            else:
                locked_names.add(_name(current_requirement))
        elif _HASH.fullmatch(line):
            current_has_hash = True
    if current_requirement is not None and not current_has_hash:
        failures.append(f"release lock entry has no SHA-256 hash: {current_requirement}")
    release_inputs = []
    for raw in (root / "requirements" / "release.in").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            release_inputs.append(line)
    release_input_names = {_name(line) for line in release_inputs}
    missing_locked = sorted(release_input_names - locked_names)
    if missing_locked:
        failures.append(f"release constraints absent from hash lock: {', '.join(missing_locked)}")

    for requirement in requirements:
        name = _name(requirement)
        if name not in constrained_names and name not in {"tomli"}:
            failures.append(f"direct requirement is absent from constraints: {name}")

    build_requirements = {
        _name(str(requirement))
        for requirement in (document.get("build-system") or {}).get("requires", ())
    }
    dev_requirements = {
        _name(str(requirement)) for requirement in optional.get("dev", ())
    }
    missing_no_isolation = sorted(build_requirements - dev_requirements)
    if missing_no_isolation:
        failures.append(
            "build requirements missing from dev extra for --no-isolation: "
            + ", ".join(missing_no_isolation)
        )

    bootstrap = (root / "scripts" / "bootstrap-dev.sh").read_text(encoding="utf-8")
    package_build = (root / "packaging" / "build_macos_pkg.sh").read_text(
        encoding="utf-8"
    )
    for path, text in (
        ("scripts/bootstrap-dev.sh", bootstrap),
        ("packaging/build_macos_pkg.sh", package_build),
    ):
        if "release-constraints.txt" not in text:
            failures.append(f"{path} does not use release constraints")
        if 'pip==26.1.2' not in text and 'PINNED_PIP="26.1.2"' not in text:
            failures.append(f"{path} does not pin pip")
    if 'PINNED_PYINSTALLER="6.21.0"' not in package_build:
        failures.append("packaging/build_macos_pkg.sh does not pin PyInstaller")
    if "release-environment.txt" not in package_build:
        failures.append("packaging/build_macos_pkg.sh does not snapshot its environment")
    if "release-lock.txt" not in package_build or "--require-hashes" not in package_build:
        failures.append("packaging/build_macos_pkg.sh does not enforce the hash-bound release lock")
    return tuple(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        failures = validate_dependency_policy(args.root.resolve())
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"dependency policy could not be evaluated: {exc}")
        return 2
    if failures:
        print("dependency policy failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("dependency policy passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
