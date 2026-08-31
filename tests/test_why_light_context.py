from __future__ import annotations

import importlib
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sidepulse.dnd_policy import DndMode, DndSource


def _why():
    return importlib.import_module("sidepulse.why_light_context")


def _available_context(why):
    return why.WhyLightContext(
        selected_semantic=why.LightSemantic.ATTENTION,
        winning_priority=why.WinningPriority.P1,
        source_age=why.SourceAge.available(12.25),
        suppressions=why.SuppressionCounts(
            attention=1,
            fresh_failure=2,
            fresh_completion=3,
            active=4,
            unresolved_failure=5,
            capacity=6,
        ),
        scene_availability=why.SceneAvailability.UNAVAILABLE,
        surface_role=why.GlobalSurfaceRole.SCREEN_BAR_AND_PHYSICAL,
        focus_dnd=why.FocusDNDDecision(
            observation=why.FocusObservation.ACTIVE,
            policy=why.FocusPolicy.SUPPRESS,
            outcome=why.FocusOutcome.SUPPRESSED,
        ),
        reduce_motion=why.ReduceMotionDecision.STATIC_SUBSTITUTED,
        renderer_timing=why.RendererTiming.available(
            sample_count=8,
            latest_ms=3.25,
            p50_ms=2.5,
            p95_ms=4.75,
            source=why.OutputTimingSource.SCREEN_BAR_RENDERER,
        ),
    )


def _unavailable_context(why):
    return why.WhyLightContext(
        selected_semantic=why.LightSemantic.UNAVAILABLE,
        winning_priority=why.WinningPriority.UNAVAILABLE,
        source_age=why.SourceAge.unavailable(),
        suppressions=why.SuppressionCounts(),
        scene_availability=why.SceneAvailability.UNAVAILABLE,
        surface_role=why.GlobalSurfaceRole.UNAVAILABLE,
        focus_dnd=why.FocusDNDDecision(
            observation=why.FocusObservation.UNAVAILABLE,
            policy=why.FocusPolicy.UNAVAILABLE,
            outcome=why.FocusOutcome.UNAVAILABLE,
        ),
        reduce_motion=why.ReduceMotionDecision.UNAVAILABLE,
        renderer_timing=why.RendererTiming.unavailable(),
    )


def test_context_is_immutable_and_accepts_only_bounded_typed_state() -> None:
    """A mutable or stringly context could retain content-bearing runtime data."""
    why = _why()
    context = _available_context(why)

    assert not hasattr(context, "__dict__")
    with pytest.raises(FrozenInstanceError):
        context.selected_semantic = why.LightSemantic.ACTIVE
    with pytest.raises(TypeError):
        why.WhyLightContext(
            selected_semantic="prompt text",
            winning_priority=context.winning_priority,
            source_age=context.source_age,
            suppressions=context.suppressions,
            scene_availability=context.scene_availability,
            surface_role=context.surface_role,
            focus_dnd=context.focus_dnd,
            reduce_motion=context.reduce_motion,
            renderer_timing=context.renderer_timing,
        )


def test_context_rejects_content_bearing_subclasses() -> None:
    """A subclass must not smuggle an extra prompt, id, name, path, URL, or payload field."""
    why = _why()
    context = _available_context(why)

    class ContentBearingSourceAge(why.SourceAge):
        __slots__ = ("payload",)

    source_age = ContentBearingSourceAge(why.ValueAvailability.AVAILABLE, 1.0)
    object.__setattr__(source_age, "payload", "https://example.invalid/private")

    with pytest.raises(TypeError):
        why.WhyLightContext(
            selected_semantic=context.selected_semantic,
            winning_priority=context.winning_priority,
            source_age=source_age,
            suppressions=context.suppressions,
            scene_availability=context.scene_availability,
            surface_role=context.surface_role,
            focus_dnd=context.focus_dnd,
            reduce_motion=context.reduce_motion,
            renderer_timing=context.renderer_timing,
        )


