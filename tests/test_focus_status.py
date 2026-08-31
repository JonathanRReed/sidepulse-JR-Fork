from __future__ import annotations

import builtins
import importlib
import sys
from dataclasses import FrozenInstanceError

import pytest

from sidepulse.focus_status import (
    FocusActivity,
    FocusAuthorization,
    FocusStatusObservation,
    MacOSFocusStatusClient,
    _load_focus_status_center,
)


class _FocusStatus:
    def __init__(self, focused: object) -> None:
        self._focused = focused

    def isFocused(self) -> object:
        return self._focused


class _Center:
    def __init__(
        self,
        *,
        authorization: object = 3,
        focused: object = False,
    ) -> None:
        self.authorization = authorization
        self.focused = focused
        self.focus_reads = 0
        self.request_count = 0

    def authorizationStatus(self) -> object:
        if isinstance(self.authorization, BaseException):
            raise self.authorization
        return self.authorization

    def focusStatus(self) -> _FocusStatus | None:
        self.focus_reads += 1
        if isinstance(self.focused, BaseException):
            raise self.focused
        if self.focused is None:
            return None
        return _FocusStatus(self.focused)

    def requestAuthorizationWithCompletionHandler_(self, completion) -> None:
        self.request_count += 1
        completion(self.authorization)


def test_module_import_does_not_load_objc_or_intents(monkeypatch: pytest.MonkeyPatch) -> None:
    module_name = "sidepulse.focus_status"
    module = sys.modules.pop(module_name)
    imported: list[str] = []
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {"Intents", "objc"}:
            imported.append(name)
            raise AssertionError(f"imported {name} at module import time")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    try:
        importlib.import_module(module_name)
    finally:
        sys.modules[module_name] = module

    assert imported == []


def test_client_loads_the_bridge_only_for_the_first_observation() -> None:
    center = _Center(authorization=3, focused=True)
    loads: list[str] = []

    def load() -> object:
        loads.append("load")
        return center

    client = MacOSFocusStatusClient(bridge_loader=load)

    assert loads == []
    assert client.observe() == FocusStatusObservation(
        FocusAuthorization.AUTHORIZED,
        FocusActivity.ACTIVE,
    )
    assert client.observe().activity is FocusActivity.ACTIVE
    assert loads == ["load"]


def test_unsupported_macos_does_not_load_the_objc_bridge() -> None:
    imports: list[str] = []

    assert (
        _load_focus_status_center(
            system_name="darwin",
            version_text="11.7.10",
            objc_loader=lambda: imports.append("objc"),
        )
        is None
    )
    assert imports == []


def test_non_macos_does_not_load_the_objc_bridge() -> None:
    imports: list[str] = []

    assert (
        _load_focus_status_center(
            system_name="linux",
            version_text="27.0",
            objc_loader=lambda: imports.append("objc"),
        )
        is None
    )
    assert imports == []


def test_supported_macos_loads_the_public_intents_center() -> None:
    center = _Center()

    class CenterType:
        @staticmethod
        def defaultCenter() -> object:
            return center

    class ObjCBridge:
        def __init__(self) -> None:
            self.loaded: list[tuple[str, str]] = []
            self.registered: list[tuple[bytes, bytes, dict[str, object]]] = []
            self._C_NSInteger = b"q"

        def loadBundle(self, name, namespace, *, bundle_path) -> None:
            assert namespace == {}
            self.loaded.append((name, bundle_path))

        def registerMetaDataForSelector(self, class_name, selector, metadata) -> None:
            self.registered.append((class_name, selector, metadata))

        @staticmethod
        def lookUpClass(name: str) -> object:
            assert name == "INFocusStatusCenter"
            return CenterType

    bridge = ObjCBridge()

    assert (
        _load_focus_status_center(
            system_name="darwin",
            version_text="12.0",
            objc_loader=lambda: bridge,
        )
        is center
    )
    assert bridge.loaded == [
        ("Intents", "/System/Library/Frameworks/Intents.framework")
    ]
    assert len(bridge.registered) == 1
    class_name, selector, metadata = bridge.registered[0]
    assert class_name == b"INFocusStatusCenter"
    assert selector == b"requestAuthorizationWithCompletionHandler:"
    assert metadata["arguments"][2]["callable"]["arguments"][1]["type"] == b"q"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, FocusAuthorization.NOT_DETERMINED),
        (1, FocusAuthorization.RESTRICTED),
        (2, FocusAuthorization.DENIED),
        (3, FocusAuthorization.AUTHORIZED),
        (4, FocusAuthorization.UNAVAILABLE),
        (True, FocusAuthorization.UNAVAILABLE),
        ("3", FocusAuthorization.UNAVAILABLE),
    ],
)
def test_observation_maps_authorization_to_closed_typed_values(
    raw: object,
    expected: FocusAuthorization,
) -> None:
    observation = MacOSFocusStatusClient(center=_Center(authorization=raw)).observe()

    assert observation.authorization is expected


