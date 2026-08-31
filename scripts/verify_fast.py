#!/usr/bin/env python3
"""Fast, fail-closed verification for ordinary JR Bar source changes."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
CHECK_PATHS: Final = ("src", "tests", "packaging", "scripts")
IMPORT_MODULES: Final = (
    "sidepulse",
    "sidepulse.adaptive_refresh",
    "sidepulse.hook_client",
    "sidepulse.settings",
    "sidepulse.status_bar",
    "sidepulse._status_bar_production",
    "sidepulse.provider_usage_controller_actions",
    "sidepulse.provider_usage_status_bar",
    "sidepulse.why_light_context",
)
CONTRACT_TESTS: Final = (
    "tests/test_architecture_ratchets.py",
    "tests/test_deterministic_timing_contract.py",
    "tests/test_status_bar_facade_contract.py",
    "tests/test_status_bar_adapter_reload_contract.py",
    "tests/test_provider_usage_status_bar_contract.py",
    "tests/test_provider_usage_window_contract.py",
    "tests/test_settings_window_injection_ratchet.py",
    "tests/test_workflow_contract.py",
    "tests/test_repository_hygiene.py",
    "tests/test_build_script_contract.py",
    "tests/test_executable_packaging.py",
    "tests/test_release_gate_contract.py",
    "tests/test_dependency_and_entitlements.py",
)
FIXTURE_TESTS: Final = (
    "tests/test_provider_adapters.py",
    "tests/test_provider_usage_parsers.py",
    "tests/test_integration_compatibility_manifest.py",
    "tests/test_settings_schema_coverage.py",
    "tests/test_provider_fixture_ownership.py",
)
FOCUSED_TESTS: Final = (
    "tests/test_adaptive_refresh_acceptance.py",
    "tests/test_core_state.py",
    "tests/test_core_state_determinism.py",
    "tests/test_refresh_admission.py",
    "tests/test_presentation_policy.py",
    "tests/test_presentation_safety_compiler.py",
    "tests/test_hook_client.py",
    "tests/test_hook_ingress.py",
    "tests/test_alcove_observation.py",
    "tests/test_macos_notifications.py",
    "tests/test_provider_usage_sync_service.py",
    "tests/test_provider_usage_controller_actions.py",
    "tests/test_provider_feature_settings.py",
    "tests/test_provider_contracts.py",
    "tests/test_provider_instances.py",
    "tests/test_provider_state_boundaries.py",
    "tests/test_provider_usage_settings.py",
    "tests/test_provider_usage_runtime.py",
    "tests/test_provider_usage_store.py",
    "tests/test_provider_usage_sync.py",
    "tests/test_provider_usage_sync_projection.py",
    "tests/test_provider_usage_menu.py",
    "tests/test_provider_usage_center.py",
    "tests/test_usage_event_hooks.py",
    "tests/test_usage_graph_worker.py",
    "tests/test_local_health.py",
    "tests/test_why_light_context.py",
    "tests/test_why_light_projection.py",
    "tests/test_why_panel.py",
    "tests/test_settings_accessibility.py",
)


@dataclass(frozen=True, slots=True)
class GateStep:
    name: str
    command: tuple[str, ...]


def _pytest_step(python: str, name: str, tests: tuple[str, ...]) -> GateStep:
    return GateStep(name, (python, "-m", "pytest", "-q", *tests))


def build_steps(*, python: str, root: Path, fix: bool) -> tuple[GateStep, ...]:
    """Build the complete auditable step list without executing anything."""
    import_code = (
        "import importlib; "
        f"names={IMPORT_MODULES!r}; "
        "[importlib.import_module(name) for name in names]"
    )
    ordinary = (
        GateStep("Ruff", (python, "-m", "ruff", "check", *CHECK_PATHS)),
        GateStep("Import smoke", (python, "-c", import_code)),
        _pytest_step(python, "Contract tests", CONTRACT_TESTS),
        GateStep(
            "Tracked secret scan",
            (python, str(root / "scripts" / "scan_secrets.py"), "--root", str(root)),
        ),
        _pytest_step(python, "Fixture validation", FIXTURE_TESTS),
        _pytest_step(python, "Focused tests", FOCUSED_TESTS),
        GateStep(
            "Bytecode compilation",
            (python, "-m", "compileall", "-q", *CHECK_PATHS),
        ),
        GateStep(
            "Dependency policy",
            (
                python,
                str(root / "scripts" / "verify_dependency_policy.py"),
                "--root",
                str(root),
            ),
        ),
        GateStep(
            "Version contract",
            (python, str(root / "scripts" / "validate_release_version.py")),
        ),
        GateStep("Diff hygiene", ("git", "diff", "--check")),
    )
    if not fix:
        return ordinary
    return (
        GateStep(
            "Ruff safe fixes",
            (python, "-m", "ruff", "check", "--fix", *CHECK_PATHS),
        ),
        *ordinary,
    )


def run_steps(
    steps: tuple[GateStep, ...],
    *,
    root: Path,
    runner=subprocess.run,
) -> int:
    """Run in order, stopping at and preserving the first failure code."""
    gate_started = time.perf_counter()
    for step in steps:
        started = time.perf_counter()
        print(f"==> {step.name}", flush=True)
        result = runner(step.command, cwd=root, check=False)
        elapsed = time.perf_counter() - started
        if result.returncode != 0:
            print(
                f"JR Bar fast gate stopped at {step.name} "
                f"after {elapsed:.2f}s (exit {result.returncode}).",
                file=sys.stderr,
                flush=True,
            )
            return int(result.returncode)
        print(f"    passed in {elapsed:.2f}s", flush=True)
    print(
        f"JR Bar fast gate passed in {time.perf_counter() - gate_started:.2f}s.",
        flush=True,
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="Apply Ruff safe fixes first.")
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the exact ordered commands without running them.",
    )
    return parser


def main(arguments: list[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    steps = build_steps(python=sys.executable, root=ROOT, fix=options.fix)
    if options.list:
        for step in steps:
            print(f"{step.name}: {shlex.join(step.command)}")
        return 0
    return run_steps(steps, root=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
