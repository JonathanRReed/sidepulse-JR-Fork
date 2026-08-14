from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sidepulse.capacity_types import SourceKey
from sidepulse.collector import (
    LATEST_STATE_MAX_BYTES,
    AgentMonitor,
    LiveAgentMonitor,
    RestoreHealth,
    SourceSpec,
    agent_status_from_canonical_work,
)
from sidepulse.models import AgentMode, HookEvent
from sidepulse.operator_state import BootIdentifier, ClockSample, TransitionKind
from sidepulse.provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderFactBatch,
    ProviderRequestFact,
    ProviderRequestState,
    ProviderWatermark,
    ProviderWorkFact,
    RequestIdentifier,
    RequestKey,
    RequestKind,
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
    WorkLifecycle,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
SOURCE = SourceKey("codex", "hooks", "global", "live_agent_events")


def _clock(wall: float = NOW.timestamp(), monotonic: float = 100.0) -> ClockSample:
    return ClockSample(wall, monotonic, BootIdentifier("boot:test"))


def _watermark(sequence: int, *, token: str | None = None) -> ProviderWatermark:
    return ProviderWatermark(
        source_key=SOURCE,
        basis=WatermarkBasis.PROVIDER_SEQUENCE,
        occurred_at_epoch=NOW.timestamp(),
        event_token=EventToken(token or f"event:{sequence}"),
        sequence=sequence,
        tie_break_rank=10,
    )


def _batch(
    lifecycle: WorkLifecycle,
    sequence: int,
    *,
    authority: ObservationAuthority = ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    work_id: str = "work:one",
    request_live: bool = False,
) -> ProviderFactBatch:
    watermark = _watermark(sequence)
    work_key = WorkKey(SOURCE, WorkIdentifier(work_id))
    request_facts = ()
    if request_live:
        request_facts = (
            ProviderRequestFact(
                RequestKey(work_key, RequestIdentifier("request:one")),
                ProviderRequestState.LIVE,
                RequestKind.PERMISSION,
                NextActor.USER,
                watermark,
            ),
        )
    return ProviderFactBatch(
        source_key=SOURCE,
        observation_authority=authority,
        source_health=SourceHealth.HEALTHY,
        source_freshness=SourceFreshness.FRESH,
        observed_at_epoch=watermark.occurred_at_epoch,
        watermark=watermark,
        work_facts=(
            ProviderWorkFact(
                work_key,
                lifecycle,
                watermark,
                f"Codex {work_id}",
                None,
                NextActor.USER if request_live else NextActor.PROVIDER,
            ),
        ),
        request_facts=request_facts,
        diagnostics=(),
    )


def test_live_monitor_has_one_canonical_authority_and_emits_edges_once() -> None:
    monitor = LiveAgentMonitor(clock_sampler=lambda: _clock())

    monitor.ingest_batch(_batch(WorkLifecycle.ACTIVE, 1), clock=_clock(monotonic=101.0))
    first = monitor.snapshot()
    repeated = monitor.snapshot()
    monitor.ingest_batch(
        _batch(WorkLifecycle.COMPLETED, 2),
        clock=_clock(monotonic=102.0),
    )
    completed = monitor.snapshot()

    assert not hasattr(monitor, "statuses_by_key")
    assert first.operator_state is monitor.operator_state or first.operator_state is not None
    assert first.statuses[0].work_key == WorkKey(SOURCE, WorkIdentifier("work:one"))
    assert first.statuses[0].request_key is None
    assert first.statuses[0].mode is AgentMode.WORKING
    assert tuple(event.kind for event in first.operator_events) == (
        TransitionKind.BECAME_ACTIVE,
    )
    assert repeated.operator_events == ()
    assert tuple(event.kind for event in completed.operator_events) == (
        TransitionKind.COMPLETED,
    )
    assert monitor.snapshot().operator_events == ()


