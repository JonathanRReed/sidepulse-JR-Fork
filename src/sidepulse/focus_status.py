"""Lazy, read-only-by-default access to public macOS Focus status.

Observation loads Apple's public Intents framework only on macOS 12 or later.
It never requests authorization. The system prompt is reachable only through
the explicit ``request_authorization`` method used by the Settings action.
"""

from __future__ import annotations

import platform
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Final

_INTENTS_FRAMEWORK: Final = "/System/Library/Frameworks/Intents.framework"
_UNSET_CENTER: Final = object()


class FocusAuthorization(str, Enum):
    NOT_DETERMINED = "not_determined"
    RESTRICTED = "restricted"
    DENIED = "denied"
    AUTHORIZED = "authorized"
    UNAVAILABLE = "unavailable"


class FocusActivity(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class FocusStatusObservation:
    authorization: FocusAuthorization
    activity: FocusActivity

    def __post_init__(self) -> None:
        if type(self.authorization) is not FocusAuthorization:
            raise TypeError("Focus authorization must be typed")
        if type(self.activity) is not FocusActivity:
            raise TypeError("Focus activity must be typed")


def _macos_major_version(version_text: object) -> int | None:
    if type(version_text) is not str:
        return None
    head = version_text.partition(".")[0]
    if not head.isascii() or not head.isdigit():
        return None
    return int(head)


def _authorization_block_metadata(integer_type: bytes) -> dict[str, object]:
    return {
        "arguments": {
            2: {
                "callable": {
                    "retval": {"type": b"v"},
                    "arguments": {
                        0: {"type": b"^v"},
                        1: {"type": integer_type},
                    },
                }
            }
        }
    }


def _load_focus_status_center(
    *,
    system_name: str | None = None,
    version_text: str | None = None,
    objc_loader: Callable[[], object] | None = None,
) -> object | None:
    """Load the public center without importing Intents on unsupported hosts."""
    effective_system = sys.platform if system_name is None else system_name
    effective_version = platform.mac_ver()[0] if version_text is None else version_text
    major = _macos_major_version(effective_version)
    if effective_system != "darwin" or major is None or major < 12:
        return None
    try:
        if objc_loader is None:
            import objc

            bridge = objc
        else:
            bridge = objc_loader()
        bridge.loadBundle(
            "Intents",
            {},
            bundle_path=_INTENTS_FRAMEWORK,
        )
        bridge.registerMetaDataForSelector(
            b"INFocusStatusCenter",
            b"requestAuthorizationWithCompletionHandler:",
            _authorization_block_metadata(bridge._C_NSInteger),
        )
        center_type = bridge.lookUpClass("INFocusStatusCenter")
        return center_type.defaultCenter()
    except Exception:
        return None


def _authorization(raw: object) -> FocusAuthorization:
    if type(raw) is not int:
        return FocusAuthorization.UNAVAILABLE
    return {
        0: FocusAuthorization.NOT_DETERMINED,
        1: FocusAuthorization.RESTRICTED,
        2: FocusAuthorization.DENIED,
        3: FocusAuthorization.AUTHORIZED,
    }.get(raw, FocusAuthorization.UNAVAILABLE)


class MacOSFocusStatusClient:
    """Polling adapter over one process-owned ``INFocusStatusCenter``."""

    def __init__(
        self,
        *,
        center: object = _UNSET_CENTER,
        bridge_loader: Callable[[], object | None] = _load_focus_status_center,
    ) -> None:
        if not callable(bridge_loader):
            raise TypeError("Focus bridge loader must be callable")
        self._bridge_loader = bridge_loader
        self._center = None if center is _UNSET_CENTER else center
        self._loaded = center is not _UNSET_CENTER
        self._load_lock = threading.Lock()

    def _center_for_use(self) -> object | None:
        with self._load_lock:
            if not self._loaded:
                try:
                    self._center = self._bridge_loader()
                except Exception:
                    self._center = None
                self._loaded = True
            return self._center

    def observe(self) -> FocusStatusObservation:
        """Read authorization and coarse status without requesting permission."""
        center = self._center_for_use()
        if center is None:
            return FocusStatusObservation(
                FocusAuthorization.UNAVAILABLE,
                FocusActivity.UNAVAILABLE,
            )
        try:
            authorization = _authorization(center.authorizationStatus())
        except Exception:
            return FocusStatusObservation(
                FocusAuthorization.UNAVAILABLE,
                FocusActivity.UNAVAILABLE,
            )
        if authorization is not FocusAuthorization.AUTHORIZED:
            return FocusStatusObservation(
                authorization,
                FocusActivity.UNAVAILABLE,
            )
        try:
            status = center.focusStatus()
            focused = None if status is None else status.isFocused()
        except Exception:
            focused = None
        if type(focused) is bool:
            activity = (
                FocusActivity.ACTIVE if focused else FocusActivity.INACTIVE
            )
        else:
            activity = FocusActivity.UNAVAILABLE
        return FocusStatusObservation(authorization, activity)

    def request_authorization(
        self,
        completion: Callable[[FocusAuthorization], None],
    ) -> bool:
        """Invoke the system prompt only for an explicit Settings action."""
        if not callable(completion):
            raise TypeError("Focus authorization completion must be callable")
        center = self._center_for_use()
        if center is None:
            return False

        def completed(raw: object) -> None:
            try:
                completion(_authorization(raw))
            except Exception:
                pass

        try:
            center.requestAuthorizationWithCompletionHandler_(completed)
        except Exception:
            return False
        return True


__all__ = [
    "FocusActivity",
    "FocusAuthorization",
    "FocusStatusObservation",
    "MacOSFocusStatusClient",
]
