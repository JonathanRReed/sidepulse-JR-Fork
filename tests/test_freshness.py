from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sidepulse.capacity_types import SourceKey
from sidepulse.collector import (
    CODEX_TRANSCRIPT_PROVIDER,
    AgentMonitor,
    LiveAgentMonitor,
    SourceSpec,
    status_is_stale,
)
from sidepulse.completions import detect_completion_batch
from sidepulse.freshness import (
    FUTURE_CLOCK_SKEW_SECONDS,
    bounded_age_seconds,
    is_recent,
)
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.operator_state import BootIdentifier, ClockSample
from sidepulse.provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderFactBatch,
    ProviderWatermark,
    ProviderWorkFact,
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
    WorkLifecycle,
)
from sidepulse.settings import AgentMonitorSettings


def test_future_clock_skew_is_clamped_only_through_explicit_boundary() -> None:
    """Removing the future-skew bound would make far-future state actionable."""
    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)

    assert bounded_age_seconds(
        now,
        now + timedelta(seconds=FUTURE_CLOCK_SKEW_SECONDS),
    ) == 0.0
    assert is_recent(
        now,
        now + timedelta(seconds=FUTURE_CLOCK_SKEW_SECONDS),
        120.0,
    )
    assert bounded_age_seconds(
        now,
        now + timedelta(seconds=FUTURE_CLOCK_SKEW_SECONDS + 0.001),
    ) == float("inf")
    assert not is_recent(
        now,
        now + timedelta(seconds=FUTURE_CLOCK_SKEW_SECONDS + 0.001),
        120.0,
    )
    assert not is_recent(now, now + timedelta(minutes=10), 120.0)


def test_age_window_is_inclusive_and_negative_windows_are_never_recent() -> None:
    """Changing the inclusive boundary or accepting negatives breaks one policy."""
    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)

    assert is_recent(now, now - timedelta(seconds=120), 120.0)
    assert not is_recent(now, now - timedelta(seconds=120.001), 120.0)
    assert not is_recent(now, now, -0.001)


def test_naive_and_aware_datetimes_share_utc_policy_without_crashing() -> None:
    """Removing datetime normalization would crash or compare different zones."""
    aware_now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    naive_stamp = datetime(2026, 8, 12, 11, 59, 0)
    aware_offset_stamp = datetime.fromisoformat("2026-08-12T06:59:00-05:00")

    assert bounded_age_seconds(aware_now, naive_stamp) == 60.0
    assert bounded_age_seconds(aware_now.replace(tzinfo=None), aware_offset_stamp) == 60.0


def _status(
    agent_id: str,
    updated_at: datetime,
    *,
    mode: AgentMode = AgentMode.COMPLETED,
    event_name: str = "Stop",
) -> AgentStatus:
    return AgentStatus(
        provider="codex",
        agent_id=agent_id,
        display_name=agent_id,
        mode=mode,
        updated_at=updated_at,
        event_name=event_name,
        session_id=agent_id.rsplit(":", 1)[-1],
    )


def test_all_completion_surfaces_reject_same_implausible_future_time() -> None:
    """A future row must not celebrate, badge, or enter recent menu rows."""
    from sidepulse.status_bar import recent_statuses, unseen_completions

    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    future = _status("codex:session:future", now + timedelta(minutes=10))
    snapshot = SimpleNamespace(
        statuses=(),
        stale_statuses=(future,),
        collected_at=now,
    )

    batch = detect_completion_batch(
        {future.agent_id: AgentMode.WORKING},
        (future,),
        now,
    )

    assert batch.statuses == ()
    assert unseen_completions(snapshot, SimpleNamespace()) == []
    assert recent_statuses(snapshot) == []


def test_collector_staleness_rejects_implausibly_future_status() -> None:
    """Replacing bounded age with a clamped negative would pin live state."""
    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    future = _status(
        "codex:session:future",
        now + timedelta(minutes=10),
        mode=AgentMode.WORKING,
        event_name="PreToolUse",
    )

    assert status_is_stale(
        future,
        now,
        stale_after_seconds=3600.0,
        tool_running_timeout_seconds=0.0,
        completed_visible_seconds=1200.0,
        idle_visible_seconds=0.0,
    )


