from __future__ import annotations

import math
import time

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSGraphicsContext,
    NSScreen,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
    NSWorkspace,
)
from Foundation import NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer
from Quartz import CGContextFillRect, CGContextSetRGBFillColor

from .led_status import LedDisplayState, normalize_brightness, program_for_display_state
from .led_wasm import LedWasmUnavailableError, SdLedWasmController

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
# The bracket is clipped to a rounded rect: the underline's ends curve
# up into the risers and the riser tops are capped instead of ending
# in hard right angles -- "more rounded on the corners so they feel
# more naturally part of it".
BRACKET_CORNER_RADIUS = 5.0
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
# 30, not 60: each frame costs a JavaScriptCore WASM step plus the
# 4-layer glow fill, and the ~90ms exponential smoothing in
# _colors_for_draw makes 30fps visually identical for glow-blob motion.
# Measured: the active-animation CPU cost is roughly linear in this.
FRAME_RATE = 30.0
# After a second of visually identical frames the timer drops to this
# rate; any changed frame (or a new program) restores full rate. A
# static program burned 60 Python+WASM ticks per second for nothing.
IDLE_FRAME_RATE = 10.0
IDLE_AFTER_STATIC_FRAMES = int(FRAME_RATE)
# Mirrored module-level for the frame-rate contract test.
TARGET_SAMPLE_INTERVAL_VIEW_CONTRACT = 1.0 / 15.0
FRAME_INTERVAL = 1.0 / FRAME_RATE
# Re-run reposition (Alcove tracking, churn-guarded) about every 2s.
REPOSITION_EVERY_N_FRAMES = int(FRAME_RATE * 2)


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000.0)


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
    # Never wider than the screen itself, whatever the user typed.
    notch_width = min(notch_width, frame.size.width - 8.0)
    wing = min(wing, max(0.0, (frame.size.width - notch_width) / 2.0 - 4.0))
    width = notch_width + 2.0 * wing
    height = window_height_for_notch_depth(notch_depth_for_screen(screen))
    x = frame.origin.x + (frame.size.width - width) / 2.0
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


# NOTE on sizing to Alcove: tried twice, reverted twice. Alcove's
# window bounds say nothing about the visible capsule (it keeps a wide,
# often invisible window; notification HUDs widen it further), and its
# capsule can't be told apart from a near-black menu bar in pixels.
# Automatic geometry is hardware-notch; the Bar Size sliders are the
# manual override for anything else.



def led_band_rect(width: float):
    return ((0.0, 0.0), (float(width), LED_BAND_HEIGHT))


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


