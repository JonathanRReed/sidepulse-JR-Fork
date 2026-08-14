from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from sidepulse.attention import (
    AttentionProjection,
    LifecycleMode,
    ProjectedAgentRow,
)
from sidepulse.capacity_types import SourceKey
from sidepulse.mailbox import (
    AgentMailboxProjection,
    MailboxRow,
    MailboxSectionKind,
    normalized_activity_label,
    project_canonical_mailbox,
    project_mailbox,
)
from sidepulse.mailbox_preferences import (
    LegacyMailboxPreference as MailboxPreference,
)
from sidepulse.mailbox_preferences import (
    apply_mailbox_preferences,
)
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.operator_state import (
    AcknowledgementEligibility,
    CanonicalOperatorState,
    CanonicalRequestTruth,
    CanonicalWorkTruth,
    ClockContinuityState,
    ClockContinuityStatus,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
)
from sidepulse.provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderWatermark,
    RequestIdentifier,
    RequestKey,
    RequestKind,
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
    WorkLifecycle,
)

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


def _status(
    agent_id: str,
    mode: AgentMode,
    *,
    minutes_ago: int = 0,
    provider: str = "codex",
    session_id: str | None = None,
    event_name: str = "PostToolUse",
    tool_name: str | None = None,
    display_name: str | None = None,
    cwd: str | None = "/Users/private/project",
    message: str | None = "private prompt text",
) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=agent_id,
        display_name=display_name or agent_id.rsplit(":", 1)[-1],
        mode=mode,
        updated_at=NOW - timedelta(minutes=minutes_ago),
        event_name=event_name,
        session_id=session_id,
        cwd=cwd,
        tool_name=tool_name,
        message=message,
        origin="private-origin",
    )


def _row(
    agent_id: str,
    lifecycle_mode: LifecycleMode,
    *,
    minutes_ago: int = 0,
    actionable: bool = False,
    provider: str = "codex",
    session_id: str | None = None,
    source_mode: AgentMode | None = None,
    event_name: str = "PostToolUse",
    tool_name: str | None = None,
    display_name: str | None = None,
) -> ProjectedAgentRow:
    mode = source_mode or {
        LifecycleMode.IDLE: AgentMode.IDLE_READY,
        LifecycleMode.ACTIVE: AgentMode.TOOL_RUNNING,
        LifecycleMode.WAITING: AgentMode.WAITING_FOR_INPUT,
        LifecycleMode.COMPLETED_RECENTLY: AgentMode.COMPLETED,
        LifecycleMode.FAILED_VISIBLE: AgentMode.BLOCKED_ERROR,
        LifecycleMode.UNKNOWN: AgentMode.UNKNOWN,
    }[lifecycle_mode]
    status = _status(
        agent_id,
        mode,
        minutes_ago=minutes_ago,
        provider=provider,
        session_id=session_id,
        event_name=event_name,
        tool_name=tool_name,
        display_name=display_name,
    )
    return ProjectedAgentRow(
        agent_id=agent_id,
        provider=provider,
        display_name=status.display_name,
        lifecycle_mode=lifecycle_mode,
        actionable=actionable,
        is_subagent=status.is_subagent,
        updated_at=status.updated_at,
        source_status=status,
    )


def _projection(*rows: ProjectedAgentRow) -> AttentionProjection:
    actionable = tuple(
        sorted(
            (row for row in rows if row.actionable),
            key=lambda row: (row.updated_at, row.agent_id),
        )
    )
    return AttentionProjection(
        lifecycle_mode=rows[0].lifecycle_mode if rows else LifecycleMode.IDLE,
        actionable_attention=actionable,
        visible_rows=rows,
        transient_signals=(),
        dominant_provider=rows[0].provider if rows else None,
        click_target_agent_id=actionable[0].agent_id if actionable else None,
    )


def _section(projected, kind: MailboxSectionKind):
    return next(section for section in projected.sections if section.kind == kind)


