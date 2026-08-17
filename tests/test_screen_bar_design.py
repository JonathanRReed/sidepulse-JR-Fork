from __future__ import annotations

import math

import pytest

from sidepulse.screen_bar_design import (
    MAX_BAND_WIDTH,
    MIN_BAND_WIDTH,
    ScreenBarSemantic,
    rounded_band_bounds,
    visual_for_semantic,
)


def test_screen_bar_is_centered_and_bounded_on_wide_surfaces() -> None:
    left, right = rounded_band_bounds(1400.0)
    assert math.isclose((left + right) / 2.0, 700.0)
    assert math.isclose(right - left, MAX_BAND_WIDTH)
    assert left > 0.0


def test_screen_bar_uses_safe_available_width_on_narrow_surfaces() -> None:
    left, right = rounded_band_bounds(170.0)
    assert left == 8.0
    assert right == 162.0
    assert right - left < MIN_BAND_WIDTH


def test_screen_bar_semantics_differ_by_motion_or_shape_not_only_color() -> None:
    assert visual_for_semantic(ScreenBarSemantic.SILENT).outline_only is True
    assert visual_for_semantic(ScreenBarSemantic.WORKING).motion == "travel"
    assert visual_for_semantic(ScreenBarSemantic.NEEDS_INPUT).finite is True
    assert visual_for_semantic(ScreenBarSemantic.COMPLETED).motion == "single-sweep"
    assert visual_for_semantic(ScreenBarSemantic.FAILED).segmented is True
    assert visual_for_semantic(ScreenBarSemantic.QUOTA).endpoint_accent is True
    assert visual_for_semantic(
        ScreenBarSemantic.FAILED,
        reduce_motion=True,
    ).motion == "steady"


def test_screen_bar_geometry_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        rounded_band_bounds(float("nan"))
    with pytest.raises(ValueError):
        rounded_band_bounds(300.0, preferred_width=True)
