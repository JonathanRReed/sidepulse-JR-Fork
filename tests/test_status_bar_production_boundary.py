from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS_BAR = ROOT / "src" / "sidepulse" / "status_bar.py"
PRODUCTION_STATUS_BAR = ROOT / "src" / "sidepulse" / "_status_bar_production.py"
STATUS_BAR_FILES = (STATUS_BAR, PRODUCTION_STATUS_BAR)
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


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _method(path: Path, name: str) -> ast.FunctionDef:
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing production status-bar method: {name}")


def _effective_method(name: str) -> ast.FunctionDef:
    for path in STATUS_BAR_FILES:
        try:
            return _method(path, name)
        except AssertionError:
            continue
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


def _stored_attributes(node: ast.AST) -> frozenset[str]:
    return frozenset(
        candidate.attr
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Attribute)
        and isinstance(candidate.ctx, ast.Store)
    )


def test_full_refresh_is_admitted_through_the_single_controller_layer() -> None:
    public_source = STATUS_BAR.read_text(encoding="utf-8")
    production_calls = _call_names(_method(PRODUCTION_STATUS_BAR, "refresh_"))

    assert "def refresh_(" not in public_source
    assert "self._observe_refresh_state" in production_calls
    assert "admit_refresh" in production_calls
    assert "_LegacyStatusBarController.refresh_" in production_calls
    assert production_calls.index("admit_refresh") < production_calls.index(
        "_LegacyStatusBarController.refresh_"
    )


def test_slow_refresh_producers_delegate_to_latest_wins_services() -> None:
    transcript = _call_names(_effective_method("ingest_transcript_fallback"))
    intake = _call_names(_effective_method("refresh_intake_report"))
    ledger = _call_names(_effective_method("publish_local_ledger_now"))

    assert "self._transcript_service" in transcript
    assert "monitor.input_signature" not in transcript
    assert "self._intake_service" in intake
    assert "_legacy.probe_providers" not in intake
    assert "self._ledger_publisher" in ledger
    assert "_legacy.publish_local_ledger" not in ledger


def test_intake_refresh_does_not_renew_the_probe_timestamp() -> None:
    method = _effective_method("refresh_intake_report")

    assert "_intake_probed_at" not in _stored_attributes(method)
    assert "self._intake_service" in _call_names(method)


def test_escalation_urgency_calls_the_stage_reader() -> None:
    calls = _call_names(_effective_method("_observe_refresh_state"))

    assert "stage_reader" in calls


def test_hook_bursts_use_the_legacy_refresh_floor() -> None:
    # The dispatch site lives in the retained legacy controller; the
    # coalescing override lives in the production layer. The contract spans
    # all three files.
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            *STATUS_BAR_FILES,
            ROOT / "src" / "sidepulse" / "status_bar_legacy.py",
        )
    )

    assert (
        "EVENT_COALESCE_SECONDS = _legacy.EVENT_REFRESH_FLOOR_SECONDS"
        in text
    )
    assert '"refreshFromEvent:"' in text
    assert '"trailingRefreshFire:"' in text


def test_full_refresh_heartbeat_uses_the_normal_status_interval() -> None:
    text = PRODUCTION_STATUS_BAR.read_text(encoding="utf-8")

    assert (
        "FULL_REFRESH_HEARTBEAT_SECONDS = _legacy.STATUS_BAR_REFRESH_SECONDS"
        in text
    )


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
        "sqlite3.connect",
    }
    calls = set(_call_names(_method(PRODUCTION_STATUS_BAR, "refresh_")))

    assert not (calls & forbidden)