def test_lower_authority_fallback_cannot_override_direct_truth() -> None:
    monitor = LiveAgentMonitor(clock_sampler=lambda: _clock())
    monitor.ingest_batch(_batch(WorkLifecycle.ACTIVE, 10), clock=_clock(monotonic=110.0))
    monitor.snapshot()

    monitor.ingest_batch(
        _batch(
            WorkLifecycle.COMPLETED,
            11,
            authority=ObservationAuthority.FALLBACK_OBSERVATION,
        ),
        clock=_clock(monotonic=111.0),
    )
    snapshot = monitor.snapshot()

    assert snapshot.statuses[0].mode is AgentMode.WORKING
    assert snapshot.operator_events == ()
    assert snapshot.operator_state is not None
    assert snapshot.operator_state.works[0].observation_authority is (
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION
    )


def test_real_transcript_fallback_record_cannot_override_direct_truth() -> None:
    monitor = LiveAgentMonitor(
        clock_sampler=lambda: _clock(
            wall=NOW.timestamp() + 2.0,
            monotonic=112.0,
        ),
    )
    monitor.ingest_batch(
        _batch(WorkLifecycle.ACTIVE, 10),
        clock=_clock(monotonic=110.0),
    )
    monitor.snapshot()

    monitor.ingest_record(
        HookEvent(
            provider="codex",
            logged_at=NOW + timedelta(seconds=1),
            event_name="Stop",
            raw={
                "hook_event_name": "Stop",
                "session_id": "work:one",
                "event_id": "event:11",
                "sequence": 11,
                "source": "codex-transcripts",
            },
            session_id="work:one",
        )
    )
    snapshot = monitor.snapshot()

    assert snapshot.operator_events == ()
    assert snapshot.operator_state is not None
    assert snapshot.operator_state.works[0].lifecycle is WorkLifecycle.ACTIVE
    assert snapshot.operator_state.works[0].observation_authority is (
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION
    )


def test_canonical_projection_derives_content_free_legacy_fields_and_typed_keys() -> None:
    monitor = LiveAgentMonitor(clock_sampler=lambda: _clock())
    monitor.ingest_batch(
        _batch(WorkLifecycle.WAITING, 1, request_live=True),
        clock=_clock(monotonic=101.0),
    )
    work = monitor.operator_state.works[0]

    status = agent_status_from_canonical_work(work)
    snapshot_status = monitor.snapshot().statuses[0]

    assert status.work_key == work.key
    assert status.request_key == work.request_keys[0]
    assert status.mode is AgentMode.WAITING_FOR_INPUT
    assert status.event_name == "PermissionRequest"
    assert status.cwd is status.tool_name is status.message is status.origin is None
    assert snapshot_status == status


def test_one_thousand_works_and_requests_are_bounded_and_deterministic() -> None:
    watermark = _watermark(1)
    work_keys = tuple(
        WorkKey(SOURCE, WorkIdentifier(f"work:{index:04d}")) for index in range(1_000)
    )
    work_facts = tuple(
        ProviderWorkFact(
            key,
            WorkLifecycle.WAITING,
            watermark,
            f"Codex {key.work_id.value}",
            None,
            NextActor.USER,
        )
        for key in reversed(work_keys)
    )
    request_facts = tuple(
        ProviderRequestFact(
            RequestKey(key, RequestIdentifier(f"request:{index:04d}")),
            ProviderRequestState.LIVE,
            RequestKind.PERMISSION,
            NextActor.USER,
            watermark,
        )
        for index, key in reversed(tuple(enumerate(work_keys)))
    )
    batch = ProviderFactBatch(
        SOURCE,
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        SourceHealth.HEALTHY,
        SourceFreshness.FRESH,
        watermark.occurred_at_epoch,
        watermark,
        work_facts,
        request_facts,
        (),
    )
    first = LiveAgentMonitor(clock_sampler=lambda: _clock())
    second = LiveAgentMonitor(clock_sampler=lambda: _clock())

    first.ingest_batch(batch, clock=_clock(monotonic=101.0))
    second.ingest_batch(batch, clock=_clock(monotonic=101.0))

    assert len(first.operator_state.works) == 1_000
    assert len(first.operator_state.requests) == 1_000
    assert first.operator_state == second.operator_state
    assert tuple(work.key for work in first.operator_state.works) == work_keys


