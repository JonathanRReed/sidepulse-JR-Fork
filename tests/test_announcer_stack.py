from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from sidepulse.announcer_stack import (
    AnnouncerAlertPriority,
    AnnouncerStackAction,
    AnnouncerStackIntent,
    AnnouncerStackState,
    AnnouncerStackVisibility,
    collapse_announcer_stack,
    empty_announcer_stack_state,
    expand_announcer_stack,
    mark_selected_announcer_alert_seen,
    project_announcer_stack,
    reconcile_announcer_stack,
    reduce_announcer_stack_intent,
    select_next_announcer_alert,
    select_previous_announcer_alert,
)
from sidepulse.attention import LifecycleMode, ProjectedAgentRow
from sidepulse.capacity_types import SourceKey
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.operator_state import (
    BootIdentifier,
    ClockSample,
    empty_operator_state,
    reduce_operator_state,
)
from sidepulse.provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderFactBatch,
    ProviderRequestFact,
    ProviderRequestState,
    ProviderWatermark,
    ProviderWorkFact,
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


def _source(value: str = "source:main") -> SourceKey:
    return SourceKey("codex", "hook", value, "agent")


def _work(value: str = "work:one", *, source: SourceKey | None = None) -> WorkKey:
    return WorkKey(source or _source(), WorkIdentifier(value))


def _request(
    value: str = "request:one", *, work: WorkKey | None = None
) -> RequestKey:
    return RequestKey(work or _work(), RequestIdentifier(value))


def _watermark(source: SourceKey) -> ProviderWatermark:
    return ProviderWatermark(
        source,
        WatermarkBasis.PROVIDER_EVENT_ID,
        1_800_000_000,
        EventToken("event:one"),
        None,
        0,
    )


def _canonical(
    requests: tuple[tuple[RequestKey, RequestKind], ...],
):
    work_keys = tuple(dict.fromkeys(request.work_key for request, _ in requests))
    work_facts = tuple(
        ProviderWorkFact(
            key=work,
            lifecycle=WorkLifecycle.WAITING,
            watermark=_watermark(work.source_key),
            safe_label=f"Codex {work.work_id.value}",
            parent_key=None,
            next_actor=NextActor.USER,
        )
        for work in work_keys
    )
    request_facts = tuple(
        ProviderRequestFact(
            key=request,
            state=ProviderRequestState.LIVE,
            request_kind=kind,
            next_actor=NextActor.USER,
            watermark=_watermark(request.work_key.source_key),
        )
        for request, kind in requests
    )
    source = work_keys[0].source_key
    batch = ProviderFactBatch(
        source_key=source,
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        source_health=SourceHealth.HEALTHY,
        source_freshness=SourceFreshness.FRESH,
        observed_at_epoch=1_800_000_000,
        watermark=_watermark(source),
        work_facts=work_facts,
        request_facts=request_facts,
        diagnostics=(),
    )
    return reduce_operator_state(
        empty_operator_state(),
        batch,
        clock=ClockSample(1_800_000_000, 1, BootIdentifier("boot:one")),
    ).state


def _row(
    agent_id: str,
    work: WorkKey | None,
    *,
    request: RequestKey | None = None,
    message: str | None = "Approve access?",
    display_name: str = "Codex Main",
    event_name: str = "PermissionRequest",
    tool_name: str | None = None,
    subagent: bool = False,
) -> ProjectedAgentRow:
    status = AgentStatus(
        provider="codex",
        agent_id=agent_id,
        display_name=display_name,
        mode=AgentMode.WAITING_FOR_INPUT,
        updated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        event_name=event_name,
        session_id="session:one",
        tool_name=tool_name,
        message=message,
        work_key=work,
        request_key=request,
    )
    return ProjectedAgentRow(
        agent_id=agent_id,
        provider="codex",
        display_name=display_name,
        lifecycle_mode=LifecycleMode.WAITING,
        actionable=True,
        is_subagent=subagent,
        updated_at=status.updated_at,
        source_status=status,
        work_key=work,
        request_key=request,
    )