def test_attention_projection_rejects_future_permission_outside_collector() -> None:
    """A direct projection caller must not turn future input into attention."""
    from sidepulse.attention import project_attention
    from sidepulse.collector import MonitorSnapshot, aggregate_status

    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    future = _status(
        "codex:session:future",
        now + timedelta(minutes=10),
        mode=AgentMode.WAITING_FOR_INPUT,
        event_name="PermissionRequest",
    )
    snapshot = MonitorSnapshot(
        aggregate=aggregate_status((future,)),
        statuses=(future,),
        stale_statuses=(),
        sources=(),
        collected_at=now,
    )

    projection = project_attention(snapshot, AgentMonitorSettings())

    assert projection.actionable_attention == ()
    assert projection.click_target_agent_id is None


def test_attention_projection_excludes_implausibly_future_active_row() -> None:
    """Filtering only actionable rows would still let future work dominate."""
    from sidepulse.attention import LifecycleMode, project_attention
    from sidepulse.collector import MonitorSnapshot, aggregate_status

    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    future = _status(
        "codex:session:future",
        now + timedelta(minutes=10),
        mode=AgentMode.WORKING,
        event_name="PreToolUse",
    )
    snapshot = MonitorSnapshot(
        aggregate=aggregate_status((future,)),
        statuses=(future,),
        stale_statuses=(),
        sources=(),
        collected_at=now,
    )

    projection = project_attention(snapshot, AgentMonitorSettings())

    assert projection.visible_rows == ()
    assert projection.lifecycle_mode is LifecycleMode.IDLE


def test_attention_projection_excludes_future_failure_signal_but_accepts_small_skew() -> None:
    """Warm replay cannot pulse future failures, while small skew stays usable."""
    from sidepulse.attention import project_attention
    from sidepulse.collector import MonitorSnapshot, aggregate_status

    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    future_failure = _status(
        "codex:session:future",
        now + timedelta(minutes=10),
        mode=AgentMode.BLOCKED_ERROR,
        event_name="PostToolUseFailure",
    )
    skewed_ask = _status(
        "codex:session:skewed",
        now + timedelta(seconds=FUTURE_CLOCK_SKEW_SECONDS),
        mode=AgentMode.WAITING_FOR_INPUT,
        event_name="PermissionRequest",
    )
    snapshot = MonitorSnapshot(
        aggregate=aggregate_status((future_failure, skewed_ask)),
        statuses=(future_failure, skewed_ask),
        stale_statuses=(),
        sources=(),
        collected_at=now,
    )

    projection = project_attention(snapshot, AgentMonitorSettings())

    assert projection.transient_signals == ()
    assert tuple(row.agent_id for row in projection.visible_rows) == (
        skewed_ask.agent_id,
    )
    assert tuple(row.agent_id for row in projection.actionable_attention) == (
        skewed_ask.agent_id,
    )


def test_failed_latest_state_replace_keeps_dirty_state_and_write_time() -> None:
    """Clearing dirty before replace succeeds would silently lose persistence."""
    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "latest.json"
        monitor = LiveAgentMonitor(latest_state_path=state_path)
        monitor._latest_state_dirty = True
        monitor._latest_state_written_at = 123.0

        with (
            patch("sidepulse.collector.time.monotonic", return_value=456.0),
            patch(
                "sidepulse.private_io._replace_private_leaf",
                side_effect=OSError("replace failed"),
            ),
        ):
            monitor.write_latest_state()

        assert monitor._latest_state_dirty is True
        assert monitor._latest_state_written_at == 123.0
        assert not state_path.exists()


_RESTORE_SOURCE = SourceKey("codex", "hooks", "global", "live_agent_events")
_RESTORE_EPOCH = datetime(2026, 8, 13, 12, tzinfo=timezone.utc).timestamp()


def _restore_clock(
    *,
    wall: float = _RESTORE_EPOCH,
    monotonic: float = 100.0,
) -> ClockSample:
    return ClockSample(wall, monotonic, BootIdentifier("boot:restore-test"))