def test_v2_restore_is_metadata_only_restored_and_edge_free(tmp_path: Path) -> None:
    path = tmp_path / "latest.json"
    monitor = LiveAgentMonitor(
        latest_state_path=path,
        clock_sampler=lambda: _clock(monotonic=101.0),
    )
    monitor.ingest_batch(
        _batch(WorkLifecycle.WAITING, 1, request_live=True),
        clock=_clock(monotonic=101.0),
    )
    monitor.snapshot()
    monitor.write_latest_state()

    raw = path.read_text()
    document = json.loads(raw)
    forbidden = (
        "display_name",
        "cwd",
        "message",
        "tool_name",
        "origin",
        "navigation",
        "delivery",
        "raw_error",
        "account_label",
    )
    assert document["version"] == 2
    assert all(field not in raw for field in forbidden)

    restored = LiveAgentMonitor(
        latest_state_path=path,
        clock_sampler=lambda: _clock(monotonic=102.0),
    )
    snapshot = restored.snapshot()

    assert snapshot.restore_health is RestoreHealth.HEALTHY
    assert snapshot.operator_events == ()
    assert snapshot.operator_state is not None
    assert snapshot.operator_state.works[0].observation_authority is (
        ObservationAuthority.RESTORED_LAST_KNOWN
    )
    assert snapshot.operator_state.works[0].source_freshness is SourceFreshness.RESTORED


def test_v2_restore_rejects_dangling_request_authority(tmp_path: Path) -> None:
    path = tmp_path / "latest.json"
    monitor = LiveAgentMonitor(
        latest_state_path=path,
        clock_sampler=lambda: _clock(monotonic=101.0),
    )
    monitor.ingest_batch(
        _batch(WorkLifecycle.WAITING, 1, request_live=True),
        clock=_clock(monotonic=101.0),
    )
    monitor.write_latest_state()

    document = json.loads(path.read_text())
    document["requests"] = []
    path.write_text(json.dumps(document))

    restored = LiveAgentMonitor(
        latest_state_path=path,
        clock_sampler=lambda: _clock(monotonic=102.0),
    )
    snapshot = restored.snapshot()

    assert snapshot.restore_health is RestoreHealth.CORRUPT
    assert snapshot.operator_state is not None
    assert snapshot.operator_state.works == ()
    assert snapshot.operator_events == ()


def _legacy_document(agent_id: str = "codex:session:work:one") -> dict[str, object]:
    return {
        "updated_at": NOW.isoformat(),
        "statuses": [
            {
                "provider": "codex",
                "agent_id": agent_id,
                "mode": "working",
                "updated_at": NOW.isoformat(),
            }
        ],
    }


def test_v1_migrates_only_one_exact_current_work_key_without_edges(tmp_path: Path) -> None:
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(_legacy_document()))
    exact = WorkKey(SOURCE, WorkIdentifier("work:one"))

    monitor = LiveAgentMonitor(
        latest_state_path=path,
        restore_work_keys=(exact,),
        clock_sampler=lambda: _clock(),
    )
    snapshot = monitor.snapshot()

    assert snapshot.restore_health is RestoreHealth.HEALTHY
    assert snapshot.operator_events == ()
    assert tuple(work.key for work in monitor.operator_state.works) == (exact,)
    assert monitor.operator_state.works[0].observation_authority is (
        ObservationAuthority.RESTORED_LAST_KNOWN
    )
    assert monitor.operator_state.works[0].source_freshness is SourceFreshness.RESTORED


def test_v1_zero_and_ambiguous_matches_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(_legacy_document()))
    hook_key = WorkKey(SOURCE, WorkIdentifier("work:one"))
    transcript_key = WorkKey(
        SourceKey("codex", "transcripts", "local", "live_agent_events"),
        WorkIdentifier("work:one"),
    )

    missing = LiveAgentMonitor(
        latest_state_path=path,
        restore_work_keys=(),
        clock_sampler=lambda: _clock(),
    )
    ambiguous = LiveAgentMonitor(
        latest_state_path=path,
        restore_work_keys=(hook_key, transcript_key),
        clock_sampler=lambda: _clock(),
    )

    assert missing.restore_health is RestoreHealth.DEGRADED
    assert missing.operator_state.works == ()
    assert missing.snapshot().operator_events == ()
    assert ambiguous.restore_health is RestoreHealth.DEGRADED
    assert ambiguous.operator_state.works == ()
    assert ambiguous.snapshot().operator_events == ()


