"""Pure geometry and semantic presentation policy for the Screen Bar."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Final

BAND_HEIGHT: Final = 6.0
COMPACT_BAND_HEIGHT: Final = 5.0
GLOW_HEIGHT: Final = 14.0
WINDOW_WIDTH: Final = 260.0
MIN_BAND_WIDTH: Final = 180.0
MAX_BAND_WIDTH: Final = 420.0
EDGE_INSET: Final = 8.0
VERTICAL_INSET: Final = 1.0
CORNER_RADIUS: Final = 3.0
OUTLINE_ALPHA: Final = 0.24
HALO_ALPHA: Final = 0.16


class ScreenBarSemantic(str, Enum):
    SILENT = "silent"
    IDLE = "idle"
    WORKING = "working"
    NEEDS_INPUT = "needs_input"
    COMPLETED = "completed"
    FAILED = "failed"
    QUOTA = "quota"


@dataclass(frozen=True, slots=True)
class ScreenBarVisual:
    semantic: ScreenBarSemantic
    motion: str
    segmented: bool
    endpoint_accent: bool
    outline_only: bool
    finite: bool


def _finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("screen bar geometry must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("screen bar geometry must be finite")
    return result


def rounded_band_bounds(
    total_width: float,
    *,
    preferred_width: float | None = None,
    edge_inset: float = EDGE_INSET,
) -> tuple[float, float]:
    """Return a centered, bounded band that never degenerates into a hairline.

    On a narrow surface the bar uses all safe width. On a wide Alcove or
    menu-bar window it remains a deliberate 180–420 point status object rather
    than a one-pixel rule stretching across the display.
    """
    width = max(0.0, _finite(total_width))
    inset = max(0.0, _finite(edge_inset))
    available = max(0.0, width - 2.0 * inset)
    if available <= 0.0:
        return (width / 2.0, width / 2.0)
    requested = available if preferred_width is None else max(0.0, _finite(preferred_width))
    if available >= MIN_BAND_WIDTH:
        requested = max(MIN_BAND_WIDTH, requested)
    band_width = min(available, MAX_BAND_WIDTH, requested)
    left = (width - band_width) / 2.0
    return (left, left + band_width)


def visual_for_semantic(
    semantic: ScreenBarSemantic | str,
    *,
    reduce_motion: bool = False,
) -> ScreenBarVisual:
    value = semantic if isinstance(semantic, ScreenBarSemantic) else ScreenBarSemantic(semantic)
    motion = {
        ScreenBarSemantic.SILENT: "none",
        ScreenBarSemantic.IDLE: "steady",
        ScreenBarSemantic.WORKING: "travel",
        ScreenBarSemantic.NEEDS_INPUT: "edge-pulse",
        ScreenBarSemantic.COMPLETED: "single-sweep",
        ScreenBarSemantic.FAILED: "segmented-warning",
        ScreenBarSemantic.QUOTA: "endpoint",
    }[value]
    if reduce_motion and motion not in {"none", "steady", "endpoint"}:
        motion = "steady"
    return ScreenBarVisual(
        semantic=value,
        motion=motion,
        segmented=value is ScreenBarSemantic.FAILED,
        endpoint_accent=value is ScreenBarSemantic.QUOTA,
        outline_only=value is ScreenBarSemantic.SILENT,
        finite=value in {ScreenBarSemantic.NEEDS_INPUT, ScreenBarSemantic.COMPLETED},
    )


__all__ = [
    "BAND_HEIGHT",
    "COMPACT_BAND_HEIGHT",
    "CORNER_RADIUS",
    "EDGE_INSET",
    "GLOW_HEIGHT",
    "HALO_ALPHA",
    "MAX_BAND_WIDTH",
    "MIN_BAND_WIDTH",
    "OUTLINE_ALPHA",
    "VERTICAL_INSET",
    "WINDOW_WIDTH",
    "ScreenBarSemantic",
    "ScreenBarVisual",
    "rounded_band_bounds",
    "visual_for_semantic",
]
