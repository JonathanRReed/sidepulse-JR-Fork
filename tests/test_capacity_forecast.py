from __future__ import annotations

import math
from dataclasses import replace

import pytest

from sidepulse.capacity_forecast import (
    MAX_CURRENT_OBSERVATION_AGE_SECONDS,
    MAX_FORECAST_EPOCH_SECONDS,
    MAX_SLOPE_INTERVAL_SECONDS,
    ForecastRefusalCode,
    PaceSignal,
    analyze_capacity_forecast,
)
from sidepulse.capacity_history import (
    CAPACITY_HISTORY_SCHEMA_VERSION,
    CapacityHistorySample,
)
from sidepulse.capacity_types import (
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ForecastConfidence,
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


def _source(instance: str = "primary") -> SourceKey:
    return SourceKey("codex", "codex", instance, "capacity")


def _lane(*, source: SourceKey | None = None, window: str = "session") -> QuotaLaneKey:
    return QuotaLaneKey(
        source=source or _source(),
        opaque_scope="account",
        pool="all",
        model=None,
        window=window,
        effect=QuotaEffect.ALL_WORKLOADS,
    )


def _health(
    *,
    source: SourceKey | None = None,
    kind: SourceHealthKind = SourceHealthKind.HEALTHY,
    observed_at: float = 6_000.0,
) -> CapacitySourceHealth:
    return CapacitySourceHealth(
        source=source or _source(),
        kind=kind,
        observed_at=observed_at,
        last_attempt_at=observed_at,
        retry_at=None,
        reason_code=None if kind is SourceHealthKind.HEALTHY else f"source_{kind.value}",
        has_last_known_good=kind in {SourceHealthKind.STALE, SourceHealthKind.PARTIAL},
    )


def _observation(
    *,
    lane: QuotaLaneKey | None = None,
    account: str | None = "acct-a",
    observed_at: float = 6_000.0,
    remaining: float = 20.0,
    value_state: ObservationState = ObservationState.OBSERVED,
    health_kind: SourceHealthKind = SourceHealthKind.HEALTHY,
    reset_state: ResetState = ResetState.FUTURE,
    reset_epoch: float | None = 10_000.0,
    window_minutes: float | None = 100.0,
) -> QuotaLaneObservation:
    key = lane or _lane()
    if remaining == 0.0 and value_state is ObservationState.OBSERVED:
        value_state = ObservationState.OBSERVED_ZERO
    return QuotaLaneObservation(
        key=key,
        semantic_name="Session window",
        horizon=QuotaHorizon.SHORT,
        value=CapacityValue(CapacityUnit.PERCENT_REMAINING, remaining, value_state),
        reset=ResetFact(reset_state, reset_epoch, window_minutes, observed_at),
        observed_at=observed_at,
        source_health=_health(source=key.source, kind=health_kind, observed_at=observed_at),
        account_discriminator=account,
    )


def _sample(
    observed_at: float,
    remaining: float,
    *,
    lane: QuotaLaneKey | None = None,
    account: str = "acct-a",
    reset_epoch: float | None = 10_000.0,
    window_minutes: float | None = 100.0,
    health: SourceHealthKind = SourceHealthKind.HEALTHY,
    disposition: SampleDisposition = SampleDisposition.ACCEPTED,
) -> CapacityHistorySample:
    return CapacityHistorySample(
        schema_version=CAPACITY_HISTORY_SCHEMA_VERSION,
        lane_key=lane or _lane(),
        account_discriminator=account,
        observed_at=observed_at,
        remaining=remaining,
        reset_epoch=reset_epoch,
        window_minutes=window_minutes,
        source_health=health,
        disposition=disposition,
        refusal_code=None if disposition is SampleDisposition.ACCEPTED else disposition.value,
    )


def _valid_history() -> tuple[CapacityHistorySample, ...]:
    return (
        _sample(4_200.0, 80.0),
        _sample(4_800.0, 60.0),
        _sample(5_400.0, 40.0),
    )


def _analyze(
    current: QuotaLaneObservation | None = None,
    history: tuple[CapacityHistorySample, ...] | None = None,
    **kwargs: object,
):
    return analyze_capacity_forecast(
        current or _observation(),
        _valid_history() if history is None else history,
        now=kwargs.pop("now", 6_000.0),
        history_consent=kwargs.pop("history_consent", True),
        continuity_disposition=kwargs.pop("continuity_disposition", SampleDisposition.ACCEPTED),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("current", "history", "kwargs", "expected"),
    (
        (
            _observation(account=None),
            _valid_history(),
            {},
            ForecastRefusalCode.NO_ACCOUNT_DISCRIMINATOR,
        ),
        (
            _observation(),
            _valid_history(),
            {"continuity_disposition": SampleDisposition.IDENTITY_AMBIGUOUS},
            ForecastRefusalCode.IDENTITY_CHANGED,
        ),
        (
            _observation(observed_at=4_100.0, remaining=95.0),
            (_sample(4_050.0, 96.0),),
            {"now": 4_100.0},
            ForecastRefusalCode.INSUFFICIENT_CYCLE_ELAPSED,
        ),
        (
            _observation(
                value_state=ObservationState.PARTIAL,
                health_kind=SourceHealthKind.PARTIAL,
            ),
            _valid_history(),
            {},
            ForecastRefusalCode.SOURCE_PARTIAL,
        ),
        (
            _observation(
                value_state=ObservationState.STALE,
                health_kind=SourceHealthKind.STALE,
            ),
            _valid_history(),
            {},
            ForecastRefusalCode.SOURCE_STALE,
        ),
        (
            _observation(reset_state=ResetState.UNKNOWN, reset_epoch=None),
            _valid_history(),
            {},
            ForecastRefusalCode.RESET_UNKNOWN,
        ),
        (
            _observation(reset_state=ResetState.DISPUTED),
            _valid_history(),
            {},
            ForecastRefusalCode.RESET_DISPUTED,
        ),
        (
            _observation(),
            (_sample(5_400.0, 40.0), _sample(4_800.0, 60.0)),
            {},
            ForecastRefusalCode.HISTORY_OUT_OF_ORDER,
        ),
        (
            _observation(),
            (*_valid_history(), _sample(5_500.0, 30.0, lane=_lane(window="weekly"))),
            {},
            ForecastRefusalCode.CROSS_LANE_HISTORY,
        ),
        (
            _observation(),
            (*_valid_history(), _sample(5_500.0, 30.0, account="acct-b")),
            {},
            ForecastRefusalCode.CROSS_ACCOUNT_HISTORY,
        ),
        (
            _observation(),
            (_sample(4_200.0, 80.0), _sample(4_800.0, 85.0)),
            {},
            ForecastRefusalCode.NONMONOTONIC_USAGE,
        ),
        (
            _observation(),
            _valid_history(),
            {"now": math.nan},
            ForecastRefusalCode.INVALID_CLOCK,
        ),
        (
            _observation(),
            _valid_history(),
            {"history_consent": False},
            ForecastRefusalCode.HISTORY_CONSENT_REQUIRED,
        ),
    ),
)
def test_authority_refusals_never_emit_a_numeric_forecast(
    current: QuotaLaneObservation,
    history: tuple[CapacityHistorySample, ...],
    kwargs: dict[str, object],
    expected: ForecastRefusalCode,
) -> None:
    """Every failed authority gate must withhold pace and exhaustion numbers."""
    result = _analyze(current, history, **kwargs)

    assert result.confidence is ForecastConfidence.UNAVAILABLE
    assert result.refusal_code is expected
    assert result.pace_signal is None
    assert result.runway is None


def test_fewer_than_three_valid_slopes_allows_only_categorical_low_pace() -> None:
    """Removing a third slope must make numeric exhaustion unavailable."""
    result = _analyze(history=(_sample(4_800.0, 60.0), _sample(5_400.0, 40.0)))

    assert result.confidence is ForecastConfidence.LOW_LINEAR
    assert result.refusal_code is ForecastRefusalCode.INSUFFICIENT_SLOPES
    assert result.pace_signal is PaceSignal.DECLINING
    assert result.runway is None


def test_unbounded_history_interval_refuses_instead_of_extrapolating() -> None:
    """Removing the maximum interval fence must make this source gap emit a number."""
    current_at = MAX_SLOPE_INTERVAL_SECONDS + 100_000.0
    reset_at = current_at + 200_000.0
    window_minutes = (reset_at - 1_000.0) / 60.0
    current = _observation(
        observed_at=current_at,
        remaining=20.0,
        reset_epoch=reset_at,
        window_minutes=window_minutes,
    )
    history = (_sample(1_000.0, 80.0, reset_epoch=reset_at, window_minutes=window_minutes),)

    result = _analyze(current, history, now=current_at)

    assert result.confidence is ForecastConfidence.UNAVAILABLE
    assert result.refusal_code is ForecastRefusalCode.INTERVAL_UNBOUNDED
    assert result.pace_signal is None
    assert result.runway is None


def test_duplicate_timestamps_and_rejected_stale_points_are_excluded() -> None:
    """Counting duplicate or stale points must distort the robust interval."""
    history = (
        _sample(4_200.0, 80.0),
        _sample(4_200.0, 80.0),
        _sample(
            4_500.0,
            70.0,
            health=SourceHealthKind.STALE,
            disposition=SampleDisposition.SOURCE_STALE,
        ),
        _sample(4_800.0, 60.0),
        _sample(5_400.0, 40.0),
    )

    result = _analyze(history=history)

    assert result.confidence is ForecastConfidence.MEDIUM_OBSERVED
    assert result.refusal_code is None
    assert result.runway is not None
    assert result.runway.earliest_exhaustion_epoch == pytest.approx(6_600.0)
    assert result.runway.latest_exhaustion_epoch == pytest.approx(6_600.0)


def test_reset_transitions_are_not_treated_as_usage_slopes() -> None:
    """Treating recovery across a reset as burn must corrupt the current-cycle runway."""
    history = (
        _sample(100.0, 90.0, reset_epoch=4_000.0, window_minutes=50.0),
        _sample(3_900.0, 5.0, reset_epoch=4_000.0, window_minutes=50.0),
        *_valid_history(),
    )

    result = _analyze(history=history)

    assert result.confidence is ForecastConfidence.MEDIUM_OBSERVED
    assert result.runway is not None
    assert result.runway.earliest_exhaustion_epoch == pytest.approx(6_600.0)
    assert result.runway.latest_exhaustion_epoch == pytest.approx(6_600.0)


def test_robust_runway_is_an_interval_bounded_by_the_confirmed_reset() -> None:
    """Replacing the robust envelope with a point ETA or post-reset claim must fail."""
    result = _analyze()

    assert result.confidence is ForecastConfidence.MEDIUM_OBSERVED
    assert result.horizon is QuotaHorizon.SHORT
    assert result.pace_signal is PaceSignal.DECLINING
    assert result.runway is not None
    assert math.isfinite(result.runway.earliest_exhaustion_epoch)
    assert math.isfinite(result.runway.latest_exhaustion_epoch)
    assert 6_000.0 <= result.runway.earliest_exhaustion_epoch
    assert result.runway.earliest_exhaustion_epoch <= result.runway.latest_exhaustion_epoch
    assert result.runway.latest_exhaustion_epoch <= 10_000.0
    assert not hasattr(result.runway, "point_eta")
    assert not hasattr(result.runway, "session_equivalents")
    assert not hasattr(result.runway, "token_equivalents")


def test_idle_and_post_reset_exhaustion_abstain_without_manufacturing_a_boundary() -> None:
    """A zero or too-slow slope must never become a finite exhaustion claim."""
    idle = _analyze(
        current=_observation(remaining=100.0),
        history=(
            _sample(4_200.0, 100.0),
            _sample(4_800.0, 100.0),
            _sample(5_400.0, 100.0),
        ),
    )
    slow = _analyze(
        history=(
            _sample(4_200.0, 20.9),
            _sample(4_800.0, 20.6),
            _sample(5_400.0, 20.3),
        )
    )

    assert idle.confidence is ForecastConfidence.MEDIUM_OBSERVED
    assert idle.pace_signal is PaceSignal.IDLE
    assert idle.refusal_code is ForecastRefusalCode.NO_POSITIVE_BURN
    assert idle.runway is None
    assert slow.confidence is ForecastConfidence.MEDIUM_OBSERVED
    assert slow.refusal_code is ForecastRefusalCode.EXHAUSTION_NOT_BEFORE_RESET
    assert slow.runway is None


def test_five_complete_identity_matched_cycles_raise_only_internal_confidence() -> None:
    """Counting incomplete or mismatched cycles must not produce historical confidence."""
    reset_epochs = (70_000.0, 76_000.0, 82_000.0, 88_000.0, 94_000.0)
    history: list[CapacityHistorySample] = []
    for reset_epoch in reset_epochs:
        cycle_start = reset_epoch - 6_000.0
        history.extend(
            (
                _sample(cycle_start + 100.0, 95.0, reset_epoch=reset_epoch),
                _sample(reset_epoch - 100.0, 5.0, reset_epoch=reset_epoch),
            )
        )
    history.extend(
        (
            _sample(94_200.0, 80.0, reset_epoch=100_000.0),
            _sample(94_800.0, 60.0, reset_epoch=100_000.0),
            _sample(95_400.0, 40.0, reset_epoch=100_000.0),
        )
    )
    current = _observation(
        observed_at=96_000.0,
        reset_epoch=100_000.0,
        remaining=20.0,
    )

    result = _analyze(current, tuple(history), now=96_000.0)

    assert result.confidence is ForecastConfidence.HIGH_HISTORICAL
    assert result.runway is not None
    assert result.runway.latest_exhaustion_epoch <= 100_000.0


def test_history_is_bounded_before_any_slope_work() -> None:
    """Removing the history bound must permit unbounded diagnostic work."""
    history = tuple(_sample(float(index + 1), 100.0) for index in range(4_097))

    result = _analyze(history=history)

    assert result.confidence is ForecastConfidence.UNAVAILABLE
    assert result.refusal_code is ForecastRefusalCode.HISTORY_TOO_LARGE
    assert result.runway is None


def test_current_observation_after_injected_now_refuses_invalid_clock() -> None:
    """Using a future sample with an earlier clock must not create negative elapsed time."""
    result = _analyze(now=5_999.0)

    assert result.confidence is ForecastConfidence.UNAVAILABLE
    assert result.refusal_code is ForecastRefusalCode.INVALID_CLOCK
    assert result.runway is None


def test_old_healthy_observation_is_stale_by_age() -> None:
    """Trusting a healthy label forever must turn an old sample into fresh authority."""
    observed_at = 6_000.0 - MAX_CURRENT_OBSERVATION_AGE_SECONDS - 1.0
    current = _observation(observed_at=observed_at)

    result = _analyze(current, (), now=6_000.0)

    assert result.confidence is ForecastConfidence.UNAVAILABLE
    assert result.refusal_code is ForecastRefusalCode.SOURCE_STALE
    assert result.runway is None


def test_reset_that_has_passed_relative_to_injected_clock_is_not_future() -> None:
    """Comparing reset only with observation time must claim beyond a passed boundary."""
    current = _observation(
        observed_at=9_999.0,
        reset_epoch=10_000.0,
        window_minutes=100.0,
    )

    result = _analyze(current, (), now=10_000.5)

    assert result.confidence is ForecastConfidence.UNAVAILABLE
    assert result.refusal_code is ForecastRefusalCode.RESET_NOT_FUTURE
    assert result.runway is None


def test_reset_observation_and_window_duration_must_be_stable() -> None:
    """Mixing stale reset truth or a changed duration must fabricate cycle progress."""
    current = _observation()
    stale_reset = replace(
        current,
        reset=ResetFact(ResetState.FUTURE, 10_000.0, 100.0, 5_000.0),
    )
    changed_duration_history = (
        _sample(4_200.0, 80.0, window_minutes=90.0),
        _sample(4_800.0, 60.0),
        _sample(5_400.0, 40.0),
    )

    for result in (
        _analyze(stale_reset, _valid_history()),
        _analyze(current, changed_duration_history),
    ):
        assert result.confidence is ForecastConfidence.UNAVAILABLE
        assert result.refusal_code is ForecastRefusalCode.RESET_UNSTABLE
        assert result.runway is None


def test_subminimum_nonmonotonic_interval_is_excluded_before_slope_validation() -> None:
    """Treating a subminimum transition as a slope must reject otherwise useful evidence."""
    history = (
        _sample(4_200.0, 80.0),
        _sample(4_201.0, 85.0),
        _sample(4_800.0, 60.0),
        _sample(5_400.0, 40.0),
    )

    result = _analyze(history=history)

    assert result.confidence is ForecastConfidence.MEDIUM_OBSERVED
    assert result.refusal_code is not ForecastRefusalCode.NONMONOTONIC_USAGE


def test_different_duration_cycles_do_not_raise_historical_confidence() -> None:
    """Counting cycles from a different reset duration must overstate evidence quality."""
    history: list[CapacityHistorySample] = []
    for reset_epoch in (70_000.0, 76_000.0, 82_000.0, 88_000.0, 94_000.0):
        duration = 3_000.0
        history.extend(
            (
                _sample(
                    reset_epoch - duration + 100.0,
                    95.0,
                    reset_epoch=reset_epoch,
                    window_minutes=50.0,
                ),
                _sample(
                    reset_epoch - 100.0,
                    5.0,
                    reset_epoch=reset_epoch,
                    window_minutes=50.0,
                ),
            )
        )
    history.extend(
        (
            _sample(94_200.0, 80.0, reset_epoch=100_000.0),
            _sample(94_800.0, 60.0, reset_epoch=100_000.0),
            _sample(95_400.0, 40.0, reset_epoch=100_000.0),
        )
    )
    current = _observation(
        observed_at=96_000.0,
        reset_epoch=100_000.0,
        remaining=20.0,
    )

    result = _analyze(current, tuple(history), now=96_000.0)

    assert result.confidence is ForecastConfidence.MEDIUM_OBSERVED


@pytest.mark.parametrize("now", (MAX_FORECAST_EPOCH_SECONDS + 1.0, float("inf")))
def test_injected_clock_is_finite_and_epoch_bounded(now: float) -> None:
    """Removing the epoch ceiling must admit unbounded forecast arithmetic."""
    result = _analyze(now=now)

    assert result.confidence is ForecastConfidence.UNAVAILABLE
    assert result.refusal_code is ForecastRefusalCode.INVALID_CLOCK
    assert result.runway is None
