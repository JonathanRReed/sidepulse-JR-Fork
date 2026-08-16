"""Bounded LaunchAgent lifecycle facade over the historical implementation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import _status_bar_launch_legacy as _legacy

LAUNCHCTL_TIMEOUT_SECONDS = 15.0
LAUNCH_AGENT_THROTTLE_SECONDS = 10
LAUNCH_AGENT_EXIT_TIMEOUT_SECONDS = 5

_ORIGINAL_BUILD_LAUNCH_AGENT_PLIST = _legacy.build_launch_agent_plist


def build_launch_agent_plist(
    python_executable: Path | str | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> dict:
    plist = _ORIGINAL_BUILD_LAUNCH_AGENT_PLIST(
        python_executable=python_executable,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    plist["ThrottleInterval"] = LAUNCH_AGENT_THROTTLE_SECONDS
    plist["ExitTimeOut"] = LAUNCH_AGENT_EXIT_TIMEOUT_SECONDS
    plist["ProcessType"] = "Interactive"
    return plist


def _launchctl_run(
    arguments: list[str],
    *,
    check: bool,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_legacy.trusted_system_tool("launchctl")), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=LAUNCHCTL_TIMEOUT_SECONDS,
        check=check,
    )


def restart_launch_agent(plist_path: Path) -> None:
    bootout_launch_agent(plist_path)
    _launchctl_run(
        ["bootstrap", _legacy.launch_domain(), str(plist_path)],
        check=True,
    )
    _launchctl_run(
        [
            "kickstart",
            "-k",
            f"{_legacy.launch_domain()}/{_legacy.LAUNCH_AGENT_LABEL}",
        ],
        check=True,
    )


def launch_agent_running() -> bool:
    try:
        result = _launchctl_run(
            [
                "print",
                f"{_legacy.launch_domain()}/{_legacy.LAUNCH_AGENT_LABEL}",
            ],
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def bootout_launch_agent(plist_path: Path) -> None:
    try:
        _launchctl_run(
            ["bootout", _legacy.launch_domain(), str(plist_path)],
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


_legacy.LAUNCHCTL_TIMEOUT_SECONDS = LAUNCHCTL_TIMEOUT_SECONDS
_legacy.LAUNCH_AGENT_THROTTLE_SECONDS = LAUNCH_AGENT_THROTTLE_SECONDS
_legacy.LAUNCH_AGENT_EXIT_TIMEOUT_SECONDS = LAUNCH_AGENT_EXIT_TIMEOUT_SECONDS
_legacy.build_launch_agent_plist = build_launch_agent_plist
_legacy.restart_launch_agent = restart_launch_agent
_legacy.launch_agent_running = launch_agent_running
_legacy.bootout_launch_agent = bootout_launch_agent

for _name in dir(_legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_legacy, _name)


def development_python_executable() -> Path | None:
    """Interpreter for a source-checkout LaunchAgent, or None when packaged."""
    if getattr(sys, "frozen", False):
        return None
    executable = sys.executable
    if not executable:
        return None
    return Path(executable)


def install_launch_agent(
    *,
    start: bool = True,
    plist_path: Path | None = None,
    python_executable: Path | str | None = None,
    legacy_plist_path: Path | None = None,
):
    """Install the menu-bar LaunchAgent.

    Packaged builds keep the app-bundle executable. A source checkout
    uses this interpreter unless the caller passes another one; the
    plist builder still refuses a bare production install.
    """
    if python_executable is None:
        python_executable = development_python_executable()
    return _legacy.install_launch_agent(
        start=start,
        plist_path=plist_path,
        python_executable=python_executable,
        legacy_plist_path=legacy_plist_path,
    )


__all__ = tuple(sorted(name for name in globals() if not name.startswith("_")))
