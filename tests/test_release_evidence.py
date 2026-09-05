from __future__ import annotations

import copy
import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import (
    capture_installed_release_baseline,
    release_evidence,
    verify_installed_upgrade,
    verify_uninstalled_candidate,
)

COMMIT = "a" * 40
OBSERVED_AT = "2026-08-29T12:00:00Z"


def _fixture(tmp_path: Path) -> dict[str, object]:
    pkg = tmp_path / "dist" / "SidePulse-0.5.0-arm64.pkg"
    pkg.parent.mkdir(parents=True)
    pkg.write_bytes(b"signed-and-stapled-pkg")
    app = tmp_path / "build" / "SidePulse.app"
    executable = app / "Contents" / "MacOS" / "SidePulse"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"signed-app")
    executable.chmod(0o755)
    sbom = tmp_path / "dist" / "sidepulse-sbom.cdx.json"
    sbom.write_text('{"bomFormat":"CycloneDX"}\n', encoding="utf-8")
    performance = tmp_path / "dist" / "performance.json"
    performance.write_text('{"warm_launch_ms":450}\n', encoding="utf-8")
    update_archive = tmp_path / "dist" / "SidePulse-0.5.0-arm64.zip"
    update_archive.write_bytes(b"signed-notarized-update-archive")
    candidate = release_evidence.create_candidate(
        root=tmp_path,
        version="0.5.0",
        architecture="arm64",
        commit=COMMIT,
        pkg=pkg,
        app=app,
        update_archive=update_archive,
        bundle_identifier="io.sidepulse.app",
        team_identifier="ABCDE12345",
    )
    appcast = tmp_path / "dist" / "appcast.xml"
    appcast.write_text("<rss><channel><item>signed</item></channel></rss>\n", encoding="utf-8")
    channel_metadata = tmp_path / "dist" / "jr-bar-update-channel.json"
    channel_metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "document": "jr-bar-update-channel",
                "candidate_id": candidate["candidate_id"],
                "channel": "stable",
                "version": "0.5.0",
                "build": "5",
                "architecture": "arm64",
                "feed_url": "https://github.com/JonathanRReed/sidepulse-JR-Fork/releases/download/updates/appcast.xml",
                "download_url": "https://github.com/JonathanRReed/sidepulse-JR-Fork/releases/download/v0.5.0/SidePulse-0.5.0-arm64.zip",
                "phased_rollout_interval_seconds": 86400,
                "public_key_fingerprint_sha256": "7" * 64,
                "archive": {
                    "name": "SidePulse-0.5.0-arm64.zip",
                    "bytes": update_archive.stat().st_size,
                    "sha256": release_evidence.sha256_file(update_archive),
                    "ed_signature": "signed-archive",
                },
                "appcast": {
                    "name": "appcast.xml",
                    "bytes": appcast.stat().st_size,
                    "sha256": release_evidence.sha256_file(appcast),
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "root": tmp_path,
        "pkg": pkg,
        "app": app,
        "sbom": sbom,
        "performance": performance,
        "update_archive": update_archive,
        "appcast": appcast,
        "channel_metadata": channel_metadata,
        "candidate": candidate,
    }


