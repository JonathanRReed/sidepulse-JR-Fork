from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import inf, nan

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.operator_state import (
    MAX_CANONICAL_REQUESTS,
    MAX_CANONICAL_WORKS,
    MAX_CLOCK_DELTA_DIVERGENCE_SECONDS,
    MAX_EVENTS_PER_REDUCTION,
    TIMING_RECOVERY_CONFIRMATIONS,
    TIMING_UNCERTAINTY_LEASE_SECONDS,
    AcknowledgementEligibility,
    BootIdentifier,
    CanonicalOperatorState,
    ClockContinuityStatus,
    ClockSample,
    InterruptionClass,
    InvalidationDomain,
    OperatorStateValidationError,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
    classify_operator_event,
    empty_operator_state,
    reduce_operator_state,
    semantic_event_key_from_payload,
    semantic_event_key_to_payload,
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


def _source(
    instance: str = "local:01",
    *,
    provider: str = "codex",
) -> SourceKey:
    return SourceKey(provider, "hooks", instance, "live_agent_events")


def _work_key(
    value: str = "work:01",
    *,
    source: SourceKey | None = None,
) -> WorkKey:
    return WorkKey(source or _source(), WorkIdentifier(value))


def _request_key(
    value: str = "request:01",
    *,
    work_key: WorkKey | None = None,
) -> RequestKey:
    return RequestKey(work_key or _work_key(), RequestIdentifier(value))


def _watermark(
    token: str = "event:001",
    *,
    source: SourceKey | None = None,
    epoch: float = 1_800_000_000.0,
    rank: int = 10,
    basis: WatermarkBasis = WatermarkBasis.PROVIDER_EVENT_ID,
    sequence: int | None = None,
) -> ProviderWatermark:
    return ProviderWatermark(
        source_key=source or _source(),
        basis=basis,
        occurred_at_epoch=epoch,
        event_token=EventToken(token),
        sequence=sequence,
        tie_break_rank=rank,
    )


def _work_fact(
    lifecycle: WorkLifecycle = WorkLifecycle.ACTIVE,
    *,
    key: WorkKey | None = None,
    watermark: ProviderWatermark | None = None,
    parent_key: WorkKey | None = None,
    next_actor: NextActor = NextActor.PROVIDER,
) -> ProviderWorkFact:
    actual_key = key or _work_key()
    return ProviderWorkFact(
        key=actual_key,
        lifecycle=lifecycle,
        watermark=watermark or _watermark(source=actual_key.source_key),
        safe_label=f"Codex {actual_key.work_id.value}",
        parent_key=parent_key,
        next_actor=next_actor,
    )


def _request_fact(
    state: ProviderRequestState = ProviderRequestState.LIVE,
    *,
    key: RequestKey | None = None,
    watermark: ProviderWatermark | None = None,
    request_kind: RequestKind = RequestKind.PERMISSION,
    next_actor: NextActor = NextActor.USER,
) -> ProviderRequestFact:
    actual_key = key or _request_key()
    return ProviderRequestFact(
        key=actual_key,
        state=state,
        request_kind=request_kind,
        next_actor=next_actor,
        watermark=watermark or _watermark(source=actual_key.work_key.source_key),
    )


def _batch(
    *,
    source: SourceKey | None = None,
    watermark: ProviderWatermark | None = None,
    work_facts: tuple[ProviderWorkFact, ...] = (),
    request_facts: tuple[ProviderRequestFact, ...] = (),
    authority: ObservationAuthority = ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    health: SourceHealth = SourceHealth.HEALTHY,
    freshness: SourceFreshness = SourceFreshness.FRESH,
    observed_at: float | None = None,
) -> ProviderFactBatch:
    actual_source = source or _source()
    actual_watermark = watermark or _watermark(source=actual_source)
    return ProviderFactBatch(
        source_key=actual_source,
        observation_authority=authority,
        source_health=health,
        source_freshness=freshness,
        observed_at_epoch=(
            actual_watermark.occurred_at_epoch if observed_at is None else observed_at
        ),
        watermark=actual_watermark,
        work_facts=work_facts,
        request_facts=request_facts,
        diagnostics=(),
    )


def _clock(
    *,
    wall: float = 1_800_000_000.0,
    monotonic: float = 100.0,
    boot: str = "boot:01",
) -> ClockSample:
    return ClockSample(wall, monotonic, BootIdentifier(boot))


def _initial_active_request(
    *,
    authority: ObservationAuthority = ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    freshness: SourceFreshness = SourceFreshness.FRESH,
) -> tuple[CanonicalOperatorState, ProviderFactBatch, WorkKey, RequestKey]:
    work_key = _work_key()
    request_key = _request_key(work_key=work_key)
    watermark = _watermark()
    batch = _batch(
        watermark=watermark,
        work_facts=(_work_fact(key=work_key, watermark=watermark),),
        request_facts=(_request_fact(key=request_key, watermark=watermark),),
        authority=authority,
        freshness=freshness,
    )
    result = reduce_operator_state(empty_operator_state(), batch, clock=_clock())
    return result.state, batch, work_key, request_key


def _event_kinds(result: object) -> tuple[TransitionKind, ...]:
    return tuple(event.kind for event in result.events)  # type: ignore[attr-defined]


def test_empty_state_and_event_classification_are_exact() -> None:
    """Wrong default truth or interruption classes would create phantom operator cues."""
    state = empty_operator_state()

    assert state == CanonicalOperatorState(
        schema_version=1,
        generation=0,
        works=(),
        requests=(),
        source_watermarks=(),
        timing_uncertain_sources=(),
        clock_continuity=state.clock_continuity,
        last_clock=None,
    )
    assert state.clock_continuity.status is ClockContinuityStatus.STABLE
    assert classify_operator_event(TransitionKind.REQUEST_OPENED) is InterruptionClass.ACTION_REQUIRED
    assert classify_operator_event(TransitionKind.FAILED) is InterruptionClass.IMPORTANT_OUTCOME
    assert classify_operator_event(TransitionKind.COMPLETED) is InterruptionClass.COURTESY
    assert classify_operator_event(TransitionKind.BECAME_ACTIVE) is InterruptionClass.AMBIENT


@pytest.mark.parametrize(
    ("lifecycle", "expected_kind"),
    [
        (WorkLifecycle.IDLE, None),
        (WorkLifecycle.ACTIVE, TransitionKind.BECAME_ACTIVE),
        (WorkLifecycle.WAITING, TransitionKind.BECAME_ACTIVE),
        (WorkLifecycle.COMPLETED, TransitionKind.COMPLETED),
        (WorkLifecycle.FAILED, TransitionKind.FAILED),
        (WorkLifecycle.UNKNOWN, None),
    ],
)
def test_fresh_work_lifecycle_has_one_truth_and_expected_edge(
    lifecycle: WorkLifecycle,
    expected_kind: TransitionKind | None,
) -> None:
    """A wrong initial lifecycle branch would duplicate or misclassify one work key."""
    fact = _work_fact(lifecycle)
    result = reduce_operator_state(
        empty_operator_state(),
        _batch(work_facts=(fact,)),
        clock=_clock(),
    )

    assert len(result.state.works) == 1
    assert result.state.works[0].key == fact.key
    assert result.state.works[0].lifecycle is lifecycle
    assert _event_kinds(result) == (() if expected_kind is None else (expected_kind,))


def test_lifecycle_transitions_emit_only_newer_provider_edges() -> None:
    """Terminal and idle edges must follow accepted provider transitions, not refreshes."""
    key = _work_key()
    active_mark = _watermark("event:001")
    active = reduce_operator_state(
        empty_operator_state(),
        _batch(
            watermark=active_mark,
            work_facts=(_work_fact(key=key, watermark=active_mark),),
        ),
        clock=_clock(),
    )

    idle_mark = _watermark("event:002", epoch=1_800_000_001.0)
    idle = reduce_operator_state(
        active.state,
        _batch(
            watermark=idle_mark,
            work_facts=(
                _work_fact(WorkLifecycle.IDLE, key=key, watermark=idle_mark),
            ),
        ),
        clock=_clock(wall=1_800_000_001.0, monotonic=101.0),
    )
    assert _event_kinds(idle) == (TransitionKind.BECAME_IDLE,)

    complete_mark = _watermark("event:003", epoch=1_800_000_002.0)
    completed = reduce_operator_state(
        idle.state,
        _batch(
            watermark=complete_mark,
            work_facts=(
                _work_fact(WorkLifecycle.COMPLETED, key=key, watermark=complete_mark),
            ),
        ),
        clock=_clock(wall=1_800_000_002.0, monotonic=102.0),
    )
    assert _event_kinds(completed) == (TransitionKind.COMPLETED,)
    assert InvalidationDomain.COMPLETION in completed.invalidations

    failed_mark = _watermark("event:004", epoch=1_800_000_003.0)
    failed = reduce_operator_state(
        completed.state,
        _batch(
            watermark=failed_mark,
            work_facts=(
                _work_fact(WorkLifecycle.FAILED, key=key, watermark=failed_mark),
            ),
        ),
        clock=_clock(wall=1_800_000_003.0, monotonic=103.0),
    )
    assert _event_kinds(failed) == (TransitionKind.FAILED,)


def test_request_opens_and_only_newer_explicit_provider_fact_resolves_it() -> None:
    """Absence, refresh, or local presentation state must never resolve a live ask."""
    state, initial_batch, _, request_key = _initial_active_request()
    request = state.requests[0]

    assert request.key == request_key
    assert request.phase is RequestPhase.LIVE_UNACKNOWLEDGED
    assert request.acknowledgement_eligibility is AcknowledgementEligibility.ELIGIBLE

    absence_mark = _watermark("event:002", epoch=1_800_000_010.0)
    absence = reduce_operator_state(
        state,
        _batch(watermark=absence_mark),
        clock=_clock(wall=1_800_000_010.0, monotonic=110.0),
    )
    assert absence.state.requests[0].phase is RequestPhase.LIVE_UNACKNOWLEDGED
    assert TransitionKind.REQUEST_RESOLVED not in _event_kinds(absence)

    duplicate = reduce_operator_state(
        absence.state,
        initial_batch,
        clock=_clock(wall=1_800_000_010.0, monotonic=110.0),
    )
    assert duplicate.state.requests[0].phase is RequestPhase.LIVE_UNACKNOWLEDGED

    resolved_mark = _watermark("event:003", epoch=1_800_000_011.0)
    resolved = reduce_operator_state(
        duplicate.state,
        _batch(
            watermark=resolved_mark,
            request_facts=(
                _request_fact(
                    ProviderRequestState.RESOLVED,
                    key=request_key,
                    watermark=resolved_mark,
                ),
            ),
        ),
        clock=_clock(wall=1_800_000_011.0, monotonic=111.0),
    )
    assert resolved.state.requests[0].phase is RequestPhase.RESOLVED
    assert resolved.state.requests[0].acknowledgement_eligibility is (
        AcknowledgementEligibility.RESOLVED
    )
    assert _event_kinds(resolved) == (TransitionKind.REQUEST_RESOLVED,)


def test_acknowledgement_is_reversible_presentation_state_and_never_an_edge() -> None:
    """Treating acknowledgement as provider truth would irreversibly hide a live ask."""
    state, batch, _, request_key = _initial_active_request()
    progressed = reduce_operator_state(
        state,
        batch,
        clock=_clock(wall=1_800_000_010.0, monotonic=110.0),
    )
    assert progressed.state.requests[0].eligible_elapsed_seconds == 10.0

    acknowledged = reduce_operator_state(
        progressed.state,
        batch,
        clock=_clock(wall=1_800_000_020.0, monotonic=120.0),
        acknowledged_requests=frozenset({request_key}),
    )
    assert acknowledged.state.requests[0].phase is RequestPhase.LIVE_ACKNOWLEDGED
    assert acknowledged.state.requests[0].eligible_elapsed_seconds == 10.0
    assert acknowledged.state.requests[0].acknowledgement_eligibility is (
        AcknowledgementEligibility.ALREADY_ACKNOWLEDGED
    )
    assert acknowledged.events == ()
    assert acknowledged.invalidations == frozenset({InvalidationDomain.MAILBOX})

    reversed_ack = reduce_operator_state(
        acknowledged.state,
        batch,
        clock=_clock(wall=1_800_000_030.0, monotonic=130.0),
    )
    assert reversed_ack.state.requests[0].phase is RequestPhase.LIVE_UNACKNOWLEDGED
    assert reversed_ack.state.requests[0].eligible_elapsed_seconds == 10.0
    assert reversed_ack.events == ()


def test_duplicate_older_and_equal_losing_watermarks_do_not_change_truth_or_emit() -> None:
    """A refresh generation or older event must not overwrite current provider truth."""
    key = _work_key()
    current_mark = _watermark("event:b", epoch=1_800_000_010.0, rank=20)
    current = reduce_operator_state(
        empty_operator_state(),
        _batch(
            watermark=current_mark,
            work_facts=(_work_fact(key=key, watermark=current_mark),),
        ),
        clock=_clock(wall=1_800_000_010.0),
    )

    for losing_mark in (
        current_mark,
        _watermark("event:z", epoch=1_800_000_009.0, rank=255),
        _watermark("event:a", epoch=1_800_000_010.0, rank=20),
        _watermark("event:z", epoch=1_800_000_010.0, rank=19),
    ):
        losing = reduce_operator_state(
            current.state,
            _batch(
                watermark=losing_mark,
                work_facts=(
                    _work_fact(WorkLifecycle.FAILED, key=key, watermark=losing_mark),
                ),
            ),
            clock=_clock(wall=1_800_000_010.0),
        )
        assert losing.state.works[0].lifecycle is WorkLifecycle.ACTIVE
        assert losing.events == ()


def test_equal_time_rank_then_event_token_selects_one_deterministic_truth() -> None:
    """Ignoring adapter rank or token ordering would make tuple arrival order semantic."""
    key = _work_key()
    low = _watermark("event:a", rank=1)
    first = reduce_operator_state(
        empty_operator_state(),
        _batch(watermark=low, work_facts=(_work_fact(key=key, watermark=low),)),
        clock=_clock(),
    )

    high_rank = _watermark("event:a", rank=2)
    ranked = reduce_operator_state(
        first.state,
        _batch(
            watermark=high_rank,
            work_facts=(
                _work_fact(WorkLifecycle.COMPLETED, key=key, watermark=high_rank),
            ),
        ),
        clock=_clock(),
    )
    assert ranked.state.works[0].lifecycle is WorkLifecycle.COMPLETED

    high_token = _watermark("event:b", rank=2)
    tokened = reduce_operator_state(
        ranked.state,
        _batch(
            watermark=high_token,
            work_facts=(
                _work_fact(WorkLifecycle.FAILED, key=key, watermark=high_token),
            ),
        ),
        clock=_clock(),
    )
    assert tokened.state.works[0].lifecycle is WorkLifecycle.FAILED


def test_fresh_high_authority_truth_rejects_newer_fallback_but_accepts_later_direct() -> None:
    """A newer transcript fallback must not overwrite fresh authoritative lifecycle."""
    key = _work_key()
    authoritative_mark = _watermark("event:001")
    authoritative = reduce_operator_state(
        empty_operator_state(),
        _batch(
            watermark=authoritative_mark,
            work_facts=(_work_fact(key=key, watermark=authoritative_mark),),
            authority=ObservationAuthority.AUTHORITATIVE_PROVIDER,
        ),
        clock=_clock(),
    )

    fallback_mark = _watermark("event:010", epoch=1_800_000_010.0)
    fallback = reduce_operator_state(
        authoritative.state,
        _batch(
            watermark=fallback_mark,
            work_facts=(
                _work_fact(WorkLifecycle.COMPLETED, key=key, watermark=fallback_mark),
            ),
            authority=ObservationAuthority.FALLBACK_OBSERVATION,
            health=SourceHealth.PARTIAL,
        ),
        clock=_clock(wall=1_800_000_010.0, monotonic=110.0),
    )
    assert fallback.state.works[0].lifecycle is WorkLifecycle.ACTIVE
    assert fallback.state.works[0].watermark == authoritative_mark
    assert fallback.state.works[0].source_health is SourceHealth.PARTIAL
    assert TransitionKind.COMPLETED not in _event_kinds(fallback)

    direct_mark = _watermark("event:005", epoch=1_800_000_005.0)
    direct = reduce_operator_state(
        fallback.state,
        _batch(
            watermark=direct_mark,
            work_facts=(
                _work_fact(WorkLifecycle.COMPLETED, key=key, watermark=direct_mark),
            ),
            authority=ObservationAuthority.AUTHORITATIVE_PROVIDER,
        ),
        clock=_clock(wall=1_800_000_011.0, monotonic=111.0),
    )
    assert direct.state.works[0].lifecycle is WorkLifecycle.COMPLETED
    assert set(_event_kinds(direct)) == {
        TransitionKind.COMPLETED,
        TransitionKind.SOURCE_RECOVERED,
    }


def test_request_only_truth_retains_authority_against_newer_fallback_resolution() -> None:
    """Dropping request authority would let fallback evidence resolve an authoritative ask."""
    request_key = _request_key()
    live_mark = _watermark("event:001")
    authoritative = reduce_operator_state(
        empty_operator_state(),
        _batch(
            watermark=live_mark,
            request_facts=(_request_fact(key=request_key, watermark=live_mark),),
            authority=ObservationAuthority.AUTHORITATIVE_PROVIDER,
        ),
        clock=_clock(),
    )
    assert authoritative.state.requests[0].phase is RequestPhase.LIVE_UNACKNOWLEDGED

    resolved_mark = _watermark("event:002", epoch=1_800_000_001.0)
    fallback = reduce_operator_state(
        authoritative.state,
        _batch(
            watermark=resolved_mark,
            request_facts=(
                _request_fact(
                    ProviderRequestState.RESOLVED,
                    key=request_key,
                    watermark=resolved_mark,
                ),
            ),
            authority=ObservationAuthority.FALLBACK_OBSERVATION,
        ),
        clock=_clock(wall=1_800_000_001.0, monotonic=101.0),
    )

    assert fallback.state.requests[0].phase is RequestPhase.LIVE_UNACKNOWLEDGED
    assert fallback.state.requests[0].watermark == live_mark
    assert TransitionKind.REQUEST_RESOLVED not in _event_kinds(fallback)


def test_sources_reduce_independently_and_outputs_are_stably_sorted() -> None:
    """Cross-source comparison or arrival ordering would let one provider suppress another."""
    source_b = _source("local:02", provider="claude")
    source_a = _source("local:01")
    key_b = _work_key("work:02", source=source_b)
    key_a = _work_key("work:01", source=source_a)
    mark_b = _watermark("event:002", source=source_b)
    mark_a = _watermark("event:001", source=source_a)

    with_b = reduce_operator_state(
        empty_operator_state(),
        _batch(
            source=source_b,
            watermark=mark_b,
            work_facts=(
                ProviderWorkFact(
                    key_b,
                    WorkLifecycle.FAILED,
                    mark_b,
                    "Claude work:02",
                    None,
                    NextActor.NONE,
                ),
            ),
        ),
        clock=_clock(),
    )
    with_both = reduce_operator_state(
        with_b.state,
        _batch(
            source=source_a,
            watermark=mark_a,
            work_facts=(_work_fact(key=key_a, watermark=mark_a),),
        ),
        clock=_clock(),
    )

    assert tuple(work.key for work in with_both.state.works) == (key_b, key_a)
    assert tuple(work.lifecycle for work in with_both.state.works) == (
        WorkLifecycle.FAILED,
        WorkLifecycle.ACTIVE,
    )
    assert tuple(source for source, _ in with_both.state.source_watermarks) == (
        source_b,
        source_a,
    )


def test_one_stale_source_does_not_block_a_fresh_sibling_source() -> None:
    """A source-scoped uncertainty lease must not stall independent provider truth."""
    source_a = _source("local:01")
    source_b = _source("local:02", provider="claude")
    key_a = _work_key("work:01", source=source_a)
    mark_a = _watermark("event:a1", source=source_a)
    active_a = reduce_operator_state(
        empty_operator_state(),
        _batch(
            source=source_a,
            watermark=mark_a,
            work_facts=(_work_fact(key=key_a, watermark=mark_a),),
        ),
        clock=_clock(),
    )
    stale_mark = _watermark("event:a2", source=source_a, epoch=1_800_000_001.0)
    stale_a = reduce_operator_state(
        active_a.state,
        _batch(
            source=source_a,
            watermark=stale_mark,
            health=SourceHealth.UNAVAILABLE,
            freshness=SourceFreshness.UNAVAILABLE,
        ),
        clock=_clock(wall=1_800_000_001.0, monotonic=101.0),
    )

    key_b = _work_key("work:02", source=source_b)
    mark_b = _watermark("event:b1", source=source_b, epoch=1_800_000_002.0)
    fresh_b = reduce_operator_state(
        stale_a.state,
        _batch(
            source=source_b,
            watermark=mark_b,
            work_facts=(
                ProviderWorkFact(
                    key_b,
                    WorkLifecycle.ACTIVE,
                    mark_b,
                    "Claude work:02",
                    None,
                    NextActor.PROVIDER,
                ),
            ),
        ),
        clock=_clock(wall=1_800_000_002.0, monotonic=102.0),
    )

    truth_by_key = {work.key: work for work in fresh_b.state.works}
    assert truth_by_key[key_a].source_freshness is SourceFreshness.UNAVAILABLE
    assert truth_by_key[key_b].lifecycle is WorkLifecycle.ACTIVE
    assert TransitionKind.BECAME_ACTIVE in _event_kinds(fresh_b)
    assert source_a in fresh_b.state.timing_uncertain_sources
    assert source_b not in fresh_b.state.timing_uncertain_sources


def _two_active_request_sources() -> tuple[
    CanonicalOperatorState,
    SourceKey,
    RequestKey,
    SourceKey,
    RequestKey,
]:
    state_a, _, _, request_a = _initial_active_request()
    source_a = request_a.work_key.source_key
    source_b = _source("local:02", provider="claude")
    work_b = _work_key("work:02", source=source_b)
    request_b = _request_key("request:02", work_key=work_b)
    mark_b = _watermark("event:b1", source=source_b, epoch=1_800_000_001.0)
    state_b = reduce_operator_state(
        state_a,
        _batch(
            source=source_b,
            watermark=mark_b,
            work_facts=(
                ProviderWorkFact(
                    work_b,
                    WorkLifecycle.ACTIVE,
                    mark_b,
                    "Claude work:02",
                    None,
                    NextActor.PROVIDER,
                ),
            ),
            request_facts=(_request_fact(key=request_b, watermark=mark_b),),
        ),
        clock=_clock(wall=1_800_000_001.0, monotonic=101.0),
    ).state
    return state_b, source_a, request_a, source_b, request_b


def test_staggered_source_loss_uses_independent_uncertainty_leases() -> None:
    """A later source loss must not inherit an earlier source's expiry deadline."""
    state, source_a, request_a, source_b, request_b = _two_active_request_sources()
    stale_a_mark = _watermark("event:a2", source=source_a, epoch=1_800_000_010.0)
    stale_a = reduce_operator_state(
        state,
        _batch(
            source=source_a,
            watermark=stale_a_mark,
            health=SourceHealth.UNAVAILABLE,
            freshness=SourceFreshness.UNAVAILABLE,
        ),
        clock=_clock(wall=1_800_000_010.0, monotonic=110.0),
    )
    stale_b_mark = _watermark("event:b2", source=source_b, epoch=1_800_000_900.0)
    stale_b = reduce_operator_state(
        stale_a.state,
        _batch(
            source=source_b,
            watermark=stale_b_mark,
            health=SourceHealth.UNAVAILABLE,
            freshness=SourceFreshness.UNAVAILABLE,
        ),
        clock=_clock(wall=1_800_000_900.0, monotonic=1_000.0),
    )
    expired_a = reduce_operator_state(
        stale_b.state,
        _batch(
            source=source_a,
            watermark=stale_a_mark,
            health=SourceHealth.UNAVAILABLE,
            freshness=SourceFreshness.UNAVAILABLE,
        ),
        clock=_clock(wall=1_800_003_610.0, monotonic=3_710.0),
    )

    requests = {request.key: request for request in expired_a.state.requests}
    assert requests[request_a].phase is RequestPhase.UNKNOWN_EXPIRED
    assert requests[request_b].phase is RequestPhase.STALE_HOLD


def test_each_uncertain_source_requires_two_current_recovery_samples() -> None:
    """Recovery confirmations from one source must not release a sibling source."""
    state, source_a, request_a, source_b, request_b = _two_active_request_sources()
    stale_a_mark = _watermark("event:a2", source=source_a, epoch=1_800_000_010.0)
    stale_a = reduce_operator_state(
        state,
        _batch(
            source=source_a,
            watermark=stale_a_mark,
            health=SourceHealth.UNAVAILABLE,
            freshness=SourceFreshness.UNAVAILABLE,
        ),
        clock=_clock(wall=1_800_000_010.0, monotonic=110.0),
    )
    stale_b_mark = _watermark("event:b2", source=source_b, epoch=1_800_000_011.0)
    both_stale = reduce_operator_state(
        stale_a.state,
        _batch(
            source=source_b,
            watermark=stale_b_mark,
            health=SourceHealth.UNAVAILABLE,
            freshness=SourceFreshness.UNAVAILABLE,
        ),
        clock=_clock(wall=1_800_000_011.0, monotonic=111.0),
    )

    fresh_a_mark = _watermark("event:a3", source=source_a, epoch=1_800_000_012.0)
    first_a = reduce_operator_state(
        both_stale.state,
        _batch(source=source_a, watermark=fresh_a_mark),
        clock=_clock(wall=1_800_000_012.0, monotonic=112.0),
    )
    second_a = reduce_operator_state(
        first_a.state,
        _batch(source=source_a, watermark=fresh_a_mark),
        clock=_clock(wall=1_800_000_013.0, monotonic=113.0),
    )
    after_a = {request.key: request for request in second_a.state.requests}
    assert after_a[request_a].phase is RequestPhase.LIVE_UNACKNOWLEDGED
    assert after_a[request_b].phase is RequestPhase.STALE_HOLD

    fresh_b_mark = _watermark("event:b3", source=source_b, epoch=1_800_000_014.0)
    first_b = reduce_operator_state(
        second_a.state,
        _batch(source=source_b, watermark=fresh_b_mark),
        clock=_clock(wall=1_800_000_014.0, monotonic=114.0),
    )
    assert {
        request.key: request.phase for request in first_b.state.requests
    }[request_b] is RequestPhase.STALE_HOLD

    second_b = reduce_operator_state(
        first_b.state,
        _batch(source=source_b, watermark=fresh_b_mark),
        clock=_clock(wall=1_800_000_015.0, monotonic=115.0),
    )
    assert {
        request.key: request.phase for request in second_b.state.requests
    }[request_b] is RequestPhase.LIVE_UNACKNOWLEDGED


def test_parent_cycles_are_removed_without_changing_deterministic_row_order() -> None:
    """A provider parent cycle must not create recursive or order-dependent state."""
    key_a = _work_key("work:a")
    key_b = _work_key("work:b")
    mark_a = _watermark("event:a")
    mark_b = _watermark("event:b")
    fact_a = _work_fact(key=key_a, watermark=mark_a, parent_key=key_b)
    fact_b = _work_fact(key=key_b, watermark=mark_b, parent_key=key_a)
    batch_ab = _batch(
        watermark=mark_b,
        work_facts=(fact_a, fact_b),
    )
    batch_ba = _batch(
        watermark=mark_b,
        work_facts=(fact_b, fact_a),
    )

    first = reduce_operator_state(empty_operator_state(), batch_ab, clock=_clock())
    second = reduce_operator_state(empty_operator_state(), batch_ba, clock=_clock())

    assert first == second
    assert tuple(work.key for work in first.state.works) == (key_a, key_b)
    assert all(work.parent_key is None for work in first.state.works)
    assert any(item.identifier.value == "parent_cycle_removed" for item in first.diagnostics)


def test_unknown_request_is_retained_but_never_actionable_or_cued() -> None:
    """Unknown provider request semantics must not manufacture an actionable ask."""
    key = _request_key()
    mark = _watermark()
    result = reduce_operator_state(
        empty_operator_state(),
        _batch(
            watermark=mark,
            request_facts=(
                _request_fact(
                    ProviderRequestState.UNKNOWN,
                    key=key,
                    watermark=mark,
                    request_kind=RequestKind.UNKNOWN,
                    next_actor=NextActor.UNKNOWN,
                ),
            ),
        ),
        clock=_clock(),
    )

    assert result.state.requests[0].phase is RequestPhase.UNKNOWN_EXPIRED
    assert result.state.requests[0].acknowledgement_eligibility is (
        AcknowledgementEligibility.NOT_ACTIONABLE
    )
    assert TransitionKind.REQUEST_OPENED not in _event_kinds(result)


def test_restored_truth_is_stale_and_corroboration_never_replays_edges() -> None:
    """Restoring an active ask must not replay lifecycle or notification cues."""
    work_key = _work_key()
    request_key = _request_key(work_key=work_key)
    mark = _watermark()
    restored_batch = _batch(
        watermark=mark,
        work_facts=(_work_fact(key=work_key, watermark=mark),),
        request_facts=(_request_fact(key=request_key, watermark=mark),),
        authority=ObservationAuthority.RESTORED_LAST_KNOWN,
        freshness=SourceFreshness.RESTORED,
    )
    restored = reduce_operator_state(
        empty_operator_state(),
        restored_batch,
        clock=_clock(),
    )

    assert restored.events == ()
    assert restored.state.works[0].source_freshness is SourceFreshness.RESTORED
    assert restored.state.requests[0].phase is RequestPhase.STALE_HOLD

    current_batch = _batch(
        watermark=mark,
        work_facts=(_work_fact(key=work_key, watermark=mark),),
        request_facts=(_request_fact(key=request_key, watermark=mark),),
        authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    )
    first_confirmation = reduce_operator_state(
        restored.state,
        current_batch,
        clock=_clock(wall=1_800_000_001.0, monotonic=101.0),
    )
    recovered = reduce_operator_state(
        first_confirmation.state,
        current_batch,
        clock=_clock(wall=1_800_000_002.0, monotonic=102.0),
    )

    assert recovered.state.clock_continuity.status is ClockContinuityStatus.STABLE
    assert recovered.state.requests[0].phase is RequestPhase.LIVE_UNACKNOWLEDGED
    assert TransitionKind.BECAME_ACTIVE not in _event_kinds(recovered)
    assert TransitionKind.REQUEST_OPENED not in _event_kinds(recovered)


def test_stale_source_holds_request_freezes_elapsed_and_expires_unknown() -> None:
    """Source loss must not keep escalating or synthesize provider resolution."""
    state, batch, _, request_key = _initial_active_request()
    progressed = reduce_operator_state(
        state,
        batch,
        clock=_clock(wall=1_800_000_010.0, monotonic=110.0),
    )
    stale_mark = _watermark("event:002", epoch=1_800_000_011.0)
    stale_batch = _batch(
        watermark=stale_mark,
        health=SourceHealth.UNAVAILABLE,
        freshness=SourceFreshness.UNAVAILABLE,
    )
    stale = reduce_operator_state(
        progressed.state,
        stale_batch,
        clock=_clock(wall=1_800_000_011.0, monotonic=111.0),
        acknowledged_requests=frozenset({request_key}),
    )

    assert stale.state.requests[0].phase is RequestPhase.STALE_HOLD
    assert stale.state.requests[0].eligible_elapsed_seconds == 10.0
    assert stale.state.requests[0].acknowledgement_eligibility is (
        AcknowledgementEligibility.STALE_HOLD
    )
    assert TransitionKind.REQUEST_OPENED not in _event_kinds(stale)
    assert TransitionKind.REQUEST_RESOLVED not in _event_kinds(stale)

    expired = reduce_operator_state(
        stale.state,
        stale_batch,
        clock=_clock(
            wall=1_800_000_011.0 + TIMING_UNCERTAINTY_LEASE_SECONDS,
            monotonic=111.0 + TIMING_UNCERTAINTY_LEASE_SECONDS,
        ),
    )
    assert expired.state.requests[0].phase is RequestPhase.UNKNOWN_EXPIRED
    assert expired.state.requests[0].watermark == batch.request_facts[0].watermark
    assert expired.state.source_watermarks
    assert TransitionKind.REQUEST_RESOLVED not in _event_kinds(expired)


@pytest.mark.parametrize(
    "discontinuous_clock",
    [
        _clock(wall=1_800_000_100.0, monotonic=101.0),
        _clock(wall=1_799_999_999.0, monotonic=101.0),
        _clock(wall=1_800_000_001.0, monotonic=99.0),
        _clock(wall=1_800_000_001.0, monotonic=1.0, boot="boot:02"),
    ],
)
def test_clock_discontinuity_quarantines_truth_without_terminal_or_resolution_edges(
    discontinuous_clock: ClockSample,
) -> None:
    """A wall, monotonic, or boot discontinuity must not manufacture semantic truth."""
    state, _, work_key, request_key = _initial_active_request()
    future_mark = _watermark("event:002", epoch=1_800_000_001.0)
    result = reduce_operator_state(
        state,
        _batch(
            watermark=future_mark,
            work_facts=(
                _work_fact(WorkLifecycle.COMPLETED, key=work_key, watermark=future_mark),
            ),
            request_facts=(
                _request_fact(
                    ProviderRequestState.RESOLVED,
                    key=request_key,
                    watermark=future_mark,
                ),
            ),
        ),
        clock=discontinuous_clock,
    )

    assert result.state.clock_continuity.status is ClockContinuityStatus.UNCERTAIN
    assert result.state.works[0].lifecycle is WorkLifecycle.ACTIVE
    assert result.state.works[0].timing_uncertain is True
    assert result.state.requests[0].phase is RequestPhase.STALE_HOLD
    assert not {
        TransitionKind.COMPLETED,
        TransitionKind.FAILED,
        TransitionKind.REQUEST_RESOLVED,
    }.intersection(_event_kinds(result))


def test_consistent_sleep_length_wall_and_monotonic_advance_remains_stable() -> None:
    """Treating ordinary sleep-length monotonic advance as rollback would stall truth."""
    state, batch, _, _ = _initial_active_request()
    slept = reduce_operator_state(
        state,
        batch,
        clock=_clock(wall=1_800_003_600.0, monotonic=3_700.0),
    )

    assert slept.state.clock_continuity.status is ClockContinuityStatus.STABLE
    assert slept.state.requests[0].phase is RequestPhase.LIVE_UNACKNOWLEDGED
    assert slept.state.requests[0].eligible_elapsed_seconds == 3_600.0


def test_future_dated_fact_is_quarantined_instead_of_becoming_newest() -> None:
    """A future provider timestamp must not skip over the retained source watermark."""
    state, _, work_key, _ = _initial_active_request()
    future_mark = _watermark("event:future", epoch=1_800_010_000.0)
    quarantined = reduce_operator_state(
        state,
        _batch(
            watermark=future_mark,
            work_facts=(
                _work_fact(WorkLifecycle.COMPLETED, key=work_key, watermark=future_mark),
            ),
            observed_at=1_800_010_000.0,
        ),
        clock=_clock(wall=1_800_000_001.0, monotonic=101.0),
    )

    assert quarantined.state.works[0].lifecycle is WorkLifecycle.ACTIVE
    assert quarantined.state.source_watermarks[0][1].event_token == EventToken("event:001")
    assert quarantined.state.clock_continuity.status is ClockContinuityStatus.UNCERTAIN
    assert any(
        item.identifier.value == "future_fact_quarantined"
        for item in quarantined.diagnostics
    )


def test_two_current_samples_recover_clock_without_replaying_retained_event() -> None:
    """One clean sample is insufficient, while recovery must not repeat an active cue."""
    state, batch, _, _ = _initial_active_request()
    jumped = reduce_operator_state(
        state,
        batch,
        clock=_clock(wall=1_800_000_100.0, monotonic=101.0),
    )
    assert jumped.state.clock_continuity.recovery_confirmations == 0

    first = reduce_operator_state(
        jumped.state,
        batch,
        clock=_clock(wall=1_800_000_101.0, monotonic=102.0),
    )
    assert first.state.clock_continuity.status is ClockContinuityStatus.UNCERTAIN
    assert first.state.clock_continuity.recovery_confirmations == 1

    second = reduce_operator_state(
        first.state,
        batch,
        clock=_clock(wall=1_800_000_102.0, monotonic=103.0),
    )
    assert second.state.clock_continuity.status is ClockContinuityStatus.STABLE
    assert second.state.clock_continuity.recovery_confirmations == TIMING_RECOVERY_CONFIRMATIONS
    assert second.state.requests[0].phase is RequestPhase.LIVE_UNACKNOWLEDGED
    assert TransitionKind.BECAME_ACTIVE not in _event_kinds(second)
    assert TransitionKind.REQUEST_OPENED not in _event_kinds(second)


def test_one_rollback_preserves_the_maximum_bounded_work_set() -> None:
    """Clock rollback must not bulk-prune all retained work as apparently future dated."""
    source = _source()
    facts = tuple(
        _work_fact(
            key=_work_key(f"work:{index:04d}", source=source),
            watermark=_watermark(f"event:{index:04d}", source=source),
        )
        for index in range(MAX_CANONICAL_WORKS)
    )
    state = reduce_operator_state(
        empty_operator_state(),
        _batch(source=source, work_facts=facts),
        clock=_clock(),
    ).state
    assert len(state.works) == MAX_CANONICAL_WORKS

    rolled_back = reduce_operator_state(
        state,
        _batch(source=source),
        clock=_clock(wall=1_799_999_000.0, monotonic=101.0),
    )
    assert len(rolled_back.state.works) == MAX_CANONICAL_WORKS
    assert all(work.timing_uncertain for work in rolled_back.state.works)


@pytest.mark.parametrize(
    ("wall", "monotonic"),
    [
        (nan, 1.0),
        (inf, 1.0),
        (-1.0, 1.0),
        (1.0, nan),
        (1.0, inf),
        (1.0, -1.0),
        (True, 1.0),
        (1.0, True),
    ],
)
def test_clock_samples_reject_nonfinite_negative_and_boolean_values(
    wall: object,
    monotonic: object,
) -> None:
    """Malformed numeric samples must fail closed before continuity arithmetic."""
    with pytest.raises(OperatorStateValidationError, match="invalid clock sample"):
        ClockSample(wall, monotonic, BootIdentifier("boot:01"))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    ["", "boot identity", "/private/boot", "boot\n01", "x" * 65],
)
def test_boot_identifier_is_bounded_opaque_and_content_free(value: str) -> None:
    """Display or path text in boot identity would create persisted private linkage."""
    with pytest.raises(OperatorStateValidationError, match="invalid boot identifier"):
        BootIdentifier(value)


