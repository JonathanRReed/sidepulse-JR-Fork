"""Builds the SidePulse.app wrapper bundle the launchd job runs inside.

Why a bundle at all: macOS TCC (Full Disk Access today; Calendars and
notification observation tomorrow) attributes permissions to the
process's REAL executable and displays that executable in Privacy &
Security. As a bare venv process the app appeared as "python" -- and
worse, the venv's `python` is a symlink, so even granting the visible
path did nothing because access is attributed to the resolved Homebrew
Cellar binary that no reasonable person can find. Wrapped in
SidePulse.app, the Privacy list shows "SidePulse" by name and a grant
sticks to the app.

Why the bundle contains ONLY Info.plist and the executable: TCC also
requires the bundle's code signature to VERIFY -- a granted toggle for
an app whose signature fails validation is silently ignored (learned
the hard way: Jonathan granted FDA, macOS quit-and-reopened the app,
and reads still returned EPERM). The first bundle design shipped
`Contents/pyvenv.cfg` + a `Contents/lib` symlink so CPython could find
the venv, but codesign refuses to seal a bundle with loose files in
Contents/ ("code object is not signed at all: .../pyvenv.cfg") -- and
that failure was being swallowed, leaving the copied interpreter's own
"org.python.python" identity on the bundle. Now the interpreter finds
its runtime through the environment instead:

- PYTHONHOME -> the CPython framework's version directory (via
  Homebrew's stable /opt/homebrew/opt/... path, so patch upgrades
  don't strand it),
- PYTHONPATH -> the boot-shim directory + the venv's site-packages
  (a real directory path, so every `pip install` is picked up live).

Both are baked into the launchd job's EnvironmentVariables AND the
bundle's LSEnvironment, so launchd starts and Finder/Raycast/TCC
"reopen" launches all resolve identically. The executable itself is a
byte-for-byte COPY of the framework's GUI Mach-O
(Resources/Python.app/Contents/MacOS/Python -- NOT the bin/ stub,
which re-execs the Cellar binary and escapes TCC attribution).

The boot shim (sitecustomize.py, in the state directory, deliberately
OUTSIDE the sealed bundle so it can change without re-signing): a
plain launch of SidePulse.app -- Finder, Raycast, Spotlight, or TCC's
own quit-and-reopen -- is a bare interpreter with no arguments, which
would otherwise exit silently. The shim detects exactly that case and
hands control to the launchd job instead.

Ad-hoc signing means the TCC grant is pinned to the executable's
cdhash: a Python interpreter upgrade rebuilds the bundle and the
grant must be re-made (the Focus Dimming card shows the walkthrough
again whenever that happens).
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .providers import default_state_dir

APP_BUNDLE_NAME = "SidePulse.app"
APP_BUNDLE_IDENTIFIER = "io.sidepulse.app"
APP_EXECUTABLE_NAME = "SidePulse"


class AppBundleError(RuntimeError):
    """The bundle could not be built or signed; TCC grants would not
    work, so this must never fail silently."""


@dataclass(frozen=True)
class AppBundleResult:
    bundle_path: Path
    executable_path: Path
    changed: bool
    environment: dict[str, str] = field(default_factory=dict)


def default_app_bundle_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / "Applications" / APP_BUNDLE_NAME


def running_inside_bundle() -> bool:
    return f"{APP_BUNDLE_NAME}/Contents/MacOS/" in (sys.executable or "")


def default_boot_dir() -> Path:
    return default_state_dir() / "bundle-boot"


def _info_plist(environment: dict[str, str]) -> bytes:
    info: dict[str, Any] = {
        "CFBundleName": "SidePulse",
        "CFBundleDisplayName": "SidePulse",
        "CFBundleIdentifier": APP_BUNDLE_IDENTIFIER,
        "CFBundleExecutable": APP_EXECUTABLE_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        # Menu-bar app: no Dock icon, no app switcher entry.
        "LSUIElement": True,
        # Finder/Raycast/TCC-reopen launches resolve the interpreter's
        # runtime exactly like the launchd job does.
        "LSEnvironment": environment,
        # Usage descriptions for the TCC prompts the bundle presents.
        # EVERY foreseeable key ships in one batch: each Info.plist
        # change re-signs the bundle, and macOS pins existing TCC
        # grants (Full Disk Access included) to the exact signature --
        # so every new key here costs the user a re-grant. Batch them.
        "NSCalendarsUsageDescription": (
            "SidePulse can glow before calendar events start."
        ),
        "NSCalendarsFullAccessUsageDescription": (
            "SidePulse can glow before calendar events start."
        ),
        "NSRemindersFullAccessUsageDescription": (
            "SidePulse can glow when reminders come due."
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


def _framework_home(gui_binary: Path) -> Path:
    """The framework version directory PYTHONHOME should point at.
    Prefers Homebrew's stable opt path (/opt/homebrew/opt/python@3.x/
    Frameworks/...) over the resolved Cellar path, which embeds the
    exact patch version and dies on the next `brew upgrade`."""
    # gui: .../Versions/3.x/Resources/Python.app/Contents/MacOS/Python
    version_dir = gui_binary.parents[4]
    parts = version_dir.parts
    if "Cellar" in parts:
        index = parts.index("Cellar")
        formula = parts[index + 1]
        stable = Path(*parts[:index]) / "opt" / formula / Path(*parts[index + 3:])
        if stable.exists():
            return stable
    return version_dir


def _site_packages(venv_root: Path) -> Path:
    candidates = sorted((venv_root / "lib").glob("python3.*/site-packages"))
    if not candidates:
        raise AppBundleError(f"no site-packages found under {venv_root / 'lib'}")
    return candidates[-1]


# Runs at every startup of any interpreter that has the boot dir on its
# PYTHONPATH. The guards keep it inert everywhere except a plain
# no-argument launch of the bundle executable (Finder, Raycast,
# Spotlight, TCC's quit-and-reopen) -- exactly the case that used to be
# a silent dead end. It hands control to the launchd job (restarting it
# if it was already running -- what a user double-clicking the app
# means) and exits this bare interpreter.
_BOOT_SHIM = '''\
"""SidePulse boot shim -- see sidepulse/app_bundle.py. Auto-generated;
edits are overwritten on the next bundle build."""
import os
import sys


def _sidepulse_plain_launch_boot():
    executable = sys.executable or ""
    if "SidePulse.app/Contents/MacOS/" not in executable:
        return
    arguments = list(getattr(sys, "argv", []) or [""])
    if len(arguments) > 1 or arguments[0] != "":
        return
    import subprocess

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONHOME", "PYTHONPATH")
    }
    subprocess.Popen(
        [{venv_python!r}, "-m", "sidepulse", "status-bar", "start"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    os._exit(0)


_sidepulse_plain_launch_boot()
'''


def _codesign(bundle: Path) -> None:
    sign = subprocess.run(
        ["codesign", "--force", "--sign", "-", str(bundle)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if sign.returncode != 0:
        raise AppBundleError(f"codesign failed for {bundle}: {sign.stderr.strip()}")
    verify = subprocess.run(
        ["codesign", "--verify", "--strict", str(bundle)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if verify.returncode != 0:
        raise AppBundleError(
            f"bundle signature does not verify for {bundle}: {verify.stderr.strip()}"
        )


def _signature_valid(bundle: Path) -> bool:
    try:
        verify = subprocess.run(
            ["codesign", "--verify", "--strict", str(bundle)],
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return verify.returncode == 0


def build_app_bundle(
    bundle_path: Path | None = None,
    venv_python: Path | str | None = None,
    boot_dir: Path | None = None,
) -> AppBundleResult:
    """Creates or refreshes the wrapper bundle. Idempotent: recopies the
    interpreter only when the resolved CPython binary changed, rewrites
    metadata only when its content differs, and re-signs only when the
    bundle changed or its signature no longer verifies."""
    bundle = bundle_path or default_app_bundle_path()
    venv_exe = Path(venv_python or sys.executable or "python3")
    real_interpreter = _resolved_interpreter(venv_exe)
    venv_root = venv_exe.parent.parent
    boot = boot_dir or default_boot_dir()

    boot.mkdir(parents=True, exist_ok=True)
    shim_path = boot / "sitecustomize.py"
    shim_source = _BOOT_SHIM.replace("{venv_python!r}", repr(str(venv_exe)))
    if not shim_path.exists() or shim_path.read_text() != shim_source:
        shim_path.write_text(shim_source)

    environment = {
        "PYTHONHOME": str(_framework_home(real_interpreter)),
        "PYTHONPATH": f"{boot}:{_site_packages(venv_root)}",
    }

    contents = bundle / "Contents"
    macos_dir = contents / "MacOS"
    executable = macos_dir / APP_EXECUTABLE_NAME
    changed = False

    macos_dir.mkdir(parents=True, exist_ok=True)

    info_path = contents / "Info.plist"
    info_data = _info_plist(environment)
    if not info_path.exists() or info_path.read_bytes() != info_data:
        info_path.write_bytes(info_data)
        changed = True

    # Signing rewrites the copied binary (the signature is embedded),
    # so the copy can't be compared against the source directly; a
    # sidecar in the boot dir records which source binary was copied.
    source_stat = real_interpreter.stat()
    source_identity = f"{real_interpreter}|{source_stat.st_size}|{source_stat.st_mtime_ns}"
    identity_path = boot / "bundled-interpreter.txt"
    recorded = identity_path.read_text() if identity_path.exists() else None
    if not executable.exists() or recorded != source_identity:
        shutil.copy2(real_interpreter, executable)
        executable.chmod(0o755)
        identity_path.write_text(source_identity)
        changed = True

    # Migrate away from the first bundle layout: loose files in
    # Contents/ make codesign refuse to seal the bundle.
    legacy_pyvenv = contents / "pyvenv.cfg"
    if legacy_pyvenv.exists():
        legacy_pyvenv.unlink()
        changed = True
    legacy_lib = contents / "lib"
    if legacy_lib.is_symlink():
        legacy_lib.unlink()
        changed = True
    elif legacy_lib.exists():
        shutil.rmtree(legacy_lib)
        changed = True

    if changed or not _signature_valid(bundle):
        _codesign(bundle)
        changed = True

    return AppBundleResult(
        bundle_path=bundle,
        executable_path=executable,
        changed=changed,
        environment=environment,
    )
