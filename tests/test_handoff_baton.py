from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sidepulse.handoff_baton import (
    DEFAULT_HANDOFF_WINDOW_SECONDS,
    MAX_HANDOFF_WINDOW_SECONDS,
    HandoffBatonMotionVariant,
    HandoffBatonRefusal,
    HandoffEndpoint,
    HandoffLinkKind,
    plan_handoff_baton,
)


def _endpoint(
    *,
    event: str,
    agent: str,
    segment: str,
    name: str,
    observed_at: float,
    project: str | None = "project:jr-bar",
    task: str | None = "task:handoff",
) -> HandoffEndpoint:
    return HandoffEndpoint(
        event_identity=event,
        agent_identity=agent,
        segment_identity=segment,
        accessibility_name=name,
        observed_at=observed_at,
        project_identity=project,
        task_identity=task,
    )


def test_exact_task_evidence_yields_one_finite_source_to_destination_pass() -> None:
    decision = plan_handoff_baton(
        _endpoint(
            event="event:completed",
            agent="agent:claude",
            segment="segment:claude",
            name="Claude",
            observed_at=10.0,
        ),
        _endpoint(
            event="event:started",
            agent="agent:codex",
            segment="segment:codex",
            name="Codex",
            observed_at=12.5,
        ),
    )

    assert decision.admitted is True
    assert decision.refusal is None
    assert decision.plan is not None
    assert decision.plan.linkage.kind is HandoffLinkKind.TASK
    assert decision.plan.linkage.identity == "task:handoff"
    assert decision.plan.elapsed_seconds == 2.5
    assert decision.plan.window_seconds == DEFAULT_HANDOFF_WINDOW_SECONDS
    assert decision.plan.presentation.source_segment_identity == "segment:claude"
    assert decision.plan.presentation.destination_segment_identity == "segment:codex"
    assert decision.plan.presentation.settle_at_destination is True
    assert decision.plan.motion.variant is HandoffBatonMotionVariant.TRAVEL_ONCE
    assert decision.plan.motion.finite is True
    assert decision.plan.motion.spatial_travel is True
    assert decision.plan.motion.passes == 1
    assert decision.plan.motion.loops == 0


def test_shared_project_is_sufficient_when_task_identities_differ() -> None:
    source = _endpoint(
        event="event:completed",
        agent="agent:one",
        segment="segment:one",
        name="First agent",
        observed_at=1.0,
        task="task:build",
    )
    destination = _endpoint(
        event="event:started",
        agent="agent:two",
        segment="segment:two",
        name="Second agent",
        observed_at=2.0,
        task="task:review",
    )

    decision = plan_handoff_baton(source, destination)

    assert decision.plan is not None
    assert decision.plan.linkage.kind is HandoffLinkKind.PROJECT
    assert decision.plan.linkage.identity == "project:jr-bar"


@pytest.mark.parametrize(
    ("source_project", "source_task", "destination_project", "destination_task"),
    [
        (None, None, None, None),
        ("project:a", None, "project:b", None),
        (None, "task:a", None, "task:b"),
        ("project:a", "task:a", "project:b", "task:b"),
    ],
)
def test_timing_coincidence_never_creates_a_baton_without_linkage_evidence(
    source_project: str | None,
    source_task: str | None,
    destination_project: str | None,
    destination_task: str | None,
) -> None:
    source = _endpoint(
        event="event:completed",
        agent="agent:one",
        segment="segment:one",
        name="First agent",
        observed_at=10.0,
        project=source_project,
        task=source_task,
    )
    destination = _endpoint(
        event="event:started",
        agent="agent:two",
        segment="segment:two",
        name="Second agent",
        observed_at=10.001,
        project=destination_project,
        task=destination_task,
    )

    decision = plan_handoff_baton(source, destination)

    assert decision.admitted is False
    assert decision.plan is None
    assert decision.refusal is HandoffBatonRefusal.MISSING_LINKAGE


def test_timing_is_directional_and_must_fit_the_bounded_window() -> None:
    source = _endpoint(
        event="event:completed",
        agent="agent:one",
        segment="segment:one",
        name="First agent",
        observed_at=20.0,
    )
    before = _endpoint(
        event="event:started-before",
        agent="agent:two",
        segment="segment:two",
        name="Second agent",
        observed_at=19.999,
    )
    late = _endpoint(
        event="event:started-late",
        agent="agent:two",
        segment="segment:two",
        name="Second agent",
        observed_at=20.0 + DEFAULT_HANDOFF_WINDOW_SECONDS + 0.001,
    )

    assert plan_handoff_baton(source, before).refusal is (
        HandoffBatonRefusal.DESTINATION_PRECEDES_SOURCE
    )
    assert plan_handoff_baton(source, late).refusal is (
        HandoffBatonRefusal.OUTSIDE_WINDOW
    )


