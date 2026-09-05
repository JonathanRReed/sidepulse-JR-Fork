from __future__ import annotations

import math
import os
import platform
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

from .product_identity import PRODUCT_DISPLAY_NAME

ALCOVE_ALPHA_THRESHOLD = 0.08
ALCOVE_CONFIDENCE_MINIMUM = 0.75
ALCOVE_MAX_AGE_SECONDS = 2.0
ALCOVE_HOLD_SECONDS = 8.0
ALCOVE_MAX_WIDTH = 520.0
ALCOVE_MAX_BAND_FACTOR = 1.8
ALCOVE_MAX_CONTOUR_POINTS = 64
# CGWindowList reports an owner NAME, not a bundle id. One definition,
# here, because every other Alcove constant that got a second copy in
# another module is exactly how the last Alcove bug was introduced.
ALCOVE_OWNER_NAME: Final = "Alcove"
ALCOVE_BUNDLE_ID: Final = "com.henrikruscon.Alcove"


class AlcoveCaptureStatus(str, Enum):
    """Why the last capsule measurement did or did not produce geometry.

    Every one of these used to be the same value: ``None``. "Screen
    Recording was never granted", "Alcove is not running", "the window
    moved mid-capture" and "there was genuinely nothing there" are four
    completely different facts with four different fixes, and returning
    one bare ``None`` for all of them is why "Alcove mode doesn't seem to
    be working" could not be answered by anything -- not the UI, not
    doctor, not a single line in the log.
    """

    CAPTURED = "captured"
    SCREEN_RECORDING_DENIED = "screen_recording_denied"
    WINDOW_UNAVAILABLE = "window_unavailable"
    IMAGE_UNUSABLE = "image_unusable"
    CAPTURE_FAILED = "capture_failed"
    CAPTURE_API_UNAVAILABLE = "capture_api_unavailable"
    # Never returned by a capture: the user turned following off, or a
    # manual wing length is overriding it. Said out loud so a surface
    # never has to guess whether silence means "off" or "broken".
    NOT_FOLLOWING = "not_following"


