from __future__ import annotations

import re
from dataclasses import replace

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.delivery_ledger import (
    DeliveryChannel,
    DeliveryDiagnostic,
    DeliveryDisposition,
    DeliveryKey,
    DeliveryLedger,
    DeliveryReceipt,
    DeliverySummaryKey,
    record_delivery,
)
from sidepulse.interruption_policy import (
    ActionTokenBinding,
    ChannelDeliveryPlan,
    FiniteCueBatch,
    GenericNotificationCopy,
    InterruptionPlan,
    InterruptionPolicyValidationError,
    InterruptionRoute,
    InterruptionState,
    QuietExitRoute,
    QuietReason,
    QuietState,
    QuietSummary,
    action_token_metadata,
    generic_notification_copy,
    issue_action_token,
    plan_finite_cues,
    plan_interruptions,
    resolve_action_token,
)
from sidepulse.local_triage import LocalAcknowledgement, LocalTriageState
from sidepulse.operator_state import (
    AcknowledgementEligibility,
    CanonicalOperatorEvent,
    CanonicalRequestTruth,
    InterruptionClass,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
    classify_operator_event,
)
from sidepulse.presentation_policy import GlanceSemantic
from sidepulse.provider_facts import (
    EventToken,
    NextActor,
    ProviderWatermark,
    RequestIdentifier,
    RequestKey,
    RequestKind,
    SourceFreshness,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
)
from sidepulse.signal_coordinator import FiniteCueCoordinator

NOW = 1_786_536_000.0
EMPTY_SUMMARY = QuietSummary(0, 0, 0, None)
NO_QUIET = QuietState(False, frozenset(), None)
EMPTY_STATE = InterruptionState(EMPTY_SUMMARY, None)

SUPPRESSIBLE_CHANNELS = frozenset(
    {
        DeliveryChannel.STATUS_ITEM_CUE,
        DeliveryChannel.SCREEN_BAR_CUE,
        DeliveryChannel.HARDWARE_CUE,
        DeliveryChannel.SYSTEM_NOTIFICATION,
        DeliveryChannel.SOUND,
    }
)
ALL_CHANNELS = frozenset(DeliveryChannel)


def _source(
    provider: str = "codex",
    suffix: str = "01",
    *,
    source_instance: str | None = None,
) -> SourceKey:
    return SourceKey(
        provider,
        "hooks",
        source_instance or f"local:{suffix}",
        "live_agent_events",
    )


def _watermark(
    source: SourceKey,
    suffix: str,
    *,
    occurred_at: float = NOW - 10.0,
) -> ProviderWatermark:
    return ProviderWatermark(
        source_key=source,
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        occurred_at_epoch=occurred_at,
        event_token=EventToken(f"event:{suffix}"),
        sequence=None,
        tie_break_rank=10,
    )


def _work_event(
    kind: TransitionKind,
    *,
    interruption_class: InterruptionClass | None = None,
    provider: str = "codex",
    suffix: str = "01",
    freshness: SourceFreshness = SourceFreshness.FRESH,
    work_id: str | None = None,
    source_instance: str | None = None,
) -> CanonicalOperatorEvent:
    source = _source(provider, suffix, source_instance=source_instance)
    subject = WorkKey(source, WorkIdentifier(work_id or f"work:{suffix}"))
    watermark = _watermark(source, suffix)
    key = SemanticEventKey(subject, kind, watermark)
    return CanonicalOperatorEvent(
        key=key,
        subject_key=subject,
        kind=kind,
        interruption_class=(interruption_class if interruption_class is not None else classify_operator_event(kind)),
        occurred_at_epoch=watermark.occurred_at_epoch,
        source_freshness=freshness,
    )


