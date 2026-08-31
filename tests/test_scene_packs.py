from __future__ import annotations

import json

import pytest

from sidepulse.scene_pack_preview import plan_scene_pack_import
from sidepulse.scene_packs import (
    CURRENT_SCENE_PACK_VERSION,
    ScenePackError,
    export_scene_pack,
    validate_scene_pack,
)
from sidepulse.scenes import MotionLevel, Scene


def _pack(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "quiet-work",
        "name": "Quiet Work",
        "version": 2,
        "scenes": [
            {
                "scene": "calm",
                "label": "Quiet Calm",
                "surface_role": "ambient",
                "brightness": 0.35,
                "motion": "reduced",
                "notifications": "important",
                "display_admission": "asks",
                "device_selection": "active",
            },
            {
                "scene": "demo",
                "label": "Quiet Demo",
                "surface_role": "preview",
                "brightness": 0.75,
                "motion": "full",
                "notifications": "all",
                "display_admission": "all",
                "device_selection": "all",
            },
        ],
        "safety": {"data_only": True, "network": False},
        "accessibility": {
            "reduced_motion": True,
            "high_contrast": True,
            "non_color_cues": True,
        },
    }
    payload.update(overrides)
    return payload


def test_v1_scene_pack_migrates_to_complete_existing_scene_policies() -> None:
    legacy = _pack(
        version=None,
        schema_version=1,
        scenes=None,
        policies=[
            {
                "id": "night",
                "label": "Legacy Night",
                "surface_role": "ambient",
                "brightness": 0.15,
                "motion": "static",
                "notifications": "none",
                "display_admission": "none",
                "device_selection": "active",
            }
        ],
        safety=None,
        accessibility=None,
    )
    legacy = {key: value for key, value in legacy.items() if value is not None}

    pack = validate_scene_pack(legacy)

    assert pack.version == CURRENT_SCENE_PACK_VERSION
    assert pack.scenes[0].scene is Scene.NIGHT
    assert pack.scenes[0].policy.motion is MotionLevel.STATIC
    assert json.loads(export_scene_pack(pack).decode("utf-8"))["version"] == 2


def test_import_plan_previews_normal_and_reduce_motion_without_mutation() -> None:
    source = _pack()

    plan = plan_scene_pack_import(source)
    source["name"] = "Changed after validation"
    source["scenes"][0]["brightness"] = 1.0  # type: ignore[index]

    demo = next(row for row in plan.rows if row.scene is Scene.DEMO)
    calm = next(row for row in plan.rows if row.scene is Scene.CALM)
    assert plan.pack.name == "Quiet Work"
    assert plan.migrated is False
    assert calm.normal.brightness == 0.35
    assert demo.normal.effective_motion is MotionLevel.FULL
    assert demo.reduced_motion.effective_motion is MotionLevel.STATIC


@pytest.mark.parametrize(
    "override",
    [
        {"safety": {"data_only": True, "network": True}},
        {
            "accessibility": {
                "reduced_motion": False,
                "high_contrast": True,
                "non_color_cues": True,
            }
        },
        {"command": "python -m private.module"},
        {
            "scenes": [
                {
                    "scene": "calm",
                    "label": "Unsafe",
                    "surface_role": "ambient",
                    "brightness": 0.4,
                    "motion": "reduced",
                    "notifications": "important",
                    "display_admission": "asks",
                    "device_selection": "active",
                    "script": "sh /tmp/private",
                }
            ]
        },
        {
            "scenes": [
                {
                    "scene": "calm",
                    "label": "Too Bright",
                    "surface_role": "ambient",
                    "brightness": 1.1,
                    "motion": "reduced",
                    "notifications": "important",
                    "display_admission": "asks",
                    "device_selection": "active",
                }
            ]
        },
    ],
)
def test_scene_pack_rejects_unsafe_inaccessible_or_invalid_data(
    override: dict[str, object],
) -> None:
    with pytest.raises(ScenePackError):
        validate_scene_pack(_pack(**override))


def test_scene_pack_rejects_duplicate_scene_overrides() -> None:
    scene = _pack()["scenes"][0]  # type: ignore[index]

    with pytest.raises(ScenePackError, match="duplicate scene"):
        validate_scene_pack(_pack(scenes=[scene, dict(scene)]))  # type: ignore[arg-type]
