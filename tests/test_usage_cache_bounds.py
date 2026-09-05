"""The scan cache is an accelerator, not a ledger -- it must stay bounded.

Measured on the owner's machine before these bounds existed: 18.2 MB holding
211k records and 90 days of history, for a graph window defaulting to 7 days.
That file is parsed into Python objects on every scan, which made it the
largest single contributor to the app's resident memory.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from sidepulse import usage_stats
from sidepulse.private_io import atomic_private_write


DAY = 24 * 60 * 60


def _claude_line(session: str, message_id: str, epoch: float) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    return json.dumps(
        {
            "type": "assistant",
            "sessionId": session,
            "timestamp": stamp,
            "message": {
                "id": message_id,
                "model": "claude-opus-4",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        }
    )


def _write_transcript(root: Path, name: str, lines: list[str]) -> Path:
    project = root / "project"
    project.mkdir(parents=True, exist_ok=True)
    target = project / name
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _scan(root: Path, cache: Path, *, since_epoch: float) -> usage_stats.UsageTotals:
    return usage_stats.scan_usage(root, cache, since_epoch=since_epoch)


def test_records_outside_the_widest_window_are_not_cached(tmp_path: Path) -> None:
    """A 7-day window must not leave 90 days of records resident forever."""
    now = time.time()
    root = tmp_path / "projects"
    _write_transcript(
        root,
        "session.jsonl",
        [
            _claude_line("s1", "fresh", now - 1 * DAY),
            _claude_line("s1", "ancient", now - 60 * DAY),
        ],
    )
    cache = tmp_path / "usage-scan-cache.json"

    _scan(root, cache, since_epoch=now - 7 * DAY)

    payload = json.loads(cache.read_text(encoding="utf-8"))
    cached_epochs = [
        record[3]
        for entry in payload["files"].values()
        for record in entry["records"]
    ]
    assert cached_epochs, "the fresh record should still be cached"
    assert min(cached_epochs) > now - 30 * DAY, (
        "a record 60 days old was retained for a 7-day window"
    )


def test_widening_the_window_rescans_instead_of_undercounting(tmp_path: Path) -> None:
    """The failure mode retention introduces, guarded.

    A truncated entry still matches its file on (mtime, size, device, inode).
    Without the recorded floor, the next scan would accept that partial entry
    as the file's complete history and report a usage number that is simply
    wrong -- quietly, and for as long as the file is untouched.
    """
    now = time.time()
    root = tmp_path / "projects"
    _write_transcript(
        root,
        "session.jsonl",
        [
            _claude_line("s1", "fresh", now - 1 * DAY),
            _claude_line("s1", "older", now - 45 * DAY),
        ],
    )
    cache = tmp_path / "usage-scan-cache.json"

    narrow = _scan(root, cache, since_epoch=now - 7 * DAY)
    assert len(narrow.records) == 1, "the 7-day window should see one record"

    # Same file, untouched. Only the question changed.
    wide = _scan(root, cache, since_epoch=now - 90 * DAY)
    assert len(wide.records) == 2, (
        "widening the window returned a truncated cache entry as complete"
    )


def test_unbounded_window_still_retains_everything(tmp_path: Path) -> None:
    """since_epoch=0 means 'no window'; retention must not silently apply."""
    now = time.time()
    root = tmp_path / "projects"
    _write_transcript(
        root,
        "session.jsonl",
        [
            _claude_line("s1", "fresh", now - 1 * DAY),
            _claude_line("s1", "ancient", now - 300 * DAY),
        ],
    )
    cache = tmp_path / "usage-scan-cache.json"

    _scan(root, cache, since_epoch=0.0)

    payload = json.loads(cache.read_text(encoding="utf-8"))
    cached = [
        record[3]
        for entry in payload["files"].values()
        for record in entry["records"]
    ]
    assert len(cached) == 2, "an unbounded scan must cache every record"


def test_cache_write_respects_the_byte_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The budget binds even when every record is inside the window."""
    now = time.time()
    root = tmp_path / "projects"
    for index in range(6):
        _write_transcript(
            root,
            f"session-{index}.jsonl",
            [
                _claude_line(f"s{index}", f"m{index}-{n}", now - 60 * (n + 1))
                for n in range(40)
            ],
        )
    cache = tmp_path / "usage-scan-cache.json"

    # Small enough to admit some files and refuse the rest.
    monkeypatch.setattr(usage_stats, "USAGE_CACHE_MAX_BYTES", 4_000)
    _scan(root, cache, since_epoch=now - 7 * DAY)

    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["files"], "the budget should still admit the newest files"
    assert len(payload["files"]) < 6, "the byte budget never bound"
    assert cache.stat().st_size < 40_000, "cache far exceeded its budget"