def _request_event(
    *,
    phase: RequestPhase = RequestPhase.LIVE_UNACKNOWLEDGED,
    freshness: SourceFreshness = SourceFreshness.FRESH,
    request_kind: RequestKind = RequestKind.PERMISSION,
    elapsed: float = 10.0,
    suffix: str = "01",
    provider: str = "codex",
    interruption_class: InterruptionClass = InterruptionClass.ACTION_REQUIRED,
) -> tuple[CanonicalOperatorEvent, CanonicalRequestTruth]:
    source = _source(provider, suffix)
    work_key = WorkKey(source, WorkIdentifier(f"work:{suffix}"))
    request_key = RequestKey(work_key, RequestIdentifier(f"request:{suffix}"))
    watermark = _watermark(source, suffix)
    event_key = SemanticEventKey(
        request_key,
        TransitionKind.REQUEST_OPENED,
        watermark,
    )
    event = CanonicalOperatorEvent(
        key=event_key,
        subject_key=request_key,
        kind=TransitionKind.REQUEST_OPENED,
        interruption_class=interruption_class,
        occurred_at_epoch=watermark.occurred_at_epoch,
        source_freshness=freshness,
    )
    eligibility = {
        RequestPhase.LIVE_UNACKNOWLEDGED: AcknowledgementEligibility.ELIGIBLE,
        RequestPhase.LIVE_ACKNOWLEDGED: AcknowledgementEligibility.ALREADY_ACKNOWLEDGED,
        RequestPhase.STALE_HOLD: AcknowledgementEligibility.STALE_HOLD,
        RequestPhase.RESOLVED: AcknowledgementEligibility.RESOLVED,
        RequestPhase.UNKNOWN_EXPIRED: AcknowledgementEligibility.NOT_ACTIONABLE,
    }[phase]
    request = CanonicalRequestTruth(
        key=request_key,
        phase=phase,
        request_kind=request_kind,
        next_actor=(
            NextActor.USER
            if phase
            in {
                RequestPhase.LIVE_UNACKNOWLEDGED,
                RequestPhase.LIVE_ACKNOWLEDGED,
                RequestPhase.STALE_HOLD,
            }
            else NextActor.NONE
        ),
        watermark=watermark,
        source_freshness=freshness,
        acknowledgement_eligibility=eligibility,
        semantic_event_key=event_key,
        opened_at_epoch=NOW - elapsed,
        eligible_elapsed_seconds=elapsed,
    )
    return event, request


def _plan(
    events: tuple[CanonicalOperatorEvent, ...],
    requests: tuple[CanonicalRequestTruth, ...] = (),
    *,
    local_triage: LocalTriageState | None = None,
    quiet: QuietState = NO_QUIET,
    ledger: DeliveryLedger | None = None,
    previous: InterruptionState = EMPTY_STATE,
    now: float = NOW,
) -> InterruptionPlan:
    return plan_interruptions(
        events=events,
        requests=requests,
        local_triage=local_triage or LocalTriageState(()),
        quiet=quiet,
        ledger=ledger or DeliveryLedger(()),
        previous=previous,
        now=now,
    )


def _delivery_key(route: InterruptionRoute, delivery: ChannelDeliveryPlan) -> DeliveryKey:
    return DeliveryKey(route.event_key, delivery.channel, delivery.stage)


def _ledger_for_routes(
    plan: InterruptionPlan,
    *,
    disposition: DeliveryDisposition | None = None,
    recorded_at: float = NOW,
) -> DeliveryLedger:
    ledger = DeliveryLedger(())
    for route in plan.routes:
        for delivery in route.deliveries:
            ledger = record_delivery(
                ledger,
                DeliveryReceipt(
                    key=_delivery_key(route, delivery),
                    disposition=disposition or delivery.disposition,
                    recorded_at_epoch=recorded_at,
                    attempt_generation=0,
                    diagnostic=None,
                ),
            )
    return ledger


def _channels(route: InterruptionRoute) -> frozenset[DeliveryChannel]:
    return frozenset(delivery.channel for delivery in route.deliveries)


@pytest.mark.parametrize(
    ("event_kind", "interruption_class", "expected_channels"),
    (
        (
            TransitionKind.FAILED,
            InterruptionClass.IMPORTANT_OUTCOME,
            ALL_CHANNELS,
        ),
        (
            TransitionKind.COMPLETED,
            InterruptionClass.COURTESY,
            ALL_CHANNELS - {DeliveryChannel.SOUND},
        ),
        (
            TransitionKind.BECAME_ACTIVE,
            InterruptionClass.AMBIENT,
            frozenset({DeliveryChannel.MAILBOX_CUE, DeliveryChannel.HISTORY_FACT}),
        ),
    ),
)
def test_canonical_interruption_class_selects_the_exact_stage_zero_channel_family(
    event_kind: TransitionKind,
    interruption_class: InterruptionClass,
    expected_channels: frozenset[DeliveryChannel],
) -> None:
    event = _work_event(event_kind, interruption_class=interruption_class)

    route = _plan((event,)).routes[0]

    assert route.event_key == event.key
    assert route.interruption_class is interruption_class
    assert route.request_key is None
    assert _channels(route) == expected_channels
    assert {delivery.stage for delivery in route.deliveries} == {0}
    assert {delivery.disposition for delivery in route.deliveries} == {DeliveryDisposition.PENDING}
    assert not route.static_visibility_required


def test_action_required_routes_all_canonical_channels_and_requires_static_truth() -> None:
    event, request = _request_event()

    route = _plan((event,), (request,)).routes[0]

    assert route.interruption_class is InterruptionClass.ACTION_REQUIRED
    assert route.request_key == request.key
    assert _channels(route) == ALL_CHANNELS
    assert route.static_visibility_required
    assert {delivery.stage for delivery in route.deliveries} == {0}
    assert _plan((event,), (request,)).next_escalation_epoch == NOW + 20.0


