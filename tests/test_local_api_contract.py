import json
import math

import pytest

from sidepulse.local_api_contract import (
    LocalAPIRequest,
    ReplayGuard,
    decode_request,
    redacted_response,
    validate_authenticated_request,
)


def request(**changes):
    values = dict(client_id="streamdeck", capability="status.read", nonce="n-1", issued_at=100.0, expires_at=120.0)
    values.update(changes)
    return LocalAPIRequest(**values)


def test_redacted_read_request_round_trips_exact_schema():
    decoded = decode_request(request().encode())
    assert decoded.client_id == "streamdeck"
    assert json.loads(redacted_response("status.read", {"state": "idle"}, generated_at=101).encode())["privacy"] == "redacted"

    signed = request().sign(b"secret")
    decoded_signed = decode_request(signed.encode())
    assert decoded_signed.auth == signed.auth
    assert decoded_signed.payload == {}


def test_unknown_fields_and_oversized_payload_rejected():
    document = json.loads(request().encode())
    document["extra"] = True
    with pytest.raises(ValueError):
        decode_request(json.dumps(document).encode())
    with pytest.raises(ValueError):
        request(payload={str(i): i for i in range(17)}).encode()
    with pytest.raises(ValueError, match="payload"):
        request(payload={"path": "/private"}).encode()
    with pytest.raises(ValueError, match="timestamps"):
        request(issued_at=math.nan).encode()
    with pytest.raises(ValueError, match="authentication"):
        decode_request(request(auth="not-a-valid-tag").encode())


def test_authenticated_request_requires_valid_signature_and_replay_guard():
    signed = request().sign(b"secret")
    guard = ReplayGuard()
    validate_authenticated_request(signed, b"secret", now=110, replay_guard=guard)
    with pytest.raises(ValueError, match="replayed"):
        validate_authenticated_request(signed, b"secret", now=110, replay_guard=guard)
    with pytest.raises(ValueError, match="authentication"):
        validate_authenticated_request(request().sign(b"wrong"), b"secret", now=110)


def test_expired_and_non_read_capabilities_rejected():
    with pytest.raises(ValueError, match="expired"):
        validate_authenticated_request(request(expires_at=105).sign(b"secret"), b"secret", now=105)
    with pytest.raises(ValueError, match="unsupported"):
        redacted_response("agents.clear", {})
