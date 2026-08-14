from __future__ import annotations

import math
from dataclasses import replace

import pytest

from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.presentation_policy import (
    CapacityGlance,
    FiniteCue,
    GlanceInputs,
    GlanceOverrideReason,
    GlanceSemantic,
    MotionClass,
    PresentationProgram,
    SemanticGlyph,
    compose_presentation_program,
    enforce_temporal_safety,
    resolve_glance,
)
from sidepulse.temporal_safety import (
    CalibrationState,
    StaticSemanticFallback,
    TemporalFrame,
    TemporalProgram,
)


def _cue(
    semantic: GlanceSemantic,
    *,
    event_key: str | object = "episode-1",
    repetitions: int | object = 2,
    duration_seconds: float | object = 0.4,
) -> FiniteCue:
    return FiniteCue(
        event_key=event_key,  # type: ignore[arg-type]
        semantic=semantic,
        repetitions=repetitions,  # type: ignore[arg-type]
        duration_seconds=duration_seconds,  # type: ignore[arg-type]
    )


def _inputs_for(semantic: GlanceSemantic) -> GlanceInputs:
    values: dict[str, object] = {
        "actionable_episode_key": None,
        "fresh_failure": None,
        "fresh_completion": None,
        "active": False,
        "unresolved_failure": False,
        "capacity": None,
    }
    if semantic is GlanceSemantic.ATTENTION:
        values["actionable_episode_key"] = "attention-episode"
    elif semantic is GlanceSemantic.FRESH_FAILURE:
        values["fresh_failure"] = _cue(GlanceSemantic.FRESH_FAILURE)
    elif semantic is GlanceSemantic.FRESH_COMPLETION:
        values["fresh_completion"] = _cue(GlanceSemantic.FRESH_COMPLETION)
    elif semantic is GlanceSemantic.ACTIVE:
        values["active"] = True
    elif semantic is GlanceSemantic.UNRESOLVED_FAILURE:
        values["unresolved_failure"] = True
    elif semantic is GlanceSemantic.CAPACITY:
        values["capacity"] = CapacityGlance("codex", 0.25)
    return GlanceInputs(**values)  # type: ignore[arg-type]


def _combined_inputs(*semantics: GlanceSemantic) -> GlanceInputs:
    requested = set(semantics)
    return GlanceInputs(
        actionable_episode_key=(
            "attention-episode" if GlanceSemantic.ATTENTION in requested else None
        ),
        fresh_failure=(
            _cue(GlanceSemantic.FRESH_FAILURE, event_key="failure-episode")
            if GlanceSemantic.FRESH_FAILURE in requested
            else None
        ),
        fresh_completion=(
            _cue(GlanceSemantic.FRESH_COMPLETION, event_key="completion-episode")
            if GlanceSemantic.FRESH_COMPLETION in requested
            else None
        ),
        active=GlanceSemantic.ACTIVE in requested,
        unresolved_failure=GlanceSemantic.UNRESOLVED_FAILURE in requested,
        capacity=(
            CapacityGlance("codex", 0.25)
            if GlanceSemantic.CAPACITY in requested
            else None
        ),
    )


def _resolve(inputs: GlanceInputs, *, reduce_motion: bool = False):
    return resolve_glance(
        inputs,
        presentation_time=10.0,
        relay_epoch=2.0,
        preferences=AccessibilityDisplayPreferences(reduce_motion=reduce_motion),
    )


