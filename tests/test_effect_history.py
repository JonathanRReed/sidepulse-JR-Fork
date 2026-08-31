from __future__ import annotations

import json
from dataclasses import fields

import pytest

from sidepulse.effect_history import (
    EFFECT_EVENT_VERSION,
    MAX_EFFECT_EVENTS,
    MAX_EFFECT_HISTORY_BYTES,
    EffectAcknowledgementSource,
    EffectBrowserRow,
    EffectEvent,
    EffectHistory,
    EffectHistoryValidationError,
    EffectOutcome,
    EffectSemanticCategory,
    EffectSuppressionReason,
    EffectSurface,
    mark_effect_history_seen,
    project_effect_history,
    record_effect_event,
    record_effect_events,
)

NOW = 1_800_000_000.0


def _event(
    suffix: str = "one",
    *,
    seconds_ago: float = 0.0,
    surface: EffectSurface = EffectSurface.GLANCE_LIGHT,
    outcome: EffectOutcome = EffectOutcome.SHOWN,
    suppression_reason: EffectSuppressionReason | None = None,
    acknowledgement_source: EffectAcknowledgementSource | None = None,
    effect_id: str = "ask-heartbeat",
) -> EffectEvent:
    return EffectEvent(
        event_id=f"effect-event:{suffix}",
        occurred_at_epoch=NOW - seconds_ago,
        effect_id=effect_id,
        semantic_category=EffectSemanticCategory.ATTENTION,
        surface=surface,
        outcome=outcome,
        suppression_reason=suppression_reason,
        acknowledgement_source=acknowledgement_source,
    )


def test_event_is_a_versioned_exact_content_free_fact() -> None:
    event = _event()

    assert event.version == EFFECT_EVENT_VERSION == 1
    assert {field.name for field in fields(event)} == {
        "acknowledgement_source",
        "effect_id",
        "event_id",
        "occurred_at_epoch",
        "outcome",
        "semantic_category",
        "suppression_reason",
        "surface",
        "version",
    }
    for forbidden in ("prompt", "message", "session", "path", "url"):
        assert not hasattr(event, forbidden)


@pytest.mark.parametrize(
    ("outcome", "suppression_reason", "acknowledgement_source"),
    (
        (EffectOutcome.SHOWN, None, EffectAcknowledgementSource.SCREEN_BAR),
        (EffectOutcome.SUPPRESSED, None, None),
        (
            EffectOutcome.SUPPRESSED,
            EffectSuppressionReason.DO_NOT_DISTURB,
            EffectAcknowledgementSource.SCREEN_BAR,
        ),
        (EffectOutcome.ACKNOWLEDGED, None, None),
        (
            EffectOutcome.ACKNOWLEDGED,
            EffectSuppressionReason.FOCUS,
            EffectAcknowledgementSource.AGENT_BROWSER,
        ),
        (EffectOutcome.EXPIRED, EffectSuppressionReason.EXPIRED, None),
    ),
)
def test_outcome_specific_metadata_cannot_be_ambiguous(
    outcome: EffectOutcome,
    suppression_reason: EffectSuppressionReason | None,
    acknowledgement_source: EffectAcknowledgementSource | None,
) -> None:
    with pytest.raises(EffectHistoryValidationError):
        _event(
            outcome=outcome,
            suppression_reason=suppression_reason,
            acknowledgement_source=acknowledgement_source,
        )


@pytest.mark.parametrize(
    "event_id",
    (
        "",
        "../effect-event:one",
        "effect/event/one",
        "https://private.example/effect",
        "effect-event:one?prompt=private",
        "effect-event:one\nmessage",
        "x" * 129,
    ),
)
def test_product_event_identity_is_opaque_and_not_a_content_carrier(
    event_id: str,
) -> None:
    with pytest.raises(EffectHistoryValidationError):
        EffectEvent(
            event_id=event_id,
            occurred_at_epoch=NOW,
            effect_id="pulse",
            semantic_category=EffectSemanticCategory.ATTENTION,
            surface=EffectSurface.SCREEN_BAR,
            outcome=EffectOutcome.SHOWN,
        )


def test_exact_duplicate_is_idempotent() -> None:
    event = _event()
    history = record_effect_event(EffectHistory(), event)

    assert record_effect_event(history, event) is history


