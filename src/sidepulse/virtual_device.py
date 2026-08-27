from __future__ import annotations

import math
import threading
import time
from enum import Enum

import objc
import Quartz
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSColorSpace,
    NSCursor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGradient,
    NSGraphicsContext,
    NSLineBreakByTruncatingTail,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
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
from Foundation import NSObject, NSRunLoop, NSRunLoopCommonModes, NSString
from Quartz import CGContextFillRect, CGContextSetRGBFillColor

from .accessibility_display import AccessibilityDisplayPreferences
from .draw_guard import guard_draw
from .alcove_observation import (
    ALCOVE_STATUS_LOG_LINES,
    AlcoveCaptureRequest,
    AlcoveCaptureStatus,
    AlcoveObservationBuffer,
    AlcoveObservationReducer,
    AlcoveObservationWorker,
    note_alcove_status,
    screen_recording_granted,
)
from .led_status import LedDisplayState, normalize_brightness, program_for_display_state
from .led_wasm import LedWasmUnavailableError, SdLedWasmController
from .presentation_policy import MotionClass
from .presentation_scheduler import PresentationSchedulerInputs
from .render_policy import (
    ACTIVE_RENDER_FPS,
    GENTLE_MOTION_FPS,  # noqa: F401  -- re-exported; callers patch it here
    STATIC_WATCH_FPS,
    BoundedRenderCache,
    GlowGeometryKey,
    GlowPaintKey,
    RenderDriverKind,
    choose_render_schedule,
    display_link_fps,
    presentation_hold_seconds,
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
# "never set" -- distinct from every real value, including None.
_UNSET = object()
ALCOVE_BUNDLE_ID = "com.henrikruscon.Alcove"

#: How long the Screen Bar may hold a deduped program before it is
#: re-commanded regardless.
#:
#: The LED controller has always had this (``LED_REASSERT_SECONDS``, 60s)
#: and the Screen Bar had NOTHING: once ``_program_identity`` matched, the
#: gate returned forever. The asymmetry was backwards. The strip is
#: animated by firmware, which cannot stop; the notch is animated by a
#: Python sampler with six silent ``return`` paths in
#: ``ScreenBarSampler._execute_command`` (superseded generation, missing
#: neighbour frame, interval wait, bare except...) and a ``_step`` that
#: returns None on any LED-count or channel-range mismatch. Any one of
#: them left the notch with no motion loop, falling back to
#: ``last_safe_colors`` -- one frozen colour, no error, no log, and
#: nothing to ever re-command it. Reported live as "the screen bar and
#: pulse bar aren't doing anything right now; they're just stuck on a
#: color".
#:
#: Shorter than the strip's 60s because re-applying here is an in-process
#: sampler reconcile, not ~30 syscalls plus an fsync to USB storage, and
#: because the phase anchor is absolute -- a reassert resumes the same
#: phase rather than restarting the animation.
SCREEN_BAR_REASSERT_SECONDS = 10.0
# CGWindowList reports an owner NAME, not a bundle id, so the window probe
# matches on that while process detection matches the bundle id. The name
# is DEFINED in alcove_observation (which also probes the window list for
# the settings pane and doctor) and re-exported here, not copied -- a
# second copy of an Alcove constant is how the last Alcove bug got in.
COMPACT_ACCENT_HEIGHT = 2.5
# NSStatusWindowLevel without another SDK dependency; and the level the
# wrap bracket rides at while Alcove is running -- kCGMaximumWindowLevel,
# one above Alcove's own near-maximum overlay, so the bracket's glow
# stays visible around the hardware notch instead of buried under
# Alcove's opaque backdrop. The window ignores mouse events, so being
# top-most is purely visual.
STATUS_WINDOW_LEVEL = 25
# Fallback only. Alcove's real layer is MEASURED at runtime (see
# alcove_window_level) -- measured live on 2026-08-14 at 2147483629 and
# 2147483628, so this constant happens to be one above today. Trusting
# it blindly means that the day Alcove raises its own level we sit
# silently underneath it forever, and the bracket simply disappears.
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
# alcove_observation.py owns the capsule measurement pipeline and its
# timings. These two are RE-EXPORTED from it rather than redefined here:
# a second copy shadowed the live constants, which is precisely how the
# next Alcove bug would have been introduced.
# `noqa: F401` is load-bearing, not decoration: `ruff --fix` deleted both of
# these as unused, which is exactly the "second copy shadows the live
# constant" failure the comment above warns about -- only in reverse.
from .alcove_observation import (  # noqa: E402, F401
    ALCOVE_HOLD_SECONDS,
    ALCOVE_OWNER_NAME,
)
WING_SAFETY_MARGIN = 28.0
WING_MIN_USABLE = 24.0
# The linked-hardware phase handshake only fires on programs that have
# been showing at least this long -- see reanchor_program.
REANCHOR_STEADY_SECONDS = 5.0

# --- The announcer pill ---------------------------------------------------
# The three-surfaces law gives the Screen Bar the ANNOUNCER job: it
# carries WORDS -- the session name and the actual question. The notch
# body's pixels are hardware-occluded (they sit behind the physical
# camera housing), so the words live in a small pill just below the
# notch, shown only while an actionable ask is live. Clicking it jumps
# to the asking session, same as clicking the bar.
ANNOUNCER_NAME_CAP = 40
ANNOUNCER_QUESTION_CAP = 80
ANNOUNCER_PILL_HEIGHT = 22.0
ANNOUNCER_PILL_MAX_WIDTH = 460.0
ANNOUNCER_PILL_PADDING = 14.0


def announcer_text_for_attention(projection) -> str | None:
    """One bounded line: session name plus the actual question."""
    rows = getattr(projection, "actionable_attention", ()) if projection else ()
    if not rows:
        return None
    row = rows[0]
    name = " ".join(str(getattr(row, "display_name", "") or "").split())
    name = name[:ANNOUNCER_NAME_CAP] or str(getattr(row, "provider", "agent")).title()
    message = getattr(getattr(row, "source_status", None), "message", None) or ""
    question = " ".join(str(message).split())[:ANNOUNCER_QUESTION_CAP]
    return f"{name} — {question}" if question else f"{name} needs you"
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
WING_RISER_SOLID_FRACTION = 0.45
LED_BLEND_RADIUS_LEDS = 1.5
BLEND_COLUMN_WIDTH = 2.0
LED_GLOW_HEIGHT = 11.0
# The notch's bloom, as per-layer LEVEL only -- a fraction of the LED's own
# colour, never a multiplier above it. Two things used to live here and both
# had to go, for the same reason:
#
#   LED_GAMMA = 0.86 raised every channel to a power before scaling, so the
#   Screen Bar emitted light proportional to (code/255)**1.89 while the strip
#   emitted (code/255)**1.0 -- and it lifted small channels more than large
#   ones, which is a hue shift dressed up as a tone map.
#
#   LED_CORE_BOOST = 1.22 / LED_HOTLINE_BOOST = 1.46 then multiplied PAST full
#   scale and clipped. On #FF3A00 red was already at full so it clipped while
#   green took its whole 22%, and the ask colour arrived on screen as #FF5700 --
#   orange. That is not bloom, it is a hue error: the boost was buying "hotter
#   than the colour" the only way an already-maxed channel can, by desaturating
#   towards white. A level above 1.0 cannot exist on this surface, so these are
#   1.0 and the halo comes from the layers that surround the core instead --
#   the 0.82 and 0.64 glow rects above the band, which are true fractions and
#   are preserved exactly.
LED_CORE_BOOST = 1.0
LED_HOTLINE_BOOST = 1.0
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

# How long an uncommitted Studio preview may own the Screen Bar before the
# live render takes it back on its own. Hover exit is what normally ends a
# preview; this is the backstop for the cases where no exit event ever
# arrives (the window closed under the pointer, the pane was torn down
# mid-hover). Checked against the live sync path, which is the only clock
# this class can rely on already ticking.
PREVIEW_HOLD_MAX_SECONDS = 20.0
_OFF_COLORS = ((0.0, 0.0, 0.0, 0.0),) * LED_COUNT


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000.0)


def virtual_display_state_for_projection(projection, active_signal=None) -> LedDisplayState:
    """Screen Bar state adapter for the shared attention projection."""
    from .led_status import display_state_for_projection

    return display_state_for_projection(projection, active_signal)


def measured_notch_silhouette(
    screen, below_window_number: int = 0, max_width: float | None = None
):
    """The hardware notch's full measured outline: per-row black-run
    insets through the notch's depth, so the drawn body can follow the
    REAL corner curve instead of guessing a radius. The top-row width
    alone left the body's straight walls standing beside the physical
    notch's curved bottom corners -- dark slivers a few points wide,
    visible every day. Returns (x, top_width, insets) where insets[r] is
    (left_inset, right_inset) in points for pixel row r from the top, or
    None under exactly the same validation that guards the width scan."""
    try:
        depth = int(round(notch_depth_for_screen(screen)))
        if depth <= 2:
            return None
        runs, scale, frame_x = _captured_notch_runs(
            screen, rows=depth, below_window_number=below_window_number
        )
        return _validated_notch_silhouette(runs, scale, frame_x, max_width=max_width)
    except Exception:
        return None


