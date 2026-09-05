"""Versioned, data-only Scene pack contracts.

Scene packs may override policies for JR-Bar's existing Scenes. They contain
only bounded JSON data and are never imported as Python modules or executed as
callbacks.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, TypeVar
from urllib.parse import urlparse

from .dnd_policy import DisplayAdmission
from .scenes import (
    DeviceSelection,
    MotionLevel,
    NotificationMode,
    Scene,
    ScenePolicy,
    SurfaceRole,
)

CURRENT_SCENE_PACK_VERSION: Final = 2
MAX_SCENE_PACK_BYTES: Final = 128_000
MAX_SCENES_PER_PACK: Final = len(Scene)

_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
_TOP_LEVEL_KEYS: Final = frozenset(
    {
        "accessibility",
        "id",
        "name",
        "safety",
        "scenes",
        "version",
    }
)
_SCENE_KEYS: Final = frozenset(
    {
        "brightness",
        "device_selection",
        "display_admission",
        "label",
        "motion",
        "notifications",
        "scene",
        "surface_role",
    }
)
_CODE_KEYS: Final = frozenset(
    {
        "callback",
        "code",
        "command",
        "entrypoint",
        "executable",
        "handler",
        "hook",
        "import",
        "module",
        "plugin",
        "script",
    }
)
_CODE_MARKERS: Final = (
    re.compile(
        r"(?<![A-Za-z0-9_])(?:python(?:\d+(?:\.\d+)*)?|javascript|bash|sh|eval|exec|subprocess)(?![A-Za-z0-9_])",
        re.I,
    ),
    re.compile(r"(?<![A-Za-z0-9_])import(?:\s+[A-Za-z_]|\s*[\"'])", re.I),
    re.compile(r"(?<![A-Za-z0-9_])require\s*\(", re.I),
    re.compile(r"(?<![A-Za-z0-9_])/bin/", re.I),
)


class ScenePackError(ValueError):
    """Raised when a Scene pack is malformed or unsafe."""


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(child) for key, child in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return deepcopy(value)


@dataclass(frozen=True, slots=True)
class ScenePackEntry:
    """One labeled policy override for an existing JR-Bar Scene."""

    scene: Scene
    label: str
    policy: ScenePolicy


@dataclass(frozen=True, slots=True)
class ScenePack:
    """An immutable, validated Scene pack."""

    pack_id: str
    name: str
    version: int
    scenes: tuple[ScenePackEntry, ...]
    safety: Mapping[str, Any]
    accessibility: Mapping[str, Any]


def _text(value: object, field: str, limit: int = 160) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ScenePackError(f"{field} must be a non-empty bounded string")
    return value.strip()


def _identifier(value: object, field: str) -> str:
    result = _text(value, field, 80)
    if _IDENTIFIER.fullmatch(result) is None:
        raise ScenePackError(f"{field} must be a lowercase data identifier")
    return result


def _reject_executable_content(value: object, path: str = "pack") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if type(key) is not str or key.casefold() in _CODE_KEYS:
                raise ScenePackError(f"{path} contains executable content")
            _reject_executable_content(child, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_executable_content(child, f"{path}[{index}]")
        return
    if isinstance(value, str):
        parsed = urlparse(value)
        is_http_url = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
            and parsed.username is None
            and parsed.password is None
            and value.isprintable()
            and not any(character.isspace() for character in value)
        )
        if not is_http_url and any(marker.search(value) for marker in _CODE_MARKERS):
            raise ScenePackError(f"{path} contains executable content")


def _bounded_json(payload: Mapping[str, Any]) -> None:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ScenePackError("Scene pack must contain only finite JSON data") from error
    if len(encoded) > MAX_SCENE_PACK_BYTES:
        raise ScenePackError("Scene pack exceeds size limit")


def _migrated_scene(value: object) -> object:
    if not isinstance(value, Mapping):
        return deepcopy(value)
    result = deepcopy(dict(value))
    if "scene" not in result and "id" in result:
        result["scene"] = result.pop("id")
    return result


def migrate_scene_pack(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate the supported version-1 representation into version 2."""

    if not isinstance(payload, Mapping):
        raise ScenePackError("Scene pack must be an object")
    try:
        result = deepcopy(dict(payload))
    except (TypeError, ValueError) as error:
        raise ScenePackError("Scene pack must contain detached JSON data") from error
    version = result.get("version", result.get("schema_version", 1))
    if type(version) is not int:
        raise ScenePackError("Scene pack version must be an integer")
    if version == 1:
        policies = result.pop("policies", result.get("scenes", []))
        result.pop("schema_version", None)
        result["version"] = CURRENT_SCENE_PACK_VERSION
        result["scenes"] = [
            _migrated_scene(value)
            for value in policies
        ] if isinstance(policies, list) else policies
        result.setdefault("safety", {"data_only": True, "network": False})
        result.setdefault(
            "accessibility",
            {
                "reduced_motion": True,
                "high_contrast": True,
                "non_color_cues": True,
            },
        )
    return result


_EnumType = TypeVar("_EnumType")


def _enum_value(enum_type: type[_EnumType], value: object, field: str) -> _EnumType:
    if type(value) is not str:
        raise ScenePackError(f"{field} must be a known value")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ScenePackError(f"{field} must be a known value") from error