def test_router_never_reclassifies_a_canonical_event_from_its_transition_kind() -> None:
    event = _work_event(
        TransitionKind.BECAME_ACTIVE,
        interruption_class=InterruptionClass.IMPORTANT_OUTCOME,
    )

    route = _plan((event,)).routes[0]

    assert route.interruption_class is InterruptionClass.IMPORTANT_OUTCOME
    assert _channels(route) == ALL_CHANNELS


def test_only_a_fresh_explicit_current_request_may_interrupt_persistently() -> None:
    stale_event, stale = _request_event(
        phase=RequestPhase.STALE_HOLD,
        freshness=SourceFreshness.STALE,
    )
    unknown_event, unknown = _request_event(
        request_kind=RequestKind.UNKNOWN,
        suffix="02",
    )
    unknown = replace(
        unknown,
        acknowledgement_eligibility=AcknowledgementEligibility.NOT_ACTIONABLE,
    )

    stale_route, unknown_route = _plan(
        (stale_event, unknown_event),
        (stale, unknown),
    ).routes

    assert stale_route.static_visibility_required
    assert stale_route.deliveries == ()
    assert not unknown_route.static_visibility_required
    assert unknown_route.deliveries == ()


@pytest.mark.parametrize(
    "phase",
    (RequestPhase.RESOLVED, RequestPhase.UNKNOWN_EXPIRED),
)
def test_terminal_current_request_cancels_the_older_action_edge(
    phase: RequestPhase,
) -> None:
    event, request = _request_event(phase=phase)

    plan = _plan((event,), (request,))

    assert plan.routes[0].deliveries == ()
    assert not plan.routes[0].static_visibility_required
    assert plan.next_escalation_epoch is None


def test_exact_local_acknowledgement_stops_interruptions_but_keeps_static_truth() -> None:
    event, request = _request_event()
    triage = LocalTriageState((LocalAcknowledgement(request.key, NOW - 1.0),))

    route = _plan((event,), (request,), local_triage=triage).routes[0]

    assert _channels(route) == {
        DeliveryChannel.MAILBOX_CUE,
        DeliveryChannel.HISTORY_FACT,
    }
    assert route.static_visibility_required
    assert _plan((event,), (request,), local_triage=triage).next_escalation_epoch is None


def test_canonical_live_acknowledged_phase_stops_later_escalation() -> None:
    event, request = _request_event(phase=RequestPhase.LIVE_ACKNOWLEDGED)

    plan = _plan((event,), (request,))

    assert _channels(plan.routes[0]) == {
        DeliveryChannel.MAILBOX_CUE,
        DeliveryChannel.HISTORY_FACT,
    }
    assert plan.next_escalation_epoch is None


def test_resume_escalation_uses_the_current_unacknowledged_truth_again() -> None:
    event, request = _request_event(elapsed=31.0)
    stage_zero = _plan((event,), (replace(request, eligible_elapsed_seconds=1.0),))
    ledger = _ledger_for_routes(stage_zero, disposition=DeliveryDisposition.DELIVERED)

    resumed = _plan((), (request,), ledger=ledger)

    assert len(resumed.routes) == 1
    assert {delivery.stage for delivery in resumed.routes[0].deliveries} == {1}
    assert resumed.next_escalation_epoch == NOW + 89.0


@pytest.mark.parametrize(
    ("elapsed", "expected_stage", "next_delta"),
    (
        (0.0, 0, 30.0),
        (29.999, 0, 0.001),
        (30.0, 1, 90.0),
        (120.0, 2, 180.0),
        (300.0, 3, None),
    ),
)
def test_escalation_stages_are_exact_finite_edges(
    elapsed: float,
    expected_stage: int,
    next_delta: float | None,
) -> None:
    event, request = _request_event(elapsed=elapsed)

    plan = _plan((event,), (request,))

    assert {delivery.stage for delivery in plan.routes[0].deliveries if delivery.channel in SUPPRESSIBLE_CHANNELS} == {
        expected_stage
    }
    assert plan.next_escalation_epoch == (None if next_delta is None else pytest.approx(NOW + next_delta))


def test_late_poll_emits_only_the_current_stage_and_never_replays_skipped_stages() -> None:
    event, initial = _request_event(elapsed=1.0)
    ledger = _ledger_for_routes(
        _plan((event,), (initial,)),
        disposition=DeliveryDisposition.DELIVERED,
    )
    late = replace(initial, eligible_elapsed_seconds=125.0)

    plan = _plan((), (late,), ledger=ledger)

    assert {delivery.stage for delivery in plan.routes[0].deliveries} == {2}
    assert all(delivery.stage not in {0, 1} for delivery in plan.routes[0].deliveries)


