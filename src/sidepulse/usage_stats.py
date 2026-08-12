"""Today's Claude usage, computed the way T3 Code does it.

A faithful port of t3code's usage pipeline (apps/server/src/usage/*
@ 5a84614), because they measurably do this best:

- **Substring gate before json.loads** (`mightCarryUsage`): a cheap
  ``'"usage"' in line`` check skips roughly half the lines and is worth
  about an order of magnitude on the parse path.
- **Global first-seen dedupe** (`dedupeKey`): Claude Code repeats the
  full ``usage`` object once per content block and again in resumed /
  forked session files -- summing naively overcounts ~2.4x. Every
  record carries the assistant message id; the aggregator keeps the
  first sighting across ALL files.
- **Per-file (size, mtime) parse cache, persisted** (`usageScanCache`):
  transcripts are append-only, so a file whose size and mtime match the
  cache is not even opened. Their measurement: a cold 30-day scan ~3.5s
  vs ~11ms warm. Serialization is positional arrays with interned
  session/model string tables -- the difference between a cache
  measured in tens of megabytes and one under a few.
- **Corrupt cache decodes to empty**: one cold scan, never a broken
  surface.
- **cacheSavingsUsd**: what prompt caching saved (cached reads billed
  at a tenth of the uncached rate) -- the stat people actually love.

Costs use current Anthropic per-MTok list rates; unknown models fall
back to Sonnet pricing rather than pricing at zero (undercounting reads
as "free", which is a lie).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

CACHE_VERSION = 1
USAGE_MARKER = '"usage"'

# (input $/MTok, output $/MTok) by model-id substring, first match wins.
# Cache reads bill at 0.1x input; cache writes at 1.25x input.
MODEL_PRICING: tuple[tuple[str, float, float], ...] = (
    ("opus", 15.0, 75.0),
    ("sonnet", 3.0, 15.0),
    ("haiku", 1.0, 5.0),
)
DEFAULT_PRICING = (3.0, 15.0)
CACHE_READ_RATE = 0.1
CACHE_WRITE_RATE = 1.25


@dataclass
class UsageTotals:
    sessions: set[str] = field(default_factory=set)
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cache_savings_usd: float = 0.0


def _pricing_for_model(model: str) -> tuple[float, float]:
    lowered = model.lower()
    for marker, input_rate, output_rate in MODEL_PRICING:
        if marker in lowered:
            return input_rate, output_rate
    return DEFAULT_PRICING


def _record_from_line(line: str, session_id: str) -> tuple | None:
    """[session, model, epoch, input, cached_in, cache_create, output,
    dedupe_key] or None. The gate has already run."""
    try:
        row = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(row, dict) or row.get("type") != "assistant":
        return None
    message = row.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    timestamp = row.get("timestamp")
    if not isinstance(timestamp, str):
        return None
    try:
        epoch = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None

    def _int(key: str) -> int:
        value = usage.get(key)
        return int(value) if isinstance(value, (int, float)) else 0

    dedupe = str(message.get("id") or "")
    if not dedupe:
        # No message id means no safe dedupe; skip rather than overcount.
        return None
    return (
        session_id,
        str(message.get("model") or ""),
        epoch,
        _int("input_tokens"),
        _int("cache_read_input_tokens"),
        _int("cache_creation_input_tokens"),
        _int("output_tokens"),
        dedupe,
    )


def _parse_file(path: Path) -> list[tuple]:
    session_id = path.stem
    records: list[tuple] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                # T3's mightCarryUsage: skip before parsing.
                if USAGE_MARKER not in line:
                    continue
                record = _record_from_line(line, session_id)
                if record is not None:
                    records.append(record)
    except OSError:
        return []
    return records


def _load_cache(cache_path: Path) -> dict:
    try:
        data = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        # A corrupt or old cache costs one cold scan, never a broken menu.
        return {}
    files = data.get("files")
    sessions = data.get("sessions")
    models = data.get("models")
    dedupes = data.get("dedupes")
    if not all(isinstance(part, (dict, list)) for part in (files, sessions, models, dedupes)):
        return {}
    return data


def _decode_records(entry: dict, sessions: list, models: list, dedupes: list) -> list[tuple]:
    records = []
    for row in entry.get("records", []):
        try:
            records.append(
                (
                    str(sessions[row[0]]),
                    str(models[row[1]]),
                    float(row[2]),
                    int(row[3]),
                    int(row[4]),
                    int(row[5]),
                    int(row[6]),
                    str(dedupes[row[7]]),
                )
            )
        except (IndexError, TypeError, ValueError):
            return []
    return records


def _intern(value: str, table: list[str], index: dict[str, int]) -> int:
    position = index.get(value)
    if position is None:
        position = len(table)
        table.append(value)
        index[value] = position
    return position


def scan_usage(
    root: Path,
    cache_path: Path | None = None,
    *,
    since_epoch: float = 0.0,
) -> UsageTotals:
    """Aggregate usage for records at/after ``since_epoch``, warm from
    the persisted cache, re-parsing only files whose (size, mtime)
    changed. Writes the refreshed cache back atomically."""
    cache = _load_cache(cache_path) if cache_path is not None else {}
    cached_files = cache.get("files", {}) if cache else {}
    cached_sessions = cache.get("sessions", []) if cache else []
    cached_models = cache.get("models", []) if cache else []
    cached_dedupes = cache.get("dedupes", []) if cache else []

    sessions_table: list[str] = []
    sessions_index: dict[str, int] = {}
    models_table: list[str] = []
    models_index: dict[str, int] = {}
    dedupes_table: list[str] = []
    dedupes_index: dict[str, int] = {}
    new_files: dict[str, dict] = {}

    all_records: list[tuple] = []
    try:
        paths = [p for p in root.rglob("*.jsonl") if p.is_file()]
    except OSError:
        paths = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        key = str(path)
        entry = cached_files.get(key)
        if (
            isinstance(entry, dict)
            and entry.get("size") == stat.st_size
            and entry.get("mtime") == stat.st_mtime
        ):
            records = _decode_records(entry, cached_sessions, cached_models, cached_dedupes)
        else:
            records = _parse_file(path)
        all_records.extend(records)
        new_files[key] = {
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "records": [
                [
                    _intern(r[0], sessions_table, sessions_index),
                    _intern(r[1], models_table, models_index),
                    r[2],
                    r[3],
                    r[4],
                    r[5],
                    r[6],
                    _intern(r[7], dedupes_table, dedupes_index),
                ]
                for r in records
            ],
        }

    if cache_path is not None:
        payload = {
            "version": CACHE_VERSION,
            "files": new_files,
            "sessions": sessions_table,
            "models": models_table,
            "dedupes": dedupes_table,
        }
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            scratch = cache_path.with_name(
                f"{cache_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            scratch.write_text(json.dumps(payload, separators=(",", ":")))
            os.replace(scratch, cache_path)
        except OSError:
            pass

    totals = UsageTotals()
    seen: set[str] = set()
    for session, model, epoch, inp, cached_in, cache_create, out, dedupe in all_records:
        # Global first-seen dedupe across ALL files -- resumed/forked
        # sessions copy records forward and content blocks repeat usage.
        if dedupe in seen:
            continue
        seen.add(dedupe)
        if epoch < since_epoch:
            continue
        totals.sessions.add(session)
        totals.input_tokens += inp
        totals.cached_input_tokens += cached_in
        totals.cache_creation_tokens += cache_create
        totals.output_tokens += out
        input_rate, output_rate = _pricing_for_model(model)
        totals.cost_usd += (
            inp * input_rate
            + cached_in * input_rate * CACHE_READ_RATE
            + cache_create * input_rate * CACHE_WRITE_RATE
            + out * output_rate
        ) / 1_000_000.0
        totals.cache_savings_usd += (
            cached_in * input_rate * (1.0 - CACHE_READ_RATE)
        ) / 1_000_000.0
    return totals


def usage_summary_line(totals: UsageTotals) -> str | None:
    """"Claude today: 14 sessions · $6.20 · saved $18.40" -- None when
    there is nothing to say (an empty line would be clutter)."""
    count = len(totals.sessions)
    if count == 0:
        return None
    plural = "session" if count == 1 else "sessions"
    parts = [f"{count} {plural}"]
    if totals.cost_usd >= 0.005:
        parts.append(f"${totals.cost_usd:.2f}")
    if totals.cache_savings_usd >= 0.005:
        parts.append(f"saved ${totals.cache_savings_usd:.2f} with caching")
    return "Claude today: " + " · ".join(parts)
