from __future__ import annotations

import subprocess
import sys


def test_automatic_gap_fallback_is_stable_when_settings_imports_first() -> None:
    script = """
from sidepulse import screen_bar_design, settings_window, virtual_device
from sidepulse.screen_bar_runtime import install_screen_bar_runtime

install_screen_bar_runtime()
assert settings_window.SCREEN_BAR_AUTOMATIC_GAP_FALLBACK == screen_bar_design.WINDOW_WIDTH
assert virtual_device.WINDOW_WIDTH == screen_bar_design.WINDOW_WIDTH
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
