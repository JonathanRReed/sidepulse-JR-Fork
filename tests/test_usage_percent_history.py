"""Percent-history recording and the all-provider chart projection."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from types import SimpleNamespace

from sidepulse.persistence_writer import (
    PersistenceDisposition,
    SerialPersistenceWriter,
)
from sidepulse.provider_feature_settings import (
    ProviderInstanceRetentionPolicy,
    ProviderInstanceRetentionProjection,
)
from sidepulse.usage_percent_history import (
    append_percent_observations,
    filter_new_observations,
    percent_graph_model,
    record_state_observations,
)

NOW = datetime(2026, 8, 21, 12, 0, 0)
NOW_EPOCH = NOW.timestamp()


def _line(provider, lane, percent, epoch, *, source_instance_id=None):
    payload = {
        "provider_id": provider,
        "lane_id": lane,
        "remaining_percent": percent,
        "observed_at_epoch": epoch,
    }
    if source_instance_id is not None:
        payload["source_instance_id"] = source_instance_id
    return (
        json.dumps(payload)
        + "\n"
    )


def _retention(*policies):
    return ProviderInstanceRetentionProjection(
        tuple(
            ProviderInstanceRetentionPolicy(provider, instance, days)
            for provider, instance, days in policies
        )
    )


def test_filter_records_first_sight_and_movement_only() -> None:
    fresh, updated = filter_new_observations(
        {}, [("grok", "default", "weekly", 88.0)], now_epoch=NOW_EPOCH
    )
    assert [record["provider_id"] for record in fresh] == ["grok"]
    assert fresh[0]["source_instance_id"] == "default"

    # Unmoved and recent: nothing new to say.
    fresh2, updated2 = filter_new_observations(
        updated, [("grok", "default", "weekly", 88.4)], now_epoch=NOW_EPOCH + 60
    )
    assert fresh2 == []

    # Real movement records immediately.
    fresh3, _ = filter_new_observations(
        updated2, [("grok", "default", "weekly", 84.0)], now_epoch=NOW_EPOCH + 120
    )
    assert [record["remaining_percent"] for record in fresh3] == [84.0]

    # Long silence records even without movement (heartbeat).
    fresh4, _ = filter_new_observations(
        updated, [("grok", "default", "weekly", 88.0)], now_epoch=NOW_EPOCH + 3600
    )
    assert len(fresh4) == 1


def test_filter_keeps_same_provider_lane_independent_per_source_instance() -> None:
    fresh, updated = filter_new_observations(
        {},
        [
            ("claude", "default", "weekly", 71.0),
            ("claude", "work", "weekly", 63.0),
        ],
        now_epoch=NOW_EPOCH,
    )

    assert [record["source_instance_id"] for record in fresh] == ["default", "work"]
    assert set(updated) == {
        ("claude", "default", "weekly"),
        ("claude", "work", "weekly"),
    }


def test_filter_rejects_junk() -> None:
    fresh, _ = filter_new_observations(
        {},
        [
            ("", "default", "weekly", 50.0),
            ("grok", "", "weekly", 50.0),
            ("grok", "default", "", 50.0),
            ("grok", "default", "weekly", 250.0),
            ("grok", "default", "five-hour", None),
        ],
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


def test_graph_model_keeps_same_provider_instances_as_exact_series() -> None:
    day = 86_400.0
    text = "".join(
        (
            # Legacy rows belong to the default instance and keep the
            # provider-only label users already saw.
            _line("claude", "weekly", 80.0, NOW_EPOCH - day),
            _line(
                "claude",
                "weekly",
                60.0,
                NOW_EPOCH - 2 * day,
                source_instance_id="work",
            ),
            _line(
                "claude",
                "weekly",
                30.0,
                NOW_EPOCH,
                source_instance_id="work",
            ),
        )
    )

    model = percent_graph_model(
        text,
        days=3,
        provider_ids=("claude",),
        now=NOW,
    )

    by_identity = {
        series["identity"]: series
        for series in model["series"]
    }
    assert tuple(by_identity) == (
        ("claude", "default"),
        ("claude", "work"),
    )
    assert by_identity[("claude", "default")]["label"] == "claude"
    assert by_identity[("claude", "default")]["values"] == (-1.0, 80.0, 80.0)
    assert by_identity[("claude", "work")]["label"] == "claude · work"
    assert by_identity[("claude", "work")]["values"] == (60.0, 60.0, 30.0)


def test_append_writes_private_jsonl(tmp_path) -> None:
    target = tmp_path / "usage-percent-history.jsonl"
    records, _ = filter_new_observations(
        {}, [("claude", "default", "weekly", 71.0)], now_epoch=NOW_EPOCH
    )
    assert append_percent_observations(target, records) == 1
    assert append_percent_observations(target, []) == 0
    stored = json.loads(target.read_text().strip())
    assert stored["provider_id"] == "claude"
    assert stored["source_instance_id"] == "default"
    assert stored["remaining_percent"] == 71.0


def test_append_prunes_each_exact_instance_by_its_retention(tmp_path) -> None:
    day = 86_400.0
    target = tmp_path / "usage-percent-history.jsonl"
    target.write_text(
        "".join(
            (
                _line(
                    "claude",
                    "weekly",
                    80.0,
                    NOW_EPOCH - 8 * day,
                    source_instance_id="default",
                ),
                _line(
                    "claude",
                    "weekly",
                    70.0,
                    NOW_EPOCH - 8 * day,
                    source_instance_id="work",
                ),
                _line(
                    "claude",
                    "weekly",
                    60.0,
                    NOW_EPOCH - day,
                    source_instance_id="disabled",
                ),
                _line(
                    "claude",
                    "weekly",
                    50.0,
                    NOW_EPOCH - day,
                    source_instance_id="unknown",
                ),
            )
        )
    )
    retention = _retention(
        ("claude", "default", 7),
        ("claude", "work", 30),
        ("claude", "disabled", 0),
    )

    assert (
        append_percent_observations(
            target,
            [],
            retention_projection=retention,
            now_epoch=NOW_EPOCH,
        )
        == 0
    )

    stored = [json.loads(line) for line in target.read_text().splitlines()]
    assert [(row["source_instance_id"], row["remaining_percent"]) for row in stored] == [
        ("work", 70.0)
    ]


def test_append_migrates_legacy_rows_to_default_instance(tmp_path) -> None:
    target = tmp_path / "usage-percent-history.jsonl"
    target.write_text(_line("claude", "weekly", 72.0, NOW_EPOCH))

    append_percent_observations(
        target,
        [],
        retention_projection=_retention(("claude", "default", 7)),
        now_epoch=NOW_EPOCH,
    )

    stored = json.loads(target.read_text().strip())
    assert stored["source_instance_id"] == "default"


def test_recording_advances_dedupe_only_after_writer_accepts(monkeypatch, tmp_path) -> None:
    controller = SimpleNamespace()
    lane = SimpleNamespace(lane_id="weekly", remaining_percent=71.0)
    snapshot = SimpleNamespace(provider_id="claude", lanes=(lane,))

    class RefusingWriter:
        @staticmethod
        def submit(*_args, **_kwargs):
            return PersistenceDisposition.REFUSED_FULL

    assert (
        record_state_observations(
            controller,
            (snapshot,),
            writer=RefusingWriter(),
        )
        is False
    )
    assert not hasattr(controller, "_sidepulse_percent_history_last")

    target = tmp_path / "usage-percent-history.jsonl"
    monkeypatch.setattr(
        "sidepulse.usage_percent_history.default_percent_history_path",
        lambda: target,
    )
    writer = SerialPersistenceWriter()
    assert record_state_observations(controller, (snapshot,), writer=writer) is True
    assert writer.close(timeout_seconds=1.0) is True
    assert target.exists()
    assert controller._sidepulse_percent_history_last


def test_recording_dedupes_exact_source_instances_independently(monkeypatch, tmp_path) -> None:
    controller = SimpleNamespace()
    snapshots = (
        SimpleNamespace(
            provider_id="claude",
            source_instance_id="default",
            lanes=(SimpleNamespace(lane_id="weekly", remaining_percent=71.0),),
        ),
        SimpleNamespace(
            provider_id="claude",
            source_instance_id="work",
            lanes=(SimpleNamespace(lane_id="weekly", remaining_percent=63.0),),
        ),
    )
    target = tmp_path / "usage-percent-history.jsonl"
    monkeypatch.setattr(
        "sidepulse.usage_percent_history.default_percent_history_path",
        lambda: target,
    )
    writer = SerialPersistenceWriter()

    assert (
        record_state_observations(
            controller,
            snapshots,
            writer=writer,
            retention_projection=_retention(
                ("claude", "default", 7),
                ("claude", "work", 30),
            ),
        )
        is True
    )
    assert writer.close(timeout_seconds=1.0) is True

    stored = [json.loads(line) for line in target.read_text().splitlines()]
    assert [row["source_instance_id"] for row in stored] == ["default", "work"]
    assert set(controller._sidepulse_percent_history_last) == {
        ("claude", "default", "weekly"),
        ("claude", "work", "weekly"),
    }


def test_retention_change_prunes_disabled_instance_without_fresh_sample(
    monkeypatch,
    tmp_path,
) -> None:
    controller = SimpleNamespace()
    target = tmp_path / "usage-percent-history.jsonl"
    target.write_text(
        _line(
            "claude",
            "weekly",
            71.0,
            NOW_EPOCH,
            source_instance_id="work",
        )
    )
    snapshot = SimpleNamespace(
        provider_id="claude",
        source_instance_id="work",
        lanes=(SimpleNamespace(lane_id="weekly", remaining_percent=71.0),),
    )
    monkeypatch.setattr(
        "sidepulse.usage_percent_history.default_percent_history_path",
        lambda: target,
    )
    writer = SerialPersistenceWriter()

    assert (
        record_state_observations(
            controller,
            (snapshot,),
            writer=writer,
            retention_projection=_retention(("claude", "work", 0)),
        )
        is True
    )
    assert writer.close(timeout_seconds=1.0) is True

    assert target.read_text() == ""
    assert not getattr(controller, "_sidepulse_percent_history_last", {})


def test_accepted_append_failure_keeps_observation_retryable(monkeypatch, tmp_path) -> None:
    controller = SimpleNamespace()
    lane = SimpleNamespace(lane_id="weekly", remaining_percent=63.0)
    snapshot = SimpleNamespace(provider_id="claude", lanes=(lane,))

    def fail_append(*_args, **_kwargs):
        raise OSError("private payload must not surface")

    monkeypatch.setattr(
        "sidepulse.usage_percent_history.append_percent_observations",
        fail_append,
    )
    failed_writer = SerialPersistenceWriter()
    assert record_state_observations(controller, (snapshot,), writer=failed_writer) is True
    assert failed_writer.close(timeout_seconds=1.0) is True
    assert not getattr(controller, "_sidepulse_percent_history_last", {})
    assert not controller._sidepulse_percent_history_pending

    target = tmp_path / "usage-percent-history.jsonl"
    monkeypatch.setattr(
        "sidepulse.usage_percent_history.append_percent_observations",
        append_percent_observations,
    )
    monkeypatch.setattr(
        "sidepulse.usage_percent_history.default_percent_history_path",
        lambda: target,
    )
    retry_writer = SerialPersistenceWriter()
    assert record_state_observations(controller, (snapshot,), writer=retry_writer) is True
    assert retry_writer.close(timeout_seconds=1.0) is True

    assert target.exists()
    assert controller._sidepulse_percent_history_last


def test_newer_sample_is_ordered_behind_a_pending_append(monkeypatch, tmp_path) -> None:
    controller = SimpleNamespace()
    first = SimpleNamespace(
        provider_id="claude",
        lanes=(SimpleNamespace(lane_id="weekly", remaining_percent=71.0),),
    )
    second = SimpleNamespace(
        provider_id="claude",
        lanes=(SimpleNamespace(lane_id="weekly", remaining_percent=68.0),),
    )
    target = tmp_path / "usage-percent-history.jsonl"
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def delayed_append(path, records):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            release.wait(2.0)
        return append_percent_observations(path, records)

    monkeypatch.setattr(
        "sidepulse.usage_percent_history.default_percent_history_path",
        lambda: target,
    )
    monkeypatch.setattr(
        "sidepulse.usage_percent_history.append_percent_observations",
        delayed_append,
    )
    writer = SerialPersistenceWriter()
    assert record_state_observations(controller, (first,), writer=writer) is True
    assert started.wait(1.0)
    assert record_state_observations(controller, (second,), writer=writer) is True

    release.set()
    assert writer.close(timeout_seconds=1.0) is True

    stored = [json.loads(line) for line in target.read_text().splitlines()]
    assert [row["remaining_percent"] for row in stored] == [71.0, 68.0]
    assert not controller._sidepulse_percent_history_pending
    assert (
        controller._sidepulse_percent_history_last[
            ("claude", "default", "weekly")
        ][0]
        == 68.0
    )
