from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime, timezone

import pytest

from sidepulse.attention import LifecycleMode
from sidepulse.capacity_types import SourceKey
from sidepulse.mailbox import (
    AgentMailboxProjection,
    LegacyAgentMailboxProjection,
    MailboxSection,
    MailboxSectionKind,
)
from sidepulse.mailbox import (
    LegacyMailboxRow as MailboxRow,
)
from sidepulse.mailbox import (
    MailboxRow as CanonicalMailboxRow,
)
from sidepulse.mailbox_preferences import (
    LegacyMailboxPreference as MailboxPreference,
)
from sidepulse.mailbox_preferences import (
    MailboxPreference as CanonicalMailboxPreference,
)
from sidepulse.mailbox_preferences import (
    MailboxPreferenceMode,
    apply_mailbox_preferences,
)
from sidepulse.mailbox_preferences import (
    MailboxPreferenceProjection as CanonicalMailboxPreferenceProjection,
)
from sidepulse.provider_facts import (
    NextActor,
    SourceFreshness,
    WorkIdentifier,
    WorkKey,
    WorkLifecycle,
)

NOW = 1_786_536_000.0


def _row(
    agent_id: str,
    lifecycle_mode: LifecycleMode,
    *,
    updated_at: float = NOW - 60.0,
    actionable: bool = False,
    stable_order: int = 0,
    navigation_agent_id: str | None = None,
    display_name: str | None = None,
    worker_count: int = 0,
) -> MailboxRow:
    return MailboxRow(
        agent_id=agent_id,
        provider=agent_id.partition(":")[0],
        display_name=display_name or agent_id.rsplit(":", 1)[-1],
        lifecycle_mode=lifecycle_mode,
        activity_label=None,
        actionable=actionable,
        navigation_agent_id=navigation_agent_id or agent_id,
        worker_count=worker_count,
        updated_at=datetime.fromtimestamp(updated_at, tz=timezone.utc),
        stable_order=stable_order,
    )