def _restore_batch(
    lifecycle: WorkLifecycle,
    sequence: int,
    *,
    epoch: float = _RESTORE_EPOCH,
) -> ProviderFactBatch:
    watermark = ProviderWatermark(
        _RESTORE_SOURCE,
        WatermarkBasis.PROVIDER_SEQUENCE,
        epoch,
        EventToken(f"event:restore:{sequence}"),
        sequence,
        10,
    )
    key = WorkKey(_RESTORE_SOURCE, WorkIdentifier("work:restore"))
    return ProviderFactBatch(
        _RESTORE_SOURCE,
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        SourceHealth.HEALTHY,
        SourceFreshness.FRESH,
        epoch,
        watermark,
        (
            ProviderWorkFact(
                key,
                lifecycle,
                watermark,
                "Codex work:restore",
                None,
                NextActor.PROVIDER,
            ),
        ),
        (),
        (),
    )


def test_wall_rollback_after_v2_restore_quarantines_new_truth_without_edges(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "latest.json"
    original = LiveAgentMonitor(
        latest_state_path=state_path,
        clock_sampler=lambda: _restore_clock(monotonic=101.0),
    )
    original.ingest_batch(
        _restore_batch(WorkLifecycle.ACTIVE, 1),
        clock=_restore_clock(monotonic=101.0),
    )
    original.snapshot()
    original.write_latest_state()
    restored = LiveAgentMonitor(
        latest_state_path=state_path,
        clock_sampler=lambda: _restore_clock(
            wall=_RESTORE_EPOCH - 100.0,
            monotonic=102.0,
        ),
    )

    restored.ingest_batch(
        _restore_batch(
            WorkLifecycle.COMPLETED,
            2,
            epoch=_RESTORE_EPOCH + 1.0,
        )
    )
    snapshot = restored.snapshot()

    assert snapshot.operator_events == ()
    assert snapshot.operator_state is not None
    assert snapshot.operator_state.works[0].lifecycle is WorkLifecycle.ACTIVE
    assert snapshot.operator_state.works[0].timing_uncertain is True
    assert snapshot.operator_state.works[0].source_freshness is (
        SourceFreshness.TIMING_UNCERTAIN
    )


def test_warm_identical_current_snapshot_never_replays_restored_edge(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "latest.json"
    batch = _restore_batch(WorkLifecycle.ACTIVE, 1)
    original = LiveAgentMonitor(
        latest_state_path=state_path,
        clock_sampler=lambda: _restore_clock(monotonic=101.0),
    )
    original.ingest_batch(batch, clock=_restore_clock(monotonic=101.0))
    original.snapshot()
    original.write_latest_state()
    restored = LiveAgentMonitor(
        latest_state_path=state_path,
        clock_sampler=lambda: _restore_clock(
            wall=_RESTORE_EPOCH + 1.0,
            monotonic=102.0,
        ),
    )

    restored.ingest_batch(batch)
    first = restored.snapshot()
    restored.ingest_batch(batch)
    second = restored.snapshot()

    assert first.operator_events == ()
    assert second.operator_events == ()
    assert first.operator_state is not None
    assert first.operator_state.works[0].lifecycle is WorkLifecycle.ACTIVE


def _write_codex_transcript(root: Path, index: int, *, mtime: float) -> Path:
    session_id = f"00000000-0000-7000-8000-{index:012x}"
    path = root / f"rollout-2026-08-12T12-00-00-{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "timestamp": "2026-08-12T12:00:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": str(index)}],
                },
            }
        )
        + "\n"
    )
    os.utime(path, (mtime, mtime))
    return path


def test_transcript_rotation_is_bounded_without_cross_root_eviction() -> None:
    """Global path pruning would incorrectly discard another active root."""
    with tempfile.TemporaryDirectory() as tmp:
        root_a = Path(tmp) / "a"
        root_b = Path(tmp) / "b"
        a_paths = [_write_codex_transcript(root_a, i, mtime=100.0 + i) for i in range(13)]
        b_paths = [_write_codex_transcript(root_b, 100 + i, mtime=200.0 + i) for i in range(2)]
        monitor = AgentMonitor(
            sources=(
                SourceSpec(CODEX_TRANSCRIPT_PROVIDER, root_a),
                SourceSpec(CODEX_TRANSCRIPT_PROVIDER, root_b),
            ),
            transcript_records_cache_max_entries=14,
            transcript_file_list_cache_max_entries=2,
        )

        with patch("sidepulse.collector.TRANSCRIPT_FILE_LIST_CACHE_SECONDS", 0.0):
            tuple(monitor.iter_records())
            newest = _write_codex_transcript(root_a, 99, mtime=1000.0)
            tuple(monitor.iter_records())

        cached_paths = {Path(key[1]) for key in monitor._transcript_records_cache}
        assert len(cached_paths) == 14
        assert set(b_paths) <= cached_paths
        assert newest in cached_paths
        assert set(a_paths[:2]).isdisjoint(cached_paths)


