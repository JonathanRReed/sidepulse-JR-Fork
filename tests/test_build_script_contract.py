from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "packaging" / "build_macos_pkg.sh"
HOOK_BENCHMARK = ROOT / "scripts" / "benchmark_hook_ingress.py"


def test_package_builder_fails_fast_and_never_defaults_to_apple_python_39() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "set -euo pipefail" in text
    assert 'BUILD_PYTHON="${BUILD_PYTHON:-/usr/bin/python3}"' not in text
    assert "JR-Bar release packaging requires Python 3.12" in text
    assert "sys.version_info[:2] != (3, 12)" in text
    assert "scripts/validate_release_version.py" in text


def test_release_workflow_selects_the_locked_python_runtime() -> None:
    workflow = (ROOT / ".github" / "workflows" / "self-hosted-macos.yml").read_text(encoding="utf-8")

    assert 'BUILD_PYTHON: "python3.12"' in workflow


def test_source_install_drops_only_the_incompatible_build_constraint() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert (
        'env -u PIP_BUILD_CONSTRAINT "$VENV_DIR/bin/python" -m pip install '
        '"$ROOT_DIR" --no-deps --no-build-isolation'
    ) in text
    assert 'export PIP_CONSTRAINT="$CONSTRAINTS"' in text
    assert 'export PIP_BUILD_CONSTRAINT="$CONSTRAINTS"' in text


def test_package_builder_embeds_creator_micro_backend() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "--hidden-import sidepulse.creator_micro_adapter" in text
    assert "--hidden-import sidepulse.creator_micro_hidapi" in text
    assert "--hidden-import hid" in text


def test_package_builder_embeds_distribution_metadata_for_runtime_version() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "--copy-metadata sidepulse" in text


def test_package_builder_sets_display_name_without_changing_bundle_identity() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'PRODUCT_DISPLAY_NAME="JR-Bar"' in text
    assert ":CFBundleDisplayName string $PRODUCT_DISPLAY_NAME" in text
    assert ":CFBundleName string $PRODUCT_DISPLAY_NAME" in text
    assert 'MINIMUM_SUPPORTED_MACOS="11.0"' in text
    assert ":LSMinimumSystemVersion string $MINIMUM_SUPPORTED_MACOS" in text
    assert "--name SidePulse" in text
    assert 'APP_ID="io.sidepulse.app"' in text


def test_package_builder_verifies_delivered_signature_identity() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    signer = (ROOT / "packaging" / "sign_macos_app.py").read_text(encoding="utf-8")

    assert "TeamIdentifier" in text
    assert "verify_macos_app.py" in text
    # The strict deep verification now lives in the signer the script runs.
    assert "packaging/sign_macos_app.py" in text
    for flag in ('"--verify"', '"--deep"', '"--strict"'):
        assert flag in signer


def test_package_builder_retains_structured_notarization_evidence() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "--output-format json" in text
    assert "notary-submission.json" in text
    assert "notary-log.json" in text
    assert "notary-submission-id" in text
    assert "notary-submitted-pkg.sha256" in text


def test_package_builder_embeds_reviewed_sparkle_before_signing() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    prepare = 'scripts/prepare_sparkle.py" --output "$SPARKLE_DISTRIBUTION"'
    embed = '"$SPARKLE_DISTRIBUTION/Sparkle.framework"'
    license_copy = 'Contents/Resources/ThirdPartyLicenses/Sparkle.txt'
    sign = 'packaging/sign_macos_app.py"'
    verify = 'packaging/verify_sparkle_bundle.py"'

    assert prepare in text
    assert 'SPARKLE_ARCHIVE="${SPARKLE_ARCHIVE:-}"' in text
    assert embed in text
    assert license_copy in text
    assert 'packaging/sparkle_public_ed_key.txt' in text
    assert text.index(prepare) < text.index(embed) < text.index(sign) < text.index(verify)


def test_package_builder_writes_only_reviewed_sparkle_info_keys() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    for key in (
        "SUFeedURL",
        "SUPublicEDKey",
        "SURequireSignedFeed",
        "SUVerifyUpdateBeforeExtraction",
    ):
        assert f":{key}" in text
    for forbidden in (
        "SUEnableAutomaticChecks",
        "SUEnableInstallerLauncherService",
        "SUEnableDownloaderService",
        "com.apple.security.temporary-exception.mach-lookup.global-name",
    ):
        assert forbidden not in text
    assert 'packaging/entitlements.plist"' in text


def test_production_builder_notarizes_app_before_final_zip_and_pkg() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    app_submit = 'notarytool submit "$APP_NOTARY_ZIP"'
    app_staple = 'stapler staple "$APP_PATH"'
    app_validate = 'stapler validate "$APP_PATH"'
    updater_zip = 'scripts/package_sparkle_archive.py"'
    package = 'scripts/package_macos_artifact.py"'
    pkg_submit = 'notarytool submit "$OUTPUT_PKG"'

    assert "app-notary-submission.json" in text
    assert "app-notary-log.json" in text
    assert "app-notary-submitted-zip.sha256" in text
    assert "--format updater-path" in text
    assert text.index(app_submit) < text.index(app_staple) < text.index(app_validate)
    assert text.index(app_validate) < text.index(updater_zip) < text.index(package)
    assert text.index(package) < text.index(pkg_submit)


def test_unsigned_builder_explicitly_refuses_updater_evidence_claims() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "ALLOW_UNSIGNED is local-only" in text
    assert "No updater archive or updater evidence was produced" in text


def test_clean_install_verifies_t3_integration_artifacts_and_commands() -> None:
    text = (ROOT / "scripts" / "verify_clean_install.py").read_text(encoding="utf-8")

    assert '"integration_compatibility.json"' in text
    assert '"sidepulse-integrations"' in text
    assert '"integrations", "status", "--json"' in text
    assert '"sidepulse.t3_compat"' in text
    assert "sidepulse.codexbar_compat" not in text


def test_hook_ingress_benchmark_has_bounded_content_free_report_contract() -> None:
    text = HOOK_BENCHMARK.read_text(encoding="utf-8")

    assert "MINIMUM_SAMPLES: Final = 50" in text
    assert 'choices=("both", "server-up", "server-down")' in text
    assert "TemporaryDirectory" in text
    for field in (
        "sample_count",
        "median_ms",
        "p95_ms",
        "accepted",
        "refused",
        "failed",
        "fallback",
    ):
        assert f'"{field}"' in text
    for forbidden in ("prompt_text", "tool_input", "tool_output", "raw_payload"):
        assert forbidden not in text


def test_hook_ingress_benchmark_refuses_too_few_samples() -> None:
    result = subprocess.run(
        [sys.executable, str(HOOK_BENCHMARK), "--samples", "49"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    assert "at least 50" in result.stderr
