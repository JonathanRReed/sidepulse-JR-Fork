from __future__ import annotations

from dataclasses import fields

import pytest

from sidepulse.activity_ledger import ActivityEntry, ActivityKind, ActivityLedger, record_activity
from sidepulse.away_summary import (
    MAX_AWAY_SUMMARY_ITEMS,
    AwaySummaryConsent,
    AwaySummaryKind,
    AwaySummaryPolicy,
    AwaySummaryStatus,
    acknowledge_away_summary,
    project_away_summary,
)
from sidepulse.operator_history import HistoryCoverage, OperatorHistoryDay
from sidepulse.operator_history_store import OperatorHistoryState
from sidepulse.provider_contracts import ProviderIdentifier

NOW = 1_800_000_000.0


def _entry(
    at: float,
    *,
    kind: ActivityKind = ActivityKind.COMPLETED,
    label: str = "workspace",
    provider: str = "codex",
) -> ActivityEntry:
    return ActivityEntry(kind, at, label, provider, "opaque-session-id", "ignored detail")


def _history_day(
    *,
    completed: int = 2,
    failed: int = 1,
    needs_user: int = 0,
) -> OperatorHistoryDay:
    return OperatorHistoryDay(
        "2027-01-15",
        0,
        ProviderIdentifier("codex"),
        1,
        needs_user,
        completed,
        failed,
        0,
        (0, 0, 0, 0),
        (0, 0, 0, 0),
        1,
        0,
        0,
        0,
        HistoryCoverage.COMPLETE,
        max(1, completed + failed + needs_user),
    )


def test_policy_requires_explicit_consent_and_bounds_retention() -> None:
    assert AwaySummaryPolicy().consent is AwaySummaryConsent.DISABLED
    assert AwaySummaryPolicy(consent=True).consent is AwaySummaryConsent.ENABLED

    with pytest.raises(ValueError):
        AwaySummaryPolicy(consent=True, retention_days=8)


def test_disabled_projection_does_not_expose_retained_or_unread_content() -> None:
    ledger = record_activity(ActivityLedger(), _entry(NOW - 10.0))

    projection = project_away_summary(
        ledger,
        OperatorHistoryState((_history_day(),)),
        policy=AwaySummaryPolicy(),
        now_epoch=NOW,
    )

    assert projection.status is AwaySummaryStatus.DISABLED
    assert projection.items == ()
    assert projection.unseen_items == ()
    assert projection.live_unread_watermark == ledger.last_seen_epoch


def test_first_launch_is_quiet_without_erasing_the_live_watermark() -> None:
    ledger = record_activity(ActivityLedger(last_seen_epoch=NOW - 100.0), _entry(NOW - 10.0))

    projection = project_away_summary(
        ledger,
        (),
        policy=AwaySummaryPolicy(consent=True, retention_days=7),
        now_epoch=NOW,
        first_launch=True,
    )

    assert projection.status is AwaySummaryStatus.FIRST_LAUNCH
    assert projection.items == ()
    assert projection.unseen_items == ()
    assert projection.live_unread_watermark == NOW - 100.0


def test_projection_separates_retained_items_from_live_unread_items() -> None:
    old = _entry(NOW - 8 * 86_400.0, kind=ActivityKind.BLOCKED)
    seen = _entry(NOW - 500.0, kind=ActivityKind.ASKED)
    unseen = _entry(NOW - 50.0, kind=ActivityKind.COMPLETED)
    ledger = ActivityLedger(last_seen_epoch=NOW - 100.0)
    for entry in (old, seen, unseen):
        ledger = record_activity(ledger, entry)

    projection = project_away_summary(
        ledger,
        (),
        policy=AwaySummaryPolicy(consent=True, retention_days=7),
        now_epoch=NOW,
    )

    assert projection.status is AwaySummaryStatus.UNSEEN
    assert tuple(item.kind for item in projection.items) == (
        AwaySummaryKind.COMPLETED,
        AwaySummaryKind.ASKED,
    )
    assert tuple(item.kind for item in projection.unseen_items) == (AwaySummaryKind.COMPLETED,)
    assert projection.retained_items == projection.items
    assert projection.live_unread_watermark == NOW - 100.0


def test_seen_projection_keeps_retained_history_and_does_not_clear_it() -> None:
    entry = _entry(NOW - 10.0)
    ledger = record_activity(ActivityLedger(last_seen_epoch=NOW), entry)

    projection = project_away_summary(
        ledger,
        (),
        policy=AwaySummaryPolicy(consent=True, retention_days=7),
        now_epoch=NOW,
    )

    assert projection.status is AwaySummaryStatus.SEEN
    assert projection.items
    assert projection.unseen_items == ()


def test_operator_history_contributes_only_bounded_outcome_counters() -> None:
    projection = project_away_summary(
        ActivityLedger(),
        OperatorHistoryState((_history_day(),)),
        policy=AwaySummaryPolicy(consent=True, retention_days=7),
        now_epoch=1_800_000_000.0,
    )

    assert projection.history_totals.completed == 2
    assert projection.history_totals.failed == 1
    assert projection.history_totals.started == 1
    assert all("ignored detail" not in sentence for sentence in projection.summary_sentences)


def test_projection_order_and_count_are_deterministically_bounded() -> None:
    ledger = ActivityLedger()
    for index in range(MAX_AWAY_SUMMARY_ITEMS + 10):
        ledger = record_activity(
            ledger,
            _entry(NOW - index, label=f"workspace-{index}"),
        )

    projection = project_away_summary(
        ledger,
        (),
        policy=AwaySummaryPolicy(consent=True, retention_days=7),
        now_epoch=NOW,
    )

    assert len(projection.items) <= MAX_AWAY_SUMMARY_ITEMS
    assert [item.occurred_at_epoch for item in projection.items] == sorted(
        (item.occurred_at_epoch for item in projection.items), reverse=True
    )


def test_acknowledgement_advances_only_the_live_activity_watermark() -> None:
    entry = _entry(NOW - 10.0)
    ledger = record_activity(ActivityLedger(last_seen_epoch=NOW - 20.0), entry)
    acknowledged = acknowledge_away_summary(ledger, NOW)

    assert acknowledged.last_seen_epoch == NOW
    assert acknowledged.entries == ledger.entries


def test_projection_models_have_no_content_storage_fields() -> None:
    fields_by_model = {
        model.__name__: {field.name for field in fields(model)}
        for model in (
            __import__("sidepulse.away_summary", fromlist=["AwaySummaryEntry"]).AwaySummaryEntry,
        )
    }
    assert not fields_by_model["AwaySummaryEntry"] & {
        "prompt",
        "transcript",
        "path",
        "message",
        "content",
        "detail",
    }
