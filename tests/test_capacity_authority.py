from __future__ import annotations

from dataclasses import replace
from itertools import permutations

import pytest

from sidepulse.capacity_authority import (
    MAX_CAPACITY_BINDING_AGE_SECONDS,
    classify_applicability,
    evaluate_lane_authority,
    project_source_health,
    select_binding_lanes,
)
from sidepulse.capacity_types import (
    CapacityAccountBinding,
    CapacityEvidenceClass,
    CapacitySnapshot,
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ExecutionContext,
    LaneApplicability,
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    ResetState,
    SourceHealthKind,
    SourceKey,
)

NOW = 1_000.0


def _source(
    provider_id: str = "codex", source_instance_id: str = "local:primary"
) -> SourceKey:
    return SourceKey(
        provider_id,
        "quota",
        source_instance_id,
        "capacity.v1",
    )


def _health(
    source: SourceKey,
    *,
    kind: SourceHealthKind = SourceHealthKind.HEALTHY,
    has_last_known_good: bool = False,
) -> CapacitySourceHealth:
    return CapacitySourceHealth(
        source=source,
        kind=kind,
        observed_at=NOW,
        last_attempt_at=NOW,
        retry_at=None,
        reason_code=None,
        has_last_known_good=has_last_known_good,
    )


def _lane(
    *,
    provider_id: str = "codex",
    source_instance_id: str = "local:primary",
    opaque_scope: str = "all",
    pool: str = "general",
    model: str | None = None,
    window: str = "session",
    effect: QuotaEffect = QuotaEffect.ALL_WORKLOADS,
    horizon: QuotaHorizon = QuotaHorizon.SHORT,
    remaining: float | None = 50.0,
    state: ObservationState = ObservationState.OBSERVED,
    reset_epoch: float | None = 2_000.0,
    reset_state: ResetState = ResetState.FUTURE,
    health_kind: SourceHealthKind = SourceHealthKind.HEALTHY,
) -> QuotaLaneObservation:
    source = _source(provider_id, source_instance_id)
    return QuotaLaneObservation(
        key=QuotaLaneKey(
            source=source,
            opaque_scope=opaque_scope,
            pool=pool,
            model=model,
            window=window,
            effect=effect,
        ),
        semantic_name="Session window" if horizon is QuotaHorizon.SHORT else "Weekly window",
        horizon=horizon,
        value=CapacityValue(CapacityUnit.PERCENT_REMAINING, remaining, state),
        reset=ResetFact(reset_state, reset_epoch, 300.0, NOW),
        observed_at=NOW,
        source_health=_health(
            source,
            kind=health_kind,
            has_last_known_good=state
            in {ObservationState.STALE, ObservationState.LAST_KNOWN_GOOD},
        ),
        account_discriminator=None,
    )


def _snapshot(*lanes: QuotaLaneObservation) -> CapacitySnapshot:
    health_by_source = {lane.source_health.source: lane.source_health for lane in lanes}
    return CapacitySnapshot(NOW, tuple(lanes), tuple(health_by_source.values()))


def _context(
    *,
    providers: tuple[str, ...] = ("codex",),
    instances: tuple[str, ...] = ("local:primary",),
    model: str | None = "gpt-5",
    feature: str | None = None,
) -> ExecutionContext:
    return ExecutionContext(providers, instances, model, feature)


def _binding(*, pool: str = "general", account: str = "acct:primary") -> CapacityAccountBinding:
    return CapacityAccountBinding(
        source=_source(),
        provider_id="codex",
        auth_mode="chatgpt-plan",
        opaque_account_id=account,
        pool_id=pool,
        evidence_class=CapacityEvidenceClass.OFFICIAL_LOCAL,
        observed_at=NOW,
    )


def test_authority_refuses_a_bound_lane_when_its_account_or_pool_differs() -> None:
    """Ignoring account or pool binding would release an unrelated account's quota."""
    lane = replace(_lane(), account_discriminator="acct:other")

    authority = evaluate_lane_authority(lane, _context(), NOW, binding=_binding())

    assert authority.bindable is False
    assert authority.refusal_code == "account_binding_mismatch"

    wrong_pool = evaluate_lane_authority(
        _lane(pool="api-org"), _context(), NOW, binding=_binding()
    )
    assert wrong_pool.bindable is False
    assert wrong_pool.refusal_code == "pool_binding_mismatch"