PAIRWISE_PRIORITY_CASES = (
    (GlanceSemantic.ATTENTION, GlanceSemantic.FRESH_FAILURE, GlanceSemantic.ATTENTION),
    (GlanceSemantic.ATTENTION, GlanceSemantic.FRESH_COMPLETION, GlanceSemantic.ATTENTION),
    (GlanceSemantic.ATTENTION, GlanceSemantic.ACTIVE, GlanceSemantic.ATTENTION),
    (GlanceSemantic.ATTENTION, GlanceSemantic.UNRESOLVED_FAILURE, GlanceSemantic.ATTENTION),
    (GlanceSemantic.ATTENTION, GlanceSemantic.CAPACITY, GlanceSemantic.ATTENTION),
    (GlanceSemantic.ATTENTION, GlanceSemantic.REST, GlanceSemantic.ATTENTION),
    (GlanceSemantic.FRESH_FAILURE, GlanceSemantic.FRESH_COMPLETION, GlanceSemantic.FRESH_FAILURE),
    (GlanceSemantic.FRESH_FAILURE, GlanceSemantic.ACTIVE, GlanceSemantic.FRESH_FAILURE),
    (GlanceSemantic.FRESH_FAILURE, GlanceSemantic.UNRESOLVED_FAILURE, GlanceSemantic.FRESH_FAILURE),
    (GlanceSemantic.FRESH_FAILURE, GlanceSemantic.CAPACITY, GlanceSemantic.FRESH_FAILURE),
    (GlanceSemantic.FRESH_FAILURE, GlanceSemantic.REST, GlanceSemantic.FRESH_FAILURE),
    (GlanceSemantic.FRESH_COMPLETION, GlanceSemantic.ACTIVE, GlanceSemantic.FRESH_COMPLETION),
    (
        GlanceSemantic.FRESH_COMPLETION,
        GlanceSemantic.UNRESOLVED_FAILURE,
        GlanceSemantic.FRESH_COMPLETION,
    ),
    (GlanceSemantic.FRESH_COMPLETION, GlanceSemantic.CAPACITY, GlanceSemantic.FRESH_COMPLETION),
    (GlanceSemantic.FRESH_COMPLETION, GlanceSemantic.REST, GlanceSemantic.FRESH_COMPLETION),
    (GlanceSemantic.ACTIVE, GlanceSemantic.UNRESOLVED_FAILURE, GlanceSemantic.ACTIVE),
    (GlanceSemantic.ACTIVE, GlanceSemantic.CAPACITY, GlanceSemantic.ACTIVE),
    (GlanceSemantic.ACTIVE, GlanceSemantic.REST, GlanceSemantic.ACTIVE),
    (GlanceSemantic.UNRESOLVED_FAILURE, GlanceSemantic.CAPACITY, GlanceSemantic.UNRESOLVED_FAILURE),
    (GlanceSemantic.UNRESOLVED_FAILURE, GlanceSemantic.REST, GlanceSemantic.UNRESOLVED_FAILURE),
    (GlanceSemantic.CAPACITY, GlanceSemantic.REST, GlanceSemantic.CAPACITY),
)


@pytest.mark.parametrize(("higher", "lower", "expected"), PAIRWISE_PRIORITY_CASES)
def test_pairwise_priority_is_the_exact_seven_level_contract(
    higher: GlanceSemantic,
    lower: GlanceSemantic,
    expected: GlanceSemantic,
) -> None:
    resolved = _resolve(_combined_inputs(higher, lower))

    assert resolved.semantic is expected


def test_all_inputs_resolve_to_actionable_attention() -> None:
    resolved = _resolve(
        _combined_inputs(
            GlanceSemantic.ATTENTION,
            GlanceSemantic.FRESH_FAILURE,
            GlanceSemantic.FRESH_COMPLETION,
            GlanceSemantic.ACTIVE,
            GlanceSemantic.UNRESOLVED_FAILURE,
            GlanceSemantic.CAPACITY,
        )
    )

    assert resolved.semantic is GlanceSemantic.ATTENTION
    assert resolved.glyph is SemanticGlyph.FULL_ANCHOR
    assert resolved.cue == FiniteCue(
        event_key="attention-episode",
        semantic=GlanceSemantic.ATTENTION,
        repetitions=2,
        duration_seconds=0.24,
    )


@pytest.mark.parametrize(
    ("reason", "semantic"),
    (
        (GlanceOverrideReason.FOCUS, None),
        (GlanceOverrideReason.NONE, GlanceSemantic.REST),
        ("focus", GlanceSemantic.REST),
    ),
)
def test_incomplete_or_untyped_override_cannot_outrank_attention(
    reason: GlanceOverrideReason | object,
    semantic: GlanceSemantic | None,
) -> None:
    inputs = replace(
        _combined_inputs(GlanceSemantic.ATTENTION),
        override_reason=reason,  # type: ignore[arg-type]
        override_semantic=semantic,
    )

    resolved = _resolve(inputs)

    assert resolved.semantic is GlanceSemantic.ATTENTION
    assert resolved.override_reason is GlanceOverrideReason.NONE


