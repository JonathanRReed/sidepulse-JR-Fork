from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "sidepulse"


def test_status_bar_has_no_codexbar_runtime_dependency() -> None:
    source = (SRC / "status_bar.py").read_text(encoding="utf-8")
    assert "codexbar_compat" not in source
    assert "CodexBar" not in source
    assert "_sidepulse_codexbar" not in source


def test_only_t3_is_an_optional_external_agent_integration() -> None:
    cli = (SRC / "integration_cli.py").read_text(encoding="utf-8")
    assert '"t3code"' in cli
    assert '"codexbar"' not in cli

    manifest = json.loads(
        (SRC / "resources" / "integration_compatibility.json").read_text(
            encoding="utf-8"
        )
    )
    entries = manifest.get("integrations", manifest)
    if isinstance(entries, list):
        names = {entry.get("integration") or entry.get("id") for entry in entries}
    else:
        names = set(entries)
    assert "t3code" in names
    assert "codexbar" not in names


def test_codexbar_modules_are_absent() -> None:
    assert not (SRC / "codexbar_compat.py").exists()
    assert not (SRC / "_codexbar_compat_legacy.py").exists()


def test_clean_install_does_not_import_codexbar() -> None:
    source = (ROOT / "scripts" / "verify_clean_install.py").read_text(
        encoding="utf-8"
    )
    assert "sidepulse.codexbar_compat" not in source
