"""Typed scoped effect assignments with owner-private persistence and cache."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from .effect_registry import EFFECT_REGISTRY, EffectRegistry
from .effect_studio import AssignmentScope, plan_assignment
from .private_io import atomic_private_write, read_private_text
from .scenes import Scene
from .semantic_effect_router import URGENT_SEMANTICS, SemanticEventKind
from .state_paths import default_state_dir

EFFECT_ASSIGNMENT_STORE_NAME: Final = "effect-assignments.json"
EFFECT_ASSIGNMENT_STORE_VERSION: Final = 1
MAX_EFFECT_ASSIGNMENTS: Final = 256
MAX_EFFECT_ASSIGNMENT_STORE_BYTES: Final = 256 * 1024

_DOCUMENT_FIELDS: Final = frozenset({"assignments", "version"})
_ASSIGNMENT_FIELDS: Final = frozenset({"effect_id", "scope", "target_id"})

_SEMANTIC_TARGETS: Final = {
    SemanticEventKind.ASK: "asking",
    SemanticEventKind.FAILURE: "failure",
    SemanticEventKind.NOTIFICATION: "notification",
    SemanticEventKind.HANDOFF: "transition",
    SemanticEventKind.WORK: "working",
    SemanticEventKind.COMPLETION: "completion",
    SemanticEventKind.RECOVERY: "recovery",
    SemanticEventKind.ENVIRONMENT: "environment",
    SemanticEventKind.IDLE: "idle",
}

_SCOPE_PRECEDENCE: Final = (
    AssignmentScope.DEVICE,
    AssignmentScope.PROJECT,
    AssignmentScope.PROVIDER_INSTANCE,
    AssignmentScope.PROVIDER,
    AssignmentScope.SCENE,
    AssignmentScope.SEMANTIC,
    AssignmentScope.GLOBAL,
)


class EffectAssignmentStoreError(ValueError):
    """A scoped assignment could not be validated or persisted safely."""


class AssignmentRestoreHealth(str, Enum):
    HEALTHY = "healthy"
    MISSING = "missing"
    OVERSIZED = "oversized"
    CORRUPT = "corrupt"
    UNAVAILABLE = "unavailable"


def _bounded_optional_identifier(value: object, field: str) -> str | None:
    if value is None:
        return None
    if (
        type(value) is not str
        or not value
        or len(value) > 160
        or not value.isprintable()
        or value.strip() != value
    ):
        raise EffectAssignmentStoreError(f"invalid {field}")
    return value


@dataclass(frozen=True, slots=True)
class EffectAssignmentRecord:
    effect_id: str
    scope: AssignmentScope
    target_id: str | None

    def __post_init__(self) -> None:
        if (
            type(self.effect_id) is not str
            or not self.effect_id
            or len(self.effect_id) > 160
            or not self.effect_id.isprintable()
            or self.effect_id.strip() != self.effect_id
            or type(self.scope) is not AssignmentScope
        ):
            raise EffectAssignmentStoreError("invalid effect assignment")
        target = _bounded_optional_identifier(self.target_id, "assignment target")
        if (self.scope is AssignmentScope.GLOBAL) != (target is None):
            raise EffectAssignmentStoreError("invalid effect assignment target")
        if (
            self.scope is AssignmentScope.SEMANTIC
            and target in {"asking", "failure"}
            and self.effect_id != "alert"
        ):
            raise EffectAssignmentStoreError(
                "asking and failure must retain the alert safeguard"
            )

    @property
    def key(self) -> tuple[AssignmentScope, str | None]:
        return self.scope, self.target_id

    @classmethod
    def create(
        cls,
        effect_id: object,
        scope: object,
        target_id: object = None,
        *,
        registry: EffectRegistry = EFFECT_REGISTRY,
    ) -> EffectAssignmentRecord:
        try:
            plan = plan_assignment(effect_id, scope, target_id, registry)
            return cls(plan.effect_id, plan.scope, plan.target_id)
        except (TypeError, ValueError) as error:
            raise EffectAssignmentStoreError("invalid effect assignment") from error


@dataclass(frozen=True, slots=True)
class EffectAssignmentDocument:
    assignments: tuple[EffectAssignmentRecord, ...] = ()

    def __post_init__(self) -> None:
        if not (
            type(self.assignments) is tuple
            and len(self.assignments) <= MAX_EFFECT_ASSIGNMENTS
            and all(
                type(assignment) is EffectAssignmentRecord
                for assignment in self.assignments
            )
        ):
            raise EffectAssignmentStoreError("invalid effect assignment document")
        keys = tuple(assignment.key for assignment in self.assignments)
        if len(set(keys)) != len(keys):
            raise EffectAssignmentStoreError("duplicate effect assignment scope")

    def assignment_for(
        self,
        scope: AssignmentScope,
        target_id: str | None,
    ) -> EffectAssignmentRecord | None:
        return next(
            (
                assignment
                for assignment in self.assignments
                if assignment.scope is scope and assignment.target_id == target_id
            ),
            None,
        )

    def with_assignment(
        self,
        assignment: EffectAssignmentRecord,
    ) -> EffectAssignmentDocument:
        if type(assignment) is not EffectAssignmentRecord:
            raise EffectAssignmentStoreError("assignment must be typed")
        retained = tuple(
            current
            for current in self.assignments
            if current.key != assignment.key
        )
        return EffectAssignmentDocument((*retained, assignment))

    def without_assignment(
        self,
        scope: AssignmentScope,
        target_id: str | None,
    ) -> EffectAssignmentDocument:
        return EffectAssignmentDocument(
            tuple(
                assignment
                for assignment in self.assignments
                if assignment.key != (scope, target_id)
            )
        )


@dataclass(frozen=True, slots=True)
class EffectAssignmentContext:
    semantic: SemanticEventKind
    scene: Scene
    provider_id: str | None = None
    provider_instance_id: str | None = None
    project_id: str | None = None
    device_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.semantic) is not SemanticEventKind or type(self.scene) is not Scene:
            raise EffectAssignmentStoreError("invalid effect assignment context")
        for field, value in (
            ("provider_id", self.provider_id),
            ("provider_instance_id", self.provider_instance_id),
            ("project_id", self.project_id),
            ("device_id", self.device_id),
        ):
            _bounded_optional_identifier(value, field)


def _target_for_scope(
    context: EffectAssignmentContext,
    scope: AssignmentScope,
) -> str | None:
    return {
        AssignmentScope.GLOBAL: None,
        AssignmentScope.SEMANTIC: _SEMANTIC_TARGETS[context.semantic],
        AssignmentScope.PROVIDER: context.provider_id,
        AssignmentScope.PROVIDER_INSTANCE: context.provider_instance_id,
        AssignmentScope.PROJECT: context.project_id,
        AssignmentScope.DEVICE: context.device_id,
        AssignmentScope.SCENE: context.scene.value,
    }[scope]


def resolve_effect_assignment(
    document: EffectAssignmentDocument,
    context: EffectAssignmentContext,
) -> EffectAssignmentRecord | None:
    """Resolve the most specific assignment while preserving urgent alerts."""

    if type(document) is not EffectAssignmentDocument:
        raise EffectAssignmentStoreError("assignment document must be typed")
    if type(context) is not EffectAssignmentContext:
        raise EffectAssignmentStoreError("assignment context must be typed")
    scopes = (
        (AssignmentScope.SEMANTIC,)
        if context.semantic in URGENT_SEMANTICS
        else _SCOPE_PRECEDENCE
    )
    for scope in scopes:
        target = _target_for_scope(context, scope)
        if scope is not AssignmentScope.GLOBAL and target is None:
            continue
        assignment = document.assignment_for(scope, target)
        if assignment is not None:
            return assignment
    return None


@dataclass(frozen=True, slots=True)
class EffectAssignmentRestore:
    document: EffectAssignmentDocument
    health: AssignmentRestoreHealth


def default_effect_assignment_path(home: Path | None = None) -> Path:
    return default_state_dir(home) / EFFECT_ASSIGNMENT_STORE_NAME


def _strict_object(pairs: list[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise EffectAssignmentStoreError("effect assignment JSON is invalid")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise EffectAssignmentStoreError("effect assignment JSON is invalid")


def _decode(raw: str) -> EffectAssignmentDocument:
    value = json.loads(
        raw,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
    if type(value) is not dict or frozenset(value) != _DOCUMENT_FIELDS:
        raise EffectAssignmentStoreError("effect assignment document is invalid")
    if value["version"] != EFFECT_ASSIGNMENT_STORE_VERSION:
        raise EffectAssignmentStoreError("effect assignment version is unsupported")
    rows = value["assignments"]
    if type(rows) is not list or len(rows) > MAX_EFFECT_ASSIGNMENTS:
        raise EffectAssignmentStoreError("effect assignments exceed their bound")
    assignments = []
    for row in rows:
        if type(row) is not dict or frozenset(row) != _ASSIGNMENT_FIELDS:
            raise EffectAssignmentStoreError("effect assignment row is invalid")
        try:
            assignments.append(
                EffectAssignmentRecord(
                    effect_id=row["effect_id"],
                    scope=AssignmentScope(row["scope"]),
                    target_id=row["target_id"],
                )
            )
        except (TypeError, ValueError) as error:
            raise EffectAssignmentStoreError(
                "effect assignment row is invalid"
            ) from error
    return EffectAssignmentDocument(tuple(assignments))


def load_effect_assignments(path: Path | None = None) -> EffectAssignmentRestore:
    target = default_effect_assignment_path() if path is None else Path(path)
    try:
        document = _decode(
            read_private_text(target, max_bytes=MAX_EFFECT_ASSIGNMENT_STORE_BYTES)
        )
    except FileNotFoundError:
        return EffectAssignmentRestore(
            EffectAssignmentDocument(),
            AssignmentRestoreHealth.MISSING,
        )
    except OSError as error:
        health = (
            AssignmentRestoreHealth.OVERSIZED
            if "exceeds maximum size" in str(error)
            else AssignmentRestoreHealth.UNAVAILABLE
        )
        return EffectAssignmentRestore(EffectAssignmentDocument(), health)
    except (RecursionError, TypeError, UnicodeError, ValueError):
        return EffectAssignmentRestore(
            EffectAssignmentDocument(),
            AssignmentRestoreHealth.CORRUPT,
        )
    return EffectAssignmentRestore(document, AssignmentRestoreHealth.HEALTHY)


def save_effect_assignments(
    path: Path,
    document: EffectAssignmentDocument,
) -> Path:
    if type(document) is not EffectAssignmentDocument:
        raise EffectAssignmentStoreError("assignment document must be typed")
    payload = json.dumps(
        {
            "assignments": [
                {
                    "effect_id": assignment.effect_id,
                    "scope": assignment.scope.value,
                    "target_id": assignment.target_id,
                }
                for assignment in document.assignments
            ],
            "version": EFFECT_ASSIGNMENT_STORE_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(payload.encode()) > MAX_EFFECT_ASSIGNMENT_STORE_BYTES:
        raise EffectAssignmentStoreError("assignment document exceeds its bound")
    try:
        return atomic_private_write(Path(path), f"{payload}\n")
    except OSError as error:
        raise EffectAssignmentStoreError("effect assignments could not be saved") from error


class EffectAssignmentStore:
    """Explicit I/O owner used by user actions, never preview refresh."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = default_effect_assignment_path() if path is None else Path(path)

    def load(self) -> EffectAssignmentRestore:
        return load_effect_assignments(self.path)

    def save(self, document: EffectAssignmentDocument) -> Path:
        return save_effect_assignments(self.path, document)


