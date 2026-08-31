from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.announcer_stack import AnnouncerAlertIdentity
from sidepulse.ask_heartbeat_sync import (
    ASK_HEARTBEAT_BURST_SECONDS,
    ASK_HEARTBEAT_CYCLE_SECONDS,
    ASK_HEARTBEAT_PULSE_COUNT,
    ASK_HEARTBEAT_SIGNATURE,
    MAX_ASK_HEARTBEAT_PRESENTATIONS,
    AskHeartbeatPresentation,
    AskHeartbeatPresentationMode,
    AskHeartbeatValidationError,
    plan_ask_heartbeat_sync,
)

NOW = 1_800_000_000.0


def _ask(
    identity: str,
    offset: float = 0.0,
    stage: int = 0,
) -> AskHeartbeatPresentation:
    return AskHeartbeatPresentation(
        request_identity=AnnouncerAlertIdentity(f"request:v1:{identity}"),
        presented_at_epoch=NOW + offset,
        escalation_stage=stage,
    )


def _plan(
    *presentations: AskHeartbeatPresentation,
    reduce_motion: bool = False,
):
    return plan_ask_heartbeat_sync(
        presentations,
        accessibility_preferences=AccessibilityDisplayPreferences(
            reduce_motion=reduce_motion
        ),
    )


def test_reserved_signature_is_bounded_and_not_the_general_picker_motion() -> None:
    plan = _plan(_ask("a"))

    assert ASK_HEARTBEAT_SIGNATURE != "heartbeat"
    assert plan.cadence.signature == ASK_HEARTBEAT_SIGNATURE
    assert plan.cadence.mode is AskHeartbeatPresentationMode.SYNCED_HEARTBEAT
    assert plan.cadence.pulse_count == ASK_HEARTBEAT_PULSE_COUNT == 2
    assert plan.cadence.cycle_seconds == ASK_HEARTBEAT_CYCLE_SECONDS == 1.0
    assert plan.cadence.pulse_count / plan.cadence.cycle_seconds <= 2.0


def test_simultaneous_asks_share_phase_but_keep_exact_request_members() -> None:
    first = _ask("first", 0.0)
    second = _ask("second", 0.8)

    plan = _plan(first, second)

    assert len(plan.cohorts) == 1
    assert plan.request_count == 2
    assert plan.request_identities == (
        first.request_identity,
        second.request_identity,
    )
    assert plan.cohorts[0].request_identities == plan.request_identities
    assert len({member.presentation_identity for member in plan.cohorts[0].members}) == 2
    assert "first" not in plan.accessibility_value
    assert "second" not in plan.accessibility_value
    assert plan.accessibility_help == (
        "Open Screen Bar or the alert stack to identify and answer each asking session."
    )


def test_later_member_does_not_rename_or_restart_the_anchor_cadence() -> None:
    first = _ask("first", 0.0)
    one = _plan(first)
    two = _plan(first, _ask("second", 1.0))
    reversed_input = _plan(_ask("second", 1.0), first)

    assert one.cohorts[0].sync_identity == two.cohorts[0].sync_identity
    assert reversed_input == two


def test_burst_window_is_anchored_and_does_not_extend_with_each_arrival() -> None:
    first = _ask("first", 0.0)
    middle = _ask("middle", ASK_HEARTBEAT_BURST_SECONDS - 0.1)
    outside = _ask("outside", ASK_HEARTBEAT_BURST_SECONDS + 0.1)

    plan = _plan(first, middle, outside)

    assert tuple(len(cohort.members) for cohort in plan.cohorts) == (2, 1)
    assert plan.cohorts[0].request_identities == (
        first.request_identity,
        middle.request_identity,
    )
    assert plan.cohorts[1].request_identities == (outside.request_identity,)


def test_exact_window_boundary_and_different_escalation_do_not_share_phase() -> None:
    fresh = _ask("fresh", 0.0, 0)
    exact_boundary = _ask("boundary", ASK_HEARTBEAT_BURST_SECONDS, 0)
    escalated = _ask("escalated", 0.5, 2)

    plan = _plan(fresh, exact_boundary, escalated)

    assert len(plan.cohorts) == 3
    assert {cohort.escalation_stage for cohort in plan.cohorts} == {0, 2}
    assert len({cohort.sync_identity for cohort in plan.cohorts}) == 3


def test_duplicate_presentation_coalesces_without_absorbing_another_request() -> None:
    first = _ask("first", 0.0)
    duplicate = _ask("first", 0.5)
    second = _ask("second", 0.7)

    plan = _plan(first, duplicate, second)

    assert plan.request_count == 2
    assert plan.request_identities == (
        first.request_identity,
        second.request_identity,
    )
    assert plan.cohorts[0].members[0].presented_at_epoch == first.presented_at_epoch
    assert plan.cohorts[0].members[1].request_identity == second.request_identity


