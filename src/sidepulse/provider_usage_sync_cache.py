"""Worker-refreshed cross-Mac sync evidence for memory-only UI lookup."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from .provider_feature_settings import ProviderInstanceSharingProjection
from .provider_usage_runtime import ProviderUsageState
from .provider_usage_sync import MergedProviderSync

# Only the worker can revalidate signed packet timestamps. Keep the memory
# lease short so an unchanged local snapshot cannot pin remote evidence.
_TTL_SECONDS = 30.0
SharingSignature = tuple[tuple[str, str, str], ...]
_memo: (
    tuple[
        float,
        tuple[object, ...],
        SharingSignature | None,
        MergedProviderSync | None,
    ]
    | None
) = None
_memo_generation = 0


def default_provider_sync_directory(home: Path | None = None) -> Path:
    base = Path.home() if home is None else Path(home)
    return base / ".local" / "state" / "sidepulse" / "provider-sync"


def sharing_projection_signature(
    sharing: ProviderInstanceSharingProjection,
) -> SharingSignature:
    """Return a stable, non-secret signature for outbound sharing choices."""

    if type(sharing) is not ProviderInstanceSharingProjection:
        raise TypeError("expected ProviderInstanceSharingProjection")
    return tuple(
        (
            policy.provider_id,
            policy.source_instance_id,
            policy.remote_sharing_choice,
        )
        for policy in sharing.providers
    )


def invalidate_cached_merged_sync(
    *,
    sharing_signature: SharingSignature | None = None,
) -> None:
    """Fence worker publication and drop evidence from another policy."""

    global _memo, _memo_generation
    _memo_generation += 1
    if sharing_signature is None or _memo is None or _memo[2] != sharing_signature:
        _memo = None


def cached_merged_sync(
    state,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> MergedProviderSync | None:
    """Return one value-keyed merge without disk, Keychain, or network work."""
    global _memo
    if type(state) is not ProviderUsageState:
        return None
    memo = _memo
    if memo is None:
        return None
    elapsed = float(monotonic()) - memo[0]
    if not 0.0 <= elapsed < _TTL_SECONDS:
        _memo = None
        return None
    if memo[1] == state.snapshots:
        return memo[3]
    return None


def refresh_cached_merged_sync(
    state: ProviderUsageState,
    *,
    loader: Callable[[ProviderUsageState], MergedProviderSync | None] | None = None,
    sharing_signature: SharingSignature | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> MergedProviderSync | None:
    """Refresh local sync evidence on a worker, then publish one atomic memo."""
    if type(state) is not ProviderUsageState:
        raise ValueError("invalid provider usage state")
    generation = _memo_generation
    resolved_signature = sharing_signature
    load = loader
    try:
        if load is None:
            from .provider_credential_store import ProviderCredentialStore
            from .provider_feature_settings import project_instance_policies
            from .provider_usage_settings import load_provider_usage_settings
            from .provider_usage_sync_runtime import load_cached_merged_sync
            from .provider_usage_sync_settings import (
                default_provider_sync_settings_path,
                load_provider_sync_settings,
            )

            sharing = project_instance_policies(
                load_provider_usage_settings().settings
            ).sharing
            resolved_signature = sharing_projection_signature(sharing)
            merged = load_cached_merged_sync(
                state,
                settings_loader=lambda: load_provider_sync_settings(
                    default_provider_sync_settings_path()
                ),
                sharing_loader=lambda: sharing,
                credentials=ProviderCredentialStore(),
                local_directory=default_provider_sync_directory(),
            )
        else:
            merged = load(state)
    except Exception:
        merged = None
    if merged is not None and type(merged) is not MergedProviderSync:
        merged = None
    global _memo
    if generation != _memo_generation:
        return None
    _memo = (float(monotonic()), state.snapshots, resolved_signature, merged)
    return merged


__all__ = [
    "cached_merged_sync",
    "default_provider_sync_directory",
    "invalidate_cached_merged_sync",
    "refresh_cached_merged_sync",
    "sharing_projection_signature",
]
