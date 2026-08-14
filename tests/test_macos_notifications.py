from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from sidepulse.macos_notifications import (
    MacOSNotificationClient,
    NotificationAuthorizationState,
)


class FakeCenter:
    def __init__(self, authorization_status: int) -> None:
        self.authorization_status = authorization_status
        self.authorization_requests: list[int] = []
        self.notification_requests: list[object] = []
        self.delivery_error = None
        self.delegate = None
        self.authorization_callbacks_enabled = True
        self.delivery_callbacks_enabled = True
        self.delivery_thread_ids: list[int] = []

    def setDelegate_(self, delegate) -> None:
        self.delegate = delegate

    def getNotificationSettingsWithCompletionHandler_(self, callback) -> None:
        if self.authorization_callbacks_enabled:
            callback(SimpleNamespace(authorizationStatus=lambda: self.authorization_status))

    def requestAuthorizationWithOptions_completionHandler_(
        self,
        options: int,
        callback,
    ) -> None:
        self.authorization_requests.append(options)
        callback(self.authorization_status in {2, 3}, None)

    def addNotificationRequest_withCompletionHandler_(self, request, callback) -> None:
        self.delivery_thread_ids.append(threading.get_ident())
        self.notification_requests.append(request)
        if self.delivery_callbacks_enabled:
            callback(self.delivery_error)


@dataclass
class FakeContent:
    title: str = ""
    body: str = ""
    user_info: dict[str, str] | None = None

    def setTitle_(self, title: str) -> None:
        self.title = title

    def setBody_(self, body: str) -> None:
        self.body = body

    def setUserInfo_(self, user_info: dict[str, str]) -> None:
        self.user_info = user_info


def client(center: FakeCenter) -> MacOSNotificationClient:
    return MacOSNotificationClient(
        center=center,
        content_factory=FakeContent,
        request_factory=lambda identifier, content: SimpleNamespace(
            identifier=identifier,
            content=content,
            trigger=None,
        ),
        wait_timeout=0.1,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, NotificationAuthorizationState.NOT_DETERMINED),
        (1, NotificationAuthorizationState.DENIED),
        (2, NotificationAuthorizationState.AUTHORIZED),
        (3, NotificationAuthorizationState.PROVISIONAL),
        (99, NotificationAuthorizationState.UNAVAILABLE),
    ],
)
def test_authorization_state_maps_only_documented_macos_values(
    raw: int,
    expected: NotificationAuthorizationState,
) -> None:
    assert client(FakeCenter(raw)).authorization_state() is expected


def test_delivery_requires_authorized_or_provisional_state() -> None:
    denied = FakeCenter(1)
    not_determined = FakeCenter(0)
    denied_client = client(denied)
    not_determined_client = client(not_determined)

    assert denied_client.authorization_state() is NotificationAuthorizationState.DENIED
    assert (
        not_determined_client.authorization_state()
        is NotificationAuthorizationState.NOT_DETERMINED
    )

    assert denied_client.deliver("completion.a", "SidePulse", "Finished", {}) is False
    assert (
        not_determined_client.deliver(
            "completion.a",
            "SidePulse",
            "Finished",
            {},
        )
        is False
    )
    assert denied.notification_requests == []
    assert not_determined.notification_requests == []


@pytest.mark.parametrize("raw", [2, 3])
def test_authorized_delivery_uses_stable_identifier_and_opaque_metadata(
    raw: int,
) -> None:
    center = FakeCenter(raw)
    notification_client = client(center)
    assert notification_client.authorization_state() is {
        2: NotificationAuthorizationState.AUTHORIZED,
        3: NotificationAuthorizationState.PROVISIONAL,
    }[raw]

    assert notification_client.deliver(
        "completion.semantic-01",
        "SidePulse",
        "A Codex session finished",
        {"action_token": "A" * 43},
    )

    deadline = time.monotonic() + 1.0
    while not center.notification_requests and time.monotonic() < deadline:
        time.sleep(0.005)
    request = center.notification_requests[0]
    assert request.identifier == "completion.semantic-01"
    assert request.trigger is None
    assert request.content.title == "SidePulse"
    assert request.content.body == "A Codex session finished"
    assert request.content.user_info == {"action_token": "A" * 43}