def test_transcript_file_list_cache_has_deterministic_count_bound() -> None:
    """Removing file-list eviction would grow one entry per discovered root."""
    with tempfile.TemporaryDirectory() as tmp:
        roots = [Path(tmp) / str(index) for index in range(3)]
        for root in roots:
            root.mkdir()
        monitor = AgentMonitor(
            transcript_file_list_cache_max_entries=2,
        )

        for root in roots:
            monitor._recent_transcript_files(
                root,
                limit=1,
                provider=CODEX_TRANSCRIPT_PROVIDER,
            )

        assert list(monitor._transcript_file_list_cache) == [
            (CODEX_TRANSCRIPT_PROVIDER, str(roots[1]), 1),
            (CODEX_TRANSCRIPT_PROVIDER, str(roots[2]), 1),
        ]


def test_transcript_file_list_cache_is_scoped_by_provider() -> None:
    """Sharing one root/limit key would skip pruning for the second provider."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        monitor = AgentMonitor(transcript_file_list_cache_max_entries=2)

        monitor._recent_transcript_files(
            root,
            limit=1,
            provider=CODEX_TRANSCRIPT_PROVIDER,
        )
        monitor._recent_transcript_files(
            root,
            limit=1,
            provider="claude-transcripts",
        )

        assert len(monitor._transcript_file_list_cache) == 2


def _usage_row(message_id: str, timestamp: str, tokens: int = 1) -> dict:
    return {
        "type": "assistant",
        "timestamp": timestamp,
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


def _write_usage_file(root: Path, name: str, rows: list[dict], *, mtime: float) -> Path:
    path = root / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    os.utime(path, (mtime, mtime))
    return path


def test_usage_cache_rotation_is_bounded_without_changing_current_totals() -> None:
    """Applying the cache cap before aggregation would undercount this scan."""
    from sidepulse.usage_stats import scan_usage

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "usage"
        cache = Path(tmp) / "cache.json"
        paths = [
            _write_usage_file(
                root,
                str(index),
                [_usage_row(f"m{index}", "2026-08-12T12:00:00Z", tokens=index + 1)],
                mtime=100.0 + index,
            )
            for index in range(5)
        ]

        first = scan_usage(root, cache, cache_max_files=3)
        assert first.input_tokens == 15
        assert len(first.records) == 5
        assert len(json.loads(cache.read_text())["files"]) == 3

        paths[-1].unlink()
        _write_usage_file(
            root,
            "replacement",
            [_usage_row("replacement", "2026-08-12T12:00:00Z", tokens=9)],
            mtime=1000.0,
        )
        second = scan_usage(root, cache, cache_max_files=3)

        assert second.input_tokens == 19
        cached_paths = set(json.loads(cache.read_text())["files"])
        assert len(cached_paths) == 3
        assert str(paths[-1]) not in cached_paths


def test_usage_cache_preserves_unwalked_roots_without_counting_them() -> None:
    """Treating an unwalked root as deleted loses valid warm cache state."""
    from sidepulse.usage_stats import scan_usage

    with tempfile.TemporaryDirectory() as tmp:
        root_a = Path(tmp) / "a"
        root_b = Path(tmp) / "b"
        cache = Path(tmp) / "cache.json"
        _write_usage_file(
            root_b,
            "b",
            [_usage_row("b", "2026-08-12T12:00:00Z", tokens=7)],
            mtime=100.0,
        )
        scan_usage(root_b, cache, cache_max_files=10)
        _write_usage_file(
            root_a,
            "a",
            [_usage_row("a", "2026-08-12T12:00:00Z", tokens=3)],
            mtime=200.0,
        )

        current = scan_usage(root_a, cache, cache_max_files=10)

        assert current.input_tokens == 3
        cached = json.loads(cache.read_text())
        assert len(cached["files"]) == 2
        assert str(root_a) not in cache.read_text()
        assert str(root_b) not in cache.read_text()


def test_usage_totals_records_exclude_rows_before_since_epoch() -> None:
    """Filtering totals but retaining old records bloats every downstream view."""
    from sidepulse.usage_stats import scan_usage

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_usage_file(
            root,
            "mixed",
            [
                _usage_row("old", "2026-08-12T11:00:00Z", tokens=100),
                _usage_row("new", "2026-08-12T13:00:00Z", tokens=3),
            ],
            mtime=100.0,
        )
        since = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc).timestamp()

        totals = scan_usage(root, since_epoch=since)

        assert totals.input_tokens == 3
        assert len(totals.records) == 1
        assert totals.records[0][8].startswith("message:")
        assert totals.records[0][8] != "new"


def test_pre_window_duplicate_does_not_suppress_in_window_usage() -> None:
    """Adding old dedupe keys to seen would undercount the requested window."""
    from sidepulse.usage_stats import scan_usage

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_usage_file(
            root,
            "mixed",
            [
                _usage_row("same", "2026-08-12T11:00:00Z", tokens=100),
                _usage_row("same", "2026-08-12T13:00:00Z", tokens=7),
            ],
            mtime=100.0,
        )
        since = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc).timestamp()

        totals = scan_usage(root, since_epoch=since)

        assert totals.input_tokens == 7
        assert len(totals.records) == 1
        assert totals.records[0][3] == datetime(
            2026, 8, 12, 13, 0, tzinfo=timezone.utc
        ).timestamp()


def test_usage_records_match_cross_file_deduped_totals() -> None:
    """Returning duplicate records would disagree with deduped totals."""
    from sidepulse.usage_stats import scan_usage

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        row = _usage_row("same", "2026-08-12T13:00:00Z", tokens=7)
        _write_usage_file(root, "a", [row], mtime=100.0)
        _write_usage_file(root, "b", [row], mtime=101.0)
        since = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc).timestamp()

        totals = scan_usage(root, since_epoch=since)

        assert totals.input_tokens == 7
        assert len(totals.records) == 1


def test_failed_usage_read_does_not_cache_a_durable_empty_result() -> None:
    """A failed read must not look like a valid empty warm cache on the next scan."""
    import sidepulse.usage_stats as usage_stats

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "usage"
        cache = Path(tmp) / "cache.json"
        _write_usage_file(
            root,
            "a",
            [_usage_row("msg", "2026-08-13T12:00:00Z", tokens=7)],
            mtime=100.0,
        )

        with patch(
            "sidepulse.usage_stats._read_verified_prefix",
            return_value=None,
        ):
            failed = usage_stats.scan_usage(root, cache)

        assert failed.source_coverage["claude"].status is usage_stats.UsageSourceStatus.FAILED
        if cache.exists():
            assert json.loads(cache.read_text())["files"] == {}

        recovered = usage_stats.scan_usage(root, cache)

        assert recovered.input_tokens == 7
        assert recovered.source_coverage["claude"].files_read == 1
        assert recovered.source_coverage["claude"].cache_hits == 0


def test_bare_session_starts_never_reach_the_recent_fallback() -> None:
    """A session that only ever emitted SessionStart is a CLI launch
    (shell completions, --version), not work -- listing it in the
    stale-only fallback read as "grok is running" with no session."""
    from sidepulse.status_bar import recent_statuses

    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    bare_start = _status(
        "codex:session:bare",
        now - timedelta(minutes=5),
        mode=AgentMode.IDLE_READY,
        event_name="SessionStart",
    )
    real_work = _status(
        "codex:session:real",
        now - timedelta(minutes=50),
        mode=AgentMode.WORKING,
        event_name="PostToolUse",
    )
    snapshot = SimpleNamespace(
        statuses=(),
        stale_statuses=(bare_start, real_work),
        collected_at=now,
    )

    rows = recent_statuses(snapshot)

    assert [row.agent_id for row in rows] == ["codex:session:real"]
