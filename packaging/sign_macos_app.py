#!/usr/bin/env python3
"""Sign a macOS application inside out with an explicit reviewed plan."""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

BUNDLE_SUFFIXES = frozenset({".app", ".appex", ".bundle", ".framework", ".plugin", ".xpc"})
SIGNABLE_FILE_SUFFIXES = frozenset({".dylib", ".so"})
CommandRunner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True, slots=True)
class SignPlan:
    app: Path
    nested_code: tuple[Path, ...]
    nested_bundles: tuple[Path, ...]

    @property
    def ordered_targets(self) -> tuple[Path, ...]:
        return (*self.nested_code, *self.nested_bundles, self.app)


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve(strict=False)
    base = root.resolve(strict=False)
    return resolved == base or base in resolved.parents


def _safe_regular_file(path: Path, root: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_nlink == 1
        and _inside(path, root)
    )


def _safe_bundle(path: Path, root: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and path.suffix.casefold() in BUNDLE_SUFFIXES
        and _inside(path, root)
    )


def _is_macho(path: Path, *, runner: CommandRunner = subprocess.run) -> bool:
    try:
        completed = runner(
            ["/usr/bin/file", "-b", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and "Mach-O" in (completed.stdout or "")


def build_sign_plan(
    app: Path,
    *,
    macho_detector: Callable[[Path], bool] | None = None,
) -> SignPlan:
    root = Path(app).resolve(strict=True)
    if root.suffix.casefold() != ".app" or not _safe_bundle(root, root):
        raise ValueError("signing target must be a safe .app bundle")
    detector = macho_detector or _is_macho
    nested_code = []
    nested_bundles = []
    for path in root.rglob("*"):
        if path.is_symlink():
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                raise ValueError(f"broken bundle symlink: {path}") from exc
            if not _inside(resolved, root):
                raise ValueError(f"bundle symlink escapes candidate: {path}")
            continue
        if path != root and _safe_bundle(path, root):
            nested_bundles.append(path)
            continue
        if not _safe_regular_file(path, root):
            continue
        executable = bool(path.stat().st_mode & 0o111)
        if path.suffix.casefold() in SIGNABLE_FILE_SUFFIXES or executable:
            if detector(path):
                nested_code.append(path)
    depth_key = lambda path: (-len(path.relative_to(root).parts), str(path))
    return SignPlan(
        root,
        tuple(sorted(set(nested_code), key=depth_key)),
        tuple(sorted(set(nested_bundles), key=depth_key)),
    )


def _sign_command(
    target: Path,
    *,
    identity: str,
    entitlements: Path | None,
    timestamp: bool,
) -> list[str]:
    command = [
        "/usr/bin/codesign",
        "--force",
        "--options",
        "runtime",
    ]
    if timestamp:
        command.append("--timestamp")
    if entitlements is not None:
        command.extend(["--entitlements", str(entitlements)])
    command.extend(["--sign", identity, str(target)])
    return command


def sign_macos_app(
    app: Path,
    *,
    identity: str,
    entitlements: Path,
    runner: CommandRunner = subprocess.run,
    macho_detector: Callable[[Path], bool] | None = None,
) -> SignPlan:
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError("signing identity is required")
    entitlement_path = Path(entitlements).resolve(strict=True)
    if not _safe_regular_file(entitlement_path, entitlement_path.parent):
        raise ValueError("entitlements must be a safe regular file")
    plan = build_sign_plan(app, macho_detector=macho_detector)
    timestamp = identity != "-"
    for target in (*plan.nested_code, *plan.nested_bundles):
        runner(
            _sign_command(
                target,
                identity=identity,
                entitlements=None,
                timestamp=timestamp,
            ),
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
    runner(
        _sign_command(
            plan.app,
            identity=identity,
            entitlements=entitlement_path,
            timestamp=timestamp,
        ),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    runner(
        [
            "/usr/bin/codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=2",
            str(plan.app),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return plan


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--entitlements", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        plan = sign_macos_app(
            args.app,
            identity=args.identity,
            entitlements=args.entitlements,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"macOS signing failed: {exc}")
        return 1
    print(
        "signed inside out: "
        f"{len(plan.nested_code)} code files, "
        f"{len(plan.nested_bundles)} nested bundles, 1 application"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
