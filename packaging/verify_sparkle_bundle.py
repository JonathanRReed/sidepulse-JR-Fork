#!/usr/bin/env python3
"""Verify the exact embedded Sparkle framework and all of its nested code."""

from __future__ import annotations

import argparse
import base64
import binascii
import plistlib
import re
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

SPARKLE_VERSION = "2.9.6"
SPARKLE_BUILD = "2061"
SPARKLE_FEED_URL = (
    "https://github.com/JonathanRReed/sidepulse-JR-Fork/"
    "releases/download/updates/appcast.xml"
)
FORBIDDEN_INFO_KEYS = frozenset(
    {
        "SUEnableAutomaticChecks",
        "SUEnableDownloaderService",
        "SUEnableInstallerLauncherService",
        "com.apple.security.temporary-exception.mach-lookup.global-name",
    }
)
REQUIRED_SYMLINKS = {
    "Versions/Current": "B",
    "Sparkle": "Versions/Current/Sparkle",
    "Autoupdate": "Versions/Current/Autoupdate",
    "Updater.app": "Versions/Current/Updater.app",
    "Resources": "Versions/Current/Resources",
    "XPCServices": "Versions/Current/XPCServices",
}
CommandRunner = Callable[..., subprocess.CompletedProcess]


class SparkleBundleError(ValueError):
    """The embedded Sparkle bundle did not satisfy the release contract."""


@dataclass(frozen=True, slots=True)
class VerificationReport:
    version: str
    team_identifier: str | None
    verified_targets: tuple[Path, ...]


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve(strict=False)
    base = root.resolve(strict=False)
    return resolved == base or base in resolved.parents


def _require_regular(path: Path, *, label: str, executable: bool = False) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SparkleBundleError(f"missing required Sparkle member: {label}") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise SparkleBundleError(f"unsafe required Sparkle member: {label}")
    if metadata.st_nlink != 1:
        raise SparkleBundleError(f"hard-linked required Sparkle member: {label}")
    if executable and not metadata.st_mode & 0o111:
        raise SparkleBundleError(f"Sparkle executable bit is missing: {label}")