def test_clock_and_state_records_are_immutable() -> None:
    """Mutation would make equal reductions depend on object sharing and call order."""
    clock = _clock()
    state = empty_operator_state()
    with pytest.raises(FrozenInstanceError):
        clock.wall_epoch = 1.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        state.generation = 2  # type: ignore[misc]


def test_semantic_event_key_codec_is_exact_content_free_and_round_trips() -> None:
    """A lossy or additive event codec would collapse delivery idempotency identities."""
    request_key = _request_key("request:x:y", work_key=_work_key("work:a:b"))
    watermark = _watermark("event:z:y")
    key = SemanticEventKey(
        request_key,
        TransitionKind.REQUEST_OPENED,
        watermark,
    )
    payload = {
        "version": {"major": 1, "minor": 0},
        "subject_kind": "request",
        "subject_key": {
            "version": {"major": 1, "minor": 0},
            "provider_id": "codex",
            "adapter_id": "hooks",
            "source_instance_id": "local:01",
            "capability_id": "live_agent_events",
            "work_id": "work:a:b",
            "request_id": "request:x:y",
        },
        "transition_kind": "request_opened",
        "provider_watermark": {
            "basis": "provider_event_id",
            "occurred_at_epoch": 1_800_000_000.0,
            "event_token": "event:z:y",
            "sequence": None,
            "tie_break_rank": 10,
        },
    }

    assert semantic_event_key_to_payload(key) == payload
    assert semantic_event_key_from_payload(payload) == key


