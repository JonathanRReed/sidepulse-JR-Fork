from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from sidepulse.attention import (
    LifecycleMode,
    SignalKind,
    project_attention,
    project_attention_from_operator_state,
    stable_event_key,
)
from sidepulse.capacity_types import SourceKey
from sidepulse.collector import MonitorSnapshot, aggregate_status
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.operator_state import (
    AcknowledgementEligibility,
    CanonicalOperatorEvent,
    CanonicalOperatorState,
    CanonicalRequestTruth,
    CanonicalWorkTruth,
    ClockContinuityState,
    ClockContinuityStatus,
    InterruptionClass,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
)
from sidepulse.provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderWatermark,
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
from sidepulse.settings import AgentMonitorSettings


def status(
    *,
    provider: str = "claude",
    agent_id: str = "claude:session:main",
    event_name: str,
    mode: AgentMode,
    updated_at: datetime = datetime(2026, 8, 12, tzinfo=timezone.utc),
) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=agent_id,
        display_name=f"{provider.title()} main",
        mode=mode,
        updated_at=updated_at,
        event_name=event_name,
        session_id="main",
    )


def snapshot_with(*statuses: AgentStatus) -> MonitorSnapshot:
    return MonitorSnapshot(
        aggregate=aggregate_status(statuses),
        statuses=statuses,
        stale_statuses=(),
        sources=(),
        collected_at=max(
            (status.updated_at for status in statuses),
            default=datetime(2026, 8, 12, tzinfo=timezone.utc),
        ),
    )


def test_terminal_failure_is_visible_but_not_actionable() -> None:
    failed = status(event_name="StopFailure", mode=AgentMode.BLOCKED_ERROR)

    projection = project_attention(snapshot_with(failed), AgentMonitorSettings())

    assert projection.actionable_attention == ()
    assert projection.transient_signals[0].kind is SignalKind.FAILURE
    assert projection.transient_signals[0].repetitions == 2
    assert projection.transient_signals[0].source_agent_id == failed.agent_id


def test_transient_tool_failure_never_fires_the_failure_signal() -> None:
    # A failed tool the agent continues past is routine agentic work; the
    # red failure blink is reserved for terminal failures the operator
    # must resolve (StopFailure, PermissionDenied).
    failed = status(event_name="PostToolUseFailure", mode=AgentMode.WORKING)

    projection = project_attention(snapshot_with(failed), AgentMonitorSettings())

    assert projection.transient_signals == ()


def test_main_permission_request_is_persistent_attention() -> None:
    snapshot = snapshot_with(
        status(event_name="PermissionRequest", mode=AgentMode.WAITING_FOR_INPUT)
    )

    projection = project_attention(snapshot, AgentMonitorSettings())

    assert len(projection.actionable_attention) == 1


def test_subagent_attention_obeys_one_setting_everywhere() -> None:
    snapshot = snapshot_with(
        status(
            agent_id="claude:agent:worker",
            event_name="PermissionRequest",
            mode=AgentMode.WAITING_FOR_INPUT,
        )
    )

    assert project_attention(snapshot, AgentMonitorSettings()).actionable_attention == ()
    enabled = replace(AgentMonitorSettings(), subagent_asks_alert=True)
    assert len(project_attention(snapshot, enabled).actionable_attention) == 1


def test_consumed_failure_event_does_not_repeat_transient_signal() -> None:
    failed = status(event_name="PostToolUseFailure", mode=AgentMode.BLOCKED_ERROR)

    projection = project_attention(
        snapshot_with(failed),
        AgentMonitorSettings(),
        consumed_event_keys=(stable_event_key(failed),),
    )

    assert projection.transient_signals == ()


def test_duplicate_failure_records_collapse_to_one_signal() -> None:
    failed = status(event_name="StopFailure", mode=AgentMode.BLOCKED_ERROR)

    projection = project_attention(
        snapshot_with(failed, failed),
        AgentMonitorSettings(),
    )

    assert len(projection.transient_signals) == 1


def test_failure_aliases_share_one_stable_key_and_consumed_signal() -> None:
    terminal = status(event_name="PostToolUseFailure", mode=AgentMode.BLOCKED_ERROR)
    legacy = status(event_name="PostToolUse", mode=AgentMode.BLOCKED_ERROR)

    projection = project_attention(
        snapshot_with(terminal, legacy),
        AgentMonitorSettings(),
        consumed_event_keys=(stable_event_key(terminal),),
    )

    assert stable_event_key(terminal) == stable_event_key(legacy)
    assert projection.transient_signals == ()


def test_visible_rows_map_agent_modes_to_lifecycle_without_hiding_failure() -> None:
    statuses = (
        status(event_name="Test", mode=AgentMode.IDLE_READY),
        status(event_name="PreToolUse", mode=AgentMode.TOOL_RUNNING),
        status(event_name="Stop", mode=AgentMode.WAITING_FOR_INPUT),
        status(event_name="Stop", mode=AgentMode.COMPLETED),
        status(event_name="PostToolUseFailure", mode=AgentMode.BLOCKED_ERROR),
        status(event_name="Test", mode=AgentMode.UNKNOWN),
    )

    projection = project_attention(snapshot_with(*statuses), AgentMonitorSettings())

    assert tuple(row.lifecycle_mode for row in projection.visible_rows) == (
        LifecycleMode.IDLE,
        LifecycleMode.ACTIVE,
        LifecycleMode.UNKNOWN,
        LifecycleMode.COMPLETED_RECENTLY,
        LifecycleMode.FAILED_VISIBLE,
        LifecycleMode.UNKNOWN,
    )
    assert projection.visible_rows[4].actionable is False
    assert projection.visible_rows[4].source_status is statuses[4]