class EffectAssignmentCache:
    """Thread-safe in-memory runtime authority refreshed after explicit saves."""

    def __init__(
        self,
        document: EffectAssignmentDocument = EffectAssignmentDocument(),
        registry: EffectRegistry = EFFECT_REGISTRY,
    ) -> None:
        if type(document) is not EffectAssignmentDocument or not isinstance(
            registry, EffectRegistry
        ):
            raise EffectAssignmentStoreError("invalid assignment cache input")
        self._lock = threading.RLock()
        self._document = document
        self._registry = registry
        self._generation = 0

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def snapshot(self) -> EffectAssignmentDocument:
        with self._lock:
            return self._document

    def registry(self) -> EffectRegistry:
        with self._lock:
            return self._registry

    def replace(
        self,
        document: EffectAssignmentDocument,
        *,
        registry: EffectRegistry | None = None,
    ) -> None:
        if type(document) is not EffectAssignmentDocument or (
            registry is not None and not isinstance(registry, EffectRegistry)
        ):
            raise EffectAssignmentStoreError("invalid assignment cache update")
        with self._lock:
            self._document = document
            if registry is not None:
                self._registry = registry
            self._generation += 1


__all__ = [
    "EFFECT_ASSIGNMENT_STORE_NAME",
    "EFFECT_ASSIGNMENT_STORE_VERSION",
    "MAX_EFFECT_ASSIGNMENTS",
    "MAX_EFFECT_ASSIGNMENT_STORE_BYTES",
    "AssignmentRestoreHealth",
    "EffectAssignmentCache",
    "EffectAssignmentContext",
    "EffectAssignmentDocument",
    "EffectAssignmentRecord",
    "EffectAssignmentRestore",
    "EffectAssignmentStore",
    "EffectAssignmentStoreError",
    "default_effect_assignment_path",
    "load_effect_assignments",
    "resolve_effect_assignment",
    "save_effect_assignments",
]
