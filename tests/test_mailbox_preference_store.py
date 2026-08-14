from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.mailbox_preference_store import (
    LegacyMailboxPreference,
    MailboxMigrationResult,
    MailboxPreferenceDocument,
    MailboxSnoozePreset,
    load_mailbox_preference_document,
    load_mailbox_preferences,
    resolve_legacy_mailbox_preferences,
    resolve_mailbox_snooze_preset,
    save_mailbox_preferences,
    save_mailbox_preferences_v2,
)
from sidepulse.mailbox_preferences import (
    MailboxPreference as CanonicalMailboxPreference,
)
from sidepulse.mailbox_preferences import (
    MailboxPreferenceMode,
)
from sidepulse.provider_facts import WorkIdentifier, WorkKey, work_key_to_payload

MailboxPreference = LegacyMailboxPreference


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _stored_document(path: Path) -> dict:
    return json.loads(path.read_text())


def _work_key(
    work_id: str = "work:01",
    *,
    source_instance: str = "local:01",
) -> WorkKey:
    source = SourceKey("codex", "hooks", source_instance, "live_agent_events")
    return WorkKey(source, WorkIdentifier(work_id))


def _swap_parent_when_opening(
    parent: Path,
    outside: Path,
    matches_leaf,
):
    held_parent = parent.with_name(f"{parent.name}-held")
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and matches_leaf(Path(path).name):
            parent.rename(held_parent)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    return held_parent, swapping_open


def test_store_round_trip_creates_private_versioned_allowlisted_json(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    preferences = (
        MailboxPreference(
            agent_id="codex:session:alpha",
            mode=MailboxPreferenceMode.PINNED,
            pin_order=7,
            snoozed_at=1_786_536_000.25,
            snoozed_until=1_786_539_600.25,
            last_visited_at=1_786_535_900.5,
        ),
        MailboxPreference(
            agent_id="claude:session:beta",
            mode=MailboxPreferenceMode.WATCHED,
        ),
    )

    save_mailbox_preferences(target, preferences)

    assert load_mailbox_preferences(target) == preferences
    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o600
    document = _stored_document(target)
    assert set(document) == {"preferences", "version"}
    assert document["version"] == 1
    assert all(
        set(entry)
        == {
            "agent_id",
            "last_visited_at",
            "mode",
            "pin_order",
            "snoozed_at",
            "snoozed_until",
        }
        for entry in document["preferences"]
    )


def test_load_tightens_broad_owned_modes_without_changing_data(tmp_path: Path) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    target.parent.mkdir(mode=0o777)
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "preferences": [
                    {
                        "agent_id": "codex:session:broad",
                        "mode": "watched",
                        "pin_order": None,
                        "snoozed_at": None,
                        "snoozed_until": None,
                        "last_visited_at": 1_786_536_000.0,
                    }
                ],
            }
        )
    )
    target.parent.chmod(0o777)
    target.chmod(0o644)

    loaded = load_mailbox_preferences(target)

    assert loaded == (
        MailboxPreference(
            agent_id="codex:session:broad",
            mode=MailboxPreferenceMode.WATCHED,
            last_visited_at=1_786_536_000.0,
        ),
    )
    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o600


@pytest.mark.parametrize("operation", ("load", "save"))
@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_store_refuses_linked_leaves(
    tmp_path: Path,
    operation: str,
    link_kind: str,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("outside stays unchanged")
    outside.chmod(0o644)
    target = tmp_path / "state" / "mailbox-preferences.json"
    target.parent.mkdir()
    if link_kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)

    if operation == "load":
        assert load_mailbox_preferences(target) == ()
    else:
        with pytest.raises(OSError):
            save_mailbox_preferences(
                target,
                (MailboxPreference("codex:session:safe", MailboxPreferenceMode.WATCHED),),
            )

    assert outside.read_text() == "outside stays unchanged"
    assert _mode(outside) == 0o644