def _brightness(value: object, field: str) -> float:
    if type(value) not in {int, float}:
        raise ScenePackError(f"{field} must be a finite number from 0 through 1")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ScenePackError(f"{field} must be a finite number from 0 through 1")
    return result


def _entry(value: object, index: int) -> ScenePackEntry:
    if not isinstance(value, Mapping):
        raise ScenePackError(f"scenes[{index}] must be an object")
    unknown = set(value) - _SCENE_KEYS
    if unknown:
        raise ScenePackError(f"scenes[{index}] contains unsupported fields")
    scene = _enum_value(Scene, value.get("scene"), f"scenes[{index}].scene")
    label = _text(value.get("label"), f"scenes[{index}].label")
    policy = ScenePolicy(
        scene=scene,
        surface_role=_enum_value(
            SurfaceRole,
            value.get("surface_role"),
            f"scenes[{index}].surface_role",
        ),
        brightness=_brightness(
            value.get("brightness"),
            f"scenes[{index}].brightness",
        ),
        motion=_enum_value(
            MotionLevel,
            value.get("motion"),
            f"scenes[{index}].motion",
        ),
        notifications=_enum_value(
            NotificationMode,
            value.get("notifications"),
            f"scenes[{index}].notifications",
        ),
        display_admission=_enum_value(
            DisplayAdmission,
            value.get("display_admission"),
            f"scenes[{index}].display_admission",
        ),
        device_selection=_enum_value(
            DeviceSelection,
            value.get("device_selection"),
            f"scenes[{index}].device_selection",
        ),
    )
    return ScenePackEntry(scene=scene, label=label, policy=policy)


def _entry_mapping(entry: ScenePackEntry) -> dict[str, object]:
    policy = entry.policy
    return {
        "brightness": policy.brightness,
        "device_selection": policy.device_selection.value,
        "display_admission": policy.display_admission.value,
        "label": entry.label,
        "motion": policy.motion.value,
        "notifications": policy.notifications.value,
        "scene": entry.scene.value,
        "surface_role": policy.surface_role.value,
    }


def _pack_mapping(pack: ScenePack) -> dict[str, Any]:
    return {
        "accessibility": _thaw_json(pack.accessibility),
        "id": pack.pack_id,
        "name": pack.name,
        "safety": _thaw_json(pack.safety),
        "scenes": [_entry_mapping(entry) for entry in pack.scenes],
        "version": pack.version,
    }


def validate_scene_pack(payload: ScenePack | Mapping[str, Any]) -> ScenePack:
    """Validate one pack without filesystem, network, or runtime side effects."""

    source = _pack_mapping(payload) if isinstance(payload, ScenePack) else payload
    migrated = migrate_scene_pack(source)
    _bounded_json(migrated)
    _reject_executable_content(migrated)
    unknown = set(migrated) - _TOP_LEVEL_KEYS
    if unknown:
        raise ScenePackError("Scene pack contains unsupported fields")
    if migrated.get("version") != CURRENT_SCENE_PACK_VERSION:
        raise ScenePackError("unsupported Scene pack version")
    pack_id = _identifier(migrated.get("id"), "id")
    name = _text(migrated.get("name"), "name")
    scenes = migrated.get("scenes")
    if (
        type(scenes) is not list
        or not scenes
        or len(scenes) > MAX_SCENES_PER_PACK
    ):
        raise ScenePackError("scenes must be a non-empty bounded list")
    entries = tuple(_entry(value, index) for index, value in enumerate(scenes))
    identities = tuple(entry.scene for entry in entries)
    if len(set(identities)) != len(identities):
        raise ScenePackError("duplicate scene override")
    safety = migrated.get("safety")
    if (
        not isinstance(safety, Mapping)
        or set(safety) != {"data_only", "network"}
        or safety.get("data_only") is not True
        or safety.get("network") is not False
    ):
        raise ScenePackError("safety must declare data_only and no network")
    accessibility = migrated.get("accessibility")
    required_accessibility = {
        "reduced_motion",
        "high_contrast",
        "non_color_cues",
    }
    if (
        not isinstance(accessibility, Mapping)
        or set(accessibility) != required_accessibility
        or any(accessibility.get(key) is not True for key in required_accessibility)
    ):
        raise ScenePackError("Scene pack must declare accessibility support")
    return ScenePack(
        pack_id=pack_id,
        name=name,
        version=CURRENT_SCENE_PACK_VERSION,
        scenes=entries,
        safety=_freeze_json(safety),
        accessibility=_freeze_json(accessibility),
    )


def export_scene_pack(payload: ScenePack | Mapping[str, Any]) -> bytes:
    """Return canonical JSON bytes for a validated Scene pack."""

    pack = validate_scene_pack(payload)
    encoded = json.dumps(
        _pack_mapping(pack),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_SCENE_PACK_BYTES:
        raise ScenePackError("Scene pack exceeds size limit")
    return encoded


__all__ = [
    "CURRENT_SCENE_PACK_VERSION",
    "MAX_SCENES_PER_PACK",
    "MAX_SCENE_PACK_BYTES",
    "ScenePack",
    "ScenePackEntry",
    "ScenePackError",
    "export_scene_pack",
    "migrate_scene_pack",
    "validate_scene_pack",
]
