from __future__ import annotations

from dataclasses import replace

import pytest

from sidepulse.capacity_calibration import (
    CALIBRATION_SCHEMA_VERSION,
    ROBUST_METHOD_VERSION,
    CalibrationComparison,
    CalibrationCycle,
    CalibrationReport,
    CalibrationScore,
    ForecastClaimClass,
    ForecastIdentityClass,
    ForecastReleaseAuthority,
    apply_forecast_release,
    evaluate_forecast_calibration,
    naive_baseline_exhaustion,
)
from sidepulse.capacity_forecast import ForecastRefusalCode, analyze_capacity_forecast
from sidepulse.capacity_types import (
    ForecastReleaseState,
    QuotaHorizon,
    SampleDisposition,
)
from tests.test_capacity_forecast import _lane, _observation, _sample, _valid_history


def _cycle(
    *,
    remaining: tuple[float, ...] = (80.0, 60.0, 40.0, 20.0, 0.0),
    times: tuple[float, ...] = (100.0, 200.0, 300.0, 400.0, 500.0),
    reset_epoch: float = 1_000.0,
    actual_exhaustion_epoch: float | None = 500.0,
    horizon: QuotaHorizon = QuotaHorizon.SHORT,
) -> CalibrationCycle:
    samples = tuple(
        _sample(
            observed_at,
            value,
            reset_epoch=reset_epoch,
            window_minutes=15.0,
        )
        for observed_at, value in zip(times, remaining, strict=True)
    )
    return CalibrationCycle(
        identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
        horizon=horizon,
        lane_key=samples[0].lane_key,
        account_discriminator=samples[0].account_discriminator,
        reset_epoch=reset_epoch,
        window_minutes=15.0,
        samples=samples,
        actual_exhaustion_epoch=actual_exhaustion_epoch,
    )


def _diagnostic():
    return analyze_capacity_forecast(
        _observation(),
        _valid_history(),
        now=6_000.0,
        history_consent=True,
        continuity_disposition=SampleDisposition.ACCEPTED,
    )


def _score(
    *,
    error: float | None,
    false_warning: float = 0.05,
    miss: float = 0.05,
    abstention: float = 0.05,
    coverage: float | None = 0.9,
    in_sample_error: float | None = None,
) -> CalibrationScore:
    return CalibrationScore(
        sample_count=100,
        eligible_cycle_count=10,
        mean_absolute_timing_error=error,
        interval_coverage=coverage,
        false_warning_rate=false_warning,
        miss_rate=miss,
        abstention_rate=abstention,
        in_sample_mean_absolute_timing_error=in_sample_error,
    )


def _report(
    *,
    candidate: CalibrationScore | None = None,
    baseline: CalibrationScore | None = None,
) -> CalibrationReport:
    return CalibrationReport(
        schema_version=CALIBRATION_SCHEMA_VERSION,
        method_version=ROBUST_METHOD_VERSION,
        comparisons=(
            CalibrationComparison(
                identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
                horizon=QuotaHorizon.SHORT,
                candidate=candidate or _score(error=40.0),
                baseline=baseline or _score(error=80.0, false_warning=0.1, miss=0.1),
            ),
        ),
        origin_audits=(),
    )


def _authority(
    *,
    state: ForecastReleaseState = ForecastReleaseState.AUTHORIZED,
    issued_at: float = 1_000.0,
    expires_at: float = 20_000.0,
) -> ForecastReleaseAuthority:
    return ForecastReleaseAuthority(
        method_version=ROBUST_METHOD_VERSION,
        schema_version=CALIBRATION_SCHEMA_VERSION,
        identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
        horizon=QuotaHorizon.SHORT,
        permitted_claim_classes=(ForecastClaimClass.EXHAUSTION_ENVELOPE,),
        calibration_sample_min=50,
        calibration_sample_max=200,
        issued_at=issued_at,
        expires_at=expires_at,
        release_state=state,
    )


def test_naive_baseline_is_the_most_recent_valid_slope_carried_forward() -> None:
    """Replacing the frozen latest slope with an average must change this literal result."""
    samples = (
        _sample(100.0, 100.0, reset_epoch=2_000.0),
        _sample(200.0, 80.0, reset_epoch=2_000.0),
        _sample(300.0, 70.0, reset_epoch=2_000.0),
    )

    prediction = naive_baseline_exhaustion(
        samples,
        origin_at=300.0,
        remaining=70.0,
        reset_epoch=2_000.0,
    )

    assert prediction == pytest.approx(1_000.0)


def test_naive_baseline_abstains_when_exhaustion_is_not_before_reset() -> None:
    """Allowing a baseline claim beyond reset must create a misleading comparator."""
    samples = (
        _sample(100.0, 100.0, reset_epoch=500.0),
        _sample(200.0, 99.0, reset_epoch=500.0),
    )

    assert (
        naive_baseline_exhaustion(
            samples,
            origin_at=200.0,
            remaining=99.0,
            reset_epoch=500.0,
        )
        is None
    )


