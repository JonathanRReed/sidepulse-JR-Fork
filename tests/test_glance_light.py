from __future__ import annotations

import json
from dataclasses import fields

import pytest

from sidepulse.glance_light import (
    GLANCE_LIGHT_DESTINATIONS,
    GLANCE_LIGHT_DOCUMENT_SCHEMA,
    GLANCE_LIGHT_DOCUMENT_VERSION,
    GlanceDestination,
    GlanceEnvironment,
    GlanceKind,
    GlanceLightState,
    GlancePattern,
    GlancePriority,
    GlancePrivacyClass,
    acknowledge_glance_notification,
    default_glance_cadence,
    expire_glance_notifications,
    make_glance_notification,
    mark_glance_notification_seen,
    plan_glance_light,
    resolve_glance_notification,
    restore_glance_light_document,
    serialize_glance_light_document,
)

NOW = 1_800_000_000.0


def _notification(
    notification_id: str,
    kind: GlanceKind,
    *,
    created_at_epoch: float = NOW,
    destinations: tuple[GlanceDestination, ...] = GLANCE_LIGHT_DESTINATIONS,
):
    return make_glance_notification(
        notification_id=notification_id,
        kind=kind,
        created_at_epoch=created_at_epoch,
        destinations=destinations,
    )


def test_default_language_and_lifetimes_match_the_glance_light_contract() -> None:
    ask = _notification("gl:ask", GlanceKind.UNANSWERED_ASK)
    failure = _notification("gl:failure", GlanceKind.FAILURE)
    completion = _notification("gl:completion", GlanceKind.COMPLETED_UNSEEN)
    informational = _notification("gl:information", GlanceKind.INFORMATIONAL)

    assert ask.priority is GlancePriority.ACTION_REQUIRED
    assert ask.privacy_class is GlancePrivacyClass.CONTENT_FREE
    assert ask.expires_at_epoch is None
    assert default_glance_cadence(ask.kind).pattern is GlancePattern.DOUBLE_SOFT_PULSE
    assert default_glance_cadence(ask.kind).pulse_count == 2
    assert default_glance_cadence(ask.kind).uses_identity_tint

    assert failure.priority is GlancePriority.FAILURE
    assert failure.expires_at_epoch is None
    assert default_glance_cadence(failure.kind).pattern is GlancePattern.TRIPLE_FAILURE
    assert default_glance_cadence(failure.kind).pulse_count == 3
    assert default_glance_cadence(failure.kind).positional_failure_signature

    assert completion.priority is GlancePriority.COMPLETION
    assert completion.expires_at_epoch == NOW + 15 * 60
    assert default_glance_cadence(completion.kind).pattern is GlancePattern.SHORT_WINK
    assert default_glance_cadence(completion.kind).repeat_interval_seconds == 30.0

    assert informational.priority is GlancePriority.INFORMATIONAL
    assert informational.expires_at_epoch == NOW + 5 * 60
    assert default_glance_cadence(informational.kind).pattern is GlancePattern.STEADY_DIM
    assert default_glance_cadence(informational.kind).repeat_interval_seconds is None


def test_planner_selects_highest_priority_and_summarizes_all_pending_items() -> None:
    state = GlanceLightState(
        (
            _notification("gl:information", GlanceKind.INFORMATIONAL, created_at_epoch=NOW - 3),
            _notification("gl:ask", GlanceKind.UNANSWERED_ASK, created_at_epoch=NOW - 2),
            _notification("gl:failure", GlanceKind.FAILURE, created_at_epoch=NOW - 1),
        )
    )

    plan = plan_glance_light(state, now_epoch=NOW)

    assert plan.selected_notification_id == "gl:failure"
    assert plan.notification_count == 3
    assert plan.count_summary == "3 notifications"
    assert plan.announcer_summary == plan.count_summary
    assert tuple(item.destination for item in plan.surface_plans) == GLANCE_LIGHT_DESTINATIONS
    assert all(item.notification_count == 3 for item in plan.surface_plans)
    assert all(item.pattern is GlancePattern.TRIPLE_FAILURE for item in plan.surface_plans)


def test_equal_priority_selection_is_oldest_first_then_exact_id() -> None:
    state = GlanceLightState(
        (
            _notification("gl:z", GlanceKind.UNANSWERED_ASK, created_at_epoch=NOW - 1),
            _notification("gl:b", GlanceKind.UNANSWERED_ASK, created_at_epoch=NOW - 2),
            _notification("gl:a", GlanceKind.UNANSWERED_ASK, created_at_epoch=NOW - 2),
        )
    )

    plan = plan_glance_light(state, now_epoch=NOW)

    assert plan.selected_notification_id == "gl:a"


