from __future__ import annotations

import threading

from sidepulse.webhook_delivery import (
    WebhookDeliveryService,
    WebhookEndpoint,
    WebhookReason,
    WebhookResult,
)


def _endpoint(url: str) -> WebhookEndpoint:
    return WebhookEndpoint(
        url=url,
        host="example.com",
        port=443,
        request_target="/hook",
        addresses=("93.184.216.34",),
    )


def test_queue_depth_is_bounded_while_one_delivery_is_in_flight() -> None:
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    delivered = []

    def deliver(_target, payload):
        started.set()
        release.wait(1.0)
        delivered.append(payload["event"])
        return WebhookResult(WebhookReason.DELIVERED, 204)

    service = WebhookDeliveryService(
        maximum_queue_depth=1,
        validator=_endpoint,
        deliver=deliver,
    )
    assert service.submit("https://example.com/hook", {"event": "first"}) is None
    assert started.wait(1.0)
    assert (
        service.submit(
            "https://example.com/hook",
            {"event": "second"},
            callback=lambda _receipt: completed.set(),
        )
        is None
    )
    assert service.submit(
        "https://example.com/hook",
        {"event": "third"},
    ) is WebhookReason.QUEUE_FULL

    release.set()
    assert completed.wait(1.0)
    assert delivered == ["first", "second"]
    service.close()


def test_close_discards_queued_work_but_allows_inflight_cleanup() -> None:
    started = threading.Event()
    release = threading.Event()
    first_done = threading.Event()
    second_done = threading.Event()

    def deliver(_target, _payload):
        started.set()
        release.wait(1.0)
        return WebhookResult(WebhookReason.DELIVERED, 204)

    service = WebhookDeliveryService(
        maximum_queue_depth=2,
        validator=_endpoint,
        deliver=deliver,
    )
    service.submit(
        "https://example.com/hook",
        {"event": "first"},
        callback=lambda _receipt: first_done.set(),
    )
    assert started.wait(1.0)
    service.submit(
        "https://example.com/hook",
        {"event": "second"},
        callback=lambda _receipt: second_done.set(),
    )

    service.close()
    release.set()

    assert first_done.wait(1.0)
    assert not second_done.wait(0.2)
