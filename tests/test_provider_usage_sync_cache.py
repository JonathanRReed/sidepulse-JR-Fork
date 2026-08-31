from __future__ import annotations

from sidepulse import provider_usage_sync_cache as sync_cache
from sidepulse.provider_feature_settings import ProviderInstanceSharingProjection
from sidepulse.provider_usage_platform import ProviderSourceState, ProviderUsageSnapshot
from sidepulse.provider_usage_runtime import ProviderUsageState
from sidepulse.provider_usage_sync import MergedProviderSync


def _snapshot(observed_at: float = 1000.0) -> ProviderUsageSnapshot:
    return ProviderUsageSnapshot(
        provider_id="codex",
        account_label=None,
        observed_at=observed_at,
        state=ProviderSourceState.DISABLED,
        reason_code=None,
        action_label=None,
        lanes=(),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
    )


def _merged() -> MergedProviderSync:
    return MergedProviderSync((), (), 0, 0, 0, None, None)


def test_cached_merge_is_refreshed_once_then_reused_by_logical_snapshot_value(
    monkeypatch,
) -> None:
    monkeypatch.setattr(sync_cache, "_memo", None)
    first = ProviderUsageState((_snapshot(),), 1000.0, 1100.0, False)
    equivalent = ProviderUsageState((_snapshot(),), 2000.0, 2100.0, True)
    merged = _merged()
    loads: list[ProviderUsageState] = []

    result = sync_cache.refresh_cached_merged_sync(
        first,
        loader=lambda state: loads.append(state) or merged,
    )

    assert result is merged
    assert sync_cache.cached_merged_sync(equivalent) is merged
    assert loads == [first]


def test_cached_merge_never_reuses_another_logical_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(sync_cache, "_memo", None)
    first = ProviderUsageState((_snapshot(1000.0),), 1000.0, 1100.0, False)
    changed = ProviderUsageState((_snapshot(1001.0),), 1001.0, 1101.0, False)

    sync_cache.refresh_cached_merged_sync(first, loader=lambda _state: _merged())

    assert sync_cache.cached_merged_sync(changed) is None


def test_mismatched_lookup_does_not_evict_fresh_matching_evidence(monkeypatch) -> None:
    monkeypatch.setattr(sync_cache, "_memo", None)
    first = ProviderUsageState((_snapshot(1000.0),), 1000.0, 1100.0, False)
    changed = ProviderUsageState((_snapshot(1001.0),), 1001.0, 1101.0, False)
    merged = _merged()
    sync_cache.refresh_cached_merged_sync(
        first,
        loader=lambda _state: merged,
        monotonic=lambda: 100.0,
    )

    assert sync_cache.cached_merged_sync(changed, monotonic=lambda: 100.0) is None
    assert sync_cache.cached_merged_sync(first, monotonic=lambda: 100.0) is merged


def test_cached_merge_expires_without_reusing_identical_snapshots(monkeypatch) -> None:
    """A memo must be revalidated before remote packet freshness can drift."""

    monkeypatch.setattr(sync_cache, "_memo", None)
    state = ProviderUsageState((_snapshot(),), 1000.0, 1100.0, False)
    merged = _merged()

    sync_cache.refresh_cached_merged_sync(
        state,
        loader=lambda _state: merged,
        monotonic=lambda: 100.0,
    )

    assert sync_cache.cached_merged_sync(state, monotonic=lambda: 129.999) is merged
    assert sync_cache.cached_merged_sync(state, monotonic=lambda: 130.0) is None


def test_policy_invalidation_drops_worker_result_that_finishes_late(monkeypatch) -> None:
    """An old-policy worker must not republish after the policy fence moves."""

    monkeypatch.setattr(sync_cache, "_memo", None)
    monkeypatch.setattr(sync_cache, "_memo_generation", 0)
    state = ProviderUsageState((_snapshot(),), 1000.0, 1100.0, False)

    def load(_state):
        sync_cache.invalidate_cached_merged_sync(
            sharing_signature=(("codex", "default", "never"),)
        )
        return _merged()

    result = sync_cache.refresh_cached_merged_sync(
        state,
        loader=load,
        sharing_signature=(("codex", "default", "status_only"),),
        monotonic=lambda: 100.0,
    )

    assert result is None
    assert sync_cache.cached_merged_sync(state, monotonic=lambda: 100.0) is None


def test_default_worker_refresh_injects_privacy_safe_sharing_projection(
    monkeypatch,
) -> None:
    from sidepulse import provider_usage_sync_runtime

    captured = {}

    def load_cached(_state, **kwargs):
        captured.update(kwargs)
        return _merged()

    monkeypatch.setattr(sync_cache, "_memo", None)
    monkeypatch.setattr(provider_usage_sync_runtime, "load_cached_merged_sync", load_cached)

    sync_cache.refresh_cached_merged_sync(
        ProviderUsageState((_snapshot(),), 1000.0, 1100.0, False)
    )

    assert type(captured["sharing_loader"]()) is ProviderInstanceSharingProjection
