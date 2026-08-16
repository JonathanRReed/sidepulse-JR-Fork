import plistlib
from pathlib import Path

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


def test_source_entitlements_match_the_exact_reviewed_allowlist() -> None:
    source = source_entitlements(ROOT / "packaging" / "entitlements.plist")

    assert source == REQUIRED_ENTITLEMENTS
    assert validate_entitlements(source) == ()
    assert not (FORBIDDEN_ENTITLEMENTS & source.keys())


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
