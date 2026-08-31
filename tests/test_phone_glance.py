import base64
import hashlib
import hmac
import json

import pytest

from sidepulse.phone_glance import (
    PhoneGlance,
    PhoneGlanceEnvelope,
    PhoneGlancePolicy,
    PhoneGlanceRefused,
    build_phone_glance,
    encode_phone_glance,
    receive_phone_glance,
)

KEY = b"phone-glance-test-key"


def sign(payload: bytes) -> str:
    return hmac.new(KEY, payload, hashlib.sha256).hexdigest()


def verify(payload: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(payload), signature)


def decode_signed_body(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def glance(**kw):
    values = {
        "message": "private",
        "usage": {"input_tokens": 3, "token": "secret"},
        "capacity": {"remaining_percent": 80, "path": "/private"},
        **kw,
    }
    return PhoneGlance("mac", 1, 1000.0, "working", "pending", **values)

def test_default_is_minimized_and_authenticated():
    e = build_phone_glance(glance(), PhoneGlancePolicy("mac"), signer=sign)
    assert e.payload == {"status": "working", "outcome": "pending"}
    assert b"private" not in encode_phone_glance(e)
    assert receive_phone_glance(e, PhoneGlancePolicy("mac"), verifier=verify, now=1000.0) == e

def test_optional_fields_are_independent():
    e = build_phone_glance(glance(), PhoneGlancePolicy("mac", include_message=True, include_usage=True, include_capacity=True), signer=sign)
    assert set(e.payload) == {"status", "outcome", "message", "usage", "capacity"}
    assert e.payload["usage"] == {"input_tokens": 3}
    assert e.payload["capacity"] == {"remaining_percent": 80}

def test_source_replay_age_future_and_tamper_fail_closed():
    policy = PhoneGlancePolicy("mac")
    e = build_phone_glance(glance(), policy, signer=sign)
    for bad, now, last in (
        (PhoneGlanceEnvelope("other", 1, 1000, e.payload, e.signature), 1000, None),
        (e, 1000, 1),
        (e, 2000, None),
        (e, 900, None),
    ):
        try:
            receive_phone_glance(bad, policy, verifier=verify, now=now, last_sequence=last)
        except PhoneGlanceRefused:
            pass
        else:
            assert False

def test_unconsented_fields_and_invalid_payload_shapes_are_refused():
    broad = build_phone_glance(
        glance(),
        PhoneGlancePolicy("mac", include_message=True, include_usage=True, include_capacity=True),
        signer=sign,
    )
    with pytest.raises(PhoneGlanceRefused):
        PhoneGlanceEnvelope(
            "mac",
            1,
            1000.0,
            {**dict(broad.payload), "usage": {"secret": "x"}},
            broad.signature,
        )
    with pytest.raises(PhoneGlanceRefused):
        PhoneGlanceEnvelope(
            "mac",
            1,
            1000.0,
            {**dict(broad.payload), "extra": "x"},
            broad.signature,
        )

    try:
        receive_phone_glance(broad, PhoneGlancePolicy("mac"), verifier=verify, now=1000.0)
    except PhoneGlanceRefused:
        pass
    else:
        assert False


def test_non_string_optional_map_keys_fail_closed():
    private = PhoneGlance(
        "mac",
        1,
        1000.0,
        "working",
        "pending",
        usage={"input_tokens": 3, 1: "private"},
    )

    envelope = build_phone_glance(
        private,
        PhoneGlancePolicy("mac", include_usage=True),
        signer=sign,
    )

    assert envelope.payload["usage"] == {"input_tokens": 3}


def test_network_encoding_carries_the_exact_bytes_given_to_the_signer():
    signed: list[bytes] = []

    def capture(payload: bytes) -> str:
        signed.append(payload)
        return sign(payload)

    envelope = build_phone_glance(glance(), PhoneGlancePolicy("mac"), signer=capture)
    network = json.loads(encode_phone_glance(envelope))

    assert decode_signed_body(network["signed_body"]) == signed[0]
    assert b" " not in signed[0]
    assert set(network) == {
        "source_id",
        "sequence",
        "observed_at",
        "payload",
        "signed_body",
        "signature",
    }


def test_network_signature_is_exact_lowercase_sha256_hex():
    valid_payload = {"status": "working", "outcome": "pending"}

    for invalid_signature in ("0" * 63, "0" * 65, "A" * 64, "g" * 64):
        with pytest.raises(PhoneGlanceRefused, match="invalid signature"):
            PhoneGlanceEnvelope("mac", 1, 1000.0, valid_payload, invalid_signature)


def test_readable_fields_must_match_the_authenticated_signed_body():
    policy = PhoneGlancePolicy("mac")
    envelope = build_phone_glance(glance(), policy, signer=sign)
    tampered = PhoneGlanceEnvelope(
        envelope.source_id,
        envelope.sequence,
        envelope.observed_at,
        {"status": "waiting", "outcome": "pending"},
        envelope.signature,
        envelope.signed_body,
    )

    with pytest.raises(PhoneGlanceRefused, match="signed body mismatch"):
        receive_phone_glance(tampered, policy, verifier=verify, now=1000.0)


def test_tampered_signed_body_fails_authentication():
    policy = PhoneGlancePolicy("mac")
    envelope = build_phone_glance(glance(), policy, signer=sign)
    assert envelope.signed_body is not None
    replacement = "A" if envelope.signed_body[-1] != "A" else "B"
    tampered = PhoneGlanceEnvelope(
        envelope.source_id,
        envelope.sequence,
        envelope.observed_at,
        envelope.payload,
        envelope.signature,
        envelope.signed_body[:-1] + replacement,
    )

    with pytest.raises(PhoneGlanceRefused):
        receive_phone_glance(tampered, policy, verifier=verify, now=1000.0)


def test_complete_network_response_is_counted_against_the_byte_cap():
    policy = PhoneGlancePolicy(
        "mac",
        include_message=True,
        include_usage=True,
        include_capacity=True,
    )
    envelope = build_phone_glance(
        glance(
            message="m" * 512,
            usage={
                "estimated_cost_usd": "1" * 512,
                "input_tokens": 3,
                "cached_input_tokens": 4,
                "output_tokens": 5,
                "model_count": 6,
            },
            capacity={
                "remaining_percent": 80,
                "reset_at": 1001,
                "window": "w" * 512,
                "label": "l" * 512,
            },
        ),
        policy,
        signer=sign,
    )
    encoded = encode_phone_glance(envelope)

    assert len(encoded) <= 8 * 1024
    with pytest.raises(PhoneGlanceRefused, match="payload too large"):
        encode_phone_glance(envelope, max_bytes=len(encoded) - 1)


def test_in_memory_envelope_without_signed_body_keeps_legacy_verification():
    policy = PhoneGlancePolicy("mac")
    built = build_phone_glance(glance(), policy, signer=sign)
    legacy = PhoneGlanceEnvelope(
        built.source_id,
        built.sequence,
        built.observed_at,
        built.payload,
        built.signature,
    )

    assert legacy.signed_body is None
    assert receive_phone_glance(legacy, policy, verifier=verify, now=1000.0) == legacy


def test_encoding_legacy_envelope_synthesizes_the_canonical_signed_body():
    policy = PhoneGlancePolicy("mac")
    built = build_phone_glance(glance(), policy, signer=sign)
    legacy = PhoneGlanceEnvelope(
        built.source_id,
        built.sequence,
        built.observed_at,
        built.payload,
        built.signature,
    )

    network = json.loads(encode_phone_glance(legacy))
    signed_bytes = decode_signed_body(network["signed_body"])

    assert json.loads(signed_bytes) == {
        "source_id": "mac",
        "sequence": 1,
        "observed_at": 1000.0,
        "payload": {"status": "working", "outcome": "pending"},
    }
    assert verify(signed_bytes, network["signature"])


@pytest.mark.parametrize("signed_body", ("Zg==", "Zh"))
def test_padded_and_noncanonical_base64url_signed_bodies_are_refused(
    signed_body: str,
):
    with pytest.raises(PhoneGlanceRefused, match="invalid signed body"):
        PhoneGlanceEnvelope(
            "mac",
            1,
            1000.0,
            {"status": "working", "outcome": "pending"},
            "0" * 64,
            signed_body,
        )
