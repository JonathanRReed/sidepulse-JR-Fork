from __future__ import annotations

from sidepulse.effect_assignment_store import (
    AssignmentRestoreHealth,
    EffectAssignmentCache,
    EffectAssignmentContext,
    EffectAssignmentDocument,
    EffectAssignmentRecord,
    load_effect_assignments,
    resolve_effect_assignment,
    save_effect_assignments,
)
from sidepulse.effect_studio import AssignmentScope
from sidepulse.scenes import Scene
from sidepulse.semantic_effect_router import SemanticEventKind


def _record(
    effect_id: str,
    scope: AssignmentScope,
    target_id: str | None,
) -> EffectAssignmentRecord:
    return EffectAssignmentRecord.create(effect_id, scope, target_id)


def test_owner_private_assignment_store_round_trips_typed_records(tmp_path) -> None:
    path = tmp_path / "effect-assignments.json"
    document = EffectAssignmentDocument(
        (
            _record("pulse", AssignmentScope.PROVIDER, "claude"),
            _record("notification", AssignmentScope.SCENE, Scene.NIGHT.value),
        )
    )

    save_effect_assignments(path, document)
    restored = load_effect_assignments(path)

    assert restored.health is AssignmentRestoreHealth.HEALTHY
    assert restored.document == document
    assert path.stat().st_mode & 0o077 == 0


def test_assignment_resolution_uses_most_specific_matching_scope() -> None:
    document = EffectAssignmentDocument(
        (
            _record("none", AssignmentScope.GLOBAL, None),
            _record("pulse", AssignmentScope.SEMANTIC, "notification"),
            _record("rainbow", AssignmentScope.PROVIDER, "claude"),
            _record(
                "notification",
                AssignmentScope.PROVIDER_INSTANCE,
                "claude:work",
            ),
            _record("pulse", AssignmentScope.PROJECT, "jr-bar"),
            _record("rainbow", AssignmentScope.DEVICE, "device-1"),
            _record("notification", AssignmentScope.SCENE, Scene.NIGHT.value),
        )
    )
    base = dict(
        semantic=SemanticEventKind.NOTIFICATION,
        scene=Scene.NIGHT,
        provider_id="claude",
        provider_instance_id="claude:work",
        project_id="jr-bar",
    )

    assert resolve_effect_assignment(
        document,
        EffectAssignmentContext(**base, device_id="device-1"),
    ).effect_id == "rainbow"
    assert resolve_effect_assignment(
        document,
        EffectAssignmentContext(**base),
    ).effect_id == "pulse"
    assert resolve_effect_assignment(
        document,
        EffectAssignmentContext(**{**base, "project_id": None}),
    ).effect_id == "notification"


def test_urgent_semantics_keep_the_alert_safeguard() -> None:
    document = EffectAssignmentDocument(
        (
            _record("none", AssignmentScope.GLOBAL, None),
            _record("alert", AssignmentScope.SEMANTIC, "asking"),
            _record("rainbow", AssignmentScope.DEVICE, "device-1"),
        )
    )

    asking = resolve_effect_assignment(
        document,
        EffectAssignmentContext(
            semantic=SemanticEventKind.ASK,
            scene=Scene.FOCUS,
            device_id="device-1",
        ),
    )
    failure = resolve_effect_assignment(
        document,
        EffectAssignmentContext(
            semantic=SemanticEventKind.FAILURE,
            scene=Scene.FOCUS,
            device_id="device-1",
        ),
    )

    assert asking is not None and asking.effect_id == "alert"
    assert failure is None


def test_assignment_cache_replaces_snapshots_without_disk_access() -> None:
    original = EffectAssignmentDocument(
        (_record("pulse", AssignmentScope.PROVIDER, "claude"),)
    )
    updated = EffectAssignmentDocument(
        (_record("rainbow", AssignmentScope.PROVIDER, "claude"),)
    )
    cache = EffectAssignmentCache(original)

    assert cache.generation == 0
    assert cache.snapshot() == original
    cache.replace(updated)

    assert cache.generation == 1
    assert cache.snapshot() == updated
