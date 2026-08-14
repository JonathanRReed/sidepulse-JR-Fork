from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"


def run_sidepulse_module(temp_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "HOME": str(temp_root / "home"),
        "PYTHONPATH": str(SOURCE_ROOT),
        "XDG_CONFIG_HOME": str(temp_root / "config"),
        "XDG_STATE_HOME": str(temp_root / "state"),
    }
    return subprocess.run(
        [sys.executable, "-m", "sidepulse", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


class ModuleEntrypointTests(unittest.TestCase):
    def test_module_agent_monitor_help_begins_with_nested_cli_help(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_sidepulse_module(Path(temp_dir), "agent-monitor", "--help")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.startswith("usage: sidepulse agent-monitor "), result.stdout)

    def test_module_help_begins_with_sidepulse_cli_help(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_sidepulse_module(Path(temp_dir), "--help")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.stdout.startswith("usage: sidepulse "), result.stdout)

    def test_module_machine_mode_emits_one_json_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_sidepulse_module(Path(temp_dir), "agent-monitor", "doctor", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(
            set(payload),
            {"document", "findings", "last_failure_class", "version"},
        )
        self.assertEqual(payload["document"], "sidepulse-doctor")
        self.assertEqual(payload["version"], 1)
        self.assertEqual(
            tuple(finding["check"] for finding in payload["findings"]),
            (
                "package_import_root",
                "signature_state",
                "launch_agent_state",
                "private_path_modes",
                "hook_detector_state",
                "negotiated_source_health",
                "worker_registry_bounds",
                "timer_registry_bounds",
                "mounted_device_health",
            ),
        )
        self.assertNotIn(str(Path(temp_dir)), result.stdout)

    def test_module_cli_diagnostics_are_stderr_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_sidepulse_module(Path(temp_dir), "not-a-command")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("usage: sidepulse "), result.stderr)

    def test_module_unsupported_version_is_stderr_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_sidepulse_module(Path(temp_dir), "--version")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.startswith("usage: sidepulse "), result.stderr)
        self.assertIn("sidepulse: error:", result.stderr)


if __name__ == "__main__":
    unittest.main()
