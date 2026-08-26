from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse import usage_stats
from sidepulse.usage_stats import (
    UsageSourceCoverage,
    UsageSourceStatus,
    build_usage_inventory,
    scan_usage,
)
from sidepulse.usage_view import source_text_for_coverage


def _claude_row(message_id: str, *, tokens: int = 7) -> dict:
    return {
        "type": "assistant",
        "timestamp": "2026-08-12T12:00:00Z",
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


def _codex_row(*, tokens: int = 11) -> dict:
    return {
        "type": "event_msg",
        "timestamp": "2026-08-12T12:00:00Z",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": tokens,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 0,
                }
            },
        },
    }


def _codex_rate_row(*, used_percent: float = 37) -> dict:
    return {
        "type": "event_msg",
        "timestamp": "2026-08-12T12:01:00Z",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": 11,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 0,
                }
            },
            "rate_limits": {
                "primary": {
                    "used_percent": used_percent,
                    "window_minutes": 300,
                    "resets_at": 1_777_777_777,
                }
            },
        },
    }


def _write_rows(path: Path, *rows: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


def _coverage(totals, provider_id: str):
    return totals.source_coverage[provider_id]


def test_missing_roots_are_not_reported_as_observed_empty_usage(tmp_path: Path) -> None:
    totals = scan_usage(
        tmp_path / "missing-claude",
        codex_root=tmp_path / "missing-codex",
    )

    for provider_id in ("claude", "codex"):
        coverage = _coverage(totals, provider_id)
        assert coverage.status is UsageSourceStatus.MISSING
        assert coverage.root_present is False
        assert coverage.root_walked is False
        assert coverage.files_discovered == 0
        assert coverage.files_read == 0


def test_existing_empty_roots_are_successfully_observed(tmp_path: Path) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    claude_root.mkdir()
    codex_root.mkdir()

    totals = scan_usage(claude_root, codex_root=codex_root)

    for provider_id in ("claude", "codex"):
        coverage = _coverage(totals, provider_id)
        assert coverage.status is UsageSourceStatus.OK
        assert coverage.root_present is True
        assert coverage.root_walked is True
        assert coverage.files_discovered == 0
        assert coverage.files_read == 0


def test_readable_provider_files_report_local_counts_and_totals(tmp_path: Path) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    _write_rows(claude_root / "project" / "claude.jsonl", _claude_row("m1"))
    _write_rows(codex_root / "rollout.jsonl", _codex_row())

    totals = scan_usage(claude_root, codex_root=codex_root)

    assert totals.input_tokens == 7
    assert totals.codex_tokens == 11
    for provider_id in ("claude", "codex"):
        coverage = _coverage(totals, provider_id)
        assert coverage.status is UsageSourceStatus.OK
        assert coverage.files_discovered == 1
        assert coverage.files_read == 1
        assert coverage.cache_hits == 0


def test_warm_unchanged_physical_files_are_cache_hits_not_parser_reads(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    cache_path = tmp_path / "state" / "usage-cache.json"
    _write_rows(claude_root / "claude.jsonl", _claude_row("m1"))
    _write_rows(codex_root / "codex.jsonl", _codex_row())
    cold = scan_usage(claude_root, cache_path, codex_root=codex_root)

    warm = scan_usage(claude_root, cache_path, codex_root=codex_root)

    assert warm.records == cold.records
    for provider_id in ("claude", "codex"):
        coverage = _coverage(warm, provider_id)
        assert coverage.status is UsageSourceStatus.OK
        assert coverage.files_discovered == 1
        assert coverage.files_read == 0
        assert coverage.cache_hits == 1


def test_warm_malformed_file_retains_partial_coverage_diagnostics(
    tmp_path: Path,
) -> None:
    root = tmp_path / "claude"
    malformed = root / "malformed.jsonl"
    malformed.parent.mkdir()
    malformed.write_text('{"type":"assistant","message":{"usage":BROKEN}}\n')
    cache_path = tmp_path / "state" / "usage-cache.json"

    cold = scan_usage(root, cache_path)
    warm = scan_usage(root, cache_path)

    assert _coverage(cold, "claude").status is UsageSourceStatus.PARTIAL
    warm_coverage = _coverage(warm, "claude")
    assert warm_coverage.status is UsageSourceStatus.PARTIAL
    assert warm_coverage.files_read == 0
    assert warm_coverage.cache_hits == 1
    assert warm_coverage.malformed_lines == 1


def test_cache_entries_are_never_reused_by_the_other_provider_parser(
    tmp_path: Path,
) -> None:
    shared_root = tmp_path / "shared"
    _write_rows(
        shared_root / "mixed.jsonl",
        _claude_row("claude", tokens=7),
        _codex_row(tokens=11),
    )
    cache_path = tmp_path / "state" / "usage-cache.json"
    scan_usage(shared_root, cache_path)

    totals = scan_usage(
        tmp_path / "missing-claude",
        cache_path,
        codex_root=shared_root,
    )

    codex = _coverage(totals, "codex")
    assert totals.input_tokens == 0
    assert totals.codex_tokens == 11
    assert codex.files_read == 1
    assert codex.cache_hits == 0


def test_malformed_candidate_keeps_valid_sibling_totals_and_marks_only_claude_partial(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    _write_rows(claude_root / "valid.jsonl", _claude_row("valid", tokens=13))
    malformed = claude_root / "malformed.jsonl"
    malformed.write_text(
        "ordinary transcript text that is not a usage candidate\n"
        '{"type":"assistant","message":{"usage":BROKEN}}\n'
    )
    codex_root.mkdir()

    totals = scan_usage(claude_root, codex_root=codex_root)

    claude = _coverage(totals, "claude")
    codex = _coverage(totals, "codex")
    assert totals.input_tokens == 13
    assert claude.status is UsageSourceStatus.PARTIAL
    assert claude.malformed_lines == 1
    assert claude.files_read == 2
    assert codex.status is UsageSourceStatus.OK
    assert codex.malformed_lines == 0


@pytest.mark.parametrize("provider_id", ("claude", "codex"))
def test_nonfinite_provider_token_line_is_partial_instead_of_crashing_scan(
    tmp_path: Path,
    provider_id: str,
) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    claude_root.mkdir()
    codex_root.mkdir()
    if provider_id == "claude":
        _write_rows(
            claude_root / "invalid.jsonl",
            _claude_row("invalid", tokens=float("inf")),
        )
        _write_rows(
            claude_root / "valid.jsonl",
            _claude_row("valid", tokens=59),
        )
    else:
        _write_rows(
            codex_root / "invalid.jsonl",
            _codex_row(tokens=float("inf")),
        )
        _write_rows(codex_root / "valid.jsonl", _codex_row(tokens=59))

    totals = scan_usage(claude_root, codex_root=codex_root)

    coverage = _coverage(totals, provider_id)
    expected = totals.input_tokens if provider_id == "claude" else totals.codex_tokens
    assert expected == 59
    assert coverage.status is UsageSourceStatus.PARTIAL
    assert coverage.malformed_lines == 1
    assert coverage.files_read == 2


def test_replaced_candidate_is_not_opened_as_discovered_and_valid_sibling_survives(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "claude"
    bad = _write_rows(claude_root / "bad.jsonl", _claude_row("old", tokens=101))
    _write_rows(claude_root / "valid.jsonl", _claude_row("valid", tokens=17))
    held = bad.with_name("bad-held.jsonl")
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and Path(path) == bad:
            bad.rename(held)
            _write_rows(bad, _claude_row("replacement", tokens=999))
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with patch("sidepulse.usage_stats.os.open", side_effect=swapping_open):
        totals = scan_usage(claude_root)

    coverage = _coverage(totals, "claude")
    assert totals.input_tokens == 17
    assert coverage.status is UsageSourceStatus.PARTIAL
    assert coverage.files_discovered == 2
    assert coverage.files_read == 1
    assert coverage.unreadable_files == 1


def test_in_place_candidate_mutation_after_discovery_is_a_bounded_failure(
    tmp_path: Path,
) -> None:
    """Mutation between stat and open is BOUNDED, no longer refused.

    2026-08-20: the strict refusal starved the HOT file -- a live agent
    transcript grew between the frozen inventory's stat and its open on
    EVERY scan, so the busiest file was skipped as unreadable forever
    and codex's weekly froze at an older file's number. Reads are now
    growth-tolerant snapshots: exactly the stat'd bytes, trimmed to the
    last newline. A mid-scan REWRITE therefore yields at most the new
    content's prefix (usually nothing), and the very next scan
    converges on the file's real content.
    """
    root = tmp_path / "claude"
    changing = _write_rows(root / "changing.jsonl", _claude_row("old", tokens=101))
    _write_rows(root / "valid.jsonl", _claude_row("valid", tokens=31))
    real_open = os.open
    mutated = False

    def mutating_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal mutated
        if not mutated and Path(path) == changing:
            _write_rows(changing, _claude_row("replacement", tokens=999))
            mutated = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with patch("sidepulse.usage_stats.os.open", side_effect=mutating_open):
        totals = scan_usage(root)

    # The snapshot prefix of the replacement holds no complete line, so
    # only the untouched sibling counts -- bounded, never invented.
    assert totals.input_tokens == 31

    # And the next scan (no mutation) converges on the real content.
    settled = scan_usage(root)
    assert settled.input_tokens == 31 + 999


def test_root_walk_failure_and_all_file_read_failure_are_failed(
    tmp_path: Path,
) -> None:
    walk_root = tmp_path / "walk-failure"
    walk_root.mkdir()
    real_scandir = os.scandir

    def refusing_scandir(path):
        if Path(path) == walk_root:
            raise PermissionError("root cannot be walked")
        return real_scandir(path)

    with patch("sidepulse.usage_stats.os.scandir", side_effect=refusing_scandir):
        walk_totals = scan_usage(walk_root)

    walked = _coverage(walk_totals, "claude")
    assert walked.status is UsageSourceStatus.FAILED
    assert walked.root_present is True
    assert walked.root_walked is False

    read_root = tmp_path / "read-failure"
    candidate = _write_rows(read_root / "only.jsonl", _claude_row("only"))
    real_open = os.open

    def refusing_open(path, flags, mode=0o777, *, dir_fd=None):
        if Path(path) == candidate:
            raise PermissionError("candidate cannot be opened")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with patch("sidepulse.usage_stats.os.open", side_effect=refusing_open):
        read_totals = scan_usage(read_root)

    unreadable = _coverage(read_totals, "claude")
    assert unreadable.status is UsageSourceStatus.FAILED
    assert unreadable.root_walked is True
    assert unreadable.files_discovered == 1
    assert unreadable.files_read == 0
    assert unreadable.unreadable_files == 1


def test_nested_walk_failure_is_partial_and_keeps_valid_sibling_totals(
    tmp_path: Path,
) -> None:
    root = tmp_path / "claude"
    blocked = root / "blocked"
    _write_rows(root / "valid.jsonl", _claude_row("valid", tokens=29))
    _write_rows(blocked / "hidden.jsonl", _claude_row("hidden", tokens=997))
    real_scandir = os.scandir

    def refusing_nested_scandir(path):
        if Path(path) == blocked:
            raise PermissionError("nested directory cannot be walked")
        return real_scandir(path)

    with patch(
        "sidepulse.usage_stats.os.scandir",
        side_effect=refusing_nested_scandir,
    ):
        totals = scan_usage(root)

    coverage = _coverage(totals, "claude")
    assert totals.input_tokens == 29
    assert coverage.status is UsageSourceStatus.PARTIAL
    assert coverage.root_present is True
    assert coverage.root_walked is True
    assert coverage.files_discovered == 1
    assert coverage.files_read == 1


def test_nested_walk_failure_does_not_prune_unobserved_cache_entries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "claude"
    blocked = root / "blocked"
    _write_rows(blocked / "hidden.jsonl", _claude_row("hidden"))
    cache_path = tmp_path / "state" / "usage-cache.json"
    scan_usage(root, cache_path)
    real_scandir = os.scandir

    def refusing_nested_scandir(path):
        if Path(path) == blocked:
            raise PermissionError("nested directory cannot be walked")
        return real_scandir(path)

    with patch(
        "sidepulse.usage_stats.os.scandir",
        side_effect=refusing_nested_scandir,
    ):
        totals = scan_usage(root, cache_path)

    assert _coverage(totals, "claude").status is UsageSourceStatus.PARTIAL
    retained_files = json.loads(cache_path.read_text())["files"]
    assert len(retained_files) == 1
    assert str(root) not in cache_path.read_text()


def test_symlink_leaf_and_root_never_expose_external_transcripts(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside_file = _write_rows(outside / "private.jsonl", _claude_row("external", tokens=313))
    leaf_root = tmp_path / "leaf-root"
    leaf_root.mkdir()
    (leaf_root / "linked.jsonl").symlink_to(outside_file)

    leaf_totals = scan_usage(leaf_root)

    leaf = _coverage(leaf_totals, "claude")
    assert leaf_totals.input_tokens == 0
    assert leaf.status is UsageSourceStatus.OK
    assert leaf.files_discovered == 0
    assert leaf.files_read == 0
    assert leaf.skipped_symlinks == 1

    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(outside, target_is_directory=True)
    root_totals = scan_usage(linked_root)

    root_coverage = _coverage(root_totals, "claude")
    assert root_totals.input_tokens == 0
    assert root_coverage.status is UsageSourceStatus.FAILED
    assert root_coverage.root_present is True
    assert root_coverage.root_walked is False


def test_root_replaced_by_symlink_during_discovery_does_not_expose_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / "claude"
    root.mkdir()
    held_root = tmp_path / "held-claude"
    outside = tmp_path / "outside"
    _write_rows(outside / "private.jsonl", _claude_row("external", tokens=313))
    real_scandir = os.scandir
    replaced = False

    def replacing_root_scandir(path):
        nonlocal replaced
        if not replaced and Path(path) == root:
            root.rename(held_root)
            root.symlink_to(outside, target_is_directory=True)
            replaced = True
        return real_scandir(path)

    with patch(
        "sidepulse.usage_stats.os.scandir",
        side_effect=replacing_root_scandir,
    ):
        totals = scan_usage(root)

    coverage = _coverage(totals, "claude")
    assert totals.input_tokens == 0
    assert coverage.status is UsageSourceStatus.FAILED
    assert coverage.root_present is True
    assert coverage.root_walked is False
    assert coverage.files_read == 0


def test_hard_link_paths_count_one_physical_file_and_one_duplicate(tmp_path: Path) -> None:
    root = tmp_path / "claude"
    original = _write_rows(root / "a.jsonl", _claude_row("physical", tokens=19))
    os.link(original, root / "b.jsonl")

    totals = scan_usage(root)
    totals_again = scan_usage(root)

    coverage = _coverage(totals, "claude")
    assert totals.input_tokens == 19
    assert coverage.status is UsageSourceStatus.OK
    assert coverage.files_discovered == 2
    assert coverage.files_read == 1
    assert coverage.duplicate_physical_files == 1
    assert totals_again.records[0][:8] == totals.records[0][:8]
    assert totals_again.records[0][8].startswith("message:")


@pytest.mark.parametrize(
    ("claude_setup", "codex_setup", "claude_status", "codex_status"),
    (
        ("missing", "empty", UsageSourceStatus.MISSING, UsageSourceStatus.OK),
        ("malformed", "valid", UsageSourceStatus.PARTIAL, UsageSourceStatus.OK),
        ("empty", "failed", UsageSourceStatus.OK, UsageSourceStatus.FAILED),
    ),
)
def test_provider_coverage_states_do_not_bleed_across_roots(
    tmp_path: Path,
    claude_setup: str,
    codex_setup: str,
    claude_status: UsageSourceStatus,
    codex_status: UsageSourceStatus,
) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    if claude_setup == "empty":
        claude_root.mkdir()
    elif claude_setup == "malformed":
        _write_rows(claude_root / "bad.jsonl", {"usage": "not-provider-usage"})
    if codex_setup == "empty":
        codex_root.mkdir()
    elif codex_setup == "valid":
        _write_rows(codex_root / "valid.jsonl", _codex_row())
    elif codex_setup == "failed":
        codex_root.mkdir()

    real_scandir = os.scandir

    def maybe_refuse_codex(path):
        if codex_setup == "failed" and Path(path) == codex_root:
            raise PermissionError("codex walk failed")
        return real_scandir(path)

    with patch("sidepulse.usage_stats.os.scandir", side_effect=maybe_refuse_codex):
        totals = scan_usage(claude_root, codex_root=codex_root)

    assert _coverage(totals, "claude").status is claude_status
    assert _coverage(totals, "codex").status is codex_status


def test_failed_read_is_not_persisted_as_a_successful_empty_cache_entry(
    tmp_path: Path,
) -> None:
    root = tmp_path / "claude"
    candidate = _write_rows(root / "retry.jsonl", _claude_row("retry", tokens=23))
    cache_path = tmp_path / "state" / "usage-cache.json"
    real_open = os.open

    def refusing_open(path, flags, mode=0o777, *, dir_fd=None):
        if Path(path) == candidate:
            raise PermissionError("transient read failure")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with patch("sidepulse.usage_stats.os.open", side_effect=refusing_open):
        failed = scan_usage(root, cache_path)

    recovered = scan_usage(root, cache_path)

    assert _coverage(failed, "claude").status is UsageSourceStatus.FAILED
    assert recovered.input_tokens == 23
    assert _coverage(recovered, "claude").files_read == 1
    assert _coverage(recovered, "claude").cache_hits == 0


def test_warm_cache_hit_revalidates_physical_file_after_discovery(
    tmp_path: Path,
) -> None:
    root = tmp_path / "claude"
    candidate = _write_rows(root / "cached.jsonl", _claude_row("old", tokens=41))
    cache_path = tmp_path / "state" / "usage-cache.json"
    scan_usage(root, cache_path)
    held = candidate.with_name("cached-held.jsonl")
    real_validation = usage_stats._physical_file_unchanged
    replaced = False

    def replacing_before_validation(path: Path, expected_stat: os.stat_result):
        nonlocal replaced
        if not replaced and path == candidate:
            candidate.rename(held)
            _write_rows(candidate, _claude_row("replacement", tokens=999))
            replaced = True
        return real_validation(path, expected_stat)

    with patch(
        "sidepulse.usage_stats._physical_file_unchanged",
        side_effect=replacing_before_validation,
    ):
        totals = scan_usage(root, cache_path)

    coverage = _coverage(totals, "claude")
    assert totals.input_tokens == 0
    assert coverage.status is UsageSourceStatus.FAILED
    assert coverage.files_read == 0
    assert coverage.cache_hits == 0
    assert coverage.unreadable_files == 1


@pytest.mark.parametrize(
    "corruption",
    (
        "records-not-list",
        "non-string-intern-table",
        "nonfinite-timestamp",
        "negative-token-count",
        "provider-mismatch",
    ),
)
def test_structurally_corrupt_cache_entry_is_reparsed_instead_of_published(
    tmp_path: Path,
    corruption: str,
) -> None:
    root = tmp_path / "claude"
    _write_rows(
        root / "valid.jsonl",
        _claude_row("valid", tokens=47),
    )
    cache_path = tmp_path / "state" / "usage-cache.json"
    scan_usage(root, cache_path)
    payload = json.loads(cache_path.read_text())
    entry = next(iter(payload["files"].values()))
    if corruption == "records-not-list":
        entry["records"] = None
    elif corruption == "non-string-intern-table":
        payload["models"] = [123 for _value in payload["models"]]
    elif corruption == "nonfinite-timestamp":
        entry["records"][0][3] = float("nan")
    elif corruption == "negative-token-count":
        entry["records"][0][4] = -1
    else:
        payload["models"][entry["records"][0][0]] = "codex"
    cache_path.write_text(json.dumps(payload))

    recovered = scan_usage(root, cache_path)

    coverage = _coverage(recovered, "claude")
    assert recovered.input_tokens == 47
    assert coverage.status is UsageSourceStatus.OK
    assert coverage.files_read == 1
    assert coverage.cache_hits == 0


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("files", []),
        ("sessions", {}),
        ("models", {}),
        ("dedupes", {}),
    ),
)
def test_wrong_cache_container_shapes_fall_back_to_cold_scan(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    root = tmp_path / "claude"
    _write_rows(root / "valid.jsonl", _claude_row("valid", tokens=53))
    cache_path = tmp_path / "state" / "usage-cache.json"
    scan_usage(root, cache_path)
    payload = json.loads(cache_path.read_text())
    payload[field] = replacement
    cache_path.write_text(json.dumps(payload))

    recovered = scan_usage(root, cache_path)

    coverage = _coverage(recovered, "claude")
    assert recovered.input_tokens == 53
    assert coverage.files_read == 1
    assert coverage.cache_hits == 0


def test_coverage_source_text_uses_only_bounded_counts_and_status(tmp_path: Path) -> None:
    root = tmp_path / "private-root-name"
    _write_rows(root / "valid.jsonl", _claude_row("valid"))
    (root / "bad.jsonl").write_text('{"usage":BROKEN}\n')
    coverage = _coverage(scan_usage(root), "claude")

    text = source_text_for_coverage(coverage)

    assert text == "Local transcripts · 2 files · partial"
    assert str(root) not in text
    assert len(text) <= 120


def test_public_coverage_model_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        UsageSourceCoverage(
            provider_id="claude",
            status=UsageSourceStatus.OK,
            root_present=True,
            root_walked=True,
            files_discovered=-1,
            files_read=0,
            cache_hits=0,
            malformed_lines=0,
            unreadable_files=0,
            skipped_symlinks=0,
            duplicate_physical_files=0,
        )


def test_one_frozen_inventory_supplies_usage_and_codex_rate_evidence(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    claude_root.mkdir()
    _write_rows(codex_root / "rollout.jsonl", _codex_rate_row(used_percent=37))
    inventory = build_usage_inventory(claude_root, codex_root=codex_root)

    with patch("sidepulse.usage_stats.os.walk", side_effect=AssertionError("second walk")):
        totals = scan_usage(
            claude_root,
            codex_root=codex_root,
            inventory=inventory,
        )

    with (
        patch("sidepulse.usage_stats.os.walk", side_effect=AssertionError("second walk")),
        patch("sidepulse.usage_stats.os.open", side_effect=AssertionError("second open")),
    ):
        limits = usage_stats.codex_rate_limits(codex_root)

    assert totals.codex_tokens == 11
    assert usage_stats.codex_windows_from_limits(limits) == [
        {
            "label": "primary",
            "used_percent": 37.0,
            "window_minutes": 300,
            "resets_at": 1_777_777_777,
        }
    ]


def test_cached_codex_rate_evidence_does_not_wait_for_a_historical_rescan(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    cache_path = tmp_path / "state" / "usage-cache.json"
    claude_root.mkdir()
    _write_rows(codex_root / "rollout.jsonl", _codex_rate_row(used_percent=41))
    scan_usage(claude_root, cache_path, codex_root=codex_root)

    with (
        patch("sidepulse.usage_stats.os.walk", side_effect=AssertionError("rescan")),
        patch(
            "sidepulse.usage_stats._parse_codex_file",
            side_effect=AssertionError("transcript"),
        ),
    ):
        limits = usage_stats.cached_codex_rate_limits(cache_path)

    assert usage_stats.codex_windows_from_limits(limits) == [
        {
            "label": "primary",
            "used_percent": 41.0,
            "window_minutes": 300,
            "resets_at": 1_777_777_777,
        }
    ]


def test_file_added_after_inventory_freeze_is_not_opened_or_counted(tmp_path: Path) -> None:
    root = tmp_path / "claude"
    first = _write_rows(root / "first.jsonl", _claude_row("first", tokens=13))
    inventory = build_usage_inventory(root)
    late = _write_rows(root / "late.jsonl", _claude_row("late", tokens=997))
    real_open = os.open

    def reject_late(path, flags, mode=0o777, *, dir_fd=None):
        if Path(path) == late:
            raise AssertionError("path added after inventory freeze was opened")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with patch("sidepulse.usage_stats.os.open", side_effect=reject_late):
        totals = scan_usage(root, inventory=inventory)

    assert totals.input_tokens == 13
    assert _coverage(totals, "claude").files_discovered == 1
    assert first.exists()


def test_inventory_bounds_oversized_and_excess_files_as_partial_coverage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "claude"
    _write_rows(root / "a.jsonl", _claude_row("a", tokens=3))
    _write_rows(root / "b.jsonl", _claude_row("b", tokens=5))
    oversized = _write_rows(root / "oversized.jsonl", _claude_row("large", tokens=997))
    inventory = build_usage_inventory(
        root,
        max_files_per_source=1,
        max_file_bytes=max(1, oversized.stat().st_size - 1),
    )

    totals = scan_usage(root, inventory=inventory)

    coverage = _coverage(totals, "claude")
    assert coverage.status is UsageSourceStatus.PARTIAL
    assert coverage.files_read == 1
    assert coverage.truncated_files == 1
    assert coverage.oversized_files == 1
    assert totals.input_tokens in {3, 5}


def test_unknown_models_count_activity_but_have_no_estimated_price(tmp_path: Path) -> None:
    root = tmp_path / "claude"
    _write_rows(
        root / "models.jsonl",
        _claude_row("known", tokens=1_000_000),
        {
            **_claude_row("unknown", tokens=2_000_000),
            "message": {
                **_claude_row("unknown", tokens=2_000_000)["message"],
                "model": "private-experimental-model",
            },
        },
    )

    totals = scan_usage(root)

    assert totals.input_tokens == 3_000_000
    assert totals.estimated_cost_usd == pytest.approx(3.0)
    assert totals.pricing_coverage.priced_records == 1
    assert totals.pricing_coverage.total_records == 2
    assert totals.pricing_coverage.priced_token_count == 1_000_000
    assert totals.pricing_coverage.total_token_count == 3_000_000
    assert totals.pricing_coverage.table_version
    assert totals.pricing_coverage.table_as_of == "2026-08-26"
    summary = usage_stats.usage_summary_line(totals, "cost")
    assert summary is not None
    assert "estimated" in summary.lower()
    assert "pricing coverage 33%" in summary.lower()


def test_persisted_usage_cache_contains_no_paths_titles_or_raw_models(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private-client-name"
    raw_model = "claude-sonnet-private-suffix"
    raw_title = "private-project-session"
    raw_error = "private read failure detail"
    raw_message_id = "message-private"
    path = private_root / f"{raw_title}.jsonl"
    _write_rows(
        path,
        {
            **_claude_row(raw_message_id, tokens=7),
            "message": {
                **_claude_row(raw_message_id, tokens=7)["message"],
                "model": raw_model,
            },
        },
    )
    cache_path = tmp_path / "state" / "usage-cache.json"

    with patch("sidepulse.usage_stats.os.scandir", wraps=os.scandir):
        scan_usage(private_root, cache_path)
    persisted = cache_path.read_text()

    assert str(private_root) not in persisted
    assert str(path) not in persisted
    assert raw_title not in persisted
    assert raw_model not in persisted
    assert raw_message_id not in persisted
    assert hashlib.sha256(raw_message_id.encode()).hexdigest() not in persisted
    assert hashlib.blake2b(raw_message_id.encode()).hexdigest() not in persisted
    assert raw_error not in persisted


def test_codex_rate_limit_labels_are_product_owned_and_bounded(tmp_path: Path) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    claude_root.mkdir()
    raw_label = "private-account@example.com/" + "x" * 200
    row = _codex_rate_row()
    row["payload"]["rate_limits"]["additional_rate_limits"] = [
        {
            "name": raw_label,
            "used_percent": index,
            "window_minutes": 60,
        }
        for index in range(100)
    ]
    _write_rows(codex_root / "rollout.jsonl", row)

    totals = scan_usage(claude_root, codex_root=codex_root)
    windows = usage_stats.codex_windows_from_limits(
        usage_stats.codex_rate_limits(totals)
    )

    assert len(windows) <= 32
    assert all(raw_label not in window["label"] for window in windows)