def test_all_context_enums_are_finite_and_include_explicit_unavailable_states() -> None:
    """An open-ended string state could leak names, ids, paths, URLs, or payloads."""
    why = _why()

    assert {item.value for item in why.LightSemantic} == {
        "attention",
        "fresh_failure",
        "fresh_completion",
        "active",
        "unresolved_failure",
        "capacity",
        "rest",
        "unavailable",
    }
    assert {item.value for item in why.WinningPriority} == {0, 1, 2, 3, 4, 5, 6, 7}
    assert {item.value for item in why.SceneAvailability} == {"unavailable"}
    assert {item.value for item in why.GlobalSurfaceRole} == {
        "none",
        "screen_bar",
        "physical",
        "screen_bar_and_physical",
        "unavailable",
    }
    assert {item.value for item in why.FocusObservation} == {
        "active",
        "inactive",
        "unavailable",
    }
    assert {item.value for item in why.FocusPolicy} == {
        "allow",
        "suppress",
        "unavailable",
    }
    assert {item.value for item in why.FocusOutcome} == {
        "allowed",
        "suppressed",
        "unavailable",
    }
    assert {item.value for item in why.ReduceMotionDecision} == {
        "unavailable",
        "no_motion_requested",
        "motion_unchanged",
        "static_substituted",
    }
    assert {item.value for item in why.ValueAvailability} == {
        "available",
        "unavailable",
    }
    assert {item.value for item in why.OutputTimingSource} == {
        "screen_bar_renderer",
        "physical_hardware_write",
        "unavailable",
    }


def test_formatter_has_a_fixed_content_free_shape_for_available_state() -> None:
    """Variable formatter sections could expand with arbitrary runtime content."""
    why = _why()

    assert why.format_why_light_context(_available_context(why)) == (
        "Current light context\n"
        "Semantic: Attention needed\n"
        "Winning priority: P1\n"
        "Source age: 12.2 seconds\n"
        "Current suppressions: total 21\n"
        "  Attention 1; fresh failure 2; fresh completion 3\n"
        "  Active 4; unresolved failure 5; capacity 6\n"
        "Scene: Unavailable\n"
        "Global surface role: Screen Bar and physical devices\n"
        "Focus/DND: Active; policy Suppress; decision Suppressed\n"
        "Reduce Motion: Static signal substituted\n"
        "Screen Bar renderer timing: latest 3.2 ms; p50 2.5 ms; "
        "p95 4.8 ms; samples 8"
    )


def test_formatter_keeps_every_fact_row_readable_at_the_fixed_panel_width() -> None:
    """A very long logical row soft-wraps into an ambiguous count block."""
    why = _why()

    lines = why.format_why_light_context(_available_context(why)).splitlines()

    assert max(map(len, lines)) <= 88


def test_formatter_adds_bounded_dnd_facts_without_changing_the_fixed_shape() -> None:
    why = _why()
    context = _available_context(why)
    focus = why.FocusDNDDecision(
        observation=why.FocusObservation.ACTIVE,
        policy=why.FocusPolicy.SUPPRESS,
        outcome=why.FocusOutcome.SUPPRESSED,
        dnd_modes=(DndMode.MUTE,),
        dnd_sources=(DndSource.MANUAL,),
        dnd_return_epoch=1_800_000_000.0,
    )
    updated = why.WhyLightContext(
        selected_semantic=context.selected_semantic,
        winning_priority=context.winning_priority,
        source_age=context.source_age,
        suppressions=context.suppressions,
        scene_availability=context.scene_availability,
        surface_role=context.surface_role,
        focus_dnd=focus,
        reduce_motion=context.reduce_motion,
        renderer_timing=context.renderer_timing,
    )

    original_lines = why.format_why_light_context(context).splitlines()
    rendered_lines = why.format_why_light_context(updated).splitlines()

    assert len(rendered_lines) == len(original_lines)
    assert (
        rendered_lines[9]
        == "Focus/DND: Active; policy Suppress; decision Suppressed; "
        "DND Mute; source Manual; returns 2027-01-15 08:00Z"
    )


def test_formatter_honestly_formats_every_unavailable_value() -> None:
    """An unavailable observation must never be presented as inactive or off."""
    why = _why()

    assert why.format_why_light_context(_unavailable_context(why)) == (
        "Current light context\n"
        "Semantic: Unavailable\n"
        "Winning priority: Unavailable\n"
        "Source age: Unavailable\n"
        "Current suppressions: total 0\n"
        "  Attention 0; fresh failure 0; fresh completion 0\n"
        "  Active 0; unresolved failure 0; capacity 0\n"
        "Scene: Unavailable\n"
        "Global surface role: Unavailable\n"
        "Focus/DND: Unavailable; policy Unavailable; decision Unavailable\n"
        "Reduce Motion: Unavailable\n"
        "Output timing: Unavailable"
    )


