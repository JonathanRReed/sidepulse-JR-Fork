from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "packaging" / "build_macos_pkg.sh"


def test_package_builder_fails_fast_and_never_defaults_to_apple_python_39() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "set -euo pipefail" in text
    assert 'BUILD_PYTHON="${BUILD_PYTHON:-/usr/bin/python3}"' not in text
    assert "SidePulse requires Python 3.10+" in text
    assert "sys.version_info < (3, 10)" in text


def test_package_builder_verifies_delivered_signature_identity() -> None:
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    signer = (ROOT / "packaging" / "sign_macos_app.py").read_text(
        encoding="utf-8"
    )

    assert "TeamIdentifier" in text
    assert "verify_macos_app.py" in text
    # The strict deep verification now lives in the signer the script runs.
    assert "packaging/sign_macos_app.py" in text
    for flag in ('"--verify"', '"--deep"', '"--strict"'):
        assert flag in signer


def test_clean_install_verifies_t3_integration_artifacts_and_commands() -> None:
    text = (ROOT / "scripts" / "verify_clean_install.py").read_text(
        encoding="utf-8"
    )

    assert '"integration_compatibility.json"' in text
    assert '"sidepulse-integrations"' in text
    assert '"integrations", "status", "--json"' in text
    assert '"sidepulse.t3_compat"' in text
    assert "sidepulse.codexbar_compat" not in text
