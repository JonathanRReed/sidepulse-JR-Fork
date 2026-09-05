#!/usr/bin/env python3
"""Create JR-Bar's exact supplemental Sparkle update archive."""

from __future__ import annotations

import argparse
import os
import plistlib
import posixpath
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

DEFAULT_DITTO = Path("/usr/bin/ditto")
EXPECTED_APP_NAME = "SidePulse.app"
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
_ARCHITECTURE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


class SparkleArchiveError(RuntimeError):
    """Raised when the supplemental update archive is unsafe or incomplete."""


def _require_plain_directory(path: Path, *, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SparkleArchiveError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise SparkleArchiveError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise SparkleArchiveError(f"{label} is not a directory: {path}")
    return path.resolve(strict=True)


def _require_executable(path: Path) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SparkleArchiveError(f"ditto tool is missing: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
        raise SparkleArchiveError(f"ditto tool is missing or not executable: {path}")
    return path.resolve(strict=True)


def _safe_member_name(relative: Path) -> None:
    for part in relative.parts:
        if not part or part in {".", ".."} or "\\" in part or any(ord(character) < 32 for character in part):
            raise SparkleArchiveError(f"application bundle contains an unsafe path: {relative}")


def _validate_bundle_tree(app: Path) -> None:
    for member in sorted(app.rglob("*"), key=lambda item: item.as_posix()):
        relative = member.relative_to(app)
        _safe_member_name(relative)
        metadata = member.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(member)
            if not target or Path(target).is_absolute():
                raise SparkleArchiveError(f"application bundle contains an unsafe symlink: {relative}")
            resolved_target = (member.parent / target).resolve(strict=False)
            try:
                resolved_target.relative_to(app)
            except ValueError:
                raise SparkleArchiveError(f"application bundle contains an escaping symlink: {relative}") from None
        elif stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                raise SparkleArchiveError(f"application bundle contains a hard-linked file: {relative}")
        elif not stat.S_ISDIR(metadata.st_mode):
            raise SparkleArchiveError(f"application bundle contains an unsupported entry: {relative}")


def _bundle_version(app: Path) -> str:
    plist_path = app / "Contents" / "Info.plist"
    try:
        metadata = plist_path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SparkleArchiveError(f"application Info.plist is not a regular file: {plist_path}")
        with plist_path.open("rb") as stream:
            document = plistlib.load(stream)
    except SparkleArchiveError:
        raise
    except (OSError, plistlib.InvalidFileException) as exc:
        raise SparkleArchiveError(f"application Info.plist is missing or invalid: {plist_path}") from exc
    version = document.get("CFBundleShortVersionString") if isinstance(document, dict) else None
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None or ".." in version:
        raise SparkleArchiveError("application bundle version is missing or unsafe")
    return version


def _validate_output_identity(output: Path, *, version: str) -> str:
    prefix = f"SidePulse-{version}-"
    if not output.name.startswith(prefix) or not output.name.endswith(".zip"):
        raise SparkleArchiveError(
            f"archive name must match bundle version: SidePulse-{version}-<architecture>.zip"
        )
    architecture = output.name[len(prefix) : -len(".zip")]
    if _ARCHITECTURE.fullmatch(architecture) is None or ".." in architecture:
        raise SparkleArchiveError(
            f"archive name must be SidePulse-{version}-<architecture>.zip"
        )
    return architecture


def _validate_output_path(*, app: Path, output: Path) -> Path:
    output_parent = _require_plain_directory(output.parent, label="archive output parent")
    unresolved_output = output_parent / output.name
    try:
        unresolved_output.relative_to(app)
    except ValueError:
        pass
    else:
        raise SparkleArchiveError("archive output must not be inside SidePulse.app")
    try:
        metadata = unresolved_output.lstat()
    except FileNotFoundError:
        return unresolved_output
    except OSError as exc:
        raise SparkleArchiveError(f"archive output cannot be inspected: {output}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise SparkleArchiveError(f"archive output must not be a symlink: {output}")
    raise SparkleArchiveError(f"archive output already exists: {output}")


def _validate_zip_member_name(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise SparkleArchiveError(f"archive contains an unsafe member: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SparkleArchiveError(f"archive contains an unsafe member: {name!r}")
    if path.parts[0] != EXPECTED_APP_NAME:
        raise SparkleArchiveError(f"archive contains an unrelated top-level member: {name}")
    return path


def validate_archive(*, archive: Path, app: Path | None = None) -> None:
    """Validate safe members and, when supplied, exact source file bytes."""

    try:
        with zipfile.ZipFile(archive) as bundle_zip:
            members: dict[str, zipfile.ZipInfo] = {}
            for info in bundle_zip.infolist():
                path = _validate_zip_member_name(info.filename)
                normalized = path.as_posix().rstrip("/")
                if normalized in members:
                    raise SparkleArchiveError(f"archive repeats a member: {normalized}")
                members[normalized] = info
                archived_type = stat.S_IFMT(info.external_attr >> 16)
                if archived_type == stat.S_IFLNK:
                    try:
                        target = bundle_zip.read(info).decode("utf-8")
                    except UnicodeError:
                        raise SparkleArchiveError(
                            f"archive contains an invalid symlink target: {normalized}"
                        ) from None
                    if (
                        not target
                        or target.startswith("/")
                        or "\\" in target
                        or "\x00" in target
                    ):
                        raise SparkleArchiveError(
                            f"archive contains an unsafe symlink target: {normalized}"
                        )
                    resolved_target = posixpath.normpath(
                        posixpath.join(posixpath.dirname(normalized), target)
                    )
                    if resolved_target != EXPECTED_APP_NAME and not resolved_target.startswith(
                        f"{EXPECTED_APP_NAME}/"
                    ):
                        raise SparkleArchiveError(
                            f"archive contains an escaping symlink: {normalized}"
                        )
            if not members or EXPECTED_APP_NAME not in members:
                raise SparkleArchiveError("archive does not contain SidePulse.app as its only root")
            corrupt = bundle_zip.testzip()
            if corrupt is not None:
                raise SparkleArchiveError(f"archive member failed CRC validation: {corrupt}")
            if app is not None:
                for source in sorted(app.rglob("*"), key=lambda item: item.as_posix()):
                    if source.is_dir() and not source.is_symlink():
                        continue
                    relative = source.relative_to(app).as_posix()
                    archived_name = f"{EXPECTED_APP_NAME}/{relative}"
                    info = members.get(archived_name)
                    if info is None:
                        raise SparkleArchiveError(f"archive omitted an application member: {relative}")
                    source_metadata = source.lstat()
                    archived_type = stat.S_IFMT(info.external_attr >> 16)
                    if stat.S_ISLNK(source_metadata.st_mode):
                        if archived_type != stat.S_IFLNK:
                            raise SparkleArchiveError(f"archive did not preserve a symlink: {relative}")
                        if bundle_zip.read(info).decode("utf-8") != os.readlink(source):
                            raise SparkleArchiveError(f"archive changed a symlink target: {relative}")
                    elif not stat.S_ISREG(archived_type) or bundle_zip.read(info) != source.read_bytes():
                        raise SparkleArchiveError(f"archive changed application bytes: {relative}")
    except SparkleArchiveError:
        raise
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise SparkleArchiveError(f"archive is missing or invalid: {archive}") from exc


def package_archive(
    *,
    app: Path,
    output: Path,
    ditto: Path = DEFAULT_DITTO,
) -> Path:
    """Create one exact-name ZIP atomically from a validated SidePulse.app."""

    app_path = Path(app)
    if app_path.name != EXPECTED_APP_NAME:
        raise SparkleArchiveError(f"application bundle must be named {EXPECTED_APP_NAME}: {app_path}")
    resolved_app = _require_plain_directory(app_path, label="application bundle")
    _validate_bundle_tree(resolved_app)
    version = _bundle_version(resolved_app)
    output_path = Path(output)
    _validate_output_identity(output_path, version=version)
    resolved_output = _validate_output_path(app=resolved_app, output=output_path)
    ditto_path = _require_executable(Path(ditto))

    with tempfile.TemporaryDirectory(
        dir=resolved_output.parent,
        prefix=f".{resolved_output.name}.",
    ) as staging_directory:
        staged_archive = Path(staging_directory) / resolved_output.name
        try:
            result = subprocess.run(
                [
                    str(ditto_path),
                    "-c",
                    "-k",
                    "--norsrc",
                    "--keepParent",
                    str(resolved_app),
                    str(staged_archive),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            raise SparkleArchiveError("ditto timed out after 300 seconds") from None
        except OSError as exc:
            raise SparkleArchiveError(f"ditto could not start: {exc}") from None
        if result.returncode != 0:
            raise SparkleArchiveError(f"ditto failed with exit code {result.returncode}")
        if not staged_archive.is_file():
            raise SparkleArchiveError(f"ditto reported success without creating: {staged_archive.name}")
        validate_archive(archive=staged_archive, app=resolved_app)
        os.replace(staged_archive, resolved_output)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ditto", type=Path, default=DEFAULT_DITTO, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        output = package_archive(app=args.app, output=args.output, ditto=args.ditto)
    except SparkleArchiveError as exc:
        print(f"Sparkle archive packaging failed: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
