from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sidepulse.colors import ColorSettings, StudioPreviewSession
from sidepulse.effect_packs import EffectPackError
from sidepulse.effect_registry import EffectDefinition, EffectRegistry
from sidepulse.effect_studio import (
    MAX_PHYSICAL_PREVIEW_SECONDS,
    MAX_SYNTHETIC_EVENTS,
    PHYSICAL_PREVIEW_RELEASE_TRIGGERS,
    AssignmentScope,
    EffectStudioError,
    PolicyDecision,
    SemanticFamily,
    SourceFreshness,
    StudioSessionAction,
    StudioSurface,
    SuppressedSignal,
    SyntheticScenario,
    build_gallery_index,
    build_gallery_projection,
    build_gallery_rows,
    build_surface_simulations,
    build_synthetic_timeline,
    plan_assignment,
    plan_commit,
    plan_compare,
    plan_pack_export,
    plan_pack_import,
    plan_physical_preview,
    plan_preview,
    plan_revert,
    project_gallery_pack,
    project_why_effect,
)
from sidepulse.scenes import Scene


def _pack(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "calm-pack",
        "name": "Calm Pack",
        "version": 2,
        "effects": [
            {
                "id": "soft-pulse",
                "label": "Soft Pulse",
                "meaning": "quiet working",
                "surfaces": ["screen_bar"],
            }
        ],
        "safety": {"data_only": True, "network": False},
        "accessibility": {"reduced_motion": True, "high_contrast": True},
    }
    payload.update(overrides)
    return payload


def test_gallery_projects_registry_metadata_in_stable_searchable_rows() -> None:
    registry = EffectRegistry(
        (
            EffectDefinition(
                "work-pulse",
                "Work Pulse",
                "A restrained pulse.",
                "working",
                surfaces=("screen_bar", "sidepulse_pro"),
                reduce_motion_fallback="still",
                energy="medium",
            ),
            EffectDefinition("still", "Still", "No motion.", "idle"),
        )
    )

    rows = build_gallery_rows(registry, query=" restrained ")

    assert tuple(row.effect_id for row in rows) == ("work-pulse",)
    assert rows[0].semantic_family is SemanticFamily.WORKING
    assert rows[0].supported_surfaces == (
        StudioSurface.SCREEN_BAR,
        StudioSurface.SIDEPULSE_PRO,
    )
    assert rows[0].duration_seconds is None
    assert rows[0].reduce_motion_effect_id == "still"
    assert rows[0].energy == "medium"


@pytest.mark.parametrize("query", (None, 3, "x" * 121))
def test_gallery_rejects_invalid_or_unbounded_search_values(query: object) -> None:
    with pytest.raises(EffectStudioError):
        build_gallery_rows(EffectRegistry(), query=query)


def test_gallery_index_projects_pack_license_without_mutable_state() -> None:
    licensed = _pack(
        license={
            "spdx_id": "MIT",
            "label": "MIT License",
            "source_url": "https://example.com/source",
            "attribution_url": "https://example.com/credit",
        }
    )
    projection = project_gallery_pack(licensed)
    index = build_gallery_index((licensed, _pack(id="alpha-pack", name="Alpha Pack")))

    assert projection.pack_id == "calm-pack"
    assert projection.effect_ids == ("soft-pulse",)
    assert projection.license_spdx_id == "MIT"
    assert projection.license_label == "MIT License"
    assert projection.source_url == "https://example.com/source"
    assert projection.license == projection.license_metadata
    assert tuple(row.pack_id for row in index) == ("alpha-pack", "calm-pack")
    assert build_gallery_projection((licensed,)) == (projection,)
    with pytest.raises(FrozenInstanceError):
        projection.pack_id = "changed"  # type: ignore[misc]


def test_gallery_index_rejects_duplicate_pack_ids() -> None:
    with pytest.raises(EffectStudioError, match="duplicate gallery pack"):
        build_gallery_index((_pack(), _pack()))


def test_surface_simulations_are_side_by_side_and_resolve_reduced_motion() -> None:
    registry = EffectRegistry(
        (
            EffectDefinition(
                "moving",
                "Moving",
                "Moves.",
                "working",
                surfaces=("screen_bar", "sidepulse_dot"),
                reduce_motion_fallback="still",
            ),
            EffectDefinition(
                "still",
                "Still",
                "Does not move.",
                "idle",
                surfaces=("screen_bar", "sidepulse_dot"),
            ),
        )
    )

    cells = build_surface_simulations("moving", registry, reduce_motion=True)

    assert tuple(cell.surface for cell in cells) == (
        StudioSurface.SCREEN_BAR,
        StudioSurface.SIDEPULSE_PRO,
        StudioSurface.SIDEPULSE_DOT,
        StudioSurface.GLANCE_LIGHT,
    )
    assert tuple(cell.led_count for cell in cells) == (24, 24, 2, 1)
    assert cells[0].supported is True
    assert cells[0].rendered_effect_id == "still"
    assert cells[1].supported is False
    assert cells[2].supported is True