def test_identity_projection_is_canonical_and_content_free_for_each_request() -> None:
    work = _work()
    request = _request(work=work)
    state = _canonical(((request, RequestKind.PERMISSION),))
    row = _row("codex:session:one", work)

    stack = reconcile_announcer_stack(empty_announcer_stack_state(), state, (row,), ())
    plan = project_announcer_stack(stack, state, (row,), ())

    assert plan.visibility is AnnouncerStackVisibility.COLLAPSED
    assert len(plan.alerts) == 1
    assert plan.alerts[0].identity.value == (
        'request:v1:{"adapter_id":"hook","capability_id":"agent",'
        '"provider_id":"codex","request_id":"request:one",'
        '"source_instance_id":"source:main","version":{"major":1,"minor":0},'
        '"work_id":"work:one"}'
    )
    assert plan.alerts[0].question == "Needs your input"
    assert plan.alerts[0].priority is AnnouncerAlertPriority.PERMISSION


def test_plan_generation_tracks_initial_equivalent_and_intent_state_generations() -> None:
    work = _work()
    request = _request(work=work)
    canonical = _canonical(((request, RequestKind.INPUT),))
    row = _row("codex:session:one", work)
    state = reconcile_announcer_stack(empty_announcer_stack_state(), canonical, (row,), ())
    assert project_announcer_stack(state, canonical, (row,), ()).generation == state.generation

    equivalent = reconcile_announcer_stack(state, canonical, (row,), ())
    assert project_announcer_stack(equivalent, canonical, (row,), ()).generation == equivalent.generation

    accepted = reduce_announcer_stack_intent(
        equivalent,
        AnnouncerStackIntent(
            AnnouncerStackAction.EXPAND,
            equivalent.generation,
            equivalent.selected_identity,
        ),
    )
    assert project_announcer_stack(accepted, canonical, (row,), ()).generation == accepted.generation


def test_exact_status_request_key_supplies_bounded_single_line_question() -> None:
    work = _work()
    request = _request(work=work)
    state = _canonical(((request, RequestKind.INPUT),))
    row = _row("codex:session:one", work, request=request, message="wrong")
    status = _row(
        "codex:session:one", work, request=request,
        message="  Run\n tests?  ",
    ).source_status

    stack = reconcile_announcer_stack(empty_announcer_stack_state(), state, (row,), (status,))
    plan = project_announcer_stack(stack, state, (row,), (status,))

    assert plan.alerts[0].question == "Run tests?"
    assert plan.collapsed_text == "Codex Main: Run tests?"


def test_stable_order_selection_and_navigation_wrap() -> None:
    work_a = _work("work:a")
    work_b = _work("work:b")
    request_a = _request("request:a", work=work_a)
    request_b = _request("request:b", work=work_b)
    state_a = _canonical(((request_a, RequestKind.INPUT),))
    row_a = _row("codex:session:a", work_a)
    first = reconcile_announcer_stack(empty_announcer_stack_state(), state_a, (row_a,), ())
    first = replace(first, selected_identity=None)
    state_b = _canonical(((request_a, RequestKind.INPUT), (request_b, RequestKind.PERMISSION)))
    rows = (row_a, _row("codex:session:b", work_b))
    second = reconcile_announcer_stack(first, state_b, rows, ())
    plan = project_announcer_stack(second, state_b, rows, ())

    assert [alert.identity for alert in plan.alerts] == list(second.ordered_identities)
    assert plan.alerts[0].priority is AnnouncerAlertPriority.INPUT
    assert plan.selected_index == 1
    assert select_previous_announcer_alert(second).selected_identity == second.ordered_identities[0]
    assert select_next_announcer_alert(select_previous_announcer_alert(second)).selected_identity == second.ordered_identities[1]


