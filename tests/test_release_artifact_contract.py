from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import release_artifact_contract

ROOT = Path(__file__).resolve().parents[1]


def test_pkg_is_the_only_authoritative_macos_release_artifact() -> None:
    document = release_artifact_contract.contract_document(
        version="0.5.0",
        architecture="arm64",
    )

    assert document == {
        "schema_version": 3,
        "product_display_name": "JR Bar",
        "compatibility_app_bundle": "SidePulse.app",
        "authoritative_macos_artifact": {
            "kind": "pkg",
            "name": "SidePulse-0.5.0-arm64.pkg",
            "primary": True,
            "required": True,
        },
        "required_signing_inputs": [
            "APP_SIGN_IDENTITY",
            "INSTALLER_SIGN_IDENTITY",
            "NOTARY_PROFILE",
            "SPARKLE_KEY_ACCOUNT",
        ],
        "developer_release_artifacts": [
            {
                "kind": "wheel",
                "name": "sidepulse-0.5.0-py3-none-any.whl",
                "authoritative_macos_product": False,
                "required_for_github_release": True,
            },
            {
                "kind": "sdist",
                "name": "sidepulse-0.5.0.tar.gz",
                "authoritative_macos_product": False,
                "required_for_github_release": True,
            },
        ],
        "supplemental_macos_artifacts": [
            {
                "kind": "sparkle_update_archive",
                "name": "SidePulse-0.5.0-arm64.zip",
                "authoritative_macos_product": False,
                "primary": False,
                "required_for_github_release": True,
                "contents": ["SidePulse.app"],
            },
            {
                "kind": "sparkle_appcast",
                "name": "appcast.xml",
                "authoritative_macos_product": False,
                "primary": False,
                "required_for_github_release": True,
                "signed": True,
            },
            {
                "kind": "sparkle_channel_metadata",
                "name": "jr-bar-update-channel.json",
                "authoritative_macos_product": False,
                "primary": False,
                "required_for_github_release": True,
                "required_fields": [
                    "candidate_id",
                    "channel",
                    "version",
                    "build",
                    "architecture",
                    "feed_url",
                    "download_url",
                    "phased_rollout_interval_seconds",
                    "public_key_fingerprint_sha256",
                    "archive",
                    "appcast",
                ],
            },
        ],
        "updater": {
            "kind": "sparkle",
            "appcast_supported": True,
            "framework": {
                "version": "2.9.6",
                "archive_url": (
                    "https://github.com/sparkle-project/Sparkle/releases/"
                    "download/2.9.6/Sparkle-2.9.6.tar.xz"
                ),
                "archive_sha256": (
                    "52bf9e88cdd972fc0c81501377a880e90d47031bd8ca5462488f843e2609e192"
                ),
            },
            "feed_url": (
                "https://github.com/JonathanRReed/sidepulse-JR-Fork/"
                "releases/download/updates/appcast.xml"
            ),
            "channels": {
                "stable": {
                    "default": True,
                    "sparkle_channel": None,
                    "phased_rollout_interval": 86400,
                },
                "beta": {
                    "default": False,
                    "sparkle_channel": "beta",
                    "phased_rollout_interval": None,
                },
            },
        },
    }


def test_authoritative_artifact_path_is_exact_and_not_a_glob(tmp_path: Path) -> None:
    assert (
        release_artifact_contract.artifact_path(
            tmp_path,
            version="0.5.0",
            architecture="x86_64",
        )
        == tmp_path / "SidePulse-0.5.0-x86_64.pkg"
    )

    assert release_artifact_contract.developer_artifact_paths(
        tmp_path,
        version="0.5.0",
    ) == (
        tmp_path / "sidepulse-0.5.0-py3-none-any.whl",
        tmp_path / "sidepulse-0.5.0.tar.gz",
    )
    assert release_artifact_contract.updater_archive_name(
        version="0.5.0",
        architecture="x86_64",
    ) == "SidePulse-0.5.0-x86_64.zip"
    assert release_artifact_contract.updater_archive_path(
        tmp_path,
        version="0.5.0",
        architecture="x86_64",
    ) == (tmp_path / "SidePulse-0.5.0-x86_64.zip")
    assert release_artifact_contract.appcast_name() == "appcast.xml"
    assert release_artifact_contract.appcast_path(tmp_path) == tmp_path / "appcast.xml"
    assert release_artifact_contract.channel_metadata_name() == "jr-bar-update-channel.json"
    assert release_artifact_contract.channel_metadata_path(tmp_path) == (
        tmp_path / "jr-bar-update-channel.json"
    )