def test_naive_baseline_refuses_cross_identity_and_out_of_order_history() -> None:
    """Using the latest pair without validating its identity must contaminate baseline truth."""
    cross_account = (
        _sample(100.0, 100.0, reset_epoch=2_000.0),
        _sample(200.0, 80.0, reset_epoch=2_000.0, account="acct-b"),
    )
    cross_lane = (
        _sample(100.0, 100.0, reset_epoch=2_000.0),
        _sample(
            200.0,
            80.0,
            reset_epoch=2_000.0,
            lane=_lane(window="weekly"),
        ),
    )
    out_of_order = (
        _sample(200.0, 80.0, reset_epoch=2_000.0),
        _sample(100.0, 100.0, reset_epoch=2_000.0),
    )

    for samples in (cross_account, cross_lane, out_of_order):
        assert (
            naive_baseline_exhaustion(
                samples,
                origin_at=300.0,
                remaining=70.0,
                reset_epoch=2_000.0,
            )
            is None
        )


def test_rolling_origin_metrics_cover_constant_burn_without_leakage() -> None:
    """Using the target sample at an origin must violate the strict audit ordering."""
    report = evaluate_forecast_calibration((_cycle(),))
    comparison = report.comparisons[0]

    assert comparison.identity_class is ForecastIdentityClass.OPAQUE_ACCOUNT
    assert comparison.horizon is QuotaHorizon.SHORT
    assert comparison.candidate.sample_count > 0
    assert comparison.candidate.eligible_cycle_count == 1
    assert comparison.candidate.mean_absolute_timing_error == pytest.approx(0.0)
    assert comparison.candidate.interval_coverage == pytest.approx(1.0)
    assert comparison.candidate.false_warning_rate == pytest.approx(0.0)
    assert comparison.candidate.miss_rate == pytest.approx(0.0)
    assert all(audit.origin_at < audit.target_at for audit in report.origin_audits)


def test_future_samples_after_target_cannot_improve_rolling_origin_results() -> None:
    """Admitting post-target samples must leak future truth into calibration."""
    base = _cycle()
    leaked = replace(
        base,
        samples=(*base.samples, _sample(600.0, 0.0, reset_epoch=1_000.0, window_minutes=15.0)),
    )

    without_future = evaluate_forecast_calibration((base,)).comparisons
    with_future = evaluate_forecast_calibration((leaked,)).comparisons

    assert with_future == without_future


def test_long_idle_never_exhausts_without_false_warning() -> None:
    """Converting idle observations into a warning must raise false-warning rate."""
    report = evaluate_forecast_calibration(
        (
            _cycle(
                remaining=(100.0, 100.0, 100.0, 100.0, 100.0),
                actual_exhaustion_epoch=None,
            ),
        )
    )
    score = report.comparisons[0].candidate

    assert score.false_warning_rate == pytest.approx(0.0)
    assert score.abstention_rate == pytest.approx(1.0)
    assert score.mean_absolute_timing_error is None


@pytest.mark.parametrize(
    "cycle",
    (
        _cycle(remaining=(90.0, 88.0, 50.0, 49.0, 0.0)),
        _cycle(times=(100.0, 250.0, 400.0, 490.0, 500.0)),
        _cycle(remaining=(80.0, 79.0, 78.0, 20.0, 0.0)),
        _cycle(
            remaining=(90.0, 80.0, 80.0, 80.0, 80.0),
            actual_exhaustion_epoch=None,
        ),
        _cycle(
            times=(100.0, 200.0, 300.0, 400.0, 900.0),
            actual_exhaustion_epoch=900.0,
        ),
    ),
)
def test_bursty_reset_near_early_never_and_gap_cases_remain_bounded(
    cycle: CalibrationCycle,
) -> None:
    """Any synthetic adversary producing an unbounded metric must fail validation."""
    report = evaluate_forecast_calibration((cycle,))
    comparison = report.comparisons[0]

    for score in (comparison.candidate, comparison.baseline):
        assert 0.0 <= score.false_warning_rate <= 1.0
        assert 0.0 <= score.miss_rate <= 1.0
        assert 0.0 <= score.abstention_rate <= 1.0
        if score.interval_coverage is not None:
            assert 0.0 <= score.interval_coverage <= 1.0


def test_reports_are_partitioned_by_identity_class_and_horizon() -> None:
    """Merging horizons must let one source's performance authorize another."""
    report = evaluate_forecast_calibration((_cycle(horizon=QuotaHorizon.SHORT), _cycle(horizon=QuotaHorizon.LONG)))

    assert tuple((row.identity_class, row.horizon) for row in report.comparisons) == (
        (ForecastIdentityClass.OPAQUE_ACCOUNT, QuotaHorizon.SHORT),
        (ForecastIdentityClass.OPAQUE_ACCOUNT, QuotaHorizon.LONG),
    )