class AlcoveConfidenceState(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    PERMISSION_DENIED = "permission_denied"
    DISCONNECTED = "disconnected"
    UNSUPPORTED = "unsupported"
    NOT_FOLLOWING = "not_following"
    RECOVERING = "recovering"


class AlcoveGeometryIntent(str, Enum):
    FOLLOW_LIVE = "follow_live"
    HOLD_LAST_GOOD = "hold_last_good"
    USE_SCREEN_BAR_GEOMETRY = "use_screen_bar_geometry"


class AlcoveMotionIntent(str, Enum):
    TRACK = "track"
    HOLD = "hold"
    SETTLE_ON_RECOVERY = "settle_on_recovery"
    STATIC = "static"


@dataclass(frozen=True, slots=True)
class AlcoveSilhouette:
    """Validated, immutable geometry crossing into the native view."""

    center_x: float
    width: float
    height: float
    contour: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        values = (self.center_x, self.width, self.height)
        if any(type(value) not in (int, float) or not math.isfinite(float(value)) for value in values):
            raise ValueError("silhouette dimensions must be finite numbers")
        if not (40.0 <= float(self.width) <= ALCOVE_MAX_WIDTH):
            raise ValueError("silhouette width is unsafe")
        if not (1.0 <= float(self.height) <= ALCOVE_MAX_WIDTH * ALCOVE_MAX_BAND_FACTOR):
            raise ValueError("silhouette height is unsafe")
        if type(self.contour) is not tuple or not (4 <= len(self.contour) <= ALCOVE_MAX_CONTOUR_POINTS):
            raise ValueError("silhouette contour must be a bounded tuple")
        if self.contour[0] != self.contour[-1]:
            raise ValueError("silhouette contour must be closed")
        normalized = []
        left = float(self.center_x) - float(self.width) / 2.0
        right = float(self.center_x) + float(self.width) / 2.0
        for point in self.contour:
            if type(point) is not tuple or len(point) != 2:
                raise ValueError("silhouette contour points must be tuples")
            x, y = point
            if any(type(value) not in (int, float) or not math.isfinite(float(value)) for value in point):
                raise ValueError("silhouette contour points must be finite")
            if not (left - 2.0 <= float(x) <= right + 2.0) or not (-2.0 <= float(y) <= float(self.height) + 2.0):
                raise ValueError("silhouette contour point is unsafe")
            normalized.append((float(x), float(y)))
        object.__setattr__(self, "center_x", float(self.center_x))
        object.__setattr__(self, "width", float(self.width))
        object.__setattr__(self, "height", float(self.height))
        object.__setattr__(self, "contour", tuple(normalized))

    def __eq__(self, other):
        if isinstance(other, tuple) and len(other) == 4:
            return (self.center_x, self.width, self.height, self.contour) == other
        if type(other) is not AlcoveSilhouette:
            return NotImplemented
        return (
            self.center_x,
            self.width,
            self.height,
            self.contour,
        ) == (other.center_x, other.width, other.height, other.contour)


@dataclass(frozen=True, slots=True)
class AlcoveConfidenceProjection:
    state: AlcoveConfidenceState
    message: str
    accessibility_value: str
    accessibility_help: str
    geometry_intent: AlcoveGeometryIntent
    motion_intent: AlcoveMotionIntent
    needs_permission_action: bool


_CONFIDENCE_COPY = MappingProxyType({
    AlcoveConfidenceState.FRESH: (
        "Live. Matching Alcove's width.", "Fresh",
        "The Screen Bar is following a current measurement.",
    ),
    AlcoveConfidenceState.STALE: (
        "Stale. {geometry} while JR-Bar checks again.", "Stale", "{help}",
    ),
    AlcoveConfidenceState.PERMISSION_DENIED: (
        "Screen Recording is off, so JR-Bar cannot see Alcove's capsule. The bar keeps its own size until you grant it.",
        "Permission denied", "Grant Screen Recording access to let the Screen Bar follow Alcove.",
    ),
    AlcoveConfidenceState.DISCONNECTED: (
        "Disconnected. Alcove is not showing a capsule, so the bar is using its own size.",
        "Disconnected", "Following resumes when an Alcove capsule is available.",
    ),
    AlcoveConfidenceState.UNSUPPORTED: (
        "Unsupported shape. The captured capsule could not be measured safely, so the bar is using its own size.",
        "Unsupported", "No unsafe geometry is used.",
    ),
    AlcoveConfidenceState.NOT_FOLLOWING: (
        "Not following Alcove. The bar uses its own size.",
        "Not following", "The setting or manual geometry controls the shape.",
    ),
    AlcoveConfidenceState.RECOVERING: (
        "Recovering. {geometry} until a fresh measurement arrives.", "Recovering", "{help}",
    ),
})


def _confidence_projection(
    state: AlcoveConfidenceState,
    *,
    held: bool = False,
) -> AlcoveConfidenceProjection:
    if state is AlcoveConfidenceState.STALE:
        geometry = "Holding the last trusted width" if held else "Using the Screen Bar's own size"
        help_text = "The held shape will expire and is not current." if held else "No current trusted shape is available."
        message, value, help_template = _CONFIDENCE_COPY[state]
        return AlcoveConfidenceProjection(state, message.format(geometry=geometry), value, help_template.format(help=help_text), AlcoveGeometryIntent.HOLD_LAST_GOOD if held else AlcoveGeometryIntent.USE_SCREEN_BAR_GEOMETRY, AlcoveMotionIntent.HOLD if held else AlcoveMotionIntent.STATIC, False)
    if state is AlcoveConfidenceState.RECOVERING:
        if held:
            message = "Recovering. Holding the last trusted width while measurement resumes."
            intent = AlcoveGeometryIntent.HOLD_LAST_GOOD
            motion = AlcoveMotionIntent.HOLD
            help_text = "The held shape is temporary."
        else:
            message = "Recovering. Using the Screen Bar's own size until a fresh measurement arrives."
            intent = AlcoveGeometryIntent.USE_SCREEN_BAR_GEOMETRY
            motion = AlcoveMotionIntent.STATIC
            help_text = "The app is waiting for fresh evidence."
        return AlcoveConfidenceProjection(state, message, "Recovering", help_text, intent, motion, False)
    message, value, help_text = _CONFIDENCE_COPY[state]
    if state is AlcoveConfidenceState.FRESH:
        return AlcoveConfidenceProjection(state, message, value, help_text, AlcoveGeometryIntent.FOLLOW_LIVE, AlcoveMotionIntent.TRACK, False)
    permission = state is AlcoveConfidenceState.PERMISSION_DENIED
    return AlcoveConfidenceProjection(state, message, value, help_text, AlcoveGeometryIntent.USE_SCREEN_BAR_GEOMETRY, AlcoveMotionIntent.STATIC, permission)


# What each outcome means to someone reading a settings pane. Content-free
# by construction: fixed product sentences, no paths, no window titles, no
# measurements.
ALCOVE_STATUS_MESSAGES: Final[dict[AlcoveCaptureStatus, str]] = {
    AlcoveCaptureStatus.CAPTURED: "Matching Alcove's width.",
    AlcoveCaptureStatus.SCREEN_RECORDING_DENIED: (
        f"Screen Recording is off, so {PRODUCT_DISPLAY_NAME} cannot see Alcove's capsule. "
        "The bar keeps its classic size until you grant it."
    ),
    AlcoveCaptureStatus.WINDOW_UNAVAILABLE: (
        "Alcove is not showing a capsule right now, so the bar is using its "
        "own size."
    ),
    AlcoveCaptureStatus.IMAGE_UNUSABLE: (
        "Alcove's capsule was captured but could not be measured, so the bar "
        "is using its own size."
    ),
    AlcoveCaptureStatus.CAPTURE_FAILED: (
        "Measuring Alcove's capsule failed, so the bar is using its own size."
    ),
    AlcoveCaptureStatus.CAPTURE_API_UNAVAILABLE: (
        "Alcove measurement is unavailable on this macOS installation, so the "
        "bar is using its own size."
    ),
    AlcoveCaptureStatus.NOT_FOLLOWING: (
        "Not following Alcove -- the bar uses its own size."
    ),
}

# One short line per outcome for the app log. Logged on TRANSITION only --
# see note_alcove_status -- because a per-frame line at 1.5s cadence is a
# log nobody reads, and zero lines is why this was invisible for a week.
ALCOVE_STATUS_LOG_LINES: Final[dict[AlcoveCaptureStatus, str]] = {
    AlcoveCaptureStatus.CAPTURED: "alcove: following the capsule",
    AlcoveCaptureStatus.SCREEN_RECORDING_DENIED: (
        "alcove: screen recording not granted -- not following"
    ),
    AlcoveCaptureStatus.WINDOW_UNAVAILABLE: "alcove: no capsule window on screen",
    AlcoveCaptureStatus.IMAGE_UNUSABLE: "alcove: captured, image unusable",
    AlcoveCaptureStatus.CAPTURE_FAILED: "alcove: capture failed",
    AlcoveCaptureStatus.CAPTURE_API_UNAVAILABLE: (
        "alcove: ScreenCaptureKit measurement unavailable"
    ),
    AlcoveCaptureStatus.NOT_FOLLOWING: "alcove: following is off",
}


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
class AlcoveCaptureOutcome:
    """One capture attempt: what happened, and the geometry if any.

    The pairing is enforced rather than documented -- an outcome cannot
    claim CAPTURED with nothing to show, and cannot smuggle geometry out
    of a failure. That invariant is the whole point of replacing None.
    """

    status: AlcoveCaptureStatus
    observation: AlcoveObservation | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not AlcoveCaptureStatus:
            raise ValueError("invalid alcove capture status")
        if self.status is AlcoveCaptureStatus.NOT_FOLLOWING:
            # A capture never decides whether following is enabled.
            raise ValueError("not_following is not a capture outcome")
        captured = self.status is AlcoveCaptureStatus.CAPTURED
        if captured != isinstance(self.observation, AlcoveObservation):
            raise ValueError("only a successful capture carries an observation")


@dataclass(frozen=True, slots=True)
class AlcoveStatusSnapshot:
    """The latest recorded outcome and when it was recorded."""

    status: AlcoveCaptureStatus
    updated_at: float
    geometry_age_seconds: float | None = None
    geometry_available: bool = False


@dataclass(frozen=True, slots=True)
class RawAlphaImage:
    width: int
    height: int
    bytes_per_row: int
    bytes_per_pixel: int
    alpha_offset: int
    pixels: bytes


# --- Screen Recording permission ----------------------------------------
# Following Alcove means capturing another application's window, which is
# Screen Recording. Nothing in this app ever preflighted it, so a denied
# permission was indistinguishable from "Alcove isn't running".
#
# Preflight is CHEAP and never prompts. Requesting is a different call and
# is reserved for an explicit user action (see request_screen_recording_access):
# a permission dialog nobody asked for, raised from a 1.5s background
# cadence, is its own bug.
SCREEN_RECORDING_PREFLIGHT_TTL_SECONDS: Final = 5.0
# A recorded outcome older than this is not a reading. Following runs at a
# ~1.5s cadence, so past half a minute the render path has stopped
# reporting -- and a surface must not present its last words as current.
ALCOVE_STATUS_MAX_AGE_SECONDS: Final = 30.0

_screen_recording_lock = threading.Lock()
_screen_recording_cache: tuple[float, bool | None] | None = None

_status_lock = threading.Lock()
_status_snapshot: AlcoveStatusSnapshot | None = None
_window_presence_lock = threading.Lock()
_window_presence_cache: tuple[float, bool | None] | None = None
_window_presence_refreshing = False
_window_presence_generation = 0
ALCOVE_WINDOW_PRESENCE_TTL_SECONDS: Final = 1.5


def _quartz():
    import Quartz

    return Quartz


def _preflight_screen_capture_access() -> bool | None:
    """CGPreflightScreenCaptureAccess, or None when it cannot be asked.

    None is NOT "denied": on a build without the symbol, or with Quartz
    unavailable, we genuinely do not know -- and reporting "not granted"
    to a user whose permission is fine is the same dishonesty in reverse.
    """
    try:
        preflight = getattr(_quartz(), "CGPreflightScreenCaptureAccess", None)
    except Exception:
        return None
    if preflight is None:
        return None
    try:
        return bool(preflight())
    except Exception:
        return None


def screen_recording_granted(
    *,
    force: bool = False,
    now: float | None = None,
    preflight: Callable[[], bool | None] | None = None,
) -> bool | None:
    """Cached preflight. True, False, or None when it cannot be determined.

    Cached because the answer is read on every reposition; time-bounded
    because the answer genuinely changes -- the user can grant or revoke
    it in System Settings while we are running, and a permanently cached
    "denied" would strand the feature exactly as a permanently cached
    None did.
    """
    global _screen_recording_cache
    moment = time.monotonic() if now is None else float(now)
    if not math.isfinite(moment):
        moment = time.monotonic()
    if not force:
        with _screen_recording_lock:
            cached = _screen_recording_cache
        if (
            cached is not None
            and 0.0 <= moment - cached[0] < SCREEN_RECORDING_PREFLIGHT_TTL_SECONDS
        ):
            return cached[1]
    probe = preflight or _preflight_screen_capture_access
    try:
        granted = probe()
    except Exception:
        granted = None
    if granted is not None:
        granted = bool(granted)
    with _screen_recording_lock:
        _screen_recording_cache = (moment, granted)
    return granted


def reset_screen_recording_cache() -> None:
    """Force the next preflight to ask the system again."""
    global _screen_recording_cache
    with _screen_recording_lock:
        _screen_recording_cache = None


def request_screen_recording_access(
    *,
    request: Callable[[], bool] | None = None,
) -> bool | None:
    """Prompt for Screen Recording. EXPLICIT USER ACTION ONLY.

    Never call this from the observer, from reposition, or from any timer.
    A modal permission dialog the user did not ask for is indistinguishable
    from malware behaviour and trains people to deny it.
    """
    caller = request
    if caller is None:
        try:
            caller = getattr(_quartz(), "CGRequestScreenCaptureAccess", None)
        except Exception:
            caller = None
    if caller is None:
        return None
    try:
        granted = bool(caller())
    except Exception:
        granted = None
    reset_screen_recording_cache()
    return granted


def _presence_from_window_info(info: object) -> bool | None:
    if info is None:
        return None
    try:
        return any(
            str(entry.get("kCGWindowOwnerName", "")) == ALCOVE_OWNER_NAME
            for entry in info
        )
    except Exception:
        return None


def _query_alcove_window_presence() -> bool | None:
    try:
        quartz = _quartz()
        info = quartz.CGWindowListCopyWindowInfo(
            quartz.kCGWindowListOptionOnScreenOnly,
            quartz.kCGNullWindowID,
        )
    except Exception:
        return None
    return _presence_from_window_info(info)


def _start_alcove_window_presence_refresh(task) -> None:
    threading.Thread(
        target=task,
        name="sidepulse-alcove-window-presence",
        daemon=True,
    ).start()


def reset_alcove_window_presence_cache() -> None:
    global _window_presence_cache, _window_presence_refreshing
    global _window_presence_generation
    with _window_presence_lock:
        _window_presence_cache = None
        _window_presence_refreshing = False
        _window_presence_generation += 1


def alcove_window_present(
    *,
    window_lister: Callable[[], object] | None = None,
    now: float | None = None,
) -> bool | None:
    """Cached Alcove presence, or None while the background probe warms."""
    if window_lister is not None:
        try:
            return _presence_from_window_info(window_lister())
        except Exception:
            return None

    global _window_presence_refreshing
    moment = time.monotonic() if now is None else float(now)
    task = None
    with _window_presence_lock:
        cached = _window_presence_cache
        fresh = (
            cached is not None
            and 0.0 <= moment - cached[0] < ALCOVE_WINDOW_PRESENCE_TTL_SECONDS
        )
        if not fresh and not _window_presence_refreshing:
            _window_presence_refreshing = True
            generation = _window_presence_generation

            def refresh() -> None:
                global _window_presence_cache, _window_presence_refreshing
                value = _query_alcove_window_presence()
                with _window_presence_lock:
                    if generation != _window_presence_generation:
                        return
                    _window_presence_cache = (moment, value)
                    _window_presence_refreshing = False

            task = refresh
        value = None if cached is None else cached[1]
    if task is not None:
        try:
            _start_alcove_window_presence_refresh(task)
        except Exception:
            with _window_presence_lock:
                if generation == _window_presence_generation:
                    _window_presence_refreshing = False
    return value


def alcove_follow_blocker(*, following: bool = True) -> AlcoveCaptureStatus | None:
    """The reason following cannot work right now, or None.

    None means "nothing visible is in the way" -- deliberately NOT a claim
    that a capture succeeded. Only a real capture may claim CAPTURED, which
    is why a surface with no live reading reports "unavailable" instead of
    inventing good news.
    """
    if not following:
        return AlcoveCaptureStatus.NOT_FOLLOWING
    if screen_recording_granted() is False:
        return AlcoveCaptureStatus.SCREEN_RECORDING_DENIED
    if alcove_window_present() is False:
        return AlcoveCaptureStatus.WINDOW_UNAVAILABLE
    return None


def note_alcove_status(
    status: AlcoveCaptureStatus,
    *,
    now: float | None = None,
    geometry_age_seconds: float | None = None,
    geometry_available: bool | None = None,
) -> bool:
    """Record the current outcome; True only when it CHANGED.

    The return value is the whole logging policy: callers log on a True,
    which is once per transition rather than once per frame.
    """
    global _status_snapshot
    if type(status) is not AlcoveCaptureStatus:
        return False
    moment = time.monotonic() if now is None else float(now)
    if not math.isfinite(moment):
        moment = time.monotonic()
    if geometry_age_seconds is not None and (
        not _finite(geometry_age_seconds) or float(geometry_age_seconds) < 0.0
    ):
        geometry_age_seconds = None
        geometry_available = False
    if geometry_available is None:
        geometry_available = status is AlcoveCaptureStatus.CAPTURED
    if geometry_available and geometry_age_seconds is None and status is AlcoveCaptureStatus.CAPTURED:
        geometry_age_seconds = 0.0
    if type(geometry_available) is not bool or geometry_age_seconds is None:
        geometry_available = False
    if not geometry_available:
        geometry_age_seconds = None
    with _status_lock:
        previous = _status_snapshot
        _status_snapshot = AlcoveStatusSnapshot(
            status=status,
            updated_at=moment,
            geometry_age_seconds=geometry_age_seconds,
            geometry_available=geometry_available,
        )
    return previous is None or previous.status is not status


def latest_alcove_status() -> AlcoveStatusSnapshot | None:
    with _status_lock:
        return _status_snapshot


def reset_alcove_status() -> None:
    global _status_snapshot
    with _status_lock:
        _status_snapshot = None


def _finite(*values: object) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in values)
    except (TypeError, ValueError, OverflowError):
        return False


