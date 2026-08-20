from __future__ import annotations

import dataclasses
import math
import threading
import time
from dataclasses import replace

import pytest

from sidepulse.alcove_observation import (
    ALCOVE_HOLD_SECONDS,
    AlcoveCaptureRequest,
    AlcoveObservation,
    AlcoveObservationBuffer,
    AlcoveObservationReducer,
    AlcoveObservationWorker,
    RawAlphaImage,
    scan_alpha_image,
    validate_observation,
)


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
    values = {
        "request_id": 11,
        "generation": 7,
        "screen_id": "built-in:1",
        "window_number": 99,
        "center_x": 756.0,
        "width": 272.0,
        "height": 32.0,
        "captured_at": 100.0,
        "confidence": 0.95,
    }
    values.update(changes)
    if "contour" not in changes:
        center = float(values["center_x"])
        half_width = float(values["width"]) / 2.0
        left = center - half_width
        right = center + half_width
        values["contour"] = (
            (left, 8.0),
            (left, 16.0),
            (left, 32.0),
            (right, 32.0),
            (right, 16.0),
            (right, 8.0),
            (left, 8.0),
        )
    return AlcoveObservation(**values)


def test_valid_observation_preserves_measured_center_size_contour_and_identity() -> None:
    request = _request()
    observation = _observation()

    assert validate_observation(observation, request, now=101.0)
    assert observation.center_x == 756.0
    assert observation.width == 272.0
    assert observation.height == 32.0
    assert observation.contour[0] == observation.contour[-1]
    assert observation.window_number == request.window_number
    assert observation.generation == request.generation
    assert observation.screen_id == request.screen_id


@pytest.mark.parametrize(
    "mutation",
    [
        {"generation": 8},
        {"screen_id": "external:2"},
        {"window_number": 100},
        {"confidence": 0.49},
        {"captured_at": 97.0},
        {"center_x": math.nan},
        {"width": math.inf},
        {"height": 0.0},
        {"width": 2000.0},
        {"center_x": -40.0},
        {"contour": ((620.0, 8.0),) * 65},
        {"contour": ((620.0, 8.0), (math.nan, 9.0), (620.0, 8.0))},
    ],
)
def test_invalid_or_stale_observations_are_rejected(mutation: dict[str, object]) -> None:
    assert not validate_observation(
        replace(_observation(), **mutation),
        _request(),
        now=101.0,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"screen_id": ""},
        {"display_id": 0},
        {"window_number": 0},
        {"screen_width": 0.0},
        {"window_width": math.inf},
        {"menu_band_height": -1.0},
        {"scale": math.nan},
    ],
)
def test_invalid_plain_request_geometry_cannot_validate(mutation: dict[str, object]) -> None:
    assert not validate_observation(
        _observation(),
        replace(_request(), **mutation),
        now=101.0,
    )


def test_rapid_same_window_replacement_rejects_old_or_pre_request_capture() -> None:
    active = _request(request_id=22, requested_at=100.5)

    assert not validate_observation(
        _observation(request_id=21, captured_at=100.9),
        active,
        now=101.0,
    )
    assert not validate_observation(
        _observation(request_id=22, captured_at=100.4),
        active,
        now=101.0,
    )
    assert validate_observation(
        _observation(request_id=22, captured_at=100.5),
        active,
        now=101.0,
    )


def test_reducer_tracks_both_directions_and_never_holds_a_too_wide_bracket() -> None:
    """A bracket narrower than the capsule hides inside Alcove's black; a
    bracket WIDER than it is a glowing sliver on the wallpaper. Narrowing
    therefore adopts immediately -- the old 3-second damping was 3 seconds
    of visible overhang on every capsule collapse."""
    request = _request()
    reducer = AlcoveObservationReducer()

    assert reducer.apply(_observation(width=272.0), request, now=100.1)
    assert reducer.current(now=100.1).width == 272.0
    assert reducer.apply(_observation(width=340.0, captured_at=101.0), request, now=101.1)
    assert reducer.current(now=101.1).width == 340.0

    # Collapse: the very next valid measurement wins.
    assert reducer.apply(_observation(width=210.0, captured_at=102.0), request, now=102.1)
    assert reducer.current(now=102.1).width == 210.0
    # Sub-point jitter in either direction is ignored.
    assert reducer.apply(_observation(width=210.4, captured_at=103.0), request, now=103.1)
    assert reducer.current(now=103.1).width == 210.0
    assert reducer.apply(_observation(width=209.7, captured_at=104.0), request, now=104.1)
    assert reducer.current(now=104.1).width == 210.0


def test_reducer_holds_last_good_for_eight_seconds_then_uses_hardware_fallback() -> None:
    reducer = AlcoveObservationReducer()
    request = _request()
    assert reducer.apply(_observation(), request, now=100.1)

    assert reducer.current(now=100.1 + ALCOVE_HOLD_SECONDS) is not None
    assert reducer.current(now=100.2 + ALCOVE_HOLD_SECONDS) is None
    assert ALCOVE_HOLD_SECONDS == 8.0