def test_projects_authoritative_lifecycles_into_fixed_shelves() -> None:
    old_ask = _row(
        "codex:session:old-ask",
        LifecycleMode.WAITING,
        minutes_ago=8,
        actionable=True,
        event_name="PermissionRequest",
    )
    new_ask = _row(
        "claude:session:new-ask",
        LifecycleMode.WAITING,
        minutes_ago=2,
        actionable=True,
        provider="claude",
        event_name="Notification",
    )
    working = _row(
        "codex:session:working",
        LifecycleMode.ACTIVE,
        source_mode=AgentMode.WORKING,
    )
    tool_running = _row(
        "codex:session:tool",
        LifecycleMode.ACTIVE,
        source_mode=AgentMode.TOOL_RUNNING,
    )
    long_task = _row(
        "devin:session:long",
        LifecycleMode.ACTIVE,
        provider="devin",
        source_mode=AgentMode.LONG_TASK_PROGRESS,
    )
    failed = _row("codex:session:failed", LifecycleMode.FAILED_VISIBLE)
    unseen_done = _row("codex:session:done-new", LifecycleMode.COMPLETED_RECENTLY)
    seen_done = _row("codex:session:done-seen", LifecycleMode.COMPLETED_RECENTLY)
    idle = _row("codex:session:idle", LifecycleMode.IDLE)
    unknown = _row("codex:session:unknown", LifecycleMode.UNKNOWN)

    projected = project_mailbox(
        _projection(
            new_ask,
            failed,
            working,
            seen_done,
            old_ask,
            tool_running,
            long_task,
            unseen_done,
            idle,
            unknown,
        ),
        seen_completion_ids={seen_done.agent_id},
    )

    assert tuple(section.kind for section in projected.sections) == (
        MailboxSectionKind.NEEDS_YOU,
        MailboxSectionKind.IN_PROGRESS,
        MailboxSectionKind.READY_FOR_REVIEW,
        MailboxSectionKind.RECENT,
    )
    needs_you = _section(projected, MailboxSectionKind.NEEDS_YOU)
    assert tuple(row.agent_id for row in needs_you.rows) == (
        old_ask.agent_id,
        new_ask.agent_id,
    )
    assert tuple(row.navigation_agent_id for row in needs_you.rows) == (
        old_ask.agent_id,
        new_ask.agent_id,
    )
    assert all(row.work_key is None and row.request_key is None for row in needs_you.rows)
    assert all(row.actionable for row in needs_you.rows)

    assert {row.agent_id for row in _section(projected, MailboxSectionKind.IN_PROGRESS).rows} == {
        working.agent_id,
        tool_running.agent_id,
        long_task.agent_id,
    }
    ready = _section(projected, MailboxSectionKind.READY_FOR_REVIEW).rows
    assert {row.agent_id for row in ready} == {failed.agent_id, unseen_done.agent_id}
    assert next(row for row in ready if row.agent_id == failed.agent_id).actionable is False
    assert {row.agent_id for row in _section(projected, MailboxSectionKind.RECENT).rows} == {
        seen_done.agent_id,
        idle.agent_id,
        unknown.agent_id,
    }
    assert projected.active_count == 5
    assert projected.needs_you_count == 2
    assert projected.ready_count == 2


def test_tool_activity_changes_do_not_reorder_and_new_rows_append() -> None:
    first = _row(
        "codex:session:first",
        LifecycleMode.ACTIVE,
        minutes_ago=4,
        tool_name="Read",
    )
    second = _row(
        "codex:session:second",
        LifecycleMode.ACTIVE,
        minutes_ago=3,
        tool_name="Edit",
    )
    initial = project_mailbox(_projection(first, second))
    previous = dict(initial.retained_order)

    refreshed_first = replace(
        first,
        updated_at=NOW,
        source_status=replace(first.source_status, updated_at=NOW, tool_name="Bash"),
    )
    earlier_new_row = _row(
        "codex:session:new",
        LifecycleMode.ACTIVE,
        minutes_ago=20,
        tool_name="Grep",
    )
    refreshed = project_mailbox(
        _projection(second, earlier_new_row, refreshed_first),
        previous_order=previous,
    )

    rows = _section(refreshed, MailboxSectionKind.IN_PROGRESS).rows
    assert tuple(row.agent_id for row in rows) == (
        first.agent_id,
        second.agent_id,
        earlier_new_row.agent_id,
    )
    assert rows[0].activity_label == "Running command"
    assert rows[0].stable_order == previous[first.agent_id]
    assert rows[-1].stable_order > max(previous.values())


@pytest.mark.parametrize(
    ("current_lifecycle", "current_mode", "expected_section"),
    (
        (
            LifecycleMode.ACTIVE,
            AgentMode.WORKING,
            MailboxSectionKind.IN_PROGRESS,
        ),
        (
            LifecycleMode.COMPLETED_RECENTLY,
            AgentMode.COMPLETED,
            MailboxSectionKind.READY_FOR_REVIEW,
        ),
    ),
)
def test_newer_identity_copy_cannot_resurrect_an_older_actionable_ask(
    current_lifecycle: LifecycleMode,
    current_mode: AgentMode,
    expected_section: MailboxSectionKind,
) -> None:
    old_ask = _row(
        "codex:session:reused",
        LifecycleMode.WAITING,
        minutes_ago=5,
        actionable=True,
        event_name="PermissionRequest",
    )
    current = _row(
        "codex:session:reused",
        current_lifecycle,
        minutes_ago=0,
        source_mode=current_mode,
    )

    projected = project_mailbox(_projection(current, old_ask))

    assert _section(projected, MailboxSectionKind.NEEDS_YOU).rows == ()
    assert tuple(row.agent_id for row in _section(projected, expected_section).rows) == (
        current.agent_id,
    )


