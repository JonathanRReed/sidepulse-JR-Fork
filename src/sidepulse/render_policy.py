from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar

from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.presentation_scheduler import FRAME_FALLBACK_INTERVAL_SECONDS

ACTIVE_RENDER_FPS = 60.0
STATIC_WATCH_FPS = 4.0
# A slow breathe does not need transition framerates. Rendering an idle
# pulse at 60-120 Hz was the single largest CPU draw in the app, and it
# never stopped, because "is anything animating" was true whenever any
# agent existed.
GENTLE_MOTION_FPS = 30.0
# How fast each driver actually calls back. A cadence the driver cannot
# produce is not a cadence, it is a wish: asking a 60 Hz timer for 40 fps got
# 30, deterministically, at zero jitter -- and the policy went on reporting 40.
# The timer's rate is the shared runtime scheduler's frame-fallback interval,
# imported rather than restated so the two cannot drift apart. The display
# link's is what _install_display_link negotiates: the panel, floored at 60 and
# capped at 120.
TIMER_DRIVER_FPS = 1.0 / FRAME_FALLBACK_INTERVAL_SECONDS
DISPLAY_LINK_MIN_FPS = 60.0
DISPLAY_LINK_MAX_FPS = 120.0


def refresh_divisor_fps(refresh_hz: float | None, target_fps: float) -> float:
    """Snap a target framerate to an integer divisor of the panel.

    A cadence that is not a whole fraction of the display's refresh
    beats against vsync and reads as judder -- the exact opposite of
    what a slower cadence is for. 20 fps asked of a 120 Hz panel becomes
    exactly every sixth frame; asked of a 90 Hz panel it becomes 18
    (every fifth) rather than a cadence that slips a frame forever.

    DELIBERATELY UNWIRED. This is the right answer for a driver that is
    genuinely vsync-locked to the panel, and neither driver here is one -- see
    the comment in ``choose_render_schedule`` for why composing it with
    ``deliverable_fps`` cost a third of the framerate on every panel that is
    not a whole multiple of 60. Kept because the reasoning above is sound and a
    future vsync-locked driver would want it; not called, and
    ``tests/test_panel_refresh_delivered_fps.py`` fails if it is called again.
    """
    if not refresh_hz or refresh_hz <= 0.0 or target_fps <= 0.0:
        return target_fps
    if target_fps >= refresh_hz:
        return float(refresh_hz)
    for divisor in range(1, 241):
        candidate = refresh_hz / divisor
        if candidate <= target_fps + 1e-9:
            return candidate
    return target_fps


@dataclass(frozen=True, slots=True)
class RenderEnvironment:
    visible: bool = True
    display_asleep: bool = False
    low_power: bool = False
    thermal: str = "nominal"
    preferences: AccessibilityDisplayPreferences = field(
        default_factory=AccessibilityDisplayPreferences
    )
    accessibility_generation: int = 0


@dataclass(frozen=True, slots=True)
class AccessibilityGenerationChange:
    presentation_dirty: bool
    create_cue: bool


def reduce_accessibility_generation_change(
    previous: RenderEnvironment,
    current: RenderEnvironment,
) -> AccessibilityGenerationChange:
    """Reduce a preference generation edge without inventing an arrival cue."""
    return AccessibilityGenerationChange(
        presentation_dirty=(
            current.accessibility_generation != previous.accessibility_generation
        ),
        create_cue=False,
    )


@dataclass(frozen=True, slots=True)
class RenderCadence:
    fps: float
    sample_fps: float

    @property
    def interval(self) -> float | None:
        return None if self.fps <= 0.0 else 1.0 / self.fps

    @property
    def sample_interval(self) -> float | None:
        return None if self.sample_fps <= 0.0 else 1.0 / self.sample_fps


class RenderDriverKind(str, Enum):
    PAUSED = "paused"
    DISPLAY_LINK = "display_link"
    TIMER = "timer"


@dataclass(frozen=True, slots=True)
class RenderSchedule:
    driver: RenderDriverKind
    cadence: RenderCadence
    next_visual_change_at: float | None = None
    # How fast the chosen driver will actually call back. Carried on the
    # schedule because the gate that turns callbacks into frames has to know
    # both numbers to be safe against jitter -- see presentation_hold_seconds.
    # 0.0 means "not stated", which the gate treats as the old fixed tolerance.
    driver_fps: float = 0.0


