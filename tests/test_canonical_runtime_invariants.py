from __future__ import annotations

import json
import os
import socket
import stat
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sidepulse.audit import TRIM_THRESHOLD_BYTES
from sidepulse.capacity_types import (
    CapacityUnit,
    CapacityValue,
    ObservationState,
    QuotaEffect,
    QuotaLaneKey,
    ResetState,
    SourceKey,
)
from sidepulse.collector import LiveAgentMonitor, RestoreHealth
from sidepulse.delivery_ledger import (
    DeliveryChannel,
    DeliveryDiagnostic,
    DeliveryDisposition,
    DeliveryKey,
    DeliveryLedger,
    DeliveryReceipt,
    record_delivery,
)
from sidepulse.delivery_ledger_store import load_delivery_ledger, save_delivery_ledger
from sidepulse.hook import write_normalized_hook_record
from sidepulse.ipc import (
    MAX_HINT_BYTES,
    HookEventServer,
    ProviderRefreshHint,
    _hint_from_wire,
    send_refresh_hint,
)
from sidepulse.models import HookEvent
from sidepulse.operator_state import (
    BootIdentifier,
    ClockContinuityStatus,
    ClockSample,
    InvalidationDomain,
    TransitionKind,
    empty_operator_state,
    reduce_operator_state,
)
from sidepulse.provider_adapters import (
    minimize_hook_event,
    normalized_provider_record_to_payload,
    provider_facts_for_record,
)
from sidepulse.provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderFactBatch,
    ProviderFactValidationError,
    ProviderQuotaWindow,
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
from sidepulse.provider_runtime import (
    CooldownKey,
    OpaqueScopeIdentifier,
    ProviderInvocation,
    ProviderOutcomeKind,
    ProviderResult,
    ProviderRuntime,
    ProviderRuntimeDiagnostic,
    RefreshTrigger,
)
from sidepulse.providers import negotiated_provider_sources
from sidepulse.reset_policy import plan_reset_boundary_refresh
from sidepulse.usage_view import UsageWindowViewModel

_BASE = 1_786_536_000.0
_BOOT = BootIdentifier("boot:canonical")
_CODEX = SourceKey("codex", "hooks", "global", "live_agent_events")
_CLAUDE = SourceKey("claude", "hooks", "global", "live_agent_events")
_DEVIN = SourceKey("devin", "hooks", "global", "live_agent_events")
_FUTURE = SourceKey("futureai", "hooks", "global", "live_agent_events")
_PRIVACY_CORPUS = (
    "PRIVATE-PROMPT-SENTINEL",
    "PRIVATE-RESPONSE-SENTINEL",
    "PRIVATE-TOOL-ARGUMENT-SENTINEL",
    "PRIVATE-COMMAND-SENTINEL",
    "/Users/private/PRIVATE-ABSOLUTE-PATH",
    "private-account@example.invalid",
    "PRIVATE-ACCOUNT-LABEL-SENTINEL",
    "Bearer PRIVATE-BEARER-TOKEN",
    "session=PRIVATE-COOKIE-SENTINEL",
    "PRIVATE-RAW-ERROR-SENTINEL",
    "https://example.invalid/PRIVATE-URL-SENTINEL",
    "|||PRIVATE-DELIMITER-SENTINEL|||",
    "PRIVATE\aCONTROL-SENTINEL",
    "P" * 1_048_576,
)


@dataclass(frozen=True, slots=True)
class _StepReceipt:
    number: int
    work_count: int
    request_phases: tuple[str, ...]
    event_kinds: tuple[str, ...]
    interruption_classes: tuple[str, ...]
    delivery_dispositions: tuple[str, ...]
    source_health: tuple[str, ...]
    refresh_invocations: tuple[str, ...]
    reset_lanes: tuple[str, ...]
    invalidations: tuple[str, ...]
    restarted: bool
    event_keys: tuple[str, ...] = ()
    source_observations: tuple[str, ...] = ()
    runtime_publications: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _ScaleReceipt:
    primary_count: int
    worker_count: int
    request_count: int
    unique_work_count: int
    unique_request_count: int
    stable_parent_count: int
    opened_edge_count: int
    completed_edge_count: int
    resolved_edge_count: int
    final_sequence: int
    final_lifecycle: str
    elapsed_seconds: float


def _clock(offset: float, monotonic: float) -> ClockSample:
    return ClockSample(_BASE + offset, monotonic, _BOOT)


def _watermark(
    source: SourceKey,
    sequence: int,
    *,
    epoch_offset: float | None = None,
) -> ProviderWatermark:
    offset = float(sequence) if epoch_offset is None else epoch_offset
    return ProviderWatermark(
        source,
        WatermarkBasis.PROVIDER_SEQUENCE,
        _BASE + offset,
        EventToken(f"event:{source.provider_id}:{sequence}"),
        sequence,
        10,
    )


def _work(source: SourceKey, value: str) -> WorkKey:
    return WorkKey(source, WorkIdentifier(value))


def _request(work_key: WorkKey, value: str = "request:one") -> RequestKey:
    return RequestKey(work_key, RequestIdentifier(value))


def _label(key: WorkKey) -> str:
    return f"{key.source_key.provider_id.title()} {key.work_id.value}"


