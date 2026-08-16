#!/usr/bin/env python3
"""Install the built wheel into an empty environment and test shipped surfaces."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def run(*arguments: object, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONPATH": "",
    }
    return subprocess.run(
        [str(argument) for argument in arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=900,
        env=environment,
    )


def wheel_path() -> Path:
    wheels = sorted(DIST.glob("sidepulse-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one SidePulse wheel in {DIST}, found {len(wheels)}")
    return wheels[0]


def main() -> int:
    wheel = wheel_path()
    with tempfile.TemporaryDirectory(prefix="sidepulse-clean-install-") as directory:
        root = Path(directory)
        environment = root / "venv"
        run(sys.executable, "-m", "venv", environment)
        python = environment / "bin" / "python"
        run(python, "-m", "pip", "install", wheel)

        probe = """
import importlib.metadata
import importlib.resources
import sidepulse

assert importlib.metadata.version("sidepulse") == sidepulse.__version__
resources = importlib.resources.files("sidepulse.resources")
assert (resources / "sdled.wasm").is_file()
assert (resources / "sd_eject_guard.c").is_file()
assert (resources / "integration_compatibility.json").is_file()
for module in (
    "sidepulse.cli",
    "sidepulse.cli_entry",
    "sidepulse.codexbar_compat",
    "sidepulse.hook_entry",
    "sidepulse.integration_cli",
    "sidepulse.integration_compatibility",
    "sidepulse.integration_settings",
    "sidepulse.status_bar_launch",
    "sidepulse.t3_compat",
    "agent_monitor.hook_entry",
    "sidepulse_cli.hook_entry",
):
    __import__(module)
"""
        run(python, "-c", probe, cwd=root)

        bin_dir = environment / "bin"
        for name in (
            "sidepulse",
            "sidepulse-integrations",
            "agent-monitor",
            "agent-status-bar",
        ):
            path = bin_dir / name
            if not path.is_file() or not os.access(path, os.X_OK):
                raise RuntimeError(f"missing installed console script: {path}")

        run(bin_dir / "sidepulse", "--help", cwd=root)
        run(bin_dir / "sidepulse", "integrations", "status", "--json", cwd=root)
        run(bin_dir / "sidepulse-integrations", "status", "--json", cwd=root)
        run(bin_dir / "agent-monitor", "--help", cwd=root)
        run(python, "-m", "agent_monitor.hook_entry", cwd=root)
        run(python, "-m", "sidepulse_cli.hook_entry", cwd=root)

        if platform.system() == "Darwin":
            run(python, "-c", "import sidepulse.status_bar", cwd=root)

    print(f"Clean-install verification passed: {wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
