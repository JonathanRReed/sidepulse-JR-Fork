from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

import sidepulse.clear_agents as clear_agents_module
from sidepulse.capacity_types import SourceKey
from sidepulse.clear_agents import (
    MAX_CLEAR_TARGETS,
    ClearAgentsBatchReceipt,
    ClearAgentsPlanError,
    ClearAgentsRefusal,
    ClearAgentsState,
    CompletionPresentationKey,
    CompletionPresentationReceipt,
    completion_presentation_key,
    plan_clear_agents_commit,
    plan_clear_agents_undo,
    project_clear_agents_preview,
)
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.provider_facts import WorkIdentifier, WorkKey

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
NOW_EPOCH = NOW.timestamp()


def _source(
    provider: str = "codex", source_instance: str = "local"
) -> SourceKey:
    return SourceKey(provider, "hooks", source_instance, "agent_events")


def _status(
    agent_id: str,
    *,
    provider: str = "codex",
    source: SourceKey | None = None,
    mode: AgentMode = AgentMode.COMPLETED,
    event_name: str = "Stop",
    updated_at: datetime = NOW,
    keyed: bool = True,
    display_name: str = "Codex",
) -> AgentStatus:
    actual_source = source or _source(provider)
    return AgentStatus(
        provider=provider,
        agent_id=agent_id,
        display_name=display_name,
        mode=mode,
        updated_at=updated_at,
        event_name=event_name,
        work_key=(
            WorkKey(actual_source, WorkIdentifier(agent_id.replace(":", ".")))
            if keyed
            else None
        ),
    )


def _commit(
    statuses: tuple[AgentStatus, ...],
    *,
    state: ClearAgentsState = ClearAgentsState(),
    batch_id: str = "batch-1",
    at: float = NOW_EPOCH,
):
    preview = project_clear_agents_preview(statuses, state=state, now_epoch=at)
    return plan_clear_agents_commit(
        preview,
        preview,
        state,
        batch_id=batch_id,
        committed_at_epoch=at,
    )


def _receipt(status: AgentStatus, acknowledged_at: float) -> CompletionPresentationReceipt:
    key = completion_presentation_key(status)
    assert key is not None
    return CompletionPresentationReceipt(key, acknowledged_at)


def _receipt_state(
    rows: tuple[tuple[AgentStatus, float], ...],
    *,
    generation: int = 0,
    latest_batch: ClearAgentsBatchReceipt | None = None,
) -> ClearAgentsState:
    return ClearAgentsState(
        generation=generation,
        receipts=tuple(
            sorted(
                (_receipt(status, acknowledged_at) for status, acknowledged_at in rows),
                key=lambda receipt: receipt.key,
            )
        ),
        latest_batch=latest_batch,
    )


def test_key_is_exact_source_bound_content_free_and_immutable() -> None:
    status = _status("codex:session:one")

    key = completion_presentation_key(status)

    assert key == CompletionPresentationKey(
        source_key=_source(),
        agent_id="codex:session:one",
        event_name="Stop",
        completed_at_epoch=NOW_EPOCH,
    )
    assert not hasattr(key, "message")
    assert not hasattr(key, "cwd")
    with pytest.raises(FrozenInstanceError):
        key.agent_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "status",
    (
        _status("codex:session:working", mode=AgentMode.WORKING),
        _status("codex:session:closed", event_name="SessionEnd"),
        _status("codex:session:unkeyed", keyed=False),
        _status(
            "codex:session:mismatch",
            provider="codex",
            source=_source("claude"),
        ),
    ),
)
def test_only_exact_local_completed_events_produce_keys(status: AgentStatus) -> None:
    assert completion_presentation_key(status) is None


