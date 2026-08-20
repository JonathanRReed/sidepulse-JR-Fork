"""Ended (unconfirmed): the honest word for a session nobody closed.

Hooks run inside the agent's process: a killed terminal or crashed turn
never sends Stop/SessionEnd. The old display demoted silent WORKING rows
to "Completed" -- a lie of kind that even fired celebration sweeps for
crashes -- and exempted TOOL_RUNNING entirely, so one orphan PreToolUse
read "Tool Running (1 active)" until retention.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sidepulse._collector_legacy import (
    POST_TOOL_WORKING_VISIBLE_SECONDS,
    WORKING_SILENCE_SECONDS,
    status_counts_active,
    status_for_snapshot,
)
from sidepulse.attention import LifecycleMode, _lifecycle_mode
from sidepulse.models import AgentMode, AgentStatus

_NOW = datetime.now(timezone.utc)


def _status(mode: AgentMode, event_name: str, *, silent_for: float) -> AgentStatus:
    return AgentStatus(
        provider="grok",
        agent_id="grok:session:x",
        display_name="grok x",
        mode=mode,
        updated_at=_NOW - timedelta(seconds=silent_for),
        event_name=event_name,
        session_id="x",
    )


def test_silent_working_ends_unconfirmed_not_completed() -> None:
    demoted = status_for_snapshot(
        _status(AgentMode.WORKING, "UserPromptSubmit", silent_for=WORKING_SILENCE_SECONDS + 1),
        _NOW,
        post_tool_working_visible_seconds=POST_TOOL_WORKING_VISIBLE_SECONDS,
    )
    assert demoted.mode is AgentMode.ENDED_UNCONFIRMED


def test_silent_tool_running_is_no_longer_exempt() -> None:
    demoted = status_for_snapshot(
        _status(AgentMode.TOOL_RUNNING, "PreToolUse", silent_for=WORKING_SILENCE_SECONDS + 1),
        _NOW,
        post_tool_working_visible_seconds=POST_TOOL_WORKING_VISIBLE_SECONDS,
    )
    assert demoted.mode is AgentMode.ENDED_UNCONFIRMED


def test_a_live_turn_is_untouched() -> None:
    live = status_for_snapshot(
        _status(AgentMode.WORKING, "UserPromptSubmit", silent_for=30.0),
        _NOW,
        post_tool_working_visible_seconds=POST_TOOL_WORKING_VISIBLE_SECONDS,
    )
    assert live.mode is AgentMode.WORKING


def test_ended_unconfirmed_never_counts_active_and_never_signals() -> None:
    ended = _status(AgentMode.ENDED_UNCONFIRMED, "PreToolUse", silent_for=700.0)
    assert status_counts_active(ended) is False
    assert _lifecycle_mode(ended, False) is LifecycleMode.IDLE


def test_replayed_normalized_records_keep_their_own_time() -> None:
    """parse_log_line must read occurred_at_epoch: normalized hook records
    carry no logged_at, and the parse-time fallback re-stamped yesterday's
    events as seconds old on every replay-built refresh."""
    import json

    from sidepulse.providers import parse_log_line

    epoch = 1_787_100_000.0
    line = json.dumps(
        {
            "adapter_id": "hooks",
            "capability_id": "live_agent_events",
            "event_name": "SessionStart",
            "event_token": "tok",
            "occurred_at_epoch": epoch,
            "provider_id": "grok",
            "session_id": "s1",
        }
    )
    record = parse_log_line("grok", line)
    assert record is not None
    assert abs(record.logged_at.timestamp() - epoch) < 1.0


def test_a_silent_active_work_claims_active_nowhere() -> None:
    """ONE clock for 'working went silent', consumed by every canonical
    surface: the row layer demoted at its window while the menu-bar
    title, the mailbox count, and the LIGHTS kept reading raw lifecycle
    ACTIVE -- 'it says an agent's running even though it's been done for
    10 minutes.'"""
    from sidepulse._settings_legacy import AgentMonitorSettings
    from sidepulse.attention import project_attention_from_operator_state
    from sidepulse.capacity_types import SourceKey
    from sidepulse.mailbox import project_canonical_mailbox
    from sidepulse.operator_state import (
        ACTIVE_SILENCE_SECONDS,
        BootIdentifier,
        ClockSample,
        active_work_went_silent,
        empty_operator_state,
        reduce_operator_state,
    )
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

    source = SourceKey("grok", "hooks", "global", "live_agent_events")
    watermark = ProviderWatermark(
        source_key=source,
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        occurred_at_epoch=1_800_000_000.0,
        event_token=EventToken("tok"),
        sequence=None,
        tie_break_rank=10,
    )
    batch = ProviderFactBatch(
        source_key=source,
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        source_health=SourceHealth.HEALTHY,
        source_freshness=SourceFreshness.FRESH,
        observed_at_epoch=1_800_000_000.0,
        watermark=watermark,
        work_facts=(
            ProviderWorkFact(
                key=WorkKey(source, WorkIdentifier("session:x")),
                lifecycle=WorkLifecycle.ACTIVE,
                watermark=watermark,
                safe_label="Grok session:x",
                parent_key=None,
                next_actor=NextActor.PROVIDER,
            ),
        ),
        request_facts=(),
        diagnostics=(),
    )
    silent_for = ACTIVE_SILENCE_SECONDS + 60.0
    state = reduce_operator_state(
        empty_operator_state(),
        batch,
        clock=ClockSample(
            1_800_000_000.0 + silent_for,
            100.0 + silent_for,
            BootIdentifier("boot:01"),
        ),
    ).state
    assert state.works[0].lifecycle is WorkLifecycle.ACTIVE  # raw truth kept

    projection = project_attention_from_operator_state(
        state, (), AgentMonitorSettings()
    )
    assert all(
        row.lifecycle_mode.value != "active" for row in projection.visible_rows
    ), "the lights must not claim work from a silent session"

    mailbox = project_canonical_mailbox(state)
    assert mailbox.active_count == 0
    in_progress = next(
        section for section in mailbox.sections if section.kind.value == "in_progress"
    )
    assert not in_progress.rows
    assert active_work_went_silent(state.works[0], state.last_clock.wall_epoch)


