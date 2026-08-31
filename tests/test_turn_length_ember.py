from __future__ import annotations

from dataclasses import FrozenInstanceError
from itertools import pairwise

import pytest

from sidepulse.turn_length_ember import (
    DEFAULT_BREATHE_PERIOD_SECONDS,
    FAIR_THERMAL_BREATHE_PERIOD_SECONDS,
    SEMANTIC_DISCLOSURE,
    TURN_AGE_BANDS,
    EmberDegradation,
    EmberMotion,
    ThermalState,
    TurnAgeBucket,
    TurnLengthEmberError,
    age_band_for_elapsed,
    plan_turn_length_ember,
)


@pytest.mark.parametrize(
    ("elapsed", "bucket", "label", "minimum", "maximum"),
    [
        (0, TurnAgeBucket.UNDER_TWO_MINUTES, "Under 2 minutes", 0.0, 120.0),
        (119.999, TurnAgeBucket.UNDER_TWO_MINUTES, "Under 2 minutes", 0.0, 120.0),
        (
            120,
            TurnAgeBucket.TWO_TO_TEN_MINUTES,
            "2 to under 10 minutes",
            120.0,
            600.0,
        ),
        (
            599.999,
            TurnAgeBucket.TWO_TO_TEN_MINUTES,
            "2 to under 10 minutes",
            120.0,
            600.0,
        ),
        (
            600,
            TurnAgeBucket.TEN_TO_THIRTY_MINUTES,
            "10 to under 30 minutes",
            600.0,
            1_800.0,
        ),
        (
            1_800,
            TurnAgeBucket.THIRTY_MINUTES_OR_MORE,
            "30 minutes or more",
            1_800.0,
            None,
        ),
        (
            86_400,
            TurnAgeBucket.THIRTY_MINUTES_OR_MORE,
            "30 minutes or more",
            1_800.0,
            None,
        ),
    ],
)
def test_age_bands_have_disclosed_half_open_boundaries(
    elapsed, bucket, label, minimum, maximum
) -> None:
    band = age_band_for_elapsed(elapsed)

    assert band.bucket is bucket
    assert band.label == label
    assert band.minimum_seconds == minimum
    assert band.maximum_seconds == maximum


def test_age_band_contract_is_complete_ordered_and_broad() -> None:
    assert tuple(band.bucket for band in TURN_AGE_BANDS) == tuple(TurnAgeBucket)
    assert TURN_AGE_BANDS[0].minimum_seconds == 0.0
    for previous, current in pairwise(TURN_AGE_BANDS):
        assert previous.maximum_seconds == current.minimum_seconds
        assert current.minimum_seconds - previous.minimum_seconds >= 120.0
    assert TURN_AGE_BANDS[-1].maximum_seconds is None


def test_normal_plan_is_renderer_shaped_and_only_claims_elapsed_time() -> None:
    plan = plan_turn_length_ember(elapsed_seconds=300)

    assert plan.visible is True
    assert plan.bucket is TurnAgeBucket.TWO_TO_TEN_MINUTES
    assert plan.age_label == "2 to under 10 minutes"
    assert plan.motion is EmberMotion.BREATHE
    assert plan.breathe_period_seconds == DEFAULT_BREATHE_PERIOD_SECONDS
    assert plan.animated is True
    assert 0.0 <= plan.saturation <= 1.0
    assert 0.0 <= plan.luminance <= 1.0
    assert plan.degradation == ()
    assert SEMANTIC_DISCLOSURE in plan.accessibility_text
    assert "progress" in plan.accessibility_text
    assert "productivity" in plan.accessibility_text
    assert "difficulty" in plan.accessibility_text


def test_visual_values_change_only_at_disclosed_bucket_boundaries() -> None:
    before = plan_turn_length_ember(elapsed_seconds=599.999)
    boundary = plan_turn_length_ember(elapsed_seconds=600)
    within = plan_turn_length_ember(elapsed_seconds=1_799.999)

    assert (before.saturation, before.luminance) != (
        boundary.saturation,
        boundary.luminance,
    )
    assert (boundary.saturation, boundary.luminance) == (
        within.saturation,
        within.luminance,
    )


def test_reduce_motion_preserves_age_semantics_as_steady_light() -> None:
    regular = plan_turn_length_ember(elapsed_seconds=900)
    reduced = plan_turn_length_ember(elapsed_seconds=900, reduce_motion=True)

    assert reduced.visible is True
    assert reduced.bucket is regular.bucket
    assert reduced.age_label == regular.age_label
    assert reduced.saturation == regular.saturation
    assert reduced.luminance == regular.luminance
    assert reduced.motion is EmberMotion.STEADY
    assert reduced.breathe_period_seconds is None
    assert reduced.animated is False
    assert reduced.degradation == (EmberDegradation.REDUCE_MOTION,)


