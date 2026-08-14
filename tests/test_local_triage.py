from __future__ import annotations

from dataclasses import replace

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.local_triage import (
    LocalAcknowledgement,
    LocalTriageMutationKind,
    LocalTriageState,
    LocalTriageValidationError,
    apply_local_triage_mutation,
    reconcile_local_triage,
)
from sidepulse.operator_state import (
    AcknowledgementEligibility,
    CanonicalRequestTruth,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
)
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

NOW = 1_786_536_000.0


def _request_key(
    request_id: str = "request:01",
    *,
    source_instance: str = "local:01",
    work_id: str = "work:01",
) -> RequestKey:
    source = SourceKey("codex", "hooks", source_instance, "live_agent_events")
    return RequestKey(WorkKey(source, WorkIdentifier(work_id)), RequestIdentifier(request_id))


def _request(
    key: RequestKey | None = None,
    *,
    phase: RequestPhase = RequestPhase.LIVE_UNACKNOWLEDGED,
    eligibility: AcknowledgementEligibility = AcknowledgementEligibility.ELIGIBLE,
    event_token: str = "event:001",
) -> CanonicalRequestTruth:
    actual_key = key or _request_key()
    watermark = ProviderWatermark(
        source_key=actual_key.work_key.source_key,
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        occurred_at_epoch=NOW - 30.0,
        event_token=EventToken(event_token),
        sequence=None,
        tie_break_rank=10,
    )
    event_key = SemanticEventKey(
        subject_key=actual_key,
        transition_kind=(
            TransitionKind.REQUEST_RESOLVED
            if phase in {RequestPhase.RESOLVED, RequestPhase.UNKNOWN_EXPIRED}
            else TransitionKind.REQUEST_OPENED
        ),
        provider_watermark=watermark,
    )
    return CanonicalRequestTruth(
        key=actual_key,
        phase=phase,
        request_kind=RequestKind.PERMISSION,
        next_actor=(
            NextActor.NONE
            if phase in {RequestPhase.RESOLVED, RequestPhase.UNKNOWN_EXPIRED}
            else NextActor.USER
        ),
        watermark=watermark,
        source_freshness=(
            SourceFreshness.STALE
            if phase is RequestPhase.STALE_HOLD
            else SourceFreshness.FRESH
        ),
        acknowledgement_eligibility=eligibility,
        semantic_event_key=event_key,
        opened_at_epoch=NOW - 120.0,
        eligible_elapsed_seconds=90.0,
    )


def test_acknowledge_is_exact_idempotent_reversible_and_does_not_mutate_truth() -> None:
    request = _request()
    original = request

    acknowledged = apply_local_triage_mutation(
        LocalTriageState(()),
        request=request,
        mutation=LocalTriageMutationKind.ACKNOWLEDGE,
        now=NOW,
    )
    repeated = apply_local_triage_mutation(
        acknowledged,
        request=request,
        mutation=LocalTriageMutationKind.ACKNOWLEDGE,
        now=NOW + 1.0,
    )
    resumed = apply_local_triage_mutation(
        repeated,
        request=request,
        mutation=LocalTriageMutationKind.RESUME_ESCALATION,
        now=NOW + 2.0,
    )

    assert acknowledged == LocalTriageState((LocalAcknowledgement(request.key, NOW),))
    assert repeated is acknowledged
    assert resumed == LocalTriageState(())
    assert request == original


def test_resume_removes_only_the_selected_exact_request_key() -> None:
    first = _request(_request_key("request:first"), event_token="event:first")
    second = _request(_request_key("request:second"), event_token="event:second")
    state = LocalTriageState(
        (
            LocalAcknowledgement(first.key, NOW - 2.0),
            LocalAcknowledgement(second.key, NOW - 1.0),
        )
    )

    result = apply_local_triage_mutation(
        state,
        request=second,
        mutation=LocalTriageMutationKind.RESUME_ESCALATION,
        now=NOW,
    )

    assert result == LocalTriageState((LocalAcknowledgement(first.key, NOW - 2.0),))


def test_same_request_id_under_two_source_instances_never_collides() -> None:
    first = _request(
        _request_key("request:same", source_instance="local:01"),
        event_token="event:first",
    )
    second = _request(
        _request_key("request:same", source_instance="local:02"),
        event_token="event:second",
    )

    state = apply_local_triage_mutation(
        LocalTriageState(()),
        request=first,
        mutation=LocalTriageMutationKind.ACKNOWLEDGE,
        now=NOW,
    )
    state = apply_local_triage_mutation(
        state,
        request=second,
        mutation=LocalTriageMutationKind.ACKNOWLEDGE,
        now=NOW + 1.0,
    )

    assert tuple(item.request_key for item in state.acknowledgements) == (
        first.key,
        second.key,
    )