@pytest.mark.parametrize(
    "evidence_class",
    (CapacityEvidenceClass.UI_LINK_ONLY, CapacityEvidenceClass.UNSUPPORTED),
)
def test_authority_never_releases_a_non_observable_bound_source(
    evidence_class: CapacityEvidenceClass,
) -> None:
    """Treating a setup link as a measured source would fabricate capacity authority."""
    lane = replace(
        _lane(), account_discriminator="acct:primary", auth_mode="chatgpt-plan"
    )
    binding = replace(_binding(), evidence_class=evidence_class)

    authority = evaluate_lane_authority(lane, _context(), NOW, binding=binding)

    assert authority.bindable is False
    assert authority.refusal_code == "capacity_not_observable"


def test_compact_projection_does_not_release_an_unbound_account_lane() -> None:
    """Looking up bindings by source alone would merge two accounts in one source family."""
    primary = replace(
        _lane(window="primary"),
        account_discriminator="acct:primary",
        auth_mode="chatgpt-plan",
    )
    other = replace(
        _lane(window="other"),
        account_discriminator="acct:other",
        auth_mode="chatgpt-plan",
    )

    projection = select_binding_lanes(
        _snapshot(primary, other),
        _context(),
        NOW,
        bindings=(_binding(),),
    )

    assert [row.lane.key.window for row in projection.binding_lanes] == ["primary"]
    assert projection.detail_lanes[0].refusal_code == "account_binding_required"


def test_authority_requires_exact_auth_mode_and_fresh_binding_time() -> None:
    """Ignoring auth or stale binding time could release capacity from another plan."""
    lane = replace(_lane(), account_discriminator="acct:primary", auth_mode="api-organization")
    wrong_auth = evaluate_lane_authority(lane, _context(), NOW, binding=_binding())
    exact_boundary = evaluate_lane_authority(
        replace(lane, auth_mode="chatgpt-plan"),
        _context(),
        NOW,
        binding=replace(_binding(), observed_at=NOW - MAX_CAPACITY_BINDING_AGE_SECONDS),
    )
    stale = evaluate_lane_authority(
        replace(lane, auth_mode="chatgpt-plan"),
        _context(),
        NOW,
        binding=replace(_binding(), observed_at=NOW - MAX_CAPACITY_BINDING_AGE_SECONDS - 1.0),
    )
    future = evaluate_lane_authority(
        replace(lane, auth_mode="chatgpt-plan"),
        _context(),
        NOW,
        binding=replace(_binding(), observed_at=NOW + 1.0),
    )

    assert wrong_auth.refusal_code == "auth_mode_binding_mismatch"
    assert exact_boundary.bindable is True
    assert stale.refusal_code == "binding_stale"
    assert future.refusal_code == "binding_clock_uncertain"


def test_compact_projection_requires_explicit_legacy_opt_in_when_unbound() -> None:
    """An omitted bindings argument must not silently become capacity authority."""
    projection = select_binding_lanes(_snapshot(_lane()), _context(), NOW)
    legacy_projection = select_binding_lanes(
        _snapshot(_lane()), _context(), NOW, allow_unbound_legacy=True
    )

    assert projection.binding_lanes == ()
    assert projection.detail_lanes[0].refusal_code == "account_binding_required"
    assert len(legacy_projection.binding_lanes) == 1


@pytest.mark.parametrize(
    ("lane", "expected_bindable", "expected_reason"),
    [
        (
            _lane(remaining=0.0, state=ObservationState.OBSERVED_ZERO),
            True,
            None,
        ),
        (
            _lane(remaining=None, state=ObservationState.NULL),
            False,
            "usage_missing",
        ),
        (
            _lane(remaining=None, state=ObservationState.UNAVAILABLE),
            False,
            "usage_unavailable",
        ),
    ],
)
def test_observed_zero_binds_but_missing_capacity_does_not(
    lane: QuotaLaneObservation,
    expected_bindable: bool,
    expected_reason: str | None,
) -> None:
    authority = evaluate_lane_authority(lane, _context(), NOW, allow_unbound_legacy=True)

    assert authority.bindable is expected_bindable
    assert authority.refusal_code == expected_reason


def test_fresh_lane_outranks_stale_lower_remaining_lane() -> None:
    fresh = _lane(window="fresh", remaining=40.0)
    stale = _lane(
        window="stale",
        remaining=1.0,
        state=ObservationState.STALE,
        health_kind=SourceHealthKind.STALE,
    )

    projection = select_binding_lanes(
        _snapshot(stale, fresh), _context(), NOW, limit=1, allow_unbound_legacy=True
    )

    assert [row.lane.key.window for row in projection.binding_lanes] == ["fresh"]
    assert projection.detail_lanes[1].freshness == ObservationState.STALE


