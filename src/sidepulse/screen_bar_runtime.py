"""Narrow AppKit installer for the production Screen Bar design.

The Objective-C view class is not rebound or subclassed. Only two private
Python drawing helpers are replaced, which keeps PyObjC method registration
stable while letting Alcove and notchless layouts use the same rounded status
band as the normal Screen Bar.
"""

from __future__ import annotations

from AppKit import NSBezierPath, NSColor, NSGraphicsContext

from . import screen_bar_design as design


def _min_glow(view) -> float:
    try:
        return max(0.0, min(1.0, float(getattr(view, "min_glow", 0.25))))
    except (TypeError, ValueError):
        return 0.25


def _rounded_status_band(view, *, bracket_allowed: bool) -> None:
    from . import virtual_device as vd

    colors = view._bracket_colors(view._colors_for_draw_cached())
    width = float(view.bounds().size.width)
    height = float(view.bounds().size.height)
    # Hug the CAPSULE, not the window. Alcove's capsule tucks in at its
    # rounded bottom corners, so a band sized from the window (or from
    # the capsule's WIDEST row) pokes out past it and reads as a wider
    # shadow box behind the app (2026-08-27 owner screenshot). The
    # bottom contour row is the width that matters at the row the band
    # actually occupies.
    preferred = width
    lift = 0.0
    silhouette = getattr(view, "alcove_silhouette", None)
    if silhouette is not None:
        try:
            _center, sil_width, sil_height, contour = silhouette
            if contour:
                bottom_y = max(point[1] for point in contour)
                bottom_xs = [
                    point[0]
                    for point in contour
                    if point[1] >= bottom_y - 0.5
                ]
                bottom_width = (
                    max(bottom_xs) - min(bottom_xs)
                    if len(bottom_xs) >= 2
                    else float(sil_width)
                )
            else:
                bottom_width = float(sil_width)
            if bottom_width > 0.0:
                preferred = min(width, bottom_width)
            # Same lift the classic drawer uses: the band kisses the
            # capsule's lower edge instead of the window bottom.
            lift = max(0.0, height - float(sil_height) - 1.0)
        except (TypeError, ValueError):
            preferred = width
            lift = 0.0
    left, right = design.rounded_band_bounds(width, preferred_width=preferred)
    band_width = max(0.0, right - left)
    if band_width <= 0.0 or height <= 0.0:
        return

    band_height = min(
        design.BAND_HEIGHT,
        max(1.0, height - design.VERTICAL_INSET),
    )
    y = min(lift + design.VERTICAL_INSET, max(0.0, height - band_height))
    radius = min(design.CORNER_RADIUS, band_height / 2.0, band_width / 2.0)
    floor = _min_glow(view)

    core = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        ((left, y), (band_width, band_height)),
        radius,
        radius,
    )
    cg_context = vd.current_cg_context()
    # Fit all eight LEDs into the measured band, including a narrower Alcove
    # contour. Sampling the original notch width would crop the endpoint LEDs.
    led_width = band_width / vd.LED_COUNT
    NSGraphicsContext.saveGraphicsState()
    try:
        core.addClip()
        runs = vd._glow_runs(
            view._glow_geometry_cache,
            view._glow_paint_cache,
            colors=colors,
            brightness=view.brightness,
            led_width=led_width,
            notch_width=band_width,
            x_start=left,
            x_end=right,
            wing_offset=left,
            wing_taper_floor=1.0,
            silhouette=(
                view.alcove_silhouette[3]
                if view.alcove_silhouette is not None
                else "rounded-status-band"
            ),
            screen_identity=view.render_screen_identity,
            scale=view.render_scale,
        )
        core_rect = ((left, y), (band_width, band_height))
        if not vd.draw_horizontal_glow_gradient(
            cg_context,
            core_rect,
            runs,
            boost=vd.LED_CORE_BOOST,
            alpha_scale=0.95,
        ):
            for run in runs:
                if run is None:
                    continue
                run_x, run_width, color = run
                vd.fill_rect_with_cg(
                    cg_context,
                    ((run_x, y), (run_width, band_height)),
                    vd.tone_mapped_led_color(
                        *color,
                        boost=vd.LED_CORE_BOOST,
                        alpha_scale=0.95,
                    ),
                )
    finally:
        NSGraphicsContext.restoreGraphicsState()

    # The optional housing outline is neutral and independent of the signal.
    # Never spread a comet's color over the dark remainder of the band.
    off = str(getattr(view, "current_program", "")).strip().lower() == "off"
    outline_alpha = 0.0 if off else design.OUTLINE_ALPHA * floor
    if outline_alpha > 0.001:
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.25,
            0.25,
            0.25,
            outline_alpha,
        ).set()
        core.setLineWidth_(0.8)
        core.stroke()

    if bracket_allowed and str(getattr(view, "bracket_style", "auto")) == "bracket":
        riser = min(vd.WING_RISER_WIDTH, max(2.0, band_height))
        view._draw_wing_riser(
            cg_context,
            colors[0],
            left,
            min(right, left + riser),
            min(height, band_height + 8.0),
            outer_on_left=True,
        )
        view._draw_wing_riser(
            cg_context,
            colors[-1],
            max(left, right - riser),
            right,
            min(height, band_height + 8.0),
            outer_on_left=False,
        )

    view._draw_standing_gauges(cg_context, height, edge_inset=left)


def _draw_compact_accent(view) -> None:
    _rounded_status_band(view, bracket_allowed=False)


def _draw_wings_only(view) -> None:
    # Alcove follows the measured center/width, but SidePulse remains a bounded
    # status band. Bracket risers appear only when the explicit bracket style
    # is selected, never as an automatic side effect of Alcove being present.
    _rounded_status_band(view, bracket_allowed=True)


def _window_height_for_notch_depth(notch_depth: float) -> float:
    depth = max(0.0, float(notch_depth))
    return max(
        depth + design.BAND_HEIGHT,
        design.BAND_HEIGHT + design.GLOW_HEIGHT + 2.0,
    )


def install_screen_bar_runtime() -> None:
    """Install the reviewed design once without rebinding an Objective-C class."""
    from . import virtual_device as vd

    if getattr(vd, "_sidepulse_rounded_band_installed", False):
        return
    vd.LED_BAND_HEIGHT = design.BAND_HEIGHT
    vd.COMPACT_ACCENT_HEIGHT = design.COMPACT_BAND_HEIGHT
    vd.LED_GLOW_HEIGHT = design.GLOW_HEIGHT
    vd.WINDOW_WIDTH = design.WINDOW_WIDTH
    vd.window_height_for_notch_depth = _window_height_for_notch_depth
    vd.WINDOW_HEIGHT = _window_height_for_notch_depth(vd.FALLBACK_NOTCH_DEPTH)
    vd.VirtualLedView._draw_compact_accent = _draw_compact_accent
    vd.VirtualLedView._draw_wings_only = _draw_wings_only
    vd._sidepulse_rounded_band_installed = True


__all__ = ["install_screen_bar_runtime"]
