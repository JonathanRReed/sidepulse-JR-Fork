from pathlib import Path

from scripts.verify_performance_budget import validate_performance_evidence

ROOT = Path(__file__).resolve().parents[1]


def test_authoritative_release_gate_requires_every_external_evidence_class() -> None:
    text = (ROOT / "scripts" / "verify_macos_release.sh").read_text()
    for required in (
        "verify.sh --no-bootstrap",
        "verify_performance_budget.py",
        "pkgutil --check-signature",
        "spctl -a -vv -t install",
        "stapler validate",
        "verify_hardware_release.py",
        "verify_installed_upgrade.py",
        "git rev-parse origin/main",
    ):
        assert required in text


def test_release_publication_is_draft_first_and_rolls_back_on_failure() -> None:
    text = (ROOT / "scripts" / "publish_release.sh").read_text()
    assert "git status --porcelain --untracked-files=all" in text
    assert "--draft" in text
    assert "release upload" in text
    assert "release edit" in text
    assert "--cleanup-tag" in text


def test_performance_evidence_requires_measured_budgets_and_trace_review() -> None:
    evidence = {
        "warm_launch_ms": 450,
        "menu_open_p95_ms": 40,
        "pane_switch_p95_ms": 80,
        "longest_main_thread_task_ms": 12,
        "idle_cpu_hidden_percent": 0.5,
        "idle_cpu_static_bar_percent": 1.0,
        "idle_cpu_motion_percent": 2.5,
        "measurement_duration_seconds": 300,
        "instruments_trace_reviewed": True,
        "menu_tracking_io_observed": False,
    }
    assert validate_performance_evidence(evidence) == ()

    evidence["menu_open_p95_ms"] = 75
    assert any(
        failure.startswith("menu_open_p95_ms")
        for failure in validate_performance_evidence(evidence)
    )
