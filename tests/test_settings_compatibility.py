import json
from dataclasses import replace
from pathlib import Path

import pytest

from sidepulse.settings import (
    CURRENT_SETTINGS_SCHEMA_VERSION,
    SettingsWriteRefusedError,
    load_settings,
    load_settings_document,
    save_settings,
)

_DND_FIELDS = {
    "dnd_schedule_enabled": True,
    "dnd_schedule_start_minutes": 21 * 60 + 30,
    "dnd_schedule_end_minutes": 6 * 60 + 15,
    "dnd_schedule_mode": "dim",
    "dnd_dim_fraction": 0.25,
    "dnd_override_mode": "asks_only",
    "dnd_override_created_epoch": 1_800_000_000.0,
    "dnd_override_until_epoch": 1_800_003_600.0,
    "dnd_focus_mode": "pause",
}

_GLOBAL_ACTION_SHORTCUT = {
    "reveal_current_ask": {
        "key_code": 40,
        "key_label": "K",
        "modifiers": ["control", "shift"],
    }
}


def test_new_settings_leave_global_actions_unassigned(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "missing-settings.json")

    assert settings.global_action_shortcuts == {}


def test_new_settings_leave_dnd_inactive_with_exact_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "missing-settings.json")

    assert settings.dnd_schedule_enabled is False
    assert settings.dnd_schedule_start_minutes == 1320
    assert settings.dnd_schedule_end_minutes == 420
    assert settings.dnd_schedule_mode == "dark"
    assert settings.dnd_dim_fraction == 0.15
    assert settings.dnd_override_mode is None
    assert settings.dnd_override_created_epoch is None
    assert settings.dnd_override_until_epoch is None
    assert settings.dnd_focus_mode == "pause"
    assert settings.dnd_persisted_refusals == ()
    assert settings.focus_sync_enabled is False


def test_dnd_scalars_round_trip_losslessly_and_preserve_unknown_fields(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps(
            {
                "settings_schema_version": CURRENT_SETTINGS_SCHEMA_VERSION,
                **_DND_FIELDS,
                "future_top_level": {"preserve": True},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_settings_document(target)

    assert {
        key: getattr(loaded.settings, key)
        for key in _DND_FIELDS
    } == _DND_FIELDS
    assert loaded.settings.dnd_persisted_refusals == ()
    save_settings(loaded.settings, target, compatibility=loaded.compatibility)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert {key: document[key] for key in _DND_FIELDS} == _DND_FIELDS
    assert document["future_top_level"] == {"preserve": True}


@pytest.mark.parametrize(
    ("field", "bad_value", "expected"),
    (
        ("dnd_schedule_enabled", 1, False),
        ("dnd_schedule_start_minutes", True, 1320),
        ("dnd_schedule_end_minutes", 1440, 420),
        ("dnd_schedule_mode", "future_mode", "dark"),
        ("dnd_dim_fraction", 0.0, 0.15),
        ("dnd_focus_mode", "resume", "pause"),
    ),
)
def test_malformed_dnd_scalar_is_individually_defaulted_and_reported(
    tmp_path: Path,
    field: str,
    bad_value: object,
    expected: object,
) -> None:
    target = tmp_path / "settings.json"
    document = {
        "settings_schema_version": CURRENT_SETTINGS_SCHEMA_VERSION,
        **_DND_FIELDS,
        "dnd_override_mode": None,
        "dnd_override_created_epoch": None,
        "dnd_override_until_epoch": None,
    }
    document[field] = bad_value
    target.write_text(json.dumps(document), encoding="utf-8")

    loaded = load_settings_document(target)

    assert getattr(loaded.settings, field) == expected
    assert tuple(item.field for item in loaded.settings.dnd_persisted_refusals) == (
        field,
    )
    for valid_field, valid_value in _DND_FIELDS.items():
        if valid_field not in {field, "dnd_override_mode", "dnd_override_created_epoch", "dnd_override_until_epoch"}:
            assert getattr(loaded.settings, valid_field) == valid_value


def test_malformed_dnd_override_is_ignored_as_one_typed_refusal(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps(
            {
                "settings_schema_version": CURRENT_SETTINGS_SCHEMA_VERSION,
                **_DND_FIELDS,
                "dnd_override_until_epoch": None,
                "future_top_level": "keep",
            }
        ),
        encoding="utf-8",
    )

    loaded = load_settings_document(target)

    assert loaded.settings.dnd_override_mode is None
    assert loaded.settings.dnd_override_created_epoch is None
    assert loaded.settings.dnd_override_until_epoch is None
    assert tuple(item.field for item in loaded.settings.dnd_persisted_refusals) == (
        "dnd_override",
    )
    assert loaded.settings.dnd_schedule_enabled is True
    assert loaded.settings.dnd_schedule_mode == "dim"


def test_dnd_save_rejects_invalid_programmatic_values(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    loaded = load_settings_document(target)
    invalid = replace(loaded.settings, dnd_dim_fraction=float("nan"))

    with pytest.raises(ValueError, match="invalid DND settings"):
        save_settings(invalid, target, compatibility=loaded.compatibility)

    assert not target.exists()


def test_global_action_shortcuts_round_trip_without_losing_unknown_top_level_fields(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps(
            {
                "settings_schema_version": CURRENT_SETTINGS_SCHEMA_VERSION,
                "global_action_shortcuts": _GLOBAL_ACTION_SHORTCUT,
                "future_top_level": {"preserve": True},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_settings_document(target)

    assert loaded.settings.global_action_shortcuts == _GLOBAL_ACTION_SHORTCUT
    save_settings(loaded.settings, target, compatibility=loaded.compatibility)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["global_action_shortcuts"] == _GLOBAL_ACTION_SHORTCUT
    assert document["future_top_level"] == {"preserve": True}


def test_global_action_shortcuts_are_owned_so_clear_does_not_resurrect_entries(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps(
            {
                "settings_schema_version": CURRENT_SETTINGS_SCHEMA_VERSION,
                "global_action_shortcuts": _GLOBAL_ACTION_SHORTCUT,
                "future_top_level": "keep",
            }
        ),
        encoding="utf-8",
    )
    loaded = load_settings_document(target)

    cleared = replace(loaded.settings, global_action_shortcuts={})
    save_settings(cleared, target, compatibility=loaded.compatibility)

    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["global_action_shortcuts"] == {}
    assert document["future_top_level"] == "keep"


def test_newer_settings_schema_is_read_only_and_never_overwritten(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings.json"
    original = {
        "settings_schema_version": 999,
        "future_feature": {"preserve": True},
        "tips_enabled": False,
        **_DND_FIELDS,
    }
    target.write_text(json.dumps(original), encoding="utf-8")

    loaded = load_settings_document(target)

    assert loaded.compatibility.read_only is True
    assert loaded.settings.tips_enabled is False
    assert loaded.settings.dnd_schedule_enabled is True
    assert loaded.settings.dnd_schedule_mode == "dim"
    assert loaded.settings.dnd_override_mode == "asks_only"
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
