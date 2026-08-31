from __future__ import annotations

from dataclasses import replace

import pytest

from sidepulse.dnd_policy import DisplayAdmission
from sidepulse.effect_registry import EffectDefinition, EffectRegistry
from sidepulse.scenes import Scene
from sidepulse.semantic_effect_router import (
    DEFAULT_SEMANTIC_EFFECT_MAP,
    SEMANTIC_PRIORITY,
    CourtesySuppression,
    SceneEffectAssignment,
    SemanticEffectAssignment,
    SemanticEffectCandidate,
    SemanticEffectMap,
    SemanticEffectSelection,
    SemanticEventKind,
    SuppressionReason,
    route_semantic_effects,
)


def _candidate(
    key: str,
    semantic: SemanticEventKind,
    *,
    sequence: int = 0,
    destination_surfaces: tuple[str, ...] = (),
) -> SemanticEffectCandidate:
    return SemanticEffectCandidate(
        key=key,
        semantic=semantic,
        sequence=sequence,
        destination_surfaces=destination_surfaces,
    )


def _replace_effect(
    semantic: SemanticEventKind,
    effect_identifier: str,
) -> SemanticEffectMap:
    assignments = tuple(
        SemanticEffectAssignment(
            item.semantic,
            effect_identifier if item.semantic is semantic else item.effect_identifier,
        )
        for item in DEFAULT_SEMANTIC_EFFECT_MAP.assignments
    )
    return SemanticEffectMap(assignments=assignments)


def _suppressed(
    selection: SemanticEffectSelection,
) -> dict[str, SuppressionReason]:
    return {
        item.candidate.key: item.reason
        for item in selection.suppressed
    }


def test_semantic_vocabulary_and_priority_match_the_approved_order() -> None:
    assert tuple(item.value for item in SemanticEventKind) == (
        "ask",
        "failure",
        "notification",
        "handoff",
        "work",
        "completion",
        "recovery",
        "environment",
        "idle",
    )
    assert tuple(
        sorted(
            SemanticEventKind,
            key=SEMANTIC_PRIORITY.__getitem__,
            reverse=True,
        )
    ) == (
        SemanticEventKind.ASK,
        SemanticEventKind.FAILURE,
        SemanticEventKind.NOTIFICATION,
        SemanticEventKind.HANDOFF,
        SemanticEventKind.WORK,
        SemanticEventKind.COMPLETION,
        SemanticEventKind.RECOVERY,
        SemanticEventKind.ENVIRONMENT,
        SemanticEventKind.IDLE,
    )


def test_highest_semantic_priority_wins_and_reports_every_loser() -> None:
    candidates = (
        _candidate("work-2", SemanticEventKind.WORK, sequence=2),
        _candidate("failure-1", SemanticEventKind.FAILURE, sequence=1),
        _candidate("ask-1", SemanticEventKind.ASK, sequence=1),
        _candidate("notification-1", SemanticEventKind.NOTIFICATION, sequence=1),
    )

    selection = route_semantic_effects(candidates)

    assert selection.winner == candidates[2]
    assert selection.registry_effect_identifier == "alert"
    assert selection.reduce_motion_substitution is None
    assert selection.destination_surfaces == (
        "status_bar",
        "screen_bar",
        "sidepulse_pro",
        "sidepulse_dot",
        "glance_light",
        "settings_preview",
    )
    assert _suppressed(selection) == {
        "failure-1": SuppressionReason.LOWER_PRIORITY,
        "notification-1": SuppressionReason.LOWER_PRIORITY,
        "work-2": SuppressionReason.LOWER_PRIORITY,
    }


def test_every_default_semantic_resolves_through_the_runtime_registry() -> None:
    for semantic in SemanticEventKind:
        selection = route_semantic_effects((_candidate(semantic.value, semantic),))

        assert selection.winner is not None
        assert selection.registry_effect_identifier is not None
        assert selection.destination_surfaces


def test_equal_semantics_use_sequence_then_key_without_input_order_dependence() -> None:
    older = _candidate("z-older", SemanticEventKind.WORK, sequence=1)
    alpha = _candidate("a-newer", SemanticEventKind.WORK, sequence=2)
    omega = _candidate("z-newer", SemanticEventKind.WORK, sequence=2)

    forward = route_semantic_effects((older, omega, alpha))
    reverse = route_semantic_effects((alpha, omega, older))

    assert forward.winner == reverse.winner == alpha
    assert tuple(item.candidate.key for item in forward.suppressed) == (
        "z-newer",
        "z-older",
    )


@pytest.mark.parametrize(
    ("admission", "candidates", "winner_key"),
    (
        (
            DisplayAdmission.ALL,
            (
                _candidate("ask", SemanticEventKind.ASK),
                _candidate("failure", SemanticEventKind.FAILURE),
            ),
            "ask",
        ),
        (
            DisplayAdmission.CRITICAL,
            (
                _candidate("failure", SemanticEventKind.FAILURE),
                _candidate("work", SemanticEventKind.WORK),
            ),
            "failure",
        ),
        (
            DisplayAdmission.ASKS,
            (
                _candidate("failure", SemanticEventKind.FAILURE),
                _candidate("ask", SemanticEventKind.ASK),
            ),
            "ask",
        ),
        (
            DisplayAdmission.NONE,
            (_candidate("ask", SemanticEventKind.ASK),),
            None,
        ),
    ),
)
def test_display_admission_uses_existing_dnd_capabilities(
    admission: DisplayAdmission,
    candidates: tuple[SemanticEffectCandidate, ...],
    winner_key: str | None,
) -> None:
    selection = route_semantic_effects(
        candidates,
        display_admission=admission,
    )

    assert (
        selection.winner.key if selection.winner is not None else None
    ) == winner_key
    refused = {
        item.candidate.key
        for item in selection.suppressed
        if item.reason is SuppressionReason.DISPLAY_ADMISSION
    }
    if admission is DisplayAdmission.CRITICAL:
        assert refused == {"work"}
    elif admission is DisplayAdmission.ASKS:
        assert refused == {"failure"}
    elif admission is DisplayAdmission.NONE:
        assert refused == {"ask"}
    else:
        assert refused == set()


