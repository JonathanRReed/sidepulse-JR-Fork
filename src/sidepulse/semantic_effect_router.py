"""Pure semantic event-to-effect arbitration for JR-Bar surfaces.

The router consumes content-free facts that runtime owners have already
collected. It performs no rendering, notification delivery, settings access,
timing, device access, or other I/O. Effect definitions and Reduce Motion
fallbacks remain owned by :mod:`sidepulse.effect_registry`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

from .dnd_policy import DisplayAdmission
from .effect_registry import EFFECT_REGISTRY, EffectRegistry
from .scenes import DEFAULT_SCENE, Scene

MAX_SEMANTIC_EFFECT_CANDIDATES: Final = 128
MAX_DESTINATION_SURFACES: Final = 16


class SemanticEventKind(str, Enum):
    """Stable semantic meanings in their approved arbitration order."""

    ASK = "ask"
    FAILURE = "failure"
    NOTIFICATION = "notification"
    HANDOFF = "handoff"
    WORK = "work"
    COMPLETION = "completion"
    RECOVERY = "recovery"
    ENVIRONMENT = "environment"
    IDLE = "idle"


SEMANTIC_PRIORITY: Mapping[SemanticEventKind, int] = MappingProxyType(
    {
        SemanticEventKind.ASK: 90,
        SemanticEventKind.FAILURE: 80,
        SemanticEventKind.NOTIFICATION: 70,
        SemanticEventKind.HANDOFF: 60,
        SemanticEventKind.WORK: 50,
        SemanticEventKind.COMPLETION: 40,
        SemanticEventKind.RECOVERY: 30,
        SemanticEventKind.ENVIRONMENT: 20,
        SemanticEventKind.IDLE: 10,
    }
)

URGENT_SEMANTICS: Final = frozenset(
    {SemanticEventKind.ASK, SemanticEventKind.FAILURE}
)
COURTESY_SEMANTICS: Final = frozenset(
    {
        SemanticEventKind.COMPLETION,
        SemanticEventKind.RECOVERY,
        SemanticEventKind.ENVIRONMENT,
    }
)


class SuppressionReason(str, Enum):
    DISPLAY_ADMISSION = "display_admission"
    COURTESY_FOCUS = "courtesy_focus"
    COURTESY_SNOOZE = "courtesy_snooze"
    COURTESY_BUDGET = "courtesy_budget"
    EFFECT_NOT_REGISTERED = "effect_not_registered"
    NO_SUPPORTED_DESTINATION = "no_supported_destination"
    LOWER_PRIORITY = "lower_priority"


@dataclass(frozen=True, slots=True)
class CourtesySuppression:
    """Content-free reasons that currently hold courtesy-only signals."""

    focus: bool = False
    snoozed: bool = False
    budget_exhausted: bool = False

    def __post_init__(self) -> None:
        if not all(
            type(value) is bool
            for value in (self.focus, self.snoozed, self.budget_exhausted)
        ):
            raise ValueError("courtesy suppression inputs must be booleans")

    @property
    def reason(self) -> SuppressionReason | None:
        if self.focus:
            return SuppressionReason.COURTESY_FOCUS
        if self.snoozed:
            return SuppressionReason.COURTESY_SNOOZE
        if self.budget_exhausted:
            return SuppressionReason.COURTESY_BUDGET
        return None


@dataclass(frozen=True, slots=True)
class SemanticEffectCandidate:
    """One active, content-free semantic fact available for presentation.

    ``key`` is an opaque stable identity. ``sequence`` is a caller-provided
    monotonic ordering token used only to break ties within one semantic
    priority. An empty destination tuple requests every surface supported by
    the selected registry effect.
    """

    key: str
    semantic: SemanticEventKind
    sequence: int = 0
    destination_surfaces: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.key) is not str
            or not self.key
            or len(self.key) > 128
            or self.key.strip() != self.key
        ):
            raise ValueError("semantic candidate key must be bounded opaque text")
        if type(self.semantic) is not SemanticEventKind:
            raise ValueError("semantic candidate kind must be known")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("semantic candidate sequence must be a nonnegative integer")
        if type(self.destination_surfaces) is not tuple:
            raise ValueError("semantic candidate destinations must be a tuple")
        if len(self.destination_surfaces) > MAX_DESTINATION_SURFACES:
            raise ValueError("semantic candidate destinations must remain bounded")
        if not all(
            type(surface) is str
            and surface
            and len(surface) <= 64
            and surface.strip() == surface
            for surface in self.destination_surfaces
        ):
            raise ValueError("semantic candidate destinations must be bounded identifiers")
        if len(set(self.destination_surfaces)) != len(self.destination_surfaces):
            raise ValueError("semantic candidate destinations must be unique")


def _validate_assignment(
    semantic: SemanticEventKind,
    effect_identifier: str,
) -> None:
    if type(semantic) is not SemanticEventKind:
        raise ValueError("semantic effect assignment kind must be known")
    if (
        type(effect_identifier) is not str
        or not effect_identifier
        or len(effect_identifier) > 128
        or effect_identifier.strip() != effect_identifier
    ):
        raise ValueError("semantic effect identifier must be bounded text")


@dataclass(frozen=True, slots=True)
class SemanticEffectAssignment:
    semantic: SemanticEventKind
    effect_identifier: str

    def __post_init__(self) -> None:
        _validate_assignment(self.semantic, self.effect_identifier)


@dataclass(frozen=True, slots=True)
class SceneEffectAssignment:
    scene: Scene
    semantic: SemanticEventKind
    effect_identifier: str

    def __post_init__(self) -> None:
        if type(self.scene) is not Scene:
            raise ValueError("scene effect assignment scene must be known")
        _validate_assignment(self.semantic, self.effect_identifier)


@dataclass(frozen=True, slots=True)
class SemanticEffectMap:
    """Global semantic assignments plus optional Scene-specific refinements.

    Scene assignments deliberately cannot replace ASK or FAILURE. Those urgent
    meanings always resolve through their global assignments so a calm, night,
    demo, or custom Scene cannot silently relabel an urgent override.
    """

    assignments: tuple[SemanticEffectAssignment, ...]
    scene_assignments: tuple[SceneEffectAssignment, ...] = ()

    def __post_init__(self) -> None:
        if type(self.assignments) is not tuple:
            raise ValueError("semantic effect assignments must be a tuple")
        if not all(type(item) is SemanticEffectAssignment for item in self.assignments):
            raise ValueError("semantic effect assignments must be typed")
        assignment_semantics = tuple(item.semantic for item in self.assignments)
        if len(set(assignment_semantics)) != len(assignment_semantics):
            raise ValueError("semantic effect assignments must be unique")
        if set(assignment_semantics) != set(SemanticEventKind):
            raise ValueError("semantic effect assignments must cover every semantic kind")
        if type(self.scene_assignments) is not tuple:
            raise ValueError("scene effect assignments must be a tuple")
        if not all(
            type(item) is SceneEffectAssignment for item in self.scene_assignments
        ):
            raise ValueError("scene effect assignments must be typed")
        scene_keys = tuple(
            (item.scene, item.semantic) for item in self.scene_assignments
        )
        if len(set(scene_keys)) != len(scene_keys):
            raise ValueError("scene effect assignments must be unique")

    def effect_identifier_for(
        self,
        semantic: SemanticEventKind,
        scene: Scene,
    ) -> str:
        if type(semantic) is not SemanticEventKind or type(scene) is not Scene:
            raise ValueError("effect resolution requires a known semantic and Scene")
        if semantic not in URGENT_SEMANTICS:
            for assignment in self.scene_assignments:
                if assignment.scene is scene and assignment.semantic is semantic:
                    return assignment.effect_identifier
        for assignment in self.assignments:
            if assignment.semantic is semantic:
                return assignment.effect_identifier
        raise ValueError("semantic effect map is incomplete")


DEFAULT_SEMANTIC_EFFECT_MAP: Final = SemanticEffectMap(
    assignments=(
        SemanticEffectAssignment(SemanticEventKind.ASK, "alert"),
        SemanticEffectAssignment(SemanticEventKind.FAILURE, "alert"),
        SemanticEffectAssignment(SemanticEventKind.NOTIFICATION, "notification"),
        SemanticEffectAssignment(SemanticEventKind.HANDOFF, "pulse"),
        SemanticEffectAssignment(SemanticEventKind.WORK, "pulse"),
        SemanticEffectAssignment(SemanticEventKind.COMPLETION, "notification"),
        SemanticEffectAssignment(SemanticEventKind.RECOVERY, "notification"),
        SemanticEffectAssignment(SemanticEventKind.ENVIRONMENT, "none"),
        SemanticEffectAssignment(SemanticEventKind.IDLE, "none"),
    )
)


@dataclass(frozen=True, slots=True)
class SemanticEffectSuppression:
    candidate: SemanticEffectCandidate
    reason: SuppressionReason

    def __post_init__(self) -> None:
        if type(self.candidate) is not SemanticEffectCandidate:
            raise ValueError("semantic suppression candidate must be typed")
        if type(self.reason) is not SuppressionReason:
            raise ValueError("semantic suppression reason must be known")


@dataclass(frozen=True, slots=True)
class SemanticEffectSelection:
    winner: SemanticEffectCandidate | None
    suppressed: tuple[SemanticEffectSuppression, ...]
    registry_effect_identifier: str | None
    reduce_motion_substitution: str | None
    destination_surfaces: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.winner is not None and type(self.winner) is not SemanticEffectCandidate:
            raise ValueError("semantic effect winner must be typed")
        if type(self.suppressed) is not tuple or not all(
            type(item) is SemanticEffectSuppression for item in self.suppressed
        ):
            raise ValueError("semantic effect suppressions must be an immutable tuple")
        if self.winner is None:
            if (
                self.registry_effect_identifier is not None
                or self.reduce_motion_substitution is not None
                or self.destination_surfaces
            ):
                raise ValueError("an empty semantic selection cannot carry an effect")
            return
        if (
            type(self.registry_effect_identifier) is not str
            or not self.registry_effect_identifier
        ):
            raise ValueError("a semantic effect winner requires a registry identifier")
        if self.reduce_motion_substitution is not None and (
            type(self.reduce_motion_substitution) is not str
            or not self.reduce_motion_substitution
            or self.reduce_motion_substitution == self.registry_effect_identifier
        ):
            raise ValueError("Reduce Motion substitution must name a different effect")
        if type(self.destination_surfaces) is not tuple or not self.destination_surfaces:
            raise ValueError("a semantic effect winner requires destination surfaces")


def _is_admitted(
    semantic: SemanticEventKind,
    display_admission: DisplayAdmission,
) -> bool:
    if display_admission is DisplayAdmission.ALL:
        return True
    if display_admission is DisplayAdmission.CRITICAL:
        return semantic in URGENT_SEMANTICS
    if display_admission is DisplayAdmission.ASKS:
        return semantic is SemanticEventKind.ASK
    return False


def _ranked_candidates(
    candidates: tuple[SemanticEffectCandidate, ...],
) -> tuple[SemanticEffectCandidate, ...]:
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                -SEMANTIC_PRIORITY[candidate.semantic],
                -candidate.sequence,
                candidate.key,
            ),
        )
    )


def route_semantic_effects(
    candidates: tuple[SemanticEffectCandidate, ...],
    *,
    scene: Scene = DEFAULT_SCENE,
    display_admission: DisplayAdmission = DisplayAdmission.ALL,
    courtesy_suppression: CourtesySuppression = CourtesySuppression(),
    effect_map: SemanticEffectMap = DEFAULT_SEMANTIC_EFFECT_MAP,
    reduce_motion: bool = False,
    registry: EffectRegistry = EFFECT_REGISTRY,
) -> SemanticEffectSelection:
    """Select one registered effect without performing runtime or surface I/O."""

    if type(candidates) is not tuple:
        raise ValueError("semantic effect candidates must be a tuple")
    if len(candidates) > MAX_SEMANTIC_EFFECT_CANDIDATES:
        raise ValueError("semantic effect candidates must remain bounded")
    if not all(type(item) is SemanticEffectCandidate for item in candidates):
        raise ValueError("semantic effect candidates must be typed")
    candidate_keys = tuple(item.key for item in candidates)
    if len(set(candidate_keys)) != len(candidate_keys):
        raise ValueError("semantic effect candidate keys must be unique")
    if type(scene) is not Scene:
        raise ValueError("semantic effect routing Scene must be known")
    if type(display_admission) is not DisplayAdmission:
        raise ValueError("semantic effect display admission must be known")
    if type(courtesy_suppression) is not CourtesySuppression:
        raise ValueError("semantic effect courtesy suppression must be typed")
    if type(effect_map) is not SemanticEffectMap:
        raise ValueError("semantic effect map must be typed")
    if type(reduce_motion) is not bool:
        raise ValueError("semantic effect Reduce Motion input must be a boolean")
    if not isinstance(registry, EffectRegistry):
        raise ValueError("semantic effect registry must use EffectRegistry")

    winner: SemanticEffectCandidate | None = None
    winner_effect_identifier: str | None = None
    winner_substitution: str | None = None
    winner_destinations: tuple[str, ...] = ()
    suppressed: list[SemanticEffectSuppression] = []
    courtesy_reason = courtesy_suppression.reason

    for candidate in _ranked_candidates(candidates):
        if not _is_admitted(candidate.semantic, display_admission):
            suppressed.append(
                SemanticEffectSuppression(
                    candidate,
                    SuppressionReason.DISPLAY_ADMISSION,
                )
            )
            continue
        if candidate.semantic in COURTESY_SEMANTICS and courtesy_reason is not None:
            suppressed.append(SemanticEffectSuppression(candidate, courtesy_reason))
            continue

        effect_identifier = effect_map.effect_identifier_for(
            candidate.semantic,
            scene,
        )
        effect = registry.get(effect_identifier)
        if effect is None:
            suppressed.append(
                SemanticEffectSuppression(
                    candidate,
                    SuppressionReason.EFFECT_NOT_REGISTERED,
                )
            )
            continue

        effective_effect = effect
        substitution: str | None = None
        if reduce_motion:
            try:
                effective_effect = registry.reduced_motion(effect_identifier)
            except KeyError:
                suppressed.append(
                    SemanticEffectSuppression(
                        candidate,
                        SuppressionReason.EFFECT_NOT_REGISTERED,
                    )
                )
                continue
            if effective_effect.identifier != effect_identifier:
                substitution = effective_effect.identifier

        supported_surfaces = frozenset(effective_effect.surfaces)
        destinations = (
            tuple(
                surface
                for surface in candidate.destination_surfaces
                if surface in supported_surfaces
            )
            if candidate.destination_surfaces
            else effective_effect.surfaces
        )
        if not destinations:
            suppressed.append(
                SemanticEffectSuppression(
                    candidate,
                    SuppressionReason.NO_SUPPORTED_DESTINATION,
                )
            )
            continue
        if winner is not None:
            suppressed.append(
                SemanticEffectSuppression(
                    candidate,
                    SuppressionReason.LOWER_PRIORITY,
                )
            )
            continue

        winner = candidate
        winner_effect_identifier = effect_identifier
        winner_substitution = substitution
        winner_destinations = destinations

    return SemanticEffectSelection(
        winner=winner,
        suppressed=tuple(suppressed),
        registry_effect_identifier=winner_effect_identifier,
        reduce_motion_substitution=winner_substitution,
        destination_surfaces=winner_destinations,
    )


__all__ = [
    "COURTESY_SEMANTICS",
    "DEFAULT_SEMANTIC_EFFECT_MAP",
    "MAX_DESTINATION_SURFACES",
    "MAX_SEMANTIC_EFFECT_CANDIDATES",
    "SEMANTIC_PRIORITY",
    "URGENT_SEMANTICS",
    "CourtesySuppression",
    "SceneEffectAssignment",
    "SemanticEffectAssignment",
    "SemanticEffectCandidate",
    "SemanticEffectMap",
    "SemanticEffectSelection",
    "SemanticEffectSuppression",
    "SemanticEventKind",
    "SuppressionReason",
    "route_semantic_effects",
]