def test_load_uses_held_parent_when_path_is_swapped(tmp_path: Path) -> None:
    parent = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = parent / "mailbox-preferences.json"
    inside = MailboxPreference("codex:session:inside", MailboxPreferenceMode.WATCHED)
    outside_preference = MailboxPreference(
        "codex:session:outside",
        MailboxPreferenceMode.PINNED,
        pin_order=0,
    )
    save_mailbox_preferences(target, (inside,))
    save_mailbox_preferences(outside / target.name, (outside_preference,))
    held_parent, swapping_open = _swap_parent_when_opening(
        parent,
        outside,
        lambda leaf: leaf == target.name,
    )

    with patch("sidepulse.private_io.os.open", side_effect=swapping_open):
        loaded = load_mailbox_preferences(target)

    assert loaded == (inside,)
    assert load_mailbox_preferences(held_parent / target.name) == (inside,)
    assert load_mailbox_preferences(outside / target.name) == (outside_preference,)


def test_save_uses_held_parent_when_path_is_swapped(tmp_path: Path) -> None:
    parent = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = parent / "mailbox-preferences.json"
    old = MailboxPreference("codex:session:old", MailboxPreferenceMode.WATCHED)
    new = MailboxPreference("codex:session:new", MailboxPreferenceMode.WATCHED)
    save_mailbox_preferences(target, (old,))
    held_parent, swapping_open = _swap_parent_when_opening(
        parent,
        outside,
        lambda leaf: leaf.startswith(f"{target.name}.") and leaf.endswith(".tmp"),
    )

    with patch("sidepulse.private_io.os.open", side_effect=swapping_open):
        save_mailbox_preferences(target, (new,))

    assert load_mailbox_preferences(held_parent / target.name) == (new,)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "payload",
    (
        "{",
        json.dumps({"version": 2, "preferences": []}),
        json.dumps({"version": True, "preferences": []}),
        json.dumps({"version": 1, "preferences": {}}),
        json.dumps({"version": 1, "preferences": [None]}),
        json.dumps(
            {
                "version": 1,
                "preferences": [
                    {
                        "agent_id": "codex:session:invalid-entry",
                        "mode": "watched",
                        "pin_order": 4,
                        "snoozed_at": None,
                        "snoozed_until": None,
                        "last_visited_at": None,
                    }
                ],
            }
        ),
        json.dumps(
            {
                "version": 1,
                "preferences": [
                    {
                        "agent_id": "codex:session:huge-epoch",
                        "mode": "watched",
                        "pin_order": None,
                        "snoozed_at": None,
                        "snoozed_until": None,
                        "last_visited_at": 10**400,
                    }
                ],
            }
        ),
        json.dumps(
            {
                "version": 1,
                "preferences": [
                    {
                        "agent_id": "codex:session:extra-field",
                        "mode": "default",
                        "pin_order": None,
                        "snoozed_at": None,
                        "snoozed_until": None,
                        "last_visited_at": None,
                        "prompt": "private prompt",
                    }
                ],
            }
        ),
    ),
)
def test_corrupt_unsupported_nonlist_and_invalid_entry_data_fail_visible(
    tmp_path: Path,
    payload: str,
) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    target.parent.mkdir()
    target.write_text(payload)

    assert load_mailbox_preferences(target) == ()


def test_oversized_valid_json_fails_visible(tmp_path: Path) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    target.parent.mkdir()
    valid = json.dumps(
        {
            "version": 1,
            "preferences": [
                {
                    "agent_id": "codex:session:oversized",
                    "mode": "watched",
                    "pin_order": None,
                    "snoozed_at": None,
                    "snoozed_until": None,
                    "last_visited_at": None,
                }
            ],
        }
    )
    target.write_text(" " * 1_048_577 + valid)

    assert load_mailbox_preferences(target) == ()


