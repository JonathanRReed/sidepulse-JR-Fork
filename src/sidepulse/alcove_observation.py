from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

ALCOVE_ALPHA_THRESHOLD = 0.08
ALCOVE_CONFIDENCE_MINIMUM = 0.75
ALCOVE_MAX_AGE_SECONDS = 2.0
ALCOVE_NARROW_AFTER_SECONDS = 3.0
ALCOVE_HOLD_SECONDS = 8.0
ALCOVE_MAX_WIDTH = 520.0
ALCOVE_MAX_BAND_FACTOR = 1.8
ALCOVE_MAX_CONTOUR_POINTS = 64


@dataclass(frozen=True, slots=True)
class AlcoveCaptureRequest:
    request_id: int
    generation: int
    screen_id: str
    display_id: int
    window_number: int
    screen_x: float
    screen_y: float
    screen_width: float
    screen_height: float
    window_x: float
    window_y: float
    window_width: float
    menu_band_height: float
    scale: float
    requested_at: float


@dataclass(frozen=True, slots=True)
class AlcoveObservation:
    request_id: int
    generation: int
    screen_id: str
    window_number: int
    center_x: float
    width: float
    height: float
    contour: tuple[tuple[float, float], ...]
    captured_at: float
    confidence: float


@dataclass(frozen=True, slots=True)
class RawAlphaImage:
    width: int
    height: int
    bytes_per_row: int
    bytes_per_pixel: int
    alpha_offset: int
    pixels: bytes


def _finite(*values: object) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in values)
    except (TypeError, ValueError, OverflowError):
        return False


def validate_observation(
    observation: AlcoveObservation,
    request: AlcoveCaptureRequest,
    *,
    now: float,
) -> bool:
    """Accept only fresh geometry for the exact main-thread request fence."""
    if not isinstance(observation, AlcoveObservation):
        return False
    if (
        not request.screen_id
        or not (1 <= request.request_id < 2**63)
        or request.display_id <= 0
        or request.window_number <= 0
        or not _finite(
            request.screen_x,
            request.screen_y,
            request.screen_width,
            request.screen_height,
            request.window_x,
            request.window_y,
            request.window_width,
            request.menu_band_height,
            request.scale,
            request.requested_at,
        )
        or request.screen_width <= 0.0
        or request.screen_height <= 0.0
        or request.window_width <= 0.0
        or request.menu_band_height <= 0.0
        or request.scale <= 0.0
    ):
        return False
    if (
        observation.request_id != request.request_id
        or observation.generation != request.generation
        or observation.screen_id != request.screen_id
        or observation.window_number != request.window_number
    ):
        return False
    if not _finite(
        now,
        observation.center_x,
        observation.width,
        observation.height,
        observation.captured_at,
        observation.confidence,
        request.screen_x,
        request.screen_width,
        request.menu_band_height,
    ):
        return False
    age = float(now) - observation.captured_at
    if (
        observation.captured_at < request.requested_at
        or age < -0.25
        or age > ALCOVE_MAX_AGE_SECONDS
    ):
        return False
    if observation.confidence < ALCOVE_CONFIDENCE_MINIMUM:
        return False
    maximum_width = min(ALCOVE_MAX_WIDTH, max(0.0, request.screen_width - 8.0))
    maximum_height = request.menu_band_height * ALCOVE_MAX_BAND_FACTOR
    if not (40.0 <= observation.width <= maximum_width):
        return False
    if not (1.0 <= observation.height <= maximum_height):
        return False
    left = observation.center_x - observation.width / 2.0
    right = observation.center_x + observation.width / 2.0
    screen_right = request.screen_x + request.screen_width
    if left < request.screen_x - 1.0 or right > screen_right + 1.0:
        return False
    contour = observation.contour
    if not (4 <= len(contour) <= ALCOVE_MAX_CONTOUR_POINTS):
        return False
    if contour[0] != contour[-1]:
        return False
    for point in contour:
        if not isinstance(point, tuple) or len(point) != 2 or not _finite(*point):
            return False
        x, y = point
        if x < left - 2.0 or x > right + 2.0:
            return False
        if y < request.window_y - 2.0 or y > request.window_y + maximum_height + 2.0:
            return False
    return True