class _ExplosiveDict(dict[object, object]):
    def __iter__(self):  # type: ignore[no-untyped-def]
        raise AssertionError("mapping subclass was executed")

    def __getitem__(self, key: object) -> object:
        raise AssertionError("mapping subclass value was read")


def test_semantic_event_key_decoder_rejects_nonexact_and_executable_shapes() -> None:
    """Permissive event decoding could execute mappings or smuggle private fields."""
    key = SemanticEventKey(
        _work_key(),
        TransitionKind.BECAME_ACTIVE,
        _watermark(),
    )
    valid = semantic_event_key_to_payload(key)
    missing = dict(valid)
    missing.pop("transition_kind")
    wrong_version = {**valid, "version": {"major": 2, "minor": 0}}
    boolean_version = {**valid, "version": {"major": True, "minor": 0}}
    extra = {**valid, "prompt": "private"}

    for payload in (
        missing,
        wrong_version,
        boolean_version,
        extra,
        _ExplosiveDict(valid),
        {**valid, "subject_key": _ExplosiveDict()},
        {**valid, "provider_watermark": _ExplosiveDict()},
        [valid],
        None,
    ):
        assert semantic_event_key_from_payload(payload) is None


def test_semantic_event_key_refuses_cross_source_subject_and_watermark() -> None:
    """A cross-source edge could acknowledge or deliver for the wrong provider source."""
    with pytest.raises(OperatorStateValidationError, match="invalid semantic event key"):
        SemanticEventKey(
            _work_key(source=_source("local:01")),
            TransitionKind.BECAME_ACTIVE,
            _watermark(source=_source("local:02")),
        )