@pytest.mark.parametrize(
    "reason",
    tuple(reason for reason in GlanceOverrideReason if reason is not GlanceOverrideReason.NONE),
)
def test_complete_typed_outer_override_is_explicit(reason: GlanceOverrideReason) -> None:
    base = _combined_inputs(GlanceSemantic.ATTENTION)
    inputs = replace(
        base,
        override_reason=reason,
        override_semantic=GlanceSemantic.REST,
    )

    resolved = _resolve(inputs)

    assert resolved.semantic is GlanceSemantic.REST
    assert resolved.glyph is SemanticGlyph.REST
    assert resolved.cue is None
    assert resolved.override_reason is reason


@pytest.mark.parametrize(
    "capacity",
    (
        CapacityGlance("", 0.5),
        CapacityGlance("x" * 129, 0.5),
        CapacityGlance("codex", -0.01),
        CapacityGlance("codex", 1.01),
        CapacityGlance("codex", math.nan),
        CapacityGlance("codex", math.inf),
        CapacityGlance(42, 0.5),  # type: ignore[arg-type]
    ),
)
def test_invalid_capacity_fails_closed_to_rest(capacity: CapacityGlance) -> None:
    resolved = _resolve(
        GlanceInputs(None, None, None, False, False, capacity)
    )

    assert resolved.semantic is GlanceSemantic.REST
    assert resolved.glyph is SemanticGlyph.REST
    assert resolved.cue is None
    assert resolved.next_visual_change_at is None


@pytest.mark.parametrize("event_key", ("", "x" * 129, 42))
def test_invalid_event_key_keeps_static_fresh_failure_semantics(
    event_key: object,
) -> None:
    resolved = _resolve(
        GlanceInputs(
            None,
            _cue(GlanceSemantic.FRESH_FAILURE, event_key=event_key),
            None,
            False,
            False,
            None,
        )
    )

    assert resolved.semantic is GlanceSemantic.FRESH_FAILURE
    assert resolved.glyph is SemanticGlyph.LEFT_ANCHOR
    assert resolved.cue is None
    assert resolved.next_visual_change_at is None


@pytest.mark.parametrize(
    ("repetitions", "duration_seconds"),
    (
        (0, 0.4),
        (3, 0.4),
        (True, 0.4),
        (2, 0.0),
        (2, -0.1),
        (2, math.nan),
        (2, math.inf),
        (2, 61.0),
    ),
)
def test_invalid_cue_budget_keeps_a_bounded_static_semantic(
    repetitions: object,
    duration_seconds: object,
) -> None:
    resolved = _resolve(
        GlanceInputs(
            None,
            _cue(
                GlanceSemantic.FRESH_FAILURE,
                repetitions=repetitions,
                duration_seconds=duration_seconds,
            ),
            None,
            False,
            False,
            None,
        )
    )

    assert resolved.semantic is GlanceSemantic.FRESH_FAILURE
    assert resolved.cue is None
    assert resolved.next_visual_change_at is None


def test_invalid_accessibility_preferences_fail_closed_instead_of_changing_motion() -> None:
    preferences = AccessibilityDisplayPreferences(reduce_motion=1)  # type: ignore[arg-type]

    resolved = resolve_glance(
        _combined_inputs(GlanceSemantic.ATTENTION),
        presentation_time=10.0,
        relay_epoch=2.0,
        preferences=preferences,
    )

    assert resolved.semantic is GlanceSemantic.REST
    assert resolved.cue is None
    assert resolved.relay_epoch == 0.0