def project_alcove_confidence(
    *,
    following: bool,
    snapshot: AlcoveStatusSnapshot | None,
    blocker: AlcoveCaptureStatus | None,
    now: float,
) -> AlcoveConfidenceProjection:
    """Resolve raw capture facts into the single seven-state product contract."""
    if type(following) is not bool or not _finite(now):
        return _confidence_projection(AlcoveConfidenceState.RECOVERING)
    if not following:
        return _confidence_projection(AlcoveConfidenceState.NOT_FOLLOWING)
    if blocker is AlcoveCaptureStatus.SCREEN_RECORDING_DENIED:
        return _confidence_projection(AlcoveConfidenceState.PERMISSION_DENIED)
    if blocker is AlcoveCaptureStatus.WINDOW_UNAVAILABLE:
        return _confidence_projection(AlcoveConfidenceState.DISCONNECTED)
    if blocker is not None and type(blocker) is not AlcoveCaptureStatus:
        return _confidence_projection(AlcoveConfidenceState.RECOVERING)
    if snapshot is None or type(snapshot.status) is not AlcoveCaptureStatus or type(snapshot.geometry_available) is not bool:
        return _confidence_projection(AlcoveConfidenceState.RECOVERING)
    status_age = float(now) - float(snapshot.updated_at)
    geometry_age = snapshot.geometry_age_seconds
    if not _finite(snapshot.updated_at, status_age) or status_age < 0.0:
        return _confidence_projection(AlcoveConfidenceState.RECOVERING)
    if geometry_age is not None and (not _finite(geometry_age) or float(geometry_age) < 0.0):
        return _confidence_projection(AlcoveConfidenceState.RECOVERING)
    if status_age > ALCOVE_STATUS_MAX_AGE_SECONDS:
        state = AlcoveConfidenceState.STALE
    elif snapshot.status is AlcoveCaptureStatus.SCREEN_RECORDING_DENIED:
        return _confidence_projection(AlcoveConfidenceState.PERMISSION_DENIED)
    elif snapshot.status is AlcoveCaptureStatus.WINDOW_UNAVAILABLE:
        return _confidence_projection(AlcoveConfidenceState.DISCONNECTED)
    elif snapshot.status is AlcoveCaptureStatus.IMAGE_UNUSABLE:
        state = AlcoveConfidenceState.UNSUPPORTED
    elif snapshot.status is AlcoveCaptureStatus.CAPTURE_API_UNAVAILABLE:
        state = AlcoveConfidenceState.UNSUPPORTED
    elif snapshot.status is AlcoveCaptureStatus.CAPTURE_FAILED:
        state = AlcoveConfidenceState.RECOVERING
    elif snapshot.status is not AlcoveCaptureStatus.CAPTURED:
        return _confidence_projection(AlcoveConfidenceState.RECOVERING)
    elif not snapshot.geometry_available or geometry_age is None:
        state = AlcoveConfidenceState.STALE
    elif float(geometry_age) + status_age <= ALCOVE_MAX_AGE_SECONDS:
        state = AlcoveConfidenceState.FRESH
    else:
        state = AlcoveConfidenceState.STALE
    held = bool(snapshot.geometry_available and geometry_age is not None and float(geometry_age) + status_age <= ALCOVE_HOLD_SECONDS)
    return _confidence_projection(state, held=held if state in (AlcoveConfidenceState.STALE, AlcoveConfidenceState.RECOVERING) else False)


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
        # Asymmetry is the law here, and it points the OTHER way from the
        # old 3-second narrow damping: a bracket narrower than the capsule
        # paints INSIDE Alcove's black and is invisible; a bracket wider
        # than the capsule paints a glowing sliver on the wallpaper --
        # the "corners past the capsule" seen on every collapse. So a
        # narrower measurement is adopted IMMEDIATELY; only sub-point
        # jitter is ignored either way.
        if adopted is None or abs(observation.width - adopted.width) > 0.5:
            self._adopted = observation
        return True

    def current(self, *, now: float) -> AlcoveObservation | None:
        if (
            self._adopted is None
            or self._last_good_at is None
            or not _finite(now)
            or float(now) - self._last_good_at > ALCOVE_HOLD_SECONDS
        ):
            self._adopted = None
            return None
        return self._adopted

    def last_good_age(self, *, now: float) -> float | None:
        if self._last_good_at is None or not _finite(now):
            return None
        age = float(now) - self._last_good_at
        return age if age >= 0.0 else None

    def reset(self) -> None:
        self._adopted = None
        self._last_good_at = None


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