def test_request_and_work_outputs_remain_unique_sorted_and_bounded() -> None:
    """Sequential source publications must not grow canonical output without limit."""
    source = _source()
    first_work_facts = tuple(
        _work_fact(
            key=_work_key(f"work:b{index:04d}", source=source),
            watermark=_watermark(f"event:b{index:04d}", source=source),
        )
        for index in range(MAX_CANONICAL_WORKS)
    )
    first_request_facts = tuple(
        _request_fact(
            key=_request_key(
                f"request:b{index:04d}",
                work_key=first_work_facts[index].key,
            ),
            watermark=_watermark(f"event:r{index:04d}", source=source),
        )
        for index in range(MAX_CANONICAL_REQUESTS)
    )
    first = reduce_operator_state(
        empty_operator_state(),
        _batch(
            source=source,
            watermark=_watermark("event:z", source=source, rank=255),
            work_facts=first_work_facts,
            request_facts=first_request_facts,
        ),
        clock=_clock(),
    )

    next_mark = _watermark("event:zz", source=source, epoch=1_800_000_001.0)
    added_work = _work_fact(
        key=_work_key("work:a0000", source=source),
        watermark=next_mark,
    )
    added_request = _request_fact(
        key=_request_key("request:a0000", work_key=added_work.key),
        watermark=next_mark,
    )
    second = reduce_operator_state(
        first.state,
        _batch(
            source=source,
            watermark=next_mark,
            work_facts=(added_work,),
            request_facts=(added_request,),
        ),
        clock=_clock(wall=1_800_000_001.0, monotonic=101.0),
    )

    work_keys = tuple(work.key for work in second.state.works)
    request_keys = tuple(request.key for request in second.state.requests)
    assert len(work_keys) == MAX_CANONICAL_WORKS
    assert len(request_keys) == MAX_CANONICAL_REQUESTS
    assert work_keys == tuple(sorted(set(work_keys)))
    assert request_keys == tuple(sorted(set(request_keys)))
    assert len(second.events) <= MAX_EVENTS_PER_REDUCTION


