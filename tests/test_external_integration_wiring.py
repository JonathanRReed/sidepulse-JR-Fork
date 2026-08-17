from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS_BAR = ROOT / "src" / "sidepulse" / "status_bar.py"
BACKGROUND_MODULES = (
    "_integration_settings_legacy.py",
    "integration_compatibility.py",
    "integration_settings.py",
    "t3_compat.py",
)


def _tree() -> ast.Module:
    return ast.parse(STATUS_BAR.read_text(encoding="utf-8"))


def test_public_status_bar_facade_defines_no_integration_worker_or_controller() -> None:
    source = STATUS_BAR.read_text(encoding="utf-8")
    classes = {
        node.name for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)
    }

    assert classes == {"_StatusBarFacade"}
    assert "T3SnapshotService" not in source
    assert "sqlite3.connect" not in source
    assert "subprocess.run" not in source
    assert "CodexBar" not in source
    assert "codexbar_compat" not in source


def test_t3_compatibility_remains_available_through_the_cli() -> None:
    source = (ROOT / "src" / "sidepulse" / "integration_cli.py").read_text(
        encoding="utf-8"
    )
    assert "read_t3_snapshot" in source
    assert 'choices=("t3code",)' in source
    assert "codexbar" not in source


def test_integration_workers_are_appkit_free() -> None:
    for name in BACKGROUND_MODULES:
        source = (ROOT / "src" / "sidepulse" / name).read_text(encoding="utf-8")
        assert "import AppKit" not in source
        assert "from AppKit" not in source
        assert "import Foundation" not in source
        assert "from Foundation" not in source
        assert "import objc" not in source
