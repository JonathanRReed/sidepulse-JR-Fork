"""The notification-identity surface: bounded copy and opaque action tokens.

The channel/stage planner and delivery ledger this file used to exercise were
deleted 2026-08-26 (production never constructed a ledger). Routes here are
built exactly the way the live notification path builds them: directly.
"""

from __future__ import annotations

import re

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.interruption_policy import (
    ActionTokenBinding,
    GenericNotificationCopy,
    InterruptionPolicyValidationError,
    InterruptionRoute,
    action_token_metadata,
    generic_notification_copy,
    issue_action_token,
    resolve_action_token,
)
from sidepulse.operator_state import (
    CanonicalOperatorEvent,
    InterruptionClass,
    SemanticEventKey,
    TransitionKind,
    classify_operator_event,
)
from sidepulse.provider_facts import (
    EventToken,
    ProviderWatermark,
    RequestIdentifier,
    RequestKey,
    SourceFreshness,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
)

NOW = 1_786_536_000.0


def _source(
    provider: str = "codex",
    suffix: str = "01",
    *,
    source_instance: str | None = None,
) -> SourceKey:
    return SourceKey(
        provider,
        "hooks",
        source_instance or f"local:{suffix}",
        "live_agent_events",
    )


def _watermark(
    source: SourceKey,
    suffix: str,
    *,
    occurred_at: float = NOW - 10.0,
) -> ProviderWatermark:
    return ProviderWatermark(
        source_key=source,
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        occurred_at_epoch=occurred_at,
        event_token=EventToken(f"event:{suffix}"),
        sequence=None,
        tie_break_rank=10,
    )


def _work_event(
    kind: TransitionKind,
    *,
    interruption_class: InterruptionClass | None = None,
    provider: str = "codex",
    suffix: str = "01",
    freshness: SourceFreshness = SourceFreshness.FRESH,
    work_id: str | None = None,
    source_instance: str | None = None,
) -> CanonicalOperatorEvent:
    source = _source(provider, suffix, source_instance=source_instance)
    subject = WorkKey(source, WorkIdentifier(work_id or f"work:{suffix}"))
    watermark = _watermark(source, suffix)
    key = SemanticEventKey(subject, kind, watermark)
    return CanonicalOperatorEvent(
        key=key,
        subject_key=subject,
        kind=kind,
        interruption_class=(interruption_class if interruption_class is not None else classify_operator_event(kind)),
        occurred_at_epoch=watermark.occurred_at_epoch,
        source_freshness=freshness,
    )


def _request_route(provider: str = "codex", suffix: str = "01") -> InterruptionRoute:
    source = _source(provider, suffix)
    work_key = WorkKey(source, WorkIdentifier(f"work:{suffix}"))
    request_key = RequestKey(work_key, RequestIdentifier(f"request:{suffix}"))
    event_key = SemanticEventKey(
        request_key,
        TransitionKind.REQUEST_OPENED,
        _watermark(source, suffix),
    )
    return InterruptionRoute(event_key, InterruptionClass.ACTION_REQUIRED, request_key)


def _route(event: CanonicalOperatorEvent) -> InterruptionRoute:
    return InterruptionRoute(event.key, event.interruption_class, None)


@pytest.mark.parametrize(
    ("route_factory", "expected_body"),
    (
        (_request_route, "A Codex session needs you"),
        (
            lambda: _route(_work_event(TransitionKind.COMPLETED)),
            "A Codex session finished",
        ),
        (
            lambda: _route(_work_event(TransitionKind.FAILED)),
            "A Codex session finished",
        ),
    ),
)
def test_generic_notification_copy_contains_only_product_owned_provider_semantics(
    route_factory,
    expected_body: str,
) -> None:
    copy = generic_notification_copy(route_factory())

    assert copy == GenericNotificationCopy("JR Bar", expected_body)


def test_notification_copy_never_echoes_opaque_or_private_shaped_source_values() -> None:
    sentinel = "prompt:Users:jonathan:project-secret"
    event = _work_event(
        TransitionKind.COMPLETED,
        provider="unlisted",
        suffix="private",
        work_id=sentinel,
    )

    copy = generic_notification_copy(_route(event))

    assert copy == GenericNotificationCopy("JR Bar", "A Provider session finished")
    rendered = f"{copy.title} {copy.body}"
    assert sentinel not in rendered
    assert "secret" not in rendered.lower()


@pytest.mark.parametrize(
    "sentinel",
    (
        "prompt:delete-files",
        "path:Users:jonathan:Documents:secret",
        "email:jonathan.example.com",
        "credential:sk-secret",
        "session:abc123",
        "url:https:example.com:private",
        "raw-error:permission-denied",
    ),
)
def test_generic_copy_excludes_the_full_grammar_compatible_private_corpus(
    sentinel: str,
) -> None:
    event = _work_event(
        TransitionKind.COMPLETED,
        source_instance=sentinel,
    )

    copy = generic_notification_copy(_route(event))

    assert sentinel not in f"{copy.title} {copy.body}"


