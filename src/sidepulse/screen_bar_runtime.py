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


def _visible_identity(view, colors):
    identity = view._bar_identity_color(colors)
    if max(identity) > 0.001:
        return identity
    program = str(getattr(view, "current_program", "") or "").strip().lower()
    if program == "off":
        return identity
    # Connected but silent is an outline, not an apparently missing surface.
    # The user's 0% minimum-glow choice still wins and may make it fully dark.
    return (0.10, 0.34, 0.40, design.OUTLINE_ALPHA * _min_glow(view))


def _rounded_status_band(view, *, bracket_allowed: bool) -> None:
    from . import virtual_device as vd

    colors = view._bracket_colors(view._colors_for_draw_cached())
    width = float(view.bounds().size.width)
    height = float(view.bounds().size.height)
    left, right = design.rounded_band_bounds(width, preferred_width=width)
    band_width = max(0.0, right - left)
    if band_width <= 0.0 or height <= 0.0:
        return

    band_height = min(
        design.BAND_HEIGHT,
        max(1.0, height - design.VERTICAL_INSET),
    )
    y = min(design.VERTICAL_INSET, max(0.0, height - band_height))
    radius = min(design.CORNER_RADIUS, band_height / 2.0, band_width / 2.0)
    identity = _visible_identity(view, colors)
    red, green, blue, alpha = identity
    floor = _min_glow(view)

    # A bounded bloom makes the surface readable without turning it into a
    # full-width menu-bar rule. It is deliberately one shared color so bloom
    # cannot shift hue by clipping channels independently.
    halo_y = max(0.0, y - 1.5)
    halo_height = min(max(0.0, height - halo_y), band_height + 5.0)
    if halo_height > 0.0:
        halo_left = max(0.0, left - 2.0)
        halo_right = min(width, right + 2.0)
        halo = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((halo_left, halo_y), (halo_right - halo_left, halo_height)),
            min(radius + 2.0, halo_height / 2.0),
            min(radius + 2.0, halo_height / 2.0),
        )
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            red,
            green,
            blue,
            min(0.28, max(0.0, alpha) * design.HALO_ALPHA),
        ).set()
        halo.fill()

    core = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        ((left, y), (band_width, band_height)),
        radius,
        radius,
    )
    cg_context = vd.current_cg_context()
    notch_width, wing_offset = view._notch_geometry()
    if notch_width <= 0.0:
        return
    led_width = notch_width / vd.LED_COUNT
    NSGraphicsContext.saveGraphicsState()
    try:
        core.addClip()
        column_x = left
        while column_x < right:
            column_width = min(vd.BLEND_COLUMN_WIDTH, right - column_x)
            center_x = column_x + column_width / 2.0
            color = vd.glow_color_for_column(
                colors,
                led_width,
                notch_width,
                wing_offset,
                center_x,
                taper_floor=1.0,
            )
            vd.fill_rect_with_cg(
                cg_context,
                ((column_x, y), (column_width, band_height)),
                vd.tone_mapped_led_color(
                    *color,
                    boost=vd.LED_CORE_BOOST,
                    alpha_scale=0.95,
                ),
            )
            column_x += column_width
        # A restrained highlight gives the band a native luminous edge without
        # creating the old one-pixel line across the entire window.
        vd.fill_rect_with_cg(
            cg_context,
            ((left, y + band_height - 0.55), (band_width, 0.55)),
            (1.0, 1.0, 1.0, min(0.12, max(0.0, alpha * 0.10))),
        )
    finally:
        NSGraphicsContext.restoreGraphicsState()

    # Leave an outline for the connected-but-silent state unless the user chose
    # a zero minimum glow. This distinguishes healthy quiet from a missing bar
    # while preserving the explicit pitch-black setting.
    outline_alpha = min(
        0.62,
        max(design.OUTLINE_ALPHA * floor, alpha * 0.55),
    )
    if outline_alpha > 0.001:
        NSColor.colorWithCalibratedRed_green_blue_alpha_(
            red,
            green,
            blue,
            outline_alpha,
        ).set()
        core.setLineWidth_(0.8)
        core.stroke()

    if bracket_allowed and str(getattr(view, "bracket_style", "auto")) == "bracket":
        edge = _visible_identity(view, colors)
        riser = min(vd.WING_RISER_WIDTH, max(2.0, band_height))
        view._draw_wing_riser(
            cg_context,
            edge,
            left,
            min(right, left + riser),
            min(height, band_height + 8.0),
            outer_on_left=True,
        )
        view._draw_wing_riser(
            cg_context,
            edge,
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
