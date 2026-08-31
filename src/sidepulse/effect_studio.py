"""Pure Effect Studio and Preview Lab projections.

The models in this module describe UI-ready data and explicit operation plans.
They never render, write settings, touch devices, read files, or perform
network work. Runtime owners remain responsible for executing an approved plan.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from itertools import islice
from typing import Any

from .colors import ColorSettings, StudioPreviewSession
from .effect_packs import (
    EffectPack,
    EffectPackError,
    LicenseMetadata,
    effect_definitions_from_pack,
    export_pack,
    validate_pack,
)
from .effect_registry import EFFECT_REGISTRY, EffectDefinition, EffectRegistry
from .scenes import ScenePolicy, policy_for_scene, scene_from_value

MAX_SEARCH_CHARACTERS = 120
MAX_SYNTHETIC_EVENTS = 32
MAX_SYNTHETIC_TIMELINE_SECONDS = 300.0
MAX_PHYSICAL_PREVIEW_SECONDS = 30.0
MAX_ASSIGNMENT_TARGET_CHARACTERS = 160
MAX_SUPPRESSED_SIGNALS = 16
MAX_POLICY_DECISIONS = 16
MAX_SUPPRESSED_SIGNAL_COUNT = 99
MAX_SOURCE_AGE_SECONDS = 30.0 * 24.0 * 60.0 * 60.0
MAX_EFFECT_EXPIRATION_SECONDS = MAX_SOURCE_AGE_SECONDS
FRESH_SOURCE_AGE_SECONDS = 60.0

PHYSICAL_PREVIEW_RELEASE_TRIGGERS: tuple[str, ...] = (
    "close",
    "sleep",
    "app_termination",
    "error",
)

_OPAQUE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]*\Z")


class EffectStudioError(ValueError):
    """Raised when a Studio projection or plan cannot be made safely."""


class SemanticFamily(str, Enum):
    WORKING = "working"
    ASKING = "asking"
    COMPLETION = "completion"
    FAILURE = "failure"
    RECOVERY = "recovery"
    NOTIFICATION = "notification"
    QUOTA = "quota"
    ENVIRONMENT = "environment"
    IDLE = "idle"
    TRANSITION = "transition"


class StudioSurface(str, Enum):
    SCREEN_BAR = "screen_bar"
    SIDEPULSE_PRO = "sidepulse_pro"
    SIDEPULSE_DOT = "sidepulse_dot"
    GLANCE_LIGHT = "glance_light"


class SyntheticScenario(str, Enum):
    ONE_AGENT = "one_agent"
    SEVERAL_AGENTS = "several_agents"
    ASKING = "asking"
    FAILURE = "failure"
    HANDOFF = "handoff"
    COMPLETION = "completion"
    QUOTA_RESET = "quota_reset"
    DND = "dnd"
    LOW_POWER = "low_power"
    SLEEP = "sleep"
    LID_TRANSITION = "lid_transition"
    REMOTE_FLEET_CHANGE = "remote_fleet_change"


class ColorVisionMode(str, Enum):
    STANDARD = "standard"
    PROTANOPIA = "protanopia"
    DEUTERANOPIA = "deuteranopia"
    TRITANOPIA = "tritanopia"
    MONOCHROMACY = "monochromacy"


class StudioSessionAction(str, Enum):
    PREVIEW = "preview"
    COMPARE = "compare"
    COMMIT = "commit"
    REVERT = "revert"


class AssignmentScope(str, Enum):
    GLOBAL = "global"
    SEMANTIC = "semantic"
    PROVIDER = "provider"
    PROVIDER_INSTANCE = "provider_instance"
    PROJECT = "project"
    DEVICE = "device"
    SCENE = "scene"


class PhysicalPreviewDecision(str, Enum):
    ALLOWED = "allowed"
    CONSENT_REQUIRED = "consent_required"


class SourceFreshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class PolicyDecision(str, Enum):
    ROUTE_WINNER = "route_winner"
    DND_SUPPRESSED = "dnd_suppressed"
    LOW_POWER_SUBSTITUTE = "low_power_substitute"
    REDUCE_MOTION_SUBSTITUTE = "reduce_motion_substitute"
    SCENE_OVERRIDE = "scene_override"
    SURFACE_UNSUPPORTED = "surface_unsupported"
    EXPIRED = "expired"


_SEMANTIC_ORDER = {family: index for index, family in enumerate(SemanticFamily)}
_SURFACE_REGISTRY_KEYS: dict[StudioSurface, frozenset[str]] = {
    StudioSurface.SCREEN_BAR: frozenset({"screen_bar", "settings_preview", "status_bar"}),
    StudioSurface.SIDEPULSE_PRO: frozenset({"sidepulse_pro", "pro"}),
    StudioSurface.SIDEPULSE_DOT: frozenset({"sidepulse_dot", "dot"}),
    StudioSurface.GLANCE_LIGHT: frozenset({"glance_light"}),
}
_SURFACE_LED_COUNTS: dict[StudioSurface, int] = {
    StudioSurface.SCREEN_BAR: 24,
    StudioSurface.SIDEPULSE_PRO: 24,
    StudioSurface.SIDEPULSE_DOT: 2,
    StudioSurface.GLANCE_LIGHT: 1,
}
_SCENARIO_SEMANTICS: dict[SyntheticScenario, SemanticFamily] = {
    SyntheticScenario.ONE_AGENT: SemanticFamily.WORKING,
    SyntheticScenario.SEVERAL_AGENTS: SemanticFamily.WORKING,
    SyntheticScenario.ASKING: SemanticFamily.ASKING,
    SyntheticScenario.FAILURE: SemanticFamily.FAILURE,
    SyntheticScenario.HANDOFF: SemanticFamily.TRANSITION,
    SyntheticScenario.COMPLETION: SemanticFamily.COMPLETION,
    SyntheticScenario.QUOTA_RESET: SemanticFamily.QUOTA,
    SyntheticScenario.DND: SemanticFamily.IDLE,
    SyntheticScenario.LOW_POWER: SemanticFamily.ENVIRONMENT,
    SyntheticScenario.SLEEP: SemanticFamily.IDLE,
    SyntheticScenario.LID_TRANSITION: SemanticFamily.TRANSITION,
    SyntheticScenario.REMOTE_FLEET_CHANGE: SemanticFamily.TRANSITION,
}
_SCENARIO_AGENT_COUNTS: dict[SyntheticScenario, int] = {
    SyntheticScenario.ONE_AGENT: 1,
    SyntheticScenario.SEVERAL_AGENTS: 4,
    SyntheticScenario.ASKING: 1,
    SyntheticScenario.FAILURE: 1,
    SyntheticScenario.HANDOFF: 2,
    SyntheticScenario.COMPLETION: 1,
    SyntheticScenario.QUOTA_RESET: 0,
    SyntheticScenario.DND: 1,
    SyntheticScenario.LOW_POWER: 1,
    SyntheticScenario.SLEEP: 0,
    SyntheticScenario.LID_TRANSITION: 1,
    SyntheticScenario.REMOTE_FLEET_CHANGE: 4,
}

DEFAULT_SYNTHETIC_SCENARIOS: tuple[SyntheticScenario, ...] = tuple(SyntheticScenario)


def _bounded_number(
    value: object,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EffectStudioError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise EffectStudioError(f"{field_name} is outside the bounded range")
    return number


def _bounded_text(value: object, *, field_name: str, maximum: int) -> str:
    if type(value) is not str:
        raise EffectStudioError(f"{field_name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or not normalized.isprintable():
        raise EffectStudioError(f"{field_name} must be non-empty bounded text")
    return normalized


def _registry(value: object) -> EffectRegistry:
    if not isinstance(value, EffectRegistry):
        raise EffectStudioError("registry must be EffectRegistry")
    return value


def _require_effect(registry: EffectRegistry, effect_id: object) -> EffectDefinition:
    identifier = _bounded_text(effect_id, field_name="effect_id", maximum=160)
    effect = registry.get(identifier)
    if effect is None:
        raise EffectStudioError(f"unknown effect: {identifier}")
    return effect


def _semantic_family(effect: EffectDefinition) -> SemanticFamily:
    searchable = " ".join(
        (
            effect.identifier,
            effect.label,
            effect.description,
            effect.meaning,
            effect.catalog,
        )
    ).casefold()
    families = (
        (SemanticFamily.ASKING, ("asking", "attention", "question")),
        (SemanticFamily.FAILURE, ("failure", "failed", "error")),
        (SemanticFamily.RECOVERY, ("recovery", "recovered", "repair")),
        (SemanticFamily.COMPLETION, ("completion", "completed", "done")),
        (SemanticFamily.NOTIFICATION, ("notification", "new event")),
        (SemanticFamily.QUOTA, ("quota", "capacity", "reset")),
        (SemanticFamily.ENVIRONMENT, ("environment", "weather", "battery", "power")),
        (SemanticFamily.IDLE, ("idle", "steady", "no effect")),
        (SemanticFamily.TRANSITION, ("transition", "handoff", "rainbow")),
    )
    for family, markers in families:
        if any(marker in searchable for marker in markers):
            return family
    return SemanticFamily.WORKING


def _supported_surfaces(effect: EffectDefinition) -> tuple[StudioSurface, ...]:
    declared = frozenset(effect.surfaces)
    if "all" in declared:
        return tuple(StudioSurface)
    return tuple(
        surface
        for surface in StudioSurface
        if declared.intersection(_SURFACE_REGISTRY_KEYS[surface])
    )


@dataclass(frozen=True, slots=True)
class GalleryRow:
    effect_id: str
    label: str
    purpose: str
    semantic_family: SemanticFamily
    when_it_runs: str
    supported_surfaces: tuple[StudioSurface, ...]
    duration_seconds: float | None
    energy: str
    safety: str
    reduce_motion_effect_id: str
    parameters: tuple[str, ...]
    catalog: str


@dataclass(frozen=True, slots=True)
class GalleryPackProjection:
    """Read-only index entry derived from one validated community pack."""

    pack_id: str
    name: str
    effect_count: int
    effect_ids: tuple[str, ...]
    license_spdx_id: str | None
    license_label: str | None
    source_url: str | None
    attribution_url: str | None

    @property
    def license_source_url(self) -> str | None:
        return self.source_url

    @property
    def license_attribution_url(self) -> str | None:
        return self.attribution_url

    @property
    def license_metadata(self):
        """Return the pack's immutable license facts, when supplied."""
        return self.license

    @property
    def license(self):
        if self.license_spdx_id is None or self.license_label is None:
            return None
        return LicenseMetadata(
            spdx_id=self.license_spdx_id,
            label=self.license_label,
            source_url=self.source_url,
            attribution_url=self.attribution_url,
        )