def test_tied_identity_timestamp_prefers_non_actionable_current_lifecycle() -> None:
    stale_ask = _row(
        "codex:session:tied",
        LifecycleMode.WAITING,
        actionable=True,
        event_name="PermissionRequest",
    )
    current = _row(
        "codex:session:tied",
        LifecycleMode.ACTIVE,
        source_mode=AgentMode.WORKING,
    )

    projected = project_mailbox(_projection(stale_ask, current))

    assert _section(projected, MailboxSectionKind.NEEDS_YOU).rows == ()
    assert tuple(
        row.agent_id for row in _section(projected, MailboxSectionKind.IN_PROGRESS).rows
    ) == (current.agent_id,)


@pytest.mark.parametrize(
    ("stale_lifecycle", "stale_mode"),
    (
        (LifecycleMode.FAILED_VISIBLE, AgentMode.BLOCKED_ERROR),
        (LifecycleMode.COMPLETED_RECENTLY, AgentMode.COMPLETED),
    ),
)
def test_tied_terminal_copy_cannot_resurrect_over_current_active_lifecycle(
    stale_lifecycle: LifecycleMode,
    stale_mode: AgentMode,
) -> None:
    stale_terminal = _row(
        "codex:session:tied-terminal",
        stale_lifecycle,
        source_mode=stale_mode,
    )
    current = _row(
        "codex:session:tied-terminal",
        LifecycleMode.ACTIVE,
        source_mode=AgentMode.WORKING,
    )

    projected = project_mailbox(_projection(stale_terminal, current))

    assert tuple(
        row.agent_id for row in _section(projected, MailboxSectionKind.IN_PROGRESS).rows
    ) == (current.agent_id,)
    assert _section(projected, MailboxSectionKind.READY_FOR_REVIEW).rows == ()


def test_newer_worker_copy_prevents_stale_ask_and_duplicate_rollup_count() -> None:
    parent = _row("claude:session:main", LifecycleMode.ACTIVE, provider="claude")
    stale_worker_ask = _row(
        "claude:agent:reused",
        LifecycleMode.WAITING,
        minutes_ago=4,
        actionable=True,
        provider="claude",
        session_id="main",
        event_name="PermissionRequest",
    )
    current_worker = _row(
        "claude:agent:reused",
        LifecycleMode.ACTIVE,
        provider="claude",
        session_id="main",
        source_mode=AgentMode.WORKING,
    )

    projected = project_mailbox(_projection(parent, stale_worker_ask, current_worker))

    assert _section(projected, MailboxSectionKind.NEEDS_YOU).rows == ()
    rows = _section(projected, MailboxSectionKind.IN_PROGRESS).rows
    assert tuple(row.agent_id for row in rows) == (parent.agent_id,)
    assert rows[0].worker_count == 1
    assert rows[0].navigation_agent_id == parent.agent_id


def test_workers_roll_up_under_parent_and_actionable_worker_keeps_click_identity() -> None:
    parent = _row("claude:session:main", LifecycleMode.ACTIVE, provider="claude")
    active_worker = _row(
        "claude:agent:worker-active",
        LifecycleMode.ACTIVE,
        provider="claude",
        session_id="main",
    )
    worker_ask = _row(
        "claude:agent:worker-ask",
        LifecycleMode.WAITING,
        minutes_ago=5,
        actionable=True,
        provider="claude",
        session_id="main",
        event_name="PermissionRequest",
    )

    projected = project_mailbox(_projection(parent, active_worker, worker_ask))

    all_rows = tuple(row for section in projected.sections for row in section.rows)
    assert tuple(row.agent_id for row in all_rows) == (parent.agent_id,)
    rollup = all_rows[0]
    assert rollup.worker_count == 2
    assert rollup.actionable is True
    assert rollup.lifecycle_mode == LifecycleMode.WAITING
    assert rollup.navigation_agent_id == worker_ask.agent_id
    assert _section(projected, MailboxSectionKind.NEEDS_YOU).rows == (rollup,)


def test_snoozed_worker_family_wakes_to_exact_actionable_worker_identity() -> None:
    parent = _row("claude:session:main-wake", LifecycleMode.ACTIVE, provider="claude")
    worker_ask = _row(
        "claude:agent:worker-wake",
        LifecycleMode.WAITING,
        actionable=True,
        provider="claude",
        session_id="main-wake",
        event_name="PermissionRequest",
    )
    mailbox = project_mailbox(_projection(parent, worker_ask))
    preference = MailboxPreference(
        parent.agent_id,
        snoozed_at=(NOW - timedelta(minutes=1)).timestamp(),
        snoozed_until=(NOW + timedelta(hours=1)).timestamp(),
    )

    result = apply_mailbox_preferences(mailbox, (preference,), now=NOW.timestamp())
    row = _section(result.projection, MailboxSectionKind.NEEDS_YOU).rows[0]

    assert row.agent_id == parent.agent_id
    assert row.navigation_agent_id == worker_ask.agent_id
    assert row.worker_count == 1
    assert result.woke_agent_ids == (parent.agent_id,)


