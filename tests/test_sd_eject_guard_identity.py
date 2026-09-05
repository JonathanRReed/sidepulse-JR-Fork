from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.sd_eject_guard_launch import (
    SdEjectGuardInstallError,
    SdEjectGuardPaths,
    build_sd_eject_guard_plist,
    run_sd_eject_guard_interactive,
)

VOLUME_UUID = "12345678-1234-4ABC-9DEF-1234567890AB"


def paths(tmp_path: Path) -> SdEjectGuardPaths:
    return SdEjectGuardPaths(
        scope="user",
        plist_path=tmp_path / "guard.plist",
        binary_path=tmp_path / "guard",
        stdout_path=tmp_path / "guard.out.log",
        stderr_path=tmp_path / "guard.err.log",
    )


def test_selected_volume_uuid_enables_exact_launch_job(tmp_path: Path) -> None:
    target = paths(tmp_path)

    plist = build_sd_eject_guard_plist(target, volume_uuid=VOLUME_UUID)

    assert plist["ProgramArguments"] == [
        str(target.binary_path),
        "--volume-uuid",
        VOLUME_UUID,
    ]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True


def test_invalid_volume_uuid_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SdEjectGuardInstallError, match="volume UUID"):
        build_sd_eject_guard_plist(paths(tmp_path), volume_uuid="../../disk2")


def test_interactive_guard_passes_exact_selected_identity(tmp_path: Path) -> None:
    target = paths(tmp_path)
    completed = subprocess.CompletedProcess([], 0)

    with (
        patch(
            "sidepulse.sd_eject_guard_launch.ensure_sd_eject_guard_binary",
            return_value=target,
        ),
        patch(
            "sidepulse.sd_eject_guard_launch.subprocess.run",
            return_value=completed,
        ) as run,
    ):
        result = run_sd_eject_guard_interactive(
            scope="user",
            volume_uuid=VOLUME_UUID,
        )

    assert result == 0
    run.assert_called_once_with(
        [str(target.binary_path), "--volume-uuid", VOLUME_UUID],
        check=False,
    )