def driver_callback_fps(
    driver: RenderDriverKind, refresh_hz: float | None
) -> float:
    """The rate the chosen driver will really fire at."""
    if driver is RenderDriverKind.TIMER:
        return TIMER_DRIVER_FPS
    if driver is RenderDriverKind.DISPLAY_LINK:
        panel = float(refresh_hz) if refresh_hz else DISPLAY_LINK_MIN_FPS
        return min(max(DISPLAY_LINK_MIN_FPS, panel), DISPLAY_LINK_MAX_FPS)
    return 0.0


def deliverable_fps(driver_fps: float, target_fps: float) -> float:
    """The fastest rate at or below ``target_fps`` this driver can produce.

    A driver that fires N times a second can only deliver N/1, N/2, N/3...
    Anything else is delivered as the next one DOWN -- never up, because these
    targets are thermal and low-power ceilings and overshooting one is the
    failure the ceiling exists to prevent. Rounding down here is what makes the
    policy's number and the screen's number the same number.
    """
    if driver_fps <= 0.0 or target_fps <= 0.0:
        return target_fps
    if target_fps >= driver_fps:
        return driver_fps
    divisor = math.ceil(driver_fps / target_fps - 1e-9)
    return driver_fps / max(1, divisor)


def presentation_hold_seconds(schedule: RenderSchedule) -> float | None:
    """How long after a presented frame the next callback must be refused.

    The naive threshold is the cadence interval itself, and it fails the moment
    the cadence equals the driver rate: the stamp is taken from the JITTERED
    callback time, so one late callback makes the next measurement come up
    short and it is dropped -- 60 fps asked of a 60 Hz driver measured 50.2 fps
    under 4 ms of main-thread jitter, and the gate can never recover lost time,
    only lose more. Irregular drops are judder, which is the exact failure a
    steadier cadence exists to prevent.

    Half a DRIVER period of headroom fixes it, because the question the gate is
    really asking is "will there be another callback before the interval is
    up?". At 1:1 the answer is never, so nothing is dropped; at 2:1 a callback
    has to arrive more than half a period early to be miscounted, which is 8.3
    ms of jitter at 60 Hz rather than 1.7 ms.
    """
    interval = schedule.cadence.interval
    if interval is None:
        return None
    driver_fps = getattr(schedule, "driver_fps", 0.0) or 0.0
    if driver_fps <= 0.0:
        # Nobody said what the driver is (hand-built schedules, tests). Keep
        # the old fixed tolerance rather than inventing a period.
        return interval * 0.9
    period = 1.0 / driver_fps
    ticks = max(1, math.ceil(interval / period - 1e-9))
    return (ticks - 0.5) * period


def choose_render_cadence(
    environment: RenderEnvironment,
    animation_active: bool,
    *,
    gentle_motion: bool = False,
    refresh_hz: float | None = None,
) -> RenderCadence:
    """Choose one deterministic paint and sampling cadence.

    Transitions get the full pipeline required for smooth motion.
    ``gentle_motion`` marks slow, continuous breathing -- the resting
    state of the whole app -- which looks identical at a fraction of the
    cost. Static output keeps only a low-frequency change watcher, and a
    hidden or sleeping surface does no paint or WASM work at all.

    This returns the CEILING the environment allows, in whole fps. It is not
    snapped to anything and ``refresh_hz`` is accepted but ignored -- which rate
    is reachable depends on the driver, not the panel, and that decision belongs
    to ``choose_render_schedule`` where the driver is known. (The docstring used
    to claim a snap to an integer divisor of ``refresh_hz``; no such snap has
    ever happened in this function.)
    """
    if not environment.visible or environment.display_asleep:
        return RenderCadence(fps=0.0, sample_fps=0.0)

    thermal = str(environment.thermal).strip().lower()
    if not animation_active:
        constrained = environment.low_power or thermal in {"serious", "critical"}
        fps = 1.0 if constrained else STATIC_WATCH_FPS
        return RenderCadence(fps=fps, sample_fps=fps)

    fps = GENTLE_MOTION_FPS if gentle_motion else ACTIVE_RENDER_FPS
    if thermal == "fair":
        fps = min(fps, 45.0)
    elif thermal == "serious":
        fps = min(fps, 15.0)
    elif thermal == "critical":
        fps = min(fps, 8.0)

    if environment.low_power:
        if thermal == "serious":
            fps = min(fps, 10.0)
        elif thermal == "critical":
            fps = min(fps, 5.0)
        else:
            fps = min(fps, 30.0)
    return RenderCadence(fps=fps, sample_fps=fps)


