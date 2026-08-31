"""AppKit-free deterministic Effect Studio preview samples."""

from __future__ import annotations

import colorsys
import hashlib
from dataclasses import dataclass
from typing import Final

from .effect_studio import (
    ColorVisionMode,
    GalleryRow,
    SemanticFamily,
    SurfaceSimulation,
    SyntheticScenario,
)

PREVIEW_SAMPLE_LIMIT: Final = 12

_FAMILY_HUES: Final = {
    SemanticFamily.WORKING: 0.58,
    SemanticFamily.ASKING: 0.11,
    SemanticFamily.COMPLETION: 0.36,
    SemanticFamily.FAILURE: 0.99,
    SemanticFamily.RECOVERY: 0.42,
    SemanticFamily.NOTIFICATION: 0.77,
    SemanticFamily.QUOTA: 0.52,
    SemanticFamily.ENVIRONMENT: 0.45,
    SemanticFamily.IDLE: 0.60,
    SemanticFamily.TRANSITION: 0.88,
}

_COLOR_VISION_MATRICES: Final = {
    ColorVisionMode.PROTANOPIA: (
        (0.56667, 0.43333, 0.0),
        (0.55833, 0.44167, 0.0),
        (0.0, 0.24167, 0.75833),
    ),
    ColorVisionMode.DEUTERANOPIA: (
        (0.625, 0.375, 0.0),
        (0.70, 0.30, 0.0),
        (0.0, 0.30, 0.70),
    ),
    ColorVisionMode.TRITANOPIA: (
        (0.95, 0.05, 0.0),
        (0.0, 0.43333, 0.56667),
        (0.0, 0.475, 0.525),
    ),
}


@dataclass(frozen=True, slots=True)
class PreviewSample:
    """A device-independent, deterministic LED sample for one static frame."""

    hue: float
    saturation: float
    brightness: float

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, float) and 0.0 <= value <= 1.0
            for value in (self.hue, self.saturation, self.brightness)
        ):
            raise ValueError("invalid preview sample")


def deterministic_preview_samples(
    row: GalleryRow,
    simulation: SurfaceSimulation,
    scenario: SyntheticScenario,
    *,
    color_vision_mode: ColorVisionMode = ColorVisionMode.STANDARD,
) -> tuple[PreviewSample, ...]:
    """Return a stable static frame without clocks, random state, or I/O."""

    if type(row) is not GalleryRow or type(simulation) is not SurfaceSimulation:
        raise TypeError("preview requires typed Studio projections")
    if type(scenario) is not SyntheticScenario:
        raise TypeError("scenario must be SyntheticScenario")
    if type(color_vision_mode) is not ColorVisionMode:
        raise TypeError("color_vision_mode must be ColorVisionMode")
    count = min(PREVIEW_SAMPLE_LIMIT, simulation.led_count)
    key = (
        f"{simulation.rendered_effect_id}|{simulation.surface.value}|"
        f"{scenario.value}|{int(simulation.reduce_motion)}"
    ).encode()
    digest = hashlib.shake_256(key).digest(count * 3)
    hue = _FAMILY_HUES[row.semantic_family]
    if not simulation.supported:
        samples = tuple(
            PreviewSample(hue, 0.0, 0.16) for _index in range(count)
        )
    elif simulation.reduce_motion:
        samples = tuple(
            PreviewSample(hue, 0.66, 0.70) for _index in range(count)
        )
    else:
        samples = tuple(
            PreviewSample(
                (hue + ((digest[index] % 19) - 9) / 360.0) % 1.0,
                0.62 + (digest[index + count] % 20) / 100.0,
                0.42 + (digest[index + 2 * count] % 50) / 100.0,
            )
            for index in range(count)
        )
    return tuple(
        _simulate_color_vision(sample, color_vision_mode) for sample in samples
    )


def _simulate_color_vision(
    sample: PreviewSample,
    mode: ColorVisionMode,
) -> PreviewSample:
    if mode is ColorVisionMode.STANDARD:
        return sample
    red, green, blue = colorsys.hsv_to_rgb(
        sample.hue,
        sample.saturation,
        sample.brightness,
    )
    if mode is ColorVisionMode.MONOCHROMACY:
        luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        return PreviewSample(0.0, 0.0, min(1.0, max(0.0, luminance)))
    matrix = _COLOR_VISION_MATRICES[mode]
    projected = tuple(
        min(1.0, max(0.0, row[0] * red + row[1] * green + row[2] * blue))
        for row in matrix
    )
    hue, saturation, brightness = colorsys.rgb_to_hsv(*projected)
    return PreviewSample(float(hue), float(saturation), float(brightness))


__all__ = [
    "PREVIEW_SAMPLE_LIMIT",
    "PreviewSample",
    "deterministic_preview_samples",
]
