from __future__ import annotations

import os
import plistlib
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .private_io import (
    PrivateWriteTransaction,
    ensure_private_directory,
    read_private_bytes_with_identity,
)
from .product_identity import PRODUCT_DISPLAY_NAME
from .providers import default_state_dir
from .trusted_tools import trusted_system_tool

LAUNCH_AGENT_LABEL = "io.sidepulse.agentstatus"
LAUNCH_AGENT_FILENAME = f"{LAUNCH_AGENT_LABEL}.plist"
LEGACY_LAUNCH_AGENT_LABEL = "com.sidepulse.agentstatus"
LEGACY_LAUNCH_AGENT_FILENAME = f"{LEGACY_LAUNCH_AGENT_LABEL}.plist"

TERMINAL_BUNDLE_IDENTIFIER = "com.apple.Terminal"
ITERM_BUNDLE_IDENTIFIER = "com.googlecode.iterm2"
GHOSTTY_BUNDLE_IDENTIFIER = "com.mitchellh.ghostty"
REVIEWED_TERMINAL_BUNDLE_IDENTIFIERS = (
    TERMINAL_BUNDLE_IDENTIFIER,
    ITERM_BUNDLE_IDENTIFIER,
    GHOSTTY_BUNDLE_IDENTIFIER,
)
GHOSTTY_APPLICATION_PATHS = (Path("/Applications/Ghostty.app"),)
APPLE_EVENTS_USAGE_DESCRIPTION = (
    f"{PRODUCT_DISPLAY_NAME} uses Automation only to open a reviewed resume command in "
    "Terminal or iTerm2 when you choose Open."
)
UNSUPPORTED_TERMINAL_FALLBACK_COPY = (
    f"{PRODUCT_DISPLAY_NAME} does not support this terminal yet, so it opened Terminal."
)
UNAVAILABLE_GHOSTTY_FALLBACK_COPY = (
    f"{PRODUCT_DISPLAY_NAME} could not verify Ghostty, so it opened Terminal."
)


class TerminalLaunchKind(str, Enum):
    APPLE_EVENTS = "apple-events"
    EXECUTABLE = "executable"


@dataclass(frozen=True, slots=True)
class TerminalLaunchPlan:
    requested_bundle_identifier: str | None
    selected_bundle_identifier: str
    kind: TerminalLaunchKind
    executable_path: Path | None = None
    fallback_copy: str | None = None

    def __post_init__(self) -> None:
        requested_valid = self.requested_bundle_identifier is None or (
            type(self.requested_bundle_identifier) is str
            and 1 <= len(self.requested_bundle_identifier) <= 255
            and self.requested_bundle_identifier.isprintable()
        )
        if not requested_valid or type(self.selected_bundle_identifier) is not str:
            raise ValueError("invalid terminal launch identity")
        if self.kind is TerminalLaunchKind.APPLE_EVENTS:
            if not (
                self.selected_bundle_identifier
                in {TERMINAL_BUNDLE_IDENTIFIER, ITERM_BUNDLE_IDENTIFIER}
                and self.executable_path is None
            ):
                raise ValueError("invalid Apple Events terminal launch plan")
        elif self.kind is TerminalLaunchKind.EXECUTABLE:
            if not (
                self.selected_bundle_identifier == GHOSTTY_BUNDLE_IDENTIFIER
                and isinstance(self.executable_path, Path)
                and self.executable_path.is_absolute()
            ):
                raise ValueError("invalid executable terminal launch plan")
        else:
            raise ValueError("invalid terminal launch kind")
        if self.fallback_copy is not None and self.fallback_copy not in {
            UNSUPPORTED_TERMINAL_FALLBACK_COPY,
            UNAVAILABLE_GHOSTTY_FALLBACK_COPY,
        }:
            raise ValueError("invalid terminal fallback copy")


@dataclass(frozen=True)
class LaunchAgentResult:
    label: str
    plist_path: Path
    changed: bool
    started: bool = False
    stopped: bool = False


def terminal_navigation_requires_apple_events(
    bundle_identifiers: Sequence[str] = REVIEWED_TERMINAL_BUNDLE_IDENTIFIERS,
    *,
    fallback_to_terminal: bool = True,
) -> bool:
    """Return whether an enabled reviewed terminal action sends Apple Events."""
    reviewed = frozenset(bundle_identifiers)
    return fallback_to_terminal or bool(
        reviewed & {TERMINAL_BUNDLE_IDENTIFIER, ITERM_BUNDLE_IDENTIFIER}
    )


def _safe_real_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def _safe_real_file(path: Path, *, executable: bool = False) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        return False
    return not executable or bool(metadata.st_mode & 0o111)