_CAPTURE_API_UNAVAILABLE = object()
_CAPTURE_FAILED = object()
_screen_capture_kit_lock = threading.Lock()
_screen_capture_kit_classes: tuple[object, object, object, object] | None = None
_screen_capture_kit_load_attempted = False


def _macos_major_version() -> int:
    try:
        version = platform.mac_ver()[0]
        if version:
            return int(version.split(".", 1)[0])
    except (TypeError, ValueError, OSError):
        pass
    try:
        if os.uname().sysname == "Darwin":
            return max(0, int(os.uname().release.split(".", 1)[0]) - 9)
    except (AttributeError, TypeError, ValueError, OSError):
        pass
    return 0


def _load_screen_capture_kit():
    """Load only the ScreenCaptureKit surface used by Alcove measurement."""
    global _screen_capture_kit_classes, _screen_capture_kit_load_attempted
    with _screen_capture_kit_lock:
        if _screen_capture_kit_load_attempted:
            return _screen_capture_kit_classes
        _screen_capture_kit_load_attempted = True
        try:
            import AppKit
            import objc

            bundle = AppKit.NSBundle.bundleWithPath_(
                "/System/Library/Frameworks/ScreenCaptureKit.framework"
            )
            if bundle is None or not bundle.load():
                return None
            objc.registerMetaDataForSelector(
                b"SCShareableContent",
                b"getShareableContentExcludingDesktopWindows:onScreenWindowsOnly:completionHandler:",
                {
                    "arguments": {
                        4: {
                            "callable": {
                                "retval": {"type": b"v"},
                                "arguments": {
                                    0: {"type": b"^v"},
                                    1: {"type": b"@"},
                                    2: {"type": b"@"},
                                },
                            }
                        }
                    }
                },
            )
            objc.registerMetaDataForSelector(
                b"SCScreenshotManager",
                b"captureImageWithFilter:configuration:completionHandler:",
                {
                    "arguments": {
                        4: {
                            "callable": {
                                "retval": {"type": b"v"},
                                "arguments": {
                                    0: {"type": b"^v"},
                                    1: {"type": b"^{CGImage=}"},
                                    2: {"type": b"@"},
                                },
                            }
                        }
                    }
                },
            )
            _screen_capture_kit_classes = (
                objc.lookUpClass("SCShareableContent"),
                objc.lookUpClass("SCContentFilter"),
                objc.lookUpClass("SCStreamConfiguration"),
                objc.lookUpClass("SCScreenshotManager"),
            )
        except Exception:
            _screen_capture_kit_classes = None
        return _screen_capture_kit_classes