def test_reducer_rejects_noncanonical_inputs_without_executing_subclasses() -> None:
    """Reducer boundaries must reject collection subclasses before iterating them."""
    state, batch, _, _ = _initial_active_request()
    with pytest.raises(OperatorStateValidationError, match="invalid acknowledged requests"):
        reduce_operator_state(
            state,
            batch,
            clock=_clock(),
            acknowledged_requests=frozenset({"request:01"}),  # type: ignore[arg-type]
        )
    with pytest.raises(OperatorStateValidationError, match="invalid operator state"):
        reduce_operator_state(object(), batch, clock=_clock())  # type: ignore[arg-type]


def test_declared_clock_and_output_bounds_are_exact() -> None:
    """Changing safety bounds would weaken reviewed chaos and memory guarantees."""
    assert MAX_CLOCK_DELTA_DIVERGENCE_SECONDS == 5.0
    assert TIMING_UNCERTAINTY_LEASE_SECONDS == 3_600.0
    assert TIMING_RECOVERY_CONFIRMATIONS == 2
    assert MAX_CANONICAL_WORKS == 1_000
    assert MAX_CANONICAL_REQUESTS == 1_000
    assert MAX_EVENTS_PER_REDUCTION == 2_000


def test_day_old_work_is_retired_from_the_canonical_catalog() -> None:
    """Without an age bound the catalog kept every session ever seen, and
    a days-old work with no Stop sat in the Agent Browser as "active"
    forever. A later batch from ANY source retires works whose newest
    event is older than CANONICAL_WORK_RETENTION_SECONDS."""
    from sidepulse.operator_state import CANONICAL_WORK_RETENTION_SECONDS

    state, _batch_used, work_key, request_key = _initial_active_request()
    assert any(work.key == work_key for work in state.works)

    later_wall = 1_800_000_000.0 + CANONICAL_WORK_RETENTION_SECONDS + 3_600.0
    other_source = _source("local:02")
    other_work_key = _work_key(value="work:02", source=other_source)
    other_watermark = _watermark(source=other_source, epoch=later_wall)
    fresh_batch = _batch(
        source=other_source,
        watermark=other_watermark,
        work_facts=(_work_fact(key=other_work_key, watermark=other_watermark),),
    )

    result = reduce_operator_state(
        state,
        fresh_batch,
        clock=_clock(wall=later_wall, monotonic=100.0 + (later_wall - 1_800_000_000.0)),
    )

    surviving = {work.key for work in result.state.works}
    assert other_work_key in surviving
    assert work_key not in surviving
    assert all(request.key != request_key for request in result.state.requests)


