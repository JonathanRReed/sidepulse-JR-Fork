"""Builds the SidePulse.app wrapper bundle the launchd job runs inside.

Why a bundle at all: macOS TCC (Full Disk Access today; Calendars and
notification observation tomorrow) attributes permissions to the process's
REAL executable and displays that executable in Privacy & Security. As a
bare venv process the app appeared as "python" -- and worse, the venv's
`python` is a symlink, so even granting the visible path did nothing
because access is attributed to the resolved Homebrew Cellar binary that
no reasonable person can find. Wrapped in SidePulse.app, the Privacy list
shows "SidePulse" by name and a grant sticks to the app.

How the wrapper works (verified empirically before this module existed):
the bundle's Contents/MacOS/SidePulse is a byte-for-byte COPY of the
venv's resolved CPython binary -- a copy, not a symlink, because TCC
resolves symlinks. CPython finds its home via Contents/pyvenv.cfg (a copy
of the venv's own, whose `home =` line points at the base interpreter's
bin directory) and its packages via Contents/lib, a symlink to the venv's
lib -- so the bundle needs no duplicate site-packages and picks up every
`pip install --force-reinstall` into the venv automatically. Only a new
CPython binary (e.g. a Homebrew upgrade) requires a rebuild, which
build_app_bundle() detects and performs idempotently.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_BUNDLE_NAME = "SidePulse.app"
APP_BUNDLE_IDENTIFIER = "io.sidepulse.app"
APP_EXECUTABLE_NAME = "SidePulse"


@dataclass(frozen=True)
class AppBundleResult:
    bundle_path: Path
    executable_path: Path
    changed: bool


def default_app_bundle_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / "Applications" / APP_BUNDLE_NAME


def bundle_executable_path(bundle_path: Path | None = None) -> Path:
    return (bundle_path or default_app_bundle_path()) / "Contents" / "MacOS" / APP_EXECUTABLE_NAME


def running_inside_bundle() -> bool:
    return f"{APP_BUNDLE_NAME}/Contents/MacOS/" in (sys.executable or "")


def _info_plist() -> bytes:
    info: dict[str, Any] = {
        "CFBundleName": "SidePulse",
        "CFBundleDisplayName": "SidePulse",
        "CFBundleIdentifier": APP_BUNDLE_IDENTIFIER,
        "CFBundleExecutable": APP_EXECUTABLE_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        # Menu-bar app: no Dock icon, no app switcher entry.
        "LSUIElement": True,
        # Usage descriptions for the TCC prompts the bundle unlocks --
        # present now so future EventKit/notification features can ask.
        "NSCalendarsUsageDescription": (
            "SidePulse can glow before calendar events start."
        ),
    }
    return plistlib.dumps(info)


def _resolved_interpreter(venv_python: Path) -> Path:
    """The REAL interpreter Mach-O, not the bin stub. Framework CPython's
    bin/python3.x is a tiny launcher that re-execs
    Resources/Python.app/Contents/MacOS/Python -- copying the stub into
    the bundle meant the RUNNING process became the Cellar's "Python"
    again and TCC attribution escaped the wrapper. Copying the GUI
    binary itself runs in place with no re-exec (verified: sys.executable
    stays inside the bundle and AppKit imports cleanly)."""
    resolved = Path(os.path.realpath(venv_python))
    gui_binary = resolved.parent.parent / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    if gui_binary.exists():
        return gui_binary
    return resolved


def build_app_bundle(
    bundle_path: Path | None = None,
    venv_python: Path | str | None = None,
) -> AppBundleResult:
    """Creates or refreshes the wrapper bundle. Idempotent: rebuilds the
    copied interpreter only when the resolved CPython binary changed, and
    rewrites metadata only when its content differs."""
    bundle = bundle_path or default_app_bundle_path()
    venv_exe = Path(venv_python or sys.executable or "python3")
    real_interpreter = _resolved_interpreter(venv_exe)
    venv_root = venv_exe.parent.parent

    contents = bundle / "Contents"
    macos_dir = contents / "MacOS"
    executable = macos_dir / APP_EXECUTABLE_NAME
    changed = False

    macos_dir.mkdir(parents=True, exist_ok=True)

    info_path = contents / "Info.plist"
    info_data = _info_plist()
    if not info_path.exists() or info_path.read_bytes() != info_data:
        info_path.write_bytes(info_data)
        changed = True

    source_stat = real_interpreter.stat()
    needs_copy = (
        not executable.exists()
        or executable.stat().st_size != source_stat.st_size
        or executable.stat().st_mtime < source_stat.st_mtime
    )
    if needs_copy:
        shutil.copy2(real_interpreter, executable)
        executable.chmod(0o755)
        changed = True

    pyvenv_target = contents / "pyvenv.cfg"
    pyvenv_source = venv_root / "pyvenv.cfg"
    if pyvenv_source.exists():
        source_text = pyvenv_source.read_text()
        if not pyvenv_target.exists() or pyvenv_target.read_text() != source_text:
            pyvenv_target.write_text(source_text)
            changed = True

    lib_link = contents / "lib"
    lib_target = venv_root / "lib"
    if lib_link.is_symlink():
        if Path(os.readlink(lib_link)) != lib_target:
            lib_link.unlink()
            lib_link.symlink_to(lib_target)
            changed = True
    elif lib_link.exists():
        shutil.rmtree(lib_link)
        lib_link.symlink_to(lib_target)
        changed = True
    else:
        lib_link.symlink_to(lib_target)
        changed = True

    if changed:
        # Ad-hoc signature so the bundle has a stable code identity for
        # TCC. Best effort: an unsigned bundle still works path-based.
        try:
            subprocess.run(
                ["codesign", "--force", "--sign", "-", str(bundle)],
                check=False,
                capture_output=True,
                timeout=30,
            )
        except Exception:
            pass

    return AppBundleResult(bundle_path=bundle, executable_path=executable, changed=changed)