def test_late_first_observation_still_requires_static_mailbox_and_history_publication() -> None:
    event, request = _request_event(elapsed=125.0)

    route = _plan((event,), (request,)).routes[0]

    assert {
        (delivery.channel, delivery.stage)
        for delivery in route.deliveries
        if delivery.channel in {DeliveryChannel.MAILBOX_CUE, DeliveryChannel.HISTORY_FACT}
    } == {
        (DeliveryChannel.MAILBOX_CUE, 0),
        (DeliveryChannel.HISTORY_FACT, 0),
    }
    assert {delivery.stage for delivery in route.deliveries if delivery.channel in SUPPRESSIBLE_CHANNELS} == {2}


def test_duplicate_poll_and_restart_ledger_never_reemit_an_exact_channel_stage() -> None:
    event, request = _request_event()
    first = _plan((event,), (request,))
    ledger = _ledger_for_routes(first, disposition=DeliveryDisposition.DELIVERED)

    repeated = _plan((event,), (request,), ledger=ledger)

    assert repeated.routes[0].deliveries == ()


def test_one_failed_channel_never_blocks_or_retries_its_missing_siblings() -> None:
    event, request = _request_event()
    failed_key = DeliveryKey(event.key, DeliveryChannel.SYSTEM_NOTIFICATION, 0)
    delivered_key = DeliveryKey(event.key, DeliveryChannel.MAILBOX_CUE, 0)
    ledger = DeliveryLedger(
        (
            DeliveryReceipt(
                delivered_key,
                DeliveryDisposition.DELIVERED,
                NOW - 1.0,
                0,
                None,
            ),
            DeliveryReceipt(
                failed_key,
                DeliveryDisposition.FAILED,
                NOW - 1.0,
                0,
                DeliveryDiagnostic.PERMISSION_DENIED,
            ),
        )
    )

    route = _plan((event,), (request,), ledger=ledger).routes[0]

    assert DeliveryChannel.SYSTEM_NOTIFICATION not in _channels(route)
    assert DeliveryChannel.MAILBOX_CUE not in _channels(route)
    assert DeliveryChannel.SOUND in _channels(route)


def test_disabled_or_expired_receipt_does_not_replay_after_policy_changes() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    ledger = DeliveryLedger(
        (
            DeliveryReceipt(
                DeliveryKey(event.key, DeliveryChannel.SYSTEM_NOTIFICATION, 0),
                DeliveryDisposition.DISABLED,
                NOW - 1.0,
                0,
                None,
            ),
            DeliveryReceipt(
                DeliveryKey(event.key, DeliveryChannel.SOUND, 0),
                DeliveryDisposition.EXPIRED,
                NOW - 1.0,
                0,
                None,
            ),
        )
    )

    route = _plan((event,), ledger=ledger).routes[0]

    assert DeliveryChannel.SYSTEM_NOTIFICATION not in _channels(route)
    assert DeliveryChannel.SOUND not in _channels(route)


def test_user_quiet_suppresses_interruptions_but_not_static_mailbox_or_history() -> None:
    event, request = _request_event()
    quiet = QuietState(True, frozenset({QuietReason.USER_QUIET}), NOW - 30.0)

    plan = _plan((event,), (request,), quiet=quiet)
    route = plan.routes[0]
    dispositions = {delivery.channel: delivery.disposition for delivery in route.deliveries}

    assert dispositions[DeliveryChannel.MAILBOX_CUE] is DeliveryDisposition.PENDING
    assert dispositions[DeliveryChannel.HISTORY_FACT] is DeliveryDisposition.PENDING
    assert {dispositions[channel] for channel in SUPPRESSIBLE_CHANNELS} == {DeliveryDisposition.SUPPRESSED_QUIET}
    assert plan.state.quiet_summary == QuietSummary(1, 0, 0, event.occurred_at_epoch)


@pytest.mark.parametrize(
    ("reasons", "expected"),
    (
        (frozenset({QuietReason.FOCUS}), DeliveryDisposition.SUPPRESSED_POLICY),
        (
            frozenset({QuietReason.USER_QUIET, QuietReason.FOCUS}),
            DeliveryDisposition.SUPPRESSED_POLICY,
        ),
    ),
)
def test_focus_policy_is_the_strictest_suppression_and_never_builds_quiet_summary(
    reasons: frozenset[QuietReason],
    expected: DeliveryDisposition,
) -> None:
    event, request = _request_event()

    plan = _plan(
        (event,),
        (request,),
        quiet=QuietState(True, reasons, NOW - 30.0),
    )
    dispositions = {
        delivery.disposition for delivery in plan.routes[0].deliveries if delivery.channel in SUPPRESSIBLE_CHANNELS
    }

    assert dispositions == {expected}
    assert plan.state.quiet_summary == EMPTY_SUMMARY