def _validated_ghostty_executable(
    bundle: Path,
    *,
    command_runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> Path | None:
    """Resolve one reviewed Ghostty bundle without PATH or user-directory search."""
    candidate_bundle = Path(bundle)
    if not candidate_bundle.is_absolute() or candidate_bundle.name != "Ghostty.app":
        return None
    contents = candidate_bundle / "Contents"
    macos = contents / "MacOS"
    info_path = contents / "Info.plist"
    executable = macos / "ghostty"
    if not all(
        _safe_real_directory(path)
        for path in (candidate_bundle, contents, macos)
    ) or not (
        _safe_real_file(info_path)
        and _safe_real_file(executable, executable=True)
    ):
        return None
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    if not isinstance(info, dict) or not (
        info.get("CFBundleIdentifier") == GHOSTTY_BUNDLE_IDENTIFIER
        and info.get("CFBundleExecutable") == "ghostty"
    ):
        return None
    try:
        resolved_bundle = candidate_bundle.resolve(strict=True)
        resolved_executable = executable.resolve(strict=True)
    except OSError:
        return None
    if resolved_executable != resolved_bundle / "Contents" / "MacOS" / "ghostty":
        return None
    run_command = command_runner or subprocess.run
    try:
        verified = run_command(
            [
                str(trusted_system_tool("codesign")),
                "--verify",
                "--deep",
                "--strict",
                str(candidate_bundle),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return executable if verified.returncode == 0 else None


def resolve_ghostty_executable() -> Path | None:
    """Resolve Ghostty only from reviewed system-wide application locations."""
    for bundle in GHOSTTY_APPLICATION_PATHS:
        executable = _validated_ghostty_executable(bundle)
        if executable is not None:
            return executable
    return None


def resolve_terminal_launch(bundle_identifier: str | None) -> TerminalLaunchPlan:
    """Resolve one deterministic terminal action or a product-owned fallback."""
    requested = bundle_identifier if type(bundle_identifier) is str else None
    if requested == TERMINAL_BUNDLE_IDENTIFIER:
        return TerminalLaunchPlan(
            requested,
            TERMINAL_BUNDLE_IDENTIFIER,
            TerminalLaunchKind.APPLE_EVENTS,
        )
    if requested == ITERM_BUNDLE_IDENTIFIER:
        return TerminalLaunchPlan(
            requested,
            ITERM_BUNDLE_IDENTIFIER,
            TerminalLaunchKind.APPLE_EVENTS,
        )
    if requested == GHOSTTY_BUNDLE_IDENTIFIER:
        executable = resolve_ghostty_executable()
        if executable is not None:
            return TerminalLaunchPlan(
                requested,
                GHOSTTY_BUNDLE_IDENTIFIER,
                TerminalLaunchKind.EXECUTABLE,
                executable_path=Path(executable),
            )
        return TerminalLaunchPlan(
            requested,
            TERMINAL_BUNDLE_IDENTIFIER,
            TerminalLaunchKind.APPLE_EVENTS,
            fallback_copy=UNAVAILABLE_GHOSTTY_FALLBACK_COPY,
        )
    return TerminalLaunchPlan(
        requested,
        TERMINAL_BUNDLE_IDENTIFIER,
        TerminalLaunchKind.APPLE_EVENTS,
        fallback_copy=UNSUPPORTED_TERMINAL_FALLBACK_COPY,
    )


def _applescript_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def terminal_launch_arguments(
    plan: TerminalLaunchPlan,
    command: str,
) -> tuple[str, ...]:
    """Build exact argv for a reviewed terminal plan without invoking a shell."""
    if type(plan) is not TerminalLaunchPlan:
        raise ValueError("invalid terminal launch plan")
    if not (
        type(command) is str
        and 1 <= len(command) <= 8_192
        and command.isprintable()
    ):
        raise ValueError("invalid terminal command")
    if plan.kind is TerminalLaunchKind.EXECUTABLE:
        if plan.executable_path is None:
            raise ValueError("executable terminal launch is missing its path")
        return (
            str(plan.executable_path),
            "+new-window",
            "-e",
            "/bin/zsh",
            "-lc",
            command,
        )
    if plan.selected_bundle_identifier == TERMINAL_BUNDLE_IDENTIFIER:
        script = "\n".join(
            (
                f'tell application id "{TERMINAL_BUNDLE_IDENTIFIER}"',
                "  activate",
                f"  do script {_applescript_quote(command)}",
                "end tell",
            )
        )
    elif plan.selected_bundle_identifier == ITERM_BUNDLE_IDENTIFIER:
        script = "\n".join(
            (
                f'tell application id "{ITERM_BUNDLE_IDENTIFIER}"',
                "  activate",
                f"  create window with default profile command {_applescript_quote(command)}",
                "end tell",
            )
        )
    else:
        raise ValueError("unreviewed Apple Events terminal target")
    return (str(trusted_system_tool("osascript")), "-e", script)


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
    frozen = bool(getattr(sys, "frozen", False)) and python_executable is None
    if frozen:
        executable = str(production_bundle_executable())
    elif python_executable is not None:
        executable = str(Path(python_executable))
    else:
        raise RuntimeError(
            "production LaunchAgent installation requires the packaged SidePulse.app; "
            "build and install the macOS package, or pass an explicit development "
            "python_executable"
        )
    state_dir = default_state_dir()
    stdout = stdout_path or state_dir / "status-bar.out.log"
    stderr = stderr_path or state_dir / "status-bar.err.log"

    if frozen:
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
    if not target.parent.exists():
        ensure_private_directory(target.parent)
    parent_info = target.parent.lstat()
    if (
        stat.S_ISLNK(parent_info.st_mode)
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.getuid()
        or stat.S_IMODE(parent_info.st_mode) & 0o022
    ):
        raise OSError("refusing unsafe LaunchAgent parent")
    parent_identity = (parent_info.st_dev, parent_info.st_ino)
    try:
        existing, target_identity = read_private_bytes_with_identity(
            target,
            tighten=False,
            max_bytes=1024 * 1024,
        )
    except FileNotFoundError:
        existing = None
        target_identity = None
    if existing is not None:
        target_info = target.lstat()
        if (
            target_info.st_uid != os.getuid()
            or stat.S_IMODE(target_info.st_mode) & 0o022
        ):
            raise OSError("refusing unsafe LaunchAgent file")
    changed = existing != data

    started = False
    previously_running = start and launch_agent_running()
    activation_attempted = False
    ensure_private_directory(default_state_dir())
    try:
        with PrivateWriteTransaction() as transaction:
            if changed:
                transaction.write(
                    target,
                    data,
                    max_original_bytes=1024 * 1024,
                    expected_identity=target_identity,
                    expected_parent_identity=parent_identity,
                )
                transaction.verify(target, data, max_bytes=1024 * 1024)
            if start:
                activation_attempted = True
                restart_launch_agent(target)
                started = True
    except BaseException as install_error:
        if activation_attempted and previously_running and existing is not None:
            try:
                restart_launch_agent(target)
            except BaseException as recovery_error:
                raise OSError(
                    "LaunchAgent update failed and the previous job could not be restarted"
                ) from recovery_error
        raise install_error

    legacy_removed = False
    if legacy_target is not None:
        legacy_removed = remove_legacy_launch_agent(legacy_target)
    changed = changed or legacy_removed

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
    launchctl = str(trusted_system_tool("launchctl"))
    subprocess.run(
        [launchctl, "bootstrap", launch_domain(), str(plist_path)],
        check=True,
    )
    subprocess.run(
        [launchctl, "kickstart", "-k", f"{launch_domain()}/{LAUNCH_AGENT_LABEL}"],
        check=False,
    )


def launch_agent_running() -> bool:
    result = subprocess.run(
        [
            str(trusted_system_tool("launchctl")),
            "print",
            f"{launch_domain()}/{LAUNCH_AGENT_LABEL}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def bootout_launch_agent(plist_path: Path) -> None:
    subprocess.run(
        [str(trusted_system_tool("launchctl")), "bootout", launch_domain(), str(plist_path)],
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


def launch_agent_path_env(_python_executable: str) -> str:
    return "/usr/bin:/bin:/usr/sbin:/sbin"


def production_bundle_executable(executable: Path | str | None = None) -> Path:
    """Resolve the current frozen PyInstaller executable inside SidePulse.app."""
    candidate = Path(executable or sys.executable or "")
    if not candidate.is_absolute():
        raise RuntimeError("packaged SidePulse executable path must be absolute")
    if candidate.name != "SidePulse" or candidate.parent.name != "MacOS":
        raise RuntimeError(f"packaged SidePulse executable has an unexpected path: {candidate}")
    contents = candidate.parent.parent
    bundle = contents.parent
    if contents.name != "Contents" or bundle.name != "SidePulse.app":
        raise RuntimeError(f"packaged SidePulse executable is not inside SidePulse.app: {candidate}")

    for path, expected_type in (
        (bundle, stat.S_ISDIR),
        (contents, stat.S_ISDIR),
        (candidate.parent, stat.S_ISDIR),
        (candidate, stat.S_ISREG),
    ):
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RuntimeError(f"packaged SidePulse path is missing: {path}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError(f"packaged SidePulse path must not be a symlink: {path}")
        if not expected_type(metadata.st_mode):
            raise RuntimeError(f"packaged SidePulse path has an unexpected type: {path}")

    try:
        resolved_bundle = bundle.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"packaged SidePulse path cannot be resolved: {candidate}") from exc
    expected_candidate = resolved_bundle / "Contents" / "MacOS" / "SidePulse"
    if resolved_candidate != expected_candidate:
        raise RuntimeError(
            f"packaged SidePulse executable resolves outside its expected bundle path: {candidate}"
        )
    if not candidate.lstat().st_mode & 0o111:
        raise RuntimeError(f"packaged SidePulse executable is missing or not executable: {candidate}")
    return candidate
