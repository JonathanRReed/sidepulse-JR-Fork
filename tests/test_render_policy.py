from __future__ import annotations

import math
from dataclasses import FrozenInstanceError

import pytest

from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.render_policy import (
    BoundedRenderCache,
    GlowGeometryKey,
    GlowPaintKey,
    RenderEnvironment,
    choose_render_cadence,
    choose_render_schedule,
    reduce_accessibility_generation_change,
    rounded_silhouette,
    runtime_render_environment,
)


def test_hidden_or_sleeping_surface_pauses() -> None:
    assert choose_render_cadence(RenderEnvironment(visible=False), True).fps == 0.0
    assert (
        choose_render_cadence(
            RenderEnvironment(visible=True, display_asleep=True), True
        ).fps
        == 0.0
    )


def test_static_and_active_cadences_are_adaptive() -> None:
    environment = RenderEnvironment(visible=True)

    static = choose_render_cadence(environment, False)
    active = choose_render_cadence(environment, True)

    assert 0.0 < static.fps <= 4.0
    assert active.fps >= 60.0
    assert active.fps > static.fps
    assert active.sample_fps == active.fps


def test_render_schedule_reuses_the_cadence_policy_result() -> None:
    environment = RenderEnvironment(visible=True, low_power=True, thermal="serious")

    schedule = choose_render_schedule(
        environment,
        animation_active=True,
        display_link_available=True,
    )

    assert schedule.cadence == choose_render_cadence(environment, animation_active=True)


def test_render_schedule_preserves_the_next_visual_change_deadline() -> None:
    """Catches finite cue demotion losing the pulse deadline at driver selection."""
    schedule = choose_render_schedule(
        RenderEnvironment(visible=True),
        animation_active=True,
        display_link_available=True,
        next_visual_change_at=42.75,
    )

    assert schedule.next_visual_change_at == 42.75


def test_low_power_and_thermal_pressure_reduce_cadence() -> None:
    normal = choose_render_cadence(RenderEnvironment(visible=True), True)
    low_power = choose_render_cadence(
        RenderEnvironment(visible=True, low_power=True), True
    )
    serious = choose_render_cadence(
        RenderEnvironment(visible=True, thermal="serious"), True
    )
    constrained = choose_render_cadence(
        RenderEnvironment(visible=True, low_power=True, thermal="serious"), True
    )
    critical = choose_render_cadence(
        RenderEnvironment(visible=True, thermal="critical"), True
    )

    assert low_power.fps < normal.fps
    assert serious.fps < normal.fps
    assert constrained.fps <= 10.0
    assert critical.fps < serious.fps


def test_accessibility_snapshot_and_generation_do_not_change_cadence() -> None:
    baseline = RenderEnvironment(visible=True, low_power=True, thermal="serious")
    accessible = RenderEnvironment(
        visible=True,
        low_power=True,
        thermal="serious",
        preferences=AccessibilityDisplayPreferences(
            reduce_motion=True,
            reduce_transparency=True,
            increase_contrast=True,
            differentiate_without_color=True,
        ),
        accessibility_generation=41,
    )

    assert choose_render_cadence(accessible, False) == choose_render_cadence(
        baseline, False
    )
    assert choose_render_cadence(accessible, True) == choose_render_cadence(
        baseline, True
    )
    assert choose_render_schedule(
        accessible, True, display_link_available=True
    ) == choose_render_schedule(baseline, True, display_link_available=True)
    with pytest.raises(FrozenInstanceError):
        accessible.accessibility_generation = 42  # type: ignore[misc]


def test_accessibility_generation_change_marks_dirty_without_creating_cue() -> None:
    previous = RenderEnvironment(accessibility_generation=8)
    current = RenderEnvironment(
        preferences=AccessibilityDisplayPreferences(reduce_motion=True),
        accessibility_generation=9,
    )

    change = reduce_accessibility_generation_change(previous, current)

    assert change.presentation_dirty is True
    assert change.create_cue is False


def test_unchanged_accessibility_generation_is_not_dirty_or_a_cue() -> None:
    previous = RenderEnvironment(accessibility_generation=8)
    current = RenderEnvironment(
        preferences=AccessibilityDisplayPreferences(increase_contrast=True),
        accessibility_generation=8,
    )

    change = reduce_accessibility_generation_change(previous, current)

    assert change.presentation_dirty is False
    assert change.create_cue is False


def test_geometry_key_is_color_free_but_invalidates_geometry_inputs() -> None:
    cache: BoundedRenderCache[object] = BoundedRenderCache(max_entries=3)
    builds = 0

    def build() -> object:
        nonlocal builds
        builds += 1
        return object()

    base = GlowGeometryKey.from_output(
        screen_identity="built-in:1",
        scale=2.0,
        dimensions=(220.0, 37.0),
        led_count=8,
        width=220.0,
        silhouette=((0.0, 0.0), (220.0, 0.0), (220.0, 37.0), (0.0, 0.0)),
    )
    first = cache.get_or_build(base, build)
    second = cache.get_or_build(base, build)
    resized = cache.get_or_build(
        GlowGeometryKey.from_output(
            screen_identity="built-in:1",
            scale=2.0,
            dimensions=(221.0, 37.0),
            led_count=8,
            width=221.0,
            silhouette=((0.0, 0.0), (221.0, 0.0), (221.0, 37.0), (0.0, 0.0)),
        ),
        build,
    )

    assert first is second
    assert resized is not first
    assert builds == 2
    assert cache.metrics.hits == 1
    assert cache.metrics.misses == 2