def _projection(
    *,
    needs_you: tuple[MailboxRow, ...] = (),
    in_progress: tuple[MailboxRow, ...] = (),
    ready: tuple[MailboxRow, ...] = (),
    recent: tuple[MailboxRow, ...] = (),
    overflows: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> AgentMailboxProjection:
    rows_by_kind = (needs_you, in_progress, ready, recent)
    kinds = (
        MailboxSectionKind.NEEDS_YOU,
        MailboxSectionKind.IN_PROGRESS,
        MailboxSectionKind.READY_FOR_REVIEW,
        MailboxSectionKind.RECENT,
    )
    sections = tuple(
        MailboxSection(kind=kind, rows=rows, overflow_count=overflow)
        for kind, rows, overflow in zip(kinds, rows_by_kind, overflows, strict=True)
    )
    return LegacyAgentMailboxProjection(
        sections=sections,
        active_count=len(needs_you) + len(in_progress) + overflows[0] + overflows[1],
        needs_you_count=len(needs_you) + overflows[0],
        ready_count=len(ready) + overflows[2],
        retained_order=tuple(
            (row.agent_id, row.stable_order)
            for rows in rows_by_kind
            for row in rows
            if row.agent_id
        ),
    )


def _rows(result, kind: MailboxSectionKind) -> tuple[MailboxRow, ...]:
    return next(section.rows for section in result.projection.sections if section.kind == kind)


@pytest.mark.parametrize(
    ("mode", "pin_order"),
    (
        (MailboxPreferenceMode.DEFAULT, None),
        (MailboxPreferenceMode.WATCHED, None),
        (MailboxPreferenceMode.PINNED, 4),
    ),
)
def test_snooze_never_hides_actionable_rows(
    mode: MailboxPreferenceMode,
    pin_order: int | None,
) -> None:
    ask = _row(
        "codex:session:ask",
        LifecycleMode.WAITING,
        updated_at=NOW,
        actionable=True,
    )
    preference = MailboxPreference(
        agent_id=ask.agent_id,
        mode=mode,
        pin_order=pin_order,
        snoozed_at=NOW,
        snoozed_until=NOW + 3_600.0,
    )

    result = apply_mailbox_preferences(_projection(needs_you=(ask,)), (preference,), now=NOW)

    assert _rows(result, MailboxSectionKind.NEEDS_YOU) == (ask,)
    assert result.next_wake_epoch is None
    assert result.woke_agent_ids == (ask.agent_id,)


@pytest.mark.parametrize(
    ("section", "mode"),
    (
        (MailboxSectionKind.IN_PROGRESS, LifecycleMode.ACTIVE),
        (MailboxSectionKind.RECENT, LifecycleMode.IDLE),
    ),
)
def test_running_and_quiet_rows_stay_snoozed_to_exact_deadline(
    section: MailboxSectionKind,
    mode: LifecycleMode,
) -> None:
    row = _row(f"claude:session:{mode.value}", mode)
    preference = MailboxPreference(
        agent_id=row.agent_id,
        snoozed_at=NOW - 60.0,
        snoozed_until=NOW + 3_600.25,
    )
    projection = (
        _projection(in_progress=(row,))
        if section == MailboxSectionKind.IN_PROGRESS
        else _projection(recent=(row,))
    )

    result = apply_mailbox_preferences(projection, (preference,), now=NOW)

    assert _rows(result, section) == ()
    assert result.next_wake_epoch == NOW + 3_600.25
    assert result.woke_agent_ids == ()


@pytest.mark.parametrize(
    ("lifecycle_mode", "section"),
    (
        (LifecycleMode.FAILED_VISIBLE, MailboxSectionKind.READY_FOR_REVIEW),
        (LifecycleMode.COMPLETED_RECENTLY, MailboxSectionKind.READY_FOR_REVIEW),
    ),
)
def test_new_failure_and_completion_after_snooze_wake_early(
    lifecycle_mode: LifecycleMode,
    section: MailboxSectionKind,
) -> None:
    row = _row(
        f"devin:session:{lifecycle_mode.value}",
        lifecycle_mode,
        updated_at=NOW - 30.0,
    )
    preference = MailboxPreference(
        agent_id=row.agent_id,
        snoozed_at=NOW - 60.0,
        snoozed_until=NOW + 3_600.0,
    )

    result = apply_mailbox_preferences(_projection(ready=(row,)), (preference,), now=NOW)

    assert _rows(result, section) == (row,)
    assert result.next_wake_epoch is None
    assert result.woke_agent_ids == (row.agent_id,)


@pytest.mark.parametrize(
    "lifecycle_mode",
    (LifecycleMode.FAILED_VISIBLE, LifecycleMode.COMPLETED_RECENTLY),
)
def test_preexisting_failure_and_completion_stay_snoozed(
    lifecycle_mode: LifecycleMode,
) -> None:
    row = _row(
        f"codex:session:old-{lifecycle_mode.value}",
        lifecycle_mode,
        updated_at=NOW - 60.0,
    )
    preference = MailboxPreference(
        agent_id=row.agent_id,
        snoozed_at=NOW - 60.0,
        snoozed_until=NOW + 600.0,
    )

    result = apply_mailbox_preferences(_projection(ready=(row,)), (preference,), now=NOW)

    assert _rows(result, MailboxSectionKind.READY_FOR_REVIEW) == ()
    assert result.next_wake_epoch == NOW + 600.0
    assert result.woke_agent_ids == ()


def test_new_ask_at_equal_snooze_timestamp_fails_visible() -> None:
    ask = _row(
        "codex:session:equal-ask",
        LifecycleMode.WAITING,
        updated_at=NOW - 60.0,
        actionable=True,
    )
    preference = MailboxPreference(
        agent_id=ask.agent_id,
        snoozed_at=NOW - 60.0,
        snoozed_until=NOW + 600.0,
    )

    result = apply_mailbox_preferences(_projection(needs_you=(ask,)), (preference,), now=NOW)

    assert _rows(result, MailboxSectionKind.NEEDS_YOU) == (ask,)
    assert result.woke_agent_ids == (ask.agent_id,)


def test_unchanged_working_activity_does_not_wake_early() -> None:
    working = _row("codex:session:working", LifecycleMode.ACTIVE, updated_at=NOW)
    preference = MailboxPreference(
        agent_id=working.agent_id,
        snoozed_at=NOW - 60.0,
        snoozed_until=NOW + 600.0,
    )

    result = apply_mailbox_preferences(
        _projection(in_progress=(working,)),
        (preference,),
        now=NOW,
    )

    assert _rows(result, MailboxSectionKind.IN_PROGRESS) == ()
    assert result.next_wake_epoch == NOW + 600.0
    assert result.woke_agent_ids == ()


@pytest.mark.parametrize(
    ("snoozed_at", "snoozed_until"),
    (
        (NOW - 60.0, NOW - 1.0),
        (NOW + 10.0, NOW + 5.0),
        (float("nan"), NOW + 60.0),
        (NOW - 60.0, float("inf")),
        (NOW - 60.0, NOW + 366.0 * 86_400.0 + 1.0),
        (None, NOW + 60.0),
        (NOW - 60.0, None),
    ),
)
def test_invalid_expired_reversed_nonfinite_and_far_future_snoozes_fail_visible(
    snoozed_at: float | None,
    snoozed_until: float | None,
) -> None:
    row = _row("codex:session:malformed", LifecycleMode.IDLE)
    preference = MailboxPreference(
        agent_id=row.agent_id,
        snoozed_at=snoozed_at,
        snoozed_until=snoozed_until,
        last_visited_at=NOW,
    )

    result = apply_mailbox_preferences(_projection(recent=(row,)), (preference,), now=NOW)

    assert _rows(result, MailboxSectionKind.RECENT) == (row,)
    assert result.next_wake_epoch is None
    assert result.woke_agent_ids == ()
    assert len(result.retained_preferences) == 1
    retained = result.retained_preferences[0]
    assert retained.snoozed_at is None
    assert retained.snoozed_until is None


def test_wall_clock_rollback_keeps_still_valid_future_epoch_without_extending_it() -> None:
    row = _row("codex:session:rollback", LifecycleMode.ACTIVE)
    preference = MailboxPreference(
        agent_id=row.agent_id,
        snoozed_at=NOW + 60.0,
        snoozed_until=NOW + 120.0,
    )

    result = apply_mailbox_preferences(
        _projection(in_progress=(row,)),
        (preference,),
        now=NOW,
    )

    assert _rows(result, MailboxSectionKind.IN_PROGRESS) == ()
    assert result.next_wake_epoch == NOW + 120.0


def test_preferences_reorder_only_inside_authoritative_shelves() -> None:
    ask = _row(
        "codex:session:ask",
        LifecycleMode.WAITING,
        actionable=True,
        stable_order=9,
    )
    rows = (
        _row("codex:session:default-a", LifecycleMode.ACTIVE, stable_order=0),
        _row("codex:session:watch-a", LifecycleMode.ACTIVE, stable_order=1),
        _row("codex:session:pin-five", LifecycleMode.ACTIVE, stable_order=2),
        _row("codex:session:pin-one", LifecycleMode.ACTIVE, stable_order=3),
        _row("codex:session:watch-b", LifecycleMode.ACTIVE, stable_order=4),
        _row("codex:session:default-b", LifecycleMode.ACTIVE, stable_order=5),
    )
    preferences = (
        MailboxPreference(rows[1].agent_id, MailboxPreferenceMode.WATCHED),
        MailboxPreference(rows[2].agent_id, MailboxPreferenceMode.PINNED, pin_order=5),
        MailboxPreference(rows[3].agent_id, MailboxPreferenceMode.PINNED, pin_order=1),
        MailboxPreference(rows[4].agent_id, MailboxPreferenceMode.WATCHED),
        MailboxPreference(ask.agent_id, MailboxPreferenceMode.DEFAULT),
    )

    result = apply_mailbox_preferences(
        _projection(needs_you=(ask,), in_progress=rows),
        preferences,
        now=NOW,
    )

    assert tuple(section.kind for section in result.projection.sections) == (
        MailboxSectionKind.NEEDS_YOU,
        MailboxSectionKind.IN_PROGRESS,
        MailboxSectionKind.READY_FOR_REVIEW,
        MailboxSectionKind.RECENT,
    )
    assert _rows(result, MailboxSectionKind.NEEDS_YOU) == (ask,)
    assert tuple(row.agent_id for row in _rows(result, MailboxSectionKind.IN_PROGRESS)) == (
        "codex:session:pin-one",
        "codex:session:pin-five",
        "codex:session:watch-a",
        "codex:session:watch-b",
        "codex:session:default-a",
        "codex:session:default-b",
    )


def test_duplicate_preferences_choose_newest_then_safer_visible_and_lower_pin() -> None:
    rows = tuple(
        _row(f"codex:session:{name}", LifecycleMode.IDLE, stable_order=index)
        for index, name in enumerate(("newest", "safe", "pin"))
    )
    preferences = (
        MailboxPreference(
            rows[0].agent_id,
            snoozed_at=NOW - 200.0,
            snoozed_until=NOW + 600.0,
        ),
        MailboxPreference(
            rows[0].agent_id,
            mode=MailboxPreferenceMode.WATCHED,
            last_visited_at=NOW - 100.0,
        ),
        MailboxPreference(
            rows[1].agent_id,
            snoozed_at=NOW - 100.0,
            snoozed_until=NOW + 600.0,
        ),
        MailboxPreference(
            rows[1].agent_id,
            mode=MailboxPreferenceMode.WATCHED,
            last_visited_at=NOW - 100.0,
        ),
        MailboxPreference(
            rows[2].agent_id,
            mode=MailboxPreferenceMode.PINNED,
            pin_order=8,
            last_visited_at=NOW - 100.0,
        ),
        MailboxPreference(
            rows[2].agent_id,
            mode=MailboxPreferenceMode.PINNED,
            pin_order=2,
            last_visited_at=NOW - 100.0,
        ),
    )

    result = apply_mailbox_preferences(_projection(recent=rows), preferences, now=NOW)
    retained = {preference.agent_id: preference for preference in result.retained_preferences}

    assert _rows(result, MailboxSectionKind.RECENT) == (rows[2], rows[0], rows[1])
    assert retained[rows[0].agent_id].mode == MailboxPreferenceMode.WATCHED
    assert retained[rows[1].agent_id].mode == MailboxPreferenceMode.WATCHED
    assert retained[rows[2].agent_id].pin_order == 2


def test_duplicate_projection_rows_cannot_resurrect_stale_attention_or_terminal_state() -> None:
    current = _row("codex:session:reused", LifecycleMode.ACTIVE, updated_at=NOW)
    stale_ask = _row(
        current.agent_id,
        LifecycleMode.WAITING,
        updated_at=NOW - 60.0,
        actionable=True,
    )
    stale_failure = _row(
        current.agent_id,
        LifecycleMode.FAILED_VISIBLE,
        updated_at=NOW - 120.0,
    )
    projection = _projection(
        needs_you=(stale_ask,),
        in_progress=(current,),
        ready=(stale_failure,),
    )

    result = apply_mailbox_preferences(projection, (), now=NOW)

    assert _rows(result, MailboxSectionKind.NEEDS_YOU) == ()
    assert _rows(result, MailboxSectionKind.IN_PROGRESS) == (current,)
    assert _rows(result, MailboxSectionKind.READY_FOR_REVIEW) == ()
    assert result.projection.active_count == 1
    assert result.projection.needs_you_count == 0
    assert result.projection.ready_count == 0


def test_woke_marker_survives_rebuild_until_trigger_has_been_visited() -> None:
    row = _row("claude:session:woke", LifecycleMode.IDLE)
    preference = MailboxPreference(
        agent_id=row.agent_id,
        mode=MailboxPreferenceMode.WATCHED,
        snoozed_at=NOW - 3_600.0,
        snoozed_until=NOW - 60.0,
    )
    projection = _projection(recent=(row,))

    first = apply_mailbox_preferences(projection, (preference,), now=NOW)
    rebuilt = apply_mailbox_preferences(
        projection,
        first.retained_preferences,
        now=NOW + 30.0,
    )
    visited = apply_mailbox_preferences(
        projection,
        (replace(rebuilt.retained_preferences[0], last_visited_at=NOW - 60.0),),
        now=NOW + 30.0,
    )

    assert first.woke_agent_ids == (row.agent_id,)
    assert rebuilt.woke_agent_ids == (row.agent_id,)
    assert visited.woke_agent_ids == ()
    assert visited.retained_preferences == (
        MailboxPreference(
            agent_id=row.agent_id,
            mode=MailboxPreferenceMode.WATCHED,
            last_visited_at=NOW - 60.0,
        ),
    )


def test_earliest_wake_is_taken_only_from_rows_actually_hidden() -> None:
    codex = _row("codex:session:hidden", LifecycleMode.ACTIVE, stable_order=0)
    claude = _row("claude:session:hidden", LifecycleMode.IDLE, stable_order=1)
    ask = _row(
        "devin:session:ask",
        LifecycleMode.WAITING,
        actionable=True,
        stable_order=2,
    )
    preferences = (
        MailboxPreference(codex.agent_id, snoozed_at=NOW - 60.0, snoozed_until=NOW + 500.0),
        MailboxPreference(claude.agent_id, snoozed_at=NOW - 60.0, snoozed_until=NOW + 300.0),
        MailboxPreference(ask.agent_id, snoozed_at=NOW - 60.0, snoozed_until=NOW + 100.0),
    )

    result = apply_mailbox_preferences(
        _projection(needs_you=(ask,), in_progress=(codex,), recent=(claude,)),
        preferences,
        now=NOW,
    )

    assert result.next_wake_epoch == NOW + 300.0


def test_preferences_are_deduped_unknowns_removed_and_retention_is_capped_at_one_hundred() -> None:
    rows = tuple(
        _row(
            f"codex:session:{index:03d}",
            LifecycleMode.WAITING if index == 109 else LifecycleMode.IDLE,
            actionable=index == 109,
            stable_order=index,
        )
        for index in range(110)
    )
    preferences = (
        *(
            MailboxPreference(
                row.agent_id,
                mode=(
                    MailboxPreferenceMode.WATCHED
                    if index < 100
                    else MailboxPreferenceMode.DEFAULT
                ),
                last_visited_at=NOW - float(index),
            )
            for index, row in enumerate(rows)
        ),
        MailboxPreference(""),
        MailboxPreference("unknown:session:not-current", MailboxPreferenceMode.PINNED, 0),
        MailboxPreference(rows[0].agent_id, last_visited_at=NOW - 10_000.0),
    )

    result = apply_mailbox_preferences(_projection(recent=rows), preferences, now=NOW)
    retained_ids = {preference.agent_id for preference in result.retained_preferences}

    assert len(result.retained_preferences) == 100
    assert rows[109].agent_id in retained_ids
    assert rows[0].agent_id in retained_ids
    assert "" not in retained_ids
    assert "unknown:session:not-current" not in retained_ids


def test_retained_preferences_follow_the_literal_priority_contract() -> None:
    actionable = _row(
        "codex:session:actionable",
        LifecycleMode.WAITING,
        actionable=True,
        stable_order=0,
    )
    watched = _row("codex:session:watched", LifecycleMode.IDLE, stable_order=4)
    recent_visit = _row("codex:session:recent-visit", LifecycleMode.IDLE, stable_order=1)
    stable_first = _row("codex:session:stable-first", LifecycleMode.IDLE, stable_order=2)
    stable_second = _row("codex:session:stable-second", LifecycleMode.IDLE, stable_order=3)
    projection = _projection(
        needs_you=(actionable,),
        recent=(watched, recent_visit, stable_first, stable_second),
    )
    overflow_id = "codex:session:overflow-snooze"
    projection = replace(
        projection,
        retained_order=(*projection.retained_order, (overflow_id, 5)),
    )
    preferences = (
        MailboxPreference(actionable.agent_id, last_visited_at=NOW - 1_000.0),
        MailboxPreference(watched.agent_id, mode=MailboxPreferenceMode.WATCHED),
        MailboxPreference(
            overflow_id,
            snoozed_at=NOW - 60.0,
            snoozed_until=NOW + 600.0,
        ),
        MailboxPreference(recent_visit.agent_id, last_visited_at=NOW - 10.0),
        MailboxPreference(stable_first.agent_id, last_visited_at=NOW - 20.0),
        MailboxPreference(stable_second.agent_id, last_visited_at=NOW - 20.0),
    )

    result = apply_mailbox_preferences(projection, preferences, now=NOW)

    assert tuple(item.agent_id for item in result.retained_preferences) == (
        actionable.agent_id,
        watched.agent_id,
        overflow_id,
        recent_visit.agent_id,
        stable_first.agent_id,
        stable_second.agent_id,
    )


def test_woke_ids_use_current_stable_mailbox_order() -> None:
    first = _row("codex:session:woke-first", LifecycleMode.IDLE, stable_order=0)
    second = _row("codex:session:woke-second", LifecycleMode.IDLE, stable_order=1)
    projection = replace(
        _projection(recent=(first, second)),
        retained_order=((second.agent_id, 0), (first.agent_id, 1)),
    )
    preferences = (
        MailboxPreference(
            first.agent_id,
            snoozed_at=NOW - 600.0,
            snoozed_until=NOW - 60.0,
        ),
        MailboxPreference(
            second.agent_id,
            snoozed_at=NOW - 600.0,
            snoozed_until=NOW - 60.0,
        ),
    )

    result = apply_mailbox_preferences(projection, preferences, now=NOW)

    assert result.woke_agent_ids == (first.agent_id, second.agent_id)


def test_preference_output_cannot_copy_raw_payload_or_infer_identity_from_display_text() -> None:
    secret = "Bearer-sk-private-/Users/jonathan/Secret"
    row = _row(
        "codex:session:safe-id",
        LifecycleMode.IDLE,
        display_name=secret,
    )
    preferences = (
        MailboxPreference(secret, MailboxPreferenceMode.PINNED, pin_order=0),
        MailboxPreference("", MailboxPreferenceMode.WATCHED, last_visited_at=NOW),
    )

    result = apply_mailbox_preferences(_projection(recent=(row,)), preferences, now=NOW)

    assert result.retained_preferences == ()
    assert result.woke_agent_ids == ()
    assert tuple(field.name for field in fields(MailboxPreference)) == (
        "agent_id",
        "mode",
        "pin_order",
        "snoozed_at",
        "snoozed_until",
        "last_visited_at",
    )


def _work_key(
    work_id: str,
    *,
    source_instance: str = "local:01",
) -> WorkKey:
    source = SourceKey("codex", "hooks", source_instance, "live_agent_events")
    return WorkKey(source, WorkIdentifier(work_id))


def _work_row(
    key: WorkKey,
    lifecycle: WorkLifecycle,
    *,
    updated_at: float = NOW - 60.0,
    actionable: bool = False,
    stable_order: int = 0,
) -> CanonicalMailboxRow:
    return CanonicalMailboxRow(
        work_key=key,
        request_key=None,
        safe_label=f"Codex {stable_order + 1}",
        lifecycle=lifecycle,
        next_actor=NextActor.USER if actionable else NextActor.PROVIDER,
        source_freshness=SourceFreshness.FRESH,
        request_keys=(),
        actionable=actionable,
        worker_count=0,
        updated_at_epoch=updated_at,
        stable_order=stable_order,
        timing_uncertain=False,
    )


def _work_projection(
    *,
    needs_you: tuple[CanonicalMailboxRow, ...] = (),
    in_progress: tuple[CanonicalMailboxRow, ...] = (),
    ready: tuple[CanonicalMailboxRow, ...] = (),
    recent: tuple[CanonicalMailboxRow, ...] = (),
) -> AgentMailboxProjection:
    rows_by_kind = (needs_you, in_progress, ready, recent)
    kinds = (
        MailboxSectionKind.NEEDS_YOU,
        MailboxSectionKind.IN_PROGRESS,
        MailboxSectionKind.READY_FOR_REVIEW,
        MailboxSectionKind.RECENT,
    )
    return AgentMailboxProjection(
        sections=tuple(
            MailboxSection(kind, rows, 0)
            for kind, rows in zip(kinds, rows_by_kind, strict=True)
        ),
        active_count=len(needs_you) + len(in_progress),
        needs_you_count=len(needs_you),
        ready_count=len(ready),
        retained_order=tuple(
            (row.work_key, row.stable_order)
            for rows in rows_by_kind
            for row in rows
        ),
    )


def _canonical_rows(
    result: CanonicalMailboxPreferenceProjection,
    kind: MailboxSectionKind,
) -> tuple[CanonicalMailboxRow, ...]:
    return next(section.rows for section in result.projection.sections if section.kind is kind)


def test_work_key_preferences_do_not_collide_across_source_instances() -> None:
    first = _work_row(
        _work_key("same", source_instance="local:01"),
        WorkLifecycle.ACTIVE,
        stable_order=0,
    )
    second = _work_row(
        _work_key("same", source_instance="local:02"),
        WorkLifecycle.ACTIVE,
        stable_order=1,
    )
    result = apply_mailbox_preferences(
        _work_projection(in_progress=(first, second)),
        (
            CanonicalMailboxPreference(
                second.work_key,
                MailboxPreferenceMode.PINNED,
                pin_order=0,
            ),
            CanonicalMailboxPreference(
                first.work_key,
                MailboxPreferenceMode.WATCHED,
            ),
        ),
        now=NOW,
    )

    assert isinstance(result, CanonicalMailboxPreferenceProjection)
    assert tuple(row.work_key for row in _canonical_rows(result, MailboxSectionKind.IN_PROGRESS)) == (
        second.work_key,
        first.work_key,
    )
    assert {item.work_key for item in result.retained_preferences} == {
        first.work_key,
        second.work_key,
    }


def test_empty_canonical_projection_returns_work_key_preference_projection() -> None:
    result = apply_mailbox_preferences(_work_projection(), (), now=NOW)

    assert type(result) is CanonicalMailboxPreferenceProjection
    assert result.projection == _work_projection()
    assert result.retained_preferences == ()
    assert result.next_wake_epoch is None
    assert result.woke_work_keys == ()


def test_work_key_snooze_never_hides_actionable_and_wakes_exact_family() -> None:
    row = _work_row(
        _work_key("ask"),
        WorkLifecycle.WAITING,
        updated_at=NOW,
        actionable=True,
    )
    preference = CanonicalMailboxPreference(
        row.work_key,
        snoozed_at=NOW,
        snoozed_until=NOW + 3_600.0,
    )

    result = apply_mailbox_preferences(
        _work_projection(needs_you=(row,)),
        (preference,),
        now=NOW,
    )

    assert isinstance(result, CanonicalMailboxPreferenceProjection)
    assert _canonical_rows(result, MailboxSectionKind.NEEDS_YOU) == (row,)
    assert result.next_wake_epoch is None
    assert result.woke_work_keys == (row.work_key,)


def test_work_key_snooze_hides_quiet_until_one_common_exact_deadline() -> None:
    first = _work_row(_work_key("first"), WorkLifecycle.ACTIVE, stable_order=0)
    second = _work_row(_work_key("second"), WorkLifecycle.IDLE, stable_order=1)
    result = apply_mailbox_preferences(
        _work_projection(in_progress=(first,), recent=(second,)),
        (
            CanonicalMailboxPreference(
                first.work_key,
                snoozed_at=NOW - 60.0,
                snoozed_until=NOW + 600.0,
            ),
            CanonicalMailboxPreference(
                second.work_key,
                snoozed_at=NOW - 60.0,
                snoozed_until=NOW + 300.0,
            ),
        ),
        now=NOW,
    )

    assert isinstance(result, CanonicalMailboxPreferenceProjection)
    assert _canonical_rows(result, MailboxSectionKind.IN_PROGRESS) == ()
    assert _canonical_rows(result, MailboxSectionKind.RECENT) == ()
    assert result.next_wake_epoch == NOW + 300.0
    assert result.woke_work_keys == ()


def test_work_key_terminal_edge_wakes_early_and_woke_persists_until_visit() -> None:
    row = _work_row(
        _work_key("finished"),
        WorkLifecycle.COMPLETED,
        updated_at=NOW - 30.0,
    )
    preference = CanonicalMailboxPreference(
        row.work_key,
        MailboxPreferenceMode.WATCHED,
        snoozed_at=NOW - 60.0,
        snoozed_until=NOW + 600.0,
    )
    projection = _work_projection(ready=(row,))

    first = apply_mailbox_preferences(projection, (preference,), now=NOW)
    rebuilt = apply_mailbox_preferences(
        projection,
        first.retained_preferences,
        now=NOW + 10.0,
    )
    visited_preference = replace(
        rebuilt.retained_preferences[0],
        last_visited_at=NOW - 30.0,
    )
    visited = apply_mailbox_preferences(
        projection,
        (visited_preference,),
        now=NOW + 10.0,
    )

    assert isinstance(first, CanonicalMailboxPreferenceProjection)
    assert isinstance(rebuilt, CanonicalMailboxPreferenceProjection)
    assert isinstance(visited, CanonicalMailboxPreferenceProjection)
    assert first.woke_work_keys == (row.work_key,)
    assert rebuilt.woke_work_keys == (row.work_key,)
    assert visited.woke_work_keys == ()
    assert visited.retained_preferences == (
        CanonicalMailboxPreference(
            row.work_key,
            MailboxPreferenceMode.WATCHED,
            last_visited_at=NOW - 30.0,
        ),
    )


def test_work_key_preference_retention_remains_capped_at_one_hundred() -> None:
    rows = tuple(
        _work_row(
            _work_key(f"work:{index:03d}"),
            WorkLifecycle.WAITING if index == 109 else WorkLifecycle.IDLE,
            actionable=index == 109,
            stable_order=index,
        )
        for index in range(110)
    )
    preferences = tuple(
        CanonicalMailboxPreference(
            row.work_key,
            MailboxPreferenceMode.WATCHED,
            last_visited_at=NOW - float(index),
        )
        for index, row in enumerate(rows)
    )

    result = apply_mailbox_preferences(
        _work_projection(needs_you=(rows[-1],), recent=rows[:-1]),
        preferences,
        now=NOW,
    )

    assert isinstance(result, CanonicalMailboxPreferenceProjection)
    assert len(result.retained_preferences) == 100
    assert rows[-1].work_key in {item.work_key for item in result.retained_preferences}


def test_canonical_preference_dtos_expose_only_source_scoped_fields() -> None:
    assert tuple(field.name for field in fields(CanonicalMailboxPreference)) == (
        "work_key",
        "mode",
        "pin_order",
        "snoozed_at",
        "snoozed_until",
        "last_visited_at",
    )
    assert tuple(field.name for field in fields(CanonicalMailboxPreferenceProjection)) == (
        "projection",
        "retained_preferences",
        "next_wake_epoch",
        "woke_work_keys",
    )