class AlcoveObservationReducer:
    """Main-thread width hysteresis and stale fallback for validated observations."""

    def __init__(self) -> None:
        self._adopted: AlcoveObservation | None = None
        self._last_good_at: float | None = None
        self._narrow_candidate: AlcoveObservation | None = None
        self._narrow_since: float | None = None

    def apply(
        self,
        observation: AlcoveObservation,
        request: AlcoveCaptureRequest,
        *,
        now: float,
    ) -> bool:
        if not validate_observation(observation, request, now=now):
            return False
        self._last_good_at = float(now)
        adopted = self._adopted
        if adopted is None or observation.width > adopted.width + 0.5:
            self._adopted = observation
            self._narrow_candidate = None
            self._narrow_since = None
            return True
        if observation.width < adopted.width - 4.0:
            candidate = self._narrow_candidate
            if (
                candidate is None
                or self._narrow_since is None
                or abs(observation.width - candidate.width) > 4.0
            ):
                self._narrow_candidate = observation
                self._narrow_since = float(now)
                return True
            if float(now) - self._narrow_since >= ALCOVE_NARROW_AFTER_SECONDS:
                self._adopted = candidate
                self._narrow_candidate = None
                self._narrow_since = None
            return True
        self._narrow_candidate = None
        self._narrow_since = None
        return True

    def current(self, *, now: float) -> AlcoveObservation | None:
        if (
            self._adopted is None
            or self._last_good_at is None
            or not _finite(now)
            or float(now) - self._last_good_at > ALCOVE_HOLD_SECONDS
        ):
            self._adopted = None
            self._narrow_candidate = None
            self._narrow_since = None
            return None
        return self._adopted

    def reset(self) -> None:
        self._adopted = None
        self._last_good_at = None
        self._narrow_candidate = None
        self._narrow_since = None