@pytest.mark.parametrize(
    ("observation", "policy", "outcome", "expected"),
    [
        ("INACTIVE", "SUPPRESS", "ALLOWED", "Inactive; policy Suppress; decision Allowed"),
        ("ACTIVE", "ALLOW", "ALLOWED", "Active; policy Allow; decision Allowed"),
        ("ACTIVE", "SUPPRESS", "ALLOWED", "Active; policy Suppress; decision Allowed"),
        ("ACTIVE", "SUPPRESS", "SUPPRESSED", "Active; policy Suppress; decision Suppressed"),
    ],
)
def test_focus_formatter_distinguishes_observation_policy_and_actual_decision(
    observation: str,
    policy: str,
    outcome: str,
    expected: str,
) -> None:
    """Collapsing Focus state would misreport unreadable or policy-exempt decisions."""
    why = _why()
    context = _available_context(why)
    focus = why.FocusDNDDecision(
        observation=why.FocusObservation[observation],
        policy=why.FocusPolicy[policy],
        outcome=why.FocusOutcome[outcome],
    )

    updated = why.WhyLightContext(
        selected_semantic=context.selected_semantic,
        winning_priority=context.winning_priority,
        source_age=context.source_age,
        suppressions=context.suppressions,
        scene_availability=context.scene_availability,
        surface_role=context.surface_role,
        focus_dnd=focus,
        reduce_motion=context.reduce_motion,
        renderer_timing=context.renderer_timing,
    )

    focus_line = next(
        line
        for line in why.format_why_light_context(updated).splitlines()
        if line.startswith("Focus/DND:")
    )
    assert focus_line == f"Focus/DND: {expected}"


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("NO_MOTION_REQUESTED", "No motion requested"),
        ("MOTION_UNCHANGED", "Motion requested; no substitution"),
        ("STATIC_SUBSTITUTED", "Static signal substituted"),
        ("UNAVAILABLE", "Unavailable"),
    ],
)
def test_reduce_motion_never_claims_substitution_when_no_motion_was_requested(
    decision: str,
    expected: str,
) -> None:
    """No-motion output must remain distinct from a static accessibility substitute."""
    why = _why()
    context = _available_context(why)
    updated = why.WhyLightContext(
        selected_semantic=context.selected_semantic,
        winning_priority=context.winning_priority,
        source_age=context.source_age,
        suppressions=context.suppressions,
        scene_availability=context.scene_availability,
        surface_role=context.surface_role,
        focus_dnd=context.focus_dnd,
        reduce_motion=why.ReduceMotionDecision[decision],
        renderer_timing=context.renderer_timing,
    )

    motion_line = next(
        line
        for line in why.format_why_light_context(updated).splitlines()
        if line.startswith("Reduce Motion:")
    )
    assert motion_line == f"Reduce Motion: {expected}"


@pytest.mark.parametrize(
    ("factory", "arguments"),
    [
        ("SourceAge", (-0.1,)),
        ("SourceAge", (2_592_000.1,)),
        ("RendererTiming", (0, 1.0, 1.0, 1.0)),
        ("RendererTiming", (1, -0.1, 1.0, 1.0)),
        ("RendererTiming", (1, 1.0, 2.0, 1.0)),
    ],
)
def test_numeric_context_rejects_out_of_bounds_values(factory: str, arguments: tuple[object, ...]) -> None:
    """Unbounded ages, counts, or timings would break the bounded snapshot contract."""
    why = _why()

    with pytest.raises(ValueError):
        if factory == "SourceAge":
            why.SourceAge.available(*arguments)
        else:
            why.RendererTiming.available(
                *arguments,
                source=why.OutputTimingSource.SCREEN_BAR_RENDERER,
            )


def test_suppression_counts_reject_out_of_bounds_values() -> None:
    """No current suppression category may grow beyond the fixed display bound."""
    why = _why()

    with pytest.raises(ValueError):
        why.SuppressionCounts(attention=why.MAX_SUPPRESSION_COUNT + 1)


def test_focus_decision_rejects_inconsistent_unknown_and_suppressed_states() -> None:
    """Unavailable or inactive Focus observations cannot honestly claim suppression."""
    why = _why()

    with pytest.raises(ValueError):
        why.FocusDNDDecision(
            observation=why.FocusObservation.UNAVAILABLE,
            policy=why.FocusPolicy.SUPPRESS,
            outcome=why.FocusOutcome.SUPPRESSED,
        )
    with pytest.raises(ValueError):
        why.FocusDNDDecision(
            observation=why.FocusObservation.INACTIVE,
            policy=why.FocusPolicy.SUPPRESS,
            outcome=why.FocusOutcome.SUPPRESSED,
        )
    with pytest.raises(ValueError):
        why.FocusDNDDecision(
            observation=why.FocusObservation.ACTIVE,
            policy=why.FocusPolicy.ALLOW,
            outcome=why.FocusOutcome.SUPPRESSED,
        )


def test_context_import_is_appkit_free() -> None:
    """The pure context module must remain usable without loading macOS UI bindings."""
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "sys.path.insert(0, 'src'); "
                "import sidepulse.why_light_context; "
                "raise SystemExit(1 if 'AppKit' in sys.modules else 0)"
            ),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
