from __future__ import annotations

import math
import time

import objc
import Quartz
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSColorSpace,
    NSCursor,
    NSGradient,
    NSGraphicsContext,
    NSScreen,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
    NSWorkspace,
    NSWorkspaceScreensDidSleepNotification,
    NSWorkspaceScreensDidWakeNotification,
)
from Foundation import NSObject, NSRunLoop, NSRunLoopCommonModes
from Quartz import CGContextFillRect, CGContextSetRGBFillColor

from .accessibility_display import AccessibilityDisplayPreferences
from .alcove_observation import (
    AlcoveCaptureRequest,
    AlcoveObservationBuffer,
    AlcoveObservationReducer,
    AlcoveObservationWorker,
)
from .led_status import LedDisplayState, normalize_brightness, program_for_display_state
from .led_wasm import LedWasmUnavailableError, SdLedWasmController
from .presentation_policy import MotionClass
from .presentation_scheduler import PresentationSchedulerInputs
from .render_policy import (
    ACTIVE_RENDER_FPS,
    STATIC_WATCH_FPS,
    BoundedRenderCache,
    GlowGeometryKey,
    GlowPaintKey,
    RenderDriverKind,
    alcove_bracket_corner_radius,
    choose_render_schedule,
    rounded_silhouette,
    runtime_render_environment,
)
from .screen_bar_pipeline import (
    PresentationTick,
    SamplerCommand,
    ScreenBarSampler,
    TwoSampleBuffer,
    display_colors_for_tick,
    presentation_time,
)

VIRTUAL_DEVICE_ID = "virtual:status-bar"
VIRTUAL_DEVICE_NAME = "Screen Bar"
LED_COUNT = 8
WINDOW_WIDTH = 220.0
FALLBACK_NOTCH_DEPTH = 32.0
LED_BAND_HEIGHT = 5.0
WINDOW_HEIGHT = FALLBACK_NOTCH_DEPTH + LED_BAND_HEIGHT
NOTCH_BOTTOM_RADIUS = 8.0

# Alcove (https://henrikruscon.com) renders a Dynamic-Island-style notch
# overlay and has no published compatibility API. Rather than compete with
# it for the same black backdrop shape at the same position, SidePulse
# drops its own camera-housing rectangle and glow layers entirely and draws
# just a thin colored accent line at the same position -- reads as a status
# accent under Alcove's own shape, not a second competing widget. See
# docs/superpowers/specs/2026-08-10-agent-color-customizer-design.md
# section 6 (superseded from an earlier offset-pill attempt that read as
# disconnected/floating rather than integrated).
ALCOVE_BUNDLE_ID = "com.henrikruscon.Alcove"
COMPACT_ACCENT_HEIGHT = 2.5
# NSStatusWindowLevel without another SDK dependency; and the level the
# wrap bracket rides at while Alcove is running -- kCGMaximumWindowLevel,
# one above Alcove's own near-maximum overlay, so the bracket's glow
# stays visible around the hardware notch instead of buried under
# Alcove's opaque backdrop. The window ignores mouse events, so being
# top-most is purely visual.
STATUS_WINDOW_LEVEL = 25
ABOVE_ALCOVE_WINDOW_LEVEL = 2147483630

# "Wrap the menu bar" extends the LED glow beyond the notch's own width,
# reaching toward the menu bar's edges on both sides -- opt-in (see
# AgentMonitorSettings.virtual_status_device_wraps_menu_bar) since the safe
# amount of room genuinely depends on how cluttered the user's own menu bar
# is (app menu titles on the left, status icons on the right). Rather than
# guess a fixed width, wing_width_for_screen() measures the real per-user
# gap the system itself reports and stays comfortably inside it.
WING_MAX_WIDTH = 110.0
# Automatic wings are a TIGHT hug: the risers land essentially flush
# with the notch's own corners and the bar reads as the size of the
# actual notch. Auto wings at the full 110pt made the bracket twice
# the notch's width -- "nowhere near the correct size" -- 28pt still
# read as overhang, and 10pt "could be a little tighter". At 6pt the
# riser IS the wing. Anyone who wants longer wings has the Wing
# Length slider (up to 400pt).
# 14, not 6: the pixel-measured notch width wobbles run to run (dark
# wallpapers and the bar's own glow skew the pure-black scan by up to
# ~40pt), and with 6pt wings a wide measurement hid the ENTIRE bar
# behind the physical notch -- "I don't see the screen bar anymore".
WING_AUTO_LENGTH = 14.0

# --- Alcove capsule following -------------------------------------------
# The visible capsule is measured from Alcove's OWN window image (alpha
# channel), so the bracket can widen to embrace an expanded live
# activity. See measured_alcove_capsule_width for why this succeeds
# where two window-bounds attempts failed.
ALCOVE_CAPSULE_ALPHA_THRESHOLD = 0.08
# A lit region taller than this many menu-band heights is Alcove's
# hover panel, not the capsule -- never size to it (the ballooning
# failure both reverted attempts hit).
ALCOVE_CAPSULE_MAX_BAND_FACTOR = 1.8
# Breathing room past the capsule's edge on each side.
ALCOVE_CAPSULE_MARGIN = 2.0
# Hard ceiling on the followed width, whatever the measurement says.
ALCOVE_FOLLOW_MAX_WIDTH = 520.0
# Re-measure at most this often, whatever cadence reposition() runs at
# (the sync path can drive it at 4Hz during agent-event bursts).
ALCOVE_MEASURE_TTL_SECONDS = 1.5
# Widen instantly; adopt a NARROWER capsule only after it has held for
# this long (media pills flicker during track changes).
ALCOVE_NARROW_AFTER_SECONDS = 3.0
# Keep the last good width through brief no-reading gaps (capsule
# mid-transition, hover panel open) before falling back to hardware.
ALCOVE_HOLD_SECONDS = 8.0
WING_SAFETY_MARGIN = 28.0
WING_MIN_USABLE = 24.0
# A wing that's just a flat horizontal strip fading sideways reads as a
# line trailing off into nothing, not as light that's actually reaching
# and wrapping the menu bar's own corner. A soft vertical riser at each
# wing's *outer* edge -- brightest where it meets the horizontal glow,
# fading upward -- turns the shape into a bracket ("|____|") instead,
# which is what "extend the glow along the menu bar" is actually meant
# to look like.
WING_RISER_WIDTH = 6.0
# Keep Alcove's accent away from the transparent window boundary. Core
# Graphics clips bloom and stroke antialiasing at a window edge, which made
# both corner risers look squared off or missing on the installed app.
ALCOVE_ACCENT_EDGE_INSET = 6.0
# In wings-only (Alcove) mode the bracket is SidePulse's entire visible
# presence, so its horizontal stroke keeps at least this fraction of the
# edge color all the way out to where it meets the riser.
WINGS_ONLY_TAPER_FLOOR = 0.55
WING_RISER_SOLID_FRACTION = 0.45
LED_BLEND_RADIUS_LEDS = 1.5
BLEND_COLUMN_WIDTH = 2.0
LED_GLOW_HEIGHT = 11.0
LED_CORE_BOOST = 1.22
LED_HOTLINE_BOOST = 1.46
LED_GAMMA = 0.86
# Native gradient allocation is much more expensive than selecting a nearby
# cached color. Five bits per channel bounds nearest-bucket error to roughly
# four 8-bit channel steps on this soft peripheral glow while allowing a
# smooth multi-frame transition to reuse gradients materially.
RISER_GRADIENT_COLOR_LEVELS = 32
# Active motion runs at display cadence. The adaptive policy lowers this
# deterministically for power or thermal pressure and uses a slow watcher for
# static output. Cached geometry and native gradients keep 60 Hz from
# multiplying the old bridged fill workload.
FRAME_RATE = ACTIVE_RENDER_FPS
IDLE_FRAME_RATE = STATIC_WATCH_FPS
# Mirrored module-level for the frame-rate contract test.
TARGET_SAMPLE_INTERVAL_VIEW_CONTRACT = 1.0 / FRAME_RATE
FRAME_INTERVAL = 1.0 / FRAME_RATE
REPOSITION_INTERVAL_SECONDS = 2.0
SAMPLER_CLOSE_TIMEOUT_SECONDS = 0.25
_OFF_COLORS = ((0.0, 0.0, 0.0, 0.0),) * LED_COUNT


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000.0)


def virtual_display_state_for_projection(projection, active_signal=None) -> LedDisplayState:
    """Screen Bar state adapter for the shared attention projection."""
    from .led_status import display_state_for_projection

    return display_state_for_projection(projection, active_signal)


def measured_notch_bounds(screen, below_window_number: int = 0):
    """The hardware notch's EXACT horizontal bounds, measured from the
    screen's own pixels: the notch is the only pure-black (0, 0, 0) run
    in the menu bar's top rows (the menu bar itself never composites to
    true black -- measured (1, 1, 1) even over a black wallpaper).
    Verified against the 14-inch panel: pixels say 663..849 (186.0pt)
    where the aux-area slot said 185 -- this is the ground truth the
    user actually sees, needs no per-model lookup table, and works on
    future Macs unseen. ``below_window_number`` excludes SidePulse's
    own bar window from the composite so it can never contaminate the
    measurement. Returns (x, width) in points, or None (no notch, or
    anything unexpected -- callers fall back to the aux-area slot)."""
    try:
        import Quartz
        from AppKit import NSBitmapImageRep

        frame = screen.frame()
        rect = Quartz.CGRectMake(float(frame.origin.x), 0.0, float(frame.size.width), 2.0)
        option = (
            Quartz.kCGWindowListOptionOnScreenBelowWindow
            if below_window_number
            else Quartz.kCGWindowListOptionOnScreenOnly
        )
        image = Quartz.CGWindowListCreateImage(
            rect, option, int(below_window_number), Quartz.kCGWindowImageNominalResolution
        )
        if image is None:
            return None
        rep = NSBitmapImageRep.alloc().initWithCGImage_(image)
        if rep is None:
            return None
        width_px = int(rep.pixelsWide())
        if width_px <= 0:
            return None
        scale = width_px / float(frame.size.width)
        y = min(1, int(rep.pixelsHigh()) - 1)
        center_px = width_px / 2.0
        run_start = None
        best = None
        for x in range(width_px):
            color = rep.colorAtX_y_(x, y)
            is_black = (
                color is not None
                and color.redComponent() == 0.0
                and color.greenComponent() == 0.0
                and color.blueComponent() == 0.0
            )
            if is_black:
                if run_start is None:
                    run_start = x
                continue
            if run_start is not None:
                if run_start <= center_px <= x:
                    best = (run_start, x)
                run_start = None
        if run_start is not None and run_start <= center_px:
            best = (run_start, width_px)
        if best is None:
            return None
        notch_x = best[0] / scale
        notch_width = (best[1] - best[0]) / scale
        # Sanity band: MacBook notches live in roughly 150-260pt; a run
        # outside that is a black wallpaper edge or a screen-saver, not
        # the notch.
        if not (120.0 <= notch_width <= 320.0):
            return None
        return (float(frame.origin.x) + notch_x, notch_width)
    except Exception:
        return None


def slot_width_for_screen(screen) -> float:
    """Use the system-reported gap between the notch's left and right areas."""
    try:
        left = screen.auxiliaryTopLeftArea()
        right = screen.auxiliaryTopRightArea()
        left_edge = left.origin.x + left.size.width
        width = right.origin.x - left_edge
        if width >= 120.0:
            return max(180.0, min(320.0, float(width)))
    except Exception:
        pass
    return WINDOW_WIDTH


