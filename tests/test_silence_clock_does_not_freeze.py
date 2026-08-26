"""Silence must be measured against a clock that keeps running.

Every age window here used ``state.last_clock.wall_epoch`` -- the moment
of the last observed event -- as "now". That reads like the real clock
while an agent is chattering, and FREEZES the instant everything goes
quiet: the window is measured against a clock that stops advancing
exactly when silence begins, so nothing can ever age out. Reported live
as "you ended, yet it still says agents are active and it's showing the
claude colors going across" -- a finished turn held its completion sweep
forever, because the only clock that could retire it was that turn's own
last event.

The existing silence tests all advanced the clock to simulate silence,
which is the one thing real silence never does. These pin the quiet case
for EVERY provider, since the freeze was provider-independent.
"""

from __future__ import annotations

import pytest

from sidepulse._settings_legacy import AgentMonitorSettings
from sidepulse.attention import project_attention_from_operator_state
from sidepulse.capacity_types import SourceKey
from sidepulse.mailbox import project_canonical_mailbox
from sidepulse.operator_state import (
    ACTIVE_SILENCE_SECONDS,
    BootIdentifier,
    ClockSample,
    active_silence_seconds_for,
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
    _expected_safe_label,
)
from sidepulse.providers import PROVIDER_SPECS

LAST_EVENT_AT = 1_800_000_000.0

# Every registered provider, so this can never be a claude-only guarantee.
ALL_PROVIDERS = tuple(spec.provider for spec in PROVIDER_SPECS)


def state_that_went_quiet(provider: str, lifecycle: WorkLifecycle):
    """A work heard from at LAST_EVENT_AT, then total silence -- so the
    canonical clock never advances past that moment."""
    source = SourceKey(provider, "hooks", "global", "live_agent_events")
    watermark = ProviderWatermark(
        source_key=source,
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        occurred_at_epoch=LAST_EVENT_AT,
        event_token=EventToken("tok"),
        sequence=None,
        tie_break_rank=10,
    )
    batch = ProviderFactBatch(
        source_key=source,
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        source_health=SourceHealth.HEALTHY,
        source_freshness=SourceFreshness.FRESH,
        observed_at_epoch=LAST_EVENT_AT,
        watermark=watermark,
        work_facts=(
            ProviderWorkFact(
                key=WorkKey(source, WorkIdentifier("session:x")),
                lifecycle=lifecycle,
                watermark=watermark,
                safe_label=_expected_safe_label(WorkKey(source, WorkIdentifier("session:x"))),
                parent_key=None,
                next_actor=(
                    NextActor.PROVIDER
                    if lifecycle is WorkLifecycle.ACTIVE
                    else NextActor.NONE
                ),
            ),
        ),
        request_facts=(),
        diagnostics=(),
    )
    return reduce_operator_state(
        empty_operator_state(),
        batch,
        # The clock stops here. Nothing arrives after this, which is
        # precisely what "the agent finished" looks like.
        clock=ClockSample(LAST_EVENT_AT, 100.0, BootIdentifier("boot:01")),
    ).state


def freeze_wall_clock(monkeypatch, seconds_after_last_event: float) -> None:
    monkeypatch.setattr(
        "sidepulse.operator_state.time.time",
        lambda: LAST_EVENT_AT + seconds_after_last_event,
    )


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_a_silent_active_work_stops_claiming_the_lights(provider, monkeypatch) -> None:
    state = state_that_went_quiet(provider, WorkLifecycle.ACTIVE)
    freeze_wall_clock(monkeypatch, active_silence_seconds_for(provider) + 60.0)

    projection = project_attention_from_operator_state(
        state, (), AgentMonitorSettings()
    )
    assert all(
        row.lifecycle_mode.value != "active" for row in projection.visible_rows
    ), f"{provider}: the lights still claim a session that went quiet"


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_a_silent_active_work_stops_being_counted(provider, monkeypatch) -> None:
    state = state_that_went_quiet(provider, WorkLifecycle.ACTIVE)
    freeze_wall_clock(monkeypatch, active_silence_seconds_for(provider) + 60.0)

    assert project_canonical_mailbox(state).active_count == 0, (
        f"{provider}: still counted as working after going quiet"
    )


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_a_finished_turn_does_not_hold_its_completion_forever(
    provider, monkeypatch
) -> None:
    """The exact reported symptom: the completion sweep never retiring."""
    state = state_that_went_quiet(provider, WorkLifecycle.COMPLETED)
    freeze_wall_clock(monkeypatch, 3_600.0)

    projection = project_attention_from_operator_state(
        state, (), AgentMonitorSettings()
    )
    assert all(
        row.lifecycle_mode.value != "completed_recently"
        for row in projection.visible_rows
    ), f"{provider}: still showing 'just finished' an hour later"


def test_work_still_inside_its_window_is_left_alone(monkeypatch) -> None:
    """The fix must not retire work that is merely between tool calls."""
    state = state_that_went_quiet("claude", WorkLifecycle.ACTIVE)
    freeze_wall_clock(monkeypatch, ACTIVE_SILENCE_SECONDS - 30.0)

    projection = project_attention_from_operator_state(
        state, (), AgentMonitorSettings()
    )
    assert any(row.lifecycle_mode.value == "active" for row in projection.visible_rows)


def test_a_wall_clock_behind_the_evidence_cannot_rejuvenate_work(monkeypatch) -> None:
    """A machine whose clock sits behind the events (restore after sleep,
    a clock stepped backwards) must not make silent work look young."""
    state = state_that_went_quiet("claude", WorkLifecycle.ACTIVE)
    freeze_wall_clock(monkeypatch, -86_400.0)

    projection = project_attention_from_operator_state(
        state, (), AgentMonitorSettings()
    )
    # Floored at the observed moment: neither aged out nor rejuvenated.
    assert all(row.lifecycle_mode.value != "completed_recently" for row in projection.visible_rows)