MAX_GALLERY_PACKS = 128


def project_gallery_pack(
    pack: EffectPack | Mapping[str, Any],
) -> GalleryPackProjection:
    """Project one data-only pack into bounded gallery index metadata."""

    if not isinstance(pack, (EffectPack, Mapping)):
        raise EffectStudioError("pack must be an object")
    validated = validate_pack(pack)
    license_metadata = validated.license
    return GalleryPackProjection(
        pack_id=validated.pack_id,
        name=validated.name,
        effect_count=len(validated.effects),
        effect_ids=tuple(str(effect["id"]) for effect in validated.effects),
        license_spdx_id=(license_metadata.spdx_id if license_metadata else None),
        license_label=(license_metadata.label if license_metadata else None),
        source_url=(license_metadata.source_url if license_metadata else None),
        attribution_url=(
            license_metadata.attribution_url if license_metadata else None
        ),
    )


def build_gallery_index(
    packs: Iterable[EffectPack | Mapping[str, Any]],
) -> tuple[GalleryPackProjection, ...]:
    """Build a deterministic, read-only index from validated pack metadata."""

    if isinstance(packs, EffectPack):
        values = (packs,)
    elif isinstance(packs, Mapping):
        values = (packs,)
    elif isinstance(packs, (str, bytes)):
        raise EffectStudioError("packs must be a bounded iterable")
    else:
        try:
            values = tuple(islice(packs, MAX_GALLERY_PACKS + 1))
        except TypeError as error:
            raise EffectStudioError("packs must be a bounded iterable") from error
    if len(values) > MAX_GALLERY_PACKS:
        raise EffectStudioError("packs exceed the gallery bound")
    projections = tuple(project_gallery_pack(pack) for pack in values)
    identifiers = tuple(projection.pack_id for projection in projections)
    if len(set(identifiers)) != len(identifiers):
        raise EffectStudioError("duplicate gallery pack identifier")
    return tuple(
        sorted(projections, key=lambda projection: (projection.name.casefold(), projection.pack_id))
    )