@pytest.mark.parametrize(
    ("focused", "expected"),
    [
        (True, FocusActivity.ACTIVE),
        (False, FocusActivity.INACTIVE),
        (None, FocusActivity.UNAVAILABLE),
        (1, FocusActivity.UNAVAILABLE),
        ("yes", FocusActivity.UNAVAILABLE),
    ],
)
def test_authorized_observation_maps_only_real_boolean_focus_status(
    focused: object,
    expected: FocusActivity,
) -> None:
    observation = MacOSFocusStatusClient(
        center=_Center(authorization=3, focused=focused)
    ).observe()

    assert observation == FocusStatusObservation(
        FocusAuthorization.AUTHORIZED,
        expected,
    )


@pytest.mark.parametrize("authorization", [0, 1, 2, 4, True, "3"])
def test_unauthorized_or_unknown_status_never_claims_focus_is_inactive(
    authorization: object,
) -> None:
    center = _Center(authorization=authorization, focused=False)

    observation = MacOSFocusStatusClient(center=center).observe()

    assert observation.activity is FocusActivity.UNAVAILABLE
    assert center.focus_reads == 0


@pytest.mark.parametrize(
    "center",
    [
        _Center(authorization=RuntimeError("authorization failed")),
        _Center(authorization=3, focused=RuntimeError("status failed")),
    ],
)
def test_bridge_failures_return_typed_unavailable_observations(center: _Center) -> None:
    observation = MacOSFocusStatusClient(center=center).observe()

    assert observation.activity is FocusActivity.UNAVAILABLE
    if isinstance(center.authorization, BaseException):
        assert observation.authorization is FocusAuthorization.UNAVAILABLE
    else:
        assert observation.authorization is FocusAuthorization.AUTHORIZED


def test_observation_is_immutable() -> None:
    observation = FocusStatusObservation(
        FocusAuthorization.AUTHORIZED,
        FocusActivity.ACTIVE,
    )

    with pytest.raises(FrozenInstanceError):
        observation.activity = FocusActivity.INACTIVE  # type: ignore[misc]


@pytest.mark.parametrize(
    ("authorization", "activity"),
    [
        ("authorized", FocusActivity.ACTIVE),
        (FocusAuthorization.AUTHORIZED, "active"),
    ],
)
def test_observation_refuses_untyped_values(
    authorization: object,
    activity: object,
) -> None:
    with pytest.raises(TypeError, match="Focus"):
        FocusStatusObservation(authorization, activity)  # type: ignore[arg-type]


def test_observation_never_requests_authorization() -> None:
    center = _Center(authorization=0, focused=False)

    observation = MacOSFocusStatusClient(center=center).observe()

    assert observation.authorization is FocusAuthorization.NOT_DETERMINED
    assert center.request_count == 0


def test_only_explicit_request_method_calls_authorization_selector() -> None:
    center = _Center(authorization=3)
    results: list[FocusAuthorization] = []
    client = MacOSFocusStatusClient(center=center)

    assert client.request_authorization(results.append)

    assert center.request_count == 1
    assert results == [FocusAuthorization.AUTHORIZED]


def test_explicit_request_maps_unknown_callback_values_to_unavailable() -> None:
    center = _Center(authorization="authorized")
    results: list[FocusAuthorization] = []

    assert MacOSFocusStatusClient(center=center).request_authorization(results.append)

    assert results == [FocusAuthorization.UNAVAILABLE]


def test_explicit_request_is_refused_when_the_public_center_is_unavailable() -> None:
    requested: list[FocusAuthorization] = []
    client = MacOSFocusStatusClient(bridge_loader=lambda: None)

    assert not client.request_authorization(requested.append)
    assert requested == []


def test_explicit_request_rejects_a_noncallable_completion() -> None:
    with pytest.raises(TypeError, match="completion"):
        MacOSFocusStatusClient(center=_Center()).request_authorization("later")  # type: ignore[arg-type]


def test_explicit_request_refuses_selector_failures() -> None:
    class BrokenCenter(_Center):
        def requestAuthorizationWithCompletionHandler_(self, completion) -> None:
            raise RuntimeError("request failed")

    results: list[FocusAuthorization] = []

    assert not MacOSFocusStatusClient(center=BrokenCenter()).request_authorization(
        results.append
    )
    assert results == []


def test_completion_exceptions_do_not_escape_the_objc_callback_boundary() -> None:
    def raise_from_completion(_state: FocusAuthorization) -> None:
        raise RuntimeError("consumer failed")

    assert MacOSFocusStatusClient(center=_Center()).request_authorization(
        raise_from_completion
    )
