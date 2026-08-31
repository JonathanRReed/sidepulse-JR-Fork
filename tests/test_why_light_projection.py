from __future__ import annotations

import math

import pytest

from sidepulse.presentation_policy import GlanceSemantic
from sidepulse.why_light_context import (
    FocusDNDDecision,
    FocusObservation,
    FocusOutcome,
    FocusPolicy,
    GlobalSurfaceRole,
    LightSemantic,
    OutputTimingSource,
    ReduceMotionDecision,
    SceneAvailability,
    ValueAvailability,
    WinningPriority,
)
from sidepulse.why_light_projection import project_why_light_context


def _facts(**changes: object) -> dict[str, object]:
    facts: dict[str, object] = {
        "selected_semantic": GlanceSemantic.ACTIVE,
        "source_age_seconds": 12.5,
        "suppressed_attention": 0,
        "suppressed_fresh_failure": 0,
        "suppressed_fresh_completion": 0,
        "suppressed_active": 0,
        "suppressed_unresolved_failure": 0,
        "suppressed_capacity": 0,
        "focus_observation_available": True,
        "focus_active": False,
        "focus_policy_suppresses": False,
        "screen_bar_active": True,
        "physical_surfaces_active": False,
        "reduce_motion_enabled": False,
        "motion_requested": True,
        "renderer_sample_count": 4,
        "renderer_timing_source": OutputTimingSource.SCREEN_BAR_RENDERER,
        "renderer_latest_ms": 2.5,
        "renderer_p50_ms": 2.0,
        "renderer_p95_ms": 3.5,
    }
    facts.update(changes)
    return facts


@pytest.mark.parametrize(
    ("selected", "semantic", "priority"),
    (
        (GlanceSemantic.ATTENTION, LightSemantic.ATTENTION, WinningPriority.P1),
        (
            GlanceSemantic.FRESH_FAILURE,
            LightSemantic.FRESH_FAILURE,
            WinningPriority.P2,
        ),
        (
            GlanceSemantic.FRESH_COMPLETION,
            LightSemantic.FRESH_COMPLETION,
            WinningPriority.P3,
        ),
        (GlanceSemantic.ACTIVE, LightSemantic.ACTIVE, WinningPriority.P4),
        (
            GlanceSemantic.UNRESOLVED_FAILURE,
            LightSemantic.UNRESOLVED_FAILURE,
            WinningPriority.P5,
        ),
        (GlanceSemantic.CAPACITY, LightSemantic.CAPACITY, WinningPriority.P6),
        (GlanceSemantic.REST, LightSemantic.REST, WinningPriority.P7),
    ),
)
def test_selected_semantic_maps_to_the_canonical_priority_without_reselection(
    selected: GlanceSemantic,
    semantic: LightSemantic,
    priority: WinningPriority,
) -> None:
    context = project_why_light_context(**_facts(selected_semantic=selected))

    assert context.selected_semantic is semantic
    assert context.winning_priority is priority


def test_numeric_source_age_is_carried_without_reading_a_clock() -> None:
    context = project_why_light_context(**_facts(source_age_seconds=42.25))

    assert context.source_age.availability is ValueAvailability.AVAILABLE
    assert context.source_age.seconds == 42.25


@pytest.mark.parametrize(
    "invalid_age",
    (True, -0.1, math.nan, math.inf, "42", object()),
)
def test_malformed_source_age_becomes_explicitly_unavailable(
    invalid_age: object,
) -> None:
    context = project_why_light_context(**_facts(source_age_seconds=invalid_age))

    assert context.source_age.availability is ValueAvailability.UNAVAILABLE


def test_current_suppressions_are_projected_as_bounded_semantic_counts() -> None:
    context = project_why_light_context(
        **_facts(
            suppressed_attention=1,
            suppressed_fresh_failure=1,
            suppressed_fresh_completion=1,
            suppressed_active=1,
            suppressed_unresolved_failure=1,
            suppressed_capacity=1,
        )
    )

    assert context.suppressions.attention == 1
    assert context.suppressions.fresh_failure == 1
    assert context.suppressions.fresh_completion == 1
    assert context.suppressions.active == 1
    assert context.suppressions.unresolved_failure == 1
    assert context.suppressions.capacity == 1
    assert context.suppressions.total == 6


@pytest.mark.parametrize(
    "invalid_count",
    (True, -1, 1.5, math.inf, "1", object(), 10**12),
)
def test_malformed_or_unbounded_suppression_is_not_retained(
    invalid_count: object,
) -> None:
    context = project_why_light_context(
        **_facts(
            suppressed_attention=invalid_count,
            suppressed_fresh_failure=1,
        )
    )

    assert context.suppressions.attention == 0
    assert context.suppressions.fresh_failure == 1