def build_gallery_projection(
    packs: Iterable[EffectPack | Mapping[str, Any]],
) -> tuple[GalleryPackProjection, ...]:
    """Descriptive alias for callers treating the index as a projection."""

    return build_gallery_index(packs)


def build_gallery_rows(
    registry: EffectRegistry = EFFECT_REGISTRY,
    *,
    query: object = "",
    semantic_family: SemanticFamily | None = None,
) -> tuple[GalleryRow, ...]:
    """Project registry definitions into deterministic, searchable gallery rows."""

    selected_registry = _registry(registry)
    if type(query) is not str or len(query) > MAX_SEARCH_CHARACTERS:
        raise EffectStudioError("query must be bounded text")
    if semantic_family is not None and type(semantic_family) is not SemanticFamily:
        raise EffectStudioError("semantic_family must be SemanticFamily")
    needle = query.strip().casefold()
    rows: list[GalleryRow] = []
    for effect in selected_registry.list():
        family = _semantic_family(effect)
        if semantic_family is not None and family is not semantic_family:
            continue
        searchable = " ".join(
            (effect.identifier, effect.label, effect.description, effect.meaning, effect.catalog)
        ).casefold()
        if needle and needle not in searchable:
            continue
        rows.append(
            GalleryRow(
                effect_id=effect.identifier,
                label=effect.label,
                purpose=effect.description,
                semantic_family=family,
                when_it_runs=effect.meaning,
                supported_surfaces=_supported_surfaces(effect),
                duration_seconds=None,
                energy=effect.energy,
                safety=effect.safety,
                reduce_motion_effect_id=effect.reduce_motion_fallback or effect.identifier,
                parameters=effect.parameters,
                catalog=effect.catalog,
            )
        )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                _SEMANTIC_ORDER[row.semantic_family],
                row.label.casefold(),
                row.effect_id,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class SurfaceSimulation:
    surface: StudioSurface
    requested_effect_id: str
    rendered_effect_id: str
    supported: bool
    led_count: int
    reduce_motion: bool


