from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
from math import inf, nan
from zoneinfo import ZoneInfo

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.operator_history import (
    HistoryCoverage,
    HistoryEventKind,
    HistoryValidationError,
    OperatorHistoryDay,
    OperatorHistoryProjection,
    RuntimeHistoryEvent,
    aggregate_operator_history,
    merge_operator_history_days,
    project_operator_history,
)
from sidepulse.operator_state import SemanticEventKey, TransitionKind
from sidepulse.provider_contracts import ProviderIdentifier
from sidepulse.provider_facts import (
    EventToken,
    ProviderWatermark,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
)

NOW = 1_800_000_000.0
DAY = 86_400.0


def _event_key(
    suffix: str,
    *,
    provider: str = "codex",
    transition: TransitionKind = TransitionKind.BECAME_ACTIVE,
    occurred_at: float = NOW,
) -> SemanticEventKey:
    source = SourceKey(provider, "hooks", "local:01", "live_agent_events")
    work = WorkKey(source, WorkIdentifier(f"work:{suffix}"))
    return SemanticEventKey(
        work,
        transition,
        ProviderWatermark(
            source,
            WatermarkBasis.PROVIDER_EVENT_ID,
            occurred_at,
            EventToken(f"event:{suffix}"),
            None,
            0,
        ),
    )


def _event(
    suffix: str,
    kind: HistoryEventKind,
    *,
    provider: str = "codex",
    occurred_at: float = NOW,
    active_seconds: float | None = None,
    attention_wait_seconds: float | None = None,
    primary_count: int = 1,
    worker_count: int = 0,
    transition: TransitionKind = TransitionKind.BECAME_ACTIVE,
) -> RuntimeHistoryEvent:
    return RuntimeHistoryEvent(
        _event_key(
            suffix,
            provider=provider,
            transition=transition,
            occurred_at=occurred_at,
        ),
        ProviderIdentifier(provider),
        kind,
        occurred_at,
        active_seconds,
        attention_wait_seconds,
        primary_count,
        worker_count,
    )


def _day(
    day_key: str = "2027-01-15",
    *,
    provider: str = "codex",
    coverage: HistoryCoverage = HistoryCoverage.COMPLETE,
    completed: int = 1,
    failed: int = 0,
    needs_user: int = 0,
    sample_count: int = 1,
    offset: int = 0,
) -> OperatorHistoryDay:
    return OperatorHistoryDay(
        day_key,
        offset,
        ProviderIdentifier(provider),
        0,
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
        coverage,
        sample_count,
    )


