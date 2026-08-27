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

Costs are local estimates under an explicitly versioned table. Unknown
models remain activity but are excluded from the estimate and reduce pricing
coverage instead of silently inheriting another model's price.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from .capacity_sources import (
    EvidenceMetricKind,
    SupportedCapacityEvidence,
    SupportedLaneEvidence,
)
from .capacity_types import (
    ObservationState,
    QuotaEffect,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from .private_io import atomic_private_write, ensure_private_directory, read_private_text
from .providers import NegotiatedProviderSource, negotiated_provider_sources
from .reset_policy import parse_reset_epoch

CACHE_VERSION = 7
# The byte budget below is the real bound; this one only stops the candidate
# list itself from growing without limit. At 4096 it bound FIRST on the owner's
# corpus -- 5,605 transcripts across ~/.claude and ~/.codex -- so ~1,500 files
# were re-read on every scan cycle no matter how much budget was left. A file
# with nothing inside the window costs ~320 bytes to remember, so 8192 of them
# is 2.6MB of an 8MB budget.
USAGE_CACHE_MAX_FILES = 8192
USAGE_INVENTORY_MAX_FILES = 4096
USAGE_FILE_MAX_BYTES = 64 * 1024 * 1024

# The scan cache is an accelerator, never the source of truth: the transcripts
# on disk are. So it is allowed to forget. Three bounds keep it from becoming
# the largest thing this process owns (measured at 18.2 MB / 211k records
# before these landed, which is most of the app's resident memory):
#
#   1. Retention -- records older than the widest window the UI can currently
#      ask for are dropped on write. Widening the graph range costs one cold
#      rescan, once, instead of every process paying for a year of history.
#   2. A byte budget on write, so a pathological corpus cannot outgrow the cap
#      between retention passes.
#   3. A byte ceiling on read, so an oversized cache written by any other
#      version degrades to a cold scan instead of being parsed into memory.
USAGE_CACHE_MAX_BYTES = 8 * 1024 * 1024
USAGE_CACHE_RETENTION_HEADROOM_SECONDS = 3 * 24 * 60 * 60
# Conservative per-record cost: a serialized record plus its amortized share of
# the interning tables. Measured at ~80 bytes/record; budgeted at 110 so the
# estimate overshoots and the cap binds early rather than late.
_CACHE_BYTES_PER_RECORD = 110
_CACHE_BYTES_PER_ENTRY = 320

# The dedupe table was the single largest line item in the cache: one 64-char
# HMAC per usage event, ~99k of them. 128 bits is still absurd headroom for a
# per-machine table this size (birthday collision odds ~1e-29), and it is an
# HMAC under a per-cache secret, so truncation costs no forgery resistance.
DEDUPE_DIGEST_HEX_CHARS = 32
USAGE_MARKER = '"usage"'
CODEX_MARKER = '"token_count"'
PRICING_TABLE_VERSION = "sidepulse-rates-v2"
PRICING_TABLE_AS_OF = "2026-08-26"


# (input $/MTok, output $/MTok) by model-id substring, first match wins.
# Cache reads bill at 0.1x input; cache writes at 1.25x input.
# Rates checked 2026-08-26 against Anthropic's published pricing
# (Fable 5: $10/$50, cache read $1/MTok). Before this row, 55% of this
# machine's tokens were fable-model records dropped from every dollar
# figure with no disclosure -- the "$23k estimated" was a silent floor.
MODEL_PRICING: tuple[tuple[str, float, float], ...] = (
    ("fable", 10.0, 50.0),
    ("opus", 15.0, 75.0),
    ("sonnet", 3.0, 15.0),
    ("haiku", 1.0, 5.0),
)
# Codex models, same substring-match convention. Checked 2026-08-26
# against OpenAI's published pricing (GPT-5.6 Sol after the Aug-22 cut;
# Luna/Terra; GPT-5.4 family). Order matters: longest prefixes first.
GPT_MODEL_PRICING: tuple[tuple[str, float, float], ...] = (
    ("gpt-5.6-luna", 0.20, 1.20),
    ("gpt-5.6-terra", 2.0, 12.0),
    ("gpt-5.6-sol", 4.0, 20.0),
    ("gpt-5.6", 4.0, 20.0),
    ("gpt-5.4-mini", 0.75, 4.50),
    ("gpt-5.4-nano", 0.20, 1.25),
    ("gpt-5.4", 2.50, 15.0),
    ("gpt-5", 2.50, 15.0),
)
CACHE_READ_RATE = 0.1
CACHE_WRITE_RATE = 1.25


class PricingCoverage(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class PricingCoverageMetrics:
    priced_records: int
    total_records: int
    priced_token_count: int
    total_token_count: int
    table_version: str = PRICING_TABLE_VERSION
    table_as_of: str = PRICING_TABLE_AS_OF

    @property
    def fraction(self) -> float | None:
        if self.total_token_count == 0:
            return None
        return self.priced_token_count / self.total_token_count


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
    estimated_cost_usd: float = 0.0
    estimated_cache_savings_usd: float = 0.0
    source_coverage: dict[str, UsageSourceCoverage] = field(default_factory=dict)
    pricing_coverage: PricingCoverageMetrics = field(
        default_factory=lambda: PricingCoverageMetrics(0, 0, 0, 0)
    )
    codex_rate_limit_evidence: tuple[dict, ...] = ()
    #: When that evidence was written (epoch seconds). Without it a
    #: reading frozen days ago renders identically to a live one.
    codex_rate_limit_observed_at: float | None = None

    @property
    def local_activity_coverage(self) -> dict[str, UsageSourceCoverage]:
        return self.source_coverage

    @property
    def cost_usd(self) -> float:
        """Compatibility alias for the explicitly estimated monetary value."""
        return self.estimated_cost_usd

    @cost_usd.setter
    def cost_usd(self, value: float) -> None:
        self.estimated_cost_usd = value

    @property
    def cache_savings_usd(self) -> float:
        """Compatibility alias for the explicitly estimated savings value."""
        return self.estimated_cache_savings_usd

    @cache_savings_usd.setter
    def cache_savings_usd(self, value: float) -> None:
        self.estimated_cache_savings_usd = value


class UsageSourceStatus(str, Enum):
    OK = "ok"
    MISSING = "missing"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class UsageSourceCoverage:
    provider_id: str
    status: UsageSourceStatus
    root_present: bool
    root_walked: bool
    files_discovered: int
    files_read: int
    cache_hits: int
    malformed_lines: int
    unreadable_files: int
    skipped_symlinks: int
    duplicate_physical_files: int
    oversized_files: int = 0
    truncated_files: int = 0

    def __post_init__(self) -> None:
        counts = (
            self.files_discovered,
            self.files_read,
            self.cache_hits,
            self.malformed_lines,
            self.unreadable_files,
            self.skipped_symlinks,
            self.duplicate_physical_files,
            self.oversized_files,
            self.truncated_files,
        )
        if any(isinstance(value, bool) or value < 0 for value in counts):
            raise ValueError("usage source coverage counts must be nonnegative")


@dataclass(frozen=True, slots=True)
class ProviderUsageResult:
    source_key: SourceKey
    coverage: UsageSourceCoverage
    observed_session_count: int
    input_tokens: int
    cached_input_tokens: int
    cache_creation_tokens: int
    output_tokens: int
    pricing_coverage: PricingCoverage
    priced_record_count: int
    unpriced_record_count: int
    covered_cost_estimate_usd: float | None
    covered_cache_savings_estimate_usd: float | None
    pricing_as_of: str | None

    def __post_init__(self) -> None:
        counts = (
            self.observed_session_count,
            self.input_tokens,
            self.cached_input_tokens,
            self.cache_creation_tokens,
            self.output_tokens,
            self.priced_record_count,
            self.unpriced_record_count,
        )
        estimates = (
            self.covered_cost_estimate_usd,
            self.covered_cache_savings_estimate_usd,
        )
        if not (
            type(self.source_key) is SourceKey
            and type(self.coverage) is UsageSourceCoverage
            and self.coverage.provider_id == self.source_key.provider_id
            and all(type(value) is int and value >= 0 for value in counts)
            and type(self.pricing_coverage) is PricingCoverage
            and all(
                value is None
                or (
                    type(value) in {int, float}
                    and math.isfinite(value)
                    and value >= 0.0
                )
                for value in estimates
            )
            and (
                self.pricing_as_of is None
                or (
                    type(self.pricing_as_of) is str
                    and self.pricing_as_of == PRICING_TABLE_AS_OF
                )
            )
        ):
            raise ValueError("invalid provider usage result")
        if self.pricing_coverage is PricingCoverage.UNAVAILABLE:
            valid_pricing = (
                self.priced_record_count == 0
                and self.covered_cost_estimate_usd is None
                and self.covered_cache_savings_estimate_usd is None
                and self.pricing_as_of is None
            )
        elif self.pricing_coverage is PricingCoverage.PARTIAL:
            valid_pricing = (
                self.priced_record_count > 0
                and self.unpriced_record_count > 0
                and self.covered_cost_estimate_usd is not None
                and self.covered_cache_savings_estimate_usd is not None
                and self.pricing_as_of == PRICING_TABLE_AS_OF
            )
        else:
            valid_pricing = (
                self.priced_record_count > 0
                and self.unpriced_record_count == 0
                and self.covered_cost_estimate_usd is not None
                and self.covered_cache_savings_estimate_usd is not None
                and self.pricing_as_of == PRICING_TABLE_AS_OF
            )
        if not valid_pricing:
            raise ValueError("invalid provider pricing coverage")
        for name in (
            "covered_cost_estimate_usd",
            "covered_cache_savings_estimate_usd",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, float(value))


@dataclass(frozen=True, slots=True)
class _DiscoveredUsageFile:
    path: Path
    info: os.stat_result


@dataclass(frozen=True, slots=True)
class _ParseResult:
    records: list[tuple]
    malformed_lines: int
    read_ok: bool
    rate_limit_windows: tuple[dict, ...] = ()
    #: True when the parse ended exactly on a newline boundary -- the
    #: incremental-tail precondition. Prefix-bounded reads always trim
    #: to the last newline, so this is False only for degenerate files.
    eof_newline: bool = True
    #: Absolute byte offset this parse covered (the resume point for the
    #: next incremental tail). -1 means "the whole stat'd file" for
    #: paths that never learned to report it.
    parsed_size: int = -1


@dataclass(frozen=True, slots=True)
class _InventorySource:
    provider_id: str
    root: Path
    root_key: str | None
    walk_complete: bool
    coverage: UsageSourceCoverage
    candidates: tuple[_DiscoveredUsageFile, ...]


@dataclass(frozen=True, slots=True)
class LocalUsageInventory:
    """One frozen, bounded no-follow admission set for local evidence."""

    sources: tuple[_InventorySource, ...] = field(repr=False)

    @property
    def file_count(self) -> int:
        return sum(len(source.candidates) for source in self.sources)


@dataclass(slots=True)
class _CoverageState:
    provider_id: str
    root_present: bool = False
    root_walked: bool = False
    walk_failed: bool = False
    files_discovered: int = 0
    files_read: int = 0
    cache_hits: int = 0
    malformed_lines: int = 0
    unreadable_files: int = 0
    skipped_symlinks: int = 0
    duplicate_physical_files: int = 0
    oversized_files: int = 0
    truncated_files: int = 0

    def finalize(self) -> UsageSourceCoverage:
        if not self.root_present:
            status = UsageSourceStatus.MISSING
        elif not self.root_walked:
            status = UsageSourceStatus.FAILED
        else:
            usable_files = self.files_read + self.cache_hits
            physical_candidates = max(
                0,
                self.files_discovered - self.duplicate_physical_files,
            )
            if (
                physical_candidates > 0
                and usable_files == 0
                and self.unreadable_files > 0
            ):
                status = UsageSourceStatus.FAILED
            elif (
                self.walk_failed
                or self.malformed_lines > 0
                or self.unreadable_files > 0
                or self.oversized_files > 0
                or self.truncated_files > 0
            ):
                status = UsageSourceStatus.PARTIAL
            else:
                status = UsageSourceStatus.OK
        return UsageSourceCoverage(
            provider_id=self.provider_id,
            status=status,
            root_present=self.root_present,
            root_walked=self.root_walked,
            files_discovered=self.files_discovered,
            files_read=self.files_read,
            cache_hits=self.cache_hits,
            malformed_lines=self.malformed_lines,
            unreadable_files=self.unreadable_files,
            skipped_symlinks=self.skipped_symlinks,
            duplicate_physical_files=self.duplicate_physical_files,
            oversized_files=self.oversized_files,
            truncated_files=self.truncated_files,
        )

    @classmethod
    def from_coverage(cls, coverage: UsageSourceCoverage) -> _CoverageState:
        return cls(
            provider_id=coverage.provider_id,
            root_present=coverage.root_present,
            root_walked=coverage.root_walked,
            walk_failed=coverage.status in {UsageSourceStatus.PARTIAL, UsageSourceStatus.FAILED}
            and not coverage.unreadable_files
            and not coverage.malformed_lines
            and not coverage.oversized_files
            and not coverage.truncated_files,
            files_discovered=coverage.files_discovered,
            skipped_symlinks=coverage.skipped_symlinks,
            duplicate_physical_files=coverage.duplicate_physical_files,
            oversized_files=coverage.oversized_files,
            truncated_files=coverage.truncated_files,
        )


def _pricing_key_for_model(model: str) -> str:
    lowered = model.lower()
    for marker, _input_rate, _output_rate in MODEL_PRICING:
        if marker in lowered:
            return marker
    return "unknown"


def _pricing_for_model(model: str) -> tuple[float, float] | None:
    pricing_key = _pricing_key_for_model(model)
    for marker, input_rate, output_rate in MODEL_PRICING:
        if marker == pricing_key:
            return input_rate, output_rate
    return None


def _gpt_pricing_for_model(model: str) -> tuple[float, float] | None:
    lowered = str(model or "").lower()
    for marker, input_rate, output_rate in GPT_MODEL_PRICING:
        if marker in lowered:
            return input_rate, output_rate
    return None


def _token_counts(mapping: dict, keys: tuple[str, ...]) -> tuple[int, ...] | None:
    values: list[int] = []
    for key in keys:
        if key not in mapping:
            values.append(0)
            continue
        value = mapping.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
        ):
            return None
        values.append(int(value))
    return tuple(values)


def _record_from_line(line: str, session_id: str, dedupe_secret: bytes) -> tuple | None:
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

    raw_dedupe = str(message.get("id") or "")
    if not raw_dedupe:
        # No message id means no safe dedupe; skip rather than overcount.
        return None
    dedupe = "message:" + hmac.new(
        dedupe_secret,
        raw_dedupe.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:DEDUPE_DIGEST_HEX_CHARS]
    counts = _token_counts(
        usage,
        (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "output_tokens",
        ),
    )
    if counts is None:
        return None
    return (
        "claude",
        session_id,
        _pricing_key_for_model(str(message.get("model") or "")),
        epoch,
        counts[0],
        counts[1],
        counts[2],
        counts[3],
        dedupe,
    )


def _read_verified_prefix(
    path: Path,
    expected_stat: os.stat_result,
    resume_offset: int = 0,
) -> tuple[str, int] | None:
    """The snapshot's bytes of a possibly still-growing file, or None.

    The scan works from a FROZEN inventory: every candidate is stat'd
    first, then parsed. A live agent transcript appends every few
    seconds, so by the time the loop reached the busiest file its size
    no longer matched the stat and the strict open refused it -- the
    hottest file was skipped as "unreadable" on EVERY scan, and codex's
    rate windows froze at whatever an older file last said (observed
    live 2026-08-20: weekly pinned at 99% while the active rollout said
    95%). Growth is not corruption: same device and inode, size and
    mtime at or past the snapshot, is the same file with more history.
    This reads exactly the snapshot's bytes, trimmed back to the last
    newline so no line is ever split mid-write, and reports the
    absolute offset covered.
    """
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != expected_stat.st_dev
            or current.st_ino != expected_stat.st_ino
            or current.st_size < expected_stat.st_size
            or current.st_mtime_ns < expected_stat.st_mtime_ns
            or resume_offset < 0
            or resume_offset > expected_stat.st_size
        ):
            return None
        target = expected_stat.st_size - resume_offset
        chunks: list[bytes] = []
        os.lseek(descriptor, resume_offset, os.SEEK_SET)
        while target > 0:
            chunk = os.read(descriptor, min(target, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            target -= len(chunk)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    payload = b"".join(chunks)
    cut = payload.rfind(b"\n")
    if cut < 0:
        return "", resume_offset
    payload = payload[: cut + 1]
    return (
        payload.decode("utf-8", errors="replace"),
        resume_offset + len(payload),
    )


def _scan_codex_lines(handle):
    """The shared per-line codex scan for full and tail parses."""
    last_counts = None
    last_epoch = None
    rate_limit_windows: tuple[dict, ...] = ()
    malformed_lines = 0
    eof_newline = True
    for line in handle:
        eof_newline = line.endswith("\n")
        if CODEX_MARKER not in line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            malformed_lines += 1
            continue
        payload = row.get("payload") if isinstance(row, dict) else None
        limits = payload.get("rate_limits") if isinstance(payload, dict) else None
        if isinstance(limits, dict):
            rate_limit_windows = tuple(codex_windows_from_limits(limits))
        if (
            not isinstance(payload, dict)
            or payload.get("type") != "token_count"
        ):
            malformed_lines += 1
            continue
        info = payload.get("info")
        if not isinstance(info, dict):
            malformed_lines += 1
            continue
        totals = info.get("total_token_usage")
        if not isinstance(totals, dict):
            malformed_lines += 1
            continue
        counts = _token_counts(
            totals,
            (
                "input_tokens",
                "cached_input_tokens",
                "cache_write_input_tokens",
                "output_tokens",
            ),
        )
        if counts is None:
            malformed_lines += 1
            continue
        timestamp = row.get("timestamp")
        try:
            last_epoch = datetime.fromisoformat(
                str(timestamp).replace("Z", "+00:00")
            ).timestamp()
        except (TypeError, ValueError):
            malformed_lines += 1
            continue
        last_counts = counts
    return last_counts, last_epoch, rate_limit_windows, malformed_lines, eof_newline


def _parse_codex_file(path: Path, expected_stat: os.stat_result) -> _ParseResult:
    """One record per rollout: total_token_usage is CUMULATIVE per
    session, so the LAST token_count event is the exact session total --
    no dedupe gymnastics needed (CodexBar's reading of the format)."""
    snapshot = _read_verified_prefix(path, expected_stat)
    if snapshot is None:
        return _ParseResult([], 0, False)
    text, parsed_size = snapshot
    (
        last_counts,
        last_epoch,
        rate_limit_windows,
        malformed_lines,
        _eof,
    ) = _scan_codex_lines(text.splitlines(keepends=True))
    if last_counts is None or last_epoch is None:
        return _ParseResult(
            [], malformed_lines, True, rate_limit_windows, True, parsed_size
        )

    physical_id = f"codex:{expected_stat.st_dev}:{expected_stat.st_ino}"

    return _ParseResult(
        [
            (
                "codex",
                physical_id,
                "codex",
                last_epoch,
                last_counts[0],
                last_counts[1],
                last_counts[2],
                last_counts[3],
                physical_id,
            )
        ],
        malformed_lines,
        True,
        rate_limit_windows,
        True,
        parsed_size,
    )


_LATEST_CODEX_RATE_LIMITS: dict[str, tuple[dict, ...]] = {}


def _root_key(path: Path) -> str | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return None
    return f"root:{info.st_dev}:{info.st_ino}"


def codex_rate_limits(source: Path | UsageTotals) -> dict | None:
    """Return Codex limits admitted by the preceding frozen inventory.

    The ``Path`` form is retained for the current controller but performs only
    a root identity check. It never walks or opens a transcript.
    """
    if isinstance(source, UsageTotals):
        windows = source.codex_rate_limit_evidence
    else:
        key = _root_key(source)
        windows = _LATEST_CODEX_RATE_LIMITS.get(key or "", ())
    if not windows:
        return None
    return {"_inventory_windows": [dict(window) for window in windows]}


def cached_codex_rate_limits(cache_path: Path) -> dict | None:
    """Read only the freshest bounded Codex limit evidence from its cache.

    This keeps the short capacity refresh independent from the much deeper
    historical graph scan. The cache loader enforces the exact source binding,
    private-file contract, and structural bounds before any window is admitted.
    """
    if not isinstance(cache_path, Path):
        return None
    source = next(
        (
            row
            for row in negotiated_provider_sources()
            if row.source_key.provider_id == "codex"
            and row.source_key.capability_id == "transcript_usage"
            and row.observation_invocation_allowed
        ),
        None,
    )
    if source is None:
        return None
    provider_cache = _secondary_provider_cache_path(cache_path, source.source_key)
    cache = _load_cache(provider_cache, source.source_key)
    files = cache.get("files")
    if not isinstance(files, dict):
        return None
    candidates: list[tuple[float, str, tuple[dict, ...]]] = []
    for key, entry in tuple(files.items())[:USAGE_CACHE_MAX_FILES]:
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        mtime = entry.get("mtime")
        windows = entry.get("rate_limit_windows")
        if (
            isinstance(mtime, bool)
            or not isinstance(mtime, (int, float))
            or not math.isfinite(float(mtime))
            or not isinstance(windows, list)
        ):
            continue
        admitted = tuple(
            dict(window) for window in windows[:32] if isinstance(window, dict)
        )
        if admitted:
            candidates.append((float(mtime), key, admitted))
    if not candidates:
        return None
    windows = max(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]
    return {"_inventory_windows": [dict(window) for window in windows]}


def codex_windows_from_limits(payload: object) -> list[dict]:
    """Normalize embedded Codex limits without inventing window semantics."""
    if not isinstance(payload, dict):
        return []
    inventory_windows = payload.get("_inventory_windows")
    if isinstance(inventory_windows, list):
        return [dict(window) for window in inventory_windows[:32] if isinstance(window, dict)]
    windows: list[dict] = []

    def product_label(value: object) -> str:
        if not isinstance(value, str):
            return "limit"
        normalized = "-".join(value.strip().lower().split())
        return {
            "primary": "primary",
            "secondary": "secondary",
            "spark": "Spark",
        }.get(normalized, "limit")

    def add(label: str, entry: object) -> None:
        if not isinstance(entry, dict) or len(windows) >= 32:
            return
        percent = entry.get("used_percent")
        if (
            isinstance(percent, bool)
            or not isinstance(percent, (int, float))
            or not math.isfinite(float(percent))
        ):
            return
        minutes = entry.get("window_minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
            seconds = entry.get("limit_window_seconds", entry.get("window_seconds"))
            minutes = (
                seconds / 60.0
                if not isinstance(seconds, bool)
                and isinstance(seconds, (int, float))
                and math.isfinite(float(seconds))
                else None
            )
        if isinstance(minutes, (int, float)) and not math.isfinite(float(minutes)):
            minutes = None
        reset_at = entry.get("resets_at", entry.get("reset_at"))
        windows.append(
            {
                "label": product_label(label),
                "used_percent": max(0.0, min(100.0, float(percent))),
                "window_minutes": (
                    max(1, int(round(float(minutes))))
                    if isinstance(minutes, (int, float)) and float(minutes) > 0.0
                    else None
                ),
                "resets_at": (
                    reset_at
                    if not isinstance(reset_at, bool)
                    and isinstance(reset_at, (str, int, float))
                    else None
                ),
            }
        )

    add("primary", payload.get("primary", payload.get("primary_window")))
    add("secondary", payload.get("secondary", payload.get("secondary_window")))
    additional = payload.get("additional_rate_limits", payload.get("limits"))
    if isinstance(additional, list):
        for entry in additional:
            if not isinstance(entry, dict):
                continue
            label = product_label(entry.get("name") or entry.get("limit_name"))
            nested = entry.get("rate_limit")
            if isinstance(nested, dict):
                before = len(windows)
                add(label, nested.get("primary", nested.get("primary_window")))
                add(f"{label} secondary", nested.get("secondary", nested.get("secondary_window")))
                if len(windows) != before:
                    continue
            add(label, entry)
    # Banked credits ride the same evidence stream as windows: the
    # rollout's rate_limits carries {"credits": {has_credits, balance}}
    # and nothing surfaced it (t3code PR #7813 showed how much operators
    # want this number). A credits record has no used_percent, so it
    # travels under its own key and the parser lifts it off the lane
    # path into snapshot.credits_remaining.
    credits = payload.get("credits")
    if isinstance(credits, dict) and credits.get("has_credits") is True:
        try:
            balance = float(str(credits.get("balance", "")).replace(",", ""))
        except (TypeError, ValueError):
            balance = None
        if balance is not None and math.isfinite(balance) and balance > 0.0:
            windows.append({"label": "credits", "credits_balance": balance})
    return windows


# The only translation from a Codex window label to a declared lane identity.
# `codex_windows_from_limits` already collapses the payload's shapes onto a
# small product-owned label set; exactly two of those labels name a ceiling
# this build declared. Everything else -- "Spark", a bare "limit", a nested
# secondary -- is DROPPED rather than force-fitted, because Codex's own window
# set is not stable: OpenAI removed the 5-hour window entirely for ~2.5 weeks
# in 2026 and `additional_rate_limits[]` carries model-specific sub-caps. When
# `primary` is absent, `windows[0]` is whatever that list held, and the legacy
# path rendered it as the ceiling that stops work.
CODEX_LANE_IDENTITIES = {
    "primary": "five-hour",
    "secondary": "weekly",
}
# What each declared lane's window actually IS, in minutes. The label map above
# is a naming convention of OpenAI's that has already changed once; the window
# length is the thing the lane means. When the payload states a length, that is
# what binds -- a `primary` carrying a 10,080-minute window is the WEEKLY
# ceiling under a renamed key, and filing it as `five-hour` produced a card
# whose row said "7d" while the authority layer had it recorded as the 5-hour
# lane. A stated length that matches no declared lane is dropped, exactly as an
# unrecognised label is; the label map is the fallback for a payload that
# states no length at all.
CODEX_LANE_WINDOW_MINUTES = {
    "five-hour": 300,
    "weekly": 10_080,
}
# A window whose boundary is still a full window away and whose usage is zero
# is not a reading of an empty allowance -- it is what Codex writes when it has
# no attested balance to report, and the boundary slides forward with the wall
# clock every time it writes another one. Two minutes of slack is far more than
# a real fixed boundary needs to fall out of this band, and far less than the
# lifetime of the placeholder, which never does.
CODEX_FRESH_WINDOW_TOLERANCE_SECONDS = 120.0
CODEX_QUOTA_SOURCE = SourceKey(
    provider_id="codex",
    adapter_id="quota",
    source_instance_id="local",
    capability_id="remote_quota_windows",
)
# One machine runs one signed-in Codex CLI at a time, so this scopes reset
# continuity to "the ChatGPT plan currently signed in for Codex". Like
# Claude's, it is deliberately NOT an account identifier and nothing derived
# from a token reaches it; an account switch is handled by advancing the
# source generation, which retires every reset boundary the old account set.
CODEX_ACCOUNT_SCOPE = "codex-chatgpt"


def _codex_window_minutes(window: dict) -> float | None:
    minutes = window.get("window_minutes")
    if (
        isinstance(minutes, bool)
        or not isinstance(minutes, (int, float))
        or not math.isfinite(float(minutes))
        or float(minutes) <= 0.0
    ):
        return None
    return float(minutes)


def _codex_lane_window_id(window: dict) -> str | None:
    """Which declared lane this payload window IS.

    Two gates, and both matter. The label gate is unchanged and still decides
    WHETHER this window may become a lane at all: only the plan's own `primary`
    and `secondary` qualify, so a `Spark` or `Fable` allowance is dropped rather
    than promoted to a ceiling that stops work. The length gate then decides
    WHICH declared lane it is, because the label is a name OpenAI has already
    moved once and the window length is what the lane means -- a `primary`
    carrying 10,080 minutes is the weekly ceiling under a renamed key. A
    declared label carrying a length that matches no declared lane is dropped
    for the same reason an undeclared label is: force-fitting it is how a
    7-day ceiling ended up filed as the 5-hour one.
    """
    window_id = CODEX_LANE_IDENTITIES.get(window.get("label"))
    if window_id is None:
        return None
    minutes = _codex_window_minutes(window)
    if minutes is None:
        return window_id
    for candidate, declared in CODEX_LANE_WINDOW_MINUTES.items():
        if int(round(minutes)) == declared:
            return candidate
    return None


def _codex_window_is_unattested(
    window: dict,
    *,
    reset_epoch: float | None,
    observed_at: float,
    used: float,
) -> bool:
    """True when this window states no balance, only that it just opened.

    Zero used with the whole window still ahead is not the same claim as
    "none of this allowance has been spent". It is the shape Codex writes when
    it has nothing to report, and rendering it as a confident percentage is the
    Spark class of defect: a status bar inventing a number.
    """
    if used != 0.0 or reset_epoch is None:
        return False
    minutes = _codex_window_minutes(window)
    if minutes is None:
        return False
    window_seconds = minutes * 60.0
    remaining = float(reset_epoch) - float(observed_at)
    return remaining >= window_seconds - CODEX_FRESH_WINDOW_TOLERANCE_SECONDS


def _codex_lane_evidence(descriptor, window: object, *, observed_at: float):
    """Map one normalized Codex window onto one declared lane, or nothing."""
    if not isinstance(window, dict):
        return None
    window_id = _codex_lane_window_id(window)
    if window_id is None:
        return None
    lane = next(
        (
            candidate
            for candidate in descriptor.lanes
            if candidate.key.window == window_id
            and candidate.key.model is None
            and candidate.key.effect is QuotaEffect.ALL_WORKLOADS
        ),
        None,
    )
    if lane is None:
        return None

    used = window.get("used_percent")
    if (
        isinstance(used, bool)
        or not isinstance(used, (int, float))
        or not math.isfinite(float(used))
    ):
        return None

    # `parse_reset_epoch` is the one place that accepts both the ISO string and
    # the epoch/second forms this file has carried, and returns None for
    # anything that is not a credible future boundary. A window with no such
    # boundary stays honestly reset-less rather than gaining a countdown.
    reset_epoch = parse_reset_epoch(window.get("resets_at"), now=observed_at)
    percent = max(0.0, min(100.0, float(used)))
    unattested = _codex_window_is_unattested(
        window,
        reset_epoch=reset_epoch,
        observed_at=observed_at,
        used=percent,
    )
    return SupportedLaneEvidence(
        key=lane.key,
        metric_kind=EvidenceMetricKind.PERCENT_USED,
        # The window and its boundary are still real facts and still carried.
        # Only the balance is withheld, because only the balance was invented.
        percent=None if unattested else percent,
        state=ObservationState.NULL if unattested else ObservationState.OBSERVED,
        reset_state=(ResetState.FUTURE if reset_epoch is not None else ResetState.UNKNOWN),
        reset_epoch=reset_epoch,
        window_minutes=_codex_window_minutes(window),
    )


def codex_capacity_evidence_from_windows(
    descriptor,
    windows,
    *,
    observed_at: float,
):
    """Project normalized Codex windows onto the declared lanes of one source.

    The descriptor owns lane identity; this only decides which declared lane
    each product label refers to. `used_percent` stays used-first all the way
    into `SupportedLaneEvidence` -- the single inversion to remaining belongs
    to `capacity_sources`, so exactly one place can invert it wrongly.

    `auth_mode` is deliberately left unset: the codex policy declares two
    ChatGPT modes and a rollout file on disk cannot say which credential wrote
    it. Claiming one would be a guess, and it also has to match the refresh
    key's scope, which is None for the same reason.
    """
    if descriptor.source != CODEX_QUOTA_SOURCE:
        raise ValueError("codex capacity descriptor mismatch")
    matched: dict[object, SupportedLaneEvidence] = {}
    for window in windows or ():
        evidence = _codex_lane_evidence(descriptor, window, observed_at=observed_at)
        if evidence is not None:
            # First match wins, and `codex_windows_from_limits` emits the
            # plan's own primary and secondary windows before anything from
            # `additional_rate_limits[]`. That ordering is load-bearing: an
            # extra allowance that happens to be named "primary" must not be
            # able to displace the real 5-hour ceiling.
            matched.setdefault(evidence.key, evidence)
    # Declaration order, not payload order: Codex has moved windows around
    # between releases, and reading order (shortest window first) is a product
    # decision rather than whatever the file happened to list first.
    return SupportedCapacityEvidence(
        source=CODEX_QUOTA_SOURCE,
        health_kind=SourceHealthKind.HEALTHY,
        lanes=tuple(matched[lane.key] for lane in descriptor.lanes if lane.key in matched),
        account_discriminator=CODEX_ACCOUNT_SCOPE,
        has_last_known_good=False,
        auth_mode=None,
    )


def _parse_file(
    path: Path,
    expected_stat: os.stat_result,
    dedupe_secret: bytes,
) -> _ParseResult:
    session_id = f"claude:{expected_stat.st_dev}:{expected_stat.st_ino}"
    snapshot = _read_verified_prefix(path, expected_stat)
    if snapshot is None:
        return _ParseResult([], 0, False)
    text, parsed_size = snapshot
    records: list[tuple] = []
    malformed_lines = 0
    for line in text.splitlines(keepends=True):
        # T3's mightCarryUsage: skip before parsing.
        if USAGE_MARKER not in line:
            continue
        record = _record_from_line(line, session_id, dedupe_secret)
        if record is not None:
            records.append(record)
        else:
            malformed_lines += 1
    return _ParseResult(records, malformed_lines, True, (), True, parsed_size)


def _parse_file_tail(
    path: Path,
    expected_stat: os.stat_result,
    resume_offset: int,
    provider_id: str,
    dedupe_secret: bytes,
) -> _ParseResult:
    """Parse only the bytes appended past ``resume_offset``.

    Live transcripts grow every few seconds; re-reading a 10MB session
    file in full on each poll was a measured ~15-20% of a core in
    bursts. The offset is the cached entry's own parsed size. Reads are
    growth-tolerant (_read_verified_prefix): the file may keep growing
    while we read -- we parse exactly the inventory snapshot's bytes,
    trimmed to the last newline.
    """
    snapshot = _read_verified_prefix(path, expected_stat, resume_offset)
    if snapshot is None:
        return _ParseResult([], 0, False)
    text, parsed_size = snapshot
    lines = text.splitlines(keepends=True)
    if provider_id == "codex":
        (
            last_counts,
            last_epoch,
            rate_limit_windows,
            malformed_lines,
            _eof,
        ) = _scan_codex_lines(lines)
        if last_counts is None or last_epoch is None:
            return _ParseResult(
                [], malformed_lines, True, rate_limit_windows, True, parsed_size
            )
        physical_id = f"codex:{expected_stat.st_dev}:{expected_stat.st_ino}"
        return _ParseResult(
            [
                (
                    "codex",
                    physical_id,
                    "codex",
                    last_epoch,
                    last_counts[0],
                    last_counts[1],
                    last_counts[2],
                    last_counts[3],
                    physical_id,
                )
            ],
            malformed_lines,
            True,
            rate_limit_windows,
            True,
            parsed_size,
        )
    session_id = f"claude:{expected_stat.st_dev}:{expected_stat.st_ino}"
    records: list[tuple] = []
    malformed_lines = 0
    for line in lines:
        if USAGE_MARKER not in line:
            continue
        record = _record_from_line(line, session_id, dedupe_secret)
        if record is not None:
            records.append(record)
        else:
            malformed_lines += 1
    return _ParseResult(records, malformed_lines, True, (), True, parsed_size)


def _incremental_resume_offset(entry: object, candidate_stat: os.stat_result) -> int | None:
    """The byte offset a grown file may resume from, or None.

    Requires: same physical file (device+inode), strictly larger size,
    and a cached parse that ended on a newline. Shrunk files, replaced
    inodes, and mid-line tails all return None -- the caller falls back
    to the existing full reparse.
    """
    if not isinstance(entry, dict):
        return None
    try:
        cached_size = int(entry["size"])
        if (
            int(entry["device"]) != candidate_stat.st_dev
            or int(entry["inode"]) != candidate_stat.st_ino
            or entry.get("eof_newline") is not True
            or cached_size < 0
            or cached_size >= candidate_stat.st_size
        ):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return cached_size


def _source_key_payload(source_key: SourceKey) -> dict[str, str]:
    return {
        "provider_id": source_key.provider_id,
        "adapter_id": source_key.adapter_id,
        "source_instance_id": source_key.source_instance_id,
        "capability_id": source_key.capability_id,
    }


def _load_cache(
    cache_path: Path,
    source_key: SourceKey | None = None,
) -> dict:
    try:
        cache_path.lstat()
        ensure_private_directory(cache_path.parent)
        # An oversized cache is refused rather than parsed: read_private_text
        # raises OSError past the cap, which lands in the handler below as an
        # ordinary cold scan. The next write replaces it with a capped one.
        data = json.loads(
            read_private_text(cache_path, max_bytes=USAGE_CACHE_MAX_BYTES)
        )
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        # A corrupt or old cache costs one cold scan, never a broken menu.
        return {}
    if source_key is not None and data.get("source_key") != _source_key_payload(source_key):
        return {}
    files = data.get("files")
    sessions = data.get("sessions")
    models = data.get("models")
    dedupes = data.get("dedupes")
    dedupe_secret = data.get("dedupe_secret")
    if (
        not isinstance(files, dict)
        or not isinstance(sessions, list)
        or not isinstance(models, list)
        or not isinstance(dedupes, list)
        or not isinstance(dedupe_secret, str)
        or len(dedupe_secret) != 64
        or not all(isinstance(value, str) for value in sessions)
        or not all(isinstance(value, str) for value in models)
        or not all(isinstance(value, str) for value in dedupes)
    ):
        return {}
    try:
        bytes.fromhex(dedupe_secret)
    except ValueError:
        return {}
    return data


def _decode_records(
    entry: dict,
    sessions: list,
    models: list,
    dedupes: list,
    *,
    expected_provider: str,
) -> list[tuple] | None:
    rows = entry.get("records", [])
    if (
        not isinstance(rows, list)
        or not all(isinstance(value, str) for value in sessions)
        or not all(isinstance(value, str) for value in models)
        or not all(isinstance(value, str) for value in dedupes)
    ):
        return None
    records = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 9:
            return None
        model_index, session_index, usage_model_index = row[:3]
        dedupe_index = row[8]
        indexes = (model_index, session_index, usage_model_index, dedupe_index)
        if any(not isinstance(index, int) or isinstance(index, bool) for index in indexes):
            return None
        epoch = row[3]
        token_counts = row[4:8]
        if (
            isinstance(epoch, bool)
            or not isinstance(epoch, (int, float))
            or not math.isfinite(float(epoch))
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for value in token_counts
            )
        ):
            return None
        try:
            provider = models[model_index]
            if provider != expected_provider:
                return None
            records.append(
                (
                    provider,
                    sessions[session_index],
                    models[usage_model_index],
                    float(epoch),
                    token_counts[0],
                    token_counts[1],
                    token_counts[2],
                    token_counts[3],
                    dedupes[dedupe_index],
                )
            )
        except IndexError:
            return None
    return records


def _decode_cached_records(
    entry: object,
    provider_id: str,
    expected_stat: os.stat_result,
    sessions: list,
    models: list,
    dedupes: list,
    *,
    require_stat_match: bool = True,
) -> tuple[list[tuple], int, tuple[dict, ...]] | None:
    if not isinstance(entry, dict):
        return None
    try:
        if require_stat_match and (
            int(entry["size"]) != expected_stat.st_size
            or float(entry["mtime"]) != expected_stat.st_mtime
            or int(entry["device"]) != expected_stat.st_dev
            or int(entry["inode"]) != expected_stat.st_ino
        ):
            return None
        if entry["provider"] != provider_id:
            return None
        malformed_lines = int(entry.get("malformed_lines", 0))
        if malformed_lines < 0:
            return None
        raw_windows = entry.get("rate_limit_windows", [])
        if not isinstance(raw_windows, list):
            return None
        rate_limit_windows = tuple(
            dict(window) for window in raw_windows[:32] if isinstance(window, dict)
        )
    except (KeyError, TypeError, ValueError):
        return None
    records = _decode_records(
        entry,
        sessions,
        models,
        dedupes,
        expected_provider=provider_id,
    )
    return None if records is None else (records, malformed_lines, rate_limit_windows)


def _physical_file_unchanged(path: Path, expected_stat: os.stat_result) -> bool:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return False
    try:
        current = os.fstat(descriptor)
        return bool(
            stat.S_ISREG(current.st_mode)
            and current.st_dev == expected_stat.st_dev
            and current.st_ino == expected_stat.st_ino
            and current.st_size == expected_stat.st_size
            and current.st_mtime_ns == expected_stat.st_mtime_ns
        )
    finally:
        os.close(descriptor)


def _discover_usage_files(
    root: Path,
    provider_id: str,
    *,
    max_files: int,
    max_file_bytes: int,
) -> tuple[_CoverageState, list[_DiscoveredUsageFile], Path | None]:
    coverage = _CoverageState(provider_id=provider_id)
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return coverage, [], None
    except OSError:
        coverage.root_present = True
        return coverage, [], None

    coverage.root_present = True
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        return coverage, [], None

    candidates: list[_DiscoveredUsageFile] = []
    seen_physical_files: set[tuple[int, int]] = set()
    walk_errors: list[OSError] = []

    def on_error(error: OSError) -> None:
        walk_errors.append(error)

    try:
        walker = os.walk(root, followlinks=False, onerror=on_error)
        saw_root = False
        for directory, directory_names, file_names in walker:
            base = Path(directory)
            if base == root:
                saw_root = True
            directory_names.sort()
            file_names.sort()
            safe_directories: list[str] = []
            for name in directory_names:
                child = base / name
                try:
                    info = child.lstat()
                except OSError as error:
                    walk_errors.append(error)
                    continue
                if stat.S_ISDIR(info.st_mode):
                    safe_directories.append(name)
            directory_names[:] = safe_directories

            for name in file_names:
                if not name.endswith(".jsonl"):
                    continue
                path = base / name
                try:
                    info = path.lstat()
                except OSError:
                    coverage.unreadable_files += 1
                    continue
                if stat.S_ISLNK(info.st_mode):
                    coverage.skipped_symlinks += 1
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                coverage.files_discovered += 1
                physical_key = (info.st_dev, info.st_ino)
                if physical_key in seen_physical_files:
                    coverage.duplicate_physical_files += 1
                    continue
                seen_physical_files.add(physical_key)
                if info.st_size > max_file_bytes:
                    coverage.oversized_files += 1
                    continue
                if len(candidates) >= max_files:
                    coverage.truncated_files += 1
                    continue
                candidates.append(_DiscoveredUsageFile(path=path, info=info))
    except OSError:
        walk_errors.append(OSError(f"failed to walk usage root: {root}"))

    try:
        current_root_info = root.lstat()
    except OSError:
        current_root_info = None
    if (
        current_root_info is None
        or not stat.S_ISDIR(current_root_info.st_mode)
        or current_root_info.st_dev != root_info.st_dev
        or current_root_info.st_ino != root_info.st_ino
    ):
        coverage.root_walked = False
        coverage.files_discovered = 0
        coverage.unreadable_files = 0
        coverage.skipped_symlinks = 0
        coverage.duplicate_physical_files = 0
        return coverage, [], None

    coverage.root_walked = saw_root
    coverage.walk_failed = bool(walk_errors)
    if not coverage.root_walked or coverage.walk_failed:
        return coverage, candidates, None
    try:
        return coverage, candidates, root.resolve()
    except OSError:
        return coverage, candidates, root


def build_usage_inventory(
    root: Path,
    *,
    codex_root: Path | None = None,
    max_files_per_source: int = USAGE_INVENTORY_MAX_FILES,
    max_file_bytes: int = USAGE_FILE_MAX_BYTES,
) -> LocalUsageInventory:
    """Freeze one bounded, no-follow admission set for all local consumers."""
    if (
        isinstance(max_files_per_source, bool)
        or not isinstance(max_files_per_source, int)
        or not 0 <= max_files_per_source <= USAGE_INVENTORY_MAX_FILES
        or isinstance(max_file_bytes, bool)
        or not isinstance(max_file_bytes, int)
        or not 1 <= max_file_bytes <= USAGE_FILE_MAX_BYTES
    ):
        raise ValueError("invalid usage inventory bounds")

    sources: list[_InventorySource] = []
    source_roots: list[tuple[str, Path]] = [("claude", root)]
    if codex_root is not None:
        source_roots.append(("codex", codex_root))
    for provider_id, source_root in source_roots:
        coverage, candidates, scanned_root = _discover_usage_files(
            source_root,
            provider_id,
            max_files=max_files_per_source,
            max_file_bytes=max_file_bytes,
        )
        sources.append(
            _InventorySource(
                provider_id=provider_id,
                root=source_root,
                root_key=_root_key(source_root),
                walk_complete=scanned_root is not None,
                coverage=coverage.finalize(),
                candidates=tuple(candidates),
            )
        )
    return LocalUsageInventory(tuple(sources))


def _cached_entry_covers(entry: object, since_epoch: float) -> bool:
    """Does this cache entry hold enough history to answer this window?

    Retention lets an entry keep only part of a file. The entry still matches
    its file on (mtime, size, device, inode), so nothing else here can tell a
    truncated entry from a complete one -- only the floor it recorded can.
    """
    if not isinstance(entry, dict):
        return False
    try:
        cached_since = float(entry.get("since", 0.0))
    except (TypeError, ValueError):
        return False
    return cached_since <= since_epoch


def _cache_file_key(provider_id: str, info: os.stat_result) -> str:
    return f"{provider_id}:{info.st_dev}:{info.st_ino}"


def _intern(value: str, table: list[str], index: dict[str, int]) -> int:
    position = index.get(value)
    if position is None:
        position = len(table)
        table.append(value)
        index[value] = position
    return position


def _scan_inventory_usage(
    inventory: LocalUsageInventory,
    expected_roots: dict[str, Path],
    cache_path: Path | None = None,
    *,
    since_epoch: float = 0.0,
    cache_max_files: int = USAGE_CACHE_MAX_FILES,
    cache_source_key: SourceKey,
) -> UsageTotals:
    """Scan one frozen provider-local inventory into private aggregate state."""
    inventory_roots = {source.provider_id: source.root for source in inventory.sources}
    if inventory_roots != expected_roots:
        raise ValueError("usage inventory roots do not match scan roots")

    cache = (
        _load_cache(cache_path, cache_source_key)
        if cache_path is not None
        else {}
    )
    cached_files = cache.get("files", {}) if cache else {}
    cached_sessions = cache.get("sessions", []) if cache else []
    cached_models = cache.get("models", []) if cache else []
    cached_dedupes = cache.get("dedupes", []) if cache else []
    dedupe_secret = (
        bytes.fromhex(cache["dedupe_secret"])
        if cache
        else secrets.token_bytes(32)
    )

    # A cache entry may hold only part of a file's history. It therefore
    # records the floor it was truncated to, and is refused below unless that
    # floor still covers the window being asked for -- otherwise a partial
    # entry would read as a complete one and silently undercount usage.
    retention_epoch = (
        max(0.0, since_epoch - USAGE_CACHE_RETENTION_HEADROOM_SECONDS)
        if since_epoch > 0.0
        else 0.0
    )

    sessions_table: list[str] = []
    sessions_index: dict[str, int] = {}
    models_table: list[str] = []
    models_index: dict[str, int] = {}
    dedupes_table: list[str] = []
    dedupes_index: dict[str, int] = {}
    scanned_files: dict[
        str,
        tuple[os.stat_result, str, str | None, int, list[tuple], tuple[dict, ...], bool, int],
    ] = {}
    coverage_states: dict[str, _CoverageState] = {}

    all_records: list[tuple] = []
    observed_root_keys: set[str] = set()
    rate_candidates: list[tuple[int, tuple[dict, ...]]] = []

    for source in inventory.sources:
        coverage = _CoverageState.from_coverage(source.coverage)
        coverage_states[source.provider_id] = coverage
        parser = _parse_codex_file if source.provider_id == "codex" else _parse_file
        if source.root_key is not None and source.walk_complete:
            observed_root_keys.add(source.root_key)
        for candidate in source.candidates:
            key = _cache_file_key(source.provider_id, candidate.info)
            raw_cached_entry = cached_files.get(key)
            cached_entry = (
                _decode_cached_records(
                    raw_cached_entry,
                    coverage.provider_id,
                    candidate.info,
                    cached_sessions,
                    cached_models,
                    cached_dedupes,
                )
                if _cached_entry_covers(raw_cached_entry, since_epoch)
                else None
            )
            if cached_entry is not None:
                cached_records, cached_malformed_lines, cached_rate_windows = cached_entry
                if not _physical_file_unchanged(candidate.path, candidate.info):
                    coverage.unreadable_files += 1
                    continue
                coverage.cache_hits += 1
                coverage.malformed_lines += cached_malformed_lines
                all_records.extend(cached_records)
                if source.provider_id == "codex" and cached_rate_windows:
                    rate_candidates.append((candidate.info.st_mtime_ns, cached_rate_windows))
                scanned_files[key] = (
                    candidate.info,
                    source.provider_id,
                    source.root_key,
                    cached_malformed_lines,
                    cached_records,
                    cached_rate_windows,
                    raw_cached_entry.get("eof_newline") is True
                    if isinstance(raw_cached_entry, dict)
                    else False,
                    int(raw_cached_entry.get("size", candidate.info.st_size))
                    if isinstance(raw_cached_entry, dict)
                    else candidate.info.st_size,
                )
                continue

            # Incremental tail: the file GREW in place (same device and
            # inode, cached parse ended on a newline). Read only the
            # appended bytes instead of re-parsing a 10MB live
            # transcript on every poll. Claude records append; a codex
            # tail's token_count is the session's new CUMULATIVE total
            # and REPLACES the cached record. Any anomaly falls through
            # to the full reparse below.
            resume_offset = (
                _incremental_resume_offset(raw_cached_entry, candidate.info)
                if _cached_entry_covers(raw_cached_entry, since_epoch)
                else None
            )
            if resume_offset is not None:
                base = _decode_cached_records(
                    raw_cached_entry,
                    coverage.provider_id,
                    candidate.info,
                    cached_sessions,
                    cached_models,
                    cached_dedupes,
                    require_stat_match=False,
                )
                if base is not None:
                    base_records, base_malformed, base_windows = base
                    tail = _parse_file_tail(
                        candidate.path,
                        candidate.info,
                        resume_offset,
                        source.provider_id,
                        dedupe_secret,
                    )
                    if tail.read_ok:
                        coverage.files_read += 1
                        merged_malformed = base_malformed + tail.malformed_lines
                        coverage.malformed_lines += merged_malformed
                        if source.provider_id == "codex":
                            merged_records = list(
                                tail.records if tail.records else base_records
                            )
                            merged_windows = tail.rate_limit_windows or base_windows
                        else:
                            merged_records = [*base_records, *tail.records]
                            merged_windows = ()
                        all_records.extend(merged_records)
                        if source.provider_id == "codex" and merged_windows:
                            rate_candidates.append(
                                (candidate.info.st_mtime_ns, merged_windows)
                            )
                        scanned_files[key] = (
                            candidate.info,
                            source.provider_id,
                            source.root_key,
                            merged_malformed,
                            merged_records,
                            merged_windows,
                            tail.eof_newline,
                            tail.parsed_size
                            if tail.parsed_size >= 0
                            else candidate.info.st_size,
                        )
                        continue

            result = (
                parser(candidate.path, candidate.info)
                if source.provider_id == "codex"
                else parser(candidate.path, candidate.info, dedupe_secret)
            )
            if not result.read_ok:
                coverage.unreadable_files += 1
                continue
            coverage.files_read += 1
            coverage.malformed_lines += result.malformed_lines
            all_records.extend(result.records)
            if source.provider_id == "codex" and result.rate_limit_windows:
                rate_candidates.append((candidate.info.st_mtime_ns, result.rate_limit_windows))
            scanned_files[key] = (
                candidate.info,
                source.provider_id,
                source.root_key,
                result.malformed_lines,
                result.records,
                result.rate_limit_windows,
                result.eof_newline,
                result.parsed_size
                if result.parsed_size >= 0
                else candidate.info.st_size,
            )

    retained_cached_files: dict[
        str,
        tuple[float, int, int, int, str, str | None, int, list[tuple], tuple[dict, ...], bool],
    ] = {}
    for key, entry in cached_files.items():
        if key in scanned_files or not isinstance(entry, dict):
            continue
        try:
            cached_mtime = float(entry["mtime"])
            cached_size = int(entry["size"])
            cached_device = int(entry["device"])
            cached_inode = int(entry["inode"])
            cached_provider = str(entry["provider"])
            cached_root_key = entry.get("root_key")
            if cached_root_key is not None and not isinstance(cached_root_key, str):
                continue
            if cached_root_key in observed_root_keys:
                continue
            if not _cached_entry_covers(entry, since_epoch):
                # Written for a narrower window than the one being asked for
                # now. Drop it so the file is rescanned rather than reporting
                # a truncated history as complete.
                continue
            cached_malformed_lines = int(entry.get("malformed_lines", 0))
            raw_windows = entry.get("rate_limit_windows", [])
            if not isinstance(raw_windows, list):
                continue
            cached_rate_windows = tuple(
                dict(window) for window in raw_windows[:32] if isinstance(window, dict)
            )
            if cached_provider not in {"claude", "codex"}:
                continue
            if cached_malformed_lines < 0:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        cached_records = _decode_records(
            entry,
            cached_sessions,
            cached_models,
            cached_dedupes,
            expected_provider=cached_provider,
        )
        if cached_records is None:
            continue
        retained_cached_files[key] = (
            cached_mtime,
            cached_size,
            cached_device,
            cached_inode,
            cached_provider,
            cached_root_key,
            cached_malformed_lines,
            cached_records,
            cached_rate_windows,
            entry.get("eof_newline") is True,
        )

    cache_candidates = [
        (
            key,
            file_info.st_mtime,
            parsed_size,
            file_info.st_dev,
            file_info.st_ino,
            provider_id,
            root_key,
            malformed_lines,
            records,
            rate_windows,
            eof_newline,
        )
        for key, (
            file_info,
            provider_id,
            root_key,
            malformed_lines,
            records,
            rate_windows,
            eof_newline,
            parsed_size,
        ) in scanned_files.items()
    ]
    cache_candidates.extend(
        (key, mtime, size, device, inode, provider, root_key, malformed_lines, records, rate_windows, eof_newline)
        for key, (
            mtime,
            size,
            device,
            inode,
            provider,
            root_key,
            malformed_lines,
            records,
            rate_windows,
            eof_newline,
        ) in retained_cached_files.items()
    )
    cache_candidates.sort(key=lambda item: (-item[1], item[0]))
    selected_cache_candidates = cache_candidates[: max(0, cache_max_files)]
    # Newest first, so both bounds below drop the least useful history.
    cache_budget = USAGE_CACHE_MAX_BYTES
    new_files: dict[str, dict] = {}
    for (
        key,
        mtime,
        size,
        device,
        inode,
        provider,
        root_key,
        malformed_lines,
        records,
        rate_windows,
        eof_newline,
    ) in selected_cache_candidates:
        if retention_epoch > 0.0:
            records = [record for record in records if record[3] >= retention_epoch]
            # An entry with NO surviving records is still worth keeping: it
            # records that this file has nothing at or after the floor, which
            # costs ~300 bytes and saves re-reading the file.
            #
            # Dropping it instead looks like a saving and is the opposite. On
            # this corpus ~2,000 of 2,633 files are older than the window, so
            # dropping them meant re-reading 2,000 transcripts on every scan
            # cycle -- measured live as a CPU pin at 104%, permanently. The
            # cache exists to avoid exactly that read.
        cost = _CACHE_BYTES_PER_ENTRY + _CACHE_BYTES_PER_RECORD * len(records)
        if cost > cache_budget and new_files:
            # Skip this one, but keep going. Stopping here defeated the whole
            # point of the paragraph above: candidates are newest first, so the
            # expensive record-heavy entries come first and the cheap
            # record-less ones -- the ones the comment says are worth ~300
            # bytes to save a whole file read -- come last. On a 365-day window
            # over 5,600 transcripts the budget was spent on ~560 entries and
            # the remaining ~5,000 files were re-read on every scan cycle,
            # which is exactly the CPU pin that paragraph was written about.
            # The first entry is still always admitted: an empty cache would
            # mean a cold scan on every single refresh.
            continue
        cache_budget -= cost
        new_files[key] = {
            "since": retention_epoch,
            "size": size,
            "mtime": mtime,
            "device": device,
            "inode": inode,
            "provider": provider,
            "root_key": root_key,
            "eof_newline": bool(eof_newline),
            "malformed_lines": malformed_lines,
            "rate_limit_windows": [dict(window) for window in rate_windows],
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
            "source_key": _source_key_payload(cache_source_key),
            "files": new_files,
            "sessions": sessions_table,
            "models": models_table,
            "dedupes": dedupes_table,
            "dedupe_secret": dedupe_secret.hex(),
        }
        if payload != cache:
            try:
                atomic_private_write(
                    cache_path,
                    json.dumps(payload, separators=(",", ":")),
                )
            except OSError:
                pass

    totals = UsageTotals()
    totals.source_coverage = {
        provider_id: coverage.finalize()
        for provider_id, coverage in coverage_states.items()
    }
    seen: set[str] = set()
    priced_records = 0
    total_pricing_records = 0
    priced_token_count = 0
    total_pricing_token_count = 0
    for record in all_records:
        provider, session, model, epoch, inp, cached_in, cache_create, out, dedupe = record
        if epoch < since_epoch:
            continue
        # Global first-seen dedupe across ALL files -- resumed/forked
        # sessions copy records forward and content blocks repeat usage. Apply
        # the requested window first so an old copy cannot suppress a current
        # one, and expose exactly the same canonical stream downstream.
        if dedupe in seen:
            continue
        seen.add(dedupe)
        totals.records.append(record)
        totals.sessions.add(session)
        if provider == "codex":
            totals.codex_sessions.add(session)
            totals.codex_tokens += inp + cached_in + out
            continue
        totals.input_tokens += inp
        totals.cached_input_tokens += cached_in
        totals.cache_creation_tokens += cache_create
        totals.output_tokens += out
        total_pricing_records += 1
        record_tokens = inp + cached_in + cache_create + out
        total_pricing_token_count += record_tokens
        pricing = _pricing_for_model(model)
        if pricing is None:
            continue
        priced_records += 1
        priced_token_count += record_tokens
        input_rate, output_rate = pricing
        totals.estimated_cost_usd += (
            inp * input_rate
            + cached_in * input_rate * CACHE_READ_RATE
            + cache_create * input_rate * CACHE_WRITE_RATE
            + out * output_rate
        ) / 1_000_000.0
        totals.estimated_cache_savings_usd += (
            cached_in * input_rate * (1.0 - CACHE_READ_RATE)
        ) / 1_000_000.0
    totals.pricing_coverage = PricingCoverageMetrics(
        priced_records=priced_records,
        total_records=total_pricing_records,
        priced_token_count=priced_token_count,
        total_token_count=total_pricing_token_count,
    )
    if rate_candidates:
        newest = max(rate_candidates, key=lambda item: item[0])
        totals.codex_rate_limit_evidence = newest[1]
        totals.codex_rate_limit_observed_at = newest[0] / 1_000_000_000.0
    codex_source = next(
        (source for source in inventory.sources if source.provider_id == "codex"),
        None,
    )
    # Rebind, never clear-then-repopulate: the UI thread reads this global
    # between scans, and the gap after clear() showed it an empty table.
    latest: dict[str, tuple[dict, ...]] = {}
    if codex_source is not None and codex_source.root_key is not None:
        latest[codex_source.root_key] = totals.codex_rate_limit_evidence
    global _LATEST_CODEX_RATE_LIMITS
    _LATEST_CODEX_RATE_LIMITS = latest
    return totals


def _provider_inventory(
    provider_id: str,
    root: Path,
    *,
    max_files: int = USAGE_INVENTORY_MAX_FILES,
    max_file_bytes: int = USAGE_FILE_MAX_BYTES,
) -> LocalUsageInventory:
    coverage, candidates, scanned_root = _discover_usage_files(
        root,
        provider_id,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
    )
    return LocalUsageInventory(
        (
            _InventorySource(
                provider_id=provider_id,
                root=root,
                root_key=_root_key(root),
                walk_complete=scanned_root is not None,
                coverage=coverage.finalize(),
                candidates=tuple(candidates),
            ),
        )
    )


def _validated_transcript_source(source: NegotiatedProviderSource) -> SourceKey:
    if not (
        type(source) is NegotiatedProviderSource
        and source in negotiated_provider_sources()
        and source.observation_invocation_allowed
        and source.source_key.capability_id == "transcript_usage"
        and source.source_key.provider_id in {"claude", "codex"}
    ):
        raise ValueError("source is not an enabled transcript usage capability")
    return source.source_key


def _provider_result(
    source_key: SourceKey,
    totals: UsageTotals,
) -> ProviderUsageResult:
    records = tuple(
        record for record in totals.records if record[0] == source_key.provider_id
    )
    sessions = {record[1] for record in records}
    input_tokens = sum(record[4] for record in records)
    cached_input_tokens = sum(record[5] for record in records)
    cache_creation_tokens = sum(record[6] for record in records)
    output_tokens = sum(record[7] for record in records)
    metrics = totals.pricing_coverage
    priced_records = metrics.priced_records
    unpriced_records = max(0, metrics.total_records - metrics.priced_records)
    if priced_records == 0:
        pricing = PricingCoverage.UNAVAILABLE
        cost = None
        savings = None
        pricing_as_of = None
        if source_key.provider_id == "codex":
            unpriced_records = len(records)
    else:
        pricing = (
            PricingCoverage.PARTIAL
            if unpriced_records > 0
            else PricingCoverage.COMPLETE
        )
        cost = totals.estimated_cost_usd
        savings = totals.estimated_cache_savings_usd
        pricing_as_of = metrics.table_as_of
    return ProviderUsageResult(
        source_key=source_key,
        coverage=totals.source_coverage[source_key.provider_id],
        observed_session_count=len(sessions),
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_creation_tokens=cache_creation_tokens,
        output_tokens=output_tokens,
        pricing_coverage=pricing,
        priced_record_count=priced_records,
        unpriced_record_count=unpriced_records,
        covered_cost_estimate_usd=cost,
        covered_cache_savings_estimate_usd=savings,
        pricing_as_of=pricing_as_of,
    )


def _scan_provider_usage_with_totals(
    source: NegotiatedProviderSource,
    root: Path,
    cache_path: Path | None,
    *,
    since_epoch: float,
    cache_max_files: int = USAGE_CACHE_MAX_FILES,
    inventory: LocalUsageInventory | None = None,
) -> tuple[ProviderUsageResult, UsageTotals]:
    source_key = _validated_transcript_source(source)
    local_inventory = inventory or _provider_inventory(source_key.provider_id, root)
    totals = _scan_inventory_usage(
        local_inventory,
        {source_key.provider_id: root},
        cache_path,
        since_epoch=since_epoch,
        cache_max_files=cache_max_files,
        cache_source_key=source_key,
    )
    return _provider_result(source_key, totals), totals


def _secondary_provider_cache_path(
    cache_path: Path | None,
    source_key: SourceKey,
) -> Path | None:
    if cache_path is None:
        return None
    suffix = ".".join(
        (
            source_key.provider_id,
            source_key.adapter_id,
            source_key.source_instance_id,
            source_key.capability_id,
        )
    )
    return cache_path.with_name(f"{cache_path.name}.{suffix}")


def _merge_usage_totals(parts: tuple[UsageTotals, ...]) -> UsageTotals:
    merged = UsageTotals()
    for part in parts:
        merged.sessions.update(part.sessions)
        merged.codex_sessions.update(part.codex_sessions)
        merged.codex_tokens += part.codex_tokens
        merged.records.extend(part.records)
        merged.input_tokens += part.input_tokens
        merged.cached_input_tokens += part.cached_input_tokens
        merged.cache_creation_tokens += part.cache_creation_tokens
        merged.output_tokens += part.output_tokens
        merged.estimated_cost_usd += part.estimated_cost_usd
        merged.estimated_cache_savings_usd += part.estimated_cache_savings_usd
        merged.source_coverage.update(part.source_coverage)
        if part.codex_rate_limit_evidence:
            merged.codex_rate_limit_evidence = part.codex_rate_limit_evidence
    priced_records = sum(part.pricing_coverage.priced_records for part in parts)
    total_records = sum(part.pricing_coverage.total_records for part in parts)
    priced_tokens = sum(part.pricing_coverage.priced_token_count for part in parts)
    total_tokens = sum(part.pricing_coverage.total_token_count for part in parts)
    merged.pricing_coverage = PricingCoverageMetrics(
        priced_records,
        total_records,
        priced_tokens,
        total_tokens,
    )
    return merged


def scan_usage(
    root: Path,
    cache_path: Path | None = None,
    *,
    since_epoch: float = 0.0,
    codex_root: Path | None = None,
    cache_max_files: int = USAGE_CACHE_MAX_FILES,
    inventory: LocalUsageInventory | None = None,
) -> UsageTotals:
    """Compatibility aggregation over independent provider-local scans."""
    roots = {"claude": root}
    if codex_root is not None:
        roots["codex"] = codex_root
    frozen = inventory or build_usage_inventory(root, codex_root=codex_root)
    by_provider = {item.provider_id: item for item in frozen.sources}
    transcript_sources = {
        row.source_key.provider_id: row
        for row in negotiated_provider_sources()
        if row.source_key.capability_id == "transcript_usage"
        and row.observation_invocation_allowed
    }
    parts: list[UsageTotals] = []
    for index, (provider_id, provider_root) in enumerate(roots.items()):
        source = transcript_sources.get(provider_id)
        source_inventory = by_provider.get(provider_id)
        if source is None or source_inventory is None:
            continue
        provider_cache = (
            cache_path
            if index == 0
            else _secondary_provider_cache_path(cache_path, source.source_key)
        )
        _result, totals = _scan_provider_usage_with_totals(
            source,
            provider_root,
            provider_cache,
            since_epoch=since_epoch,
            cache_max_files=cache_max_files,
            inventory=LocalUsageInventory((source_inventory,)),
        )
        parts.append(totals)
    return _merge_usage_totals(tuple(parts))


def _compact_tokens(count: int) -> str:
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}B"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.0f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}K"
    return str(count)


def compact_token_count(count: int) -> str:
    return _compact_tokens(max(0, int(count)))


def usage_summary_line(
    totals: UsageTotals,
    mode: str = "tokens",
    *,
    period_label: str = "Today",
) -> str | None:
    """Tokens-first (cost approximated in parens, the CodexBar
    presentation) or cost-first -- None when there is nothing to say.

    The provider is deliberately NOT named here. Every view that shows this
    line already owns a provider title and prepends it, so naming the provider
    a second time produced "Claude · Claude, last 365 days: ..." -- one fact
    emitted by two owners who did not know about each other. The period is the
    only prefix this line is entitled to.
    """
    count = len(totals.sessions) - len(totals.codex_sessions)
    if count <= 0:
        return None
    plural = "session" if count == 1 else "sessions"
    parts = [f"{count} {plural}"]
    claude_tokens = (
        totals.input_tokens + totals.cached_input_tokens + totals.output_tokens
    )
    if mode == "sessions":
        pass
    elif mode == "cost":
        if totals.estimated_cost_usd >= 0.005:
            parts.append(f"estimated ${totals.estimated_cost_usd:.2f}")
        if totals.estimated_cache_savings_usd >= 0.005:
            parts.append(
                f"saved ${totals.estimated_cache_savings_usd:.2f} with caching (estimated)"
            )
        pricing_fraction = totals.pricing_coverage.fraction
        if pricing_fraction is not None:
            parts.append(f"pricing coverage {pricing_fraction:.0%}")
        if (
            totals.pricing_coverage.priced_records > 0
            and totals.pricing_coverage.priced_records
            < totals.pricing_coverage.total_records
        ):
            parts.append("Estimate, known models only")
    else:
        if claude_tokens:
            approx = (
                f" (~${totals.estimated_cost_usd:,.0f}) estimated"
                if totals.estimated_cost_usd >= 0.5
                else ""
            )
            parts.append(f"{_compact_tokens(claude_tokens)} tokens{approx}")
        if totals.estimated_cache_savings_usd >= 0.5:
            parts.append(
                f"saved ~${totals.estimated_cache_savings_usd:,.0f} with caching (estimated)"
            )
        # A dollar figure without its coverage is a silent floor: the
        # Overview line showed "$23k estimated" while 55% of the tokens
        # were unpriced models (audit, 2026-08-26). Cost mode already
        # disclosed; the tokens-mode line now does too when partial.
        pricing_fraction = totals.pricing_coverage.fraction
        if (
            totals.estimated_cost_usd >= 0.5
            and pricing_fraction is not None
            and pricing_fraction < 0.995
        ):
            parts.append(f"{pricing_fraction:.0%} of tokens priced")
    return str(period_label) + ": " + " · ".join(parts)


def usage_period_label(days: int) -> str:
    if days == 7:
        return "Last 7 days"
    if days == 30:
        return "Last 30 days"
    if days == 90:
        return "Last 90 days"
    if days == 365:
        return "Last 365 days"
    raise ValueError("usage period must be 7, 30, 90, or 365 days")


def nice_usage_scale(maximum: float) -> float:
    """Return the next quiet 1, 2, 5 scale step above one metric maximum."""
    value = max(0.0, float(maximum))
    if value <= 0.0:
        return 1.0
    magnitude = 10.0 ** math.floor(math.log10(value))
    normalized = value / magnitude
    for step in (1.0, 2.0, 5.0, 10.0):
        if normalized < step:
            return step * magnitude
    return 10.0 * magnitude


def daily_buckets(records, days: int = 7, *, now: datetime | None = None):
    """Per local-calendar-day totals for the last N days:
    {day_iso: {"claude_cost": float, "codex_tokens": int, "sessions": int}}.
    Day keys come from each record's own timestamp converted to LOCAL
    time (the CodexBar rule: a 23:30 UTC session lands in YOUR day)."""
    current = now or datetime.now()
    day_keys = [
        (current - timedelta(days=offset)).date().isoformat()
        for offset in range(days - 1, -1, -1)
    ]
    buckets = {
        key: {
            "claude_cost": 0.0,
            "codex_tokens": 0,
            "sessions": set(),
            "providers": {},
        }
        for key in day_keys
    }
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
        provider_bucket = bucket["providers"].setdefault(
            provider,
            {"tokens": 0, "cost": 0.0, "sessions": set()},
        )
        provider_bucket["tokens"] += inp + cached_in + cache_create + out
        provider_bucket["sessions"].add(session)
        if provider == "codex":
            bucket["codex_tokens"] += inp + cached_in + out
            # Codex cost was skipped by code choice while the records
            # carried model + token splits the whole time (owner
            # decision 2026-08-26: price it). No cache-write premium --
            # OpenAI bills cache writes at the plain input rate.
            gpt_pricing = _gpt_pricing_for_model(model)
            if gpt_pricing is None:
                continue
            input_rate, output_rate = gpt_pricing
            provider_bucket["cost"] += (
                inp * input_rate
                + cached_in * input_rate * CACHE_READ_RATE
                + cache_create * input_rate
                + out * output_rate
            ) / 1_000_000.0
        else:
            pricing = _pricing_for_model(model)
            if pricing is None:
                continue
            input_rate, output_rate = pricing
            cost = (
                inp * input_rate
                + cached_in * input_rate * CACHE_READ_RATE
                + cache_create * input_rate * CACHE_WRITE_RATE
                + out * output_rate
            ) / 1_000_000.0
            provider_bucket["cost"] += cost
            if provider == "claude":
                bucket["claude_cost"] += cost
    for bucket in buckets.values():
        bucket["sessions"] = len(bucket["sessions"])
        for provider_bucket in bucket["providers"].values():
            provider_bucket["sessions"] = len(provider_bucket["sessions"])
    return buckets


def usage_graph_model(
    records,
    *,
    days: int,
    metric: str,
    provider_ids: tuple[str, ...],
    now: datetime | None = None,
    extra_sessions: dict[str, dict[str, int]] | None = None,
) -> dict:
    """Build one shared-axis, range-consistent graph projection.

    Only providers with an admitted local usage record are returned. The
    selected metric is common to every series, so unlike the old dual-axis
    chart, line height always means the same thing.
    """
    if metric not in {"tokens", "cost", "sessions"}:
        raise ValueError("usage metric is tokens, cost, or sessions")
    if (
        type(provider_ids) is not tuple
        or not provider_ids
        or len(provider_ids) != len(set(provider_ids))
        or not all(type(value) is str and value for value in provider_ids)
    ):
        raise ValueError("usage providers must be a nonempty unique tuple")
    buckets = daily_buckets(records, days=days, now=now)
    if metric == "sessions" and extra_sessions:
        # Ledger-derived sessions for providers with no local
        # transcripts (grok, devin, any hook-emitting provider) --
        # "why does the graph only have Claude and Codex?" answered
        # for the one metric every provider can honestly report.
        for provider_id, day_counts in extra_sessions.items():
            for day, count in day_counts.items():
                bucket = buckets.get(day)
                if bucket is None:
                    continue
                entry = bucket["providers"].setdefault(provider_id, {})
                entry["sessions"] = max(
                    int(entry.get("sessions", 0) or 0), int(count)
                )
    label_stride = max(1, days // 6)
    labels = tuple(
        day[5:].replace("-", "/") if index % label_stride == 0 else ""
        for index, day in enumerate(buckets)
    )
    series = []
    for provider_id in provider_ids:
        values = tuple(
            bucket["providers"].get(provider_id, {}).get(metric, 0)
            for bucket in buckets.values()
        )
        if any(float(value) > 0.0 for value in values):
            series.append({"provider_id": provider_id, "values": values})
    maximum = max(
        (
            float(value)
            for provider_series in series
            for value in provider_series["values"]
        ),
        default=0.0,
    )
    return {
        "days": days,
        "period_label": usage_period_label(days),
        "metric": metric,
        "labels": labels,
        "series": tuple(series),
        "scale_max": nice_usage_scale(maximum),
    }


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
