from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.ambient_effect_dispatch import (
    MAX_AMBIENT_OUTPUT_DURATION_MS,
    AmbientEffectFamily,
    AmbientEffectSurface,
    AmbientSemanticColors,
    compile_ambient_effect_dispatch,
)
from sidepulse.animation import errors_only, read_program
from sidepulse.announcer_stack import AnnouncerAlertIdentity
from sidepulse.ask_heartbeat_sync import AskHeartbeatPresentation, plan_ask_heartbeat_sync
from sidepulse.capacity_types import SourceKey
from sidepulse.clear_agents import CompletionPresentationKey
from sidepulse.completion_meniscus import (
    CompletionMeniscusGeometry,
    CompletionMeniscusSurface,
    SelectedUnseenCompletionEvidence,
    plan_completion_meniscus,
)
from sidepulse.courtesy_signatures import CourtesySemantic, plan_courtesy_signature
from sidepulse.dot_binary_heartbeat import DotSecondaryPolicy, plan_dot_binary_heartbeat
from sidepulse.firefly_completion import FireflyCompletionEvidence, plan_firefly_completion
from sidepulse.fleet_arrival_departure import (
    FleetArrivalDepartureAccessibility,
    FleetArrivalDepartureCue,
    FleetArrivalDepartureIdentity,
    FleetCueDisposition,
    FleetEndpointRole,
    FleetPresenceTransition,
)
from sidepulse.fleet_bands import FleetBand, FleetPlan
from sidepulse.glance_light import (
    GlanceKind,
    GlanceLightState,
    make_glance_notification,
    plan_glance_light,
)
from sidepulse.handoff_baton import HandoffEndpoint, plan_handoff_baton
from sidepulse.milestone_odometer import (
    MilestoneOdometerPreferences,
    MilestoneOdometerState,
    plan_milestone_odometer,
)
from sidepulse.rainstick_idle import plan_rainstick_idle
from sidepulse.recovery_grace_note import (
    RECOVERY_WIPE_DURATION_SECONDS,
    RecoveryGraceDisposition,
    RecoveryGraceIdentity,
    RecoveryGracePlan,
    RecoveryGracePresentation,
)
from sidepulse.semantic_effect_router import (
    SemanticEffectCandidate,
    SemanticEventKind,
    route_semantic_effects,
)
from sidepulse.turn_length_ember import plan_turn_length_ember

SOURCE = SourceKey("codex", "hooks", "local:test", "live_agent_events")


def _completion(index: int = 1) -> CompletionPresentationKey:
    return CompletionPresentationKey(SOURCE, f"agent:{index}", "Stop", float(index))


def _fleet_band(identity: str, start: int, end: int) -> FleetBand:
    return FleetBand(
        identity=identity,
        semantic="working",
        led_start=start,
        led_end=end,
        screen_start=float(start * 10),
        screen_end=float(end * 10),
    )


def _firefly():
    identity = "fleet:one"
    active = _fleet_band(identity, 0, 2)
    stable = _fleet_band(identity, 4, 8)
    decision = plan_firefly_completion(
        FireflyCompletionEvidence(_completion(), identity, active),
        FleetPlan(
            mode="segmented",
            bands=(stable,),
            member_slots=((identity, 4, 8),),
            led_count=8,
            screen_bar_width=80.0,
        ),
    )
    assert decision.plan is not None
    return decision.plan


def _meniscus(*, reduce_motion: bool = False):
    return plan_completion_meniscus(
        SelectedUnseenCompletionEvidence(_completion()),
        CompletionMeniscusGeometry(
            CompletionMeniscusSurface.SCREEN_BAR,
            0.0,
            0.0,
            80.0,
            12.0,
        ),
        AccessibilityDisplayPreferences(reduce_motion=reduce_motion),
    )


def _handoff():
    decision = plan_handoff_baton(
        HandoffEndpoint(
            "event:done",
            "agent:one",
            "segment:one",
            "First agent",
            10.0,
            project_identity="project:one",
        ),
        HandoffEndpoint(
            "event:start",
            "agent:two",
            "segment:two",
            "Second agent",
            11.0,
            project_identity="project:one",
        ),
    )
    assert decision.plan is not None
    return decision.plan