def _screen_capture_kit_image(
    request: AlcoveCaptureRequest,
    *,
    api=None,
    timeout_seconds: float = 2.0,
):
    """Capture the selected Alcove window with ScreenCaptureKit.

    Pixels remain in memory only long enough for the existing alpha scanner;
    this bridge does not persist, encode, or log them.
    """
    classes = _load_screen_capture_kit() if api is None else api
    if classes is None:
        return _CAPTURE_API_UNAVAILABLE
    shareable, content_filter, configuration, screenshot_manager = classes
    content_result: dict[str, object] = {}
    content_ready = threading.Event()

    def content_callback(content, error) -> None:
        content_result["content"] = content
        content_result["error"] = error
        content_ready.set()

    try:
        shareable.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            True,
            True,
            content_callback,
        )
        if not content_ready.wait(max(0.05, float(timeout_seconds))):
            return _CAPTURE_FAILED
        content = content_result.get("content")
        if content is None or content_result.get("error") is not None:
            return _CAPTURE_FAILED
        target = None
        for window in content.windows() or ():
            if int(window.windowID()) != request.window_number:
                continue
            application = window.owningApplication()
            if application is None or str(application.bundleIdentifier()) != ALCOVE_BUNDLE_ID:
                continue
            target = window
            break
        if target is None:
            return None

        capture_filter = content_filter.alloc().initWithDesktopIndependentWindow_(target)
        capture_configuration = configuration.alloc().init()
        pixel_width = max(1, int(round(request.window_width * request.scale)))
        probe_height = request.menu_band_height * (ALCOVE_MAX_BAND_FACTOR + 0.2)
        pixel_height = max(1, int(round(probe_height * request.scale)))
        capture_configuration.setWidth_(pixel_width)
        capture_configuration.setHeight_(pixel_height)
        capture_configuration.setShowsCursor_(False)
        try:
            import Quartz

            capture_configuration.setSourceRect_(
                Quartz.CGRectMake(0.0, 0.0, request.window_width, probe_height)
            )
        except Exception:
            return _CAPTURE_FAILED

        image_result: dict[str, object] = {}
        image_ready = threading.Event()

        def image_callback(image, error) -> None:
            image_result["image"] = image
            image_result["error"] = error
            image_ready.set()

        screenshot_manager.captureImageWithFilter_configuration_completionHandler_(
            capture_filter,
            capture_configuration,
            image_callback,
        )
        if not image_ready.wait(max(0.05, float(timeout_seconds))):
            return _CAPTURE_FAILED
        if image_result.get("error") is not None:
            return _CAPTURE_FAILED
        return image_result.get("image")
    except Exception:
        return _CAPTURE_FAILED