@pytest.mark.parametrize(
    ("presentation_time", "relay_epoch"),
    (
        (math.nan, 2.0),
        (math.inf, 2.0),
        (-1.0, 0.0),
        (10.0, math.nan),
        (10.0, -1.0),
        (10.0, 11.0),
        (1_700_000_001.0, 1_700_000_000.0),
    ),
)
def test_invalid_or_wall_clock_anchors_fail_to_a_bounded_rest_result(
    presentation_time: float,
    relay_epoch: float,
) -> None:
    resolved = resolve_glance(
        _combined_inputs(GlanceSemantic.ATTENTION),
        presentation_time=presentation_time,
        relay_epoch=relay_epoch,
        preferences=AccessibilityDisplayPreferences(),
    )

    assert resolved.semantic is GlanceSemantic.REST
    assert resolved.glyph is SemanticGlyph.REST
    assert resolved.cue is None
    assert resolved.override_reason is GlanceOverrideReason.NONE
    assert resolved.relay_epoch == 0.0
    assert resolved.next_visual_change_at is None


@pytest.mark.parametrize(
    "semantic",
    (GlanceSemantic.ATTENTION, GlanceSemantic.FRESH_FAILURE, GlanceSemantic.FRESH_COMPLETION),
)
def test_reduce_motion_preserves_semantic_and_episode_anchor_as_static(
    semantic: GlanceSemantic,
) -> None:
    resolved = _resolve(_inputs_for(semantic), reduce_motion=True)

    assert resolved.semantic is semantic
    assert resolved.relay_epoch == 2.0
    assert resolved.cue is None
    assert resolved.next_visual_change_at is None


def test_finite_cue_deadline_uses_presentation_time_without_changing_relay_epoch() -> None:
    resolved = _resolve(_inputs_for(GlanceSemantic.FRESH_FAILURE))

    assert resolved.cue is not None
    assert resolved.next_visual_change_at == pytest.approx(10.8)
    assert resolved.relay_epoch == 2.0


@pytest.mark.parametrize("led_count", (2, 8))
@pytest.mark.parametrize("semantic", tuple(GlanceSemantic))
def test_every_semantic_composes_one_bounded_surface_program(
    semantic: GlanceSemantic,
    led_count: int,
) -> None:
    preferences = AccessibilityDisplayPreferences()
    resolved = resolve_glance(
        _inputs_for(semantic),
        presentation_time=10.0,
        relay_epoch=2.0,
        preferences=preferences,
    )

    program = compose_presentation_program(
        resolved,
        presentation_time=10.0,
        led_count=led_count,
        color="#FFFFFF",
        preferences=preferences,
        capacity_remaining_fraction=0.5,
    )

    assert program.semantic is semantic
    assert program.glyph is resolved.glyph
    assert program.static_fallback_dsl
    assert "repeat" not in program.static_fallback_dsl
    assert len(program.dsl.encode("utf-8")) <= 512
    assert len(program.dsl.splitlines()) <= 20
    if program.motion is MotionClass.STATIC:
        assert program.temporal is None
    else:
        assert isinstance(program.temporal, TemporalProgram)
        assert program.temporal.repeat_count is not None
        assert program.temporal.repeat_count <= 2


def _grayscale_vector(program: PresentationProgram) -> tuple[int, ...]:
    segments = program.static_fallback_dsl.split("; ")
    return tuple(int(segment.split("#", 1)[1][:2], 16) for segment in segments)


@pytest.mark.parametrize("differentiate_without_color", (False, True))
@pytest.mark.parametrize("led_count", (2, 8))
def test_semantic_glyphs_survive_equal_gray_conversion(
    differentiate_without_color: bool,
    led_count: int,
) -> None:
    preferences = AccessibilityDisplayPreferences(
        reduce_motion=True,
        differentiate_without_color=differentiate_without_color,
    )
    semantics = tuple(GlanceSemantic)
    if led_count == 2:
        semantics = tuple(
            semantic for semantic in semantics if semantic is not GlanceSemantic.CAPACITY
        )

    vectors = []
    for semantic in semantics:
        resolved = resolve_glance(
            _inputs_for(semantic),
            presentation_time=10.0,
            relay_epoch=2.0,
            preferences=preferences,
        )
        program = compose_presentation_program(
            resolved,
            presentation_time=10.0,
            led_count=led_count,
            color="#FFFFFF",
            preferences=preferences,
            capacity_remaining_fraction=0.5,
        )
        vectors.append(_grayscale_vector(program))

    assert len(set(vectors)) == len(vectors)