def test_one_thousand_source_rows_remain_one_hundred_primary_preferences() -> None:
    mains = tuple(
        _row(
            f"claude:session:main-{index:03d}",
            LifecycleMode.ACTIVE,
            provider="claude",
            session_id=f"main-{index:03d}",
        )
        for index in range(100)
    )
    workers = tuple(
        _row(
            f"claude:agent:worker-{parent_index:03d}-{worker_index}",
            LifecycleMode.WAITING if (parent_index, worker_index) == (57, 4) else LifecycleMode.ACTIVE,
            actionable=(parent_index, worker_index) == (57, 4),
            provider="claude",
            session_id=f"main-{parent_index:03d}",
            event_name="PermissionRequest" if (parent_index, worker_index) == (57, 4) else "PostToolUse",
        )
        for parent_index in range(100)
        for worker_index in range(9)
    )
    mailbox = project_mailbox(
        _projection(*mains, *workers),
        max_rows_per_section=100,
        max_primary_agents=100,
    )
    preferences = tuple(
        MailboxPreference(
            main.agent_id,
            last_visited_at=NOW.timestamp() - float(index),
        )
        for index, main in enumerate(mains)
    ) + tuple(
        MailboxPreference(worker.agent_id, last_visited_at=NOW.timestamp())
        for worker in workers
    )

    result = apply_mailbox_preferences(mailbox, preferences, now=NOW.timestamp())
    all_rows = tuple(row for section in result.projection.sections for row in section.rows)
    family = next(row for row in all_rows if row.agent_id == mains[57].agent_id)

    assert len(all_rows) == 100
    assert len(result.retained_preferences) == 100
    assert all(":agent:" not in preference.agent_id for preference in result.retained_preferences)
    assert family.worker_count == 9
    assert family.navigation_agent_id == "claude:agent:worker-057-4"
    assert family.actionable is True


def test_orphan_workers_form_one_deterministic_background_rollup() -> None:
    working = _row(
        "claude:agent:working",
        LifecycleMode.ACTIVE,
        provider="claude",
        session_id="missing",
        minutes_ago=1,
    )
    second = _row(
        "codex:agent:second",
        LifecycleMode.ACTIVE,
        provider="codex",
        session_id="gone",
        minutes_ago=2,
    )

    forward = project_mailbox(_projection(working, second))
    reverse = project_mailbox(_projection(second, working))
    forward_rows = tuple(row for section in forward.sections for row in section.rows)
    reverse_rows = tuple(row for section in reverse.sections for row in section.rows)

    assert len(forward_rows) == 1
    assert forward_rows == reverse_rows
    assert forward_rows[0].display_name == "Background agents"
    assert forward_rows[0].worker_count == 2
    assert forward_rows[0].lifecycle_mode == LifecycleMode.ACTIVE
    assert forward_rows[0].navigation_agent_id is None


def test_each_shelf_is_bounded_with_exact_overflow_count() -> None:
    rows = tuple(
        _row(
            f"codex:session:{index:02d}",
            LifecycleMode.ACTIVE,
            minutes_ago=20 - index,
        )
        for index in range(15)
    )

    section = _section(project_mailbox(_projection(*rows)), MailboxSectionKind.IN_PROGRESS)

    assert len(section.rows) == 12
    assert section.overflow_count == 3


def test_retention_evicts_absent_identities_before_live_actionable_rows() -> None:
    previous = {f"expired:{index:03d}": index for index in range(100)}
    ask = _row(
        "codex:session:live-ask",
        LifecycleMode.WAITING,
        actionable=True,
        event_name="PermissionRequest",
    )

    projected = project_mailbox(
        _projection(ask),
        previous_order=previous,
        max_primary_agents=100,
    )
    retained = dict(projected.retained_order)

    assert len(retained) == 100
    assert ask.agent_id in retained
    assert "expired:000" not in retained
    assert set(previous) - set(retained) == {"expired:000"}


