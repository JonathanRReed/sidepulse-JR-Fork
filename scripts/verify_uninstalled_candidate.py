#!/usr/bin/env python3
"""Verify the supported JR-Bar uninstaller removed only owned product state."""

from __future__ import annotations

import argparse
import os
import pwd
import subprocess
import sys
from pathlib import Path

try:
    from scripts import release_evidence
except ImportError:  # Direct execution adds scripts/, not the repository root.
    import release_evidence  # type: ignore[no-redef]

from sidepulse.lid_sleep import SLEEP_HELPER_SUDOERS_PATH
from sidepulse.providers import detect_provider_configs
from sidepulse.sd_eject_guard_launch import (
    SD_EJECT_GUARD_LABEL,
    SD_EJECT_GUARD_LEGACY_BINARY_NAMES,
    system_sd_eject_guard_paths,
    user_sd_eject_guard_paths,
)

DEFAULT_APP = Path("/Applications/SidePulse.app")
DEFAULT_CLI_LINK = Path("/usr/local/bin/sidepulse")
DEFAULT_RECEIPT_DIR = Path("/var/db/sidepulse")
PACKAGE_IDENTIFIER = "io.sidepulse.app"
STATUS_BAR_LABEL = "io.sidepulse.agentstatus"
LEGACY_STATUS_BAR_LABEL = "com.sidepulse.agentstatus"


def owned_file_leftovers(
    home: Path,
    *,
    system_paths: tuple[Path, ...] | None = None,
) -> tuple[Path, ...]:
    # The supported uninstaller runs in the target user's environment. Check
    # both the home-relative fallback and any active XDG data location so a
    # non-default user guard cannot escape the release evidence gate.
    user_guards = tuple(
        dict.fromkeys(
            (
                user_sd_eject_guard_paths(home),
                user_sd_eject_guard_paths(),
            )
        )
    )
    user_paths = [
        home / "Library" / "LaunchAgents" / f"{STATUS_BAR_LABEL}.plist",
        home / "Library" / "LaunchAgents" / f"{LEGACY_STATUS_BAR_LABEL}.plist",
    ]
    for user_guard in user_guards:
        user_paths.extend(
            (
                user_guard.plist_path,
                user_guard.binary_path,
                *(user_guard.binary_path.parent / name for name in SD_EJECT_GUARD_LEGACY_BINARY_NAMES),
            )
        )
    if system_paths is None:
        system_guard = system_sd_eject_guard_paths()
        selected_system_paths = (
            SLEEP_HELPER_SUDOERS_PATH,
            system_guard.plist_path,
            system_guard.binary_path,
            *(system_guard.binary_path.parent / name for name in SD_EJECT_GUARD_LEGACY_BINARY_NAMES),
        )
    else:
        selected_system_paths = system_paths
    candidates = tuple(dict.fromkeys((*user_paths, *selected_system_paths)))
    return tuple(path for path in candidates if path.exists() or path.is_symlink())


def _loaded_owned_jobs(uid: int) -> tuple[str, ...]:
    targets = (
        f"gui/{uid}/{STATUS_BAR_LABEL}",
        f"gui/{uid}/{LEGACY_STATUS_BAR_LABEL}",
        f"gui/{uid}/{SD_EJECT_GUARD_LABEL}",
        f"system/{SD_EJECT_GUARD_LABEL}",
    )
    loaded = []
    for target in targets:
        completed = subprocess.run(
            ["/bin/launchctl", "print", target],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode == 0:
            loaded.append(target)
    return tuple(loaded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--pkg", type=Path, required=True)
    parser.add_argument("--before-settings", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--cli-link", type=Path, default=DEFAULT_CLI_LINK)
    parser.add_argument("--receipt-dir", type=Path, default=DEFAULT_RECEIPT_DIR)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--user", required=True)
    args = parser.parse_args()

    try:
        if args.app.exists() or args.app.is_symlink():
            raise ValueError("the supported uninstaller left the application installed")
        if args.receipt_dir.exists() or args.receipt_dir.is_symlink():
            raise ValueError("the supported uninstaller left JR-Bar setup receipts")
        if args.cli_link.is_symlink():
            target = os.readlink(args.cli_link)
            if target == str(args.app / "Contents" / "MacOS" / "SidePulse"):
                raise ValueError("the supported uninstaller left its owned CLI link")
        package_info = subprocess.run(
            ["/usr/sbin/pkgutil", "--pkg-info", PACKAGE_IDENTIFIER],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if package_info.returncode == 0:
            raise ValueError("the supported uninstaller left the package receipt")
        if release_evidence.sha256_file(args.before_settings) != release_evidence.sha256_file(args.settings):
            raise ValueError("the supported uninstaller changed preserved user settings")
        account = pwd.getpwnam(args.user)
        if account.pw_uid != os.getuid():
            raise ValueError("uninstall evidence must run as the target release user")
        user_home = Path(account.pw_dir).resolve(strict=True)
        configured = tuple(config.provider for config in detect_provider_configs(user_home) if config.hooks_enabled)
        if configured:
            raise ValueError("the supported uninstaller left owned provider hooks: " + ", ".join(configured))
        leftovers = owned_file_leftovers(user_home)
        if leftovers:
            raise ValueError(
                "the supported uninstaller left owned integration files: " + ", ".join(str(path) for path in leftovers)
            )
        loaded_jobs = _loaded_owned_jobs(account.pw_uid)
        if loaded_jobs:
            raise ValueError("the supported uninstaller left owned launchd jobs: " + ", ".join(loaded_jobs))
        candidate = release_evidence.load_json_object(
            args.candidate.resolve(strict=True),
            label="candidate",
        )
        receipt = release_evidence.create_receipt(
            root=args.root,
            candidate=candidate,
            kind="uninstall",
            tool="verify_uninstalled_candidate.py",
            input_path=args.pkg,
            output_text="supported JR-Bar uninstallation verified",
            details={
                "app_state": "removed",
                "owned_cli_link_state": "removed-or-not-present",
                "package_receipt_state": "removed",
                "user_state": "preserved",
                "owned_integration_state": "removed",
            },
        )
        release_evidence.write_json(args.output, receipt)
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"uninstall verification failed: {exc}", file=sys.stderr)
        return 1
    print("supported JR-Bar uninstallation verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
