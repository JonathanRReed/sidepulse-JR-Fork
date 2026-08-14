from __future__ import annotations

from dataclasses import fields
from math import inf, nan

import pytest

from sidepulse.capacity_history import (
    ACTIVITY_HISTORY_SCHEMA_VERSION,
    CAPACITY_HISTORY_SCHEMA_VERSION,
    NO_OBSERVATION,
    ActivityHistorySample,
    CapacityHistorySample,
    HistoryContinuity,
    HistoryInterval,
    HistoryRetentionPolicy,
    HistoryValidationError,
    NoObservationInterval,
    admit_capacity_sample,
    prune_capacity_history,
    summarize_capacity_history,
)
from sidepulse.capacity_types import (
    QuotaEffect,
    QuotaLaneKey,
    SampleDisposition,
    SourceHealthKind,
    SourceKey,
)

NOW = 1_800_000_000.0
DAY = 86_400.0


def _source() -> SourceKey:
    return SourceKey("codex", "quota", "source:local-01", "remote_quota_windows")


def _lane() -> QuotaLaneKey:
    return QuotaLaneKey(
        _source(),
        "all",
        "requests",
        None,
        "session",
        QuotaEffect.ALL_WORKLOADS,
    )


def _capacity(
    *,
    observed_at: float = NOW,
    remaining: float = 45.0,
    reset_epoch: float | None = NOW + 3_600.0,
    disposition: SampleDisposition = SampleDisposition.ACCEPTED,
    refusal_code: str | None = None,
) -> CapacityHistorySample:
    return CapacityHistorySample(
        CAPACITY_HISTORY_SCHEMA_VERSION,
        _lane(),
        "acct:opaque-01",
        observed_at,
        remaining,
        reset_epoch,
        300.0,
        SourceHealthKind.HEALTHY,
        disposition,
        refusal_code,
    )


def _activity(*, observed_at: float = NOW) -> ActivityHistorySample:
    return ActivityHistorySample(
        ACTIVITY_HISTORY_SCHEMA_VERSION,
        _source(),
        observed_at,
        12,
        3,
        0.8,
        0.5,
        1.25,
    )


def test_history_samples_expose_only_the_metadata_allowlist() -> None:
    """Adding content, path, title, account display, or raw-error fields is a privacy bug."""
    assert tuple(field.name for field in fields(CapacityHistorySample)) == (
        "schema_version",
        "lane_key",
        "account_discriminator",
        "observed_at",
        "remaining",
        "reset_epoch",
        "window_minutes",
        "source_health",
        "disposition",
        "refusal_code",
    )
    assert tuple(field.name for field in fields(ActivityHistorySample)) == (
        "schema_version",
        "source_key",
        "observed_at",
        "event_count",
        "session_count",
        "coverage",
        "priced_coverage",
        "estimated_cost",
    )


@pytest.mark.parametrize(
    "forbidden",
    (
        "prompt",
        "response",
        "transcript",
        "path",
        "title",
        "display_account_name",
        "email",
        "raw_error",
        "credential",
        "access_token",
        "undeclared",
    ),
)
def test_capacity_constructor_rejects_every_undeclared_field(forbidden: str) -> None:
    """An expanded constructor would let private provider payloads enter retention."""
    values = {
        "schema_version": CAPACITY_HISTORY_SCHEMA_VERSION,
        "lane_key": _lane(),
        "account_discriminator": "acct:opaque-01",
        "observed_at": NOW,
        "remaining": 45.0,
        "reset_epoch": NOW + 3_600.0,
        "window_minutes": 300.0,
        "source_health": SourceHealthKind.HEALTHY,
        "disposition": SampleDisposition.ACCEPTED,
        "refusal_code": None,
        forbidden: "PRIVATE SENTINEL",
    }
    with pytest.raises(TypeError):
        CapacityHistorySample(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "account_discriminator",
    ("person@example.com", "/Users/private/account", "Display Account", "token:secret"),
)
def test_history_rejects_account_display_email_and_path_identity(
    account_discriminator: str,
) -> None:
    """Only an opaque source discriminator can establish longitudinal identity."""
    with pytest.raises(HistoryValidationError):
        CapacityHistorySample(
            CAPACITY_HISTORY_SCHEMA_VERSION,
            _lane(),
            account_discriminator,
            NOW,
            45.0,
            NOW + 3_600.0,
            300.0,
            SourceHealthKind.HEALTHY,
            SampleDisposition.ACCEPTED,
            None,
        )