def test_preview_is_deterministic_bounded_and_reports_protected_reasons() -> None:
    clear_b = _status("codex:session:b", updated_at=NOW + timedelta(seconds=2))
    clear_a = _status("codex:session:a", updated_at=NOW + timedelta(seconds=1))
    active = _status("codex:session:active", mode=AgentMode.WORKING)
    waiting = _status("codex:session:waiting", mode=AgentMode.WAITING_FOR_INPUT)
    failed = _status("codex:session:failed", mode=AgentMode.BLOCKED_ERROR)
    queued = _status("codex:session:queued", mode=AgentMode.IDLE_READY)
    unkeyed = _status("codex:session:unkeyed", keyed=False)
    remote = _status("remote:studio:codex:session:done")
    session_end = _status("codex:session:closed", event_name="SessionEnd")

    preview = project_clear_agents_preview(
        (clear_b, clear_a, unkeyed),
        state=ClearAgentsState(),
        now_epoch=NOW_EPOCH,
        protected_statuses=(active, waiting, failed, queued, remote, session_end),
        queued_agent_ids=(queued.agent_id,),
    )
    reordered = project_clear_agents_preview(
        (unkeyed, clear_a, clear_b),
        state=ClearAgentsState(),
        now_epoch=NOW_EPOCH,
        protected_statuses=(session_end, remote, queued, failed, waiting, active),
        queued_agent_ids=(queued.agent_id,),
    )

    assert preview == reordered
    assert preview.clearable_count == 2
    assert preview.clearable_keys == tuple(sorted(preview.clearable_keys))
    assert tuple(item.key.completed_at_epoch for item in preview.items) == (
        (NOW + timedelta(seconds=2)).timestamp(),
        (NOW + timedelta(seconds=1)).timestamp(),
    )
    assert tuple(item.safe_label for item in preview.items) == ("Codex", "Codex")
    assert preview.protected_counts.active == 1
    assert preview.protected_counts.waiting == 1
    assert preview.protected_counts.failed == 1
    assert preview.protected_counts.queued == 1
    assert preview.protected_counts.remote_completions == 1
    assert preview.protected_counts.unkeyed_local_completions == 1
    assert preview.protected_counts.other == 1
    assert preview.protected_counts.total == 7
    assert all("/" not in fact for fact in preview.preservation_facts)


def test_preview_label_falls_back_instead_of_rendering_a_raw_path() -> None:
    preview = project_clear_agents_preview(
        (_status("codex:session:path", display_name="/Users/person/private"),),
        state=ClearAgentsState(),
        now_epoch=NOW_EPOCH,
    )

    assert preview.items[0].safe_label == "Codex"


def test_commit_adds_only_exact_new_receipts_and_records_five_minute_undo() -> None:
    first = _status("codex:session:first")
    second = _status("codex:session:second", updated_at=NOW + timedelta(seconds=1))

    plan = _commit((second, first))

    assert plan.previous_state == ClearAgentsState()
    assert plan.next_state.generation == 1
    assert plan.cleared_count == 2
    assert plan.next_state.acknowledged_keys == frozenset(
        completion_presentation_key(row) for row in (first, second)
    )
    assert (
        plan.batch_receipt.undo_deadline_epoch
        - plan.batch_receipt.committed_at_epoch
        == 300.0
    )
    assert plan.next_state.latest_batch == plan.batch_receipt


def test_commit_evicts_old_acknowledgements_when_receipt_cap_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _status("codex:session:old", updated_at=NOW - timedelta(seconds=3))
    middle = _status("codex:session:middle", updated_at=NOW - timedelta(seconds=2))
    newest = _status("codex:session:newest", updated_at=NOW - timedelta(seconds=1))
    state = _receipt_state(((old, 10.0), (middle, 20.0), (newest, 30.0)))
    monkeypatch.setattr(clear_agents_module, "MAX_COMPLETION_RECEIPTS", 3)
    incoming = _status("codex:session:incoming")

    plan = _commit((incoming,), state=state, at=40.0)

    retained = plan.next_state.acknowledged_keys
    assert completion_presentation_key(old) not in retained
    assert completion_presentation_key(middle) in retained
    assert completion_presentation_key(newest) in retained
    assert completion_presentation_key(incoming) in retained


