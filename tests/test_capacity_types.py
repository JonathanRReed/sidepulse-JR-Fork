from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import inf, nan

import pytest

from sidepulse.capacity_types import (
    MAX_EXECUTION_CONTEXT_MEMBERS,
    MAX_LANES_PER_OBSERVATION,
    CapacityAccountBinding,
    CapacityEvidenceClass,
    CapacitySnapshot,
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValidationError,
    CapacityValue,
    ExecutionContext,
    ForecastConfidence,
    LaneApplicability,
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    ResetState,
    SampleDisposition,
    SourceHealthKind,
    SourceKey,
)


def _source() -> SourceKey:
    return SourceKey("codex", "quota", "source:local-01", "remote_quota_windows")


def test_account_binding_is_frozen_exact_and_has_no_display_identity() -> None:
    """Dropping source, pool, or opaque-account validation could merge unrelated plans."""
    binding = CapacityAccountBinding(
        source=_source(),
        provider_id="codex",
        auth_mode="chatgpt-plan",
        opaque_account_id="acct:opaque-01",
        pool_id="consumer",
        evidence_class=CapacityEvidenceClass.OFFICIAL_LOCAL,
        observed_at=1_800_000_000.0,
    )

    assert binding.source == _source()
    assert binding.evidence_class is CapacityEvidenceClass.OFFICIAL_LOCAL
    with pytest.raises(FrozenInstanceError):
        binding.pool_id = "api-org"  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    (
        {"provider_id": "claude"},
        {"auth_mode": ""},
        {"opaque_account_id": "person@example.com"},
        {"opaque_account_id": "/Users/private/account"},
        {"opaque_account_id": "token:secret"},
        {"opaque_account_id": "acct\\nprivate"},
        {"opaque_account_id": "åccount"},
        {"opaque_account_id": "a" * 65},
        {"observed_at": float("nan")},
        {"observed_at": True},
    ),
)
def test_account_binding_rejects_ambiguous_or_private_identity(changes: dict[str, object]) -> None:
    """Accepting an unsafe binding could expose one account's capacity as another's."""
    values: dict[str, object] = {
        "source": _source(),
        "provider_id": "codex",
        "auth_mode": "chatgpt-plan",
        "opaque_account_id": "acct:opaque-01",
        "pool_id": "consumer",
        "evidence_class": CapacityEvidenceClass.OFFICIAL_LOCAL,
        "observed_at": 1_800_000_000.0,
    }
    values.update(changes)

    with pytest.raises(CapacityValidationError, match="invalid capacity account binding"):
        CapacityAccountBinding(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("account_discriminator", ("token:secret", "bearer-account"))
def test_lane_rejects_credential_shaped_account_discriminators(
    account_discriminator: str,
) -> None:
    """Retaining a credential-shaped lane account would leak it into capacity state."""
    with pytest.raises(CapacityValidationError, match="account discriminator"):
        QuotaLaneObservation(
            key=_lane_key(),
            semantic_name="Session window",
            horizon=QuotaHorizon.SHORT,
            value=CapacityValue(CapacityUnit.PERCENT_REMAINING, 45.0, ObservationState.OBSERVED),
            reset=_reset(),
            observed_at=1_800_000_000.0,
            source_health=_health(),
            account_discriminator=account_discriminator,
        )


def _lane_key(
    *,
    source: SourceKey | None = None,
    opaque_scope: str = "all",
    pool: str = "requests",
    model: str | None = None,
    window: str = "session",
    effect: QuotaEffect = QuotaEffect.ALL_WORKLOADS,
) -> QuotaLaneKey:
    return QuotaLaneKey(
        source or _source(),
        opaque_scope,
        pool,
        model,
        window,
        effect,
    )


def _health(*, source: SourceKey | None = None) -> CapacitySourceHealth:
    return CapacitySourceHealth(
        source=source or _source(),
        kind=SourceHealthKind.HEALTHY,
        observed_at=1_800_000_000.0,
        last_attempt_at=1_799_999_999.0,
        retry_at=None,
        reason_code=None,
        has_last_known_good=False,
    )


def _reset() -> ResetFact:
    return ResetFact(
        state=ResetState.FUTURE,
        reset_epoch=1_800_003_600.0,
        window_minutes=300.0,
        observed_at=1_800_000_000.0,
    )


def _observation(
    *,
    key: QuotaLaneKey | None = None,
    remaining: float = 45.0,
) -> QuotaLaneObservation:
    lane_key = key or _lane_key()
    return QuotaLaneObservation(
        key=lane_key,
        semantic_name="Session window",
        horizon=QuotaHorizon.SHORT,
        value=CapacityValue(
            CapacityUnit.PERCENT_REMAINING,
            remaining,
            ObservationState.OBSERVED,
        ),
        reset=_reset(),
        observed_at=1_800_000_000.0,
        source_health=_health(source=lane_key.source),
        account_discriminator="acct:opaque-01",
    )


def test_canonical_enums_keep_truth_and_authority_states_distinct() -> None:
    """Collapsing a missing or withheld state into another state loses authority truth."""
    assert len(set(ObservationState)) == 7
    assert ObservationState.OBSERVED_ZERO is not ObservationState.NULL
    assert ObservationState.UNAVAILABLE is not ObservationState.PARTIAL
    assert ObservationState.STALE is not ObservationState.LAST_KNOWN_GOOD
    assert len(set(ResetState)) == 6
    assert LaneApplicability.AMBIGUOUS is not LaneApplicability.INAPPLICABLE
    assert SampleDisposition.IDENTITY_AMBIGUOUS is not SampleDisposition.INVALID
    assert ForecastConfidence.UNAVAILABLE is not ForecastConfidence.LOW_LINEAR


def test_source_and_lane_keys_are_immutable_stable_value_identities() -> None:
    """Mutable or object-identity keys would split the same lane across observations."""
    source = _source()
    same_source = SourceKey("codex", "quota", "source:local-01", "remote_quota_windows")
    key = _lane_key(source=source)

    assert source == same_source
    assert hash(source) == hash(same_source)
    assert key == _lane_key(source=same_source)
    with pytest.raises(FrozenInstanceError):
        source.provider_id = "claude"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_id", "Codex Display"),
        ("adapter_id", "Quota Menu"),
        ("source_instance_id", "user@example.com"),
        ("source_instance_id", "/Users/private/quota.json"),
        ("capability_id", "Remote quota"),
    ],
)
def test_source_key_rejects_display_and_private_text_as_identity(
    field: str,
    value: str,
) -> None:
    """Display labels, account text, and paths must never become stable source keys."""
    fields = {
        "provider_id": "codex",
        "adapter_id": "quota",
        "source_instance_id": "source:local-01",
        "capability_id": "remote_quota_windows",
    }
    fields[field] = value

    with pytest.raises(CapacityValidationError, match="invalid source key"):
        SourceKey(**fields)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("opaque_scope", "Account One"),
        ("opaque_scope", "person@example.com"),
        ("pool", "/tmp/provider-pool"),
        ("model", "Model Display Name"),
        ("window", "300 minutes"),
    ],
)
def test_lane_key_rejects_display_names_paths_and_duration_labels(
    field: str,
    value: str,
) -> None:
    """Presentation text and inferred durations must not define quota continuity."""
    fields = {
        "source": _source(),
        "opaque_scope": "all",
        "pool": "requests",
        "model": None,
        "window": "session",
        "effect": QuotaEffect.ALL_WORKLOADS,
    }
    fields[field] = value

    with pytest.raises(CapacityValidationError, match="invalid quota lane key"):
        QuotaLaneKey(**fields)


