from __future__ import annotations

import inspect
import plistlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.status_bar_launch import (
    GHOSTTY_BUNDLE_IDENTIFIER,
    ITERM_BUNDLE_IDENTIFIER,
    TERMINAL_BUNDLE_IDENTIFIER,
    TerminalLaunchKind,
    _validated_ghostty_executable,
    resolve_terminal_launch,
    terminal_launch_arguments,
    terminal_navigation_requires_apple_events,
)


def _make_ghostty_bundle(
    root: Path,
    *,
    identifier: str = GHOSTTY_BUNDLE_IDENTIFIER,
    executable_name: str = "ghostty",
) -> Path:
    bundle = root / "Ghostty.app"
    executable = bundle / "Contents" / "MacOS" / executable_name
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"ghostty")
    executable.chmod(0o755)
    (bundle / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": identifier,
                "CFBundleExecutable": executable_name,
            }
        )
    )
    return bundle


def _valid_codesign(command, **_kwargs):
    arguments = [str(part) for part in command]
    assert arguments[:4] == [
        "/usr/bin/codesign",
        "--verify",
        "--deep",
        "--strict",
    ]
    return subprocess.CompletedProcess(arguments, 0, "", "")


def test_reviewed_terminal_matrix_routes_exact_hosts_and_product_fallback(
    tmp_path: Path,
) -> None:
    ghostty = _make_ghostty_bundle(tmp_path) / "Contents" / "MacOS" / "ghostty"

    terminal = resolve_terminal_launch(TERMINAL_BUNDLE_IDENTIFIER)
    iterm = resolve_terminal_launch(ITERM_BUNDLE_IDENTIFIER)
    with patch(
        "sidepulse._status_bar_launch_legacy.resolve_ghostty_executable",
        return_value=ghostty,
    ):
        ghostty_plan = resolve_terminal_launch(GHOSTTY_BUNDLE_IDENTIFIER)
    unsupported = resolve_terminal_launch("dev.example.mutable-terminal")

    assert (terminal.kind, terminal.selected_bundle_identifier) == (
        TerminalLaunchKind.APPLE_EVENTS,
        TERMINAL_BUNDLE_IDENTIFIER,
    )
    assert (iterm.kind, iterm.selected_bundle_identifier) == (
        TerminalLaunchKind.APPLE_EVENTS,
        ITERM_BUNDLE_IDENTIFIER,
    )
    assert (ghostty_plan.kind, ghostty_plan.executable_path) == (
        TerminalLaunchKind.EXECUTABLE,
        ghostty,
    )
    assert (unsupported.kind, unsupported.selected_bundle_identifier) == (
        TerminalLaunchKind.APPLE_EVENTS,
        TERMINAL_BUNDLE_IDENTIFIER,
    )
    assert unsupported.fallback_copy == (
        "SidePulse does not support this terminal yet, so it opened Terminal."
    )


def test_terminal_launch_arguments_are_literal_absolute_and_do_not_use_shell_search(
    tmp_path: Path,
) -> None:
    ghostty = _make_ghostty_bundle(tmp_path) / "Contents" / "MacOS" / "ghostty"
    command = "cd '/tmp/project with spaces' && codex resume session-1"

    terminal = terminal_launch_arguments(
        resolve_terminal_launch(TERMINAL_BUNDLE_IDENTIFIER),
        command,
    )
    iterm = terminal_launch_arguments(
        resolve_terminal_launch(ITERM_BUNDLE_IDENTIFIER),
        command,
    )
    with patch(
        "sidepulse._status_bar_launch_legacy.resolve_ghostty_executable",
        return_value=ghostty,
    ):
        ghostty_arguments = terminal_launch_arguments(
            resolve_terminal_launch(GHOSTTY_BUNDLE_IDENTIFIER),
            command,
        )

    assert terminal[0:2] == ("/usr/bin/osascript", "-e")
    assert 'tell application id "com.apple.Terminal"' in terminal[2]
    assert iterm[0:2] == ("/usr/bin/osascript", "-e")
    assert 'tell application id "com.googlecode.iterm2"' in iterm[2]
    assert ghostty_arguments == (
        str(ghostty),
        "+new-window",
        "-e",
        "/bin/zsh",
        "-lc",
        command,
    )
    assert all(part not in {"ghostty", "osascript", "sh", "zsh"} for part in terminal)
    assert all(part not in {"ghostty", "osascript", "sh", "zsh"} for part in ghostty_arguments)


