"""Pure scene policies for the JR-Bar ambient display.

Scenes are intentionally data only.  Runtime and AppKit layers can consume a
validated :class:`ScenePolicy` without this module knowing about devices,
windows, preferences, or notification APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .dnd_policy import DisplayAdmission


class Scene(str, Enum):
    FOCUS = "focus"
    CALM = "calm"
    NIGHT = "night"
    DEMO = "demo"
    TRAVEL = "travel"
    DND = "dnd"


DEFAULT_SCENE = Scene.CALM


class SurfaceRole(str, Enum):
    AMBIENT = "ambient"
    STATUS = "status"
    PREVIEW = "preview"


class MotionLevel(str, Enum):
    FULL = "full"
    REDUCED = "reduced"
    STATIC = "static"


class NotificationMode(str, Enum):
    ALL = "all"
    IMPORTANT = "important"
    NONE = "none"


class DeviceSelection(str, Enum):
    ACTIVE = "active"
    ALL = "all"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ScenePolicy:
    """Complete, serializable presentation policy for one scene."""

    scene: Scene
    surface_role: SurfaceRole
    brightness: float
    motion: MotionLevel
    notifications: NotificationMode
    display_admission: DisplayAdmission
    device_selection: DeviceSelection
    reduce_motion: bool = False

    @property
    def effective_motion(self) -> MotionLevel:
        if self.reduce_motion or self.motion is MotionLevel.STATIC:
            return MotionLevel.STATIC
        if self.motion is MotionLevel.FULL:
            return MotionLevel.REDUCED if self.reduce_motion else MotionLevel.FULL
        return MotionLevel.STATIC if self.reduce_motion else MotionLevel.REDUCED


SCENE_POLICIES: dict[Scene, ScenePolicy] = {
    Scene.FOCUS: ScenePolicy(
        Scene.FOCUS,
        SurfaceRole.STATUS,
        0.72,
        MotionLevel.REDUCED,
        NotificationMode.IMPORTANT,
        DisplayAdmission.ASKS,
        DeviceSelection.ACTIVE,
    ),
    Scene.CALM: ScenePolicy(
        Scene.CALM,
        SurfaceRole.AMBIENT,
        0.42,
        MotionLevel.REDUCED,
        NotificationMode.IMPORTANT,
        DisplayAdmission.ASKS,
        DeviceSelection.ACTIVE,
    ),
    Scene.NIGHT: ScenePolicy(
        Scene.NIGHT,
        SurfaceRole.AMBIENT,
        0.18,
        MotionLevel.STATIC,
        NotificationMode.NONE,
        DisplayAdmission.NONE,
        DeviceSelection.ACTIVE,
    ),
    Scene.DEMO: ScenePolicy(
        Scene.DEMO,
        SurfaceRole.PREVIEW,
        0.8,
        MotionLevel.FULL,
        NotificationMode.ALL,
        DisplayAdmission.ALL,
        DeviceSelection.ALL,
    ),
    Scene.TRAVEL: ScenePolicy(
        Scene.TRAVEL,
        SurfaceRole.STATUS,
        0.62,
        MotionLevel.REDUCED,
        NotificationMode.IMPORTANT,
        DisplayAdmission.CRITICAL,
        DeviceSelection.ACTIVE,
    ),
    Scene.DND: ScenePolicy(
        Scene.DND,
        SurfaceRole.AMBIENT,
        0.28,
        MotionLevel.STATIC,
        NotificationMode.NONE,
        DisplayAdmission.NONE,
        DeviceSelection.NONE,
    ),
}


def scene_from_value(value: object) -> Scene | None:
    """Parse a persisted scene value, returning ``None`` for invalid input."""
    if isinstance(value, Scene):
        return value
    if type(value) is not str:
        return None
    try:
        return Scene(value)
    except ValueError:
        return None


def policy_for_scene(scene: object, *, reduce_motion: bool = False) -> ScenePolicy | None:
    """Return an immutable policy, failing closed for malformed input."""
    selected = scene_from_value(scene)
    if selected is None or type(reduce_motion) is not bool:
        return None
    policy = SCENE_POLICIES.get(selected)
    if policy is None:
        return None
    values = {
        field: getattr(policy, field) for field in policy.__dataclass_fields__
    }
    return ScenePolicy(**{**values, "reduce_motion": reduce_motion})


def effective_policy_for_scene(
    scene: object,
    *,
    accessibility_preferences: object | None = None,
) -> ScenePolicy | None:
    """Resolve a scene against an already-read accessibility snapshot.

    This adapter deliberately performs no system preference lookup. Runtime
    owners can pass their existing accessibility snapshot when one is
    available; callers without one retain the scene's normal motion policy.
    """
    reduce_motion = False
    if accessibility_preferences is not None:
        reduce_motion = getattr(accessibility_preferences, "reduce_motion", None)
        if type(reduce_motion) is not bool:
            return None
    return policy_for_scene(scene, reduce_motion=reduce_motion)


def scene_options() -> tuple[Scene, ...]:
    """Return scenes in stable menu order."""
    return tuple(Scene)


__all__ = [
    "DEFAULT_SCENE",
    "SCENE_POLICIES",
    "DeviceSelection",
    "MotionLevel",
    "NotificationMode",
    "Scene",
    "ScenePolicy",
    "SurfaceRole",
    "effective_policy_for_scene",
    "policy_for_scene",
    "scene_from_value",
    "scene_options",
]