@pytest.mark.parametrize("remaining", [-0.001, 100.001, nan, inf, -inf, True, "50"])
def test_percent_remaining_rejects_nonfinite_out_of_range_and_non_numeric_values(
    remaining: object,
) -> None:
    """Invalid numbers must not enter ranking or appear as provider capacity."""
    with pytest.raises(CapacityValidationError, match="invalid remaining capacity"):
        CapacityValue(
            CapacityUnit.PERCENT_REMAINING,
            remaining,  # type: ignore[arg-type]
            ObservationState.OBSERVED,
        )


def test_zero_remaining_requires_and_preserves_observed_zero_truth() -> None:
    """Treating an observed zero as missing would hide an exhausted binding lane."""
    value = CapacityValue(
        CapacityUnit.PERCENT_REMAINING,
        0.0,
        ObservationState.OBSERVED_ZERO,
    )

    assert value.remaining == 0.0
    assert value.state is ObservationState.OBSERVED_ZERO
    with pytest.raises(CapacityValidationError, match="observed zero state"):
        CapacityValue(
            CapacityUnit.PERCENT_REMAINING,
            0.0,
            ObservationState.OBSERVED,
        )
    with pytest.raises(CapacityValidationError, match="observed zero state"):
        CapacityValue(
            CapacityUnit.PERCENT_REMAINING,
            0.1,
            ObservationState.OBSERVED_ZERO,
        )


