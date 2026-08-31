from dataclasses import replace
from types import SimpleNamespace

from sidepulse.ambient_effect_consumer import active_hardware_ambient_presentation
from sidepulse.ambient_effect_dispatch import AmbientEffectFamily, AmbientEffectSurface
from sidepulse.ambient_effect_runtime import (
    active_ambient_surface_output,
    install_ambient_effect_runtime,
)
from sidepulse.capacity_types import SourceKey
from sidepulse.dnd_policy import compose_dnd_contributions
from sidepulse.effect_assignment_store import (
    EffectAssignmentCache,
    EffectAssignmentDocument,
    EffectAssignmentRecord,
)
from sidepulse.effect_history import EffectOutcome
from sidepulse.effect_studio import AssignmentScope
from sidepulse.glance_light import GlanceLightState
from sidepulse.operator_state import (
    AcknowledgementEligibility,
    CanonicalOperatorEvent,
    CanonicalRequestTruth,
    CanonicalWorkTruth,
    InterruptionClass,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
    classify_operator_event,
    empty_operator_state,
)
from sidepulse.provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderWatermark,
    RequestIdentifier,
    RequestKey,
    RequestKind,
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
    WorkLifecycle,
)


class _Writer:
    def __init__(self) -> None:
        self.submissions: list[tuple[str, bool]] = []

    def submit(self, key, _operation, *, replace_pending=False):
        self.submissions.append((key, replace_pending))


class _Event:
    transition_kind = TransitionKind.REQUEST_OPENED

    def __repr__(self) -> str:
        return "content-free-event"


def _controller_type():
    class Controller:
        _effect_assignment_cache = EffectAssignmentCache()

        def __init__(self) -> None:
            self.settings = SimpleNamespace(active_scene="calm")
            self._accessibility_display_preferences = SimpleNamespace(
                reduce_motion=False
            )
            self._notification_action_bindings = {}
            self._persistence_writer = _Writer()
            self.virtual_status_device = object()
            self.observed_event_batches = []

        def current_dnd_projection(self):
            return compose_dnd_contributions(())

        def _deliver_semantic_notification(
            self,
            _event_key,
            _interruption_class,
            **_kwargs,
        ):
            self._notification_action_bindings["opaque-token"] = object()
            return True

        def _activate_notification_action(self, token):
            return token == "opaque-token"

        def observe_operator_history_events(self, events, state):
            self.observed_event_batches.append((events, state))

    return Controller


def _canonical_state(
    *,
    lifecycle: WorkLifecycle,
    request_open: bool = False,
    health: SourceHealth = SourceHealth.HEALTHY,
    epoch: float = 1_800_000_000.0,
):
    source = SourceKey("codex", "hooks", "local:01", "live_agent_events")
    work_key = WorkKey(source, WorkIdentifier("work:01"))
    request_key = RequestKey(work_key, RequestIdentifier("request:01"))
    watermark = ProviderWatermark(
        source,
        WatermarkBasis.PROVIDER_EVENT_ID,
        epoch,
        EventToken(f"event:{int(epoch)}"),
        None,
        1,
    )
    requests = ()
    request_keys = ()
    if request_open:
        semantic_key = SemanticEventKey(
            request_key,
            TransitionKind.REQUEST_OPENED,
            watermark,
        )
        requests = (
            CanonicalRequestTruth(
                request_key,
                RequestPhase.LIVE_UNACKNOWLEDGED,
                RequestKind.INPUT,
                NextActor.USER,
                watermark,
                SourceFreshness.FRESH,
                AcknowledgementEligibility.ELIGIBLE,
                semantic_key,
                epoch,
                0.0,
            ),
        )
        request_keys = (request_key,)
    work = CanonicalWorkTruth(
        work_key,
        lifecycle,
        watermark,
        ObservationAuthority.AUTHORITATIVE_PROVIDER,
        health,
        SourceFreshness.FRESH,
        NextActor.USER if request_open else NextActor.PROVIDER,
        "Codex work 01",
        None,
        request_keys,
        False,
    )
    state = replace(
        empty_operator_state(),
        generation=1,
        works=(work,),
        requests=requests,
    )
    return state, work_key, request_key, watermark


