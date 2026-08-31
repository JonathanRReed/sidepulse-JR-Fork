#!/usr/bin/env python3
"""Capture a fail-closed pre-upgrade JR Bar installation baseline."""

from __future__ import annotations

import argparse
import hashlib
import plistlib
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

try:
    from scripts import release_evidence
except ImportError:  # Direct execution adds scripts/, not the repository root.
    import release_evidence  # type: ignore[no-redef]


EXPECTED_BUNDLE_IDENTIFIER = "io.sidepulse.app"
PACKAGE_IDENTIFIER = "io.sidepulse.app"


def _team_identifier(app: Path) -> str:
    completed = subprocess.run(
        ["/usr/bin/codesign", "-dv", "--verbose=4", str(app)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    for line in completed.stderr.splitlines():
        if line.startswith("TeamIdentifier="):
            team = line.split("=", 1)[1].strip()
            if team and team != "not set":
                return team
    raise ValueError("pre-upgrade application has no TeamIdentifier")


def capture_baseline(
    *,
    app: Path,
    settings: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    team_reader: Callable[[Path], str] = _team_identifier,
) -> dict[str, object]:
    try:
        app_metadata = app.lstat()
    except FileNotFoundError as exc:
        raise ValueError("pre-upgrade application is missing") from exc
    if stat.S_ISLNK(app_metadata.st_mode) or not stat.S_ISDIR(app_metadata.st_mode):
        raise ValueError("pre-upgrade application must be a real app directory")
    executable = app / "Contents" / "MacOS" / "SidePulse"
    if not executable.is_file():
        raise ValueError("pre-upgrade application executable is missing")
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    if not isinstance(info, dict):
        raise ValueError("pre-upgrade Info.plist is not a dictionary")
    if info.get("CFBundleIdentifier") != EXPECTED_BUNDLE_IDENTIFIER:
        raise ValueError("pre-upgrade bundle identifier is invalid")
    version = info.get("CFBundleShortVersionString")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("pre-upgrade application version is missing")
    package_info = runner(
        ["/usr/sbin/pkgutil", "--pkg-info", PACKAGE_IDENTIFIER],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if package_info.returncode != 0 or f"package-id: {PACKAGE_IDENTIFIER}" not in package_info.stdout:
        raise ValueError("pre-upgrade package receipt is missing")
    if not settings.is_file():
        raise ValueError("pre-upgrade settings are missing")
    return {
        "schema_version": 1,
        "package_identifier": PACKAGE_IDENTIFIER,
        "bundle_identifier": EXPECTED_BUNDLE_IDENTIFIER,
        "version": version.strip(),
        "team_identifier": team_reader(app),
        "app_sha256": release_evidence.sha256_tree(app),
        "settings_sha256": release_evidence.sha256_file(settings),
        "package_receipt_sha256": hashlib.sha256(package_info.stdout.encode("utf-8", errors="replace")).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        release_evidence.write_json(
            args.output,
            capture_baseline(app=args.app, settings=args.settings),
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"pre-upgrade baseline capture failed: {exc}", file=sys.stderr)
        return 1
    print(f"pre-upgrade baseline captured: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
