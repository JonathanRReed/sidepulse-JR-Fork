"""Keep a drawing bug from being a fatal one.

A Python exception raised inside a PyObjC `drawRect:` does not fail the draw.
PyObjC converts it to an Objective-C exception (`PyObjCErr_ToObjCWithGILState`),
AppKit hands that to `+[NSApplication _crashOnException:]`, and the process
takes `EXC_BREAKPOINT`/`SIGTRAP` on the main thread. SidePulse died exactly that
way on 2026-08-14 at 10:38:22, one minute after launch, inside
`_NSViewDrawRect` under `-[NSViewBackingLayer display]` -- and with no launchd
job behind the status bar it stayed dead. Every stale row the owner then saw,
and every hook event that piled up against a socket nobody was listening on,
followed from one exception in one chart.

So no draw callback may end the process. A guarded callback that raises leaves
its rectangle undrawn, records the failure so it is visible rather than
swallowed, and returns. The guard is deliberately not a substitute for
validating what a view is asked to draw; it is the floor under it.

Pure module: no AppKit import, so the guard itself is testable without a window
server, which is the whole reason the defect it protects against went unseen.
"""

from __future__ import annotations

import functools
import sys
import threading

# Bounded so a view failing every frame cannot turn a rendering bug into an
# unbounded log or an unbounded dict.
MAX_TRACKED_DRAW_FAILURES = 16
MAX_DRAW_FAILURE_COUNT = 1_000_000

_LOCK = threading.Lock()
_FAILURES: dict[str, int] = {}


def record_draw_failure(name: str, error: BaseException | None = None) -> int:
    """Count one failed draw for `name`, reporting the first of each kind."""
    key = str(name)[:120] or "unknown"
    with _LOCK:
        first = key not in _FAILURES
        if first and len(_FAILURES) >= MAX_TRACKED_DRAW_FAILURES:
            key = "other"
            first = key not in _FAILURES
        count = min(MAX_DRAW_FAILURE_COUNT, _FAILURES.get(key, 0) + 1)
        _FAILURES[key] = count
    if first:
        try:
            print(
                f"sidepulse: draw failed in {key}: {error!r}; "
                "the view is skipped rather than killing the app",
                file=sys.stderr,
                flush=True,
            )
        except Exception:
            pass
    return count


def draw_failures() -> tuple[tuple[str, int], ...]:
    with _LOCK:
        return tuple(sorted(_FAILURES.items()))


def reset_draw_failures() -> None:
    with _LOCK:
        _FAILURES.clear()


def guard_draw(method):
    """Wrap one `drawRect:` so a Python exception cannot reach AppKit.

    The wrapper keeps the original name and the exact two-argument shape PyObjC
    uses to derive the selector, so the guarded method is still `drawRect:` to
    the runtime.
    """

    @functools.wraps(method)
    def drawRect_(self, rect):
        try:
            return method(self, rect)
        except Exception as error:  # noqa: BLE001 - the point is to catch all.
            record_draw_failure(type(self).__name__, error)
            return None

    return drawRect_


__all__ = [
    "MAX_TRACKED_DRAW_FAILURES",
    "draw_failures",
    "guard_draw",
    "record_draw_failure",
    "reset_draw_failures",
]