def _batch(
    source: SourceKey,
    sequence: int,
    *,
    work_key: WorkKey | None = None,
    lifecycle: WorkLifecycle | None = None,
    parent_key: WorkKey | None = None,
    request_key: RequestKey | None = None,
    request_state: ProviderRequestState | None = None,
    health: SourceHealth = SourceHealth.HEALTHY,
    freshness: SourceFreshness = SourceFreshness.FRESH,
    authority: ObservationAuthority = ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    epoch_offset: float | None = None,
) -> ProviderFactBatch:
    watermark = _watermark(source, sequence, epoch_offset=epoch_offset)
    work_facts = ()
    if work_key is not None and lifecycle is not None:
        work_facts = (
            ProviderWorkFact(
                work_key,
                lifecycle,
                watermark,
                _label(work_key),
                parent_key,
                NextActor.USER
                if request_state is ProviderRequestState.LIVE
                else NextActor.PROVIDER
                if lifecycle is WorkLifecycle.ACTIVE
                else NextActor.NONE,
            ),
        )
    request_facts = ()
    if request_key is not None and request_state is not None:
        request_facts = (
            ProviderRequestFact(
                request_key,
                request_state,
                RequestKind.PERMISSION,
                NextActor.USER
                if request_state is ProviderRequestState.LIVE
                else NextActor.NONE,
                watermark,
            ),
        )
    return ProviderFactBatch(
        source,
        authority,
        health,
        freshness,
        watermark.occurred_at_epoch,
        watermark,
        work_facts,
        request_facts,
        (),
    )


def _receipt(
    number: int,
    monitor: LiveAgentMonitor,
    *,
    events=(),
    ledger: DeliveryLedger = DeliveryLedger(()),
    health_extra: tuple[str, ...] = (),
    refresh_invocations: tuple[str, ...] = (),
    reset_lanes: tuple[str, ...] = (),
    invalidations: frozenset[InvalidationDomain] = frozenset(),
    restarted: bool = False,
    source_observations: tuple[str, ...] = (),
    runtime_publications: tuple[str, ...] = (),
) -> _StepReceipt:
    state = monitor.operator_state

    def event_key(event) -> str:
        subject = event.subject_key
        if type(subject) is WorkKey:
            identity = (
                f"work:{subject.source_key.provider_id}:{subject.work_id.value}"
            )
        else:
            identity = (
                f"request:{subject.work_key.source_key.provider_id}:"
                f"{subject.work_key.work_id.value}:{subject.request_id.value}"
            )
        return (
            f"{identity}|{event.kind.value}|"
            f"{event.key.provider_watermark.sequence}"
        )

    return _StepReceipt(
        number=number,
        work_count=len(state.works),
        request_phases=tuple(request.phase.value for request in state.requests),
        event_kinds=tuple(event.kind.value for event in events),
        interruption_classes=tuple(event.interruption_class.value for event in events),
        delivery_dispositions=tuple(
            item.disposition.value for item in ledger.receipts
        ),
        source_health=tuple(
            sorted(
                {
                    f"{work.key.source_key.provider_id}:{work.source_health.value}"
                    for work in state.works
                }
                | set(health_extra)
            )
        ),
        refresh_invocations=refresh_invocations,
        reset_lanes=reset_lanes,
        invalidations=tuple(sorted(domain.value for domain in invalidations)),
        restarted=restarted,
        event_keys=tuple(event_key(event) for event in events),
        source_observations=source_observations,
        runtime_publications=runtime_publications,
    )


def _ingest(
    monitor: LiveAgentMonitor,
    batch: ProviderFactBatch,
    *,
    clock: ClockSample,
):
    result = reduce_operator_state(monitor.operator_state, batch, clock=clock)
    monitor.ingest_batch(batch, clock=clock)
    return result


def _poll_runtime(
    runtime: ProviderRuntime,
    *,
    wanted: int,
    monotonic_now: float | None = None,
) -> tuple[ProviderResult, ...]:
    results: list[ProviderResult] = []
    deadline = time.monotonic() + 2.0
    while len(results) < wanted and time.monotonic() < deadline:
        results.extend(
            runtime.poll(
                monotonic_now=(
                    time.monotonic() if monotonic_now is None else monotonic_now
                )
            )
        )
    assert len(results) == wanted
    return tuple(results)


