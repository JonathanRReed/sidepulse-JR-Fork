from __future__ import annotations

import json
from pathlib import Path

import pytest

from sidepulse.settings import (
    AgentMonitorSettings,
    SettingsConcurrentWriteError,
    load_settings_document,
    save_settings,
)


def test_external_edit_after_load_is_never_silently_overwritten(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings.json"
    save_settings(AgentMonitorSettings(), target)
    loaded = load_settings_document(target)

    external = json.loads(target.read_text(encoding="utf-8"))
    external["external_owner"] = {"preserve": True}
    target.write_text(json.dumps(external), encoding="utf-8")

    with pytest.raises(SettingsConcurrentWriteError):
        save_settings(
            loaded.settings.with_tips_enabled(False),
            target,
            compatibility=loaded.compatibility,
        )

    assert json.loads(target.read_text(encoding="utf-8")) == external


def test_successful_save_refreshes_the_expected_document_digest(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings.json"
    loaded = load_settings_document(target)
    updated = loaded.settings.with_tips_enabled(False)

    save_settings(updated, target, compatibility=loaded.compatibility)
    save_settings(updated, target)

    assert json.loads(target.read_text(encoding="utf-8"))["tips_enabled"] is False