def test_retention_fires_even_while_dead_sources_hold_timing_quarantine() -> None:
    """Per-source timing quarantines linger forever for sources that never
    send again, so global continuity can sit UNCERTAIN indefinitely after
    a restart. Age retirement must not be held hostage by that -- only a
    genuinely distrusted clock (discontinuity / future facts) blocks it."""
    from sidepulse.operator_state import CANONICAL_WORK_RETENTION_SECONDS

    state, _batch_used, work_key, _request_key = _initial_active_request()

    # A clock jump quarantines every known source; the dead work's source
    # never sends again, so its timing entry can only age out via lease.
    jump_clock = _clock(wall=1_800_000_500.0, monotonic=90_000.0, boot="boot:02")
    jump_source = _source("local:03")
    jump_watermark = _watermark(source=jump_source, epoch=1_800_000_500.0)
    jump_batch = _batch(source=jump_source, watermark=jump_watermark)
    quarantined = reduce_operator_state(state, jump_batch, clock=jump_clock)
    assert quarantined.state.clock_continuity.status is ClockContinuityStatus.UNCERTAIN

    later_wall = 1_800_000_500.0 + CANONICAL_WORK_RETENTION_SECONDS + 3_600.0
    later_clock = _clock(
        wall=later_wall,
        monotonic=90_000.0 + (later_wall - 1_800_000_500.0),
        boot="boot:02",
    )
    later_watermark = _watermark(
        "event:900", source=jump_source, epoch=later_wall
    )
    later_work_key = _work_key(value="work:03", source=jump_source)
    later_batch = _batch(
        source=jump_source,
        watermark=later_watermark,
        work_facts=(_work_fact(key=later_work_key, watermark=later_watermark),),
    )
    result = reduce_operator_state(quarantined.state, later_batch, clock=later_clock)

    surviving = {work.key for work in result.state.works}
    assert work_key not in surviving


