from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sidepulse.integration_compatibility import (
    load_integration_compatibility_manifest,
)
from sidepulse.provider_fixture_ownership import (
    load_provider_fixture_ownership_manifest,
    validate_provider_fixture_ownership,
)
from sidepulse.providers import PROVIDER_REGISTRY

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "providers"


def test_manifest_has_one_synthetic_owned_fixture_for_every_registered_provider() -> None:
    manifest = load_provider_fixture_ownership_manifest()

    assert manifest.cross_provider_identifiers == ()
    assert {entry.provider for entry in manifest.entries} == set(PROVIDER_REGISTRY)
    assert len(manifest.entries) == len(PROVIDER_REGISTRY)
    assert all(entry.synthetic for entry in manifest.entries)
    assert all(entry.reviewed_on == "2026-08-29" for entry in manifest.entries)


def test_fixture_ownership_validator_checks_fixture_hash_and_exact_owner() -> None:
    manifest = validate_provider_fixture_ownership(FIXTURE_ROOT)

    assert manifest.schema_version == 1
    assert all(entry.sha256.startswith("sha256:") for entry in manifest.entries)
    assert all(entry.fixture_version == 1 for entry in manifest.entries)


def test_clean_install_probe_requires_the_packaged_ownership_manifest() -> None:
    clean_install = Path(__file__).parents[1] / "scripts" / "verify_clean_install.py"

    assert '"provider_fixture_ownership.json"' in clean_install.read_text(encoding="utf-8")


def test_fixture_ownership_validator_rejects_owner_mismatch(tmp_path: Path) -> None:
    copied = tmp_path / "providers"
    _copy_fixture_tree(copied)
    target = copied / "codex" / "codex-session-start.json"
    document = json.loads(target.read_text())
    document["provider"] = "claude"
    target.write_text(json.dumps(document, indent=2) + "\n")

    with pytest.raises(ValueError, match="provider ownership"):
        validate_provider_fixture_ownership(copied)


@pytest.mark.parametrize(
    "bad_value",
    [
        "/Users/example/private.jsonl",
        "person@example.invalid",
        "Bearer synthetic-secret",
        "user prompt text",
        "transcript content",
    ],
)
def test_fixture_ownership_validator_rejects_sensitive_or_content_like_payload(
    tmp_path: Path, bad_value: str
) -> None:
    copied = tmp_path / "providers"
    _copy_fixture_tree(copied)
    target = copied / "codex" / "codex-session-start.json"
    document = json.loads(target.read_text())
    document["payload"]["synthetic_value"] = bad_value
    target.write_text(json.dumps(document, indent=2) + "\n")

    with pytest.raises(ValueError, match="fixture content"):
        validate_provider_fixture_ownership(copied)


def test_fixture_ownership_allowlist_is_explicit_and_can_name_cross_provider_ids(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "providers"
    _copy_fixture_tree(copied)
    target = copied / "codex" / "codex-session-start.json"
    document = json.loads(target.read_text())
    document["payload"]["related_provider"] = "claude"
    target.write_text(json.dumps(document, indent=2) + "\n")

    with pytest.raises(ValueError, match="cross-provider"):
        validate_provider_fixture_ownership(copied)

    manifest_path = Path(__file__).parents[1] / "src" / "sidepulse" / "resources" / "provider_fixture_ownership.json"
    manifest_document = json.loads(manifest_path.read_text())
    manifest_document["crossProviderIdentifiers"] = ["claude"]
    next(row for row in manifest_document["fixtures"] if row["provider"] == "codex")[
        "sha256"
    ] = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    # The production manifest is immutable in this test. The module-level
    # document parser exercises the explicit allowlist without editing it.
    from sidepulse.provider_fixture_ownership import validate_provider_fixture_document

    validated = validate_provider_fixture_document(manifest_document, copied)
    assert "claude" in validated.cross_provider_identifiers


def test_t3_manifest_requires_a_valid_reviewed_on_date() -> None:
    t3code = load_integration_compatibility_manifest().entry("t3code")

    assert t3code is not None
    assert t3code.reviewed_on == "2026-08-29"


def _copy_fixture_tree(destination: Path) -> None:
    for source in FIXTURE_ROOT.rglob("*"):
        relative = source.relative_to(FIXTURE_ROOT)
        target = destination / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
