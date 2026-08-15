from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


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