def test_synthetic_timeline_is_bounded_ordered_and_scrubbable() -> None:
    timeline = build_synthetic_timeline(
        (
            SyntheticScenario.ONE_AGENT,
            SyntheticScenario.ASKING,
            SyntheticScenario.COMPLETION,
        ),
        step_seconds=2.5,
        cursor_seconds=3.0,
        paused=True,
        reduce_motion=True,
    )

    assert tuple(event.offset_seconds for event in timeline.events) == (0.0, 2.5, 5.0)
    assert tuple(event.scenario for event in timeline.events) == (
        SyntheticScenario.ONE_AGENT,
        SyntheticScenario.ASKING,
        SyntheticScenario.COMPLETION,
    )
    assert timeline.duration_seconds == 5.0
    assert timeline.cursor_seconds == 3.0
    assert timeline.paused is True
    assert timeline.reduce_motion is True


def test_synthetic_timeline_rejects_invalid_values_and_event_overflow() -> None:
    with pytest.raises(EffectStudioError):
        build_synthetic_timeline((SyntheticScenario.ONE_AGENT,), paused="yes")
    with pytest.raises(EffectStudioError):
        build_synthetic_timeline(("one_agent",))
    with pytest.raises(EffectStudioError):
        build_synthetic_timeline(
            (SyntheticScenario.ONE_AGENT,) * (MAX_SYNTHETIC_EVENTS + 1)
        )


def test_session_action_plans_are_explicit_and_do_not_mutate_preview_session() -> None:
    committed_colors = ColorSettings.defaults()
    candidate_colors = committed_colors.with_agent_color("claude", "#10A37F")
    session = StudioPreviewSession(committed_colors, candidate_colors)
    registry = EffectRegistry(
        (
            EffectDefinition("before", "Before", "Before.", "idle"),
            EffectDefinition("after", "After", "After.", "working"),
        )
    )

    preview = plan_preview(session, "before", "after", registry)
    compare = plan_compare(session, "before", "after", registry)
    commit = plan_commit(session, "before", "after", registry)
    revert = plan_revert(session, "before", registry)

    assert preview.action is StudioSessionAction.PREVIEW
    assert compare.action is StudioSessionAction.COMPARE
    assert compare.comparison_enabled is True
    assert commit.action is StudioSessionAction.COMMIT
    assert commit.settings_write_required is True
    assert commit.result_effect_id == "after"
    assert revert.action is StudioSessionAction.REVERT
    assert revert.result_effect_id == "before"
    assert preview.committed_colors == committed_colors
    assert preview.effective_colors == candidate_colors
    preview.committed_colors.mode_colors["idle"] = "#123456"
    preview.effective_colors.agent_colors["claude"] = "#654321"
    assert committed_colors.mode_colors["idle"] != "#123456"
    assert candidate_colors.agent_colors["claude"] != "#654321"
    assert session.committed == committed_colors
    assert session.candidate == candidate_colors
    assert session.previewing is True


def test_session_plans_fail_closed_for_missing_effects_or_wrong_session_type() -> None:
    registry = EffectRegistry((EffectDefinition("before", "Before", "Before.", "idle"),))
    with pytest.raises(EffectStudioError):
        plan_preview(StudioPreviewSession(ColorSettings.defaults()), "before", "missing", registry)
    with pytest.raises(EffectStudioError):
        plan_revert(object(), "before", registry)


def test_physical_preview_requires_exact_consent_and_has_bounded_release_plan() -> None:
    denied = plan_physical_preview(
        "pulse",
        "device-1",
        consent_granted=False,
        duration_seconds=10.0,
    )
    allowed = plan_physical_preview(
        "pulse",
        "device-1",
        consent_granted=True,
        duration_seconds=MAX_PHYSICAL_PREVIEW_SECONDS,
    )

    assert denied.allowed is False
    assert denied.duration_seconds == 0.0
    assert allowed.allowed is True
    assert allowed.status_label == "Previewing, not saved"
    assert allowed.release_triggers == PHYSICAL_PREVIEW_RELEASE_TRIGGERS


@pytest.mark.parametrize(
    ("consent", "duration"),
    ((1, 10.0), (True, 0.0), (True, MAX_PHYSICAL_PREVIEW_SECONDS + 0.1)),
)
def test_physical_preview_rejects_ambiguous_consent_or_invalid_duration(
    consent: object,
    duration: float,
) -> None:
    with pytest.raises(EffectStudioError):
        plan_physical_preview(
            "pulse",
            "device-1",
            consent_granted=consent,
            duration_seconds=duration,
        )


