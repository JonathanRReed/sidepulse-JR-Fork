"""Reads the current screen brightness so LED brightness can automatically
track it -- dim to match a dark room, brighten in daylight -- using
whatever the system (including its own auto-brightness, if the user has
that on) has already decided the screen should be.

``CoreDisplay_Display_GetUserBrightness`` is not part of Apple's public
SDK -- there is no documented, guaranteed-stable API for reading screen
brightness on Apple Silicon. This is a long-used technique (the open-source
``brightness`` CLI and several menu-bar utilities rely on the same call),
and it was verified working on the development Mac this feature was built
against, but Apple could change or remove it in a future macOS release
without notice. Every caller must treat failure as "unavailable" and fall
back to whatever brightness was already configured -- never let it raise
into a code path that assumes it always works.
"""

from __future__ import annotations

import ctypes

CORE_DISPLAY_PATH = "/System/Library/Frameworks/CoreDisplay.framework/CoreDisplay"

# A dim room shouldn't mean "can't see the LEDs at all" -- auto-brightness
# still guarantees at least this much (roughly 8%) regardless of how low
# the screen goes.
MIN_AUTO_BRIGHTNESS = 20


class DisplayBrightnessUnavailableError(RuntimeError):
    pass


_lib = None
_load_failed = False


def _ensure_lib():
    global _lib, _load_failed
    if _lib is not None:
        return _lib
    if _load_failed:
        raise DisplayBrightnessUnavailableError("CoreDisplay previously failed to load.")
    try:
        lib = ctypes.CDLL(CORE_DISPLAY_PATH)
        lib.CoreDisplay_Display_GetUserBrightness.restype = ctypes.c_double
        lib.CoreDisplay_Display_GetUserBrightness.argtypes = [ctypes.c_int]
    except Exception as exc:
        _load_failed = True
        raise DisplayBrightnessUnavailableError(str(exc)) from exc
    _lib = lib
    return lib


def current_screen_brightness_fraction() -> float:
    """Returns the main display's current brightness as 0.0-1.0.

    Raises DisplayBrightnessUnavailableError if this technique isn't
    available on this Mac/macOS version. Callers must catch this and fall
    back to a fixed brightness rather than letting it propagate into the
    LED sync loop.
    """
    try:
        from Quartz import CGMainDisplayID
    except Exception as exc:
        raise DisplayBrightnessUnavailableError(str(exc)) from exc

    lib = _ensure_lib()
    display_id = CGMainDisplayID()
    try:
        value = float(lib.CoreDisplay_Display_GetUserBrightness(display_id))
    except Exception as exc:
        raise DisplayBrightnessUnavailableError(str(exc)) from exc
    if value != value:  # NaN
        raise DisplayBrightnessUnavailableError("CoreDisplay returned NaN.")
    return max(0.0, min(1.0, value))


def auto_led_brightness() -> int:
    """The 0-255 LED brightness to use for auto-brightness-enabled devices
    right now. Raises DisplayBrightnessUnavailableError on failure --
    callers should catch this and keep the device's manually configured
    brightness for that tick instead of failing the whole LED sync.
    """
    fraction = current_screen_brightness_fraction()
    return max(MIN_AUTO_BRIGHTNESS, round(fraction * 255))