@pytest.mark.parametrize(
    (
        "observation_available",
        "active",
        "suppresses",
        "expected",
    ),
    (
        (
            False,
            True,
            True,
            FocusDNDDecision(
                FocusObservation.UNAVAILABLE,
                FocusPolicy.UNAVAILABLE,
                FocusOutcome.UNAVAILABLE,
            ),
        ),
        (
            True,
            False,
            True,
            FocusDNDDecision(
                FocusObservation.INACTIVE,
                FocusPolicy.SUPPRESS,
                FocusOutcome.ALLOWED,
            ),
        ),
        (
            True,
            True,
            False,
            FocusDNDDecision(
                FocusObservation.ACTIVE,
                FocusPolicy.ALLOW,
                FocusOutcome.ALLOWED,
            ),
        ),
        (
            True,
            True,
            True,
            FocusDNDDecision(
                FocusObservation.ACTIVE,
                FocusPolicy.SUPPRESS,
                FocusOutcome.SUPPRESSED,
            ),
        ),
    ),
)
def test_focus_explanation_separates_observation_policy_and_outcome(
    observation_available: bool,
    active: bool,
    suppresses: bool,
    expected: FocusDNDDecision,
) -> None:
    context = project_why_light_context(
        **_facts(
            focus_observation_available=observation_available,
            focus_active=active,
            focus_policy_suppresses=suppresses,
        )
    )

    assert context.focus_dnd == expected


@pytest.mark.parametrize(
    ("screen_bar", "physical", "expected"),
    (
        (False, False, GlobalSurfaceRole.NONE),
        (True, False, GlobalSurfaceRole.SCREEN_BAR),
        (False, True, GlobalSurfaceRole.PHYSICAL),
        (True, True, GlobalSurfaceRole.SCREEN_BAR_AND_PHYSICAL),
    ),
)
def test_surface_role_describes_global_scope_without_a_device_identity(
    screen_bar: bool,
    physical: bool,
    expected: GlobalSurfaceRole,
) -> None:
    context = project_why_light_context(
        **_facts(
            screen_bar_active=screen_bar,
            physical_surfaces_active=physical,
        )
    )

    assert context.surface_role is expected


def test_scene_remains_unavailable_until_the_scene_owner_exists() -> None:
    context = project_why_light_context(**_facts())

    assert context.scene_availability is SceneAvailability.UNAVAILABLE


@pytest.mark.parametrize(
    ("reduce_motion", "motion_requested", "expected"),
    (
        (False, False, ReduceMotionDecision.NO_MOTION_REQUESTED),
        (True, False, ReduceMotionDecision.NO_MOTION_REQUESTED),
        (False, True, ReduceMotionDecision.MOTION_UNCHANGED),
        (True, True, ReduceMotionDecision.STATIC_SUBSTITUTED),
    ),
)
def test_reduce_motion_reports_substitution_only_for_requested_motion(
    reduce_motion: bool,
    motion_requested: bool,
    expected: ReduceMotionDecision,
) -> None:
    context = project_why_light_context(
        **_facts(
            reduce_motion_enabled=reduce_motion,
            motion_requested=motion_requested,
        )
    )

    assert context.reduce_motion is expected


def test_cached_renderer_timing_is_projected_without_a_second_store() -> None:
    context = project_why_light_context(
        **_facts(
            renderer_sample_count=8,
            renderer_latest_ms=4.0,
            renderer_p50_ms=2.0,
            renderer_p95_ms=5.0,
        )
    )

    assert context.renderer_timing.availability is ValueAvailability.AVAILABLE
    assert context.renderer_timing.source is OutputTimingSource.SCREEN_BAR_RENDERER
    assert context.renderer_timing.sample_count == 8
    assert context.renderer_timing.latest_ms == 4.0
    assert context.renderer_timing.p50_ms == 2.0
    assert context.renderer_timing.p95_ms == 5.0


@pytest.mark.parametrize(
    "changes",
    (
        {"renderer_sample_count": 0},
        {"renderer_sample_count": True},
        {"renderer_latest_ms": -1.0},
        {"renderer_p50_ms": math.nan},
        {"renderer_p95_ms": math.inf},
        {"renderer_p50_ms": 5.0, "renderer_p95_ms": 4.0},
    ),
)
def test_malformed_renderer_timing_becomes_explicitly_unavailable(
    changes: dict[str, object],
) -> None:
    context = project_why_light_context(**_facts(**changes))

    assert context.renderer_timing.availability is ValueAvailability.UNAVAILABLE


class _ContentTrap:
    def __str__(self) -> str:
        raise AssertionError("content-bearing inputs must not be stringified")


def test_content_bearing_objects_are_never_coerced_or_retained() -> None:
    trap = _ContentTrap()

    context = project_why_light_context(
        **_facts(
            selected_semantic=trap,
            source_age_seconds=trap,
            suppressed_attention=trap,
            focus_observation_available=trap,
            screen_bar_active=trap,
            reduce_motion_enabled=trap,
            renderer_sample_count=trap,
        )
    )

    assert context.selected_semantic is LightSemantic.UNAVAILABLE
    assert context.winning_priority is WinningPriority.UNAVAILABLE
    assert context.source_age.availability is ValueAvailability.UNAVAILABLE
    assert context.suppressions.attention == 0
    assert context.surface_role is GlobalSurfaceRole.UNAVAILABLE
    assert context.focus_dnd.observation is FocusObservation.UNAVAILABLE
    assert context.reduce_motion is ReduceMotionDecision.UNAVAILABLE
    assert context.renderer_timing.availability is ValueAvailability.UNAVAILABLE


def test_projection_api_has_no_content_bearing_identity_arguments() -> None:
    with pytest.raises(TypeError):
        project_why_light_context(
            **_facts(),
            device_id="not-accepted",
        )
