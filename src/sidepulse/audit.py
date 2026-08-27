"""State-directory janitors: bound every log, remove dead residue.

This module used to also hold the status-audit event log (writer, CSV and
HTML exporters). That plane was deleted 2026-08-26: the writer had been a
compatibility no-op since the canonical-history migration, so the exports
were permanently empty and the Settings pane had already stopped exposing
them. Its on-disk residue (event-status.jsonl) is now cleaned up by
remove_orphaned_state_files like every other removed feature's file.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .private_io import (
    atomic_private_write,
    read_private_text,
)

# Must stay BELOW the collector's LATEST_STATE_MAX_BYTES (4 MiB): with the
# trim threshold above the read cap, a log between the two was writable but
# unreadable and its provider went silent (the 2026-08-21 claude outage).
TRIM_THRESHOLD_BYTES = 3 * 1024 * 1024
TRIM_KEEP_LINES = 4000
# A line COUNT alone cannot bound a file: records vary from ~500 bytes
# (claude) to ~5,800 (codex), so 4,000 lines is anywhere from 1 MB to 22 MB.
# Measured live, three logs sat at exactly 4,000 lines and 23.1 / 10.7 /
# 7.8 MB -- pinned at the line cap and permanently above the byte threshold,
# so compaction re-ran on EVERY hook write: read the whole file, rebuild it,
# atomically replace it, fsync. 46 MB of I/O per event, forever.
#
# So the post-trim size is also bounded, strictly below the threshold that
# triggers a trim. One compaction now actually ends the need to compact.
TRIM_TARGET_BYTES = 2 * 1024 * 1024


# State files left behind by features that no longer exist. Nothing in the
# tree reads or writes these; they are pure residue from a removed debug
# path, and one of them was 17.7 MB on the owner's machine. Matched by exact
# stem so a rename can never turn this into a wildcard delete.
ORPHANED_STATE_STEMS = ("usage-debug-cache.json", "event-status.jsonl")


def remove_orphaned_state_files(state_dir: Path) -> int:
    """Delete state files whose owning feature was removed. Never raises.

    Deliberately narrow: exact names only, only directly inside the state
    directory, and only regular files. A cache with no reader is dead weight,
    but a janitor that guesses is worse than the weight.
    """
    removed = 0
    base = Path(state_dir)
    for stem in ORPHANED_STATE_STEMS:
        for path in (base / stem, *base.glob(f"{stem}.*")):
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            try:
                path.unlink()
            except OSError:
                continue
            removed += 1
    return removed


def trim_process_log(
    path: Path,
    *,
    max_bytes: int = TRIM_THRESHOLD_BYTES,
    target_bytes: int = TRIM_TARGET_BYTES,
) -> bool:
    """Bound a log that a live process holds open, keeping the same inode.

    stdout/stderr are redirected by launchd, which opens these paths once and
    holds an O_APPEND descriptor for the life of the process. Replacing the
    file atomically -- the way every other trim here works -- would leave the
    app writing into an unlinked inode: the log on disk would freeze, the
    output would go nowhere, and nothing would report it.

    So this truncates in place instead. Racing an append can cost a few bytes
    of a single line; a silently dead log costs every line after it.
    """
    target = Path(path)
    try:
        info = target.lstat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size <= max_bytes:
        return False
    try:
        descriptor = os.open(target, os.O_RDWR | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        size = os.lseek(descriptor, 0, os.SEEK_END)
        start = max(0, size - target_bytes)
        os.lseek(descriptor, start, os.SEEK_SET)
        tail = os.read(descriptor, target_bytes)
        if start > 0:
            # Drop the partial first line left by seeking into the middle.
            newline = tail.find(b"\n")
            tail = tail[newline + 1 :] if newline >= 0 else b""
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, tail)
        os.ftruncate(descriptor, len(tail))
    except OSError:
        return False
    finally:
        os.close(descriptor)
    return True


def trim_oversized_process_logs(state_dir: Path) -> int:
    """Bound the app's own stdout/stderr logs. Never raises.

    Measured here: 8.6 MB of stderr (one hour of a since-fixed hook crash
    loop) and 7.1 MB of stdout, neither of which anything ever bounded.
    """
    trimmed = 0
    try:
        candidates = sorted(Path(state_dir).glob("*.log"))
    except OSError:
        return 0
    for path in candidates:
        try:
            if trim_process_log(path):
                trimmed += 1
        except OSError:
            continue
    return trimmed


def trim_oversized_logs(state_dir: Path) -> int:
    """Rotates every .jsonl under the state dir that has grown past
    TRIM_THRESHOLD_BYTES down to its last TRIM_KEEP_LINES lines
    (atomic replace). Hook logs and the event ledger appended forever
    with no cap -- a long-lived install accumulated unbounded disk and
    made every export read the whole history into memory. Returns how
    many files were trimmed; never raises."""
    trimmed = 0
    try:
        candidates = list(Path(state_dir).rglob("*.jsonl"))
    except OSError:
        return 0
    for path in candidates:
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            if compact_jsonl_file(path):
                trimmed += 1
        except OSError:
            continue
    return trimmed


def compact_jsonl_file(path: Path) -> bool:
    """Atomically bound one active JSONL file to its newest useful tail."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size <= TRIM_THRESHOLD_BYTES:
        return False
    lines = read_private_text(path, errors="replace").splitlines(keepends=True)
    atomic_private_write(path, "".join(_bounded_tail(lines)))
    return True


def _bounded_tail(lines: list[str]) -> list[str]:
    """Newest lines within BOTH the line cap and the byte budget.

    Always keeps at least one line: an empty log would read as "no history"
    to the collector rather than "history was trimmed".
    """
    kept = lines[-TRIM_KEEP_LINES:]
    if not kept:
        return kept
    total = 0
    first = len(kept) - 1
    for index in range(len(kept) - 1, -1, -1):
        total += len(kept[index].encode("utf-8", errors="replace"))
        if total > TRIM_TARGET_BYTES and index < len(kept) - 1:
            break
        first = index
    return kept[first:]