def test_ghostty_resolver_requires_reviewed_identity_signature_and_real_paths(
    tmp_path: Path,
) -> None:
    bundle = _make_ghostty_bundle(tmp_path)
    executable = bundle / "Contents" / "MacOS" / "ghostty"

    assert _validated_ghostty_executable(bundle, command_runner=_valid_codesign) == executable

    wrong_identity = _make_ghostty_bundle(
        tmp_path / "wrong-identity",
        identifier="dev.example.ghostty-copy",
    )
    assert (
        _validated_ghostty_executable(
            wrong_identity,
            command_runner=_valid_codesign,
        )
        is None
    )
    symlinked = _make_ghostty_bundle(tmp_path / "symlinked")
    symlinked_executable = symlinked / "Contents" / "MacOS" / "ghostty"
    symlinked_executable.unlink()
    symlinked_executable.symlink_to("/usr/bin/true")
    assert (
        _validated_ghostty_executable(
            symlinked,
            command_runner=_valid_codesign,
        )
        is None
    )

    invalid_signature = _make_ghostty_bundle(tmp_path / "invalid-signature")

    def rejected_codesign(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", "invalid signature")

    assert (
        _validated_ghostty_executable(
            invalid_signature,
            command_runner=rejected_codesign,
        )
        is None
    )


def test_terminal_resolver_has_no_caller_selected_executable_bypass() -> None:
    assert "ghostty_executable" not in inspect.signature(
        resolve_terminal_launch
    ).parameters


def test_ghostty_resolution_checks_only_reviewed_absolute_application_paths(
    tmp_path: Path,
) -> None:
    bundle = _make_ghostty_bundle(tmp_path)
    executable = bundle / "Contents" / "MacOS" / "ghostty"

    with (
        patch(
            "sidepulse._status_bar_launch_legacy.GHOSTTY_APPLICATION_PATHS",
            (bundle,),
        ),
        patch(
            "sidepulse._status_bar_launch_legacy.subprocess.run",
            side_effect=_valid_codesign,
        ),
        patch("sidepulse._status_bar_launch_legacy.Path.home", side_effect=AssertionError),
    ):
        plan = resolve_terminal_launch(GHOSTTY_BUNDLE_IDENTIFIER)

    assert plan.kind is TerminalLaunchKind.EXECUTABLE
    assert plan.executable_path == executable


def test_unavailable_ghostty_uses_terminal_with_specific_product_copy() -> None:
    with patch(
        "sidepulse._status_bar_launch_legacy.GHOSTTY_APPLICATION_PATHS",
        (Path("/Applications/DefinitelyMissingGhostty.app"),),
    ):
        plan = resolve_terminal_launch(GHOSTTY_BUNDLE_IDENTIFIER)

    assert plan.kind is TerminalLaunchKind.APPLE_EVENTS
    assert plan.selected_bundle_identifier == TERMINAL_BUNDLE_IDENTIFIER
    assert plan.fallback_copy == (
        "SidePulse could not verify Ghostty, so it opened Terminal."
    )


@pytest.mark.parametrize(
    ("bundle_identifiers", "fallback_to_terminal", "expected"),
    [
        ((TERMINAL_BUNDLE_IDENTIFIER,), False, True),
        ((ITERM_BUNDLE_IDENTIFIER,), False, True),
        ((GHOSTTY_BUNDLE_IDENTIFIER,), False, False),
        ((GHOSTTY_BUNDLE_IDENTIFIER,), True, True),
        ((), False, False),
    ],
)
def test_apple_events_requirement_follows_only_reviewed_runtime_actions(
    bundle_identifiers: tuple[str, ...],
    fallback_to_terminal: bool,
    expected: bool,
) -> None:
    assert (
        terminal_navigation_requires_apple_events(
            bundle_identifiers,
            fallback_to_terminal=fallback_to_terminal,
        )
        is expected
    )


def test_status_bar_executes_each_reviewed_plan_and_logs_only_real_fallback(
    tmp_path: Path,
) -> None:
    try:
        from sidepulse import status_bar
    except (ImportError, SystemExit) as exc:
        pytest.skip(str(exc))

    bundle = _make_ghostty_bundle(tmp_path)
    launched: list[tuple[str, ...]] = []

    def popen(arguments, **_kwargs):
        launched.append(tuple(str(part) for part in arguments))
        return subprocess.CompletedProcess(arguments, 0, "", "")

    with (
        patch.object(status_bar.subprocess, "Popen", side_effect=popen),
        patch.object(status_bar, "log_status_bar") as log,
        patch(
            "sidepulse._status_bar_launch_legacy.GHOSTTY_APPLICATION_PATHS",
            (bundle,),
        ),
        patch(
            "sidepulse._status_bar_launch_legacy.subprocess.run",
            side_effect=_valid_codesign,
        ),
    ):
        terminal = status_bar.open_terminal_command(
            "echo terminal",
            terminal_bundle_identifier=TERMINAL_BUNDLE_IDENTIFIER,
        )
        iterm = status_bar.open_terminal_command(
            "echo iterm",
            terminal_bundle_identifier=ITERM_BUNDLE_IDENTIFIER,
        )
        ghostty = status_bar.open_terminal_command(
            "echo ghostty",
            terminal_bundle_identifier=GHOSTTY_BUNDLE_IDENTIFIER,
        )
        unsupported = status_bar.open_terminal_command(
            "echo fallback",
            terminal_bundle_identifier="dev.example.unsupported",
        )

    assert [plan.kind for plan in (terminal, iterm, ghostty, unsupported)] == [
        TerminalLaunchKind.APPLE_EVENTS,
        TerminalLaunchKind.APPLE_EVENTS,
        TerminalLaunchKind.EXECUTABLE,
        TerminalLaunchKind.APPLE_EVENTS,
    ]
    assert launched[0][0:2] == ("/usr/bin/osascript", "-e")
    assert launched[1][0:2] == ("/usr/bin/osascript", "-e")
    assert launched[2] == (
        str(bundle / "Contents" / "MacOS" / "ghostty"),
        "+new-window",
        "-e",
        "/bin/zsh",
        "-lc",
        "echo ghostty",
    )
    assert launched[3][0:2] == ("/usr/bin/osascript", "-e")
    log.assert_called_once_with(
        "SidePulse does not support this terminal yet, so it opened Terminal."
    )
