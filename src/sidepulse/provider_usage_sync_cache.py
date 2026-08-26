"""Best-effort cached view of cross-Mac sync for window-path callers.

The Usage Center window must never block on SFTP (or fail because a
peer is asleep), so this module reads only the LOCAL documents the last
sync pull verified and cached (see load_cached_merged_sync). A short
TTL memo keeps the keychain/settings/file reads off every refresh tick;
any failure degrades to None, which the projection renders as the
plain "across this Mac" totals.
"""

from __future__ import annotations

import time
from pathlib import Path

from .provider_usage_sync import MergedProviderSync

_TTL_SECONDS = 30.0
_memo: tuple[float, object, MergedProviderSync | None] | None = None


def default_provider_sync_directory(home: Path | None = None) -> Path:
    base = Path.home() if home is None else Path(home)
    return base / ".local" / "state" / "sidepulse" / "provider-sync"


def cached_merged_sync(state) -> MergedProviderSync | None:
    """Merged sync for this usage state, memoized; never fetches."""
    global _memo
    monotonic = time.monotonic()
    if (
        _memo is not None
        and monotonic - _memo[0] < _TTL_SECONDS
        and _memo[1] is state
    ):
        return _memo[2]
    try:
        from .provider_credential_store import ProviderCredentialStore
        from .provider_usage_sync_runtime import load_cached_merged_sync
        from .provider_usage_sync_settings import (
            default_provider_sync_settings_path,
            load_provider_sync_settings,
        )

        merged = load_cached_merged_sync(
            state,
            settings_loader=lambda: load_provider_sync_settings(
                default_provider_sync_settings_path()
            ),
            credentials=ProviderCredentialStore(),
            local_directory=default_provider_sync_directory(),
        )
    except Exception:
        merged = None
    _memo = (monotonic, state, merged)
    return merged


__all__ = ["cached_merged_sync", "default_provider_sync_directory"]