@pytest.mark.parametrize(
    "semantic",
    (
        GlanceSemantic.ATTENTION,
        GlanceSemantic.FRESH_FAILURE,
        GlanceSemantic.FRESH_COMPLETION,
    ),
)
def test_finite_semantic_cues_settle_after_at_most_two_repetitions(
    semantic: GlanceSemantic,
) -> None:
    preferences = AccessibilityDisplayPreferences()
    resolved = resolve_glance(
        _inputs_for(semantic),
        presentation_time=10.0,
        relay_epoch=2.0,
        preferences=preferences,
    )

    program = compose_presentation_program(
        resolved,
        presentation_time=10.0,
        led_count=8,
        color="#FFFFFF",
        preferences=preferences,
    )

    assert program.motion is MotionClass.FINITE
    assert program.temporal is not None
    assert program.temporal.repeat_count == 1
    assert resolved.cue is not None
    assert len(program.temporal.frames) == resolved.cue.repetitions * 2
    assert len({frame.luminance for frame in program.temporal.frames}) > 1
    assert "repeat" not in program.dsl
    assert program.dsl.endswith(program.static_fallback_dsl)
    assert program.next_visual_change_at == resolved.next_visual_change_at


def _indexed_intensities_after_each_line(
    dsl: str,
    *,
    led_count: int,
) -> tuple[tuple[int, ...], ...]:
    values = [0] * led_count
    frames = []
    for line in dsl.splitlines():
        for segment in line.split("; "):
            index_text, color_and_timing = segment.split(":", 1)
            values[int(index_text)] = int(color_and_timing.split("#", 1)[1][:2], 16)
        frames.append(tuple(values))
    return tuple(frames)


@pytest.mark.parametrize(
    ("semantic", "anchor_indices"),
    (
        (GlanceSemantic.FRESH_FAILURE, (0, 1)),
        (GlanceSemantic.FRESH_COMPLETION, (6, 7)),
    ),
)
def test_finite_motion_never_replaces_the_spatial_semantic_glyph(
    semantic: GlanceSemantic,
    anchor_indices: tuple[int, int],
) -> None:
    preferences = AccessibilityDisplayPreferences()
    resolved = resolve_glance(
        _inputs_for(semantic),
        presentation_time=10.0,
        relay_epoch=2.0,
        preferences=preferences,
    )

    program = compose_presentation_program(
        resolved,
        presentation_time=10.0,
        led_count=8,
        color="#FFFFFF",
        preferences=preferences,
    )

    for frame in _indexed_intensities_after_each_line(program.dsl, led_count=8):
        anchored = [frame[index] for index in anchor_indices]
        remainder = [
            value for index, value in enumerate(frame) if index not in anchor_indices
        ]
        assert min(anchored) > max(remainder)


def test_semantic_attention_keeps_the_existing_two_tap_deadline_contract() -> None:
    preferences = AccessibilityDisplayPreferences()
    resolved = resolve_glance(
        _inputs_for(GlanceSemantic.ATTENTION),
        presentation_time=10.0,
        relay_epoch=2.0,
        preferences=preferences,
    )

    program = compose_presentation_program(
        resolved,
        presentation_time=10.0,
        led_count=8,
        color="#FFFFFF",
        preferences=preferences,
    )

    assert resolved.cue is not None
    assert resolved.cue.repetitions == 2
    assert program.temporal is not None
    assert len(program.temporal.frames) == 4
    assert program.next_visual_change_at == resolved.next_visual_change_at == 10.48