@pytest.mark.parametrize(
    "state",
    [ObservationState.NULL, ObservationState.UNAVAILABLE],
)
def test_missing_remaining_has_no_numeric_value(state: ObservationState) -> None:
    """Fabricating zero for null or unavailable evidence would manufacture exhaustion."""
    value = CapacityValue(CapacityUnit.PERCENT_REMAINING, None, state)

    assert value.remaining is None
    assert value.state is state


@pytest.mark.parametrize(
    ("remaining", "state"),
    [
        (25.0, ObservationState.PARTIAL),
        (25.0, ObservationState.STALE),
        (25.0, ObservationState.LAST_KNOWN_GOOD),
        (None, ObservationState.PARTIAL),
    ],
)
def test_partial_stale_and_last_known_good_remain_explicit(
    remaining: float | None,
    state: ObservationState,
) -> None:
    """Fallback or incomplete evidence must retain its state beside any usable value."""
    value = CapacityValue(CapacityUnit.PERCENT_REMAINING, remaining, state)

    assert value.remaining == remaining
    assert value.state is state


@pytest.mark.parametrize(
    ("remaining", "state"),
    [
        (None, ObservationState.OBSERVED),
        (None, ObservationState.OBSERVED_ZERO),
        (None, ObservationState.STALE),
        (None, ObservationState.LAST_KNOWN_GOOD),
        (1.0, ObservationState.NULL),
        (1.0, ObservationState.UNAVAILABLE),
    ],
)
def test_remaining_value_and_state_cannot_contradict_each_other(
    remaining: float | None,
    state: ObservationState,
) -> None:
    """Contradictory numeric and missing states would make consumers guess authority."""
    with pytest.raises(CapacityValidationError, match="remaining capacity state"):
        CapacityValue(CapacityUnit.PERCENT_REMAINING, remaining, state)


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "state": ResetState.FUTURE,
            "reset_epoch": 1_799_999_999.0,
            "window_minutes": 300.0,
            "observed_at": 1_800_000_000.0,
        },
        {
            "state": ResetState.DUE,
            "reset_epoch": 1_800_000_001.0,
            "window_minutes": 300.0,
            "observed_at": 1_800_000_000.0,
        },
        {
            "state": ResetState.UNKNOWN,
            "reset_epoch": 1_800_000_001.0,
            "window_minutes": None,
            "observed_at": 1_800_000_000.0,
        },
        {
            "state": ResetState.FUTURE,
            "reset_epoch": inf,
            "window_minutes": 300.0,
            "observed_at": 1_800_000_000.0,
        },
        {
            "state": ResetState.FUTURE,
            "reset_epoch": 1_800_000_001.0,
            "window_minutes": 0.0,
            "observed_at": 1_800_000_000.0,
        },
    ],
)
def test_reset_fact_rejects_contradictory_or_invalid_clock_truth(
    kwargs: dict[str, object],
) -> None:
    """A malformed reset boundary must not become a countdown or continuity fact."""
    with pytest.raises(CapacityValidationError, match="invalid reset fact"):
        ResetFact(**kwargs)  # type: ignore[arg-type]


def test_public_capacity_records_validate_types_and_matching_source_identity() -> None:
    """Annotations alone would let observations combine facts from sibling sources."""
    other_source = SourceKey("claude", "quota", "source:local-02", "remote_quota_windows")
    with pytest.raises(CapacityValidationError, match="source health"):
        _health(source="codex")  # type: ignore[arg-type]
    with pytest.raises(CapacityValidationError, match="observation source"):
        QuotaLaneObservation(
            key=_lane_key(),
            semantic_name="Session window",
            horizon=QuotaHorizon.SHORT,
            value=CapacityValue(
                CapacityUnit.PERCENT_REMAINING,
                45.0,
                ObservationState.OBSERVED,
            ),
            reset=_reset(),
            observed_at=1_800_000_000.0,
            source_health=_health(source=other_source),
            account_discriminator="acct:opaque-01",
        )


