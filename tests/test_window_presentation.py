"""The suite must never fight the owner for the desktop.

Reported live 2026-08-26: running the tests made the machine unusable
-- AppKit tests exercised product paths that called
makeKeyAndOrderFront_ / activateIgnoringOtherApps_, yanking focus from
whatever the owner was typing, repeatedly, for minutes. The fix is a
single presentation gate (window_presentation.py) plus a PROHIBITED
activation policy in conftest. These tests are the ratchet that keeps
both true.
"""

from __future__ import annotations

import re
from pathlib import Path

from sidepulse import window_presentation

SRC = Path(__file__).resolve().parent.parent / "src" / "sidepulse"

_TAKEOVER_CALLS = re.compile(
    r"activateIgnoringOtherApps_|orderFrontRegardless|makeKeyAndOrderFront_"
)


def test_no_direct_desktop_takeover_calls_outside_the_gate() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.glob("*.py")):
        if path.name == "window_presentation.py":
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _TAKEOVER_CALLS.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{path.name}:{line_number}: {line.strip()}")
    assert not offenders, (
        "window presentation must go through window_presentation.py "
        "(present_window/activate_app) so the test sandbox can suppress "
        f"it: {offenders}"
    )


def test_takeover_is_suppressed_inside_the_test_sandbox() -> None:
    # conftest exports SIDEPULSE_TESTING=1 for every test process.
    assert window_presentation.desktop_takeover_suppressed()


def test_present_window_is_inert_while_suppressed() -> None:
    class Window:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def makeKeyAndOrderFront_(self, _sender) -> None:
            self.calls.append("key")

        def orderFrontRegardless(self) -> None:
            self.calls.append("front")

    window = Window()
    window_presentation.present_window(window)
    window_presentation.present_window(window, key=False)
    window_presentation.activate_app()
    assert window.calls == []