def test_repeated_quiet_planning_deduplicates_the_same_semantic_event_in_state() -> None:
    event, request = _request_event()
    quiet = QuietState(True, frozenset({QuietReason.USER_QUIET}), NOW - 30.0)
    first = _plan((event,), (request,), quiet=quiet)

    repeated = _plan((event,), (request,), quiet=quiet, previous=first.state)

    assert repeated.state.quiet_summary == first.state.quiet_summary


def test_quiet_exit_uses_a_delivery_summary_key_and_never_borrows_member_identity() -> None:
    event, request = _request_event()
    quiet = QuietState(True, frozenset({QuietReason.USER_QUIET}), NOW - 30.0)
    during = _plan((event,), (request,), quiet=quiet)
    ledger = _ledger_for_routes(during)

    exited = _plan(
        (),
        (request,),
        ledger=ledger,
        previous=during.state,
        now=NOW + 1.0,
    )

    assert exited.quiet_exit_summary == QuietSummary(
        1,
        0,
        0,
        event.occurred_at_epoch,
    )
    assert type(exited.quiet_exit_route) is QuietExitRoute
    assert type(exited.quiet_exit_route.summary_key) is DeliverySummaryKey
    assert exited.quiet_exit_route.summary_key != event.key
    assert exited.quiet_exit_route.summary_key.member_count == 1
    assert exited.quiet_exit_route.deliveries == (
        ChannelDeliveryPlan(
            DeliveryChannel.SYSTEM_NOTIFICATION,
            0,
            DeliveryDisposition.PENDING,
        ),
    )


def test_focus_remaining_after_user_quiet_suppresses_the_one_exit_summary() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    quiet = QuietState(True, frozenset({QuietReason.USER_QUIET}), NOW - 30.0)
    during = _plan((event,), quiet=quiet)
    ledger = _ledger_for_routes(during)

    exited = _plan(
        (),
        quiet=QuietState(True, frozenset({QuietReason.FOCUS}), NOW - 1.0),
        ledger=ledger,
        previous=during.state,
        now=NOW + 1.0,
    )

    assert exited.quiet_exit_route is not None
    assert exited.quiet_exit_route.deliveries[0].disposition is (DeliveryDisposition.SUPPRESSED_POLICY)


def test_terminal_quiet_summary_exposes_only_exact_suppressed_keys_for_supersession() -> None:
    event = _work_event(TransitionKind.FAILED)
    quiet = QuietState(True, frozenset({QuietReason.USER_QUIET}), NOW - 30.0)
    during = _plan((event,), quiet=quiet)
    ledger = _ledger_for_routes(during)
    exited = _plan(
        (),
        ledger=ledger,
        previous=during.state,
        now=NOW + 1.0,
    )
    assert exited.quiet_exit_route is not None
    summary_key = DeliveryKey(
        exited.quiet_exit_route.summary_key,
        DeliveryChannel.SYSTEM_NOTIFICATION,
        0,
    )
    delivered_summary = record_delivery(
        ledger,
        DeliveryReceipt(
            summary_key,
            DeliveryDisposition.DELIVERED,
            NOW + 2.0,
            0,
            None,
        ),
    )

    terminal = _plan(
        (),
        ledger=delivered_summary,
        previous=exited.state,
        now=NOW + 2.0,
    )

    expected = tuple(
        receipt.key for receipt in ledger.receipts if receipt.disposition is DeliveryDisposition.SUPPRESSED_QUIET
    )
    assert terminal.quiet_exit_route is not None
    assert terminal.quiet_exit_route.deliveries == ()
    assert terminal.quiet_exit_route.supersede_keys == expected


def test_quiet_exit_caps_one_summary_at_five_hundred_twelve_semantic_keys() -> None:
    events = tuple(_work_event(TransitionKind.COMPLETED, suffix=f"{index:04d}") for index in range(600))
    quiet = QuietState(True, frozenset({QuietReason.USER_QUIET}), NOW - 30.0)
    during = _plan(events, quiet=quiet)
    suppressed_receipts = tuple(
        DeliveryReceipt(
            DeliveryKey(event.key, DeliveryChannel.SYSTEM_NOTIFICATION, 0),
            DeliveryDisposition.SUPPRESSED_QUIET,
            NOW,
            0,
            None,
        )
        for event in events[:512]
    )

    exited = _plan(
        (),
        ledger=DeliveryLedger(suppressed_receipts),
        previous=during.state,
        now=NOW + 1.0,
    )

    assert exited.quiet_exit_route is not None
    assert exited.quiet_exit_route.summary_key.member_count == 512
    assert exited.quiet_exit_summary == QuietSummary(
        0,
        0,
        512,
        NOW - 10.0,
    )


