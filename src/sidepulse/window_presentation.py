"""One rule for putting a window in front of the person.

Every window-presentation site used to call makeKeyAndOrderFront_ /
orderFrontRegardless / activateIgnoringOtherApps_ directly. Under the
test suite that meant the FULL desktop takeover, thousands of times per
run: focus yanked from whatever the owner was typing, for minutes
("makes this computer unusable", reported live 2026-08-26). Routing
every site through this module makes "never fight the user for the
desktop while testing" a property of the codebase instead of a hope.

In production nothing changes: presenting and activating are exactly
what a person's click asked for.
"""

from __future__ import annotations

import os


def desktop_takeover_suppressed() -> bool:
    """True when this process must not raise windows or steal focus --
    the test sandbox (conftest sets SIDEPULSE_TESTING, and pytest sets
    PYTEST_CURRENT_TEST) or an explicit headless opt-in."""
    return (
        os.environ.get("SIDEPULSE_TESTING") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
        or os.environ.get("SIDEPULSE_HEADLESS") == "1"
    )


def present_window(window, *, key: bool = True) -> None:
    """Order a window front (key when asked), unless suppressed."""
    if window is None or desktop_takeover_suppressed():
        return
    try:
        if key:
            window.makeKeyAndOrderFront_(None)
        else:
            window.orderFrontRegardless()
    except Exception:
        pass


def activate_app() -> None:
    """Bring the app forward for a user-initiated moment, unless
    suppressed. Falls back across the AppKit API generations."""
    if desktop_takeover_suppressed():
        return
    try:
        from AppKit import NSApp

        try:
            NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            NSApp.activate()
    except Exception:
        pass


__all__ = [
    "activate_app",
    "desktop_takeover_suppressed",
    "present_window",
]
