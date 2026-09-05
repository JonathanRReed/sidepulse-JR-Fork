from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS_BAR = ROOT / "src" / "sidepulse" / "status_bar_legacy.py"
USAGE_STATUS_BAR = ROOT / "src" / "sidepulse" / "provider_usage_status_bar.py"
PERCENT_HISTORY = ROOT / "src" / "sidepulse" / "usage_percent_history.py"
CAPACITY_HISTORY_RUNTIME = (
    ROOT / "src" / "sidepulse" / "capacity_history_runtime.py"
)


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1, f"expected one {name}, found {len(matches)}"
    return matches[0]


def _calls(node: ast.AST) -> list[str]:
    result = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name):
            result.append(child.func.id)
        elif isinstance(child.func, ast.Attribute):
            result.append(child.func.attr)
    return result


def _qualified_calls(node: ast.AST) -> list[str]:
    result = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name):
            result.append(child.func.id)
        elif isinstance(child.func, ast.Attribute):
            result.append(ast.unparse(child.func))
    return result


def test_percent_and_reset_state_use_the_shared_writer() -> None:
    percent = _function(PERCENT_HISTORY, "record_state_observations")
    usage_apply = _function(USAGE_STATUS_BAR, "applyProviderUsageState_")
    reset_persist = _function(USAGE_STATUS_BAR, "_persist_reset_delivery_state")

    assert "submit" in _calls(percent)
    assert "Thread" not in _calls(percent)
    assert "_persist_reset_delivery_state" in _calls(usage_apply)
    assert "submit" in _calls(reset_persist)
    assert "_persist_reset_delivery_state" in _calls(usage_apply)
    assert "Thread" not in _calls(usage_apply)


def test_operator_history_write_paths_use_the_shared_writer() -> None:
    retention = _function(STATUS_BAR, "start_operator_history_retention_change")
    events = _function(STATUS_BAR, "_enqueue_operator_history_events")

    assert "submit" in _calls(retention)
    assert "submit" in _calls(events)
    assert "Thread" not in _calls(retention)
    assert "Thread" not in _calls(events)


def test_capacity_reconciliation_queues_flush_and_fences_store_identity() -> None:
    record = _function(STATUS_BAR, "record_capacity_history")
    flush = _function(STATUS_BAR, "_flush_capacity_history_store")
    runtime_record = _function(CAPACITY_HISTORY_RUNTIME, "record_capacity_history_runtime")
    runtime_flush = _function(CAPACITY_HISTORY_RUNTIME, "flush_capacity_history_store_runtime")

    assert "record_capacity_history_runtime" in _calls(record)
    assert "flush_capacity_history_store_runtime" in _calls(flush)
    assert "submit" in _calls(runtime_record)
    assert "flush" not in _calls(runtime_record)
    assert "flush" in _calls(runtime_flush)
    flush_names = {
        child.id for child in ast.walk(runtime_flush) if isinstance(child, ast.Name)
    }
    assert "generation" in flush_names
    flush_attributes = {
        child.attr for child in ast.walk(runtime_flush) if isinstance(child, ast.Attribute)
    }
    assert "_capacity_history_generation" in flush_attributes


def test_termination_force_submits_before_one_drain_and_quit_keeps_its_routes() -> None:
    terminate = _function(STATUS_BAR, "applicationWillTerminate_")
    quit_action = _function(STATUS_BAR, "quit_")

    termination_calls = _calls(terminate)
    assert "_flush_capacity_history_store" in termination_calls

    qualified_termination_calls = _qualified_calls(terminate)
    persistence_submit = "self._persistence_writer.submit"
    persistence_close = "self._persistence_writer.close"
    assert qualified_termination_calls.count(persistence_close) == 1
    assert qualified_termination_calls.index(
        persistence_submit
    ) < qualified_termination_calls.index(persistence_close)

    quit_calls = _calls(quit_action)
    assert "applicationWillTerminate_" in quit_calls
    assert "terminate_" in quit_calls
    assert "close" not in quit_calls
