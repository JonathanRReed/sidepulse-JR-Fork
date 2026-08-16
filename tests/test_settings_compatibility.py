import json
from pathlib import Path

import pytest

from sidepulse.settings import (
    CURRENT_SETTINGS_SCHEMA_VERSION,
    SettingsWriteRefusedError,
    load_settings,
    load_settings_document,
    save_settings,
)


def test_newer_settings_schema_is_read_only_and_never_overwritten(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings.json"
    original = {
        "settings_schema_version": 999,
        "future_feature": {"preserve": True},
        "tips_enabled": False,
    }
    target.write_text(json.dumps(original), encoding="utf-8")

    loaded = load_settings_document(target)

    assert loaded.compatibility.read_only is True
    assert loaded.settings.tips_enabled is False
    with pytest.raises(SettingsWriteRefusedError):
        save_settings(loaded.settings, target, compatibility=loaded.compatibility)
    assert json.loads(target.read_text(encoding="utf-8")) == original


def test_legacy_convenience_loader_also_blocks_future_schema_writes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings.json"
    original = {"settings_schema_version": 99, "future": "keep"}
    target.write_text(json.dumps(original), encoding="utf-8")

    settings = load_settings(target)

    with pytest.raises(SettingsWriteRefusedError):
        save_settings(settings.with_tips_enabled(False), target)
    assert json.loads(target.read_text(encoding="utf-8")) == original


def test_schema_one_migrates_to_two_without_changing_user_values(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps(
            {
                "settings_schema_version": 1,
                "tips_enabled": False,
                "future_but_readable_extension": {"keep": True},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_settings_document(target)
    assert loaded.compatibility.migrated is True
    assert loaded.settings.tips_enabled is False

    save_settings(loaded.settings, target, compatibility=loaded.compatibility)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["settings_schema_version"] == CURRENT_SETTINGS_SCHEMA_VERSION
    assert document["tips_enabled"] is False
    assert document["future_but_readable_extension"] == {"keep": True}


def test_current_schema_round_trip_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    first = load_settings(target)
    save_settings(first, target)
    before = target.read_text(encoding="utf-8")

    second = load_settings(target)
    save_settings(second, target)

    assert target.read_text(encoding="utf-8") == before


def test_invalid_schema_version_preserves_corrupt_source(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps({"settings_schema_version": True, "sentinel": "keep"}),
        encoding="utf-8",
    )

    loaded = load_settings_document(target)

    assert loaded.settings.tips_enabled is True
    assert not target.exists()
    corrupt = target.with_name("settings.json.corrupt")
    assert json.loads(corrupt.read_text(encoding="utf-8"))["sentinel"] == "keep"
