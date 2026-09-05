from __future__ import annotations

import json
from pathlib import Path

from sidepulse import usage_stats


def _meta(
    session_id: str,
    *,
    forked_from_id: str | None = None,
    parent_thread_id: str | None = None,
    timestamp: str = "2026-08-13T10:00:30Z",
) -> dict:
    payload: dict[str, object] = {"id": session_id, "timestamp": timestamp}
    if forked_from_id is not None:
        payload["forked_from_id"] = forked_from_id
    if parent_thread_id is not None:
        payload["parent_thread_id"] = parent_thread_id
    return {"type": "session_meta", "timestamp": timestamp, "payload": payload}


def _tokens(
    total: int,
    last: int,
    timestamp: str,
    *,
    cached_total: int = 0,
    cached_last: int = 0,
    write_total: int = 0,
    write_last: int = 0,
    output_total: int = 0,
    output_last: int = 0,
) -> dict:
    return {
        "type": "event_msg",
        "timestamp": timestamp,
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": total,
                    "cached_input_tokens": cached_total,
                    "cache_write_input_tokens": write_total,
                    "output_tokens": output_total,
                },
                "last_token_usage": {
                    "input_tokens": last,
                    "cached_input_tokens": cached_last,
                    "cache_write_input_tokens": write_last,
                    "output_tokens": output_last,
                },
            },
        },
    }


def _legacy_tokens(total: int, timestamp: str) -> dict:
    row = _tokens(total, total, timestamp)
    del row["payload"]["info"]["last_token_usage"]
    return row


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _append(path: Path, rows: list[dict]) -> None:
    with path.open("a") as handle:
        handle.write("".join(json.dumps(row) + "\n" for row in rows))


def _scan(tmp_path: Path, root: Path, cache: Path | None = None, *, since: float = 0.0):
    claude = tmp_path / "claude"
    claude.mkdir(exist_ok=True)
    return usage_stats.scan_usage(claude, cache, codex_root=root, since_epoch=since)


def _codex_cache(cache: Path) -> Path:
    matches = [
        path
        for path in cache.parent.glob(cache.name + ".codex.*")
        if path.suffix != ".sqlite3"
    ]
    assert len(matches) == 1
    return matches[0]