@pytest.mark.parametrize(
    ("authority", "now", "expected"),
    (
        (None, 6_000.0, ForecastRefusalCode.AUTHORITY_MISSING),
        (
            ForecastReleaseAuthority.withheld(),
            6_000.0,
            ForecastRefusalCode.AUTHORITY_WITHHELD,
        ),
        (
            _authority(state=ForecastReleaseState.REVOKED),
            6_000.0,
            ForecastRefusalCode.RELEASE_AUTHORITY_REVOKED,
        ),
        (
            _authority(expires_at=5_999.0),
            6_000.0,
            ForecastRefusalCode.AUTHORITY_EXPIRED,
        ),
        (
            _authority(issued_at=6_001.0),
            6_000.0,
            ForecastRefusalCode.AUTHORITY_NOT_YET_VALID,
        ),
    ),
)
def test_missing_withheld_revoked_or_invalid_time_authority_releases_no_number(
    authority: ForecastReleaseAuthority | None,
    now: float,
    expected: ForecastRefusalCode,
) -> None:
    """Relaxing any release-state or time gate must expose numeric runway."""
    released = apply_forecast_release(
        _diagnostic(),
        authority,
        _report(),
        identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
        horizon=QuotaHorizon.SHORT,
        claim_class=ForecastClaimClass.EXHAUSTION_ENVELOPE,
        now=now,
    )

    assert released.status_text == "Forecast unavailable"
    assert released.refusal_code is expected
    assert released.earliest_exhaustion_epoch is None
    assert released.latest_exhaustion_epoch is None


def test_authority_scope_method_claim_and_sample_bounds_must_match() -> None:
    """Ignoring an authority scope mismatch must authorize unreviewed evidence."""
    variants = (
        replace(_authority(), method_version="other-v1"),
        replace(_authority(), horizon=QuotaHorizon.LONG),
        replace(_authority(), permitted_claim_classes=(ForecastClaimClass.PACE_DIAGNOSTIC,)),
        replace(_authority(), calibration_sample_min=101),
    )

    for authority in variants:
        released = apply_forecast_release(
            _diagnostic(),
            authority,
            _report(),
            identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
            horizon=QuotaHorizon.SHORT,
            claim_class=ForecastClaimClass.EXHAUSTION_ENVELOPE,
            now=6_000.0,
        )

        assert released.status_text == "Forecast unavailable"
        assert released.earliest_exhaustion_epoch is None
        assert released.latest_exhaustion_epoch is None


def test_caller_cannot_relabel_a_short_diagnostic_as_a_long_horizon() -> None:
    """Matching caller, authority, and report labels must not override diagnostic truth."""
    short_diagnostic = _diagnostic()
    long_authority = replace(_authority(), horizon=QuotaHorizon.LONG)
    short_comparison = _report().comparisons[0]
    long_report = replace(
        _report(),
        comparisons=(replace(short_comparison, horizon=QuotaHorizon.LONG),),
    )

    released = apply_forecast_release(
        short_diagnostic,
        long_authority,
        long_report,
        identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
        horizon=QuotaHorizon.LONG,
        claim_class=ForecastClaimClass.EXHAUSTION_ENVELOPE,
        now=6_000.0,
    )

    assert released.status_text == "Forecast unavailable"
    assert released.refusal_code is ForecastRefusalCode.AUTHORITY_MISMATCHED
    assert released.earliest_exhaustion_epoch is None
    assert released.latest_exhaustion_epoch is None


def test_attractive_in_sample_fit_cannot_override_worse_rolling_origin_error() -> None:
    """Consulting in-sample fit must authorize a candidate that loses prospectively."""
    report = _report(
        candidate=_score(error=100.0, in_sample_error=0.0),
        baseline=_score(error=50.0, false_warning=0.1, miss=0.1),
    )

    released = apply_forecast_release(
        _diagnostic(),
        _authority(),
        report,
        identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
        horizon=QuotaHorizon.SHORT,
        claim_class=ForecastClaimClass.EXHAUSTION_ENVELOPE,
        now=6_000.0,
    )

    assert released.refusal_code is ForecastRefusalCode.BASELINE_NOT_BEATEN
    assert released.earliest_exhaustion_epoch is None
    assert released.latest_exhaustion_epoch is None


