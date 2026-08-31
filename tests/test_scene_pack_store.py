from __future__ import annotations

import json
import stat
from pathlib import Path

from sidepulse.effect_pack_store import PackMutationStatus
from sidepulse.scene_pack_store import ScenePackStore


def _pack(*, name: str = "Quiet Work", version: int = 2) -> dict[str, object]:
    return {
        "id": "quiet-work",
        "name": name,
        "version": version,
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
            }
        ],
        "safety": {"data_only": True, "network": False},
        "accessibility": {
            "reduced_motion": True,
            "high_contrast": True,
            "non_color_cues": True,
        },
    }


def test_scene_pack_store_round_trip_is_private_atomic_and_bounded(
    tmp_path: Path,
) -> None:
    store = ScenePackStore(tmp_path / "scene-packs")

    installed = store.install(_pack())
    target = store.root / "quiet-work.json"

    assert installed.status is PackMutationStatus.INSTALLED
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert store.list()[0].pack_id == "quiet-work"
    assert store.inspect("quiet-work").name == "Quiet Work"
    assert store.preview("quiet-work").rows[0].normal.brightness == 0.35

    updated = store.update(_pack(name="Quiet Work 2"))
    assert updated.status is PackMutationStatus.UPDATED
    assert store.inspect("quiet-work").name == "Quiet Work 2"

    exported = tmp_path / "exports" / "quiet-work.json"
    exported.parent.mkdir()
    store.export("quiet-work", exported)
    assert exported.read_bytes() == store.canonical_export("quiet-work")

    removed = store.remove("quiet-work")
    assert removed.status is PackMutationStatus.REMOVED
    assert store.list() == ()


def test_scene_pack_store_previews_before_import_and_leaves_no_store_on_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unsafe.json"
    source.write_text(
        json.dumps({**_pack(), "entrypoint": "python -m private.module"}),
        encoding="utf-8",
    )
    store = ScenePackStore(tmp_path / "scene-packs")

    try:
        store.install(source)
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe Scene pack was accepted")

    assert not store.root.exists()