def test_untrusted_parser_valid_studio_animation_uses_static_fallback() -> None:
    fallback = "0:#FFFFFF; 1:#000000"
    studio = PresentationProgram(
        semantic=GlanceSemantic.ACTIVE,
        glyph=SemanticGlyph.CENTER_PAIR,
        motion=MotionClass.CONTINUOUS,
        dsl="0:#FFFFFF 800ms pulse; 1:#FFFFFF 800ms pulse 800ms\nrepeat",
        static_fallback_dsl=fallback,
        temporal=None,
        trusted_period_seconds=None,
        relay_epoch=2.0,
        next_visual_change_at=None,
    )

    safe = enforce_temporal_safety(studio, calibration=CalibrationState())

    assert safe.motion is MotionClass.STATIC
    assert safe.dsl == fallback
    assert safe.temporal is None


def test_noncontinuous_programs_do_not_invent_refresh_scoped_playback_anchors() -> None:
    preferences = AccessibilityDisplayPreferences()
    static_resolved = resolve_glance(
        GlanceInputs(None, None, None, False, False, None),
        presentation_time=10.0,
        relay_epoch=2.0,
        preferences=preferences,
    )
    finite_resolved = resolve_glance(
        GlanceInputs("attention:request", None, None, False, False, None),
        presentation_time=10.0,
        relay_epoch=2.0,
        preferences=preferences,
    )

    static = compose_presentation_program(
        static_resolved,
        presentation_time=10.0,
        led_count=8,
        color="#FFFFFF",
        preferences=preferences,
    )
    finite = compose_presentation_program(
        finite_resolved,
        presentation_time=10.0,
        led_count=8,
        color="#FFFFFF",
        preferences=preferences,
    )

    assert static.motion is MotionClass.STATIC
    assert finite.motion is MotionClass.FINITE
    assert static.playback_anchor is None
    assert finite.playback_anchor is None


def test_finite_program_cannot_hide_an_unbounded_dsl_repeat() -> None:
    fallback = "0:#FFFFFF; 1:#000000"
    candidate = PresentationProgram(
        semantic=GlanceSemantic.FRESH_COMPLETION,
        glyph=SemanticGlyph.RIGHT_ANCHOR,
        motion=MotionClass.FINITE,
        dsl="#FFFFFF 200ms\noff 200ms\nrepeat",
        static_fallback_dsl=fallback,
        temporal=TemporalProgram(
            frames=(TemporalFrame(1.0, 0.2), TemporalFrame(0.0, 0.2)),
            repeat_count=1,
            static_fallback=StaticSemanticFallback("fresh_completion", 0.5),
        ),
        trusted_period_seconds=None,
        relay_epoch=2.0,
        next_visual_change_at=10.4,
    )

    safe = enforce_temporal_safety(candidate, calibration=CalibrationState())

    assert safe.motion is MotionClass.STATIC
    assert safe.dsl == fallback


@pytest.mark.parametrize(
    ("surface", "calibration"),
    (
        ("screen_bar", CalibrationState()),
        ("status_item", CalibrationState()),
        ("physical_uncalibrated", CalibrationState()),
        (
            "physical_calibrated",
            CalibrationState(
                physical_luminance_calibrated=True,
                flash_area_calibrated=True,
            ),
        ),
        ("studio", CalibrationState()),
    ),
)
def test_four_flash_surface_fixture_falls_back_even_when_calibrated(
    surface: str,
    calibration: CalibrationState,
) -> None:
    fallback = "0:#808080; 1:#000000"
    frames = tuple(
        TemporalFrame(float(index % 2), 0.1)
        for index in range(9)
    )
    candidate = PresentationProgram(
        semantic=GlanceSemantic.FRESH_FAILURE,
        glyph=SemanticGlyph.LEFT_ANCHOR,
        motion=MotionClass.FINITE,
        dsl=f"# fixture: {surface}\n#FFFFFF 100ms\noff 100ms",
        static_fallback_dsl=fallback,
        temporal=TemporalProgram(
            frames=frames,
            repeat_count=1,
            static_fallback=StaticSemanticFallback("fresh_failure", 0.5),
        ),
        trusted_period_seconds=None,
        relay_epoch=2.0,
        next_visual_change_at=10.9,
    )

    safe = enforce_temporal_safety(candidate, calibration=calibration)

    assert safe.motion is MotionClass.STATIC
    assert safe.dsl == fallback
