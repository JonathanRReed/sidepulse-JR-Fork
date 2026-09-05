from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
_ACTION_USE = re.compile(r"uses:\s+[^@\s]+@([^\s#]+)")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}\Z")


def test_tests_run_for_pull_requests_and_pushes_on_hosted_macos() -> None:
    text = (WORKFLOWS / "tests.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "\n  push:" in text
    assert "\n  pull_request:" in text
    assert "security:" in text
    security = text.split("  security:", 1)[1].split("\n  macos:", 1)[0]
    assert "runs-on: macos-latest" in security
    assert "self-hosted" not in security


def test_publish_workflow_remains_manual_only() -> None:
    text = (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "\n  push:" not in text
    assert "\n  pull_request:" not in text


def test_fork_workflow_does_not_publish_upstream_pypi_name() -> None:
    text = (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")

    assert "gh-action-pypi-publish" not in text
    assert "upload-artifact" in text
    assert "scripts/validate_release_version.py" in text


def test_every_third_party_action_is_pinned_to_an_immutable_commit() -> None:
    for workflow in WORKFLOWS.glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        refs = _ACTION_USE.findall(text)
        assert refs, f"{workflow.name} declares no action steps"
        assert all(_COMMIT_SHA.fullmatch(ref) for ref in refs), (
            f"{workflow.name} contains a floating action reference: {refs}"
        )


def test_self_hosted_gate_uses_the_reviewed_production_runner() -> None:
    text = (WORKFLOWS / "self-hosted-macos.yml").read_text(encoding="utf-8")

    assert "self-hosted" in text
    assert "sidepulse-production" in text
    assert "verify_macos_release.sh" in text
    assert "environment:" in text


def test_ordinary_tests_never_run_repository_code_on_production_runner() -> None:
    text = (WORKFLOWS / "self-hosted-macos.yml").read_text(encoding="utf-8")

    assert "tests:" in text
    assert "runs-on: macos-" in text
    assert "release:" in text
    release = text.split("  release:", 1)[1]
    assert "runs-on: [self-hosted, macOS, ARM64, sidepulse-production]" in release
    assert "environment: sidepulse-production" in release


def test_release_ref_is_approved_before_checkout_or_repository_scripts() -> None:
    text = (WORKFLOWS / "self-hosted-macos.yml").read_text(encoding="utf-8")
    release = text.split("  release:", 1)[1]

    guard = release.index("github.ref_type == 'tag'")
    checkout = release.index("actions/checkout@")
    script = release.index("./scripts/")
    assert guard < checkout < script
    assert "github.ref_protected" in release
    assert "github.sha == inputs.release_commit" in release
    assert "ref: ${{ inputs.release_commit }}" in release
    assert "SIDEPULSE_RUN_UNINSTALL: \"1\"" in release
    assert "SPARKLE_KEY_ACCOUNT:" in release