def build_surface_simulations(
    effect_id: object,
    registry: EffectRegistry = EFFECT_REGISTRY,
    *,
    reduce_motion: object = False,
) -> tuple[SurfaceSimulation, ...]:
    """Describe all four Studio surfaces in their fixed side-by-side order."""

    selected_registry = _registry(registry)
    requested = _require_effect(selected_registry, effect_id)
    if type(reduce_motion) is not bool:
        raise EffectStudioError("reduce_motion must be bool")
    rendered = requested
    if reduce_motion:
        try:
            rendered = selected_registry.reduced_motion(requested.identifier)
        except KeyError as error:
            raise EffectStudioError("effect has an unknown reduced-motion fallback") from error
    supported = frozenset(_supported_surfaces(rendered))
    return tuple(
        SurfaceSimulation(
            surface=surface,
            requested_effect_id=requested.identifier,
            rendered_effect_id=rendered.identifier,
            supported=surface in supported,
            led_count=_SURFACE_LED_COUNTS[surface],
            reduce_motion=reduce_motion,
        )
        for surface in StudioSurface
    )


@dataclass(frozen=True, slots=True)
class SyntheticEvent:
    scenario: SyntheticScenario
    offset_seconds: float
    semantic_family: SemanticFamily
    agent_count: int

    def __post_init__(self) -> None:
        if type(self.scenario) is not SyntheticScenario:
            raise EffectStudioError("scenario must be SyntheticScenario")
        _bounded_number(
            self.offset_seconds,
            field_name="offset_seconds",
            minimum=0.0,
            maximum=MAX_SYNTHETIC_TIMELINE_SECONDS,
        )
        if self.semantic_family is not _SCENARIO_SEMANTICS[self.scenario]:
            raise EffectStudioError("synthetic event semantic does not match its scenario")
        if type(self.agent_count) is not int or self.agent_count != _SCENARIO_AGENT_COUNTS[self.scenario]:
            raise EffectStudioError("synthetic event agent count does not match its scenario")


@dataclass(frozen=True, slots=True)
class SyntheticTimeline:
    events: tuple[SyntheticEvent, ...]
    duration_seconds: float
    cursor_seconds: float
    paused: bool
    reduce_motion: bool
    color_vision_mode: ColorVisionMode

    def __post_init__(self) -> None:
        if type(self.events) is not tuple or not self.events or len(self.events) > MAX_SYNTHETIC_EVENTS:
            raise EffectStudioError("events must be a non-empty bounded tuple")
        if not all(type(event) is SyntheticEvent for event in self.events):
            raise EffectStudioError("events must contain SyntheticEvent values")
        offsets = tuple(event.offset_seconds for event in self.events)
        if offsets != tuple(sorted(offsets)) or len(set(offsets)) != len(offsets):
            raise EffectStudioError("event offsets must be strictly increasing")
        duration = _bounded_number(
            self.duration_seconds,
            field_name="duration_seconds",
            minimum=0.0,
            maximum=MAX_SYNTHETIC_TIMELINE_SECONDS,
        )
        cursor = _bounded_number(
            self.cursor_seconds,
            field_name="cursor_seconds",
            minimum=0.0,
            maximum=duration,
        )
        if duration != offsets[-1] or cursor != self.cursor_seconds:
            raise EffectStudioError("timeline duration or cursor does not match its events")
        if type(self.paused) is not bool or type(self.reduce_motion) is not bool:
            raise EffectStudioError("timeline flags must be bool")
        if type(self.color_vision_mode) is not ColorVisionMode:
            raise EffectStudioError("color_vision_mode must be ColorVisionMode")


