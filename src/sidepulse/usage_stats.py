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

CACHE_VERSION = 2
USAGE_MARKER = '"usage"'
CODEX_MARKER = '"token_count"'


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
    codex_sessions: set[str] = field(default_factory=set)
    codex_tokens: int = 0
    records: list = field(default_factory=list)
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
        "claude",
        session_id,
        str(message.get("model") or ""),
        epoch,
        _int("input_tokens"),
        _int("cache_read_input_tokens"),
        _int("cache_creation_input_tokens"),
        _int("output_tokens"),
        dedupe,
    )


def _parse_codex_file(path: Path) -> list[tuple]:
    """One record per rollout: total_token_usage is CUMULATIVE per
    session, so the LAST token_count event is the exact session total --
    no dedupe gymnastics needed (CodexBar's reading of the format)."""
    last_info = None
    last_epoch = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if CODEX_MARKER not in line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                payload = row.get("payload") if isinstance(row, dict) else None
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue
                info = payload.get("info")
                if not isinstance(info, dict):
                    continue
                totals = info.get("total_token_usage")
                if not isinstance(totals, dict):
                    continue
                timestamp = row.get("timestamp")
                try:
                    last_epoch = datetime.fromisoformat(
                        str(timestamp).replace("Z", "+00:00")
                    ).timestamp()
                except (TypeError, ValueError):
                    continue
                last_info = totals
    except OSError:
        return []
    if last_info is None or last_epoch is None:
        return []

    def _int(key: str) -> int:
        value = last_info.get(key)
        return int(value) if isinstance(value, (int, float)) else 0

    return [
        (
            "codex",
            path.stem,
            "codex",
            last_epoch,
            _int("input_tokens"),
            _int("cached_input_tokens"),
            _int("cache_write_input_tokens"),
            _int("output_tokens"),
            str(path),
        )
    ]


def codex_rate_limits(root: Path) -> dict | None:
    """The newest rollout's embedded rate_limits: Codex ships its own
    5h/weekly used-percent right in the session file (CodexBar finding)
    -- read ONE file, no API call, no auth."""
    try:
        newest = max(
            (p for p in root.rglob("*.jsonl") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            default=None,
        )
    except OSError:
        newest = None
    if newest is None:
        return None
    latest = None
    try:
        with newest.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"rate_limits"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                payload = row.get("payload") if isinstance(row, dict) else None
                limits = payload.get("rate_limits") if isinstance(payload, dict) else None
                if isinstance(limits, dict):
                    latest = limits
    except OSError:
        return None
    return latest


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
                    str(models[row[0]]),
                    str(sessions[row[1]]),
                    str(models[row[2]]),
                    float(row[3]),
                    int(row[4]),
                    int(row[5]),
                    int(row[6]),
                    int(row[7]),
                    str(dedupes[row[8]]),
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
    codex_root: Path | None = None,
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
        paths = [(p, _parse_file) for p in root.rglob("*.jsonl") if p.is_file()]
    except OSError:
        paths = []
    if codex_root is not None:
        try:
            paths.extend(
                (p, _parse_codex_file)
                for p in codex_root.rglob("*.jsonl")
                if p.is_file()
            )
        except OSError:
            pass
    for path, parser in paths:
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
            records = parser(path)
        all_records.extend(records)
        new_files[key] = {
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "records": [
                [
                    _intern(r[0], models_table, models_index),
                    _intern(r[1], sessions_table, sessions_index),
                    _intern(r[2], models_table, models_index),
                    r[3],
                    r[4],
                    r[5],
                    r[6],
                    r[7],
                    _intern(r[8], dedupes_table, dedupes_index),
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
    totals.records = [r for r in all_records]
    seen: set[str] = set()
    for provider, session, model, epoch, inp, cached_in, cache_create, out, dedupe in all_records:
        # Global first-seen dedupe across ALL files -- resumed/forked
        # sessions copy records forward and content blocks repeat usage.
        if dedupe in seen:
            continue
        seen.add(dedupe)
        if epoch < since_epoch:
            continue
        totals.sessions.add(session)
        if provider == "codex":
            totals.codex_sessions.add(session)
            totals.codex_tokens += inp + cached_in + out
            continue
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


def daily_buckets(records, days: int = 7, *, now: datetime | None = None):
    """Per local-calendar-day totals for the last N days:
    {day_iso: {"claude_cost": float, "codex_tokens": int, "sessions": int}}.
    Day keys come from each record's own timestamp converted to LOCAL
    time (the CodexBar rule: a 23:30 UTC session lands in YOUR day)."""
    current = now or datetime.now()
    day_keys = [
        (current - __import__("datetime").timedelta(days=offset)).date().isoformat()
        for offset in range(days - 1, -1, -1)
    ]
    buckets = {key: {"claude_cost": 0.0, "codex_tokens": 0, "sessions": set()} for key in day_keys}
    seen: set[str] = set()
    for provider, session, model, epoch, inp, cached_in, cache_create, out, dedupe in records:
        if dedupe in seen:
            continue
        seen.add(dedupe)
        day = datetime.fromtimestamp(epoch).date().isoformat()
        bucket = buckets.get(day)
        if bucket is None:
            continue
        bucket["sessions"].add(session)
        if provider == "codex":
            bucket["codex_tokens"] += inp + cached_in + out
        else:
            input_rate, output_rate = _pricing_for_model(model)
            bucket["claude_cost"] += (
                inp * input_rate
                + cached_in * input_rate * CACHE_READ_RATE
                + cache_create * input_rate * CACHE_WRITE_RATE
                + out * output_rate
            ) / 1_000_000.0
    for bucket in buckets.values():
        bucket["sessions"] = len(bucket["sessions"])
    return buckets


def hourly_session_counts(records, *, now: datetime | None = None) -> list[int]:
    """Today's distinct sessions per hour (local), for the sparkline."""
    current = now or datetime.now()
    today = current.date()
    hours: list[set] = [set() for _ in range(24)]
    for provider, session, _model, epoch, *_rest in records:
        stamp = datetime.fromtimestamp(epoch)
        if stamp.date() != today:
            continue
        hours[stamp.hour].add(session)
    return [len(bucket) for bucket in hours]
