#!/usr/bin/env python3
"""Prepare the pinned Sparkle distribution for JR-Bar packaging."""

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import shutil
import stat
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

try:
    from scripts.release_artifact_contract import (
        SPARKLE_ARCHIVE_SHA256,
        SPARKLE_ARCHIVE_URL,
        SPARKLE_VERSION,
    )
except ImportError:  # Direct execution adds scripts/, not the repository root.
    from release_artifact_contract import (  # type: ignore[no-redef]
        SPARKLE_ARCHIVE_SHA256,
        SPARKLE_ARCHIVE_URL,
        SPARKLE_VERSION,
    )

_STAGING_NAME = ".prepare-sparkle"
_DOWNLOADED_ARCHIVE_NAME = "Sparkle.tar.xz"
_FRAMEWORK_ROOT = PurePosixPath("Sparkle.framework")
_REQUIRED_FRAMEWORK_MEMBERS = (
    PurePosixPath("Sparkle.framework/Versions/B/Resources/Info.plist"),
    PurePosixPath("Sparkle.framework/Versions/B/Sparkle"),
)
_REQUIRED_DISTRIBUTION_MEMBERS = (
    PurePosixPath("bin/generate_appcast"),
    PurePosixPath("bin/generate_keys"),
    PurePosixPath("bin/sign_update"),
    PurePosixPath("LICENSE"),
)
_OUTPUT_NAMES = ("Sparkle.framework", "bin", "LICENSE")