def build_synthetic_timeline(
    scenarios: tuple[SyntheticScenario, ...] = DEFAULT_SYNTHETIC_SCENARIOS,
    *,
    step_seconds: object = 2.0,
    cursor_seconds: object = 0.0,
    paused: object = True,
    reduce_motion: object = False,
    color_vision_mode: ColorVisionMode = ColorVisionMode.STANDARD,
) -> SyntheticTimeline:
    """Build a bounded deterministic timeline without clocks or live events."""

    if type(scenarios) is not tuple or not scenarios or len(scenarios) > MAX_SYNTHETIC_EVENTS:
        raise EffectStudioError("scenarios must be a non-empty bounded tuple")
    if not all(type(scenario) is SyntheticScenario for scenario in scenarios):
        raise EffectStudioError("scenarios must contain SyntheticScenario values")
    step = _bounded_number(
        step_seconds,
        field_name="step_seconds",
        minimum=0.001,
        maximum=MAX_SYNTHETIC_TIMELINE_SECONDS,
    )
    duration = step * (len(scenarios) - 1)
    if duration > MAX_SYNTHETIC_TIMELINE_SECONDS:
        raise EffectStudioError("synthetic timeline exceeds its duration bound")
    cursor = _bounded_number(
        cursor_seconds,
        field_name="cursor_seconds",
        minimum=0.0,
        maximum=duration,
    )
    if type(paused) is not bool or type(reduce_motion) is not bool:
        raise EffectStudioError("timeline flags must be bool")
    if type(color_vision_mode) is not ColorVisionMode:
        raise EffectStudioError("color_vision_mode must be ColorVisionMode")
    events = tuple(
        SyntheticEvent(
            scenario=scenario,
            offset_seconds=step * index,
            semantic_family=_SCENARIO_SEMANTICS[scenario],
            agent_count=_SCENARIO_AGENT_COUNTS[scenario],
        )
        for index, scenario in enumerate(scenarios)
    )
    return SyntheticTimeline(
        events=events,
        duration_seconds=duration,
        cursor_seconds=cursor,
        paused=paused,
        reduce_motion=reduce_motion,
        color_vision_mode=color_vision_mode,
    )


@dataclass(frozen=True, slots=True)
class StudioSessionPlan:
    action: StudioSessionAction
    baseline_effect_id: str
    candidate_effect_id: str | None
    result_effect_id: str
    committed_colors: ColorSettings
    effective_colors: ColorSettings
    previewing: bool
    comparison_enabled: bool
    settings_write_required: bool


def _detached_color_settings(colors: ColorSettings) -> ColorSettings:
    """Copy a color-settings value without sharing its mutable mappings."""

    return replace(
        colors,
        mode_colors=dict(colors.mode_colors),
        agent_colors=dict(colors.agent_colors),
        session_colors=dict(colors.session_colors),
        fade_floor=dict(colors.fade_floor),
        fade_ceiling=dict(colors.fade_ceiling),
        mode_animation=dict(colors.mode_animation),
        provider_animation=dict(colors.provider_animation),
        speed_overrides=dict(colors.speed_overrides),
    )