def test_commit_never_evicts_a_current_new_target_even_when_older_by_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _status("codex:session:first", updated_at=NOW - timedelta(seconds=2))
    second = _status("codex:session:second", updated_at=NOW - timedelta(seconds=1))
    state = _receipt_state(((first, 100.0), (second, 90.0)))
    monkeypatch.setattr(clear_agents_module, "MAX_COMPLETION_RECEIPTS", 2)
    target = _status("codex:session:target")

    plan = _commit((target,), state=state, at=1.0)

    retained = plan.next_state.acknowledged_keys
    assert completion_presentation_key(target) in retained
    assert completion_presentation_key(first) in retained
    assert completion_presentation_key(second) not in retained


def test_commit_never_evicts_keys_from_latest_live_undo_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected = _status("codex:session:protected", updated_at=NOW - timedelta(seconds=3))
    newest = _status("codex:session:newest", updated_at=NOW - timedelta(seconds=2))
    middle = _status("codex:session:middle", updated_at=NOW - timedelta(seconds=1))
    protected_key = completion_presentation_key(protected)
    assert protected_key is not None
    batch = ClearAgentsBatchReceipt(
        "previous",
        (protected_key,),
        1.0,
        301.0,
        1,
    )
    state = _receipt_state(
        ((protected, 1.0), (newest, 100.0), (middle, 90.0)),
        generation=1,
        latest_batch=batch,
    )
    monkeypatch.setattr(clear_agents_module, "MAX_COMPLETION_RECEIPTS", 3)
    target = _status("codex:session:target")

    plan = _commit((target,), state=state, at=50.0)

    retained = plan.next_state.acknowledged_keys
    assert protected_key in retained
    assert completion_presentation_key(target) in retained
    assert completion_presentation_key(newest) in retained
    assert completion_presentation_key(middle) not in retained


def test_eviction_tie_break_and_persisted_order_are_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = tuple(
        _status(f"codex:session:{name}", updated_at=NOW - timedelta(seconds=index))
        for index, name in enumerate(("a", "b", "c"), start=1)
    )
    state = _receipt_state(tuple((row, 10.0) for row in rows))
    monkeypatch.setattr(clear_agents_module, "MAX_COMPLETION_RECEIPTS", 3)
    target = _status("codex:session:target")

    first = _commit((target,), state=state, at=20.0).next_state.receipts
    second = _commit((target,), state=state, at=20.0).next_state.receipts

    assert first == second
    assert first == tuple(sorted(first, key=lambda receipt: receipt.key))
    retained_old_keys = tuple(
        receipt.key for receipt in first if receipt.key != completion_presentation_key(target)
    )
    assert retained_old_keys == tuple(
        sorted(receipt.key for receipt in state.receipts)
    )[-2:]


def test_commit_refuses_when_protected_targets_and_live_undo_cannot_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _status("codex:session:first", updated_at=NOW - timedelta(seconds=2))
    second = _status("codex:session:second", updated_at=NOW - timedelta(seconds=1))
    first_key = completion_presentation_key(first)
    second_key = completion_presentation_key(second)
    assert first_key is not None and second_key is not None
    batch = ClearAgentsBatchReceipt(
        "previous",
        tuple(sorted((first_key, second_key))),
        1.0,
        301.0,
        1,
    )
    state = _receipt_state(
        ((first, 1.0), (second, 1.0)),
        generation=1,
        latest_batch=batch,
    )
    monkeypatch.setattr(clear_agents_module, "MAX_COMPLETION_RECEIPTS", 2)
    target = _status("codex:session:target")
    preview = project_clear_agents_preview(
        (target,), state=state, now_epoch=50.0
    )

    with pytest.raises(ClearAgentsPlanError) as raised:
        plan_clear_agents_commit(
            preview,
            preview,
            state,
            batch_id="next",
            committed_at_epoch=50.0,
        )

    assert raised.value.reason is ClearAgentsRefusal.INVALID