def _details(kind: str, candidate: dict[str, object]) -> dict[str, object]:
    app = candidate["app"]
    assert isinstance(app, dict)
    details: dict[str, object] = {}
    if kind == "pkg-signature":
        details = {
            "installer_identity": "Developer ID Installer: JR Bar (ABCDE12345)",
            "team_identifier": "ABCDE12345",
        }
    elif kind == "notarization":
        details = {
            "status": "Accepted",
            "submission_id": "2efe2717-52ef-43a5-96dc-0797e4ca1041",
            "submitted_pkg_sha256": "b" * 64,
            "log_sha256": "c" * 64,
        }
    elif kind == "stapling":
        pkg = candidate["pkg"]
        assert isinstance(pkg, dict)
        details = {
            "notarized_pkg_sha256": "b" * 64,
            "stapled_pkg_sha256": pkg["sha256"],
        }
    elif kind == "package-contents":
        details = {
            "packaged_app_sha256": app["sha256"],
            "payload_sha256": "d" * 64,
        }
    elif kind == "settings-preservation":
        details = {
            "settings_state": "preserved",
            "before_settings_sha256": "1" * 64,
            "after_settings_sha256": "2" * 64,
        }
    elif kind == "installed-upgrade":
        details = {
            "installed_app_sha256": app["sha256"],
            "previous_version": "0.4.0",
            "previous_app_sha256": "3" * 64,
            "previous_package_receipt_sha256": "4" * 64,
        }
    elif kind == "uninstall":
        details = {
            "app_state": "removed",
            "owned_cli_link_state": "removed-or-not-present",
            "package_receipt_state": "removed",
            "user_state": "preserved",
            "owned_integration_state": "removed",
        }
    elif kind == "clean-install":
        details = {"installed_app_sha256": app["sha256"]}
    elif kind == "update-archive":
        archive = candidate["update_archive"]
        assert isinstance(archive, dict)
        details = {
            "archive_sha256": archive["sha256"],
            "archived_app_sha256": app["sha256"],
        }
    elif kind == "sparkle-nested-signing":
        details = {
            "team_identifier": "ABCDE12345",
            "nested_code_count": 6,
        }
    elif kind == "app-notarization":
        details = {
            "status": "Accepted",
            "submission_id": "c8474735-68e8-4317-82cd-e45a3bd09706",
            "submitted_app_zip_sha256": "5" * 64,
            "log_sha256": "6" * 64,
        }
    elif kind == "app-stapling":
        details = {
            "submission_id": "c8474735-68e8-4317-82cd-e45a3bd09706",
            "stapled_app_sha256": app["sha256"],
        }
    elif kind == "signed-appcast":
        archive = candidate["update_archive"]
        assert isinstance(archive, dict)
        details = {"archive_sha256": archive["sha256"]}
    elif kind == "hardware-smoke":
        details = {"hardware_profile": "both"}
    return details


def _receipts(
    fixture: dict[str, object],
    *,
    hardware_profile: str = "software",
) -> list[dict[str, object]]:
    candidate = fixture["candidate"]
    assert isinstance(candidate, dict)
    app_kinds = release_evidence.APP_INPUT_RECEIPTS
    receipts = []
    for kind in sorted(release_evidence.REQUIRED_RECEIPT_KINDS):
        if kind == "hardware-smoke" and hardware_profile == "software":
            continue
        if kind == "performance":
            input_path = fixture["performance"]
        elif kind == "sbom":
            input_path = fixture["sbom"]
        elif kind == "update-archive":
            input_path = fixture["update_archive"]
        elif kind == "signed-appcast":
            input_path = fixture["appcast"]
        elif kind in app_kinds:
            input_path = fixture["app"]
        else:
            input_path = fixture["pkg"]
        assert isinstance(input_path, Path)
        receipts.append(
            release_evidence.create_receipt(
                root=fixture["root"],
                candidate=candidate,
                kind=kind,
                tool=f"test-{kind}",
                input_path=input_path,
                output_text=f"{kind} passed",
                details=(
                    {"hardware_profile": hardware_profile}
                    if kind == "hardware-smoke"
                    else _details(kind, candidate)
                ),
                observed_at=OBSERVED_AT,
            )
        )
    return receipts


def _manifest(
    fixture: dict[str, object],
    receipts: list[dict[str, object]],
    *,
    hardware_profile: str = "software",
):
    return release_evidence.build_manifest(
        root=fixture["root"],
        candidate=fixture["candidate"],
        receipts=receipts,
        sbom=fixture["sbom"],
        performance_evidence=fixture["performance"],
        artifacts=(
            fixture["pkg"],
            fixture["sbom"],
            fixture["update_archive"],
            fixture["appcast"],
            fixture["channel_metadata"],
        ),
        hardware_profile=hardware_profile,
        generated_at=OBSERVED_AT,
    )