def test_quiet_exit_can_recover_one_summary_from_restart_ledger_and_public_state() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    suppressed = DeliveryReceipt(
        DeliveryKey(event.key, DeliveryChannel.SYSTEM_NOTIFICATION, 0),
        DeliveryDisposition.SUPPRESSED_QUIET,
        NOW - 1.0,
        0,
        None,
    )
    restored_state = InterruptionState(
        QuietSummary(0, 0, 1, event.occurred_at_epoch),
        None,
    )

    plan = _plan(
        (),
        ledger=DeliveryLedger((suppressed,)),
        previous=restored_state,
    )

    assert plan.quiet_exit_route is not None
    assert plan.quiet_exit_route.summary_key.member_count == 1


def test_quiet_exit_without_recorded_suppression_clears_accumulation_once() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    quiet = QuietState(True, frozenset({QuietReason.USER_QUIET}), NOW - 1.0)
    during = _plan((event,), quiet=quiet)

    exited = _plan((), previous=during.state, now=NOW + 1.0)
    repeated = _plan((), previous=exited.state, now=NOW + 2.0)

    assert exited.quiet_exit_route is None
    assert exited.state.quiet_summary == EMPTY_SUMMARY
    assert exited.state.last_quiet_exit_epoch == NOW + 1.0
    assert repeated.state.last_quiet_exit_epoch == NOW + 1.0


def test_inert_ambient_suppression_from_an_old_ledger_never_manufactures_a_summary() -> None:
    ambient = _work_event(TransitionKind.BECAME_ACTIVE)
    receipt = DeliveryReceipt(
        DeliveryKey(ambient.key, DeliveryChannel.SYSTEM_NOTIFICATION, 0),
        DeliveryDisposition.SUPPRESSED_QUIET,
        NOW - 1.0,
        0,
        None,
    )
    restored_state = InterruptionState(QuietSummary(0, 0, 1, NOW - 10.0), None)

    plan = _plan(
        (),
        ledger=DeliveryLedger((receipt,)),
        previous=restored_state,
    )

    assert plan.quiet_exit_route is None
    assert plan.state.quiet_summary == EMPTY_SUMMARY


def test_live_ask_after_quiet_emits_only_the_current_not_yet_consumed_stage() -> None:
    event, request = _request_event(elapsed=1.0)
    quiet = QuietState(True, frozenset({QuietReason.USER_QUIET}), NOW - 1.0)
    during = _plan((event,), (request,), quiet=quiet)
    ledger = _ledger_for_routes(during)
    later = replace(request, eligible_elapsed_seconds=40.0)

    exited = _plan(
        (),
        (later,),
        ledger=ledger,
        previous=during.state,
        now=NOW + 40.0,
    )

    assert exited.quiet_exit_route is not None
    assert {delivery.stage for delivery in exited.routes[0].deliveries} == {1}


@pytest.mark.parametrize(
    ("event_factory", "expected_body"),
    (
        (lambda: _request_event()[0], "A Codex session needs you"),
        (
            lambda: _work_event(TransitionKind.COMPLETED),
            "A Codex session finished",
        ),
        (
            lambda: _work_event(TransitionKind.FAILED),
            "A Codex session finished",
        ),
    ),
)
def test_generic_notification_copy_contains_only_product_owned_provider_semantics(
    event_factory,
    expected_body: str,
) -> None:
    event = event_factory()
    request = _request_event()[1] if event.kind is TransitionKind.REQUEST_OPENED else None
    route = _plan((event,), (() if request is None else (request,))).routes[0]

    copy = generic_notification_copy(route)

    assert copy == GenericNotificationCopy("SidePulse", expected_body)


def test_notification_copy_never_echoes_opaque_or_private_shaped_source_values() -> None:
    sentinel = "prompt:Users:jonathan:project-secret"
    event = _work_event(
        TransitionKind.COMPLETED,
        provider="unlisted",
        suffix="private",
        work_id=sentinel,
    )
    route = _plan((event,)).routes[0]

    copy = generic_notification_copy(route)

    assert copy == GenericNotificationCopy("SidePulse", "A Provider session finished")
    rendered = f"{copy.title} {copy.body}"
    assert sentinel not in rendered
    assert "secret" not in rendered.lower()