def capture_display_region_image(
    *,
    display_id: int,
    source_width: float,
    source_height: float,
    pixel_width: int,
    pixel_height: int,
    excluded_window_number: int = 0,
    api=None,
    timeout_seconds: float = 2.0,
):
    """Capture one display-local rectangle with ScreenCaptureKit.

    This is intentionally synchronous only to its caller. The Screen Bar's
    notch probe calls it from a daemon worker, never from AppKit layout. The
    returned CGImage remains in memory and is neither encoded nor logged.
    """
    if (
        int(display_id) <= 0
        or not math.isfinite(float(source_width))
        or not math.isfinite(float(source_height))
        or float(source_width) <= 0.0
        or float(source_height) <= 0.0
        or int(pixel_width) <= 0
        or int(pixel_height) <= 0
    ):
        return None
    classes = _load_screen_capture_kit() if api is None else api
    if classes is None:
        return None
    shareable, content_filter, configuration, screenshot_manager = classes
    content_result: dict[str, object] = {}
    content_ready = threading.Event()

    def content_callback(content, error) -> None:
        content_result["content"] = content
        content_result["error"] = error
        content_ready.set()

    try:
        shareable.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            False,
            True,
            content_callback,
        )
        if not content_ready.wait(max(0.05, float(timeout_seconds))):
            return None
        content = content_result.get("content")
        if content is None or content_result.get("error") is not None:
            return None
        display = next(
            (
                candidate
                for candidate in content.displays() or ()
                if int(candidate.displayID()) == int(display_id)
            ),
            None,
        )
        if display is None:
            return None
        excluded = []
        if int(excluded_window_number) > 0:
            excluded = [
                window
                for window in content.windows() or ()
                if int(window.windowID()) == int(excluded_window_number)
            ]
        capture_filter = content_filter.alloc().initWithDisplay_excludingWindows_(
            display,
            excluded,
        )
        capture_configuration = configuration.alloc().init()
        capture_configuration.setWidth_(int(pixel_width))
        capture_configuration.setHeight_(int(pixel_height))
        capture_configuration.setShowsCursor_(False)
        import Quartz

        capture_configuration.setSourceRect_(
            Quartz.CGRectMake(
                0.0,
                0.0,
                float(source_width),
                float(source_height),
            )
        )
        image_result: dict[str, object] = {}
        image_ready = threading.Event()

        def image_callback(image, error) -> None:
            image_result["image"] = image
            image_result["error"] = error
            image_ready.set()

        screenshot_manager.captureImageWithFilter_configuration_completionHandler_(
            capture_filter,
            capture_configuration,
            image_callback,
        )
        if not image_ready.wait(max(0.05, float(timeout_seconds))):
            return None
        if image_result.get("error") is not None:
            return None
        return image_result.get("image")
    except Exception:
        return None


