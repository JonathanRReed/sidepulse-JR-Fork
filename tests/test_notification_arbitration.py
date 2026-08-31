from __future__ import annotations

from sidepulse.capacity_types import SourceKey
from sidepulse.interruption_policy import ActionTokenBinding
from sidepulse.notification_arbitration import (
    issue_notification_action_binding,
    plan_semantic_notification,
    prune_notification_action_bindings,
    resolve_notification_work_key,
    should_post_completion_notification,
)
from sidepulse.operator_state import InterruptionClass, SemanticEventKey, TransitionKind
from sidepulse.product_identity import PRODUCT_DISPLAY_NAME
from sidepulse.provider_facts import (
    EventToken,
    ProviderWatermark,
    RequestIdentifier,
    RequestKey,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
)

NOW = 1_800_000_000.0


def _event_keys():
    source = SourceKey("codex", "hooks", "local:test", "live_agent_events")
    work_key = WorkKey(source, WorkIdentifier("work:test"))
    request_key = RequestKey(work_key, RequestIdentifier("request:test"))
    watermark = ProviderWatermark(
        source,
        WatermarkBasis.PROVIDER_SEQUENCE,
        NOW,
        EventToken("event:test"),
        7,
        1,
    )
    return (
        work_key,
        request_key,
        SemanticEventKey(work_key, TransitionKind.COMPLETED, watermark),
        SemanticEventKey(request_key, TransitionKind.REQUEST_OPENED, watermark),
    )


def test_prune_notification_bindings_drops_expired_and_stale_generations() -> None:
    _work_key, _request_key, completed_event, attention_event = _event_keys()
    fresh_binding = ActionTokenBinding(
        token="A" * 43,
        event_fingerprint="a" * 64,
        operator_generation=9,
        expires_at_epoch=NOW + 60.0,
    )
    expired_binding = ActionTokenBinding(
        token="B" * 43,
        event_fingerprint="b" * 64,
        operator_generation=9,
        expires_at_epoch=NOW - 1.0,
    )
    stale_generation = ActionTokenBinding(
        token="C" * 43,
        event_fingerprint="c" * 64,
        operator_generation=8,
        expires_at_epoch=NOW + 60.0,
    )

    retained = prune_notification_action_bindings(
        {
            fresh_binding.token: (fresh_binding, completed_event),
            expired_binding.token: (expired_binding, completed_event),
            stale_generation.token: (stale_generation, attention_event),
        },
        now=NOW,
        current_generation=9,
        max_bindings=4,
    )

    assert retained == {fresh_binding.token: (fresh_binding, completed_event)}


def test_issue_notification_binding_keeps_the_newest_bounded_set() -> None:
    _work_key, _request_key, completed_event, _attention_event = _event_keys()
    older = ActionTokenBinding(
        token="D" * 43,
        event_fingerprint="d" * 64,
        operator_generation=4,
        expires_at_epoch=NOW + 10.0,
    )
    newer = ActionTokenBinding(
        token="E" * 43,
        event_fingerprint="e" * 64,
        operator_generation=4,
        expires_at_epoch=NOW + 20.0,
    )

    issued = issue_notification_action_binding(
        event_key=completed_event,
        operator_generation=4,
        now=NOW,
        randomness=b"x" * 32,
        existing_bindings={
            older.token: (older, completed_event),
            newer.token: (newer, completed_event),
        },
        max_bindings=2,
        ttl_seconds=300.0,
    )

    assert issued is not None
    bindings, binding = issued
    assert binding.token in bindings
    assert older.token not in bindings
    assert newer.token in bindings


def test_plan_semantic_notification_returns_content_free_delivery_payload() -> None:
    _work_key, request_key, _completed_event, attention_event = _event_keys()

    planned = plan_semantic_notification(
        event_key=attention_event,
        interruption_class=InterruptionClass.ACTION_REQUIRED,
        prefix="attention",
        request_key=request_key,
        operator_generation=4,
        now=NOW,
        randomness=b"y" * 32,
        existing_bindings={},
        max_bindings=8,
        ttl_seconds=300.0,
    )

    assert planned is not None
    bindings, identifier, title, body, metadata = planned
    assert identifier.startswith("attention.")
    assert title == PRODUCT_DISPLAY_NAME
    assert body == "A Codex session needs you"
    assert set(metadata) == {"action_token"}
    assert metadata["action_token"] in bindings


def test_resolve_notification_work_key_returns_the_underlying_work_key_once() -> None:
    work_key, request_key, _completed_event, attention_event = _event_keys()
    planned = plan_semantic_notification(
        event_key=attention_event,
        interruption_class=InterruptionClass.ACTION_REQUIRED,
        prefix="attention",
        request_key=request_key,
        operator_generation=4,
        now=NOW,
        randomness=b"z" * 32,
        existing_bindings={},
        max_bindings=8,
        ttl_seconds=300.0,
    )
    assert planned is not None
    bindings, _identifier, _title, _body, metadata = planned

    updated, resolved = resolve_notification_work_key(
        presented_token=metadata["action_token"],
        bindings=bindings,
        current_generation=4,
        now=NOW + 1.0,
    )

    assert resolved == work_key
    assert metadata["action_token"] not in updated


def test_should_post_completion_notification_requires_all_gates() -> None:
    assert should_post_completion_notification(
        status_present=True,
        completion_notifications_enabled=True,
        may_interrupt=True,
        snoozed=False,
        event_present=True,
    )
    assert not should_post_completion_notification(
        status_present=True,
        completion_notifications_enabled=True,
        may_interrupt=True,
        snoozed=True,
        event_present=True,
    )
    assert not should_post_completion_notification(
        status_present=False,
        completion_notifications_enabled=True,
        may_interrupt=True,
        snoozed=False,
        event_present=True,
    )


def test_completion_notification_consumes_banner_grant_not_audio_or_webhook() -> None:
    common = {
        "status_present": True,
        "completion_notifications_enabled": True,
        "may_interrupt": True,
        "snoozed": False,
        "event_present": True,
    }

    assert should_post_completion_notification(**common, banner_allowed=True)
    assert not should_post_completion_notification(**common, banner_allowed=False)
