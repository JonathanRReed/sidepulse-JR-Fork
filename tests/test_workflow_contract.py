from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
_ACTION_USE = re.compile(r"uses:\s+[^@\s]+@([^\s#]+)")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}\Z")


def test_hosted_workflows_are_manual_only() -> None:
    for name in ("tests.yml", "publish.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")

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