def wing_width_for_screen(screen, notch_width: float) -> float:
    """How far the LED glow can safely extend beyond each side of the
    notch, toward the menu bar's own edges.

    Uses the same system-reported auxiliary areas as slot_width_for_screen
    (the actual, per-user gap beside the notch) rather than a fixed
    constant -- a user with a spartan menu bar and one with a dozen
    menu-bar apps crammed in have very different amounts of real safe
    space, and guessing wrong means drawing over an app menu's title or a
    status icon. Falls back to 0.0 (no wing) on any screen that doesn't
    report a notch gap at all -- external displays and non-notched
    MacBooks included, where "wrap the menu bar" has nothing to attach to.
    """
    try:
        left = screen.auxiliaryTopLeftArea()
        right = screen.auxiliaryTopRightArea()
    except Exception:
        return 0.0
    if left.size.width <= 0.0 or right.size.width <= 0.0:
        return 0.0
    # Each auxiliary area spans from the notch's edge to the safe-area
    # boundary; the *gap* itself (slot_width_for_screen's basis) already
    # consumes the middle, so it's not double counted here -- this reads
    # each area's own remaining width after backing off a safety margin
    # from its outer edge, where a menu extra or status icon is most
    # likely to start. When the requested gap is WIDER than the hardware
    # slot (Alcove-measured or user-set), the widening eats into each
    # side's room symmetrically -- without subtracting that overhang,
    # the auto wing extended over app menus and status icons.
    hardware_slot = right.origin.x - (left.origin.x + left.size.width)
    overhang = 0.0
    if hardware_slot > 0.0 and float(notch_width) > hardware_slot:
        overhang = (float(notch_width) - hardware_slot) / 2.0
    left_room = left.size.width - WING_SAFETY_MARGIN - overhang
    right_room = right.size.width - WING_SAFETY_MARGIN - overhang
    room = min(left_room, right_room)
    if room < WING_MIN_USABLE:
        return 0.0
    return max(0.0, min(WING_AUTO_LENGTH, room))


def notch_depth_for_screen(screen) -> float:
    try:
        notch_depth = float(screen.safeAreaInsets().top)
    except Exception:
        return 0.0
    return notch_depth if notch_depth >= 1.0 else 0.0


def screen_has_notch(screen) -> bool:
    return notch_depth_for_screen(screen) > 0.0


def window_height_for_notch_depth(notch_depth: float) -> float:
    return max(0.0, float(notch_depth)) + LED_BAND_HEIGHT


def virtual_window_frame_for_screen(
    screen,
    *,
    wrap_menu_bar: bool = False,
    gap_width: float | None = None,
    wing_length: float | None = None,
    alcove_total_width: float | None = None,
    alcove_center_x: float | None = None,
):
    """The Screen Bar window's full frame. With ``wrap_menu_bar`` on, the
    window widens symmetrically to include room for the wing glow on each
    side -- centered on the notch either way. ``gap_width`` and
    ``wing_length`` are the user's manual geometry (None = Automatic):
    the durable answer to notch companions like Alcove whose visual
    width changes at runtime, and to future Macs with different notches.
    """
    frame = screen.frame()
    notch_width = float(gap_width) if gap_width else slot_width_for_screen(screen)
    if wrap_menu_bar:
        if wing_length is not None:
            # A user-set wing can be short but never invisibly zero --
            # a zero wing in bracket mode made the whole bar vanish.
            wing = max(float(wing_length), WING_MIN_USABLE)
        else:
            wing = wing_width_for_screen(screen, notch_width)
    else:
        wing = 0.0
    # Alcove following (wings-only mode): the bar tracks the measured
    # capsule EXACTLY -- growing past the hardware notch for an
    # expanded live activity and narrowing back below even a manual
    # gap once it collapses ("match Alcove" means match, not "at
    # least"). A manual wing length still wins upstream (the tracker
    # never runs); with no reading this stays None and classic
    # geometry holds. The screen caps below still apply.
    if wrap_menu_bar and alcove_total_width is not None:
        target = max(140.0, float(alcove_total_width))
        if target >= notch_width:
            wing = (target - notch_width) / 2.0
        else:
            notch_width = target
            wing = 0.0
    # Never wider than the screen itself, whatever the user typed.
    notch_width = min(notch_width, frame.size.width - 8.0)
    wing = min(wing, max(0.0, (frame.size.width - notch_width) / 2.0 - 4.0))
    width = notch_width + 2.0 * wing
    height = window_height_for_notch_depth(notch_depth_for_screen(screen))
    center_x = (
        float(alcove_center_x)
        if alcove_total_width is not None and alcove_center_x is not None
        else float(frame.origin.x) + float(frame.size.width) / 2.0
    )
    center_x = min(
        float(frame.origin.x) + float(frame.size.width) - width / 2.0,
        max(float(frame.origin.x) + width / 2.0, center_x),
    )
    x = center_x - width / 2.0
    y = frame.origin.y + frame.size.height - height
    return ((x, y), (width, height))


def is_alcove_running() -> bool:
    try:
        apps = NSWorkspace.sharedWorkspace().runningApplications()
        return any(app.bundleIdentifier() == ALCOVE_BUNDLE_ID for app in apps)
    except Exception:
        # Fail safe to "not running" -- the caller falls back to the normal
        # full-width layout, never to a crash.
        return False


def _screen_capture_values(screen):
    """Resolve AppKit screen state on main and return only plain values."""
    try:
        frame = screen.frame()
        description = screen.deviceDescription()
        display_id = int(description.get("NSScreenNumber", 0))
        scale = float(screen.backingScaleFactor())
        screen_x = float(frame.origin.x)
        screen_y = float(frame.origin.y)
        screen_width = float(frame.size.width)
        screen_height = float(frame.size.height)
    except Exception:
        return None
    if (
        display_id <= 0
        or not all(
            math.isfinite(value)
            for value in (screen_x, screen_y, screen_width, screen_height, scale)
        )
        or screen_width <= 0.0
        or screen_height <= 0.0
        or scale <= 0.0
    ):
        return None
    screen_id = (
        f"{display_id}:{screen_x:.3f}:{screen_y:.3f}:"
        f"{screen_width:.3f}:{screen_height:.3f}"
    )
    return (
        screen_id,
        display_id,
        screen_x,
        screen_y,
        screen_width,
        screen_height,
        scale,
    )


def _alcove_window_values(screen_x: float, screen_width: float):
    """Select Alcove's on-screen window on main without capturing pixels."""
    try:
        info = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
        )
    except Exception:
        return None
    candidates = []
    for entry in info or ():
        try:
            if str(entry.get("kCGWindowOwnerName", "")) != "Alcove":
                continue
            window_number = int(entry.get("kCGWindowNumber", 0))
            bounds = entry.get("kCGWindowBounds") or {}
            window_x = float(bounds.get("X", 0.0))
            window_y = float(bounds.get("Y", 0.0))
            window_width = float(bounds.get("Width", 0.0))
            window_height = float(bounds.get("Height", 0.0))
        except (TypeError, ValueError, OverflowError):
            continue
        if (
            window_number <= 0
            or not all(
                math.isfinite(value)
                for value in (window_x, window_y, window_width, window_height)
            )
            or window_width < 40.0
            or window_height < 1.0
        ):
            continue
        center_x = window_x + window_width / 2.0
        if not (screen_x <= center_x <= screen_x + screen_width):
            continue
        candidates.append(
            (window_y, -window_width, window_number, window_x, window_y, window_width)
        )
    if not candidates:
        return None
    _sort_y, _sort_width, window_number, window_x, window_y, window_width = min(
        candidates
    )
    return window_number, window_x, window_y, window_width


def measured_alcove_capsule_width(menu_band_height: float) -> float | None:
    """Compatibility seam. Alcove capture now exists only in the serial worker."""
    del menu_band_height
    return None


class AlcoveCapsuleTracker:
    """Hysteresis around the raw measurement: widen instantly (a live
    activity just appeared -- embrace it now), narrow only after the
    smaller reading holds for ALCOVE_NARROW_AFTER_SECONDS, and ride out
    reading gaps for ALCOVE_HOLD_SECONDS before surrendering to
    hardware geometry. Injectable measure/clock for tests."""

    def __init__(self, measure=None, clock=time.monotonic):
        # measure=None resolves to the module function AT CALL TIME so
        # tests can patch sidepulse.virtual_device.
        # measured_alcove_capsule_width and every tracker sees it.
        self._measure = measure
        self._clock = clock
        self._measured_at: float | None = None
        self._measured_value: float | None = None
        self._adopted: float | None = None
        self._narrow_candidate: float | None = None
        self._narrow_since: float | None = None
        self._last_good: float | None = None

    def desired_total_width(self, menu_band_height: float) -> float | None:
        """The bar's minimum total width in points, or None for
        hardware geometry."""
        now = self._clock()
        # Measurement TTL: the window capture is the expensive part and
        # reposition() can be driven at 4Hz by the sync path -- the
        # capsule doesn't move that fast, and adoption hysteresis below
        # already works on seconds.
        if (
            self._measured_at is not None
            and now - self._measured_at < ALCOVE_MEASURE_TTL_SECONDS
        ):
            reading = self._measured_value
        else:
            measure = (
                self._measure
                if self._measure is not None
                else measured_alcove_capsule_width
            )
            reading = measure(menu_band_height)
            self._measured_at = now
            self._measured_value = reading
        if reading is not None:
            reading = min(reading + 2.0 * ALCOVE_CAPSULE_MARGIN, ALCOVE_FOLLOW_MAX_WIDTH)
            self._last_good = now
            if self._adopted is None or reading > self._adopted + 0.5:
                self._adopted = reading
                self._narrow_since = None
                self._narrow_candidate = None
            elif reading < self._adopted - 4.0:
                if (
                    self._narrow_since is None
                    or self._narrow_candidate is None
                    or reading > self._narrow_candidate + 4.0
                ):
                    self._narrow_since = now
                    self._narrow_candidate = reading
                elif now - self._narrow_since >= ALCOVE_NARROW_AFTER_SECONDS:
                    self._adopted = self._narrow_candidate
                    self._narrow_since = None
                    self._narrow_candidate = None
            else:
                self._narrow_since = None
                self._narrow_candidate = None
            return self._adopted
        if (
            self._adopted is not None
            and self._last_good is not None
            and now - self._last_good <= ALCOVE_HOLD_SECONDS
        ):
            return self._adopted
        self._adopted = None
        self._narrow_since = None
        self._narrow_candidate = None
        return None




def led_band_rect(width: float):
    return ((0.0, 0.0), (float(width), LED_BAND_HEIGHT))


def alcove_accent_horizontal_bounds(width: float) -> tuple[float, float]:
    bounded_width = max(0.0, float(width))
    inset = min(ALCOVE_ACCENT_EDGE_INSET, bounded_width / 2.0)
    return inset, max(inset, bounded_width - inset)


def _legibility_boost(color, floor: float):
    """The bracket is a STATUS surface: any lit LED renders at least
    this visible on it, whatever fade floors and Focus dimming did to
    the underlying program. Relay's 1% resting glow stacked with
    nighttime dimming made the whole bar read as "gone" -- the physical
    LEDs may whisper; the on-screen bracket must stay readable. Truly
    off (alpha 0) stays off."""
    red, green, blue, alpha = color
    if floor <= 0.0 or alpha <= 0.0005 or alpha >= floor:
        return color
    factor = floor / alpha
    return (
        min(1.0, red * factor),
        min(1.0, green * factor),
        min(1.0, blue * factor),
        floor,
    )


def notch_bar_path(rect):
    """A top-attached notch silhouette extended by the 5 pt LED band."""
    x, y = rect[0]
    width, height = rect[1]
    right = x + width
    top = y + height
    bottom_radius = min(NOTCH_BOTTOM_RADIUS, width / 2.0, height / 2.0)

    path = NSBezierPath.bezierPath()
    path.moveToPoint_((x, top))
    path.lineToPoint_((right, top))
    path.lineToPoint_((right, y + bottom_radius))
    path.curveToPoint_controlPoint1_controlPoint2_(
        (right - bottom_radius, y),
        (right, y + bottom_radius * 0.45),
        (right - bottom_radius * 0.45, y),
    )
    path.lineToPoint_((x + bottom_radius, y))
    path.curveToPoint_controlPoint1_controlPoint2_(
        (x, y + bottom_radius),
        (x + bottom_radius * 0.45, y),
        (x, y + bottom_radius * 0.45),
    )
    path.lineToPoint_((x, top))
    path.closePath()
    return path


def virtual_led_colors(
    state: LedDisplayState,
    elapsed: float,
    brightness: float = 255,
) -> list[tuple[float, float, float, float]]:
    """Return the eight LED colors for the same status animations as the device."""
    scale = normalize_brightness(brightness) / 255.0
    if state == LedDisplayState.DONE:
        return [(0.0, scale, 0.4 * scale, 1.0)] * LED_COUNT
    if state == LedDisplayState.ASK:
        amount = 0.5 - 0.5 * math.cos(2.0 * math.pi * (elapsed % 1.6) / 1.6)
        return [(scale * amount, 0.227 * scale * amount, 0.0, amount)] * LED_COUNT
    if state == LedDisplayState.IDLE:
        amount = 0.5 - 0.5 * math.cos(2.0 * math.pi * (elapsed % 6.0) / 6.0)
        dim = 2.0 / 255.0
        return [(dim * scale * amount, dim * scale * amount, 2 * dim * scale * amount, amount)] * LED_COUNT

    # Mirrors rolling_program(): 760 ms pulses staggered by 95 ms.
    cycle = 0.76 + 0.095 * (LED_COUNT - 1)
    colors = []
    for index in range(LED_COUNT):
        local = (elapsed % cycle) - index * 0.095
        amount = 0.0
        if 0.0 <= local <= 0.76:
            amount = math.sin(math.pi * local / 0.76) ** 2
        colors.append((0.0, 0.898 * scale * amount, scale * amount, amount))
    return colors