def test_oversized_cache_is_refused_not_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache written by any other version must degrade to a cold scan."""
    now = time.time()
    root = tmp_path / "projects"
    _write_transcript(root, "session.jsonl", [_claude_line("s1", "only", now - 60)])
    cache = tmp_path / "usage-scan-cache.json"

    atomic_private_write(cache, "x" * 50_000)
    monkeypatch.setattr(usage_stats, "USAGE_CACHE_MAX_BYTES", 4_000)

    totals = _scan(root, cache, since_epoch=now - 7 * DAY)

    assert totals.records, "an oversized cache must not break the scan"


def test_dedupe_digest_is_truncated(tmp_path: Path) -> None:
    """The dedupe table was 41% of the cache at full SHA-256 width."""
    now = time.time()
    root = tmp_path / "projects"
    _write_transcript(root, "session.jsonl", [_claude_line("s1", "only", now - 60)])
    cache = tmp_path / "usage-scan-cache.json"

    _scan(root, cache, since_epoch=now - 7 * DAY)

    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["dedupes"], "expected a dedupe table"
    for value in payload["dedupes"]:
        assert value.startswith("message:")
        assert len(value) == len("message:") + usage_stats.DEDUPE_DIGEST_HEX_CHARS


def test_files_outside_the_window_are_remembered_not_rescanned(tmp_path: Path) -> None:
    """The regression that pinned a real machine's CPU at 104%.

    Retention drops records older than the window. If the whole ENTRY is
    dropped when nothing survives, the file is absent from the cache -- so
    the next scan re-reads it from disk, finds nothing in window, and drops
    it again. Forever, every cycle.

    On the owner's corpus that was ~2,000 of 2,633 transcripts re-read every
    five minutes. Keeping a record-less entry costs ~300 bytes and is the
    entire point of having a cache.
    """
    now = time.time()
    root = tmp_path / "projects"
    _write_transcript(root, "old.jsonl", [_claude_line("s-old", "ancient", now - 60 * DAY)])
    _write_transcript(root, "new.jsonl", [_claude_line("s-new", "fresh", now - 1 * DAY)])
    cache = tmp_path / "usage-scan-cache.json"

    _scan(root, cache, since_epoch=now - 7 * DAY)

    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert len(payload["files"]) == 2, (
        "the out-of-window file was forgotten and will be rescanned every cycle"
    )
    empty = [
        entry for entry in payload["files"].values() if not entry["records"]
    ]
    assert len(empty) == 1, "expected one record-less entry for the old file"


def test_a_spent_budget_still_remembers_the_files_that_cost_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same 104% CPU pin, reached by the other road.

    Candidates are admitted newest first, so the record-heavy entries come
    first and the record-less ones -- the whole reason the previous test
    exists -- come last. Stopping at the first entry that did not fit meant
    the budget was spent on recent history and every out-of-window file was
    re-read on every cycle anyway. On a 365-day window over 5,600 transcripts
    that was ~5,000 files per scan.
    """
    now = time.time()
    root = tmp_path / "projects"
    # Admission order is by file mtime, newest first, so the busy files are
    # stamped newer than the ancient ones -- which is what a real corpus looks
    # like and what puts the cheap entries last.
    for index in range(6):
        target = _write_transcript(
            root,
            f"ancient-{index}.jsonl",
            [_claude_line(f"old{index}", f"old{index}", now - 60 * DAY)],
        )
        os.utime(target, (now - 60 * DAY, now - 60 * DAY))
    for index in range(4):
        target = _write_transcript(
            root,
            f"busy-{index}.jsonl",
            [
                _claude_line(f"s{index}", f"m{index}-{n}", now - 60 * (n + 1))
                for n in range(40)
            ],
        )
        os.utime(target, (now - 60, now - 60))
    cache = tmp_path / "usage-scan-cache.json"

    # Room for one busy file, then exactly the six cheap ones. Derived from the
    # module's own costs so this stays honest if they are retuned.
    busy_cost = usage_stats._CACHE_BYTES_PER_ENTRY + 40 * usage_stats._CACHE_BYTES_PER_RECORD
    monkeypatch.setattr(
        usage_stats,
        "USAGE_CACHE_MAX_BYTES",
        busy_cost + 6 * usage_stats._CACHE_BYTES_PER_ENTRY,
    )
    _scan(root, cache, since_epoch=now - 7 * DAY)

    payload = json.loads(cache.read_text(encoding="utf-8"))
    remembered = set(payload["files"])
    empty = [entry for entry in payload["files"].values() if not entry["records"]]

    assert len(empty) == 6, (
        "the out-of-window files were skipped once the budget ran out on the "
        "newer, record-heavy ones, so every one of them is re-read on every "
        "scan cycle"
    )
    assert len(remembered) == 7, "the byte budget never bound at all"


