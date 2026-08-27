"""SidePulse.app identity helpers.

The production bundle is the self-contained PyInstaller app built by
packaging/build_macos_pkg.sh. This module keeps only the two facts other
code needs about it: where the installed bundle lives, and whether the
current process is running from inside one. (macOS TCC attributes
permissions to the process's real executable, so "are we the bundle?"
decides which path the Privacy walkthroughs point at.)

The mutable development-wrapper builder that used to live here was
deleted 2026-08-26: nothing called build_app_bundle -- not the app, not
the CLI, not a script -- and the packaging pipeline owns Info.plist,
signing, and the Apple-Events usage description now.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_BUNDLE_NAME = "SidePulse.app"
APP_BUNDLE_IDENTIFIER = "io.sidepulse.app"
APP_EXECUTABLE_NAME = "SidePulse"


def default_app_bundle_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / "Applications" / APP_BUNDLE_NAME


def running_inside_bundle() -> bool:
    return f"{APP_BUNDLE_NAME}/Contents/MacOS/" in (sys.executable or "")