def blended_led_color_at_x(
    colors: list[tuple[float, float, float, float]],
    x: float,
    led_width: float,
) -> tuple[float, float, float, float]:
    """Blend one source LED across exactly three virtual LED slots."""
    if led_width <= 0.0:
        return (0.0, 0.0, 0.0, 0.0)

    radius = led_width * LED_BLEND_RADIUS_LEDS
    totals = [0.0, 0.0, 0.0, 0.0]
    for index, color in enumerate(colors):
        center = (index + 0.5) * led_width
        distance = abs(float(x) - center)
        if distance > radius:
            continue
        phase = distance / radius
        weight = 0.5 + 0.5 * math.cos(math.pi * phase)
        for channel in range(4):
            totals[channel] += color[channel] * weight
    return tuple(min(1.0, max(0.0, value)) for value in totals)


def glow_color_for_column(
    colors: list[tuple[float, float, float, float]],
    led_width: float,
    notch_width: float,
    wing_offset: float,
    center_x: float,
    taper_floor: float = 0.0,
) -> tuple[float, float, float, float]:
    """The LED glow color at one column, in *view*-local x (0 = the
    view's own left edge, not the notch's).

    Inside the notch body this is an ordinary inter-LED blend. Inside a
    "wrap the menu bar" wing (wing_offset > 0), it's the nearest edge
    LED's own color eased out across the wing's *actual* assigned width
    (wing_offset itself -- both wings are always equal, see
    virtual_window_frame_for_screen) rather than sampled with
    blended_led_color_at_x's own inter-LED blend radius, which is sized
    for blending between neighboring LEDs a few points apart and would
    otherwise fade to nothing tens of points before reaching the far edge
    of a wide wing -- reading as "the glow doesn't reach the edges" even
    though the window itself is genuinely that wide.
    """
    # Wings sample their color at the edge LED's CENTER, not at the
    # notch's geometric edge -- at the edge itself the inter-LED blend
    # has already fallen off half an LED early, so the whole wing
    # inherited a pre-dimmed color and the notch/wing boundary showed a
    # hard brightness seam ("the wings are fucked up").
    if wing_offset > 0.0 and center_x < wing_offset:
        distance_into_wing = wing_offset - center_x
        edge_x = led_width * 0.5
        wing_width = wing_offset
    elif wing_offset > 0.0 and center_x > wing_offset + notch_width:
        distance_into_wing = center_x - (wing_offset + notch_width)
        edge_x = notch_width - led_width * 0.5
        wing_width = wing_offset
    else:
        return blended_led_color_at_x(colors, center_x - wing_offset, led_width)

    red, green, blue, alpha = blended_led_color_at_x(colors, edge_x, led_width)
    phase = min(1.0, max(0.0, distance_into_wing / wing_width))
    # taper_floor keeps the wing's far end at a fraction of the edge
    # color instead of easing to zero -- the wings-only (Alcove) bracket
    # uses it so the horizontal stroke visibly MEETS its riser, one
    # continuous |____| shape rather than a fade-out and a floating bar.
    taper = taper_floor + (1.0 - taper_floor) * (0.5 + 0.5 * math.cos(math.pi * phase))
    return red * taper, green * taper, blue * taper, alpha * taper


def tone_mapped_led_color(
    red: float,
    green: float,
    blue: float,
    alpha: float,
    *,
    boost: float = 1.0,
    alpha_scale: float = 1.0,
) -> tuple[float, float, float, float]:
    def channel(value: float) -> float:
        if value <= 0.0:
            return 0.0
        return min(1.0, (value ** LED_GAMMA) * boost)

    return (
        channel(red),
        channel(green),
        channel(blue),
        min(1.0, max(0.0, alpha * alpha_scale)),
    )


def fill_rect_with_color(rect, color) -> None:
    red, green, blue, alpha = color
    if max(red, green, blue, alpha) <= 0.001:
        return
    NSColor.colorWithCalibratedRed_green_blue_alpha_(
        red, green, blue, alpha
    ).set()
    NSBezierPath.bezierPathWithRect_(rect).fill()


def fill_rect_with_cg(context, rect, color) -> None:
    red, green, blue, alpha = color
    if max(red, green, blue, alpha) <= 0.001:
        return
    if context is None:
        fill_rect_with_color(rect, color)
        return
    CGContextSetRGBFillColor(context, red, green, blue, alpha)
    CGContextFillRect(context, rect)


def current_cg_context():
    try:
        return NSGraphicsContext.currentContext().CGContext()
    except Exception:
        try:
            return NSGraphicsContext.currentContext().graphicsPort()
        except Exception:
            return None


def _glow_runs(
    geometry_cache,
    paint_cache,
    *,
    colors,
    brightness,
    led_width,
    notch_width,
    x_start,
    x_end,
    wing_offset,
    wing_taper_floor,
    silhouette="glow-row",
    screen_identity="unknown",
    scale=1.0,
):
    geometry_key = GlowGeometryKey.from_output(
        screen_identity=screen_identity,
        scale=scale,
        dimensions=(
            led_width,
            notch_width,
            x_start,
            x_end,
            wing_offset,
            BLEND_COLUMN_WIDTH,
        ),
        led_count=LED_COUNT,
        width=x_end - x_start,
        silhouette=silhouette,
    )

    def build_geometry():
        columns: list[tuple[float, float, float]] = []
        column_x = x_start
        while column_x < x_end:
            column_width = min(BLEND_COLUMN_WIDTH, x_end - column_x)
            columns.append((column_x, column_width, column_x + column_width / 2.0))
            column_x += column_width
        return tuple(columns)

    columns = geometry_cache.get_or_build(geometry_key, build_geometry)
    paint_key = GlowPaintKey.from_output(
        geometry=geometry_key,
        colors=colors,
        brightness=brightness,
        variant=(wing_taper_floor,),
    )

    def build_paint():
        runs: list = []
        for column_x, column_width, center_x in columns:
            red, green, blue, alpha = glow_color_for_column(
                colors,
                led_width,
                notch_width,
                wing_offset,
                center_x,
                taper_floor=wing_taper_floor,
            )
            if max(red, green, blue, alpha) <= 0.001:
                if runs and runs[-1] is not None:
                    runs.append(None)
                continue
            quantized = (
                round(red * 1024.0) / 1024.0,
                round(green * 1024.0) / 1024.0,
                round(blue * 1024.0) / 1024.0,
                round(alpha * 1024.0) / 1024.0,
            )
            last = runs[-1] if runs else None
            if last is not None and last[2] == quantized:
                last[1] += column_width
            else:
                runs.append([column_x, column_width, quantized])
        return tuple(
            None if run is None else (run[0], run[1], run[2]) for run in runs
        )

    return paint_cache.get_or_build(paint_key, build_paint)


def _gradient_color(color):
    red, green, blue, alpha = color
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(
        red, green, blue, alpha
    )


def _riser_gradient_bucket(color):
    maximum = RISER_GRADIENT_COLOR_LEVELS - 1
    return tuple(
        round(max(0.0, min(1.0, float(channel))) * maximum) / maximum
        for channel in color
    )


def native_riser_gradients(
    cache,
    key,
    core_color,
    soft_color,
):
    """Build two cached native vertical gradients, or raise for fallback."""

    def build():
        gradients = []
        for color in (core_color, soft_color):
            red, green, blue, _alpha = color
            transparent = (red, green, blue, 0.0)
            gradient = NSGradient.alloc().initWithColors_atLocations_colorSpace_(
                [
                    _gradient_color(color),
                    _gradient_color(color),
                    _gradient_color(transparent),
                ],
                [0.0, WING_RISER_SOLID_FRACTION, 1.0],
                NSColorSpace.deviceRGBColorSpace(),
            )
            if gradient is None:
                raise RuntimeError("NSGradient initialization failed")
            gradients.append(gradient)
        return tuple(gradients)

    return cache.get_or_build(key, build)


