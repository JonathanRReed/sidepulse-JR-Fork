"""Bounded Core Graphics helpers for Screen Bar rendering."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import Quartz

Color = tuple[float, float, float, float]
GlowRun = tuple[float, float, Color]


def draw_horizontal_gradient(
    context,
    rect,
    runs: Iterable[GlowRun | None],
    *,
    color_mapper: Callable[[Color], Color],
) -> bool:
    """Draw mapped glow runs as one clipped horizontal gradient."""
    if context is None:
        return False
    try:
        (origin_x, origin_y), (width, height) = rect
        origin_x = float(origin_x)
        origin_y = float(origin_y)
        width = float(width)
        height = float(height)
    except (TypeError, ValueError):
        return False
    if width <= 0.0 or height <= 0.0:
        return False

    segments = [run for run in runs if run is not None and float(run[1]) > 0.0]
    if not segments:
        return True

    locations: list[float] = []
    components: list[float] = []

    def append_stop(location: float, color: Color) -> None:
        bounded_location = max(0.0, min(1.0, float(location)))
        mapped = color_mapper(color)
        if locations and bounded_location <= locations[-1]:
            if bounded_location == locations[-1]:
                components[-4:] = mapped
            return
        locations.append(bounded_location)
        components.extend(mapped)

    transparent = (0.0, 0.0, 0.0, 0.0)
    first_x = float(segments[0][0])
    append_stop(0.0, transparent if first_x > origin_x else segments[0][2])
    previous_end = origin_x
    for run_x, run_width, color in segments:
        run_x = float(run_x)
        run_width = float(run_width)
        if run_x > previous_end + 0.001:
            append_stop((previous_end - origin_x) / width, transparent)
            append_stop((run_x - origin_x) / width, transparent)
        center_x = run_x + run_width / 2.0
        append_stop((center_x - origin_x) / width, color)
        previous_end = max(previous_end, run_x + run_width)
    if previous_end < origin_x + width - 0.001:
        append_stop((previous_end - origin_x) / width, transparent)
        append_stop(1.0, transparent)
    else:
        append_stop(1.0, segments[-1][2])
    if len(locations) == 1:
        append_stop(1.0, segments[-1][2])
    if len(locations) < 2:
        return False

    try:
        color_space = Quartz.CGColorSpaceCreateDeviceRGB()
        gradient = Quartz.CGGradientCreateWithColorComponents(
            color_space,
            components,
            locations,
            len(locations),
        )
        if gradient is None:
            return False
        Quartz.CGContextSaveGState(context)
        try:
            Quartz.CGContextClipToRect(context, rect)
            mid_y = origin_y + height / 2.0
            Quartz.CGContextDrawLinearGradient(
                context,
                gradient,
                (origin_x, mid_y),
                (origin_x + width, mid_y),
                Quartz.kCGGradientDrawsBeforeStartLocation
                | Quartz.kCGGradientDrawsAfterEndLocation,
            )
        finally:
            Quartz.CGContextRestoreGState(context)
    except Exception:
        return False
    return True
