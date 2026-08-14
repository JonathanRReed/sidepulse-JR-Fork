from __future__ import annotations

import os
import subprocess
import urllib.request
from unittest.mock import patch

import pytest

from sidepulse.capacity_sources import (
    EvidenceMetricKind,
    SupportedCapacityEvidence,
    SupportedLaneEvidence,
    normalize_supported_quota_evidence,
)
from sidepulse.capacity_types import (
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from sidepulse.provider_contracts import (
    AdapterIdentifier,
    CapabilityIdentifier,
    CapacityLaneDescriptor,
    CapacitySourceDescriptor,
    ProviderIdentifier,
    SchemaVersion,
    SourceInstanceIdentifier,
)

SOURCE = SourceKey("codex", "quota", "local", "remote_quota_windows")


def _key(
    window: str,
    *,
    effect: QuotaEffect = QuotaEffect.ALL_WORKLOADS,
    model: str | None = None,
) -> QuotaLaneKey:
    return QuotaLaneKey(
        source=SOURCE,
        opaque_scope="shared" if effect is not QuotaEffect.MODEL else model or "unknown",
        pool="plan",
        model=model,
        window=window,
        effect=effect,
    )


SHORT_KEY = _key("session")
LONG_KEY = _key("weekly")
MODEL_KEY = _key("weekly_opus", effect=QuotaEffect.MODEL, model="claude-opus")
UNKNOWN_KEY = _key("other", effect=QuotaEffect.UNKNOWN)


def _descriptor() -> CapacitySourceDescriptor:
    return CapacitySourceDescriptor(
        provider_id=ProviderIdentifier("codex"),
        adapter_id=AdapterIdentifier("quota"),
        source_instance_id=SourceInstanceIdentifier("local"),
        capability_id=CapabilityIdentifier("remote_quota_windows"),
        capability_version=SchemaVersion(1, 0),
        lanes=(
            CapacityLaneDescriptor(SHORT_KEY, "Session window", QuotaHorizon.SHORT, True),
            CapacityLaneDescriptor(LONG_KEY, "Weekly window", QuotaHorizon.LONG, True),
            CapacityLaneDescriptor(MODEL_KEY, "Opus weekly", QuotaHorizon.LONG, True),
            CapacityLaneDescriptor(UNKNOWN_KEY, "Other limit", QuotaHorizon.OTHER, False),
        ),
    )


def _evidence(*lanes: SupportedLaneEvidence, health=SourceHealthKind.HEALTHY):
    return SupportedCapacityEvidence(
        source=SOURCE,
        health_kind=health,
        lanes=tuple(lanes),
        account_discriminator=None,
        has_last_known_good=False,
    )


def _lane(
    key: QuotaLaneKey,
    percent: float | None,
    *,
    metric=EvidenceMetricKind.PERCENT_REMAINING,
    state=ObservationState.OBSERVED,
    reset_state=ResetState.UNKNOWN,
    reset_epoch: float | None = None,
) -> SupportedLaneEvidence:
    return SupportedLaneEvidence(
        key=key,
        metric_kind=metric,
        percent=percent,
        state=state,
        reset_state=reset_state,
        reset_epoch=reset_epoch,
        window_minutes=None,
    )


def test_used_first_evidence_converts_once_and_preserves_observed_zero() -> None:
    result = normalize_supported_quota_evidence(
        _descriptor(),
        _evidence(
            _lane(
                SHORT_KEY,
                100,
                metric=EvidenceMetricKind.PERCENT_USED,
                state=ObservationState.OBSERVED,
            ),
            _lane(LONG_KEY, 0, state=ObservationState.OBSERVED_ZERO),
        ),
        observed_at=100.0,
    )

    short, long = result.snapshot.lanes
    assert short.value.remaining == 0.0
    assert short.value.state is ObservationState.OBSERVED_ZERO
    assert long.value.remaining == 0.0
    assert long.value.state is ObservationState.OBSERVED_ZERO
    assert result.diagnostics == ("converted_percent_used",)


@pytest.mark.parametrize(
    ("state", "percent"),
    (
        (ObservationState.NULL, None),
        (ObservationState.UNAVAILABLE, None),
        (ObservationState.PARTIAL, None),
        (ObservationState.STALE, 42),
    ),
)
def test_missing_partial_and_stale_values_remain_distinct(state, percent) -> None:
    result = normalize_supported_quota_evidence(
        _descriptor(),
        _evidence(_lane(SHORT_KEY, percent, state=state)),
        observed_at=100.0,
    )

    value = result.snapshot.lanes[0].value
    assert value.state is state
    assert value.remaining == percent


def test_reset_unknown_and_source_partial_remain_independent_facts() -> None:
    result = normalize_supported_quota_evidence(
        _descriptor(),
        _evidence(
            _lane(SHORT_KEY, 55, reset_state=ResetState.UNKNOWN),
            health=SourceHealthKind.PARTIAL,
        ),
        observed_at=100.0,
    )

    lane = result.snapshot.lanes[0]
    assert lane.reset.state is ResetState.UNKNOWN
    assert lane.value.remaining == 55.0
    assert lane.source_health.kind is SourceHealthKind.PARTIAL
    assert lane.source_health.reason_code == "source_partial"


def test_static_descriptor_owns_model_scope_semantics_and_unknown_effect() -> None:
    result = normalize_supported_quota_evidence(
        _descriptor(),
        _evidence(_lane(MODEL_KEY, 35), _lane(UNKNOWN_KEY, 20)),
        observed_at=100.0,
    )

    model, unknown = result.snapshot.lanes
    assert model.semantic_name == "Opus weekly"
    assert model.key.model == "claude-opus"
    assert model.key.effect is QuotaEffect.MODEL
    assert unknown.semantic_name == "Other limit"
    assert unknown.key.effect is QuotaEffect.UNKNOWN


def test_supported_normalization_performs_no_io() -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("capacity normalization crossed an I/O boundary")

    with (
        patch.object(os, "open", side_effect=unexpected),
        patch.object(subprocess, "run", side_effect=unexpected),
        patch.object(urllib.request, "urlopen", side_effect=unexpected),
    ):
        result = normalize_supported_quota_evidence(
            _descriptor(),
            _evidence(_lane(SHORT_KEY, 60)),
            observed_at=100.0,
        )

    assert result.snapshot.lanes[0].value.remaining == 60.0


@pytest.mark.parametrize("percent", (-0.01, 100.01, float("nan"), float("inf")))
def test_invalid_percent_evidence_fails_closed(percent: float) -> None:
    with pytest.raises(ValueError):
        normalize_supported_quota_evidence(
            _descriptor(),
            _evidence(_lane(SHORT_KEY, percent)),
            observed_at=100.0,
        )


def test_undeclared_or_duplicate_lanes_fail_closed() -> None:
    undeclared = _key("undeclared")
    with pytest.raises(ValueError, match="declared"):
        normalize_supported_quota_evidence(
            _descriptor(),
            _evidence(_lane(undeclared, 50)),
            observed_at=100.0,
        )
    with pytest.raises(ValueError, match="duplicate"):
        normalize_supported_quota_evidence(
            _descriptor(),
            _evidence(_lane(SHORT_KEY, 50), _lane(SHORT_KEY, 49)),
            observed_at=100.0,
        )