def choose_render_schedule(
    environment: RenderEnvironment,
    animation_active: bool,
    *,
    display_link_available: bool,
    next_visual_change_at: float | None = None,
    gentle_motion: bool = False,
    refresh_hz: float | None = None,
) -> RenderSchedule:
    """Choose the render driver while preserving the established cadence policy."""
    cadence = choose_render_cadence(
        environment,
        animation_active,
        gentle_motion=gentle_motion,
    )
    if cadence.fps <= 0.0:
        driver = RenderDriverKind.PAUSED
    elif (
        animation_active
        and display_link_available
        and environment.visible
        and not environment.display_asleep
        and not environment.low_power
        and str(environment.thermal).strip().lower() == "nominal"
    ):
        driver = RenderDriverKind.DISPLAY_LINK
    else:
        driver = RenderDriverKind.TIMER
    driver_fps = driver_callback_fps(driver, refresh_hz)
    # ONE quantiser, and it is the driver's. The gate downstream presents one
    # driver callback in every n, so the only rates that physically exist are
    # driver_fps/n. A second quantiser in FRONT of this one can only move the
    # target off that lattice, where this one then has to round it further
    # DOWN -- composing quantisers is lossless only when one lattice contains
    # the other, and {panel/m} sits inside {driver/n} exactly when the panel is
    # a whole multiple of the driver.
    #
    # A panel snap used to sit here (refresh_divisor_fps). For the 60 Hz
    # fallback timer that made it a no-op on 60/120/240 Hz -- precisely the
    # panels the earlier tests covered -- and a loss everywhere else: a 144 Hz
    # display turned a 30 fps breathe into 28.8, which this call then floored to
    # 20. A third of the delivered framerate, spent to correct a 4% misreport.
    #
    # It could not have bought vsync alignment on either path anyway. The
    # fallback timer free-runs at TIMER_DRIVER_FPS and is not locked to the
    # panel at all, so subsampling it by an integer changes how OFTEN it beats
    # against a 144 Hz panel, never WHETHER it beats. And the display link IS
    # the panel -- driver_callback_fps returns exactly what _install_display_link
    # negotiates -- so quantising to that driver already quantises to the panel.
    snapped = deliverable_fps(driver_fps, cadence.fps)
    if snapped != cadence.fps:
        cadence = RenderCadence(fps=snapped, sample_fps=snapped)
    return RenderSchedule(
        driver=driver,
        cadence=cadence,
        next_visual_change_at=next_visual_change_at,
        driver_fps=driver_fps,
    )


def alcove_bracket_corner_radius(width: float, height: float) -> float:
    """Return a bracket radius that never exceeds either dimension."""
    return max(0.0, min(8.0, float(width) / 2.0, float(height) / 2.0))


_THERMAL_NAMES = {
    0: "nominal",
    1: "fair",
    2: "serious",
    3: "critical",
}


def runtime_render_environment(
    *,
    visible: bool,
    display_asleep: bool = False,
    process_info=None,
) -> RenderEnvironment:
    """Read public ProcessInfo power state with a fail-open fallback."""
    if process_info is None:
        try:
            from Foundation import NSProcessInfo

            process_info = NSProcessInfo.processInfo()
        except Exception:
            process_info = None

    low_power = False
    thermal = "nominal"
    if process_info is not None:
        try:
            low_power = bool(process_info.isLowPowerModeEnabled())
        except Exception:
            pass
        try:
            thermal = _THERMAL_NAMES.get(int(process_info.thermalState()), "nominal")
        except Exception:
            pass
    return RenderEnvironment(
        visible=bool(visible),
        display_asleep=bool(display_asleep),
        low_power=low_power,
        thermal=thermal,
    )


