from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS_BAR = ROOT / "src" / "sidepulse" / "status_bar.py"
BACKGROUND_MODULES = (
    "battery_runtime.py",
    "transcript_runtime.py",
    "intake_runtime.py",
    "ledger_runtime.py",
    "core_state.py",
    "refresh_admission.py",
    "performance_metrics.py",
    "presentation_compiler.py",
    "firmware_validation.py",
    "webhook_delivery.py",
)


def _tree() -> ast.Module:
    return ast.parse(STATUS_BAR.read_text(encoding="utf-8"))


def _method(name: str) -> ast.FunctionDef:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing production status-bar method: {name}")


def _call_names(node: ast.AST) -> tuple[str, ...]:
    result = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        function = call.func
        if isinstance(function, ast.Name):
            result.append(function.id)
            continue
        if not isinstance(function, ast.Attribute):
            continue
        parts = [function.attr]
        owner = function.value
        while isinstance(owner, ast.Attribute):
            parts.append(owner.attr)
            owner = owner.value
        if isinstance(owner, ast.Name):
            parts.append(owner.id)
        result.append(".".join(reversed(parts)))
    return tuple(result)


def test_full_refresh_is_admitted_through_typed_state_deltas() -> None:
    calls = _call_names(_method("refresh_"))

    assert "self._observe_refresh_state" in calls
    assert "admit_refresh" in calls
    assert "_LegacyStatusBarController.refresh_" in calls
    assert calls.index("admit_refresh") < calls.index("_LegacyStatusBarController.refresh_")


def test_slow_refresh_producers_delegate_to_latest_wins_services() -> None:
    transcript = _call_names(_method("ingest_transcript_fallback"))
    intake = _call_names(_method("refresh_intake_report"))
    ledger = _call_names(_method("publish_local_ledger_now"))

    assert "self._transcript_service" in transcript
    assert "monitor.input_signature" not in transcript
    assert "self._intake_service" in intake
    assert "_legacy.probe_providers" not in intake
    assert "self._ledger_publisher" in ledger
    assert "_legacy.publish_local_ledger" not in ledger


def test_hook_bursts_are_coalesced_for_at_most_fifty_milliseconds() -> None:
    text = STATUS_BAR.read_text(encoding="utf-8")

    assert "EVENT_COALESCE_SECONDS = 0.05" in text
    assert '"refreshFromEvent:"' in text
    assert '"trailingRefreshFire:"' in text


def test_background_runtime_modules_cannot_import_appkit_or_objc() -> None:
    for name in BACKGROUND_MODULES:
        text = (ROOT / "src" / "sidepulse" / name).read_text(encoding="utf-8")
        assert "import AppKit" not in text
        assert "from AppKit" not in text
        assert "import Foundation" not in text
        assert "from Foundation" not in text
        assert "import objc" not in text


def test_refresh_boundary_has_no_direct_blocking_io_calls() -> None:
    forbidden = {
        "subprocess.run",
        "subprocess.Popen",
        "urllib.request.urlopen",
        "Path.read_text",
        "Path.read_bytes",
        "Path.write_text",
        "Path.write_bytes",
        "os.fsync",
    }
    calls = set(_call_names(_method("refresh_")))

    assert not (calls & forbidden)