def test_corrupt_unsupported_and_oversize_restore_are_visible(tmp_path: Path) -> None:
    path = tmp_path / "latest.json"
    cases = (
        ("{", RestoreHealth.CORRUPT),
        (json.dumps({"version": 99}), RestoreHealth.UNSUPPORTED),
        ("x" * (LATEST_STATE_MAX_BYTES + 1), RestoreHealth.UNAVAILABLE),
    )
    for raw, expected in cases:
        path.write_text(raw)
        monitor = LiveAgentMonitor(
            latest_state_path=path,
            clock_sampler=lambda: _clock(),
        )
        snapshot = monitor.snapshot()
        assert snapshot.restore_health is expected
        assert snapshot.aggregate.mode is AgentMode.UNKNOWN
        assert snapshot.operator_events == ()


def test_offline_collector_uses_canonical_reduction_not_legacy_status_map(
    tmp_path: Path,
) -> None:
    log = tmp_path / "codex.jsonl"
    log.write_text(
        json.dumps(
            {
                "logged_at": NOW.isoformat(),
                "event": {
                    "hook_event_name": "PreToolUse",
                    "session_id": "work:offline",
                    "tool_name": "private-tool",
                    "cwd": "/private/path",
                    "prompt": "private prompt",
                },
            }
        )
        + "\n"
    )
    monitor = AgentMonitor(
        sources=(SourceSpec("codex", log),),
        clock_sampler=lambda: _clock(),
    )

    first = monitor.snapshot()
    repeated = monitor.snapshot()

    assert first.operator_state is not None
    assert first.statuses[0].work_key is not None
    assert first.statuses[0].tool_name is None
    assert first.statuses[0].cwd is None
    assert tuple(event.kind for event in first.operator_events) == (
        TransitionKind.BECAME_ACTIVE,
    )
    assert repeated.operator_events == ()


def test_offline_reduction_does_not_replay_old_edges_when_log_grows(
    tmp_path: Path,
) -> None:
    log = tmp_path / "codex.jsonl"
    active = {
        "logged_at": NOW.isoformat(),
        "event": {
            "hook_event_name": "PreToolUse",
            "session_id": "work:offline",
            "event_id": "event:1",
            "sequence": 1,
        },
    }
    completed = {
        "logged_at": (NOW + timedelta(seconds=1)).isoformat(),
        "event": {
            "hook_event_name": "Stop",
            "session_id": "work:offline",
            "event_id": "event:2",
            "sequence": 2,
        },
    }
    log.write_text(json.dumps(active) + "\n")
    monitor = AgentMonitor(
        sources=(SourceSpec("codex", log),),
        clock_sampler=lambda: _clock(wall=NOW.timestamp() + 2.0),
    )

    assert tuple(event.kind for event in monitor.snapshot().operator_events) == (
        TransitionKind.BECAME_ACTIVE,
    )
    with log.open("a") as handle:
        handle.write(json.dumps(completed) + "\n")

    assert tuple(event.kind for event in monitor.snapshot().operator_events) == (
        TransitionKind.COMPLETED,
    )


def test_unknown_live_event_degrades_existing_source_without_changing_truth() -> None:
    monitor = LiveAgentMonitor(
        clock_sampler=lambda: _clock(
            wall=NOW.timestamp() + 2.0,
            monotonic=103.0,
        ),
    )
    monitor.ingest_batch(
        _batch(WorkLifecycle.ACTIVE, 1),
        clock=_clock(monotonic=101.0),
    )
    monitor.snapshot()

    monitor.ingest_record(
        HookEvent(
            provider="codex",
            logged_at=NOW + timedelta(seconds=1),
            event_name="UnknownProviderEvent",
            raw={
                "event_id": "event:unknown",
                "sequence": 2,
            },
            session_id="work:one",
        )
    )
    snapshot = monitor.snapshot()

    assert snapshot.operator_state is not None
    assert snapshot.operator_state.works[0].lifecycle is WorkLifecycle.ACTIVE
    assert snapshot.operator_state.works[0].source_health is SourceHealth.PARTIAL
    assert tuple(event.kind for event in snapshot.operator_events) == (
        TransitionKind.SOURCE_DEGRADED,
    )
