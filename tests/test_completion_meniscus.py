from __future__ import annotations

import pytest

from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.capacity_types import SourceKey
from sidepulse.clear_agents import CompletionPresentationKey
from sidepulse.completion_meniscus import (
    RIPPLE_DURATION_MS,
    STATIC_HIGHLIGHT_DURATION_MS,
    CompletionMeniscusGeometry,
    CompletionMeniscusMode,
    CompletionMeniscusSurface,
    SelectedUnseenCompletionEvidence,
    plan_completion_meniscus,
)


def _completion(*, completed_at: float = 100.0) -> CompletionPresentationKey:
    return CompletionPresentationKey(
        SourceKey("codex", "hooks", "local:test", "live_agent_events"),
        "codex:session:alpha",
        "Stop",
        completed_at,
    )


def _evidence(*, completed_at: float = 100.0) -> SelectedUnseenCompletionEvidence:
    return SelectedUnseenCompletionEvidence(_completion(completed_at=completed_at))


@pytest.mark.parametrize(
    "surface",
    (CompletionMeniscusSurface.ALCOVE, CompletionMeniscusSurface.SCREEN_BAR),
)
def test_one_finite_ripple_expands_from_exact_surface_center(surface) -> None:
    geometry = CompletionMeniscusGeometry(surface, 20.0, 8.0, 200.0, 24.0)

    plan = plan_completion_meniscus(
        _evidence(),
        geometry,
        AccessibilityDisplayPreferences(),
    )

    assert plan.mode is CompletionMeniscusMode.CENTER_OUT_RIPPLE
    assert plan.duration_ms == RIPPLE_DURATION_MS == 900
    assert plan.passes == 1
    assert plan.loops == 0
    assert plan.release_to_live_surface is True
    assert [frame.elapsed_ms for frame in plan.frames] == [0, 180, 450, 702, 900]
    assert {frame.center_x for frame in plan.frames} == {120.0}
    assert {frame.center_y for frame in plan.frames} == {20.0}
    assert [frame.radius for frame in plan.frames] == sorted(
        frame.radius for frame in plan.frames
    )
    assert plan.frames[0].radius == 0.0
    assert plan.frames[-1].radius == 100.0
    assert plan.frames[-1].crest_height == 0.0
    assert plan.frames[-1].opacity == 0.0


def test_reduce_motion_is_a_brief_static_center_highlight() -> None:
    geometry = CompletionMeniscusGeometry(
        CompletionMeniscusSurface.ALCOVE,
        10.0,
        4.0,
        100.0,
        20.0,
    )

    plan = plan_completion_meniscus(
        _evidence(),
        geometry,
        AccessibilityDisplayPreferences(reduce_motion=True),
    )

    assert plan.mode is CompletionMeniscusMode.STATIC_CENTER_HIGHLIGHT
    assert plan.duration_ms == STATIC_HIGHLIGHT_DURATION_MS == 650
    assert plan.passes == 0
    assert plan.loops == 0
    assert [frame.elapsed_ms for frame in plan.frames] == [0, 650]
    assert plan.frames[0].center_x == plan.frames[1].center_x == 60.0
    assert plan.frames[0].center_y == plan.frames[1].center_y == 14.0
    assert plan.frames[0].radius == plan.frames[1].radius
    assert plan.frames[0].crest_height == plan.frames[1].crest_height
    assert plan.frames[0].intensity == plan.frames[1].intensity
    assert "Reduce Motion" in plan.accessibility.help
    assert "travels" not in plan.accessibility.help


def test_accessibility_preferences_are_projected_without_work_content() -> None:
    plan = plan_completion_meniscus(
        _evidence(),
        CompletionMeniscusGeometry(
            CompletionMeniscusSurface.SCREEN_BAR,
            0.0,
            0.0,
            160.0,
            18.0,
        ),
        AccessibilityDisplayPreferences(
            reduce_transparency=True,
            increase_contrast=True,
            differentiate_without_color=True,
        ),
    )

    assert plan.appearance.opaque_fill is True
    assert plan.appearance.high_contrast is True
    assert plan.appearance.center_outline is True
    assert all(
        frame.opacity == 1.0 for frame in plan.frames if frame.intensity > 0.0
    )
    assert max(frame.intensity for frame in plan.frames) == 1.0
    accessibility_text = " ".join(
        (
            plan.accessibility.label,
            plan.accessibility.value,
            plan.accessibility.help,
        )
    )
    assert "codex" not in accessibility_text.casefold()
    assert "alpha" not in accessibility_text.casefold()
    assert "stop" not in accessibility_text.casefold()


def test_exact_completion_identity_is_preserved_without_entering_surface_text() -> None:
    first = plan_completion_meniscus(
        _evidence(completed_at=100.0),
        CompletionMeniscusGeometry(
            CompletionMeniscusSurface.SCREEN_BAR,
            0.0,
            0.0,
            80.0,
            10.0,
        ),
        AccessibilityDisplayPreferences(),
    )
    second = plan_completion_meniscus(
        _evidence(completed_at=101.0),
        first.geometry,
        AccessibilityDisplayPreferences(),
    )

    assert first.completion_key != second.completion_key
    assert first.completion_key.completed_at_epoch == 100.0
    assert second.completion_key.completed_at_epoch == 101.0
    assert first.accessibility == second.accessibility


def test_geometry_rejects_unknown_or_unbounded_surface_facts() -> None:
    with pytest.raises(ValueError, match="surface must be known"):
        CompletionMeniscusGeometry(  # type: ignore[arg-type]
            "screen_bar", 0.0, 0.0, 80.0, 10.0
        )

    with pytest.raises(ValueError, match="dimensions are out of bounds"):
        CompletionMeniscusGeometry(
            CompletionMeniscusSurface.SCREEN_BAR,
            0.0,
            0.0,
            0.0,
            10.0,
        )


def test_planner_requires_typed_selected_unseen_evidence_and_preferences() -> None:
    geometry = CompletionMeniscusGeometry(
        CompletionMeniscusSurface.SCREEN_BAR,
        0.0,
        0.0,
        80.0,
        10.0,
    )

    with pytest.raises(ValueError, match="evidence must be exact and selected"):
        plan_completion_meniscus(  # type: ignore[arg-type]
            _completion(),
            geometry,
            AccessibilityDisplayPreferences(),
        )

    with pytest.raises(ValueError, match="preferences must be typed"):
        plan_completion_meniscus(  # type: ignore[arg-type]
            _evidence(),
            geometry,
            object(),
        )