def test_surface_plans_are_bounded_low_energy_and_respect_destinations() -> None:
    notification = _notification(
        "gl:dot-and-orb",
        GlanceKind.UNANSWERED_ASK,
        destinations=(GlanceDestination.DOT, GlanceDestination.SCREEN_BAR_ORB),
    )

    plan = plan_glance_light(GlanceLightState((notification,)), now_epoch=NOW)
    by_destination = {item.destination: item for item in plan.surface_plans}

    assert by_destination[GlanceDestination.DOT].active
    assert by_destination[GlanceDestination.SCREEN_BAR_ORB].active
    assert not by_destination[GlanceDestination.PRO_ENDPOINT].active
    assert not by_destination[GlanceDestination.MENU_ACCENT].active
    assert all(0.0 <= item.intensity <= 0.25 for item in plan.surface_plans)
    assert all(item.energy_class == "low" for item in plan.surface_plans)


def test_dnd_is_dark_by_default_and_optional_asks_only_is_a_dim_marker() -> None:
    state = GlanceLightState(
        (
            _notification("gl:failure", GlanceKind.FAILURE, created_at_epoch=NOW - 2),
            _notification("gl:ask", GlanceKind.UNANSWERED_ASK, created_at_epoch=NOW - 1),
        )
    )

    dark = plan_glance_light(
        state,
        now_epoch=NOW,
        environment=GlanceEnvironment(dnd_active=True),
    )
    asks_only = plan_glance_light(
        state,
        now_epoch=NOW,
        environment=GlanceEnvironment(dnd_active=True, dim_asks_in_dnd=True),
    )

    assert dark.selected_notification_id is None
    assert dark.notification_count == 2
    assert all(not surface.active for surface in dark.surface_plans)
    assert all(surface.pattern is GlancePattern.DARK for surface in dark.surface_plans)

    assert asks_only.selected_notification_id == "gl:ask"
    assert asks_only.notification_count == 2
    assert all(surface.pattern is GlancePattern.STATIC_MARKER for surface in asks_only.surface_plans)
    assert all(surface.intensity <= 0.08 for surface in asks_only.surface_plans)


@pytest.mark.parametrize(
    "environment",
    (
        GlanceEnvironment(low_power=True),
        GlanceEnvironment(serious_thermal=True),
        GlanceEnvironment(low_power=True, serious_thermal=True),
    ),
)
def test_constrained_power_or_thermal_state_uses_a_static_marker(
    environment: GlanceEnvironment,
) -> None:
    state = GlanceLightState((_notification("gl:ask", GlanceKind.UNANSWERED_ASK),))

    plan = plan_glance_light(state, now_epoch=NOW, environment=environment)

    assert plan.selected_notification_id == "gl:ask"
    assert all(surface.pattern is GlancePattern.STATIC_MARKER for surface in plan.surface_plans)
    assert all(surface.repeat_interval_seconds is None for surface in plan.surface_plans)
    assert all(surface.intensity <= 0.12 for surface in plan.surface_plans)


def test_acknowledgement_by_exact_id_clears_every_destination() -> None:
    first = _notification("gl:Exact", GlanceKind.UNANSWERED_ASK)
    second = _notification("gl:exact", GlanceKind.INFORMATIONAL)
    state = GlanceLightState((first, second))

    acknowledged = acknowledge_glance_notification(
        state,
        notification_id="gl:Exact",
        acknowledged_at_epoch=NOW + 1,
    )
    plan = plan_glance_light(acknowledged, now_epoch=NOW + 2)

    assert acknowledged.notifications[0].acknowledged_at_epoch == NOW + 1
    assert acknowledged.notifications[0].destinations == GLANCE_LIGHT_DESTINATIONS
    assert acknowledged.notifications[1].acknowledged_at_epoch is None
    assert plan.selected_notification_id == "gl:exact"
    assert plan.notification_count == 1


def test_seen_and_resolved_are_distinct_receipts_and_each_clears_surfaces() -> None:
    original = GlanceLightState((_notification("gl:ask", GlanceKind.UNANSWERED_ASK),))

    seen = mark_glance_notification_seen(
        original,
        notification_id="gl:ask",
        seen_at_epoch=NOW + 1,
    )
    resolved = resolve_glance_notification(
        seen,
        notification_id="gl:ask",
        resolved_at_epoch=NOW + 2,
    )

    assert seen.notifications[0].seen_at_epoch == NOW + 1
    assert seen.notifications[0].resolved_at_epoch is None
    assert seen.notifications[0].acknowledged_at_epoch is None
    assert resolved.notifications[0].seen_at_epoch == NOW + 1
    assert resolved.notifications[0].resolved_at_epoch == NOW + 2
    assert plan_glance_light(seen, now_epoch=NOW + 1).selected_notification_id is None
    assert plan_glance_light(resolved, now_epoch=NOW + 2).selected_notification_id is None