@pytest.mark.parametrize(
    "sentinel",
    (
        "prompt:delete-files",
        "path:Users:jonathan:Documents:secret",
        "email:jonathan.example.com",
        "credential:sk-secret",
        "session:abc123",
        "url:https:example.com:private",
        "raw-error:permission-denied",
    ),
)
def test_generic_copy_excludes_the_full_grammar_compatible_private_corpus(
    sentinel: str,
) -> None:
    event = _work_event(
        TransitionKind.COMPLETED,
        source_instance=sentinel,
    )

    copy = generic_notification_copy(_plan((event,)).routes[0])

    assert sentinel not in f"{copy.title} {copy.body}"


def test_quiet_summary_copy_is_one_bounded_count_only_message() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    quiet = QuietState(True, frozenset({QuietReason.USER_QUIET}), NOW - 1.0)
    during = _plan((event,), quiet=quiet)
    exited = _plan(
        (),
        ledger=_ledger_for_routes(during),
        previous=during.state,
        now=NOW + 1.0,
    )
    assert exited.quiet_exit_route is not None

    copy = generic_notification_copy(exited.quiet_exit_route)

    assert copy == GenericNotificationCopy("SidePulse", "SidePulse has 1 update")


def test_action_token_payload_is_opaque_bounded_and_contains_no_navigation_identity() -> None:
    event = _work_event(
        TransitionKind.COMPLETED,
        suffix="private",
        work_id="session:private:project-secret",
    )

    binding = issue_action_token(
        randomness=b"r" * 32,
        event_key=event.key,
        operator_generation=42,
        now=NOW,
        ttl_seconds=120.0,
    )

    assert type(binding) is ActionTokenBinding
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", binding.token)
    assert "session" not in binding.token
    assert "secret" not in binding.token
    assert "session" not in binding.event_fingerprint
    assert not hasattr(binding, "event_key")
    assert binding.expires_at_epoch == NOW + 120.0


def test_action_token_notification_metadata_contains_only_the_opaque_token() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    binding = issue_action_token(
        randomness=b"m" * 32,
        event_key=event.key,
        operator_generation=3,
        now=NOW,
    )

    metadata = action_token_metadata(binding)

    assert metadata == {"action_token": binding.token}
    assert not {
        "agent_id",
        "work_key",
        "request_key",
        "session_id",
        "title",
        "path",
        "url",
        "command",
        "event_fingerprint",
        "operator_generation",
        "expires_at_epoch",
    }.intersection(metadata)


def test_action_token_value_is_bound_to_event_generation_and_expiry_metadata() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    other = _work_event(TransitionKind.COMPLETED, suffix="02")

    baseline = issue_action_token(
        randomness=b"z" * 32,
        event_key=event.key,
        operator_generation=1,
        now=NOW,
        ttl_seconds=30.0,
    )
    changed_event = issue_action_token(
        randomness=b"z" * 32,
        event_key=other.key,
        operator_generation=1,
        now=NOW,
        ttl_seconds=30.0,
    )
    changed_generation = issue_action_token(
        randomness=b"z" * 32,
        event_key=event.key,
        operator_generation=2,
        now=NOW,
        ttl_seconds=30.0,
    )
    changed_expiry = issue_action_token(
        randomness=b"z" * 32,
        event_key=event.key,
        operator_generation=1,
        now=NOW,
        ttl_seconds=31.0,
    )

    assert (
        len(
            {
                baseline.token,
                changed_event.token,
                changed_generation.token,
                changed_expiry.token,
            }
        )
        == 4
    )


def test_action_token_reresolves_only_one_exact_current_generation_candidate() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    other = _work_event(TransitionKind.COMPLETED, suffix="02")
    binding = issue_action_token(
        randomness=b"x" * 32,
        event_key=event.key,
        operator_generation=7,
        now=NOW,
        ttl_seconds=30.0,
    )

    assert (
        resolve_action_token(
            binding,
            presented_token=binding.token,
            candidate_event_keys=(other.key, event.key),
            current_generation=7,
            now=NOW + 29.0,
        )
        == event.key
    )
    assert (
        resolve_action_token(
            binding,
            presented_token="x" * 43,
            candidate_event_keys=(event.key,),
            current_generation=7,
            now=NOW + 1.0,
        )
        is None
    )
    assert (
        resolve_action_token(
            binding,
            presented_token=binding.token,
            candidate_event_keys=(event.key,),
            current_generation=8,
            now=NOW + 1.0,
        )
        is None
    )
    assert (
        resolve_action_token(
            binding,
            presented_token=binding.token,
            candidate_event_keys=(other.key,),
            current_generation=7,
            now=NOW + 1.0,
        )
        is None
    )
    assert (
        resolve_action_token(
            binding,
            presented_token=binding.token,
            candidate_event_keys=(event.key,),
            current_generation=7,
            now=NOW + 30.0,
        )
        is None
    )