def _run_composed_fixture(root: Path) -> tuple[_StepReceipt, ...]:
    """Run the exact Task 9 sequence through the canonical source runtime."""
    state_path = root / "state" / "latest.json"
    ledger_path = root / "state" / "delivery-ledger.json"
    codex_work = _work(_CODEX, "work:codex")
    claude_work = _work(_CLAUDE, "work:claude")
    claude_request = _request(claude_work)
    devin_work = _work(_DEVIN, "work:devin")

    seed = LiveAgentMonitor(
        latest_state_path=state_path,
        clock_sampler=lambda: _clock(5.0, 105.0),
    )
    seed.ingest_batch(
        _batch(
            _CODEX,
            5,
            work_key=codex_work,
            lifecycle=WorkLifecycle.COMPLETED,
        ),
        clock=_clock(5.0, 105.0),
    )
    seed.ingest_batch(
        _batch(
            _CLAUDE,
            5,
            work_key=claude_work,
            lifecycle=WorkLifecycle.WAITING,
            request_key=claude_request,
            request_state=ProviderRequestState.LIVE,
        ),
        clock=_clock(5.0, 105.0),
    )
    seed.write_latest_state()

    monitor = LiveAgentMonitor(
        latest_state_path=state_path,
        clock_sampler=lambda: _clock(6.0, 106.0),
    )
    restored = monitor.snapshot()
    assert restored.restore_health is RestoreHealth.HEALTHY
    assert restored.operator_events == ()
    receipts = [_receipt(1, monitor, restarted=True)]

    before_hint = monitor.operator_state
    monitor.reconcile_refresh_hint(
        ProviderRefreshHint(_CODEX, EventToken("event:codex:hint")),
        log_path=root / "state" / "not-published.jsonl",
    )
    assert monitor.operator_state == before_hint
    receipts.append(_receipt(2, monitor))

    step3_events = []
    step3_invalidations: set[InvalidationDomain] = set()
    for batch, clock in (
        (
            _batch(
                _CODEX,
                6,
                work_key=codex_work,
                lifecycle=WorkLifecycle.ACTIVE,
            ),
            _clock(6.0, 106.0),
        ),
        (
            _batch(
                _CLAUDE,
                6,
                work_key=claude_work,
                lifecycle=WorkLifecycle.WAITING,
                request_key=claude_request,
                request_state=ProviderRequestState.LIVE,
            ),
            _clock(6.1, 106.1),
        ),
        (
            _batch(
                _DEVIN,
                1,
                work_key=devin_work,
                lifecycle=WorkLifecycle.ACTIVE,
                epoch_offset=6.2,
            ),
            _clock(6.2, 106.2),
        ),
    ):
        result = _ingest(monitor, batch, clock=clock)
        step3_events.extend(result.events)
        step3_invalidations.update(result.invalidations)
    unknown = _batch(
        _FUTURE,
        1,
        health=SourceHealth.PARTIAL,
        freshness=SourceFreshness.PARTIAL,
        epoch_offset=6.3,
    )
    assert unknown.work_facts == unknown.request_facts == ()
    unknown_result = _ingest(
        monitor,
        unknown,
        clock=_clock(6.3, 106.3),
    )
    assert unknown_result.events == ()
    assert unknown.source_health is SourceHealth.PARTIAL
    assert _FUTURE in dict(unknown_result.state.source_watermarks)
    assert _FUTURE in unknown_result.state.timing_uncertain_sources
    assert InvalidationDomain.SOURCE_HEALTH in unknown_result.invalidations
    step3_invalidations.update(unknown_result.invalidations)
    receipts.append(
        _receipt(
            3,
            monitor,
            events=tuple(step3_events),
            health_extra=(
                f"{unknown.source_key.provider_id}:{unknown.source_health.value}",
            ),
            invalidations=frozenset(step3_invalidations),
            source_observations=(
                f"{unknown.source_key.provider_id}:{unknown.source_health.value}:"
                f"zero_fact:uncertain:no_edge:source_health_invalidated",
            ),
        )
    )

    request_event = monitor.operator_state.requests[0].semantic_event_key
    ledger = DeliveryLedger(())
    for channel, disposition, diagnostic in (
        (DeliveryChannel.MAILBOX_CUE, DeliveryDisposition.DELIVERED, None),
        (
            DeliveryChannel.SYSTEM_NOTIFICATION,
            DeliveryDisposition.SUPPRESSED_QUIET,
            None,
        ),
        (
            DeliveryChannel.SOUND,
            DeliveryDisposition.FAILED,
            DeliveryDiagnostic.CHANNEL_UNAVAILABLE,
        ),
    ):
        ledger = record_delivery(
            ledger,
            DeliveryReceipt(
                DeliveryKey(request_event, channel, 0),
                disposition,
                _BASE + 7.0,
                1,
                diagnostic,
            ),
        )
    receipts.append(_receipt(4, monitor, ledger=ledger))

    older = _ingest(
        monitor,
        _batch(
            _CODEX,
            5,
            work_key=codex_work,
            lifecycle=WorkLifecycle.COMPLETED,
        ),
        clock=_clock(7.0, 107.0),
    )
    receipts.append(
        _receipt(5, monitor, events=older.events, ledger=ledger, invalidations=older.invalidations)
    )

    completed = _ingest(
        monitor,
        _batch(
            _CODEX,
            7,
            work_key=codex_work,
            lifecycle=WorkLifecycle.COMPLETED,
            epoch_offset=8.0,
        ),
        clock=_clock(8.0, 108.0),
    )
    receipts.append(
        _receipt(
            6,
            monitor,
            events=completed.events,
            ledger=ledger,
            invalidations=completed.invalidations,
        )
    )

    rollback = _ingest(
        monitor,
        _batch(
            _CODEX,
            8,
            work_key=codex_work,
            lifecycle=WorkLifecycle.COMPLETED,
            epoch_offset=3.0,
        ),
        clock=_clock(3.0, 109.0),
    )
    receipts.append(
        _receipt(
            7,
            monitor,
            events=rollback.events,
            ledger=ledger,
            invalidations=rollback.invalidations,
        )
    )

    stale = _ingest(
        monitor,
        _batch(
            _CLAUDE,
            7,
            health=SourceHealth.TIMED_OUT,
            freshness=SourceFreshness.STALE,
            epoch_offset=4.0,
        ),
        clock=_clock(4.0, 110.0),
    )
    receipts.append(
        _receipt(
            8,
            monitor,
            events=stale.events,
            ledger=ledger,
            invalidations=stale.invalidations,
        )
    )

    monitor.write_latest_state()
    save_delivery_ledger(ledger_path, ledger)
    monitor = LiveAgentMonitor(
        latest_state_path=state_path,
        clock_sampler=lambda: _clock(4.0, 110.0),
    )
    ledger_restore = load_delivery_ledger(ledger_path)
    restart_snapshot = monitor.snapshot()
    assert restart_snapshot.operator_events == ()
    assert ledger_restore.ledger == ledger
    receipts.append(_receipt(9, monitor, ledger=ledger_restore.ledger, restarted=True))

    step10_events = []
    step10_invalidations: set[InvalidationDomain] = set()
    recovery = (
        (
            _batch(
                _CODEX,
                8,
                work_key=codex_work,
                lifecycle=WorkLifecycle.COMPLETED,
                epoch_offset=5.0,
            ),
            _clock(5.0, 111.0),
        ),
        (
            _batch(
                _CODEX,
                9,
                work_key=codex_work,
                lifecycle=WorkLifecycle.COMPLETED,
                epoch_offset=6.0,
            ),
            _clock(6.0, 112.0),
        ),
        (
            _batch(
                _CLAUDE,
                8,
                work_key=claude_work,
                lifecycle=WorkLifecycle.WAITING,
                request_key=claude_request,
                request_state=ProviderRequestState.LIVE,
                epoch_offset=7.0,
            ),
            _clock(7.0, 113.0),
        ),
        (
            _batch(
                _CLAUDE,
                9,
                work_key=claude_work,
                lifecycle=WorkLifecycle.IDLE,
                request_key=claude_request,
                request_state=ProviderRequestState.RESOLVED,
                epoch_offset=8.0,
            ),
            _clock(8.0, 114.0),
        ),
        (
            _batch(
                _DEVIN,
                2,
                work_key=devin_work,
                lifecycle=WorkLifecycle.ACTIVE,
                epoch_offset=9.0,
            ),
            _clock(9.0, 115.0),
        ),
        (
            _batch(
                _DEVIN,
                3,
                work_key=devin_work,
                lifecycle=WorkLifecycle.ACTIVE,
                epoch_offset=10.0,
            ),
            _clock(10.0, 116.0),
        ),
        (
            _batch(
                _FUTURE,
                2,
                epoch_offset=11.0,
            ),
            _clock(11.0, 117.0),
        ),
        (
            _batch(
                _FUTURE,
                3,
                epoch_offset=12.0,
            ),
            _clock(12.0, 118.0),
        ),
    )
    for batch, clock in recovery:
        result = _ingest(monitor, batch, clock=clock)
        step10_events.extend(result.events)
        step10_invalidations.update(result.invalidations)
    assert monitor.operator_state.clock_continuity.status is ClockContinuityStatus.STABLE
    receipts.append(
        _receipt(
            10,
            monitor,
            events=tuple(step10_events),
            ledger=ledger_restore.ledger,
            invalidations=frozenset(step10_invalidations),
        )
    )

    quota_source = SourceKey("codex", "quota", "local", "remote_quota_windows")
    transcript_source = SourceKey("codex", "transcripts", "local", "transcript_usage")
    sibling_quota = SourceKey(
        "claude", "quota", "experimental-remote", "remote_quota_windows"
    )
    quota_scope = OpaqueScopeIdentifier("scope:default")
    calls: dict[SourceKey, int] = {}

    def adapter(invocation: ProviderInvocation, _cancel) -> ProviderResult:
        calls[invocation.source_key] = calls.get(invocation.source_key, 0) + 1
        if invocation.source_key == quota_source and calls[invocation.source_key] == 1:
            return ProviderResult(
                invocation,
                ProviderOutcomeKind.COOLDOWN,
                None,
                (),
                CooldownKey(quota_source, quota_scope),
                time.monotonic() + 0.05,
                ProviderRuntimeDiagnostic("rate_limited"),
            )
        if invocation.source_key == sibling_quota:
            lane = QuotaLaneKey(
                sibling_quota,
                "scope:default",
                "shared",
                None,
                "five-hour",
                QuotaEffect.ALL_WORKLOADS,
            )
            window = ProviderQuotaWindow(
                lane,
                20.0,
                300,
                _BASE + 3_600.0,
                _watermark(sibling_quota, 1, epoch_offset=11.0),
                SourceHealth.HEALTHY,
                False,
            )
            return ProviderResult(
                invocation,
                ProviderOutcomeKind.SUCCESS,
                None,
                (window,),
                None,
                None,
                None,
            )
        return ProviderResult(
            invocation,
            ProviderOutcomeKind.SUCCESS,
            _batch(invocation.source_key, calls[invocation.source_key], epoch_offset=11.0),
            (),
            None,
            None,
            None,
        )

    runtime = ProviderRuntime(
        {
            quota_source: adapter,
            transcript_source: adapter,
            sibling_quota: adapter,
        }
    )
    initial_invocations = (
        ProviderInvocation(quota_source, 1, 1.0, RefreshTrigger.AUTOMATIC, quota_scope),
        ProviderInvocation(transcript_source, 1, 1.0, RefreshTrigger.AUTOMATIC),
        ProviderInvocation(sibling_quota, 1, 1.0, RefreshTrigger.AUTOMATIC),
    )
    assert runtime.request(initial_invocations) == initial_invocations
    first_results = _poll_runtime(runtime, wanted=3)
    manual = ProviderInvocation(quota_source, 2, 1.0, RefreshTrigger.MANUAL, quota_scope)
    assert runtime.request((manual,)) == ()
    assert runtime.state_for(quota_source).queued_after_cooldown
    queued_results = _poll_runtime(
        runtime,
        wanted=1,
        monotonic_now=time.monotonic() + 1.0,
    )
    assert len(queued_results) == 1
    queued_result = queued_results[0]
    assert queued_result.invocation.source_key == quota_source
    assert queued_result.invocation.generation == 2
    assert queued_result.outcome is ProviderOutcomeKind.SUCCESS
    assert not runtime.state_for(quota_source).queued_after_cooldown
    assert calls[transcript_source] == calls[sibling_quota] == 1
    runtime.stop(deadline_seconds=1.0)
    receipts.append(
        _receipt(
            11,
            monitor,
            ledger=ledger_restore.ledger,
            health_extra=tuple(
                sorted(f"runtime:{result.outcome.value}" for result in first_results)
            ),
            refresh_invocations=(
                *(
                    f"{item.source_key.provider_id}:{item.source_key.capability_id}"
                    for item in initial_invocations
                ),
                "codex:manual-queued",
            ),
            runtime_publications=(
                f"{queued_result.invocation.source_key.provider_id}:"
                f"{queued_result.invocation.source_key.capability_id}:"
                f"generation-{queued_result.invocation.generation}:"
                f"{queued_result.outcome.value}:not-queued",
            ),
        )
    )

    lane = QuotaLaneKey(
        quota_source,
        "scope:default",
        "shared",
        None,
        "five-hour",
        QuotaEffect.ALL_WORKLOADS,
    )
    window = UsageWindowViewModel(
        lane,
        "Codex",
        "Product-owned limit",
        300,
        CapacityValue(
            CapacityUnit.PERCENT_REMAINING,
            25.0,
            ObservationState.OBSERVED,
        ),
        _BASE + 20.0,
        _BASE + 20.0,
        ResetState.FUTURE,
    )
    reset = plan_reset_boundary_refresh(
        (window,),
        now=_BASE,
        normal_refresh_deadline=_BASE + 100.0,
    )
    duplicate = plan_reset_boundary_refresh(
        (window,),
        now=_BASE,
        normal_refresh_deadline=_BASE + 100.0,
        attempted_lane_keys=frozenset(reset.lane_keys),
    )
    assert duplicate.lane_keys == ()
    receipts.append(
        _receipt(
            12,
            monitor,
            ledger=ledger_restore.ledger,
            reset_lanes=tuple(item.opaque_scope for item in reset.lane_keys),
        )
    )
    return tuple(receipts)