def test_worker_identities_never_consume_retained_primary_capacity() -> None:
    previous = {
        **{f"codex:session:{index:03d}": index for index in range(99)},
        "claude:agent:old-worker": 99,
    }
    parent = _row("claude:session:main", LifecycleMode.ACTIVE, provider="claude")
    worker = _row(
        "claude:agent:current-worker",
        LifecycleMode.ACTIVE,
        provider="claude",
        session_id="main",
    )

    projected = project_mailbox(
        _projection(parent, worker),
        previous_order=previous,
        max_primary_agents=100,
    )
    retained = dict(projected.retained_order)

    assert "claude:agent:old-worker" not in retained
    assert worker.agent_id not in retained
    assert parent.agent_id in retained
    assert len(retained) == 100


@pytest.mark.parametrize(
    ("mode", "tool_name", "expected"),
    (
        (AgentMode.TOOL_RUNNING, "Read", "Reading files"),
        (AgentMode.TOOL_RUNNING, "read_file", "Reading files"),
        (AgentMode.TOOL_RUNNING, "Edit", "Editing files"),
        (AgentMode.TOOL_RUNNING, "apply_patch", "Editing files"),
        (AgentMode.TOOL_RUNNING, "Grep", "Searching files"),
        (AgentMode.TOOL_RUNNING, "glob", "Searching files"),
        (AgentMode.TOOL_RUNNING, "Bash", "Running command"),
        (AgentMode.TOOL_RUNNING, "exec_command", "Running command"),
        (AgentMode.WORKING, None, "Thinking"),
        (AgentMode.LONG_TASK_PROGRESS, None, "Thinking"),
        (AgentMode.WAITING_FOR_INPUT, None, "Waiting for approval"),
    ),
)
def test_normalized_activity_uses_product_owned_vocabulary(
    mode: AgentMode,
    tool_name: str | None,
    expected: str,
) -> None:
    status = _status(
        "codex:session:activity",
        mode,
        tool_name=tool_name,
        cwd="/Users/jonathan/Secret Folder",
        message="approve token sk-private-value",
    )

    assert normalized_activity_label(status) == expected


def test_unknown_activity_is_sanitized_bounded_and_never_uses_payload_fields() -> None:
    ordinary = _status(
        "codex:session:ordinary",
        AgentMode.TOOL_RUNNING,
        tool_name="custom_analysis_v2",
        cwd="/Users/jonathan/Secret Folder",
        message="authorization Bearer private-value",
    )
    suspicious = _status(
        "codex:session:suspicious",
        AgentMode.TOOL_RUNNING,
        tool_name="https://private.example/run?token=SECRET-VALUE",
        cwd="/private/path",
        message="raw private prompt",
    )

    ordinary_label = normalized_activity_label(ordinary)
    assert ordinary_label == "Using Custom Analysis V2"
    assert len(ordinary_label) <= 48
    assert normalized_activity_label(suspicious) == "Using tool"
    combined_private_text = " ".join(
        value
        for value in (
            ordinary.cwd,
            ordinary.message,
            suspicious.cwd,
            suspicious.message,
            suspicious.tool_name,
        )
        if value
    )
    assert all(secret not in ordinary_label for secret in combined_private_text.split())


def test_non_activity_lifecycles_do_not_surface_raw_tool_or_message_text() -> None:
    for mode in (
        AgentMode.IDLE_READY,
        AgentMode.COMPLETED,
        AgentMode.BLOCKED_ERROR,
        AgentMode.UNKNOWN,
    ):
        status = _status(
            f"codex:session:{mode.value}",
            mode,
            tool_name="private_tool_name",
            message="private message text",
        )
        assert normalized_activity_label(status) is None


def _canonical_work_key(
    work_id: str,
    *,
    source_instance: str = "local:01",
) -> WorkKey:
    source = SourceKey("codex", "hooks", source_instance, "live_agent_events")
    return WorkKey(source, WorkIdentifier(work_id))


def _canonical_watermark(
    key: WorkKey,
    *,
    epoch: float,
    token: str,
) -> ProviderWatermark:
    return ProviderWatermark(
        source_key=key.source_key,
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        occurred_at_epoch=epoch,
        event_token=EventToken(token),
        sequence=None,
        tie_break_rank=10,
    )


def _canonical_request(
    key: RequestKey,
    *,
    phase: RequestPhase = RequestPhase.LIVE_UNACKNOWLEDGED,
    opened_at: float = NOW.timestamp() - 60.0,
) -> CanonicalRequestTruth:
    watermark = _canonical_watermark(
        key.work_key,
        epoch=opened_at,
        token=f"event:{key.request_id.value}",
    )
    return CanonicalRequestTruth(
        key=key,
        phase=phase,
        request_kind=RequestKind.PERMISSION,
        next_actor=NextActor.USER if phase is not RequestPhase.RESOLVED else NextActor.NONE,
        watermark=watermark,
        source_freshness=SourceFreshness.FRESH,
        acknowledgement_eligibility=(
            AcknowledgementEligibility.ELIGIBLE
            if phase is RequestPhase.LIVE_UNACKNOWLEDGED
            else AcknowledgementEligibility.RESOLVED
        ),
        semantic_event_key=SemanticEventKey(
            key,
            TransitionKind.REQUEST_OPENED,
            watermark,
        ),
        opened_at_epoch=opened_at,
        eligible_elapsed_seconds=60.0,
    )


