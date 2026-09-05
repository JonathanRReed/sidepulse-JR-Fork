from __future__ import annotations

from unittest.mock import patch

from AppKit import NSApplication, NSImage

from sidepulse import virtual_device
from sidepulse.presentation_policy import MotionClass
from sidepulse.render_policy import RenderEnvironment


def _view():
    return virtual_device.VirtualLedView.alloc().initWithFrame_(
        ((0.0, 0.0), (220.0, 37.0))
    )


def test_draw_does_not_search_objc_for_uninitialized_python_state() -> None:
    NSApplication.sharedApplication()
    view = _view()
    view._presentation_colors = ((0.1, 0.4, 0.8, 1.0),) * virtual_device.LED_COUNT
    image = NSImage.alloc().initWithSize_((220.0, 37.0))
    missing = set()

    def observed_getattr(target, name, *default):
        if target is view and name not in vars(view):
            missing.add(name)
        return getattr(target, name, *default)

    image.lockFocus()
    try:
        with patch.object(virtual_device, "getattr", observed_getattr, create=True):
            view.drawRect_(((0.0, 0.0), (220.0, 37.0)))
    finally:
        image.unlockFocus()

    assert missing == set(), f"Per-frame Objective-C attribute misses: {missing}"


def test_glow_row_uses_four_native_gradient_draws_without_bridged_column_fills() -> None:
    view = _view()
    colors = ((0.1, 0.4, 0.8, 1.0),) * virtual_device.LED_COUNT
    draws: list[tuple[object, object, float, float]] = []
    fallback_fills: list[object] = []

    with (
        patch.object(
            virtual_device,
            "draw_horizontal_glow_gradient",
            side_effect=lambda context, rect, runs, *, boost, alpha_scale: (
                draws.append((context, rect, boost, alpha_scale)) or True
            ),
        ),
        patch.object(
            virtual_device,
            "fill_rect_with_cg",
            side_effect=lambda *args: fallback_fills.append(args),
        ),
    ):
        view._fill_glow_row(
            object(),
            colors,
            27.5,
            220.0,
            11.0,
            37.0,
            x_start=0.0,
            x_end=220.0,
            wing_offset=0.0,
        )

    assert [(boost, alpha) for _context, _rect, boost, alpha in draws] == [
        (0.82, 0.18),
        (0.64, 0.07),
        (virtual_device.LED_CORE_BOOST, 0.92),
        (virtual_device.LED_HOTLINE_BOOST, 0.72),
    ]
    assert fallback_fills == []


def test_glow_row_retains_bounded_fill_fallback_when_native_gradient_fails() -> None:
    view = _view()
    colors = ((0.1, 0.4, 0.8, 1.0),) * virtual_device.LED_COUNT
    fallback_fills: list[object] = []

    with (
        patch.object(
            virtual_device,
            "draw_horizontal_glow_gradient",
            return_value=False,
        ),
        patch.object(
            virtual_device,
            "fill_rect_with_cg",
            side_effect=lambda *args: fallback_fills.append(args),
        ),
    ):
        view._fill_glow_row(
            object(),
            colors,
            27.5,
            220.0,
            11.0,
            37.0,
            x_start=0.0,
            x_end=220.0,
            wing_offset=0.0,
        )

    assert 1 <= len(fallback_fills) <= 440


def test_native_gradient_rejects_missing_context_or_degenerate_geometry() -> None:
    runs = ((0.0, 10.0, (1.0, 0.0, 0.0, 1.0)),)

    assert not virtual_device.draw_horizontal_glow_gradient(
        None,
        ((0.0, 0.0), (10.0, 2.0)),
        runs,
        boost=1.0,
        alpha_scale=1.0,
    )
    assert not virtual_device.draw_horizontal_glow_gradient(
        object(),
        ((0.0, 0.0), (0.0, 2.0)),
        runs,
        boost=1.0,
        alpha_scale=1.0,
    )


def test_continuous_sampler_uses_the_same_gentle_cadence_as_the_surface() -> None:
    class Window:
        @staticmethod
        def isVisible() -> bool:
            return True

    class View:
        current_program = None

        def setPresentationProgram_startedAt_(self, program, started_at) -> None:
            self.current_program = program

        def setRenderFps_(self, _fps) -> None:
            pass

    class Sampler:
        def __init__(self) -> None:
            self.commands = []

        def reconcile(self, command) -> None:
            self.commands.append(command)

    device = virtual_device.VirtualStatusDevice.alloc().init()
    sampler = Sampler()
    device.window = Window()
    device.view = View()
    device._sampler = sampler
    device.show = lambda: None
    device._runtime_environment = lambda **_kwargs: RenderEnvironment(visible=True)
    device._display_link_available = lambda: False
    device._publish_presentation_schedule = lambda: None

    device.set_program("#12E3B0 6s pulse", motion=MotionClass.CONTINUOUS)

    assert sampler.commands[-1].sample_interval == (
        1.0 / virtual_device.GENTLE_MOTION_FPS
    )