def test_oversized_store_is_refused_from_opened_size_before_payload_read(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    target.parent.mkdir()
    target.write_bytes(b" " * 1_048_577)
    real_read = os.read
    payload_bytes_read = 0

    def observing_read(descriptor: int, size: int) -> bytes:
        nonlocal payload_bytes_read
        chunk = real_read(descriptor, size)
        payload_bytes_read += len(chunk)
        return chunk

    with patch("sidepulse.private_io.os.read", side_effect=observing_read):
        assert load_mailbox_preferences(target) == ()

    assert payload_bytes_read == 0


def test_failed_atomic_replace_preserves_previous_store_and_removes_scratch(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    old = MailboxPreference("codex:session:old", MailboxPreferenceMode.WATCHED)
    new = MailboxPreference("codex:session:new", MailboxPreferenceMode.PINNED, 2)
    save_mailbox_preferences(target, (old,))
    previous_bytes = target.read_bytes()

    with (
        patch("sidepulse.private_io.os.replace", side_effect=OSError("private replace failed")),
        pytest.raises(OSError, match="private replace failed"),
    ):
        save_mailbox_preferences(target, (new,))

    assert target.read_bytes() == previous_bytes
    assert load_mailbox_preferences(target) == (old,)
    assert list(target.parent.glob(f"{target.name}.*.tmp")) == []


@dataclass(frozen=True, slots=True)
class _ExtendedMailboxPreference(MailboxPreference):
    prompt: str = ""
    command: str = ""
    raw_error: str = ""


def test_save_uses_an_explicit_allowlist_and_excludes_secret_shaped_attributes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    secret = "Bearer sk-private /Users/jonathan/Secret command"
    preference = _ExtendedMailboxPreference(
        agent_id="codex:session:allowlisted",
        mode=MailboxPreferenceMode.WATCHED,
        prompt=secret,
        command=secret,
        raw_error=secret,
    )

    save_mailbox_preferences(target, (preference,))

    raw = target.read_text()
    assert secret not in raw
    assert "prompt" not in raw
    assert "command" not in raw
    assert "raw_error" not in raw
    assert load_mailbox_preferences(target) == (
        MailboxPreference(
            agent_id="codex:session:allowlisted",
            mode=MailboxPreferenceMode.WATCHED,
        ),
    )


def test_invalid_secret_shaped_identity_is_not_written(tmp_path: Path) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    secret = "Bearer-sk-private-/Users/jonathan/Secret"

    with pytest.raises(ValueError, match="invalid mailbox preference"):
        save_mailbox_preferences(target, (MailboxPreference(secret),))

    assert not target.exists()


def test_save_caps_at_first_one_hundred_canonical_preferences_deterministically(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    preferences = tuple(
        MailboxPreference(
            agent_id=f"codex:session:{index:03d}",
            mode=MailboxPreferenceMode.WATCHED,
            last_visited_at=float(index),
        )
        for index in range(105)
    )

    save_mailbox_preferences(target, preferences)
    first_bytes = target.read_bytes()
    first_document = _stored_document(target)
    save_mailbox_preferences(target, preferences)

    assert target.read_bytes() == first_bytes
    assert len(first_document["preferences"]) == 100
    assert [entry["agent_id"] for entry in first_document["preferences"]] == [
        f"codex:session:{index:03d}" for index in range(100)
    ]
    assert tuple(item.agent_id for item in load_mailbox_preferences(target)) == tuple(
        f"codex:session:{index:03d}" for index in range(100)
    )


@pytest.mark.parametrize(
    ("preset", "seconds"),
    (
        (MailboxSnoozePreset.ONE_HOUR, 3_600.0),
        (MailboxSnoozePreset.THREE_HOURS, 10_800.0),
    ),
)
def test_duration_presets_add_exact_elapsed_seconds_across_dst_gap(
    preset: MailboxSnoozePreset,
    seconds: float,
) -> None:
    local_timezone = ZoneInfo("America/New_York")
    now = datetime(2026, 3, 8, 1, 30, tzinfo=local_timezone).timestamp()

    result = resolve_mailbox_snooze_preset(
        preset,
        now=now,
        local_timezone=local_timezone,
    )

    assert result == now + seconds


@pytest.mark.parametrize(
    ("preset", "now"),
    (
        (MailboxSnoozePreset.ONE_HOUR, 1e20),
        (MailboxSnoozePreset.THREE_HOURS, 1e21),
    ),
)
def test_duration_presets_refuse_nonadvancing_float_deadlines(
    preset: MailboxSnoozePreset,
    now: float,
) -> None:
    assert (
        resolve_mailbox_snooze_preset(
            preset,
            now=now,
            local_timezone=ZoneInfo("UTC"),
        )
        is None
    )


def test_evening_preset_uses_future_local_six_pm_or_is_omitted() -> None:
    local_timezone = ZoneInfo("America/Chicago")
    morning = datetime(2026, 8, 12, 10, 15, tzinfo=local_timezone).timestamp()
    at_evening = datetime(2026, 8, 12, 18, 0, tzinfo=local_timezone).timestamp()
    expected = datetime(2026, 8, 12, 18, 0, tzinfo=local_timezone).timestamp()

    assert (
        resolve_mailbox_snooze_preset(
            MailboxSnoozePreset.THIS_EVENING,
            now=morning,
            local_timezone=local_timezone,
        )
        == expected
    )
    assert (
        resolve_mailbox_snooze_preset(
            MailboxSnoozePreset.THIS_EVENING,
            now=at_evening,
            local_timezone=local_timezone,
        )
        is None
    )


def test_tomorrow_preset_uses_next_local_date_at_nine() -> None:
    local_timezone = ZoneInfo("America/New_York")
    now = datetime(2026, 3, 7, 23, 30, tzinfo=local_timezone).timestamp()
    expected = datetime(2026, 3, 8, 9, 0, tzinfo=local_timezone).timestamp()

    assert (
        resolve_mailbox_snooze_preset(
            MailboxSnoozePreset.TOMORROW_MORNING,
            now=now,
            local_timezone=local_timezone,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("now_local", "expected_local"),
    (
        ((2026, 8, 16, 12, 0), (2026, 8, 17, 9, 0)),
        ((2026, 8, 17, 8, 0), (2026, 8, 24, 9, 0)),
    ),
)
def test_next_monday_preset_uses_following_monday_strictly_in_future(
    now_local: tuple[int, int, int, int, int],
    expected_local: tuple[int, int, int, int, int],
) -> None:
    local_timezone = ZoneInfo("America/Chicago")
    now = datetime(*now_local, tzinfo=local_timezone).timestamp()
    expected = datetime(*expected_local, tzinfo=local_timezone).timestamp()

    assert (
        resolve_mailbox_snooze_preset(
            MailboxSnoozePreset.NEXT_MONDAY_MORNING,
            now=now,
            local_timezone=local_timezone,
        )
        == expected
    )
    assert expected > now


def test_nonexistent_calendar_target_advances_to_next_valid_local_instant() -> None:
    local_timezone = ZoneInfo("Pacific/Apia")
    now = datetime(2011, 12, 29, 12, 0, tzinfo=local_timezone).timestamp()
    expected = datetime(2011, 12, 31, 0, 0, tzinfo=local_timezone).timestamp()

    assert (
        resolve_mailbox_snooze_preset(
            MailboxSnoozePreset.TOMORROW_MORNING,
            now=now,
            local_timezone=local_timezone,
        )
        == expected
    )


def test_ambiguous_calendar_target_chooses_earlier_occurrence() -> None:
    local_timezone = ZoneInfo("Pacific/Kwajalein")
    now = datetime(1969, 9, 29, 12, 0, tzinfo=local_timezone).timestamp()
    expected = datetime(
        1969,
        9,
        30,
        9,
        0,
        tzinfo=local_timezone,
        fold=0,
    ).timestamp()
    later = datetime(
        1969,
        9,
        30,
        9,
        0,
        tzinfo=local_timezone,
        fold=1,
    ).timestamp()

    result = resolve_mailbox_snooze_preset(
        MailboxSnoozePreset.TOMORROW_MORNING,
        now=now,
        local_timezone=local_timezone,
    )

    assert result == expected
    assert result < later


def test_timezone_change_recomputes_calendar_preset_without_changing_now() -> None:
    now = datetime(2026, 8, 12, 15, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    new_york_expected = datetime(
        2026,
        8,
        12,
        18,
        0,
        tzinfo=ZoneInfo("America/New_York"),
    ).timestamp()
    los_angeles_expected = datetime(
        2026,
        8,
        12,
        18,
        0,
        tzinfo=ZoneInfo("America/Los_Angeles"),
    ).timestamp()

    new_york = resolve_mailbox_snooze_preset(
        MailboxSnoozePreset.THIS_EVENING,
        now=now,
        local_timezone=ZoneInfo("America/New_York"),
    )
    los_angeles = resolve_mailbox_snooze_preset(
        MailboxSnoozePreset.THIS_EVENING,
        now=now,
        local_timezone=ZoneInfo("America/Los_Angeles"),
    )

    assert new_york == new_york_expected
    assert los_angeles == los_angeles_expected
    assert new_york != los_angeles


def test_default_timezone_is_resolved_fresh_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "America/Chicago")
    now = datetime(2026, 8, 12, 15, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    expected = datetime(
        2026,
        8,
        12,
        18,
        0,
        tzinfo=ZoneInfo("America/Chicago"),
    ).timestamp()

    assert (
        resolve_mailbox_snooze_preset(
            MailboxSnoozePreset.THIS_EVENING,
            now=now,
        )
        == expected
    )


@pytest.mark.parametrize(
    "now",
    (float("nan"), float("inf"), -float("inf"), 10**400),
)
def test_nonfinite_or_unrepresentable_clock_is_refused(now: float | int) -> None:
    assert (
        resolve_mailbox_snooze_preset(
            MailboxSnoozePreset.ONE_HOUR,
            now=now,
            local_timezone=ZoneInfo("UTC"),
        )
        is None
    )


def test_unknown_preset_is_refused() -> None:
    assert (
        resolve_mailbox_snooze_preset(
            "private forged preset",
            now=1_786_536_000.0,
            local_timezone=ZoneInfo("UTC"),
        )
        is None
    )


def test_v2_round_trip_uses_only_exact_source_scoped_work_keys(tmp_path: Path) -> None:
    target = tmp_path / "state" / "mailbox-preferences-v2.json"
    first_key = _work_key("same", source_instance="local:01")
    second_key = _work_key("same", source_instance="local:02")
    preferences = (
        CanonicalMailboxPreference(first_key, MailboxPreferenceMode.PINNED, pin_order=3),
        CanonicalMailboxPreference(
            second_key,
            MailboxPreferenceMode.WATCHED,
            last_visited_at=1_786_536_000.0,
        ),
    )

    save_mailbox_preferences_v2(target, preferences)
    document = load_mailbox_preference_document(target)

    assert document == MailboxPreferenceDocument(2, preferences, (), False)
    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o600
    raw = _stored_document(target)
    assert raw["version"] == 2
    assert [entry["work_key"] for entry in raw["preferences"]] == [
        work_key_to_payload(first_key),
        work_key_to_payload(second_key),
    ]
    assert all("agent_id" not in entry for entry in raw["preferences"])


def test_strict_v1_document_decodes_only_legacy_fields(tmp_path: Path) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    legacy = LegacyMailboxPreference(
        "codex:session:legacy",
        MailboxPreferenceMode.WATCHED,
        None,
        None,
        None,
        1_786_536_000.0,
    )
    target.parent.mkdir()
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "preferences": [
                    {
                        "agent_id": legacy.agent_id,
                        "mode": legacy.mode.value,
                        "pin_order": legacy.pin_order,
                        "snoozed_at": legacy.snoozed_at,
                        "snoozed_until": legacy.snoozed_until,
                        "last_visited_at": legacy.last_visited_at,
                    }
                ],
            }
        )
    )

    assert load_mailbox_preference_document(target) == MailboxPreferenceDocument(
        1,
        (),
        (legacy,),
        False,
    )


@pytest.mark.parametrize(
    ("matches", "unresolved_count"),
    (
        ({}, 1),
        ({"codex:session:legacy": ()}, 1),
        (
            {
                "codex:session:legacy": (
                    _work_key("first"),
                    _work_key("second"),
                )
            },
            1,
        ),
    ),
)
def test_legacy_zero_or_ambiguous_matches_never_authorize_v2_write(
    matches: dict[str, tuple[WorkKey, ...]],
    unresolved_count: int,
) -> None:
    document = MailboxPreferenceDocument(
        1,
        (),
        (LegacyMailboxPreference("codex:session:legacy", MailboxPreferenceMode.WATCHED),),
        False,
    )

    result = resolve_legacy_mailbox_preferences(document, matches)

    assert result == MailboxMigrationResult((), unresolved_count, False)


def test_unique_legacy_matches_migrate_in_memory_without_legacy_id() -> None:
    first_key = _work_key("first")
    second_key = _work_key("second")
    document = MailboxPreferenceDocument(
        1,
        (),
        (
            LegacyMailboxPreference(
                "codex:session:first",
                MailboxPreferenceMode.PINNED,
                7,
                None,
                None,
                1_786_536_000.0,
            ),
            LegacyMailboxPreference(
                "codex:session:second",
                MailboxPreferenceMode.WATCHED,
            ),
        ),
        False,
    )

    result = resolve_legacy_mailbox_preferences(
        document,
        {
            "codex:session:first": (first_key,),
            "codex:session:second": (second_key,),
        },
    )

    assert result == MailboxMigrationResult(
        (
            CanonicalMailboxPreference(
                first_key,
                MailboxPreferenceMode.PINNED,
                7,
                last_visited_at=1_786_536_000.0,
            ),
            CanonicalMailboxPreference(second_key, MailboxPreferenceMode.WATCHED),
        ),
        0,
        True,
    )


def test_resolved_work_key_collision_blocks_write_and_marks_record_unresolved() -> None:
    shared_key = _work_key("shared")
    document = MailboxPreferenceDocument(
        1,
        (),
        (
            LegacyMailboxPreference("codex:session:first", MailboxPreferenceMode.WATCHED),
            LegacyMailboxPreference("codex:session:second", MailboxPreferenceMode.PINNED, 1),
        ),
        False,
    )

    result = resolve_legacy_mailbox_preferences(
        document,
        {
            "codex:session:first": (shared_key,),
            "codex:session:second": (shared_key,),
        },
    )

    assert result.preferences == (
        CanonicalMailboxPreference(shared_key, MailboxPreferenceMode.WATCHED),
    )
    assert result.unresolved_count == 1
    assert result.may_write_v2 is False


def test_unresolved_migration_leaves_v1_bytes_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    save_mailbox_preferences(
        target,
        (LegacyMailboxPreference("codex:session:legacy", MailboxPreferenceMode.WATCHED),),
    )
    before = target.read_bytes()
    document = load_mailbox_preference_document(target)
    result = resolve_legacy_mailbox_preferences(document, {})

    assert result.may_write_v2 is False
    assert target.read_bytes() == before


def test_failed_v2_replace_preserves_exact_v1_bytes(tmp_path: Path) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    save_mailbox_preferences(
        target,
        (LegacyMailboxPreference("codex:session:legacy", MailboxPreferenceMode.WATCHED),),
    )
    before = target.read_bytes()

    with (
        patch("sidepulse.private_io.os.replace", side_effect=OSError("replace failed")),
        pytest.raises(OSError, match="replace failed"),
    ):
        save_mailbox_preferences_v2(
            target,
            (CanonicalMailboxPreference(_work_key(), MailboxPreferenceMode.WATCHED),),
        )

    assert target.read_bytes() == before


def test_v2_save_rereads_and_restores_previous_bytes_on_verification_failure(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    save_mailbox_preferences(
        target,
        (LegacyMailboxPreference("codex:session:legacy", MailboxPreferenceMode.WATCHED),),
    )
    before = target.read_bytes()
    wrong = MailboxPreferenceDocument(2, (), (), False)

    with (
        patch(
            "sidepulse.mailbox_preference_store.load_mailbox_preference_document",
            return_value=wrong,
        ),
        pytest.raises(OSError, match="verification"),
    ):
        save_mailbox_preferences_v2(
            target,
            (CanonicalMailboxPreference(_work_key(), MailboxPreferenceMode.WATCHED),),
        )

    assert target.read_bytes() == before


def test_v2_store_excludes_secret_shaped_extended_attributes(tmp_path: Path) -> None:
    target = tmp_path / "state" / "mailbox-preferences-v2.json"
    sentinel = "Bearer sk-private /Users/jonathan/Secret command"

    @dataclass(frozen=True, slots=True)
    class ExtendedPreference(CanonicalMailboxPreference):
        prompt: str = sentinel
        command: str = sentinel
        raw_error: str = sentinel

    save_mailbox_preferences_v2(
        target,
        (ExtendedPreference(_work_key(), MailboxPreferenceMode.WATCHED),),
    )

    raw = target.read_text()
    assert sentinel not in raw
    assert "prompt" not in raw
    assert "command" not in raw
    assert "raw_error" not in raw