def test_same_event_and_time_delivered_to_two_surfaces_is_not_collapsed() -> None:
    light = _event(surface=EffectSurface.GLANCE_LIGHT)
    screen_bar = _event(surface=EffectSurface.SCREEN_BAR)

    history = record_effect_events(EffectHistory(), (light, screen_bar))

    assert len(history.events) == 2
    assert {event.surface for event in history.events} == {
        EffectSurface.GLANCE_LIGHT,
        EffectSurface.SCREEN_BAR,
    }


def test_history_count_cap_retains_newest_events() -> None:
    history = record_effect_events(
        EffectHistory(),
        tuple(
            _event(f"event-{index:03d}", seconds_ago=float(index), effect_id="p")
            for index in range(MAX_EFFECT_EVENTS + 40)
        ),
    )

    assert len(history.events) == MAX_EFFECT_EVENTS
    assert history.events[0].event_id == "effect-event:event-000"
    assert history.events[-1].event_id == f"effect-event:event-{MAX_EFFECT_EVENTS - 1:03d}"


def test_history_byte_cap_binds_independently_of_count() -> None:
    history = record_effect_events(
        EffectHistory(),
        tuple(
            _event(
                f"{'e' * 100}{index:03d}",
                seconds_ago=float(index),
                effect_id=f"{'f' * 80}{index:03d}",
            )
            for index in range(MAX_EFFECT_EVENTS)
        ),
    )
    payload = [
        {
            "acknowledgement_source": (
                None
                if event.acknowledgement_source is None
                else event.acknowledgement_source.value
            ),
            "effect_id": event.effect_id,
            "event_id": event.event_id,
            "occurred_at_epoch": event.occurred_at_epoch,
            "outcome": event.outcome.value,
            "semantic_category": event.semantic_category.value,
            "suppression_reason": (
                None
                if event.suppression_reason is None
                else event.suppression_reason.value
            ),
            "surface": event.surface.value,
            "version": event.version,
        }
        for event in history.events
    ]

    assert len(history.events) < MAX_EFFECT_EVENTS
    assert len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) < (
        MAX_EFFECT_HISTORY_BYTES
    )
    assert history.events[0].event_id.endswith("000")


def test_seen_watermark_moves_only_forward_and_unseen_is_strictly_after_it() -> None:
    history = record_effect_events(
        EffectHistory(),
        (
            _event("old", seconds_ago=60.0),
            _event("boundary", seconds_ago=30.0),
            _event("new"),
        ),
    )

    seen = mark_effect_history_seen(history, NOW - 30.0)
    rewound = mark_effect_history_seen(seen, NOW - 90.0)

    assert tuple(event.event_id for event in seen.unseen) == ("effect-event:new",)
    assert rewound is seen


def test_browser_projection_explains_shown_suppressed_acknowledged_and_expired() -> None:
    history = record_effect_events(
        EffectHistory(),
        (
            _event("shown"),
            _event(
                "suppressed",
                seconds_ago=1.0,
                outcome=EffectOutcome.SUPPRESSED,
                suppression_reason=EffectSuppressionReason.DO_NOT_DISTURB,
            ),
            _event(
                "acknowledged",
                seconds_ago=2.0,
                outcome=EffectOutcome.ACKNOWLEDGED,
                acknowledgement_source=EffectAcknowledgementSource.SCREEN_BAR,
            ),
            _event("expired", seconds_ago=3.0, outcome=EffectOutcome.EXPIRED),
        ),
    )

    rows = project_effect_history(history)

    assert all(type(row) is EffectBrowserRow for row in rows)
    assert [row.explanation for row in rows] == [
        "Shown on Glance Light for Attention.",
        "Suppressed on Glance Light by Do Not Disturb.",
        "Acknowledged from Screen Bar for Glance Light.",
        "Expired before Glance Light could present it.",
    ]
    assert all(row.unseen for row in rows)
    assert {field.name for field in fields(rows[0])} == {
        "effect_id",
        "event_id",
        "explanation",
        "occurred_at_epoch",
        "outcome",
        "semantic_category",
        "surface",
        "unseen",
    }
    assert not any(
        forbidden in row.explanation.casefold()
        for row in rows
        for forbidden in ("prompt", "message", "session", "path", "url")
    )