def _captured_notch_runs(screen, *, rows: int, below_window_number: int = 0):
    """(per-row center black runs in px, scale, frame_x) for the top rows."""
    import Quartz

    frame = screen.frame()
    rect = Quartz.CGRectMake(
        float(frame.origin.x), 0.0, float(frame.size.width), float(rows)
    )
    option = (
        Quartz.kCGWindowListOptionOnScreenBelowWindow
        if below_window_number
        else Quartz.kCGWindowListOptionOnScreenOnly
    )
    image = Quartz.CGWindowListCreateImage(
        rect, option, int(below_window_number), Quartz.kCGWindowImageNominalResolution
    )
    if image is None:
        raise ValueError("no composite image")
    width_px = int(Quartz.CGImageGetWidth(image))
    if width_px <= 0:
        raise ValueError("empty composite image")
    scale = width_px / float(frame.size.width)
    runs = _cgimage_black_runs(image, rows=rows, width_px=width_px)
    if runs is None:
        runs = _bitmap_rep_black_runs(image, rows=rows, width_px=width_px)
    return runs, scale, float(frame.origin.x)


def _cgimage_black_runs(image, *, rows: int, width_px: int):
    """Per-row center black runs read from the image's raw bytes.

    ``colorAtX_y_`` crossed the PyObjC bridge once per PIXEL -- >100k
    NSColor round trips per measure, 150-300ms of main-thread time. The
    raw buffer is one bridge crossing and the scan is a plain byte loop.
    Returns None when the pixel format is not the 32-bit layout window
    composites use, and the caller falls back to the bridge path.
    """
    import Quartz

    try:
        bytes_per_pixel = int(Quartz.CGImageGetBitsPerPixel(image)) // 8
        if bytes_per_pixel != 4:
            return None
        bytes_per_row = int(Quartz.CGImageGetBytesPerRow(image))
        available = int(Quartz.CGImageGetHeight(image))
        provider = Quartz.CGImageGetDataProvider(image)
        data = bytes(Quartz.CGDataProviderCopyData(provider))
    except Exception:
        return None
    if available <= 0 or len(data) < bytes_per_row * available:
        return None
    runs = []
    for row in range(rows):
        y = min(row, available - 1)
        runs.append(
            _center_black_run_in_bytes(
                data, y * bytes_per_row, width_px, bytes_per_pixel
            )
        )
    return runs


def _center_black_run_in_bytes(data, base: int, width_px: int, bytes_per_pixel: int):
    """The pure-black run covering the horizontal center, in px, or None.

    A black pixel in an opaque 32-bit composite is three zero colour
    channels plus a full alpha byte, in whichever byte order the image
    uses -- so "at most one nonzero byte in the pixel" identifies black
    without knowing the channel layout: any actual colour keeps alpha
    AND at least one colour channel nonzero. A fully transparent pixel
    (all four bytes zero) also reads black, exactly as the NSColor path
    always treated it.
    """
    center_px = width_px / 2.0
    run_start = None
    best = None
    for x in range(width_px):
        i = base + x * bytes_per_pixel
        nonzero = (
            (1 if data[i] else 0)
            + (1 if data[i + 1] else 0)
            + (1 if data[i + 2] else 0)
            + (1 if data[i + 3] else 0)
        )
        if nonzero <= 1:
            if run_start is None:
                run_start = x
            continue
        if run_start is not None:
            if run_start <= center_px <= x:
                best = (run_start, x)
            run_start = None
    if run_start is not None and run_start <= center_px:
        best = (run_start, width_px)
    return best


def _bitmap_rep_black_runs(image, *, rows: int, width_px: int):
    """The original per-pixel bridge path, kept as the format fallback."""
    from AppKit import NSBitmapImageRep

    rep = NSBitmapImageRep.alloc().initWithCGImage_(image)
    if rep is None:
        raise ValueError("unreadable composite image")
    available = int(rep.pixelsHigh())
    runs = []
    for row in range(rows):
        y = min(row, available - 1) if available > 0 else 0
        runs.append(_center_black_run(rep, y, width_px))
    return runs


def _center_black_run(rep, y: int, width_px: int):
    """The pure-black run covering the horizontal center, in px, or None."""
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
    return best


def _validated_notch_silhouette(runs, scale, frame_x, max_width: float | None = None):
    """Validate raw per-row runs into (x, top_width, per-row insets).

    Every rejection is a fact about the composite, not the notch: the
    notch itself cannot widen with depth, cannot out-grow the gap the
    system reports between its auxiliary areas, and cannot vanish
    mid-face. Anything violating those is an impostor (Alcove's capsule
    was measured at 266pt over this panel's 186pt notch and sailed
    through the old 120-320 sanity band) or a corrupted capture -- both
    fall back to the parametric shape rather than poison the body."""
    if not runs or runs[0] is None or scale <= 0.0:
        return None
    left_0, right_0 = runs[0]
    top_width = (right_0 - left_0) / scale
    # Sanity band: MacBook notches live in roughly 150-260pt.
    if not (120.0 <= top_width <= 320.0):
        return None
    if max_width is not None and top_width > float(max_width):
        return None
    insets: list[tuple[float, float]] = []
    floor_left = 0.0
    floor_right = 0.0
    for run in runs:
        if run is None:
            return None
        left, right = run
        inset_left = (left - left_0) / scale
        inset_right = (right_0 - right) / scale
        # A row WIDER than the top row is not a notch silhouette.
        if inset_left < -1.0 or inset_right < -1.0:
            return None
        inset_left = max(floor_left, inset_left)
        inset_right = max(floor_right, inset_right)
        # A run collapsing toward nothing mid-face is not the notch.
        if inset_left + inset_right > top_width - 8.0:
            return None
        insets.append((inset_left, inset_right))
        floor_left = inset_left
        floor_right = inset_right
    return (frame_x + left_0 / scale, top_width, tuple(insets))


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


def _hardware_slot_ceiling(screen) -> float | None:
    """The widest the physical notch could possibly be: the raw gap the
    system reports between its auxiliary top areas, plus antialiasing
    slop. None on screens that report no areas (external displays)."""
    try:
        left = screen.auxiliaryTopLeftArea()
        right = screen.auxiliaryTopRightArea()
        gap = right.origin.x - (left.origin.x + left.size.width)
        if gap >= 120.0:
            return float(gap) + 6.0
    except Exception:
        pass
    return None


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


class ScreenBarWingState(str, Enum):
    """Why "extend glow along the menu bar" is or is not doing anything.

    ``wing_width_for_screen`` answers with 0.0 for four unrelated facts:
    this display reports no notch gap at all (every external monitor and
    every non-notched Mac), the gap exists but the menu bar is too full
    to lend any of it, the screen would not report its areas, and -- the
    only one anybody expected -- the user turned the switch off. All four
    render identically: a bar the same size it was, and a switch still
    reading ON.
    """

    EXTENDED = "extended"
    NO_SAFE_AREA = "no_safe_area"
    MENU_BAR_FULL = "menu_bar_full"
    UNREADABLE = "unreadable"
    #: A manual wing length overrides the measurement entirely.
    MANUAL = "manual"
    NOT_EXTENDING = "not_extending"


def screen_bar_wing_state(
    screen,
    notch_width: float,
    *,
    wrap_menu_bar: bool,
    wing_length: float | None = None,
) -> ScreenBarWingState:
    """The reason behind ``wing_width_for_screen``'s number.

    Deliberately re-derived from the same two auxiliary areas rather than
    inferred from the returned width: a 0.0 is the value being explained,
    so it cannot also be the evidence.
    """
    if not wrap_menu_bar:
        return ScreenBarWingState.NOT_EXTENDING
    if wing_length is not None:
        return ScreenBarWingState.MANUAL
    try:
        left = screen.auxiliaryTopLeftArea()
        right = screen.auxiliaryTopRightArea()
    except Exception:
        return ScreenBarWingState.UNREADABLE
    try:
        left_width = float(left.size.width)
        right_width = float(right.size.width)
    except Exception:
        return ScreenBarWingState.UNREADABLE
    if left_width <= 0.0 or right_width <= 0.0:
        # No notch, so no menu-bar area beside one. Nothing is broken and
        # nothing will ever happen here either.
        return ScreenBarWingState.NO_SAFE_AREA
    if wing_width_for_screen(screen, notch_width) <= 0.0:
        return ScreenBarWingState.MENU_BAR_FULL
    return ScreenBarWingState.EXTENDED