def _recovery():
    from sidepulse.provider_facts import EventToken, ProviderWatermark, WatermarkBasis

    watermark = ProviderWatermark(
        source_key=SOURCE,
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        occurred_at_epoch=10.0,
        event_token=EventToken("recovered:1"),
        sequence=None,
        tie_break_rank=1,
    )
    return RecoveryGracePlan(
        dedupe_identity=RecoveryGraceIdentity(watermark),
        disposition=RecoveryGraceDisposition.EMIT,
        presentation=RecoveryGracePresentation.RESTRAINED_WIPE,
        suppression_reason=None,
        repetitions=1,
        duration_seconds=RECOVERY_WIPE_DURATION_SECONDS,
        returns_to_normal=True,
        consumes_finite_cue=True,
        accessibility_text="Source recovered. A restrained recovery cue plays once.",
    )


def _ask(*, reduce_motion: bool = False):
    return plan_ask_heartbeat_sync(
        (
            AskHeartbeatPresentation(
                AnnouncerAlertIdentity("request:v1:one"),
                10.0,
            ),
        ),
        accessibility_preferences=AccessibilityDisplayPreferences(
            reduce_motion=reduce_motion
        ),
    )


def _milestone(*, reduce_motion: bool = False):
    return plan_milestone_odometer(
        MilestoneOdometerPreferences(enabled=True, milestone_steps=(1,)),
        MilestoneOdometerState(),
        (_completion(),),
        reduce_motion=reduce_motion,
    )


def _fleet_cue() -> FleetArrivalDepartureCue:
    identity = FleetArrivalDepartureIdentity(
        "machine:one",
        "episode:one",
        FleetPresenceTransition.ARRIVAL,
    )
    return FleetArrivalDepartureCue(
        identity=identity,
        endpoint_role=FleetEndpointRole.ARRIVAL_ENDPOINT,
        disposition=FleetCueDisposition.WINK,
        suppression_reason=None,
        duration_ms=650,
        passes=1,
        loops=0,
        returns_to_baseline=True,
        accessibility=FleetArrivalDepartureAccessibility(
            "Fleet presence",
            "Remote machine arrived",
            "A trusted remote machine joined the fleet.",
        ),
    )


def _glance():
    notification = make_glance_notification(
        notification_id="glance:one",
        kind=GlanceKind.UNANSWERED_ASK,
        created_at_epoch=10.0,
    )
    return plan_glance_light(GlanceLightState((notification,)), now_epoch=10.0)


@pytest.mark.parametrize(
    ("keyword", "plan", "family"),
    (
        (
            "semantic_selection",
            route_semantic_effects(
                (SemanticEffectCandidate("semantic:work", SemanticEventKind.WORK),)
            ),
            AmbientEffectFamily.SEMANTIC_SELECTION,
        ),
        ("glance_light", _glance(), AmbientEffectFamily.GLANCE_LIGHT),
        ("firefly_completion", _firefly(), AmbientEffectFamily.FIREFLY_COMPLETION),
        ("completion_meniscus", _meniscus(), AmbientEffectFamily.COMPLETION_MENISCUS),
        ("handoff_baton", _handoff(), AmbientEffectFamily.HANDOFF_BATON),
        ("recovery_grace", _recovery(), AmbientEffectFamily.RECOVERY_GRACE),
        ("ask_heartbeat", _ask(), AmbientEffectFamily.ASK_HEARTBEAT),
        (
            "turn_length_ember",
            plan_turn_length_ember(elapsed_seconds=300.0),
            AmbientEffectFamily.TURN_LENGTH_EMBER,
        ),
        (
            "rainstick_idle",
            plan_rainstick_idle(preference_enabled=True),
            AmbientEffectFamily.RAINSTICK_IDLE,
        ),
        (
            "dot_binary_heartbeat",
            plan_dot_binary_heartbeat(
                (SemanticEventKind.ASK,),
                secondary_policy=DotSecondaryPolicy.FLEET_SIZE,
                fleet_size=2,
            ),
            AmbientEffectFamily.DOT_BINARY_HEARTBEAT,
        ),
        ("milestone_odometer", _milestone(), AmbientEffectFamily.MILESTONE_ODOMETER),
        ("fleet_arrival_departure", _fleet_cue(), AmbientEffectFamily.FLEET_ARRIVAL_DEPARTURE),
        (
            "courtesy_signature",
            plan_courtesy_signature(CourtesySemantic.REMINDER),
            AmbientEffectFamily.COURTESY_SIGNATURE,
        ),
    ),
)
def test_every_renderer_neutral_family_compiles_to_a_named_accessible_effect(
    keyword: str,
    plan: object,
    family: AmbientEffectFamily,
) -> None:
    dispatch = compile_ambient_effect_dispatch(**{keyword: plan})

    assert dispatch.outputs
    assert all(output.family is family for output in dispatch.outputs)
    assert all(output.effect_identity for output in dispatch.outputs)
    assert all(output.accessibility_text for output in dispatch.outputs)
    assert all(output.static_fallback_program for output in dispatch.outputs)