def _canonical_work(
    key: WorkKey,
    *,
    lifecycle: WorkLifecycle = WorkLifecycle.ACTIVE,
    next_actor: NextActor = NextActor.PROVIDER,
    parent_key: WorkKey | None = None,
    requests: tuple[RequestKey, ...] = (),
    safe_label: str | None = None,
    epoch: float = NOW.timestamp(),
    freshness: SourceFreshness = SourceFreshness.FRESH,
) -> CanonicalWorkTruth:
    return CanonicalWorkTruth(
        key=key,
        lifecycle=lifecycle,
        watermark=_canonical_watermark(key, epoch=epoch, token=f"event:{key.work_id.value}"),
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        source_health=SourceHealth.HEALTHY,
        source_freshness=freshness,
        next_actor=next_actor,
        safe_label=safe_label or f"Codex {key.work_id.value}",
        parent_key=parent_key,
        request_keys=requests,
        timing_uncertain=freshness is SourceFreshness.TIMING_UNCERTAIN,
    )


def _canonical_state(
    works: tuple[CanonicalWorkTruth, ...],
    requests: tuple[CanonicalRequestTruth, ...] = (),
    *,
    generation: int = 1,
) -> CanonicalOperatorState:
    return CanonicalOperatorState(
        schema_version=1,
        generation=generation,
        works=works,
        requests=requests,
        source_watermarks=(),
        timing_uncertain_sources=(),
        clock_continuity=ClockContinuityState(ClockContinuityStatus.STABLE, None, 0),
        last_clock=None,
    )


def test_canonical_mailbox_keeps_source_scoped_identity_and_exact_retained_order() -> None:
    first_key = _canonical_work_key("same", source_instance="local:01")
    second_key = _canonical_work_key("same", source_instance="local:02")
    first = _canonical_work(first_key, epoch=NOW.timestamp() - 30.0)
    second = _canonical_work(second_key, epoch=NOW.timestamp() - 20.0)

    initial = project_canonical_mailbox(_canonical_state((second, first)))
    refreshed = project_canonical_mailbox(
        _canonical_state(
            (
                replace(first, watermark=_canonical_watermark(first_key, epoch=NOW.timestamp(), token="event:new")),
                second,
            ),
            generation=2,
        ),
        previous_order=dict(initial.retained_order),
    )

    rows = _section(refreshed, MailboxSectionKind.IN_PROGRESS).rows
    assert tuple(row.work_key for row in rows) == (first_key, second_key)
    assert dict(refreshed.retained_order) == dict(initial.retained_order)
    assert len(dict(refreshed.retained_order)) == 2


def test_canonical_mailbox_joins_only_exact_request_keys_and_attaches_workers() -> None:
    parent_key = _canonical_work_key("family")
    worker_key = _canonical_work_key("worker")
    sibling_source_worker_key = _canonical_work_key("worker", source_instance="local:02")
    actionable_key = RequestKey(worker_key, RequestIdentifier("request:same"))
    colliding_key = RequestKey(sibling_source_worker_key, RequestIdentifier("request:same"))
    parent = _canonical_work(parent_key)
    worker = _canonical_work(
        worker_key,
        parent_key=parent_key,
        next_actor=NextActor.USER,
        requests=(actionable_key,),
    )
    request = _canonical_request(actionable_key, opened_at=NOW.timestamp() - 120.0)
    collision = _canonical_request(colliding_key, opened_at=NOW.timestamp() - 600.0)

    projection = project_canonical_mailbox(
        _canonical_state((parent, worker), (collision, request))
    )
    row = _section(projection, MailboxSectionKind.NEEDS_YOU).rows[0]

    assert row.work_key == parent_key
    assert row.request_key == actionable_key
    assert row.request_keys == (actionable_key,)
    assert row.actionable is True
    assert row.worker_count == 1
    assert row.updated_at_epoch == NOW.timestamp() - 120.0