def space_hides_menu_bar(screen) -> bool:
    """True on a full-screen space: the menu bar is gone, so the
    screen's visible frame reaches the very top. A bar drawn at the
    notch then floats over someone's full-screen VIDEO -- the 2026-08-21
    report. (A user who auto-hides the menu bar reads the same way;
    the show-in-full-screen switch exists for them.)"""
    try:
        frame = screen.frame()
        visible = screen.visibleFrame()
        top_inset = (frame.origin.y + frame.size.height) - (
            visible.origin.y + visible.size.height
        )
        return top_inset < 1.0
    except Exception:
        return False


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
    alcove_total_height: float | None = None,
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
    depth = notch_depth_for_screen(screen)
    if alcove_total_width is not None and alcove_total_height is not None:
        # Follow the capsule's measured DEPTH too. The window used to
        # keep hardware-notch height while an expanded Alcove capsule
        # ran twice as tall, so the status band rendered mid-capsule as
        # a detached smear ("it looks bad", reported live 2026-08-27).
        # With the observed height, the band hugs the capsule's real
        # bottom edge exactly as it hugs the notch when not following.
        depth = max(depth, float(alcove_total_height))
    height = window_height_for_notch_depth(depth)
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


class AlcovePresenceProbe:
    """Answers "is Alcove running" without ever blocking the main thread.

    ``bundleIdentifier()`` on a lazily-faulted NSRunningApplication is a
    BLOCKING XPC round trip to LaunchServices
    (_LSCopyApplicationInformation ->
    xpc_connection_send_message_with_reply_sync), and
    ``is_alcove_running`` does one per running app. Measured on the live
    app with `sample`: 636 of 4503 main-thread samples -- 14.1% of the
    main thread, one full core's worth of a 100.7% CPU process -- inside
    this one call, from ``reposition()`` on a 2s timer whose 3s TTL did
    not even cover its own cadence.

    So the main thread only ever READS a cached answer, and refreshes
    happen on a daemon worker. The very first read is synchronous, once
    per process, because the first layout has to be right and there is
    nothing cached to be right with.
    """

    def __init__(self, *, probe=None, ttl_seconds: float = 3.0) -> None:
        # Late-bound on purpose: `probe=is_alcove_running` captured the
        # function at CLASS DEFINITION, so tests patching the module
        # function patched nothing -- five "alcove honesty" tests
        # silently depended on the real Alcove app being open.
        self._probe = probe if probe is not None else (lambda: is_alcove_running())
        self._ttl_seconds = max(0.0, float(ttl_seconds))
        self._value: bool | None = None
        self._sampled_at = 0.0
        self._refreshing = False
        self._lock = threading.Lock()

    def running(self, *, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else float(now)
        with self._lock:
            value = self._value
            fresh = value is not None and moment - self._sampled_at < self._ttl_seconds
            if fresh:
                return value
            if value is None:
                # Cold start only. Never again on this main thread.
                self._value = self._sample()
                self._sampled_at = moment
                return self._value
            if self._refreshing:
                return value
            self._refreshing = True
        self._start_refresh()
        return value

    def _sample(self) -> bool:
        try:
            return bool(self._probe())
        except Exception:
            return False

    def _start_refresh(self) -> None:
        thread = threading.Thread(
            target=self._refresh,
            name="sidepulse-alcove-probe",
            daemon=True,
        )
        thread.start()

    def _refresh(self) -> None:
        sampled = self._sample()
        with self._lock:
            self._value = sampled
            self._sampled_at = time.monotonic()
            self._refreshing = False


def _on_screen_windows() -> list:
    """Every on-screen window, or an empty list. Injectable for tests.

    The empty list is deliberately NOT a reason here, and this is the one
    place in the Alcove path where that is defensible: the only consumer
    is alcove_window_level, which already treats both "no windows" and
    "no Alcove windows" as "keep the floor level". Whether following is
    working at all is answered by capture_alcove_observation's status,
    which never collapses its endings -- so nothing user-visible depends
    on telling those two apart here.
    """
    try:
        import Quartz

        return list(
            Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
            )
            or []
        )
    except Exception:
        return []


def alcove_window_level(
    default: int = ABOVE_ALCOVE_WINDOW_LEVEL,
    *,
    window_lister=None,
) -> int:
    """One above Alcove's HIGHEST on-screen window layer.

    We draw a bracket around Alcove's capsule, which only works while we
    are above it. The level used to be a hardcoded near-INT32_MAX guess;
    it is right today purely by luck, and nothing would ever tell us if
    that stopped being true -- the bracket would just vanish behind
    Alcove with no error. Measure instead, and keep the constant as the
    floor so we never end up BELOW the old behavior.
    """
    lister = window_lister or _on_screen_windows
    try:
        levels = [
            int(entry.get("kCGWindowLayer", 0))
            for entry in lister()
            if str(entry.get("kCGWindowOwnerName", "")) == ALCOVE_OWNER_NAME
        ]
    except Exception:
        return default
    if not levels:
        return default
    # Never exceed the maximum a window level may legally take.
    return max(default, min(max(levels) + 1, 2147483631))


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
            if str(entry.get("kCGWindowOwnerName", "")) != ALCOVE_OWNER_NAME:
                continue
            window_number = int(entry.get("kCGWindowNumber", 0))
            window_layer = int(entry.get("kCGWindowLayer", 0))
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
            (
                window_y,
                -window_width,
                # Alcove stacks an empty backing window at the same bounds
                # as the visible capsule; the capsule lives on the HIGHER
                # window layer, and a bare window-number tiebreak selected
                # the empty backing sheet — every capture then scanned as
                # "image unusable" and the bar never followed the capsule.
                -window_layer,
                -window_number,
                window_number,
                window_x,
                window_y,
                window_width,
            )
        )
    if not candidates:
        return None
    (
        _sort_y,
        _sort_width,
        _sort_layer,
        _sort_number,
        window_number,
        window_x,
        window_y,
        window_width,
    ) = min(candidates)
    return window_number, window_x, window_y, window_width


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
    # Shared scale, capped at the brightest channel, for the same reason
    # tone_mapped_led_color uses one: clamping each channel on its own turns
    # "make this more visible" into "make this a different colour".
    peak = max(red, green, blue)
    factor = floor / alpha
    if peak > 0.0:
        factor = min(factor, 1.0 / peak)
    return (red * factor, green * factor, blue * factor, floor)