@pytest.mark.parametrize("remaining", (-0.1, 100.1, nan, inf, True))
def test_capacity_history_values_are_finite_and_bounded(remaining: object) -> None:
    """Unbounded or nonfinite metadata must not reach summaries or disk."""
    with pytest.raises(HistoryValidationError):
        _capacity(remaining=remaining)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "values",
    (
        {"event_count": -1},
        {"session_count": 1_000_001},
        {"coverage": 1.01},
        {"priced_coverage": -0.01},
        {"estimated_cost": inf},
    ),
)
def test_activity_history_values_are_finite_and_bounded(values: dict[str, object]) -> None:
    """Local activity remains one bounded aggregate record, never broad usage history."""
    kwargs: dict[str, object] = {
        "schema_version": ACTIVITY_HISTORY_SCHEMA_VERSION,
        "source_key": _source(),
        "observed_at": NOW,
        "event_count": 12,
        "session_count": 3,
        "coverage": 0.8,
        "priced_coverage": 0.5,
        "estimated_cost": 1.25,
    }
    kwargs.update(values)
    with pytest.raises(HistoryValidationError):
        ActivityHistorySample(**kwargs)  # type: ignore[arg-type]


def test_admission_rejects_duplicate_out_of_order_and_identity_discontinuity() -> None:
    """Polling and ambiguous identity must not create retained samples or idle writes."""
    previous = _capacity(observed_at=NOW - 60.0)
    duplicate = _capacity(observed_at=NOW)
    out_of_order = _capacity(observed_at=NOW - 120.0, remaining=40.0)

    duplicate_result = admit_capacity_sample(previous, duplicate, HistoryContinuity.CONTINUOUS)
    old_result = admit_capacity_sample(previous, out_of_order, HistoryContinuity.CONTINUOUS)
    missing_result = admit_capacity_sample(previous, _capacity(remaining=40.0), HistoryContinuity.MISSING)

    assert duplicate_result.sample is None
    assert duplicate_result.disposition is SampleDisposition.DUPLICATE
    assert old_result.sample is None
    assert old_result.disposition is SampleDisposition.OUT_OF_ORDER
    assert missing_result.sample is None
    assert missing_result.disposition is SampleDisposition.IDENTITY_AMBIGUOUS
    assert missing_result.refusal_code == "identity_missing"


def test_admission_preserves_bounded_candidate_refusal_without_raw_error() -> None:
    """A rejected typed candidate keeps its code and disposition, but is not admitted."""
    candidate = _capacity(
        remaining=40.0,
        disposition=SampleDisposition.SOURCE_PARTIAL,
        refusal_code="source_partial",
    )

    result = admit_capacity_sample(None, candidate, HistoryContinuity.CONTINUOUS)

    assert result.sample is None
    assert result.disposition is SampleDisposition.SOURCE_PARTIAL
    assert result.refusal_code == "source_partial"
    assert not hasattr(result, "raw_error")