def test_receipt_hides_only_same_event_newer_event_reappears() -> None:
    original = _status("codex:session:same")
    committed = _commit((original,)).next_state
    same = project_clear_agents_preview(
        (original,), state=committed, now_epoch=NOW_EPOCH
    )
    newer = project_clear_agents_preview(
        (_status("codex:session:same", updated_at=NOW + timedelta(seconds=1)),),
        state=committed,
        now_epoch=NOW_EPOCH + 1,
    )

    assert same.clearable_count == 0
    assert newer.clearable_count == 1
    assert newer.clearable_keys[0] not in committed.acknowledged_keys


def test_acknowledged_row_is_neither_clearable_nor_reclassified_as_protected() -> None:
    done = _status("codex:session:acknowledged")
    committed = _commit((done,)).next_state

    preview = project_clear_agents_preview(
        (done,), state=committed, now_epoch=NOW_EPOCH
    )

    assert preview.clearable_count == 0
    assert preview.protected_counts.total == 0
    assert preview.fence.protected_signatures == ()


def test_type_valid_source_mismatch_stays_protected_instead_of_clearing() -> None:
    mismatch = _status(
        "codex:session:mismatch",
        provider="codex",
        source=_source("claude"),
    )

    preview = project_clear_agents_preview(
        (mismatch,), state=ClearAgentsState(), now_epoch=NOW_EPOCH
    )

    assert preview.clearable_count == 0
    assert preview.protected_counts.unkeyed_local_completions == 1
    assert preview.fence.protected_signatures[0].source_key == _source("claude")


def test_same_agent_and_event_on_another_source_is_untouched() -> None:
    original = _status("codex:session:same", source=_source(source_instance="one"))
    committed = _commit((original,)).next_state
    collision = _status(
        "codex:session:same", source=_source(source_instance="two")
    )

    preview = project_clear_agents_preview(
        (collision,), state=committed, now_epoch=NOW_EPOCH
    )

    assert preview.clearable_count == 1
    assert preview.clearable_keys[0].source_key.source_instance_id == "two"


def test_commit_refuses_changed_targets_protected_lifecycle_and_generation() -> None:
    done = _status("codex:session:done")
    original = project_clear_agents_preview(
        (done,),
        state=ClearAgentsState(),
        now_epoch=NOW_EPOCH,
        protected_statuses=(
            _status("codex:session:live", mode=AgentMode.WORKING),
        ),
    )
    changed_target = project_clear_agents_preview(
        (_status("codex:session:new"),),
        state=ClearAgentsState(),
        now_epoch=NOW_EPOCH,
        protected_statuses=(
            _status("codex:session:live", mode=AgentMode.WORKING),
        ),
    )
    changed_protected = project_clear_agents_preview(
        (done,),
        state=ClearAgentsState(),
        now_epoch=NOW_EPOCH,
        protected_statuses=(
            _status("codex:session:live", mode=AgentMode.WAITING_FOR_INPUT),
        ),
    )
    changed_generation = project_clear_agents_preview(
        (done,),
        state=ClearAgentsState(generation=1),
        now_epoch=NOW_EPOCH,
        protected_statuses=(
            _status("codex:session:live", mode=AgentMode.WORKING),
        ),
    )

    for fresh in (changed_target, changed_protected, changed_generation):
        with pytest.raises(ClearAgentsPlanError) as raised:
            plan_clear_agents_commit(
                original,
                fresh,
                ClearAgentsState(),
                batch_id="batch",
                committed_at_epoch=NOW_EPOCH,
            )
        assert raised.value.reason is ClearAgentsRefusal.STALE_PREVIEW


def test_empty_and_oversized_commits_fail_closed() -> None:
    empty = project_clear_agents_preview(
        (), state=ClearAgentsState(), now_epoch=NOW_EPOCH
    )
    with pytest.raises(ClearAgentsPlanError) as empty_error:
        plan_clear_agents_commit(
            empty,
            empty,
            ClearAgentsState(),
            batch_id="batch",
            committed_at_epoch=NOW_EPOCH,
        )
    assert empty_error.value.reason is ClearAgentsRefusal.EMPTY

    rows = tuple(
        _status(
            f"codex:session:{index}",
            updated_at=NOW + timedelta(seconds=index),
        )
        for index in range(MAX_CLEAR_TARGETS + 1)
    )
    with pytest.raises(ClearAgentsPlanError) as oversized_error:
        project_clear_agents_preview(
            rows, state=ClearAgentsState(), now_epoch=NOW_EPOCH
        )
    assert oversized_error.value.reason is ClearAgentsRefusal.INVALID


