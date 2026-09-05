"""Pixel scanning and validation for the measured hardware notch."""

from __future__ import annotations


def cgimage_black_runs(image, *, rows: int, width_px: int):
    """Read per-row center black runs from a Core Graphics image."""
    import Quartz

    try:
        bytes_per_pixel = int(Quartz.CGImageGetBitsPerPixel(image)) // 8
        if bytes_per_pixel != 4:
            return None
        bytes_per_row = int(Quartz.CGImageGetBytesPerRow(image))
        available = int(Quartz.CGImageGetHeight(image))
        provider = Quartz.CGImageGetDataProvider(image)
        data = bytes(Quartz.CGDataProviderCopyData(provider))
    except Exception:
        return None
    if available <= 0 or len(data) < bytes_per_row * available:
        return None
    runs = []
    for row in range(rows):
        y = min(row, available - 1)
        runs.append(
            center_black_run_in_bytes(
                data,
                y * bytes_per_row,
                width_px,
                bytes_per_pixel,
            )
        )
    return runs


def center_black_run_in_bytes(
    data,
    base: int,
    width_px: int,
    bytes_per_pixel: int,
):
    """Return the pure-black run covering the horizontal center."""
    center_px = width_px / 2.0
    run_start = None
    best = None
    for x in range(width_px):
        i = base + x * bytes_per_pixel
        nonzero = (
            (1 if data[i] else 0)
            + (1 if data[i + 1] else 0)
            + (1 if data[i + 2] else 0)
            + (1 if data[i + 3] else 0)
        )
        if nonzero <= 1:
            if run_start is None:
                run_start = x
            continue
        if run_start is not None:
            if run_start <= center_px <= x:
                best = (run_start, x)
            run_start = None
    if run_start is not None and run_start <= center_px:
        best = (run_start, width_px)
    return best


def bitmap_rep_black_runs(image, *, rows: int, width_px: int):
    """Use the per-pixel AppKit bridge when raw bytes are unavailable."""
    from AppKit import NSBitmapImageRep

    rep = NSBitmapImageRep.alloc().initWithCGImage_(image)
    if rep is None:
        raise ValueError("unreadable composite image")
    available = int(rep.pixelsHigh())
    runs = []
    for row in range(rows):
        y = min(row, available - 1) if available > 0 else 0
        runs.append(center_black_run(rep, y, width_px))
    return runs


def center_black_run(rep, y: int, width_px: int):
    """Return the pure-black run covering the horizontal center."""
    center_px = width_px / 2.0
    run_start = None
    best = None
    for x in range(width_px):
        color = rep.colorAtX_y_(x, y)
        is_black = (
            color is not None
            and color.redComponent() == 0.0
            and color.greenComponent() == 0.0
            and color.blueComponent() == 0.0
        )
        if is_black:
            if run_start is None:
                run_start = x
            continue
        if run_start is not None:
            if run_start <= center_px <= x:
                best = (run_start, x)
            run_start = None
    if run_start is not None and run_start <= center_px:
        best = (run_start, width_px)
    return best


def validated_notch_silhouette(
    runs,
    scale,
    frame_x,
    max_width: float | None = None,
):
    """Validate raw runs into an origin, width, and per-row insets."""
    if not runs or runs[0] is None or scale <= 0.0:
        return None
    left_0, right_0 = runs[0]
    top_width = (right_0 - left_0) / scale
    if not (120.0 <= top_width <= 320.0):
        return None
    if max_width is not None and top_width > float(max_width):
        return None
    insets: list[tuple[float, float]] = []
    floor_left = 0.0
    floor_right = 0.0
    for run in runs:
        if run is None:
            return None
        left, right = run
        inset_left = (left - left_0) / scale
        inset_right = (right_0 - right) / scale
        if inset_left < -1.0 or inset_right < -1.0:
            return None
        inset_left = max(floor_left, inset_left)
        inset_right = max(floor_right, inset_right)
        if inset_left + inset_right > top_width - 8.0:
            return None
        insets.append((inset_left, inset_right))
        floor_left = inset_left
        floor_right = inset_right
    return (frame_x + left_0 / scale, top_width, tuple(insets))