def test_expiration_is_deterministic_at_the_exact_boundary() -> None:
    completion = _notification("gl:completion", GlanceKind.COMPLETED_UNSEEN)
    state = GlanceLightState((completion,))
    expiry = NOW + 15 * 60

    assert expire_glance_notifications(state, now_epoch=expiry - 0.001) == state
    assert expire_glance_notifications(state, now_epoch=expiry).notifications == ()
    assert plan_glance_light(state, now_epoch=expiry).notification_count == 0


def test_future_notifications_are_not_presented_early() -> None:
    future = _notification(
        "gl:future",
        GlanceKind.FAILURE,
        created_at_epoch=NOW + 1,
    )

    plan = plan_glance_light(GlanceLightState((future,)), now_epoch=NOW)

    assert plan.selected_notification_id is None
    assert plan.notification_count == 0
    assert all(not surface.active for surface in plan.surface_plans)


def test_exact_versioned_json_document_round_trips_without_content_fields() -> None:
    notification = _notification("gl:roundtrip", GlanceKind.COMPLETED_UNSEEN)
    state = mark_glance_notification_seen(
        GlanceLightState((notification,)),
        notification_id=notification.notification_id,
        seen_at_epoch=NOW + 1,
    )

    encoded = serialize_glance_light_document(state)

    assert encoded is not None
    document = json.loads(encoded)
    assert set(document) == {"schema", "version", "notifications"}
    assert document["schema"] == GLANCE_LIGHT_DOCUMENT_SCHEMA
    assert document["version"] == GLANCE_LIGHT_DOCUMENT_VERSION
    assert set(document["notifications"][0]) == {
        "id",
        "kind",
        "priority",
        "created_at_epoch",
        "expires_at_epoch",
        "acknowledged_at_epoch",
        "seen_at_epoch",
        "resolved_at_epoch",
        "privacy_class",
        "destinations",
    }
    assert restore_glance_light_document(encoded) == state

    model_fields = {field.name for field in fields(type(notification))}
    assert not model_fields & {
        "prompt",
        "text",
        "message",
        "content",
        "payload",
        "provider_payload",
        "path",
        "transcript",
    }


@pytest.mark.parametrize(
    "mutate",
    (
        lambda document: document.update(extra=True),
        lambda document: document.update(version=2),
        lambda document: document.update(schema="other"),
        lambda document: document["notifications"][0].update(extra=True),
        lambda document: document["notifications"][0].update(kind="prompt"),
        lambda document: document["notifications"][0].update(privacy_class="private_text"),
        lambda document: document["notifications"].append(dict(document["notifications"][0])),
    ),
)
def test_restore_fails_closed_for_non_exact_or_invalid_documents(mutate) -> None:
    encoded = serialize_glance_light_document(
        GlanceLightState((_notification("gl:valid", GlanceKind.INFORMATIONAL),))
    )
    assert encoded is not None
    document = json.loads(encoded)
    mutate(document)

    assert restore_glance_light_document(json.dumps(document)) is None


@pytest.mark.parametrize(
    "document",
    (
        None,
        7,
        "",
        "[]",
        "not json",
        '{"schema":"sidepulse.glance-light","schema":"sidepulse.glance-light","version":1,"notifications":[]}',
        '{"schema":"sidepulse.glance-light","version":1,"notifications":NaN}',
    ),
)
def test_restore_fails_closed_without_a_partial_state(document: object) -> None:
    assert restore_glance_light_document(document) is None


def test_invalid_planner_inputs_fail_closed_to_a_dark_plan() -> None:
    invalid_state = plan_glance_light(object(), now_epoch=NOW)  # type: ignore[arg-type]
    invalid_clock = plan_glance_light(GlanceLightState(), now_epoch=float("nan"))
    invalid_environment = plan_glance_light(
        GlanceLightState((_notification("gl:ask", GlanceKind.UNANSWERED_ASK),)),
        now_epoch=NOW,
        environment=object(),  # type: ignore[arg-type]
    )

    for plan in (invalid_state, invalid_clock, invalid_environment):
        assert plan.selected_notification_id is None
        assert plan.notification_count == 0
        assert all(not surface.active for surface in plan.surface_plans)
