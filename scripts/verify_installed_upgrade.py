#!/usr/bin/env python3
"""Verify an installed SidePulse upgrade preserved identity and user state."""

from __future__ import annotations

import argparse
import json
import plistlib
import subprocess
from pathlib import Path

EXPECTED_BUNDLE_IDENTIFIER = "io.sidepulse.app"


def _json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"settings document is not an object: {path}")
    return value


def _bundle_info(app: Path) -> dict:
    info_path = app / "Contents" / "Info.plist"
    value = plistlib.loads(info_path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("installed Info.plist is not a dictionary")
    return value


def _team_identifier(app: Path) -> str:
    completed = subprocess.run(
        ["/usr/bin/codesign", "-dv", "--verbose=4", str(app)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("installed application signature is invalid")
    for line in completed.stderr.splitlines():
        if line.startswith("TeamIdentifier="):
            return line.split("=", 1)[1].strip()
    raise ValueError("installed application has no TeamIdentifier")


def _preserved(before: dict[str, object], after: dict[str, object]) -> bool:
    return all(
        key == "settings_schema_version"
        or (key in after and after[key] == value)
        for key, value in before.items()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app",
        type=Path,
        default=Path("/Applications/SidePulse.app"),
    )
    parser.add_argument("--before-settings", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--expected-team", required=True)
    args = parser.parse_args()

    try:
        info = _bundle_info(args.app)
        if info.get("CFBundleIdentifier") != EXPECTED_BUNDLE_IDENTIFIER:
            raise ValueError("installed bundle identifier changed")
        if _team_identifier(args.app) != args.expected_team:
            raise ValueError("installed signing team changed")
        before = _json_object(args.before_settings)
        after = _json_object(args.settings)
        if not _preserved(before, after):
            raise ValueError("upgrade removed or changed an existing settings field")
        if type(after.get("settings_schema_version")) is not int:
            raise ValueError("upgraded settings have no valid schema version")
        subprocess.run(
            ["/usr/sbin/spctl", "-a", "-vv", str(args.app)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as exc:
        print(f"installed upgrade verification failed: {exc}")
        return 1
    print("installed upgrade verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