def test_completed_settles_to_idle_after_the_recent_window() -> None:
    """'The completed state doesn't go away after two minutes' -- a
    COMPLETED work held the done green (and the COMPLETED aggregate,
    which also fed the keep-awake grace) until the presence horizon
    dropped the row, up to an hour later. COMPLETED is a moment: past
    COMPLETED_RECENT_SECONDS the row settles to the idle whisper."""
    from sidepulse._settings_legacy import AgentMonitorSettings
    from sidepulse.attention import (
        LifecycleMode,
        project_attention_from_operator_state,
    )
    from sidepulse.capacity_types import SourceKey
    from sidepulse.operator_state import (
        COMPLETED_RECENT_SECONDS,
        BootIdentifier,
        ClockSample,
        completed_work_no_longer_recent,
        empty_operator_state,
        reduce_operator_state,
    )
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

    source = SourceKey("claude", "hooks", "global", "live_agent_events")
    watermark = ProviderWatermark(
        source_key=source,
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        occurred_at_epoch=1_800_000_000.0,
        event_token=EventToken("tok"),
        sequence=None,
        tie_break_rank=10,
    )

    def state_after(seconds: float):
        batch = ProviderFactBatch(
            source_key=source,
            observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            source_health=SourceHealth.HEALTHY,
            source_freshness=SourceFreshness.FRESH,
            observed_at_epoch=1_800_000_000.0,
            watermark=watermark,
            work_facts=(
                ProviderWorkFact(
                    key=WorkKey(source, WorkIdentifier("session:done")),
                    lifecycle=WorkLifecycle.COMPLETED,
                    watermark=watermark,
                    safe_label="Claude session:done",
                    parent_key=None,
                    next_actor=NextActor.USER,
                ),
            ),
            request_facts=(),
            diagnostics=(),
        )
        return reduce_operator_state(
            empty_operator_state(),
            batch,
            clock=ClockSample(
                1_800_000_000.0 + seconds,
                100.0 + seconds,
                BootIdentifier("boot:01"),
            ),
        ).state

    # Inside the window: the celebration is honest.
    fresh = state_after(COMPLETED_RECENT_SECONDS - 30.0)
    fresh_rows = project_attention_from_operator_state(
        fresh, (), AgentMonitorSettings()
    ).visible_rows
    assert any(
        row.lifecycle_mode is LifecycleMode.COMPLETED_RECENTLY
        for row in fresh_rows
    )

    # Past it: the raw truth stays COMPLETED, the display settles.
    stale = state_after(COMPLETED_RECENT_SECONDS + 60.0)
    assert stale.works[0].lifecycle is WorkLifecycle.COMPLETED  # raw truth kept
    stale_rows = project_attention_from_operator_state(
        stale, (), AgentMonitorSettings()
    ).visible_rows
    assert all(
        row.lifecycle_mode is not LifecycleMode.COMPLETED_RECENTLY
        for row in stale_rows
    ), "done is a moment, not a state"
    assert completed_work_no_longer_recent(
        stale.works[0], stale.last_clock.wall_epoch
    )
