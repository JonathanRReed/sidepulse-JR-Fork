"""The "what did I miss" ledger, from the transition to the dropdown row.

The dropdown shows what is happening NOW. After ten minutes away the owner's
actual question is what CHANGED, and before this there was no answer at all:
a completion that fired while he was gone left no trace on any surface. The
LEDs had finished their burst, the Screen Bar announcement was gone, and the
mailbox only ever shows a session's CURRENT state.

The load-bearing claims:

  * a completion, an ask and an error that happened while the menu was shut
    are all still in the dropdown when it is next opened, named and timed;
  * "left" means the last time the dropdown was opened -- persisted, so a
    restart does not mark everything read, and monotonic, so a clock that
    steps backwards does not mark everything unread;
  * a first observation is never news: launching the app cannot manufacture
    a "while you were away" list out of state that was simply already true;
  * sub-agents never appear -- one main agent fans out to 100+ workers;
  * threshold crossings come from the AUTHORISED capacity projection, so the
    drifted-payload reading that used to render as a 5-hour ceiling cannot
    mint one either;
  * the ledger is bounded by BOTH an entry count and a byte cap, because a
    count alone cannot bound a file whose rows vary in size (audit.py learned
    that at 23 MB);
  * clicking a row reveals that session through the same action every other
    session row in this dropdown already uses.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sidepulse.activity_ledger import (
    MAX_ACTIVITY_ENTRIES,
    MAX_ACTIVITY_LEDGER_BYTES,
    ActivityEntry,
    ActivityKind,
    ActivityLedger,
    ActivityRestoreHealth,
    ActivityValidationError,
    activity_row_text,
    mark_activity_seen,
    record_activities,
    record_activity,
    relative_age_label,
    safe_activity_text,
)
from sidepulse.activity_ledger_store import (
    ACTIVITY_LEDGER_NAME,
    default_activity_ledger_path,
    load_activity_ledger,
    save_activity_ledger,
)
from sidepulse.capacity_refresh import RefreshCause
from sidepulse.capacity_types import (
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from sidepulse.completions import detect_attention_transitions
from sidepulse.models import AgentMode, AgentStatus
from tests.test_sidepulse import isolate_controller

NOW = 1_800_000_000.0
CODEX_QUOTA_SOURCE = SourceKey("codex", "quota", "local", "remote_quota_windows")


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def controller(request):
    class ControllerCase:
        def __init__(self) -> None:
            self._cleanups = []

        def addCleanup(self, callback) -> None:
            self._cleanups.append(callback)

        def skipTest(self, reason: str) -> None:
            pytest.skip(reason)

        def close(self) -> None:
            for callback in reversed(self._cleanups):
                callback()

    case = ControllerCase()
    isolate_controller(case)
    request.addfinalizer(case.close)
    return case.controller, case.status_bar, case._activity_ledger_path


def _entry(
    seconds_ago: float = 0.0,
    *,
    kind: ActivityKind = ActivityKind.COMPLETED,
    label: str = "sidepulse-manager",
    subject_id: str | None = "claude:session:one",
    detail: str | None = None,
) -> ActivityEntry:
    return ActivityEntry(
        kind,
        NOW - seconds_ago,
        label,
        "claude",
        subject_id,
        detail,
    )


def _status(
    agent_id: str,
    mode: AgentMode,
    *,
    display_name: str = "sidepulse-manager",
    session_id: str = "one",
    updated_at: datetime | None = None,
    event_name: str = "Stop",
) -> AgentStatus:
    return AgentStatus(
        provider=agent_id.split(":", 1)[0],
        agent_id=agent_id,
        display_name=display_name,
        mode=mode,
        updated_at=updated_at or datetime.now(timezone.utc),
        event_name=event_name,
        session_id=session_id,
    )


def _snapshot(statuses=(), stale=()):
    return SimpleNamespace(
        statuses=list(statuses),
        stale_statuses=list(stale),
        collected_at=datetime.now(timezone.utc),
    )


def _titles(menu):
    return [menu.itemAtIndex_(index).title() for index in range(menu.numberOfItems())]


def _activity_item(menu):
    for index in range(menu.numberOfItems()):
        item = menu.itemAtIndex_(index)
        title = item.title()
        if title.startswith("Since you left") or title == "Recent activity":
            return item
    return None


# --------------------------------------------------------------------------
# The pure model: bounds, identity, the "left" watermark.
# --------------------------------------------------------------------------


def test_the_ledger_is_bounded_by_entry_count_and_by_bytes() -> None:
    """A count cap alone cannot bound a file whose rows vary in size."""
    ledger = ActivityLedger()
    for index in range(400):
        ledger = record_activity(
            ledger,
            _entry(
                index,
                label="A" * 96,
                subject_id=f"claude:session:{'b' * 200}{index}",
            ),
        )

    assert len(ledger.entries) <= MAX_ACTIVITY_ENTRIES
    encoded = json.dumps(
        [
            {
                "kind": entry.kind.value,
                "occurred_at_epoch": entry.occurred_at_epoch,
                "label": entry.label,
                "provider": entry.provider,
                "subject_id": entry.subject_id,
                "detail": entry.detail,
            }
            for entry in ledger.entries
        ],
        separators=(",", ":"),
    )
    assert len(encoded.encode("utf-8")) <= MAX_ACTIVITY_LEDGER_BYTES
    # With rows this fat the BYTE budget binds first, so the count cap alone
    # would have let the file grow past its own limit.
    assert len(ledger.entries) < MAX_ACTIVITY_ENTRIES
    # Newest survive: the ledger answers "what did I miss", not "what is the
    # oldest thing still on file".
    assert ledger.entries[0].occurred_at_epoch == NOW


def test_a_small_ledger_is_bounded_by_the_entry_count() -> None:
    ledger = ActivityLedger()
    for index in range(400):
        ledger = record_activity(ledger, _entry(index, label="s"))

    assert len(ledger.entries) == MAX_ACTIVITY_ENTRIES
    assert ledger.entries[0].occurred_at_epoch == NOW


def test_the_same_fact_is_recorded_once() -> None:
    ledger = record_activity(ActivityLedger(), _entry())
    assert record_activity(ledger, _entry()) is ledger
    assert len(ledger.entries) == 1


def test_two_facts_at_the_same_instant_both_survive() -> None:
    """Same timestamp, different sessions -- two events, not one."""
    ledger = record_activities(
        ActivityLedger(),
        (
            _entry(subject_id="claude:session:one"),
            _entry(subject_id="codex:session:two"),
        ),
    )
    assert len(ledger.entries) == 2


def test_the_seen_watermark_only_moves_forward() -> None:
    """A clock that steps backwards must not mark read rows unread again."""
    ledger = mark_activity_seen(record_activity(ActivityLedger(), _entry()), NOW)
    assert ledger.unseen == ()

    rewound = mark_activity_seen(ledger, NOW - 10_000.0)

    assert rewound.last_seen_epoch == NOW
    assert rewound.unseen == ()


def test_unseen_is_strictly_after_the_watermark() -> None:
    ledger = record_activities(
        ActivityLedger(),
        (_entry(60.0, subject_id="a"), _entry(0.0, subject_id="b")),
    )
    marked = mark_activity_seen(ledger, NOW - 30.0)

    assert [entry.subject_id for entry in marked.unseen] == ["b"]


def test_relative_times_read_the_way_the_rest_of_the_menu_does() -> None:
    assert relative_age_label(0.0) == "just now"
    assert relative_age_label(59.0) == "just now"
    assert relative_age_label(4 * 60.0) == "4m ago"
    assert relative_age_label(2 * 3_600.0) == "2h ago"
    assert relative_age_label(3 * 24 * 3_600.0) == "3d ago"


def test_a_row_names_the_session_what_happened_and_when() -> None:
    assert (
        activity_row_text(_entry(4 * 60.0), NOW)
        == "sidepulse-manager · finished · 4m ago"
    )
    assert (
        activity_row_text(_entry(0.0, kind=ActivityKind.ASKED), NOW)
        == "sidepulse-manager · asked you · just now"
    )
    assert (
        activity_row_text(_entry(0.0, kind=ActivityKind.BLOCKED), NOW)
        == "sidepulse-manager · hit an error · just now"
    )
    assert (
        activity_row_text(
            _entry(
                0.0,
                kind=ActivityKind.THRESHOLD_CROSSED,
                label="Claude 5-hour",
                subject_id=None,
                detail="90%",
            ),
            NOW,
        )
        == "Claude 5-hour · passed 90% · just now"
    )


def test_a_display_name_with_control_characters_cannot_reach_a_row() -> None:
    """Hook-supplied names have already arrived with newlines in them."""
    cleaned = safe_activity_text("evil\nname\twith\x00junk", 96)

    assert cleaned == "evil name with junk"
    assert ActivityEntry(ActivityKind.COMPLETED, NOW, cleaned, "claude").label == cleaned


def test_an_unprintable_label_is_refused_rather_than_stored() -> None:
    with pytest.raises(ActivityValidationError):
        ActivityEntry(ActivityKind.COMPLETED, NOW, "two\nlines", "claude")


# --------------------------------------------------------------------------
# The store.
# --------------------------------------------------------------------------


def test_a_saved_ledger_restores_exactly(tmp_path: Path) -> None:
    path = tmp_path / ACTIVITY_LEDGER_NAME
    ledger = mark_activity_seen(
        record_activities(
            ActivityLedger(),
            (
                _entry(30.0, subject_id="claude:session:one"),
                _entry(
                    10.0,
                    kind=ActivityKind.THRESHOLD_CROSSED,
                    label="Codex Weekly",
                    subject_id=None,
                    detail="95%",
                ),
            ),
        ),
        NOW - 20.0,
    )

    save_activity_ledger(path, ledger)
    restored = load_activity_ledger(path)

    assert restored.health is ActivityRestoreHealth.HEALTHY
    assert restored.ledger == ledger
    assert [entry.detail for entry in restored.ledger.unseen] == ["95%"]


def test_a_corrupt_document_restores_empty_rather_than_raising(
    tmp_path: Path,
) -> None:
    path = tmp_path / ACTIVITY_LEDGER_NAME
    path.write_text('{"version":1,"entries":[],"stowaway":1}\n', encoding="utf-8")

    restored = load_activity_ledger(path)

    assert restored.health is ActivityRestoreHealth.CORRUPT
    assert restored.ledger == ActivityLedger()


def test_a_missing_document_is_named_missing_not_corrupt(tmp_path: Path) -> None:
    restored = load_activity_ledger(tmp_path / "absent.json")

    assert restored.health is ActivityRestoreHealth.MISSING


def test_an_unsupported_version_is_named_rather_than_guessed(
    tmp_path: Path,
) -> None:
    path = tmp_path / ACTIVITY_LEDGER_NAME
    path.write_text(
        '{"version":2,"entries":[],"last_seen_epoch":0.0}\n',
        encoding="utf-8",
    )

    assert load_activity_ledger(path).health is ActivityRestoreHealth.UNSUPPORTED


def test_the_store_never_writes_more_than_its_byte_cap(tmp_path: Path) -> None:
    path = tmp_path / ACTIVITY_LEDGER_NAME
    ledger = ActivityLedger()
    for index in range(400):
        ledger = record_activity(
            ledger,
            _entry(
                index,
                label="Z" * 96,
                subject_id=f"claude:session:{'y' * 200}{index}",
            ),
        )

    save_activity_ledger(path, ledger)

    assert path.stat().st_size <= MAX_ACTIVITY_LEDGER_BYTES
    assert load_activity_ledger(path).health is ActivityRestoreHealth.HEALTHY


def test_trimming_activity_can_never_evict_a_delivery_receipt(
    tmp_path: Path,
) -> None:
    """Why this was never built into the (since-deleted) `delivery_ledger`.

    That ledger was write-once dedup state: evicting an old receipt re-arms a
    notification that already fired. This one MUST evict by age. Sharing one
    document would make trimming the display feed silently re-fire delivered
    cues, so they were two files, and this pins that the activity writer
    stays out of the delivery path even now that nothing writes it.
    """
    # A stand-in document at the old delivery ledger's path. Its module and
    # store are deleted (2026-08-26; production never constructed a ledger).
    # The claim under test is unchanged: the activity writer must not touch
    # that path, whatever wrote it.
    delivery_path = tmp_path / "delivery-ledger.json"
    delivery_path.write_text('{"version": 1, "receipts": []}', encoding="utf-8")
    before = delivery_path.read_text(encoding="utf-8")

    ledger = ActivityLedger()
    for index in range(400):
        ledger = record_activity(ledger, _entry(index, label="z" * 96))
    save_activity_ledger(tmp_path / ACTIVITY_LEDGER_NAME, ledger)

    assert delivery_path.read_text(encoding="utf-8") == before
    assert default_activity_ledger_path().name == ACTIVITY_LEDGER_NAME
    assert default_activity_ledger_path().name != "delivery-ledger.json"


# --------------------------------------------------------------------------
# The transition detector.
# --------------------------------------------------------------------------


def test_a_first_observation_is_never_news() -> None:
    """Launching the app must not manufacture a while-you-were-away list."""
    waiting = _status("claude:session:one", AgentMode.WAITING_FOR_INPUT)

    assert detect_attention_transitions({}, (waiting,), datetime.now(timezone.utc)) == ()


def test_entering_an_ask_or_an_error_is_recorded_once() -> None:
    now = datetime.now(timezone.utc)
    waiting = _status("claude:session:one", AgentMode.WAITING_FOR_INPUT, updated_at=now)
    blocked = _status("codex:session:two", AgentMode.BLOCKED_ERROR, updated_at=now)
    previous = {
        "claude:session:one": AgentMode.WORKING,
        "codex:session:two": AgentMode.WORKING,
    }

    fired = detect_attention_transitions(previous, (waiting, blocked), now)
    assert [status.agent_id for status in fired] == [
        "claude:session:one",
        "codex:session:two",
    ]

    settled = {
        "claude:session:one": AgentMode.WAITING_FOR_INPUT,
        "codex:session:two": AgentMode.BLOCKED_ERROR,
    }
    assert detect_attention_transitions(settled, (waiting, blocked), now) == ()


def test_a_sub_agent_asking_is_never_a_transition() -> None:
    """Locked rule: sub-agents are never shown. 100+ per parent is normal."""
    now = datetime.now(timezone.utc)
    worker = _status(
        "claude:agent:worker-1",
        AgentMode.WAITING_FOR_INPUT,
        display_name="worker 1",
        updated_at=now,
    )

    fired = detect_attention_transitions(
        {"claude:agent:worker-1": AgentMode.WORKING},
        (worker,),
        now,
    )

    assert fired == ()


def test_an_ancient_transition_is_not_replayed_as_fresh() -> None:
    now = datetime.now(timezone.utc)
    stale = _status(
        "claude:session:one",
        AgentMode.WAITING_FOR_INPUT,
        updated_at=now - timedelta(hours=3),
    )

    assert (
        detect_attention_transitions(
            {"claude:session:one": AgentMode.WORKING},
            (stale,),
            now,
        )
        == ()
    )


# --------------------------------------------------------------------------
# The controller and the dropdown.
# --------------------------------------------------------------------------


def test_a_completion_that_fired_while_you_were_away_is_in_the_dropdown(
    controller,
) -> None:
    """The headline. Before this, that completion left no trace anywhere."""
    target, status_bar, _path = controller
    working = _status("claude:session:one", AgentMode.WORKING, event_name="PreToolUse")
    done = _status("claude:session:one", AgentMode.COMPLETED)

    target.track_completions((working,))
    target.track_completions((done,))

    menu = status_bar.build_menu(
        _snapshot(stale=(done,)),
        status_bar.STATE_IDLE,
        target,
    )
    item = _activity_item(menu)

    assert item is not None, _titles(menu)
    assert item.title() == "Since you left · 1"
    rows = [
        item.submenu().itemAtIndex_(index).title()
        for index in range(item.submenu().numberOfItems())
    ]
    assert "sidepulse-manager · finished · just now" in rows
    # The boundary is stated, not implied.
    assert rows[0] == "Menu not opened yet · showing everything kept"


def test_an_ask_and_an_error_reach_the_dropdown_too(controller) -> None:
    target, status_bar, _path = controller
    target.track_completions(
        (
            _status("claude:session:one", AgentMode.WORKING, event_name="PreToolUse"),
            _status(
                "codex:session:two",
                AgentMode.WORKING,
                display_name="codex-thing",
                session_id="two",
                event_name="PreToolUse",
            ),
        )
    )
    target.track_completions(
        (
            _status(
                "claude:session:one",
                AgentMode.WAITING_FOR_INPUT,
                event_name="Notification",
            ),
            _status(
                "codex:session:two",
                AgentMode.BLOCKED_ERROR,
                display_name="codex-thing",
                session_id="two",
                event_name="Stop",
            ),
        )
    )

    kinds = {entry.kind for entry in target.activity_ledger.entries}
    assert kinds == {ActivityKind.ASKED, ActivityKind.BLOCKED}

    menu = status_bar.build_menu(_snapshot(), status_bar.STATE_ASK, target)
    item = _activity_item(menu)
    rows = [
        item.submenu().itemAtIndex_(index).title()
        for index in range(item.submenu().numberOfItems())
    ]
    assert "sidepulse-manager · asked you · just now" in rows
    assert "codex-thing · hit an error · just now" in rows


def test_a_sub_agent_never_reaches_the_ledger(controller) -> None:
    target, _status_bar, _path = controller
    parent = _status("claude:session:one", AgentMode.WORKING, event_name="PreToolUse")
    worker_working = AgentStatus(
        provider="claude",
        agent_id="claude:agent:worker-1",
        display_name="worker 1",
        mode=AgentMode.WORKING,
        updated_at=datetime.now(timezone.utc),
        event_name="PreToolUse",
        session_id="one",
    )
    worker_done = AgentStatus(
        provider="claude",
        agent_id="claude:agent:worker-1",
        display_name="worker 1",
        mode=AgentMode.COMPLETED,
        updated_at=datetime.now(timezone.utc),
        event_name="Stop",
        session_id="one",
    )

    target.track_completions((parent, worker_working))
    target.track_completions((parent, worker_done))

    assert target.activity_ledger.entries == ()


def test_the_section_is_absent_when_nothing_has_happened(controller) -> None:
    """A permanent "Since you left · 0" is the same cry-wolf failure the
    capacity card was just taught not to commit."""
    target, status_bar, _path = controller

    menu = status_bar.build_menu(_snapshot(), status_bar.STATE_IDLE, target)

    assert _activity_item(menu) is None
    assert not any("Since you left" in title for title in _titles(menu))


def test_opening_the_menu_is_the_visit_that_clears_since_you_left(
    controller,
) -> None:
    target, status_bar, _path = controller
    working = _status("claude:session:one", AgentMode.WORKING, event_name="PreToolUse")
    done = _status("claude:session:one", AgentMode.COMPLETED)
    target.track_completions((working,))
    target.track_completions((done,))
    snapshot = _snapshot(stale=(done,))
    target.last_snapshot = snapshot

    assert _activity_item(
        status_bar.build_menu(snapshot, status_bar.STATE_IDLE, target)
    ).title() == "Since you left · 1"

    with (
        patch.object(target, "maybe_refresh_usage_summary"),
        patch.object(target, "schedule_capacity_timers"),
    ):
        target.menuWillOpen_(None)

    item = _activity_item(status_bar.build_menu(snapshot, status_bar.STATE_IDLE, target))
    # Read, not deleted: it is still the answer to "what happened today".
    assert item.title() == "Recent activity"
    rows = [
        item.submenu().itemAtIndex_(index).title()
        for index in range(item.submenu().numberOfItems())
    ]
    assert any("finished" in row for row in rows)
    assert rows[0].startswith("Menu last opened")


def test_clicking_a_row_reveals_that_session(controller) -> None:
    """The same action every other session row in this dropdown carries."""
    target, status_bar, _path = controller
    working = _status("claude:session:one", AgentMode.WORKING, event_name="PreToolUse")
    done = _status("claude:session:one", AgentMode.COMPLETED)
    target.track_completions((working,))
    target.track_completions((done,))

    menu = status_bar.build_menu(
        _snapshot(stale=(done,)),
        status_bar.STATE_IDLE,
        target,
    )
    submenu = _activity_item(menu).submenu()
    row = next(
        submenu.itemAtIndex_(index)
        for index in range(submenu.numberOfItems())
        if "finished" in submenu.itemAtIndex_(index).title()
    )

    assert row.isEnabled()
    assert row.action() == "openSessionPrimary:"
    assert row.representedObject().agent_id == "claude:session:one"

    with (
        patch.object(status_bar.StatusBarController, "open_session", autospec=True) as open_session,
        patch.object(status_bar.StatusBarController, "close_status_menu", autospec=True),
    ):
        target.openSessionPrimary_(SimpleNamespace(representedObject=row.representedObject))

    assert open_session.call_args.args[1].agent_id == "claude:session:one"


def test_a_row_whose_session_is_gone_stays_visible_and_disabled(
    controller,
) -> None:
    target, status_bar, _path = controller
    working = _status("claude:session:one", AgentMode.WORKING, event_name="PreToolUse")
    done = _status("claude:session:one", AgentMode.COMPLETED)
    target.track_completions((working,))
    target.track_completions((done,))

    # The session has aged out of the snapshot entirely.
    menu = status_bar.build_menu(_snapshot(), status_bar.STATE_IDLE, target)
    submenu = _activity_item(menu).submenu()
    row = next(
        submenu.itemAtIndex_(index)
        for index in range(submenu.numberOfItems())
        if "finished" in submenu.itemAtIndex_(index).title()
    )

    assert not row.isEnabled()
    assert row.action() is None


def test_the_ledger_survives_a_restart(controller) -> None:
    """Persistence is the whole point: "while I was gone" outlives a relaunch."""
    target, status_bar, path = controller
    working = _status("claude:session:one", AgentMode.WORKING, event_name="PreToolUse")
    done = _status("claude:session:one", AgentMode.COMPLETED)
    target.track_completions((working,))
    target.track_completions((done,))
    assert path.exists()

    # A fresh controller, wired only by its own default path.
    restarted = status_bar.StatusBarController.alloc().init()

    assert restarted.activity_ledger_path == path
    assert [entry.kind for entry in restarted.ensure_activity_ledger().entries] == [
        ActivityKind.COMPLETED
    ]
    assert restarted.activity_ledger.entries[0].label == "sidepulse-manager"


def test_the_menu_rebuilds_when_the_ledger_changes(controller) -> None:
    """Otherwise the section renders once and then freezes for 30 seconds."""
    target, status_bar, _path = controller
    target.status_bar_devices = lambda *args, **kwargs: []
    snapshot = _snapshot()

    with patch("sidepulse.status_bar.time.monotonic", return_value=100.0):
        before = status_bar.menu_content_signature(
            snapshot, status_bar.STATE_IDLE, target
        )
        target.track_completions(
            (_status("claude:session:one", AgentMode.WORKING, event_name="PreToolUse"),)
        )
        target.track_completions((_status("claude:session:one", AgentMode.COMPLETED),))
        after = status_bar.menu_content_signature(
            snapshot, status_bar.STATE_IDLE, target
        )
        target.mark_activity_seen_now(time.time())
        seen = status_bar.menu_content_signature(
            snapshot, status_bar.STATE_IDLE, target
        )

    assert before != after
    assert after != seen


def test_a_ledger_that_cannot_be_read_is_an_empty_section_not_a_crash(
    controller,
) -> None:
    target, status_bar, path = controller
    path.write_text("{not json", encoding="utf-8")

    menu = status_bar.build_menu(_snapshot(), status_bar.STATE_IDLE, target)

    assert _activity_item(menu) is None
    assert target.activity_ledger == ActivityLedger()


# --------------------------------------------------------------------------
# Threshold crossings, through the real capacity plane.
# --------------------------------------------------------------------------


def _run_codex_refresh(target, status_bar, limits):
    """One real codex capacity refresh, worker to publish (no fake lanes)."""
    target.rebuild_capacity_refresh_coordinator()
    refresh_key = target._capacity_refresh_keys_by_provider["codex"]
    monotonic_now = time.monotonic()
    decision = target._capacity_refresh_coordinator.request_refresh(
        refresh_key,
        RefreshCause.MANUAL,
        monotonic_now,
    )
    target._capacity_refresh_coordinator.register_started(
        refresh_key,
        decision.generation,
        monotonic_now + 30.0,
    )
    published = []
    with (
        patch.object(
            status_bar.usage_stats,
            "cached_codex_rate_limits",
            return_value=limits,
        ),
        patch.object(
            target,
            "performSelectorOnMainThread_withObject_waitUntilDone_",
            side_effect=lambda _sel, payload, _wait: published.append(payload),
        ),
    ):
        target._usage_refresh_source_worker(
            refresh_key.source,
            decision.generation,
            None,
            {},
            None,
        )
    assert published, "the worker published nothing"
    with patch.object(target, "schedule_capacity_timers"):
        target.applyUsageSummary_(published[0])


def _limits(primary_used: float):
    reset = time.time() + 3_600.0
    return {
        "primary": {
            "used_percent": primary_used,
            "window_minutes": 300,
            "resets_at": reset,
        },
        "secondary": {
            "used_percent": 10.0,
            "window_minutes": 7 * 24 * 60,
            "resets_at": reset + 86_400.0,
        },
    }


def test_a_ceiling_crossed_while_you_were_away_is_recorded(controller) -> None:
    target, status_bar, _path = controller

    _run_codex_refresh(target, status_bar, _limits(85.0))
    # A first reading is a repaint, never an edge.
    assert target.activity_ledger.entries == ()

    _run_codex_refresh(target, status_bar, _limits(96.0))

    rows = [
        activity_row_text(entry, time.time())
        for entry in target.activity_ledger.entries
    ]
    assert rows == [
        "Codex 5-hour · passed 90% · just now",
        "Codex 5-hour · passed 95% · just now",
    ]


def test_a_drifted_payload_cannot_mint_a_threshold_crossing(controller) -> None:
    """The 97%-used `Spark` allowance that used to render as the 5-hour
    ceiling authorises no lane, so it can cross nothing either."""
    target, status_bar, _path = controller
    reset = time.time() + 3_600.0

    _run_codex_refresh(target, status_bar, _limits(85.0))
    _run_codex_refresh(
        target,
        status_bar,
        {
            "additional_rate_limits": [
                {
                    "name": "GPT-5.3-Codex-Spark",
                    "used_percent": 97.0,
                    "window_minutes": 300,
                    "resets_at": reset,
                }
            ]
        },
    )

    assert target.activity_ledger.entries == ()


def _lane(used_percent: float) -> QuotaLaneObservation:
    return QuotaLaneObservation(
        key=QuotaLaneKey(
            CODEX_QUOTA_SOURCE,
            "all",
            "codex-chatgpt-plan",
            None,
            "five-hour",
            QuotaEffect.ALL_WORKLOADS,
        ),
        semantic_name="5-hour",
        horizon=QuotaHorizon.SHORT,
        value=CapacityValue(
            CapacityUnit.PERCENT_REMAINING,
            100.0 - used_percent,
            ObservationState.OBSERVED,
        ),
        reset=ResetFact(ResetState.FUTURE, NOW + 3_600.0, 300.0, NOW),
        observed_at=NOW,
        source_health=CapacitySourceHealth(
            CODEX_QUOTA_SOURCE,
            SourceHealthKind.HEALTHY,
            NOW,
            NOW,
            None,
            None,
            False,
        ),
        account_discriminator="codex-chatgpt",
    )


def _authorised(status_bar, used_percent: float, state: ObservationState):
    return status_bar.AuthorisedCapacity(
        lanes=(_lane(used_percent),),
        withheld=(),
        freshness=(("5-hour", state),),
    )


def test_a_forgiven_reading_never_becomes_a_crossing(controller) -> None:
    """An edge computed against a number the source can no longer stand
    behind is not an edge: the refresh succeeded, but the NUMBER is a
    memory the authority layer merely forgave."""
    target, status_bar, _path = controller
    target.record_capacity_threshold_crossings(
        _authorised(status_bar, 85.0, ObservationState.OBSERVED),
        occurred_at=NOW,
    )

    forgiven = target.record_capacity_threshold_crossings(
        _authorised(status_bar, 96.0, ObservationState.LAST_KNOWN_GOOD),
        occurred_at=NOW,
    )
    assert forgiven == ()

    # The guard is about the reading's freshness, not about the number: the
    # same percentage from a fresh reading does cross.
    fresh = target.record_capacity_threshold_crossings(
        _authorised(status_bar, 96.0, ObservationState.OBSERVED),
        occurred_at=NOW,
    )
    assert [entry.detail for entry in fresh] == ["90%", "95%"]
