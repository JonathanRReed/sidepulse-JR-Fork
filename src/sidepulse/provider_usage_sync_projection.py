"""Apply freshest cross-Mac account quota without double-counting local usage."""

from __future__ import annotations

from dataclasses import replace

from .provider_usage_runtime import ProviderUsageState
from .provider_usage_sync import MergedProviderSync


def apply_merged_sync_to_state(
    local: ProviderUsageState,
    merged: MergedProviderSync | None,
) -> ProviderUsageState:
    if type(local) is not ProviderUsageState:
        raise ValueError("invalid local provider usage state")
    if merged is None:
        return local
    remote_by_identity = {
        snapshot.identity: snapshot for snapshot in merged.quota_snapshots
    }
    snapshots = []
    for current in local.snapshots:
        remote = remote_by_identity.get(current.identity)
        if remote is None or remote.observed_at <= current.observed_at:
            snapshots.append(current)
            continue
        snapshots.append(
            replace(
                current,
                account_label=remote.account_label or current.account_label,
                observed_at=remote.observed_at,
                state=remote.state,
                reason_code=remote.reason_code,
                action_label=remote.action_label,
                lanes=remote.lanes,
                credits_remaining=remote.credits_remaining,
                incident=remote.incident,
            )
        )
    return ProviderUsageState(
        tuple(snapshots),
        local.refreshed_at,
        local.next_refresh_at,
        local.refreshing,
    )


__all__ = ["apply_merged_sync_to_state"]
