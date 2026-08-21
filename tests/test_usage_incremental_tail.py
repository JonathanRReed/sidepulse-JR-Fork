"""The usage cache tails a grown transcript instead of re-reading it.

Live transcripts grow every few seconds, so the ACTIVE session file
failed the size/mtime cache match on every poll and was re-parsed IN
FULL each time -- measured at ~15-20% of a core in bursts on a 10MB
claude.jsonl (2026-08-20). A grown file (same device+inode, cached
parse ended on a newline) now resumes from the cached byte offset.
"""

from __future__ import annotations

import json

import pytest

from sidepulse import usage_stats


def _assistant_row(message_id, *, inp=1000, out=500):
    return {
        "type": "assistant",
        "timestamp": "2026-08-11T12:00:00Z",
        "message": {
            "id": message_id,
            "model": "claude-sonnet-5",
            "usage": {
                "input_tokens": inp,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "output_tokens": out,
            },
        },
    }


def _write_lines(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def _append_lines(path, rows):
    with path.open("a") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


@pytest.fixture
def counting_full_parse(monkeypatch):
    calls = []
    real = usage_stats._parse_file

    def wrapper(path, expected_stat, dedupe_secret):
        calls.append(str(path))
        return real(path, expected_stat, dedupe_secret)

    monkeypatch.setattr(usage_stats, "_parse_file", wrapper)
    return calls


def test_appended_lines_parse_without_a_full_reread(tmp_path, counting_full_parse):
    root = tmp_path / "claude"
    transcript = root / "proj" / "session.jsonl"
    cache = tmp_path / "cache.json"
    _write_lines(transcript, [_assistant_row("msg_a"), _assistant_row("msg_b")])

    first = usage_stats.scan_usage(root, cache)
    assert first.input_tokens == 2000
    assert counting_full_parse == [str(transcript)]  # cold scan reads it

    _append_lines(transcript, [_assistant_row("msg_c", inp=7000, out=1)])
    second = usage_stats.scan_usage(root, cache)

    # The appended record landed WITHOUT a second full parse.
    assert second.input_tokens == 9000
    assert counting_full_parse == [str(transcript)], (
        "a grown file must resume from the cached offset, not re-read"
    )

    # And the incremental result persists: a third scan with no growth
    # is a pure cache hit (no parse of any kind).
    third = usage_stats.scan_usage(root, cache)
    assert third.input_tokens == 9000
    assert counting_full_parse == [str(transcript)]


def test_truncated_file_falls_back_to_a_full_reparse(tmp_path, counting_full_parse):
    root = tmp_path / "claude"
    transcript = root / "proj" / "session.jsonl"
    cache = tmp_path / "cache.json"
    _write_lines(
        transcript,
        [_assistant_row("msg_a"), _assistant_row("msg_b"), _assistant_row("msg_c")],
    )
    usage_stats.scan_usage(root, cache)
    assert len(counting_full_parse) == 1

    # Rewrite SHORTER (a rotation/rewrite, not growth): full reparse.
    _write_lines(transcript, [_assistant_row("msg_z", inp=42, out=0)])
    result = usage_stats.scan_usage(root, cache)
    assert result.input_tokens == 42
    assert len(counting_full_parse) == 2


def test_a_mid_line_snapshot_never_splits_the_line(tmp_path, counting_full_parse):
    root = tmp_path / "claude"
    transcript = root / "proj" / "session.jsonl"
    cache = tmp_path / "cache.json"
    _write_lines(transcript, [_assistant_row("msg_a")])
    # Strip the trailing newline: the snapshot ends mid-line. The
    # prefix-bounded read trims back to the last newline, so the
    # partial line is simply not consumed yet.
    transcript.write_text(transcript.read_text().rstrip("\n"))
    first = usage_stats.scan_usage(root, cache)
    assert first.input_tokens == 0  # the only line is still unfinished
    assert len(counting_full_parse) == 1

    # The writer completes the line and appends another: BOTH arrive via
    # the incremental tail, never split, with no second full reparse.
    with transcript.open("a") as handle:
        handle.write("\n" + json.dumps(_assistant_row("msg_b")) + "\n")
    result = usage_stats.scan_usage(root, cache)
    assert result.input_tokens == 2000
    assert len(counting_full_parse) == 1


def test_codex_tail_total_replaces_the_cached_cumulative(tmp_path):
    codex_root = tmp_path / "codex"
    claude_root = tmp_path / "claude"
    claude_root.mkdir()
    rollout = codex_root / "rollout-x.jsonl"
    cache = tmp_path / "cache.json"

    def token_count(total, when):
        return {
            "timestamp": when,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": total,
                        "cached_input_tokens": 0,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 5,
                    }
                },
            },
        }

    _write_lines(rollout, [token_count(100, "2026-08-11T12:00:00Z")])
    first = usage_stats.scan_usage(claude_root, cache, codex_root=codex_root)
    assert first.codex_tokens == 105  # input 100 + output 5

    # The rollout's totals are CUMULATIVE: the appended count is the new
    # session total and must REPLACE the cached record, not add to it.
    _append_lines(rollout, [token_count(250, "2026-08-11T13:00:00Z")])
    second = usage_stats.scan_usage(claude_root, cache, codex_root=codex_root)
    assert second.codex_tokens == 255


def test_a_file_growing_mid_scan_is_parsed_not_skipped(tmp_path):
    """The live pin: the scan works from a frozen inventory, and the
    hottest file always grew between its stat and its open -- the strict
    verifier refused it as 'changed while opening' on EVERY scan, so
    codex's weekly froze at an older file's number (observed live
    2026-08-20: pinned 99% while the active rollout said 95%). Growth
    is history, not corruption: parse the snapshot's bytes."""
    import os

    transcript = tmp_path / "session.jsonl"
    _write_lines(transcript, [_assistant_row("msg_a"), _assistant_row("msg_b")])
    frozen = os.stat(transcript)

    # The file grows AFTER the inventory stat, BEFORE the parse.
    _append_lines(transcript, [_assistant_row("msg_c", inp=9999)])

    result = usage_stats._parse_file(transcript, frozen, b"\x00" * 32)
    assert result.read_ok, "a grown file is the same file with more history"
    assert len(result.records) == 2  # exactly the snapshot's two lines
    assert result.parsed_size == frozen.st_size
