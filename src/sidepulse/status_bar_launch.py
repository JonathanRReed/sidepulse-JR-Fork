from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .providers import default_state_dir

LAUNCH_AGENT_LABEL = "io.sidepulse.agentstatus"
LAUNCH_AGENT_FILENAME = f"{LAUNCH_AGENT_LABEL}.plist"
LEGACY_LAUNCH_AGENT_LABEL = "com.sidepulse.agentstatus"
LEGACY_LAUNCH_AGENT_FILENAME = f"{LEGACY_LAUNCH_AGENT_LABEL}.plist"


@dataclass(frozen=True)
class LaunchAgentResult:
    label: str
    plist_path: Path
    changed: bool
    started: bool = False
    stopped: bool = False


def launch_agent_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / "Library" / "LaunchAgents" / LAUNCH_AGENT_FILENAME


def legacy_launch_agent_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / "Library" / "LaunchAgents" / LEGACY_LAUNCH_AGENT_FILENAME


def launch_agent_installed(plist_path: Path | None = None) -> bool:
    target = plist_path or launch_agent_path()
    return target.exists()


def build_launch_agent_plist(
    python_executable: Path | str | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> dict[str, Any]:
    bundle_environment: dict[str, str] = {}
    if python_executable is None and not getattr(sys, "frozen", False):
        # Run inside the SidePulse.app wrapper so macOS Privacy lists
        # show "SidePulse" by name and TCC grants stick to the app --
        # see app_bundle.py for why a bare venv process can't get there.
        from .app_bundle import build_app_bundle

        bundle = build_app_bundle()
        python_executable = bundle.executable_path
        # The sealed bundle carries no pyvenv.cfg; the interpreter's
        # home and site-packages come from these variables instead.
        bundle_environment = bundle.environment
    executable = str(python_executable or sys.executable or "python3")
    state_dir = default_state_dir()
    stdout = stdout_path or state_dir / "status-bar.out.log"
    stderr = stderr_path or state_dir / "status-bar.err.log"

    if getattr(sys, "frozen", False) and python_executable is None:
        program_arguments = [executable, "status-bar", "start", "--foreground"]
    else:
        program_arguments = [
            executable,
            "-m",
            "sidepulse",
            "status-bar",
            "--foreground",
        ]

    plist: dict[str, Any] = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": program_arguments,
        "RunAtLoad": True,
        # Unconditional: granting a TCC permission quits the app with a
        # CLEAN exit (observed: last exit code 0), so a SuccessfulExit
        # condition would have left it dead -- exactly the "I granted
        # Full Disk Access and SidePulse never came back" failure. The
        # Quit menu item boots the job out instead of just exiting, so
        # quitting still sticks (see quit_ in status_bar.py).
        "KeepAlive": True,
        "StandardOutPath": str(stdout),
        "StandardErrorPath": str(stderr),
        "WorkingDirectory": str(Path.home()),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "PATH": launch_agent_path_env(executable),
            **bundle_environment,
        },
    }
    return plist


def install_launch_agent(
    *,
    start: bool = True,
    plist_path: Path | None = None,
    python_executable: Path | str | None = None,
    legacy_plist_path: Path | None = None,
) -> LaunchAgentResult:
    target = plist_path or launch_agent_path()
    legacy_target = legacy_plist_path if legacy_plist_path is not None else (
        legacy_launch_agent_path() if plist_path is None else None
    )
    plist = build_launch_agent_plist(python_executable=python_executable)
    data = plistlib.dumps(plist, sort_keys=False)
    existing = target.read_bytes() if target.exists() else None
    changed = existing != data

    target.parent.mkdir(parents=True, exist_ok=True)
    default_state_dir().mkdir(parents=True, exist_ok=True)
    if changed:
        target.write_bytes(data)
    legacy_removed = False
    if legacy_target is not None:
        legacy_removed = remove_legacy_launch_agent(legacy_target)
    changed = changed or legacy_removed

    started = False
    if start:
        restart_launch_agent(target)
        started = True

    return LaunchAgentResult(
        label=LAUNCH_AGENT_LABEL,
        plist_path=target,
        changed=changed,
        started=started,
    )


def uninstall_launch_agent(plist_path: Path | None = None) -> LaunchAgentResult:
    target = plist_path or launch_agent_path()
    bootout_launch_agent(target)
    changed = target.exists()
    if target.exists():
        target.unlink()
    return LaunchAgentResult(
        label=LAUNCH_AGENT_LABEL,
        plist_path=target,
        changed=changed,
        stopped=True,
    )


def restart_launch_agent(plist_path: Path) -> None:
    bootout_launch_agent(plist_path)
    subprocess.run(
        ["launchctl", "bootstrap", launch_domain(), str(plist_path)],
        check=True,
    )
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"{launch_domain()}/{LAUNCH_AGENT_LABEL}"],
        check=False,
    )


def bootout_launch_agent(plist_path: Path) -> None:
    subprocess.run(
        ["launchctl", "bootout", launch_domain(), str(plist_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def remove_legacy_launch_agent(plist_path: Path | None = None) -> bool:
    target = plist_path or legacy_launch_agent_path()
    if not target.exists():
        return False
    bootout_launch_agent(target)
    target.unlink()
    return True


def launch_domain() -> str:
    return f"gui/{os.getuid()}"


def launch_agent_path_env(python_executable: str) -> str:
    candidates = [
        Path.home() / ".local" / "bin",
        executable_parent(python_executable),
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/bin"),
        Path("/usr/sbin"),
        Path("/sbin"),
        Path("/opt/anaconda3/bin"),
    ]
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        if candidate is None:
            continue
        text = str(candidate)
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
    return ":".join(result)


def executable_parent(python_executable: str) -> Path | None:
    path = Path(python_executable)
    if not path.is_absolute():
        return None
    return path.parent