def _privacy_surfaces(root: Path) -> tuple[object, ...]:
    source = next(
        row
        for row in negotiated_provider_sources()
        if row.source_key == _CODEX
    )
    raw = {
        "request_id": "request:privacy",
        "event_id": "event:privacy",
        "sequence": 1,
        "prompt": _PRIVACY_CORPUS[0],
        "response": _PRIVACY_CORPUS[1],
        "tool_argument": _PRIVACY_CORPUS[2],
        "command": _PRIVACY_CORPUS[3],
        "cwd": _PRIVACY_CORPUS[4],
        "email": _PRIVACY_CORPUS[5],
        "account_label": _PRIVACY_CORPUS[6],
        "authorization": _PRIVACY_CORPUS[7],
        "cookie": _PRIVACY_CORPUS[8],
        "error": _PRIVACY_CORPUS[9],
        "url": _PRIVACY_CORPUS[10],
        "delimiter": _PRIVACY_CORPUS[11],
        "control": _PRIVACY_CORPUS[12],
        "oversize": _PRIVACY_CORPUS[13],
    }
    ingress = HookEvent(
        provider="codex",
        logged_at=datetime.fromtimestamp(_BASE, tz=timezone.utc),
        event_name="PermissionRequest",
        raw=raw,
        session_id="work:privacy",
        cwd=_PRIVACY_CORPUS[4],
        tool_name=_PRIVACY_CORPUS[2],
        message=_PRIVACY_CORPUS[0],
        origin=_PRIVACY_CORPUS[5],
    )
    normalized = minimize_hook_event(
        ingress,
        source_key=source.source_key,
        contract=source.contract,
        observation_authority=source.registration.observation_authority,
    )
    normalized_payload = normalized_provider_record_to_payload(normalized)
    normalized_path = root / "state" / "normalized.jsonl"
    with (
        patch("sidepulse.audit.TRIM_THRESHOLD_BYTES", 1),
        patch("sidepulse.audit.TRIM_KEEP_LINES", 1),
    ):
        write_normalized_hook_record(normalized_path, normalized)
        write_normalized_hook_record(normalized_path, normalized)
    batch = provider_facts_for_record(
        normalized,
        contract=source.contract,
        observation_authority=source.registration.observation_authority,
        observed_at_epoch=_BASE,
    )

    state_path = root / "state" / "latest.json"
    monitor = LiveAgentMonitor(
        latest_state_path=state_path,
        clock_sampler=lambda: _clock(0.0, 100.0),
    )
    monitor.ingest_batch(batch, clock=_clock(0.0, 100.0))
    snapshot = monitor.snapshot()
    monitor.write_latest_state()
    restored = LiveAgentMonitor(
        latest_state_path=state_path,
        clock_sampler=lambda: _clock(1.0, 101.0),
    ).snapshot()

    event_key = snapshot.operator_state.requests[0].semantic_event_key
    ledger = record_delivery(
        DeliveryLedger(()),
        DeliveryReceipt(
            DeliveryKey(event_key, DeliveryChannel.MAILBOX_CUE, 0),
            DeliveryDisposition.DELIVERED,
            _BASE,
            1,
            None,
        ),
    )
    ledger_path = root / "state" / "privacy-ledger.json"
    save_delivery_ledger(ledger_path, ledger)
    ledger_restore = load_delivery_ledger(ledger_path)

    invocation = ProviderInvocation(
        _CODEX,
        1,
        1.0,
        RefreshTrigger.AUTOMATIC,
    )
    fake_adapter_result = ProviderResult(
        invocation,
        ProviderOutcomeKind.SUCCESS,
        batch,
        (),
        None,
        None,
        None,
    )
    authority_evidence, authority_persistence = _authority_privacy_evidence(
        root,
        source,
    )
    wire_evidence = _wire_privacy_evidence()
    return (
        normalized,
        normalized_payload,
        batch,
        batch.diagnostics,
        monitor.operator_state,
        snapshot,
        restored,
        ledger,
        ledger_restore,
        fake_adapter_result,
        normalized_path.read_text(),
        state_path.read_text(),
        ledger_path.read_text(),
        authority_evidence,
        authority_persistence,
        wire_evidence,
    )


