from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from sidepulse.effect_history import (
    EffectEvent,
    EffectHistory,
    EffectOutcome,
    EffectSemanticCategory,
    EffectSurface,
)
from sidepulse.effect_history_store import save_effect_history
from sidepulse.effect_pack_store import (
    EFFECT_PACK_STORE_DIRECTORY,
    EffectPackStore,
    EffectPackStoreError,
    PackMutationStatus,
    default_effect_pack_store_path,
)
from sidepulse.effect_packs import export_pack


def _pack(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "calm-pack",
        "name": "Calm Pack",
        "version": 2,
        "effects": [
            {
                "id": "soft-pulse",
                "label": "Soft Pulse",
                "description": "A quiet work pulse.",
                "meaning": "quiet working",
                "surfaces": ["screen_bar", "settings_preview"],
            }
        ],
        "safety": {"data_only": True, "network": False},
        "accessibility": {"reduced_motion": True, "high_contrast": True},
    }
    payload.update(overrides)
    return payload


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_default_store_uses_the_private_jr_bar_state_directory(
    tmp_path: Path,
) -> None:
    assert default_effect_pack_store_path(tmp_path) == (
        tmp_path
        / ".local"
        / "state"
        / "sidepulse"
        / "agent-monitor"
        / EFFECT_PACK_STORE_DIRECTORY
    )


