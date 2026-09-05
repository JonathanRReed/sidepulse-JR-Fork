from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sidepulse import usage_stats

DEDUPE_SECRET = b"\x07" * 32


def _claude_row(message_id: str, tokens: int, *, padding: str = "") -> dict:
    return {
        "type": "assistant",
        "timestamp": "2026-08-11T12:00:00Z",
        "padding": padding,
        "message": {
            "id": message_id,
            "model": "claude-sonnet-5",
            "usage": {
                "input_tokens": tokens,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    }


def _codex_row(tokens: int, *, padding: str = "", rate_limits: dict | None = None) -> dict:
    payload = {
        "type": "token_count",
        "padding": padding,
        "info": {
            "total_token_usage": {
                "input_tokens": tokens,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 0,
            },
            "last_token_usage": {
                "input_tokens": tokens,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    }
    if rate_limits is not None:
        payload["rate_limits"] = rate_limits
    return {
        "timestamp": "2026-08-11T12:00:00Z",
        "type": "event_msg",
        "payload": payload,
    }


def _parse(path: Path, provider: str, *, resume_offset: int = 0):
    frozen = os.stat(path)
    if resume_offset:
        return usage_stats._parse_file_tail(
            path,
            frozen,
            resume_offset,
            provider,
            DEDUPE_SECRET,
        )
    if provider == "codex":
        return usage_stats._parse_codex_file(path, frozen, DEDUPE_SECRET)
    return usage_stats._parse_file(path, frozen, DEDUPE_SECRET)


@pytest.mark.parametrize("provider", ("claude", "codex"))
@pytest.mark.parametrize("incremental", (False, True))
def test_oversized_record_is_partial_and_following_record_survives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    incremental: bool,
) -> None:
    monkeypatch.setattr(usage_stats, "USAGE_RECORD_MAX_BYTES", 512)
    path = tmp_path / f"{provider}.jsonl"
    prefix = "{}\n" if incremental else ""
    resume_offset = len(prefix.encode("utf-8"))
    if provider == "claude":
        rows = (
            _claude_row("oversized", 999, padding="x" * 512),
            _claude_row("valid", 7),
        )
    else:
        rows = (
            _codex_row(999, padding="x" * 512),
            _codex_row(
                11,
                rate_limits={
                    "primary": {
                        "used_percent": 25,
                        "window_minutes": 300,
                    }
                },
            ),
        )
    path.write_text(
        prefix + "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    result = _parse(path, provider, resume_offset=resume_offset)

    assert result.read_ok is True
    assert result.malformed_lines == 1
    assert len(result.records) == 1
    assert result.records[0][4] == (7 if provider == "claude" else 11)
    if provider == "codex":
        assert result.rate_limit_windows == (
            {
                "label": "primary",
                "used_percent": 25.0,
                "window_minutes": 300,
                "resets_at": None,
            },
        )


@pytest.mark.parametrize("provider", ("claude", "codex"))
def test_recursive_json_is_partial_and_following_record_survives(
    tmp_path: Path,
    provider: str,
) -> None:
    path = tmp_path / f"{provider}.jsonl"
    marker = '"usage"' if provider == "claude" else '"token_count"'
    recursive = '{"marker":' + marker + ',"nested":' + "[" * 10_000 + "0" + "]" * 10_000 + "}"
    valid = _claude_row("valid", 13) if provider == "claude" else _codex_row(17)
    path.write_text(recursive + "\n" + json.dumps(valid) + "\n", encoding="utf-8")

    result = _parse(path, provider)

    assert result.read_ok is True
    assert result.malformed_lines == 1
    assert len(result.records) == 1
    assert result.records[0][4] == (13 if provider == "claude" else 17)
