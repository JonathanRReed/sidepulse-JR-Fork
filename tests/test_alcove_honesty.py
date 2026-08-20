"""Alcove following must say what it is doing, or why it is not.

The owner reported "Alcove mode doesn't seem to be working" and NOTHING
could answer it. Screen Recording never granted, Alcove not running, the
window moving mid-capture and a genuinely empty capture were the SAME
value -- a bare ``None`` from capture_alcove_observation, swallowed by a
blanket ``except Exception``. There were zero lines matching "alcove" in
the app's own log, no diagnostic code, and a settings switch that read ON
in every one of those failures.

These tests pin the four outcomes apart, pin the preflight that
distinguishes the permission case, pin that the reason reaches the log
once per transition, pin the doctor code, and pin what the Screen Bar
pane actually shows.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_sidepulse import isolate_controller

from sidepulse.alcove_observation import (
    AlcoveCaptureOutcome,
    AlcoveCaptureRequest,
    AlcoveCaptureStatus,
    AlcoveObservation,
    AlcoveObservationBuffer,
    AlcoveObservationWorker,
    alcove_follow_blocker,
    capture_alcove_observation,
    latest_alcove_status,
    note_alcove_status,
    request_screen_recording_access,
    reset_alcove_status,
    reset_screen_recording_cache,
    screen_recording_granted,
)


@pytest.fixture(autouse=True)
def _isolated_alcove_state():
    """Process-wide permission cache and status record are shared state."""
    reset_screen_recording_cache()
    reset_alcove_status()
    yield
    reset_screen_recording_cache()
    reset_alcove_status()


def _request(**changes) -> AlcoveCaptureRequest:
    values = {
        "request_id": 11,
        "generation": 7,
        "screen_id": "built-in:1",
        "display_id": 1,
        "window_number": 99,
        "screen_x": 0.0,
        "screen_y": 0.0,
        "screen_width": 1512.0,
        "screen_height": 982.0,
        "window_x": 444.0,
        "window_y": 0.0,
        "window_width": 624.0,
        "menu_band_height": 37.0,
        "scale": 2.0,
        "requested_at": 100.0,
    }
    values.update(changes)
    return AlcoveCaptureRequest(**values)


def _observation(**changes) -> AlcoveObservation:
    contour = (
        (464.0, 8.0),
        (464.0, 32.0),
        (736.0, 32.0),
        (736.0, 8.0),
        (464.0, 8.0),
    )
    values = {
        "request_id": 11,
        "generation": 7,
        "screen_id": "built-in:1",
        "window_number": 99,
        "center_x": 600.0,
        "width": 272.0,
        "height": 32.0,
        "contour": contour,
        "captured_at": 100.0,
        "confidence": 0.95,
    }
    values.update(changes)
    return AlcoveObservation(**values)


class _FakeImage:
    """A CGImage stand-in with a fully transparent (unmeasurable) band."""

    def __init__(self, *, width: int = 8, height: int = 4, data: object | None = b"") -> None:
        self.width = width
        self.height = height
        self.data = bytes(width * height * 4) if data == b"" else data


class _FakeQuartz:
    """Only the calls capture_alcove_observation makes, nothing else."""

    kCGWindowListOptionIncludingWindow = 8
    kCGWindowImageNominalResolution = 16

    def __init__(self, *, image: object | None, provider: object = object()) -> None:
        self.image = image
        self.provider = provider
        self.create_calls = 0

    def CGRectMake(self, x, y, width, height):
        return (x, y, width, height)

    def CGWindowListCreateImage(self, *_args):
        self.create_calls += 1
        return self.image

    def CGImageGetWidth(self, image):
        return image.width

    def CGImageGetHeight(self, image):
        return image.height

    def CGImageGetBitsPerPixel(self, _image):
        return 32

    def CGImageGetBytesPerRow(self, image):
        return image.width * 4

    def CGImageGetBitmapInfo(self, _image):
        # kCGImageAlphaPremultipliedLast, big-endian: alpha at byte 3.
        return 1

    def CGImageGetDataProvider(self, _image):
        return self.provider

    def CGDataProviderCopyData(self, _provider):
        return self.image.data


# --- 1. the four outcomes are four values, not one None -----------------


def test_denied_screen_recording_is_named_and_never_captures(monkeypatch) -> None:
    """The permission case must be knowable WITHOUT attempting a capture.

    Without preflight, a denied capture comes back as a blank image and is
    indistinguishable from "Alcove is showing nothing".
    """
    quartz = _FakeQuartz(image=_FakeImage())
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    outcome = capture_alcove_observation(_request(), screen_recording=False)

    assert outcome.status is AlcoveCaptureStatus.SCREEN_RECORDING_DENIED
    assert outcome.observation is None
    assert quartz.create_calls == 0, "denied must not even try to capture"


def test_a_missing_window_is_not_an_unusable_image(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "Quartz", _FakeQuartz(image=None))
    missing = capture_alcove_observation(_request(), screen_recording=True)

    monkeypatch.setitem(sys.modules, "Quartz", _FakeQuartz(image=_FakeImage()))
    unusable = capture_alcove_observation(_request(), screen_recording=True)

    assert missing.status is AlcoveCaptureStatus.WINDOW_UNAVAILABLE
    assert unusable.status is AlcoveCaptureStatus.IMAGE_UNUSABLE
    assert missing.status is not unusable.status


def test_a_nil_data_provider_is_unusable_rather_than_a_crash(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        _FakeQuartz(image=_FakeImage(), provider=None),
    )

    outcome = capture_alcove_observation(_request(), screen_recording=True)

    assert outcome.status is AlcoveCaptureStatus.IMAGE_UNUSABLE


def test_an_unexpected_failure_still_cannot_raise_but_must_say_so(monkeypatch) -> None:
    class Exploding(_FakeQuartz):
        def CGWindowListCreateImage(self, *_args):
            raise RuntimeError("no window server")

    monkeypatch.setitem(sys.modules, "Quartz", Exploding(image=None))

    outcome = capture_alcove_observation(_request(), screen_recording=True)

    assert outcome.status is AlcoveCaptureStatus.CAPTURE_FAILED
    assert outcome.observation is None


def test_a_measurable_capsule_reports_captured_with_its_geometry(monkeypatch) -> None:
    """The success path must survive being made honest."""
    width_px, height_px = 624, 74
    pixels = bytearray(width_px * height_px * 4)
    for y in range(8, 41):
        for x in range(176, 448):
            pixels[(y * width_px + x) * 4 + 3] = 255
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        _FakeQuartz(
            image=_FakeImage(width=width_px, height=height_px, data=bytes(pixels))
        ),
    )

    outcome = capture_alcove_observation(_request(), screen_recording=True)

    assert outcome.status is AlcoveCaptureStatus.CAPTURED
    assert outcome.observation is not None
    assert outcome.observation.width == pytest.approx(272.0, abs=1.0)


def test_an_outcome_cannot_claim_success_with_nothing_to_show() -> None:
    """The pairing is the invariant that replaces None, so enforce it."""
    with pytest.raises(ValueError, match="observation"):
        AlcoveCaptureOutcome(AlcoveCaptureStatus.CAPTURED)
    with pytest.raises(ValueError, match="observation"):
        AlcoveCaptureOutcome(AlcoveCaptureStatus.IMAGE_UNUSABLE, _observation())
    with pytest.raises(ValueError, match="not_following"):
        AlcoveCaptureOutcome(AlcoveCaptureStatus.NOT_FOLLOWING)


# --- 2. the reason survives the worker thread ---------------------------


def test_the_worker_reports_the_reason_the_buffer_never_could() -> None:
    """An empty buffer is not a diagnosis.

    The buffer only ever carries successes, so before this the main thread
    could see that nothing arrived and still had no idea why.
    """
    buffer = AlcoveObservationBuffer()
    done = threading.Event()

    def capture(_request):
        done.set()
        return AlcoveCaptureOutcome(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED)

    worker = AlcoveObservationWorker(buffer, capture=capture)
    try:
        worker.reconcile(_request(requested_at=time.monotonic()))
        assert done.wait(2.0)
        deadline = time.monotonic() + 2.0
        while worker.last_status is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert worker.last_status is AlcoveCaptureStatus.SCREEN_RECORDING_DENIED
        assert buffer.take() is None
    finally:
        worker.close(timeout_seconds=1.0)


def test_a_capture_that_declines_to_say_why_is_a_failure_not_a_silence() -> None:
    buffer = AlcoveObservationBuffer()
    done = threading.Event()

    def capture(_request):
        done.set()
        return None

    worker = AlcoveObservationWorker(buffer, capture=capture)
    try:
        worker.reconcile(_request(requested_at=time.monotonic()))
        assert done.wait(2.0)
        deadline = time.monotonic() + 2.0
        while worker.last_status is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert worker.last_status is AlcoveCaptureStatus.CAPTURE_FAILED
    finally:
        worker.close(timeout_seconds=1.0)


# --- 3. preflight: cached, and never a surprise prompt ------------------


def test_preflight_is_cached_and_refreshable() -> None:
    calls: list[int] = []

    def probe() -> bool:
        calls.append(1)
        return True

    first = screen_recording_granted(now=100.0, preflight=probe)
    second = screen_recording_granted(now=101.0, preflight=probe)

    assert (first, second) == (True, True)
    assert len(calls) == 1, "a per-frame TCC probe is not a cache"

    # It genuinely changes: the user can grant or revoke it while we run.
    assert screen_recording_granted(now=200.0, preflight=probe) is True
    assert len(calls) == 2
    assert screen_recording_granted(force=True, now=200.0, preflight=lambda: False) is False


def test_an_unaskable_preflight_is_unknown_not_denied() -> None:
    """None is not False. Telling someone their permission is off when we
    simply could not ask is the same dishonesty, pointing the other way."""
    assert screen_recording_granted(preflight=lambda: None) is None
    reset_screen_recording_cache()
    assert alcove_follow_blocker(following=True) is not (
        AlcoveCaptureStatus.SCREEN_RECORDING_DENIED
    )


def test_nothing_on_the_background_path_may_request_access(monkeypatch) -> None:
    """A permission dialog nobody asked for is its own bug."""
    requested: list[int] = []

    def never(*_args, **_kwargs):
        requested.append(1)
        return True

    import sidepulse.alcove_observation as module

    monkeypatch.setattr(module, "_preflight_screen_capture_access", lambda: False)
    quartz = SimpleNamespace(
        CGRequestScreenCaptureAccess=never,
        CGPreflightScreenCaptureAccess=lambda: False,
    )
    monkeypatch.setattr(module, "_quartz", lambda: quartz)

    screen_recording_granted(force=True)
    capture_alcove_observation(_request())
    alcove_follow_blocker(following=True)

    assert requested == [], "only an explicit user action may prompt"

    # ...and the explicit action does ask, exactly once.
    assert request_screen_recording_access() is True
    assert requested == [1]


def test_requesting_access_invalidates_the_cached_answer() -> None:
    assert screen_recording_granted(preflight=lambda: False) is False
    request_screen_recording_access(request=lambda: True)
    assert screen_recording_granted(preflight=lambda: True) is True


# --- 4. the status record logs on transition, not per frame -------------


def test_a_repeated_outcome_is_recorded_but_reported_once() -> None:
    assert note_alcove_status(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED) is True
    assert note_alcove_status(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED) is False
    assert note_alcove_status(AlcoveCaptureStatus.CAPTURED) is True
    snapshot = latest_alcove_status()
    assert snapshot is not None
    assert snapshot.status is AlcoveCaptureStatus.CAPTURED


# --- 5. the device: preflight where following is enabled ----------------


class _Rect:
    def __init__(self, x: float, y: float, width: float, height: float) -> None:
        self.origin = SimpleNamespace(x=x, y=y)
        self.size = SimpleNamespace(width=width, height=height)


class _Screen:
    def frame(self):
        return _Rect(0.0, 0.0, 1512.0, 982.0)

    def deviceDescription(self):
        return {"NSScreenNumber": 1}

    def backingScaleFactor(self) -> float:
        return 2.0

    def safeAreaInsets(self):
        return SimpleNamespace(top=32.0)

    def auxiliaryTopLeftArea(self):
        return _Rect(0.0, 950.0, 646.0, 32.0)

    def auxiliaryTopRightArea(self):
        return _Rect(866.0, 950.0, 646.0, 32.0)


class _ScreenClass:
    @staticmethod
    def mainScreen():
        return _Screen()


class _Window:
    def __init__(self) -> None:
        self.current = _Rect(646.0, 945.0, 220.0, 37.0)
        self.levels: list[int] = []

    def isVisible(self) -> bool:
        return True

    def frame(self):
        return self.current

    def windowNumber(self) -> int:
        return 500

    def setFrame_display_(self, frame, _display) -> None:
        self.current = _Rect(frame[0][0], frame[0][1], frame[1][0], frame[1][1])

    def setLevel_(self, level: int) -> None:
        self.levels.append(level)


class _View:
    def __init__(self) -> None:
        self.silhouettes: list[object] = []
        self.compact: list[bool] = []

    def setRenderGeometryIdentity_(self, _identity) -> None:
        pass

    def setHasNotch_(self, _value) -> None:
        pass

    def setCompactMode_(self, value) -> None:
        self.compact.append(bool(value))

    def setWingsOnlyMode_(self, _value) -> None:
        pass

    def setAlcoveSilhouette_(self, value) -> None:
        self.silhouettes.append(value)

    def setFrame_(self, _frame) -> None:
        pass

    def setNotchWidth_(self, _width) -> None:
        pass


def _alcove_device(monkeypatch, *, granted, alcove_running=True, window=(99, 444.0, 0.0, 624.0)):
    from sidepulse import virtual_device

    device = virtual_device.VirtualStatusDevice.alloc().init()
    device.window = _Window()
    device.view = _View()
    device.wraps_menu_bar = True
    device.follow_alcove_width = True
    monkeypatch.setattr(virtual_device, "NSScreen", _ScreenClass)
    monkeypatch.setattr(virtual_device, "is_alcove_running", lambda: alcove_running)
    monkeypatch.setattr(
        virtual_device, "measured_notch_silhouette", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        virtual_device, "_alcove_window_values", lambda *_a: window
    )
    monkeypatch.setattr(
        virtual_device, "screen_recording_granted", lambda **_k: granted
    )
    logged: list[str] = []
    monkeypatch.setattr(
        virtual_device.VirtualStatusDevice,
        "_record_alcove_status",
        _recording_status(logged),
    )
    return device, virtual_device, logged


def _recording_status(logged: list[str]):
    """Wrap the real recorder so the log line is captured, not printed."""
    from sidepulse.alcove_observation import ALCOVE_STATUS_LOG_LINES
    from sidepulse.virtual_device import VirtualStatusDevice

    original = VirtualStatusDevice._record_alcove_status

    def recorder(self, status, *, now=None):
        changed = original(self, status, now=now)
        if changed:
            logged.append(ALCOVE_STATUS_LOG_LINES[status])
        return changed

    return recorder


def test_enabling_following_preflights_and_says_permission_is_missing(
    monkeypatch,
) -> None:
    """Requirement: preflight AT the point following is enabled."""
    from sidepulse import virtual_device

    device = virtual_device.VirtualStatusDevice.alloc().init()
    seen: list[bool] = []

    def granted(**kwargs) -> bool:
        seen.append(bool(kwargs.get("force")))
        return False

    monkeypatch.setattr(virtual_device, "screen_recording_granted", granted)

    device.set_follow_alcove(True)

    assert seen == [True], "enabling must ask the system, not a stale cache"
    snapshot = latest_alcove_status()
    assert snapshot is not None
    assert snapshot.status is AlcoveCaptureStatus.SCREEN_RECORDING_DENIED


def test_denied_permission_reports_instead_of_starting_a_capture_thread(
    monkeypatch,
) -> None:
    device, virtual_device, logged = _alcove_device(monkeypatch, granted=False)
    started: list[int] = []
    device._alcove_observer_factory = lambda _buffer: started.append(1)

    device.reposition()
    device.reposition()

    assert started == [], "a capture that can only be denied is pure cost"
    snapshot = latest_alcove_status()
    assert snapshot is not None
    assert snapshot.status is AlcoveCaptureStatus.SCREEN_RECORDING_DENIED
    assert logged == ["alcove: screen recording not granted -- not following"], (
        "the reason belongs in the log once per transition, not per frame"
    )


def test_a_missing_alcove_window_is_reported_as_such(monkeypatch) -> None:
    device, _module, logged = _alcove_device(monkeypatch, granted=True, window=None)

    device.reposition()

    snapshot = latest_alcove_status()
    assert snapshot is not None
    assert snapshot.status is AlcoveCaptureStatus.WINDOW_UNAVAILABLE
    assert logged == ["alcove: no capsule window on screen"]


def test_turning_following_off_is_said_out_loud(monkeypatch) -> None:
    device, _module, logged = _alcove_device(monkeypatch, granted=True)
    device.follow_alcove_width = False

    device.reposition()

    snapshot = latest_alcove_status()
    assert snapshot is not None
    assert snapshot.status is AlcoveCaptureStatus.NOT_FOLLOWING
    assert logged == ["alcove: following is off"]


def test_the_worker_reason_reaches_the_log_once_per_transition(monkeypatch) -> None:
    device, _module, logged = _alcove_device(monkeypatch, granted=True)

    class Observer:
        def __init__(self) -> None:
            self.last_status = AlcoveCaptureStatus.IMAGE_UNUSABLE

        def reconcile(self, _request) -> None:
            pass

        def close(self, *, timeout_seconds: float) -> bool:
            return True

    observer = Observer()
    device._alcove_observer_factory = lambda _buffer: observer

    device.reposition()
    device.reposition()
    device.reposition()
    assert logged == ["alcove: captured, image unusable"]

    observer.last_status = AlcoveCaptureStatus.CAPTURE_FAILED
    device.reposition()
    assert logged == [
        "alcove: captured, image unusable",
        "alcove: capture failed",
    ]


def test_the_first_hardware_geometry_fallback_is_logged(monkeypatch) -> None:
    """The one line that DID exist could never print the failing state.

    Its sentinel for "not logged yet" was None -- the same value
    follow_width has when nothing was measured -- so the very first pass,
    and every pass of a permanently broken follow, compared equal and
    said nothing. Zero alcove lines in the log, exactly as observed.
    """
    from sidepulse import virtual_device

    device, module, _logged = _alcove_device(monkeypatch, granted=True)
    lines: list[str] = []
    monkeypatch.setattr(
        virtual_device, "_alcove_window_values", lambda *_a: (99, 444.0, 0.0, 624.0)
    )

    class Observer:
        last_status = AlcoveCaptureStatus.IMAGE_UNUSABLE

        def reconcile(self, _request) -> None:
            pass

        def close(self, *, timeout_seconds: float) -> bool:
            return True

    device._alcove_observer_factory = lambda _buffer: Observer()
    import sidepulse.status_bar as status_bar

    monkeypatch.setattr(status_bar, "log_status_bar", lines.append)

    device.reposition()

    assert "alcove follow: hardware geometry" in lines


_CONTOUR = (
    (464.0, 8.0),
    (464.0, 32.0),
    (736.0, 32.0),
    (736.0, 8.0),
    (464.0, 8.0),
)


def _publish_capsule(device) -> None:
    request = device._alcove_request
    assert request is not None
    device._alcove_buffer.publish(
        _observation(
            request_id=request.request_id,
            generation=request.generation,
            screen_id=request.screen_id,
            window_number=request.window_number,
            contour=_CONTOUR,
            captured_at=time.monotonic(),
        )
    )


def test_a_granted_permission_still_follows_the_capsule(monkeypatch) -> None:
    """Requirement 5: do not regress the working path.

    Bracket geometry and the window level both key off a real
    observation, so a granted permission must still reach exactly the
    behaviour the existing tests pin.
    """
    device, module, _logged = _alcove_device(monkeypatch, granted=True)

    class Observer:
        last_status = AlcoveCaptureStatus.CAPTURED

        def reconcile(self, _request) -> None:
            pass

        def close(self, *, timeout_seconds: float) -> bool:
            return True

    device._alcove_observer_factory = lambda _buffer: Observer()
    device.reposition()
    _publish_capsule(device)
    device.reposition()

    inset = module.ALCOVE_ACCENT_EDGE_INSET
    assert device.window.current.size.width == pytest.approx(272.0 + 2 * inset)
    assert device.window.current.origin.x == pytest.approx(464.0 - inset)
    assert device.view.silhouettes[-1] == (600.0, 272.0, 32.0, _CONTOUR)
    assert device.window.levels[-1] >= module.ABOVE_ALCOVE_WINDOW_LEVEL
    snapshot = latest_alcove_status()
    assert snapshot is not None and snapshot.status is AlcoveCaptureStatus.CAPTURED


def test_compact_mode_keeps_measuring_the_capsule(monkeypatch) -> None:
    """Wrap OFF is the polite mode, not a teardown.

    Turning wrap off used to stop observation outright. It must still
    submit requests and receive geometry -- what the compact RENDER then
    does with that geometry is a separate, pre-existing question.
    """
    device, module, _logged = _alcove_device(monkeypatch, granted=True)
    device.wraps_menu_bar = False

    class Observer:
        last_status = AlcoveCaptureStatus.CAPTURED

        def __init__(self) -> None:
            self.requests: list[object] = []

        def reconcile(self, request) -> None:
            self.requests.append(request)

        def close(self, *, timeout_seconds: float) -> bool:
            return True

    observer = Observer()
    device._alcove_observer_factory = lambda _buffer: observer
    device.reposition()
    _publish_capsule(device)
    device.reposition()

    assert observer.requests, "compact must still ask for a measurement"
    assert device.view.compact[-1] is True
    assert device.view.silhouettes[-1] == (600.0, 272.0, 32.0, _CONTOUR)
    assert device.window.levels[-1] >= module.ABOVE_ALCOVE_WINDOW_LEVEL


# --- 6. doctor: a bounded, content-free code ---------------------------


def test_doctor_reports_the_permission_as_its_own_code(monkeypatch) -> None:
    from sidepulse import doctor

    note_alcove_status(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED)
    monkeypatch.setattr(doctor, "_alcove_following_enabled", lambda: True)

    finding = doctor._alcove_follow_state_probe()

    assert finding.check is doctor.DiagnosticCheck.ALCOVE_FOLLOW_STATE
    assert finding.code is doctor.DiagnosticCode.NOT_PERMITTED
    assert finding.count == 0 and finding.limit == 1


@pytest.mark.parametrize(
    ("status", "code_name"),
    [
        (AlcoveCaptureStatus.CAPTURED, "HEALTHY"),
        (AlcoveCaptureStatus.SCREEN_RECORDING_DENIED, "NOT_PERMITTED"),
        (AlcoveCaptureStatus.WINDOW_UNAVAILABLE, "NOT_RUNNING"),
        (AlcoveCaptureStatus.IMAGE_UNUSABLE, "UNUSABLE"),
        (AlcoveCaptureStatus.CAPTURE_FAILED, "UNAVAILABLE"),
    ],
)
def test_every_outcome_has_a_distinct_doctor_code(
    monkeypatch, status, code_name
) -> None:
    from sidepulse import doctor

    note_alcove_status(status)
    monkeypatch.setattr(doctor, "_alcove_following_enabled", lambda: True)

    finding = doctor._alcove_follow_state_probe()

    assert finding.code is getattr(doctor.DiagnosticCode, code_name)


def test_doctor_never_upgrades_no_obvious_blocker_into_success(monkeypatch) -> None:
    """"Nothing is in the way" is not "it works"."""
    import sidepulse.alcove_observation as module
    from sidepulse import doctor

    monkeypatch.setattr(doctor, "_alcove_following_enabled", lambda: True)
    monkeypatch.setattr(module, "_preflight_screen_capture_access", lambda: True)
    monkeypatch.setattr(module, "alcove_window_present", lambda **_k: True)

    finding = doctor._alcove_follow_state_probe()

    assert finding.code is doctor.DiagnosticCode.UNAVAILABLE


def test_a_stale_reading_is_not_presented_as_current(monkeypatch) -> None:
    import sidepulse.alcove_observation as module
    from sidepulse import doctor

    note_alcove_status(
        AlcoveCaptureStatus.CAPTURED,
        now=time.monotonic() - module.ALCOVE_STATUS_MAX_AGE_SECONDS - 5.0,
    )
    monkeypatch.setattr(doctor, "_alcove_following_enabled", lambda: True)
    monkeypatch.setattr(module, "_preflight_screen_capture_access", lambda: True)
    monkeypatch.setattr(module, "alcove_window_present", lambda **_k: True)

    finding = doctor._alcove_follow_state_probe()

    assert finding.code is not doctor.DiagnosticCode.HEALTHY


def test_the_alcove_finding_is_in_the_manifest_and_encodes(monkeypatch) -> None:
    from sidepulse import doctor

    note_alcove_status(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED)
    monkeypatch.setattr(doctor, "_alcove_following_enabled", lambda: True)
    result = doctor.collect_diagnostics()
    encoded = doctor.encode_diagnostic_result(result).decode("ascii")

    assert (
        doctor.DiagnosticCheck.ALCOVE_FOLLOW_STATE
        in tuple(field.check for field in doctor.DIAGNOSTIC_MANIFEST.fields)
    )
    assert '"check":"alcove_follow_state"' in encoded
    assert '"code":"not_permitted"' in encoded
    assert (
        result.finding(doctor.DiagnosticCheck.ALCOVE_FOLLOW_STATE).code
        is doctor.DiagnosticCode.NOT_PERMITTED
    )


# --- 7. the Screen Bar pane: the switch may not lie --------------------


class AlcoveSettingsSurfaceTests(unittest.TestCase):
    """The control said ON in all four failure modes and explained none.

    "Match Alcove's width automatically" is a switch with no readout, so
    a denied Screen Recording permission looked exactly like a working
    feature that had nothing to match. These drive the real pane.
    """

    def setUp(self) -> None:
        isolate_controller(self)
        reset_screen_recording_cache()
        reset_alcove_status()
        self.addCleanup(reset_screen_recording_cache)
        self.addCleanup(reset_alcove_status)
        from sidepulse import settings_window

        self.settings_window = settings_window

    def _pane(self):
        self.controller.show_settings_window()
        self.controller.ensure_settings_pane("colors_screen_bar")
        return (
            self.controller.settings_fields["alcove_follow_status"],
            self.controller.settings_buttons["alcove_screen_recording_permission"],
        )

    def test_a_denied_permission_is_named_next_to_the_switch(self) -> None:
        note_alcove_status(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED)

        label, button = self._pane()

        self.assertIn("Screen Recording is off", label.stringValue())
        self.assertFalse(button.isHidden(), "the one action that fixes it")
        self.assertEqual(button.title(), "Open Screen Recording Settings…")
        # The setting itself is still ON -- the user asked for following
        # and is getting none. That gap is exactly why the row exists.
        self.assertTrue(self.controller.settings.screen_bar_follow_alcove)

    def test_a_working_capsule_says_so_and_offers_nothing(self) -> None:
        note_alcove_status(AlcoveCaptureStatus.CAPTURED)

        label, button = self._pane()

        self.assertEqual(label.stringValue(), "Matching Alcove's width.")
        self.assertTrue(
            button.isHidden(),
            "sending someone to fix a permission that is fine is its own lie",
        )

    def test_alcove_absent_is_distinguished_from_a_denied_permission(self) -> None:
        note_alcove_status(AlcoveCaptureStatus.WINDOW_UNAVAILABLE)

        label, button = self._pane()

        self.assertIn("not showing a capsule", label.stringValue())
        self.assertNotIn("Screen Recording", label.stringValue())
        self.assertTrue(button.isHidden())

    def test_an_unusable_image_is_not_reported_as_a_missing_alcove(self) -> None:
        note_alcove_status(AlcoveCaptureStatus.IMAGE_UNUSABLE)

        label, _button = self._pane()

        self.assertIn("could not be measured", label.stringValue())

    def test_following_switched_off_reads_as_off_not_broken(self) -> None:
        self.controller.settings = self.controller.settings.with_screen_bar_follow_alcove(
            False
        )

        label, button = self._pane()

        self.assertIn("Not following Alcove", label.stringValue())
        self.assertTrue(button.isHidden())

    def test_granting_the_permission_drops_the_reading_it_just_invalidated(
        self,
    ) -> None:
        """The recorded "denied" was taken under the old permission.

        Leaving it in place would keep telling the person who just
        granted Screen Recording that Screen Recording is off, for as
        long as the record stays fresh.
        """
        note_alcove_status(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED)
        label, button = self._pane()
        opened: list[str] = []

        with (
            patch.object(self.settings_window, "open_url", opened.append),
            patch.object(
                self.settings_window,
                "request_screen_recording_access",
                lambda **_kwargs: True,
            ),
            patch.object(
                self.settings_window, "alcove_follow_blocker", lambda **_kwargs: None
            ),
        ):
            self.controller.alcove_actions.grantScreenRecording_(None)

        self.assertEqual(opened, [], "already granted needs no settings pane")
        self.assertIsNone(latest_alcove_status())
        self.assertNotIn("Screen Recording is off", label.stringValue())
        self.assertEqual(label.stringValue(), "No measurement yet.")
        self.assertTrue(button.isHidden())

    def test_a_grant_never_claims_following_works_before_it_has(self) -> None:
        """Permission is one of four things that must be true, not all."""
        note_alcove_status(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED)
        self._pane()
        messages: list[str] = []
        self.controller.set_settings_message = messages.append

        with (
            patch.object(self.settings_window, "open_url", lambda _url: None),
            patch.object(
                self.settings_window,
                "request_screen_recording_access",
                lambda **_kwargs: True,
            ),
            patch.object(
                self.settings_window,
                "alcove_follow_blocker",
                lambda **_kwargs: AlcoveCaptureStatus.WINDOW_UNAVAILABLE,
            ),
        ):
            self.controller.alcove_actions.grantScreenRecording_(None)

        self.assertEqual(len(messages), 1)
        self.assertIn("not showing a capsule", messages[0])
        self.assertNotIn("live", messages[0])

    def test_a_still_denied_permission_opens_the_right_pane(self) -> None:
        note_alcove_status(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED)
        label, button = self._pane()
        opened: list[str] = []

        with (
            patch.object(self.settings_window, "open_url", opened.append),
            patch.object(
                self.settings_window,
                "request_screen_recording_access",
                lambda **_kwargs: False,
            ),
        ):
            self.controller.alcove_actions.grantScreenRecording_(None)

        self.assertEqual(opened, [self.settings_window.SCREEN_RECORDING_SETTINGS_URL])
        self.assertIn("Privacy_ScreenCapture", opened[0])
        self.assertIn("Screen Recording is off", label.stringValue())
        self.assertFalse(button.isHidden())

    def test_the_row_refreshes_with_the_rest_of_the_window(self) -> None:
        """Panes are built once, lazily -- a frozen row is a stale lie."""
        note_alcove_status(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED)
        label, button = self._pane()

        note_alcove_status(AlcoveCaptureStatus.CAPTURED)
        self.settings_window.refresh_alcove_follow_controls(self.controller)

        self.assertEqual(label.stringValue(), "Matching Alcove's width.")
        self.assertTrue(button.isHidden())

    def test_the_dropdown_names_only_the_failure_the_user_can_fix(self) -> None:
        """Follows the "hooks are out of date" precedent, deliberately.

        A missing Alcove or an unmeasurable capsule is visible in the
        pane and fixes itself; a permission nobody was ever asked for
        has no symptom at all beyond a bar that never changes size.
        """
        note_alcove_status(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED)
        title = self.settings_window.alcove_menu_alert_title(self.controller)

        self.assertIn("Screen Recording", title)
        self.assertTrue(title.startswith("⚠ "))

        for quiet in (
            AlcoveCaptureStatus.CAPTURED,
            AlcoveCaptureStatus.WINDOW_UNAVAILABLE,
            AlcoveCaptureStatus.IMAGE_UNUSABLE,
            AlcoveCaptureStatus.NOT_FOLLOWING,
        ):
            note_alcove_status(quiet)
            self.assertEqual(
                self.settings_window.alcove_menu_alert_title(self.controller),
                "",
                quiet.value,
            )

    def test_the_menu_action_target_exists_without_opening_settings(self) -> None:
        """The dropdown must offer the click even on a first run.

        Hanging the action off the settings pane would mean the one fix
        is only reachable from the place that is already showing it.
        """
        actions = self.settings_window.alcove_actions_for(self.controller)

        self.assertTrue(actions.respondsToSelector_("grantScreenRecording:"))
        self.assertIs(
            self.settings_window.alcove_actions_for(self.controller), actions
        )
