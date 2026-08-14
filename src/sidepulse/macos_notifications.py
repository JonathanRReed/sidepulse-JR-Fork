"""Modern, explicit macOS notification authorization and delivery.

The runtime loads the public UserNotifications framework through PyObjC's
dynamic bridge. It does not observe other applications, prompt at import or
construction time, or retain source content in diagnostics.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Final

_USER_NOTIFICATIONS_FRAMEWORK: Final = "/System/Library/Frameworks/UserNotifications.framework"
_AUTHORIZATION_OPTION_ALERT: Final = 1 << 2
_REQUEST_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_ACTION_TOKEN: Final = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_PRIVATE_COPY: Final = re.compile(
    r"(?:[/\\]|://|@|\b(?:api[ _-]?key|authorization|bearer|cookie|"
    r"credential|exception|password|private[ _-]?key|raw[ _-]?error|"
    r"secret|session[ _-]?id|token|traceback)\b)",
    re.IGNORECASE,
)
_GENERIC_BODY: Final = re.compile(
    r"(?:A [A-Za-z][A-Za-z0-9 ]{0,31} session (?:needs you|finished)|"
    r"SidePulse has [1-9][0-9]{0,2} updates?)\Z"
)
_MAX_PENDING_DELIVERIES: Final = 256


class NotificationAuthorizationState(str, Enum):
    NOT_DETERMINED = "not_determined"
    DENIED = "denied"
    AUTHORIZED = "authorized"
    PROVISIONAL = "provisional"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class _NotificationBridge:
    center: object
    content_factory: Callable[[], object]
    request_factory: Callable[[str, object], object]


@dataclass(frozen=True, slots=True)
class _NotificationDelivery:
    identifier: str
    title: str
    body: str
    user_info: dict[str, str]


def _block_metadata(*argument_types: bytes) -> dict[str, object]:
    arguments = {0: {"type": b"^v"}}
    arguments.update({index: {"type": argument_type} for index, argument_type in enumerate(argument_types, start=1)})
    return {
        "callable": {
            "retval": {"type": b"v"},
            "arguments": arguments,
        }
    }


def _load_user_notifications_bridge() -> _NotificationBridge | None:
    try:
        import objc

        objc.loadBundle(
            "UserNotifications",
            {},
            bundle_path=_USER_NOTIFICATIONS_FRAMEWORK,
        )
        objc.registerMetaDataForSelector(
            b"UNUserNotificationCenter",
            b"getNotificationSettingsWithCompletionHandler:",
            {"arguments": {2: _block_metadata(b"@")}},
        )
        objc.registerMetaDataForSelector(
            b"UNUserNotificationCenter",
            b"requestAuthorizationWithOptions:completionHandler:",
            {"arguments": {3: _block_metadata(objc._C_NSBOOL, b"@")}},
        )
        objc.registerMetaDataForSelector(
            b"UNUserNotificationCenter",
            b"addNotificationRequest:withCompletionHandler:",
            {"arguments": {3: _block_metadata(b"@")}},
        )
        center_type = objc.lookUpClass("UNUserNotificationCenter")
        content_type = objc.lookUpClass("UNMutableNotificationContent")
        request_type = objc.lookUpClass("UNNotificationRequest")
        return _NotificationBridge(
            center=center_type.currentNotificationCenter(),
            content_factory=lambda: content_type.alloc().init(),
            request_factory=lambda identifier, content: request_type.requestWithIdentifier_content_trigger_(
                identifier,
                content,
                None,
            ),
        )
    except Exception:
        return None


def _authorization_state(raw: object) -> NotificationAuthorizationState:
    return {
        0: NotificationAuthorizationState.NOT_DETERMINED,
        1: NotificationAuthorizationState.DENIED,
        2: NotificationAuthorizationState.AUTHORIZED,
        3: NotificationAuthorizationState.PROVISIONAL,
    }.get(raw, NotificationAuthorizationState.UNAVAILABLE)


def _valid_delivery(
    identifier: object,
    title: object,
    body: object,
    user_info: object,
) -> bool:
    if not (
        type(identifier) is str
        and _REQUEST_IDENTIFIER.fullmatch(identifier) is not None
        and title == "SidePulse"
        and type(body) is str
        and 1 <= len(body) <= 96
        and body.isprintable()
        and _GENERIC_BODY.fullmatch(body) is not None
        and _PRIVATE_COPY.search(body) is None
        and type(user_info) is dict
    ):
        return False
    if not user_info:
        return True
    return (
        set(user_info) == {"action_token"}
        and type(user_info["action_token"]) is str
        and _ACTION_TOKEN.fullmatch(user_info["action_token"]) is not None
    )


class MacOSNotificationClient:
    """Bounded adapter over one process-owned notification center."""

    def __init__(
        self,
        *,
        center: object | None = None,
        content_factory: Callable[[], object] | None = None,
        request_factory: Callable[[str, object], object] | None = None,
        bridge_loader: Callable[[], _NotificationBridge | None] = (_load_user_notifications_bridge),
        wait_timeout: float = 1.0,
    ) -> None:
        if type(wait_timeout) not in {int, float} or not 0.0 < float(wait_timeout) <= 5.0:
            raise ValueError("invalid notification wait timeout")
        if center is None or content_factory is None or request_factory is None:
            bridge = bridge_loader()
            if bridge is None:
                center = None
                content_factory = None
                request_factory = None
            else:
                center = bridge.center
                content_factory = bridge.content_factory
                request_factory = bridge.request_factory
        self._center = center
        self._content_factory = content_factory
        self._request_factory = request_factory
        self._wait_timeout = float(wait_timeout)
        self._authorization_lock = threading.Lock()
        self._cached_authorization_state = NotificationAuthorizationState.UNAVAILABLE
        self._delivery_condition = threading.Condition()
        self._pending_deliveries: dict[str, _NotificationDelivery] = {}
        self._delivery_thread: threading.Thread | None = None
        self._closed = False
        self.last_diagnostic = "Notification authorization unavailable" if center is None else "Ready"

    @property
    def available(self) -> bool:
        return self._center is not None and callable(self._content_factory) and callable(self._request_factory)

    def authorization_state(self) -> NotificationAuthorizationState:
        if not self.available:
            self.last_diagnostic = "Notification authorization unavailable"
            return self._record_authorization_state(
                NotificationAuthorizationState.UNAVAILABLE
            )
        completed = threading.Event()
        result: list[NotificationAuthorizationState] = []

        def resolved(settings) -> None:
            try:
                result.append(_authorization_state(int(settings.authorizationStatus())))
            except Exception:
                result.append(NotificationAuthorizationState.UNAVAILABLE)
            completed.set()

        try:
            self._center.getNotificationSettingsWithCompletionHandler_(resolved)
        except Exception:
            self.last_diagnostic = "Notification authorization unavailable"
            return self._record_authorization_state(
                NotificationAuthorizationState.UNAVAILABLE
            )
        if not completed.wait(self._wait_timeout) or not result:
            self.last_diagnostic = "Notification authorization unavailable"
            return self._record_authorization_state(
                NotificationAuthorizationState.UNAVAILABLE
            )
        state = result[0]
        self.last_diagnostic = {
            NotificationAuthorizationState.NOT_DETERMINED: "Notification permission not requested",
            NotificationAuthorizationState.DENIED: "Notification permission denied",
            NotificationAuthorizationState.AUTHORIZED: "Notification permission authorized",
            NotificationAuthorizationState.PROVISIONAL: "Notification permission provisional",
            NotificationAuthorizationState.UNAVAILABLE: "Notification authorization unavailable",
        }[state]
        return self._record_authorization_state(state)

    def _record_authorization_state(
        self,
        state: NotificationAuthorizationState,
    ) -> NotificationAuthorizationState:
        with self._authorization_lock:
            self._cached_authorization_state = state
        return state

    def _cached_state(self) -> NotificationAuthorizationState:
        with self._authorization_lock:
            return self._cached_authorization_state

    def set_delegate(self, delegate: object | None) -> bool:
        """Install or clear the process-owned modern notification delegate."""
        if not self.available:
            self.last_diagnostic = "Notification authorization unavailable"
            return False
        try:
            self._center.setDelegate_(delegate)
        except Exception:
            self.last_diagnostic = "Notification delegate unavailable"
            return False
        return True

    def request_authorization(
        self,
        completion: Callable[[NotificationAuthorizationState], None] | None = None,
    ) -> bool:
        """Start the system prompt only when an explicit caller invokes this."""
        if completion is not None and not callable(completion):
            self.last_diagnostic = "Notification permission request failed"
            return False
        if not self.available:
            self.last_diagnostic = "Notification authorization unavailable"
            return False

        def completed(granted, error) -> None:
            if error is not None:
                self.last_diagnostic = "Notification permission request failed"
                state = NotificationAuthorizationState.UNAVAILABLE
            elif bool(granted):
                self.last_diagnostic = "Notification permission authorized"
                state = NotificationAuthorizationState.AUTHORIZED
            else:
                self.last_diagnostic = "Notification permission denied"
                state = NotificationAuthorizationState.DENIED
            self._record_authorization_state(state)
            if completion is not None:
                try:
                    completion(state)
                except Exception:
                    pass

        try:
            self._center.requestAuthorizationWithOptions_completionHandler_(
                _AUTHORIZATION_OPTION_ALERT,
                completed,
            )
        except Exception:
            self.last_diagnostic = "Notification permission request failed"
            return False
        return True

    def deliver(
        self,
        identifier: str,
        title: str,
        body: str,
        user_info: dict[str, str],
    ) -> bool:
        if not _valid_delivery(identifier, title, body, user_info):
            self.last_diagnostic = "Notification delivery refused"
            return False
        if self._cached_state() not in {
            NotificationAuthorizationState.AUTHORIZED,
            NotificationAuthorizationState.PROVISIONAL,
        }:
            return False
        delivery = _NotificationDelivery(
            identifier,
            title,
            body,
            dict(user_info),
        )
        with self._delivery_condition:
            if self._closed:
                self.last_diagnostic = "Notification delivery refused"
                return False
            if (
                identifier not in self._pending_deliveries
                and len(self._pending_deliveries) >= _MAX_PENDING_DELIVERIES
            ):
                self.last_diagnostic = "Notification delivery refused"
                return False
            self._pending_deliveries[identifier] = delivery
            self.last_diagnostic = "Notification queued"
            if self._delivery_thread is None:
                worker = threading.Thread(
                    target=self._run_delivery_worker,
                    name="sidepulse-notification-delivery",
                    daemon=True,
                )
                self._delivery_thread = worker
                try:
                    worker.start()
                except Exception:
                    self._delivery_thread = None
                    self._pending_deliveries.pop(identifier, None)
                    self.last_diagnostic = "Notification delivery failed"
                    return False
            self._delivery_condition.notify_all()
        return True

    def _run_delivery_worker(self) -> None:
        while True:
            with self._delivery_condition:
                while not self._pending_deliveries and not self._closed:
                    self._delivery_condition.wait()
                if self._closed:
                    return
                identifier = next(iter(self._pending_deliveries))
                delivery = self._pending_deliveries.pop(identifier)
            self._deliver_one(delivery)

    def _deliver_one(self, delivery: _NotificationDelivery) -> None:
        assert self._content_factory is not None
        assert self._request_factory is not None

        def delivered(error) -> None:
            self.last_diagnostic = (
                "Notification delivered"
                if error is None
                else "Notification delivery failed"
            )

        try:
            content = self._content_factory()
            content.setTitle_(delivery.title)
            content.setBody_(delivery.body)
            if delivery.user_info:
                content.setUserInfo_(delivery.user_info)
            request = self._request_factory(delivery.identifier, content)
            self._center.addNotificationRequest_withCompletionHandler_(
                request,
                delivered,
            )
        except Exception:
            self.last_diagnostic = "Notification delivery failed"

    def close(self, *, timeout_seconds: float) -> bool:
        if (
            type(timeout_seconds) not in {int, float}
            or not 0.0 <= float(timeout_seconds) <= 5.0
        ):
            raise ValueError("invalid notification close timeout")
        with self._delivery_condition:
            self._closed = True
            self._pending_deliveries.clear()
            self._delivery_condition.notify_all()
            worker = self._delivery_thread
        if worker is None:
            return True
        if worker.ident == threading.get_ident():
            return False
        worker.join(float(timeout_seconds))
        return not worker.is_alive()


__all__ = [
    "MacOSNotificationClient",
    "NotificationAuthorizationState",
]