def test_the_file_cap_does_not_bind_before_the_byte_budget(tmp_path: Path) -> None:
    """A real corpus is 5,605 transcripts; the cap used to be 4,096."""
    assert usage_stats.USAGE_CACHE_MAX_FILES >= 8192
    assert (
        usage_stats.USAGE_CACHE_MAX_FILES * usage_stats._CACHE_BYTES_PER_ENTRY
        < usage_stats.USAGE_CACHE_MAX_BYTES
    ), "remembering an out-of-window file must stay affordable at the cap"


def test_a_remembered_empty_entry_is_served_from_cache(tmp_path: Path) -> None:
    """Prove the read is actually skipped, not merely that the entry exists."""
    now = time.time()
    root = tmp_path / "projects"
    _write_transcript(
        root, "old.jsonl", [_claude_line("s-old", "ancient", now - 60 * DAY)]
    )
    _write_transcript(
        root, "new.jsonl", [_claude_line("s-new", "fresh", now - 1 * DAY)]
    )
    cache = tmp_path / "usage-scan-cache.json"

    first = _scan(root, cache, since_epoch=now - 7 * DAY)
    assert first.source_coverage["claude"].cache_hits == 0, "cold scan should read"

    second = _scan(root, cache, since_epoch=now - 7 * DAY)
    assert second.source_coverage["claude"].cache_hits == 2, (
        "the out-of-window file was re-read instead of served from cache"
    )


