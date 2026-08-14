from __future__ import annotations

from dataclasses import fields

from sidepulse.capacity_authority import select_binding_lanes
from sidepulse.capacity_refresh import (
    RefreshCause,
    RefreshDecision,
    RefreshDecisionKind,
    RefreshDecisionReason,
    RefreshSourceKey,
)
from sidepulse.capacity_types import (
    CapacitySnapshot,
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ExecutionContext,
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from sidepulse.capacity_view import (
    CapacityAccessibilityChildModel,
    CapacityAccessibilityGroupModel,
    build_capacity_card,
    build_capacity_card_accessibility,
    build_manual_refresh_status,
)

NOW = 10_000.0


def _lane(
    *,
    provider: str,
    instance: str,
    semantic_name: str,
    window: str,
    horizon: QuotaHorizon,
    remaining: float,
    value_state: ObservationState,
    stale: bool = False,
) -> QuotaLaneObservation:
    source = SourceKey(provider, "quota", instance, "capacity.v1")
    health = CapacitySourceHealth(
        source=source,
        kind=SourceHealthKind.STALE if stale else SourceHealthKind.HEALTHY,
        observed_at=NOW - 120.0 if stale else NOW,
        last_attempt_at=NOW,
        retry_at=None,
        reason_code=None,
        has_last_known_good=stale,
    )
    return QuotaLaneObservation(
        key=QuotaLaneKey(
            source=source,
            opaque_scope="all",
            pool="general",
            model=None,
            window=window,
            effect=QuotaEffect.ALL_WORKLOADS,
        ),
        semantic_name=semantic_name,
        horizon=horizon,
        value=CapacityValue(CapacityUnit.PERCENT_REMAINING, remaining, value_state),
        reset=ResetFact(ResetState.FUTURE, NOW + 3_600.0, 300.0, NOW),
        observed_at=health.observed_at,
        source_health=health,
        account_discriminator="account-a",
    )


def _card(now: float = NOW):
    codex = _lane(
        provider="codex",
        instance="local:primary",
        semantic_name="Session window",
        window="session",
        horizon=QuotaHorizon.SHORT,
        remaining=0.0,
        value_state=ObservationState.OBSERVED_ZERO,
    )
    claude = _lane(
        provider="claude",
        instance="remote:primary",
        semantic_name="Weekly window",
        window="weekly",
        horizon=QuotaHorizon.LONG,
        remaining=80.0,
        value_state=ObservationState.LAST_KNOWN_GOOD,
        stale=True,
    )
    snapshot = CapacitySnapshot(NOW, (claude, codex), (claude.source_health, codex.source_health))
    projection = select_binding_lanes(
        snapshot,
        ExecutionContext(
            ("codex", "claude"),
            ("local:primary", "remote:primary"),
            None,
            None,
            # The two sources this fixture actually has, as pairs. The flat
            # form matched their cross product too.
            (("codex", "local:primary"), ("claude", "remote:primary")),
        ),
        now,
        allow_unbound_legacy=True,
    )
    return build_capacity_card(projection, now)


def test_capacity_card_is_one_stable_group_with_children_in_visual_order() -> None:
    accessibility = build_capacity_card_accessibility(_card(), NOW)

    assert accessibility.label == "Capacity"
    assert accessibility.value == "2 capacity limits"
    assert [child.label for child in accessibility.children] == [
        "Codex, Session window",
        "Claude, Weekly window",
    ]
    assert accessibility.children[0].value == "0% left, Resets in 1h, Updated just now"
    assert accessibility.children[1].value == "80% left, Resets in 1h, Updated 2m ago, stale"
    assert all(child.help == "Capacity limit details" for child in accessibility.children)


def test_accessibility_models_define_semantics_without_claiming_appkit_roles() -> None:
    group_field_names = {field.name for field in fields(CapacityAccessibilityGroupModel)}
    child_field_names = {field.name for field in fields(CapacityAccessibilityChildModel)}

    assert group_field_names == {"label", "value", "help", "children"}
    assert child_field_names == {
        "label",
        "value",
        "help",
        "countdown_announcement_minute",
    }
    assert "role" not in group_field_names | child_field_names
    assert "color" not in group_field_names | child_field_names


def test_stale_and_zero_are_announced_without_relying_on_color() -> None:
    accessibility = build_capacity_card_accessibility(_card(), NOW)
    spoken = " | ".join(child.value for child in accessibility.children)

    assert "0% left" in spoken
    assert "stale" in spoken
    assert "color" not in spoken.lower()
    assert "used" not in spoken.lower()


def test_no_source_status_remains_nonempty_and_does_not_create_fake_child() -> None:
    from sidepulse.capacity_authority import CapacityProjection

    card = build_capacity_card(CapacityProjection((), ()), NOW)
    accessibility = build_capacity_card_accessibility(card, NOW)

    assert accessibility.label == "Capacity"
    assert accessibility.value == "No capacity sources"
    assert accessibility.children == ()


def test_countdown_announcement_key_is_coalesced_to_one_wall_clock_minute() -> None:
    card = _card()

    first = build_capacity_card_accessibility(card, NOW)
    same_minute = build_capacity_card_accessibility(card, NOW + 19.0)
    next_minute = build_capacity_card_accessibility(card, NOW + 60.0)

    assert first.children[0].countdown_announcement_minute == int(NOW // 60)
    assert same_minute.children[0].countdown_announcement_minute == int((NOW + 19.0) // 60)
    assert first.children[0].countdown_announcement_minute == same_minute.children[0].countdown_announcement_minute
    assert next_minute.children[0].countdown_announcement_minute != first.children[0].countdown_announcement_minute


def test_manual_refresh_status_is_pure_semantic_state_without_native_role() -> None:
    source = SourceKey("codex", "quota", "local:primary", "capacity.v1")
    decision = RefreshDecision(
        RefreshDecisionKind.QUEUED_FOR_COOLDOWN,
        RefreshSourceKey(source, "general", "account-a"),
        RefreshCause.MANUAL,
        None,
        NOW + 120.0,
        RefreshDecisionReason.COOLDOWN,
    )

    status = build_manual_refresh_status(decision, NOW)

    assert status.text == "Refresh queued for 2m"
    assert status.can_request is True
    assert status.announcement_minute == int(NOW // 60)
    assert {field.name for field in fields(status)} == {
        "text",
        "can_request",
        "announcement_minute",
    }


def test_accessibility_text_is_bounded_and_nonempty() -> None:
    accessibility = build_capacity_card_accessibility(_card(), NOW)

    values = (
        accessibility.label,
        accessibility.value,
        accessibility.help,
        *(text for child in accessibility.children for text in (child.label, child.value, child.help)),
    )
    assert all(value for value in values)
    assert all(len(value) <= 256 for value in values)