def test_mark_seen_selects_highest_priority_unseen_without_reordering() -> None:
    work_a = _work("work:a")
    work_b = _work("work:b")
    work_c = _work("work:c")
    request_a = _request("request:a", work=work_a)
    request_b = _request("request:b", work=work_b)
    request_c = _request("request:c", work=work_c)
    canonical = _canonical(
        (
            (request_a, RequestKind.INPUT),
            (request_b, RequestKind.REVIEW),
            (request_c, RequestKind.PERMISSION),
        )
    )
    rows = (
        _row("codex:session:a", work_a),
        _row("codex:session:b", work_b),
        _row("codex:session:c", work_c),
    )
    stack = reconcile_announcer_stack(empty_announcer_stack_state(), canonical, rows, ())
    stack = replace(stack, selected_identity=stack.ordered_identities[0])

    seen = mark_selected_announcer_alert_seen(stack)

    assert seen.ordered_identities == stack.ordered_identities
    assert seen.selected_identity == stack.ordered_identities[2]


def test_seen_hides_stack_but_restart_reannounces_and_open_only_advances_generation() -> None:
    work = _work()
    request = _request(work=work)
    state = _canonical(((request, RequestKind.REVIEW),))
    row = _row("codex:session:one", work)
    stack = reconcile_announcer_stack(empty_announcer_stack_state(), state, (row,), ())
    generation = stack.generation
    seen = mark_selected_announcer_alert_seen(stack)

    assert project_announcer_stack(seen, state, (row,), ()).visibility is AnnouncerStackVisibility.HIDDEN
    assert expand_announcer_stack(seen).expanded is True
    assert collapse_announcer_stack(expand_announcer_stack(seen)).expanded is False
    assert seen.ordered_identities == stack.ordered_identities
    opened = reduce_announcer_stack_intent(
        seen,
        AnnouncerStackIntent(AnnouncerStackAction.OPEN, seen.generation, seen.selected_identity),
    )
    assert opened.generation == seen.generation + 1
    assert opened == AnnouncerStackState(
        seen.ordered_identities,
        seen.first_seen_sequences,
        seen.priorities,
        seen.seen_identities,
        seen.selected_identity,
        seen.expanded,
        seen.next_sequence,
        seen.generation + 1,
    )
    assert generation + 1 == seen.generation


def test_stale_intent_is_noop_and_values_are_frozen() -> None:
    state = empty_announcer_stack_state()
    stale = AnnouncerStackIntent(AnnouncerStackAction.EXPAND, 99, None)
    assert reduce_announcer_stack_intent(state, stale) == state
    assert reduce_announcer_stack_intent(state, object()) is state  # type: ignore[arg-type]
    with pytest.raises(FrozenInstanceError):
        state.expanded = True  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kind", "priority"),
    [
        (RequestKind.PERMISSION, AnnouncerAlertPriority.PERMISSION),
        (RequestKind.APPROVAL, AnnouncerAlertPriority.APPROVAL),
        (RequestKind.REVIEW, AnnouncerAlertPriority.REVIEW),
        (RequestKind.INPUT, AnnouncerAlertPriority.INPUT),
        (RequestKind.UNKNOWN, AnnouncerAlertPriority.UNKNOWN),
    ],
)
def test_all_canonical_priorities_are_closed_and_exact(kind, priority) -> None:
    work = _work()
    request = _request(work=work)
    if kind is RequestKind.UNKNOWN:
        row = _row("codex:session:one", None, event_name="UnrecognizedAsk")
        stack = reconcile_announcer_stack(empty_announcer_stack_state(), None, (row,), ())
        assert project_announcer_stack(stack, None, (row,), ()).alerts[0].priority is priority
    else:
        state = _canonical(((request, kind),))
        row = _row("codex:session:one", work)
        stack = reconcile_announcer_stack(empty_announcer_stack_state(), state, (row,), ())
        assert project_announcer_stack(stack, state, (row,), ()).alerts[0].priority is priority


