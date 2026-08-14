"""The scan cache is an accelerator, not a ledger -- it must stay bounded.

Measured on the owner's machine before these bounds existed: 18.2 MB holding
211k records and 90 days of history, for a graph window defaulting to 7 days.
That file is parsed into Python objects on every scan, which made it the
largest single contributor to the app's resident memory.
"""

from __future__ import annotations

import json
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