def notch_bar_path_from_insets(rect, insets, band_height: float = LED_BAND_HEIGHT):
    """The classic body following the REAL notch's measured outline.

    ``rect`` is the body's box in view coordinates (width = the measured
    top width, height = notch depth + the LED band); ``insets[r]`` is the
    measured (left, right) inset in points for pixel row r counted from
    the TOP of the notch. The walls trace those insets exactly -- black
    over black inside the physical notch, never a sliver beside its
    curved corners -- and the band below continues at the BOTTOM row's
    width with a small soft corner of its own."""
    x, y = rect[0]
    width, height = rect[1]
    top = y + height
    rows = len(insets)
    if rows <= 0 or width <= 0.0 or height <= band_height:
        return notch_bar_path(rect)
    bottom_left = x + insets[-1][0]
    bottom_right = x + width - insets[-1][1]
    corner = max(0.0, min(4.0, band_height, (bottom_right - bottom_left) / 2.0))

    path = NSBezierPath.bezierPath()
    path.moveToPoint_((x + insets[0][0], top))
    path.lineToPoint_((x + width - insets[0][1], top))
    for row in range(rows):
        path.lineToPoint_((x + width - insets[row][1], top - row - 1.0))
    path.lineToPoint_((bottom_right, y + corner))
    path.curveToPoint_controlPoint1_controlPoint2_(
        (bottom_right - corner, y),
        (bottom_right, y + corner * 0.45),
        (bottom_right - corner * 0.45, y),
    )
    path.lineToPoint_((bottom_left + corner, y))
    path.curveToPoint_controlPoint1_controlPoint2_(
        (bottom_left, y + corner),
        (bottom_left + corner * 0.45, y),
        (bottom_left, y + corner * 0.45),
    )
    path.lineToPoint_((bottom_left, y + band_height))
    for row in reversed(range(rows)):
        path.lineToPoint_((x + insets[row][0], top - row - 1.0))
    path.lineToPoint_((x + insets[0][0], top))
    path.closePath()
    return path


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

    # Mirrors rolling_program(): 1400 ms pulses staggered by 170 ms.
    cycle = 1.4 + 0.17 * (LED_COUNT - 1)
    colors = []
    for index in range(LED_COUNT):
        local = (elapsed % cycle) - index * 0.17
        amount = 0.0
        if 0.0 <= local <= 1.4:
            amount = math.sin(math.pi * local / 1.4) ** 2
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
    """The Screen Bar's last step: one glow layer's colour, ready for CG.

    This is the surface half of the reconciliation in led_status: the strip
    translates a nominal sRGB code into its own linear PWM units, and this
    surface -- which is already sRGB, drawn straight into an sRGB-ish context
    -- translates it by doing nothing to it. Identity is the correct transfer
    here, and getting to identity is the whole change: the components handed to
    CGContextSetRGBFillColor are now the nominal code, so both surfaces emit
    the same fraction of their own full output for the same hex, and a 50%
    fade is the same 50% breath on both.

    ``boost`` survives because it is genuinely aesthetic: a uniform lift of one
    layer is a level, and levels are free -- "same RELATIVE light" is a
    statement about the shape of the curve, not its overall gain. What did not
    survive is anything per-channel. See LED_CORE_BOOST's comment.
    """
    faded = min(1.0, max(0.0, alpha * alpha_scale))
    # One scale, shared by all three channels, and never above 1.0. Shared is
    # what keeps the hue: scaling channels independently and clipping each on
    # its own moves the colour. Bounded is what keeps the LEVEL comparable
    # with the strip: a scale that clips for saturated colours and not for
    # unsaturated ones is a curve, not a level, and a curve on one surface and
    # not the other is the bug this whole change is about.
    scale = min(1.0, max(0.0, boost))
    return (red * scale, green * scale, blue * scale, faded)


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
            self.notch_insets = None
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

    def setNotchInsets_(self, insets):
        value = (
            None
            if not insets
            else tuple((float(left), float(right)) for left, right in insets)
        )
        if value == getattr(self, "notch_insets", None):
            return
        self.notch_insets = value
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
        self._parse_or_warm_wasm()
        self.setNeedsDisplay_(True)

    def _parse_or_warm_wasm(self) -> None:
        """Parse inline when the engine exists; otherwise warm it OFF-MAIN.

        Constructing SdLedWasmController costs ~1.1s cold (JavaScriptCore
        first load) and ~25ms per extra context, and the animation panes
        build a dozen-plus preview thumbs, each of which paid that price
        synchronously at pane open -- "when I open certain animation
        windows, it's super laggy" was exactly this. The thumb now draws
        its static first frame immediately and animates in when the
        warmed engine adopts the latest program."""
        if self.wasm_controller is not None:
            try:
                self.wasm_controller.parse(
                    self.current_program, int(self.started_at * 1000.0)
                )
            except Exception as exc:
                self.wasm_error = str(exc)
            return
        if getattr(self, "_wasm_warming", False):
            return
        self._wasm_warming = True

        def _warm() -> None:
            try:
                controller = SdLedWasmController(LED_COUNT)
            except LedWasmUnavailableError as exc:
                self.wasm_error = str(exc)
                self._wasm_warming = False
                return

            def _adopt() -> None:
                self._wasm_warming = False
                self.wasm_controller = controller
                if self.current_program is not None:
                    try:
                        controller.parse(
                            self.current_program,
                            int(self.started_at * 1000.0),
                        )
                    except Exception as exc:
                        self.wasm_error = str(exc)
                self.setNeedsDisplay_(True)

            try:
                # Same fix as usage_graph_worker: Python callables passed to
                # addOperationWithBlock_ never fire; callAfter is the way.
                from PyObjCTools import AppHelper

                AppHelper.callAfter(_adopt)
            except Exception:
                _adopt()

        threading.Thread(
            target=_warm, name="SidePulseLedWarm", daemon=True
        ).start()

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
        if self.current_program is not None and self.wasm_controller is None:
            # Never construct the engine on the DRAW path -- warming is
            # the program setter's job, off-main (see _parse_or_warm_wasm).
            self._parse_or_warm_wasm()
        if self.current_program is not None and self.wasm_controller is not None:
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

    @guard_draw
    def drawRect_(self, _rect):
        if self.wings_only_mode:
            self._draw_wings_only()
            return
        if self.compact_mode:
            self._draw_compact_accent()
            return

        colors = self._colors_for_draw_cached()
        height = self.bounds().size.height
        notch_width, wing_offset = self._notch_geometry()
        insets = getattr(self, "notch_insets", None)
        body_rect = ((wing_offset, 0.0), (notch_width, height))
        body = (
            notch_bar_path_from_insets(body_rect, insets)
            if insets
            else notch_bar_path(body_rect)
        )
        led_width = notch_width / LED_COUNT
        glow_height = min(LED_GLOW_HEIGHT, max(0.0, height - LED_BAND_HEIGHT))

        # Classic mode is CONTAINED: the notch is the canvas and nothing
        # paints outside its measured silhouette. Glow strips on the menu
        # bar's own background and riser columns beyond the notch edges
        # (the old wing passes) read as gray slabs against the wallpaper
        # -- visibly "not the notch." Wings remain the language of the
        # Alcove bracket (_draw_wings_only), where painting around the
        # capsule is the entire point.
        rim = max(0.0, min(1.0, getattr(self, "min_glow", 0.25)))

        # A MacBook gets the black camera-housing continuation. On a notchless
        # display the window remains transparent and contains only LED color.
        # OPAQUE black: at the old 0.93 alpha the menu bar's wallpaper tint
        # bled through the band, so the housing read as a gray-purple
        # capsule hanging under a true-black notch.
        # While following Alcove, ALCOVE owns the shell: painting a second
        # notch-deep slab under its capsule read as a clunky black square
        # below the notch, so only the LED band renders there.
        if self.has_notch and self.alcove_silhouette is None and rim > 0.0:
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.006, 0.007, 0.010, 1.0
            ).set()
            body.fill()

        cg_context = current_cg_context()

        # The LED row, the corner feather, and the rim all render inside
        # the housing's clip so none of them can escape the silhouette.
        NSGraphicsContext.saveGraphicsState()
        body.addClip()
        self._fill_glow_row(
            cg_context, colors, led_width, notch_width, glow_height, height,
            x_start=wing_offset, x_end=wing_offset + notch_width, wing_offset=wing_offset,
        )

        # Corner feather: the glow band used to run at full brightness
        # into the bottom-corner fillets, where the clip sliced it along
        # the curve -- a bright hook curling up at each end, and every
        # measured inset step above it highlighted as a staircase. Easing
        # the light down to housing-black before the corners keeps the
        # corners reading as clean black rounding. NOTCH DISPLAYS ONLY:
        # a notchless strip has no black housing behind it, so a black
        # feather composited dark smudges onto the menu bar there.
        feather = (
            min(14.0, notch_width / 8.0)
            if self.has_notch and self.alcove_silhouette is None
            else 0.0
        )
        if feather > 1.0:
            steps = 7
            segment = feather / steps
            for index in range(steps):
                t = 1.0 - (index + 0.5) / steps
                shade = (0.0, 0.0, 0.0, t * t * (3.0 - 2.0 * t))
                fill_rect_with_cg(
                    cg_context,
                    ((wing_offset + index * segment, 0.0), (segment + 0.5, height)),
                    shade,
                )
                fill_rect_with_cg(
                    cg_context,
                    (
                        (wing_offset + notch_width - (index + 1) * segment, 0.0),
                        (segment + 0.5, height),
                    ),
                    shade,
                )

        # Edge highlight/shadow: body-width and clipped -- the old
        # full-window version drew a faint seam straight across the menu
        # bar, and its white bottom line escaped the fillets as part of
        # the corner hooks. Skipped while following Alcove for the same
        # reason as the housing above.
        if self.has_notch and self.alcove_silhouette is None and rim > 0.0:
            fill_rect_with_cg(
                cg_context,
                ((wing_offset, LED_BAND_HEIGHT - 0.55), (notch_width, 0.55)),
                (0.0, 0.0, 0.0, 0.18 * rim),
            )
            fill_rect_with_cg(
                cg_context,
                ((wing_offset, 0.0), (notch_width, 0.45)),
                (1.0, 1.0, 1.0, 0.055 * rim),
            )

        # Standing gauges live INSIDE the housing now: 4pt tips tucked
        # just past the corner fillets, over black -- peripheral vision
        # still gets its own pixels without anything on the wallpaper.
        # Housing displays only: a notchless strip has no black behind
        # the tips, so they'd float on pure LED color.
        if self.has_notch and self.alcove_silhouette is None:
            self._draw_standing_gauges(
                cg_context, height, edge_inset=wing_offset + 6.0
            )
        NSGraphicsContext.restoreGraphicsState()

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

    def _riser_breath(self) -> float:
        """Bookends breathe. Steady uprights read as 'two LEDs that are
        always on' next to a moving underline; a slow six-second swell
        makes them part of one living piece. Reduce Motion holds steady."""
        preferences = getattr(self, "accessibility_display_preferences", None)
        if preferences is not None and getattr(preferences, "reduce_motion", False):
            return 1.0
        phase = (time.monotonic() % 6.0) / 6.0
        return 0.62 + 0.19 * (1.0 + math.cos(2.0 * math.pi * phase))

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
        #
        # While following Alcove the capsule's bottom edge sits ABOVE the
        # window's bottom (the window is notch-deep, the capsule is not),
        # and a band anchored at the window bottom floated detached below
        # the capsule -- the bulky look. The whole stack lifts so the band
        # kisses the capsule's lower edge instead.
        base_y = 0.0
        if self.alcove_silhouette is not None and _height:
            base_y = max(
                0.0,
                float(_height) - float(self.alcove_silhouette[2]) - 1.0,
            )
        bloom_y = base_y + LED_BAND_HEIGHT
        bloom_height = glow_height * 0.45
        soft_y = bloom_y + bloom_height
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
                ((run_x, base_y), (run_width, LED_BAND_HEIGHT)),
                tone_mapped_led_color(
                    red, green, blue, alpha, boost=LED_CORE_BOOST, alpha_scale=0.92
                ),
            )
            fill_rect_with_cg(
                cg_context,
                ((run_x, base_y), (run_width, 1.15)),
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

class _AnnouncerPillView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_AnnouncerPillView, self).initWithFrame_(frame)
        if self is not None:
            self.text = ""
            self.click_handler_source = None
        return self

    def isFlipped(self):
        return False

    def drawRect_(self, _rect):
        bounds = self.bounds()
        radius = bounds.size.height / 2.0
        pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((0.5, 0.5), (bounds.size.width - 1.0, bounds.size.height - 1.0)),
            radius,
            radius,
        )
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.02, 0.02, 0.03, 0.86).set()
        pill.fill()
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.14).set()
        pill.setLineWidth_(1.0)
        pill.stroke()
        text = str(self.text or "")
        if not text:
            return
        paragraph = NSMutableParagraphStyle.alloc().init()
        paragraph.setLineBreakMode_(NSLineBreakByTruncatingTail)
        attributes = {
            NSFontAttributeName: NSFont.systemFontOfSize_(11.0),
            NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(
                1.0, 0.92
            ),
            NSParagraphStyleAttributeName: paragraph,
        }
        inset = ANNOUNCER_PILL_PADDING
        NSString.stringWithString_(text).drawInRect_withAttributes_(
            (
                (inset, (bounds.size.height - 14.0) / 2.0),
                (max(0.0, bounds.size.width - 2.0 * inset), 15.0),
            ),
            attributes,
        )

    def mouseDown_(self, _event):
        source = self.click_handler_source
        handler = source() if callable(source) else None
        if callable(handler):
            try:
                handler()
            except Exception:
                pass