def test_canonical_mailbox_retires_stale_worker_without_changing_primary_truth() -> None:
    """Keeping a stale worker would resurrect its Ask over a completed primary."""
    parent_key = _canonical_work_key("completed-family")
    worker_key = _canonical_work_key("stale-worker")
    worker_request_key = RequestKey(
        worker_key,
        RequestIdentifier("request:stale-worker"),
    )
    parent = _canonical_work(
        parent_key,
        lifecycle=WorkLifecycle.COMPLETED,
        next_actor=NextActor.NONE,
    )
    stale_worker = _canonical_work(
        worker_key,
        parent_key=parent_key,
        lifecycle=WorkLifecycle.WAITING,
        next_actor=NextActor.USER,
        requests=(worker_request_key,),
        freshness=SourceFreshness.STALE,
    )
    stale_request = _canonical_request(worker_request_key)

    projection = project_canonical_mailbox(
        _canonical_state((parent, stale_worker), (stale_request,))
    )
    row = _section(projection, MailboxSectionKind.READY_FOR_REVIEW).rows[0]

    assert row.work_key == parent_key
    assert row.lifecycle is WorkLifecycle.COMPLETED
    assert row.next_actor is NextActor.NONE
    assert row.actionable is False
    assert row.request_key is None
    assert row.request_keys == ()
    assert row.worker_count == 0
    assert projection.needs_you_count == 0
    assert projection.ready_count == 1


def test_canonical_mailbox_retirement_preserves_exact_primary_request() -> None:
    """Dropping a stale worker must not drop or substitute the primary's request."""
    parent_key = _canonical_work_key("request-family")
    worker_key = _canonical_work_key("request-worker")
    parent_request_key = RequestKey(
        parent_key,
        RequestIdentifier("request:primary"),
    )
    worker_request_key = RequestKey(
        worker_key,
        RequestIdentifier("request:worker"),
    )
    parent = _canonical_work(
        parent_key,
        lifecycle=WorkLifecycle.WAITING,
        next_actor=NextActor.USER,
        requests=(parent_request_key,),
    )
    stale_worker = _canonical_work(
        worker_key,
        parent_key=parent_key,
        lifecycle=WorkLifecycle.WAITING,
        next_actor=NextActor.USER,
        requests=(worker_request_key,),
        freshness=SourceFreshness.STALE,
    )
    parent_request = _canonical_request(
        parent_request_key,
        opened_at=NOW.timestamp() - 30.0,
    )
    worker_request = _canonical_request(
        worker_request_key,
        opened_at=NOW.timestamp() - 300.0,
    )

    projection = project_canonical_mailbox(
        _canonical_state(
            (parent, stale_worker),
            (worker_request, parent_request),
        )
    )
    row = _section(projection, MailboxSectionKind.NEEDS_YOU).rows[0]

    assert row.work_key == parent_key
    assert row.request_key == parent_request_key
    assert row.request_keys == (parent_request_key,)
    assert row.worker_count == 0
    assert row.updated_at_epoch == NOW.timestamp() - 30.0


def test_canonical_mailbox_keeps_fresh_terminal_worker_until_it_becomes_stale() -> None:
    """Retiring every terminal worker would erase fresh family outcome attribution."""
    parent_key = _canonical_work_key("active-family")
    worker_key = _canonical_work_key("terminal-worker")
    parent = _canonical_work(parent_key)
    terminal_worker = _canonical_work(
        worker_key,
        parent_key=parent_key,
        lifecycle=WorkLifecycle.FAILED,
        next_actor=NextActor.NONE,
    )

    fresh = project_canonical_mailbox(
        _canonical_state((parent, terminal_worker), generation=4)
    )
    fresh_row = _section(fresh, MailboxSectionKind.IN_PROGRESS).rows[0]
    stale = project_canonical_mailbox(
        _canonical_state(
            (
                parent,
                replace(terminal_worker, source_freshness=SourceFreshness.STALE),
            ),
            generation=5,
        ),
        previous_order=dict(fresh.retained_order),
    )
    stale_row = _section(stale, MailboxSectionKind.IN_PROGRESS).rows[0]

    assert fresh_row.work_key == stale_row.work_key == parent_key
    assert fresh_row.lifecycle is stale_row.lifecycle is WorkLifecycle.ACTIVE
    assert fresh_row.worker_count == 1
    assert stale_row.worker_count == 0
    assert dict(stale.retained_order) == dict(fresh.retained_order)


def test_canonical_mailbox_does_not_promote_worker_with_missing_parent() -> None:
    """Treating an orphan worker as primary would violate the exact parent contract."""
    missing_parent = _canonical_work_key("missing-parent")
    orphan = _canonical_work(
        _canonical_work_key("orphan-worker"),
        parent_key=missing_parent,
    )

    projection = project_canonical_mailbox(_canonical_state((orphan,)))

    assert all(section.rows == () for section in projection.sections)
    assert projection.active_count == 0
    assert projection.needs_you_count == 0
    assert projection.ready_count == 0
    assert projection.retained_order == ()