def test_legacy_exact_status_join_refuses_stale_and_mismatched_row_content() -> None:
    work = _work()
    request = _request(work=work)
    row = _row("codex:session:one", work, request=request, message="stale row text")
    exact_status = _row(
        "codex:session:one", work, request=request, message="fresh status text"
    ).source_status
    stale_stack = reconcile_announcer_stack(
        empty_announcer_stack_state(), None, (row,), (exact_status,)
    )
    stale_plan = project_announcer_stack(stale_stack, None, (row,), (exact_status,))
    assert stale_plan.alerts[0].question == "fresh status text"

    other_request = _request("request:other", work=work)
    mismatched_status = _row(
        "codex:session:one", work, request=other_request, message="wrong status text"
    ).source_status
    mismatched_stack = reconcile_announcer_stack(
        empty_announcer_stack_state(), None, (row,), (mismatched_status,)
    )
    mismatched_plan = project_announcer_stack(
        mismatched_stack, None, (row,), (mismatched_status,)
    )
    assert mismatched_plan.alerts[0].question == "Needs your input"


def test_legacy_identity_and_seen_receipt_survive_status_timestamp_refresh() -> None:
    row = _row(
        "codex:session:one",
        _work(),
        event_name="ReviewRequest",
        message="Review the current change?",
    )
    first = reconcile_announcer_stack(empty_announcer_stack_state(), None, (row,), ())
    seen = mark_selected_announcer_alert_seen(first)
    refreshed_at = row.updated_at + timedelta(seconds=30)
    refreshed_status = replace(row.source_status, updated_at=refreshed_at)
    refreshed_row = replace(
        row,
        updated_at=refreshed_at,
        source_status=refreshed_status,
    )

    refreshed = reconcile_announcer_stack(
        seen,
        None,
        (refreshed_row,),
        (refreshed_status,),
    )

    assert refreshed.ordered_identities == first.ordered_identities
    assert refreshed.first_seen_sequences == first.first_seen_sequences
    assert refreshed.seen_identities == seen.seen_identities
    assert (
        project_announcer_stack(
            refreshed,
            None,
            (refreshed_row,),
            (refreshed_status,),
        ).visibility
        is AnnouncerStackVisibility.HIDDEN
    )


def test_one_work_can_project_multiple_live_canonical_requests() -> None:
    work = _work()
    first = _request("request:first", work=work)
    second = _request("request:second", work=work)
    canonical = _canonical(
        ((first, RequestKind.INPUT), (second, RequestKind.PERMISSION))
    )
    row = _row("codex:session:one", work)
    stack = reconcile_announcer_stack(empty_announcer_stack_state(), canonical, (row,), ())
    plan = project_announcer_stack(stack, canonical, (row,), ())
    assert plan.total_actionable_count == 2
    assert len(plan.alerts) == 2
    assert plan.collapsed_text == "Codex Main needs you · 2 asks"


def test_subagents_and_unadmitted_work_do_not_enter_canonical_stack() -> None:
    work = _work()
    request = _request(work=work)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    subagent = _row("codex:agent:worker", work, subagent=True)
    unrelated = _row("codex:session:other", _work("work:other"))
    stack = reconcile_announcer_stack(
        empty_announcer_stack_state(), canonical, (subagent, unrelated), ()
    )
    assert project_announcer_stack(stack, canonical, (subagent, unrelated), ()).alerts == ()


def test_malformed_actionable_row_is_excluded_without_hiding_valid_row() -> None:
    work = _work()
    request = _request(work=work)
    canonical = _canonical(((request, RequestKind.PERMISSION),))
    valid = _row("codex:session:valid", work)
    malformed = replace(valid, display_name=None)  # type: ignore[arg-type]

    stack = reconcile_announcer_stack(
        empty_announcer_stack_state(), canonical, (malformed, valid, object()), ()
    )
    plan = project_announcer_stack(stack, canonical, (malformed, valid, object()), ())

    assert plan.total_actionable_count == 1
    assert plan.alerts[0].agent_id == "codex:session:valid"