def test_reducer_never_invents_a_center_without_a_valid_observation() -> None:
    reducer = AlcoveObservationReducer()
    request = _request()

    assert reducer.current(now=100.0) is None
    assert not reducer.apply(_observation(center_x=math.nan), request, now=100.1)
    assert reducer.current(now=100.1) is None


def test_raw_alpha_scan_uses_synthetic_provider_bytes_and_returns_plain_geometry() -> None:
    width_px = 200
    height_px = 80
    pixels = bytearray(width_px * height_px * 4)
    for y in range(10, 42):
        inset = 4 if y in {10, 11, 40, 41} else 0
        for x in range(40 + inset, 160 - inset):
            pixels[(y * width_px + x) * 4 + 3] = 255
    image = RawAlphaImage(
        width=width_px,
        height=height_px,
        bytes_per_row=width_px * 4,
        bytes_per_pixel=4,
        alpha_offset=3,
        pixels=bytes(pixels),
    )
    request = _request(window_width=100.0, window_x=706.0)

    observation = scan_alpha_image(request, image, captured_at=100.25)

    assert observation is not None
    assert observation.center_x == pytest.approx(756.0, abs=0.6)
    assert observation.width == pytest.approx(60.0, abs=1.0)
    assert observation.height == pytest.approx(16.0, abs=1.0)
    assert 0.5 <= observation.confidence <= 1.0
    assert len(observation.contour) <= 64
    assert observation.contour[0] == observation.contour[-1]


def test_worker_has_one_capture_in_flight_and_only_the_latest_request_pending() -> None:
    entered = threading.Event()
    release = threading.Event()
    captured: list[int] = []
    active = 0
    maximum_active = 0
    lock = threading.Lock()
    buffer = AlcoveObservationBuffer()

    def capture(request: AlcoveCaptureRequest) -> AlcoveObservation:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
            captured.append(request.generation)
        entered.set()
        if request.generation == 0:
            assert release.wait(2.0)
        with lock:
            active -= 1
        return _observation(
            request_id=request.request_id,
            generation=request.generation,
            screen_id=request.screen_id,
            window_number=request.window_number,
            captured_at=time.monotonic(),
        )

    worker = AlcoveObservationWorker(buffer, capture=capture)
    worker.reconcile(_request(generation=0, requested_at=time.monotonic()))
    assert entered.wait(2.0)
    for generation in range(1, 100):
        worker.reconcile(
            _request(
                request_id=generation + 1,
                generation=generation,
                requested_at=time.monotonic(),
            )
        )

    assert worker.pending_count == 1
    release.set()
    deadline = time.monotonic() + 2.0
    while captured != [0, 99] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert worker.close(timeout_seconds=1.0)
    assert captured == [0, 99]
    assert maximum_active == 1


def test_worker_publishes_only_frozen_plain_values() -> None:
    buffer = AlcoveObservationBuffer()
    completed = threading.Event()

    def capture(request: AlcoveCaptureRequest) -> AlcoveObservation:
        completed.set()
        return _observation(captured_at=time.monotonic())

    worker = AlcoveObservationWorker(buffer, capture=capture)
    worker.reconcile(_request(requested_at=time.monotonic()))
    assert completed.wait(2.0)
    deadline = time.monotonic() + 2.0
    result = None
    while result is None and time.monotonic() < deadline:
        result = buffer.take()
        time.sleep(0.001)
    assert worker.close(timeout_seconds=1.0)
    assert result is not None

    def assert_plain(value: object) -> None:
        if dataclasses.is_dataclass(value):
            assert getattr(type(value), "__dataclass_params__").frozen
            for field in dataclasses.fields(value):
                assert_plain(getattr(value, field.name))
            return
        if isinstance(value, tuple):
            for item in value:
                assert_plain(item)
            return
        assert value is None or isinstance(value, (str, int, float, bool, bytes))

    assert_plain(result)
    assert all(
        forbidden not in {type(value).__name__ for value in dataclasses.astuple(result)}
        for forbidden in ("NSBitmapImageRep", "NSImage", "NSView", "NSWindow", "NSScreen")
    )


def test_worker_drops_a_late_capture_result_after_close() -> None:
    buffer = AlcoveObservationBuffer()
    entered = threading.Event()
    release = threading.Event()

    def capture(_request: AlcoveCaptureRequest) -> AlcoveObservation:
        entered.set()
        assert release.wait(2.0)
        return _observation(captured_at=time.monotonic())

    worker = AlcoveObservationWorker(buffer, capture=capture)
    worker.reconcile(_request(requested_at=time.monotonic()))
    assert entered.wait(2.0)
    assert not worker.close(timeout_seconds=0.01)
    release.set()
    deadline = time.monotonic() + 2.0
    while worker.in_flight and time.monotonic() < deadline:
        time.sleep(0.005)

    assert buffer.take() is None