def plan_session_action(
    session: object,
    action: StudioSessionAction,
    baseline_effect_id: object,
    candidate_effect_id: object | None = None,
    registry: EffectRegistry = EFFECT_REGISTRY,
) -> StudioSessionPlan:
    """Inspect a color preview session and describe one action without taking it."""

    if not isinstance(session, StudioPreviewSession):
        raise EffectStudioError("session must be StudioPreviewSession")
    if type(action) is not StudioSessionAction:
        raise EffectStudioError("action must be StudioSessionAction")
    selected_registry = _registry(registry)
    baseline = _require_effect(selected_registry, baseline_effect_id)
    candidate: EffectDefinition | None = None
    if action is StudioSessionAction.REVERT:
        if candidate_effect_id is not None:
            raise EffectStudioError("revert cannot name a candidate effect")
    else:
        candidate = _require_effect(selected_registry, candidate_effect_id)
    if not isinstance(session.committed, ColorSettings) or not isinstance(session.effective, ColorSettings):
        raise EffectStudioError("session contains invalid color settings")
    result = baseline.identifier if candidate is None else candidate.identifier
    return StudioSessionPlan(
        action=action,
        baseline_effect_id=baseline.identifier,
        candidate_effect_id=candidate.identifier if candidate is not None else None,
        result_effect_id=result,
        committed_colors=_detached_color_settings(session.committed),
        effective_colors=_detached_color_settings(session.effective),
        previewing=session.previewing,
        comparison_enabled=(
            action is StudioSessionAction.COMPARE
            and candidate is not None
            and (candidate.identifier != baseline.identifier or session.previewing)
        ),
        settings_write_required=action is StudioSessionAction.COMMIT,
    )


def plan_preview(
    session: object,
    baseline_effect_id: object,
    candidate_effect_id: object,
    registry: EffectRegistry = EFFECT_REGISTRY,
) -> StudioSessionPlan:
    return plan_session_action(
        session,
        StudioSessionAction.PREVIEW,
        baseline_effect_id,
        candidate_effect_id,
        registry,
    )


def plan_compare(
    session: object,
    baseline_effect_id: object,
    candidate_effect_id: object,
    registry: EffectRegistry = EFFECT_REGISTRY,
) -> StudioSessionPlan:
    return plan_session_action(
        session,
        StudioSessionAction.COMPARE,
        baseline_effect_id,
        candidate_effect_id,
        registry,
    )


def plan_commit(
    session: object,
    baseline_effect_id: object,
    candidate_effect_id: object,
    registry: EffectRegistry = EFFECT_REGISTRY,
) -> StudioSessionPlan:
    return plan_session_action(
        session,
        StudioSessionAction.COMMIT,
        baseline_effect_id,
        candidate_effect_id,
        registry,
    )


def plan_revert(
    session: object,
    baseline_effect_id: object,
    registry: EffectRegistry = EFFECT_REGISTRY,
) -> StudioSessionPlan:
    return plan_session_action(
        session,
        StudioSessionAction.REVERT,
        baseline_effect_id,
        None,
        registry,
    )


@dataclass(frozen=True, slots=True)
class PhysicalPreviewPlan:
    decision: PhysicalPreviewDecision
    effect_id: str
    device_id: str
    allowed: bool
    duration_seconds: float
    status_label: str
    release_triggers: tuple[str, ...]


def plan_physical_preview(
    effect_id: object,
    device_id: object,
    *,
    consent_granted: object,
    duration_seconds: object = 10.0,
    registry: EffectRegistry = EFFECT_REGISTRY,
) -> PhysicalPreviewPlan:
    """Guard a bounded hardware preview, returning data for a runtime owner."""

    effect = _require_effect(_registry(registry), effect_id)
    device = _bounded_text(
        device_id,
        field_name="device_id",
        maximum=MAX_ASSIGNMENT_TARGET_CHARACTERS,
    )
    if _OPAQUE_IDENTIFIER.fullmatch(device) is None:
        raise EffectStudioError("device_id must be an opaque identifier")
    if type(consent_granted) is not bool:
        raise EffectStudioError("consent_granted must be bool")
    duration = _bounded_number(
        duration_seconds,
        field_name="duration_seconds",
        minimum=0.001,
        maximum=MAX_PHYSICAL_PREVIEW_SECONDS,
    )
    if not consent_granted:
        return PhysicalPreviewPlan(
            decision=PhysicalPreviewDecision.CONSENT_REQUIRED,
            effect_id=effect.identifier,
            device_id=device,
            allowed=False,
            duration_seconds=0.0,
            status_label="Physical preview requires consent",
            release_triggers=(),
        )
    return PhysicalPreviewPlan(
        decision=PhysicalPreviewDecision.ALLOWED,
        effect_id=effect.identifier,
        device_id=device,
        allowed=True,
        duration_seconds=duration,
        status_label="Previewing, not saved",
        release_triggers=PHYSICAL_PREVIEW_RELEASE_TRIGGERS,
    )


@dataclass(frozen=True, slots=True)
class EffectAssignmentPlan:
    effect_id: str
    scope: AssignmentScope
    target_id: str | None
    scene_policy: ScenePolicy | None
    settings_write_required: bool = True


