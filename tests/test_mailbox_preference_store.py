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
    MailboxPreferenceDocument,
    MailboxSnoozePreset,
    load_mailbox_preference_document,
    resolve_mailbox_snooze_preset,
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


def _v2_preference(work_id: str, **changes) -> CanonicalMailboxPreference:
    from dataclasses import replace as dataclass_replace

    preference = CanonicalMailboxPreference(_work_key(work_id))
    return dataclass_replace(preference, **changes) if changes else preference


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

    document = load_mailbox_preference_document(target)

    assert document.legacy_preferences == (
        MailboxPreference(
            agent_id="codex:session:broad",
            mode=MailboxPreferenceMode.WATCHED,
            last_visited_at=1_786_536_000.0,
        ),
    )
    assert not document.degraded
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
        assert load_mailbox_preference_document(target).degraded
    else:
        with pytest.raises(OSError):
            save_mailbox_preferences_v2(target, (_v2_preference("work:safe"),))

    assert outside.read_text() == "outside stays unchanged"
    assert _mode(outside) == 0o644


def test_load_uses_held_parent_when_path_is_swapped(tmp_path: Path) -> None:
    parent = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = parent / "mailbox-preferences.json"
    inside = _v2_preference("work:inside")
    outside_preference = _v2_preference(
        "work:outside", mode=MailboxPreferenceMode.PINNED, pin_order=0
    )
    save_mailbox_preferences_v2(target, (inside,))
    save_mailbox_preferences_v2(outside / target.name, (outside_preference,))
    held_parent, swapping_open = _swap_parent_when_opening(
        parent,
        outside,
        lambda leaf: leaf == target.name,
    )

    with patch("sidepulse.private_io.os.open", side_effect=swapping_open):
        loaded = load_mailbox_preference_document(target)

    assert loaded.preferences == (inside,)
    assert load_mailbox_preference_document(held_parent / target.name).preferences == (inside,)
    assert load_mailbox_preference_document(outside / target.name).preferences == (
        outside_preference,
    )


def test_save_uses_held_parent_when_path_is_swapped(tmp_path: Path) -> None:
    parent = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = parent / "mailbox-preferences.json"
    old = _v2_preference("work:old")
    new = _v2_preference("work:new")
    save_mailbox_preferences_v2(target, (old,))
    held_parent, swapping_open = _swap_parent_when_opening(
        parent,
        outside,
        lambda leaf: leaf.startswith(f"{target.name}.") and leaf.endswith(".tmp"),
    )

    # The write itself goes through the HELD parent descriptor (the swap
    # gains the attacker nothing), and v2 is stricter than the deleted v1
    # writer after that: its post-write verification re-resolves the path,
    # sees the swapped (symlinked) parent, and reports failure instead of
    # trusting a read it can no longer attribute.
    with (
        patch("sidepulse.private_io.os.open", side_effect=swapping_open),
        pytest.raises(OSError),
    ):
        save_mailbox_preferences_v2(target, (new,))

    assert load_mailbox_preference_document(held_parent / target.name).preferences == (new,)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "payload",
    (
        "{",
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

    assert load_mailbox_preference_document(target).degraded


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

    assert load_mailbox_preference_document(target).degraded


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
        assert load_mailbox_preference_document(target).degraded

    assert payload_bytes_read == 0


def test_save_caps_at_first_one_hundred_canonical_preferences_deterministically(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    preferences = tuple(
        _v2_preference(f"work:{index:03d}", last_visited_at=float(index))
        for index in range(105)
    )

    save_mailbox_preferences_v2(target, preferences)
    first_bytes = target.read_bytes()
    first_document = _stored_document(target)
    save_mailbox_preferences_v2(target, preferences)

    assert target.read_bytes() == first_bytes
    assert len(first_document["preferences"]) == 100
    assert tuple(
        item.work_key.work_id.value
        for item in load_mailbox_preference_document(target).preferences
    ) == tuple(f"work:{index:03d}" for index in range(100))


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


def _seed_v1_store(target: Path) -> bytes:
    """Write a strict v1 document the way the deleted v1 saver did."""
    from sidepulse.private_io import atomic_private_write

    atomic_private_write(
        target,
        json.dumps(
            {
                "version": 1,
                "preferences": [
                    {
                        "agent_id": "codex:session:legacy",
                        "mode": "watched",
                        "pin_order": None,
                        "snoozed_at": None,
                        "snoozed_until": None,
                        "last_visited_at": None,
                    }
                ],
            }
        )
        + "\n",
    )
    return target.read_bytes()


def test_failed_v2_replace_preserves_exact_v1_bytes(tmp_path: Path) -> None:
    target = tmp_path / "state" / "mailbox-preferences.json"
    before = _seed_v1_store(target)

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
    before = _seed_v1_store(target)
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
