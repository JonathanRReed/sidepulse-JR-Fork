"""Detects whether a macOS Focus (Do Not Disturb, Work, Sleep, etc.) is
currently active, so LED brightness can dim/quiet down while one is on --
the device shouldn't keep pulsing at you during a call just because an
agent happens to be working.

There is no public API for this. The technique here -- reading
``~/Library/DoNotDisturb/DB/Assertions.json``, which macOS's focus daemon
(``focusd``) maintains with one entry per currently-active Focus assertion
-- is the same one several open-source menu-bar utilities rely on. Two
real caveats, both handled defensively rather than assumed away:

1. That file is TCC-protected -- the same protection macOS gives Mail/
   Messages/Photos. Reading it requires Full Disk Access granted manually
   in System Settings > Privacy & Security > Full Disk Access, for the
   *exact* binary doing the reading (the LaunchAgent's own Python
   interpreter, not Terminal or this source file). Without that grant,
   every read raises a permission error, which is caught below and turned
   into FocusSyncUnavailableError -- the feature just stays inactive,
   never blocks or breaks the LED sync loop.
2. The exact JSON schema has shifted across macOS releases and isn't
   documented anywhere official. Rather than depend on one exact key
   path (which could silently stop matching after an OS update),
   is_focus_active() searches the whole parsed structure for any
   non-empty "storeAssertionRecords" list -- the one marker for "at least
   one Focus is currently on" that's stayed consistent across the
   versions community tooling has published about. Any parse surprise is
   treated as "can't tell" (raises FocusSyncUnavailableError) rather than
   guessed at.

This was written and unit-tested against synthetic JSON shaped like the
documented format, but could not be verified against a real live Focus
session on the development machine -- that would have required first
granting Full Disk Access to a background Python process, which wasn't
done during development. Treat the exact detection as best-effort until
confirmed against a real Focus toggle.
"""

from __future__ import annotations

import json
from pathlib import Path

ASSERTIONS_PATH = Path("~/Library/DoNotDisturb/DB/Assertions.json").expanduser()


class FocusSyncUnavailableError(RuntimeError):
    pass


def _has_active_assertion(node: object) -> bool:
    if isinstance(node, dict):
        records = node.get("storeAssertionRecords")
        if isinstance(records, list) and records:
            return True
        return any(_has_active_assertion(value) for value in node.values())
    if isinstance(node, list):
        return any(_has_active_assertion(item) for item in node)
    return False


def is_focus_active() -> bool:
    """True if at least one macOS Focus is currently active.

    Raises FocusSyncUnavailableError if this can't be determined right now
    (no Full Disk Access, file missing, or unparseable) -- callers must
    treat that the same as "assume not active" rather than let it
    propagate into the LED sync loop.
    """
    try:
        raw = ASSERTIONS_PATH.read_text()
    except OSError as exc:
        raise FocusSyncUnavailableError(str(exc)) from exc
    if not raw.strip():
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FocusSyncUnavailableError(str(exc)) from exc
    return _has_active_assertion(data)