def test_canonical_mailbox_parent_swap_is_exact_and_not_sticky() -> None:
    """Remembering a prior family would attach a worker to the wrong primary."""
    first_key = _canonical_work_key("parent-a")
    second_key = _canonical_work_key("parent-b")
    worker_key = _canonical_work_key("moving-worker")
    first = _canonical_work(first_key, epoch=NOW.timestamp() - 20.0)
    second = _canonical_work(second_key, epoch=NOW.timestamp() - 10.0)
    under_first = _canonical_work(worker_key, parent_key=first_key)

    initial = project_canonical_mailbox(
        _canonical_state((first, second, under_first), generation=7)
    )
    moved = project_canonical_mailbox(
        _canonical_state(
            (first, second, replace(under_first, parent_key=second_key)),
            generation=8,
        ),
        previous_order=dict(initial.retained_order),
    )
    rows = {
        row.work_key: row
        for row in _section(moved, MailboxSectionKind.IN_PROGRESS).rows
    }

    assert rows[first_key].worker_count == 0
    assert rows[second_key].worker_count == 1
    assert dict(moved.retained_order) == dict(initial.retained_order)


def test_canonical_mailbox_restart_and_compaction_preserve_episode_order() -> None:
    """A generation reset or newer compaction watermark must not reshuffle families."""
    first_key = _canonical_work_key("restart-first")
    second_key = _canonical_work_key("restart-second")
    first = _canonical_work(first_key, epoch=NOW.timestamp() - 20.0)
    second = _canonical_work(second_key, epoch=NOW.timestamp() - 10.0)
    initial = project_canonical_mailbox(
        _canonical_state((first, second), generation=41)
    )
    compacted_first = replace(
        first,
        watermark=_canonical_watermark(
            first_key,
            epoch=NOW.timestamp() + 60.0,
            token="event:post-compaction",
        ),
    )

    restarted = project_canonical_mailbox(
        _canonical_state((second, compacted_first), generation=0),
        previous_order=dict(initial.retained_order),
    )

    assert tuple(
        row.work_key
        for row in _section(restarted, MailboxSectionKind.IN_PROGRESS).rows
    ) == (first_key, second_key)
    assert dict(restarted.retained_order) == dict(initial.retained_order)


def test_canonical_mailbox_restart_retires_restored_worker_not_primary_outcome() -> None:
    """Restored worker metadata must not survive restart as a live display row."""
    parent_key = _canonical_work_key("restored-primary")
    parent = _canonical_work(
        parent_key,
        lifecycle=WorkLifecycle.COMPLETED,
        next_actor=NextActor.NONE,
        freshness=SourceFreshness.RESTORED,
    )
    restored_worker = _canonical_work(
        _canonical_work_key("restored-worker"),
        parent_key=parent_key,
        lifecycle=WorkLifecycle.COMPLETED,
        next_actor=NextActor.NONE,
        freshness=SourceFreshness.RESTORED,
    )

    projection = project_canonical_mailbox(
        _canonical_state((parent, restored_worker), generation=0)
    )
    row = _section(projection, MailboxSectionKind.READY_FOR_REVIEW).rows[0]

    assert row.work_key == parent_key
    assert row.lifecycle is WorkLifecycle.COMPLETED
    assert row.source_freshness is SourceFreshness.RESTORED
    assert row.worker_count == 0


def test_canonical_mailbox_uses_only_safe_label_and_never_request_or_identifier_text() -> None:
    key = _canonical_work_key("opaque-private-looking-id")
    label = "Codex 7F3A"
    work = _canonical_work(key, safe_label=label)

    projection = project_canonical_mailbox(_canonical_state((work,)))
    row = _section(projection, MailboxSectionKind.IN_PROGRESS).rows[0]

    assert row.safe_label == label
    assert key.work_id.value not in row.safe_label


def test_canonical_mailbox_bounds_shelves_and_retained_primary_families() -> None:
    works = tuple(
        _canonical_work(
            _canonical_work_key(f"work:{index:03d}"),
            epoch=NOW.timestamp() + float(index),
        )
        for index in range(105)
    )

    projection = project_canonical_mailbox(
        _canonical_state(works),
        max_rows_per_section=12,
        max_primary_agents=100,
    )
    section = _section(projection, MailboxSectionKind.IN_PROGRESS)

    assert len(section.rows) == 12
    assert section.overflow_count == 93
    assert len(projection.retained_order) == 100


def test_canonical_mailbox_row_and_projection_expose_only_task_two_authority_fields() -> None:
    assert tuple(field.name for field in __import__("dataclasses").fields(MailboxRow)) == (
        "work_key",
        "request_key",
        "safe_label",
        "lifecycle",
        "next_actor",
        "source_freshness",
        "request_keys",
        "actionable",
        "worker_count",
        "updated_at_epoch",
        "stable_order",
        "timing_uncertain",
    )
    assert tuple(field.name for field in __import__("dataclasses").fields(AgentMailboxProjection)) == (
        "sections",
        "active_count",
        "needs_you_count",
        "ready_count",
        "retained_order",
    )
