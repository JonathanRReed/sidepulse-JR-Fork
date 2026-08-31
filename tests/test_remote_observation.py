"""Contract tests for the bounded, authenticated remote observation seam."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from sidepulse.remote_observation import (
    DEFAULT_CONSENT,
    RemoteObservation,
    RemoteObservationPolicy,
    RemoteObservationReceiver,
    RemoteObservationRefusalCode,
    RemoteObservationScope,
    build_observation_envelope,
    collect_remote_observations,
    encode_envelope,
    select_event_stream,
)

SECRET = b"remote-observation-test-secret-32-bytes"


def signer(payload: bytes) -> str:
    return hmac.new(SECRET, payload, hashlib.sha256).hexdigest()


def verifier(payload: bytes, signature: str) -> bool:
    return hmac.compare_digest(signer(payload), signature)


def observation(
    *,
    source_id: str = "mac-mini",
    sequence: int = 1,
    observed_at: float = 1_000.0,
    message: str | None = "do not send this",
    usage: dict[str, object] | None = None,
    capacity: dict[str, object] | None = None,
) -> RemoteObservation:
    return RemoteObservation(
        source_id=source_id,
        stream_id="agent-events",
        sequence=sequence,
        observed_at=observed_at,
        status="working",
        outcome="pending",
        message=message,
        usage=usage or {"input_tokens": 12},
        capacity=capacity or {"remaining_percent": 75},
    )


def policy(*scopes: RemoteObservationScope) -> RemoteObservationPolicy:
    return RemoteObservationPolicy(
        source_id="mac-mini",
        stream_id="agent-events",
        consents=frozenset({RemoteObservationScope.STATUS_OUTCOME, *scopes}),
    )


def test_default_envelope_is_status_outcome_only_and_minimized() -> None:
    envelope = build_observation_envelope(
        observation(), policy(), signer=signer
    )

    assert envelope.payload == {"status": "working", "outcome": "pending"}
    encoded = encode_envelope(envelope)
    assert b"do not send this" not in encoded
    assert b"input_tokens" not in encoded
    assert b"remaining_percent" not in encoded


def test_message_usage_and_capacity_are_independent_explicit_consents() -> None:
    message_only = build_observation_envelope(
        observation(), policy(RemoteObservationScope.MESSAGE_TEXT), signer=signer
    )
    usage_only = build_observation_envelope(
        observation(), policy(RemoteObservationScope.USAGE), signer=signer
    )
    capacity_only = build_observation_envelope(
        observation(), policy(RemoteObservationScope.CAPACITY), signer=signer
    )

    assert set(message_only.payload) == {"status", "outcome", "message"}
    assert set(usage_only.payload) == {"status", "outcome", "usage"}
    assert set(capacity_only.payload) == {"status", "outcome", "capacity"}


def test_receiver_requires_valid_authentication_and_exact_source_identity() -> None:
    receiver = RemoteObservationReceiver(policy(), verifier=verifier)
    envelope = build_observation_envelope(observation(), policy(), signer=signer)
    assert receiver.accept(envelope, now=1_000.0).accepted is True

    foreign = build_observation_envelope(
        observation(source_id="macbook"),
        RemoteObservationPolicy(source_id="macbook", stream_id="agent-events"),
        signer=signer,
    )
    refusal = receiver.accept(foreign, now=1_001.0).refusal
    assert refusal is not None
    assert refusal.code is RemoteObservationRefusalCode.SOURCE_IDENTITY_MISMATCH

    unsigned = envelope.__class__(
        envelope.schema_version,
        envelope.source_id,
        envelope.stream_id,
        envelope.sequence,
        envelope.observed_at,
        envelope.payload,
        "",
    )
    refusal = RemoteObservationReceiver(policy(), verifier=verifier).accept(
        unsigned, now=1_000.0
    ).refusal
    assert refusal is not None
    assert refusal.code is RemoteObservationRefusalCode.AUTHENTICATION_REQUIRED


def test_replay_and_sequence_gaps_are_typed_refusals() -> None:
    receiver = RemoteObservationReceiver(policy(), verifier=verifier)
    first = build_observation_envelope(observation(sequence=10), policy(), signer=signer)
    assert receiver.accept(first, now=1_000.0).accepted
    replay = receiver.accept(first, now=1_000.0).refusal
    assert replay is not None
    assert replay.code is RemoteObservationRefusalCode.REPLAY

    gap = build_observation_envelope(observation(sequence=12), policy(), signer=signer)
    refusal = receiver.accept(gap, now=1_001.0).refusal
    assert refusal is not None
    assert refusal.code is RemoteObservationRefusalCode.SEQUENCE_GAP


def test_receiver_enforces_age_future_size_and_count_bounds() -> None:
    bounded = RemoteObservationPolicy(
        source_id="mac-mini",
        stream_id="agent-events",
        max_event_bytes=260,
        max_events=1,
        max_event_age_seconds=10.0,
        max_future_skew_seconds=2.0,
    )
    receiver = RemoteObservationReceiver(bounded, verifier=verifier)
    old = build_observation_envelope(observation(observed_at=989.0), bounded, signer=signer)
    refusal = receiver.accept(old, now=1_000.0).refusal
    assert refusal is not None
    assert refusal.code is RemoteObservationRefusalCode.TOO_OLD

    future = build_observation_envelope(observation(observed_at=1_003.0), bounded, signer=signer)
    refusal = receiver.accept(future, now=1_000.0).refusal
    assert refusal is not None
    assert refusal.code is RemoteObservationRefusalCode.FROM_FUTURE

    large = build_observation_envelope(
        observation(message="x" * 500),
        policy(RemoteObservationScope.MESSAGE_TEXT),
        signer=signer,
    )
    refusal = receiver.accept(large, now=1_000.0).refusal
    assert refusal is not None
    assert refusal.code is RemoteObservationRefusalCode.TOO_LARGE

    accepted = build_observation_envelope(observation(), bounded, signer=signer)
    assert receiver.accept(accepted, now=1_000.0).accepted
    second = build_observation_envelope(observation(sequence=2), bounded, signer=signer)
    refusal = receiver.accept(second, now=1_001.0).refusal
    assert refusal is not None
    assert refusal.code is RemoteObservationRefusalCode.TOO_MANY


def test_content_is_redacted_and_minimized_even_with_broad_raw_mappings() -> None:
    raw = observation(
        usage={"input_tokens": 3, "prompt": "secret", "token": "secret"},
        capacity={"remaining_percent": 40, "command": "rm -rf /", "path": "/secret"},
    )
    envelope = build_observation_envelope(
        raw,
        policy(RemoteObservationScope.USAGE, RemoteObservationScope.CAPACITY),
        signer=signer,
    )

    assert envelope.payload["usage"] == {"input_tokens": 3}
    assert envelope.payload["capacity"] == {"remaining_percent": 40}
    assert "secret" not in json.dumps(envelope.payload)
    assert "command" not in json.dumps(envelope.payload)


def test_event_stream_is_selected_and_remote_commands_are_refused() -> None:
    class EventStream:
        authenticated_event_stream = True

        def __init__(self, events):
            self.events = events

        def stream_events(self, source_id, *, max_events, deadline):
            assert source_id == "mac-mini"
            assert max_events == policy().max_events
            assert deadline > 0
            return self.events

    event_stream = EventStream(())
    assert select_event_stream(event_stream, command_transport=object()) is event_stream

    envelope = build_observation_envelope(observation(), policy(), signer=signer)
    result = collect_remote_observations(
        event_stream=EventStream((envelope,)),
        receiver=RemoteObservationReceiver(policy(), verifier=verifier),
        now=1_000.0,
        monotonic=lambda: 10.0,
    )
    assert result.accepted == (envelope,)

    refused = collect_remote_observations(
        event_stream=None,
        command_transport=object(),
        receiver=RemoteObservationReceiver(policy(), verifier=verifier),
        now=1_000.0,
        monotonic=lambda: 10.0,
    )
    assert refused.refusals[0].code is RemoteObservationRefusalCode.REMOTE_COMMAND_FORBIDDEN


def test_default_consent_always_contains_status_and_outcome() -> None:
    assert DEFAULT_CONSENT == frozenset({RemoteObservationScope.STATUS_OUTCOME})
    with pytest.raises(ValueError):
        RemoteObservationPolicy(
            source_id="mac-mini",
            stream_id="agent-events",
            consents=frozenset(),
        )


def test_remote_peer_facade_exposes_only_authenticated_event_streaming() -> None:
    from sidepulse.remote_peers import collect_authenticated_remote_observations

    class EventStream:
        authenticated_event_stream = True

        def stream_events(self, source_id, *, max_events, deadline):
            assert source_id == "mac-mini"
            assert max_events > 0
            assert deadline > 0
            return ()

    result = collect_authenticated_remote_observations(
        event_stream=EventStream(),
        receiver=RemoteObservationReceiver(policy(), verifier=verifier),
        now=1_000.0,
        now_monotonic=lambda: 10.0,
    )
    assert result.accepted == ()
    assert result.refusals == ()