@pytest.mark.parametrize(
    ("suppression", "reason"),
    (
        (
            CourtesySuppression(focus=True),
            SuppressionReason.COURTESY_FOCUS,
        ),
        (
            CourtesySuppression(snoozed=True),
            SuppressionReason.COURTESY_SNOOZE,
        ),
        (
            CourtesySuppression(budget_exhausted=True),
            SuppressionReason.COURTESY_BUDGET,
        ),
    ),
)
def test_courtesy_hold_suppresses_courtesy_but_leaves_idle_presence(
    suppression: CourtesySuppression,
    reason: SuppressionReason,
) -> None:
    completion = _candidate("completion", SemanticEventKind.COMPLETION)
    environment = _candidate("weather", SemanticEventKind.ENVIRONMENT)
    idle = _candidate("idle", SemanticEventKind.IDLE)

    selection = route_semantic_effects(
        (completion, environment, idle),
        courtesy_suppression=suppression,
    )

    assert selection.winner == idle
    assert _suppressed(selection) == {
        "completion": reason,
        "weather": reason,
    }


def test_scene_assignment_changes_routine_effect_but_not_urgent_override() -> None:
    effect_map = replace(
        DEFAULT_SEMANTIC_EFFECT_MAP,
        scene_assignments=(
            SceneEffectAssignment(Scene.DEMO, SemanticEventKind.WORK, "rainbow"),
            SceneEffectAssignment(Scene.DEMO, SemanticEventKind.ASK, "none"),
            SceneEffectAssignment(Scene.DEMO, SemanticEventKind.FAILURE, "none"),
        ),
    )

    work = route_semantic_effects(
        (_candidate("work", SemanticEventKind.WORK),),
        scene=Scene.DEMO,
        effect_map=effect_map,
    )
    ask = route_semantic_effects(
        (_candidate("ask", SemanticEventKind.ASK),),
        scene=Scene.DEMO,
        effect_map=effect_map,
    )
    failure = route_semantic_effects(
        (_candidate("failure", SemanticEventKind.FAILURE),),
        scene=Scene.DEMO,
        effect_map=effect_map,
    )

    assert work.registry_effect_identifier == "rainbow"
    assert ask.registry_effect_identifier == "alert"
    assert failure.registry_effect_identifier == "alert"


def test_reduce_motion_uses_registry_fallback_and_effective_surfaces() -> None:
    registry = EffectRegistry(
        (
            EffectDefinition(
                "moving",
                "Moving",
                "Motion",
                "work",
                surfaces=("screen_bar", "status_bar"),
                reduce_motion_fallback="still",
            ),
            EffectDefinition(
                "still",
                "Still",
                "Static",
                "work",
                surfaces=("screen_bar",),
            ),
        )
    )
    mapping = _replace_effect(SemanticEventKind.WORK, "moving")

    selection = route_semantic_effects(
        (
            _candidate(
                "work",
                SemanticEventKind.WORK,
                destination_surfaces=("status_bar", "screen_bar"),
            ),
        ),
        effect_map=mapping,
        reduce_motion=True,
        registry=registry,
    )

    assert selection.registry_effect_identifier == "moving"
    assert selection.reduce_motion_substitution == "still"
    assert selection.destination_surfaces == ("screen_bar",)


def test_missing_effect_and_unsupported_surface_do_not_block_next_candidate() -> None:
    missing_map = _replace_effect(SemanticEventKind.ASK, "not-registered")
    ask = _candidate("ask", SemanticEventKind.ASK)
    idle = _candidate("idle", SemanticEventKind.IDLE)

    missing = route_semantic_effects((ask, idle), effect_map=missing_map)
    unsupported = route_semantic_effects(
        (
            _candidate(
                    "work",
                    SemanticEventKind.WORK,
                    destination_surfaces=("unsupported_surface",),
            ),
            idle,
        )
    )

    assert missing.winner == idle
    assert _suppressed(missing)["ask"] is SuppressionReason.EFFECT_NOT_REGISTERED
    assert unsupported.winner == idle
    assert (
        _suppressed(unsupported)["work"]
        is SuppressionReason.NO_SUPPORTED_DESTINATION
    )


def test_router_inputs_are_immutable_bounded_and_strictly_typed() -> None:
    with pytest.raises(ValueError, match="key"):
        _candidate("", SemanticEventKind.WORK)
    with pytest.raises(ValueError, match="sequence"):
        _candidate("work", SemanticEventKind.WORK, sequence=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unique"):
        _candidate(
            "work",
            SemanticEventKind.WORK,
            destination_surfaces=("status_bar", "status_bar"),
        )
    with pytest.raises(ValueError, match="unique"):
        SemanticEffectMap(
            assignments=(
                *DEFAULT_SEMANTIC_EFFECT_MAP.assignments,
                SemanticEffectAssignment(SemanticEventKind.WORK, "pulse"),
            )
        )
    with pytest.raises(ValueError, match="tuple"):
        route_semantic_effects([  # type: ignore[arg-type]
            _candidate("work", SemanticEventKind.WORK)
        ])
    with pytest.raises(ValueError, match="unique"):
        route_semantic_effects(
            (
                _candidate("same", SemanticEventKind.WORK),
                _candidate("same", SemanticEventKind.IDLE),
            )
        )