def _authority_privacy_evidence(root: Path, source: object) -> tuple[dict[str, int], str]:
    rejected = 0
    inert_batches = 0
    derived_tokens: list[str] = []
    normalized_payloads: list[dict[str, object]] = []
    authority_path = root / "state" / "authority.jsonl"

    for index, sentinel in enumerate(_PRIVACY_CORPUS[-3:], start=1):
        for constructor in (WorkIdentifier, RequestIdentifier, EventToken):
            try:
                constructor(sentinel)
            except ProviderFactValidationError:
                rejected += 1
            else:
                raise AssertionError("risky authority identifier was accepted")

        ingress = HookEvent(
            provider="codex",
            logged_at=datetime.fromtimestamp(_BASE + index, tz=timezone.utc),
            event_name="PermissionRequest",
            raw={
                "request_id": sentinel,
                "event_id": sentinel,
                "sequence": index,
            },
            session_id=sentinel,
            agent_id=sentinel,
        )
        normalized = minimize_hook_event(
            ingress,
            source_key=source.source_key,
            contract=source.contract,
            observation_authority=source.registration.observation_authority,
        )
        assert normalized.provider_work_id is None
        assert normalized.provider_request_id is None
        assert normalized.parent_work_id is None
        assert normalized.event_token.value != sentinel
        derived_tokens.append(normalized.event_token.value)

        batch = provider_facts_for_record(
            normalized,
            contract=source.contract,
            observation_authority=source.registration.observation_authority,
            observed_at_epoch=_BASE + index,
        )
        assert batch.work_facts == ()
        assert batch.request_facts == ()
        assert batch.source_health is SourceHealth.PARTIAL
        assert batch.source_freshness is SourceFreshness.PARTIAL
        assert tuple(
            diagnostic.identifier.value for diagnostic in batch.diagnostics
        ) == ("missing_request_identity", "missing_work_identity")
        inert_batches += 1

        write_normalized_hook_record(authority_path, normalized)
        normalized_payloads.append(normalized_provider_record_to_payload(normalized))

    persisted_text = authority_path.read_text()
    persisted_payloads = tuple(
        json.loads(line) for line in persisted_text.splitlines()
    )
    assert persisted_payloads == tuple(normalized_payloads)
    assert len(set(derived_tokens)) == len(derived_tokens)
    assert all(sentinel not in persisted_text for sentinel in _PRIVACY_CORPUS[-3:])
    return (
        {
            "authority_rejections": rejected,
            "inert_batches": inert_batches,
            "persisted_rejected_values": 0,
            "safe_persisted_records": len(persisted_payloads),
            "unique_derived_event_tokens": len(set(derived_tokens)),
        },
        persisted_text,
    )


