"""Repository-wide test sandbox and import setup.

The sandbox is established at collection time, before test modules import
SidePulse and cache default paths. Tests must never write the developer's real
settings, state, provider configuration, LaunchAgent domain, or mounted LEDs.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT_PATH = Path(__file__).resolve().parent.parent
_ROOT = str(_ROOT_PATH)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Module-level on purpose: fixtures run after test modules are imported, which
# is too late for modules that cache HOME/XDG-derived defaults.
_TEST_SANDBOX = Path(tempfile.mkdtemp(prefix="sidepulse-pytest-"))
_TEST_HOME = _TEST_SANDBOX / "home"
_TEST_CONFIG = _TEST_SANDBOX / "config"
_TEST_STATE = _TEST_SANDBOX / "state"
_TEST_CACHE = _TEST_SANDBOX / "cache"
_TEST_VOLUMES = _TEST_SANDBOX / "Volumes"
for _path in (_TEST_HOME, _TEST_CONFIG, _TEST_STATE, _TEST_CACHE, _TEST_VOLUMES):
    _path.mkdir(parents=True, exist_ok=True, mode=0o700)

os.environ["HOME"] = str(_TEST_HOME)
os.environ["XDG_CONFIG_HOME"] = str(_TEST_CONFIG)
os.environ["XDG_STATE_HOME"] = str(_TEST_STATE)
os.environ["XDG_CACHE_HOME"] = str(_TEST_CACHE)
os.environ["SIDEPULSE_TESTING"] = "1"
os.environ["SIDEPULSE_TEST_VOLUME_ROOT"] = str(_TEST_VOLUMES)

# The suite must NEVER take the desktop away from a person using this
# machine. AppKit tests build real windows, and product code they
# exercise calls makeKeyAndOrderFront_ / activateIgnoringOtherApps_ --
# for a four-minute run that meant focus being yanked from the owner's
# hands over and over ("makes this computer unusable", reported live
# 2026-08-26). Two defenses, both belt-and-suspenders with the
# SIDEPULSE_TESTING guard inside window_presentation.py:
#   1. PROHIBITED activation policy: macOS itself refuses to ever make
#      this process the active app, whatever the code under test asks.
#   2. Set at conftest import time -- before any test module can touch
#      AppKit -- exactly like the sandbox above, because a fixture
#      would be too late.
try:  # pragma: no cover - environment-dependent, no AppKit on CI
    from AppKit import NSApplication, NSApplicationActivationPolicyProhibited

    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyProhibited
    )
except Exception:
    pass

_LIVE_VOLUME_ROOT = Path("/Volumes")
_LIVE_LAUNCH_AGENT_ROOT = Path.home().expanduser() / "Library" / "LaunchAgents"


@pytest.fixture(autouse=True)
def isolate_live_settings_file(tmp_path, monkeypatch):
    """Keep all settings facades on one per-test path."""
    isolated = tmp_path / "pytest-sidepulse-settings.json"

    def _isolated_path(home=None):
        return isolated

    monkeypatch.setattr(
        "sidepulse._settings_legacy.default_settings_path",
        _isolated_path,
    )
    monkeypatch.setattr(
        "sidepulse.settings.default_settings_path",
        _isolated_path,
    )


def _is_live_volume_path(path: object) -> bool:
    candidate = Path(path)
    try:
        candidate = candidate.expanduser().resolve(strict=False)
        root = _LIVE_VOLUME_ROOT.resolve(strict=False)
    except OSError:
        candidate = candidate.absolute()
        root = _LIVE_VOLUME_ROOT
    return candidate == root or root in candidate.parents


@pytest.fixture(autouse=True)
def block_live_launchd_mutations(monkeypatch):
    """Refuse real launchctl mutations even when a test forgot to stub them."""
    original_run = subprocess.run

    def guarded_run(arguments, *args, **kwargs):
        command = list(arguments) if not isinstance(arguments, (str, bytes)) else []
        if command and Path(str(command[0])).name == "launchctl":
            operation = str(command[1]) if len(command) > 1 else ""
            if operation in {
                "bootstrap",
                "bootout",
                "kickstart",
                "enable",
                "disable",
                "remove",
                "submit",
            }:
                raise AssertionError(
                    f"test attempted live launchctl mutation: {operation}"
                )
        return original_run(arguments, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)


@pytest.fixture(autouse=True)
def block_live_volume_writes(monkeypatch):
    """Fail tests before any file or keepalive write reaches real hardware."""

    original_write_text = Path.write_text
    original_write_bytes = Path.write_bytes
    original_touch = Path.touch
    original_replace = Path.replace

    def reject(path: object, operation: str) -> None:
        if _is_live_volume_path(path):
            raise AssertionError(
                f"test attempted {operation} on mounted hardware path: {path}"
            )

    def guarded_write_text(path, *args, **kwargs):
        reject(path, "write_text")
        return original_write_text(path, *args, **kwargs)

    def guarded_write_bytes(path, *args, **kwargs):
        reject(path, "write_bytes")
        return original_write_bytes(path, *args, **kwargs)

    def guarded_touch(path, *args, **kwargs):
        reject(path, "touch")
        return original_touch(path, *args, **kwargs)

    def guarded_replace(path, target, *args, **kwargs):
        reject(path, "replace source")
        reject(target, "replace target")
        return original_replace(path, target, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", guarded_write_text, raising=False)
    monkeypatch.setattr(Path, "write_bytes", guarded_write_bytes, raising=False)
    monkeypatch.setattr(Path, "touch", guarded_touch, raising=False)
    monkeypatch.setattr(Path, "replace", guarded_replace, raising=False)

    from sidepulse import keep_awake

    original_keepalive_touch = keep_awake.touch_keepalive_file
    original_poke_status_file = keep_awake.KeepAwakeController.poke_status_file

    def guarded_keepalive_touch(path):
        reject(path, "keepalive touch")
        return original_keepalive_touch(path)

    def guarded_poke_status_file(controller, target, *args, **kwargs):
        if target is not None:
            reject(keep_awake.keepalive_file_for_target(target), "keepalive poke")
        return original_poke_status_file(controller, target, *args, **kwargs)

    def guarded_subprocess_run(arguments, *args, **kwargs):
        command = list(arguments) if not isinstance(arguments, (str, bytes)) else []
        if command and command[0] == "/usr/bin/touch":
            for target in command[1:]:
                reject(target, "subprocess touch")
        return original_subprocess_run(arguments, *args, **kwargs)

    monkeypatch.setattr(keep_awake, "touch_keepalive_file", guarded_keepalive_touch)
    monkeypatch.setattr(
        keep_awake.KeepAwakeController,
        "poke_status_file",
        guarded_poke_status_file,
    )
    original_subprocess_run = keep_awake.subprocess.run
    monkeypatch.setattr(keep_awake.subprocess, "run", guarded_subprocess_run)

    try:
        from sidepulse import status_bar
    except (ImportError, SystemExit):
        return

    original_keepalive_targets = status_bar.StatusBarController.status_keepalive_targets

    def guarded_keepalive_targets(controller):
        targets = original_keepalive_targets(controller)
        for target in targets:
            reject(target, "keepalive target selection")
        return targets

    monkeypatch.setattr(
        status_bar.StatusBarController,
        "status_keepalive_targets",
        guarded_keepalive_targets,
    )

    from sidepulse import device_writer

    original_write_led_program = device_writer.write_led_program

    def guarded_write_led_program(
        text,
        *,
        device_path=None,
        file_name="LEDS.LED",
        dry_run=False,
        preserve_existing_inode=False,
    ):
        if device_path is not None:
            reject(
                device_writer.target_from_device_path(Path(device_path), file_name),
                "LED program write",
            )
        return original_write_led_program(
            text,
            device_path=device_path,
            file_name=file_name,
            dry_run=dry_run,
            preserve_existing_inode=preserve_existing_inode,
        )

    monkeypatch.setattr(device_writer, "write_led_program", guarded_write_led_program)
