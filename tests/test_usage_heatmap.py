from __future__ import annotations

import os
import time
from datetime import datetime
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from sidepulse.usage_heatmap import build_usage_heatmap
from sidepulse.usage_stats import daily_buckets

UTC = ZoneInfo("UTC")
CHICAGO = ZoneInfo("America/Chicago")


def record(provider: str, session: str, stamp: datetime, *, tokens=(0, 0, 0, 0), dedupe="unique") -> tuple:
    return (provider, session, "model", stamp.timestamp(), *tokens, dedupe)


def test_totals_match_usage_stats_record_semantics_exactly():
    now = datetime(2026, 9, 4, 12, tzinfo=CHICAGO)
    records = [
        record("claude", "s1", now, tokens=(10, 20, 30, 40), dedupe="a"),
        record("codex", "s2", now, tokens=(5, 6, 7, 8), dedupe="b"),
    ]
    result = build_usage_heatmap(records, provider_ids=("claude", "codex"), days=7, now=now, timezone=CHICAGO)
    buckets = daily_buckets(records, days=7, now=now.replace(tzinfo=None))
    assert result.providers["claude"].totals.tokens == 100
    assert result.providers["codex"].totals.tokens == 26
    assert result.aggregate.totals.tokens == sum(
        bucket["providers"].get(provider, {}).get("tokens", 0)
        for bucket in buckets.values()
        for provider in ("claude", "codex")
    )


def test_local_calendar_handles_midnight_and_dst_boundaries():
    now = datetime(2026, 11, 2, 0, 30, tzinfo=CHICAGO)
    records = [
        record("claude", "before", datetime(2026, 11, 1, 5, 30, tzinfo=UTC), tokens=(1, 0, 0, 0), dedupe="a"),
        record("claude", "first-one", datetime(2026, 11, 1, 6, 30, tzinfo=UTC), tokens=(2, 0, 0, 0), dedupe="b"),
        record("claude", "second-one", datetime(2026, 11, 1, 7, 30, tzinfo=UTC), tokens=(4, 0, 0, 0), dedupe="c"),
    ]
    result = build_usage_heatmap(records, provider_ids=("claude",), days=7, now=now, timezone=CHICAGO)
    assert result.providers["claude"].cells[-2].day.isoformat() == "2026-11-01"
    assert result.providers["claude"].cells[-2].tokens == 7
    assert result.providers["claude"].cells[-2].sessions == 3


def test_omitted_timezone_uses_system_local_calendar(monkeypatch: pytest.MonkeyPatch):
    if not hasattr(time, "tzset"):
        pytest.skip("system timezone switching is unavailable")
    original = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    try:
        now = datetime(2026, 9, 4, 1, 0, tzinfo=CHICAGO)
        records = [record("claude", "s", datetime(2026, 9, 4, 1, 30, tzinfo=UTC), tokens=(5, 0, 0, 0))]
        result = build_usage_heatmap(records, provider_ids=("claude",), days=7, now=now)
        assert result.providers["claude"].cells[-2].tokens == 5
        assert result.timezone in {"America/Chicago", "CDT"}
    finally:
        if original is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original)
        time.tzset()


def test_selected_missing_provider_is_distinct_from_zero_activity_day():
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    result = build_usage_heatmap(
        [record("claude", "s", now, tokens=(10, 0, 0, 0))],
        provider_ids=("claude", "codex"),
        days=7,
        now=now,
        timezone=UTC,
    )
    assert tuple(result.providers) == ("claude", "codex")
    assert result.providers["codex"].data_status == "unavailable"
    assert result.providers["codex"].cells[-1].accessibility_label.endswith("data unavailable")
    assert result.providers["claude"].cells[-2].accessibility_label.endswith("zero activity")


def test_dedupe_is_global_but_sessions_are_provider_scoped():
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    records = [
        record("claude", "same", now, tokens=(10, 0, 0, 0), dedupe="copied"),
        record("claude", "other", now, tokens=(10, 0, 0, 0), dedupe="copied"),
        record("codex", "same", now, tokens=(20, 0, 0, 0), dedupe="codex-rollout"),
    ]
    result = build_usage_heatmap(records, provider_ids=("claude", "codex"), days=7, now=now, timezone=UTC)
    assert result.aggregate.totals.tokens == 30
    assert result.aggregate.totals.sessions == 2
    assert result.providers["claude"].totals.sessions == 1
    assert result.providers["codex"].totals.sessions == 1


@pytest.mark.parametrize(
    "bad_record",
    [
        (),
        ("claude",),
        ("claude", "s", "m", "bad", 1, 0, 0, 0, "x"),
        ("claude", "s", "m", float("nan"), 1, 0, 0, 0, "x"),
        ("claude", "s", "m", 0, True, 0, 0, 0, "x"),
        ("claude", "s", "m", 0, -1, 0, 0, 0, "x"),
        ("claude", "s", "m", 0, 1, 0, 0, 0, ""),
    ],
)
def test_malformed_records_are_skipped_without_marking_data_available(bad_record: tuple):
    result = build_usage_heatmap(
        [bad_record], provider_ids=("claude",), days=7, now=datetime(2026, 9, 4, tzinfo=UTC), timezone=UTC
    )
    assert result.providers["claude"].data_status == "unavailable"
    assert result.aggregate.data_status == "unavailable"


def test_outputs_are_deeply_immutable_and_provider_order_is_explicit():
    result = build_usage_heatmap(
        [], provider_ids=("codex", "claude"), days=7, now=datetime(2026, 9, 4, tzinfo=UTC), timezone=UTC
    )
    assert isinstance(result.providers, MappingProxyType)
    assert tuple(result.providers) == ("codex", "claude")
    with pytest.raises(TypeError):
        result.providers["other"] = result.aggregate  # type: ignore[index]


def test_intensity_has_a_stable_floor_so_one_tiny_event_is_not_maximum():
    now = datetime(2026, 9, 4, tzinfo=UTC)
    tiny = build_usage_heatmap(
        [record("claude", "s", now, tokens=(1, 0, 0, 0))], provider_ids=("claude",), days=7, now=now, timezone=UTC
    )
    substantial = build_usage_heatmap(
        [record("claude", "s", now, tokens=(100_000, 0, 0, 0))], provider_ids=("claude",), days=7, now=now, timezone=UTC
    )
    assert tiny.providers["claude"].cells[-1].intensity == 1
    assert substantial.providers["claude"].cells[-1].intensity == 4


def test_finite_input_and_output_bounds_are_enforced():
    now = datetime(2026, 9, 4, tzinfo=UTC)
    with pytest.raises(ValueError, match="providers"):
        build_usage_heatmap([], provider_ids=tuple(f"p{i}" for i in range(33)), now=now)
    with pytest.raises(ValueError, match="records"):
        build_usage_heatmap(iter(()), provider_ids=("claude",), now=now)
    with pytest.raises(ValueError, match="days"):
        build_usage_heatmap([], provider_ids=("claude",), days=8, now=now)


def test_large_admitted_history_keeps_exact_totals_in_a_bounded_grid():
    now = datetime(2026, 9, 4, tzinfo=UTC)
    records = [
        record("claude", f"s{index % 100}", now, tokens=(1, 0, 0, 0), dedupe=f"m{index}")
        for index in range(100_001)
    ]
    result = build_usage_heatmap(records, provider_ids=("claude",), days=365, now=now, timezone=UTC)
    assert result.aggregate.totals.tokens == 100_001
    assert result.aggregate.totals.sessions == 100
    assert len(result.aggregate.cells) == 365