def test_action_token_resolver_fails_closed_for_duplicate_candidates() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    binding = issue_action_token(
        randomness=b"d" * 32,
        event_key=event.key,
        operator_generation=1,
        now=NOW,
    )

    assert (
        resolve_action_token(
            binding,
            presented_token=binding.token,
            candidate_event_keys=(event.key, event.key),
            current_generation=1,
            now=NOW + 1.0,
        )
        is None
    )


def test_finite_cue_batch_uses_existing_presentation_types_and_hard_budget() -> None:
    action_event, request = _request_event(suffix="01")
    failure = _work_event(TransitionKind.FAILED, suffix="02")
    completion = _work_event(TransitionKind.COMPLETED, suffix="03")
    plan = _plan((completion, failure, action_event), (request,))

    batch = plan_finite_cues(plan)

    assert type(batch) is FiniteCueBatch
    assert tuple(cue.semantic for cue in batch.cues) == (
        GlanceSemantic.ATTENTION,
        GlanceSemantic.FRESH_FAILURE,
    )
    assert all(1 <= cue.repetitions <= 2 for cue in batch.cues)
    assert batch.overflowed
    assert batch.overflow_count == 1
    state = FiniteCueCoordinator().observe(
        batch.cues,
        now=10.0,
        play_motion=True,
    )
    assert state.active == batch.cues[0]
    assert state.pending == batch.cues[1]


def test_suppressed_or_ambient_routes_never_enter_the_finite_cue_batch() -> None:
    action, request = _request_event()
    ambient = _work_event(TransitionKind.BECAME_ACTIVE, suffix="02")
    quiet = QuietState(True, frozenset({QuietReason.USER_QUIET}), NOW - 1.0)

    batch = plan_finite_cues(_plan((action, ambient), (request,), quiet=quiet))

    assert batch == FiniteCueBatch((), False, 0)


@pytest.mark.parametrize("invalid_now", (float("nan"), float("inf"), -1.0, True))
def test_planner_rejects_invalid_time_without_mutating_inputs(invalid_now: object) -> None:
    event = _work_event(TransitionKind.COMPLETED)

    with pytest.raises(InterruptionPolicyValidationError, match="time"):
        _plan((event,), now=invalid_now)  # type: ignore[arg-type]


def test_planner_rejects_a_future_quiet_anchor() -> None:
    quiet = QuietState(True, frozenset({QuietReason.USER_QUIET}), NOW + 1.0)

    with pytest.raises(InterruptionPolicyValidationError, match="quiet time"):
        _plan((), quiet=quiet, now=NOW)


def test_planner_rejects_duplicate_event_and_request_keys() -> None:
    event, request = _request_event()

    with pytest.raises(InterruptionPolicyValidationError, match="duplicate event"):
        _plan((event, event), (request,))
    with pytest.raises(InterruptionPolicyValidationError, match="duplicate request"):
        _plan((event,), (request, request))


def test_two_thousand_canonical_edges_remain_bounded_before_persistent_routes() -> None:
    events = tuple(_work_event(TransitionKind.BECAME_ACTIVE, suffix=f"{index:04d}") for index in range(2_000))
    persistent_event, request = _request_event(suffix="persistent")
    receipt = DeliveryReceipt(
        DeliveryKey(
            persistent_event.key,
            DeliveryChannel.MAILBOX_CUE,
            0,
        ),
        DeliveryDisposition.DELIVERED,
        NOW - 1.0,
        0,
        None,
    )

    plan = _plan(
        events,
        (request,),
        ledger=DeliveryLedger((receipt,)),
    )

    assert len(plan.routes) == 2_000
    assert {route.event_key for route in plan.routes} == {event.key for event in events}


def test_quiet_state_and_channel_plans_fail_closed_on_parallel_or_malformed_values() -> None:
    with pytest.raises(InterruptionPolicyValidationError):
        QuietState(False, frozenset({QuietReason.USER_QUIET}), None)
    with pytest.raises(InterruptionPolicyValidationError):
        QuietState(True, frozenset(), NOW)
    with pytest.raises(InterruptionPolicyValidationError):
        ChannelDeliveryPlan(
            DeliveryChannel.SOUND,
            0,
            DeliveryDisposition.DELIVERED,
        )
    with pytest.raises(InterruptionPolicyValidationError):
        issue_action_token(
            randomness=b"short",
            event_key=_work_event(TransitionKind.COMPLETED).key,
            operator_generation=1,
            now=NOW,
        )
