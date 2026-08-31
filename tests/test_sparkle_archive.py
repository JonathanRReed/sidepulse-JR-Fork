import importlib
import os
import plistlib
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _module():
    return importlib.import_module("scripts.package_sparkle_archive")


def _app_bundle(tmp_path: Path, *, version: str = "0.5.0") -> Path:
    app = tmp_path / "build" / "SidePulse.app"
    contents = app / "Contents"
    executable = contents / "MacOS" / "SidePulse"
    resources = contents / "Resources"
    executable.parent.mkdir(parents=True)
    resources.mkdir()
    executable.write_bytes(b"signed application")
    executable.chmod(0o755)
    (resources / "JR Bar.txt").write_text("resource bytes\n", encoding="utf-8")
    (resources / "current").symlink_to("JR Bar.txt")
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(
            {
                "CFBundleIdentifier": "io.sidepulse.app",
                "CFBundleShortVersionString": version,
                "CFBundleVersion": "50",
            },
            stream,
        )
    return app


def test_package_archive_contains_only_sidepulse_app_and_preserves_bundle_tree(
    tmp_path: Path,
) -> None:
    package_sparkle_archive = _module()
    app = _app_bundle(tmp_path)
    output = tmp_path / "dist" / "SidePulse-0.5.0-arm64.zip"
    output.parent.mkdir(parents=True)

    result = package_sparkle_archive.package_archive(app=app, output=output)

    assert result == output
    with zipfile.ZipFile(output) as archive:
        members = archive.namelist()
    assert members
    assert {Path(member).parts[0] for member in members} == {"SidePulse.app"}

    extracted = tmp_path / "extracted"
    subprocess.run(
        ["/usr/bin/ditto", "-x", "-k", str(output), str(extracted)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    extracted_app = extracted / "SidePulse.app"
    assert (extracted_app / "Contents" / "Resources" / "JR Bar.txt").read_bytes() == b"resource bytes\n"
    link = extracted_app / "Contents" / "Resources" / "current"
    assert link.is_symlink()
    assert os.readlink(link) == "JR Bar.txt"
    assert os.access(extracted_app / "Contents" / "MacOS" / "SidePulse", os.X_OK)


@pytest.mark.parametrize(
    ("app_name", "output_name", "message"),
    (
        ("Other.app", "SidePulse-0.5.0-arm64.zip", "SidePulse.app"),
        ("SidePulse.app", "SidePulse-9.9.9-arm64.zip", "bundle version"),
        ("SidePulse.app", "SidePulse-0.5.0-../escape.zip", "archive name"),
        ("SidePulse.app", "latest.zip", "archive name"),
    ),
)
def test_package_archive_rejects_unsafe_or_inexact_identity(
    tmp_path: Path,
    app_name: str,
    output_name: str,
    message: str,
) -> None:
    package_sparkle_archive = _module()
    original = _app_bundle(tmp_path)
    app = original.with_name(app_name)
    if app != original:
        original.rename(app)
    output = tmp_path / "dist" / output_name
    output.parent.mkdir(parents=True)

    with pytest.raises(package_sparkle_archive.SparkleArchiveError, match=message):
        package_sparkle_archive.package_archive(app=app, output=output)

    assert not output.exists()


def test_package_archive_rejects_symlinked_inputs_outputs_and_output_inside_bundle(
    tmp_path: Path,
) -> None:
    package_sparkle_archive = _module()
    app = _app_bundle(tmp_path)
    linked_app = tmp_path / "SidePulse.app"
    linked_app.symlink_to(app, target_is_directory=True)
    output = tmp_path / "SidePulse-0.5.0-arm64.zip"

    with pytest.raises(package_sparkle_archive.SparkleArchiveError, match="symlink"):
        package_sparkle_archive.package_archive(app=linked_app, output=output)

    output.symlink_to(tmp_path / "elsewhere.zip")
    with pytest.raises(package_sparkle_archive.SparkleArchiveError, match=r"output.*symlink"):
        package_sparkle_archive.package_archive(app=app, output=output)

    nested_output = app / "SidePulse-0.5.0-arm64.zip"
    with pytest.raises(package_sparkle_archive.SparkleArchiveError, match="inside"):
        package_sparkle_archive.package_archive(app=app, output=nested_output)


def test_package_archive_surfaces_missing_tool_failure_and_missing_output(
    tmp_path: Path,
) -> None:
    package_sparkle_archive = _module()
    app = _app_bundle(tmp_path)
    output = tmp_path / "SidePulse-0.5.0-arm64.zip"

    with pytest.raises(package_sparkle_archive.SparkleArchiveError, match=r"ditto.*missing"):
        package_sparkle_archive.package_archive(
            app=app,
            output=output,
            ditto=tmp_path / "missing-ditto",
        )

    failing = tmp_path / "failing-ditto"
    failing.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
    failing.chmod(0o755)
    with pytest.raises(package_sparkle_archive.SparkleArchiveError, match="exit code 23"):
        package_sparkle_archive.package_archive(app=app, output=output, ditto=failing)

    no_output = tmp_path / "no-output-ditto"
    no_output.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    no_output.chmod(0o755)
    with pytest.raises(package_sparkle_archive.SparkleArchiveError, match="without creating"):
        package_sparkle_archive.package_archive(app=app, output=output, ditto=no_output)

    assert not output.exists()


def test_package_archive_cli_accepts_exact_builder_contract(tmp_path: Path) -> None:
    app = _app_bundle(tmp_path)
    output = tmp_path / "dist" / "SidePulse-0.5.0-x86_64.zip"
    output.parent.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "package_sparkle_archive.py"),
            "--app",
            str(app),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(output)
    assert output.is_file()


def test_validate_archive_rejects_an_escaping_symlink_target(tmp_path: Path) -> None:
    package_sparkle_archive = _module()
    archive = tmp_path / "SidePulse-0.5.0-arm64.zip"
    with zipfile.ZipFile(archive, mode="w") as bundle_zip:
        root = zipfile.ZipInfo("SidePulse.app/")
        root.create_system = 3
        root.external_attr = (0o040755 << 16) | 0x10
        bundle_zip.writestr(root, b"")
        link = zipfile.ZipInfo("SidePulse.app/Contents/Resources/escape")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        bundle_zip.writestr(link, b"../../../../outside")

    with pytest.raises(package_sparkle_archive.SparkleArchiveError, match="escaping symlink"):
        package_sparkle_archive.validate_archive(archive=archive)
