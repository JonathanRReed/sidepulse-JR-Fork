"""State logs must be bounded even when their writer goes quiet.

The machinery for this already existed twice over: `compact_jsonl_file` runs
on every hook write, and `trim_oversized_logs` sweeps the whole state
directory. The sweep had zero callers -- it was even imported into
status_bar and never invoked -- so a provider that stopped emitting left its
log frozen forever at whatever size it had reached.

Measured on the owner's machine before this was wired: codex.jsonl 23.1 MB
and devin.jsonl 10.6 MB, both far past the 5 MB threshold, neither shrinking
because nothing was appending to them any more.
"""

from __future__ import annotations

import json
from pathlib import Path

from sidepulse.audit import (
    TRIM_KEEP_LINES,
    TRIM_TARGET_BYTES,
    TRIM_THRESHOLD_BYTES,
    compact_jsonl_file,
    trim_oversized_logs,
)


def _oversized_log(path: Path, *, lines: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A wide payload so the file passes the byte threshold quickly.
    padding = "x" * 1500
    with path.open("w", encoding="utf-8") as handle:
        for index in range(lines):
            handle.write(json.dumps({"i": index, "pad": padding}) + "\n")
    return path


def test_a_quiet_providers_log_still_gets_trimmed(tmp_path: Path) -> None:
    quiet = _oversized_log(tmp_path / "codex.jsonl", lines=5000)
    assert quiet.stat().st_size > TRIM_THRESHOLD_BYTES

    assert trim_oversized_logs(tmp_path) == 1
    assert quiet.stat().st_size <= TRIM_THRESHOLD_BYTES


def test_trimming_keeps_the_newest_lines(tmp_path: Path) -> None:
    """The tail is what the collector reads; losing it would blank the UI."""
    log = _oversized_log(tmp_path / "codex.jsonl", lines=5000)
    trim_oversized_logs(tmp_path)

    kept = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    assert kept, "the log was emptied"
    assert len(kept) <= TRIM_KEEP_LINES
    assert kept[-1]["i"] == 4999, "the newest event was discarded"


def test_one_compaction_ends_the_need_to_compact(tmp_path: Path) -> None:
    """The treadmill, reproduced.

    Records vary ~500 to ~5,800 bytes, so a 4,000-line cap bounds a file
    anywhere between 1 MB and 22 MB. With wide records the file stayed above
    the 5 MB trigger after trimming, so the NEXT hook write trimmed it again:
    read 23 MB, rebuild, atomic replace, fsync -- per event.

    Observed live: codex.jsonl, devin.jsonl and event-status.jsonl all sat at
    exactly 4,000 lines and 23.1 / 10.7 / 7.8 MB.
    """
    log = _oversized_log(tmp_path / "codex.jsonl", lines=5000)

    assert compact_jsonl_file(log) is True
    assert log.stat().st_size <= TRIM_TARGET_BYTES
    assert log.stat().st_size < TRIM_THRESHOLD_BYTES

    # The whole point: a second write must NOT trigger another rewrite.
    assert compact_jsonl_file(log) is False, "compaction is on a treadmill"


def test_a_single_enormous_line_still_leaves_a_log(tmp_path: Path) -> None:
    """One record wider than the whole budget must not empty the file."""
    log = tmp_path / "codex.jsonl"
    log.write_text(json.dumps({"pad": "x" * (TRIM_TARGET_BYTES + 10)}) + "\n")
    if log.stat().st_size > TRIM_THRESHOLD_BYTES:
        compact_jsonl_file(log)
    assert log.read_text().strip(), "trimming emptied the log"


def test_small_logs_are_left_alone(tmp_path: Path) -> None:
    small = tmp_path / "claude.jsonl"
    small.write_text('{"i": 1}\n')
    assert trim_oversized_logs(tmp_path) == 0
    assert small.read_text() == '{"i": 1}\n'


def test_the_sweep_is_actually_called_at_launch() -> None:
    """It was imported into status_bar and never invoked. Reachable now."""
    from sidepulse import status_bar

    # inspect.getsource cannot read a PyObjC selector, so read the module.
    source = Path(status_bar.__file__).read_text(encoding="utf-8")
    launch = source.split("def applicationDidFinishLaunching_", 1)[1]
    body = launch.split("\n    def ", 1)[0]
    assert "trim_oversized_state_logs" in body, (
        "the state-log sweep is unreachable again"
    )


def test_the_sweep_survives_a_broken_state_dir(tmp_path: Path) -> None:
    """A log that cannot be trimmed must never take the launch down."""

    class _Probe:
        pass

    from sidepulse.status_bar import StatusBarController

    # No monkeypatching of the state dir: the real one is read, and whatever
    # it contains, this must return an int rather than raise.
    result = StatusBarController.trim_oversized_state_logs(_Probe())
    assert isinstance(result, int)


def test_orphaned_debug_cache_is_removed(tmp_path: Path) -> None:
    """17.7 MB of residue from a feature that no longer exists.

    Nothing in the tree reads or writes usage-debug-cache.json; it was left
    behind when a debug path was deleted, and it just sat there.
    """
    from sidepulse.audit import remove_orphaned_state_files

    orphan = tmp_path / "usage-debug-cache.json"
    sidecar = tmp_path / "usage-debug-cache.json.codex.transcripts.local"
    orphan.write_text("{}")
    sidecar.write_text("{}")

    assert remove_orphaned_state_files(tmp_path) == 2
    assert not orphan.exists() and not sidecar.exists()


def test_the_janitor_never_touches_live_state(tmp_path: Path) -> None:
    """A janitor that guesses is worse than the weight it removes."""
    from sidepulse.audit import remove_orphaned_state_files

    keep = {
        "usage-scan-cache.json": '{"version": 6}',
        "latest.json": "{}",
        "claude.jsonl": '{"i": 1}\n',
        "settings.json": "{}",
        "usage-debug-cache-something-else.txt": "keep me",
    }
    for name, body in keep.items():
        (tmp_path / name).write_text(body)

    assert remove_orphaned_state_files(tmp_path) == 0
    for name, body in keep.items():
        assert (tmp_path / name).read_text() == body


def test_the_janitor_survives_a_missing_state_dir(tmp_path: Path) -> None:
    from sidepulse.audit import remove_orphaned_state_files

    assert remove_orphaned_state_files(tmp_path / "nope") == 0