class AlcoveObservationBuffer:
    """One atomic latest result. The worker publishes and main thread consumes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: AlcoveObservation | None = None

    def publish(self, observation: AlcoveObservation) -> None:
        if not isinstance(observation, AlcoveObservation):
            return
        with self._lock:
            self._latest = observation

    def take(self) -> AlcoveObservation | None:
        with self._lock:
            observation = self._latest
            self._latest = None
        return observation

    def clear(self) -> None:
        with self._lock:
            self._latest = None


def _row_alpha_bounds(
    image: RawAlphaImage,
    row: int,
    *,
    byte_threshold: int,
) -> tuple[int, int] | None:
    start = row * image.bytes_per_row
    minimum: int | None = None
    maximum: int | None = None
    for x in range(image.width):
        offset = start + x * image.bytes_per_pixel + image.alpha_offset
        if image.pixels[offset] <= byte_threshold:
            continue
        if minimum is None:
            minimum = x
        maximum = x
    if minimum is None or maximum is None:
        return None
    return minimum, maximum


def scan_alpha_image(
    request: AlcoveCaptureRequest,
    image: RawAlphaImage,
    *,
    captured_at: float,
) -> AlcoveObservation | None:
    """Derive capsule geometry from provider bytes without constructing AppKit objects."""
    if (
        image.width <= 0
        or image.height <= 0
        or image.bytes_per_pixel <= 0
        or not (0 <= image.alpha_offset < image.bytes_per_pixel)
        or image.bytes_per_row < image.width * image.bytes_per_pixel
        or len(image.pixels) < image.bytes_per_row * image.height
        or not _finite(request.scale, request.window_width, request.menu_band_height)
        or request.scale <= 0.0
        or request.window_width <= 0.0
    ):
        return None
    byte_threshold = int(round(ALCOVE_ALPHA_THRESHOLD * 255.0))
    rows: list[tuple[int, int, int]] = []
    for y in range(image.height):
        bounds = _row_alpha_bounds(image, y, byte_threshold=byte_threshold)
        if bounds is not None:
            rows.append((y, bounds[0], bounds[1]))
    if not rows:
        return None
    first_y = rows[0][0]
    last_y = rows[-1][0]
    scale = image.width / request.window_width
    if not _finite(scale) or scale <= 0.0:
        return None
    height = (last_y - first_y + 1) / scale
    if height > request.menu_band_height * ALCOVE_MAX_BAND_FACTOR:
        return None
    minimum_x = min(row[1] for row in rows)
    maximum_x = max(row[2] for row in rows)
    width = (maximum_x - minimum_x + 1) / scale
    center_x = request.window_x + (minimum_x + maximum_x + 1) / (2.0 * scale)
    vertical_span = max(1, last_y - first_y + 1)
    confidence = min(1.0, len(rows) / vertical_span)

    sample_count = min(31, len(rows))
    if sample_count == 1:
        sampled = [rows[0]]
    else:
        sampled = [
            rows[round(index * (len(rows) - 1) / (sample_count - 1))]
            for index in range(sample_count)
        ]
    left_points = tuple(
        (request.window_x + left / scale, request.window_y + y / scale)
        for y, left, _right in sampled
    )
    right_points = tuple(
        (request.window_x + (right + 1) / scale, request.window_y + y / scale)
        for y, _left, right in reversed(sampled)
    )
    contour = left_points + right_points
    if contour:
        contour += (contour[0],)
    return AlcoveObservation(
        request_id=request.request_id,
        generation=request.generation,
        screen_id=request.screen_id,
        window_number=request.window_number,
        center_x=center_x,
        width=width,
        height=height,
        contour=contour,
        captured_at=float(captured_at),
        confidence=confidence,
    )


def _cg_alpha_offset(quartz: object, bitmap_info: int, bytes_per_pixel: int) -> int | None:
    alpha_mask = int(getattr(quartz, "kCGBitmapAlphaInfoMask", 0x1F))
    alpha_info = bitmap_info & alpha_mask
    first_values = {
        int(getattr(quartz, "kCGImageAlphaPremultipliedFirst", 2)),
        int(getattr(quartz, "kCGImageAlphaFirst", 4)),
        int(getattr(quartz, "kCGImageAlphaOnly", 7)),
    }
    last_values = {
        int(getattr(quartz, "kCGImageAlphaPremultipliedLast", 1)),
        int(getattr(quartz, "kCGImageAlphaLast", 3)),
    }
    if alpha_info not in first_values | last_values:
        return None
    order_mask = int(getattr(quartz, "kCGBitmapByteOrderMask", 0x7000))
    little = int(getattr(quartz, "kCGBitmapByteOrder32Little", 0x2000))
    is_little = bitmap_info & order_mask == little
    logical_first = alpha_info in first_values
    if bytes_per_pixel == 1:
        return 0
    if logical_first:
        return bytes_per_pixel - 1 if is_little else 0
    return 0 if is_little else bytes_per_pixel - 1


def capture_alcove_observation(
    request: AlcoveCaptureRequest,
) -> AlcoveObservation | None:
    """Capture one requested Alcove window as raw CGImage provider bytes."""
    try:
        import Quartz

        probe_height = request.menu_band_height * (ALCOVE_MAX_BAND_FACTOR + 0.2)
        rect = Quartz.CGRectMake(
            request.window_x,
            request.window_y,
            request.window_width,
            probe_height,
        )
        image = Quartz.CGWindowListCreateImage(
            rect,
            Quartz.kCGWindowListOptionIncludingWindow,
            request.window_number,
            Quartz.kCGWindowImageNominalResolution,
        )
        if image is None:
            return None
        width = int(Quartz.CGImageGetWidth(image))
        height = int(Quartz.CGImageGetHeight(image))
        bits_per_pixel = int(Quartz.CGImageGetBitsPerPixel(image))
        bytes_per_pixel = bits_per_pixel // 8
        bytes_per_row = int(Quartz.CGImageGetBytesPerRow(image))
        bitmap_info = int(Quartz.CGImageGetBitmapInfo(image))
        alpha_offset = _cg_alpha_offset(
            Quartz,
            bitmap_info,
            bytes_per_pixel,
        )
        if alpha_offset is None:
            return None
        provider = Quartz.CGImageGetDataProvider(image)
        data = Quartz.CGDataProviderCopyData(provider)
        raw = RawAlphaImage(
            width=width,
            height=height,
            bytes_per_row=bytes_per_row,
            bytes_per_pixel=bytes_per_pixel,
            alpha_offset=alpha_offset,
            pixels=bytes(data),
        )
        return scan_alpha_image(request, raw, captured_at=time.monotonic())
    except Exception:
        return None


class AlcoveObservationWorker:
    """One serial capture worker with one latest pending request."""

    def __init__(
        self,
        buffer: AlcoveObservationBuffer,
        *,
        capture: Callable[[AlcoveCaptureRequest], AlcoveObservation | None] | None = None,
    ) -> None:
        self._buffer = buffer
        self._capture = capture or capture_alcove_observation
        self._condition = threading.Condition()
        self._pending: AlcoveCaptureRequest | None = None
        self._accepting = True
        self._in_flight = False
        self.dropped_requests = 0
        self._thread = threading.Thread(
            target=self._run,
            name="sidepulse-alcove-observer",
            daemon=True,
        )
        self._thread.start()

    @property
    def pending_count(self) -> int:
        with self._condition:
            return int(self._pending is not None)

    @property
    def in_flight(self) -> bool:
        with self._condition:
            return self._in_flight

    def reconcile(self, request: AlcoveCaptureRequest) -> bool:
        if not isinstance(request, AlcoveCaptureRequest):
            return False
        with self._condition:
            if not self._accepting:
                return False
            if self._pending is not None:
                self.dropped_requests += 1
            self._pending = request
            self._condition.notify()
        return True

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and self._accepting:
                    self._condition.wait()
                if self._pending is None and not self._accepting:
                    return
                request = self._pending
                self._pending = None
                self._in_flight = True
            try:
                observation = self._capture(request)
                with self._condition:
                    publish = self._accepting
                if publish and isinstance(observation, AlcoveObservation):
                    self._buffer.publish(observation)
            except Exception:
                pass
            finally:
                with self._condition:
                    self._in_flight = False
                    self._condition.notify_all()

    def close(self, *, timeout_seconds: float = 0.25) -> bool:
        with self._condition:
            self._accepting = False
            self._pending = None
            self._condition.notify_all()
        self._thread.join(timeout=max(0.0, float(timeout_seconds)))
        return not self._thread.is_alive()