@pytest.mark.parametrize(
    ("version", "architecture"),
    (
        ("../0.5.0", "arm64"),
        ("0.5.0", "../arm64"),
        ("0.5.0/escape", "arm64"),
        ("0.5.0", "arm64/*.pkg"),
        ("..", "arm64"),
        ("0.5.0", ".."),
    ),
)
def test_artifact_identity_rejects_path_and_glob_syntax(
    version: str,
    architecture: str,
) -> None:
    with pytest.raises(ValueError):
        release_artifact_contract.artifact_name(
            version=version,
            architecture=architecture,
        )
    with pytest.raises(ValueError):
        release_artifact_contract.updater_archive_name(
            version=version,
            architecture=architecture,
        )


def test_contract_cli_outputs_the_exact_path_and_machine_readable_policy(
    tmp_path: Path,
) -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "release_artifact_contract.py"),
        "--version",
        "0.5.0",
        "--architecture",
        "arm64",
        "--dist-dir",
        str(tmp_path),
    ]

    path_result = subprocess.run(
        [*command, "--format", "path"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    json_result = subprocess.run(
        [*command, "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    developer_result = subprocess.run(
        [*command, "--format", "developer-paths"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    updater_result = subprocess.run(
        [*command, "--format", "updater-path"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    appcast_result = subprocess.run(
        [*command, "--format", "appcast-path"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    channel_metadata_result = subprocess.run(
        [*command, "--format", "channel-metadata-path"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert path_result.stdout.strip() == str(tmp_path / "SidePulse-0.5.0-arm64.pkg")
    assert json.loads(json_result.stdout) == release_artifact_contract.contract_document(
        version="0.5.0",
        architecture="arm64",
    )
    assert developer_result.stdout.splitlines() == [
        str(tmp_path / "sidepulse-0.5.0-py3-none-any.whl"),
        str(tmp_path / "sidepulse-0.5.0.tar.gz"),
    ]
    assert updater_result.stdout.strip() == str(tmp_path / "SidePulse-0.5.0-arm64.zip")
    assert appcast_result.stdout.strip() == str(tmp_path / "appcast.xml")
    assert channel_metadata_result.stdout.strip() == str(
        tmp_path / "jr-bar-update-channel.json"
    )


def test_release_shell_surfaces_delegate_to_the_contract() -> None:
    builder = (ROOT / "packaging" / "build_macos_pkg.sh").read_text(encoding="utf-8")
    gate = (ROOT / "scripts" / "verify_macos_release.sh").read_text(encoding="utf-8")
    publisher = (ROOT / "scripts" / "publish_release.sh").read_text(encoding="utf-8")

    for source in (builder, gate, publisher):
        assert "release_artifact_contract.py" in source
    assert 'dist/SidePulse-"$version"-*.pkg' not in publisher
    assert "--python-only" not in publisher
    assert "PYTHON_ONLY" not in publisher
    assert "dist/*.whl" not in gate
    assert "dist/*.tar.gz" not in gate
    assert "dist/*.whl" not in publisher
    assert "dist/*.tar.gz" not in publisher
    assert "developer-paths" in gate
    assert "developer-paths" in publisher
    assert "python_release_artifacts.py" in gate
