from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.dnd_policy import DisplayAdmission
from sidepulse.operator_state import (
    TIMING_RECOVERY_CONFIRMATIONS,
    CanonicalOperatorEvent,
    SemanticEventKey,
    TransitionKind,
    classify_operator_event,
)
from sidepulse.provider_facts import (
    EventToken,
    ProviderWatermark,
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
)
from sidepulse.recovery_grace_note import (
    ConfirmedRecoveryEvidence,
    RecoveryGraceDisposition,
    RecoveryGracePresentation,
    RecoveryGraceSuppressionReason,
    plan_recovery_grace_note,
)
from sidepulse.semantic_effect_router import CourtesySuppression


def _source() -> SourceKey:
    return SourceKey("codex", "hooks", "local:01", "live_agent_events")


def _watermark() -> ProviderWatermark:
    return ProviderWatermark(
        source_key=_source(),
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        occurred_at_epoch=1_800_000_100.0,
        event_token=EventToken("source-recovered:100"),
        sequence=None,
        tie_break_rank=10,
    )


def _operator_event(
    *,
    work_id: str = "work:01",
    kind: TransitionKind = TransitionKind.SOURCE_RECOVERED,
    watermark: ProviderWatermark | None = None,
    freshness: SourceFreshness = SourceFreshness.FRESH,
) -> CanonicalOperatorEvent:
    selected_watermark = watermark or _watermark()
    subject = WorkKey(selected_watermark.source_key, WorkIdentifier(work_id))
    key = SemanticEventKey(subject, kind, selected_watermark)
    return CanonicalOperatorEvent(
        key=key,
        subject_key=subject,
        kind=kind,
        interruption_class=classify_operator_event(kind),
        occurred_at_epoch=selected_watermark.occurred_at_epoch,
        source_freshness=freshness,
    )


def _evidence(
    *,
    event: CanonicalOperatorEvent | None = None,
    previous_health: SourceHealth = SourceHealth.UNAVAILABLE,
    current_health: SourceHealth = SourceHealth.HEALTHY,
    recovery_confirmations: int = TIMING_RECOVERY_CONFIRMATIONS,
) -> ConfirmedRecoveryEvidence:
    return ConfirmedRecoveryEvidence(
        event=event or _operator_event(),
        previous_health=previous_health,
        current_health=current_health,
        recovery_confirmations=recovery_confirmations,
    )


def _plan(**overrides: object):
    inputs = {
        "dnd_display_admission": DisplayAdmission.ALL,
        "courtesy_suppression": CourtesySuppression(),
        "finite_cue_available": True,
        "reduce_motion": False,
    }
    inputs.update(overrides)
    return plan_recovery_grace_note(_evidence(), **inputs)


def test_confirmed_recovery_emits_one_restrained_wipe_then_returns_to_normal() -> None:
    plan = _plan()

    assert plan.disposition is RecoveryGraceDisposition.EMIT
    assert plan.presentation is RecoveryGracePresentation.RESTRAINED_WIPE
    assert plan.repetitions == 1
    assert plan.duration_seconds == 1.2
    assert plan.returns_to_normal is True
    assert plan.consumes_finite_cue is True
    assert plan.suppression_reason is None
    assert plan.accessibility_text == (
        "Source recovered. A restrained recovery cue will play once, then "
        "normal status returns."
    )


def test_reduce_motion_substitutes_one_static_recovery_highlight() -> None:
    plan = _plan(reduce_motion=True)

    assert plan.disposition is RecoveryGraceDisposition.STATIC
    assert plan.presentation is RecoveryGracePresentation.STATIC_HIGHLIGHT
    assert plan.repetitions == 1
    assert plan.duration_seconds == 0.8
    assert plan.returns_to_normal is True
    assert plan.consumes_finite_cue is True
    assert plan.accessibility_text == (
        "Source recovered. A brief static highlight replaces motion, then "
        "normal status returns."
    )


