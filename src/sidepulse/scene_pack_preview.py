"""Pure import planning and accessibility previews for Scene packs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from .scene_packs import ScenePack, export_scene_pack, validate_scene_pack
from .scenes import Scene, ScenePolicy


@dataclass(frozen=True, slots=True)
class ScenePackPreviewRow:
    """Side-by-side normal and Reduce Motion policies for one Scene."""

    scene: Scene
    label: str
    normal: ScenePolicy
    reduced_motion: ScenePolicy


@dataclass(frozen=True, slots=True)
class ScenePackImportPlan:
    """Validated bytes and preview rows required before a store mutation."""

    pack: ScenePack
    payload: bytes
    rows: tuple[ScenePackPreviewRow, ...]
    migrated: bool


def plan_scene_pack_import(
    source: ScenePack | Mapping[str, Any],
) -> ScenePackImportPlan:
    """Validate, migrate, and preview a Scene pack without side effects."""

    if isinstance(source, ScenePack):
        source_version = source.version
    else:
        source_version = source.get("version", source.get("schema_version", 1))
    pack = validate_scene_pack(source)
    rows = tuple(
        ScenePackPreviewRow(
            scene=entry.scene,
            label=entry.label,
            normal=entry.policy,
            reduced_motion=replace(entry.policy, reduce_motion=True),
        )
        for entry in pack.scenes
    )
    return ScenePackImportPlan(
        pack=pack,
        payload=export_scene_pack(pack),
        rows=rows,
        migrated=source_version != pack.version,
    )


__all__ = [
    "ScenePackImportPlan",
    "ScenePackPreviewRow",
    "plan_scene_pack_import",
]