@dataclass(frozen=True, slots=True)
class GlowGeometryKey:
    screen_identity: str
    scale: int
    dimensions: tuple[int, ...]
    led_count: int
    width: int
    silhouette: str | tuple[int, ...]

    @classmethod
    def from_output(
        cls,
        *,
        screen_identity: str = "unknown",
        scale: float = 1.0,
        dimensions: Iterable[float],
        led_count: int = 8,
        width: float | None = None,
        silhouette: str | Iterable[Iterable[float]] = "rectangular",
        brightness: float | None = None,
        colors: Iterable[Iterable[float]] = (),
    ) -> GlowGeometryKey:
        del brightness, colors
        quantized_dimensions = tuple(
            int(round(float(value) * 64.0)) for value in dimensions
        )
        quantized_silhouette: str | tuple[int, ...]
        if isinstance(silhouette, str):
            quantized_silhouette = silhouette
        else:
            quantized_silhouette = tuple(
                int(round(float(channel) * 64.0))
                for point in silhouette
                for channel in point
            )
        resolved_width = (
            float(width)
            if width is not None
            else (quantized_dimensions[0] / 64.0 if quantized_dimensions else 0.0)
        )
        return cls(
            screen_identity=str(screen_identity),
            scale=int(round(float(scale) * 1024.0)),
            dimensions=quantized_dimensions,
            led_count=int(led_count),
            width=int(round(resolved_width * 64.0)),
            silhouette=quantized_silhouette,
        )


@dataclass(frozen=True, slots=True)
class GlowPaintKey:
    geometry: GlowGeometryKey
    colors: tuple[tuple[int, ...], ...]
    brightness: int
    contrast: bool
    transparency: bool
    differentiate_without_color: bool
    variant: tuple[int, ...]

    @classmethod
    def from_output(
        cls,
        *,
        geometry: GlowGeometryKey,
        colors: Iterable[Iterable[float]],
        brightness: float,
        contrast: bool = False,
        transparency: bool = True,
        differentiate_without_color: bool = False,
        variant: Iterable[float] = (),
    ) -> GlowPaintKey:
        return cls(
            geometry=geometry,
            colors=tuple(
                tuple(int(round(float(channel) * 1024.0)) for channel in color)
                for color in colors
            ),
            brightness=int(round(float(brightness) * 16.0)),
            contrast=bool(contrast),
            transparency=bool(transparency),
            differentiate_without_color=bool(differentiate_without_color),
            variant=tuple(int(round(float(value) * 1024.0)) for value in variant),
        )


_Value = TypeVar("_Value")


@dataclass(frozen=True, slots=True)
class RenderCacheMetrics:
    hits: int
    misses: int
    evictions: int


class BoundedRenderCache(Generic[_Value]):
    def __init__(self, *, max_entries: int = 64) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.max_entries = int(max_entries)
        self._values: OrderedDict[object, _Value] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, key: object) -> bool:
        return key in self._values

    @property
    def metrics(self) -> RenderCacheMetrics:
        return RenderCacheMetrics(
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
        )

    def get_or_build(
        self, key: object, builder: Callable[[], _Value]
    ) -> _Value:
        try:
            value = self._values.pop(key)
        except KeyError:
            self._misses += 1
            value = builder()
        else:
            self._hits += 1
        self._values[key] = value
        while len(self._values) > self.max_entries:
            self._values.popitem(last=False)
            self._evictions += 1
        return value


@dataclass(frozen=True, slots=True)
class RoundedSilhouette:
    points: tuple[tuple[float, float], ...]
    radius: float


def rounded_silhouette(
    *,
    center_x: float,
    width: float,
    height: float,
    contour: Iterable[Iterable[float]] = (),
    requested_radius: float = 8.0,
) -> RoundedSilhouette:
    """Return one closed body with a radius clamped to its measured bounds."""
    resolved_width = max(0.0, float(width))
    resolved_height = max(0.0, float(height))
    radius = max(
        0.0,
        min(
            float(requested_radius),
            resolved_width / 2.0,
            resolved_height / 2.0,
        ),
    )
    points = tuple((float(point[0]), float(point[1])) for point in contour)
    if len(points) < 3:
        left = float(center_x) - resolved_width / 2.0
        right = float(center_x) + resolved_width / 2.0
        points = (
            (left, 0.0),
            (right, 0.0),
            (right, resolved_height),
            (left, resolved_height),
        )
    if points[0] != points[-1]:
        points += (points[0],)
    return RoundedSilhouette(points=points, radius=radius)
