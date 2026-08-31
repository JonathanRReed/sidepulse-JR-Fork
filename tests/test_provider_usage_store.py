from __future__ import annotations

import json
from pathlib import Path

from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from sidepulse.provider_usage_runtime import ProviderUsageState
from sidepulse.provider_usage_store import (
    PROVIDER_USAGE_STORE_SCHEMA_VERSION,
    load_provider_usage_state,
    save_provider_usage_state,
)


def state():
    lane = UsageLane(
        provider_id="codex",
        lane_id="spark-weekly",
        label="Spark Weekly",
        remaining_percent=23,
        reset_at=3000,
        scope="all",
        model="spark",
        feature=None,
        bindable=False,
        source_id="codex-rollouts",
    )
    snapshot = ProviderUsageSnapshot(
        provider_id="codex",
        account_label="account-fixture",
        observed_at=1000,
        state=ProviderSourceState.READY,
        reason_code=None,
        action_label=None,
        lanes=(lane,),
        input_tokens=100,
        cached_input_tokens=25,
        output_tokens=50,
        model_count=2,
        estimated_cost_usd=1.5,
        cache_savings_usd=0.2,
        credits_remaining=10,
        incident=None,
    )
    return ProviderUsageState((snapshot,), 1000, 1060, False)


def test_state_round_trip_preserves_dynamic_lanes_and_qol_fields(tmp_path: Path):
    target = tmp_path / "usage.json"
    save_provider_usage_state(state(), target)
    loaded = load_provider_usage_state(target)
    assert loaded == state()
    assert json.loads(target.read_text())["schema_version"] == PROVIDER_USAGE_STORE_SCHEMA_VERSION


def test_invalid_or_future_document_fails_closed(tmp_path: Path):
    target = tmp_path / "usage.json"
    target.write_text(json.dumps({"schema_version": PROVIDER_USAGE_STORE_SCHEMA_VERSION + 1}))
    assert load_provider_usage_state(target).snapshots == ()
    target.write_text("{bad")
    assert load_provider_usage_state(target).snapshots == ()


def test_store_rejects_refreshing_state(tmp_path: Path):
    target = tmp_path / "usage.json"
    try:
        save_provider_usage_state(
            ProviderUsageState(state().snapshots, 1000, 1060, True),
            target,
        )
    except ValueError as exc:
        assert "refreshing" in str(exc)
    else:
        raise AssertionError("transient refreshing state persisted")


def test_store_preserves_two_same_provider_instances(tmp_path: Path):
    first = state().snapshots[0]
    second = type(first)(
        provider_id=first.provider_id,
        account_label="work@example.invalid",
        observed_at=first.observed_at + 1,
        state=first.state,
        reason_code=first.reason_code,
        action_label=first.action_label,
        lanes=first.lanes,
        input_tokens=first.input_tokens,
        cached_input_tokens=first.cached_input_tokens,
        output_tokens=first.output_tokens,
        model_count=first.model_count,
        estimated_cost_usd=first.estimated_cost_usd,
        cache_savings_usd=first.cache_savings_usd,
        credits_remaining=first.credits_remaining,
        incident=first.incident,
        source_instance_id="work",
    )
    target = tmp_path / "usage.json"
    save_provider_usage_state(ProviderUsageState((first, second), 1000, 1060, False), target)
    loaded = load_provider_usage_state(target)
    assert {(item.provider_id, item.source_instance_id) for item in loaded.snapshots} == {
        ("codex", "default"),
        ("codex", "work"),
    }


def test_store_migrates_snapshot_without_instance_to_default(tmp_path: Path):
    target = tmp_path / "usage.json"
    save_provider_usage_state(state(), target)
    document = json.loads(target.read_text())
    document["schema_version"] = 1
    del document["snapshots"][0]["source_instance_id"]
    target.write_text(json.dumps(document))
    loaded = load_provider_usage_state(target)
    assert loaded.snapshots[0].source_instance_id == "default"
