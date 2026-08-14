from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar

from sidepulse.accessibility_display import AccessibilityDisplayPreferences

ACTIVE_RENDER_FPS = 60.0
STATIC_WATCH_FPS = 4.0


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


def choose_render_cadence(
    environment: RenderEnvironment, animation_active: bool
) -> RenderCadence:
    """Choose one deterministic paint and sampling cadence.

    Active animation gets the 60 Hz pipeline required for smooth motion.
    Static output keeps only a low-frequency change watcher. A hidden or
    sleeping surface does no paint or WASM work at all.
    """
    if not environment.visible or environment.display_asleep:
        return RenderCadence(fps=0.0, sample_fps=0.0)

    thermal = str(environment.thermal).strip().lower()
    if not animation_active:
        constrained = environment.low_power or thermal in {"serious", "critical"}
        fps = 1.0 if constrained else STATIC_WATCH_FPS
        return RenderCadence(fps=fps, sample_fps=fps)

    fps = ACTIVE_RENDER_FPS
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
) -> RenderSchedule:
    """Choose the render driver while preserving the established cadence policy."""
    cadence = choose_render_cadence(environment, animation_active)
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
    return RenderSchedule(
        driver=driver,
        cadence=cadence,
        next_visual_change_at=next_visual_change_at,
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