def _operator_event(subject, kind, watermark):
    key = SemanticEventKey(subject, kind, watermark)
    return CanonicalOperatorEvent(
        key,
        subject,
        kind,
        classify_operator_event(kind),
        watermark.occurred_at_epoch,
        SourceFreshness.FRESH,
    )


def test_runtime_projects_successful_delivery_and_acknowledgement_across_surfaces(
    monkeypatch,
) -> None:
    controller_type = _controller_type()
    receipt = install_ambient_effect_runtime(controller_type)
    assert install_ambient_effect_runtime(controller_type) is receipt
    controller = controller_type()
    monkeypatch.setattr("sidepulse.ambient_effect_runtime.time.time", lambda: 100.0)

    assert controller._deliver_semantic_notification(
        _Event(),
        InterruptionClass.ACTION_REQUIRED,
        prefix="attention",
    )

    assert controller._semantic_effect_selection.winner.semantic.value == "ask"
    assert controller._glance_light_plan.notification_count == 1
    assert controller._glance_light_plan.selected_notification_id is not None
    assert len(controller._effect_history.events) == 4
    assert {event.outcome for event in controller._effect_history.events} == {
        EffectOutcome.SHOWN
    }
    assert len({event.surface for event in controller._effect_history.events}) == 4
    assert controller._why_effect_projection.priority == 90
    assert {key for key, _replace in controller._persistence_writer.submissions} == {
        "effect-history",
        "glance-light",
    }

    monkeypatch.setattr("sidepulse.ambient_effect_runtime.time.time", lambda: 101.0)
    assert controller._activate_notification_action("opaque-token")

    assert controller._glance_light_plan.notification_count == 0
    assert any(
        event.outcome is EffectOutcome.ACKNOWLEDGED
        for event in controller._effect_history.events
    )
    assert isinstance(controller._glance_light_state, GlanceLightState)


def test_runtime_consumes_cached_provider_assignment_without_reading_disk(
    monkeypatch,
) -> None:
    controller_type = _controller_type()
    install_ambient_effect_runtime(controller_type)
    controller = controller_type()
    state, work_key, _request_key, watermark = _canonical_state(
        lifecycle=WorkLifecycle.COMPLETED,
    )
    event_key = SemanticEventKey(
        work_key,
        TransitionKind.COMPLETED,
        watermark,
    )
    controller.current_operator_state = state
    controller._effect_assignment_cache = EffectAssignmentCache(
        EffectAssignmentDocument(
            (
                EffectAssignmentRecord.create(
                    "pulse",
                    AssignmentScope.PROVIDER,
                    "codex",
                ),
            )
        )
    )
    monkeypatch.setattr(
        "sidepulse.ambient_effect_runtime.load_effect_assignments",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("runtime route must use the cache")
        ),
        raising=False,
    )

    assert controller._deliver_semantic_notification(
        event_key,
        InterruptionClass.IMPORTANT_OUTCOME,
        prefix="completion",
    )

    assert controller._semantic_effect_selection.registry_effect_identifier == "pulse"