def test_delivery_uses_cached_authorization_without_blocking_the_caller() -> None:
    center = FakeCenter(2)
    notification_client = MacOSNotificationClient(
        center=center,
        content_factory=FakeContent,
        request_factory=lambda identifier, content: SimpleNamespace(
            identifier=identifier,
            content=content,
            trigger=None,
        ),
        wait_timeout=0.5,
    )
    caller_thread = threading.get_ident()

    assert (
        notification_client.authorization_state()
        is NotificationAuthorizationState.AUTHORIZED
    )
    center.authorization_callbacks_enabled = False
    center.delivery_callbacks_enabled = False

    started_at = time.monotonic()
    accepted = notification_client.deliver(
        "completion.semantic-02",
        "SidePulse",
        "A Codex session finished",
        {"action_token": "B" * 43},
    )
    elapsed = time.monotonic() - started_at

    assert accepted is True
    assert elapsed < 0.1
    deadline = time.monotonic() + 1.0
    while not center.notification_requests and time.monotonic() < deadline:
        time.sleep(0.005)
    assert len(center.notification_requests) == 1
    assert center.delivery_thread_ids != [caller_thread]
    assert notification_client.close(timeout_seconds=1.0) is True


def test_request_authorization_is_explicit_and_alert_only() -> None:
    center = FakeCenter(0)
    notification_client = client(center)

    assert center.authorization_requests == []
    assert notification_client.request_authorization() is True
    assert center.authorization_requests == [1 << 2]


def test_delegate_registration_never_requests_authorization() -> None:
    center = FakeCenter(0)
    notification_client = client(center)
    delegate = object()

    assert notification_client.set_delegate(delegate) is True

    assert center.delegate is delegate
    assert center.authorization_requests == []


def test_explicit_request_reports_the_resolved_authorization_state() -> None:
    center = FakeCenter(2)
    notification_client = client(center)
    resolved: list[NotificationAuthorizationState] = []

    assert notification_client.request_authorization(resolved.append) is True

    assert resolved == [NotificationAuthorizationState.AUTHORIZED]


@pytest.mark.parametrize(
    ("identifier", "title", "body", "metadata"),
    [
        ("completion.a", "SidePulse", "Finished", {"agent_id": "private"}),
        ("completion.a", "Bearer secret", "Finished", {}),
        ("completion.a", "SidePulse", "/Users/private/session", {}),
        ("completion private", "SidePulse", "Finished", {}),
        ("completion.a", "SidePulse", "Finished", {"action_token": "too-short"}),
    ],
)
def test_delivery_rejects_private_or_unbounded_inputs(
    identifier: str,
    title: str,
    body: str,
    metadata: dict[str, str],
) -> None:
    center = FakeCenter(2)

    assert client(center).deliver(identifier, title, body, metadata) is False
    assert center.notification_requests == []


def test_delivery_failure_exposes_only_product_owned_diagnostic() -> None:
    center = FakeCenter(2)
    center.delivery_error = RuntimeError("/Users/private token=secret")
    notification_client = client(center)
    assert (
        notification_client.authorization_state()
        is NotificationAuthorizationState.AUTHORIZED
    )

    assert notification_client.deliver(
        "completion.a",
        "SidePulse",
        "A Codex session finished",
        {},
    )
    deadline = time.monotonic() + 1.0
    while (
        notification_client.last_diagnostic != "Notification delivery failed"
        and time.monotonic() < deadline
    ):
        time.sleep(0.005)
    assert notification_client.last_diagnostic == "Notification delivery failed"
    assert "private" not in repr(notification_client)


def test_missing_runtime_bridge_reports_unavailable_without_prompt() -> None:
    notification_client = MacOSNotificationClient(
        center=None,
        content_factory=None,
        request_factory=None,
        bridge_loader=lambda: None,
        wait_timeout=0.01,
    )

    assert notification_client.authorization_state() is NotificationAuthorizationState.UNAVAILABLE
    assert notification_client.request_authorization() is False
