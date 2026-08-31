from pathlib import Path

from scripts.verify_performance_budget import validate_performance_evidence

ROOT = Path(__file__).resolve().parents[1]


def test_authoritative_release_gate_requires_every_external_evidence_class() -> None:
    text = (ROOT / "scripts" / "verify_macos_release.sh").read_text()
    receipt_runner = (ROOT / "scripts" / "release_evidence.py").read_text()
    for required in (
        "verify.sh --no-bootstrap",
        "verify_performance_budget.py",
        "pkgutil --check-signature",
        "spctl -a -vv -t install",
        "verify_hardware_release.py",
        "verify_installed_upgrade.py",
        "verify_uninstalled_candidate.py",
        "verify_clean_pkg_install.py",
        "release_evidence.py",
        "SIDEPULSE_RUN_UNINSTALL",
        "git rev-parse origin/main",
        '"$installed_binary" status-bar start',
    ):
        assert required in text or required in receipt_runner
    assert "stapling-receipt" in text
    assert '["/usr/bin/xcrun", "stapler", "validate", str(args.pkg)]' in receipt_runner


def test_release_gate_records_every_exact_candidate_receipt_kind() -> None:
    text = (ROOT / "scripts" / "verify_macos_release.sh").read_text()
    for required in (
        "source-gate",
        "performance",
        "pkg-signature",
        "notarization",
        "stapling",
        "pkg-gatekeeper",
        "package-contents",
        "app-signature",
        "app-gatekeeper",
        "bundle-closure",
        "entitlements",
        "hardware-smoke",
        "installed-upgrade",
        "settings-preservation",
        "update-archive",
        "sparkle-nested-signing",
        "app-notarization",
        "app-stapling",
        "signed-appcast",
        "uninstall",
        "clean-install",
        "sbom",
    ):
        assert required in text


def test_release_manifest_consumes_receipts_instead_of_asserting_success() -> None:
    text = (ROOT / "scripts" / "generate_release_manifest.py").read_text()

    assert 'parser.add_argument("--candidate"' in text
    assert 'parser.add_argument("--receipt"' in text
    assert 'developer_id_verified": True' not in text
    assert 'notarization_verified": True' not in text
    assert 'installed_upgrade": True' not in text


def test_installed_upgrade_gate_executes_doctor_integrations_and_launchagent() -> None:
    text = (ROOT / "scripts" / "verify_installed_upgrade.py").read_text()

    assert '"doctor"' in text
    assert '"integrations", "status", "--json"' in text
    assert '"/bin/launchctl", "print"' in text
    assert 'EXPECTED_LAUNCH_AGENT_LABEL = "io.sidepulse.agentstatus"' in text


def test_release_publication_is_draft_first_and_rolls_back_on_failure() -> None:
    text = (ROOT / "scripts" / "publish_release.sh").read_text()
    assert "git status --porcelain --untracked-files=all" in text
    assert "--draft" in text
    assert "release upload" in text
    assert "release edit" in text
    assert "--cleanup-tag" in text


def test_release_gate_binds_exact_sparkle_assets_and_app_notary_evidence() -> None:
    text = (ROOT / "scripts" / "verify_macos_release.sh").read_text()

    for required in (
        "--format updater-path",
        "--format appcast-path",
        "--format channel-metadata-path",
        "generate_sparkle_channel.py",
        "app-notary-submission.json",
        "app-notary-log.json",
        "app-notary-submitted-zip.sha256",
        "--update-archive",
        "SIDEPULSE_RELEASE_CHANNEL",
        '--channel "$RELEASE_CHANNEL"',
        "SIDEPULSE_SPARKLE_HISTORY_DIR",
        "--previous-appcast",
        "--previous-archive",
    ):
        assert required in text
    assert "SPARKLE_PRIVATE_KEY" not in text
    assert "NOTARY_PASSWORD" not in text


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
    assert any(failure.startswith("menu_open_p95_ms") for failure in validate_performance_evidence(evidence))