def test_day_week_and_month_summaries_expose_only_bounded_facts() -> None:
    """Summaries must not grow into scores, streaks, records, or behavioral judgments."""
    samples = (
        _capacity(observed_at=NOW - 6 * DAY, remaining=80.0, reset_epoch=NOW - 5 * DAY),
        _capacity(observed_at=NOW - 3 * DAY, remaining=60.0, reset_epoch=NOW - DAY),
        _capacity(observed_at=NOW - 60.0, remaining=20.0, reset_epoch=NOW + DAY),
    )

    day = summarize_capacity_history(samples, HistoryInterval.DAY, NOW)
    week = summarize_capacity_history(samples, HistoryInterval.SEVEN_DAYS, NOW)
    month = summarize_capacity_history(samples, HistoryInterval.THIRTY_DAYS, NOW)

    assert tuple(field.name for field in fields(type(week))) == (
        "observed_sample_count",
        "confirmed_reset_cycle_count",
        "minimum_remaining",
        "maximum_remaining",
        "no_observation_intervals",
    )
    assert day.observed_sample_count == 1
    assert day.minimum_remaining == 20.0
    assert day.maximum_remaining == 20.0
    assert week.observed_sample_count == 3
    assert week.confirmed_reset_cycle_count == 2
    assert week.minimum_remaining == 20.0
    assert week.maximum_remaining == 80.0
    assert all(type(item) is NoObservationInterval for item in week.no_observation_intervals)
    assert month.observed_sample_count == 3
    for forbidden in ("score", "ring", "streak", "record", "leaderboard", "judgment"):
        assert not hasattr(week, forbidden)


def test_reset_cycle_requires_a_later_nonmissing_reset_marker() -> None:
    """Losing reset evidence after a boundary cannot manufacture confirmation."""
    samples = (
        _capacity(
            observed_at=NOW - 2 * DAY,
            remaining=20.0,
            reset_epoch=NOW - DAY,
        ),
        _capacity(
            observed_at=NOW - 60.0,
            remaining=80.0,
            reset_epoch=None,
        ),
    )

    summary = summarize_capacity_history(samples, HistoryInterval.SEVEN_DAYS, NOW)

    assert summary.confirmed_reset_cycle_count == 0


def test_empty_summary_uses_explicit_no_observation_truth() -> None:
    """No retained sample is missing evidence, never an observed zero."""
    summary = summarize_capacity_history((), HistoryInterval.DAY, NOW)

    assert summary.observed_sample_count == 0
    assert summary.confirmed_reset_cycle_count == 0
    assert summary.minimum_remaining is NO_OBSERVATION
    assert summary.maximum_remaining is NO_OBSERVATION
    assert summary.no_observation_intervals == (NoObservationInterval(NOW - DAY, NOW),)


def test_sample_on_day_boundary_belongs_to_only_the_later_bucket() -> None:
    """Inclusive boundaries on both sides would hide a full unobserved day."""
    start = NOW - 7 * DAY
    summary = summarize_capacity_history(
        (_capacity(observed_at=start + DAY),),
        HistoryInterval.SEVEN_DAYS,
        NOW,
    )

    assert summary.no_observation_intervals == (
        NoObservationInterval(start, start + DAY),
        NoObservationInterval(start + 2 * DAY, NOW),
    )


def test_pruning_applies_age_then_count_with_only_supported_retention_days() -> None:
    """Old metadata and over-count records must be removed even if either bound alone fits."""
    with pytest.raises(HistoryValidationError):
        HistoryRetentionPolicy(14)
    samples = (
        *(_capacity(observed_at=NOW - index * 60.0, remaining=float(index % 101)) for index in range(4_100)),
        _capacity(observed_at=NOW - 8 * DAY),
    )

    retained = prune_capacity_history(samples, HistoryRetentionPolicy(7), NOW)

    assert len(retained) == 4_096
    assert retained[0].observed_at == NOW - 4_095 * 60.0
    assert retained[-1].observed_at == NOW
    assert (
        prune_capacity_history(
            (_capacity(),),
            HistoryRetentionPolicy(7, max_capacity_samples=0),
            NOW,
        )
        == ()
    )


def test_activity_sample_has_no_capacity_or_content_surface() -> None:
    """Local activity is separate from quota and cannot retain transcript-shaped data."""
    sample = _activity()

    assert sample.source_key == _source()
    assert not hasattr(sample, "lane_key")
    assert not hasattr(sample, "prompt")