@pytest.mark.parametrize(
    ("candidate", "expected"),
    (
        (
            _score(error=40.0, false_warning=0.11),
            ForecastRefusalCode.FALSE_WARNING_REGRESSED,
        ),
        (
            _score(error=40.0, miss=0.11),
            ForecastRefusalCode.MISS_RATE_REGRESSED,
        ),
    ),
)
def test_candidate_cannot_regress_baseline_warning_or_miss_rate(
    candidate: CalibrationScore,
    expected: ForecastRefusalCode,
) -> None:
    """Checking primary timing error alone must authorize harmful regressions."""
    released = apply_forecast_release(
        _diagnostic(),
        _authority(),
        _report(
            candidate=candidate,
            baseline=_score(error=80.0, false_warning=0.1, miss=0.1),
        ),
        identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
        horizon=QuotaHorizon.SHORT,
        claim_class=ForecastClaimClass.EXHAUSTION_ENVELOPE,
        now=6_000.0,
    )

    assert released.refusal_code is expected
    assert released.earliest_exhaustion_epoch is None
    assert released.latest_exhaustion_epoch is None


@pytest.mark.parametrize(
    ("candidate", "expected"),
    (
        (
            _score(error=40.0, false_warning=0.16),
            ForecastRefusalCode.FALSE_WARNING_REGRESSED,
        ),
        (
            _score(error=40.0, miss=0.21),
            ForecastRefusalCode.MISS_RATE_REGRESSED,
        ),
    ),
)
def test_candidate_must_remain_under_preregistered_warning_and_miss_ceilings(
    candidate: CalibrationScore,
    expected: ForecastRefusalCode,
) -> None:
    """Beating a permissive baseline must not bypass fixed safety ceilings."""
    released = apply_forecast_release(
        _diagnostic(),
        _authority(),
        _report(
            candidate=candidate,
            baseline=_score(error=80.0, false_warning=0.3, miss=0.3),
        ),
        identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
        horizon=QuotaHorizon.SHORT,
        claim_class=ForecastClaimClass.EXHAUSTION_ENVELOPE,
        now=6_000.0,
    )

    assert released.refusal_code is expected
    assert released.earliest_exhaustion_epoch is None
    assert released.latest_exhaustion_epoch is None


def test_calibration_cycle_actual_exhaustion_must_match_observed_zero() -> None:
    """An unobserved target or ignored observed zero must not become calibration truth."""
    with pytest.raises(ValueError):
        _cycle(remaining=(80.0, 60.0, 40.0, 20.0, 10.0))
    with pytest.raises(ValueError):
        _cycle(
            remaining=(80.0, 60.0, 40.0, 20.0, 0.0),
            actual_exhaustion_epoch=None,
        )


def test_explicit_matching_authority_can_release_only_the_bounded_interval() -> None:
    """Dropping calibration gates or adding a point ETA must change this release shape."""
    released = apply_forecast_release(
        _diagnostic(),
        _authority(),
        _report(),
        identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
        horizon=QuotaHorizon.SHORT,
        claim_class=ForecastClaimClass.EXHAUSTION_ENVELOPE,
        now=6_000.0,
    )

    assert released.status_text is None
    assert released.refusal_code is None
    assert released.earliest_exhaustion_epoch == pytest.approx(6_600.0)
    assert released.latest_exhaustion_epoch == pytest.approx(6_600.0)
    assert not hasattr(released, "point_eta")
    assert not hasattr(released, "probability")


@pytest.mark.parametrize(
    "claim_class",
    (
        ForecastClaimClass.PACE_DIAGNOSTIC,
        ForecastClaimClass.EXHAUSTION_PROBABILITY,
    ),
)
def test_unimplemented_claim_classes_cannot_release_envelope_numbers(
    claim_class: ForecastClaimClass,
) -> None:
    """Matching a non-envelope claim must not reuse the exhaustion interval shape."""
    authority = replace(
        _authority(),
        permitted_claim_classes=(claim_class,),
    )

    released = apply_forecast_release(
        _diagnostic(),
        authority,
        _report(),
        identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
        horizon=QuotaHorizon.SHORT,
        claim_class=claim_class,
        now=6_000.0,
    )

    assert released.status_text == "Forecast unavailable"
    assert released.refusal_code is ForecastRefusalCode.AUTHORITY_MISMATCHED
    assert released.earliest_exhaustion_epoch is None
    assert released.latest_exhaustion_epoch is None


def test_authority_records_are_versioned_bounded_and_cannot_self_authorize() -> None:
    """A local calibration result must not mutate withheld authority into authorized."""
    withheld = ForecastReleaseAuthority.withheld()
    evaluate_forecast_calibration((_cycle(),))

    assert withheld.release_state is ForecastReleaseState.WITHHELD
    assert withheld.schema_version == CALIBRATION_SCHEMA_VERSION
    assert withheld.permitted_claim_classes == ()
    with pytest.raises(ValueError):
        replace(_authority(), calibration_sample_min=-1)
    with pytest.raises(ValueError):
        replace(_authority(), expires_at=float("inf"))
    with pytest.raises(ValueError):
        replace(_authority(), permitted_claim_classes=tuple(ForecastClaimClass) * 20)
