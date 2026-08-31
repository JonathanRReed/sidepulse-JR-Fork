"""Read the active macOS display brightness for automatic LED scaling.

macOS has no public API for reading display brightness. DisplayServices
currently matches the Control Center slider, while the older CoreDisplay
reader and AppleARMBacklight's raw ``brightness`` key can return convincing
but incorrect values. A built-in panel may fall back to the IOKit registry's
``BrightnessMilliNits`` ratio. External, inactive, sleeping, and unreadable
displays remain unavailable so callers retain the configured manual value.
"""

from __future__ import annotations

import ctypes
import math
import re
import subprocess

DISPLAY_SERVICES_PATH = (
    "/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices"
)
IOREG_PATH = "/usr/sbin/ioreg"
IOREG_TIMEOUT_SECONDS = 1.0

# A dim room shouldn't mean "can't see the LEDs at all" -- auto-brightness
# still guarantees at least this much (roughly 8%) regardless of how low
# the screen goes.
MIN_AUTO_BRIGHTNESS = 20


class DisplayBrightnessUnavailableError(RuntimeError):
    pass


_display_services_lib = None
_display_services_load_failed = False


def _ensure_display_services_lib():
    global _display_services_lib, _display_services_load_failed
    if _display_services_lib is not None:
        return _display_services_lib
    if _display_services_load_failed:
        raise DisplayBrightnessUnavailableError(
            "DisplayServices previously failed to load."
        )
    try:
        lib = ctypes.CDLL(DISPLAY_SERVICES_PATH)
        lib.DisplayServicesGetBrightness.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_float),
        ]
        lib.DisplayServicesGetBrightness.restype = ctypes.c_int
    except Exception as exc:
        _display_services_load_failed = True
        raise DisplayBrightnessUnavailableError(str(exc)) from exc
    _display_services_lib = lib
    return lib


def display_services_brightness_fraction(display_id: int) -> float | None:
    """Return DisplayServices brightness when it is finite and in range."""
    try:
        lib = _ensure_display_services_lib()
        value = ctypes.c_float()
        status = lib.DisplayServicesGetBrightness(
            ctypes.c_uint32(display_id),
            ctypes.byref(value),
        )
        fraction = float(value.value)
    except Exception:
        return None
    if status != 0 or not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
        return None
    return fraction


def ioreg_nits_fraction(text: str) -> float | None:
    """Parse only BrightnessMilliNits value/max from an IOKit registry dump."""
    match = re.search(r'"BrightnessMilliNits"\s*=\s*\{([^}]*)\}', text)
    if match is None:
        return None
    body = match.group(1)
    value_match = re.search(r'"value"\s*=\s*(\d+)', body)
    maximum_match = re.search(r'"max"\s*=\s*(\d+)', body)
    if value_match is None or maximum_match is None:
        return None
    maximum = int(maximum_match.group(1))
    if maximum <= 0:
        return None
    return max(0.0, min(1.0, int(value_match.group(1)) / maximum))


def _ioreg_brightness_fraction() -> float | None:
    try:
        output = subprocess.run(
            [IOREG_PATH, "-rc", "AppleARMBacklight"],
            capture_output=True,
            text=True,
            timeout=IOREG_TIMEOUT_SECONDS,
            check=False,
        ).stdout
    except Exception:
        return None
    return ioreg_nits_fraction(output)


def _main_display_id() -> int:
    try:
        from Quartz import CGMainDisplayID

        return int(CGMainDisplayID())
    except Exception as exc:
        raise DisplayBrightnessUnavailableError(str(exc)) from exc


def _display_state(display_id: int) -> tuple[bool, bool, bool]:
    try:
        from Quartz import CGDisplayIsActive, CGDisplayIsAsleep, CGDisplayIsBuiltin

        return (
            bool(CGDisplayIsActive(display_id)),
            bool(CGDisplayIsAsleep(display_id)),
            bool(CGDisplayIsBuiltin(display_id)),
        )
    except Exception as exc:
        raise DisplayBrightnessUnavailableError(str(exc)) from exc


def _current_display_context() -> tuple[int, bool]:
    display_id = _main_display_id()
    active, asleep, built_in = _display_state(display_id)
    if not active or asleep:
        raise DisplayBrightnessUnavailableError("Main display is inactive or asleep.")
    return display_id, built_in


def current_screen_brightness_fraction() -> float:
    """Return the current main display's truthful brightness as 0.0-1.0.

    The main display identifier is resolved on every call because menu-bar
    ownership can move between displays. Unsupported external displays and
    unreadable built-in displays raise ``DisplayBrightnessUnavailableError``.
    """
    display_id, built_in = _current_display_context()
    fraction = display_services_brightness_fraction(display_id)
    if fraction is not None:
        return fraction
    if not built_in:
        raise DisplayBrightnessUnavailableError(
            "DisplayServices did not report brightness for the external display."
        )
    fraction = _ioreg_brightness_fraction()
    if fraction is not None:
        return fraction
    raise DisplayBrightnessUnavailableError("No trustworthy brightness reading is available.")


def auto_led_brightness() -> int:
    """The 0-255 LED brightness to use for auto-brightness-enabled devices
    right now. Raises DisplayBrightnessUnavailableError on failure --
    callers should catch this and keep the device's manually configured
    brightness for that tick instead of failing the whole LED sync.
    """
    fraction = current_screen_brightness_fraction()
    return max(MIN_AUTO_BRIGHTNESS, round(fraction * 255))