@pytest.mark.parametrize(
    "account_discriminator",
    ["person@example.com", "/Users/private/account", "Account Display", "x" * 65],
)
def test_observation_rejects_account_display_text_as_discriminator(
    account_discriminator: str,
) -> None:
    """Account continuity must use only an opaque source-provided discriminator."""
    observation = _observation()
    with pytest.raises(CapacityValidationError, match="account discriminator"):
        QuotaLaneObservation(
            key=observation.key,
            semantic_name=observation.semantic_name,
            horizon=observation.horizon,
            value=observation.value,
            reset=observation.reset,
            observed_at=observation.observed_at,
            source_health=observation.source_health,
            account_discriminator=account_discriminator,
        )


def test_snapshot_caps_lanes_and_rejects_duplicate_lane_keys() -> None:
    """Unbounded or duplicate lanes would make input order control capacity truth."""
    first = _observation()
    duplicate = _observation(remaining=20.0)
    with pytest.raises(CapacityValidationError, match="duplicate quota lane"):
        CapacitySnapshot(
            observed_at=1_800_000_000.0,
            lanes=(first, duplicate),
            source_health=(_health(),),
        )

    lanes = tuple(
        _observation(key=_lane_key(window=f"window-{index}"))
        for index in range(MAX_LANES_PER_OBSERVATION + 1)
    )
    with pytest.raises(CapacityValidationError, match="too many quota lanes"):
        CapacitySnapshot(
            observed_at=1_800_000_000.0,
            lanes=lanes,
            source_health=(_health(),),
        )


def test_feature_lanes_use_opaque_scope_as_their_distinct_identity() -> None:
    """Ignoring opaque scope would collapse sibling feature quotas into one lane."""
    fable = _lane_key(opaque_scope="fable", effect=QuotaEffect.FEATURE)
    deep_research = _lane_key(opaque_scope="deep-research", effect=QuotaEffect.FEATURE)

    snapshot = CapacitySnapshot(
        observed_at=1_800_000_000.0,
        lanes=(_observation(key=fable), _observation(key=deep_research)),
        source_health=(_health(),),
    )

    assert fable != deep_research
    assert tuple(lane.key.opaque_scope for lane in snapshot.lanes) == (
        "fable",
        "deep-research",
    )


def test_duplicate_feature_scope_is_rejected_for_the_same_pool_and_window() -> None:
    """Repeating the same feature scope must not create two conflicting facts."""
    key = _lane_key(opaque_scope="fable", effect=QuotaEffect.FEATURE)

    with pytest.raises(CapacityValidationError, match="duplicate quota lane"):
        CapacitySnapshot(
            observed_at=1_800_000_000.0,
            lanes=(_observation(key=key), _observation(key=key, remaining=20.0)),
            source_health=(_health(),),
        )


def test_feature_lane_forbids_a_model_discriminator() -> None:
    """Mixing feature scope with model identity would make authority ambiguous."""
    with pytest.raises(CapacityValidationError, match="invalid quota lane key"):
        _lane_key(
            opaque_scope="fable",
            model="gpt-5.4",
            effect=QuotaEffect.FEATURE,
        )


def test_snapshot_and_execution_context_require_bounded_immutable_tuples() -> None:
    """Accepting arbitrary iterables could trigger work or hide unbounded input."""
    with pytest.raises(CapacityValidationError, match="invalid capacity snapshot"):
        CapacitySnapshot(
            observed_at=1_800_000_000.0,
            lanes=[_observation()],  # type: ignore[arg-type]
            source_health=(_health(),),
        )

    with pytest.raises(CapacityValidationError, match="execution context"):
        ExecutionContext(
            provider_ids=tuple(f"provider-{index}" for index in range(MAX_EXECUTION_CONTEXT_MEMBERS + 1)),
            source_instances=(),
            selected_model=None,
            selected_feature=None,
        )

    context = ExecutionContext(
        provider_ids=("codex",),
        source_instances=("source:local-01",),
        selected_model="gpt-5.4",
        selected_feature=None,
    )
    assert context.provider_ids == ("codex",)
    assert context.source_instances == ("source:local-01",)