class VirtualLedView(NSView):
    def mouseDown_(self, _event):
        handler = getattr(self, "click_handler", None)
        if handler is not None:
            handler()

    def resetCursorRects(self):
        if getattr(self, "click_handler", None) is not None:
            self.addCursorRect_cursor_(self.bounds(), NSCursor.pointingHandCursor())

    def initWithFrame_(self, frame):
        self = objc.super(VirtualLedView, self).initWithFrame_(frame)
        if self is not None:
            self.state = LedDisplayState.IDLE
            self.brightness = 255
            self.started_at = time.monotonic()
            self.fixed_colors = None
            self.current_program = None
            self._presentation_colors = None
            self.wasm_controller = None
            self.wasm_error = None
            self.has_notch = True
            self.compact_mode = False
            self.wings_only_mode = False
            self.alcove_silhouette = None
            self.render_screen_identity = "unknown"
            self.render_scale = 1.0
            self._target_sample_interval = TARGET_SAMPLE_INTERVAL_VIEW_CONTRACT
            self._glow_geometry_cache = BoundedRenderCache(max_entries=64)
            self._glow_paint_cache = BoundedRenderCache(max_entries=128)
            self._riser_gradient_cache = BoundedRenderCache(max_entries=32)
            self.accessibility_display_preferences = AccessibilityDisplayPreferences()
            # None means "no wing" -- the notch silhouette fills the whole
            # view, today's exact behavior. Set to a value smaller than the
            # view's own width to inset the notch body and let the LED glow
            # spill into the remaining width on each side (see setNotchWidth_).
            self.notch_width = None
        return self

    # These four are driven by reposition() every couple of seconds;
    # change-gated so a static bar is never dirtied by a no-op sync
    # (the improvement hunt caught them forcing full 4-layer glow
    # repaints of an unchanged frame).
    def setHasNotch_(self, has_notch):
        value = bool(has_notch)
        if value == self.has_notch:
            return
        self.has_notch = value
        self.setNeedsDisplay_(True)

    def setCompactMode_(self, compact_mode):
        value = bool(compact_mode)
        if value == self.compact_mode:
            return
        self.compact_mode = value
        self.setNeedsDisplay_(True)

    def setWingsOnlyMode_(self, wings_only_mode):
        value = bool(wings_only_mode)
        if value == self.wings_only_mode:
            return
        self.wings_only_mode = value
        self.setNeedsDisplay_(True)

    def setNotchWidth_(self, notch_width):
        value = None if notch_width is None else float(notch_width)
        if value == self.notch_width:
            return
        self.notch_width = value
        self.setNeedsDisplay_(True)

    def setAlcoveSilhouette_(self, silhouette):
        value = None
        if silhouette is not None:
            center_x, width, height, contour = silhouette
            value = (
                float(center_x),
                float(width),
                float(height),
                tuple((float(x), float(y)) for x, y in contour),
            )
        if value == self.alcove_silhouette:
            return
        self.alcove_silhouette = value
        self.setNeedsDisplay_(True)

    def setRenderGeometryIdentity_(self, identity):
        screen_identity, scale = identity
        value = (str(screen_identity), float(scale))
        if value == (self.render_screen_identity, self.render_scale):
            return
        self.render_screen_identity, self.render_scale = value
        self.setNeedsDisplay_(True)

    def setAccessibilityDisplayPreferences_(self, preferences):
        if type(preferences) is not AccessibilityDisplayPreferences:
            return
        self.accessibility_display_preferences = preferences

    def _notch_geometry(self):
        """(notch_width, wing_offset) for the current bounds -- notch_width
        never exceeds the view's own width even if told otherwise (a stale
        value from a screen change is the only way that could happen), and
        wing_offset is how far in from each edge the notch body starts."""
        total_width = self.bounds().size.width
        notch_width = total_width if self.notch_width is None else min(self.notch_width, total_width)
        wing_offset = max(0.0, (total_width - notch_width) / 2.0)
        return notch_width, wing_offset

    def _alcove_body_path(self, width, height):
        observed = self.alcove_silhouette
        if observed is None:
            return None
        center_x, observed_width, observed_height, contour = observed
        left = center_x - observed_width / 2.0
        x_offset = max(0.0, (float(width) - observed_width) / 2.0)
        maximum_y = max(point[1] for point in contour)
        local_contour = tuple(
            (
                x_offset + point[0] - left,
                max(0.0, min(float(height), maximum_y - point[1])),
            )
            for point in contour
        )
        silhouette = rounded_silhouette(
            center_x=float(width) / 2.0,
            width=min(float(width), observed_width),
            height=min(float(height), observed_height),
            contour=local_contour,
            requested_radius=alcove_bracket_corner_radius(
                observed_width,
                observed_height,
            ),
        )
        path = NSBezierPath.bezierPath()
        path.moveToPoint_(silhouette.points[0])
        for point in silhouette.points[1:]:
            path.lineToPoint_(point)
        path.closePath()
        return path

    def setState_brightness_startedAt_(self, state, brightness, started_at):
        if state != self.state:
            self.started_at = started_at if started_at is not None else time.monotonic()
        self.state = state
        self.brightness = normalize_brightness(brightness)
        self.fixed_colors = None
        self.setProgram_startedAt_(
            program_for_display_state(
                state,
                led_count=LED_COUNT,
                brightness=self.brightness,
            ),
            started_at,
        )

    def setRenderFps_(self, fps):
        value = max(0.0, float(fps))
        interval = None if value <= 0.0 else 1.0 / value
        previous = getattr(self, "_target_sample_interval", None)
        self._target_sample_interval = interval
        if interval is not None and (previous is None or interval < previous):
            self._target_sample = None

    def setState_brightness_(self, state, brightness):
        self.setState_brightness_startedAt_(state, brightness, None)

    def setProgram_startedAt_(self, program, started_at):
        if program == self.current_program:
            # Unchanged program: the redraw tick's own frame-color gate
            # decides whether pixels move; dirtying here forced a full
            # repaint of a static bar on every sync tick.
            return
        self.current_program = str(program)
        self._presentation_colors = None
        self.fixed_colors = None
        self._target_sample = None
        self._frame_colors = None
        # started_at lets a caller anchor this program's animation phase to a
        # real-world instant that already happened (e.g. the moment a
        # physical device's write completed) instead of "now". Omitted, this
        # behaves exactly as before: phase-zero is the moment this method
        # runs.
        self.started_at = started_at if started_at is not None else time.monotonic()
        if self._ensure_wasm_controller():
            try:
                parse_epoch_ms = int(self.started_at * 1000.0)
                self.wasm_controller.parse(self.current_program, parse_epoch_ms)
            except Exception as exc:
                self.wasm_error = str(exc)
        self.setNeedsDisplay_(True)

    def setPresentationProgram_startedAt_(self, program, started_at):
        """Record Screen Bar program identity without parsing it on AppKit's thread."""
        self.current_program = str(program)
        self.fixed_colors = None
        self._presentation_colors = None
        self._frame_colors = None
        self.started_at = started_at if started_at is not None else time.monotonic()

    def setPresentationColors_(self, colors):
        """Install one immutable sampler result for the next main-thread paint."""
        self._presentation_colors = tuple(tuple(color) for color in colors)
        self._frame_colors = None

    def setProgram_(self, program):
        self.setProgram_startedAt_(program, None)

    def setBatteryPercent_brightness_(self, percent, brightness):
        self.current_program = None
        self._presentation_colors = None
        self.brightness = normalize_brightness(brightness)
        scale = self.brightness / 255.0
        filled = max(0.0, min(8.0, float(percent) * 8.0 / 100.0))
        colors = []
        for index in range(LED_COUNT):
            amount = max(0.0, min(1.0, filled - index))
            if percent <= 20:
                rgb = (1.0, 0.15, 0.0)
            elif percent <= 50:
                rgb = (1.0, 0.55, 0.0)
            else:
                rgb = (0.0, 1.0, 0.4)
            colors.append((*(channel * scale * amount for channel in rgb), amount))
        self.fixed_colors = colors
        self._target_sample = None
        self.setNeedsDisplay_(True)

    def setPreviewWhiteBrightness_(self, brightness):
        """A plain white glow scaled by brightness alone, with no mode
        color and no animation -- used for the Settings window's live
        brightness/wing-extension preview, where the actual mode color
        would be a red herring (brightness applies the same regardless of
        mode) and the battery-style red/orange/green coloring above would
        misrepresent what's actually being previewed."""
        self.current_program = None
        self._presentation_colors = None
        self.brightness = normalize_brightness(brightness)
        scale = self.brightness / 255.0
        self.fixed_colors = [(scale, scale, scale, scale)] * LED_COUNT
        self._target_sample = None
        self.setNeedsDisplay_(True)

    def _ensure_wasm_controller(self) -> bool:
        if self.wasm_controller is not None:
            return True
        try:
            self.wasm_controller = SdLedWasmController(LED_COUNT)
            self.wasm_error = None
            return True
        except LedWasmUnavailableError as exc:
            self.wasm_error = str(exc)
            return False

    def _colors_for_draw_cached(self):
        """The paint-path twin of _colors_for_draw: redraw_'s change
        gate already computed this frame's smoothed colors -- the three
        draw sites reuse that snapshot instead of advancing the filter
        a second (or third) time per frame. Falls through to a live
        compute on expose events that arrive outside our tick."""
        presentation = getattr(self, "_presentation_colors", None)
        if presentation is not None:
            return presentation
        cached = getattr(self, "_frame_colors", None)
        if cached is not None and time.monotonic() - cached[0] < 0.045:
            return cached[1]
        colors = self._colors_for_draw()
        self._frame_colors = (time.monotonic(), colors)
        return colors

    def _colors_for_draw(self):
        """The colors actually painted this frame: the engine's target
        colors run through a short exponential low-pass (~45ms). The
        WASM engine emits 8-bit channel values, and at slow pulse speeds
        the deepest part of a breath crosses single-digit channel values
        where each 1/255 step is a visible luminance JUMP -- the "kind
        of glitchy, not smooth" look. The filter turns those steps into
        ramps while staying fast enough that attention flashes (240ms)
        still read as flashes. Snaps (no filtering) on the first frame
        after a pause so a long-hidden bar never visibly slews."""
        target = self._target_colors_for_draw()
        now = time.monotonic()
        previous = getattr(self, "_smoothed_colors", None)
        last_time = getattr(self, "_smoothed_at", None)
        self._smoothed_at = now
        if (
            previous is None
            or last_time is None
            or len(previous) != len(target)
            or (now - last_time) > 0.5
        ):
            self._smoothed_colors = [tuple(color) for color in target]
            return self._smoothed_colors
        blend = 1.0 - math.exp(-(now - last_time) / 0.045)
        self._smoothed_colors = [
            tuple(p + (t - p) * blend for p, t in zip(prev, tgt))
            for prev, tgt in zip(previous, target)
        ]
        return self._smoothed_colors

    def _target_colors_for_draw(self):
        now = time.monotonic()
        cached = getattr(self, "_target_sample", None)
        sample_interval = getattr(
            self, "_target_sample_interval", TARGET_SAMPLE_INTERVAL_VIEW_CONTRACT
        )
        if sample_interval is None:
            return cached[1] if cached is not None else self._compute_target_colors()
        if cached is not None and now - cached[0] < sample_interval:
            return cached[1]
        colors = self._compute_target_colors()
        self._target_sample = (now, colors)
        return colors

    def _compute_target_colors(self):
        if self.fixed_colors is not None:
            return self.fixed_colors
        if self.current_program is not None and self._ensure_wasm_controller():
            try:
                pixels = self.wasm_controller.step(monotonic_ms())
                self.wasm_error = None
                return [
                    (
                        red / 255.0,
                        green / 255.0,
                        blue / 255.0,
                        max(red, green, blue) / 255.0,
                    )
                    for red, green, blue in pixels[:LED_COUNT]
                ]
            except Exception as exc:
                self.wasm_error = str(exc)
        return virtual_led_colors(
            self.state, time.monotonic() - self.started_at, self.brightness
        )

    def drawRect_(self, _rect):
        if self.wings_only_mode:
            self._draw_wings_only()
            return
        if self.compact_mode:
            self._draw_compact_accent()
            return

        colors = self._colors_for_draw_cached()
        width = self.bounds().size.width
        height = self.bounds().size.height
        notch_width, wing_offset = self._notch_geometry()
        body = notch_bar_path(((wing_offset, 0.0), (notch_width, height)))
        led_width = notch_width / LED_COUNT
        glow_height = min(LED_GLOW_HEIGHT, max(0.0, height - LED_BAND_HEIGHT))

        # A MacBook gets the black camera-housing continuation. On a notchless
        # display the window remains transparent and contains only LED color.
        # This shape always matches the *real* notch width -- wings (below)
        # only ever extend the LED glow past it, never the housing itself.
        if self.has_notch and (rim := max(0.0, min(1.0, getattr(self, "min_glow", 0.25)))) > 0.0:
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.006, 0.007, 0.010, 0.93
            ).set()
            body.fill()

        cg_context = current_cg_context()

        # Pass 1: the notch's own LED row, clipped to the housing's rounded
        # silhouette -- unchanged from before wings existed.
        NSGraphicsContext.saveGraphicsState()
        body.addClip()
        self._fill_glow_row(
            cg_context, colors, led_width, notch_width, glow_height, height,
            x_start=wing_offset, x_end=wing_offset + notch_width, wing_offset=wing_offset,
        )
        NSGraphicsContext.restoreGraphicsState()

        # Pass 2: the wings, if any -- a plain (unrounded) clip since
        # there's no housing shape to match out here; glow_color_for_column
        # eases the edge LED's own color across the wing's full assigned
        # width (see its docstring), reaching the far edge rather than
        # fading out early, so the hard clip edge is never actually
        # visible by the time it's reached.
        if wing_offset > 0.0:
            for x_start, x_end in ((0.0, wing_offset), (wing_offset + notch_width, width)):
                NSGraphicsContext.saveGraphicsState()
                NSBezierPath.bezierPathWithRect_(((x_start, 0.0), (x_end - x_start, height))).addClip()
                self._fill_glow_row(
                    cg_context, colors, led_width, notch_width, glow_height, height,
                    x_start=x_start, x_end=x_end, wing_offset=wing_offset,
                )
                NSGraphicsContext.restoreGraphicsState()

            # Pass 3: a vertical riser at each wing's own outer edge -- see
            # WING_RISER_WIDTH's comment. Sampled at the notch's nearest
            # edge LED (not the already-tapered-to-~0 color right at the
            # true edge), since the riser is a new visual element in its
            # own right, not a continuation of the horizontal taper.
            left_edge_color = blended_led_color_at_x(colors, led_width * 0.5, led_width)
            right_edge_color = blended_led_color_at_x(
                colors, notch_width - led_width * 0.5, led_width
            )
            self._draw_wing_riser(
                cg_context, left_edge_color, 0.0, min(WING_RISER_WIDTH, wing_offset), height,
                outer_on_left=True,
            )
            self._draw_wing_riser(
                cg_context,
                right_edge_color,
                max(width - WING_RISER_WIDTH, wing_offset + notch_width),
                width,
                height,
                outer_on_left=False,
            )

        # Edge highlight/shadow: unclipped and full-width (as it always
        # was, before wings existed) -- with wings on, this reads as one
        # continuous strip rather than having a visible seam where the
        # notch's own housing ends and the wing begins.
        if self.has_notch and (rim := max(0.0, min(1.0, getattr(self, "min_glow", 0.25)))) > 0.0:
            fill_rect_with_cg(
                cg_context,
                ((0.0, LED_BAND_HEIGHT - 0.55), (width, 0.55)),
                (0.0, 0.0, 0.0, 0.18 * rim),
            )
            fill_rect_with_cg(
                cg_context,
                ((0.0, 0.0), (width, 0.45)),
                (1.0, 1.0, 1.0, 0.055 * rim),
            )
        # Only meaningful with wings: the tips must sit OUTSIDE the
        # housing, or the gauge would paint over the notch body.
        if wing_offset > 0.0:
            self._draw_standing_gauges(cg_context, height)

    def _bracket_colors(self, colors):
        """The colors the Alcove bracket paints. "spatial" mirrors the
        physical LEDs exactly -- the relay ripple travels along the
        underline in lockstep with the light bar. "identity" collapses
        to one blended hue (the original visibility fix: one agent
        lights 1 of 8 LEDs, and a spatial bracket was 7/8 black).
        "auto" (default) picks spatial whenever at least two LEDs are
        lit -- multi-agent blends have rest glow everywhere, so the
        ripple reads -- and identity otherwise."""
        style = getattr(self, "bracket_style", "auto")
        # Threshold 0.0008, not 0.004: Relay's resting LEDs glow at ~1%
        # (right AT the old threshold), so auto flickered between
        # spatial and identity as the spotlight moved -- and identity's
        # alpha-weighted blend follows the spotlight, which read as the
        # bar "cycling between colors, not adhering to Relay".
        lit = sum(1 for c in colors if max(c[0], c[1], c[2], c[3]) > 0.0008)
        now = time.monotonic()
        if lit >= 2:
            self._bracket_spatial_hold_until = now + 2.0
        spatial_held = now < getattr(self, "_bracket_spatial_hold_until", 0.0)
        # The legibility floor scales with the user's dim-floor dial: at
        # 0 the bar is allowed to go PITCH BLACK -- only the moving
        # signal (relay dot, timer frontier) renders.
        floor = max(0.0, min(1.0, getattr(self, "min_glow", 0.25))) * 0.72
        identity = self._bar_identity_color(colors)
        if identity[3] > 0.0005:
            self._last_visible_bracket_identity = identity
        elif (
            floor > 0.0
            and str(getattr(self, "current_program", "")).strip().lower() != "off"
        ):
            remembered = getattr(
                self,
                "_last_visible_bracket_identity",
                (0.10, 0.32, 0.38, floor),
            )
            visible = (
                remembered[0],
                remembered[1],
                remembered[2],
                max(floor, remembered[3]),
            )
            colors = [visible] * LED_COUNT
            identity = visible
            lit = LED_COUNT
        if style == "spatial" or (style == "auto" and (lit >= 2 or spatial_held)):
            return [_legibility_boost(c, floor) for c in colors]
        return [_legibility_boost(identity, floor)] * LED_COUNT

    def setBracketStyle_(self, style):
        self.bracket_style = str(style or "auto")

    def set_standing_gauges(self, left_level, right_on):
        """Story #14: the outermost columns as persistent micro-gauges.
        left_level (0-1) is the quota ember's intensity; right_on holds
        the unseen-done green. Change-gated like every other input."""
        left_clamped = max(0.0, min(1.0, float(left_level)))
        right_flag = bool(right_on)
        if (
            getattr(self, "_gauge_left_level", 0.0) == left_clamped
            and getattr(self, "_gauge_right_on", False) == right_flag
        ):
            return
        self._gauge_left_level = left_clamped
        self._gauge_right_on = right_flag
        self.setNeedsDisplay_(True)

    def _draw_standing_gauges(self, cg_context, height, *, edge_inset=0.0):
        """The overlay pass: painted LAST so standing state survives
        whatever animation owns the center of the bar. Peripheral
        vision gets its own pixels -- a 4pt amber ember at the left tip
        (brightness tracks the worst quota window) and the unseen-done
        green at the right tip."""
        left_level = getattr(self, "_gauge_left_level", 0.0)
        right_on = getattr(self, "_gauge_right_on", False)
        if left_level <= 0.0 and not right_on:
            return
        width = self.bounds().size.width
        left_edge = max(0.0, min(float(edge_inset), width / 2.0))
        right_edge = max(left_edge, width - left_edge)
        tip_width = 4.0
        glow_height = min(LED_GLOW_HEIGHT, max(0.0, height - LED_BAND_HEIGHT))
        if left_level > 0.0:
            alpha = 0.30 + 0.62 * left_level
            fill_rect_with_cg(
                cg_context,
                ((left_edge, 0.0), (tip_width, LED_BAND_HEIGHT)),
                (1.0, 0.62, 0.18, alpha),
            )
            fill_rect_with_cg(
                cg_context,
                ((left_edge, LED_BAND_HEIGHT), (tip_width, glow_height * 0.4)),
                (1.0, 0.62, 0.18, alpha * 0.22),
            )
        if right_on:
            fill_rect_with_cg(
                cg_context,
                ((right_edge - tip_width, 0.0), (tip_width, LED_BAND_HEIGHT)),
                (0.16, 0.95, 0.45, 0.85),
            )
            fill_rect_with_cg(
                cg_context,
                (
                    (right_edge - tip_width, LED_BAND_HEIGHT),
                    (tip_width, glow_height * 0.4),
                ),
                (0.16, 0.95, 0.45, 0.20),
            )

    def setMinGlow_(self, fraction):
        self.min_glow = max(0.0, min(1.0, float(fraction)))

    def _bar_identity_color(self, colors):
        """ONE color representing the whole strip: the alpha-weighted
        blend of the lit LEDs, at the brightest lit LED's intensity.
        The bracket and accent renders are a status OUTLINE, not a
        spatial LED map -- with one agent lit out of 8 LEDs, a spatial
        render left 7/8 of the bracket black and the right riser
        sampling a dark LED: an invisible bar that read as broken.
        Animation still flows through: these colors are recomputed
        every frame, so breathing/attention phases move the whole
        bracket together."""
        lit = [c for c in colors if max(c[0], c[1], c[2], c[3]) > 0.004]
        if not lit:
            return (0.0, 0.0, 0.0, 0.0)
        total = sum(c[3] for c in lit)
        if total <= 0.0:
            return (0.0, 0.0, 0.0, 0.0)
        red = sum(c[0] * c[3] for c in lit) / total
        green = sum(c[1] * c[3] for c in lit) / total
        blue = sum(c[2] * c[3] for c in lit) / total
        alpha = max(c[3] for c in lit)
        return (red, green, blue, alpha)

    def _draw_wings_only(self):
        """The Alcove coexistence render: a continuous, unmissable LED
        underline across the WHOLE bar -- through the gap, over the
        bottom edge of Alcove's backdrop -- plus the wing glow and the
        risers at each end. The earlier wings-only attempt drew nothing
        in the gap and left only faint wing stubs; over Alcove's huge
        dark backdrop that read as "the app disappeared". The underline
        is the bar's identity now: always visible, colored by the live
        agent state, and the risers turn it into the |____| bracket.
        Painted in the single identity color (_bar_identity_color), not
        the spatial per-LED layout."""
        colors = self._bracket_colors(self._colors_for_draw_cached())
        width = self.bounds().size.width
        left_bound, right_bound = alcove_accent_horizontal_bounds(width)
        height = self.bounds().size.height
        notch_width, wing_offset = self._notch_geometry()
        led_width = notch_width / LED_COUNT
        glow_height = min(LED_GLOW_HEIGHT, max(0.0, height - LED_BAND_HEIGHT))
        cg_context = current_cg_context()

        # One rounded-rect clip softens every corner of the bracket at
        # once: the underline's ends curve up into the risers and the
        # riser tops get a cap -- no hard right angles anywhere.
        NSGraphicsContext.saveGraphicsState()
        observed_height = (
            self.alcove_silhouette[2]
            if self.alcove_silhouette is not None
            else height
        )
        bracket_radius = alcove_bracket_corner_radius(width, observed_height)
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((left_bound, 0.0), (right_bound - left_bound, height)),
            bracket_radius,
            bracket_radius,
        ).addClip()
        observed_body = self._alcove_body_path(width, height)
        if observed_body is not None:
            observed_body.addClip()

        # The full-width underline: clip to the LED band (plus a whisper
        # of bloom above it) so the gap region shows a clean bright line
        # rather than the full-height glow that belongs to the wings.
        # Taper floor 1.0 -- the underline is ONE continuous line at one
        # intensity. With the wings' 0.55 floor here, the section under
        # the notch rendered hot while the wings faded, and the seam
        # between the two read as a rendering bug, not a design.
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath.bezierPathWithRect_(
            ((left_bound, 0.0), (right_bound - left_bound, LED_BAND_HEIGHT + 3.0))
        ).addClip()
        self._fill_glow_row(
            cg_context, colors, led_width, notch_width, glow_height, height,
            x_start=left_bound, x_end=right_bound, wing_offset=wing_offset,
            wing_taper_floor=1.0,
        )
        NSGraphicsContext.restoreGraphicsState()

        if wing_offset > 0.0:
            for x_start, x_end in (
                (left_bound, wing_offset),
                (wing_offset + notch_width, right_bound),
            ):
                if x_end <= x_start:
                    continue
                NSGraphicsContext.saveGraphicsState()
                NSBezierPath.bezierPathWithRect_(((x_start, 0.0), (x_end - x_start, height))).addClip()
                self._fill_glow_row(
                    cg_context, colors, led_width, notch_width, glow_height, height,
                    x_start=x_start, x_end=x_end, wing_offset=wing_offset,
                    wing_taper_floor=WINGS_ONLY_TAPER_FLOOR,
                )
                NSGraphicsContext.restoreGraphicsState()
        # Sampled at the edge LEDs' centers -- the same fix as
        # glow_color_for_column's wings: at the geometric edge the
        # blend has already dropped and the risers came out dimmer
        # than the bar they belong to.
        left_edge_color = blended_led_color_at_x(colors, led_width * 0.5, led_width)
        right_edge_color = blended_led_color_at_x(colors, notch_width - led_width * 0.5, led_width)
        # Risers at the window's own ends, even with zero wing -- the
        # bracket's uprights must never be able to vanish.
        self._draw_wing_riser(
            cg_context,
            left_edge_color,
            left_bound,
            min(left_bound + WING_RISER_WIDTH, right_bound),
            height,
            outer_on_left=True,
        )
        self._draw_wing_riser(
            cg_context,
            right_edge_color,
            max(left_bound, right_bound - WING_RISER_WIDTH),
            right_bound,
            height,
            outer_on_left=False,
        )
        self._draw_standing_gauges(
            cg_context,
            height,
            edge_inset=left_bound,
        )
        NSGraphicsContext.restoreGraphicsState()

    def _fill_glow_row(self, cg_context, colors, led_width, notch_width, glow_height, _height, *, x_start, x_end, wing_offset, wing_taper_floor=0.0):
        """Draws the 4-layer LED glow (bloom / soft falloff / core /
        hotline) across [x_start, x_end) -- see glow_color_for_column
        for how a wing's glow is colored versus the notch body's own
        inter-LED blend.

        Audit #4 stage A: columns are sampled at BLEND_COLUMN_WIDTH,
        quantized to the same 1/1024 precision the redraw change-gate
        uses, and ADJACENT COLUMNS WITH IDENTICAL QUANTIZED COLOR
        coalesce into one rect per layer -- a solid or gently-blended
        bar collapses from ~440 bridged fills per frame to a
        handful, and the loop-invariant layer geometry is hoisted."""
        runs = _glow_runs(
            self._glow_geometry_cache,
            self._glow_paint_cache,
            colors=colors,
            brightness=self.brightness,
            led_width=led_width,
            notch_width=notch_width,
            x_start=x_start,
            x_end=x_end,
            wing_offset=wing_offset,
            wing_taper_floor=wing_taper_floor,
            silhouette=(
                self.alcove_silhouette[3]
                if self.alcove_silhouette is not None
                else "glow-row"
            ),
            screen_identity=self.render_screen_identity,
            scale=self.render_scale,
        )

        # The light source is centered on its target LED and fades
        # through the neighboring LED width on both sides, for a
        # three-LED footprint. Geometry identical for every run.
        bloom_y = LED_BAND_HEIGHT
        bloom_height = glow_height * 0.45
        soft_y = LED_BAND_HEIGHT + bloom_height
        soft_height = glow_height * 0.55
        for run in runs:
            if run is None:
                continue
            run_x, run_width, (red, green, blue, alpha) = run
            fill_rect_with_cg(
                cg_context,
                ((run_x, bloom_y), (run_width, bloom_height)),
                tone_mapped_led_color(
                    red, green, blue, alpha, boost=0.82, alpha_scale=0.18
                ),
            )
            fill_rect_with_cg(
                cg_context,
                ((run_x, soft_y), (run_width, soft_height)),
                tone_mapped_led_color(
                    red, green, blue, alpha, boost=0.64, alpha_scale=0.07
                ),
            )
            fill_rect_with_cg(
                cg_context,
                ((run_x, 0.0), (run_width, LED_BAND_HEIGHT)),
                tone_mapped_led_color(
                    red, green, blue, alpha, boost=LED_CORE_BOOST, alpha_scale=0.92
                ),
            )
            fill_rect_with_cg(
                cg_context,
                ((run_x, 0.0), (run_width, 1.15)),
                tone_mapped_led_color(
                    red, green, blue, alpha, boost=LED_HOTLINE_BOOST, alpha_scale=0.72
                ),
            )

    def _draw_wing_riser(
        self, cg_context, edge_color, x_start, x_end, height, *, outer_on_left: bool = True
    ) -> None:
        """A vertical glow at one wing's outer edge, solid for its lower
        WING_RISER_SOLID_FRACTION and softly tapering above that -- see
        WING_RISER_WIDTH's comment for why. `edge_color` is the nearest
        notch LED's own blended color, not resampled per row -- only the
        vertical taper changes going up. Solid rather than tapering the
        *entire* height: over a real menu bar's actual height (well under
        40pt), a taper starting at the very bottom reads as a faint hint
        rather than a bracket you can actually see.

        Rendering detail: the old 48-step core plus 48-step soft path made
        96 Python-to-Core-Graphics fill calls per riser. AppKit now renders
        the same solid-to-transparent shape with two native gradients. A
        lower-resolution fill fallback remains for unsupported contexts.
        """
        if x_end <= x_start or height <= 0.0:
            return
        red, green, blue, alpha = edge_color
        if max(red, green, blue, alpha) <= 0.001:
            return
        width = x_end - x_start
        core_width = max(1.0, width * 0.45)
        if outer_on_left:
            core_rect_x, soft_rect_x = x_start, x_start + core_width
        else:
            core_rect_x, soft_rect_x = x_end - core_width, x_start
        soft_width = max(0.0, width - core_width)
        core_color = tone_mapped_led_color(
            red,
            green,
            blue,
            alpha,
            boost=LED_HOTLINE_BOOST,
            alpha_scale=0.9,
        )
        soft_color = tone_mapped_led_color(
            red,
            green,
            blue,
            alpha,
            boost=LED_CORE_BOOST,
            alpha_scale=0.34,
        )
        gradient_core_color = _riser_gradient_bucket(core_color)
        gradient_soft_color = _riser_gradient_bucket(soft_color)
        geometry_key = GlowGeometryKey.from_output(
            dimensions=(width, height, core_width, soft_width, WING_RISER_SOLID_FRACTION),
            led_count=LED_COUNT,
            width=width,
            silhouette="wing-riser",
        )
        key = GlowPaintKey.from_output(
            geometry=geometry_key,
            brightness=self.brightness,
            colors=(gradient_core_color, gradient_soft_color),
        )
        try:
            core_gradient, soft_gradient = native_riser_gradients(
                self._riser_gradient_cache,
                key,
                gradient_core_color,
                gradient_soft_color,
            )
            core_gradient.drawInRect_angle_(
                ((core_rect_x, 0.0), (core_width, height)), 90.0
            )
            if soft_width > 0.0:
                soft_gradient.drawInRect_angle_(
                    ((soft_rect_x, 0.0), (soft_width, height)), 90.0
                )
            return
        except Exception:
            pass

        solid_height = height * WING_RISER_SOLID_FRACTION
        taper_height = max(1.0, height - solid_height)
        steps = 12
        for index in range(steps):
            y_start = (height / steps) * index
            y_end = (height / steps) * (index + 1)
            phase = min(1.0, max(0.0, (y_start - solid_height) / taper_height))
            taper = 0.5 + 0.5 * math.cos(math.pi * phase)
            if taper <= 0.004:
                continue
            fill_rect_with_cg(
                cg_context,
                ((core_rect_x, y_start), (core_width, y_end - y_start)),
                (*core_color[:3], core_color[3] * taper),
            )
            if soft_width > 0.0:
                fill_rect_with_cg(
                    cg_context,
                    ((soft_rect_x, y_start), (soft_width, y_end - y_start)),
                    (*soft_color[:3], soft_color[3] * taper),
                )

    def _draw_compact_accent(self) -> None:
        """When another notch app (e.g. Alcove) is occupying the notch's
        own black shape, don't draw a second competing black backdrop --
        just a clean, thin colored line at the same position the normal
        bar would use, reading as a status accent rather than a floating
        widget. No body fill, no glow layers, no edge highlights.
        Painted in the single identity color -- see _bar_identity_color
        for why a spatial per-LED accent was mostly invisible."""
        colors = self._bracket_colors(self._colors_for_draw_cached())
        width = self.bounds().size.width
        cg_context = current_cg_context()
        notch_width, wing_offset = self._notch_geometry()
        led_width = notch_width / LED_COUNT

        NSGraphicsContext.saveGraphicsState()
        column_x = 0.0
        while column_x < width:
            column_width = min(BLEND_COLUMN_WIDTH, width - column_x)
            center_x = column_x + column_width / 2.0
            red, green, blue, alpha = glow_color_for_column(colors, led_width, notch_width, wing_offset, center_x)
            if max(red, green, blue, alpha) > 0.001:
                fill_rect_with_cg(
                    cg_context,
                    ((column_x, 0.0), (column_width, COMPACT_ACCENT_HEIGHT)),
                    tone_mapped_led_color(
                        red, green, blue, alpha, boost=LED_CORE_BOOST, alpha_scale=0.95
                    ),
                )
            column_x += column_width
        NSGraphicsContext.restoreGraphicsState()