def _assignment_target(scope: AssignmentScope, target_id: object) -> tuple[str | None, ScenePolicy | None]:
    if scope is AssignmentScope.GLOBAL:
        if target_id is not None:
            raise EffectStudioError("global assignment cannot name a target")
        return None, None
    target = _bounded_text(
        target_id,
        field_name="target_id",
        maximum=MAX_ASSIGNMENT_TARGET_CHARACTERS,
    )
    if scope is AssignmentScope.SEMANTIC:
        if target not in {family.value for family in SemanticFamily}:
            raise EffectStudioError("semantic target is unknown")
        return target, None
    if scope is AssignmentScope.SCENE:
        scene = scene_from_value(target)
        policy = policy_for_scene(scene)
        if scene is None or policy is None:
            raise EffectStudioError("scene target is unknown")
        return scene.value, policy
    if _OPAQUE_IDENTIFIER.fullmatch(target) is None:
        raise EffectStudioError("assignment target must be an opaque identifier")
    return target, None


def plan_assignment(
    effect_id: object,
    scope: object,
    target_id: object = None,
    registry: EffectRegistry = EFFECT_REGISTRY,
) -> EffectAssignmentPlan:
    """Describe one scoped assignment without changing saved settings."""

    effect = _require_effect(_registry(registry), effect_id)
    if type(scope) is not AssignmentScope:
        raise EffectStudioError("scope must be AssignmentScope")
    target, scene_policy = _assignment_target(scope, target_id)
    return EffectAssignmentPlan(
        effect_id=effect.identifier,
        scope=scope,
        target_id=target,
        scene_policy=scene_policy,
    )


@dataclass(frozen=True, slots=True)
class PackImportPlan:
    pack_id: str
    name: str
    version: int
    effect_ids: tuple[str, ...]
    registry_write_required: bool = True
    license_metadata: LicenseMetadata | None = None


@dataclass(frozen=True, slots=True)
class PackExportPlan:
    pack_id: str
    name: str
    version: int
    effect_count: int
    payload: bytes
    license_metadata: LicenseMetadata | None = None


def _validated_pack(pack: EffectPack | Mapping[str, Any]) -> tuple[EffectPack, bytes]:
    if not isinstance(pack, (EffectPack, Mapping)):
        raise EffectPackError("pack must be an object")
    payload = export_pack(pack)
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EffectPackError("pack export is not valid JSON data") from error
    if not isinstance(decoded, Mapping):
        raise EffectPackError("pack must be an object")
    return validate_pack(decoded), payload


def plan_pack_import(
    pack: EffectPack | Mapping[str, Any],
    registry: EffectRegistry = EFFECT_REGISTRY,
) -> PackImportPlan:
    """Validate a pack and collisions without extending the registry."""

    selected_registry = _registry(registry)
    validated, _payload = _validated_pack(pack)
    definitions = effect_definitions_from_pack(validated)
    existing = selected_registry.as_mapping()
    for definition in definitions:
        if definition.identifier in existing:
            raise EffectPackError(
                f"effect identifier already registered: {definition.identifier}"
            )
    return PackImportPlan(
        pack_id=validated.pack_id,
        name=validated.name,
        version=validated.version,
        effect_ids=tuple(definition.identifier for definition in definitions),
        license_metadata=validated.license,
    )


def plan_pack_export(pack: EffectPack | Mapping[str, Any]) -> PackExportPlan:
    """Return canonical bytes for a caller to export, without doing file I/O."""

    validated, payload = _validated_pack(pack)
    return PackExportPlan(
        pack_id=validated.pack_id,
        name=validated.name,
        version=validated.version,
        effect_count=len(validated.effects),
        payload=payload,
        license_metadata=validated.license,
    )


@dataclass(frozen=True, slots=True)
class SuppressedSignal:
    semantic_family: SemanticFamily
    count: int

    def __post_init__(self) -> None:
        if type(self.semantic_family) is not SemanticFamily:
            raise EffectStudioError("semantic_family must be SemanticFamily")
        if type(self.count) is not int or not 0 < self.count <= MAX_SUPPRESSED_SIGNAL_COUNT:
            raise EffectStudioError("suppressed signal count is outside the bounded range")


@dataclass(frozen=True, slots=True)
class WhyEffectProjection:
    effect_id: str
    meaning: str
    semantic_family: SemanticFamily
    source_freshness: SourceFreshness
    source_age_seconds: float | None
    priority: int
    suppressed_signals: tuple[SuppressedSignal, ...]
    policy_decisions: tuple[PolicyDecision, ...]
    expires_in_seconds: float | None