def test_quiescent_source_quarantines_expire_by_lease_on_any_reduction() -> None:
    """A restart quarantines every known source, but a dead session's
    source never sends again -- its timing entry used to hold global
    clock continuity at UNCERTAIN forever. A full quiet lease now clears
    quiescent entries on any other source's reduction, letting
    continuity recover to STABLE."""
    from sidepulse.operator_state import TIMING_UNCERTAINTY_LEASE_SECONDS

    state, _batch_used, _work_key_used, _request_key = _initial_active_request()

    # The clock jump (fresh boot id) quarantines the dead work's source
    # and the live source alike.
    live_source = _source("local:09")
    jump_clock = _clock(wall=1_800_000_500.0, monotonic=50.0, boot="boot:02")
    jump_batch = _batch(
        source=live_source,
        watermark=_watermark(source=live_source, epoch=1_800_000_500.0),
    )
    quarantined = reduce_operator_state(state, jump_batch, clock=jump_clock)
    assert quarantined.state.clock_continuity.status is ClockContinuityStatus.UNCERTAIN

    # After a full quiet lease, two fresh direct batches from the LIVE
    # source alone must recover continuity -- the dead source never
    # sends again and must not be able to veto recovery.
    base_wall = 1_800_000_500.0 + TIMING_UNCERTAINTY_LEASE_SECONDS + 60.0
    current = quarantined.state
    for offset in (0.0, 30.0):
        wall = base_wall + offset
        batch = _batch(
            source=live_source,
            watermark=_watermark(
                f"event:recover:{offset}", source=live_source, epoch=wall
            ),
        )
        result = reduce_operator_state(
            current,
            batch,
            clock=_clock(
                wall=wall,
                monotonic=50.0 + (wall - 1_800_000_500.0),
                boot="boot:02",
            ),
        )
        current = result.state

    assert current.clock_continuity.status is ClockContinuityStatus.STABLE
