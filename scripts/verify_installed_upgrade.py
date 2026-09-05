#!/usr/bin/env python3
"""Verify an installed SidePulse upgrade preserved identity, state, and launch."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
from pathlib import Path

try:
    from scripts import release_evidence
except ImportError:  # Direct execution adds scripts/, not the repository root.
    import release_evidence  # type: ignore[no-redef]

EXPECTED_BUNDLE_IDENTIFIER = "io.sidepulse.app"
EXPECTED_LAUNCH_AGENT_LABEL = "io.sidepulse.agentstatus"
COMMAND_TIMEOUT_SECONDS = 30


def require_monotonic_upgrade(previous_version: str, candidate_version: str) -> None:
    try:
        release_evidence.require_strict_version_upgrade(previous_version, candidate_version)
    except release_evidence.EvidenceError as exc:
        raise ValueError(str(exc)) from exc


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
        timeout=COMMAND_TIMEOUT_SECONDS,
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
        key == "settings_schema_version" or (key in after and after[key] == value) for key, value in before.items()
    )


def _run_installed_smoke(app: Path) -> None:
    executable = app / "Contents" / "MacOS" / "SidePulse"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("installed SidePulse executable is missing")
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
        raise ValueError("installed integration status is malformed")
    launch_target = f"gui/{os.getuid()}/{EXPECTED_LAUNCH_AGENT_LABEL}"
    launch = subprocess.run(
        ["/bin/launchctl", "print", launch_target],
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    if launch.returncode != 0:
        raise ValueError("installed status-bar LaunchAgent is not running")
    if EXPECTED_LAUNCH_AGENT_LABEL not in launch.stdout:
        raise ValueError("LaunchAgent printout has the wrong identity")


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
    parser.add_argument("--root", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--pkg", type=Path)
    parser.add_argument("--receipt-dir", type=Path)
    parser.add_argument("--baseline", type=Path)
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
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=True,
        )
        _run_installed_smoke(args.app)
        evidence_values = (
            args.root,
            args.candidate,
            args.pkg,
            args.receipt_dir,
            args.baseline,
        )
        if any(value is not None for value in evidence_values):
            if any(value is None for value in evidence_values):
                raise ValueError("root, candidate, pkg, receipt-dir, and baseline are required together")
            candidate = release_evidence.load_json_object(
                args.candidate.resolve(strict=True),
                label="candidate",
            )
            candidate_app = candidate.get("app")
            if not isinstance(candidate_app, dict):
                raise ValueError("candidate app record is missing")
            baseline = release_evidence.load_json_object(
                args.baseline.resolve(strict=True),
                label="pre-upgrade baseline",
            )
            if baseline.get("schema_version") != 1:
                raise ValueError("pre-upgrade baseline schema is unsupported")
            if baseline.get("package_identifier") != EXPECTED_BUNDLE_IDENTIFIER:
                raise ValueError("pre-upgrade package receipt identity changed")
            if baseline.get("bundle_identifier") != EXPECTED_BUNDLE_IDENTIFIER:
                raise ValueError("pre-upgrade bundle identifier changed")
            if baseline.get("team_identifier") != args.expected_team:
                raise ValueError("pre-upgrade signing team changed")
            previous_version = baseline.get("version")
            if not isinstance(previous_version, str) or not previous_version:
                raise ValueError("pre-upgrade application version is missing")
            candidate_version = candidate.get("version")
            if not isinstance(candidate_version, str) or not candidate_version:
                raise ValueError("candidate application version is missing")
            require_monotonic_upgrade(previous_version, candidate_version)
            before_settings_sha256 = release_evidence.sha256_file(args.before_settings)
            if baseline.get("settings_sha256") != before_settings_sha256:
                raise ValueError("pre-upgrade settings do not match the captured baseline")
            previous_app_sha256 = baseline.get("app_sha256")
            previous_receipt_sha256 = baseline.get("package_receipt_sha256")
            for value, label in (
                (previous_app_sha256, "pre-upgrade app"),
                (previous_receipt_sha256, "pre-upgrade package receipt"),
            ):
                if not (
                    isinstance(value, str)
                    and len(value) == 64
                    and all(character in "0123456789abcdef" for character in value)
                ):
                    raise ValueError(f"{label} digest is invalid")
            installed_sha256 = release_evidence.sha256_tree(args.app)
            if installed_sha256 != candidate_app.get("sha256"):
                raise ValueError("installed app does not match the exact candidate")
            receipt_dir = args.receipt_dir.resolve()
            release_evidence.write_json(
                receipt_dir / "installed-upgrade.json",
                release_evidence.create_receipt(
                    root=args.root,
                    candidate=candidate,
                    kind="installed-upgrade",
                    tool="verify_installed_upgrade.py",
                    input_path=args.pkg,
                    output_text="installed upgrade verification passed",
                    details={
                        "installed_app_sha256": installed_sha256,
                        "previous_version": previous_version,
                        "previous_app_sha256": previous_app_sha256,
                        "previous_package_receipt_sha256": previous_receipt_sha256,
                    },
                ),
            )
            release_evidence.write_json(
                receipt_dir / "settings-preservation.json",
                release_evidence.create_receipt(
                    root=args.root,
                    candidate=candidate,
                    kind="settings-preservation",
                    tool="verify_installed_upgrade.py",
                    input_path=args.pkg,
                    output_text="upgrade preserved existing JR-Bar settings",
                    details={
                        "settings_state": "preserved",
                        "before_settings_sha256": before_settings_sha256,
                        "after_settings_sha256": release_evidence.sha256_file(args.settings),
                    },
                ),
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
