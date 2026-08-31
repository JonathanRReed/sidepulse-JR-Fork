"""Pure, content-minimized projection for "Since you were away".

This module deliberately has no persistence API.  ``ActivityLedger`` remains
the source of recent event facts and its ``last_seen_epoch`` remains the live
unread watermark.  ``OperatorHistoryState`` remains the source of retained
daily counters.  The two are projected here without introducing a third
store, and without carrying prompts, transcripts, paths, messages, or event
details into the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Final

from .activity_ledger import (
    ActivityKind,
    ActivityLedger,
    ActivityValidationError,
    mark_activity_seen,
    safe_activity_text,
)
from .operator_history import (
    OperatorHistoryDay,
    OperatorHistoryProjection,
    merge_operator_history_days,
)
from .operator_history_store import OperatorHistoryState

MAX_AWAY_SUMMARY_ITEMS: Final = 24
# Descriptive alias for integration callers that use "entries" terminology.
MAX_AWAY_SUMMARY_ENTRIES: Final = MAX_AWAY_SUMMARY_ITEMS
MAX_AWAY_SUMMARY_SENTENCES: Final = 3
MAX_AWAY_SUMMARY_SENTENCE_LENGTH: Final = 160
SUPPORTED_AWAY_RETENTION_DAYS: Final = frozenset({0, 1, 7, 30, 90})
MAX_AWAY_COUNTER: Final = 1_000_000
_SECONDS_PER_DAY: Final = 86_400.0


class AwaySummaryValidationError(ValueError):
    """Away-summary input failed closed at its pure typed boundary."""


class AwaySummaryConsent(str, Enum):
    """Whether the owner explicitly opted into retained away summaries."""

    DISABLED = "disabled"
    ENABLED = "enabled"


class AwaySummaryStatus(str, Enum):
    """The visible state of a projection, including first-launch semantics."""

    DISABLED = "disabled"
    FIRST_LAUNCH = "first-launch"
    EMPTY = "empty"
    SEEN = "seen"
    UNSEEN = "unseen"


class AwaySummaryKind(str, Enum):
    """The only event vocabulary this surface may expose."""

    COMPLETED = "completed"
    ASKED = "asked"
    BLOCKED = "blocked"
    THRESHOLD_CROSSED = "threshold_crossed"
    STARTED = "started"
    NEEDS_USER = "needs_user"
    FAILED = "failed"
    REQUEST_ACKNOWLEDGED = "request_acknowledged"
    REQUEST_RESUMED = "request_resumed"
    SOURCE_DEGRADED = "source_degraded"
    SOURCE_RECOVERED = "source_recovered"
    DEVICE_DEGRADED = "device_degraded"
    DEVICE_RECOVERED = "device_recovered"


def _finite_nonnegative(value: object) -> bool:
    return (
        type(value) in {int, float}
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0.0
    )


def _bounded_count(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_AWAY_COUNTER


@dataclass(frozen=True, slots=True)
class AwaySummaryPolicy:
    """Explicit consent plus a finite retention choice.

    Consent defaults to disabled.  Retention is expressed in days and is
    independently bounded so enabling the feature can never create an
    unbounded historical archive.
    """

    consent: AwaySummaryConsent | bool = AwaySummaryConsent.DISABLED
    retention_days: int = 7

    def __post_init__(self) -> None:
        consent = self.consent
        if type(consent) is bool:
            consent = AwaySummaryConsent.ENABLED if consent else AwaySummaryConsent.DISABLED
            object.__setattr__(self, "consent", consent)
        if type(consent) is not AwaySummaryConsent:
            raise AwaySummaryValidationError("invalid away-summary consent")
        if type(self.retention_days) is not int or self.retention_days not in SUPPORTED_AWAY_RETENTION_DAYS:
            raise AwaySummaryValidationError("invalid away-summary retention")

    @property
    def enabled(self) -> bool:
        return self.consent is AwaySummaryConsent.ENABLED and self.retention_days > 0

    @property
    def retention_window_days(self) -> int:
        return self.retention_days


@dataclass(frozen=True, slots=True)
class AwaySummaryEntry:
    """One bounded outcome or transition, with no content-bearing fields."""

    kind: AwaySummaryKind
    occurred_at_epoch: float
    provider: str
    label: str
    count: int = 1

    def __post_init__(self) -> None:
        if not (
            type(self.kind) is AwaySummaryKind
            and _finite_nonnegative(self.occurred_at_epoch)
            and type(self.provider) is str
            and 1 <= len(self.provider) <= 32
            and self.provider == self.provider.strip()
            and self.provider.isprintable()
            and type(self.label) is str
            and 1 <= len(self.label) <= 96
            and self.label == self.label.strip()
            and self.label.isprintable()
            and _bounded_count(self.count)
            and self.count >= 1
        ):
            raise AwaySummaryValidationError("invalid away-summary entry")
        object.__setattr__(self, "occurred_at_epoch", float(self.occurred_at_epoch))


@dataclass(frozen=True, slots=True)
class AwaySummaryTotals:
    """Content-free counters projected from retained operator history."""

    started: int = 0
    needs_user: int = 0
    completed: int = 0
    failed: int = 0
    acknowledged: int = 0
    source_recoveries: int = 0
    device_recoveries: int = 0

    def __post_init__(self) -> None:
        values = (
            self.started,
            self.needs_user,
            self.completed,
            self.failed,
            self.acknowledged,
            self.source_recoveries,
            self.device_recoveries,
        )
        if not all(_bounded_count(value) for value in values):
            raise AwaySummaryValidationError("invalid away-summary totals")

    @property
    def total_outcomes(self) -> int:
        return min(MAX_AWAY_COUNTER, self.completed + self.failed)

    @property
    def total_transitions(self) -> int:
        return min(
            MAX_AWAY_COUNTER,
            self.started
            + self.needs_user
            + self.acknowledged
            + self.source_recoveries
            + self.device_recoveries,
        )


@dataclass(frozen=True, slots=True)
class AwaySummaryProjection:
    """Stable read model with retained history and live unread kept separate."""

    status: AwaySummaryStatus
    items: tuple[AwaySummaryEntry, ...]
    unseen_items: tuple[AwaySummaryEntry, ...]
    live_unread_watermark: float
    retention_cutoff_epoch: float
    history_totals: AwaySummaryTotals
    summary_sentences: tuple[str, ...]

    def __post_init__(self) -> None:
        if not (
            type(self.status) is AwaySummaryStatus
            and type(self.items) is tuple
            and all(type(item) is AwaySummaryEntry for item in self.items)
            and len(self.items) <= MAX_AWAY_SUMMARY_ITEMS
            and type(self.unseen_items) is tuple
            and all(item in self.items for item in self.unseen_items)
            and _finite_nonnegative(self.live_unread_watermark)
            and _finite_nonnegative(self.retention_cutoff_epoch)
            and type(self.history_totals) is AwaySummaryTotals
            and type(self.summary_sentences) is tuple
            and 1 <= len(self.summary_sentences) <= MAX_AWAY_SUMMARY_SENTENCES
            and all(
                type(sentence) is str
                and 1 <= len(sentence) <= MAX_AWAY_SUMMARY_SENTENCE_LENGTH
                and sentence.isprintable()
                for sentence in self.summary_sentences
            )
        ):
            raise AwaySummaryValidationError("invalid away-summary projection")
        object.__setattr__(self, "live_unread_watermark", float(self.live_unread_watermark))
        object.__setattr__(self, "retention_cutoff_epoch", float(self.retention_cutoff_epoch))

    @property
    def retained_items(self) -> tuple[AwaySummaryEntry, ...]:
        """The retained projection, independent of whether it is unread."""

        return self.items

    @property
    def live_unread_items(self) -> tuple[AwaySummaryEntry, ...]:
        return self.unseen_items

    @property
    def has_unseen(self) -> bool:
        return bool(self.unseen_items)

    @property
    def live_unread_count(self) -> int:
        return len(self.unseen_items)


_ACTIVITY_KIND_MAP: Final[dict[ActivityKind, AwaySummaryKind]] = {
    ActivityKind.COMPLETED: AwaySummaryKind.COMPLETED,
    ActivityKind.ASKED: AwaySummaryKind.ASKED,
    ActivityKind.BLOCKED: AwaySummaryKind.BLOCKED,
    ActivityKind.THRESHOLD_CROSSED: AwaySummaryKind.THRESHOLD_CROSSED,
}


def _history_rows(value: object) -> tuple[OperatorHistoryDay, ...]:
    if value is None:
        return ()
    if type(value) is OperatorHistoryState:
        return value.rows
    if type(value) is OperatorHistoryProjection:
        return value.rows
    if type(value) is tuple and all(type(row) is OperatorHistoryDay for row in value):
        return value
    raise AwaySummaryValidationError("invalid operator history input")


def _activity_items(
    ledger: ActivityLedger,
    *,
    cutoff: float,
    now_epoch: float,
) -> tuple[AwaySummaryEntry, ...]:
    selected: list[AwaySummaryEntry] = []
    for entry in ledger.entries:
        if entry.occurred_at_epoch < cutoff or entry.occurred_at_epoch > now_epoch:
            continue
        kind = _ACTIVITY_KIND_MAP[entry.kind]
        label = safe_activity_text(entry.label, 96)
        provider = safe_activity_text(entry.provider, 32)
        if not label or not provider:
            continue
        selected.append(AwaySummaryEntry(kind, entry.occurred_at_epoch, provider, label))
    ordered = sorted(
        selected,
        key=lambda item: (-item.occurred_at_epoch, item.kind.value, item.provider, item.label),
    )
    return tuple(ordered[:MAX_AWAY_SUMMARY_ITEMS])


def _history_totals(rows: tuple[OperatorHistoryDay, ...], *, cutoff: float, now_epoch: float) -> AwaySummaryTotals:
    selected = tuple(
        row
        for row in merge_operator_history_days((), rows)
        if (
            datetime.fromtimestamp(cutoff, timezone(timedelta(minutes=row.timezone_offset_minutes))).date()
            <= date.fromisoformat(row.day_key)
            <= datetime.fromtimestamp(now_epoch, timezone(timedelta(minutes=row.timezone_offset_minutes))).date()
        )
    )
    values = {
        field: min(MAX_AWAY_COUNTER, sum(getattr(row, field) for row in selected))
        for field in (
            "started",
            "needs_user",
            "completed",
            "failed",
            "acknowledged",
            "source_recoveries",
            "device_recoveries",
        )
    }
    return AwaySummaryTotals(**values)


def _sentences(items: tuple[AwaySummaryEntry, ...], totals: AwaySummaryTotals) -> tuple[str, ...]:
    sentences: list[str] = []
    event_count = len(items)
    history_count = totals.total_outcomes + totals.total_transitions
    if event_count:
        sentences.append(f"Recorded {event_count} outcome or transition{'' if event_count == 1 else 's'}.")
    if totals.completed or totals.failed:
        sentences.append(f"Observed {totals.completed} completions and {totals.failed} failures.")
    if totals.needs_user or totals.acknowledged:
        sentences.append(f"Observed {totals.needs_user} attention episodes and {totals.acknowledged} acknowledgements.")
    if not sentences and history_count:
        sentences.append(f"Recorded {history_count} bounded outcome or transition{'' if history_count == 1 else 's'}.")
    if not sentences:
        sentences.append("Nothing new was recorded while you were away.")
    return tuple(sentences[:MAX_AWAY_SUMMARY_SENTENCES])


def project_away_summary(
    activity_ledger: ActivityLedger | None = None,
    operator_history: object = (),
    policy: AwaySummaryPolicy | None = None,
    now_epoch: object = None,
    *,
    first_launch: bool = False,
    has_launched: bool | None = None,
    ledger: ActivityLedger | None = None,
    history: object = None,
    now: object = None,
    live_unread_watermark: object = None,
) -> AwaySummaryProjection:
    """Project bounded retained facts and live unread state.

    ``ledger.last_seen_epoch`` is read-only here.  Call
    :func:`acknowledge_away_summary` when the surface is actually opened.
    This prevents rendering a summary from silently clearing the live unread
    watermark, while retained items remain available after acknowledgement.
    """
    if ledger is not None:
        if activity_ledger is not None:
            raise AwaySummaryValidationError("duplicate activity ledger input")
        activity_ledger = ledger
    if history is not None:
        if operator_history not in ((), None):
            raise AwaySummaryValidationError("duplicate operator history input")
        operator_history = history
    if now is not None:
        if now_epoch is not None:
            raise AwaySummaryValidationError("duplicate away-summary clock input")
        now_epoch = now
    if now_epoch is None:
        raise AwaySummaryValidationError("away-summary clock is required")
    if type(activity_ledger) is not ActivityLedger:
        raise AwaySummaryValidationError("invalid activity ledger input")
    if policy is None:
        policy = AwaySummaryPolicy()
    if type(policy) is not AwaySummaryPolicy or not _finite_nonnegative(now_epoch):
        raise AwaySummaryValidationError("invalid away-summary projection input")
    if type(first_launch) is not bool:
        raise AwaySummaryValidationError("invalid first-launch flag")
    if has_launched is not None:
        if type(has_launched) is not bool:
            raise AwaySummaryValidationError("invalid launch flag")
        first_launch = not has_launched
    rows = _history_rows(operator_history)
    now_value = float(now_epoch)
    cutoff = max(0.0, now_value - policy.retention_days * _SECONDS_PER_DAY)
    watermark = activity_ledger.last_seen_epoch
    if live_unread_watermark is not None:
        if not _finite_nonnegative(live_unread_watermark):
            raise AwaySummaryValidationError("invalid live unread watermark")
        watermark = float(live_unread_watermark)

    if not policy.enabled:
        return AwaySummaryProjection(
            AwaySummaryStatus.DISABLED,
            (),
            (),
            watermark,
            cutoff,
            AwaySummaryTotals(),
            ("Since-you-were-away summaries are disabled.",),
        )
    if first_launch:
        return AwaySummaryProjection(
            AwaySummaryStatus.FIRST_LAUNCH,
            (),
            (),
            watermark,
            cutoff,
            AwaySummaryTotals(),
            ("Since-you-were-away summaries begin after first launch.",),
        )

    items = _activity_items(activity_ledger, cutoff=cutoff, now_epoch=now_value)
    unseen = tuple(item for item in items if item.occurred_at_epoch > watermark)
    totals = _history_totals(rows, cutoff=cutoff, now_epoch=now_value)
    history_has_facts = totals.total_outcomes > 0 or totals.total_transitions > 0
    status = AwaySummaryStatus.UNSEEN if unseen else AwaySummaryStatus.SEEN if items or history_has_facts else AwaySummaryStatus.EMPTY
    return AwaySummaryProjection(status, items, unseen, watermark, cutoff, totals, _sentences(items, totals))


def acknowledge_away_summary(ledger: ActivityLedger, now_epoch: object) -> ActivityLedger:
    """Advance only the live activity watermark; retain all activity rows."""

    if type(ledger) is not ActivityLedger or not _finite_nonnegative(now_epoch):
        raise AwaySummaryValidationError("invalid away-summary acknowledgement")
    try:
        return mark_activity_seen(ledger, float(now_epoch))
    except ActivityValidationError as error:
        raise AwaySummaryValidationError(str(error)) from error


mark_away_summary_seen = acknowledge_away_summary
AwaySummaryItem = AwaySummaryEntry


__all__ = [
    "MAX_AWAY_SUMMARY_ENTRIES",
    "MAX_AWAY_SUMMARY_ITEMS",
    "MAX_AWAY_SUMMARY_SENTENCES",
    "MAX_AWAY_SUMMARY_SENTENCE_LENGTH",
    "SUPPORTED_AWAY_RETENTION_DAYS",
    "AwaySummaryConsent",
    "AwaySummaryEntry",
    "AwaySummaryItem",
    "AwaySummaryKind",
    "AwaySummaryPolicy",
    "AwaySummaryProjection",
    "AwaySummaryStatus",
    "AwaySummaryTotals",
    "AwaySummaryValidationError",
    "acknowledge_away_summary",
    "mark_away_summary_seen",
    "project_away_summary",
]