def test_all_assignment_scopes_return_data_only_plans() -> None:
    targets = {
        AssignmentScope.GLOBAL: None,
        AssignmentScope.SEMANTIC: SemanticFamily.FAILURE.value,
        AssignmentScope.PROVIDER: "claude",
        AssignmentScope.PROVIDER_INSTANCE: "claude:work",
        AssignmentScope.PROJECT: "project-42",
        AssignmentScope.DEVICE: "device-1",
        AssignmentScope.SCENE: Scene.NIGHT.value,
    }

    plans = tuple(
        plan_assignment("pulse", scope, target)
        for scope, target in targets.items()
    )

    assert tuple(plan.scope for plan in plans) == tuple(AssignmentScope)
    assert plans[-1].scene_policy is not None
    assert plans[-1].scene_policy.scene is Scene.NIGHT

@pytest.mark.parametrize(
    ("scope", "target"),
    (
        (AssignmentScope.GLOBAL, "not-global"),
        (AssignmentScope.SEMANTIC, "unknown"),
        (AssignmentScope.PROVIDER, None),
        (AssignmentScope.SCENE, "unknown"),
        ("global", None),
    ),
)
def test_assignment_scopes_fail_closed_for_invalid_targets(
    scope: object,
    target: object,
) -> None:
    with pytest.raises(EffectStudioError):
        plan_assignment("pulse", scope, target)


def test_pack_import_and_export_plans_reuse_safe_data_only_pack_contract() -> None:
    licensed = _pack(license={"spdx_id": "MIT", "label": "MIT License"})
    import_plan = plan_pack_import(licensed)
    export_plan = plan_pack_export(licensed)

    assert import_plan.pack_id == "calm-pack"
    assert import_plan.effect_ids == ("pack:calm-pack:soft-pulse",)
    assert import_plan.registry_write_required is True
    assert export_plan.pack_id == "calm-pack"
    assert export_plan.effect_count == 1
    assert import_plan.license_metadata is not None
    assert import_plan.license_metadata.spdx_id == "MIT"
    assert export_plan.license_metadata == import_plan.license_metadata
    assert export_plan.payload.startswith(b'{"accessibility"')
    assert b"\n" not in export_plan.payload


def test_pack_import_rejects_registry_collisions_without_mutating_registry() -> None:
    definition = EffectDefinition(
        "pack:calm-pack:soft-pulse",
        "Existing",
        "Existing.",
        "working",
    )
    registry = EffectRegistry((definition,))

    with pytest.raises(EffectPackError, match="already registered"):
        plan_pack_import(_pack(), registry)
    assert registry.require(definition.identifier) is definition


def test_why_effect_projection_is_bounded_content_free_registry_metadata() -> None:
    projection = project_why_effect(
        "pulse",
        source_age_seconds=12.5,
        priority=4,
        suppressed_signals=(SuppressedSignal(SemanticFamily.NOTIFICATION, 2),),
        policy_decisions=(
            PolicyDecision.ROUTE_WINNER,
            PolicyDecision.REDUCE_MOTION_SUBSTITUTE,
        ),
        expires_in_seconds=30.0,
    )

    assert projection.effect_id == "pulse"
    assert projection.source_freshness is SourceFreshness.FRESH
    assert projection.source_age_seconds == 12.5
    assert projection.priority == 4
    assert projection.suppressed_signals == (
        SuppressedSignal(SemanticFamily.NOTIFICATION, 2),
    )
    assert projection.policy_decisions == (
        PolicyDecision.ROUTE_WINNER,
        PolicyDecision.REDUCE_MOTION_SUBSTITUTE,
    )
    assert not hasattr(projection, "message")
    assert not hasattr(projection, "content")
    with pytest.raises(FrozenInstanceError):
        projection.priority = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    (
        {"source_age_seconds": -1.0},
        {"source_age_seconds": True},
        {"priority": 100},
        {"suppressed_signals": ("notification",)},
        {"policy_decisions": ("route_winner",)},
        {"expires_in_seconds": -1.0},
    ),
)
def test_why_effect_projection_fails_closed_for_untyped_or_unbounded_facts(
    changes: dict[str, object],
) -> None:
    facts: dict[str, object] = {
        "source_age_seconds": 12.5,
        "priority": 4,
        "suppressed_signals": (),
        "policy_decisions": (),
        "expires_in_seconds": None,
    }
    facts.update(changes)

    with pytest.raises(EffectStudioError):
        project_why_effect("pulse", **facts)