def test_same_request_cannot_occupy_multiple_temporal_or_stage_cohorts() -> None:
    with pytest.raises(
        AskHeartbeatValidationError,
        match="one request cannot occupy multiple",
    ):
        _plan(
            _ask("same", 0.0, 0),
            _ask("same", ASK_HEARTBEAT_BURST_SECONDS + 0.1, 0),
        )

    with pytest.raises(
        AskHeartbeatValidationError,
        match="one request cannot occupy multiple",
    ):
        _plan(_ask("same", 0.0, 0), _ask("same", 0.1, 1))


def test_presentation_identity_changes_with_time_or_stage_not_input_order() -> None:
    baseline = _ask("one", 0.0, 0)
    same = _ask("one", 0.0, 0)
    later = _ask("one", 0.1, 0)
    escalated = _ask("one", 0.0, 1)

    assert baseline.identity == same.identity
    assert baseline.identity != later.identity
    assert baseline.identity != escalated.identity


def test_reduce_motion_substitutes_static_attention_without_losing_semantics() -> None:
    first = _ask("first")
    second = _ask("second", 0.5)

    animated = _plan(first, second)
    static = _plan(first, second, reduce_motion=True)

    assert static.request_identities == animated.request_identities
    assert static.cohorts == animated.cohorts
    assert static.cadence.mode is AskHeartbeatPresentationMode.STATIC_ATTENTION
    assert static.cadence.pulse_count == 0
    assert static.cadence.cycle_seconds is None
    assert "Reduce Motion is on" in static.accessibility_value
    assert static.accessibility_label == "Synchronized ask attention"


def test_empty_plan_is_accessible_and_contains_no_motion_work() -> None:
    plan = _plan()

    assert plan.cohorts == ()
    assert plan.request_count == 0
    assert plan.request_identities == ()
    assert plan.accessibility_label == "Ask heartbeat"
    assert plan.accessibility_value == "No asking sessions need attention."


def test_public_records_are_frozen_and_do_not_offer_content_fields() -> None:
    presentation = _ask("private-request-token")
    plan = _plan(presentation)

    with pytest.raises(FrozenInstanceError):
        presentation.escalation_stage = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.request_count = 4  # type: ignore[misc]

    field_names = {
        field.name
        for record in (presentation, plan.cohorts[0].members[0], plan.cohorts[0], plan)
        for field in fields(record)
    }
    assert not field_names & {"prompt", "question", "message", "content", "body"}


@pytest.mark.parametrize(
    "presentation",
    (
        lambda: AskHeartbeatPresentation(  # type: ignore[arg-type]
            request_identity="request:v1:not-canonical",
            presented_at_epoch=NOW,
        ),
        lambda: AskHeartbeatPresentation(  # type: ignore[arg-type]
            request_identity=AnnouncerAlertIdentity("request:v1:a"),
            presented_at_epoch=True,
        ),
        lambda: AskHeartbeatPresentation(
            request_identity=AnnouncerAlertIdentity("request:v1:a"),
            presented_at_epoch=float("inf"),
        ),
        lambda: AskHeartbeatPresentation(
            request_identity=AnnouncerAlertIdentity("request:v1:a"),
            presented_at_epoch=NOW,
            escalation_stage=4,
        ),
    ),
)
def test_invalid_presentation_facts_are_rejected(presentation) -> None:
    with pytest.raises(AskHeartbeatValidationError):
        presentation()


def test_planner_rejects_invalid_preferences_window_items_and_oversized_input() -> None:
    with pytest.raises(AskHeartbeatValidationError, match="accessibility"):
        plan_ask_heartbeat_sync((), accessibility_preferences=None)  # type: ignore[arg-type]
    with pytest.raises(AskHeartbeatValidationError, match="accessibility"):
        plan_ask_heartbeat_sync(
            (),
            accessibility_preferences=AccessibilityDisplayPreferences(
                reduce_motion=1  # type: ignore[arg-type]
            ),
        )
    with pytest.raises(AskHeartbeatValidationError, match="burst window"):
        plan_ask_heartbeat_sync(
            (),
            accessibility_preferences=AccessibilityDisplayPreferences(),
            burst_window_seconds=0.0,
        )
    with pytest.raises(AskHeartbeatValidationError, match="presentations"):
        plan_ask_heartbeat_sync(
            (object(),),  # type: ignore[arg-type]
            accessibility_preferences=AccessibilityDisplayPreferences(),
        )
    with pytest.raises(AskHeartbeatValidationError, match="presentations"):
        plan_ask_heartbeat_sync(
            tuple(
                _ask(f"request-{index}", index * (ASK_HEARTBEAT_BURST_SECONDS + 1))
                for index in range(MAX_ASK_HEARTBEAT_PRESENTATIONS + 1)
            ),
            accessibility_preferences=AccessibilityDisplayPreferences(),
        )