@pytest.mark.parametrize(
    "admission",
    (DisplayAdmission.NONE, DisplayAdmission.ASKS, DisplayAdmission.CRITICAL),
)
def test_dnd_admissions_that_exclude_courtesy_suppress_recovery(
    admission: DisplayAdmission,
) -> None:
    plan = _plan(dnd_display_admission=admission)

    assert plan.disposition is RecoveryGraceDisposition.SUPPRESS
    assert plan.presentation is RecoveryGracePresentation.NONE
    assert plan.suppression_reason is RecoveryGraceSuppressionReason.DND
    assert plan.accessibility_text == (
        "Source recovered. The courtesy cue is withheld by Do Not Disturb."
    )


@pytest.mark.parametrize(
    ("suppression", "reason", "accessibility_text"),
    (
        (
            CourtesySuppression(focus=True),
            RecoveryGraceSuppressionReason.COURTESY_FOCUS,
            "Source recovered. The courtesy cue is withheld by the active focus policy.",
        ),
        (
            CourtesySuppression(snoozed=True),
            RecoveryGraceSuppressionReason.COURTESY_SNOOZE,
            "Source recovered. The courtesy cue is snoozed.",
        ),
        (
            CourtesySuppression(budget_exhausted=True),
            RecoveryGraceSuppressionReason.COURTESY_BUDGET,
            "Source recovered. The courtesy cue budget is currently exhausted.",
        ),
    ),
)
def test_courtesy_policy_suppresses_with_an_explainable_reason(
    suppression: CourtesySuppression,
    reason: RecoveryGraceSuppressionReason,
    accessibility_text: str,
) -> None:
    plan = _plan(courtesy_suppression=suppression)

    assert plan.disposition is RecoveryGraceDisposition.SUPPRESS
    assert plan.suppression_reason is reason
    assert plan.accessibility_text == accessibility_text


def test_unavailable_finite_cue_capacity_suppresses_without_queueing_motion() -> None:
    plan = _plan(finite_cue_available=False)

    assert plan.disposition is RecoveryGraceDisposition.SUPPRESS
    assert plan.suppression_reason is RecoveryGraceSuppressionReason.FINITE_CUE_UNAVAILABLE
    assert plan.repetitions == 0
    assert plan.duration_seconds == 0.0
    assert plan.returns_to_normal is False
    assert plan.consumes_finite_cue is False


def test_one_source_recovery_has_one_identity_across_multiple_work_rows() -> None:
    watermark = _watermark()
    first = _evidence(event=_operator_event(work_id="work:01", watermark=watermark))
    second = _evidence(event=_operator_event(work_id="work:02", watermark=watermark))

    first_plan = plan_recovery_grace_note(
        first,
        dnd_display_admission=DisplayAdmission.ALL,
        courtesy_suppression=CourtesySuppression(),
        finite_cue_available=True,
        reduce_motion=False,
    )
    second_plan = plan_recovery_grace_note(
        second,
        dnd_display_admission=DisplayAdmission.ALL,
        courtesy_suppression=CourtesySuppression(),
        finite_cue_available=True,
        reduce_motion=False,
        presented_identities=(first_plan.dedupe_identity,),
    )

    assert first_plan.dedupe_identity == second_plan.dedupe_identity
    assert second_plan.disposition is RecoveryGraceDisposition.SUPPRESS
    assert second_plan.suppression_reason is RecoveryGraceSuppressionReason.DUPLICATE


@pytest.mark.parametrize("confirmations", (0, 1))
def test_recovery_evidence_requires_the_existing_two_confirmation_truth(
    confirmations: int,
) -> None:
    with pytest.raises(ValueError, match="confirmed recovery evidence"):
        _evidence(recovery_confirmations=confirmations)


@pytest.mark.parametrize(
    "evidence",
    (
        lambda: _evidence(event=_operator_event(kind=TransitionKind.COMPLETED)),
        lambda: _evidence(previous_health=SourceHealth.HEALTHY),
        lambda: _evidence(current_health=SourceHealth.PARTIAL),
        lambda: _evidence(
            event=_operator_event(freshness=SourceFreshness.TIMING_UNCERTAIN)
        ),
    ),
)
def test_only_fresh_explicit_source_recovery_evidence_is_accepted(evidence) -> None:
    with pytest.raises(ValueError, match="confirmed recovery evidence"):
        evidence()


def test_recovery_plan_and_identity_are_immutable() -> None:
    plan = _plan()

    with pytest.raises(FrozenInstanceError):
        plan.repetitions = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.dedupe_identity.watermark = _watermark()  # type: ignore[misc]