def _zone_offset(zone: ZoneInfo):
    def resolve(epoch: float) -> int:
        value = datetime.fromtimestamp(epoch, zone).utcoffset()
        assert value is not None
        return int(value.total_seconds() // 60)

    return resolve


def test_history_models_expose_only_the_metadata_allowlist() -> None:
    """Adding retained identity or content fields would violate the storage boundary."""
    assert tuple(field.name for field in fields(RuntimeHistoryEvent)) == (
        "semantic_event_key",
        "provider_id",
        "kind",
        "occurred_at",
        "active_seconds",
        "attention_wait_seconds",
        "primary_count",
        "worker_count",
    )
    assert tuple(field.name for field in fields(OperatorHistoryDay)) == (
        "day_key",
        "timezone_offset_minutes",
        "provider_id",
        "started",
        "needs_user",
        "completed",
        "failed",
        "acknowledged",
        "active_duration_bands",
        "attention_wait_bands",
        "primary_count",
        "worker_count",
        "source_recoveries",
        "device_recoveries",
        "coverage",
        "sample_count",
    )
    assert tuple(field.name for field in fields(OperatorHistoryProjection)) == (
        "range_days",
        "observed_days",
        "missing_days",
        "rows",
        "summary_sentences",
        "health_label",
    )


@pytest.mark.parametrize(
    "forbidden",
    (
        "source_key",
        "work_key",
        "request_key",
        "identity_hash",
        "display_label",
        "session_title",
        "timeline",
        "prompt",
        "message",
        "command",
        "path",
        "raw_error",
        "email",
        "credential",
        "token",
        "cookie",
        "url",
        "navigation_target",
    ),
)
def test_daily_constructor_rejects_every_forbidden_field(forbidden: str) -> None:
    """No undeclared content or identity category may enter a retained day."""
    values: dict[str, object] = {field.name: getattr(_day(), field.name) for field in fields(OperatorHistoryDay)}
    values[forbidden] = "PRIVATE SENTINEL"
    with pytest.raises(TypeError):
        OperatorHistoryDay(**values)  # type: ignore[arg-type]


def test_runtime_identity_is_discarded_after_duplicate_transition_collapse() -> None:
    """Repeated observation of one semantic edge contributes exactly once."""
    terminal = _event(
        "token.sentinel",
        HistoryEventKind.COMPLETED,
        transition=TransitionKind.COMPLETED,
        active_seconds=301.0,
        primary_count=1,
        worker_count=4,
    )

    rows = aggregate_operator_history((terminal, terminal))

    assert len(rows) == 1
    assert rows[0].completed == 1
    assert rows[0].sample_count == 1
    assert rows[0].active_duration_bands == (0, 1, 0, 0)
    assert "token.sentinel" not in repr(rows)
    assert not hasattr(rows[0], "semantic_event_key")


def test_runtime_identity_sentinel_corpus_is_discarded_before_daily_output() -> None:
    """Every allowed opaque runtime identity is transient, regardless of its word shape."""
    sentinels = (
        "prompt.sentinel",
        "message.sentinel",
        "command.sentinel",
        "title.sentinel",
        "path.sentinel",
        "rawerror.sentinel",
        "email.sentinel",
        "token.sentinel",
        "url.sentinel",
        "navigationtarget.sentinel",
    )

    rows = aggregate_operator_history(tuple(_event(sentinel, HistoryEventKind.STARTED) for sentinel in sentinels))
    retained = repr(rows).casefold()

    assert rows[0].sample_count == len(sentinels)
    for sentinel in sentinels:
        assert sentinel not in retained


def test_out_of_order_input_produces_the_same_transition_only_day() -> None:
    """Poll arrival order must not change retained daily facts."""
    events = (
        _event("003", HistoryEventKind.COMPLETED, occurred_at=NOW + 30),
        _event("001", HistoryEventKind.STARTED, occurred_at=NOW + 10),
        _event("002", HistoryEventKind.NEEDS_USER, occurred_at=NOW + 20),
    )

    assert aggregate_operator_history(events) == aggregate_operator_history(tuple(reversed(events)))
    row = aggregate_operator_history(events)[0]
    assert (row.started, row.needs_user, row.completed) == (1, 1, 1)


def test_acknowledgement_is_local_and_request_resume_is_not_provider_resolution() -> None:
    """Only a SidePulse acknowledgement transition increments acknowledged."""
    rows = aggregate_operator_history(
        (
            _event("ack", HistoryEventKind.REQUEST_ACKNOWLEDGED),
            _event("resume", HistoryEventKind.REQUEST_RESUMED),
        )
    )

    assert rows[0].acknowledged == 1
    assert rows[0].sample_count == 2


def test_duration_and_attention_bands_use_exact_fixed_boundaries() -> None:
    """Exact durations must collapse into four count bands before persistence."""
    boundaries = (0.0, 299.999, 300.0, 1_799.999, 1_800.0, 7_199.999, 7_200.0)
    events = tuple(
        _event(
            f"duration:{index}",
            HistoryEventKind.COMPLETED,
            active_seconds=value,
            attention_wait_seconds=value,
        )
        for index, value in enumerate(boundaries)
    )

    row = aggregate_operator_history(events)[0]

    assert row.active_duration_bands == (2, 2, 2, 1)
    assert row.attention_wait_bands == (2, 2, 2, 1)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("occurred_at", nan),
        ("occurred_at", inf),
        ("active_seconds", nan),
        ("active_seconds", -1.0),
        ("attention_wait_seconds", inf),
        ("attention_wait_seconds", -0.1),
        ("primary_count", -1),
        ("worker_count", 1_001),
    ),
)
def test_runtime_events_reject_nonfinite_negative_and_unbounded_values(
    field: str,
    value: object,
) -> None:
    """Malformed numeric metadata must fail before bucketing or persistence."""
    good = _event("valid", HistoryEventKind.STARTED)
    values = {item.name: getattr(good, item.name) for item in fields(RuntimeHistoryEvent)}
    values[field] = value

    with pytest.raises(HistoryValidationError):
        RuntimeHistoryEvent(**values)  # type: ignore[arg-type]


def test_provider_id_must_match_the_semantic_subject_source() -> None:
    """A cross-provider tag cannot move one provider event into another provider day."""
    key = _event_key("cross", provider="codex")
    with pytest.raises(HistoryValidationError):
        RuntimeHistoryEvent(
            key,
            ProviderIdentifier("claude"),
            HistoryEventKind.STARTED,
            NOW,
            None,
            None,
            1,
            0,
        )