def test_geometry_keys_ignore_color_and_brightness_but_cover_geometry_identity() -> None:
    first = GlowGeometryKey.from_output(
        screen_identity="built-in:1",
        scale=2.0,
        dimensions=(220.0, 37.0),
        led_count=8,
        width=220.0,
        silhouette="rounded",
        brightness=255,
        colors=((0.2, 0.4, 0.8, 1.0),),
    )
    second = GlowGeometryKey.from_output(
        screen_identity="built-in:1",
        scale=2.0,
        dimensions=(220.0, 37.0),
        led_count=8,
        width=220.0,
        silhouette="rounded",
        brightness=32,
        colors=((1.0, 0.0, 0.0, 1.0),),
    )

    assert first == second
    variants = [
        {"screen_identity": "external:2"},
        {"scale": 1.0},
        {"dimensions": (221.0, 37.0)},
        {"led_count": 12},
        {"width": 218.0},
        {"silhouette": "contoured"},
    ]
    for change in variants:
        values = {
            "screen_identity": "built-in:1",
            "scale": 2.0,
            "dimensions": (220.0, 37.0),
            "led_count": 8,
            "width": 220.0,
            "silhouette": "rounded",
        }
        values.update(change)
        assert GlowGeometryKey.from_output(**values) != first


def test_paint_key_changes_for_every_paint_input() -> None:
    geometry = GlowGeometryKey.from_output(
        screen_identity="built-in:1",
        scale=2.0,
        dimensions=(220.0, 37.0),
        led_count=8,
        width=220.0,
        silhouette="rounded",
    )
    base = GlowPaintKey.from_output(
        geometry=geometry,
        colors=((0.2, 0.4, 0.8, 1.0),),
        brightness=255,
        contrast=False,
        transparency=True,
        differentiate_without_color=False,
    )
    changes = [
        {"colors": ((0.9, 0.4, 0.8, 1.0),)},
        {"brightness": 128},
        {"contrast": True},
        {"transparency": False},
        {"differentiate_without_color": True},
    ]
    for change in changes:
        values = {
            "geometry": geometry,
            "colors": ((0.2, 0.4, 0.8, 1.0),),
            "brightness": 255,
            "contrast": False,
            "transparency": True,
            "differentiate_without_color": False,
        }
        values.update(change)
        assert GlowPaintKey.from_output(**values) != base


def test_rounded_silhouette_is_one_closed_body_and_clamps_radius() -> None:
    silhouette = rounded_silhouette(
        center_x=110.0,
        width=220.0,
        height=12.0,
        contour=((0.0, 3.0), (20.0, 0.0), (200.0, 0.0), (220.0, 3.0)),
        requested_radius=80.0,
    )

    assert silhouette.points[0] == silhouette.points[-1]
    assert silhouette.radius == 6.0
    assert len(silhouette.points) >= 5
    assert all(
        math.dist(first, second) <= 220.0
        for first, second in zip(silhouette.points, silhouette.points[1:])
    )


def test_glow_composition_reuses_geometry_across_paint_changes() -> None:
    from sidepulse.virtual_device import _glow_runs

    geometry_cache: BoundedRenderCache[object] = BoundedRenderCache(max_entries=4)
    paint_cache: BoundedRenderCache[object] = BoundedRenderCache(max_entries=8)
    common = {
        "brightness": 255,
        "led_width": 10.0,
        "notch_width": 80.0,
        "x_start": 0.0,
        "x_end": 80.0,
        "wing_offset": 0.0,
        "wing_taper_floor": 1.0,
    }

    red = _glow_runs(
        geometry_cache,
        paint_cache,
        colors=((1.0, 0.0, 0.0, 1.0),) * 8,
        **common,
    )
    blue = _glow_runs(
        geometry_cache,
        paint_cache,
        colors=((0.0, 0.0, 1.0, 1.0),) * 8,
        **common,
    )
    repeated_blue = _glow_runs(
        geometry_cache,
        paint_cache,
        colors=((0.0, 0.0, 1.0, 1.0),) * 8,
        **common,
    )

    assert red != blue
    assert blue is repeated_blue
    assert geometry_cache.metrics.misses == 1
    assert geometry_cache.metrics.hits == 2
    assert paint_cache.metrics.misses == 2
    assert paint_cache.metrics.hits == 1


def test_runtime_environment_reads_public_power_state_with_fallbacks() -> None:
    class ProcessInfo:
        @staticmethod
        def isLowPowerModeEnabled() -> bool:
            return True

        @staticmethod
        def thermalState() -> int:
            return 2

    environment = runtime_render_environment(
        visible=True,
        display_asleep=False,
        process_info=ProcessInfo(),
    )
    fallback = runtime_render_environment(
        visible=True,
        process_info=object(),
    )

    assert environment.low_power is True
    assert environment.thermal == "serious"
    assert fallback.low_power is False
    assert fallback.thermal == "nominal"
