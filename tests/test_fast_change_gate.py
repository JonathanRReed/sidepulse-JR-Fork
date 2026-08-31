from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _gate():
    return importlib.import_module("scripts.verify_fast")


def test_fast_gate_has_explicit_ordered_evidence_layers() -> None:
    gate = _gate()
    steps = gate.build_steps(
        python="/tmp/jr-bar-python",
        root=ROOT,
        fix=False,
    )

    assert tuple(step.name for step in steps) == (
        "Ruff",
        "Import smoke",
        "Contract tests",
        "Tracked secret scan",
        "Fixture validation",
        "Focused tests",
        "Bytecode compilation",
        "Dependency policy",
        "Version contract",
        "Diff hygiene",
    )
    assert all(type(step.command) is tuple for step in steps)
    import_smoke = next(step.command for step in steps if step.name == "Import smoke")
    assert "sidepulse._status_bar_production" in import_smoke[-1]
    assert "sidepulse.adaptive_refresh" in import_smoke[-1]


def test_fast_gate_never_contains_expensive_or_mutating_release_work() -> None:
    gate = _gate()
    steps = gate.build_steps(
        python="/tmp/jr-bar-python",
        root=ROOT,
        fix=False,
    )
    commands = tuple(step.command for step in steps)
    words = {word for command in commands for word in command}
    joined = "\n".join(" ".join(command) for command in commands)

    pytest_commands = [command for command in commands if command[1:4] == ("-m", "pytest", "-q")]
    assert pytest_commands
    assert all("tests" not in command[4:] for command in pytest_commands)
    assert "pip" not in words
    assert "build" not in words
    assert "rm" not in words
    assert "twine" not in words
    assert "verify_macos_release.sh" not in joined
    assert "verify_hardware_release.py" not in joined
    assert "verify_clean_install.py" not in joined
    assert "notary" not in joined.casefold()
    assert "instruments" not in joined.casefold()
    assert "publish" not in joined.casefold()


def test_fast_gate_keeps_contract_fixture_and_focused_tests_separate() -> None:
    gate = _gate()
    steps = gate.build_steps(
        python="/tmp/jr-bar-python",
        root=ROOT,
        fix=False,
    )
    by_name = {step.name: step.command for step in steps}

    assert by_name["Contract tests"][-len(gate.CONTRACT_TESTS) :] == gate.CONTRACT_TESTS
    assert by_name["Fixture validation"][-len(gate.FIXTURE_TESTS) :] == gate.FIXTURE_TESTS
    assert by_name["Focused tests"][-len(gate.FOCUSED_TESTS) :] == gate.FOCUSED_TESTS
    assert set(gate.CONTRACT_TESTS).isdisjoint(gate.FIXTURE_TESTS)
    assert set(gate.CONTRACT_TESTS).isdisjoint(gate.FOCUSED_TESTS)
    assert set(gate.FIXTURE_TESTS).isdisjoint(gate.FOCUSED_TESTS)


def test_fast_gate_fixture_lane_ends_with_provider_fixture_ownership() -> None:
    gate = _gate()

    assert gate.FIXTURE_TESTS[-1] == "tests/test_provider_fixture_ownership.py"


def test_fast_gate_covers_provider_architecture_boundaries() -> None:
    gate = _gate()

    assert {
        "tests/test_adaptive_refresh_acceptance.py",
        "tests/test_provider_feature_settings.py",
        "tests/test_provider_contracts.py",
        "tests/test_provider_instances.py",
        "tests/test_provider_state_boundaries.py",
        "tests/test_provider_usage_controller_actions.py",
        "tests/test_provider_usage_runtime.py",
        "tests/test_provider_usage_store.py",
        "tests/test_provider_usage_sync.py",
    }.issubset(gate.FOCUSED_TESTS)


def test_fast_gate_stops_on_first_failure_and_preserves_its_status() -> None:
    gate = _gate()
    steps = gate.build_steps(
        python="/tmp/jr-bar-python",
        root=ROOT,
        fix=False,
    )[:3]
    calls: list[tuple[tuple[str, ...], Path, bool]] = []

    def run(command, *, cwd, check):
        calls.append((command, cwd, check))
        return SimpleNamespace(returncode=7 if len(calls) == 2 else 0)

    assert gate.run_steps(steps, root=ROOT, runner=run) == 7
    assert [call[0] for call in calls] == [steps[0].command, steps[1].command]
    assert all(call[1:] == (ROOT, False) for call in calls)


def test_fix_mode_adds_only_one_leading_safe_fix_step() -> None:
    gate = _gate()
    ordinary = gate.build_steps(
        python="/tmp/jr-bar-python",
        root=ROOT,
        fix=False,
    )
    fixing = gate.build_steps(
        python="/tmp/jr-bar-python",
        root=ROOT,
        fix=True,
    )

    assert fixing[0].name == "Ruff safe fixes"
    assert fixing[0].command[:5] == (
        "/tmp/jr-bar-python",
        "-m",
        "ruff",
        "check",
        "--fix",
    )
    assert fixing[1:] == ordinary


def test_fast_gate_list_mode_is_read_only(capsys) -> None:
    gate = _gate()

    assert gate.main(["--list"]) == 0
    output = capsys.readouterr().out

    for name in (
        "Ruff",
        "Import smoke",
        "Contract tests",
        "Tracked secret scan",
        "Fixture validation",
        "Focused tests",
    ):
        assert name in output
    assert "tests/test_provider_fixture_ownership.py" in output
    assert "JR Bar fast gate passed" not in output


def test_makefile_and_hygiene_docs_expose_the_real_fast_gate() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    hygiene = (ROOT / "docs" / "REPOSITORY-HYGIENE.md").read_text(encoding="utf-8")

    assert "fast:" in makefile
    assert ".venv/bin/python scripts/verify_fast.py" in makefile
    assert "make fast" in hygiene
    assert "--targeted" not in hygiene
    assert "--no-build" not in hygiene


def test_release_source_receipt_cannot_rebuild_or_clean_the_candidate() -> None:
    release_gate = (ROOT / "scripts" / "verify_macos_release.sh").read_text(
        encoding="utf-8"
    )

    assert (
        "record_receipt source-gate \"$pkg\" ./scripts/verify.sh "
        "--no-bootstrap --skip-build --skip-clean-install"
    ) in release_gate
