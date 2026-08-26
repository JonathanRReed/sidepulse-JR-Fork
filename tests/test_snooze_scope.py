"""Snooze scope: lights and notifications, with the raised-hand override."""

from __future__ import annotations

from datetime import datetime, timezone

from sidepulse.capacity_types import SourceKey
from sidepulse.mailbox_preferences import LegacyMailboxPreference, MailboxPreference
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.provider_facts import WorkIdentifier, WorkKey
from sidepulse.snooze_scope import filter_snoozed_statuses, status_snoozed

NOW = 1_787_000_000.0


def _work_key(provider: str, work_id: str) -> WorkKey:
    return WorkKey(
        SourceKey(provider, "hooks", "default", "live_agent_events"),
        WorkIdentifier(work_id),
    )


def _status(
    agent_id: str,
    mode: AgentMode,
    *,
    event_name: str = "PostToolUse",
    work_key: WorkKey | None = None,
    session_id: str | None = None,
    provider: str = "codex",
) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=agent_id,
        display_name=agent_id,
        mode=mode,
        updated_at=datetime.fromtimestamp(NOW, timezone.utc),
        event_name=event_name,
        session_id=session_id,
        work_key=work_key,
    )


def _snooze(work_key: WorkKey, *, until: float = NOW + 900.0) -> MailboxPreference:
    return MailboxPreference(work_key, snoozed_at=NOW - 60.0, snoozed_until=until)


def test_snoozed_working_session_is_filtered() -> None:
    key = _work_key("codex", "main")
    working = _status("codex:session:main", AgentMode.WORKING, work_key=key, session_id="main")
    other = _status("claude:session:other", AgentMode.WORKING, provider="claude", session_id="other")

    kept = filter_snoozed_statuses((working, other), (_snooze(key),), now=NOW)
    assert kept == (other,)
    assert status_snoozed(working, (_snooze(key),), now=NOW)
    assert not status_snoozed(other, (_snooze(key),), now=NOW)


def test_live_hard_ask_breaks_through_a_snooze() -> None:
    key = _work_key("codex", "main")
    ask = _status(
        "codex:session:main",
        AgentMode.WAITING_FOR_INPUT,
        event_name="PermissionRequest",
        work_key=key,
        session_id="main",
    )
    kept = filter_snoozed_statuses((ask,), (_snooze(key),), now=NOW)
    assert kept == (ask,)
    assert not status_snoozed(ask, (_snooze(key),), now=NOW)


def test_expired_snooze_no_longer_silences() -> None:
    key = _work_key("codex", "main")
    working = _status("codex:session:main", AgentMode.WORKING, work_key=key, session_id="main")
    expired = MailboxPreference(key, snoozed_at=NOW - 7_200.0, snoozed_until=NOW - 3_600.0)
    kept = filter_snoozed_statuses((working,), (expired,), now=NOW)
    assert kept == (working,)


def test_family_snooze_covers_a_worker_via_its_session_id() -> None:
    # A snooze is stored on the FAMILY key; a worker's own work key
    # differs, but its session_id is the family's work id.
    family = _work_key("codex", "main")
    worker = _status(
        "codex:agent:w1",
        AgentMode.WORKING,
        work_key=_work_key("codex", "w1"),
        session_id="main",
    )
    kept = filter_snoozed_statuses((worker,), (_snooze(family),), now=NOW)
    assert kept == ()


def test_legacy_agent_id_preferences_still_silence() -> None:
    working = _status("codex:session:main", AgentMode.WORKING, session_id="main")
    legacy = LegacyMailboxPreference(
        "codex:session:main",
        snoozed_at=NOW - 60.0,
        snoozed_until=NOW + 600.0,
    )
    assert filter_snoozed_statuses((working,), (legacy,), now=NOW) == ()


def test_unfiltered_input_returns_the_original_tuple_object() -> None:
    statuses = (_status("codex:session:main", AgentMode.WORKING, session_id="main"),)
    assert filter_snoozed_statuses(statuses, (), now=NOW) is statuses
    other = _snooze(_work_key("claude", "elsewhere"))
    assert filter_snoozed_statuses(statuses, (other,), now=NOW) is statuses