class VirtualStatusDevice(NSObject):
    def init(self):
        self = objc.super(VirtualStatusDevice, self).init()
        if self is not None:
            self.window = None
            self.view = None
            self.timer = None
            self.display_link = None
            self.wraps_menu_bar = False
            self._animation_active = True
            self._display_asleep = False
            self._static_frame_count = 0
            self._runtime_environment_cache = None
            self._power_observers_installed = False
            self._power_observer_centers = {}
            self._display_link_setup_failed = False
            self._presentation_generation = 0
            self._accessibility_display_preferences = AccessibilityDisplayPreferences()
            self._accessibility_generation = 0
            self._previous_target_timestamp = None
            self._sample_buffer = TwoSampleBuffer()
            self._sampler = None
            self._sampler_shutdown_incomplete = False
            self._sampler_factory = lambda buffer: ScreenBarSampler(
                buffer,
                led_count=LED_COUNT,
            )
            self._sampler_command = None
            self._alcove_buffer = AlcoveObservationBuffer()
            self._alcove_reducer = AlcoveObservationReducer()
            self._alcove_observer = None
            self._alcove_observer_shutdown_incomplete = False
            self._alcove_observer_factory = lambda buffer: AlcoveObservationWorker(buffer)
            self._alcove_request = None
            self._alcove_target_identity = None
            self._alcove_generation = 0
            self._alcove_request_id = 0
            self._last_safe_colors = None
            self._static_fallback_colors = _OFF_COLORS
            self._last_marked_colors = None
            self._frame_fallback_relevant = False
            self._alcove_relevant = False
            self._pointer_interaction_relevant = False
            self._presentation_schedule_reconciler = None
            self._program_identity = None
            self._enabled = True
            self._terminating = False
        return self

    @staticmethod
    def _command_with_generation(command, generation: int):
        if not isinstance(command, SamplerCommand):
            return command
        return SamplerCommand(
            generation=generation,
            program=command.program,
            parse_anchor=command.parse_anchor,
            static_fallback_program=command.static_fallback_program,
            sample_interval=command.sample_interval,
            motion=command.motion,
            next_visual_change_at=command.next_visual_change_at,
        )

    def _advance_presentation_generation(self, *, enqueue: bool) -> None:
        self._presentation_generation += 1
        self._previous_target_timestamp = None
        self._last_safe_colors = None
        self._last_marked_colors = None
        command = self._sampler_command
        if command is not None:
            command = self._command_with_generation(
                command,
                self._presentation_generation,
            )
            self._sampler_command = command
            if enqueue and self._sampler is not None:
                self._sampler.reconcile(command)

    def _resume_sampler(self) -> None:
        if (
            self._terminating
            or not self._enabled
            or self._display_asleep
            or not self._is_surface_visible()
            or self._sampler_command is None
            or self._sampler_shutdown_incomplete
        ):
            return
        if self._sampler is None:
            self._sampler = self._sampler_factory(self._sample_buffer)
            self._sampler_shutdown_incomplete = False
            self._sampler.reconcile(self._sampler_command)

    def _stop_sampler(self, *, fence_generation: bool = True) -> None:
        sampler = self._sampler
        self._sampler = None
        if sampler is None:
            return
        if fence_generation:
            self._advance_presentation_generation(enqueue=False)
        closed = sampler.close(timeout_seconds=SAMPLER_CLOSE_TIMEOUT_SECONDS)
        self._sampler_shutdown_incomplete = not closed

    def _stop_alcove_observer(self, *, fence_generation: bool = True) -> None:
        observer = self._alcove_observer
        self._alcove_observer = None
        if fence_generation:
            self._alcove_generation += 1
        self._alcove_request = None
        self._alcove_target_identity = None
        self._alcove_buffer.clear()
        self._alcove_reducer.reset()
        if observer is None:
            return
        closed = observer.close(timeout_seconds=SAMPLER_CLOSE_TIMEOUT_SECONDS)
        self._alcove_observer_shutdown_incomplete = not closed

    def _resume_alcove_observer(self) -> None:
        request = self._alcove_request
        if (
            request is None
            or self._terminating
            or not self._enabled
            or self._display_asleep
            or not self._is_surface_visible()
            or self._alcove_observer_shutdown_incomplete
        ):
            return
        if self._alcove_observer is None:
            self._alcove_observer = self._alcove_observer_factory(self._alcove_buffer)
        self._alcove_observer.reconcile(request)

    def _suspend_alcove_observer(self) -> None:
        """Stop capture after lookup loss without discarding held geometry."""
        observer = self._alcove_observer
        self._alcove_observer = None
        self._alcove_request = None
        self._alcove_buffer.clear()
        if observer is None:
            return
        closed = observer.close(timeout_seconds=SAMPLER_CLOSE_TIMEOUT_SECONDS)
        self._alcove_observer_shutdown_incomplete = not closed

    def _apply_latest_alcove_observation(self, *, now: float):
        observation = self._alcove_buffer.take()
        request = self._alcove_request
        if observation is not None and request is not None:
            self._alcove_reducer.apply(observation, request, now=now)
        return self._alcove_reducer.current(now=now)

    @staticmethod
    def _validated_fallback_colors(colors) -> tuple:
        if not isinstance(colors, tuple) or len(colors) != LED_COUNT:
            return _OFF_COLORS
        validated = []
        for color in colors:
            if not isinstance(color, tuple) or len(color) != 4:
                return _OFF_COLORS
            channels = []
            for channel in color:
                try:
                    value = float(channel)
                except (TypeError, ValueError, OverflowError):
                    return _OFF_COLORS
                if not math.isfinite(value):
                    return _OFF_COLORS
                channels.append(min(1.0, max(0.0, value)))
            validated.append(tuple(channels))
        return tuple(validated)

    def set_presentation_schedule_reconciler(self, reconciler) -> None:
        if reconciler is not None and not callable(reconciler):
            raise ValueError("presentation schedule reconciler must be callable")
        self._presentation_schedule_reconciler = reconciler
        self._publish_presentation_schedule()

    def set_pointer_interaction_relevant(self, relevant: bool) -> None:
        normalized = bool(relevant)
        if normalized == self._pointer_interaction_relevant:
            return
        self._pointer_interaction_relevant = normalized
        self._publish_presentation_schedule()

    def _presentation_deadline(self) -> float | None:
        command = self._sampler_command
        if getattr(command, "motion", None) is not MotionClass.FINITE:
            return None
        deadline = getattr(command, "next_visual_change_at", None)
        if (
            type(deadline) not in {int, float}
            or not math.isfinite(deadline)
            or float(deadline) <= 0.0
        ):
            return None
        return float(deadline)

    def presentation_scheduler_inputs(self) -> PresentationSchedulerInputs:
        return PresentationSchedulerInputs(
            screen_bar_enabled=bool(self._enabled),
            visible=self._is_surface_visible(),
            display_asleep=bool(self._display_asleep),
            app_terminating=bool(self._terminating),
            animation_active=bool(self._frame_fallback_relevant),
            next_visual_change_at=self._presentation_deadline(),
            alcove_enabled=bool(
                self.wraps_menu_bar
                and getattr(self, "follow_alcove_width", True)
            ),
            alcove_relevant=bool(self._alcove_relevant),
            pointer_interaction_relevant=bool(
                self._pointer_interaction_relevant
            ),
        )

    def _publish_presentation_schedule(self) -> None:
        reconcile = self._presentation_schedule_reconciler
        if reconcile is not None:
            reconcile(self.presentation_scheduler_inputs())

    def _demote_finite_presentation(self) -> None:
        command = self._sampler_command
        if command is None or command.motion is not MotionClass.FINITE:
            return
        self._advance_presentation_generation(enqueue=False)
        static_command = SamplerCommand(
            generation=self._presentation_generation,
            program=command.static_fallback_program,
            parse_anchor=time.monotonic(),
            static_fallback_program=command.static_fallback_program,
            sample_interval=1.0 / STATIC_WATCH_FPS,
            motion=MotionClass.STATIC,
            next_visual_change_at=None,
        )
        self._sampler_command = static_command
        self._program_identity = (
            static_command.program,
            None,
            MotionClass.STATIC,
            static_command.static_fallback_program,
            None,
            self._static_fallback_colors,
        )
        if self.view is not None:
            record_program = getattr(
                self.view,
                "setPresentationProgram_startedAt_",
                None,
            )
            if callable(record_program):
                record_program(static_command.program, static_command.parse_anchor)
            else:
                self.view.current_program = static_command.program
        self._animation_active = False
        if self._sampler is not None:
            self._sampler.reconcile(static_command)
        if self._is_surface_visible():
            self._refresh_render_cadence(False, force=True)
        else:
            self._publish_presentation_schedule()

    def presentationStaticDeadline(self) -> None:
        if not self._terminating:
            self._demote_finite_presentation()

    def presentationAlcoveObservation(self) -> None:
        if (
            not self._terminating
            and self._enabled
            and not self._display_asleep
            and self._is_surface_visible()
            and self._alcove_relevant
        ):
            self.reposition()

    def _is_surface_visible(self) -> bool:
        if self.window is None:
            return self.timer is not None or self.display_link is not None
        try:
            return bool(self.window.isVisible())
        except Exception:
            return False

    def _runtime_environment(self, *, force: bool = False):
        now = time.monotonic()
        cached = getattr(self, "_runtime_environment_cache", None)
        if not force and cached is not None and now - cached[0] < 2.0:
            previous = cached[1]
            return type(previous)(
                visible=self._is_surface_visible(),
                display_asleep=bool(self._display_asleep),
                low_power=previous.low_power,
                thermal=previous.thermal,
            )
        environment = runtime_render_environment(
            visible=self._is_surface_visible(),
            display_asleep=bool(self._display_asleep),
        )
        self._runtime_environment_cache = (now, environment)
        return environment

    def _refresh_render_cadence(
        self, animation_active: bool, *, force: bool = False
    ):
        schedule = choose_render_schedule(
            self._runtime_environment(force=force),
            animation_active,
            display_link_available=self._display_link_available(),
            next_visual_change_at=getattr(
                self._sampler_command,
                "next_visual_change_at",
                None,
            ),
        )
        self._render_schedule = schedule
        cadence = schedule.cadence
        if self.view is not None:
            self.view.setRenderFps_(cadence.sample_fps)
        if schedule.driver is RenderDriverKind.DISPLAY_LINK:
            installed = (
                self.display_link is not None
                and self.timer is None
                and not self._frame_fallback_relevant
            ) or (
                self._display_link_setup_failed
                and self.display_link is None
                and self.timer is None
                and self._frame_fallback_relevant
            )
        elif schedule.driver is RenderDriverKind.TIMER:
            installed = (
                self.display_link is None
                and self.timer is None
                and self._frame_fallback_relevant is bool(animation_active)
            )
        else:
            installed = (
                self.display_link is None
                and self.timer is None
                and not self._frame_fallback_relevant
            )
        if not installed:
            self._apply_render_schedule(schedule)
        else:
            self._publish_presentation_schedule()
        return cadence

    def _display_link_available(self) -> bool:
        """Return whether this macOS view exposes the macOS 14 display-link API."""
        return callable(
            getattr(self.view, "displayLinkWithTarget_selector_", None)
        )

    def _install_display_link(self) -> bool:
        """Register one native display callback, falling back on AppKit failure."""
        if self._display_link_setup_failed or not self._display_link_available():
            return False
        display_link = None
        try:
            display_link = self.view.displayLinkWithTarget_selector_(self, "redraw:")
            if display_link is None:
                raise RuntimeError("display link construction returned no driver")
            if not callable(getattr(display_link, "targetTimestamp", None)):
                raise RuntimeError("display link target timestamp is unavailable")
            set_frame_range = getattr(
                display_link,
                "setPreferredFrameRateRange_",
                None,
            )
            make_frame_range = getattr(Quartz, "CAFrameRateRangeMake", None)
            screen = NSScreen.mainScreen()
            maximum_fps = getattr(screen, "maximumFramesPerSecond", None)
            if (
                not callable(set_frame_range)
                or not callable(make_frame_range)
                or not callable(maximum_fps)
            ):
                raise RuntimeError("display link frame range is unavailable")
            maximum = min(max(60, int(maximum_fps())), 120)
            frame_range = make_frame_range(60, maximum, maximum)
            set_frame_range(frame_range)
            display_link.addToRunLoop_forMode_(
                NSRunLoop.currentRunLoop(), NSRunLoopCommonModes
            )
        except Exception:
            self._display_link_setup_failed = True
            if display_link is not None:
                try:
                    display_link.invalidate()
                except Exception:
                    pass
            return False
        self.display_link = display_link
        return True

    def _invalidate_frame_driver(self) -> None:
        """Stop either owned callback before the next driver is installed."""
        display_link = self.display_link
        timer = self.timer
        self.display_link = None
        self.timer = None
        self._frame_fallback_relevant = False
        self._frame_interval_current = None
        for driver in (display_link, timer):
            if driver is not None:
                try:
                    driver.invalidate()
                except Exception:
                    pass

    def _apply_render_schedule(self, schedule) -> None:
        """Transition atomically to the schedule's single frame driver."""
        self._invalidate_frame_driver()
        if schedule.driver is RenderDriverKind.PAUSED:
            self._publish_presentation_schedule()
            return
        if schedule.driver is RenderDriverKind.DISPLAY_LINK and self._install_display_link():
            self._publish_presentation_schedule()
            return
        self._frame_fallback_relevant = bool(self._animation_active)
        self._frame_interval_current = (
            schedule.cadence.interval if self._frame_fallback_relevant else None
        )
        self._publish_presentation_schedule()

    def _promote_animation(self) -> None:
        self._animation_active = True
        self._static_frame_count = 0
        self._refresh_render_cadence(True, force=True)

    def _install_power_observers(self) -> None:
        owned = getattr(self, "_power_observer_centers", None)
        if owned is None:
            owned = {}
            self._power_observer_centers = owned
        observers = (
            ("screenDidSleep:", NSWorkspaceScreensDidSleepNotification),
            ("screenDidWake:", NSWorkspaceScreensDidWakeNotification),
        )
        try:
            center = NSWorkspace.sharedWorkspace().notificationCenter()
        except Exception:
            return
        for selector, name in observers:
            if name in owned:
                continue
            try:
                center.addObserver_selector_name_object_(self, selector, name, None)
            except Exception:
                self._remove_power_observers()
                return
            owned[name] = center
        self._power_observers_installed = len(owned) == len(observers)

    def _remove_power_observers(self) -> None:
        owned = getattr(self, "_power_observer_centers", {})
        for name, center in tuple(owned.items()):
            try:
                center.removeObserver_name_object_(self, name, None)
            except Exception:
                continue
            del owned[name]
        self._power_observers_installed = len(owned) == 2

    def screenDidSleep_(self, _notification) -> None:
        self._set_display_asleep(True)

    def screenDidWake_(self, _notification) -> None:
        self._set_display_asleep(False)

    def screenDidChange_(self, _notification) -> None:
        """Fence sampler output when AppKit selects a different screen generation."""
        if self._terminating:
            return
        visible = self._is_surface_visible()
        self._invalidate_frame_driver()
        self._stop_alcove_observer()
        self._display_link_setup_failed = False
        self._advance_presentation_generation(enqueue=True)
        if visible:
            self._refresh_render_cadence(self._animation_active, force=True)
        else:
            self._publish_presentation_schedule()

    def _set_display_asleep(self, display_asleep: bool) -> None:
        self._display_asleep = bool(display_asleep)
        if self._display_asleep:
            self._invalidate_frame_driver()
            self._stop_sampler()
            self._stop_alcove_observer()
            if self.view is not None:
                self.view.setRenderFps_(0.0)
            self._publish_presentation_schedule()
        elif self._is_surface_visible():
            self._display_link_setup_failed = False
            self._resume_sampler()
            self._refresh_render_cadence(self._animation_active, force=True)
        else:
            self._publish_presentation_schedule()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled) and not self._terminating
        if not self._enabled:
            self.hide()
        else:
            self._publish_presentation_schedule()

    def set_accessibility_display_preferences(
        self,
        preferences: AccessibilityDisplayPreferences,
        *,
        generation: int,
    ) -> bool:
        if (
            type(preferences) is not AccessibilityDisplayPreferences
            or type(generation) is not int
            or generation <= self._accessibility_generation
        ):
            return False
        self._accessibility_display_preferences = preferences
        self._accessibility_generation = generation
        view = self.view
        if view is not None:
            apply_preferences = getattr(
                view,
                "setAccessibilityDisplayPreferences_",
                None,
            )
            if callable(apply_preferences):
                apply_preferences(preferences)
            view.setNeedsDisplay_(True)
        return True

    def set_wraps_menu_bar(self, enabled: bool) -> None:
        self.wraps_menu_bar = bool(enabled)
        if not self.wraps_menu_bar:
            self._alcove_relevant = False
            self._stop_alcove_observer()
        self._publish_presentation_schedule()

    def set_click_handler(self, handler) -> None:
        """Arms the bar as a click target (an ask is active: clicking
        jumps to the asking session). None restores the fully
        click-through window -- the default, and the safe state."""
        previous = (
            getattr(self.view, "click_handler", None)
            if self.view is not None
            else None
        )
        if self.view is not None:
            self.view.click_handler = handler
            view_window = self.view.window()
            if view_window is not None:
                view_window.invalidateCursorRectsForView_(self.view)
        if self.window is not None:
            self.window.setIgnoresMouseEvents_(handler is None)
        if handler is not previous and self._is_surface_visible():
            self._promote_animation()

    def set_min_glow(self, fraction: float) -> None:
        if self.view is not None:
            self.view.setMinGlow_(fraction)

    def set_standing_gauges(self, left_level: float, right_on: bool) -> None:
        if self.view is not None:
            self.view.set_standing_gauges(left_level, right_on)

    def set_follow_alcove(self, enabled: bool) -> None:
        self.follow_alcove_width = bool(enabled)
        if not self.follow_alcove_width:
            self._alcove_relevant = False
            self._stop_alcove_observer()
        self._publish_presentation_schedule()

    def set_bracket_style(self, style: str) -> None:
        if self.view is not None:
            self.view.setBracketStyle_(style)

    def set_geometry_overrides(self, gap_width: float | None, wing_length: float | None) -> None:
        """The user's manual bar geometry (None = Automatic) -- see
        virtual_window_frame_for_screen."""
        self.gap_width_override = gap_width
        self.wing_length_override = wing_length

    def show(self):
        if self._terminating or not self._enabled:
            return
        was_visible = self._is_surface_visible()
        if self.window is None:
            self._build_window()
        self.reposition()
        self._last_reposition_at = time.monotonic()
        self.window.orderFrontRegardless()
        if not was_visible:
            self._display_link_setup_failed = False
        self._install_power_observers()
        if (
            self.timer is None
            and self.display_link is None
            and not self._frame_fallback_relevant
        ):
            self._refresh_render_cadence(self._animation_active, force=True)
        self._resume_sampler()
        self._resume_alcove_observer()
        self._publish_presentation_schedule()

    def hide(self):
        self._invalidate_frame_driver()
        self._stop_sampler()
        self._stop_alcove_observer()
        if self.window is not None:
            self.window.orderOut_(None)
        self._remove_power_observers()
        # A later explicit show occurs in a fresh visible lifecycle and
        # re-registers observers before its first ongoing frame loop.
        self._display_asleep = False
        self._frame_interval_current = None
        self._publish_presentation_schedule()

    def terminate(self) -> None:
        """Bound teardown and permanently reject later driver or sampler work."""
        if self._terminating:
            return
        self._terminating = True
        self._enabled = False
        self._invalidate_frame_driver()
        self._stop_sampler()
        self._stop_alcove_observer()
        self._remove_power_observers()
        try:
            if self.window is not None:
                self.window.orderOut_(None)
        except Exception:
            pass
        finally:
            self.view = None
            self.window = None
            self._publish_presentation_schedule()

    def set_state(
        self,
        state: LedDisplayState,
        brightness: float,
        *,
        started_at: float | None = None,
    ):
        if self.view is not None:
            self.view.state = state
            self.view.brightness = normalize_brightness(brightness)
        self.set_program(
            program_for_display_state(
                state,
                led_count=LED_COUNT,
                brightness=normalize_brightness(brightness),
            ),
            started_at=started_at,
        )

    def set_battery(self, percent: int, brightness: float):
        self.show()
        self.view.setBatteryPercent_brightness_(percent, brightness)
        self._stop_sampler()
        self._sampler_command = None
        self._program_identity = None
        self._static_fallback_colors = tuple(tuple(color) for color in self.view.fixed_colors)
        self._last_safe_colors = self._static_fallback_colors
        self._animation_active = False
        self._refresh_render_cadence(False, force=True)

    def set_program(
        self,
        program: str,
        *,
        started_at: float | None = None,
        motion: MotionClass = MotionClass.CONTINUOUS,
        static_fallback_program: str = "off",
        static_fallback_colors=None,
        next_visual_change_at: float | None = None,
        dedupe_token: object | None = None,
    ):
        # Change-gated: the sync path repeats an unchanged program at up
        # to 4Hz during agent events; re-fronting the window, re-running
        # reposition() and resetting the 10Hz idle downshift every time
        # defeated the static-frame gate. (started_at is irrelevant when
        # the program is unchanged -- the view early-returns before
        # reading it.)
        fallback_colors = self._validated_fallback_colors(static_fallback_colors)
        identity = (
            ("token", dedupe_token)
            if dedupe_token is not None
            else (
                str(program),
                started_at,
                motion,
                str(static_fallback_program),
                next_visual_change_at,
                fallback_colors,
            )
        )
        if (
            self.window is not None
            and self.window.isVisible()
            and self.view is not None
            and identity == self._program_identity
        ):
            return
        if self._terminating or not self._enabled:
            return
        self.show()
        if self.view is None:
            return
        anchor = started_at if started_at is not None else time.monotonic()
        record_program = getattr(
            self.view,
            "setPresentationProgram_startedAt_",
            None,
        )
        if callable(record_program):
            record_program(str(program), anchor)
        else:
            self.view.current_program = str(program)
            self.view.started_at = anchor
        self._program_identity = identity
        self._advance_presentation_generation(enqueue=False)
        self._static_fallback_colors = fallback_colors
        self._sampler_command = SamplerCommand(
            generation=self._presentation_generation,
            program=str(program),
            parse_anchor=float(anchor),
            static_fallback_program=str(static_fallback_program),
            sample_interval=1.0 / ACTIVE_RENDER_FPS,
            motion=motion if isinstance(motion, MotionClass) else MotionClass.STATIC,
            next_visual_change_at=next_visual_change_at,
        )
        self._animation_active = self._sampler_command.motion is not MotionClass.STATIC
        if self._sampler is None:
            self._resume_sampler()
        else:
            self._sampler.reconcile(self._sampler_command)
        if (
            self._sampler_command.motion is MotionClass.FINITE
            and self._presentation_deadline() is None
        ):
            self._demote_finite_presentation()
            return
        self._refresh_render_cadence(self._animation_active, force=True)

    def reposition(self):
        screen = NSScreen.mainScreen()
        if screen is None or self.window is None:
            return
        render_screen_values = _screen_capture_values(screen)
        # Alcove coexistence is AUTOMATIC now -- the old three-way
        # compatibility setting was removed because with Alcove running,
        # every rendering style except the wings drew UNDERNEATH Alcove's
        # opaque backdrop, so the setting visibly did nothing. Semantics:
        # while Alcove runs, SidePulse always rides one level above it,
        # drawing the bracket (wings + risers) when wrap is on or a thin
        # accent underline when it's off; without Alcove, the normal full
        # render at the normal status level. Automatic size is ALWAYS
        # hardware-notch geometry: sizing to Alcove's overlay window was
        # tried twice and is a dead end -- Alcove keeps a huge (often
        # invisible) window whose bounds say nothing about what it's
        # drawing, so the bar ballooned across the menu bar. The user's
        # Bar Size sliders are the override for anything else.
        running_cache = getattr(self, "_alcove_running_cache", None)
        now = time.monotonic()
        if running_cache is not None and now - running_cache[0] < 3.0:
            alcove_active = running_cache[1]
        else:
            # Iterating every running app with bridged bundleIdentifier()
            # calls is too rich for a 2s cadence -- 3s TTL.
            alcove_active = is_alcove_running()
            self._alcove_running_cache = (now, alcove_active)
        wings_only = alcove_active and self.wraps_menu_bar
        compact = alcove_active and not self.wraps_menu_bar

        gap_override = getattr(self, "gap_width_override", None)
        wing_override = getattr(self, "wing_length_override", None)
        self._alcove_relevant = bool(
            wings_only
            and wing_override is None
            and getattr(self, "follow_alcove_width", True)
        )
        effective_gap = gap_override
        if gap_override is None:
            # Pixel-exact notch, measured once per screen configuration
            # (the notch can't change at runtime) -- see
            # measured_notch_bounds for why this beats a model table.
            frame = screen.frame()
            cache_key = (round(frame.size.width), round(frame.size.height))
            cache = getattr(self, "_notch_measure_cache", None)
            if cache is None or cache[0] != cache_key:
                bounds = measured_notch_bounds(
                    screen,
                    below_window_number=int(self.window.windowNumber() or 0),
                )
                self._notch_measure_cache = (cache_key, bounds)
                cache = self._notch_measure_cache
            if cache[1] is not None:
                effective_gap = cache[1][1]
        # Follow Alcove's visible capsule through the serial observer.
        # Reposition resolves AppKit and window identity on main, consumes
        # only a validated plain result, and never captures or scans pixels.
        follow_width = None
        follow_center_x = None
        follow_observation = None
        if (
            wings_only
            and wing_override is None
            and getattr(self, "follow_alcove_width", True)
        ):
            band = max(24.0, window_height_for_notch_depth(notch_depth_for_screen(screen)))
            screen_values = render_screen_values
            window_values = (
                None
                if screen_values is None
                else _alcove_window_values(screen_values[2], screen_values[4])
            )
            observation = None
            if screen_values is not None and window_values is not None:
                (
                    screen_id,
                    display_id,
                    screen_x,
                    screen_y,
                    screen_width,
                    screen_height,
                    scale,
                ) = screen_values
                window_number, window_x, window_y, window_width = window_values
                target_identity = (screen_id, window_number)
                if target_identity != self._alcove_target_identity:
                    self._alcove_generation += 1
                    self._alcove_target_identity = target_identity
                    self._alcove_buffer.clear()
                    self._alcove_reducer.reset()
                else:
                    observation = self._apply_latest_alcove_observation(now=now)
                self._alcove_request_id += 1
                if self._alcove_request_id >= 2**63:
                    self._alcove_request_id = 1
                    self._alcove_generation += 1
                self._alcove_request = AlcoveCaptureRequest(
                    request_id=self._alcove_request_id,
                    generation=self._alcove_generation,
                    screen_id=screen_id,
                    display_id=display_id,
                    window_number=window_number,
                    screen_x=screen_x,
                    screen_y=screen_y,
                    screen_width=screen_width,
                    screen_height=screen_height,
                    window_x=window_x,
                    window_y=window_y,
                    window_width=window_width,
                    menu_band_height=band,
                    scale=scale,
                    requested_at=now,
                )
            else:
                self._suspend_alcove_observer()
                observation = self._alcove_reducer.current(now=now)
            if self._alcove_request is not None:
                self._resume_alcove_observer()
            if observation is not None:
                follow_observation = observation
                follow_width = observation.width
                follow_center_x = observation.center_x
            if follow_width != getattr(self, "_last_follow_width", None):
                self._last_follow_width = follow_width
                try:
                    from .status_bar import log_status_bar

                    log_status_bar(
                        f"alcove follow: {follow_width:.0f}pt"
                        if follow_width is not None
                        else "alcove follow: hardware geometry"
                    )
                except Exception:
                    pass
        else:
            self._stop_alcove_observer()
        window_frame = virtual_window_frame_for_screen(
            screen,
            wrap_menu_bar=self.wraps_menu_bar,
            gap_width=effective_gap,
            wing_length=wing_override,
            alcove_total_width=follow_width,
            alcove_center_x=follow_center_x,
        )
        current = self.window.frame()
        frame_changed = (
            abs(current.origin.x - window_frame[0][0]) > 0.5
            or abs(current.origin.y - window_frame[0][1]) > 0.5
            or abs(current.size.width - window_frame[1][0]) > 0.5
            or abs(current.size.height - window_frame[1][1]) > 0.5
        )
        if frame_changed:
            self.window.setFrame_display_(window_frame, True)
        self.window.setLevel_(ABOVE_ALCOVE_WINDOW_LEVEL if alcove_active else STATUS_WINDOW_LEVEL)
        if self.view is not None:
            set_geometry_identity = getattr(
                self.view, "setRenderGeometryIdentity_", None
            )
            if render_screen_values is not None and callable(set_geometry_identity):
                set_geometry_identity(
                    (render_screen_values[0], render_screen_values[6])
                )
            self.view.setHasNotch_(screen_has_notch(screen))
            self.view.setCompactMode_(compact)
            self.view.setWingsOnlyMode_(wings_only)
            set_alcove_silhouette = getattr(self.view, "setAlcoveSilhouette_", None)
            if callable(set_alcove_silhouette):
                set_alcove_silhouette(
                    None
                    if follow_observation is None
                    else (
                        follow_observation.center_x,
                        follow_observation.width,
                        follow_observation.height,
                        follow_observation.contour,
                    )
                )
            if frame_changed:
                self.view.setFrame_(((0, 0), window_frame[1]))
            if self.wraps_menu_bar:
                notch_width = float(effective_gap) if effective_gap else slot_width_for_screen(screen)
            else:
                notch_width = None
            self.view.setNotchWidth_(notch_width)
        self._publish_presentation_schedule()

    def _build_window(self):
        self._invalidate_frame_driver()
        self._display_link_setup_failed = False
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (WINDOW_WIDTH, WINDOW_HEIGHT)),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setHasShadow_(False)
        self.window.setIgnoresMouseEvents_(True)
        self.window.setLevel_(STATUS_WINDOW_LEVEL)
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            # Stationary: Mission Control / Expose must not scoop up the
            # notch bar and float it around like a document window.
            | (1 << 4)
        )
        self.view = VirtualLedView.alloc().initWithFrame_(((0, 0), (WINDOW_WIDTH, WINDOW_HEIGHT)))
        self.window.setContentView_(self.view)

    @objc.IBAction
    def redraw_(self, sender):
        if (
            self._terminating
            or not self._enabled
            or self._display_asleep
            or not self._is_surface_visible()
        ):
            return
        view = self.view
        if view is None:
            return
        callback_timestamp = time.monotonic()
        target_timestamp = presentation_time(
            sender,
            callback_timestamp=callback_timestamp,
            previous_target=self._previous_target_timestamp,
        )
        self._previous_target_timestamp = target_timestamp
        frame_colors = display_colors_for_tick(
            self._sample_buffer,
            PresentationTick(
                callback_timestamp=callback_timestamp,
                target_timestamp=target_timestamp,
                generation=self._presentation_generation,
            ),
            last_safe_colors=self._last_safe_colors,
            static_fallback_colors=self._static_fallback_colors,
        )
        self._last_safe_colors = frame_colors
        present = getattr(view, "setPresentationColors_", None)
        if callable(present):
            present(frame_colors)
        else:
            view._presentation_colors = frame_colors
        painted = tuple(
            tuple(int(round(channel * 1024.0)) for channel in color)
            for color in frame_colors
        )
        if painted != self._last_marked_colors:
            self._last_marked_colors = painted
            view.setNeedsDisplay_(True)
