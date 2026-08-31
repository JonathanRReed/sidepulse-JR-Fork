from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import (
    generate_release_checksums,
    package_macos_artifact,
    python_release_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _package_fixture(
    tmp_path: Path,
) -> tuple[package_macos_artifact.PackageRequest, Path]:
    app = tmp_path / "SidePulse.app"
    app.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    _write_executable(scripts / "postinstall", "exit 0\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "commands.log"
    _write_executable(
        tools / "pkgbuild",
        'printf \'pkgbuild:%s\\n\' "$*" >> "$PACKAGE_SMOKE_LOG"\nfor last do :; done\n: > "$last"\n',
    )
    _write_executable(
        tools / "productbuild",
        'printf \'productbuild:%s\\n\' "$*" >> "$PACKAGE_SMOKE_LOG"\nfor last do :; done\n: > "$last"\n',
    )
    _write_executable(
        tools / "pkgutil",
        'printf \'pkgutil:%s\\n\' "$*" >> "$PACKAGE_SMOKE_LOG"\n',
    )
    request = package_macos_artifact.PackageRequest(
        app_path=app,
        scripts_dir=scripts,
        component_pkg=tmp_path / "SidePulse-component.pkg",
        output_pkg=tmp_path / "SidePulse-0.5.0-arm64.pkg",
        identifier="io.sidepulse.app",
        version="0.5.0",
        installer_sign_identity=None,
        toolchain=package_macos_artifact.PackageToolchain(
            pkgbuild=tools / "pkgbuild",
            productbuild=tools / "productbuild",
            pkgutil=tools / "pkgutil",
        ),
    )
    return request, log


def test_unsigned_pkg_assembly_executes_tools_and_creates_exact_pkg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, log = _package_fixture(tmp_path)
    monkeypatch.setenv("PACKAGE_SMOKE_LOG", str(log))

    output = package_macos_artifact.assemble_package(request)

    assert output == tmp_path / "SidePulse-0.5.0-arm64.pkg"
    assert output.is_file()
    assert log.read_text(encoding="utf-8").splitlines() == [
        "pkgbuild:--component "
        f"{request.app_path} --install-location /Applications --identifier "
        "io.sidepulse.app --version 0.5.0 --scripts "
        f"{request.scripts_dir} {request.component_pkg}",
        f"productbuild:--package {request.component_pkg} {request.output_pkg}",
    ]


def test_signed_pkg_assembly_verifies_the_created_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, log = _package_fixture(tmp_path)
    monkeypatch.setenv("PACKAGE_SMOKE_LOG", str(log))
    signed = replace(
        request,
        installer_sign_identity="Developer ID Installer: Test (TEAMID)",
    )

    package_macos_artifact.assemble_package(signed)

    assert log.read_text(encoding="utf-8").splitlines()[-2:] == [
        f"productbuild:--package {request.component_pkg} --sign "
        "Developer ID Installer: Test (TEAMID) --timestamp "
        f"{request.output_pkg}",
        f"pkgutil:--check-signature {request.output_pkg}",
    ]


def test_pkg_assembly_fails_before_writing_when_a_required_tool_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, log = _package_fixture(tmp_path)
    monkeypatch.setenv("PACKAGE_SMOKE_LOG", str(log))
    request.toolchain.productbuild.unlink()

    with pytest.raises(
        package_macos_artifact.PackageAssemblyError,
        match=r"productbuild tool is missing or not executable",
    ):
        package_macos_artifact.assemble_package(request)

    assert not request.component_pkg.exists()
    assert not request.output_pkg.exists()
    assert not log.exists()


def test_pkg_assembly_surfaces_productbuild_certificate_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, log = _package_fixture(tmp_path)
    monkeypatch.setenv("PACKAGE_SMOKE_LOG", str(log))
    _write_executable(
        request.toolchain.productbuild,
        'printf \'productbuild:%s\\n\' "$*" >> "$PACKAGE_SMOKE_LOG"\n'
        'echo "productbuild: error: no signing certificate" >&2\nexit 1\n',
    )
    signed = replace(
        request,
        installer_sign_identity="Developer ID Installer: Missing (TEAMID)",
    )

    with pytest.raises(
        package_macos_artifact.PackageAssemblyError,
        match=r"productbuild failed with exit code 1: .*no signing certificate",
    ):
        package_macos_artifact.assemble_package(signed)

    assert request.component_pkg.is_file()
    assert not request.output_pkg.exists()


def test_checksum_manifest_is_sorted_exact_and_cli_executable(tmp_path: Path) -> None:
    first = tmp_path / "dist" / "z.pkg"
    second = tmp_path / "dist" / "a.json"
    first.parent.mkdir()
    first.write_bytes(b"pkg")
    second.write_bytes(b"evidence")
    output = tmp_path / "dist" / "SHA256SUMS"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_release_checksums.py"),
            "--root",
            str(tmp_path),
            "--output",
            str(output),
            str(first),
            str(second),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").splitlines() == [
        f"{hashlib.sha256(b'evidence').hexdigest()}  dist/a.json",
        f"{hashlib.sha256(b'pkg').hexdigest()}  dist/z.pkg",
    ]
    assert stat.S_IMODE(output.stat().st_mode) == 0o644


def test_checksum_manifest_fails_closed_for_a_missing_artifact(
    tmp_path: Path,
) -> None:
    output = tmp_path / "SHA256SUMS"

    with pytest.raises(
        generate_release_checksums.ChecksumManifestError,
        match=r"release artifact is missing",
    ):
        generate_release_checksums.write_checksum_manifest(
            root=tmp_path,
            output=output,
            artifacts=(tmp_path / "missing.pkg",),
        )

    assert not output.exists()


def test_checksum_manifest_rejects_duplicate_outside_and_output_aliases(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.pkg"
    artifact.write_bytes(b"pkg")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.pkg"
    outside.write_bytes(b"outside")
    try:
        with pytest.raises(
            generate_release_checksums.ChecksumManifestError,
            match=r"duplicate release artifact",
        ):
            generate_release_checksums.checksum_manifest_text(
                root=tmp_path,
                artifacts=(artifact, artifact),
            )
        with pytest.raises(
            generate_release_checksums.ChecksumManifestError,
            match=r"outside the release root",
        ):
            generate_release_checksums.checksum_manifest_text(
                root=tmp_path,
                artifacts=(outside,),
            )
        with pytest.raises(
            generate_release_checksums.ChecksumManifestError,
            match=r"checksum output is outside the release root",
        ):
            generate_release_checksums.write_checksum_manifest(
                root=tmp_path,
                output=outside,
                artifacts=(artifact,),
            )
        with pytest.raises(
            generate_release_checksums.ChecksumManifestError,
            match=r"checksum output cannot also be an input artifact",
        ):
            generate_release_checksums.write_checksum_manifest(
                root=tmp_path,
                output=artifact,
                artifacts=(artifact,),
            )
    finally:
        outside.unlink(missing_ok=True)


def test_checksum_manifest_rejects_assets_changed_after_release_evidence(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "dist" / "SidePulse-0.5.0-arm64.pkg"
    artifact.parent.mkdir()
    artifact.write_bytes(b"candidate")
    evidence = tmp_path / "dist" / "release-verification.json"
    evidence.write_text(
        json.dumps(
            {
                "document": "jr-bar-release-evidence",
                "artifacts": [
                    {
                        "path": "dist/SidePulse-0.5.0-arm64.pkg",
                        "kind": "file",
                        "bytes": len(b"candidate"),
                        "sha256": hashlib.sha256(b"candidate").hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    artifact.write_bytes(b"changed-after-gate")

    with pytest.raises(
        generate_release_checksums.ChecksumManifestError,
        match=r"does not match release evidence",
    ):
        generate_release_checksums.write_checksum_manifest(
            root=tmp_path,
            output=tmp_path / "dist" / "SHA256SUMS",
            artifacts=(artifact, evidence),
            evidence_manifest=evidence,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("wrong-document", r"wrong document type"),
        ("empty-artifacts", r"has no artifacts"),
        ("malformed-artifact", r"artifact 0 is malformed"),
        ("duplicate-artifact", r"repeats artifact"),
        ("different-inventory", r"do not match the release evidence inventory"),
    ),
)
def test_checksum_manifest_rejects_malformed_release_evidence(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    artifact = tmp_path / "dist" / "candidate.pkg"
    artifact.parent.mkdir()
    artifact.write_bytes(b"candidate")
    record = {
        "path": "dist/candidate.pkg",
        "kind": "file",
        "bytes": len(b"candidate"),
        "sha256": hashlib.sha256(b"candidate").hexdigest(),
    }
    document: dict[str, object] = {
        "document": "jr-bar-release-evidence",
        "artifacts": [record],
    }
    if mutation == "wrong-document":
        document["document"] = "other"
    elif mutation == "empty-artifacts":
        document["artifacts"] = []
    elif mutation == "malformed-artifact":
        document["artifacts"] = [{"path": 42}]
    elif mutation == "duplicate-artifact":
        document["artifacts"] = [record, dict(record)]
    elif mutation == "different-inventory":
        document["artifacts"] = [{**record, "path": "dist/other.pkg"}]
    evidence = tmp_path / "dist" / "release-verification.json"
    evidence.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(
        generate_release_checksums.ChecksumManifestError,
        match=message,
    ):
        generate_release_checksums.write_checksum_manifest(
            root=tmp_path,
            output=tmp_path / "dist" / "SHA256SUMS",
            artifacts=(artifact, evidence),
            evidence_manifest=evidence,
        )


def test_checksum_manifest_requires_evidence_to_be_in_the_checksum_set(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "dist" / "candidate.pkg"
    artifact.parent.mkdir()
    artifact.write_bytes(b"candidate")
    evidence = tmp_path / "dist" / "release-verification.json"
    evidence.write_text(
        json.dumps(
            {
                "document": "jr-bar-release-evidence",
                "artifacts": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        generate_release_checksums.ChecksumManifestError,
        match=r"must be checksummed",
    ):
        generate_release_checksums.write_checksum_manifest(
            root=tmp_path,
            output=tmp_path / "dist" / "SHA256SUMS",
            artifacts=(artifact,),
            evidence_manifest=evidence,
        )


def test_publisher_binds_checksums_to_release_evidence() -> None:
    source = (ROOT / "scripts" / "publish_release.sh").read_text(encoding="utf-8")

    assert '--evidence-manifest "$ROOT_DIR/dist/release-verification.json"' in source


def test_python_release_artifacts_replace_only_exact_stale_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    staging = root / "build" / "python-release"
    output = root / "dist"
    output.mkdir()
    stale_wheel = output / "sidepulse-0.5.0-py3-none-any.whl"
    stale_sdist = output / "sidepulse-0.5.0.tar.gz"
    unrelated = output / "other-0.1.0-py3-none-any.whl"
    stale_wheel.write_bytes(b"stale-wheel")
    stale_sdist.write_bytes(b"stale-sdist")
    unrelated.write_bytes(b"unrelated")
    log = tmp_path / "python-build.log"
    fake_python = tmp_path / "python"
    _write_executable(
        fake_python,
        """printf '%s\\n' "$*" >> "$PYTHON_RELEASE_LOG"
if [ "$1" = "-m" ] && [ "$2" = "build" ]; then
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --outdir) out="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    /bin/mkdir -p "$out"
    printf fresh-wheel > "$out/sidepulse-0.5.0-py3-none-any.whl"
    printf fresh-sdist > "$out/sidepulse-0.5.0.tar.gz"
    exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "twine" ]; then
    exit 0
fi
exit 90
""",
    )
    monkeypatch.setenv("PYTHON_RELEASE_LOG", str(log))

    artifacts = python_release_artifacts.build_artifacts(
        python_release_artifacts.PythonReleaseRequest(
            root=root,
            staging_dir=staging,
            output_dir=output,
            version="0.5.0",
            python=fake_python,
        )
    )

    assert artifacts == (stale_wheel, stale_sdist)
    assert stale_wheel.read_bytes() == b"fresh-wheel"
    assert stale_sdist.read_bytes() == b"fresh-sdist"
    assert unrelated.read_bytes() == b"unrelated"
    assert log.read_text(encoding="utf-8").splitlines() == [
        f"-m build --no-isolation --outdir {staging} {root}",
        f"-m twine check {staging / stale_wheel.name} {staging / stale_sdist.name}",
    ]


def test_python_release_artifacts_refuse_a_nonempty_staging_directory(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "stale.whl").write_bytes(b"stale")

    with pytest.raises(
        python_release_artifacts.PythonReleaseArtifactError,
        match=r"staging directory must be empty",
    ):
        python_release_artifacts.build_artifacts(
            python_release_artifacts.PythonReleaseRequest(
                root=tmp_path,
                staging_dir=staging,
                output_dir=tmp_path / "dist",
                version="0.5.0",
                python=Path(sys.executable),
            )
        )


def test_publisher_delegates_checksum_creation_to_executable_tool() -> None:
    source = (ROOT / "scripts" / "publish_release.sh").read_text(encoding="utf-8")

    assert "generate_release_checksums.py" in source
    assert "shasum -a 256" not in source


def test_packaging_contract_requires_exact_sparkle_release_assets(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_artifact_contract.py"),
            "--version",
            "0.5.0",
            "--architecture",
            "arm64",
            "--dist-dir",
            str(tmp_path),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    contract = json.loads(result.stdout)
    assert contract["schema_version"] == 3
    assert contract["authoritative_macos_artifact"] == {
        "kind": "pkg",
        "name": "SidePulse-0.5.0-arm64.pkg",
        "primary": True,
        "required": True,
    }
    assert [item["name"] for item in contract["supplemental_macos_artifacts"]] == [
        "SidePulse-0.5.0-arm64.zip",
        "appcast.xml",
        "jr-bar-update-channel.json",
    ]
    assert contract["updater"]["kind"] == "sparkle"
    assert contract["updater"]["appcast_supported"] is True


@pytest.mark.parametrize(
    ("format_name", "expected"),
    (
        ("updater-path", "SidePulse-0.5.0-arm64.zip"),
        ("appcast-path", "appcast.xml"),
        ("channel-metadata-path", "jr-bar-update-channel.json"),
    ),
)
def test_release_contract_cli_returns_exact_updater_paths(
    tmp_path: Path,
    format_name: str,
    expected: str,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_artifact_contract.py"),
            "--version",
            "0.5.0",
            "--architecture",
            "arm64",
            "--dist-dir",
            str(tmp_path),
            "--format",
            format_name,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == tmp_path / expected


def _publisher_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "release-root"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "publish_release.sh", scripts / "publish_release.sh")
    _write_executable(scripts / "verify_macos_release.sh", "exit 0\n")
    dist = root / "dist"
    dist.mkdir()
    for name in (
        "sidepulse-0.5.0-py3-none-any.whl",
        "sidepulse-0.5.0.tar.gz",
        "release-environment.txt",
        "performance-evidence.json",
        "sidepulse-sbom.cdx.json",
        "SidePulse-0.5.0-arm64.pkg",
        "SidePulse-0.5.0-arm64.zip",
        "appcast.xml",
        "jr-bar-update-channel.json",
        "release-verification.json",
    ):
        (dist / name).write_text(name, encoding="utf-8")

    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    _write_executable(
        python,
        """case "$1" in
*validate_release_version.py) printf '0.5.0\\n' ;;
*release_artifact_contract.py)
    format=""
    while [ "$#" -gt 0 ]; do
        if [ "$1" = "--format" ]; then format="$2"; break; fi
        shift
    done
    case "$format" in
        path) printf 'dist/SidePulse-0.5.0-arm64.pkg\\n' ;;
        developer-paths) printf 'dist/sidepulse-0.5.0-py3-none-any.whl\\ndist/sidepulse-0.5.0.tar.gz\\n' ;;
        updater-path) printf 'dist/SidePulse-0.5.0-arm64.zip\\n' ;;
        appcast-path) printf 'dist/appcast.xml\\n' ;;
        channel-metadata-path) printf 'dist/jr-bar-update-channel.json\\n' ;;
        *) exit 89 ;;
    esac ;;
*generate_release_checksums.py)
    while [ "$#" -gt 0 ]; do
        if [ "$1" = "--output" ]; then : > "$2"; exit 0; fi
        shift
    done
    exit 88 ;;
*) exit 87 ;;
esac
""",
    )

    tool_bin = tmp_path / "bin"
    tool_bin.mkdir()
    log = tmp_path / "gh.log"
    state = tmp_path / "gh-state"
    state.mkdir()
    _write_executable(
        tool_bin / "git",
        """case "$1 $2" in
'status --porcelain') exit 0 ;;
'branch --show-current') printf 'main\\n' ;;
'fetch --quiet') exit 0 ;;
'rev-parse HEAD'|'rev-parse origin/main') printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\\n' ;;
'rev-parse v0.5.0') exit 1 ;;
'ls-remote --exit-code') exit 1 ;;
*) exit 86 ;;
esac
""",
    )
    _write_executable(
        tool_bin / "gh",
        """printf '%s\\n' "$*" >> "$FAKE_GH_LOG"
if [ "$1 $2 $3" = "release view v0.5.0" ]; then
    if [ -f "$FAKE_GH_STATE/version-published" ]; then
        printf 'SidePulse-0.5.0-arm64.zip\\n'
        exit 0
    fi
    exit 1
fi
if [ "$1 $2 $3" = "release view updates" ]; then exit 0; fi
if [ "$1 $2 $3" = "release create v0.5.0" ]; then
    : > "$FAKE_GH_STATE/version-draft"
    exit 0
fi
if [ "$1 $2 $3" = "release upload v0.5.0" ]; then
    if [ "${FAKE_GH_FAIL_VERSION_UPLOAD:-0}" = "1" ]; then exit 42; fi
    case "$*" in
        *SidePulse-0.5.0-arm64.zip*) : > "$FAKE_GH_STATE/archive-uploaded" ;;
        *) exit 43 ;;
    esac
    exit 0
fi
if [ "$1 $2 $3" = "release edit v0.5.0" ]; then
    [ -f "$FAKE_GH_STATE/archive-uploaded" ] || exit 44
    : > "$FAKE_GH_STATE/version-published"
    exit 0
fi
if [ "$1 $2 $3" = "release upload updates" ]; then
    [ -f "$FAKE_GH_STATE/version-published" ] || exit 45
    [ -f "$FAKE_GH_STATE/archive-uploaded" ] || exit 46
    if [ "${FAKE_GH_FAIL_FEED_UPLOAD:-0}" = "1" ]; then exit 47; fi
    exit 0
fi
if [ "$1 $2 $3" = "release delete v0.5.0" ]; then exit 0; fi
exit 85
""",
    )
    environment = {
        **os.environ,
        "PATH": f"{tool_bin}:{os.environ['PATH']}",
        "FAKE_GH_LOG": str(log),
        "FAKE_GH_STATE": str(state),
    }
    return root, environment


def test_publisher_publishes_version_archive_before_mutating_durable_feed(
    tmp_path: Path,
) -> None:
    root, environment = _publisher_fixture(tmp_path)

    result = subprocess.run(
        [str(root / "scripts" / "publish_release.sh")],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    commands = Path(environment["FAKE_GH_LOG"]).read_text(encoding="utf-8").splitlines()
    version_upload = next(index for index, line in enumerate(commands) if line.startswith("release upload v0.5.0"))
    version_publish = next(index for index, line in enumerate(commands) if line.startswith("release edit v0.5.0"))
    feed_uploads = [index for index, line in enumerate(commands) if line.startswith("release upload updates")]
    assert "SidePulse-0.5.0-arm64.zip" in commands[version_upload]
    assert feed_uploads and version_upload < version_publish < min(feed_uploads)
    assert "jr-bar-update-channel.json" in commands[feed_uploads[0]]
    assert "appcast.xml" in commands[feed_uploads[-1]]


def test_publisher_never_updates_feed_when_version_archive_upload_fails(
    tmp_path: Path,
) -> None:
    root, environment = _publisher_fixture(tmp_path)
    environment["FAKE_GH_FAIL_VERSION_UPLOAD"] = "1"

    result = subprocess.run(
        [str(root / "scripts" / "publish_release.sh")],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    commands = Path(environment["FAKE_GH_LOG"]).read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith("release upload updates") for line in commands)
    assert any(line.startswith("release delete v0.5.0") for line in commands)


def test_publisher_preserves_published_version_when_feed_update_fails(
    tmp_path: Path,
) -> None:
    root, environment = _publisher_fixture(tmp_path)
    environment["FAKE_GH_FAIL_FEED_UPLOAD"] = "1"

    result = subprocess.run(
        [str(root / "scripts" / "publish_release.sh")],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    commands = Path(environment["FAKE_GH_LOG"]).read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("release upload updates") for line in commands)
    assert not any(line.startswith("release delete v0.5.0") for line in commands)
