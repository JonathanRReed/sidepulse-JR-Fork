import io
import json

import pytest

from sidepulse.webhook_delivery import (
    WebhookReason,
    WebhookValidationError,
    _read_response,
    encode_webhook_payload,
    sanitize_webhook_payload,
    validate_webhook_url,
)


def _resolver(*_args, **_kwargs):
    return [
        (2, 1, 6, "", ("93.184.216.34", 443)),
    ]


def test_webhook_requires_https_and_a_public_destination() -> None:
    with pytest.raises(WebhookValidationError) as insecure:
        validate_webhook_url("http://example.com/hook", resolver=_resolver)
    assert insecure.value.reason is WebhookReason.INSECURE_SCHEME

    def private_resolver(*_args, **_kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", 443))]

    with pytest.raises(WebhookValidationError) as private:
        validate_webhook_url(
            "https://example.com/hook",
            resolver=private_resolver,
        )
    assert private.value.reason is WebhookReason.FORBIDDEN_DESTINATION


def test_webhook_refuses_mixed_public_and_private_dns_answers() -> None:
    def mixed_resolver(*_args, **_kwargs):
        return [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.5", 443)),
        ]

    with pytest.raises(WebhookValidationError) as refused:
        validate_webhook_url(
            "https://example.com/hook",
            resolver=mixed_resolver,
        )
    assert refused.value.reason is WebhookReason.FORBIDDEN_DESTINATION


def test_webhook_payload_drops_session_provider_and_user_labels() -> None:
    payload = {
        "event": "sidepulse.escalation",
        "stage": 3,
        "ask_count": 2,
        "oldest_ask_seconds": 305,
        "provider": "codex",
        "label": "private project title",
        "sessions": [{"provider": "claude", "label": "secret"}],
        "url": "https://secret.invalid",
    }

    safe = sanitize_webhook_payload(payload)
    encoded = json.loads(encode_webhook_payload(payload))

    assert safe == {
        "event": "sidepulse.escalation",
        "stage": 3,
        "ask_count": 2,
        "oldest_ask_seconds": 305,
    }
    assert encoded == safe


def test_response_parser_refuses_redirects_and_oversized_bodies() -> None:
    status, size = _read_response(
        io.BytesIO(
            b"HTTP/1.1 302 Found\r\n"
            b"Location: https://example.net/elsewhere\r\n"
            b"Content-Length: 0\r\n\r\n"
        )
    )
    assert status == 302
    assert size == 0

    with pytest.raises(WebhookValidationError) as oversized:
        _read_response(
            io.BytesIO(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Length: 5000\r\n\r\n"
            )
        )
    assert oversized.value.reason is WebhookReason.RESPONSE_TOO_LARGE