def test_low_power_clamps_energy_and_removes_animation() -> None:
    plan = plan_turn_length_ember(elapsed_seconds=3_600, low_power=True)

    assert plan.visible is True
    assert plan.saturation == 0.64
    assert plan.luminance == 0.30
    assert plan.motion is EmberMotion.STEADY
    assert plan.breathe_period_seconds is None
    assert plan.degradation == (EmberDegradation.LOW_POWER,)


def test_fair_thermal_pressure_slows_and_clamps_the_breathe() -> None:
    plan = plan_turn_length_ember(elapsed_seconds=3_600, thermal=ThermalState.FAIR)

    assert plan.visible is True
    assert plan.saturation == 0.70
    assert plan.luminance == 0.38
    assert plan.motion is EmberMotion.BREATHE
    assert plan.breathe_period_seconds == FAIR_THERMAL_BREATHE_PERIOD_SECONDS
    assert plan.degradation == (EmberDegradation.THERMAL_FAIR,)


def test_serious_thermal_pressure_keeps_a_dim_static_age_signal() -> None:
    plan = plan_turn_length_ember(elapsed_seconds=3_600, thermal="SERIOUS")

    assert plan.visible is True
    assert plan.saturation == 0.55
    assert plan.luminance == 0.22
    assert plan.motion is EmberMotion.STEADY
    assert plan.breathe_period_seconds is None
    assert plan.degradation == (EmberDegradation.THERMAL_SERIOUS,)


def test_critical_thermal_pressure_suppresses_all_light_output() -> None:
    plan = plan_turn_length_ember(elapsed_seconds=3_600, thermal="critical")

    assert plan.visible is False
    assert plan.bucket is TurnAgeBucket.THIRTY_MINUTES_OR_MORE
    assert plan.saturation == 0.0
    assert plan.luminance == 0.0
    assert plan.motion is EmberMotion.HIDDEN
    assert plan.breathe_period_seconds is None
    assert plan.degradation == (EmberDegradation.THERMAL_CRITICAL,)
    assert "critical thermal pressure" in plan.accessibility_text


def test_combined_constraints_have_stable_reasons_and_strictest_output() -> None:
    plan = plan_turn_length_ember(
        elapsed_seconds=3_600,
        reduce_motion=True,
        low_power=True,
        thermal=ThermalState.SERIOUS,
    )

    assert plan.visible is True
    assert plan.saturation == 0.55
    assert plan.luminance == 0.22
    assert plan.motion is EmberMotion.STEADY
    assert plan.degradation == (
        EmberDegradation.REDUCE_MOTION,
        EmberDegradation.LOW_POWER,
        EmberDegradation.THERMAL_SERIOUS,
    )


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"turn_active": False}, "no turn is active"),
        ({"surface_visible": False}, "surface is not visible"),
    ],
)
def test_inactive_or_hidden_surface_returns_zeroed_hidden_plan(kwargs, reason) -> None:
    plan = plan_turn_length_ember(elapsed_seconds=300, **kwargs)

    assert plan.visible is False
    assert plan.bucket is TurnAgeBucket.TWO_TO_TEN_MINUTES
    assert plan.saturation == 0.0
    assert plan.luminance == 0.0
    assert plan.motion is EmberMotion.HIDDEN
    assert plan.breathe_period_seconds is None
    assert reason in plan.accessibility_text
    assert SEMANTIC_DISCLOSURE in plan.accessibility_text


def test_plans_are_immutable() -> None:
    plan = plan_turn_length_ember(elapsed_seconds=0)

    with pytest.raises(FrozenInstanceError):
        plan.luminance = 1.0  # type: ignore[misc]


@pytest.mark.parametrize("value", [None, True, "1", -1, float("nan"), float("inf")])
def test_elapsed_seconds_refuses_non_finite_or_non_numeric_values(value) -> None:
    with pytest.raises(TurnLengthEmberError):
        plan_turn_length_ember(elapsed_seconds=value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("turn_active", 1),
        ("surface_visible", "yes"),
        ("reduce_motion", None),
        ("low_power", 0),
    ],
)
def test_boolean_inputs_are_strict(field, value) -> None:
    with pytest.raises(TurnLengthEmberError, match=field):
        plan_turn_length_ember(elapsed_seconds=0, **{field: value})


@pytest.mark.parametrize("thermal", [None, 0, "warm", ""])
def test_thermal_input_is_bounded(thermal) -> None:
    with pytest.raises(TurnLengthEmberError, match="thermal"):
        plan_turn_length_ember(elapsed_seconds=0, thermal=thermal)