def _legacy_capture_alcove_image(request: AlcoveCaptureRequest, quartz: object):
    """Pre-macOS-15 compatibility path for the API Apple made obsolete."""
    probe_height = request.menu_band_height * (ALCOVE_MAX_BAND_FACTOR + 0.2)
    rect = quartz.CGRectMake(
        request.window_x,
        request.window_y,
        request.window_width,
        probe_height,
    )
    return quartz.CGWindowListCreateImage(
        rect,
        quartz.kCGWindowListOptionIncludingWindow,
        request.window_number,
        quartz.kCGWindowImageNominalResolution,
    )


def _capture_alcove_image(request: AlcoveCaptureRequest, quartz: object):
    if _macos_major_version() >= 15:
        return _screen_capture_kit_image(request)
    return _legacy_capture_alcove_image(request, quartz)


def capture_alcove_observation(
    request: AlcoveCaptureRequest,
    *,
    screen_recording: bool | None = None,
    image_capture=None,
) -> AlcoveCaptureOutcome:
    """Capture one requested Alcove window, and always say what happened.

    This returned a bare ``None`` for four unrelated failures and for one
    success-with-nothing-in-it. The catch-all is still here -- a capture
    must never raise into the render path -- but it is now the LAST
    branch, not the only one, and it carries its own distinct status
    instead of impersonating the other four.
    """
    try:
        granted = (
            screen_recording_granted() if screen_recording is None else screen_recording
        )
        if granted is False:
            # Do not even attempt the capture: without permission macOS
            # hands back the desktop rather than the window, which scans
            # as "nothing there" and would be reported as a missing
            # capsule. Preflight is what makes the two distinguishable.
            return AlcoveCaptureOutcome(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED)

        import Quartz

        capture = image_capture or _capture_alcove_image
        image = capture(request, Quartz)
        if image is _CAPTURE_API_UNAVAILABLE:
            return AlcoveCaptureOutcome(AlcoveCaptureStatus.CAPTURE_API_UNAVAILABLE)
        if image is _CAPTURE_FAILED:
            return AlcoveCaptureOutcome(AlcoveCaptureStatus.CAPTURE_FAILED)
        if image is None:
            # The window went away between selection on main and capture
            # here -- Alcove quit, collapsed, or moved to another Space.
            return AlcoveCaptureOutcome(AlcoveCaptureStatus.WINDOW_UNAVAILABLE)
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
            return AlcoveCaptureOutcome(AlcoveCaptureStatus.IMAGE_UNUSABLE)
        provider = Quartz.CGImageGetDataProvider(image)
        data = None if provider is None else Quartz.CGDataProviderCopyData(provider)
        if data is None:
            return AlcoveCaptureOutcome(AlcoveCaptureStatus.IMAGE_UNUSABLE)
        raw = RawAlphaImage(
            width=width,
            height=height,
            bytes_per_row=bytes_per_row,
            bytes_per_pixel=bytes_per_pixel,
            alpha_offset=alpha_offset,
            pixels=bytes(data),
        )
        observation = scan_alpha_image(request, raw, captured_at=time.monotonic())
        if observation is None:
            # A real image with no measurable capsule in it: fully
            # transparent (the usual shape of a silently denied capture),
            # or geometry the validator refuses.
            return AlcoveCaptureOutcome(AlcoveCaptureStatus.IMAGE_UNUSABLE)
        return AlcoveCaptureOutcome(AlcoveCaptureStatus.CAPTURED, observation)
    except Exception:
        # Bounded and non-raising, as before -- but no longer the same
        # value as every other ending.
        return AlcoveCaptureOutcome(AlcoveCaptureStatus.CAPTURE_FAILED)