class SparklePreparationError(RuntimeError):
    """Raised when the pinned Sparkle distribution cannot be prepared safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SparklePreparationError(f"cannot read Sparkle archive {path}: {exc}") from None
    return digest.hexdigest()


def _verify_digest(path: Path) -> None:
    actual = _sha256(path)
    if actual != SPARKLE_ARCHIVE_SHA256:
        raise SparklePreparationError(
            "Sparkle archive SHA-256 mismatch: "
            f"expected {SPARKLE_ARCHIVE_SHA256}, received {actual}"
        )


def _normalize_member_name(name: str) -> PurePosixPath | None:
    if not name or "\x00" in name:
        raise SparklePreparationError(f"unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise SparklePreparationError(f"unsafe archive path: {name!r}")
    parts: list[str] = []
    for part in path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise SparklePreparationError(f"unsafe archive path: {name!r}")
        parts.append(part)
    return PurePosixPath(*parts) if parts else None


def _normalized_symlink_target(path: PurePosixPath, target: str) -> PurePosixPath:
    if not target or "\x00" in target:
        raise SparklePreparationError(
            f"unsafe symbolic link target for {path.as_posix()}: {target!r}"
        )
    link = PurePosixPath(target)
    if link.is_absolute():
        raise SparklePreparationError(
            f"unsafe symbolic link target for {path.as_posix()}: {target!r}"
        )
    parts = list(path.parent.parts)
    for part in link.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise SparklePreparationError(
                    f"unsafe symbolic link target for {path.as_posix()}: {target!r}"
                )
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise SparklePreparationError(
            f"unsafe symbolic link target for {path.as_posix()}: {target!r}"
        )
    normalized = PurePosixPath(*parts)
    if path.parts[0] == _FRAMEWORK_ROOT.parts[0] and normalized.parts[0] != path.parts[0]:
        raise SparklePreparationError(
            f"unsafe symbolic link target for {path.as_posix()}: {target!r}"
        )
    return normalized


def _validate_members(archive: tarfile.TarFile) -> dict[PurePosixPath, tarfile.TarInfo]:
    members: dict[PurePosixPath, tarfile.TarInfo] = {}
    symlinks: set[PurePosixPath] = set()
    for member in archive.getmembers():
        path = _normalize_member_name(member.name)
        if path is None:
            continue
        if path in members:
            raise SparklePreparationError(f"duplicate archive member: {path.as_posix()}")
        if member.islnk():
            raise SparklePreparationError(
                f"hard link archive member is not permitted: {path.as_posix()}"
            )
        if not (member.isdir() or member.isfile() or member.issym()):
            raise SparklePreparationError(
                f"unsupported archive member type: {path.as_posix()}"
            )
        if member.mode & 0o7000:
            raise SparklePreparationError(
                f"unsupported special permissions on archive member: {path.as_posix()}"
            )
        if member.issym():
            _normalized_symlink_target(path, member.linkname)
            symlinks.add(path)
        members[path] = member

    for path in members:
        for parent in path.parents:
            if parent == PurePosixPath("."):
                continue
            if parent in symlinks:
                raise SparklePreparationError(
                    f"archive member descends through symbolic link {parent.as_posix()}: "
                    f"{path.as_posix()}"
                )
            parent_member = members.get(parent)
            if parent_member is not None and not parent_member.isdir():
                raise SparklePreparationError(
                    f"archive member parent is not a directory {parent.as_posix()}: "
                    f"{path.as_posix()}"
                )

    for path in _REQUIRED_FRAMEWORK_MEMBERS:
        member = members.get(path)
        if member is None or not member.isfile():
            raise SparklePreparationError(
                f"missing required Sparkle framework member: {path.as_posix()}"
            )
    for path in _REQUIRED_DISTRIBUTION_MEMBERS:
        member = members.get(path)
        if member is None or not member.isfile():
            raise SparklePreparationError(
                f"missing required Sparkle distribution member: {path.as_posix()}"
            )
    return members


def _is_selected(path: PurePosixPath) -> bool:
    if path == _FRAMEWORK_ROOT or _FRAMEWORK_ROOT in path.parents:
        return True
    return path == PurePosixPath("bin") or path in _REQUIRED_DISTRIBUTION_MEMBERS


def _target_path(root: Path, path: PurePosixPath) -> Path:
    return root.joinpath(*path.parts)


def _extract_selected(
    archive: tarfile.TarFile,
    members: dict[PurePosixPath, tarfile.TarInfo],
    destination: Path,
) -> None:
    selected = {path: member for path, member in members.items() if _is_selected(path)}
    directories = sorted(
        ((path, member) for path, member in selected.items() if member.isdir()),
        key=lambda item: len(item[0].parts),
    )
    files = sorted(
        ((path, member) for path, member in selected.items() if member.isfile()),
        key=lambda item: item[0].as_posix(),
    )
    links = sorted(
        ((path, member) for path, member in selected.items() if member.issym()),
        key=lambda item: item[0].as_posix(),
    )

    for path, _member in directories:
        _target_path(destination, path).mkdir(parents=True, exist_ok=True)
    for path, member in files:
        target = _target_path(destination, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise SparklePreparationError(f"cannot read archive member: {path.as_posix()}")
        try:
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            os.chmod(target, member.mode & 0o777)
        except OSError as exc:
            raise SparklePreparationError(
                f"cannot extract archive member {path.as_posix()}: {exc}"
            ) from None
    for path, member in links:
        target = _target_path(destination, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.symlink_to(member.linkname)
        except OSError as exc:
            raise SparklePreparationError(
                f"cannot create symbolic link {path.as_posix()}: {exc}"
            ) from None
    for path, member in reversed(directories):
        try:
            os.chmod(_target_path(destination, path), member.mode & 0o777)
        except OSError as exc:
            raise SparklePreparationError(
                f"cannot preserve directory permissions for {path.as_posix()}: {exc}"
            ) from None


def _verify_prepared_distribution(root: Path) -> None:
    if {path.name for path in root.iterdir()} != set(_OUTPUT_NAMES):
        raise SparklePreparationError("prepared Sparkle distribution contains unexpected files")
    framework = root / "Sparkle.framework"
    framework_root = framework.resolve(strict=True)
    for path in framework.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            path.resolve(strict=True).relative_to(framework_root)
        except (OSError, RuntimeError, ValueError):
            raise SparklePreparationError(
                f"unsafe or unresolved symbolic link in Sparkle.framework: {path.relative_to(root)}"
            ) from None

    plist_path = framework / "Resources" / "Info.plist"
    try:
        with plist_path.open("rb") as source:
            info = plistlib.load(source)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise SparklePreparationError(f"cannot read Sparkle framework version: {exc}") from None
    actual_version = info.get("CFBundleShortVersionString")
    if actual_version != SPARKLE_VERSION:
        raise SparklePreparationError(
            f"Sparkle framework version mismatch: expected {SPARKLE_VERSION}, "
            f"received {actual_version!r}"
        )

    framework_binary = framework / "Versions" / "B" / "Sparkle"
    if not framework_binary.is_file() or framework_binary.is_symlink():
        raise SparklePreparationError("missing required Sparkle framework member: framework binary")
    if framework_binary.stat().st_mode & 0o111 == 0:
        raise SparklePreparationError("Sparkle framework binary is not executable")
    for path in _REQUIRED_DISTRIBUTION_MEMBERS:
        target = _target_path(root, path)
        if not target.is_file() or target.is_symlink():
            raise SparklePreparationError(
                f"missing required Sparkle distribution member: {path.as_posix()}"
            )
        if path.parts[0] == "bin" and target.stat().st_mode & 0o111 == 0:
            raise SparklePreparationError(
                f"Sparkle distribution tool is not executable: {path.as_posix()}"
            )


def _download_archive(destination: Path) -> None:
    try:
        with urllib.request.urlopen(SPARKLE_ARCHIVE_URL, timeout=60) as response:
            with destination.open("xb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
    except (OSError, urllib.error.URLError) as exc:
        raise SparklePreparationError(
            f"failed to download Sparkle {SPARKLE_VERSION} from the pinned URL: {exc}"
        ) from None


def _snapshot_supplied_archive(source: Path, destination: Path) -> None:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise SparklePreparationError("this platform cannot safely open a supplied Sparkle archive")
    descriptor: int | None = None
    try:
        descriptor = os.open(source, os.O_RDONLY | no_follow)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SparklePreparationError(f"Sparkle archive is not a regular file: {source}")
        with os.fdopen(descriptor, "rb") as input_stream:
            descriptor = None
            with destination.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        os.chmod(destination, 0o600)
    except SparklePreparationError:
        raise
    except OSError as exc:
        raise SparklePreparationError(
            f"cannot snapshot supplied Sparkle archive {source}: {exc}"
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _prepare_output(output: Path) -> bool:
    if output.is_symlink():
        raise SparklePreparationError(f"output directory must not be a symbolic link: {output}")
    if output.exists():
        if not output.is_dir():
            raise SparklePreparationError(f"output is not a directory: {output}")
        if any(output.iterdir()):
            raise SparklePreparationError(f"output directory must be empty: {output}")
        return False
    if not output.parent.is_dir():
        raise SparklePreparationError(f"output parent directory does not exist: {output.parent}")
    try:
        output.mkdir(mode=0o700)
    except OSError as exc:
        raise SparklePreparationError(f"cannot create output directory {output}: {exc}") from None
    return True


def prepare_sparkle(output: Path, *, archive: Path | None = None) -> Path:
    """Verify and selectively extract the pinned Sparkle distribution."""

    output = Path(output)
    supplied_archive: Path | None = None
    if archive is not None:
        supplied_archive = Path(archive)
        try:
            source_mode = supplied_archive.lstat().st_mode
        except OSError as exc:
            raise SparklePreparationError(
                f"cannot inspect supplied Sparkle archive {supplied_archive}: {exc}"
            ) from None
        if stat.S_ISLNK(source_mode):
            raise SparklePreparationError(
                "supplied Sparkle archive must not be a symbolic link"
            )
        if not stat.S_ISREG(source_mode):
            raise SparklePreparationError(f"Sparkle archive is not a file: {supplied_archive}")

    created_output = _prepare_output(output)
    staging = output / _STAGING_NAME
    extracted = staging / "distribution"
    published: list[Path] = []
    succeeded = False
    try:
        staging.mkdir(mode=0o700)
        extracted.mkdir(mode=0o700)
        selected_archive = staging / _DOWNLOADED_ARCHIVE_NAME
        if supplied_archive is None:
            _download_archive(selected_archive)
        else:
            _snapshot_supplied_archive(supplied_archive, selected_archive)
        _verify_digest(selected_archive)
        try:
            with tarfile.open(selected_archive, mode="r:xz") as source:
                members = _validate_members(source)
                _extract_selected(source, members, extracted)
        except (OSError, tarfile.TarError) as exc:
            raise SparklePreparationError(f"cannot read Sparkle archive: {exc}") from None
        _verify_prepared_distribution(extracted)
        for name in _OUTPUT_NAMES:
            destination = output / name
            (extracted / name).replace(destination)
            published.append(destination)
        succeeded = True
        return output
    finally:
        _remove_path(staging)
        if not succeeded:
            for path in reversed(published):
                _remove_path(path)
            if created_output and output.is_dir() and not any(output.iterdir()):
                output.rmdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args(argv)
    try:
        destination = prepare_sparkle(args.output, archive=args.archive)
    except (OSError, SparklePreparationError) as exc:
        print(f"Sparkle dependency preparation failed: {exc}", file=sys.stderr)
        return 2
    print(f"Prepared Sparkle {SPARKLE_VERSION} at {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
