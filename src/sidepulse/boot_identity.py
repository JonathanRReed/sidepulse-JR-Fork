"""The machine's boot identity, for clock-continuity comparisons."""

from __future__ import annotations

import time

_BOOT_EPOCH_BUCKET_SECONDS = 10


def boot_identifier_basis() -> str:
    """The kernel's own boot instant -- stable across sleep and restarts.

    The previous basis, ``(wall - monotonic) // 10``, DRIFTS: macOS
    monotonic time pauses during sleep, so the offset grows by every
    nap's length, and any restart after >=10s of cumulative sleep
    manufactured a "new boot" against the persisted clock -- which
    quarantined every source on wake. ``kern.boottime`` does not move.
    """
    try:
        import subprocess

        raw = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "kern.boottime"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=True,
        ).stdout.strip()
        if raw:
            return raw
    except Exception:
        pass
    # Fallback keeps the old (imperfect) basis rather than failing.
    return str(int((time.time() - time.monotonic()) // _BOOT_EPOCH_BUCKET_SECONDS))