class AnnouncerPill:
    """The words window under the notch. One line, ask-gated, clickable."""

    def __init__(self) -> None:
        self.window = None
        self.view = None
        self.text: str | None = None

    def _ensure_window(self):
        if self.window is not None:
            return
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (200.0, ANNOUNCER_PILL_HEIGHT)),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(NSColor.clearColor())
        window.setHasShadow_(False)
        window.setLevel_(STATUS_WINDOW_LEVEL)
        window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        view = _AnnouncerPillView.alloc().initWithFrame_(
            ((0, 0), (200.0, ANNOUNCER_PILL_HEIGHT))
        )
        window.setContentView_(view)
        self.window = window
        self.view = view

    def hide(self) -> None:
        self.text = None
        window = self.window
        if window is None:
            return
        if not window.isVisible():
            window.orderOut_(None)
            return
        # Exits are always shorter than entrances: a quick fade out
        # instead of a blink-out. Any failure falls back to the blink.
        try:
            from AppKit import NSAnimationContext

            def _animate(context):
                context.setDuration_(0.18)
                window.animator().setAlphaValue_(0.0)

            def _finish():
                window.orderOut_(None)
                window.setAlphaValue_(1.0)

            NSAnimationContext.runAnimationGroup_completionHandler_(_animate, _finish)
        except Exception:
            window.orderOut_(None)

    def update(
        self,
        *,
        text: str | None,
        center_x: float,
        top_y: float,
        click_handler_source=None,
    ) -> None:
        if not text:
            self.hide()
            return
        self._ensure_window()
        if self.view is not None:
            changed = text != self.text
            self.view.text = text
            self.view.click_handler_source = click_handler_source
            if changed:
                self.view.setNeedsDisplay_(True)
        self.text = text
        attributes = {NSFontAttributeName: NSFont.systemFontOfSize_(11.0)}
        measured = NSString.stringWithString_(text).sizeWithAttributes_(
            attributes
        )
        width = min(
            ANNOUNCER_PILL_MAX_WIDTH,
            max(120.0, measured.width + 2.0 * ANNOUNCER_PILL_PADDING),
        )
        frame = (
            (center_x - width / 2.0, top_y - ANNOUNCER_PILL_HEIGHT),
            (width, ANNOUNCER_PILL_HEIGHT),
        )
        self.window.setFrame_display_(frame, True)
        if self.view is not None:
            self.view.setFrame_(((0, 0), frame[1]))
        # Words are worth a click: the pill itself jumps to the session.
        self.window.setIgnoresMouseEvents_(click_handler_source is None)
        if not self.window.isVisible():
            from .window_presentation import present_window

            present_window(self.window, key=False)
            self._animate_entrance()

    def _animate_entrance(self) -> None:
        """The Dynamic Island's arrival, scaled to a pill: it hangs FROM
        the notch, so it scales up from its top-center anchor with a
        small spring settle and a fade -- never a teleport. Failure here
        must never break the pill itself, and Reduce Motion keeps the
        instant appearance (which is exactly what that setting asks for).
        """
        try:
            from .accessibility_display import read_accessibility_display_preferences

            if read_accessibility_display_preferences().reduce_motion:
                return
        except Exception:
            pass
        try:
            import Quartz
            from Foundation import NSNumber

            view = self.view
            if view is None:
                return
            view.setWantsLayer_(True)
            layer = view.layer()
            if layer is None:
                return
            bounds = view.bounds()
            # Anchor at top-center so the scale grows DOWN from the notch;
            # re-pin position so changing the anchor doesn't shift the view.
            layer.setAnchorPoint_((0.5, 1.0))
            layer.setPosition_(
                (bounds.size.width / 2.0, bounds.size.height)
            )
            spring = Quartz.CASpringAnimation.animationWithKeyPath_("transform.scale")
            spring.setMass_(1.0)
            spring.setStiffness_(190.0)
            spring.setDamping_(20.0)
            spring.setFromValue_(NSNumber.numberWithDouble_(0.94))
            spring.setToValue_(NSNumber.numberWithDouble_(1.0))
            spring.setDuration_(spring.settlingDuration())
            fade = Quartz.CABasicAnimation.animationWithKeyPath_("opacity")
            fade.setFromValue_(NSNumber.numberWithDouble_(0.0))
            fade.setToValue_(NSNumber.numberWithDouble_(1.0))
            fade.setDuration_(0.2)
            layer.addAnimation_forKey_(spring, "sidepulse.pill.entrance")
            layer.addAnimation_forKey_(fade, "sidepulse.pill.fade")
        except Exception:
            # A missing Quartz symbol or layer quirk costs the flourish,
            # never the words.
            pass

    def close(self) -> None:
        if self.window is not None:
            self.window.orderOut_(None)
            self.window = None
            self.view = None