def _require_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SparkleBundleError(f"missing required Sparkle member: {label}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise SparkleBundleError(f"unsafe required Sparkle member: {label}")


def _read_plist(path: Path, *, label: str) -> dict[str, object]:
    _require_regular(path, label=label)
    try:
        value = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise SparkleBundleError(f"invalid plist for {label}") from exc
    if not isinstance(value, dict):
        raise SparkleBundleError(f"invalid plist root for {label}")
    return value


def _validate_main_info(app: Path) -> None:
    info = _read_plist(app / "Contents/Info.plist", label="main Info.plist")
    expected: dict[str, object] = {
        "SUFeedURL": SPARKLE_FEED_URL,
        "SURequireSignedFeed": True,
        "SUVerifyUpdateBeforeExtraction": True,
    }
    for key, value in expected.items():
        if info.get(key) != value or type(info.get(key)) is not type(value):
            raise SparkleBundleError(f"invalid {key} in main Info.plist")
    public_key = info.get("SUPublicEDKey")
    if not isinstance(public_key, str):
        raise SparkleBundleError("missing SUPublicEDKey in main Info.plist")
    try:
        decoded_key = base64.b64decode(public_key, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SparkleBundleError("invalid SUPublicEDKey in main Info.plist") from exc
    if len(decoded_key) != 32:
        raise SparkleBundleError("invalid SUPublicEDKey in main Info.plist")
    forbidden = sorted(FORBIDDEN_INFO_KEYS.intersection(info))
    if forbidden:
        raise SparkleBundleError(f"forbidden Sparkle Info.plist key: {forbidden[0]}")


def _validate_symlinks(framework: Path) -> None:
    for member in framework.rglob("*"):
        if not member.is_symlink():
            continue
        try:
            target = member.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SparkleBundleError(f"broken Sparkle symlink: {member}") from exc
        if not _inside(target, framework):
            raise SparkleBundleError(f"Sparkle symlink escapes framework: {member}")
    for relative, expected_target in REQUIRED_SYMLINKS.items():
        member = framework / relative
        try:
            metadata = member.lstat()
        except OSError as exc:
            raise SparkleBundleError(f"missing Sparkle symlink: {relative}") from exc
        if not stat.S_ISLNK(metadata.st_mode):
            raise SparkleBundleError(f"required Sparkle alias is not a symlink: {relative}")
        try:
            actual_target = str(member.readlink())
        except OSError as exc:
            raise SparkleBundleError(f"cannot read Sparkle symlink: {relative}") from exc
        if actual_target != expected_target:
            raise SparkleBundleError(
                f"unexpected Sparkle symlink target: {relative} -> {actual_target}"
            )


def _combined_output(completed: subprocess.CompletedProcess) -> str:
    chunks: list[str] = []
    for value in (completed.stdout, completed.stderr):
        if isinstance(value, bytes):
            chunks.append(value.decode("utf-8", errors="replace"))
        elif isinstance(value, str):
            chunks.append(value)
    return "\n".join(chunks)


def _run(
    runner: CommandRunner,
    argv: list[str],
    *,
    text: bool,
) -> subprocess.CompletedProcess:
    try:
        return runner(
            argv,
            capture_output=True,
            text=text,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SparkleBundleError(f"verification tool failed: {argv[0]}") from exc


def _verify_signature(
    target: Path,
    *,
    runner: CommandRunner,
    production: bool,
    expected_team: str | None,
) -> str | None:
    verified = _run(
        runner,
        [
            "/usr/bin/codesign",
            "--verify",
            "--strict",
            "--verbose=2",
            str(target),
        ],
        text=True,
    )
    if verified.returncode != 0:
        raise SparkleBundleError(f"invalid nested signature: {target}")

    details = _run(
        runner,
        ["/usr/bin/codesign", "-dv", "--verbose=4", str(target)],
        text=True,
    )
    if details.returncode != 0:
        raise SparkleBundleError(f"cannot inspect nested signature: {target}")
    output = _combined_output(details)
    runtime_flags = re.search(
        r"^CodeDirectory\b.*\bflags=0x[0-9a-fA-F]+\([^)]*\bruntime\b[^)]*\)(?:\s|$)",
        output,
        re.MULTILINE,
    )
    if runtime_flags is None:
        raise SparkleBundleError(f"nested code lacks hardened runtime: {target}")
    team_match = re.search(r"^TeamIdentifier=(.+)$", output, re.MULTILINE)
    team = team_match.group(1).strip() if team_match else None
    if team == "not set":
        team = None
    if production and team != expected_team:
        raise SparkleBundleError(f"nested TeamIdentifier mismatch: {target}")

    entitlements = _run(
        runner,
        ["/usr/bin/codesign", "-d", "--entitlements", ":-", str(target)],
        text=False,
    )
    if entitlements.returncode != 0:
        raise SparkleBundleError(f"cannot inspect nested entitlements: {target}")
    payload = entitlements.stdout
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if payload:
        try:
            value = plistlib.loads(payload)
        except plistlib.InvalidFileException as exc:
            raise SparkleBundleError(f"invalid nested entitlements: {target}") from exc
        if value != {}:
            raise SparkleBundleError(f"unreviewed entitlements on nested code: {target}")
    return team


def _dependency_is_contained(dependency: str, *, binary: Path, app: Path) -> bool:
    if dependency.startswith(("/System/Library/", "/usr/lib/")):
        return True
    if dependency == "@rpath/Sparkle.framework/Versions/B/Sparkle":
        return True
    if dependency.startswith("@loader_path/"):
        relative = dependency.removeprefix("@loader_path/")
        return _inside(binary.parent / relative, app)
    if dependency.startswith("@executable_path/"):
        relative = dependency.removeprefix("@executable_path/")
        return _inside(app / "Contents/MacOS" / relative, app)
    if dependency.startswith("/"):
        return _inside(Path(dependency), app)
    return False


def _rpath_is_contained(rpath: str, *, binary: Path, app: Path) -> bool:
    if rpath == "@loader_path" or rpath.startswith("@loader_path/"):
        relative = rpath.removeprefix("@loader_path").lstrip("/")
        return _inside(binary.parent / relative, app)
    if rpath == "@executable_path" or rpath.startswith("@executable_path/"):
        relative = rpath.removeprefix("@executable_path").lstrip("/")
        return _inside(app / "Contents/MacOS" / relative, app)
    if rpath.startswith("/"):
        return _inside(Path(rpath), app)
    return False


def _verify_linkage(binary: Path, *, app: Path, runner: CommandRunner) -> None:
    linked = _run(runner, ["/usr/bin/otool", "-L", str(binary)], text=True)
    if linked.returncode != 0:
        raise SparkleBundleError(f"cannot inspect Sparkle dependencies: {binary}")
    for line in _combined_output(linked).splitlines():
        stripped = line.strip()
        if " (" not in stripped:
            continue
        dependency = stripped.split(" (", 1)[0]
        if not _dependency_is_contained(dependency, binary=binary, app=app):
            raise SparkleBundleError(f"external dependency on nested code: {dependency}")

    load_commands = _run(runner, ["/usr/bin/otool", "-l", str(binary)], text=True)
    if load_commands.returncode != 0:
        raise SparkleBundleError(f"cannot inspect Sparkle rpaths: {binary}")
    awaiting_rpath = False
    for line in _combined_output(load_commands).splitlines():
        stripped = line.strip()
        if stripped == "cmd LC_RPATH":
            awaiting_rpath = True
            continue
        if awaiting_rpath and stripped.startswith("path "):
            rpath = stripped.removeprefix("path ").split(" (offset", 1)[0]
            if not _rpath_is_contained(rpath, binary=binary, app=app):
                raise SparkleBundleError(f"external rpath on nested code: {rpath}")
            awaiting_rpath = False


def verify_sparkle_bundle(
    app: Path,
    *,
    production: bool,
    expected_team: str | None = None,
    runner: CommandRunner = subprocess.run,
) -> VerificationReport:
    try:
        app_root = Path(app).resolve(strict=True)
    except OSError as exc:
        raise SparkleBundleError(f"missing application bundle: {app}") from exc
    if app_root.suffix.casefold() != ".app" or not app_root.is_dir():
        raise SparkleBundleError("verification target must be an application bundle")
    if production and (not isinstance(expected_team, str) or not expected_team.strip()):
        raise SparkleBundleError("production verification requires an expected team")

    _validate_main_info(app_root)
    framework = app_root / "Contents/Frameworks/Sparkle.framework"
    version_root = framework / "Versions/B"
    _require_directory(framework, label="Sparkle.framework")
    _validate_symlinks(framework)

    framework_info = _read_plist(
        version_root / "Resources/Info.plist",
        label="Sparkle framework Info.plist",
    )
    version = framework_info.get("CFBundleShortVersionString")
    if version != SPARKLE_VERSION:
        raise SparkleBundleError(f"expected {SPARKLE_VERSION}, found {version!r}")
    if framework_info.get("CFBundleVersion") != SPARKLE_BUILD:
        raise SparkleBundleError(
            f"expected Sparkle build {SPARKLE_BUILD}, found {framework_info.get('CFBundleVersion')!r}"
        )
    _require_regular(
        app_root / "Contents/Resources/ThirdPartyLicenses/Sparkle.txt",
        label="Sparkle license",
    )

    binaries = (
        version_root / "Sparkle",
        version_root / "Autoupdate",
        version_root / "Updater.app/Contents/MacOS/Updater",
        version_root / "XPCServices/Downloader.xpc/Contents/MacOS/Downloader",
        version_root / "XPCServices/Installer.xpc/Contents/MacOS/Installer",
    )
    bundles = (
        version_root / "XPCServices/Downloader.xpc",
        version_root / "XPCServices/Installer.xpc",
        version_root / "Updater.app",
        framework,
    )
    for binary in binaries:
        _require_regular(
            binary,
            label=str(binary.relative_to(framework)),
            executable=True,
        )
    for bundle in bundles:
        _require_directory(bundle, label=str(bundle.relative_to(app_root)))

    observed_team: str | None = None
    targets = (*binaries, *bundles)
    for target in targets:
        team = _verify_signature(
            target,
            runner=runner,
            production=production,
            expected_team=expected_team,
        )
        if observed_team is None and team is not None:
            observed_team = team
    for binary in binaries:
        _verify_linkage(binary, app=app_root, runner=runner)
    return VerificationReport(SPARKLE_VERSION, observed_team, targets)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--expected-team")
    args = parser.parse_args(argv)
    try:
        report = verify_sparkle_bundle(
            args.app,
            production=args.production,
            expected_team=args.expected_team,
        )
    except (OSError, SparkleBundleError, subprocess.SubprocessError) as exc:
        print(f"Sparkle bundle verification failed: {exc}")
        return 1
    mode = "production" if args.production else "local"
    print(
        f"verified Sparkle {report.version}: "
        f"{len(report.verified_targets)} nested targets ({mode})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
