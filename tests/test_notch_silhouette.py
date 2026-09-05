"""The measured hardware-notch silhouette and the body drawn from it.

Two real failures anchor these tests. Alcove's capsule composites pure
black over the exact rows the notch scan reads: it was measured at 266pt
over this panel's 186pt notch, passed the 120-320 sanity band, and was
cached as "the notch" -- the classic body grew ears. And the top-width
scan alone left straight body walls standing beside the physical notch's
curved bottom corners as dark slivers.
"""

from __future__ import annotations

from itertools import pairwise

from sidepulse.alcove_observation import capture_display_region_image
from sidepulse.virtual_device import (
    LED_BAND_HEIGHT,
    NotchCaptureRequest,
    NotchSilhouetteProbe,
    _capture_notch_runs,
    _validated_notch_silhouette,
    notch_bar_path_from_insets,
)

SCALE = 2.0
FRAME_X = 0.0


def _request() -> NotchCaptureRequest:
    return NotchCaptureRequest(
        screen_id="1:0:0:1512:982",
        display_id=1,
        frame_x=0.0,
        screen_width=1512.0,
        scale=2.0,
        rows=32,
        excluded_window_number=90,
    )


def test_notch_probe_schedules_one_capture_and_serves_the_cached_result() -> None:
    tasks = []
    calls = []
    expected = (663.0, 186.0, ((0.0, 0.0),) * 32)
    probe = NotchSilhouetteProbe(
        probe=lambda request, max_width: calls.append((request, max_width)) or expected,
        start_refresh=tasks.append,
    )

    assert probe.read(_request(), max_width=191.0, now=10.0) is None
    assert probe.read(_request(), max_width=191.0, now=10.1) is None
    assert len(tasks) == 1
    assert calls == []

    tasks.pop()()

    assert probe.read(_request(), max_width=191.0, now=10.2) == expected
    assert calls == [(_request(), 191.0)]
    assert tasks == []


def test_macos_15_notch_capture_uses_screen_capture_kit_only(monkeypatch) -> None:
    from sidepulse import virtual_device

    image = object()
    monkeypatch.setattr(virtual_device, "_macos_major_version", lambda: 15)
    monkeypatch.setattr(
        virtual_device,
        "capture_display_region_image",
        lambda **kwargs: image,
    )
    monkeypatch.setattr(
        virtual_device,
        "_legacy_notch_capture_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy capture")),
    )
    monkeypatch.setattr(
        virtual_device,
        "_notch_runs_from_image",
        lambda captured, request: (("sck", captured), request.scale, request.frame_x),
    )

    assert _capture_notch_runs(_request()) == (("sck", image), 2.0, 0.0)


def test_pre_macos_15_notch_capture_keeps_the_legacy_fallback(monkeypatch) -> None:
    from sidepulse import virtual_device

    image = object()
    monkeypatch.setattr(virtual_device, "_macos_major_version", lambda: 14)
    monkeypatch.setattr(
        virtual_device,
        "capture_display_region_image",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("ScreenCaptureKit")),
    )
    monkeypatch.setattr(virtual_device, "_legacy_notch_capture_image", lambda _request: image)
    monkeypatch.setattr(
        virtual_device,
        "_notch_runs_from_image",
        lambda captured, request: (("legacy", captured), request.scale, request.frame_x),
    )

    assert _capture_notch_runs(_request()) == (("legacy", image), 2.0, 0.0)


def test_screen_capture_kit_display_capture_targets_display_and_excludes_own_window() -> None:
    image = object()

    class Display:
        def displayID(self):
            return 1

    class Window:
        def windowID(self):
            return 90

    class Content:
        def displays(self):
            return [Display()]

        def windows(self):
            return [Window()]

    class Shareable:
        @staticmethod
        def getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            _exclude_desktop, _on_screen_only, callback
        ):
            callback(Content(), None)

    class Filter:
        last = None

        @classmethod
        def alloc(cls):
            cls.last = cls()
            return cls.last

        def initWithDisplay_excludingWindows_(self, display, excluded):
            self.display = display
            self.excluded = excluded
            return self

    class Configuration:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

        def setWidth_(self, value):
            self.width = value

        def setHeight_(self, value):
            self.height = value

        def setShowsCursor_(self, value):
            self.shows_cursor = value

        def setSourceRect_(self, value):
            self.source_rect = value

    class ScreenshotManager:
        @staticmethod
        def captureImageWithFilter_configuration_completionHandler_(
            _filter, _configuration, callback
        ):
            callback(image, None)

    result = capture_display_region_image(
        display_id=1,
        source_width=1512.0,
        source_height=32.0,
        pixel_width=3024,
        pixel_height=64,
        excluded_window_number=90,
        api=(Shareable, Filter, Configuration, ScreenshotManager),
    )

    assert result is image
    assert Filter.last.display.displayID() == 1
    assert [window.windowID() for window in Filter.last.excluded] == [90]


def _notch_runs(top_width_pt=186.0, depth=32, radius=10, left_pt=663.0):
    """Synthetic per-row px runs shaped like a real notch: straight walls,
    then corners curving inward through the last `radius` rows."""
    runs = []
    left0 = left_pt * SCALE
    right0 = (left_pt + top_width_pt) * SCALE
    for row in range(depth):
        rise = max(0, row - (depth - radius))
        inset = (rise * rise) / max(1, radius) * SCALE * 0.6
        runs.append((left0 + inset, right0 - inset))
    return runs