@pytest.mark.parametrize(
    ("event_name", "tool_name", "priority"),
    [
        ("PermissionRequest", None, AnnouncerAlertPriority.PERMISSION),
        ("PlanApproval", None, AnnouncerAlertPriority.APPROVAL),
        ("ReviewRequest", None, AnnouncerAlertPriority.REVIEW),
        ("Notification", None, AnnouncerAlertPriority.INPUT),
    ],
)
def test_legacy_fallback_priority_table_is_exact(event_name, tool_name, priority) -> None:
    row = _row(
        f"codex:session:{event_name}",
        None,
        event_name=event_name,
        tool_name=tool_name,
    )
    stack = reconcile_announcer_stack(empty_announcer_stack_state(), None, (row,), ())
    plan = project_announcer_stack(stack, None, (row,), ())
    assert plan.alerts[0].priority is priority


def test_canonical_exact_status_question_is_capped_at_80_characters() -> None:
    work = _work()
    request = _request(work=work)
    canonical = _canonical(((request, RequestKind.INPUT),))
    row = _row("codex:session:one", work, request=request)
    status = _row(
        "codex:session:one",
        work,
        request=request,
        message="Q" * 100,
    ).source_status

    stack = reconcile_announcer_stack(empty_announcer_stack_state(), canonical, (row,), (status,))
    plan = project_announcer_stack(stack, canonical, (row,), (status,))

    assert plan.alerts[0].question == "Q" * 80
    assert len(plan.collapsed_text or "") <= 140


def test_priority_refresh_prunes_and_reappears_without_reordering() -> None:
    work_a = _work("work:a")
    work_b = _work("work:b")
    request_a = _request("request:a", work=work_a)
    request_b = _request("request:b", work=work_b)
    rows = (_row("codex:session:a", work_a), _row("codex:session:b", work_b))
    input_state = _canonical(
        ((request_a, RequestKind.INPUT), (request_b, RequestKind.REVIEW))
    )
    first = reconcile_announcer_stack(empty_announcer_stack_state(), input_state, rows, ())
    permission_state = _canonical(
        ((request_a, RequestKind.PERMISSION), (request_b, RequestKind.REVIEW))
    )
    refreshed = reconcile_announcer_stack(first, permission_state, rows, ())
    assert refreshed.ordered_identities == first.ordered_identities
    assert dict(refreshed.priorities)[refreshed.ordered_identities[0]] is AnnouncerAlertPriority.PERMISSION

    pruned = reconcile_announcer_stack(
        refreshed, permission_state, (rows[0],), ()
    )
    assert pruned.ordered_identities == (refreshed.ordered_identities[0],)
    reappeared = reconcile_announcer_stack(pruned, permission_state, rows, ())
    assert reappeared.ordered_identities == refreshed.ordered_identities
    assert reappeared.ordered_identities[1] in dict(reappeared.priorities)
    assert reappeared.first_seen_sequences[1][1] > pruned.next_sequence - 1


def test_generation_advances_for_equivalent_reconcile_and_each_accepted_intent() -> None:
    state = empty_announcer_stack_state()
    reconciled = reconcile_announcer_stack(state, None, (), ())
    equivalent = reconcile_announcer_stack(reconciled, None, (), ())
    assert equivalent.generation == reconciled.generation + 1
    for action in AnnouncerStackAction:
        intent = AnnouncerStackIntent(action, equivalent.generation, equivalent.selected_identity)
        next_state = reduce_announcer_stack_intent(equivalent, intent)
        assert next_state.generation == equivalent.generation + 1


def test_legacy_projection_keeps_copy_caps_and_counts_above_99_visible() -> None:
    rows = tuple(
        _row(
            f"codex:session:{index}",
            None,
            display_name="N" * 100,
            message="Q" * 200,
            event_name="UnknownAsk",
        )
        for index in range(100)
    )
    stack = reconcile_announcer_stack(empty_announcer_stack_state(), None, rows, ())
    plan = project_announcer_stack(stack, None, rows, ())
    assert plan.total_actionable_count == 100
    assert plan.collapsed_text is not None
    assert "99+ asks" in plan.collapsed_text
    assert len(plan.collapsed_text) <= 140
    assert all(len(alert.source_label) <= 40 for alert in plan.alerts)
    assert all(len(alert.question) <= 80 for alert in plan.alerts)
    assert "\n" not in plan.collapsed_text
