from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sidepulse.capacity_types import SourceKey
from sidepulse.clear_agents import CompletionPresentationKey
from sidepulse.completion_visibility import (
    plan_seen_completion_ids,
    select_clearable_completions,
    select_unseen_completions,
)
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.provider_facts import WorkIdentifier, WorkKey


def _status(
    agent_id: str,
    *,
    mode: AgentMode = AgentMode.COMPLETED,
    updated_at: datetime,
    event_name: str = "Stop",
    source_instance: str = "local.test",
    keyed: bool = True,
) -> AgentStatus:
    work_key = (
        WorkKey(
            SourceKey(
                "claude",
                "hooks",
                source_instance,
                "live_agent_events",
            ),
            WorkIdentifier(agent_id),
        )
        if keyed
        else None
    )
    return AgentStatus(
        provider="claude",
        agent_id=agent_id,
        display_name=agent_id,
        mode=mode,
        updated_at=updated_at,
        event_name=event_name,
        work_key=work_key,
    )


def test_clearable_completions_current_rows_shadow_stale_duplicates() -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    current_completion = _status(
        "claude:session:current",
        updated_at=now - timedelta(minutes=2),
    )
    current_active = _status(
        "claude:session:active",
        mode=AgentMode.WORKING,
        updated_at=now,
    )
    stale_newer_duplicate = _status(
        current_completion.agent_id,
        updated_at=now - timedelta(minutes=1),
    )
    stale_completion_blocked_by_active = _status(
        current_active.agent_id,
        updated_at=now - timedelta(seconds=1),
    )

    selected = select_clearable_completions(
        (current_completion, current_active),
        (stale_newer_duplicate, stale_completion_blocked_by_active),
        collected_at=now,
        within_seconds=20 * 60,
    )

    assert selected == (current_completion,)


def test_clearable_completions_preserve_exclusions_and_newest_first_order() -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    latest_b = _status("claude:session:b", updated_at=now - timedelta(seconds=5))
    latest_a = _status("claude:session:a", updated_at=now - timedelta(seconds=5))
    older = _status("claude:session:older", updated_at=now - timedelta(minutes=2))
    subagent = _status("claude:agent:worker", updated_at=now)
    closed = _status(
        "claude:session:closed",
        updated_at=now,
        event_name="SessionEnd",
    )
    expired = _status(
        "claude:session:expired",
        updated_at=now - timedelta(seconds=1_201),
    )

    selected = select_clearable_completions(
        (older, subagent, closed, expired, latest_b, latest_a),
        (),
        collected_at=now,
        within_seconds=1_200,
    )

    assert selected == (latest_a, latest_b, older)
    assert select_clearable_completions(
        (subagent,),
        (),
        collected_at=now,
        within_seconds=1_200,
        include_subagents=True,
    ) == (subagent,)


def test_clearable_completions_keep_same_agent_from_distinct_sources() -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    first = _status(
        "claude:session:shared",
        updated_at=now,
        source_instance="local.one",
    )
    second = _status(
        first.agent_id,
        updated_at=now - timedelta(seconds=1),
        source_instance="local.two",
    )

    selected = select_clearable_completions(
        (first, second),
        (),
        collected_at=now,
        within_seconds=300,
    )

    assert selected == (first, second)


def test_unseen_completions_apply_every_acknowledgement_exclusion() -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    eligible = _status("claude:session:eligible", updated_at=now)
    subagent = _status("claude:agent:worker", updated_at=now)
    closed = _status(
        "claude:session:closed",
        updated_at=now,
        event_name="SessionEnd",
    )
    expired = _status(
        "claude:session:expired",
        updated_at=now - timedelta(seconds=301),
    )
    cleared = _status("claude:session:cleared", updated_at=now)
    visited = _status(
        "claude:session:visited",
        updated_at=now - timedelta(seconds=30),
    )
    attended = _status("claude:session:attended", updated_at=now)

    selected = select_unseen_completions(
        (eligible, subagent, closed, expired, cleared, visited, attended),
        (),
        collected_at=now,
        within_seconds=300,
        menu_last_opened_at=now - timedelta(seconds=15),
        acknowledged_keys={
            CompletionPresentationKey(
                source_key=SourceKey(
                    "claude",
                    "hooks",
                    "local.test",
                    "live_agent_events",
                ),
                agent_id=cleared.agent_id,
                event_name="Stop",
                completed_at_epoch=now.timestamp(),
            )
        },
        attended_prompt_monotonic={attended.agent_id: 900.0},
        now_monotonic=1_020.0,
        attended_quiet_seconds=120.0,
    )

    assert selected == (eligible,)


