"""Snooze semantics: hidden while resting, woken the moment it matters."""

from __future__ import annotations

from datetime import datetime, timezone

from sidepulse.models import AgentMode, AgentStatus
from sidepulse.session_snooze import (
    SNOOZE_MAX_SECONDS,
    can_snooze,
    filter_snoozed,
    snooze_holds,
)

NOW = 1_800_000_000.0


def _status(mode: AgentMode, *, updated_epoch: float, agent_id: str = "claude:session:a"):
    return AgentStatus(
        provider="claude",
        agent_id=agent_id,
        display_name="Claude a",
        mode=mode,
        updated_at=datetime.fromtimestamp(updated_epoch, tz=timezone.utc),
        event_name="PostToolUse",
    )


def test_a_pending_ask_cannot_be_snoozed() -> None:
    assert not can_snooze(_status(AgentMode.WAITING_FOR_INPUT, updated_epoch=NOW))
    assert can_snooze(_status(AgentMode.WORKING, updated_epoch=NOW))


def test_snooze_holds_while_working_and_breaks_on_raised_hand() -> None:
    snoozed_at = NOW - 600.0
    assert snooze_holds(
        _status(AgentMode.WORKING, updated_epoch=NOW), snoozed_at, NOW
    )
    assert not snooze_holds(
        _status(AgentMode.WAITING_FOR_INPUT, updated_epoch=NOW), snoozed_at, NOW
    )


def test_fresh_failure_wakes_but_a_preexisting_one_stays_snoozed() -> None:
    snoozed_at = NOW - 600.0
    # Error NEWER than the snooze: raise it.
    assert not snooze_holds(
        _status(AgentMode.BLOCKED_ERROR, updated_epoch=NOW - 60.0), snoozed_at, NOW
    )
    # Error OLDER than the snooze: "I saw it, not now."
    assert snooze_holds(
        _status(AgentMode.BLOCKED_ERROR, updated_epoch=NOW - 900.0), snoozed_at, NOW
    )


def test_completion_after_snooze_wakes() -> None:
    snoozed_at = NOW - 600.0
    assert not snooze_holds(
        _status(AgentMode.COMPLETED, updated_epoch=NOW - 10.0), snoozed_at, NOW
    )
    assert snooze_holds(
        _status(AgentMode.COMPLETED, updated_epoch=NOW - 900.0), snoozed_at, NOW
    )


def test_nothing_hides_past_the_cap() -> None:
    assert not snooze_holds(
        _status(AgentMode.WORKING, updated_epoch=NOW),
        NOW - SNOOZE_MAX_SECONDS - 1.0,
        NOW,
    )


def test_filter_hides_held_and_prunes_broken() -> None:
    working = _status(AgentMode.WORKING, updated_epoch=NOW, agent_id="c:s:work")
    asking = _status(
        AgentMode.WAITING_FOR_INPUT, updated_epoch=NOW, agent_id="c:s:ask"
    )
    untouched = _status(AgentMode.WORKING, updated_epoch=NOW, agent_id="c:s:free")
    snoozes = {"c:s:work": NOW - 60.0, "c:s:ask": NOW - 60.0, "c:s:gone": NOW - 60.0}

    visible, kept = filter_snoozed((working, asking, untouched), snoozes, NOW)

    ids = [status.agent_id for status in visible]
    assert ids == ["c:s:ask", "c:s:free"]  # working hidden; ask woken
    assert set(kept) == {"c:s:work", "c:s:gone"}  # broken snooze pruned

    # The woken ask stays visible on the next pass -- no flapping.
    visible2, kept2 = filter_snoozed((working, asking, untouched), kept, NOW + 1)
    assert [status.agent_id for status in visible2] == ["c:s:ask", "c:s:free"]
    assert set(kept2) == {"c:s:work", "c:s:gone"}