def test_hardware_consumption_resolves_device_assignments_without_mutating_global(
    monkeypatch,
) -> None:
    controller_type = _controller_type()
    install_ambient_effect_runtime(controller_type)
    controller = controller_type()
    state, work_key, _request_key, watermark = _canonical_state(
        lifecycle=WorkLifecycle.COMPLETED,
    )
    event_key = SemanticEventKey(
        work_key,
        TransitionKind.COMPLETED,
        watermark,
    )
    controller.current_operator_state = state
    controller._ambient_assignment_device_id = "device-a"
    controller._effect_assignment_cache = EffectAssignmentCache(
        EffectAssignmentDocument(
            (
                EffectAssignmentRecord.create(
                    "pulse",
                    AssignmentScope.DEVICE,
                    "device-a",
                ),
                EffectAssignmentRecord.create(
                    "rainbow",
                    AssignmentScope.DEVICE,
                    "device-b",
                ),
            )
        )
    )
    monkeypatch.setattr("sidepulse.ambient_effect_runtime.time.time", lambda: 100.0)
    monkeypatch.setattr(
        "sidepulse.ambient_effect_runtime.time.monotonic",
        lambda: 50.0,
    )

    assert controller._deliver_semantic_notification(
        event_key,
        InterruptionClass.IMPORTANT_OUTCOME,
        prefix="completion",
    )
    assert controller._semantic_effect_selection.registry_effect_identifier == (
        "notification"
    )
    controller._glance_light_plan = None
    controller.observe_operator_history_events((), state)
    global_dispatch = controller._ambient_effect_dispatch

    device_a = active_hardware_ambient_presentation(
        controller,
        device_id="device-a",
        led_count=8,
        reduce_motion=False,
        brightness=255,
    )
    device_b = active_hardware_ambient_presentation(
        controller,
        device_id="device-b",
        led_count=8,
        reduce_motion=False,
        brightness=255,
    )
    unassigned = active_hardware_ambient_presentation(
        controller,
        device_id="device-c",
        led_count=8,
        reduce_motion=False,
        brightness=255,
    )

    assert device_a is not None and device_a.output.effect_identity == "pulse"
    assert device_b is not None and device_b.output.effect_identity == "rainbow"
    assert unassigned is not None and unassigned.output.effect_identity == "notification"
    assert controller._ambient_effect_dispatch is global_dispatch
    assert (
        global_dispatch.for_surface(AmbientEffectSurface.SCREEN_BAR).effect_identity
        == "notification"
    )


def test_device_assignment_cannot_replace_higher_priority_urgent_output(
    monkeypatch,
) -> None:
    controller_type = _controller_type()
    install_ambient_effect_runtime(controller_type)
    controller = controller_type()
    state, _work_key, request_key, watermark = _canonical_state(
        lifecycle=WorkLifecycle.WAITING,
        request_open=True,
    )
    event_key = SemanticEventKey(
        request_key,
        TransitionKind.REQUEST_OPENED,
        watermark,
    )
    controller.current_operator_state = state
    controller._effect_assignment_cache = EffectAssignmentCache(
        EffectAssignmentDocument(
            (
                EffectAssignmentRecord.create(
                    "rainbow",
                    AssignmentScope.DEVICE,
                    "device-a",
                ),
            )
        )
    )
    monkeypatch.setattr("sidepulse.ambient_effect_runtime.time.time", lambda: 100.0)
    monkeypatch.setattr(
        "sidepulse.ambient_effect_runtime.time.monotonic",
        lambda: 50.0,
    )

    assert controller._deliver_semantic_notification(
        event_key,
        InterruptionClass.ACTION_REQUIRED,
        prefix="attention",
    )
    controller._glance_light_plan = None
    controller.observe_operator_history_events((), state)

    presentation = active_hardware_ambient_presentation(
        controller,
        device_id="device-a",
        led_count=8,
        reduce_motion=False,
        brightness=255,
    )

    assert presentation is not None
    assert presentation.output.effect_identity == "sidepulse.ask-heartbeat-sync:v1"
    assert presentation.led_state.value == "ask"


def test_runtime_never_changes_a_refused_delivery() -> None:
    controller_type = _controller_type()

    def refused(self, _event_key, _interruption_class, **_kwargs):
        return False

    controller_type._deliver_semantic_notification = refused
    install_ambient_effect_runtime(controller_type)
    controller = controller_type()

    assert not controller._deliver_semantic_notification(
        _Event(),
        InterruptionClass.ACTION_REQUIRED,
        prefix="attention",
    )
    assert not hasattr(controller, "_glance_light_state")