def normalized_capture_outcome(result: object) -> AlcoveCaptureOutcome:
    """Accept an outcome, a bare observation, or anything else.

    Injected captures (tests, and any future provider) may still return a
    plain observation. Anything that is neither is a capture that declined
    to say why, which is precisely CAPTURE_FAILED -- never a quiet success
    and never an invented reason.
    """
    if type(result) is AlcoveCaptureOutcome:
        return result
    if isinstance(result, AlcoveObservation):
        return AlcoveCaptureOutcome(AlcoveCaptureStatus.CAPTURED, result)
    return AlcoveCaptureOutcome(AlcoveCaptureStatus.CAPTURE_FAILED)


class AlcoveObservationWorker:
    """One serial capture worker with one latest pending request."""

    def __init__(
        self,
        buffer: AlcoveObservationBuffer,
        *,
        capture: Callable[[AlcoveCaptureRequest], object] | None = None,
    ) -> None:
        self._buffer = buffer
        self._capture = capture or capture_alcove_observation
        self._condition = threading.Condition()
        self._pending: AlcoveCaptureRequest | None = None
        self._accepting = True
        self._in_flight = False
        self._last_status: AlcoveCaptureStatus | None = None
        self._last_status_identity: tuple[str, int, int] | None = None
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

    @property
    def last_status(self) -> AlcoveCaptureStatus | None:
        """The most recent capture's reason, or None before the first one.

        The buffer only carries successes, so without this the main
        thread can see that nothing arrived and still not know why --
        which is the original defect, moved one layer out.
        """
        with self._condition:
            return self._last_status

    @property
    def last_status_identity(self) -> tuple[str, int, int] | None:
        with self._condition:
            return self._last_status_identity

    def wait_idle(self, *, timeout_seconds: float) -> bool:
        """Wait until no capture is running or queued, up to a bound."""
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        with self._condition:
            while self._in_flight or self._pending is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return True

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
                outcome = normalized_capture_outcome(self._capture(request))
            except Exception:
                outcome = AlcoveCaptureOutcome(AlcoveCaptureStatus.CAPTURE_FAILED)
            try:
                with self._condition:
                    publish = self._accepting
                    if publish:
                        # A result that arrives after close belongs to a
                        # torn-down generation: it must not publish and
                        # must not overwrite the status either.
                        self._last_status = outcome.status
                        self._last_status_identity = (
                            request.screen_id,
                            request.window_number,
                            request.generation,
                        )
                if publish and outcome.observation is not None:
                    self._buffer.publish(outcome.observation)
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
