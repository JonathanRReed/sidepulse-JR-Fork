"""Pure projection of cached primitive facts into the Why Light context."""

from __future__ import annotations

from .dnd_policy import DndProjection
from .presentation_policy import GlanceSemantic
from .why_light_context import (
    MAX_SUPPRESSION_COUNT,
    FocusDNDDecision,
    FocusObservation,
    FocusOutcome,
    FocusPolicy,
    GlobalSurfaceRole,
    LightSemantic,
    OutputTimingSource,
    ReduceMotionDecision,
    RendererTiming,
    SceneAvailability,
    SourceAge,
    SuppressionCounts,
    WhyLightContext,
    WinningPriority,
)

_SEMANTIC_PROJECTION = {
    GlanceSemantic.ATTENTION: (LightSemantic.ATTENTION, WinningPriority.P1),
    GlanceSemantic.FRESH_FAILURE: (
        LightSemantic.FRESH_FAILURE,
        WinningPriority.P2,
    ),
    GlanceSemantic.FRESH_COMPLETION: (
        LightSemantic.FRESH_COMPLETION,
        WinningPriority.P3,
    ),
    GlanceSemantic.ACTIVE: (LightSemantic.ACTIVE, WinningPriority.P4),
    GlanceSemantic.UNRESOLVED_FAILURE: (
        LightSemantic.UNRESOLVED_FAILURE,
        WinningPriority.P5,
    ),
    GlanceSemantic.CAPACITY: (LightSemantic.CAPACITY, WinningPriority.P6),
    GlanceSemantic.REST: (LightSemantic.REST, WinningPriority.P7),
}

_UNAVAILABLE_FOCUS = FocusDNDDecision(
    FocusObservation.UNAVAILABLE,
    FocusPolicy.UNAVAILABLE,
    FocusOutcome.UNAVAILABLE,
)


def project_why_light_context(
    *,
    selected_semantic: object,
    source_age_seconds: object,
    focus_observation_available: object,
    focus_active: object,
    focus_policy_suppresses: object,
    screen_bar_active: object,
    physical_surfaces_active: object,
    reduce_motion_enabled: object,
    motion_requested: object,
    renderer_sample_count: object,
    renderer_latest_ms: object,
    renderer_p50_ms: object,
    renderer_p95_ms: object,
    renderer_timing_source: object = OutputTimingSource.SCREEN_BAR_RENDERER,
    suppressed_attention: object = 0,
    suppressed_fresh_failure: object = 0,
    suppressed_fresh_completion: object = 0,
    suppressed_active: object = 0,
    suppressed_unresolved_failure: object = 0,
    suppressed_capacity: object = 0,
    dnd_projection: object = None,
) -> WhyLightContext:
    """Project already-cached, content-free facts without reading runtime state."""
    semantic, priority = _semantic_and_priority(selected_semantic)
    return WhyLightContext(
        selected_semantic=semantic,
        winning_priority=priority,
        source_age=_source_age(source_age_seconds),
        suppressions=SuppressionCounts(
            attention=_suppression_count(suppressed_attention),
            fresh_failure=_suppression_count(suppressed_fresh_failure),
            fresh_completion=_suppression_count(suppressed_fresh_completion),
            active=_suppression_count(suppressed_active),
            unresolved_failure=_suppression_count(suppressed_unresolved_failure),
            capacity=_suppression_count(suppressed_capacity),
        ),
        scene_availability=SceneAvailability.UNAVAILABLE,
        surface_role=_surface_role(
            screen_bar_active,
            physical_surfaces_active,
        ),
        focus_dnd=_focus_decision(
            focus_observation_available,
            focus_active,
            focus_policy_suppresses,
            dnd_projection,
        ),
        reduce_motion=_reduce_motion_decision(
            reduce_motion_enabled,
            motion_requested,
        ),
        renderer_timing=_renderer_timing(
            renderer_sample_count,
            renderer_latest_ms,
            renderer_p50_ms,
            renderer_p95_ms,
            renderer_timing_source,
        ),
    )


def _semantic_and_priority(
    value: object,
) -> tuple[LightSemantic, WinningPriority]:
    if not isinstance(value, GlanceSemantic):
        return LightSemantic.UNAVAILABLE, WinningPriority.UNAVAILABLE
    return _SEMANTIC_PROJECTION[value]


def _source_age(value: object) -> SourceAge:
    try:
        return SourceAge.available(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return SourceAge.unavailable()


def _suppression_count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SUPPRESSION_COUNT:
        return 0
    return value


def _surface_role(
    screen_bar_active: object,
    physical_surfaces_active: object,
) -> GlobalSurfaceRole:
    if type(screen_bar_active) is not bool or type(physical_surfaces_active) is not bool:
        return GlobalSurfaceRole.UNAVAILABLE
    if screen_bar_active and physical_surfaces_active:
        return GlobalSurfaceRole.SCREEN_BAR_AND_PHYSICAL
    if screen_bar_active:
        return GlobalSurfaceRole.SCREEN_BAR
    if physical_surfaces_active:
        return GlobalSurfaceRole.PHYSICAL
    return GlobalSurfaceRole.NONE


def _focus_decision(
    observation_available: object,
    active: object,
    policy_suppresses: object,
    dnd_projection: object,
) -> FocusDNDDecision:
    projection = (
        dnd_projection if type(dnd_projection) is DndProjection else None
    )
    dnd_facts = {
        "dnd_modes": (
            tuple(dict.fromkeys(projection.active_modes))
            if projection is not None
            else ()
        ),
        "dnd_sources": projection.active_sources if projection is not None else (),
        "dnd_return_epoch": (
            projection.next_transition_epoch if projection is not None else None
        ),
    }
    if not all(type(value) is bool for value in (observation_available, active, policy_suppresses)):
        return FocusDNDDecision(
            FocusObservation.UNAVAILABLE,
            FocusPolicy.UNAVAILABLE,
            FocusOutcome.UNAVAILABLE,
            **dnd_facts,
        )
    if not observation_available:
        return FocusDNDDecision(
            FocusObservation.UNAVAILABLE,
            FocusPolicy.UNAVAILABLE,
            FocusOutcome.UNAVAILABLE,
            **dnd_facts,
        )

    policy = FocusPolicy.SUPPRESS if policy_suppresses else FocusPolicy.ALLOW
    suppressed = active and policy_suppresses
    return FocusDNDDecision(
        FocusObservation.ACTIVE if active else FocusObservation.INACTIVE,
        policy,
        FocusOutcome.SUPPRESSED if suppressed else FocusOutcome.ALLOWED,
        **dnd_facts,
    )


def _reduce_motion_decision(
    reduce_motion_enabled: object,
    motion_requested: object,
) -> ReduceMotionDecision:
    if type(reduce_motion_enabled) is not bool or type(motion_requested) is not bool:
        return ReduceMotionDecision.UNAVAILABLE
    if not motion_requested:
        return ReduceMotionDecision.NO_MOTION_REQUESTED
    if reduce_motion_enabled:
        return ReduceMotionDecision.STATIC_SUBSTITUTED
    return ReduceMotionDecision.MOTION_UNCHANGED


def _renderer_timing(
    sample_count: object,
    latest_ms: object,
    p50_ms: object,
    p95_ms: object,
    source: object,
) -> RendererTiming:
    try:
        return RendererTiming.available(
            sample_count,  # type: ignore[arg-type]
            latest_ms,  # type: ignore[arg-type]
            p50_ms,  # type: ignore[arg-type]
            p95_ms,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        return RendererTiming.unavailable()


__all__ = ["project_why_light_context"]