def test_year_of_positive_files_does_not_repeat_reads_past_the_view_cache_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    root = tmp_path / "projects"
    paths = []
    for index in range(6):
        paths.append(_write_transcript(
            root, f"session-{index}.jsonl",
            [_claude_line(f"s{index}", f"m{index}-{n}", now - (n + 1) * DAY) for n in range(40)],
        ))
    cache = tmp_path / "usage-scan-cache.json"
    monkeypatch.setattr(usage_stats, "USAGE_CACHE_MAX_BYTES", 4_000)
    cold = _scan(root, cache, since_epoch=now - 365 * DAY)
    assert len(cold.records) == 240
    reads = []
    original = usage_stats._read_verified_prefix

    def observed(path, info, resume_offset=0):
        reads.append((path, resume_offset, info.st_size))
        return original(path, info, resume_offset)

    monkeypatch.setattr(usage_stats, "_read_verified_prefix", observed)
    warm = _scan(root, cache, since_epoch=now - 365 * DAY)
    assert warm.records == cold.records
    assert reads == [], "unchanged positive files must survive the view-cache byte limit"
    assert warm.source_coverage["claude"].cache_hits == 6

    changed = paths[0]
    offset = changed.stat().st_size
    with changed.open("a", encoding="utf-8") as handle:
        handle.write(_claude_line("s0", "appended", now - 1) + "\n")
    updated = _scan(root, cache, since_epoch=now - 365 * DAY)
    assert len(updated.records) == 241
    assert reads == [(changed, offset, changed.stat().st_size)]


def test_changing_date_ranges_reuses_the_full_file_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    root = tmp_path / "projects"
    _write_transcript(root, "session.jsonl", [
        _claude_line("s1", "fresh", now - DAY),
        _claude_line("s1", "old", now - 200 * DAY),
    ])
    cache = tmp_path / "usage-scan-cache.json"
    assert len(_scan(root, cache, since_epoch=now - 7 * DAY).records) == 1

    def unexpected_read(*_args, **_kwargs):
        pytest.fail("changing the chart range re-opened an already indexed transcript")

    monkeypatch.setattr(usage_stats, "_read_verified_prefix", unexpected_read)
    assert len(_scan(root, cache, since_epoch=now - 365 * DAY).records) == 2


def test_warm_index_validates_metadata_without_opening_unchanged_transcripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    root = tmp_path / "projects"
    transcript = _write_transcript(root, "session.jsonl", [_claude_line("s1", "m1", now - DAY)])
    cache = tmp_path / "usage-scan-cache.json"
    cold = _scan(root, cache, since_epoch=0.0)
    transcript_opens = []
    original = os.open

    def observed(path, flags, *args, **kwargs):
        if Path(path) == transcript:
            transcript_opens.append(path)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", observed)
    warm = _scan(root, cache, since_epoch=0.0)
    assert warm.records == cold.records
    assert warm.source_coverage["claude"].cache_hits == 1
    assert transcript_opens == [], "metadata-only cache hits must not reopen source files"


@pytest.mark.parametrize("corruption", ["records", "negative-index", "negative-tokens", "timestamp", "tables"])
def test_invalid_index_document_cannot_replace_source_usage(tmp_path: Path, corruption: str) -> None:
    now = time.time()
    root = tmp_path / "projects"
    _write_transcript(root, "session.jsonl", [_claude_line("s1", "m1", now - DAY)])
    cache = tmp_path / "usage-scan-cache.json"
    _scan(root, cache, since_epoch=0.0)
    # Remove the legacy fallback so an invalid index must be reparsed.
    cache.write_text("{}", encoding="utf-8")
    connection = sqlite3.connect(cache.with_suffix(".files.sqlite3"))
    try:
        key, text = connection.execute("SELECT file_key, document FROM files").fetchone()
        document = json.loads(text)
        if corruption == "records":
            document["entry"]["records"] = None
        elif corruption == "negative-index":
            document["entry"]["records"][0][1] = -1
        elif corruption == "negative-tokens":
            document["entry"]["records"][0][4] = -1
        elif corruption == "timestamp":
            document["entry"]["records"][0][3] = float("nan")
        else:
            document["sessions"] = {}
        connection.execute("UPDATE files SET document = ? WHERE file_key = ?", (json.dumps(document), key))
        connection.commit()
    finally:
        connection.close()
    recovered = _scan(root, cache, since_epoch=0.0)
    assert recovered.input_tokens == 10
    assert recovered.output_tokens == 5
    assert recovered.source_coverage["claude"].files_read == 1