class VirtualLedView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(VirtualLedView, self).initWithFrame_(frame)
        if self is not None:
            self.state = LedDisplayState.IDLE
            self.brightness = 255
            self.started_at = time.monotonic()
            self.fixed_colors = None
            self.current_program = None
            self.wasm_controller = None
            self.wasm_error = None
            self.has_notch = True
            self.compact_mode = False
            self.wings_only_mode = False
            # None means "no wing" -- the notch silhouette fills the whole
            # view, today's exact behavior. Set to a value smaller than the
            # view's own width to inset the notch body and let the LED glow
            # spill into the remaining width on each side (see setNotchWidth_).
            self.notch_width = None
        return self

    def setHasNotch_(self, has_notch):
        self.has_notch = bool(has_notch)
        self.setNeedsDisplay_(True)

    def setCompactMode_(self, compact_mode):
        self.compact_mode = bool(compact_mode)
        self.setNeedsDisplay_(True)

    def setWingsOnlyMode_(self, wings_only_mode):
        self.wings_only_mode = bool(wings_only_mode)
        self.setNeedsDisplay_(True)

    def setNotchWidth_(self, notch_width):
        self.notch_width = None if notch_width is None else float(notch_width)
        self.setNeedsDisplay_(True)

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

    def setState_brightness_(self, state, brightness):
        self.setState_brightness_startedAt_(state, brightness, None)

    def setProgram_startedAt_(self, program, started_at):
        if program == self.current_program:
            self.setNeedsDisplay_(True)
            return
        self.current_program = str(program)
        self.fixed_colors = None
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

    def setProgram_(self, program):
        self.setProgram_startedAt_(program, None)

    def setBatteryPercent_brightness_(self, percent, brightness):
        self.current_program = None
        scale = normalize_brightness(brightness) / 255.0
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
        self.setNeedsDisplay_(True)

    def setPreviewWhiteBrightness_(self, brightness):
        """A plain white glow scaled by brightness alone, with no mode
        color and no animation -- used for the Settings window's live
        brightness/wing-extension preview, where the actual mode color
        would be a red herring (brightness applies the same regardless of
        mode) and the battery-style red/orange/green coloring above would
        misrepresent what's actually being previewed."""
        self.current_program = None
        scale = normalize_brightness(brightness) / 255.0
        self.fixed_colors = [(scale, scale, scale, scale)] * LED_COUNT
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

    def _colors_for_draw(self):
        """The colors actually painted this frame: the engine's target
        colors run through a short exponential low-pass (~90ms). The
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
        blend = 1.0 - math.exp(-(now - last_time) / 0.09)
        self._smoothed_colors = [
            tuple(p + (t - p) * blend for p, t in zip(prev, tgt))
            for prev, tgt in zip(previous, target)
        ]
        return self._smoothed_colors

    # The WASM engine is sampled at this rate; _colors_for_draw's ~90ms
    # low-pass interpolates between samples, so painting outpaces
    # stepping without visible stepping. Also dedups the 2-3 calls the
    # bar + riser draw paths make within one frame.
    TARGET_SAMPLE_INTERVAL = 1.0 / 15.0

    def _target_colors_for_draw(self):
        now = time.monotonic()
        cached = getattr(self, "_target_sample", None)
        if cached is not None and now - cached[0] < self.TARGET_SAMPLE_INTERVAL:
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

        colors = self._colors_for_draw()
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
        if self.has_notch:
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
        if self.has_notch:
            fill_rect_with_cg(
                cg_context,
                ((0.0, LED_BAND_HEIGHT - 0.55), (width, 0.55)),
                (0.0, 0.0, 0.0, 0.18),
            )
            fill_rect_with_cg(
                cg_context,
                ((0.0, 0.0), (width, 0.45)),
                (1.0, 1.0, 1.0, 0.055),
            )

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
        if style == "spatial" or (style == "auto" and (lit >= 2 or spatial_held)):
            return [_legibility_boost(c, floor) for c in colors]
        return [_legibility_boost(self._bar_identity_color(colors), floor)] * LED_COUNT

    def setBracketStyle_(self, style):
        self.bracket_style = str(style or "auto")

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
        colors = self._bracket_colors(self._colors_for_draw())
        width = self.bounds().size.width
        height = self.bounds().size.height
        notch_width, wing_offset = self._notch_geometry()
        led_width = notch_width / LED_COUNT
        glow_height = min(LED_GLOW_HEIGHT, max(0.0, height - LED_BAND_HEIGHT))
        cg_context = current_cg_context()

        # One rounded-rect clip softens every corner of the bracket at
        # once: the underline's ends curve up into the risers and the
        # riser tops get a cap -- no hard right angles anywhere.
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((0.0, 0.0), (width, height)),
            BRACKET_CORNER_RADIUS,
            BRACKET_CORNER_RADIUS,
        ).addClip()

        # The full-width underline: clip to the LED band (plus a whisper
        # of bloom above it) so the gap region shows a clean bright line
        # rather than the full-height glow that belongs to the wings.
        # Taper floor 1.0 -- the underline is ONE continuous line at one
        # intensity. With the wings' 0.55 floor here, the section under
        # the notch rendered hot while the wings faded, and the seam
        # between the two read as a rendering bug, not a design.
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath.bezierPathWithRect_(((0.0, 0.0), (width, LED_BAND_HEIGHT + 3.0))).addClip()
        self._fill_glow_row(
            cg_context, colors, led_width, notch_width, glow_height, height,
            x_start=0.0, x_end=width, wing_offset=wing_offset,
            wing_taper_floor=1.0,
        )
        NSGraphicsContext.restoreGraphicsState()

        if wing_offset > 0.0:
            for x_start, x_end in ((0.0, wing_offset), (wing_offset + notch_width, width)):
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
            cg_context, left_edge_color, 0.0, min(WING_RISER_WIDTH, width), height,
            outer_on_left=True,
        )
        self._draw_wing_riser(
            cg_context, right_edge_color, width - WING_RISER_WIDTH, width, height,
            outer_on_left=False,
        )
        NSGraphicsContext.restoreGraphicsState()

    def _fill_glow_row(self, cg_context, colors, led_width, notch_width, glow_height, _height, *, x_start, x_end, wing_offset, wing_taper_floor=0.0):
        """Draws the 4-layer LED glow (bloom / soft falloff / core / hotline)
        across [x_start, x_end) -- see glow_color_for_column for how a
        wing's glow is colored versus the notch body's own inter-LED
        blend."""
        column_x = x_start
        while column_x < x_end:
            column_width = min(BLEND_COLUMN_WIDTH, x_end - column_x)
            center_x = column_x + column_width / 2.0
            red, green, blue, alpha = glow_color_for_column(
                colors, led_width, notch_width, wing_offset, center_x, taper_floor=wing_taper_floor
            )
            if max(red, green, blue, alpha) <= 0.001:
                column_x += column_width
                continue

            # The light source is centered on its target LED and fades through
            # the neighboring LED width on both sides, for a three-LED footprint.
            fill_rect_with_cg(
                cg_context,
                ((column_x, LED_BAND_HEIGHT), (column_width, glow_height * 0.45)),
                tone_mapped_led_color(
                    red, green, blue, alpha, boost=0.82, alpha_scale=0.18
                ),
            )
            fill_rect_with_cg(
                cg_context,
                (
                    (column_x, LED_BAND_HEIGHT + glow_height * 0.45),
                    (column_width, glow_height * 0.55),
                ),
                tone_mapped_led_color(
                    red, green, blue, alpha, boost=0.64, alpha_scale=0.07
                ),
            )
            fill_rect_with_cg(
                cg_context,
                ((column_x, 0.0), (column_width, LED_BAND_HEIGHT)),
                tone_mapped_led_color(
                    red, green, blue, alpha, boost=LED_CORE_BOOST, alpha_scale=0.92
                ),
            )
            fill_rect_with_cg(
                cg_context,
                ((column_x, 0.0), (column_width, 1.15)),
                tone_mapped_led_color(
                    red, green, blue, alpha, boost=LED_HOTLINE_BOOST, alpha_scale=0.72
                ),
            )
            column_x += column_width

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

        Rendering detail: 48 steps, not 16 -- at ~37pt tall, 16 steps
        were ~2.3pt bands each and the riser read as a glitchy dotted
        column instead of a fading upright. And the riser is not a flat
        slab: a bright core hugs the window's outer edge while the
        inner half fades toward the bar, so the upright has one crisp
        edge (the bracket) and one soft edge (light).
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
        solid_height = height * WING_RISER_SOLID_FRACTION
        taper_height = max(1.0, height - solid_height)
        steps = 48
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
                tone_mapped_led_color(
                    red, green, blue, alpha, boost=LED_HOTLINE_BOOST, alpha_scale=0.9 * taper
                ),
            )
            if soft_width > 0.0:
                fill_rect_with_cg(
                    cg_context,
                    ((soft_rect_x, y_start), (soft_width, y_end - y_start)),
                    tone_mapped_led_color(
                        red, green, blue, alpha, boost=LED_CORE_BOOST, alpha_scale=0.34 * taper
                    ),
                )

    def _draw_compact_accent(self) -> None:
        """When another notch app (e.g. Alcove) is occupying the notch's
        own black shape, don't draw a second competing black backdrop --
        just a clean, thin colored line at the same position the normal
        bar would use, reading as a status accent rather than a floating
        widget. No body fill, no glow layers, no edge highlights.
        Painted in the single identity color -- see _bar_identity_color
        for why a spatial per-LED accent was mostly invisible."""
        colors = self._bracket_colors(self._colors_for_draw())
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
            self.wraps_menu_bar = False
        return self

    def set_wraps_menu_bar(self, enabled: bool) -> None:
        self.wraps_menu_bar = bool(enabled)

    def set_min_glow(self, fraction: float) -> None:
        if self.view is not None:
            self.view.setMinGlow_(fraction)

    def set_bracket_style(self, style: str) -> None:
        if self.view is not None:
            self.view.setBracketStyle_(style)

    def set_geometry_overrides(self, gap_width: float | None, wing_length: float | None) -> None:
        """The user's manual bar geometry (None = Automatic) -- see
        virtual_window_frame_for_screen."""
        self.gap_width_override = gap_width
        self.wing_length_override = wing_length

    def _install_frame_timer(self, interval: float) -> None:
        if self.timer is not None:
            self.timer.invalidate()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            interval, self, "redraw:", None, True
        )
        # Common modes too: a timer only in the default mode PAUSES
        # during menu tracking and window drags -- every opened menu
        # visibly froze the pulse mid-breath ("glitchy at times").
        NSRunLoop.currentRunLoop().addTimer_forMode_(self.timer, NSRunLoopCommonModes)
        self._frame_interval_current = interval

    def show(self):
        if self.window is None:
            self._build_window()
        self.reposition()
        self.window.orderFrontRegardless()
        if self.timer is None:
            self._install_frame_timer(FRAME_INTERVAL)
            self._static_frame_count = 0

    def hide(self):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        if self.window is not None:
            self.window.orderOut_(None)

    def set_state(
        self,
        state: LedDisplayState,
        brightness: float,
        *,
        started_at: float | None = None,
    ):
        self.show()
        self.view.setState_brightness_startedAt_(state, brightness, started_at)

    def set_battery(self, percent: int, brightness: float):
        self.show()
        self.view.setBatteryPercent_brightness_(percent, brightness)

    def set_program(self, program: str, *, started_at: float | None = None):
        self.show()
        self.view.setProgram_startedAt_(program, started_at)
        # A new program means motion may be coming -- restore full rate
        # immediately rather than waiting for a changed frame at 10Hz.
        self._static_frame_count = 0
        if (
            self.timer is not None
            and getattr(self, "_frame_interval_current", FRAME_INTERVAL) != FRAME_INTERVAL
        ):
            self._install_frame_timer(FRAME_INTERVAL)

    def reposition(self):
        screen = NSScreen.mainScreen()
        if screen is None or self.window is None:
            return
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
        alcove_active = is_alcove_running()
        wings_only = alcove_active and self.wraps_menu_bar
        compact = alcove_active and not self.wraps_menu_bar

        gap_override = getattr(self, "gap_width_override", None)
        wing_override = getattr(self, "wing_length_override", None)
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
        window_frame = virtual_window_frame_for_screen(
            screen,
            wrap_menu_bar=self.wraps_menu_bar,
            gap_width=effective_gap,
            wing_length=wing_override,
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
            self.view.setHasNotch_(screen_has_notch(screen))
            self.view.setCompactMode_(compact)
            self.view.setWingsOnlyMode_(wings_only)
            if frame_changed:
                self.view.setFrame_(((0, 0), window_frame[1]))
            if self.wraps_menu_bar:
                notch_width = float(effective_gap) if effective_gap else slot_width_for_screen(screen)
            else:
                notch_width = None
            self.view.setNotchWidth_(notch_width)

    def _build_window(self):
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
    def redraw_(self, _sender):
        view = self.view
        if view is not None:
            # Only mark dirty when this frame would actually look
            # different: a STATIC program (idle solid, all-off) used to
            # re-run the full 4-layer glow -- ~28k bridged fill calls
            # per second -- at 60fps for identical pixels.
            try:
                painted = tuple(
                    tuple(int(round(channel * 1024.0)) for channel in color)
                    for color in view._colors_for_draw()
                )
            except Exception:
                painted = None
            if painted is None or painted != getattr(self, "_last_marked_colors", None):
                self._last_marked_colors = painted
                view.setNeedsDisplay_(True)
                self._static_frame_count = 0
                if getattr(self, "_frame_interval_current", FRAME_INTERVAL) != FRAME_INTERVAL:
                    # Motion returned -- full frame rate.
                    self._install_frame_timer(FRAME_INTERVAL)
            else:
                self._static_frame_count = getattr(self, "_static_frame_count", 0) + 1
                if (
                    self._static_frame_count >= IDLE_AFTER_STATIC_FRAMES
                    and getattr(self, "_frame_interval_current", FRAME_INTERVAL)
                    == FRAME_INTERVAL
                    and self.timer is not None
                ):
                    self._install_frame_timer(1.0 / IDLE_FRAME_RATE)
        # Alcove's overlay resizes as live activities come and go; track
        # it by re-running the (cheap, churn-guarded) reposition about
        # every two seconds rather than only on screen changes.
        self._reposition_tick = getattr(self, "_reposition_tick", 0) + 1
        if self._reposition_tick >= REPOSITION_EVERY_N_FRAMES:
            self._reposition_tick = 0
            self.reposition()
