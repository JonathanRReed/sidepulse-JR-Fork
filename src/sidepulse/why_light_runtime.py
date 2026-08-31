"""Cached runtime projection for the current-light explanation."""

from __future__ import annotations

import math

from .accessibility_display import AccessibilityDisplayPreferences
from .dnd_policy import DndProjection
from .focus_status import FocusActivity, FocusAuthorization, FocusStatusObservation
from .presentation_policy import (
    FiniteCue,
    FiniteCueState,
    GlanceOverrideReason,
    GlanceSemantic,
    ResolvedGlance,
)
from .why_light_context import OutputTimingSource
from .why_light_projection import project_why_light_context


def source_age_seconds(controller: object) -> float | None:
    """Return the oldest visible cached source age without probing a source."""
    statuses = getattr(getattr(controller, "last_snapshot", None), "statuses", ())
    ages: list[float] = []
    for status in statuses if isinstance(statuses, tuple) else ():
        age_seconds = getattr(status, "age_seconds", None)
        if not callable(age_seconds):
            continue
        try:
            age = age_seconds()
        except Exception:
            continue
        if (
            isinstance(age, (int, float))
            and not isinstance(age, bool)
            and math.isfinite(float(age))
            and float(age) >= 0.0
        ):
            ages.append(float(age))
    return max(ages, default=None)


def suppressed_semantic_counts(controller: object) -> dict[GlanceSemantic, int]:
    """Count only the bounded current cue plan, never content-bearing keys."""
    counts = {semantic: 0 for semantic in GlanceSemantic}
    finite = getattr(controller, "_status_finite_cues", None)
    candidates = getattr(controller, "_status_cue_candidates", ())
    if type(finite) is not FiniteCueState or type(candidates) is not tuple:
        return counts

    suppressed: list[FiniteCue] = []
    if type(finite.pending) is FiniteCue:
        suppressed.append(finite.pending)
    if finite.overflowed:
        retained = tuple(
            cue
            for cue in (finite.active, finite.pending)
            if type(cue) is FiniteCue
        )
        suppressed.extend(
            cue
            for cue in candidates
            if type(cue) is FiniteCue and cue not in retained
        )

    seen: set[tuple[GlanceSemantic, str]] = set()
    for cue in suppressed:
        identity = (cue.semantic, cue.event_key)
        if identity in seen:
            continue
        seen.add(identity)
        counts[cue.semantic] = min(99, counts[cue.semantic] + 1)
    return counts


def project_current_why_light_context(
    controller: object,
    *,
    screen_bar_feature_enabled: bool,
    focus_observation_ttl_seconds: float,
    source_age: float | None = None,
    renderer_sample_count: int = 0,
    renderer_latest_ms: float = 0.0,
    renderer_p50_ms: float = 0.0,
    renderer_p95_ms: float = 0.0,
    renderer_timing_source: OutputTimingSource = OutputTimingSource.SCREEN_BAR_RENDERER,
):
    """Project already-cached controller facts into the immutable context."""
    glance = getattr(controller, "_current_resolved_glance", None)
    semantic = glance.semantic if type(glance) is ResolvedGlance else None
    if source_age is None:
        source_age = source_age_seconds(controller)

    suppressed = suppressed_semantic_counts(controller)
    preferences = getattr(controller, "_accessibility_display_preferences", None)
    reduce_motion = bool(
        type(preferences) is AccessibilityDisplayPreferences
        and preferences.reduce_motion
    )
    candidates = getattr(controller, "_status_cue_candidates", ())
    motion_requested = bool(
        (type(glance) is ResolvedGlance and glance.cue is not None)
        or (
            type(glance) is ResolvedGlance
            and glance.semantic is GlanceSemantic.ACTIVE
        )
        or (
            type(candidates) is tuple
            and any(type(cue) is FiniteCue for cue in candidates)
        )
    )

    dnd_controller = getattr(controller, "dnd_controller", None)
    focus_observation = getattr(dnd_controller, "focus_observation", None)
    if type(focus_observation) is not FocusStatusObservation:
        focus_observation = FocusStatusObservation(
            FocusAuthorization.UNAVAILABLE,
            FocusActivity.UNAVAILABLE,
        )
    focus_observation_available = bool(
        focus_observation.authorization is FocusAuthorization.AUTHORIZED
        and focus_observation.activity
        in (FocusActivity.ACTIVE, FocusActivity.INACTIVE)
    )
    focus_active = focus_observation.activity is FocusActivity.ACTIVE
    focus_policy_suppresses = bool(
        focus_active
        and type(glance) is ResolvedGlance
        and glance.override_reason is GlanceOverrideReason.FOCUS
    )
    dnd_projection = getattr(dnd_controller, "projection", None)
    if type(dnd_projection) is not DndProjection:
        dnd_projection = None

    settings = getattr(controller, "settings", None)
    screen_bar_active = bool(
        screen_bar_feature_enabled
        and getattr(settings, "virtual_status_device_enabled", False)
    )
    physical_surfaces_active = bool(
        getattr(controller, "leds_enabled", False)
        and getattr(controller, "_device_inventory_candidates", ())
    )
    return project_why_light_context(
        selected_semantic=semantic,
        source_age_seconds=source_age,
        suppressed_attention=suppressed[GlanceSemantic.ATTENTION],
        suppressed_fresh_failure=suppressed[GlanceSemantic.FRESH_FAILURE],
        suppressed_fresh_completion=suppressed[GlanceSemantic.FRESH_COMPLETION],
        suppressed_active=suppressed[GlanceSemantic.ACTIVE],
        suppressed_unresolved_failure=suppressed[
            GlanceSemantic.UNRESOLVED_FAILURE
        ],
        suppressed_capacity=suppressed[GlanceSemantic.CAPACITY],
        focus_observation_available=focus_observation_available,
        focus_active=focus_active,
        focus_policy_suppresses=focus_policy_suppresses,
        dnd_projection=dnd_projection,
        screen_bar_active=screen_bar_active,
        physical_surfaces_active=physical_surfaces_active,
        reduce_motion_enabled=reduce_motion,
        motion_requested=motion_requested,
        renderer_sample_count=renderer_sample_count,
        renderer_latest_ms=renderer_latest_ms,
        renderer_p50_ms=renderer_p50_ms,
        renderer_p95_ms=renderer_p95_ms,
        renderer_timing_source=renderer_timing_source,
    )


__all__ = [
    "project_current_why_light_context",
    "source_age_seconds",
    "suppressed_semantic_counts",
]