def test_unseen_completions_deduplicate_with_current_rows_winning_and_keep_order() -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    first = _status("claude:session:first", updated_at=now - timedelta(seconds=20))
    second = _status("claude:session:second", updated_at=now - timedelta(seconds=10))
    active = _status(
        "claude:session:active",
        mode=AgentMode.WORKING,
        updated_at=now,
    )
    stale_duplicate = _status(first.agent_id, updated_at=now)
    stale_blocked = _status(active.agent_id, updated_at=now)
    stale_only = _status("claude:session:stale", updated_at=now)

    selected = select_unseen_completions(
        (first, second, active),
        (stale_duplicate, stale_blocked, stale_only),
        collected_at=now,
        within_seconds=300,
        menu_last_opened_at=None,
        acknowledged_keys=frozenset(),
        attended_prompt_monotonic={},
        now_monotonic=10_000.0,
        attended_quiet_seconds=120.0,
    )

    assert selected == (first, second, stale_only)


def test_unseen_completion_becomes_eligible_after_attended_window() -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    completion = _status("claude:session:done", updated_at=now)

    selected = select_unseen_completions(
        (completion,),
        (),
        collected_at=now,
        within_seconds=300,
        menu_last_opened_at=None,
        acknowledged_keys=set(),
        attended_prompt_monotonic={completion.agent_id: 900.0},
        now_monotonic=1_020.001,
        attended_quiet_seconds=120.0,
    )

    assert selected == (completion,)


def test_unseen_completion_receipts_are_exact_to_event_time_and_source() -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    agent_id = "claude:session:reused"
    earlier = now - timedelta(minutes=1)
    current = _status(agent_id, updated_at=now)
    old_event_receipt = CompletionPresentationKey(
        source_key=SourceKey(
            "claude",
            "hooks",
            "local.test",
            "live_agent_events",
        ),
        agent_id=agent_id,
        event_name="Stop",
        completed_at_epoch=earlier.timestamp(),
    )
    other_source_receipt = CompletionPresentationKey(
        source_key=SourceKey(
            "claude",
            "hooks",
            "other.mac",
            "live_agent_events",
        ),
        agent_id=agent_id,
        event_name="Stop",
        completed_at_epoch=now.timestamp(),
    )

    selected = select_unseen_completions(
        (current,),
        (),
        collected_at=now,
        within_seconds=300,
        menu_last_opened_at=None,
        acknowledged_keys={old_event_receipt, other_source_receipt},
        attended_prompt_monotonic={},
        now_monotonic=10_000.0,
        attended_quiet_seconds=120.0,
    )

    assert selected == (current,)


def test_unseen_completions_keep_same_agent_from_distinct_sources() -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    first = _status(
        "claude:session:shared",
        updated_at=now,
        source_instance="local.one",
    )
    second = _status(
        first.agent_id,
        updated_at=now - timedelta(seconds=1),
        source_instance="local.two",
    )

    selected = select_unseen_completions(
        (first, second),
        (),
        collected_at=now,
        within_seconds=300,
        menu_last_opened_at=None,
        acknowledged_keys=frozenset(),
        attended_prompt_monotonic={},
        now_monotonic=10_000.0,
        attended_quiet_seconds=120.0,
    )

    assert selected == (first, second)


def test_unseen_completion_without_exact_work_key_cannot_be_receipt_suppressed() -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    unkeyed = _status(
        "claude:session:unkeyed",
        updated_at=now,
        keyed=False,
    )
    unrelated_receipt = CompletionPresentationKey(
        source_key=SourceKey(
            "claude",
            "hooks",
            "local.test",
            "live_agent_events",
        ),
        agent_id=unkeyed.agent_id,
        event_name="Stop",
        completed_at_epoch=now.timestamp(),
    )

    selected = select_unseen_completions(
        (unkeyed,),
        (),
        collected_at=now,
        within_seconds=300,
        menu_last_opened_at=None,
        acknowledged_keys={unrelated_receipt},
        attended_prompt_monotonic={},
        now_monotonic=10_000.0,
        attended_quiet_seconds=120.0,
    )

    assert selected == (unkeyed,)


def test_seen_id_plan_prioritizes_sorted_visible_completions_then_retained_ids() -> None:
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    newest = _status("claude:session:newest", updated_at=now)
    tie_b = _status("claude:session:b", updated_at=now - timedelta(seconds=1))
    tie_a = _status("claude:session:a", updated_at=now - timedelta(seconds=1))
    duplicate_newest = _status(
        newest.agent_id,
        updated_at=now - timedelta(minutes=1),
    )
    closed = _status(
        "claude:session:closed",
        updated_at=now,
        event_name="SessionEnd",
    )
    active = _status(
        "claude:session:active",
        mode=AgentMode.WORKING,
        updated_at=now,
    )

    planned = plan_seen_completion_ids(
        (tie_b, duplicate_newest, closed, newest, active, tie_a),
        {tie_a.agent_id, "retained-z", "retained-a"},
        limit=4,
    )

    assert planned == (
        newest.agent_id,
        tie_a.agent_id,
        tie_b.agent_id,
        "retained-a",
    )
    assert plan_seen_completion_ids((newest,), {"retained"}, limit=0) == ()