@pytest.mark.parametrize(
    ("phase", "eligibility"),
    (
        (RequestPhase.STALE_HOLD, AcknowledgementEligibility.STALE_HOLD),
        (RequestPhase.RESOLVED, AcknowledgementEligibility.RESOLVED),
        (RequestPhase.UNKNOWN_EXPIRED, AcknowledgementEligibility.RESOLVED),
        (RequestPhase.LIVE_ACKNOWLEDGED, AcknowledgementEligibility.ALREADY_ACKNOWLEDGED),
        (RequestPhase.LIVE_UNACKNOWLEDGED, AcknowledgementEligibility.NOT_ACTIONABLE),
    ),
)
def test_forged_acknowledge_rechecks_current_phase_and_eligibility(
    phase: RequestPhase,
    eligibility: AcknowledgementEligibility,
) -> None:
    request = _request(phase=phase, eligibility=eligibility)

    with pytest.raises(LocalTriageValidationError, match="not eligible"):
        apply_local_triage_mutation(
            LocalTriageState(()),
            request=request,
            mutation=LocalTriageMutationKind.ACKNOWLEDGE,
            now=NOW,
        )


@pytest.mark.parametrize("now", (float("nan"), float("inf"), -1.0, True, "now"))
def test_nonfinite_or_invalid_mutation_time_is_rejected(now: object) -> None:
    with pytest.raises(LocalTriageValidationError, match="time"):
        apply_local_triage_mutation(
            LocalTriageState(()),
            request=_request(),
            mutation=LocalTriageMutationKind.ACKNOWLEDGE,
            now=now,  # type: ignore[arg-type]
        )


def test_cap_refuses_a_five_hundred_thirteenth_exact_acknowledgement() -> None:
    acknowledgements = tuple(
        LocalAcknowledgement(_request_key(f"request:{index:03d}"), NOW - 1.0)
        for index in range(512)
    )
    state = LocalTriageState(acknowledgements)
    overflow = _request(_request_key("request:overflow"), event_token="event:overflow")

    with pytest.raises(LocalTriageValidationError, match="capacity"):
        apply_local_triage_mutation(
            state,
            request=overflow,
            mutation=LocalTriageMutationKind.ACKNOWLEDGE,
            now=NOW,
        )


def test_reconcile_preserves_live_and_stale_hold_then_prunes_only_terminal_truth() -> None:
    live = _request(_request_key("request:live"), event_token="event:live")
    stale = _request(
        _request_key("request:stale"),
        phase=RequestPhase.STALE_HOLD,
        eligibility=AcknowledgementEligibility.STALE_HOLD,
        event_token="event:stale",
    )
    resolved = _request(
        _request_key("request:resolved"),
        phase=RequestPhase.RESOLVED,
        eligibility=AcknowledgementEligibility.RESOLVED,
        event_token="event:resolved",
    )
    expired = _request(
        _request_key("request:expired"),
        phase=RequestPhase.UNKNOWN_EXPIRED,
        eligibility=AcknowledgementEligibility.RESOLVED,
        event_token="event:expired",
    )
    missing = _request_key("request:temporarily-missing")
    state = LocalTriageState(
        tuple(
            LocalAcknowledgement(key, NOW - 60.0)
            for key in (live.key, stale.key, resolved.key, expired.key, missing)
        )
    )

    result = reconcile_local_triage(state, (expired, stale, resolved, live))

    assert tuple(item.request_key for item in result.acknowledgements) == (
        live.key,
        stale.key,
        missing,
    )


def test_resume_with_a_different_current_key_cannot_remove_existing_acknowledgement() -> None:
    original = _request()
    changed_key = replace(original.key, request_id=RequestIdentifier("request:changed"))
    changed = _request(changed_key, event_token="event:changed")
    state = LocalTriageState((LocalAcknowledgement(original.key, NOW),))

    result = apply_local_triage_mutation(
        state,
        request=changed,
        mutation=LocalTriageMutationKind.RESUME_ESCALATION,
        now=NOW + 1.0,
    )

    assert result is state