def test_explicitly_queued_completed_row_is_protected_from_stale_completion() -> None:
    queued = _status("codex:session:queued-completion")

    preview = project_clear_agents_preview(
        (queued,),
        state=ClearAgentsState(),
        now_epoch=NOW_EPOCH,
        queued_agent_ids=(queued.agent_id,),
    )

    assert preview.clearable_count == 0
    assert preview.protected_counts.queued == 1


def test_undo_removes_only_latest_batch_additions() -> None:
    preserved = _status("codex:session:preserved")
    first = _commit((preserved,), batch_id="first").next_state
    added = _status("codex:session:added", updated_at=NOW + timedelta(seconds=1))
    preview = project_clear_agents_preview(
        (added,), state=first, now_epoch=NOW_EPOCH + 1
    )
    second = plan_clear_agents_commit(
        preview,
        preview,
        first,
        batch_id="second",
        committed_at_epoch=NOW_EPOCH + 1,
    ).next_state

    undo = plan_clear_agents_undo(
        second, batch_id="second", now_epoch=NOW_EPOCH + 300
    )

    assert undo.restored_count == 1
    assert completion_presentation_key(preserved) in undo.next_state.acknowledged_keys
    assert completion_presentation_key(added) not in undo.next_state.acknowledged_keys
    assert undo.next_state.latest_batch is not None
    assert undo.next_state.latest_batch.undone


def test_undo_refuses_expired_wrong_repeated_and_stale_batches() -> None:
    committed = _commit((_status("codex:session:done"),)).next_state

    with pytest.raises(ClearAgentsPlanError) as wrong:
        plan_clear_agents_undo(
            committed, batch_id="wrong", now_epoch=NOW_EPOCH
        )
    assert wrong.value.reason is ClearAgentsRefusal.WRONG_BATCH

    with pytest.raises(ClearAgentsPlanError) as expired:
        plan_clear_agents_undo(
            committed, batch_id="batch-1", now_epoch=NOW_EPOCH + 300.001
        )
    assert expired.value.reason is ClearAgentsRefusal.EXPIRED

    with pytest.raises(ClearAgentsPlanError) as clock_rollback:
        plan_clear_agents_undo(
            committed, batch_id="batch-1", now_epoch=NOW_EPOCH - 1
        )
    assert clock_rollback.value.reason is ClearAgentsRefusal.STALE_UNDO

    undone = plan_clear_agents_undo(
        committed, batch_id="batch-1", now_epoch=NOW_EPOCH
    ).next_state
    with pytest.raises(ClearAgentsPlanError) as repeated:
        plan_clear_agents_undo(undone, batch_id="batch-1", now_epoch=NOW_EPOCH)
    assert repeated.value.reason is ClearAgentsRefusal.REPEATED

    stale = replace(committed, generation=committed.generation + 1)
    with pytest.raises(ClearAgentsPlanError) as stale_error:
        plan_clear_agents_undo(stale, batch_id="batch-1", now_epoch=NOW_EPOCH)
    assert stale_error.value.reason is ClearAgentsRefusal.STALE_UNDO


def test_projection_never_mutates_mailbox_receipts_or_unrelated_state() -> None:
    state = ClearAgentsState()
    before = repr(state)

    project_clear_agents_preview(
        (_status("codex:session:done"),),
        state=state,
        now_epoch=NOW_EPOCH,
    )

    assert repr(state) == before
    assert not hasattr(state, "mailbox_retained_order")
    assert not hasattr(state, "mailbox_seen_completion_ids")
