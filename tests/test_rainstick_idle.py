from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sidepulse.rainstick_idle import (
    RAINSTICK_ACCESSIBILITY_DISCLOSURE,
    RAINSTICK_LIT_PIXEL_COUNT,
    RAINSTICK_RELATIVE_LUMINANCE,
    RAINSTICK_STEP_FREQUENCY_HZ,
    RAINSTICK_STEP_INTERVAL_SECONDS,
    RainstickDisposition,
    RainstickIdleError,
    RainstickSuppressionReason,
    RainstickThermalState,
    plan_rainstick_idle,
)


def test_rainstick_is_opt_in_and_fails_dark_by_default() -> None:
    plan = plan_rainstick_idle()

    assert plan.disposition is RainstickDisposition.SUPPRESS
    assert plan.visible is False
    assert plan.animated is False
    assert plan.cadence is None
    assert plan.geometry is None
    assert plan.suppression_reasons == (
        RainstickSuppressionReason.DISABLED_PREFERENCE,
    )
    assert (
        plan.suppression_reason
        is RainstickSuppressionReason.DISABLED_PREFERENCE
    )
    assert "preference is disabled" in plan.accessibility_text
    assert RAINSTICK_ACCESSIBILITY_DISCLOSURE in plan.accessibility_text


def test_enabled_plan_moves_exactly_one_dim_pixel_at_low_frequency() -> None:
    plan = plan_rainstick_idle(
        preference_enabled=True,
        surface_pixel_count=12,
    )

    assert plan.disposition is RainstickDisposition.MOVE
    assert plan.visible is True
    assert plan.animated is True
    assert plan.suppression_reasons == ()
    assert plan.suppression_reason is None

    cadence = plan.cadence
    assert cadence is not None
    assert cadence.moves is True
    assert cadence.step_interval_seconds == RAINSTICK_STEP_INTERVAL_SECONDS
    assert cadence.step_frequency_hz == RAINSTICK_STEP_FREQUENCY_HZ
    assert cadence.step_frequency_hz < 0.1

    geometry = plan.geometry
    assert geometry is not None
    assert geometry.surface_pixel_count == 12
    assert geometry.lit_pixel_count == RAINSTICK_LIT_PIXEL_COUNT == 1
    assert geometry.path_start_index == 0
    assert geometry.path_end_index == 11
    assert geometry.position_count == 12
    assert geometry.step_delta == 1
    assert geometry.wraps is True
    assert geometry.relative_luminance == RAINSTICK_RELATIVE_LUMINANCE
    assert geometry.relative_luminance <= 0.04
    assert geometry.static_index is None
    assert "every 30 seconds" in plan.accessibility_text
    assert "alive and watching" in plan.accessibility_text
    assert RAINSTICK_ACCESSIBILITY_DISCLOSURE in plan.accessibility_text


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        (
            {"higher_priority_signal_active": True},
            RainstickSuppressionReason.HIGHER_PRIORITY_SIGNAL,
        ),
        ({"dnd_active": True}, RainstickSuppressionReason.DND),
        (
            {"night_policy_allows_idle": False},
            RainstickSuppressionReason.NIGHT_POLICY,
        ),
        ({"display_asleep": True}, RainstickSuppressionReason.DISPLAY_ASLEEP),
        ({"surface_visible": False}, RainstickSuppressionReason.SURFACE_HIDDEN),
        ({"low_power": True}, RainstickSuppressionReason.LOW_POWER),
        (
            {"thermal": RainstickThermalState.SERIOUS},
            RainstickSuppressionReason.THERMAL_SERIOUS,
        ),
        (
            {"thermal": "CRITICAL"},
            RainstickSuppressionReason.THERMAL_CRITICAL,
        ),
    ],
)
def test_every_exclusive_surface_or_environment_policy_suppresses(
    kwargs,
    reason,
) -> None:
    plan = plan_rainstick_idle(preference_enabled=True, **kwargs)

    assert plan.disposition is RainstickDisposition.SUPPRESS
    assert plan.cadence is None
    assert plan.geometry is None
    assert plan.suppression_reasons == (reason,)
    assert RAINSTICK_ACCESSIBILITY_DISCLOSURE in plan.accessibility_text


