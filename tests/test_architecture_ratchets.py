from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "sidepulse"

# These are the audited byte sizes at the start of the production rescue.
# They may shrink. Increasing one requires extracting behavior instead of
# extending the historical monolith.
LEGACY_SIZE_CEILINGS = {
    SRC / "status_bar_legacy.py": 782_668,
    SRC / "settings_window.py": 220_568,
    ROOT / "tests" / "test_sidepulse.py": 1_065_785,
}

# Existing non-legacy modules are allowed enough room for coherent ownership,
# but no new Python source file may become another controller-sized subsystem.
NON_LEGACY_MODULE_MAX_BYTES = 184_320
EXEMPT_SOURCE_MODULES = {
    "status_bar_legacy.py",
    "settings_window.py",
}

PURE_PRODUCTION_MODULES = {
    "battery_runtime.py",
    "core_state.py",
    "firmware_validation.py",
    "intake_runtime.py",
    "ledger_runtime.py",
    "performance_metrics.py",
    "presentation_compiler.py",
    "refresh_admission.py",
    "transcript_runtime.py",
    "webhook_delivery.py",
}


def test_legacy_monoliths_can_only_shrink() -> None:
    for path, ceiling in LEGACY_SIZE_CEILINGS.items():
        assert path.stat().st_size <= ceiling, (
            f"{path.relative_to(ROOT)} grew beyond its audited ceiling of "
            f"{ceiling:,} bytes. Extract behavior into a typed module."
        )


def test_non_legacy_source_modules_stay_below_the_monolith_threshold() -> None:
    oversized = {
        path.name: path.stat().st_size
        for path in SRC.glob("*.py")
        if path.name not in EXEMPT_SOURCE_MODULES
        and path.stat().st_size > NON_LEGACY_MODULE_MAX_BYTES
    }
    assert not oversized, f"production modules are too large: {oversized}"


def test_pure_production_modules_do_not_use_baseexception_or_dynamic_exec() -> None:
    failures = []
    for name in sorted(PURE_PRODUCTION_MODULES):
        path = SRC / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                handler = node.type
                if isinstance(handler, ast.Name) and handler.id == "BaseException":
                    failures.append(f"{name}:{node.lineno}: BaseException")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"eval", "exec", "compile"}:
                    failures.append(f"{name}:{node.lineno}: {node.func.id}")
    assert not failures, f"unsafe production constructs: {failures}"


def test_production_facade_does_not_grow_into_a_second_monolith() -> None:
    facade = SRC / "status_bar.py"
    assert facade.stat().st_size <= 80_000, (
        "status_bar.py exceeded 80 KB. Split battery, transcript, intake, "
        "ledger, refresh admission, and diagnostics ownership further."
    )