@pytest.mark.parametrize(
    ("selected_model", "expected", "reason"),
    [
        (None, LaneApplicability.AMBIGUOUS, "model_unknown"),
        ("gpt-4.1", LaneApplicability.INAPPLICABLE, "model_mismatch"),
        ("gpt-5", LaneApplicability.APPLICABLE, None),
    ],
)
def test_model_lane_requires_an_exact_selected_model(
    selected_model: str | None,
    expected: LaneApplicability,
    reason: str | None,
) -> None:
    lane = _lane(
        opaque_scope="model:gpt-5",
        model="gpt-5",
        effect=QuotaEffect.MODEL,
    )
    context = _context(model=selected_model)

    assert classify_applicability(lane, context) is expected
    authority = evaluate_lane_authority(lane, context, NOW, allow_unbound_legacy=True)
    assert authority.bindable is (expected is LaneApplicability.APPLICABLE)
    assert authority.refusal_code == reason


@pytest.mark.parametrize(
    ("selected_feature", "expected", "reason"),
    [
        (None, LaneApplicability.AMBIGUOUS, "feature_unknown"),
        ("routines", LaneApplicability.INAPPLICABLE, "feature_mismatch"),
        ("fable", LaneApplicability.APPLICABLE, None),
    ],
)
def test_feature_lane_matches_the_canonical_opaque_scope(
    selected_feature: str | None,
    expected: LaneApplicability,
    reason: str | None,
) -> None:
    lane = _lane(opaque_scope="fable", effect=QuotaEffect.FEATURE)
    context = _context(feature=selected_feature)

    assert classify_applicability(lane, context) is expected
    assert evaluate_lane_authority(
        lane, context, NOW, allow_unbound_legacy=True
    ).refusal_code == reason


def test_all_workloads_lane_binds_only_for_its_execution_source() -> None:
    lane = _lane()

    matching = evaluate_lane_authority(lane, _context(), NOW, allow_unbound_legacy=True)
    wrong_provider = evaluate_lane_authority(
        lane,
        _context(providers=("claude",)),
        NOW,
        allow_unbound_legacy=True,
    )
    wrong_instance = evaluate_lane_authority(
        lane,
        _context(instances=("remote:other",)),
        NOW,
        allow_unbound_legacy=True,
    )

    assert matching.bindable is True
    assert wrong_provider.refusal_code == "source_out_of_context"
    assert wrong_instance.refusal_code == "source_out_of_context"


def test_selection_prefers_one_short_and_one_long_lane() -> None:
    tighter_short = _lane(window="short-tight", remaining=20.0)
    looser_short = _lane(window="short-loose", remaining=70.0)
    long = _lane(
        window="weekly",
        horizon=QuotaHorizon.LONG,
        remaining=90.0,
        reset_epoch=8_000.0,
    )

    projection = select_binding_lanes(
        _snapshot(looser_short, long, tighter_short),
        _context(),
        NOW,
        allow_unbound_legacy=True,
    )

    assert [row.lane.key.window for row in projection.binding_lanes] == [
        "short-tight",
        "weekly",
    ]


def test_compact_projection_never_contains_more_than_two_lanes() -> None:
    lanes = tuple(
        _lane(window=f"window-{index}", remaining=float(index + 1))
        for index in range(6)
    )

    projection = select_binding_lanes(_snapshot(*lanes), _context(), NOW, allow_unbound_legacy=True)

    assert len(projection.binding_lanes) <= 2
    assert len(projection.detail_lanes) == 6
    with pytest.raises(ValueError, match="at most two"):
        select_binding_lanes(
            _snapshot(*lanes), _context(), NOW, limit=3, allow_unbound_legacy=True
        )


def test_second_provider_can_bind_without_losing_its_provider_identity() -> None:
    codex_short = _lane(window="codex-short", remaining=60.0)
    claude_short = _lane(
        provider_id="claude",
        source_instance_id="remote:primary",
        window="claude-short",
        remaining=10.0,
    )
    codex_long = _lane(
        window="codex-weekly",
        horizon=QuotaHorizon.LONG,
        remaining=50.0,
        reset_epoch=9_000.0,
    )
    context = _context(
        providers=("codex", "claude"),
        instances=("local:primary", "remote:primary"),
    )

    projection = select_binding_lanes(
        _snapshot(codex_short, claude_short, codex_long),
        context,
        NOW,
        allow_unbound_legacy=True,
    )

    assert [row.lane.key.window for row in projection.binding_lanes] == [
        "claude-short",
        "codex-weekly",
    ]
    assert [row.provider_name for row in projection.binding_lanes] == [
        "claude",
        "codex",
    ]