def test_all_active_suppressions_are_reported_in_stable_priority_order() -> None:
    plan = plan_rainstick_idle(
        preference_enabled=False,
        higher_priority_signal_active=True,
        dnd_active=True,
        night_policy_allows_idle=False,
        display_asleep=True,
        surface_visible=False,
        low_power=True,
        thermal="serious",
        reduce_motion=True,
    )

    assert plan.suppression_reasons == (
        RainstickSuppressionReason.DISABLED_PREFERENCE,
        RainstickSuppressionReason.HIGHER_PRIORITY_SIGNAL,
        RainstickSuppressionReason.DND,
        RainstickSuppressionReason.NIGHT_POLICY,
        RainstickSuppressionReason.DISPLAY_ASLEEP,
        RainstickSuppressionReason.SURFACE_HIDDEN,
        RainstickSuppressionReason.LOW_POWER,
        RainstickSuppressionReason.THERMAL_SERIOUS,
    )
    assert (
        plan.suppression_reason
        is RainstickSuppressionReason.DISABLED_PREFERENCE
    )
    assert plan.cadence is None
    assert plan.geometry is None


def test_fair_thermal_state_remains_admitted_because_only_serious_pressure_blocks() -> None:
    plan = plan_rainstick_idle(
        preference_enabled=True,
        thermal=RainstickThermalState.FAIR,
    )

    assert plan.disposition is RainstickDisposition.MOVE
    assert plan.suppression_reasons == ()


def test_reduce_motion_substitutes_one_stationary_dim_pixel() -> None:
    plan = plan_rainstick_idle(
        preference_enabled=True,
        reduce_motion=True,
        surface_pixel_count=9,
    )

    assert plan.disposition is RainstickDisposition.STATIC
    assert plan.visible is True
    assert plan.animated is False
    assert plan.suppression_reasons == ()
    assert plan.cadence is not None
    assert plan.cadence.moves is False
    assert plan.cadence.step_interval_seconds is None
    assert plan.cadence.step_frequency_hz == 0.0
    assert plan.geometry is not None
    assert plan.geometry.lit_pixel_count == 1
    assert plan.geometry.static_index == 4
    assert plan.geometry.relative_luminance == RAINSTICK_RELATIVE_LUMINANCE
    assert "stationary pixel" in plan.accessibility_text
    assert "Reduce Motion" in plan.accessibility_text


def test_plan_and_nested_contracts_are_immutable() -> None:
    plan = plan_rainstick_idle(preference_enabled=True)

    with pytest.raises(FrozenInstanceError):
        plan.accessibility_text = "changed"  # type: ignore[misc]
    assert plan.cadence is not None
    with pytest.raises(FrozenInstanceError):
        plan.cadence.moves = False  # type: ignore[misc]
    assert plan.geometry is not None
    with pytest.raises(FrozenInstanceError):
        plan.geometry.lit_pixel_count = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preference_enabled", 1),
        ("higher_priority_signal_active", "no"),
        ("dnd_active", None),
        ("night_policy_allows_idle", 1),
        ("surface_visible", "yes"),
        ("display_asleep", 0),
        ("low_power", None),
        ("reduce_motion", "false"),
    ],
)
def test_policy_flags_are_strict_booleans(field, value) -> None:
    with pytest.raises(RainstickIdleError, match=field):
        plan_rainstick_idle(**{field: value})


@pytest.mark.parametrize("thermal", [None, 0, "warm", ""])
def test_thermal_state_is_bounded(thermal) -> None:
    with pytest.raises(RainstickIdleError, match="thermal"):
        plan_rainstick_idle(thermal=thermal)


@pytest.mark.parametrize("pixel_count", [None, True, 0, 1, 1_025, 4.0, "8"])
def test_surface_geometry_is_bounded(pixel_count) -> None:
    with pytest.raises(RainstickIdleError, match="surface_pixel_count"):
        plan_rainstick_idle(surface_pixel_count=pixel_count)