def test_source_destination_event_agent_and_segment_identities_must_differ() -> None:
    source = _endpoint(
        event="event:source",
        agent="agent:one",
        segment="segment:one",
        name="First agent",
        observed_at=1.0,
    )
    same_event = _endpoint(
        event="event:source",
        agent="agent:two",
        segment="segment:two",
        name="Second agent",
        observed_at=2.0,
    )
    same_agent = _endpoint(
        event="event:destination",
        agent="agent:one",
        segment="segment:two",
        name="First agent",
        observed_at=2.0,
    )
    same_segment = _endpoint(
        event="event:destination",
        agent="agent:two",
        segment="segment:one",
        name="Second agent",
        observed_at=2.0,
    )

    assert plan_handoff_baton(source, same_event).refusal is (
        HandoffBatonRefusal.SAME_EVENT
    )
    assert plan_handoff_baton(source, same_agent).refusal is (
        HandoffBatonRefusal.SAME_AGENT
    )
    assert plan_handoff_baton(source, same_segment).refusal is (
        HandoffBatonRefusal.SAME_SEGMENT
    )


def test_reduce_motion_returns_a_finite_static_destination_highlight() -> None:
    decision = plan_handoff_baton(
        _endpoint(
            event="event:completed",
            agent="agent:claude",
            segment="segment:claude",
            name="Claude",
            observed_at=5.0,
        ),
        _endpoint(
            event="event:started",
            agent="agent:codex",
            segment="segment:codex",
            name="Codex",
            observed_at=6.0,
        ),
        reduce_motion=True,
    )

    assert decision.plan is not None
    assert decision.plan.motion.variant is HandoffBatonMotionVariant.STATIC_HIGHLIGHT
    assert decision.plan.motion.finite is True
    assert decision.plan.motion.spatial_travel is False
    assert decision.plan.motion.passes == 0
    assert decision.plan.motion.loops == 0
    assert decision.plan.motion.duration_ms > 0
    assert decision.plan.accessibility.label == "Work handoff"
    assert decision.plan.accessibility.value == "Claude to Codex"
    assert decision.plan.accessibility.announcement == "Handoff from Claude to Codex."
    assert "static" in decision.plan.accessibility.motion_description.lower()


def test_custom_window_is_capped_and_structural_inputs_are_validated() -> None:
    source = _endpoint(
        event="event:completed",
        agent="agent:one",
        segment="segment:one",
        name="First agent",
        observed_at=1.0,
    )
    destination = _endpoint(
        event="event:started",
        agent="agent:two",
        segment="segment:two",
        name="Second agent",
        observed_at=2.0,
    )

    with pytest.raises(ValueError, match="window"):
        plan_handoff_baton(source, destination, window_seconds=0.0)
    with pytest.raises(ValueError, match="window"):
        plan_handoff_baton(
            source,
            destination,
            window_seconds=MAX_HANDOFF_WINDOW_SECONDS + 0.001,
        )
    with pytest.raises(ValueError, match="window"):
        plan_handoff_baton(source, destination, window_seconds=float("nan"))
    with pytest.raises(ValueError, match="boolean"):
        plan_handoff_baton(
            source,
            destination,
            reduce_motion=1,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="observed time"):
        _endpoint(
            event="event:bad-time",
            agent="agent:bad",
            segment="segment:bad",
            name="Bad time",
            observed_at=float("inf"),
        )


def test_decision_and_nested_plans_are_immutable() -> None:
    decision = plan_handoff_baton(
        _endpoint(
            event="event:completed",
            agent="agent:one",
            segment="segment:one",
            name="First agent",
            observed_at=1.0,
        ),
        _endpoint(
            event="event:started",
            agent="agent:two",
            segment="segment:two",
            name="Second agent",
            observed_at=2.0,
        ),
    )
    assert decision.plan is not None

    with pytest.raises(FrozenInstanceError):
        decision.plan.motion.passes = 2  # type: ignore[misc]
