"""The fleet planner owns identity, geometry, and refusal semantics."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sidepulse.attention import AttentionProjection, LifecycleMode, ProjectedAgentRow
from sidepulse.colors import plan_fleet_projection
from sidepulse.fleet_bands import FleetMember, plan_fleet_bands
from sidepulse.models import AgentMode, AgentStatus


class State(str, Enum):
    WORKING = "working"
    ASKING = "asking"


def _member(identity: str, semantic: object, *, worker: bool = False) -> FleetMember:
    return FleetMember(identity=identity, semantic=semantic, is_worker=worker)


def test_two_main_rows_get_deterministic_eight_led_and_screen_bar_partitions() -> None:
    plan = plan_fleet_bands(
        [_member("project-a", State.WORKING), _member("project-b", State.ASKING)],
        screen_bar_width=240.0,
    )

    assert plan.mode == "segmented"
    assert [(band.identity, band.led_start, band.led_end) for band in plan.bands] == [
        ("project-a", 0, 4),
        ("project-b", 4, 8),
    ]
    assert [(band.screen_start, band.screen_end) for band in plan.bands] == [
        (0.0, 120.0),
        (120.0, 240.0),
    ]


def test_sticky_slots_survive_lifecycle_state_changes() -> None:
    first = plan_fleet_bands(
        [_member("a", "working"), _member("b", "asking"), _member("c", "idle")]
    )
    second = plan_fleet_bands(
        [_member("a", "failed"), _member("b", "completed"), _member("c", "working")],
        previous_layout=first,
    )

    assert [(band.identity, band.led_start, band.led_end) for band in second.bands] == [
        ("a", 0, 3),
        ("b", 3, 6),
        ("c", 6, 8),
    ]


def test_workers_do_not_create_bands_or_change_shared_state() -> None:
    plan = plan_fleet_bands(
        [
            _member("main", "working"),
            _member("worker-1", "asking", worker=True),
            _member("worker-2", "failed", worker=True),
        ]
    )

    assert plan.mode == "shared"
    assert len(plan.bands) == 1
    assert plan.bands[0].identity is None
    assert plan.bands[0].led_start == 0
    assert plan.bands[0].led_end == 8


def test_uniform_main_fleet_collapses_to_one_full_width_shared_effect() -> None:
    plan = plan_fleet_bands([_member("a", "working"), _member("b", "working")])

    assert plan.mode == "shared"
    assert plan.bands[0].shared is True
    assert plan.bands[0].screen_start == 0.0
    assert plan.bands[0].screen_end == 1.0
    assert plan.member_slots == (("a", 0, 4), ("b", 4, 8))


def test_divergence_from_shared_mode_returns_members_to_sticky_slots() -> None:
    shared = plan_fleet_bands([_member("a", "working"), _member("b", "working")])
    divergent = plan_fleet_bands(
        [_member("a", "working"), _member("b", "asking")],
        previous_layout=shared,
    )

    assert divergent.mode == "segmented"
    assert [(band.identity, band.led_start, band.led_end) for band in divergent.bands] == [
        ("a", 0, 4),
        ("b", 4, 8),
    ]


def test_ninth_main_identity_is_an_explicit_refusal() -> None:
    plan = plan_fleet_bands([_member(f"project-{index}", "working") for index in range(9)])

    assert plan.refused is True
    assert plan.mode == "refused"
    assert plan.refusal == "fleet_member_overflow"
    assert plan.bands == ()


def test_project_or_machine_identity_can_be_used_without_a_runtime_row_id() -> None:
    plan = plan_fleet_bands(
        [
            FleetMember(project_id="repo-a", semantic="working"),
            FleetMember(machine_id="mac-b", semantic="asking"),
        ]
    )

    assert [band.identity for band in plan.bands] == ["mac-b", "repo-a"]


def test_duplicate_main_identity_with_conflicting_semantics_is_refused() -> None:
    plan = plan_fleet_bands([_member("a", "working"), _member("a", "asking")])

    assert plan.refusal == "conflicting_main_rows_for_identity"


def _projected_row(
    agent_id: str,
    origin: str,
    lifecycle: LifecycleMode,
) -> ProjectedAgentRow:
    when = datetime(2026, 8, 30, tzinfo=timezone.utc)
    status = AgentStatus(
        provider="codex",
        agent_id=agent_id,
        display_name=agent_id,
        mode=AgentMode.WORKING,
        updated_at=when,
        event_name="PreToolUse",
        origin=origin,
    )
    return ProjectedAgentRow(
        agent_id=agent_id,
        provider="codex",
        display_name=agent_id,
        lifecycle_mode=lifecycle,
        actionable=False,
        is_subagent=False,
        updated_at=when,
        source_status=status,
    )


def _projection(*rows: ProjectedAgentRow) -> AttentionProjection:
    return AttentionProjection(
        lifecycle_mode=LifecycleMode.ACTIVE,
        actionable_attention=(),
        visible_rows=tuple(rows),
        transient_signals=(),
        dominant_provider="codex",
        click_target_agent_id=None,
    )


def test_live_projection_uses_project_identity_and_keeps_sticky_slots() -> None:
    first = plan_fleet_projection(
        _projection(
            _projected_row("codex:session:a", "repo-a", LifecycleMode.ACTIVE),
            _projected_row("codex:session:b", "repo-b", LifecycleMode.WAITING),
        )
    )
    second = plan_fleet_projection(
        _projection(
            _projected_row("codex:session:a", "repo-a", LifecycleMode.COMPLETED_RECENTLY),
            _projected_row("codex:session:b", "repo-b", LifecycleMode.ACTIVE),
        ),
        previous_layout=first,
    )

    assert first.member_slots == second.member_slots
    assert first.member_slots == (
        ("project:repo-a", 0, 4),
        ("project:repo-b", 4, 8),
    )
