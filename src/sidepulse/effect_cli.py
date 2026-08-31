"""Callable command handlers for local data-only effect packs."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

from .effect_pack_store import (
    EffectHistoryProjection,
    EffectPackStore,
    EffectPackStoreError,
    PackMutationReceipt,
)
from .effect_packs import EffectPack, export_pack
from .scene_pack_preview import ScenePackImportPlan
from .scene_pack_store import ScenePackStore, ScenePackStoreError
from .scene_packs import ScenePack, export_scene_pack


def _outputs(
    stdout: TextIO | None,
    stderr: TextIO | None,
) -> tuple[TextIO, TextIO]:
    return (
        sys.stdout if stdout is None else stdout,
        sys.stderr if stderr is None else stderr,
    )


def _store(args: object, store: EffectPackStore | None) -> EffectPackStore:
    if store is not None:
        return store
    explicit = getattr(args, "store_dir", None)
    if explicit is not None:
        return EffectPackStore(Path(explicit))
    home = getattr(args, "home", None)
    if home is not None:
        from .effect_pack_store import default_effect_pack_store_path

        return EffectPackStore(default_effect_pack_store_path(Path(home)))
    return EffectPackStore()


def _scene_store(
    args: object,
    store: ScenePackStore | None,
) -> ScenePackStore:
    if store is not None:
        return store
    explicit = getattr(args, "store_dir", None)
    if explicit is not None:
        return ScenePackStore(Path(explicit))
    home = getattr(args, "home", None)
    if home is not None:
        from .scene_pack_store import default_scene_pack_store_path

        return ScenePackStore(default_scene_pack_store_path(Path(home)))
    return ScenePackStore()


def _write_json(output: TextIO, document: object) -> None:
    json.dump(
        document,
        output,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    output.write("\n")


def _license_document(pack: EffectPack) -> dict[str, object] | None:
    license_metadata = pack.license
    if license_metadata is None:
        return None
    return {
        "spdx_id": license_metadata.spdx_id,
        "label": license_metadata.label,
        "source_url": license_metadata.source_url,
        "attribution_url": license_metadata.attribution_url,
    }


def _pack_summary(pack: EffectPack) -> dict[str, object]:
    return {
        "pack_id": pack.pack_id,
        "name": pack.name,
        "version": pack.version,
        "effect_count": len(pack.effects),
        "license": _license_document(pack),
    }


def _receipt_document(receipt: PackMutationReceipt) -> dict[str, object]:
    return {
        "accepted": receipt.accepted,
        "digest": receipt.digest,
        "pack_id": receipt.pack_id,
        "previous_digest": receipt.previous_digest,
        "reason": receipt.reason,
        "status": receipt.status.value,
    }


def _write_receipt(
    receipt: PackMutationReceipt,
    *,
    output: TextIO,
    as_json: bool,
) -> int:
    if as_json:
        _write_json(output, _receipt_document(receipt))
    elif receipt.reason is not None:
        output.write(f"{receipt.pack_id}: {receipt.status.value} ({receipt.reason})\n")
    else:
        output.write(f"{receipt.pack_id}: {receipt.status.value}\n")
    return 0 if receipt.accepted else 2


def _guard(
    operation: str,
    callback: Callable[[], int],
    *,
    stderr: TextIO,
    subject: str = "effect packs",
) -> int:
    try:
        return callback()
    except (
        EffectPackStoreError,
        ScenePackStoreError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        stderr.write(f"{subject}: {operation} failed\n")
        return 1


def cmd_effect_pack_install(
    args: object,
    *,
    store: EffectPackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)
    operation = "update" if bool(getattr(args, "update", False)) else "install"

    def command() -> int:
        source = getattr(args, "source")
        selected = _store(args, store)
        receipt = (
            selected.update(source)
            if operation == "update"
            else selected.install(source)
        )
        return _write_receipt(
            receipt,
            output=output,
            as_json=bool(getattr(args, "json", False)),
        )

    return _guard(operation, command, stderr=errors)


def cmd_effect_pack_update(
    args: object,
    *,
    store: EffectPackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        receipt = _store(args, store).update(getattr(args, "source"))
        return _write_receipt(
            receipt,
            output=output,
            as_json=bool(getattr(args, "json", False)),
        )

    return _guard("update", command, stderr=errors)


def cmd_effect_pack_remove(
    args: object,
    *,
    store: EffectPackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        receipt = _store(args, store).remove(getattr(args, "pack_id"))
        return _write_receipt(
            receipt,
            output=output,
            as_json=bool(getattr(args, "json", False)),
        )

    return _guard("remove", command, stderr=errors)


def cmd_effect_pack_duplicate(
    args: object,
    *,
    store: EffectPackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        receipt = _store(args, store).duplicate(
            getattr(args, "pack_id"),
            getattr(args, "new_pack_id"),
            getattr(args, "new_name"),
        )
        return _write_receipt(
            receipt,
            output=output,
            as_json=bool(getattr(args, "json", False)),
        )

    return _guard("duplicate", command, stderr=errors)


def cmd_effect_pack_rename(
    args: object,
    *,
    store: EffectPackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        receipt = _store(args, store).rename(
            getattr(args, "pack_id"),
            getattr(args, "new_pack_id"),
            getattr(args, "new_name"),
        )
        return _write_receipt(
            receipt,
            output=output,
            as_json=bool(getattr(args, "json", False)),
        )

    return _guard("rename", command, stderr=errors)


def cmd_effect_pack_list(
    args: object,
    *,
    store: EffectPackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        packs = _store(args, store).list()
        if bool(getattr(args, "json", False)):
            _write_json(output, {"packs": [_pack_summary(pack) for pack in packs]})
        elif not packs:
            output.write("No effect packs installed.\n")
        else:
            for pack in packs:
                output.write(
                    f"{pack.pack_id}: {pack.name} "
                    f"(v{pack.version}, {len(pack.effects)} effects)\n"
                )
        return 0

    return _guard("list", command, stderr=errors)


def cmd_effect_pack_inspect(
    args: object,
    *,
    store: EffectPackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        pack = _store(args, store).inspect(getattr(args, "pack_id"))
        document = json.loads(export_pack(pack).decode("utf-8"))
        if bool(getattr(args, "json", False)):
            _write_json(output, document)
        else:
            output.write(f"{pack.pack_id}: {pack.name}\n")
            output.write(f"version: {pack.version}\n")
            output.write(f"effects: {len(pack.effects)}\n")
            if pack.license is not None:
                output.write(
                    f"license: {pack.license.spdx_id} ({pack.license.label})\n"
                )
            for effect in pack.effects:
                output.write(f"  {effect['id']}: {effect['label']}\n")
        return 0

    return _guard("inspect", command, stderr=errors)


def cmd_effect_pack_export(
    args: object,
    *,
    store: EffectPackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        selected = _store(args, store)
        pack_id = getattr(args, "pack_id")
        target = getattr(args, "target", None)
        if target is None:
            output.write(selected.canonical_export(pack_id).decode("utf-8"))
            output.write("\n")
            return 0
        written = selected.export(pack_id, Path(target))
        if bool(getattr(args, "json", False)):
            _write_json(
                output,
                {
                    "pack_id": pack_id,
                    "status": "exported",
                    "target": str(written),
                },
            )
        else:
            output.write(f"{pack_id}: exported to {written}\n")
        return 0

    return _guard("export", command, stderr=errors)


def _gallery_pack_document(projection: object) -> dict[str, object]:
    license_document = None
    spdx_id = getattr(projection, "license_spdx_id")
    label = getattr(projection, "license_label")
    if spdx_id is not None and label is not None:
        license_document = {
            "spdx_id": spdx_id,
            "label": label,
            "source_url": getattr(projection, "source_url"),
            "attribution_url": getattr(projection, "attribution_url"),
        }
    return {
        "pack_id": getattr(projection, "pack_id"),
        "name": getattr(projection, "name"),
        "effect_count": getattr(projection, "effect_count"),
        "effect_ids": list(getattr(projection, "effect_ids")),
        "license": license_document,
    }


def _gallery_effect_document(row: object) -> dict[str, object]:
    return {
        "catalog": getattr(row, "catalog"),
        "effect_id": getattr(row, "effect_id"),
        "energy": getattr(row, "energy"),
        "label": getattr(row, "label"),
        "parameters": list(getattr(row, "parameters")),
        "purpose": getattr(row, "purpose"),
        "reduce_motion_effect_id": getattr(row, "reduce_motion_effect_id"),
        "safety": getattr(row, "safety"),
        "semantic_family": getattr(row, "semantic_family").value,
        "supported_surfaces": [
            surface.value for surface in getattr(row, "supported_surfaces")
        ],
        "when_it_runs": getattr(row, "when_it_runs"),
    }


def cmd_effect_gallery(
    args: object,
    *,
    store: EffectPackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        selected = _store(args, store)
        builtin = bool(getattr(args, "builtin", False))
        if builtin:
            rows = selected.built_in_gallery(query=getattr(args, "query", ""))
            documents = [_gallery_effect_document(row) for row in rows]
            if bool(getattr(args, "json", False)):
                _write_json(output, {"effects": documents})
            elif not rows:
                output.write("No built-in effects matched.\n")
            else:
                for row in rows:
                    output.write(
                        f"{row.effect_id}: {row.label} "
                        f"({row.semantic_family.value}, {row.energy})\n"
                    )
            return 0
        projections = selected.gallery_index()
        documents = [_gallery_pack_document(row) for row in projections]
        if bool(getattr(args, "json", False)):
            _write_json(output, {"packs": documents})
        elif not projections:
            output.write("No effect packs installed.\n")
        else:
            for row in projections:
                license_label = (
                    f", {row.license_spdx_id}"
                    if row.license_spdx_id is not None
                    else ""
                )
                output.write(
                    f"{row.pack_id}: {row.name} "
                    f"({row.effect_count} effects{license_label})\n"
                )
        return 0

    return _guard("gallery", command, stderr=errors)


def _history_document(projection: EffectHistoryProjection) -> dict[str, object]:
    return {
        "health": projection.health.value,
        "events": [
            {
                "effect_id": row.effect_id,
                "event_id": row.event_id,
                "explanation": row.explanation,
                "occurred_at_epoch": row.occurred_at_epoch,
                "outcome": row.outcome.value,
                "semantic_category": row.semantic_category.value,
                "surface": row.surface.value,
                "unseen": row.unseen,
            }
            for row in projection.rows
        ],
    }


def cmd_effect_history(
    args: object,
    *,
    store: EffectPackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)
    if (
        getattr(args, "store_dir", None) is not None
        and getattr(args, "history_path", None) is None
    ):
        errors.write(
            "effect packs: --history-path is required with --store-dir\n"
        )
        return 2

    def command() -> int:
        selected = _store(args, store)
        path = getattr(args, "history_path", None)
        projection = selected.effect_history(
            None if path is None else Path(path)
        )
        document = _history_document(projection)
        if bool(getattr(args, "json", False)):
            _write_json(output, document)
        else:
            output.write(f"history: {projection.health.value}\n")
            if not projection.rows:
                output.write("No effect history.\n")
            else:
                for row in projection.rows:
                    marker = "new" if row.unseen else "seen"
                    output.write(
                        f"{row.occurred_at_epoch:.3f} {row.effect_id}: "
                        f"{row.explanation} ({marker})\n"
                    )
        return 0

    return _guard("history", command, stderr=errors)


def _scene_pack_summary(pack: ScenePack) -> dict[str, object]:
    return {
        "pack_id": pack.pack_id,
        "name": pack.name,
        "version": pack.version,
        "scene_count": len(pack.scenes),
        "scenes": [entry.scene.value for entry in pack.scenes],
    }


def _scene_policy_document(policy: object) -> dict[str, object]:
    return {
        "brightness": getattr(policy, "brightness"),
        "device_selection": getattr(policy, "device_selection").value,
        "display_admission": getattr(policy, "display_admission").value,
        "effective_motion": getattr(policy, "effective_motion").value,
        "motion": getattr(policy, "motion").value,
        "notifications": getattr(policy, "notifications").value,
        "surface_role": getattr(policy, "surface_role").value,
    }


def _scene_preview_document(plan: ScenePackImportPlan) -> dict[str, object]:
    return {
        "pack_id": plan.pack.pack_id,
        "name": plan.pack.name,
        "version": plan.pack.version,
        "migrated": plan.migrated,
        "scenes": [
            {
                "scene": row.scene.value,
                "label": row.label,
                "normal": _scene_policy_document(row.normal),
                "reduced_motion": _scene_policy_document(row.reduced_motion),
            }
            for row in plan.rows
        ],
    }


def cmd_scene_pack_install(
    args: object,
    *,
    store: ScenePackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        receipt = _scene_store(args, store).install(getattr(args, "source"))
        return _write_receipt(
            receipt,
            output=output,
            as_json=bool(getattr(args, "json", False)),
        )

    return _guard("install", command, stderr=errors, subject="Scene packs")


def cmd_scene_pack_update(
    args: object,
    *,
    store: ScenePackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        receipt = _scene_store(args, store).update(getattr(args, "source"))
        return _write_receipt(
            receipt,
            output=output,
            as_json=bool(getattr(args, "json", False)),
        )

    return _guard("update", command, stderr=errors, subject="Scene packs")


def cmd_scene_pack_remove(
    args: object,
    *,
    store: ScenePackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        receipt = _scene_store(args, store).remove(getattr(args, "pack_id"))
        return _write_receipt(
            receipt,
            output=output,
            as_json=bool(getattr(args, "json", False)),
        )

    return _guard("remove", command, stderr=errors, subject="Scene packs")


def cmd_scene_pack_list(
    args: object,
    *,
    store: ScenePackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        packs = _scene_store(args, store).list()
        if bool(getattr(args, "json", False)):
            _write_json(output, {"packs": [_scene_pack_summary(pack) for pack in packs]})
        elif not packs:
            output.write("No Scene packs installed.\n")
        else:
            for pack in packs:
                output.write(
                    f"{pack.pack_id}: {pack.name} "
                    f"(v{pack.version}, {len(pack.scenes)} Scenes)\n"
                )
        return 0

    return _guard("list", command, stderr=errors, subject="Scene packs")


def cmd_scene_pack_inspect(
    args: object,
    *,
    store: ScenePackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        pack = _scene_store(args, store).inspect(getattr(args, "pack_id"))
        if bool(getattr(args, "json", False)):
            _write_json(output, json.loads(export_scene_pack(pack).decode("utf-8")))
        else:
            output.write(f"{pack.pack_id}: {pack.name}\n")
            output.write(f"version: {pack.version}\n")
            output.write(f"Scenes: {len(pack.scenes)}\n")
            for entry in pack.scenes:
                output.write(f"  {entry.scene.value}: {entry.label}\n")
        return 0

    return _guard("inspect", command, stderr=errors, subject="Scene packs")


def cmd_scene_pack_export(
    args: object,
    *,
    store: ScenePackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        selected = _scene_store(args, store)
        pack_id = getattr(args, "pack_id")
        target = getattr(args, "target", None)
        if target is None:
            output.write(selected.canonical_export(pack_id).decode("utf-8"))
            output.write("\n")
            return 0
        written = selected.export(pack_id, Path(target))
        if bool(getattr(args, "json", False)):
            _write_json(
                output,
                {
                    "pack_id": pack_id,
                    "status": "exported",
                    "target": str(written),
                },
            )
        else:
            output.write(f"{pack_id}: exported to {written}\n")
        return 0

    return _guard("export", command, stderr=errors, subject="Scene packs")


def cmd_scene_pack_preview(
    args: object,
    *,
    store: ScenePackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output, errors = _outputs(stdout, stderr)

    def command() -> int:
        plan = _scene_store(args, store).preview_source(getattr(args, "source"))
        document = _scene_preview_document(plan)
        if bool(getattr(args, "json", False)):
            _write_json(output, document)
        else:
            migration = " (migrated)" if plan.migrated else ""
            output.write(f"{plan.pack.pack_id}: {plan.pack.name}{migration}\n")
            for row in plan.rows:
                output.write(
                    f"  {row.scene.value}: {row.label}, "
                    f"motion {row.normal.effective_motion.value} -> "
                    f"{row.reduced_motion.effective_motion.value}\n"
                )
        return 0

    return _guard("preview", command, stderr=errors, subject="Scene packs")


_HANDLERS: Mapping[str, Callable[..., int]] = {
    "install": cmd_effect_pack_install,
    "pack-install": cmd_effect_pack_install,
    "update": cmd_effect_pack_update,
    "pack-update": cmd_effect_pack_update,
    "remove": cmd_effect_pack_remove,
    "pack-remove": cmd_effect_pack_remove,
    "duplicate": cmd_effect_pack_duplicate,
    "pack-duplicate": cmd_effect_pack_duplicate,
    "rename": cmd_effect_pack_rename,
    "pack-rename": cmd_effect_pack_rename,
    "list": cmd_effect_pack_list,
    "pack-list": cmd_effect_pack_list,
    "inspect": cmd_effect_pack_inspect,
    "pack-inspect": cmd_effect_pack_inspect,
    "export": cmd_effect_pack_export,
    "pack-export": cmd_effect_pack_export,
    "gallery": cmd_effect_gallery,
    "history": cmd_effect_history,
}

_SCENE_HANDLERS: Mapping[str, Callable[..., int]] = {
    "scene-install": cmd_scene_pack_install,
    "scene-update": cmd_scene_pack_update,
    "scene-remove": cmd_scene_pack_remove,
    "scene-list": cmd_scene_pack_list,
    "scene-inspect": cmd_scene_pack_inspect,
    "scene-export": cmd_scene_pack_export,
    "scene-preview": cmd_scene_pack_preview,
}


def dispatch_effect_command(
    args: object,
    *,
    store: EffectPackStore | None = None,
    scene_store: ScenePackStore | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Dispatch an argparse-like namespace without owning parser wiring."""

    output, errors = _outputs(stdout, stderr)
    action = getattr(args, "action", None)
    if action == "install" and bool(getattr(args, "update", False)):
        action = "update"
    scene_handler = (
        _SCENE_HANDLERS.get(action) if isinstance(action, str) else None
    )
    if scene_handler is not None:
        return scene_handler(
            args,
            store=scene_store,
            stdout=output,
            stderr=errors,
        )
    handler = _HANDLERS.get(action) if isinstance(action, str) else None
    if handler is None:
        errors.write("effect packs: unknown action\n")
        return 2
    return handler(
        args,
        store=store,
        stdout=output,
        stderr=errors,
    )


__all__ = [
    "cmd_effect_gallery",
    "cmd_effect_history",
    "cmd_effect_pack_duplicate",
    "cmd_effect_pack_export",
    "cmd_effect_pack_inspect",
    "cmd_effect_pack_install",
    "cmd_effect_pack_list",
    "cmd_effect_pack_remove",
    "cmd_effect_pack_rename",
    "cmd_effect_pack_update",
    "cmd_scene_pack_export",
    "cmd_scene_pack_inspect",
    "cmd_scene_pack_install",
    "cmd_scene_pack_list",
    "cmd_scene_pack_preview",
    "cmd_scene_pack_remove",
    "cmd_scene_pack_update",
    "dispatch_effect_command",
]