def test_install_persists_canonical_private_json_and_round_trips(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incoming.json"
    source.write_text(json.dumps(_pack(), indent=2), encoding="utf-8")
    store = EffectPackStore(tmp_path / "store")

    receipt = store.install(source)
    rows = store.list()
    installed = store.inspect("calm-pack")

    assert receipt.status is PackMutationStatus.INSTALLED
    assert receipt.accepted is True
    assert tuple(row.pack_id for row in rows) == ("calm-pack",)
    assert installed.pack_id == "calm-pack"
    target = tmp_path / "store" / "calm-pack.json"
    assert target.read_bytes() == store.canonical_export("calm-pack")
    assert b"\n" not in target.read_bytes()
    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o600


def test_install_collision_and_explicit_update_have_refusal_receipts(
    tmp_path: Path,
) -> None:
    store = EffectPackStore(tmp_path / "store")
    installed = store.install(_pack())
    collision = store.install(_pack(name="Changed"))
    missing_update = store.update(_pack(id="other-pack"))
    updated = store.update(_pack(name="Changed"))
    unchanged = store.update(_pack(name="Changed"))

    assert installed.status is PackMutationStatus.INSTALLED
    assert collision.status is PackMutationStatus.REFUSED
    assert collision.reason == "already_installed"
    assert missing_update.status is PackMutationStatus.REFUSED
    assert missing_update.reason == "not_installed"
    assert updated.status is PackMutationStatus.UPDATED
    assert store.inspect("calm-pack").name == "Changed"
    assert unchanged.status is PackMutationStatus.REFUSED
    assert unchanged.reason == "already_current"


def test_remove_is_explicit_and_idempotently_refuses_missing_pack(
    tmp_path: Path,
) -> None:
    store = EffectPackStore(tmp_path / "store")
    store.install(_pack())

    removed = store.remove("calm-pack")
    missing = store.remove("calm-pack")

    assert removed.status is PackMutationStatus.REMOVED
    assert removed.accepted is True
    assert missing.status is PackMutationStatus.REFUSED
    assert missing.reason == "not_installed"
    assert store.list() == ()


def test_duplicate_preserves_pack_metadata_under_a_new_identity(
    tmp_path: Path,
) -> None:
    store = EffectPackStore(tmp_path / "store")
    store.install(
        _pack(
            license={
                "spdx_id": "CC-BY-4.0",
                "label": "Creative Commons Attribution 4.0",
                "source_url": "https://example.com/source",
            }
        )
    )

    receipt = store.duplicate("calm-pack", "calm-pack-copy", "Calm Pack Copy")
    original = store.inspect("calm-pack")
    duplicate = store.inspect("calm-pack-copy")

    assert receipt.status is PackMutationStatus.DUPLICATED
    assert duplicate.pack_id == "calm-pack-copy"
    assert duplicate.name == "Calm Pack Copy"
    assert duplicate.effects == original.effects
    assert duplicate.safety == original.safety
    assert duplicate.accessibility == original.accessibility
    assert duplicate.license == original.license
    assert _mode(tmp_path / "store" / "calm-pack-copy.json") == 0o600


def test_duplicate_refuses_collisions_without_mutating_either_pack(
    tmp_path: Path,
) -> None:
    store = EffectPackStore(tmp_path / "store")
    store.install(_pack())
    store.install(_pack(id="other-pack", name="Other Pack"))
    before = store.canonical_export("other-pack")

    receipt = store.duplicate("calm-pack", "other-pack", "Replacement")

    assert receipt.status is PackMutationStatus.REFUSED
    assert receipt.reason == "already_installed"
    assert store.canonical_export("other-pack") == before
    assert tuple(pack.pack_id for pack in store.list()) == (
        "calm-pack",
        "other-pack",
    )


def test_rename_refuses_collisions_without_mutating_either_pack(
    tmp_path: Path,
) -> None:
    store = EffectPackStore(tmp_path / "store")
    store.install(_pack())
    store.install(_pack(id="other-pack", name="Other Pack"))
    original = store.canonical_export("calm-pack")
    other = store.canonical_export("other-pack")

    receipt = store.rename("calm-pack", "other-pack", "Replacement")

    assert receipt.status is PackMutationStatus.REFUSED
    assert receipt.reason == "already_installed"
    assert store.canonical_export("calm-pack") == original
    assert store.canonical_export("other-pack") == other


def test_rename_replaces_the_identity_and_preserves_metadata(tmp_path: Path) -> None:
    store = EffectPackStore(tmp_path / "store")
    store.install(_pack())
    original = store.inspect("calm-pack")

    receipt = store.rename("calm-pack", "focused-pack", "Focused Pack")
    renamed = store.inspect("focused-pack")

    assert receipt.status is PackMutationStatus.RENAMED
    assert tuple(pack.pack_id for pack in store.list()) == ("focused-pack",)
    assert not (tmp_path / "store" / "calm-pack.json").exists()
    assert renamed.name == "Focused Pack"
    assert renamed.effects == original.effects
    assert renamed.safety == original.safety
    assert renamed.accessibility == original.accessibility
    assert renamed.license == original.license


def test_rename_rolls_back_the_new_pack_when_source_removal_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sidepulse.effect_pack_store as pack_store

    store = EffectPackStore(tmp_path / "store")
    store.install(_pack())
    original = store.canonical_export("calm-pack")

    def fail_remove(*_args: object, **_kwargs: object) -> bool:
        raise OSError("injected removal failure")

    monkeypatch.setattr(pack_store, "unlink_private_file_if_unchanged", fail_remove)

    with pytest.raises(EffectPackStoreError, match="rename failed"):
        store.rename("calm-pack", "focused-pack", "Focused Pack")

    assert store.canonical_export("calm-pack") == original
    assert not (tmp_path / "store" / "focused-pack.json").exists()


@pytest.mark.parametrize(
    ("operation", "new_pack_id", "new_name"),
    (
        ("duplicate", "../outside", "Valid Name"),
        ("duplicate", "valid-id", "   "),
        ("rename", "../outside", "Valid Name"),
        ("rename", "valid-id", "   "),
    ),
)
def test_pack_management_refuses_invalid_new_identity(
    tmp_path: Path,
    operation: str,
    new_pack_id: str,
    new_name: str,
) -> None:
    store = EffectPackStore(tmp_path / "store")
    store.install(_pack())

    with pytest.raises(EffectPackStoreError, match=r"identifier|name"):
        getattr(store, operation)("calm-pack", new_pack_id, new_name)

    assert tuple(pack.pack_id for pack in store.list()) == ("calm-pack",)


@pytest.mark.parametrize("pack_id", ("../outside", "a/b", "/absolute", ".."))
def test_pack_identifiers_cannot_traverse_the_store(
    tmp_path: Path,
    pack_id: str,
) -> None:
    store = EffectPackStore(tmp_path / "store")

    with pytest.raises(EffectPackStoreError, match="identifier"):
        store.inspect(pack_id)
    with pytest.raises(EffectPackStoreError, match="identifier"):
        store.remove(pack_id)


@pytest.mark.parametrize("operation", ("inspect", "list", "update", "remove"))
def test_store_refuses_symlinked_pack_leaf_without_touching_target(
    tmp_path: Path,
    operation: str,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("outside remains unchanged", encoding="utf-8")
    root = tmp_path / "store"
    root.mkdir()
    (root / "calm-pack.json").symlink_to(outside)
    store = EffectPackStore(root)

    with pytest.raises(EffectPackStoreError):
        if operation == "inspect":
            store.inspect("calm-pack")
        elif operation == "list":
            store.list()
        elif operation == "update":
            store.update(_pack())
        else:
            store.remove("calm-pack")

    assert outside.read_text(encoding="utf-8") == "outside remains unchanged"


def test_legacy_pack_is_migrated_and_license_survives_gallery_projection(
    tmp_path: Path,
) -> None:
    store = EffectPackStore(tmp_path / "store")
    legacy = {
        "id": "legacy-pack",
        "name": "Legacy Pack",
        "version": 1,
        "effects": [{"id": "steady", "label": "Steady"}],
        "license": {
            "spdx_id": "CC-BY-4.0",
            "label": "Creative Commons Attribution 4.0",
            "source_url": "https://example.com/source",
            "attribution_url": "https://example.com/credit",
        },
    }

    store.install(legacy)
    projection = store.gallery_index()[0]
    document = json.loads(store.canonical_export("legacy-pack"))

    assert document["version"] == 2
    assert projection.pack_id == "legacy-pack"
    assert projection.license_spdx_id == "CC-BY-4.0"
    assert projection.source_url == "https://example.com/source"
    assert projection.attribution_url == "https://example.com/credit"


def test_builtin_gallery_uses_existing_studio_projection(tmp_path: Path) -> None:
    rows = EffectPackStore(tmp_path / "store").built_in_gallery(query="pulse")

    assert "pulse" in tuple(row.effect_id for row in rows)
    assert next(row for row in rows if row.effect_id == "pulse").label == "Pulse"


def test_history_projection_is_content_free_and_preserves_restore_health(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "effect-history.json"
    save_effect_history(
        history_path,
        EffectHistory(
            (
                EffectEvent(
                    event_id="effect-event:one",
                    occurred_at_epoch=1_800_000_000.0,
                    effect_id="pulse",
                    semantic_category=EffectSemanticCategory.AMBIENT,
                    surface=EffectSurface.SCREEN_BAR,
                    outcome=EffectOutcome.SHOWN,
                ),
            )
        ),
    )

    projection = EffectPackStore(tmp_path / "store").effect_history(history_path)

    assert projection.health.value == "healthy"
    assert projection.rows[0].effect_id == "pulse"
    serialized = repr(projection).casefold()
    for forbidden in (
        "prompt",
        "transcript",
        "session",
        "person@example.com",
        "/users/private",
        "https://private.example",
    ):
        assert forbidden not in serialized


def test_store_refuses_more_than_the_bounded_pack_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sidepulse.effect_pack_store as pack_store

    monkeypatch.setattr(pack_store, "MAX_STORED_EFFECT_PACKS", 1)
    store = EffectPackStore(tmp_path / "store")
    assert store.install(_pack()).accepted is True

    receipt = store.install(_pack(id="other-pack", name="Other Pack"))

    assert receipt.status is PackMutationStatus.REFUSED
    assert receipt.reason == "pack_count_limit"
    assert store.list()[0].pack_id == "calm-pack"


def test_store_refuses_a_write_that_would_exceed_total_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sidepulse.effect_pack_store as pack_store

    first = _pack()
    monkeypatch.setattr(
        pack_store,
        "MAX_EFFECT_PACK_STORE_BYTES",
        len(export_pack(first)),
    )
    store = EffectPackStore(tmp_path / "store")
    assert store.install(first).accepted is True

    receipt = store.install(_pack(id="other-pack", name="Other Pack"))

    assert receipt.status is PackMutationStatus.REFUSED
    assert receipt.reason == "total_size_limit"


@pytest.mark.parametrize(
    "changes",
    (
        {"effects": [{"id": "unsafe", "label": "Unsafe", "script": "run()"}]},
        {"safety": {"data_only": True, "network": True}},
    ),
)
def test_store_never_accepts_executable_or_network_enabled_plugins(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    with pytest.raises(EffectPackStoreError, match="source is invalid"):
        EffectPackStore(tmp_path / "store").install(_pack(**changes))


def test_import_refuses_hard_linked_source(tmp_path: Path) -> None:
    original = tmp_path / "original.json"
    source = tmp_path / "source.json"
    original.write_text(json.dumps(_pack()), encoding="utf-8")
    os.link(original, source)

    with pytest.raises(EffectPackStoreError):
        EffectPackStore(tmp_path / "store").install(source)