def test_runtime_projects_canonical_events_through_the_shared_ambient_seam(
    monkeypatch,
) -> None:
    controller_type = _controller_type()
    install_ambient_effect_runtime(controller_type)
    controller = controller_type()
    state, work_key, request_key, watermark = _canonical_state(
        lifecycle=WorkLifecycle.ACTIVE,
        request_open=True,
    )
    events = (
        _operator_event(work_key, TransitionKind.BECAME_ACTIVE, watermark),
        _operator_event(request_key, TransitionKind.REQUEST_OPENED, watermark),
    )
    monkeypatch.setattr("sidepulse.ambient_effect_runtime.time.time", lambda: 1_800_000_010.0)

    controller.observe_operator_history_events(events, state)

    assert controller.observed_event_batches == [(events, state)]
    assert controller._ask_heartbeat_plan.request_count == 1
    assert controller._turn_length_ember_plan.visible is True
    assert controller._turn_length_ember_plan.age_label == "Under 2 minutes"
    assert controller._ambient_fleet_plan.accepted is True
    assert controller._dot_binary_heartbeat_plan.selected_semantic.value == "ask"
    assert controller._rainstick_idle_plan.disposition.value == "suppress"
    assert callable(controller._plan_finite_ambient_effect)


def test_completion_event_projects_the_finite_completion_effect_family(
    monkeypatch,
) -> None:
    controller_type = _controller_type()
    install_ambient_effect_runtime(controller_type)
    controller = controller_type()
    state, work_key, _request_key, watermark = _canonical_state(
        lifecycle=WorkLifecycle.COMPLETED,
    )
    event = _operator_event(work_key, TransitionKind.COMPLETED, watermark)
    monkeypatch.setattr("sidepulse.ambient_effect_runtime.time.time", lambda: 1_800_000_010.0)

    controller.observe_operator_history_events((event,), state)

    screen_output = controller._ambient_effect_dispatch.for_surface(
        AmbientEffectSurface.SCREEN_BAR
    )
    assert screen_output is not None
    assert screen_output.family is AmbientEffectFamily.FIREFLY_COMPLETION
    assert screen_output.semantic.value == "completion"
    assert controller._firefly_completion_decision is None
    assert controller._completion_meniscus_plans is None
    assert controller._milestone_odometer_plan is None
    assert controller._courtesy_signature_plan is None


def test_runtime_dispatch_expires_without_bypassing_the_surface_owner(
    monkeypatch,
) -> None:
    controller_type = _controller_type()
    install_ambient_effect_runtime(controller_type)
    controller = controller_type()
    state, work_key, _request_key, watermark = _canonical_state(
        lifecycle=WorkLifecycle.COMPLETED,
    )
    event = _operator_event(work_key, TransitionKind.COMPLETED, watermark)
    monkeypatch.setattr("sidepulse.ambient_effect_runtime.time.time", lambda: 100.0)
    clock = [50.0]
    monkeypatch.setattr(
        "sidepulse.ambient_effect_runtime.time.monotonic",
        lambda: clock[0],
    )

    controller.observe_operator_history_events((event,), state)

    active = active_ambient_surface_output(
        controller,
        AmbientEffectSurface.SCREEN_BAR,
        now_monotonic=50.001,
    )
    assert active is not None
    output, started_at = active
    assert started_at == 50.0

    clock[0] = 50.5
    controller.observe_operator_history_events((), state)
    retained = active_ambient_surface_output(
        controller,
        AmbientEffectSurface.SCREEN_BAR,
        now_monotonic=50.5,
    )
    assert retained is not None
    assert retained[0] == output
    assert retained[1] == 50.0

    assert active_ambient_surface_output(
        controller,
        AmbientEffectSurface.SCREEN_BAR,
        now_monotonic=50.0 + output.expires_after_ms / 1_000.0,
    ) is None