def test_mixed_snapshot_prioritizes_waiting_attention_and_its_click_target() -> None:
    base = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    first_request = status(
        provider="codex",
        agent_id="codex:session:alpha",
        event_name="PermissionRequest",
        mode=AgentMode.WAITING_FOR_INPUT,
        updated_at=base,
    )
    second_request = status(
        provider="claude",
        agent_id="claude:session:bravo",
        event_name="Notification",
        mode=AgentMode.WAITING_FOR_INPUT,
        updated_at=base.replace(minute=1),
    )
    active = status(
        provider="devin",
        agent_id="devin:session:charlie",
        event_name="PreToolUse",
        mode=AgentMode.TOOL_RUNNING,
        updated_at=base.replace(minute=2),
    )
    failure = status(
        provider="grok",
        agent_id="grok:session:delta",
        event_name="PostToolUseFailure",
        mode=AgentMode.BLOCKED_ERROR,
        updated_at=base.replace(minute=3),
    )

    projection = project_attention(
        snapshot_with(active, failure, second_request, first_request),
        AgentMonitorSettings(),
    )

    assert projection.lifecycle_mode is LifecycleMode.WAITING
    assert projection.dominant_provider == "codex"
    assert tuple(row.agent_id for row in projection.actionable_attention) == (
        "codex:session:alpha",
        "claude:session:bravo",
    )
    assert projection.click_target_agent_id == "codex:session:alpha"


def test_canonical_attention_uses_request_truth_and_semantic_failure_edges() -> None:
    source = SourceKey("codex", "hooks", "global", "live_agent_events")
    work_key = WorkKey(source, WorkIdentifier("work:canonical"))
    request_key = RequestKey(work_key, RequestIdentifier("request:canonical"))
    watermark = ProviderWatermark(
        source,
        WatermarkBasis.PROVIDER_SEQUENCE,
        1_786_632_000.0,
        EventToken("event:canonical"),
        1,
        10,
    )
    request_event_key = SemanticEventKey(
        request_key,
        TransitionKind.REQUEST_OPENED,
        watermark,
    )
    request = CanonicalRequestTruth(
        request_key,
        RequestPhase.LIVE_UNACKNOWLEDGED,
        RequestKind.PERMISSION,
        NextActor.USER,
        watermark,
        SourceFreshness.FRESH,
        AcknowledgementEligibility.ELIGIBLE,
        request_event_key,
        watermark.occurred_at_epoch,
        1.0,
    )
    work = CanonicalWorkTruth(
        work_key,
        WorkLifecycle.WAITING,
        watermark,
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        SourceHealth.HEALTHY,
        SourceFreshness.FRESH,
        NextActor.USER,
        "Codex work:canonical",
        None,
        (request_key,),
        False,
    )
    failure_key = SemanticEventKey(work_key, TransitionKind.FAILED, watermark)
    failure = CanonicalOperatorEvent(
        failure_key,
        work_key,
        TransitionKind.FAILED,
        InterruptionClass.IMPORTANT_OUTCOME,
        watermark.occurred_at_epoch,
        SourceFreshness.FRESH,
    )
    state = CanonicalOperatorState(
        1,
        1,
        (work,),
        (request,),
        ((source, watermark),),
        (),
        ClockContinuityState(ClockContinuityStatus.STABLE, None, 0),
        None,
    )

    projection = project_attention_from_operator_state(
        state,
        (failure,),
        AgentMonitorSettings(),
    )

    row = projection.actionable_attention[0]
    assert row.work_key == work_key
    assert row.request_key == request_key
    assert projection.click_target_agent_id is None
    assert projection.transient_signals[0].event_key == failure_key


def test_completed_settles_to_idle_on_the_live_projection_path() -> None:
    # 2026-08-20 final-sweep audit finding #1: the first version of the
    # 120s completed-decay lived only in the canonical projection, which
    # has zero non-test callers -- the app projects through
    # project_attention/_lifecycle_mode, and the done green still held
    # for 20-60 minutes. This pins the LIVE path, judged against the
    # snapshot's own collected_at (a clock that actually advances).
    from datetime import timedelta

    from sidepulse.operator_state import COMPLETED_RECENT_SECONDS

    finished_at = datetime(2026, 8, 12, 12, 0, 0, tzinfo=timezone.utc)
    done = status(
        event_name="Stop",
        mode=AgentMode.COMPLETED,
        updated_at=finished_at,
    )

    fresh = replace(
        snapshot_with(done),
        collected_at=finished_at
        + timedelta(seconds=COMPLETED_RECENT_SECONDS - 30.0),
    )
    fresh_row = project_attention(fresh, AgentMonitorSettings()).visible_rows[0]
    assert fresh_row.lifecycle_mode is LifecycleMode.COMPLETED_RECENTLY

    stale = replace(
        snapshot_with(done),
        collected_at=finished_at
        + timedelta(seconds=COMPLETED_RECENT_SECONDS + 60.0),
    )
    stale_row = project_attention(stale, AgentMonitorSettings()).visible_rows[0]
    assert stale_row.lifecycle_mode is LifecycleMode.IDLE, (
        "done is a moment on the LIVE path too"
    )
