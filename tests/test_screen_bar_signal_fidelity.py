"""A moving LED must not turn into a full-width colored background."""

from __future__ import annotations

import pytest
from AppKit import NSApplication, NSBitmapImageRep, NSImage

from sidepulse import screen_bar_runtime, virtual_device


def _view():
    view = virtual_device.VirtualLedView.alloc().initWithFrame_(
        ((0.0, 0.0), (260.0, 37.0))
    )
    view.current_program = "comet"
    return view


def _render(draw, width=260, height=37):
    NSApplication.sharedApplication()
    image = NSImage.alloc().initWithSize_((width, height))
    image.lockFocus()
    try:
        draw()
        return NSBitmapImageRep.alloc().initWithFocusedViewRect_(
            ((0.0, 0.0), (width, height))
        )
    finally:
        image.unlockFocus()


def _pixel(rep, x, y):
    scale = rep.pixelsWide() / 260.0
    return rep.colorAtX_y_(int(x * scale), int(y * scale))


@pytest.mark.parametrize("style", ["auto", "spatial", "bracket"])
@pytest.mark.parametrize("floor", [0.0, 0.25, 1.0])
def test_spatial_animation_preserves_dim_colors_and_single_lit_led(style, floor):
    view = _view()
    view.setBracketStyle_(style)
    view.setMinGlow_(floor)
    frames = [
        [(0.01, 0, 0, 0.01), (0, 0.01, 0, 0.01), (0, 0, 1, 1)]
        + [(0, 0, 0, 0)] * 5,
        [(0, 0, 0, 0)] * 3 + [(0, 0, 1, 1)] + [(0, 0, 0, 0)] * 4,
        [(0, 0, 0, 0)] * 8,
    ]
    for frame in frames:
        assert view._bracket_colors(frame) == frame
        assert view._classic_status_colors(frame) == frame


def test_native_gradient_keeps_empty_edges_and_internal_gaps_dark():
    def draw():
        assert virtual_device.draw_horizontal_glow_gradient(
            virtual_device.current_cg_context(),
            ((0, 0), (260, 37)),
            ((60, 20, (1, 0, 0, 1)), None, (180, 20, (0, 0, 1, 1))),
            boost=1.0,
            alpha_scale=1.0,
        )

    rep = _render(draw)
    for x in (5, 35, 100, 130, 155, 230, 255):
        assert _pixel(rep, x, 18).alphaComponent() < 0.01, x
    assert _pixel(rep, 70, 18).redComponent() > 0.8
    assert _pixel(rep, 190, 18).blueComponent() > 0.8


@pytest.mark.parametrize("bracket", [False, True])
def test_rounded_band_has_no_colored_underlay_outside_the_moving_light(bracket):
    view = _view()
    view.setBracketStyle_("bracket" if bracket else "spatial")
    view.setMinGlow_(0.0)
    view._presentation_colors = [(0, 0, 0, 0)] * 3 + [(0, 0, 1, 1)] + [(0, 0, 0, 0)] * 4
    rep = _render(lambda: screen_bar_runtime._rounded_status_band(view, bracket_allowed=bracket))
    # Sample the entire height far away from the three-slot LED footprint.
    for x in (12, 24, 235, 246):
        assert max(_pixel(rep, x, y).alphaComponent() for y in range(37)) < 0.01, x
    assert max(_pixel(rep, 114, y).blueComponent() for y in range(37)) > 0.2


@pytest.mark.parametrize(("led", "x"), [(0, 51), (7, 208)])
def test_narrowed_alcove_band_keeps_endpoint_leds_visible(led, x):
    view = _view()
    view.setBracketStyle_("spatial")
    view.setMinGlow_(0.0)
    view.alcove_silhouette = (130.0, 180.0, 37.0, ())
    frame = [(0, 0, 0, 0)] * 8
    frame[led] = (0, 0, 1, 1)
    view._presentation_colors = frame
    rep = _render(lambda: screen_bar_runtime._rounded_status_band(view, bracket_allowed=False))
    assert max(_pixel(rep, x, y).blueComponent() for y in range(37)) > 0.75
