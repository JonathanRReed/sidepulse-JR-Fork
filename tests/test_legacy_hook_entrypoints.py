from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    (
        "sidepulse.hook_entry",
        "sidepulse_cli.hook_entry",
        "agent_monitor.hook_entry",
    ),
)
def test_legacy_hook_modules_fail_open_without_arguments(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", module],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == ""