def test_route_fails_closed_on_a_request_key_that_is_not_the_events_subject() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    foreign = _request_route(suffix="02").request_key

    with pytest.raises(InterruptionPolicyValidationError):
        InterruptionRoute(event.key, event.interruption_class, foreign)


def test_action_token_payload_is_opaque_bounded_and_contains_no_navigation_identity() -> None:
    event = _work_event(
        TransitionKind.COMPLETED,
        suffix="private",
        work_id="session:private:project-secret",
    )

    binding = issue_action_token(
        randomness=b"r" * 32,
        event_key=event.key,
        operator_generation=42,
        now=NOW,
        ttl_seconds=120.0,
    )

    assert type(binding) is ActionTokenBinding
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", binding.token)
    assert "session" not in binding.token
    assert "secret" not in binding.token
    assert "session" not in binding.event_fingerprint
    assert not hasattr(binding, "event_key")
    assert binding.expires_at_epoch == NOW + 120.0


def test_action_token_notification_metadata_contains_only_the_opaque_token() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    binding = issue_action_token(
        randomness=b"m" * 32,
        event_key=event.key,
        operator_generation=3,
        now=NOW,
    )

    metadata = action_token_metadata(binding)

    assert metadata == {"action_token": binding.token}
    assert not {
        "agent_id",
        "work_key",
        "request_key",
        "session_id",
        "title",
        "path",
        "url",
        "command",
        "event_fingerprint",
        "operator_generation",
        "expires_at_epoch",
    }.intersection(metadata)


def test_action_token_value_is_bound_to_event_generation_and_expiry_metadata() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    other = _work_event(TransitionKind.COMPLETED, suffix="02")

    baseline = issue_action_token(
        randomness=b"z" * 32,
        event_key=event.key,
        operator_generation=1,
        now=NOW,
        ttl_seconds=30.0,
    )
    changed_event = issue_action_token(
        randomness=b"z" * 32,
        event_key=other.key,
        operator_generation=1,
        now=NOW,
        ttl_seconds=30.0,
    )
    changed_generation = issue_action_token(
        randomness=b"z" * 32,
        event_key=event.key,
        operator_generation=2,
        now=NOW,
        ttl_seconds=30.0,
    )
    changed_expiry = issue_action_token(
        randomness=b"z" * 32,
        event_key=event.key,
        operator_generation=1,
        now=NOW,
        ttl_seconds=31.0,
    )

    assert (
        len(
            {
                baseline.token,
                changed_event.token,
                changed_generation.token,
                changed_expiry.token,
            }
        )
        == 4
    )


def test_action_token_reresolves_only_one_exact_current_generation_candidate() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    other = _work_event(TransitionKind.COMPLETED, suffix="02")
    binding = issue_action_token(
        randomness=b"x" * 32,
        event_key=event.key,
        operator_generation=7,
        now=NOW,
        ttl_seconds=30.0,
    )

    assert (
        resolve_action_token(
            binding,
            presented_token=binding.token,
            candidate_event_keys=(other.key, event.key),
            current_generation=7,
            now=NOW + 29.0,
        )
        == event.key
    )
    assert (
        resolve_action_token(
            binding,
            presented_token="x" * 43,
            candidate_event_keys=(event.key,),
            current_generation=7,
            now=NOW + 1.0,
        )
        is None
    )
    assert (
        resolve_action_token(
            binding,
            presented_token=binding.token,
            candidate_event_keys=(event.key,),
            current_generation=8,
            now=NOW + 1.0,
        )
        is None
    )
    assert (
        resolve_action_token(
            binding,
            presented_token=binding.token,
            candidate_event_keys=(other.key,),
            current_generation=7,
            now=NOW + 1.0,
        )
        is None
    )
    assert (
        resolve_action_token(
            binding,
            presented_token=binding.token,
            candidate_event_keys=(event.key,),
            current_generation=7,
            now=NOW + 30.0,
        )
        is None
    )


def test_action_token_resolver_fails_closed_for_duplicate_candidates() -> None:
    event = _work_event(TransitionKind.COMPLETED)
    binding = issue_action_token(
        randomness=b"d" * 32,
        event_key=event.key,
        operator_generation=1,
        now=NOW,
    )

    assert (
        resolve_action_token(
            binding,
            presented_token=binding.token,
            candidate_event_keys=(event.key, event.key),
            current_generation=1,
            now=NOW + 1.0,
        )
        is None
    )
