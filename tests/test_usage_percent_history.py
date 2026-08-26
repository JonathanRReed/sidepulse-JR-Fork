"""Percent-history recording and the all-provider chart projection."""

from __future__ import annotations

import json
from datetime import datetime

from sidepulse.usage_percent_history import (
    append_percent_observations,
    filter_new_observations,
    percent_graph_model,
)

NOW = datetime(2026, 8, 21, 12, 0, 0)
NOW_EPOCH = NOW.timestamp()


def _line(provider, lane, percent, epoch):
    return (
        json.dumps(
            {
                "provider_id": provider,
                "lane_id": lane,
                "remaining_percent": percent,
                "observed_at_epoch": epoch,
            }
        )
        + "\n"
    )


def test_filter_records_first_sight_and_movement_only() -> None:
    fresh, updated = filter_new_observations(
        {}, [("grok", "weekly", 88.0)], now_epoch=NOW_EPOCH
    )
    assert [record["provider_id"] for record in fresh] == ["grok"]

    # Unmoved and recent: nothing new to say.
    fresh2, updated2 = filter_new_observations(
        updated, [("grok", "weekly", 88.4)], now_epoch=NOW_EPOCH + 60
    )
    assert fresh2 == []

    # Real movement records immediately.
    fresh3, _ = filter_new_observations(
        updated2, [("grok", "weekly", 84.0)], now_epoch=NOW_EPOCH + 120
    )
    assert [record["remaining_percent"] for record in fresh3] == [84.0]

    # Long silence records even without movement (heartbeat).
    fresh4, _ = filter_new_observations(
        updated, [("grok", "weekly", 88.0)], now_epoch=NOW_EPOCH + 3600
    )
    assert len(fresh4) == 1


def test_filter_rejects_junk() -> None:
    fresh, _ = filter_new_observations(
        {},
        [("", "weekly", 50.0), ("grok", "", 50.0), ("grok", "weekly", 250.0),
         ("grok", "five-hour", None)],
        now_epoch=NOW_EPOCH,
    )
    assert fresh == []


def test_graph_model_charts_every_provider_with_history() -> None:
    day = 86_400.0
    text = "".join(
        [
            _line("grok", "weekly", 90.0, NOW_EPOCH - 2 * day),
            _line("grok", "weekly", 70.0, NOW_EPOCH - 1 * day),
            _line("grok", "five-hour", 40.0, NOW_EPOCH - 1 * day),
            _line("devin", "monthly", 55.0, NOW_EPOCH),
            "not json\n",
        ]
    )
    model = percent_graph_model(
        text, days=3, provider_ids=("grok", "devin", "cursor"), now=NOW
    )
    assert model["metric"] == "percent"
    assert model["scale_max"] == 100.0
    by_provider = {
        series["provider_id"]: series["values"] for series in model["series"]
    }
    # cursor has no history: no line, not a zero line.
    assert set(by_provider) == {"grok", "devin"}
    # grok day -1 takes the WORST lane (40, not 70); today carries forward.
    assert by_provider["grok"] == (90.0, 40.0, 40.0)
    # devin's PRE-history days are gaps (negative sentinel the chart
    # skips), not a fabricated flat line -- the old backfill drew data
    # for days before any sample existed (audit, 2026-08-26).
    assert by_provider["devin"] == (-1.0, -1.0, 55.0)


def test_append_writes_private_jsonl(tmp_path) -> None:
    target = tmp_path / "usage-percent-history.jsonl"
    records, _ = filter_new_observations(
        {}, [("claude", "weekly", 71.0)], now_epoch=NOW_EPOCH
    )
    assert append_percent_observations(target, records) == 1
    assert append_percent_observations(target, []) == 0
    stored = json.loads(target.read_text().strip())
    assert stored["provider_id"] == "claude"
    assert stored["remaining_percent"] == 71.0
