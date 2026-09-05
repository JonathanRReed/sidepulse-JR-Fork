import plistlib
from pathlib import Path

import tomllib

from packaging.verify_entitlements import (
    FORBIDDEN_ENTITLEMENTS,
    REQUIRED_ENTITLEMENTS,
    source_entitlements,
    validate_entitlements,
)
from scripts.verify_dependency_policy import validate_dependency_policy

ROOT = Path(__file__).resolve().parents[1]


def test_reviewed_dependency_policy_is_exact() -> None:
    assert validate_dependency_policy(ROOT) == ()


def test_release_dependencies_are_hash_locked() -> None:
    lock = (ROOT / "requirements" / "release-lock.txt").read_text(encoding="utf-8")
    requirement_blocks = [block for block in lock.split("\n\n") if "==" in block]

    assert requirement_blocks
    assert all("--hash=sha256:" in block for block in requirement_blocks)
    package_build = (ROOT / "packaging" / "build_macos_pkg.sh").read_text(encoding="utf-8")
    assert 'LOCKFILE="$ROOT_DIR/requirements/release-lock.txt"' in package_build
    assert "--require-hashes" in package_build
    assert '"$ROOT_DIR" --no-deps --no-build-isolation' in package_build
    assert "--python-version 3.12" in lock
    assert "hidapi==0.14.0" in lock


def test_creator_micro_backend_is_in_the_signed_release() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    release_input = (ROOT / "requirements" / "release.in").read_text(encoding="utf-8")

    assert any(requirement.startswith("hidapi==0.14.0;") for requirement in document["project"]["dependencies"])
    assert "hidapi==0.14.0" in release_input.splitlines()


def test_no_isolation_build_backend_is_installed_by_the_dev_extra() -> None:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    build_requirements = {
        requirement.split("==", 1)[0]
        for requirement in document["build-system"]["requires"]
    }
    dev_requirements = {
        requirement.split("==", 1)[0]
        for requirement in document["project"]["optional-dependencies"]["dev"]
    }

    assert build_requirements <= dev_requirements


def test_source_entitlements_match_the_exact_reviewed_allowlist() -> None:
    source = source_entitlements(ROOT / "packaging" / "entitlements.plist")

    assert source == REQUIRED_ENTITLEMENTS
    assert validate_entitlements(source) == ()
    assert not (FORBIDDEN_ENTITLEMENTS & source.keys())


def test_source_entitlements_do_not_allow_dynamic_executable_memory() -> None:
    source = source_entitlements(ROOT / "packaging" / "entitlements.plist")

    assert "com.apple.security.cs.allow-jit" not in source
    assert "com.apple.security.cs.allow-unsigned-executable-memory" not in source


def test_entitlement_validator_rejects_any_unreviewed_capability() -> None:
    expanded = {
        **REQUIRED_ENTITLEMENTS,
        "com.apple.security.cs.disable-library-validation": True,
    }

    failures = validate_entitlements(expanded)

    assert any("forbidden entitlement" in failure for failure in failures)
    assert any("unreviewed entitlement" in failure for failure in failures)


def test_entitlements_plist_is_a_dictionary() -> None:
    value = plistlib.loads(
        (ROOT / "packaging" / "entitlements.plist").read_bytes()
    )
    assert isinstance(value, dict)
