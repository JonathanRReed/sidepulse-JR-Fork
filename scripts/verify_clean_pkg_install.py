#!/usr/bin/env python3
"""Verify a clean install contains and runs the exact JR-Bar candidate."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
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
            return line.split("=", 1)[1].strip()
    raise ValueError("clean-installed application has no TeamIdentifier")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--pkg", type=Path, required=True)
    parser.add_argument("--app", type=Path, default=Path("/Applications/SidePulse.app"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        candidate = release_evidence.load_json_object(
            args.candidate.resolve(strict=True),
            label="candidate",
        )
        candidate_app = candidate.get("app")
        if not isinstance(candidate_app, dict):
            raise ValueError("candidate app record is missing")
        installed_sha256 = release_evidence.sha256_tree(args.app)
        if installed_sha256 != candidate_app.get("sha256"):
            raise ValueError("clean-installed app does not match the exact candidate")
        info = plistlib.loads((args.app / "Contents" / "Info.plist").read_bytes())
        if not isinstance(info, dict):
            raise ValueError("clean-installed Info.plist is not a dictionary")
        if info.get("CFBundleIdentifier") != EXPECTED_BUNDLE_IDENTIFIER:
            raise ValueError("clean-installed bundle identifier changed")
        if _team_identifier(args.app) != candidate.get("team_identifier"):
            raise ValueError("clean-installed signing team changed")
        subprocess.run(
            ["/usr/sbin/spctl", "-a", "-vv", str(args.app)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        subprocess.run(
            ["/usr/sbin/pkgutil", "--pkg-info", PACKAGE_IDENTIFIER],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        executable = args.app / "Contents" / "MacOS" / "SidePulse"
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("clean-installed executable is missing")
        subprocess.run(
            [str(executable), "doctor"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        integrations = subprocess.run(
            [str(executable), "integrations", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        integration_status = json.loads(integrations.stdout)
        if not (
            isinstance(integration_status, dict)
            and isinstance(integration_status.get("t3code"), dict)
            and "codexbar" not in integration_status
        ):
            raise ValueError("clean-installed integration status is malformed")
        receipt = release_evidence.create_receipt(
            root=args.root,
            candidate=candidate,
            kind="clean-install",
            tool="verify_clean_pkg_install.py",
            input_path=args.pkg,
            output_text="clean PKG installation verified",
            details={"installed_app_sha256": installed_sha256},
        )
        release_evidence.write_json(args.output, receipt)
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as exc:
        print(f"clean PKG installation verification failed: {exc}", file=sys.stderr)
        return 1
    print("clean PKG installation verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