def test_a_real_notch_shape_validates_and_narrows_toward_the_bottom() -> None:
    result = _validated_notch_silhouette(_notch_runs(), SCALE, FRAME_X, max_width=191.0)
    assert result is not None
    x, top_width, insets = result
    assert abs(top_width - 186.0) < 0.01
    assert abs(x - 663.0) < 0.01
    assert insets[0] == (0.0, 0.0)
    final_left, final_right = insets[-1]
    assert final_left > 1.0 and final_right > 1.0
    # Monotonic: no row ever pokes back OUT past a row above it.
    for (al, ar), (bl, br) in pairwise(insets):
        assert bl >= al and br >= ar


def test_an_alcove_wide_impostor_is_rejected_by_the_slot_ceiling() -> None:
    runs = _notch_runs(top_width_pt=266.0, left_pt=623.0)
    # Passes the generic sanity band (120-320)...
    assert _validated_notch_silhouette(runs, SCALE, FRAME_X) is not None
    # ...but the physical notch cannot exceed the auxiliary-area gap.
    assert (
        _validated_notch_silhouette(runs, SCALE, FRAME_X, max_width=191.0) is None
    )


def test_a_row_wider_than_the_top_row_is_not_a_notch() -> None:
    runs = _notch_runs()
    left, right = runs[10]
    runs[10] = (left - 6.0 * SCALE, right)
    assert _validated_notch_silhouette(runs, SCALE, FRAME_X, max_width=191.0) is None


def test_a_vanishing_row_is_not_a_notch() -> None:
    runs = _notch_runs()
    runs[20] = None
    assert _validated_notch_silhouette(runs, SCALE, FRAME_X, max_width=191.0) is None


def test_the_measured_body_never_pokes_past_the_real_corner_curve() -> None:
    result = _validated_notch_silhouette(_notch_runs(), SCALE, FRAME_X, max_width=191.0)
    assert result is not None
    _x, top_width, insets = result
    depth = len(insets)
    height = depth + LED_BAND_HEIGHT
    path = notch_bar_path_from_insets(((0.0, 0.0), (top_width, height)), insets)

    # Straight-wall region: the body still reaches the full width.
    assert path.containsPoint_((0.6, height - 2.0))
    assert path.containsPoint_((top_width - 0.6, height - 2.0))

    # Corner region: the real notch has curved inward here; the OLD
    # parametric body kept full width -- the visible sliver. A point
    # just outside the measured inset must NOT be in the body.
    corner_row = depth - 1
    inset_left, inset_right = insets[corner_row]
    probe_y = height - corner_row - 0.5
    assert not path.containsPoint_((inset_left - 1.0, probe_y))
    assert not path.containsPoint_((top_width - inset_right + 1.0, probe_y))
    assert path.containsPoint_((inset_left + 1.5, probe_y))

    # The band continues at the BOTTOM width, not the top width.
    band_y = LED_BAND_HEIGHT / 2.0
    assert not path.containsPoint_((inset_left - 1.0, band_y))
    assert path.containsPoint_((top_width / 2.0, 0.5))


def test_no_insets_falls_back_to_the_parametric_shape() -> None:
    path = notch_bar_path_from_insets(((0.0, 0.0), (186.0, 37.0)), ())
    assert path.containsPoint_((93.0, 18.0))


def test_classic_draw_is_contained_and_feathers_to_black_at_the_corners() -> None:
    # 2026-08-20, from live pixels: the glow band ran at full brightness
    # into the bottom-corner fillets where the body clip sliced it along
    # the curve -- a bright hook curling up at each end -- and the wing
    # passes painted gray glow and riser columns on the menu bar's own
    # background beyond the notch. Classic mode now feathers the light to
    # housing-black before the corners and paints nothing outside the
    # body silhouette.
    import AppKit

    from sidepulse import virtual_device as vd

    wing = 30.0
    notch = 220.0
    view = vd.VirtualLedView.alloc().initWithFrame_(
        ((0, 0), (notch + 2.0 * wing, 37.0))
    )
    view.setHasNotch_(True)
    view.setNotchWidth_(notch)
    view.setPreviewWhiteBrightness_(255)

    size = (notch + 2.0 * wing, 37.0)
    image = AppKit.NSImage.alloc().initWithSize_(size)
    image.lockFocus()
    view.drawRect_(view.bounds())
    rep = AppKit.NSBitmapImageRep.alloc().initWithFocusedViewRect_(
        ((0, 0), size)
    )
    image.unlockFocus()

    def luma(x_pt: float, y_px: int) -> float:
        scale = rep.pixelsWide() / size[0]
        color = rep.colorAtX_y_(int(x_pt * scale), y_px)
        return (
            color.redComponent() + color.greenComponent() + color.blueComponent()
        )

    band_y = rep.pixelsHigh() - 3  # inside the LED band (rep y=0 is top)
    center = luma(wing + notch / 2.0, band_y)
    assert center > 1.0, f"the band itself must be lit (center luma {center})"
    # Deep into the body the band still runs at full strength...
    assert luma(wing + 20.0, band_y) > 1.0
    assert luma(wing + notch - 20.0, band_y) > 1.0
    # ...but the outermost sliver has eased down to housing-black, so the
    # corner fillets read as clean black rounding, not a bright hook.
    assert luma(wing + 1.0, band_y) < 0.2
    assert luma(wing + notch - 1.0, band_y) < 0.2
    # And the wings paint NOTHING: the window region beyond the body is
    # fully transparent (premultiplied black) all the way to the edge.
    for x_pt in (2.0, wing / 2.0, wing - 2.0):
        assert luma(x_pt, band_y) == 0.0
        assert luma(size[0] - x_pt, band_y) == 0.0