class VirtualStatusDevice(NSObject):
    def init(self):
        self = objc.super(VirtualStatusDevice, self).init()
        if self is not None:
            self.window = None
            self.view = None
            self.timer = None
            self._announcer_pill = None
            self._announcer_text = None
            self.display_link = None
            # The rate the installed link was registered for; None when there
            # is no link. Compared against schedule.driver_fps so a panel that
            # changed under us reinstalls rather than quietly disagreeing.
            self._display_link_fps = None
            self._panel_refresh_cache = None
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
            # When the last callback was actually turned into a frame. See
            # redraw_'s cadence gate -- deliberately the last PRESENTED time
            # and never an accumulated schedule, so a dropped tick cannot
            # cascade into a run of catch-up frames.
            self._last_presented_at = None
            self._frame_fallback_relevant = False
            self._alcove_relevant = False
            self._pointer_interaction_relevant = False
            self._presentation_schedule_reconciler = None
            self._program_identity = None
            self._program_applied_at = float("-inf")
            self._enabled = True
            self._terminating = False
            # Uncommitted Studio preview: the last program the LIVE path
            # asked for, and the hold that is currently painting over it.
            self._live_program_call = None
            self._preview_hold = None
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
        # A new program presents on the very next callback, whatever the
        # cadence gate would otherwise have said.
        self._last_presented_at = None
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

    def _record_alcove_status(self, status, *, now: float | None = None) -> bool:
        """Publish the outcome, and log it ONCE per transition.

        Per-frame logging at the 1.5s observation cadence is a log nobody
        reads; zero lines is how "Alcove mode doesn't seem to be working"
        stayed unanswerable. Transitions are the only interesting events,
        so the record itself decides when there is anything to say.
        """
        if type(status) is not AlcoveCaptureStatus:
            return False
        changed = note_alcove_status(status, now=now)
        if not changed:
            return False
        line = ALCOVE_STATUS_LOG_LINES.get(status)
        if line is None:
            return True
        try:
            from .status_bar import log_status_bar

            log_status_bar(line)
        except Exception:
            # Logging must never be able to break the render path.
            pass
        return True

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
            alcove_relevant=self._alcove_follow_relevant(),
            pointer_interaction_relevant=bool(
                self._pointer_interaction_relevant
            ),
            frame_interval=getattr(self, "_frame_interval_current", None),
        )

    def _alcove_follow_relevant(self) -> bool:
        """Presence-fresh relevance for the follow cadence.

        reposition() computes the same flag, but reposition only RUNS
        when something else already moved the bar -- so an Alcove
        launched against an idle bar never started its own 1.5s follow
        cadence until an unrelated settings click repositioned
        (42-90s to follow, reported live 2026-08-27). The probe read
        here is the cached answer (the worker refreshes it off-main),
        so this costs nothing and closes the chicken-and-egg.
        """
        if self._alcove_relevant:
            return True
        if not (
            self.wraps_menu_bar
            and getattr(self, "follow_alcove_width", True)
            and getattr(self, "wing_length_override", None) is None
        ):
            return False
        probe = getattr(self, "_alcove_presence_probe", None)
        if probe is None:
            return False
        try:
            if probe.running(now=time.monotonic()):
                self._alcove_relevant = True
                return True
        except Exception:
            return False
        return False

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

    def _panel_refresh_hz(self) -> float | None:
        """The display's real refresh rate, cached briefly.

        The one reader of the panel. Everything that needs the rate -- the
        cadence policy AND the display link's own negotiation -- goes through
        the schedule built from this call, because two independent readings of
        a number that changes are two numbers.

        Cached for five seconds because NSScreen is touched on every cadence
        refresh; ``screenDidChange_`` drops the cache, since a new screen
        generation is precisely when the old reading stops describing anything.
        """
        cached = getattr(self, "_panel_refresh_cache", None)
        now = time.monotonic()
        if cached is not None and now - cached[0] < 5.0:
            return cached[1]
        refresh = None
        try:
            screen = NSScreen.mainScreen()
            maximum = getattr(screen, "maximumFramesPerSecond", None)
            if callable(maximum):
                value = float(maximum())
                if value > 0.0:
                    refresh = value
        except Exception:
            refresh = None
        self._panel_refresh_cache = (now, refresh)
        return refresh

    def _refresh_render_cadence(
        self, animation_active: bool, *, force: bool = False
    ):
        # CONTINUOUS motion is the app's RESTING state -- a slow breathe
        # that looks identical at a fraction of the framerate. Only
        # FINITE cues are real transitions worth the full pipeline.
        # Treating "anything is animating" as "run at 60-120 Hz" meant
        # the idle pulse never let the surface settle, which was the
        # single largest CPU draw in the app.
        motion = getattr(self._sampler_command, "motion", None)
        gentle = motion is MotionClass.CONTINUOUS
        schedule = choose_render_schedule(
            self._runtime_environment(force=force),
            animation_active,
            display_link_available=self._display_link_available(),
            next_visual_change_at=getattr(
                self._sampler_command,
                "next_visual_change_at",
                None,
            ),
            gentle_motion=gentle,
            refresh_hz=self._panel_refresh_hz(),
        )
        previous = getattr(self, "_render_schedule", None)
        self._render_schedule = schedule
        # A cadence change presents on the next callback rather than waiting
        # out an interval measured against the old rate. This has to happen
        # HERE and not in _apply_render_schedule: every thermal and low-power
        # transition that keeps the same driver (serious -> critical, timer to
        # timer) returns "already installed" and never reaches that function,
        # which is every cadence change the app actually makes at runtime.
        if previous is None or previous.cadence != schedule.cadence:
            self._last_presented_at = None
        cadence = schedule.cadence
        if self.view is not None:
            self.view.setRenderFps_(cadence.sample_fps)
        if schedule.driver is RenderDriverKind.DISPLAY_LINK:
            # A link registered for a different rate is the wrong driver, not
            # the right driver early: the gate holds against schedule.driver_fps
            # and the callbacks arrive at whatever was negotiated. This costs
            # nothing in churn -- display_link_fps depends only on the panel, so
            # the number moves when the display does and at no other time. In
            # particular a CONTINUOUS/FINITE motion flip changes the cadence
            # without changing this, and keeps the link it has.
            installed = (
                self.display_link is not None
                and self.timer is None
                and not self._frame_fallback_relevant
                and self._display_link_fps == schedule.driver_fps
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

    def _install_display_link(self, driver_fps: float | None = None) -> bool:
        """Register one native display callback, falling back on AppKit failure.

        The rate requested here is the rate the schedule already assumed --
        ``schedule.driver_fps``, which is ``display_link_fps`` of the same panel
        reading. Passing it in rather than re-deriving it is what stops the two
        from disagreeing: this function used to read ``maximumFramesPerSecond``
        fresh off NSScreen while the policy quantised against a five-second
        cached copy, so a screen change could leave the link negotiated for one
        panel and the cadence computed for another.

        A degenerate range -- minimum, maximum and preferred all the same rate
        -- is deliberate. ``CAFrameRateRangeMake(60, 120, 120)`` on a 144 Hz
        panel has exactly one achievable member, 72, and on an adaptive panel it
        has several, so the rate a range yields is either a surprise or a guess.
        Asking for one achievable number makes the answer knowable.
        """
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
            if not callable(set_frame_range) or not callable(make_frame_range):
                raise RuntimeError("display link frame range is unavailable")
            rate = float(driver_fps or 0.0)
            if rate <= 0.0:
                rate = display_link_fps(self._panel_refresh_hz())
            frame_range = make_frame_range(rate, rate, rate)
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
        # The rate this particular link is registered for. Kept so a later
        # cadence refresh can tell "the driver is installed" from "a driver is
        # installed, for a display we are no longer on".
        self._display_link_fps = rate
        return True

    def _invalidate_frame_driver(self) -> None:
        """Stop either owned callback before the next driver is installed."""
        display_link = self.display_link
        timer = self.timer
        self.display_link = None
        self.timer = None
        self._display_link_fps = None
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
        # A cadence change presents on the next callback rather than waiting
        # out an interval measured against the old rate.
        self._last_presented_at = None
        if schedule.driver is RenderDriverKind.PAUSED:
            self._publish_presentation_schedule()
            return
        if schedule.driver is RenderDriverKind.DISPLAY_LINK and self._install_display_link(
            schedule.driver_fps
        ):
            self._publish_presentation_schedule()
            return
        # KNOWN HOLE, left open deliberately. _animation_active is False for
        # STATIC, so no frame-fallback intent is published and the first frame
        # after motion stops is sampled, published, and never presented -- the
        # notch keeps the last animated pixels until something else dirties it.
        # Gating on `schedule.cadence.fps > 0.0` instead would reinstate the
        # cadence's 4 fps static watcher and close it, EXCEPT that the
        # fallback timer's interval is a hardcoded 60 Hz in
        # presentation_scheduler.py: the app would then wake 60 times a second
        # to render nothing, which is worse than the hole. Closing this
        # properly means carrying the schedule's interval into
        # PresentationSchedulerInputs first.
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
        # A different screen generation is exactly when the cached panel rate
        # stops being about this panel. Five seconds of a stale reading is five
        # seconds of negotiating and quantising for the display we just left --
        # 60 Hz assumed on a 120 Hz panel doubles the delivered rate, and the
        # gate has no way to notice.
        self._panel_refresh_cache = None
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
        # Turning wrap OFF must not stop watching Alcove: compact mode
        # still draws an accent that should track the capsule. Only
        # set_follow_alcove turning off, or Alcove going away, ends the
        # observation -- reposition() re-derives relevance each pass.
        self.wraps_menu_bar = bool(enabled)
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

    def set_announcer_text(self, text: str | None) -> None:
        """The announcer's words: session name + the actual question,
        shown in the pill below the notch while an ask is live."""
        value = str(text) if text else None
        if value == self._announcer_text:
            return
        self._announcer_text = value
        self._sync_announcer()

    def _sync_announcer(self) -> None:
        pill = self._announcer_pill
        text = self._announcer_text
        show = (
            text is not None
            and not self._terminating
            and self._enabled
            and not self._display_asleep
            and self.window is not None
            and self.window.isVisible()
            # While Alcove runs, the space below the notch is Alcove's;
            # the dropdown ledger still carries the words there.
            and not getattr(self, "_alcove_relevant", False)
            and not getattr(self, "_compact_active", False)
        )
        if not show:
            if pill is not None:
                pill.hide()
            return
        if pill is None:
            pill = self._announcer_pill = AnnouncerPill()
        frame = self.window.frame()
        pill.update(
            text=text,
            center_x=frame.origin.x + frame.size.width / 2.0,
            top_y=frame.origin.y - 2.0,
            click_handler_source=lambda: getattr(self.view, "click_handler", None),
        )

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
            self._record_alcove_status(AlcoveCaptureStatus.NOT_FOLLOWING)
        else:
            # THE point following is enabled: ask the system, once, whether
            # we are even allowed to look. Preflight never prompts -- the
            # request call is reserved for a button the user pressed -- and
            # forcing it here refreshes a cache that may be holding a
            # "denied" the user has since fixed in System Settings.
            if screen_recording_granted(force=True) is False:
                self._record_alcove_status(
                    AlcoveCaptureStatus.SCREEN_RECORDING_DENIED
                )
        self._publish_presentation_schedule()

    def set_bracket_style(self, style: str) -> None:
        if self.view is not None:
            self.view.setBracketStyle_(style)

    def set_geometry_overrides(self, gap_width: float | None, wing_length: float | None) -> None:
        """The user's manual bar geometry (None = Automatic) -- see
        virtual_window_frame_for_screen."""
        self.gap_width_override = gap_width
        self.wing_length_override = wing_length

    def set_show_in_full_screen(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == getattr(self, "_show_in_full_screen", False):
            return
        self._show_in_full_screen = enabled
        self._reconcile_fullscreen_visibility()

    def _install_space_observer(self) -> None:
        if getattr(self, "_space_observer_center", None) is not None:
            return
        try:
            from AppKit import NSWorkspace

            center = NSWorkspace.sharedWorkspace().notificationCenter()
            center.addObserver_selector_name_object_(
                self,
                "activeSpaceDidChange:",
                "NSWorkspaceActiveSpaceDidChangeNotification",
                None,
            )
            self._space_observer_center = center
        except Exception:
            self._space_observer_center = None

    def _remove_space_observer(self) -> None:
        center = getattr(self, "_space_observer_center", None)
        if center is None:
            return
        self._space_observer_center = None
        try:
            center.removeObserver_name_object_(
                self,
                "NSWorkspaceActiveSpaceDidChangeNotification",
                None,
            )
        except Exception:
            pass

    @objc.IBAction
    def activeSpaceDidChange_(self, _notification) -> None:
        self._reconcile_fullscreen_visibility()

    def _reconcile_fullscreen_visibility(self) -> None:
        """Full-screen spaces hide the bar unless the owner opted in.

        The bar's window level rides above even full-screen video (it
        must, to beat Alcove's overlay), so without this it floated over
        movies -- the 2026-08-21 report. Hiding via orderOut_ also
        drops _is_surface_visible, so the whole render pipeline rests
        while a video plays."""
        window = self.window
        if window is None or self._terminating:
            return
        screen = None
        try:
            screen = window.screen()
        except Exception:
            screen = None
        if screen is None:
            try:
                screen = NSScreen.mainScreen()
            except Exception:
                return
        hide = (
            not getattr(self, "_show_in_full_screen", False)
            and screen is not None
            and space_hides_menu_bar(screen)
        )
        if hide == getattr(self, "_fullscreen_hidden", False):
            return
        self._fullscreen_hidden = hide
        if hide:
            window.orderOut_(None)
            pill = getattr(self, "_announcer_pill", None)
            if pill is not None:
                pill.hide()
        elif self._enabled:
            from .window_presentation import present_window

            present_window(window, key=False)

    def show(self):
        if self._terminating or not self._enabled:
            return
        was_visible = self._is_surface_visible()
        if self.window is None:
            self._build_window()
        _reposition_started = time.monotonic()
        self.reposition()
        self._last_reposition_at = time.monotonic()
        if self._last_reposition_at - _reposition_started > 0.08:
            try:
                from .status_bar import log_status_bar

                log_status_bar(
                    "screen bar reposition: "
                    f"{int((self._last_reposition_at - _reposition_started) * 1000)}ms"
                )
            except Exception:
                pass
        self._install_space_observer()
        # Decide visibility BEFORE fronting: every reassert used to
        # orderFront first and reconcile second, which popped the bar
        # back over full-screen video once per sync tick -- "the screen
        # bar is still there inside full-screen video" was this exact
        # ordering, not the detection. None forces a fresh verdict.
        self._fullscreen_hidden = None
        self._reconcile_fullscreen_visibility()
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
        if self._announcer_pill is not None:
            self._announcer_pill.hide()
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
        self._remove_space_observer()
        if self._announcer_pill is not None:
            try:
                self._announcer_pill.close()
            except Exception:
                pass
            self._announcer_pill = None
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

    # --- Uncommitted preview ------------------------------------------
    #
    # The Colour/Animation Studio shows you a candidate colour on the real
    # Screen Bar while the pointer is on a swatch, and takes it back when
    # the pointer leaves. That revert has to be structural, not a promise:
    # a preview that leaks is a setting the user never chose, on the most
    # visible surface the app owns. So a hold OWNS the surface -- live
    # updates arriving underneath it are remembered, not drawn -- and
    # releasing replays whichever live program is current, so the bar
    # lands on the truth rather than on a stale frame from before the
    # hover.

    def preview_is_held(self) -> bool:
        return self._preview_hold is not None

    def hold_preview_program(self, program: str, **kwargs) -> None:
        """Paint an uncommitted candidate over the live render."""
        if self._preview_hold is None:
            self._preview_hold = {
                "baseline": self._live_program_call,
                "pending": None,
            }
        self._preview_hold["expires_at"] = time.monotonic() + PREVIEW_HOLD_MAX_SECONDS
        self._apply_program(program, **kwargs)

    def release_preview_program(self) -> bool:
        """Give the surface back. Returns whether a hold was actually
        released, so a caller can tell "reverted" from "nothing to do"."""
        hold, self._preview_hold = self._preview_hold, None
        if hold is None:
            return False
        restore = hold["pending"] or hold["baseline"]
        if restore is None:
            return True
        program, kwargs = restore
        self._live_program_call = (program, dict(kwargs))
        self._apply_program(program, **kwargs)
        return True

    def set_program(self, program: str, **kwargs):
        """The live render path. While a preview holds the surface this
        records what the bar WOULD be showing without presenting it, so
        releasing the hold reverts to now rather than to then."""
        hold = self._preview_hold
        if hold is not None:
            if time.monotonic() < hold.get("expires_at", 0.0):
                hold["pending"] = (str(program), dict(kwargs))
                return None
            # Backstop: a hold nobody released has outlived its welcome.
            self.release_preview_program()
        self._live_program_call = (str(program), dict(kwargs))
        return self._apply_program(program, **kwargs)

    def _apply_program(
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
        motion_class = motion if isinstance(motion, MotionClass) else MotionClass.STATIC
        # The gate must include whatever ADVANCES the animation, not just
        # what the program says. A phase-free token is the right dedupe
        # for a surface whose motion is guaranteed by something else --
        # true of the strip (firmware loops), false here. So the notch
        # additionally requires that its sampler is actually running the
        # motion this token claims, and re-asserts on a timer regardless.
        now = time.monotonic()
        sampler_is_running_the_motion = self._sampler is not None and (
            self._animation_active or motion_class is MotionClass.STATIC
        )
        if (
            self.window is not None
            and self.window.isVisible()
            and self.view is not None
            and identity == self._program_identity
            and sampler_is_running_the_motion
            and now - self._program_applied_at < SCREEN_BAR_REASSERT_SECONDS
        ):
            return
        if self._terminating or not self._enabled or self._display_asleep:
            # Display asleep: nothing to show, and the change-gate above
            # cannot suppress repeats (sleep nulls the sampler, so
            # sampler_is_running_the_motion is always False). Without
            # this clause every lid-closed hardware write re-ran the
            # full show()/reposition()/orderFront dance on the main
            # thread -- newly load-bearing now that hardware writes
            # continue through display sleep.
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
        self._program_applied_at = now
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

    def reanchor_program(self, started_at: float) -> bool:
        """Snap the CURRENT program's phase to ``started_at``.

        The linked-hardware handshake: the strip restarts its cycle the
        moment the firmware picks up a changed LEDS.LED, while the bar
        anchors to the semantic relay epoch -- the same pulse ran a few
        hundred milliseconds out of phase on the two surfaces, which is
        exactly "they don't feel synced". The write-completion moment is
        the closest observable stand-in for firmware pickup, so the bar
        snaps its clock to it. Identity dedupe (the blink fix) is
        deliberately untouched: this changes only the phase of what is
        already showing, never re-fronts the window, and refuses static
        programs and sub-50ms nudges outright."""
        command = self._sampler_command
        if command is None or self.view is None:
            return False
        if command.motion is MotionClass.STATIC:
            return False
        # STEADY STATE ONLY. A program that just changed already restarted
        # both surfaces once; snapping its phase again a beat later is a
        # second visible restart -- during rapid state flips that read as
        # "everything is flashing". Long-lived programs get their sync from
        # the periodic steady-body rewrites instead (patina/reassert
        # cadence), where a one-time settle is imperceptible.
        applied_at = float(getattr(self, "_program_applied_at", 0.0) or 0.0)
        if time.monotonic() - applied_at < REANCHOR_STEADY_SECONDS:
            return False
        anchor = float(started_at)
        if not math.isfinite(anchor) or abs(anchor - command.parse_anchor) < 0.05:
            return False
        record_program = getattr(
            self.view, "setPresentationProgram_startedAt_", None
        )
        if callable(record_program):
            record_program(command.program, anchor)
        else:
            self.view.started_at = anchor
        self._advance_presentation_generation(enqueue=False)
        self._sampler_command = SamplerCommand(
            generation=self._presentation_generation,
            program=command.program,
            parse_anchor=anchor,
            static_fallback_program=command.static_fallback_program,
            sample_interval=command.sample_interval,
            motion=command.motion,
            next_visual_change_at=command.next_visual_change_at,
        )
        if self._sampler is None:
            self._resume_sampler()
        else:
            self._sampler.reconcile(self._sampler_command)
        return True

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
        now = time.monotonic()
        probe = getattr(self, "_alcove_presence_probe", None)
        if probe is None:
            # Bound late and per-instance so a test can substitute one,
            # and so the module-level function stays directly callable.
            probe = AlcovePresenceProbe()
            self._alcove_presence_probe = probe
        alcove_active = probe.running(now=now)
        wings_only = alcove_active and self.wraps_menu_bar
        compact = alcove_active and not self.wraps_menu_bar

        gap_override = getattr(self, "gap_width_override", None)
        wing_override = getattr(self, "wing_length_override", None)
        self._alcove_relevant = bool(
            alcove_active
            and wing_override is None
            and getattr(self, "follow_alcove_width", True)
        )
        effective_gap = gap_override
        measured_notch_insets = None
        if gap_override is None:
            # Pixel-exact notch, measured once per screen configuration
            # (the notch can't change at runtime) -- see
            # measured_notch_silhouette for why this beats a model table.
            # Guards, each one earned: never measure while Alcove is up
            # (its capsule composites pure black over the very rows the
            # scan reads -- 266pt "notch" over a 186pt panel), cap by the
            # auxiliary-area gap for the same reason, and never cache a
            # failed read forever (retry later instead of wearing a
            # one-time contamination for the rest of the session).
            frame = screen.frame()
            cache_key = (round(frame.size.width), round(frame.size.height))
            cache = getattr(self, "_notch_measure_cache", None)
            cache_stale = cache is None or cache[0] != cache_key
            retry_due = cache is not None and cache[1] is None and (
                now >= getattr(self, "_notch_measure_retry_at", 0.0)
            )
            if (
                not alcove_active
                and (cache_stale or retry_due)
                # A full-screen Space hides the menu bar and paints app
                # content over the very rows the scan reads: the measure
                # CANNOT succeed there, and each doomed attempt is a
                # synchronous WindowServer capture on the main thread --
                # measured at 150ms typical and 10s worst-case during
                # full-screen video. Wait for a Space that can answer.
                and not space_hides_menu_bar(screen)
            ):
                if cache_stale:
                    self._notch_measure_backoff = 60.0
                silhouette = measured_notch_silhouette(
                    screen,
                    below_window_number=int(self.window.windowNumber() or 0),
                    max_width=_hardware_slot_ceiling(screen),
                )
                self._notch_measure_cache = (cache_key, silhouette)
                cache = self._notch_measure_cache
                if silhouette is None:
                    # Repeated failure means something durable (an overlay
                    # compositing over the notch rows); retrying every
                    # minute forever just burns main-thread time. Back off
                    # up to 15 minutes; success or a screen change resets.
                    backoff = max(60.0, getattr(self, "_notch_measure_backoff", 60.0))
                    self._notch_measure_retry_at = now + backoff
                    self._notch_measure_backoff = min(900.0, backoff * 2.0)
                else:
                    self._notch_measure_backoff = 60.0
            if cache is not None and cache[0] == cache_key and cache[1] is not None:
                effective_gap = cache[1][1]
                measured_notch_insets = cache[1][2]
        # Follow Alcove's visible capsule through the serial observer.
        # Reposition resolves AppKit and window identity on main, consumes
        # only a validated plain result, and never captures or scans pixels.
        follow_width = None
        follow_center_x = None
        follow_observation = None
        # Measure whenever Alcove is up, not only in wrap mode. Gating
        # this on wings_only meant compact -- the POLITE mode, chosen by
        # someone who does not want us wrapping their menu bar -- never
        # followed the capsule at all and silently sized itself to the
        # hardware notch instead.
        follow_enabled = wing_override is None and getattr(
            self, "follow_alcove_width", True
        )
        # Preflighted, cached, and never a prompt. Denied is a state the
        # user can be told about and fix, so it gets its own branch rather
        # than being discovered one failed capture at a time.
        screen_recording = screen_recording_granted() if follow_enabled else None
        if alcove_active and follow_enabled and screen_recording is False:
            # A capture thread that can only come back denied is pure cost
            # and, worse, looks identical to "Alcove isn't running".
            self._stop_alcove_observer()
            self._record_alcove_status(
                AlcoveCaptureStatus.SCREEN_RECORDING_DENIED, now=now
            )
        elif alcove_active and follow_enabled:
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
                # Widen by the stroke inset on BOTH sides. The bracket is
                # drawn ALCOVE_ACCENT_EDGE_INSET in from each window edge,
                # so sizing the window
                # to the raw capsule width put the stroke 6pt inside
                # Alcove's real corners on each side -- visible every day
                # as a bracket that does not quite touch.
                follow_width = observation.width + 2.0 * ALCOVE_ACCENT_EDGE_INSET
                follow_center_x = observation.center_x
            # Why there is no geometry, said once per transition. The
            # main-thread lookup losing the window outranks a stale worker
            # status: we KNOW there is nothing to capture right now.
            if screen_values is None or window_values is None:
                self._record_alcove_status(
                    AlcoveCaptureStatus.WINDOW_UNAVAILABLE, now=now
                )
            else:
                self._record_alcove_status(
                    getattr(self._alcove_observer, "last_status", None), now=now
                )
            # _UNSET, not None: the previous sentinel was None, which is
            # also the value follow_width has when nothing was measured --
            # so the very first (and, when following is broken, the ONLY)
            # state this line could report was the one it never logged.
            if follow_width != getattr(self, "_last_follow_width", _UNSET):
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
            self._record_alcove_status(
                AlcoveCaptureStatus.NOT_FOLLOWING
                if not follow_enabled
                else AlcoveCaptureStatus.WINDOW_UNAVAILABLE,
                now=now,
            )
        window_frame = virtual_window_frame_for_screen(
            screen,
            wrap_menu_bar=self.wraps_menu_bar,
            gap_width=effective_gap,
            wing_length=wing_override,
            alcove_total_width=follow_width,
            alcove_center_x=follow_center_x,
            alcove_total_height=(
                follow_observation.height
                if follow_observation is not None
                else None
            ),
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
        # Read back what AppKit actually granted, and size the view from THAT.
        #
        # setFrame_display_ is a request, not an assignment: AppKit clamps to
        # screen bounds, rounds to the backing scale, and applies its own
        # constraints to a non-activating panel. A display reconfiguration or
        # Space change landing between compute and apply can move it too. If
        # the view is sized from the frame we asked for while the window is at
        # the frame we got, everything inside draws at coordinates the window
        # is not at -- content offset, clipped at the edge, or both.
        #
        # Costs one property read per reposition and removes a whole class of
        # "it looks fine on my single display" bug.
        granted = self.window.frame()
        window_frame = (
            (granted.origin.x, granted.origin.y),
            (granted.size.width, granted.size.height),
        )
        self.window.setLevel_(
            alcove_window_level() if alcove_active else STATUS_WINDOW_LEVEL
        )
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
            self._compact_active = compact
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
            set_notch_insets = getattr(self.view, "setNotchInsets_", None)
            if callable(set_notch_insets):
                # Only meaningful when the body's width IS the measured
                # width; a manual gap or a slot fallback draws parametric.
                set_notch_insets(measured_notch_insets)
        self._sync_announcer()
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

    def _due_for_presentation(self, callback_timestamp: float) -> bool:
        """Whether this callback is allowed to become a frame.

        The cadence policy already computes a real framerate -- 30 fps for the
        slow breathe that is the app's resting state, 15 under thermal
        pressure, 4 when nothing is moving -- and every driver then threw it
        away: the display link asks the panel for its maximum (60-120 Hz) and
        the fallback timer is a hardcoded 60 Hz. Half of those callbacks
        produced a byte-identical frame that the colour dedupe below discarded
        *after* paying for the whole sample-interpolate-clamp-quantise
        pipeline, and the ones that did differ were finer than the sampler
        could even produce (60 new samples/s feeding 120 callbacks/s).

        Phase-free on purpose: the comparison is against the last callback
        actually PRESENTED, never against an accumulated schedule, so one late
        or dropped tick can never cascade into a burst of catch-up frames.

        The headroom is half a DRIVER period, not a fixed percentage of the
        cadence -- see render_policy.presentation_hold_seconds for why a fixed
        percentage turned main-thread jitter into dropped frames one for one
        whenever the cadence matched the driver rate.

        Asking does not consume anything. The stamp is a separate act
        (_mark_presented) because a callback can reach the pipeline and still
        have nothing to show: if the sampler has not published this
        generation's first pair yet, the frame that gets presented is the OFF
        fallback, and spending the interval on it locked the real first frame
        of the cue out for a whole cadence period. A dark blink at the head of
        every announcer cue went from one driver tick to up to 67 ms under
        thermal pressure. Only a frame carrying real sampler output is allowed
        to start the clock.
        """
        schedule = getattr(self, "_render_schedule", None)
        if schedule is None:
            return True
        hold = presentation_hold_seconds(schedule)
        if hold is None:
            return True
        last = getattr(self, "_last_presented_at", None)
        return last is None or callback_timestamp - last >= hold

    def _mark_presented(self, callback_timestamp: float) -> None:
        """Start the cadence interval, from the frame that actually showed."""
        self._last_presented_at = callback_timestamp

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
        if not self._due_for_presentation(callback_timestamp):
            return
        # Peek before the pipeline runs: two attribute loads, and it is the
        # only way to tell a real frame from the fallback afterwards --
        # display_colors_for_tick returns colours either way.
        carries_sampler_output = (
            self._sample_buffer.read(self._presentation_generation) is not None
        )
        if carries_sampler_output:
            self._mark_presented(callback_timestamp)
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