def test_copied_rollout_events_are_counted_once(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    rows = [
        _meta("parent"),
        _tokens(100, 100, "2026-08-13T10:00:00Z"),
        _tokens(160, 60, "2026-08-13T10:01:00Z"),
    ]
    _write(root / "original.jsonl", rows)
    _write(root / "copy.jsonl", rows)

    totals = _scan(tmp_path, root)

    assert totals.codex_tokens == 160
    assert len(totals.records) == 2
    assert len(totals.codex_sessions) == 1


def test_unrelated_sessions_with_identical_event_values_both_count(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    event = _tokens(100, 100, "2026-08-13T10:00:00Z")
    _write(root / "one.jsonl", [_meta("one"), event])
    _write(root / "two.jsonl", [_meta("two"), event])

    assert _scan(tmp_path, root).codex_tokens == 200


def test_repeated_cumulative_snapshot_does_not_repeat_last_usage(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    _write(
        root / "repeat.jsonl",
        [
            _meta("repeat"),
            _tokens(100, 100, "2026-08-13T10:00:00Z"),
            _tokens(100, 100, "2026-08-13T10:01:00Z"),
        ],
    )

    assert _scan(tmp_path, root).codex_tokens == 100


def test_cached_input_is_not_counted_twice_cold_warm_and_tail(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    cache = tmp_path / "cache.json"
    rollout = root / "cached.jsonl"
    _write(
        rollout,
        [
            _meta("cached"),
            _tokens(
                100,
                100,
                "2026-08-13T10:00:00Z",
                cached_total=80,
                cached_last=80,
                output_total=10,
                output_last=10,
            ),
        ],
    )
    cold = _scan(tmp_path, root, cache)
    warm = _scan(tmp_path, root, cache)
    assert cold.codex_tokens == warm.codex_tokens == 110
    assert cold.records[0][4:8] == (20, 80, 0, 10)

    _append(
        rollout,
        [
            _tokens(
                150,
                50,
                "2026-08-13T10:01:00Z",
                cached_total=120,
                cached_last=40,
                output_total=15,
                output_last=5,
            )
        ],
    )
    tailed = _scan(tmp_path, root, cache)
    assert tailed.codex_tokens == 165
    assert tailed.records[-1][4:8] == (10, 40, 0, 5)


def test_cache_writes_are_split_from_ordinary_input_cold_warm_and_tail(
    tmp_path: Path,
) -> None:
    root = tmp_path / "codex"
    cache = tmp_path / "cache.json"
    rollout = root / "cache-write.jsonl"
    _write(
        rollout,
        [
            _meta("cache-write"),
            _tokens(
                100,
                100,
                "2026-08-13T10:00:00Z",
                cached_total=60,
                cached_last=60,
                write_total=15,
                write_last=15,
                output_total=10,
                output_last=10,
            ),
        ],
    )
    cold = _scan(tmp_path, root, cache)
    warm = _scan(tmp_path, root, cache)
    assert cold.codex_tokens == warm.codex_tokens == 110
    assert cold.records[0][4:8] == (25, 60, 15, 10)
    assert sum(cold.records[0][4:8]) == 100 + 10

    _append(
        rollout,
        [
            _tokens(
                160,
                60,
                "2026-08-13T10:01:00Z",
                cached_total=90,
                cached_last=30,
                write_total=25,
                write_last=10,
                output_total=20,
                output_last=10,
            )
        ],
    )
    tailed = _scan(tmp_path, root, cache)
    assert tailed.codex_tokens == 180
    assert tailed.records[-1][4:8] == (20, 30, 10, 10)
    assert sum(tailed.records[-1][4:8]) == (160 - 100) + (20 - 10)


def test_growing_duplicate_logical_session_keeps_only_new_events(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "codex"
    cache = tmp_path / "cache.json"
    first = _tokens(100, 100, "2026-08-13T10:00:00Z")
    second = _tokens(150, 50, "2026-08-13T10:01:00Z")
    _write(root / "first.jsonl", [_meta("same-session"), first, second])
    resumed = root / "resumed.jsonl"
    _write(resumed, [_meta("same-session"), first])
    assert _scan(tmp_path, root, cache).codex_tokens == 150

    third = _tokens(175, 25, "2026-08-13T10:02:00Z")
    _append(resumed, [second, third])
    monkeypatch.setattr(
        usage_stats,
        "_parse_codex_file",
        lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected full reparse")),
    )
    warm = _scan(tmp_path, root, cache)

    assert warm.codex_tokens == 175
    assert len(warm.codex_sessions) == 1


def test_incremental_tail_uses_cached_endpoint_for_repeated_snapshot(
    tmp_path: Path, monkeypatch,
) -> None:
    root = tmp_path / "codex"
    cache = tmp_path / "cache.json"
    rollout = root / "rollout.jsonl"
    _write(
        rollout,
        [_meta("session"), _tokens(100, 100, "2026-08-13T10:00:00Z")],
    )
    assert _scan(tmp_path, root, cache).codex_tokens == 100
    _append(rollout, [_tokens(100, 100, "2026-08-13T10:01:00Z")])
    monkeypatch.setattr(
        usage_stats,
        "_parse_codex_file",
        lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected full reparse")),
    )

    assert _scan(tmp_path, root, cache).codex_tokens == 100


def test_json_list_with_token_marker_is_malformed_not_fatal(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    _write(root / "bad.jsonl", [["token_count"]])

    totals = _scan(tmp_path, root)

    assert totals.codex_tokens == 0
    assert totals.source_coverage["codex"].malformed_lines == 1


def test_fork_counts_inherited_events_once_and_child_work_once(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    inherited = _tokens(100, 100, "2026-08-13T10:00:00Z")
    _write(root / "parent.jsonl", [_meta("parent"), inherited])
    _write(
        root / "child.jsonl",
        [
            _meta("child", forked_from_id="parent"),
            inherited,
            _tokens(140, 40, "2026-08-13T10:05:00Z"),
        ],
    )

    totals = _scan(tmp_path, root)

    assert totals.codex_tokens == 140
    assert len(totals.records) == 2


def test_nested_fork_counts_each_branches_new_work_once(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    parent = _tokens(100, 100, "2026-08-13T10:00:00Z")
    child = _tokens(140, 40, "2026-08-13T10:01:00Z")
    grand = _tokens(170, 30, "2026-08-13T10:02:00Z")
    _write(root / "parent.jsonl", [_meta("parent"), parent])
    _write(
        root / "child.jsonl",
        [_meta("child", forked_from_id="parent", timestamp="2026-08-13T10:00:30Z"), parent, child],
    )
    _write(
        root / "grand.jsonl",
        [_meta("grand", forked_from_id="child", parent_thread_id="parent", timestamp="2026-08-13T10:01:30Z"), parent, child, grand],
    )

    assert _scan(tmp_path, root).codex_tokens == 170


def test_nested_fork_keeps_unresolved_intermediate_work_when_file_is_missing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "codex"
    parent = _tokens(100, 100, "2026-08-13T10:00:00Z")
    missing_child = _tokens(140, 40, "2026-08-13T10:01:00Z")
    grand = _tokens(170, 30, "2026-08-13T10:02:00Z")
    _write(root / "parent.jsonl", [_meta("parent"), parent])
    _write(
        root / "grand.jsonl",
        [
            _meta(
                "grand",
                forked_from_id="missing-child",
                parent_thread_id="parent",
                timestamp="2026-08-13T10:01:30Z",
            ),
            parent,
            missing_child,
            grand,
        ],
    )

    assert _scan(tmp_path, root).codex_tokens == 170


def test_sibling_forks_keep_identical_post_fork_events_separate(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    parent = _tokens(100, 100, "2026-08-13T10:00:00Z")
    coincident = _tokens(140, 40, "2026-08-13T10:01:00Z")
    _write(root / "parent.jsonl", [_meta("parent"), parent])
    for child in ("child-a", "child-b"):
        _write(
            root / f"{child}.jsonl",
            [_meta(child, forked_from_id="parent"), parent, coincident],
        )

    assert _scan(tmp_path, root).codex_tokens == 180


def test_event_at_fork_boundary_reconciles_only_with_exact_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    parent = _tokens(100, 100, "2026-08-13T10:00:00Z")
    _write(root / "parent.jsonl", [_meta("parent"), parent])
    _write(
        root / "child.jsonl",
        [
            _meta("child", forked_from_id="parent", timestamp="2026-08-13T10:00:00Z"),
            parent,
            _tokens(140, 40, "2026-08-13T10:01:00Z"),
        ],
    )

    assert _scan(tmp_path, root).codex_tokens == 140


def test_identical_sibling_work_at_fork_boundary_remains_branch_owned(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    parent = _tokens(100, 100, "2026-08-13T09:59:00Z")
    coincident = _tokens(140, 40, "2026-08-13T10:00:00Z")
    _write(root / "parent.jsonl", [_meta("parent"), parent])
    for child in ("child-a", "child-b"):
        _write(
            root / f"{child}.jsonl",
            [
                _meta(child, forked_from_id="parent", timestamp="2026-08-13T10:00:00Z"),
                parent,
                coincident,
            ],
        )

    assert _scan(tmp_path, root).codex_tokens == 180


def test_missing_parent_does_not_discard_observed_fork_history(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    _write(
        root / "child.jsonl",
        [
            _meta("child", forked_from_id="missing-parent"),
            _tokens(100, 100, "2026-08-13T10:00:00Z"),
            _tokens(140, 40, "2026-08-13T10:05:00Z"),
        ],
    )

    assert _scan(tmp_path, root).codex_tokens == 140


def test_window_filters_copied_history_by_event_time(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    _write(
        root / "child.jsonl",
        [
            _meta("child", forked_from_id="parent-outside-inventory"),
            _tokens(100, 100, "2026-08-01T10:00:00Z"),
            _tokens(140, 40, "2026-08-13T10:05:00Z"),
        ],
    )
    since = usage_stats.datetime.fromisoformat("2026-08-13T00:00:00+00:00").timestamp()

    assert _scan(tmp_path, root, since=since).codex_tokens == 40


def test_last_usage_survives_cumulative_counter_reset(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    _write(
        root / "reset.jsonl",
        [
            _meta("reset"),
            _tokens(100, 100, "2026-08-13T10:00:00Z"),
            _tokens(20, 20, "2026-08-13T10:05:00Z"),
        ],
    )

    assert _scan(tmp_path, root).codex_tokens == 120


def test_legacy_cumulative_rows_use_positive_deltas_and_reset_values(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    _write(
        root / "legacy.jsonl",
        [
            _meta("legacy"),
            _legacy_tokens(100, "2026-08-13T10:00:00Z"),
            _legacy_tokens(150, "2026-08-13T10:01:00Z"),
            _legacy_tokens(20, "2026-08-13T10:02:00Z"),
        ],
    )

    assert _scan(tmp_path, root).codex_tokens == 170


def test_cache_contains_only_hmac_ids_and_new_codex_semantics_version(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    cache = tmp_path / "cache.json"
    _write(
        root / "rollout.jsonl",
        [_meta("raw-private-thread-id"), _tokens(10, 10, "2026-08-13T10:00:00Z")],
    )

    _scan(tmp_path, root, cache)
    provider_cache = _codex_cache(cache)
    persisted = provider_cache.read_text()

    assert "raw-private-thread-id" not in persisted
    payload = json.loads(persisted)
    assert payload["version"] == usage_stats.CACHE_VERSION
    assert payload["codex_semantics_version"] == usage_stats.CODEX_CACHE_SEMANTICS_VERSION


def test_old_codex_semantics_cache_forces_a_cold_rebuild(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    cache = tmp_path / "cache.json"
    _write(
        root / "rollout.jsonl",
        [_meta("session"), _tokens(10, 10, "2026-08-13T10:00:00Z")],
    )
    _scan(tmp_path, root, cache)
    provider_cache = _codex_cache(cache)
    payload = json.loads(provider_cache.read_text())
    del payload["codex_semantics_version"]
    provider_cache.write_text(json.dumps(payload))
    provider_cache.with_suffix(".files.sqlite3").unlink()

    rebuilt = _scan(tmp_path, root, cache)

    assert rebuilt.codex_tokens == 10
    assert rebuilt.source_coverage["codex"].files_read == 1
    assert json.loads(provider_cache.read_text())["codex_semantics_version"] == 4
