from sidepulse.effect_registry import EFFECT_REGISTRY
from sidepulse.effect_studio import (
    ColorVisionMode,
    StudioSurface,
    SyntheticScenario,
    build_gallery_rows,
    build_surface_simulations,
)
from sidepulse.effect_studio_preview import deterministic_preview_samples


def test_twelve_sample_frame_is_total_stable_and_color_vision_aware() -> None:
    row = next(row for row in build_gallery_rows() if row.effect_id == "pulse")
    simulation = next(
        item
        for item in build_surface_simulations("pulse", EFFECT_REGISTRY)
        if item.surface is StudioSurface.SCREEN_BAR
    )

    standard = deterministic_preview_samples(
        row,
        simulation,
        SyntheticScenario.ONE_AGENT,
        color_vision_mode=ColorVisionMode.STANDARD,
    )
    monochrome = deterministic_preview_samples(
        row,
        simulation,
        SyntheticScenario.ONE_AGENT,
        color_vision_mode=ColorVisionMode.MONOCHROMACY,
    )

    assert len(standard) == 12
    assert standard == deterministic_preview_samples(
        row,
        simulation,
        SyntheticScenario.ONE_AGENT,
        color_vision_mode=ColorVisionMode.STANDARD,
    )
    assert standard != monochrome
    assert {sample.saturation for sample in monochrome} == {0.0}