def project_why_effect(
    effect_id: object,
    *,
    source_age_seconds: object = None,
    priority: object,
    suppressed_signals: object = (),
    policy_decisions: object = (),
    expires_in_seconds: object = None,
    registry: EffectRegistry = EFFECT_REGISTRY,
) -> WhyEffectProjection:
    """Project bounded registry and routing facts with no message content."""

    effect = _require_effect(_registry(registry), effect_id)
    if source_age_seconds is None:
        age: float | None = None
        freshness = SourceFreshness.UNAVAILABLE
    else:
        age = _bounded_number(
            source_age_seconds,
            field_name="source_age_seconds",
            minimum=0.0,
            maximum=MAX_SOURCE_AGE_SECONDS,
        )
        freshness = (
            SourceFreshness.FRESH
            if age <= FRESH_SOURCE_AGE_SECONDS
            else SourceFreshness.STALE
        )
    if type(priority) is not int or not 0 <= priority <= 99:
        raise EffectStudioError("priority is outside the bounded range")
    if (
        type(suppressed_signals) is not tuple
        or len(suppressed_signals) > MAX_SUPPRESSED_SIGNALS
        or not all(type(signal) is SuppressedSignal for signal in suppressed_signals)
    ):
        raise EffectStudioError("suppressed_signals must be a bounded typed tuple")
    signal_families = tuple(signal.semantic_family for signal in suppressed_signals)
    if len(set(signal_families)) != len(signal_families):
        raise EffectStudioError("suppressed signal families must be unique")
    if (
        type(policy_decisions) is not tuple
        or len(policy_decisions) > MAX_POLICY_DECISIONS
        or not all(type(decision) is PolicyDecision for decision in policy_decisions)
        or len(set(policy_decisions)) != len(policy_decisions)
    ):
        raise EffectStudioError("policy_decisions must be a bounded unique typed tuple")
    if expires_in_seconds is None:
        expiration: float | None = None
    else:
        expiration = _bounded_number(
            expires_in_seconds,
            field_name="expires_in_seconds",
            minimum=0.0,
            maximum=MAX_EFFECT_EXPIRATION_SECONDS,
        )
    return WhyEffectProjection(
        effect_id=effect.identifier,
        meaning=effect.meaning,
        semantic_family=_semantic_family(effect),
        source_freshness=freshness,
        source_age_seconds=age,
        priority=priority,
        suppressed_signals=suppressed_signals,
        policy_decisions=policy_decisions,
        expires_in_seconds=expiration,
    )


__all__ = [
    "DEFAULT_SYNTHETIC_SCENARIOS",
    "FRESH_SOURCE_AGE_SECONDS",
    "MAX_ASSIGNMENT_TARGET_CHARACTERS",
    "MAX_EFFECT_EXPIRATION_SECONDS",
    "MAX_GALLERY_PACKS",
    "MAX_PHYSICAL_PREVIEW_SECONDS",
    "MAX_POLICY_DECISIONS",
    "MAX_SEARCH_CHARACTERS",
    "MAX_SOURCE_AGE_SECONDS",
    "MAX_SUPPRESSED_SIGNALS",
    "MAX_SUPPRESSED_SIGNAL_COUNT",
    "MAX_SYNTHETIC_EVENTS",
    "MAX_SYNTHETIC_TIMELINE_SECONDS",
    "PHYSICAL_PREVIEW_RELEASE_TRIGGERS",
    "AssignmentScope",
    "ColorVisionMode",
    "EffectAssignmentPlan",
    "EffectStudioError",
    "GalleryPackProjection",
    "GalleryRow",
    "PackExportPlan",
    "PackImportPlan",
    "PhysicalPreviewDecision",
    "PhysicalPreviewPlan",
    "PolicyDecision",
    "SemanticFamily",
    "SourceFreshness",
    "StudioSessionAction",
    "StudioSessionPlan",
    "StudioSurface",
    "SuppressedSignal",
    "SurfaceSimulation",
    "SyntheticEvent",
    "SyntheticScenario",
    "SyntheticTimeline",
    "WhyEffectProjection",
    "build_gallery_index",
    "build_gallery_projection",
    "build_gallery_rows",
    "build_surface_simulations",
    "build_synthetic_timeline",
    "plan_assignment",
    "plan_commit",
    "plan_compare",
    "plan_pack_export",
    "plan_pack_import",
    "plan_physical_preview",
    "plan_preview",
    "plan_revert",
    "plan_session_action",
    "project_gallery_pack",
    "project_why_effect",
]
