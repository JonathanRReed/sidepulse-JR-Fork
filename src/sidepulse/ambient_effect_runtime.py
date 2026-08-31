"""Narrow runtime bridge for semantic effects, Glance Light, and history.

The pure effect modules own policy. This adapter installs at the application
composition boundary, observes successful content-free notification delivery,
and publishes the resulting immutable projections on the controller. It never
changes whether a notification is delivered.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from .accessibility_display import AccessibilityDisplayPreferences
from .ambient_effect_dispatch import (
    AmbientEffectDispatch,
    AmbientEffectFamily,
    AmbientEffectSurface,
    AmbientEffectSurfaceOutput,
    AmbientSemanticColors,
    compile_ambient_effect_dispatch,
)
from .announcer_stack import announcer_alert_identity
from .ask_heartbeat_sync import (
    AskHeartbeatPlan,
    AskHeartbeatPresentation,
    plan_ask_heartbeat_sync,
)
from .clear_agents import CompletionPresentationKey
from .completion_meniscus import (
    CompletionMeniscusGeometry,
    CompletionMeniscusPlan,
    CompletionMeniscusSurface,
    SelectedUnseenCompletionEvidence,
    plan_completion_meniscus,
)
from .courtesy_signatures import (
    CourtesySemantic,
    CourtesySignaturePlan,
    plan_courtesy_signature,
)
from .dnd_policy import DisplayAdmission
from .dot_binary_heartbeat import (
    DotBinaryHeartbeatPlan,
    DotSecondaryPolicy,
    plan_dot_binary_heartbeat,
)
from .effect_assignment_store import (
    EffectAssignmentCache,
    EffectAssignmentContext,
    load_effect_assignments,
    resolve_effect_assignment,
)
from .effect_history import (
    EffectAcknowledgementSource,
    EffectEvent,
    EffectHistory,
    EffectOutcome,
    EffectSemanticCategory,
    EffectSurface,
    record_effect_events,
)
from .effect_history_store import (
    default_effect_history_path,
    load_effect_history,
    save_effect_history,
)
from .effect_pack_store import EffectPackStore, EffectPackStoreError
from .effect_packs import EffectPackError, registry_with_pack
from .effect_registry import EFFECT_REGISTRY, EffectRegistry
from .effect_studio import PolicyDecision, project_why_effect
from .finite_effect_policy import plan_finite_effect
from .firefly_completion import (
    FireflyCompletionEvidence,
    FireflyCompletionPlan,
    plan_firefly_completion,
)
from .fleet_arrival_departure import (
    FleetArrivalDepartureCue,
    FleetArrivalDepartureState,
    RemoteMachineLiveness,
    TrustedRemoteMachineLiveness,
    observe_fleet_arrival_departure,
)
from .fleet_bands import FleetBand, FleetMember, FleetPlan, plan_fleet_bands
from .glance_light import (
    MAX_GLANCE_NOTIFICATIONS,
    GlanceDestination,
    GlanceEnvironment,
    GlanceKind,
    GlanceLightPlan,
    GlanceLightState,
    acknowledge_glance_notification,
    expire_glance_notifications,
    make_glance_notification,
    plan_glance_light,
    restore_glance_light_document,
    serialize_glance_light_document,
)
from .handoff_baton import HandoffBatonPlan, HandoffEndpoint, plan_handoff_baton
from .milestone_odometer import (
    MilestoneOdometerPlan,
    MilestoneOdometerPreferences,
    MilestoneOdometerState,
    plan_milestone_odometer,
)
from .operator_state import (
    TIMING_RECOVERY_CONFIRMATIONS,
    CanonicalOperatorEvent,
    CanonicalOperatorState,
    InterruptionClass,
    RequestPhase,
    TransitionKind,
)
from .private_io import atomic_private_write, read_private_text
from .provider_facts import (
    SourceFreshness,
    SourceHealth,
    WorkKey,
    WorkLifecycle,
    work_key_to_payload,
)
from .rainstick_idle import (
    RainstickIdlePlan,
    RainstickThermalState,
    plan_rainstick_idle,
)
from .recovery_grace_note import (
    ConfirmedRecoveryEvidence,
    RecoveryGraceIdentity,
    RecoveryGracePlan,
    plan_recovery_grace_note,
)
from .scenes import DEFAULT_SCENE, Scene, scene_from_value
from .semantic_effect_router import (
    DEFAULT_SEMANTIC_EFFECT_MAP,
    SEMANTIC_PRIORITY,
    CourtesySuppression,
    SemanticEffectAssignment,
    SemanticEffectCandidate,
    SemanticEffectMap,
    SemanticEffectSelection,
    SemanticEventKind,
    route_semantic_effects,
)
from .state_paths import default_state_dir
from .turn_length_ember import (
    ThermalState,
    TurnLengthEmberPlan,
    plan_turn_length_ember,
)

GLANCE_LIGHT_STORE_NAME = "glance-light.json"
_GLANCE_HISTORY_SURFACES = {
    GlanceDestination.DOT: EffectSurface.DOT,
    GlanceDestination.PRO_ENDPOINT: EffectSurface.PRO_ENDPOINT,
    GlanceDestination.SCREEN_BAR_ORB: EffectSurface.SCREEN_BAR_ORB,
    GlanceDestination.MENU_ACCENT: EffectSurface.MENU_ACCENT,
}


@dataclass(frozen=True, slots=True)
class AmbientEffectRuntimeReceipt:
    controller: type
    delivery_method: str = "_deliver_semantic_notification"
    activation_method: str = "_activate_notification_action"
    event_observer_method: str = "observe_operator_history_events"


@dataclass(frozen=True, slots=True)
class _AmbientDeviceEffectRoute:
    candidate: SemanticEffectCandidate
    scene: Scene
    display_admission: DisplayAdmission
    reduce_motion: bool
    assignment_context: EffectAssignmentContext
    global_effect_identifier: str

    def __post_init__(self) -> None:
        if (
            type(self.candidate) is not SemanticEffectCandidate
            or type(self.scene) is not Scene
            or type(self.display_admission) is not DisplayAdmission
            or type(self.reduce_motion) is not bool
            or type(self.assignment_context) is not EffectAssignmentContext
            or self.assignment_context.device_id is not None
            or type(self.global_effect_identifier) is not str
            or not self.global_effect_identifier
        ):
            raise ValueError("invalid ambient device effect route")


_receipts: dict[type, AmbientEffectRuntimeReceipt] = {}


def _typed_plan(value: object, expected: type):
    return value if type(value) is expected else None


def _decision_plan(value: object, expected: type):
    return _typed_plan(getattr(value, "plan", None), expected)


def _ambient_colors(controller: object) -> AmbientSemanticColors:
    colors = getattr(getattr(controller, "settings", None), "colors", None)
    mode_color = getattr(colors, "mode_color", None)
    defaults = AmbientSemanticColors()

    def color(key: str, fallback: str) -> str:
        if not callable(mode_color):
            return fallback
        try:
            value = mode_color(key)
        except Exception:
            return fallback
        return value if type(value) is str else fallback

    return AmbientSemanticColors(
        ask=color("ask", defaults.ask),
        failure=color("ask", defaults.failure),
        notification=color("done", defaults.notification),
        handoff=color("working", defaults.handoff),
        work=color("working", defaults.work),
        completion=color("done", defaults.completion),
        recovery=color("done", defaults.recovery),
        environment=color("idle", defaults.environment),
        idle=color("idle", defaults.idle),
    )


def _first_screen_meniscus(controller: object) -> CompletionMeniscusPlan | None:
    plans = getattr(controller, "_completion_meniscus_plans", ())
    if type(plans) is not tuple:
        return None
    return next(
        (
            plan
            for plan in plans
            if type(plan) is CompletionMeniscusPlan
            and plan.geometry.surface is CompletionMeniscusSurface.SCREEN_BAR
        ),
        None,
    )


def _latest_fleet_cue(controller: object) -> FleetArrivalDepartureCue | None:
    decisions = getattr(controller, "_fleet_arrival_departure_decisions", ())
    if type(decisions) is not tuple:
        return None
    return next(
        (
            cue
            for decision in reversed(decisions)
            if type(cue := getattr(decision, "cue", None)) is FleetArrivalDepartureCue
        ),
        None,
    )


def _merge_runtime_dispatch(
    controller: object,
    proposed: AmbientEffectDispatch,
    *,
    now: float,
) -> tuple[AmbientEffectDispatch, dict[AmbientEffectSurface, float]]:
    previous = getattr(controller, "_ambient_effect_dispatch", None)
    prior_times = getattr(
        controller,
        "_ambient_effect_dispatch_started_at_by_surface",
        {},
    )
    fallback_started_at = getattr(
        controller,
        "_ambient_effect_dispatch_started_at",
        None,
    )
    outputs = []
    started_at_by_surface = {}
    for surface in AmbientEffectSurface:
        candidate = proposed.for_surface(surface)
        prior = (
            previous.for_surface(surface)
            if type(previous) is AmbientEffectDispatch
            else None
        )
        prior_started_at = (
            prior_times.get(surface, fallback_started_at)
            if isinstance(prior_times, dict)
            else fallback_started_at
        )
        prior_active = (
            prior is not None
            and type(prior_started_at) in {int, float}
            and max(0.0, (now - float(prior_started_at)) * 1_000.0)
            < prior.expires_after_ms
        )
        preserve_prior = prior_active and (
            candidate is None
            or prior.priority > candidate.priority
            or (
                prior.priority == candidate.priority
                and prior.effect_identity == candidate.effect_identity
                and prior.program == candidate.program
            )
        )
        selected = prior if preserve_prior else candidate
        if selected is None:
            continue
        outputs.append(selected)
        started_at_by_surface[surface] = (
            float(prior_started_at) if preserve_prior else now
        )
    return (
        AmbientEffectDispatch(tuple(outputs), proposed.suppressed),
        started_at_by_surface,
    )


def _compile_runtime_dispatch(controller: object) -> None:
    semantic_selection = _typed_plan(
        getattr(controller, "_semantic_effect_selection", None),
        SemanticEffectSelection,
    )
    proposed = compile_ambient_effect_dispatch(
        semantic_selection=semantic_selection,
        glance_light=_typed_plan(
            getattr(controller, "_glance_light_plan", None),
            GlanceLightPlan,
        ),
        firefly_completion=_decision_plan(
            getattr(controller, "_firefly_completion_decision", None),
            FireflyCompletionPlan,
        ),
        completion_meniscus=_first_screen_meniscus(controller),
        handoff_baton=_decision_plan(
            getattr(controller, "_handoff_baton_decision", None),
            HandoffBatonPlan,
        ),
        recovery_grace=_typed_plan(
            getattr(controller, "_recovery_grace_plan", None),
            RecoveryGracePlan,
        ),
        ask_heartbeat=_typed_plan(
            getattr(controller, "_ask_heartbeat_plan", None),
            AskHeartbeatPlan,
        ),
        turn_length_ember=_typed_plan(
            getattr(controller, "_turn_length_ember_plan", None),
            TurnLengthEmberPlan,
        ),
        rainstick_idle=_typed_plan(
            getattr(controller, "_rainstick_idle_plan", None),
            RainstickIdlePlan,
        ),
        dot_binary_heartbeat=_typed_plan(
            getattr(controller, "_dot_binary_heartbeat_plan", None),
            DotBinaryHeartbeatPlan,
        ),
        milestone_odometer=_typed_plan(
            getattr(controller, "_milestone_odometer_plan", None),
            MilestoneOdometerPlan,
        ),
        fleet_arrival_departure=_latest_fleet_cue(controller),
        courtesy_signature=_typed_plan(
            getattr(controller, "_courtesy_signature_plan", None),
            CourtesySignaturePlan,
        ),
        semantic_colors=_ambient_colors(controller),
    )
    now = time.monotonic()
    dispatch, started_at_by_surface = _merge_runtime_dispatch(
        controller,
        proposed,
        now=now,
    )
    setattr(controller, "_ambient_effect_dispatch", dispatch)
    setattr(controller, "_ambient_effect_dispatch_started_at", now)
    setattr(
        controller,
        "_ambient_effect_dispatch_started_at_by_surface",
        started_at_by_surface,
    )
    pending_device_route = getattr(
        controller,
        "_ambient_device_effect_route_pending",
        None,
    )
    if (
        type(pending_device_route) is _AmbientDeviceEffectRoute
        and semantic_selection is not None
        and any(
            output.family is AmbientEffectFamily.SEMANTIC_SELECTION
            and output.semantic is pending_device_route.candidate.semantic
            and output.effect_identity
            == pending_device_route.global_effect_identifier
            for output in dispatch.outputs
        )
    ):
        setattr(
            controller,
            "_ambient_device_effect_route",
            pending_device_route,
        )
    setattr(controller, "_ambient_device_effect_route_pending", None)
    for attribute in (
        "_semantic_effect_selection",
        "_glance_light_plan",
        "_firefly_completion_decision",
        "_completion_meniscus_plans",
        "_handoff_baton_decision",
        "_recovery_grace_plan",
        "_milestone_odometer_plan",
        "_fleet_arrival_departure_decisions",
        "_courtesy_signature_plan",
    ):
        setattr(controller, attribute, None)


def active_ambient_surface_output(
    controller: object,
    surface: AmbientEffectSurface,
    *,
    now_monotonic: float | None = None,
) -> tuple[AmbientEffectSurfaceOutput, float] | None:
    """Return one unexpired staged output without mutating its owner."""

    dispatch = getattr(controller, "_ambient_effect_dispatch", None)
    started_at_by_surface = getattr(
        controller,
        "_ambient_effect_dispatch_started_at_by_surface",
        {},
    )
    started_at = (
        started_at_by_surface.get(
            surface,
            getattr(controller, "_ambient_effect_dispatch_started_at", None),
        )
        if isinstance(started_at_by_surface, dict)
        else getattr(controller, "_ambient_effect_dispatch_started_at", None)
    )
    if type(dispatch) is not AmbientEffectDispatch or type(started_at) not in {
        int,
        float,
    }:
        return None
    output = dispatch.for_surface(surface)
    if output is None:
        return None
    now = time.monotonic() if now_monotonic is None else float(now_monotonic)
    elapsed_ms = max(0.0, (now - float(started_at)) * 1_000.0)
    if elapsed_ms >= output.expires_after_ms:
        return None
    return output, float(started_at)


def _active_semantic_route_anchor(
    controller: object,
    route: _AmbientDeviceEffectRoute,
    *,
    now_monotonic: float | None,
) -> tuple[AmbientEffectSurfaceOutput, float] | None:
    for surface in AmbientEffectSurface:
        active = active_ambient_surface_output(
            controller,
            surface,
            now_monotonic=now_monotonic,
        )
        if active is None:
            continue
        output, started_at = active
        if (
            output.family is AmbientEffectFamily.SEMANTIC_SELECTION
            and output.semantic is route.candidate.semantic
            and output.effect_identity == route.global_effect_identifier
        ):
            return output, started_at
    return None


def active_device_ambient_surface_output(
    controller: object,
    surface: AmbientEffectSurface,
    *,
    device_id: str,
    now_monotonic: float | None = None,
) -> tuple[AmbientEffectSurfaceOutput, float] | None:
    """Resolve one hardware assignment from the runtime cache without mutation."""

    global_output = active_ambient_surface_output(
        controller,
        surface,
        now_monotonic=now_monotonic,
    )
    route = getattr(controller, "_ambient_device_effect_route", None)
    cache = getattr(controller, "_effect_assignment_cache", None)
    if type(route) is not _AmbientDeviceEffectRoute or not isinstance(
        cache,
        EffectAssignmentCache,
    ):
        return global_output
    anchor = _active_semantic_route_anchor(
        controller,
        route,
        now_monotonic=now_monotonic,
    )
    if anchor is None:
        return global_output
    try:
        context = EffectAssignmentContext(
            semantic=route.assignment_context.semantic,
            scene=route.assignment_context.scene,
            provider_id=route.assignment_context.provider_id,
            provider_instance_id=route.assignment_context.provider_instance_id,
            project_id=route.assignment_context.project_id,
            device_id=device_id,
        )
        effect_map, registry = _effect_map_for_assignment_context(cache, context)
        selection = route_semantic_effects(
            (route.candidate,),
            scene=route.scene,
            display_admission=route.display_admission,
            effect_map=effect_map,
            reduce_motion=route.reduce_motion,
            registry=registry,
        )
        device_output = compile_ambient_effect_dispatch(
            semantic_selection=selection,
            semantic_colors=_ambient_colors(controller),
        ).for_surface(surface)
    except (TypeError, ValueError):
        return global_output
    if device_output is None:
        return global_output
    if global_output is not None:
        current = global_output[0]
        if (
            current.family is not AmbientEffectFamily.SEMANTIC_SELECTION
            and (current.priority, current.family.value)
            >= (device_output.priority, device_output.family.value)
        ):
            return global_output
    return device_output, anchor[1]


def _glance_store_path() -> Path:
    return default_state_dir() / GLANCE_LIGHT_STORE_NAME


def _load_glance_state(path: Path) -> GlanceLightState:
    try:
        raw = read_private_text(path, max_bytes=64 * 1024)
    except OSError:
        return GlanceLightState()
    return restore_glance_light_document(raw) or GlanceLightState()


def _save_glance_state(path: Path, state: GlanceLightState) -> Path:
    document = serialize_glance_light_document(state)
    if document is None:
        raise ValueError("invalid Glance Light state")
    return atomic_private_write(path, f"{document}\n")


def _notification_id(event_key: object, prefix: str) -> str:
    digest = hashlib.sha256(repr(event_key).encode("utf-8")).hexdigest()[:24]
    return f"glance.{prefix}.{digest}"


def _semantic_kind(
    event_key: object,
    interruption_class: InterruptionClass,
    prefix: str,
) -> SemanticEventKind:
    if getattr(event_key, "transition_kind", None) is TransitionKind.FAILED:
        return SemanticEventKind.FAILURE
    if interruption_class is InterruptionClass.ACTION_REQUIRED:
        return SemanticEventKind.ASK
    if prefix == "completion":
        return SemanticEventKind.COMPLETION
    return SemanticEventKind.NOTIFICATION


def _glance_kind(semantic: SemanticEventKind) -> GlanceKind:
    return {
        SemanticEventKind.ASK: GlanceKind.UNANSWERED_ASK,
        SemanticEventKind.FAILURE: GlanceKind.FAILURE,
        SemanticEventKind.COMPLETION: GlanceKind.COMPLETED_UNSEEN,
    }.get(semantic, GlanceKind.INFORMATIONAL)


def _history_category(semantic: SemanticEventKind) -> EffectSemanticCategory:
    return {
        SemanticEventKind.ASK: EffectSemanticCategory.ATTENTION,
        SemanticEventKind.FAILURE: EffectSemanticCategory.FAILURE,
        SemanticEventKind.COMPLETION: EffectSemanticCategory.COMPLETION,
        SemanticEventKind.RECOVERY: EffectSemanticCategory.RECOVERY,
        SemanticEventKind.HANDOFF: EffectSemanticCategory.HANDOFF,
    }.get(semantic, EffectSemanticCategory.NOTIFICATION)


def _environment(controller: object) -> tuple[DisplayAdmission, GlanceEnvironment]:
    projection = None
    current_dnd = getattr(controller, "current_dnd_projection", None)
    if callable(current_dnd):
        try:
            projection = current_dnd()
        except Exception:
            projection = None
    admission = getattr(projection, "display_admission", DisplayAdmission.ALL)
    if type(admission) is not DisplayAdmission:
        admission = DisplayAdmission.ALL
    return admission, GlanceEnvironment(
        dnd_active=admission is not DisplayAdmission.ALL,
        dim_asks_in_dnd=admission is DisplayAdmission.ASKS,
        low_power=bool(getattr(controller, "_ambient_low_power", False)),
        serious_thermal=bool(getattr(controller, "_ambient_serious_thermal", False)),
    )


def _history(controller: object) -> EffectHistory:
    current = getattr(controller, "_effect_history", None)
    if type(current) is EffectHistory:
        return current
    restored = load_effect_history().history
    setattr(controller, "_effect_history", restored)
    return restored


def _glance_state(controller: object) -> GlanceLightState:
    current = getattr(controller, "_glance_light_state", None)
    if type(current) is GlanceLightState:
        return current
    restored = _load_glance_state(_glance_store_path())
    setattr(controller, "_glance_light_state", restored)
    return restored


def _queue_persistence(controller: object) -> None:
    writer = getattr(controller, "_persistence_writer", None)
    submit = getattr(writer, "submit", None)
    if not callable(submit):
        return
    history = getattr(controller, "_effect_history", None)
    glance_state = getattr(controller, "_glance_light_state", None)
    try:
        if type(history) is EffectHistory:
            submit(
                "effect-history",
                lambda value=history: save_effect_history(
                    default_effect_history_path(), value
                ),
                replace_pending=True,
            )
        if type(glance_state) is GlanceLightState:
            submit(
                "glance-light",
                lambda value=glance_state: _save_glance_state(
                    _glance_store_path(), value
                ),
                replace_pending=True,
            )
    except Exception:
        return


def _upsert_glance_state(
    state: GlanceLightState,
    notification,
    *,
    now: float,
) -> GlanceLightState:
    live = expire_glance_notifications(state, now_epoch=now)
    retained = tuple(
        item
        for item in live.notifications
        if item.notification_id != notification.notification_id
    )
    return GlanceLightState(
        (notification, *retained)[:MAX_GLANCE_NOTIFICATIONS]
    )


def _work_key_for_event(event_key: object) -> WorkKey | None:
    subject = getattr(event_key, "subject_key", None)
    if type(subject) is WorkKey:
        return subject
    candidate = getattr(subject, "work_key", None)
    return candidate if type(candidate) is WorkKey else None


def _project_id_for_work(controller: object, work_key: WorkKey | None) -> str | None:
    if work_key is None:
        return None
    statuses = getattr(getattr(controller, "last_snapshot", None), "statuses", ())
    if type(statuses) is not tuple:
        return None
    status = next(
        (item for item in statuses if getattr(item, "work_key", None) == work_key),
        None,
    )
    value = getattr(status, "origin", None)
    return value if type(value) is str and value and len(value) <= 160 else None


def _effect_map_for_assignment_context(
    cache: EffectAssignmentCache,
    context: EffectAssignmentContext,
) -> tuple[SemanticEffectMap, EffectRegistry]:
    assignment = resolve_effect_assignment(cache.snapshot(), context)
    registry = cache.registry()
    if assignment is None or registry.get(assignment.effect_id) is None:
        return DEFAULT_SEMANTIC_EFFECT_MAP, registry
    assignments = tuple(
        SemanticEffectAssignment(
            current.semantic,
            assignment.effect_id
            if current.semantic is context.semantic
            else current.effect_identifier,
        )
        for current in DEFAULT_SEMANTIC_EFFECT_MAP.assignments
    )
    return SemanticEffectMap(
        assignments,
        DEFAULT_SEMANTIC_EFFECT_MAP.scene_assignments,
    ), registry


def _assignment_context(
    controller: object,
    event_key: object,
    *,
    semantic: SemanticEventKind,
    scene: Scene,
    device_id: str | None = None,
) -> EffectAssignmentContext:
    work_key = _work_key_for_event(event_key)
    source = getattr(work_key, "source_key", None)
    provider_id = getattr(source, "provider_id", None)
    instance_id = getattr(source, "source_instance_id", None)
    provider_instance_id = (
        f"{provider_id}:{instance_id}"
        if type(provider_id) is str and type(instance_id) is str
        else None
    )
    return EffectAssignmentContext(
        semantic=semantic,
        scene=scene,
        provider_id=provider_id if type(provider_id) is str else None,
        provider_instance_id=provider_instance_id,
        project_id=_project_id_for_work(controller, work_key),
        device_id=device_id,
    )


def _assigned_effect_map(
    controller: object,
    context: EffectAssignmentContext,
) -> tuple[SemanticEffectMap, EffectRegistry]:
    cache = getattr(controller, "_effect_assignment_cache", None)
    if not isinstance(cache, EffectAssignmentCache):
        return DEFAULT_SEMANTIC_EFFECT_MAP, EFFECT_REGISTRY
    try:
        return _effect_map_for_assignment_context(cache, context)
    except (TypeError, ValueError):
        return DEFAULT_SEMANTIC_EFFECT_MAP, EFFECT_REGISTRY


def _record_delivery(
    controller: object,
    *,
    event_key: object,
    interruption_class: InterruptionClass,
    prefix: str,
    action_token: str | None,
) -> None:
    now = time.time()
    notification_id = _notification_id(event_key, prefix)
    semantic = _semantic_kind(event_key, interruption_class, prefix)
    admission, glance_environment = _environment(controller)
    settings = getattr(controller, "settings", None)
    scene = scene_from_value(getattr(settings, "active_scene", None)) or DEFAULT_SCENE
    accessibility = getattr(controller, "_accessibility_display_preferences", None)
    reduce_motion = bool(getattr(accessibility, "reduce_motion", False))
    assignment_context = _assignment_context(
        controller,
        event_key,
        semantic=semantic,
        scene=scene,
    )
    effect_map, registry = _assigned_effect_map(controller, assignment_context)
    candidate = SemanticEffectCandidate(notification_id, semantic)
    selection = route_semantic_effects(
        (candidate,),
        scene=scene,
        display_admission=admission,
        effect_map=effect_map,
        reduce_motion=reduce_motion,
        registry=registry,
    )
    setattr(controller, "_semantic_effect_selection", selection)
    if selection.winner is None or selection.registry_effect_identifier is None:
        return
    setattr(
        controller,
        "_ambient_device_effect_route_pending",
        _AmbientDeviceEffectRoute(
            candidate=candidate,
            scene=scene,
            display_admission=admission,
            reduce_motion=reduce_motion,
            assignment_context=assignment_context,
            global_effect_identifier=selection.registry_effect_identifier,
        ),
    )

    notification = make_glance_notification(
        notification_id=notification_id,
        kind=_glance_kind(semantic),
        created_at_epoch=now,
    )
    state = _upsert_glance_state(_glance_state(controller), notification, now=now)
    plan = plan_glance_light(state, now_epoch=now, environment=glance_environment)
    setattr(controller, "_glance_light_state", state)
    setattr(controller, "_glance_light_plan", plan)

    effect_id = (
        selection.reduce_motion_substitution
        or selection.registry_effect_identifier
    )
    events = tuple(
        EffectEvent(
            event_id=f"{notification_id}.{surface.destination.value}.shown",
            occurred_at_epoch=now,
            effect_id=effect_id,
            semantic_category=_history_category(semantic),
            surface=_GLANCE_HISTORY_SURFACES[surface.destination],
            outcome=EffectOutcome.SHOWN,
        )
        for surface in plan.surface_plans
        if surface.active
    )
    history = record_effect_events(_history(controller), events)
    setattr(controller, "_effect_history", history)
    decisions = (
        (PolicyDecision.REDUCE_MOTION_SUBSTITUTE,)
        if selection.reduce_motion_substitution is not None
        else ()
    )
    setattr(
        controller,
        "_why_effect_projection",
        project_why_effect(
            effect_id,
            source_age_seconds=0.0,
            priority=SEMANTIC_PRIORITY[semantic],
            policy_decisions=decisions,
            registry=registry,
        ),
    )
    if action_token is not None:
        bindings = dict(getattr(controller, "_glance_action_bindings", {}))
        bindings[action_token] = notification_id
        setattr(controller, "_glance_action_bindings", bindings)
    semantics = dict(getattr(controller, "_glance_semantics", {}))
    semantics[notification_id] = semantic
    setattr(controller, "_glance_semantics", semantics)
    effects = dict(getattr(controller, "_glance_effect_ids", {}))
    effects[notification_id] = effect_id
    setattr(controller, "_glance_effect_ids", effects)
    _queue_persistence(controller)


def _record_acknowledgement(
    controller: object,
    *,
    action_token: object,
) -> None:
    if type(action_token) is not str:
        return
    bindings = dict(getattr(controller, "_glance_action_bindings", {}))
    notification_id = bindings.pop(action_token, None)
    setattr(controller, "_glance_action_bindings", bindings)
    if type(notification_id) is not str:
        return
    now = time.time()
    state = acknowledge_glance_notification(
        _glance_state(controller),
        notification_id=notification_id,
        acknowledged_at_epoch=now,
    )
    _admission, glance_environment = _environment(controller)
    setattr(controller, "_glance_light_state", state)
    setattr(
        controller,
        "_glance_light_plan",
        plan_glance_light(state, now_epoch=now, environment=glance_environment),
    )
    semantic = dict(getattr(controller, "_glance_semantics", {})).get(
        notification_id,
        SemanticEventKind.NOTIFICATION,
    )
    effect_id = dict(getattr(controller, "_glance_effect_ids", {})).get(
        notification_id,
        "notification",
    )
    event = EffectEvent(
        event_id=f"{notification_id}.notification.acknowledged",
        occurred_at_epoch=now,
        effect_id=effect_id,
        semantic_category=_history_category(semantic),
        surface=EffectSurface.NOTIFICATION,
        outcome=EffectOutcome.ACKNOWLEDGED,
        acknowledgement_source=EffectAcknowledgementSource.NOTIFICATION,
    )
    setattr(
        controller,
        "_effect_history",
        record_effect_events(_history(controller), (event,)),
    )
    _queue_persistence(controller)


def _accessibility_preferences(controller: object) -> AccessibilityDisplayPreferences:
    preferences = getattr(controller, "_accessibility_display_preferences", None)
    if type(preferences) is AccessibilityDisplayPreferences:
        return preferences
    return AccessibilityDisplayPreferences(
        reduce_motion=bool(getattr(preferences, "reduce_motion", False))
    )


def _opaque_work_identity(work_key: WorkKey, prefix: str) -> str:
    payload = repr(work_key_to_payload(work_key)).encode("utf-8")
    return f"{prefix}.{hashlib.sha256(payload).hexdigest()[:32]}"


def _opaque_event_identity(event: CanonicalOperatorEvent) -> str:
    payload = repr(event.key).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:32]
    return f"event.{event.kind.value}.{digest}"


def _work_for_key(state: CanonicalOperatorState, key: WorkKey):
    return next((work for work in state.works if work.key == key), None)


def _root_work_key(state: CanonicalOperatorState, key: WorkKey) -> WorkKey:
    by_key = {work.key: work for work in state.works}
    current = key
    visited: set[WorkKey] = set()
    while current not in visited:
        visited.add(current)
        work = by_key.get(current)
        if work is None or work.parent_key is None:
            break
        current = work.parent_key
    return current


def _fleet_plan(
    controller: object,
    state: CanonicalOperatorState,
) -> FleetPlan:
    members = tuple(
        FleetMember(
            identity=_opaque_work_identity(work.key, "fleet"),
            semantic=work.lifecycle,
            is_worker=work.parent_key is not None,
            is_main=work.parent_key is None,
        )
        for work in state.works
    )
    previous = getattr(controller, "_ambient_fleet_plan", None)
    plan = plan_fleet_bands(
        members,
        previous_layout=previous if type(previous) is FleetPlan else None,
    )
    setattr(controller, "_ambient_fleet_plan", plan)
    return plan


def _band_for_identity(
    plan: FleetPlan,
    identity: str,
    semantic: object,
) -> FleetBand | None:
    matching = tuple(band for band in plan.bands if band.identity == identity)
    if len(matching) == 1 and not matching[0].shared:
        return matching[0]
    slot = plan.slot_for(identity)
    if slot is None or plan.led_count <= 0:
        return None
    start, end = slot
    scale = float(plan.screen_bar_width) / plan.led_count
    return FleetBand(
        identity=identity,
        semantic=semantic,
        led_start=start,
        led_end=end,
        screen_start=start * scale,
        screen_end=end * scale,
        shared=False,
    )


def _completion_key(event: CanonicalOperatorEvent) -> CompletionPresentationKey | None:
    subject = event.subject_key
    if type(subject) is not WorkKey or event.kind is not TransitionKind.COMPLETED:
        return None
    return CompletionPresentationKey(
        source_key=subject.source_key,
        agent_id=_opaque_work_identity(subject, "agent"),
        event_name="Stop",
        completed_at_epoch=event.occurred_at_epoch,
    )


def _completion_firefly(
    controller: object,
    event: CanonicalOperatorEvent,
    state: CanonicalOperatorState,
    fleet: FleetPlan,
    *,
    reduce_motion: bool,
) -> None:
    subject = event.subject_key
    completion_key = _completion_key(event)
    if type(subject) is not WorkKey or completion_key is None:
        return
    root = _root_work_key(state, subject)
    fleet_identity = _opaque_work_identity(root, "fleet")
    active_band = _band_for_identity(fleet, fleet_identity, WorkLifecycle.COMPLETED)
    if active_band is None:
        return
    evidence = FireflyCompletionEvidence(
        completion_key=completion_key,
        fleet_identity=fleet_identity,
        active_band=active_band,
    )
    setattr(
        controller,
        "_firefly_completion_decision",
        plan_firefly_completion(evidence, fleet, reduce_motion=reduce_motion),
    )


def _completion_meniscus(
    controller: object,
    event: CanonicalOperatorEvent,
    preferences: AccessibilityDisplayPreferences,
) -> None:
    completion_key = _completion_key(event)
    if completion_key is None:
        return
    evidence = SelectedUnseenCompletionEvidence(completion_key)
    geometries = (
        CompletionMeniscusGeometry(
            CompletionMeniscusSurface.SCREEN_BAR,
            0.0,
            0.0,
            1.0,
            1.0,
        ),
        CompletionMeniscusGeometry(
            CompletionMeniscusSurface.ALCOVE,
            0.0,
            0.0,
            1.0,
            1.0,
        ),
    )
    setattr(
        controller,
        "_completion_meniscus_plans",
        tuple(
            plan_completion_meniscus(evidence, geometry, preferences)
            for geometry in geometries
        ),
    )


def _milestone_odometer(
    controller: object,
    event: CanonicalOperatorEvent,
    *,
    reduce_motion: bool,
) -> None:
    completion_key = _completion_key(event)
    if completion_key is None:
        return
    settings = getattr(controller, "settings", None)
    raw_steps = getattr(settings, "milestone_odometer_steps", ())
    steps = tuple(raw_steps) if type(raw_steps) in {tuple, list} else ()
    enabled = bool(getattr(settings, "milestone_odometer_enabled", False))
    preferences = MilestoneOdometerPreferences(
        enabled=enabled,
        milestone_steps=steps if enabled else (),
    )
    state = getattr(controller, "_milestone_odometer_state", None)
    if type(state) is not MilestoneOdometerState:
        state = MilestoneOdometerState()
    plan = plan_milestone_odometer(
        preferences,
        state,
        (completion_key,),
        reduce_motion=reduce_motion,
    )
    setattr(controller, "_milestone_odometer_plan", plan)
    setattr(controller, "_milestone_odometer_state", plan.state)


def _handoff_endpoint(
    event: CanonicalOperatorEvent,
    state: CanonicalOperatorState,
) -> HandoffEndpoint | None:
    subject = event.subject_key
    if type(subject) is not WorkKey:
        return None
    root = _root_work_key(state, subject)
    work = _work_for_key(state, subject)
    return HandoffEndpoint(
        event_identity=_opaque_event_identity(event),
        agent_identity=_opaque_work_identity(subject, "agent"),
        segment_identity=_opaque_work_identity(subject, "segment"),
        accessibility_name=(
            work.safe_label if work is not None else "Agent session"
        ),
        observed_at=event.occurred_at_epoch,
        project_identity=_opaque_work_identity(root, "project"),
        task_identity=(
            _opaque_work_identity(subject, "task")
            if subject == root
            else None
        ),
    )


def _observe_handoff(
    controller: object,
    event: CanonicalOperatorEvent,
    state: CanonicalOperatorState,
    *,
    reduce_motion: bool,
) -> None:
    endpoint = _handoff_endpoint(event, state)
    if endpoint is None:
        return
    completions = dict(getattr(controller, "_ambient_completion_endpoints", {}))
    project = endpoint.project_identity
    if event.kind is TransitionKind.COMPLETED and project is not None:
        completions[project] = endpoint
        if len(completions) > 32:
            completions = dict(
                sorted(
                    completions.items(),
                    key=lambda item: item[1].observed_at,
                    reverse=True,
                )[:32]
            )
    elif event.kind is TransitionKind.BECAME_ACTIVE and project in completions:
        source = completions.pop(project)
        setattr(
            controller,
            "_handoff_baton_decision",
            plan_handoff_baton(
                source,
                endpoint,
                reduce_motion=reduce_motion,
            ),
        )
    setattr(controller, "_ambient_completion_endpoints", completions)


def _observe_recovery(
    controller: object,
    event: CanonicalOperatorEvent,
    previous_health: dict[object, SourceHealth],
    *,
    reduce_motion: bool,
) -> None:
    if event.kind is not TransitionKind.SOURCE_RECOVERED:
        return
    source = event.key.provider_watermark.source_key
    prior = previous_health.get(source)
    if prior is None or prior is SourceHealth.HEALTHY:
        return
    evidence = ConfirmedRecoveryEvidence(
        event=event,
        previous_health=prior,
        current_health=SourceHealth.HEALTHY,
        recovery_confirmations=TIMING_RECOVERY_CONFIRMATIONS,
    )
    admission, _environment_plan = _environment(controller)
    finite = getattr(controller, "_status_finite_cues", None)
    finite_available = finite is None or (
        getattr(finite, "active", None) is None
        and getattr(finite, "pending", None) is None
    )
    presented = tuple(
        item
        for item in getattr(controller, "_presented_recovery_graces", ())
        if type(item) is RecoveryGraceIdentity
    )[-256:]
    plan = plan_recovery_grace_note(
        evidence,
        dnd_display_admission=admission,
        courtesy_suppression=CourtesySuppression(
            focus=admission is not DisplayAdmission.ALL
        ),
        finite_cue_available=finite_available,
        reduce_motion=reduce_motion,
        presented_identities=presented,
    )
    setattr(controller, "_recovery_grace_plan", plan)
    if plan.emits:
        setattr(
            controller,
            "_presented_recovery_graces",
            (*presented, plan.dedupe_identity)[-256:],
        )


def _source_health_by_key(
    state: CanonicalOperatorState,
) -> dict[object, SourceHealth]:
    health: dict[object, SourceHealth] = {}
    for work in state.works:
        source = work.key.source_key
        current = health.get(source)
        if current is None or current is SourceHealth.HEALTHY:
            health[source] = work.source_health
    return health


def _observe_turn_starts(
    controller: object,
    events: tuple[CanonicalOperatorEvent, ...],
    state: CanonicalOperatorState,
    *,
    reduce_motion: bool,
) -> None:
    starts = dict(getattr(controller, "_ambient_turn_starts", {}))
    for event in events:
        subject = event.subject_key
        if type(subject) is not WorkKey:
            continue
        if event.kind is TransitionKind.BECAME_ACTIVE:
            starts[subject] = event.occurred_at_epoch
        elif event.kind in {
            TransitionKind.BECAME_IDLE,
            TransitionKind.COMPLETED,
            TransitionKind.FAILED,
        }:
            starts.pop(subject, None)
    active = {
        work.key for work in state.works if work.lifecycle is WorkLifecycle.ACTIVE
    }
    starts = {key: started for key, started in starts.items() if key in active}
    setattr(controller, "_ambient_turn_starts", starts)
    if not starts:
        setattr(controller, "_turn_length_ember_plan", None)
        return
    _work_key, started = min(starts.items(), key=lambda item: item[1])
    thermal = (
        ThermalState.SERIOUS
        if bool(getattr(controller, "_ambient_serious_thermal", False))
        else ThermalState.NOMINAL
    )
    surface_visible = any(
        getattr(controller, attribute, None) is not None
        for attribute in ("status_item", "virtual_status_device")
    )
    setattr(
        controller,
        "_turn_length_ember_plan",
        plan_turn_length_ember(
            elapsed_seconds=max(0.0, time.time() - started),
            turn_active=True,
            surface_visible=surface_visible,
            reduce_motion=reduce_motion,
            low_power=bool(getattr(controller, "_ambient_low_power", False)),
            thermal=thermal,
        ),
    )


def _observe_ask_heartbeat(
    controller: object,
    state: CanonicalOperatorState,
    preferences: AccessibilityDisplayPreferences,
) -> None:
    presentations = tuple(
        AskHeartbeatPresentation(
            request_identity=announcer_alert_identity(request.key),
            presented_at_epoch=request.opened_at_epoch,
        )
        for request in state.requests
        if request.phase is RequestPhase.LIVE_UNACKNOWLEDGED
        and request.opened_at_epoch is not None
        and request.source_freshness is SourceFreshness.FRESH
    )
    setattr(
        controller,
        "_ask_heartbeat_plan",
        plan_ask_heartbeat_sync(
            presentations,
            accessibility_preferences=preferences,
        ),
    )


_COURTESY_BY_TRANSITION = {
    TransitionKind.COMPLETED: CourtesySemantic.COMPLETION,
    TransitionKind.SOURCE_RECOVERED: CourtesySemantic.RECOVERY,
    TransitionKind.REQUEST_OPENED: CourtesySemantic.INTERRUPTION,
    TransitionKind.FAILED: CourtesySemantic.FAILURE,
}


def _observe_courtesy_signature(
    controller: object,
    event: CanonicalOperatorEvent,
    *,
    reduce_motion: bool,
) -> None:
    semantic = _COURTESY_BY_TRANSITION.get(event.kind)
    if semantic is None:
        return
    setattr(
        controller,
        "_courtesy_signature_plan",
        plan_courtesy_signature(semantic, reduce_motion=reduce_motion),
    )


def _active_semantics(
    state: CanonicalOperatorState,
    events: tuple[CanonicalOperatorEvent, ...],
) -> tuple[SemanticEventKind, ...]:
    semantics: set[SemanticEventKind] = set()
    if any(
        request.phase is RequestPhase.LIVE_UNACKNOWLEDGED
        for request in state.requests
    ):
        semantics.add(SemanticEventKind.ASK)
    for work in state.works:
        semantic = {
            WorkLifecycle.ACTIVE: SemanticEventKind.WORK,
            WorkLifecycle.WAITING: SemanticEventKind.ASK,
            WorkLifecycle.COMPLETED: SemanticEventKind.COMPLETION,
            WorkLifecycle.FAILED: SemanticEventKind.FAILURE,
            WorkLifecycle.IDLE: SemanticEventKind.IDLE,
        }.get(work.lifecycle)
        if semantic is not None:
            semantics.add(semantic)
    for event in events:
        semantic = {
            TransitionKind.COMPLETED: SemanticEventKind.COMPLETION,
            TransitionKind.FAILED: SemanticEventKind.FAILURE,
            TransitionKind.SOURCE_RECOVERED: SemanticEventKind.RECOVERY,
        }.get(event.kind)
        if semantic is not None:
            semantics.add(semantic)
    return tuple(
        sorted(
            semantics,
            key=lambda semantic: (-SEMANTIC_PRIORITY[semantic], semantic.value),
        )
    )


def _observe_dot_and_rainstick(
    controller: object,
    state: CanonicalOperatorState,
    events: tuple[CanonicalOperatorEvent, ...],
    preferences: AccessibilityDisplayPreferences,
) -> None:
    semantics = _active_semantics(state, events)
    root_count = sum(work.parent_key is None for work in state.works)
    settings = getattr(controller, "settings", None)
    policy_value = getattr(
        settings,
        "dot_secondary_policy",
        DotSecondaryPolicy.FLEET_SIZE.value,
    )
    try:
        secondary_policy = DotSecondaryPolicy(policy_value)
    except ValueError:
        secondary_policy = DotSecondaryPolicy.FLEET_SIZE
    glance_state = getattr(controller, "_glance_light_state", None)
    unseen = bool(getattr(glance_state, "notifications", ()))
    setattr(
        controller,
        "_dot_binary_heartbeat_plan",
        plan_dot_binary_heartbeat(
            semantics,
            secondary_policy=secondary_policy,
            fleet_size=root_count,
            unseen_notification_present=unseen,
            reduce_motion=preferences.reduce_motion,
        ),
    )

    admission, _environment_plan = _environment(controller)
    scene = scene_from_value(getattr(settings, "active_scene", None)) or DEFAULT_SCENE
    surface_visible = any(
        getattr(controller, attribute, None) is not None
        for attribute in ("status_item", "virtual_status_device")
    )
    thermal = (
        RainstickThermalState.SERIOUS
        if bool(getattr(controller, "_ambient_serious_thermal", False))
        else RainstickThermalState.NOMINAL
    )
    higher_priority = any(
        semantic is not SemanticEventKind.IDLE for semantic in semantics
    )
    setattr(
        controller,
        "_rainstick_idle_plan",
        plan_rainstick_idle(
            preference_enabled=bool(
                getattr(settings, "rainstick_idle_enabled", False)
            ),
            higher_priority_signal_active=higher_priority,
            dnd_active=admission is not DisplayAdmission.ALL,
            night_policy_allows_idle=(
                scene.value != "night"
                or bool(getattr(settings, "rainstick_night_enabled", False))
            ),
            surface_visible=surface_visible,
            display_asleep=bool(getattr(controller, "display_asleep", False)),
            low_power=bool(getattr(controller, "_ambient_low_power", False)),
            thermal=thermal,
            reduce_motion=preferences.reduce_motion,
            surface_pixel_count=8,
        ),
    )


def _observe_remote_fleet(controller: object, *, reduce_motion: bool) -> None:
    refresh = getattr(controller, "_remote_refresh", None)
    health_rows = tuple(getattr(refresh, "health", ()))
    if not health_rows:
        return
    now = time.time()
    states = dict(getattr(controller, "_ambient_fleet_presence_states", {}))
    episodes = dict(getattr(controller, "_ambient_fleet_presence_episodes", {}))
    admission, _environment_plan = _environment(controller)
    finite = getattr(controller, "_status_finite_cues", None)
    finite_available = finite is None or (
        getattr(finite, "active", None) is None
        and getattr(finite, "pending", None) is None
    )
    decisions = []
    for row in health_rows:
        machine = getattr(row, "machine", None)
        reachable = getattr(row, "reachable", None)
        if type(machine) is not str or type(reachable) is not bool:
            continue
        liveness = (
            RemoteMachineLiveness.ONLINE
            if reachable
            else RemoteMachineLiveness.OFFLINE
        )
        prior_episode = episodes.get(machine)
        if prior_episode is None or prior_episode[0] is not liveness:
            counter = 1 if prior_episode is None else prior_episode[1] + 1
            prior_episode = (liveness, counter)
            episodes[machine] = prior_episode
        liveness_identity = f"{liveness.value}.{prior_episode[1]}"
        previous = states.get(machine)
        decision = observe_fleet_arrival_departure(
            TrustedRemoteMachineLiveness(
                machine_identity=machine,
                liveness_identity=liveness_identity,
                liveness=liveness,
                observed_at=now,
            ),
            previous if type(previous) is FleetArrivalDepartureState else None,
            dnd_display_admission=admission,
            courtesy_suppression=CourtesySuppression(
                focus=admission is not DisplayAdmission.ALL
            ),
            finite_cue_available=finite_available,
            reduce_motion=reduce_motion,
        )
        if decision.state is not None:
            states[machine] = decision.state
        decisions.append(decision)
    setattr(controller, "_ambient_fleet_presence_states", states)
    setattr(controller, "_ambient_fleet_presence_episodes", episodes)
    setattr(controller, "_fleet_arrival_departure_decisions", tuple(decisions))


def _observe_operator_events(
    controller: object,
    events: object,
    state: object,
) -> None:
    if type(state) is not CanonicalOperatorState or type(events) is not tuple:
        return
    canonical_events = tuple(
        event for event in events if type(event) is CanonicalOperatorEvent
    )
    preferences = _accessibility_preferences(controller)
    previous_health = dict(getattr(controller, "_ambient_source_health", {}))
    fleet = _fleet_plan(controller, state)
    for event in canonical_events:
        if event.kind is TransitionKind.COMPLETED:
            _completion_firefly(
                controller,
                event,
                state,
                fleet,
                reduce_motion=preferences.reduce_motion,
            )
            _completion_meniscus(controller, event, preferences)
            _milestone_odometer(
                controller,
                event,
                reduce_motion=preferences.reduce_motion,
            )
        if event.kind in {TransitionKind.COMPLETED, TransitionKind.BECAME_ACTIVE}:
            _observe_handoff(
                controller,
                event,
                state,
                reduce_motion=preferences.reduce_motion,
            )
        _observe_recovery(
            controller,
            event,
            previous_health,
            reduce_motion=preferences.reduce_motion,
        )
        _observe_courtesy_signature(
            controller,
            event,
            reduce_motion=preferences.reduce_motion,
        )
    _observe_ask_heartbeat(controller, state, preferences)
    _observe_dot_and_rainstick(
        controller,
        state,
        canonical_events,
        preferences,
    )
    _observe_remote_fleet(
        controller,
        reduce_motion=preferences.reduce_motion,
    )
    _observe_turn_starts(
        controller,
        canonical_events,
        state,
        reduce_motion=preferences.reduce_motion,
    )
    _compile_runtime_dispatch(controller)
    setattr(controller, "_ambient_source_health", _source_health_by_key(state))


def install_ambient_effect_runtime(controller: type) -> AmbientEffectRuntimeReceipt:
    """Install one idempotent adapter on the production controller class."""

    if not isinstance(controller, type):
        raise TypeError("ambient effect runtime requires a controller class")
    receipt = _receipts.get(controller)
    if receipt is not None:
        return receipt
    if not isinstance(
        getattr(controller, "_effect_assignment_cache", None),
        EffectAssignmentCache,
    ):
        restored = load_effect_assignments()
        registry = EFFECT_REGISTRY
        try:
            for pack in EffectPackStore().list():
                registry = registry_with_pack(registry, pack)
        except (EffectPackError, EffectPackStoreError, OSError, ValueError):
            registry = EFFECT_REGISTRY
        setattr(
            controller,
            "_effect_assignment_cache",
            EffectAssignmentCache(restored.document, registry),
        )
    original_delivery = getattr(controller, "_deliver_semantic_notification", None)
    original_activation = getattr(controller, "_activate_notification_action", None)
    original_event_observer = getattr(
        controller,
        "observe_operator_history_events",
        None,
    )
    if not all(
        callable(method)
        for method in (
            original_delivery,
            original_activation,
            original_event_observer,
        )
    ):
        raise RuntimeError("ambient effect runtime owner methods are unavailable")

    def deliver(self, event_key, interruption_class, **kwargs):
        before = set(getattr(self, "_notification_action_bindings", {}))
        delivered = original_delivery(
            self,
            event_key,
            interruption_class,
            **kwargs,
        )
        if delivered:
            after = set(getattr(self, "_notification_action_bindings", {}))
            tokens = sorted(after - before)
            try:
                _record_delivery(
                    self,
                    event_key=event_key,
                    interruption_class=interruption_class,
                    prefix=kwargs.get("prefix", "notification"),
                    action_token=tokens[-1] if tokens else None,
                )
            except Exception:
                pass
        return delivered

    def activate(self, token):
        activated = original_activation(self, token)
        if activated:
            try:
                _record_acknowledgement(self, action_token=token)
            except Exception:
                pass
        return activated

    def observe_events(self, events, state):
        result = original_event_observer(self, events, state)
        try:
            _observe_operator_events(self, events, state)
        except Exception:
            pass
        return result

    def plan_finite(self, effect_key, **kwargs):
        return plan_finite_effect(effect_key, **kwargs)

    setattr(controller, "_deliver_semantic_notification", deliver)
    setattr(controller, "_activate_notification_action", activate)
    setattr(controller, "observe_operator_history_events", observe_events)
    setattr(controller, "_plan_finite_ambient_effect", plan_finite)
    receipt = AmbientEffectRuntimeReceipt(controller)
    _receipts[controller] = receipt
    return receipt


__all__ = [
    "GLANCE_LIGHT_STORE_NAME",
    "AmbientEffectRuntimeReceipt",
    "active_ambient_surface_output",
    "active_device_ambient_surface_output",
    "install_ambient_effect_runtime",
]