def test_provider_failure_marks_only_its_provider_day() -> None:
    """A failed Codex observation cannot erase a healthy Claude observation."""
    rows = aggregate_operator_history(
        (
            _event("codex-failed", HistoryEventKind.SOURCE_DEGRADED, provider="codex"),
            _event("claude-start", HistoryEventKind.STARTED, provider="claude"),
        )
    )
    by_provider = {row.provider_id.value: row for row in rows}

    assert by_provider["codex"].coverage is HistoryCoverage.FAILED
    assert by_provider["claude"].coverage is HistoryCoverage.COMPLETE
    assert by_provider["claude"].started == 1


def test_mixed_observation_and_source_failure_is_partial() -> None:
    """Useful facts plus a source outage form a partial day, not complete or failed."""
    row = aggregate_operator_history(
        (
            _event("started", HistoryEventKind.STARTED),
            _event("degraded", HistoryEventKind.SOURCE_DEGRADED),
            _event("recovered", HistoryEventKind.SOURCE_RECOVERED),
        )
    )[0]

    assert row.coverage is HistoryCoverage.PARTIAL
    assert row.source_recoveries == 1


def test_primary_and_worker_counts_are_daily_high_watermarks() -> None:
    """Lifecycle transitions cannot repeatedly add the same live family census."""
    row = aggregate_operator_history(
        (
            _event("one", HistoryEventKind.STARTED, primary_count=2, worker_count=7),
            _event("two", HistoryEventKind.COMPLETED, primary_count=3, worker_count=4),
            _event("three", HistoryEventKind.FAILED, primary_count=1, worker_count=8),
        )
    )[0]

    assert row.primary_count == 3
    assert row.worker_count == 8
    assert row.completed == 1
    assert row.failed == 1


def test_terminal_worker_duplicate_contributes_once() -> None:
    """One worker terminal edge must not become repeated progress history."""
    worker_terminal = _event(
        "worker-terminal",
        HistoryEventKind.FAILED,
        primary_count=1,
        worker_count=1,
        transition=TransitionKind.FAILED,
    )

    row = aggregate_operator_history((worker_terminal, worker_terminal))[0]

    assert row.failed == 1
    assert row.worker_count == 1
    assert row.sample_count == 1


def test_local_midnight_assigns_each_utc_event_to_exactly_one_day() -> None:
    """Half-open local dates prevent a midnight edge from entering two rows."""
    before = datetime(2027, 1, 15, 5, 59, 59, tzinfo=timezone.utc).timestamp()
    midnight = datetime(2027, 1, 15, 6, 0, 0, tzinfo=timezone.utc).timestamp()
    rows = aggregate_operator_history(
        (
            _event("before", HistoryEventKind.STARTED, occurred_at=before),
            _event("midnight", HistoryEventKind.STARTED, occurred_at=midnight),
        ),
        timezone_offset_at=lambda _epoch: -360,
    )

    assert [(row.day_key, row.sample_count) for row in rows] == [
        ("2027-01-14", 1),
        ("2027-01-15", 1),
    ]


def test_dst_gap_and_fold_preserve_utc_identity_and_stored_offset() -> None:
    """Nonexistent and repeated wall times cannot drop or duplicate semantic events."""
    central = ZoneInfo("America/Chicago")
    epochs = (
        datetime(2027, 3, 14, 7, 59, tzinfo=timezone.utc).timestamp(),
        datetime(2027, 3, 14, 8, 1, tzinfo=timezone.utc).timestamp(),
        datetime(2027, 11, 7, 6, 30, tzinfo=timezone.utc).timestamp(),
        datetime(2027, 11, 7, 7, 30, tzinfo=timezone.utc).timestamp(),
    )
    rows = aggregate_operator_history(
        tuple(
            _event(f"dst:{index}", HistoryEventKind.STARTED, occurred_at=epoch) for index, epoch in enumerate(epochs)
        ),
        timezone_offset_at=_zone_offset(central),
    )

    assert sum(row.sample_count for row in rows) == 4
    assert {(row.day_key, row.timezone_offset_minutes) for row in rows} == {
        ("2027-03-14", -360),
        ("2027-03-14", -300),
        ("2027-11-07", -300),
        ("2027-11-07", -360),
    }


def test_timezone_change_keeps_each_event_in_one_locally_assigned_row() -> None:
    """Travel changes future assignment without rewriting UTC semantic identity."""
    boundary = NOW + 60.0

    def traveled(epoch: float) -> int:
        return -360 if epoch < boundary else 60

    rows = aggregate_operator_history(
        (
            _event("home", HistoryEventKind.STARTED, occurred_at=NOW),
            _event("away", HistoryEventKind.COMPLETED, occurred_at=NOW + 120.0),
        ),
        timezone_offset_at=traveled,
    )

    assert sum(row.sample_count for row in rows) == 2
    assert {row.timezone_offset_minutes for row in rows} == {-360, 60}