def test_explicit_priority_selects_one_output_per_surface() -> None:
    dispatch = compile_ambient_effect_dispatch(
        ask_heartbeat=_ask(),
        recovery_grace=_recovery(),
        rainstick_idle=plan_rainstick_idle(preference_enabled=True),
    )

    assert tuple(output.surface for output in dispatch.outputs) == (
        AmbientEffectSurface.SCREEN_BAR,
        AmbientEffectSurface.SIDEPULSE_PRO,
        AmbientEffectSurface.SIDEPULSE_DOT,
    )
    assert all(output.family is AmbientEffectFamily.ASK_HEARTBEAT for output in dispatch.outputs)
    assert len(dispatch.suppressed) == 4


def test_reduce_motion_plans_compile_static_output_without_losing_identity_or_text() -> None:
    moving = compile_ambient_effect_dispatch(completion_meniscus=_meniscus())
    static = compile_ambient_effect_dispatch(completion_meniscus=_meniscus(reduce_motion=True))

    moving_output = moving.for_surface(AmbientEffectSurface.SCREEN_BAR)
    static_output = static.for_surface(AmbientEffectSurface.SCREEN_BAR)
    assert moving_output is not None and static_output is not None
    assert moving_output.effect_identity == static_output.effect_identity
    assert static_output.animated is False
    assert "Reduce Motion" in static_output.accessibility_text
    assert static_output.program == static_output.static_fallback_program


def test_dot_binary_heartbeat_owns_dot_while_richer_surface_effects_remain_elsewhere() -> None:
    dispatch = compile_ambient_effect_dispatch(
        ask_heartbeat=_ask(),
        dot_binary_heartbeat=plan_dot_binary_heartbeat(
            (SemanticEventKind.ASK,),
            secondary_policy=DotSecondaryPolicy.UNSEEN_NOTIFICATIONS,
            unseen_notification_present=True,
        ),
    )

    dot = dispatch.for_surface(AmbientEffectSurface.SIDEPULSE_DOT)
    screen = dispatch.for_surface(AmbientEffectSurface.SCREEN_BAR)
    assert dot is not None and screen is not None
    assert dot.family is AmbientEffectFamily.DOT_BINARY_HEARTBEAT
    assert screen.family is AmbientEffectFamily.ASK_HEARTBEAT
    assert "0:" in dot.program and "1:" in dot.program


def test_every_program_is_parser_valid_bounded_and_capped_at_two_hertz() -> None:
    dispatch = compile_ambient_effect_dispatch(
        glance_light=_glance(),
        firefly_completion=_firefly(),
        ask_heartbeat=_ask(),
        dot_binary_heartbeat=plan_dot_binary_heartbeat(
            (SemanticEventKind.ASK,),
            secondary_policy=DotSecondaryPolicy.FLEET_SIZE,
            fleet_size=4,
        ),
    )

    for output in dispatch.outputs:
        led_count = 2 if output.surface is AmbientEffectSurface.SIDEPULSE_DOT else 8
        _animation, problems = read_program(output.program, led_count=led_count)
        assert errors_only(problems) == ()
        assert len(output.program.splitlines()) <= 20
        assert len(output.program.encode("utf-8")) <= 512
        assert 0.0 <= output.max_flash_hz <= 2.0
        assert 0 < output.duration_ms <= MAX_AMBIENT_OUTPUT_DURATION_MS
        assert output.expires_after_ms == output.duration_ms


def test_semantic_colors_are_typed_normalized_and_do_not_mutate_inputs() -> None:
    colors = AmbientSemanticColors(work="#123abc")
    selection = route_semantic_effects(
        (SemanticEffectCandidate("semantic:work", SemanticEventKind.WORK),)
    )

    dispatch = compile_ambient_effect_dispatch(
        semantic_selection=selection,
        semantic_colors=colors,
    )

    assert colors.work == "#123ABC"
    assert all("#123ABC" in output.program for output in dispatch.outputs)
    with pytest.raises(FrozenInstanceError):
        dispatch.outputs = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        dispatch.outputs[0].program = "off"  # type: ignore[misc]


def test_suppressed_or_empty_plans_emit_nothing() -> None:
    dispatch = compile_ambient_effect_dispatch(
        semantic_selection=route_semantic_effects(()),
        rainstick_idle=plan_rainstick_idle(),
        milestone_odometer=plan_milestone_odometer(
            MilestoneOdometerPreferences(),
            MilestoneOdometerState(),
            (),
        ),
    )

    assert dispatch.outputs == ()
    assert dispatch.suppressed == ()