def _wait_for(predicate: Callable[[], bool], *, timeout: float = 0.75) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _send_wire_payload(socket_path: Path, payload: bytes) -> None:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(0.5)
    try:
        client.connect(str(socket_path))
        client.sendall(payload)
        client.shutdown(socket.SHUT_WR)
        try:
            while client.recv(64):
                pass
        except (ConnectionResetError, TimeoutError):
            pass
    finally:
        client.close()


def _wire_privacy_evidence() -> dict[str, int]:
    hint = ProviderRefreshHint(_CODEX, EventToken("event:canonical-wire"))
    valid_wire = json.dumps(
        {
            "version": 1,
            "provider_id": "codex",
            "adapter_id": "hooks",
            "source_instance_id": "global",
            "capability_id": "live_agent_events",
            "event_token": "event:canonical-wire",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    duplicate_key = (
        b'{"version":1,"version":1,"provider_id":"codex",'
        b'"adapter_id":"hooks","source_instance_id":"global",'
        b'"capability_id":"live_agent_events",'
        b'"event_token":"event:duplicate"}'
    )
    unreadable_payload = b"\xff"
    max_sized_invalid = b"x" * MAX_HINT_BYTES
    max_plus_one = b"x" * (MAX_HINT_BYTES + 1)

    assert len(valid_wire) <= MAX_HINT_BYTES
    assert _hint_from_wire(valid_wire) == hint
    assert _hint_from_wire(duplicate_key) is None
    assert _hint_from_wire(unreadable_payload) is None
    assert _hint_from_wire(max_sized_invalid) is None

    with tempfile.TemporaryDirectory(prefix="sp-can-", dir="/tmp") as directory:
        socket_path = Path(directory) / "state" / "events.sock"
        received: list[ProviderRefreshHint] = []
        server = HookEventServer(received.append, socket_path=socket_path)
        server.start()
        try:
            refusal_counts: dict[str, int] = {}
            for name, payload in (
                ("duplicate_key_refusals", duplicate_key),
                ("unreadable_payload_refusals", unreadable_payload),
                ("max_sized_invalid_refusals", max_sized_invalid),
                ("max_plus_one_refusals", max_plus_one),
            ):
                _send_wire_payload(socket_path, payload)
                assert received == []
                refusal_counts[name] = 1
            assert send_refresh_hint(hint, socket_path=socket_path, timeout=0.5)
            assert _wait_for(lambda: received == [hint])
            assert received == [hint]
        finally:
            server.stop()
        same_uid_dispatches = len(received)

        peer_refusals = 0
        for peer_reader in (
            lambda _connection: os.geteuid() + 1,
            lambda _connection: (_ for _ in ()).throw(OSError("unreadable peer")),
        ):
            received = []
            server = HookEventServer(
                received.append,
                socket_path=socket_path,
                peer_uid_reader=peer_reader,
            )
            server.start()
            try:
                _send_wire_payload(socket_path, valid_wire)
                assert received == []
                peer_refusals += 1
            finally:
                server.stop()

    return {
        "same_uid_dispatches": same_uid_dispatches,
        **refusal_counts,
        "mismatched_peer_refusals": int(peer_refusals >= 1),
        "unreadable_peer_refusals": int(peer_refusals >= 2),
    }


def _run_scale_fixture() -> _ScaleReceipt:
    started = time.perf_counter()
    primary_keys = tuple(
        _work(_CODEX, f"primary:{index:03d}") for index in range(100)
    )
    worker_keys = tuple(
        _work(_CODEX, f"worker:{index:03d}") for index in range(900)
    )
    request_keys = tuple(
        _request(worker_keys[index], f"request:{index:03d}")
        for index in range(200)
    )
    target = worker_keys[-1]
    first_watermark = _watermark(_CODEX, 1)
    initial = ProviderFactBatch(
        _CODEX,
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        SourceHealth.HEALTHY,
        SourceFreshness.FRESH,
        first_watermark.occurred_at_epoch,
        first_watermark,
        (
            ProviderWorkFact(
                target,
                WorkLifecycle.ACTIVE,
                first_watermark,
                _label(target),
                primary_keys[99],
                NextActor.PROVIDER,
            ),
        ),
        (),
        (),
    )
    result = reduce_operator_state(
        empty_operator_state(),
        initial,
        clock=_clock(1.0, 101.0),
    )
    completed_keys = set()
    state = result.state
    for sequence in range(2, 10_002):
        result = reduce_operator_state(
            state,
            _batch(
                _CODEX,
                sequence,
                work_key=target,
                lifecycle=WorkLifecycle.COMPLETED,
                parent_key=primary_keys[99],
            ),
            clock=_clock(float(sequence), 100.0 + sequence),
        )
        completed_keys.update(
            event.key
            for event in result.events
            if event.kind is TransitionKind.COMPLETED
        )
        state = result.state

    scale_watermark = _watermark(_CODEX, 10_002)
    scale_batch = ProviderFactBatch(
        _CODEX,
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        SourceHealth.HEALTHY,
        SourceFreshness.FRESH,
        scale_watermark.occurred_at_epoch,
        scale_watermark,
        tuple(
            ProviderWorkFact(
                key,
                WorkLifecycle.COMPLETED,
                scale_watermark,
                _label(key),
                None
                if key in primary_keys
                else primary_keys[int(key.work_id.value.removeprefix("worker:")) % 100],
                NextActor.NONE,
            )
            for key in (*primary_keys, *worker_keys)
            if key != target
        ),
        tuple(
            ProviderRequestFact(
                key,
                ProviderRequestState.LIVE,
                RequestKind.PERMISSION,
                NextActor.USER,
                scale_watermark,
            )
            for key in request_keys
        ),
        (),
    )
    result = reduce_operator_state(
        state,
        scale_batch,
        clock=_clock(10_002.0, 10_102.0),
    )
    completed_keys.update(
        event.key
        for event in result.events
        if event.kind is TransitionKind.COMPLETED
    )
    opened_keys = {
        event.key
        for event in result.events
        if event.kind is TransitionKind.REQUEST_OPENED
    }

    state = result.state
    resolution_watermark = _watermark(_CODEX, 10_003)
    resolution = ProviderFactBatch(
        _CODEX,
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        SourceHealth.HEALTHY,
        SourceFreshness.FRESH,
        resolution_watermark.occurred_at_epoch,
        resolution_watermark,
        (),
        tuple(
            ProviderRequestFact(
                key,
                ProviderRequestState.RESOLVED,
                RequestKind.PERMISSION,
                NextActor.NONE,
                resolution_watermark,
            )
            for key in request_keys
        ),
        (),
    )
    result = reduce_operator_state(
        state,
        resolution,
        clock=_clock(10_003.0, 10_103.0),
    )
    resolved_keys = {
        event.key
        for event in result.events
        if event.kind is TransitionKind.REQUEST_RESOLVED
    }
    state = result.state

    old_duplicate = _batch(
        _CODEX,
        2,
        work_key=target,
        lifecycle=WorkLifecycle.ACTIVE,
    )
    for index in range(1_000):
        result = reduce_operator_state(
            state,
            old_duplicate,
            clock=_clock(10_004.0 + index, 10_104.0 + index),
        )
        assert result.events == ()
        state = result.state

    works = {work.key: work for work in state.works}
    requests = {request.key: request for request in state.requests}
    target_truth = works[target]
    return _ScaleReceipt(
        primary_count=sum(key in works for key in primary_keys),
        worker_count=sum(key in works for key in worker_keys),
        request_count=len(state.requests),
        unique_work_count=len(works),
        unique_request_count=len(requests),
        stable_parent_count=sum(
            works[key].parent_key == primary_keys[index % 100]
            for index, key in enumerate(worker_keys)
        ),
        opened_edge_count=len(opened_keys),
        completed_edge_count=len(completed_keys),
        resolved_edge_count=len(resolved_keys),
        final_sequence=target_truth.watermark.sequence or 0,
        final_lifecycle=target_truth.lifecycle.value,
        elapsed_seconds=time.perf_counter() - started,
    )


def test_exact_twelve_step_canonical_runtime_sequence(tmp_path: Path) -> None:
    """A broken cross-module contract must change an exact numbered receipt."""
    receipts = _run_composed_fixture(tmp_path)

    quiet_receipts = ("delivered", "failed", "suppressed_quiet")
    healthy = ("claude:healthy", "codex:healthy", "devin:healthy")
    expected = (
        _StepReceipt(
            1,
            2,
            ("stale_hold",),
            (),
            (),
            (),
            ("claude:healthy", "codex:healthy"),
            (),
            (),
            (),
            True,
        ),
        _StepReceipt(
            2,
            2,
            ("stale_hold",),
            (),
            (),
            (),
            ("claude:healthy", "codex:healthy"),
            (),
            (),
            (),
            False,
        ),
        _StepReceipt(
            3,
            3,
            ("live_unacknowledged",),
            ("became_active", "became_active"),
            ("ambient", "ambient"),
            (),
            (*healthy, "futureai:partial"),
            (),
            (),
            ("delivery", "lifecycle", "mailbox", "source_health"),
            False,
            (
                "work:codex:work:codex|became_active|6",
                "work:devin:work:devin|became_active|1",
            ),
            ("futureai:partial:zero_fact:uncertain:no_edge:source_health_invalidated",),
        ),
        _StepReceipt(
            4,
            3,
            ("live_unacknowledged",),
            (),
            (),
            quiet_receipts,
            healthy,
            (),
            (),
            (),
            False,
        ),
        _StepReceipt(
            5,
            3,
            ("live_unacknowledged",),
            (),
            (),
            quiet_receipts,
            healthy,
            (),
            (),
            ("mailbox",),
            False,
        ),
        _StepReceipt(
            6,
            3,
            ("live_unacknowledged",),
            ("completed",),
            ("courtesy",),
            quiet_receipts,
            healthy,
            (),
            (),
            ("completion", "delivery", "lifecycle", "mailbox"),
            False,
            ("work:codex:work:codex|completed|7",),
        ),
        _StepReceipt(
            7,
            3,
            ("stale_hold",),
            (),
            (),
            quiet_receipts,
            healthy,
            (),
            (),
            ("mailbox", "source_health"),
            False,
        ),
        _StepReceipt(
            8,
            3,
            ("stale_hold",),
            ("source_degraded",),
            ("ambient",),
            quiet_receipts,
            ("claude:timed_out", "codex:healthy", "devin:healthy"),
            (),
            (),
            ("delivery", "mailbox", "source_health"),
            False,
            ("work:claude:work:claude|source_degraded|7",),
        ),
        _StepReceipt(
            9,
            3,
            ("stale_hold",),
            (),
            (),
            quiet_receipts,
            ("claude:timed_out", "codex:healthy", "devin:healthy"),
            (),
            (),
            (),
            True,
        ),
        _StepReceipt(
            10,
            3,
            ("resolved",),
            ("became_idle", "source_recovered", "request_resolved"),
            ("ambient", "courtesy", "ambient"),
            quiet_receipts,
            healthy,
            (),
            (),
            ("delivery", "lifecycle", "mailbox", "source_health"),
            False,
            (
                "work:claude:work:claude|became_idle|9",
                "work:claude:work:claude|source_recovered|9",
                "request:claude:work:claude:request:one|request_resolved|9",
            ),
        ),
        _StepReceipt(
            11,
            3,
            ("resolved",),
            (),
            (),
            quiet_receipts,
            (*healthy, "runtime:cooldown", "runtime:success"),
            (
                "codex:remote_quota_windows",
                "codex:transcript_usage",
                "claude:remote_quota_windows",
                "codex:manual-queued",
            ),
            (),
            (),
            False,
            (),
            (),
            ("codex:remote_quota_windows:generation-2:success:not-queued",),
        ),
        _StepReceipt(
            12,
            3,
            ("resolved",),
            (),
            (),
            quiet_receipts,
            healthy,
            (),
            ("scope:default",),
            (),
            False,
        ),
    )

    assert receipts == expected


def test_adversarial_privacy_corpus_is_absent_from_every_canonical_surface(
    tmp_path: Path,
) -> None:
    """Any copied private ingress value must be observable across the full corpus."""
    surfaces = _privacy_surfaces(tmp_path)
    rendered = repr(surfaces)
    normalized_path = tmp_path / "state" / "normalized.jsonl"

    for sentinel in _PRIVACY_CORPUS:
        assert sentinel not in rendered
    authority_evidence = next(
        (
            surface
            for surface in surfaces
            if type(surface) is dict and "authority_rejections" in surface
        ),
        {},
    )
    wire_evidence = next(
        (
            surface
            for surface in surfaces
            if type(surface) is dict and "same_uid_dispatches" in surface
        ),
        {},
    )
    assert authority_evidence == {
        "authority_rejections": 9,
        "inert_batches": 3,
        "persisted_rejected_values": 0,
        "safe_persisted_records": 3,
        "unique_derived_event_tokens": 3,
    }
    assert wire_evidence == {
        "same_uid_dispatches": 1,
        "duplicate_key_refusals": 1,
        "max_sized_invalid_refusals": 1,
        "max_plus_one_refusals": 1,
        "unreadable_payload_refusals": 1,
        "mismatched_peer_refusals": 1,
        "unreadable_peer_refusals": 1,
    }
    assert stat.S_IMODE(normalized_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(normalized_path.stat().st_mode) == 0o600
    assert 0 < normalized_path.stat().st_size <= TRIM_THRESHOLD_BYTES
    assert json.loads(normalized_path.read_text()) == surfaces[1]


def test_deterministic_scale_fixture_retains_one_bounded_latest_truth() -> None:
    """A scale regression must lose a literal count, parent, edge, or watermark."""
    result = _run_scale_fixture()

    assert result.primary_count == 100
    assert result.worker_count == 900
    assert result.request_count == 200
    assert result.unique_work_count == 1_000
    assert result.unique_request_count == 200
    assert result.stable_parent_count == 900
    assert result.opened_edge_count == 200
    assert result.completed_edge_count == 1_000
    assert result.resolved_edge_count == 200
    assert result.final_sequence == 10_001
    assert result.final_lifecycle == WorkLifecycle.COMPLETED.value