def test_tied_selection_and_detail_order_are_stable_across_input_permutations() -> None:
    lanes = (
        _lane(window="alpha", remaining=25.0),
        _lane(window="bravo", remaining=25.0),
        _lane(window="charlie", remaining=25.0),
    )

    selections = {
        tuple(
            row.lane.key.window
            for row in select_binding_lanes(
                _snapshot(*ordering), _context(), NOW, allow_unbound_legacy=True
            ).binding_lanes
        )
        for ordering in permutations(lanes)
    }
    details = {
        tuple(
            row.lane.key.window
            for row in select_binding_lanes(
                _snapshot(*ordering), _context(), NOW, allow_unbound_legacy=True
            ).detail_lanes
        )
        for ordering in permutations(lanes)
    }

    assert selections == {("alpha",)}
    assert details == {("alpha", "bravo", "charlie")}


def test_one_horizon_fills_second_slot_only_from_another_source() -> None:
    codex_tight = _lane(window="codex-tight", remaining=10.0)
    codex_loose = _lane(window="codex-loose", remaining=20.0)
    claude = _lane(
        provider_id="claude",
        source_instance_id="remote:primary",
        window="claude-short",
        remaining=30.0,
    )
    context = _context(
        providers=("codex", "claude"),
        instances=("local:primary", "remote:primary"),
    )

    codex_only = select_binding_lanes(
        _snapshot(codex_tight, codex_loose),
        context,
        NOW,
        allow_unbound_legacy=True,
    )
    cross_source = select_binding_lanes(
        _snapshot(codex_tight, codex_loose, claude),
        context,
        NOW,
        allow_unbound_legacy=True,
    )

    assert [row.lane.key.window for row in codex_only.binding_lanes] == ["codex-tight"]
    assert [row.lane.key.window for row in cross_source.binding_lanes] == [
        "codex-tight",
        "claude-short",
    ]


def test_partial_quota_evidence_stays_in_detail_and_never_binds() -> None:
    partial = _lane(
        remaining=20.0,
        state=ObservationState.PARTIAL,
        health_kind=SourceHealthKind.PARTIAL,
    )

    projection = select_binding_lanes(_snapshot(partial), _context(), NOW, allow_unbound_legacy=True)

    assert projection.binding_lanes == ()
    assert projection.detail_lanes[0].refusal_code == "usage_partial"


def test_failed_source_with_last_known_good_projects_as_stale_fallback() -> None:
    lane = _lane(remaining=12.0, health_kind=SourceHealthKind.FAILED)
    lane = replace(
        lane,
        source_health=replace(lane.source_health, has_last_known_good=True),
    )

    authority = evaluate_lane_authority(lane, _context(), NOW, allow_unbound_legacy=True)

    assert authority.bindable is True
    assert authority.freshness is ObservationState.LAST_KNOWN_GOOD
    assert authority.refusal_code is None


def test_reset_credibility_uses_the_injected_clock_without_blocking_present_capacity() -> None:
    future = _lane(reset_epoch=1_100.0)
    unknown = replace(
        _lane(window="unknown-reset"),
        reset=ResetFact(ResetState.UNKNOWN, None, 300.0, NOW),
    )

    assert evaluate_lane_authority(
        future, _context(), 1_050.0, allow_unbound_legacy=True
    ).reset_credible is True
    expired = evaluate_lane_authority(future, _context(), 1_100.0, allow_unbound_legacy=True)
    assert expired.reset_credible is False
    assert expired.bindable is True
    assert evaluate_lane_authority(
        unknown, _context(), NOW, allow_unbound_legacy=True
    ).bindable is True


def test_source_health_projection_is_stably_ordered() -> None:
    codex = _lane()
    claude = _lane(
        provider_id="claude",
        source_instance_id="remote:primary",
        window="weekly",
    )
    snapshot = CapacitySnapshot(
        NOW,
        (codex, claude),
        (codex.source_health, claude.source_health),
    )

    projected = project_source_health(snapshot, NOW)

    assert [health.source.provider_id for health in projected] == ["claude", "codex"]