def test_manifest_contains_candidate_bound_receipts_without_asserted_booleans(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    document = _manifest(fixture, _receipts(fixture))

    assert document["document"] == "jr-bar-release-evidence"
    assert document["schema_version"] == release_evidence.SCHEMA_VERSION
    assert document["candidate"]["pkg"]["path"] == ("dist/SidePulse-0.5.0-arm64.pkg")
    assert document["candidate"]["update_archive"]["path"] == ("dist/SidePulse-0.5.0-arm64.zip")
    assert {item["kind"] for item in document["receipts"]} == release_evidence.SOFTWARE_RECEIPT_KINDS
    assert "true" not in json.dumps(document).casefold()


def test_software_manifest_does_not_claim_or_require_hardware_smoke(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    receipts = [
        receipt
        for receipt in _receipts(fixture)
        if receipt["kind"] != "hardware-smoke"
    ]

    document = _manifest(fixture, receipts)

    assert document["hardware_profile"] == "software"
    assert "hardware-smoke" not in {item["kind"] for item in document["receipts"]}


def test_explicit_hardware_profile_requires_real_hardware_smoke_receipt(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    receipts = [
        receipt
        for receipt in _receipts(fixture)
        if receipt["kind"] != "hardware-smoke"
    ]

    with pytest.raises(release_evidence.EvidenceError, match="hardware-smoke"):
        _manifest(fixture, receipts, hardware_profile="pro")


def test_hardware_manifest_rejects_smoke_from_another_hardware_profile(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    with pytest.raises(release_evidence.EvidenceError, match="hardware profile"):
        _manifest(
            fixture,
            _receipts(fixture, hardware_profile="both"),
            hardware_profile="pro",
        )


def test_manifest_rejects_every_missing_required_receipt(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipts = _receipts(fixture)

    for removed in release_evidence.SOFTWARE_RECEIPT_KINDS:
        incomplete = [item for item in receipts if item["kind"] != removed]
        with pytest.raises(release_evidence.EvidenceError, match=removed):
            _manifest(fixture, incomplete)


def test_manifest_rejects_a_receipt_from_another_candidate(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipts = _receipts(fixture)
    receipts[0]["candidate_id"] = "f" * 64

    with pytest.raises(release_evidence.EvidenceError, match="candidate"):
        _manifest(fixture, receipts)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("schema_version", 99, r"schema is unsupported"),
        ("candidate_pkg_sha256", "f" * 64, r"another candidate PKG"),
        ("candidate_update_archive_sha256", "f" * 64, r"another candidate update archive"),
        ("result", "failed", r"did not pass"),
        ("input.sha256", "f" * 64, r"input has changed"),
        ("output.sha256", "f" * 64, r"output digest is invalid"),
        ("details", [], r"details are invalid"),
    ),
)
def test_manifest_rejects_corrupted_receipt_integrity_fields(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    fixture = _fixture(tmp_path)
    receipts = _receipts(fixture)
    receipt = next(item for item in receipts if item["kind"] == "source-gate")
    if "." in field:
        owner, nested = field.split(".", 1)
        record = receipt[owner]
        assert isinstance(record, dict)
        record[nested] = value
    else:
        receipt[field] = value

    with pytest.raises(release_evidence.EvidenceError, match=message):
        _manifest(fixture, receipts)


def test_manifest_rejects_substituted_package_contents(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipts = _receipts(fixture)
    package_receipt = next(item for item in receipts if item["kind"] == "package-contents")
    package_receipt["details"]["packaged_app_sha256"] = "e" * 64

    with pytest.raises(release_evidence.EvidenceError, match="packaged app"):
        _manifest(fixture, receipts)


def test_manifest_rehashes_and_rejects_a_changed_candidate_pkg(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipts = _receipts(fixture)
    fixture["pkg"].write_bytes(b"substituted-after-receipts")

    with pytest.raises(release_evidence.EvidenceError, match="PKG"):
        _manifest(fixture, receipts)


def test_candidate_and_manifest_rehash_the_supplemental_update_archive(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    receipts = _receipts(fixture)
    fixture["update_archive"].write_bytes(b"changed-after-candidate-evidence")

    with pytest.raises(release_evidence.EvidenceError, match="update archive"):
        _manifest(fixture, receipts)


@pytest.mark.parametrize(
    ("asset_key", "message"),
    (
        ("update_archive", "update archive"),
        ("appcast", "appcast"),
        ("channel_metadata", "channel metadata"),
    ),
)
def test_manifest_requires_every_exact_updater_asset(
    tmp_path: Path,
    asset_key: str,
    message: str,
) -> None:
    fixture = _fixture(tmp_path)
    artifacts = [
        fixture["pkg"],
        fixture["sbom"],
        fixture["update_archive"],
        fixture["appcast"],
        fixture["channel_metadata"],
    ]
    artifacts.remove(fixture[asset_key])

    with pytest.raises(release_evidence.EvidenceError, match=message):
        release_evidence.build_manifest(
            root=fixture["root"],
            candidate=fixture["candidate"],
            receipts=_receipts(fixture),
            sbom=fixture["sbom"],
            performance_evidence=fixture["performance"],
            artifacts=artifacts,
            generated_at=OBSERVED_AT,
        )


def test_manifest_rejects_duplicate_updater_assets(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    duplicate = tmp_path / "mirror" / "appcast.xml"
    duplicate.parent.mkdir()
    duplicate.write_bytes(fixture["appcast"].read_bytes())

    with pytest.raises(release_evidence.EvidenceError, match="duplicate appcast"):
        release_evidence.build_manifest(
            root=fixture["root"],
            candidate=fixture["candidate"],
            receipts=_receipts(fixture),
            sbom=fixture["sbom"],
            performance_evidence=fixture["performance"],
            artifacts=(
                fixture["pkg"],
                fixture["sbom"],
                fixture["update_archive"],
                fixture["appcast"],
                duplicate,
                fixture["channel_metadata"],
            ),
            generated_at=OBSERVED_AT,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("candidate", "another candidate"),
        ("archive", "archive SHA-256"),
        ("appcast", "appcast SHA-256"),
        ("name", "archive name"),
    ),
)
def test_manifest_rejects_wrong_candidate_or_tampered_channel_metadata(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    fixture = _fixture(tmp_path)
    channel = json.loads(fixture["channel_metadata"].read_text(encoding="utf-8"))
    if mutation == "candidate":
        channel["candidate_id"] = "f" * 64
    elif mutation == "archive":
        channel["archive"]["sha256"] = "f" * 64
    elif mutation == "appcast":
        channel["appcast"]["sha256"] = "f" * 64
    else:
        channel["archive"]["name"] = "another.zip"
    fixture["channel_metadata"].write_text(json.dumps(channel), encoding="utf-8")

    with pytest.raises(release_evidence.EvidenceError, match=message):
        _manifest(fixture, _receipts(fixture))


def test_candidate_rejects_an_artifact_outside_release_root(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    outside = tmp_path.parent / "outside.pkg"
    outside.write_bytes(b"outside")

    with pytest.raises(release_evidence.EvidenceError, match="outside release root"):
        release_evidence.create_candidate(
            root=tmp_path,
            version="0.5.0",
            architecture="arm64",
            commit=COMMIT,
            pkg=outside,
            app=fixture["app"],
            update_archive=fixture["update_archive"],
            bundle_identifier="io.sidepulse.app",
            team_identifier="ABCDE12345",
        )


def test_candidate_rejects_symlinked_pkg_and_app_inputs(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    pkg_link = tmp_path / "dist" / "linked.pkg"
    pkg_link.symlink_to(fixture["pkg"])

    with pytest.raises(release_evidence.EvidenceError, match="symlink"):
        release_evidence.create_candidate(
            root=tmp_path,
            version="0.5.0",
            architecture="arm64",
            commit=COMMIT,
            pkg=pkg_link,
            app=fixture["app"],
            update_archive=fixture["update_archive"],
            bundle_identifier="io.sidepulse.app",
            team_identifier="ABCDE12345",
        )

    app_link = tmp_path / "build" / "Linked.app"
    app_link.symlink_to(fixture["app"], target_is_directory=True)
    with pytest.raises(release_evidence.EvidenceError, match="symlink"):
        release_evidence.create_candidate(
            root=tmp_path,
            version="0.5.0",
            architecture="arm64",
            commit=COMMIT,
            pkg=fixture["pkg"],
            app=app_link,
            update_archive=fixture["update_archive"],
            bundle_identifier="io.sidepulse.app",
            team_identifier="ABCDE12345",
        )


def test_manifest_rejects_nonaccepted_notarization(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipts = _receipts(fixture)
    receipt = next(item for item in receipts if item["kind"] == "notarization")
    receipt["details"]["status"] = "Invalid"

    with pytest.raises(release_evidence.EvidenceError, match="notarization"):
        _manifest(fixture, receipts)


def test_manifest_rejects_a_cross_candidate_notarization_chain(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipts = _receipts(fixture)
    receipt = next(item for item in receipts if item["kind"] == "notarization")
    receipt["details"]["submitted_pkg_sha256"] = "e" * 64

    with pytest.raises(release_evidence.EvidenceError, match="different PKG inputs"):
        _manifest(fixture, receipts)


@pytest.mark.parametrize("previous_version", ("0.5.0", "0.5.1", "0.6.0-beta.1"))
def test_manifest_rejects_non_monotonic_upgrade_claims(
    tmp_path: Path,
    previous_version: str,
) -> None:
    fixture = _fixture(tmp_path)
    receipts = _receipts(fixture)
    receipt = next(item for item in receipts if item["kind"] == "installed-upgrade")
    receipt["details"]["previous_version"] = previous_version

    with pytest.raises(release_evidence.EvidenceError, match="strictly newer"):
        _manifest(fixture, receipts)


@pytest.mark.parametrize(
    ("previous", "candidate"),
    (
        ("0.4.99", "0.5.0"),
        ("0.5.9", "0.5.10"),
        ("0.5.0-beta.9", "0.5.0-beta.10"),
        ("0.5.0-beta.10", "0.5.0-rc.1"),
        ("0.5.0-rc.2", "0.5.0"),
        ("1.9", "1.10"),
    ),
)
def test_version_comparator_accepts_numeric_and_prerelease_upgrades(
    previous: str,
    candidate: str,
) -> None:
    release_evidence.require_strict_version_upgrade(previous, candidate)
    verify_installed_upgrade.require_monotonic_upgrade(previous, candidate)


@pytest.mark.parametrize(
    ("previous", "candidate"),
    (
        ("0.5.0", "0.5.0"),
        ("0.5", "0.5.0"),
        ("0.5.1", "0.5.0"),
        ("0.5.0", "0.5.0-rc.1"),
        ("0.5.0-rc.2", "0.5.0-beta.9"),
        ("0.5.0-beta.10", "0.5.0-beta.2"),
    ),
)
def test_version_comparator_rejects_same_or_downgrade_transitions(
    previous: str,
    candidate: str,
) -> None:
    with pytest.raises(release_evidence.EvidenceError, match="strictly newer"):
        release_evidence.require_strict_version_upgrade(previous, candidate)
    with pytest.raises(ValueError, match="strictly newer"):
        verify_installed_upgrade.require_monotonic_upgrade(previous, candidate)


def test_receipt_rejects_high_confidence_secret_material(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    candidate = fixture["candidate"]
    assert isinstance(candidate, dict)

    with pytest.raises(release_evidence.EvidenceError, match="secret"):
        release_evidence.create_receipt(
            root=tmp_path,
            candidate=candidate,
            kind="source-gate",
            tool="pytest",
            input_path=fixture["pkg"],
            output_text="ghp_" + "A" * 36,
            observed_at=OBSERVED_AT,
        )


@pytest.mark.parametrize(
    "command",
    (
        ("sign_update", "--private-key", "base64-secret"),
        ("sign_update", "--ed-key-file", "/tmp/private-key"),
        ("xcrun", "notarytool", "submit", "--password", "notary-secret"),
        ("generate_appcast", "-----BEGIN " + "PRIVATE KEY-----"),
    ),
)
def test_receipt_runner_rejects_private_keys_and_notary_secrets_in_argv(
    command: tuple[str, ...],
) -> None:
    with pytest.raises(release_evidence.EvidenceError, match=r"secret.*argv"):
        release_evidence.assert_safe_release_command(command)


def test_receipt_runner_allows_keychain_account_and_notary_profile_names() -> None:
    release_evidence.assert_safe_release_command(
        (
            "generate_appcast",
            "--account",
            "io.sidepulse.jr-bar.sparkle",
            "--keychain-profile",
            "jr-bar-notary",
        )
    )


def test_receipt_runner_never_echoes_secret_command_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(fixture["candidate"]), encoding="utf-8")
    output = tmp_path / "receipt.json"
    marker = "-----BEGIN " + "PRIVATE KEY-----"
    monkeypatch.setenv("RELEASE_TEST_SECRET_OUTPUT", marker)

    result = release_evidence.main(
        (
            "run-receipt",
            "--root",
            str(tmp_path),
            "--candidate",
            str(candidate_path),
            "--kind",
            "source-gate",
            "--input",
            str(fixture["pkg"]),
            "--output",
            str(output),
            "--",
            sys.executable,
            "-c",
            "import os; print(os.environ['RELEASE_TEST_SECRET_OUTPUT'])",
        )
    )

    captured = capsys.readouterr()
    assert result == 1
    assert marker not in captured.out
    assert marker not in captured.err
    assert not output.exists()


def test_manifest_rejects_duplicate_and_unknown_receipts(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipts = _receipts(fixture)

    with pytest.raises(release_evidence.EvidenceError, match="duplicate"):
        _manifest(fixture, [*receipts, copy.deepcopy(receipts[0])])

    unknown = copy.deepcopy(receipts)
    unknown[0]["kind"] = "invented-check"
    with pytest.raises(release_evidence.EvidenceError, match="unknown"):
        _manifest(fixture, unknown)


def test_pkg_signature_parser_extracts_the_installer_identity_and_team() -> None:
    output = """Package \"candidate.pkg\":
   Status: signed by a certificate trusted by macOS
   Certificate Chain:
    1. Developer ID Installer: JR Bar LLC (ABCDE12345)
"""

    assert release_evidence.installer_signature_details(output) == {
        "installer_identity": "Developer ID Installer: JR Bar LLC (ABCDE12345)",
        "team_identifier": "ABCDE12345",
    }


def test_pkg_signature_parser_rejects_non_developer_id_output() -> None:
    with pytest.raises(release_evidence.EvidenceError, match="Developer ID Installer"):
        release_evidence.installer_signature_details("Status: unsigned")


def test_notarization_details_bind_submission_log_and_submitted_pkg() -> None:
    submission_id = "2efe2717-52ef-43a5-96dc-0797e4ca1041"
    submitted_sha = "b" * 64
    response = {
        "id": submission_id,
        "status": "Accepted",
        "createdDate": "2026-08-29T12:00:00Z",
    }
    log = {
        "jobId": submission_id,
        "status": "Accepted",
        "statusSummary": "Ready for distribution",
        "statusCode": 0,
        "archiveFilename": "SidePulse-0.5.0-arm64.pkg",
        "sha256": submitted_sha,
        "issues": [],
    }

    details = release_evidence.notarization_details(
        response=response,
        log=log,
        submitted_sha256=submitted_sha,
        pkg_name="SidePulse-0.5.0-arm64.pkg",
        log_sha256="c" * 64,
    )

    assert details["submission_id"] == submission_id
    assert details["submitted_pkg_sha256"] == submitted_sha
    assert details["log_issue_count"] == 0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("jobId", "11111111-1111-1111-1111-111111111111", "submission"),
        ("sha256", "d" * 64, "digest"),
        ("archiveFilename", "another.pkg", "name"),
        ("status", "Invalid", "not accepted"),
    ),
)
def test_notarization_details_reject_cross_candidate_log_fields(
    field: str,
    value: str,
    message: str,
) -> None:
    submission_id = "2efe2717-52ef-43a5-96dc-0797e4ca1041"
    submitted_sha = "b" * 64
    log = {
        "jobId": submission_id,
        "status": "Accepted",
        "archiveFilename": "SidePulse-0.5.0-arm64.pkg",
        "sha256": submitted_sha,
        "issues": [],
    }
    log[field] = value

    with pytest.raises(release_evidence.EvidenceError, match=message):
        release_evidence.notarization_details(
            response={"id": submission_id, "status": "Accepted"},
            log=log,
            submitted_sha256=submitted_sha,
            pkg_name="SidePulse-0.5.0-arm64.pkg",
            log_sha256="c" * 64,
        )


def test_uninstall_verifier_detects_an_owned_launch_agent_left_behind(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    launch_agent = home / "Library" / "LaunchAgents" / "io.sidepulse.agentstatus.plist"
    launch_agent.parent.mkdir(parents=True)
    launch_agent.write_text("owned", encoding="utf-8")

    assert verify_uninstalled_candidate.owned_file_leftovers(
        home,
        system_paths=(),
    ) == (launch_agent,)


def test_uninstall_verifier_detects_an_xdg_user_guard_left_behind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    data_home = tmp_path / "xdg-data"
    guard = data_home / "sidepulse" / "sd-eject-guard" / "SidePulse Pro Eject Prevention"
    guard.parent.mkdir(parents=True)
    guard.write_bytes(b"owned")
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))

    assert guard in verify_uninstalled_candidate.owned_file_leftovers(
        home,
        system_paths=(),
    )


def test_upgrade_baseline_rejects_settings_without_an_installed_app(
    tmp_path: Path,
) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="application is missing"):
        capture_installed_release_baseline.capture_baseline(
            app=tmp_path / "SidePulse.app",
            settings=settings,
        )


def test_upgrade_baseline_rejects_an_install_without_a_package_receipt(
    tmp_path: Path,
) -> None:
    app = tmp_path / "SidePulse.app"
    executable = app / "Contents" / "MacOS" / "SidePulse"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"old-app")
    info = {
        "CFBundleIdentifier": "io.sidepulse.app",
        "CFBundleShortVersionString": "0.4.0",
    }
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    settings = tmp_path / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")

    def no_receipt(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 1, "", "No receipt")

    with pytest.raises(ValueError, match="package receipt is missing"):
        capture_installed_release_baseline.capture_baseline(
            app=app,
            settings=settings,
            runner=no_receipt,
            team_reader=lambda _app: "ABCDE12345",
        )
