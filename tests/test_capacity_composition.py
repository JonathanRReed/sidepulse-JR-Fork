from __future__ import annotations

from unittest.mock import patch

import pytest

from sidepulse import usage_stats
from sidepulse.capacity_authority import select_binding_lanes
from sidepulse.capacity_history import CapacityHistorySummary, HistoryInterval
from sidepulse.capacity_refresh import (
    CapacityRefreshCoordinator,
    RefreshCause,
    RefreshFailureKind,
    RefreshSourceKey,
    RefreshSourceRegistration,
)
from sidepulse.capacity_types import (
    CapacitySnapshot,
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ExecutionContext,
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from sidepulse.capacity_view import (
    CapacityDetailSnapshot,
    CapacityHistoryPresentation,
    CapacityHistorySummaryInput,
    build_capacity_card,
    build_capacity_detail,
)
from sidepulse.usage_view import build_provider_usage_view
from tests.test_capacity_resource_budget import (
    CLAUDE_QUOTA,
    CLAUDE_TRANSCRIPTS,
    CODEX_QUOTA,
    CODEX_TRANSCRIPTS,
)
from tests.test_sidepulse import isolate_controller

NOW = 1_000.0


def _source(provider_id: str, instance: str) -> SourceKey:
    return SourceKey(provider_id, "quota", instance, "remote_quota_windows")


def _health(
    source: SourceKey,
    kind: SourceHealthKind,
    *,
    observed_at: float,
    has_last_known_good: bool,
) -> CapacitySourceHealth:
    return CapacitySourceHealth(
        source,
        kind,
        observed_at,
        observed_at,
        None,
        None,
        has_last_known_good,
    )


def _lane(
    source: SourceKey,
    health: CapacitySourceHealth,
    *,
    scope: str,
    semantic_name: str,
    horizon: QuotaHorizon,
    remaining: float | None,
    observation_state: ObservationState,
    reset_state: ResetState,
    reset_epoch: float | None,
    effect: QuotaEffect = QuotaEffect.ALL_WORKLOADS,
    model: str | None = None,
) -> QuotaLaneObservation:
    return QuotaLaneObservation(
        key=QuotaLaneKey(
            source=source,
            opaque_scope=scope,
            pool="plan",
            model=model,
            window=scope,
            effect=effect,
        ),
        semantic_name=semantic_name,
        horizon=horizon,
        value=CapacityValue(
            CapacityUnit.PERCENT_REMAINING,
            remaining,
            observation_state,
        ),
        reset=ResetFact(reset_state, reset_epoch, 300.0, health.observed_at),
        observed_at=health.observed_at,
        source_health=health,
        account_discriminator=None,
    )


@pytest.fixture
def controller(request):
    class ControllerCase:
        def __init__(self) -> None:
            self._cleanups = []

        def addCleanup(self, callback) -> None:
            self._cleanups.append(callback)

        def skipTest(self, reason: str) -> None:
            pytest.skip(reason)

        def close(self) -> None:
            for callback in reversed(self._cleanups):
                callback()

    case = ControllerCase()
    isolate_controller(case)
    request.addfinalizer(case.close)
    return case.controller, case.status_bar


def test_one_exact_batch_keeps_transcript_and_capacity_truth_independent(
    controller,
) -> None:
    target, _status_bar = controller
    totals = usage_stats.UsageTotals()
    totals.sessions.add("claude-session")
    totals.input_tokens = 123
    totals.codex_sessions.add("codex-session")
    totals.sessions.add("codex-session")
    totals.codex_tokens = 456
    totals.source_coverage = {
        "claude": usage_stats.UsageSourceCoverage(
            provider_id="claude",
            status=usage_stats.UsageSourceStatus.OK,
            root_present=True,
            root_walked=True,
            files_discovered=2,
            files_read=2,
            cache_hits=0,
            malformed_lines=0,
            unreadable_files=0,
            skipped_symlinks=0,
            duplicate_physical_files=0,
        ),
        "codex": usage_stats.UsageSourceCoverage(
            provider_id="codex",
            status=usage_stats.UsageSourceStatus.OK,
            root_present=True,
            root_walked=True,
            files_discovered=3,
            files_read=3,
            cache_hits=0,
            malformed_lines=0,
            unreadable_files=0,
            skipped_symlinks=0,
            duplicate_physical_files=0,
        ),
    }

    class InlineThread:
        def __init__(self, *, target, args, daemon):
            assert daemon is True
            self._target = target
            self._args = args

        def start(self) -> None:
            self._target(*self._args)

        def join(self) -> None:
            return None

    def apply_on_main(selector, payload, wait):
        assert selector == "applyUsageSummary:"
        assert wait is False
        target.applyUsageSummary_(payload)

    with (
        patch("sidepulse.status_bar.threading.Thread", InlineThread),
        patch("sidepulse.status_bar.usage_stats.scan_usage", return_value=totals) as scan,
        patch(
            "sidepulse.status_bar.usage_stats.cached_codex_rate_limits",
            return_value={
                "primary": {
                    "used_percent": 22,
                    "window_minutes": 300,
                }
            },
        ),
        patch("sidepulse.status_bar.claude_quota.fetch_windows") as claude_remote,
        patch.object(
            target,
            "performSelectorOnMainThread_withObject_waitUntilDone_",
            side_effect=apply_on_main,
        ),
        patch.object(target, "schedule_capacity_timers"),
    ):
        started = target.request_usage_refresh(
            (
                CODEX_TRANSCRIPTS,
                CLAUDE_TRANSCRIPTS,
                CODEX_QUOTA,
                CLAUDE_QUOTA,
            ),
            reason="menu-open",
        )

    assert started == (
        CODEX_TRANSCRIPTS,
        CLAUDE_TRANSCRIPTS,
        CODEX_QUOTA,
    )
    scan.assert_called_once()
    claude_remote.assert_not_called()

    codex = target._usage_provider_models["codex"]
    claude = target._usage_provider_models["claude"]
    assert codex.windows[0].percent_used == 22.0
    assert "Last 7 days: 1 session" in codex.summary_text
    assert claude.windows == ()
    assert "Last 7 days: 1 session" in claude.summary_text
    # The summary names the period, never the provider. The view owns the
    # title, and when the summary named the provider too the card read
    # "Claude · Claude, last 365 days: ...".
    assert "Codex" not in codex.summary_text
    assert "Claude" not in claude.summary_text
    assert codex.menu_line.count("Codex") == 1
    assert claude.menu_line.count("Claude") == 1

    transcript_states = target._usage_transcript_states
    assert transcript_states[CODEX_TRANSCRIPTS].last_success_at is not None
    assert transcript_states[CLAUDE_TRANSCRIPTS].last_success_at is not None
    capacity_rows = {
        row.key.source: row
        for row in target._capacity_refresh_coordinator.snapshot_state(1_000_000.0).sources
    }
    assert capacity_rows[CODEX_QUOTA].last_success_at is not None
    assert CLAUDE_QUOTA not in capacity_rows


def test_two_provider_story_never_invents_binding_or_refresh_truth() -> None:
    codex_source = _source("codex", "local")
    claude_source = _source("claude", "experimental-remote")
    codex_health = _health(
        codex_source,
        SourceHealthKind.HEALTHY,
        observed_at=NOW,
        has_last_known_good=False,
    )
    claude_health = _health(
        claude_source,
        SourceHealthKind.STALE,
        observed_at=NOW - 200.0,
        has_last_known_good=True,
    )
    lanes = (
        _lane(
            codex_source,
            codex_health,
            scope="short",
            semantic_name="Short window",
            horizon=QuotaHorizon.SHORT,
            remaining=18.0,
            observation_state=ObservationState.OBSERVED,
            reset_state=ResetState.FUTURE,
            reset_epoch=NOW + 300.0,
        ),
        _lane(
            codex_source,
            codex_health,
            scope="long",
            semantic_name="Long window",
            horizon=QuotaHorizon.LONG,
            remaining=55.0,
            observation_state=ObservationState.OBSERVED,
            reset_state=ResetState.DISPUTED,
            reset_epoch=None,
        ),
        _lane(
            codex_source,
            codex_health,
            scope="model",
            semantic_name="Selected model",
            horizon=QuotaHorizon.OTHER,
            remaining=None,
            observation_state=ObservationState.PARTIAL,
            reset_state=ResetState.UNKNOWN,
            reset_epoch=None,
            effect=QuotaEffect.MODEL,
            model="gpt-5",
        ),
        _lane(
            claude_source,
            claude_health,
            scope="short",
            semantic_name="Bearer /Users/private/secret",
            horizon=QuotaHorizon.SHORT,
            remaining=33.0,
            observation_state=ObservationState.STALE,
            reset_state=ResetState.FUTURE,
            reset_epoch=NOW + 600.0,
        ),
        _lane(
            claude_source,
            claude_health,
            scope="long",
            semantic_name="Long window",
            horizon=QuotaHorizon.LONG,
            remaining=68.0,
            observation_state=ObservationState.STALE,
            reset_state=ResetState.FUTURE,
            reset_epoch=NOW + 1_200.0,
        ),
        _lane(
            claude_source,
            claude_health,
            scope="model",
            semantic_name="Selected model",
            horizon=QuotaHorizon.OTHER,
            remaining=None,
            observation_state=ObservationState.PARTIAL,
            reset_state=ResetState.UNKNOWN,
            reset_epoch=None,
            effect=QuotaEffect.MODEL,
            model="claude-opus",
        ),
    )
    snapshot = CapacitySnapshot(NOW, lanes, (codex_health, claude_health))
    projection = select_binding_lanes(
        snapshot,
        ExecutionContext(
            provider_ids=("codex", "claude"),
            source_instances=("local", "experimental-remote"),
            selected_model=None,
            selected_feature=None,
            source_scopes=(
                ("codex", "local"),
                ("claude", "experimental-remote"),
            ),
        ),
        NOW,
        allow_unbound_legacy=True,
    )

    refresh_key = RefreshSourceKey(codex_source, "plan", None)
    refresh = CapacityRefreshCoordinator(
        (RefreshSourceRegistration(refresh_key, enabled=True, supported=True),)
    )
    decision = refresh.request_refresh(refresh_key, RefreshCause.AUTOMATIC, NOW)
    assert decision.generation == 1
    refresh.register_started(refresh_key, 1, NOW + 30.0)
    refresh.register_failure(
        refresh_key,
        1,
        RefreshFailureKind.SOURCE_UNAVAILABLE,
        NOW + 1.0,
        NOW + 120.0,
    )
    detail_snapshot = CapacityDetailSnapshot(
        snapshot,
        refresh.snapshot_state(NOW + 2.0),
        NOW + 2.0,
    )
    history_off = CapacityHistoryPresentation(False, ())
    history_on = CapacityHistoryPresentation(
        True,
        (
            CapacityHistorySummaryInput(
                HistoryInterval.DAY,
                CapacityHistorySummary(4, 1, 18.0, 68.0, ()),
            ),
        ),
    )

    card = build_capacity_card(projection, NOW + 2.0)
    detail_off = build_capacity_detail(
        detail_snapshot,
        projection,
        history_off,
        NOW + 2.0,
    )
    detail_on = build_capacity_detail(
        detail_snapshot,
        projection,
        history_on,
        NOW + 2.0,
    )
    detail_rows = tuple(
        row
        for provider in detail_off.providers
        for group in provider.groups
        for row in group.rows
    )
    source_health = {row.provider: row for row in detail_off.source_health}

    assert len(snapshot.lanes) == 6
    assert len(projection.binding_lanes) == 2
    assert len(card.rows) == 2
    # Codex's long window has a DISPUTED reset and no epoch, so it no longer
    # headlines anything -- a compact row carries a countdown, and there is no
    # boundary here to count down to. The second slot goes to Claude's stale
    # long window instead, which at least happened.
    assert {row.provider for row in card.rows} == {"Codex", "Claude"}
    assert all(
        row.reset_credible for row in projection.binding_lanes
    )
    assert len(detail_rows) == 6
    assert sum(row.binds for row in detail_rows) == 2
    assert any(row.reset_text == "Reset disputed" for row in detail_rows)
    assert sum(
        row.refusal_text == "Selected model is unavailable" for row in detail_rows
    ) == 2
    assert source_health["Codex"].status_text == "Cooling down"
    assert source_health["Claude"].status_text == "Stale"
    assert source_health["Claude"].has_last_known_good is True
    assert detail_off.history_enabled is False
    assert detail_off.history == ()
    assert detail_on.history_enabled is True
    assert detail_on.history[0].label == "Day"

    local_activity = build_provider_usage_view(
        "claude",
        "Claude",
        (),
        now=NOW,
        summary_text="Claude today: partial local activity",
        partial=True,
    )
    assert local_activity.partial is True
    assert local_activity.windows == ()
    assert local_activity.summary_text == "Claude today: partial local activity"

    public_copy = repr((card, detail_off))
    assert "/Users/private/secret" not in public_copy
    assert "Bearer" not in public_copy
    assert "authority_withheld" not in public_copy
