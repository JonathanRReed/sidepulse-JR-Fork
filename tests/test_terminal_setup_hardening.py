from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse import status_bar
from sidepulse.private_io import atomic_private_write


def test_atomic_private_write_can_publish_owner_executable(tmp_path: Path) -> None:
    target = tmp_path / "private" / "setup.command"

    atomic_private_write(target, "#!/bin/zsh\nexit 0\n", mode=0o700)

    assert target.read_text() == "#!/bin/zsh\nexit 0\n"
    assert target.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("mode", [0o755, 0o644, 0o777])
def test_atomic_private_write_rejects_non_private_modes(
    tmp_path: Path,
    mode: int,
) -> None:
    target = tmp_path / "private" / "setup.command"

    with pytest.raises(ValueError, match="private file mode"):
        atomic_private_write(target, "payload", mode=mode)

    assert not target.exists()


@pytest.mark.parametrize("leaf_kind", ["symlink", "hardlink"])
def test_setup_command_refuses_linked_leaf_without_launching(
    tmp_path: Path,
    leaf_kind: str,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    destination = tmp_path / "destination"
    destination.write_text("keep me")
    script = state_dir / "install-sleep-helper.command"
    if leaf_kind == "symlink":
        script.symlink_to(destination)
    else:
        os.link(destination, script)

    with (
        patch("sidepulse.status_bar.default_state_dir", return_value=state_dir),
        patch("sidepulse.status_bar.subprocess.Popen") as popen,
        pytest.raises(OSError),
    ):
        status_bar.open_terminal_setup_command("echo hello")

    assert destination.read_text() == "keep me"
    popen.assert_not_called()


def test_setup_command_refuses_symlinked_state_directory_without_launching(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "destination"
    destination.mkdir()
    state_dir = tmp_path / "state"
    state_dir.symlink_to(destination, target_is_directory=True)

    with (
        patch("sidepulse.status_bar.default_state_dir", return_value=state_dir),
        patch("sidepulse.status_bar.subprocess.Popen") as popen,
        pytest.raises(OSError),
    ):
        status_bar.open_terminal_setup_command("echo hello")

    assert list(destination.iterdir()) == []
    popen.assert_not_called()


@pytest.mark.parametrize("filename", ["", ".", "../escape.command", "nested/setup.command"])
def test_setup_command_rejects_non_basename_filename_without_launching(
    tmp_path: Path,
    filename: str,
) -> None:
    with (
        patch("sidepulse.status_bar.default_state_dir", return_value=tmp_path),
        patch("sidepulse.status_bar.subprocess.Popen") as popen,
        pytest.raises(ValueError, match="filename"),
    ):
        status_bar.open_terminal_setup_command("echo hello", filename=filename)

    popen.assert_not_called()


def test_setup_command_publishes_mode_700_before_expected_open_argv(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    terminal = Path("/System/Applications/Utilities/Terminal.app")
    observed_mode: list[int] = []

    def observe_launch(arguments, **_kwargs):
        observed_mode.append((state_dir / "setup.command").stat().st_mode & 0o777)
        return object()

    with (
        patch("sidepulse.status_bar.default_state_dir", return_value=state_dir),
        patch("sidepulse.status_bar._installed_terminal_application", return_value=terminal),
        patch("sidepulse.status_bar.subprocess.Popen", side_effect=observe_launch) as popen,
    ):
        script = status_bar.open_terminal_setup_command(
            "echo hello",
            filename="setup.command",
        )

    assert script == state_dir / "setup.command"
    assert script.stat().st_mode & 0o777 == 0o700
    assert observed_mode == [0o700]
    assert popen.call_args.args[0] == [
        "/usr/bin/open",
        "-a",
        str(terminal),
        str(script),
    ]