def test_merge_is_commutative_and_preserves_partial_coverage() -> None:
    """Concurrent batches for one row must merge without losing facts or claiming complete."""
    complete = _day(completed=2, sample_count=2)
    failed = _day(coverage=HistoryCoverage.FAILED, completed=0, failed=1)

    left = merge_operator_history_days((complete,), (failed,))
    right = merge_operator_history_days((failed,), (complete,))

    assert left == right
    assert left[0].completed == 2
    assert left[0].failed == 1
    assert left[0].sample_count == 3
    assert left[0].coverage is HistoryCoverage.PARTIAL


def test_empty_projection_distinguishes_no_observation_from_zero() -> None:
    """An empty ledger is missing evidence, never an observed zero-work day."""
    projection = project_operator_history(
        (),
        range_days=7,
        now=NOW,
        timezone_offset_minutes=0,
    )

    assert projection.observed_days == 0
    assert projection.missing_days == 7
    assert projection.rows == ()
    assert projection.health_label == "No Observation"
    assert projection.summary_sentences == ("No operator history was observed in this range.",)


def test_explicit_no_observation_rows_never_claim_observed_provider_days() -> None:
    """Stored missing-evidence markers must use the same neutral empty projection copy."""
    marker = OperatorHistoryDay(
        "2027-01-15",
        0,
        ProviderIdentifier("codex"),
        0,
        0,
        0,
        0,
        0,
        (0, 0, 0, 0),
        (0, 0, 0, 0),
        0,
        0,
        0,
        0,
        HistoryCoverage.NO_OBSERVATION,
        0,
    )

    projection = project_operator_history((marker,), range_days=7, now=NOW)

    assert projection.observed_days == 0
    assert projection.health_label == "No Observation"
    assert projection.summary_sentences == ("No operator history was observed in this range.",)


def test_projection_filters_range_and_counts_distinct_local_days() -> None:
    """Multiple providers and offsets on one date count as one observed local day."""
    today = datetime.fromtimestamp(NOW, timezone.utc).date()
    today_key = today.isoformat()
    yesterday_key = today.fromordinal(today.toordinal() - 1).isoformat()
    old_key = today.fromordinal(today.toordinal() - 9).isoformat()
    projection = project_operator_history(
        (
            _day(today_key, provider="codex", offset=0),
            _day(today_key, provider="claude", offset=60),
            _day(yesterday_key, coverage=HistoryCoverage.PARTIAL),
            _day(old_key),
        ),
        range_days=7,
        now=NOW,
        timezone_offset_minutes=0,
    )

    assert projection.observed_days == 2
    assert projection.missing_days == 5
    assert len(projection.rows) == 3
    assert projection.health_label == "Partial observation"


def test_summary_counts_one_provider_day_across_split_timezone_offsets() -> None:
    """DST or travel offset rows for one provider and date remain one provider-day."""
    today_key = datetime.fromtimestamp(NOW, timezone.utc).date().isoformat()
    projection = project_operator_history(
        (
            _day(today_key, provider="codex", offset=-360),
            _day(today_key, provider="codex", offset=-300),
        ),
        range_days=7,
        now=NOW,
    )

    assert projection.summary_sentences[0] == "Observed 1 provider-days across 1 local days."


def test_neutral_summary_is_bounded_deterministic_and_nonjudgmental() -> None:
    """Reflect copy may describe facts but cannot grade, rank, target, or infer causality."""
    rows = (
        _day(completed=3, failed=1, needs_user=2, sample_count=7),
        _day("2027-01-14", provider="claude", completed=2, failed=0, sample_count=3),
    )

    first = project_operator_history(rows, range_days=7, now=NOW)
    second = project_operator_history(tuple(reversed(rows)), range_days=7, now=NOW)

    assert first == second
    assert 1 <= len(first.summary_sentences) <= 3
    combined = " ".join(first.summary_sentences).casefold()
    for forbidden in (
        "score",
        "target",
        "streak",
        "record",
        "ranking",
        "rank",
        "grade",
        "because",
        "caused",
        "productive",
        "performance",
    ):
        assert forbidden not in combined


def test_no_observation_row_requires_zero_counts() -> None:
    """A missing-evidence marker cannot carry fabricated observed counters."""
    with pytest.raises(HistoryValidationError):
        _day(
            coverage=HistoryCoverage.NO_OBSERVATION,
            completed=1,
            sample_count=0,
        )


def test_observed_row_rejects_counters_that_exceed_its_sample_count() -> None:
    """A corrupt retained row cannot inflate transition facts beyond admitted samples."""
    with pytest.raises(HistoryValidationError):
        _day(completed=2, sample_count=1)
