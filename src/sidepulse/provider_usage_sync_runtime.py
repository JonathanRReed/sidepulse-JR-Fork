"""Publish, fetch, authenticate, and merge cross-Mac provider usage."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .provider_feature_settings import ProviderInstanceSharingProjection
from .provider_instances import REMOTE_SHARING_STATUS_ONLY
from .provider_usage_platform import ProviderSourceState
from .provider_usage_runtime import ProviderUsageState
from .provider_usage_sync import (
    MAX_SYNC_PACKET_BYTES,
    MergedProviderSync,
    ProviderSyncPacket,
    StaleSyncPacketError,
    decode_signed_packet,
    encode_signed_packet,
    merge_provider_sync,
)
from .provider_usage_sync_settings import ProviderSyncPeer, ProviderSyncSettings
from .provider_usage_sync_transport import (
    SftpFetchResult,
    fetch_peer_packet,
    publish_local_packet,
)


@dataclass(frozen=True, slots=True)
class PeerSyncHealth:
    peer_id: str
    reachable: bool
    reason: str | None
    generated_at: float | None


@dataclass(frozen=True, slots=True)
class ProviderSyncRefresh:
    enabled: bool
    local_packet: ProviderSyncPacket | None
    remote_packets: tuple[ProviderSyncPacket, ...]
    merged: MergedProviderSync | None
    health: tuple[PeerSyncHealth, ...]
    refreshed_at: float


def _load_sharing_projection(
    loader: Callable[[], ProviderInstanceSharingProjection | object] | None,
) -> ProviderInstanceSharingProjection | None:
    if loader is None:
        return None
    try:
        loaded = loader()
        sharing = getattr(loaded, "sharing", loaded)
    except Exception:
        return None
    if type(sharing) is not ProviderInstanceSharingProjection:
        return None
    return sharing


def build_local_sync_packet(
    state: ProviderUsageState,
    settings: ProviderSyncSettings,
    *,
    generated_at: float,
    sharing: ProviderInstanceSharingProjection | object | None = None,
) -> ProviderSyncPacket:
    if type(state) is not ProviderUsageState or type(settings) is not ProviderSyncSettings:
        raise ValueError("invalid provider sync input")
    if settings.device_id is None:
        raise ValueError("provider sync has no device id")

    def shares_status(snapshot) -> bool:
        if type(sharing) is not ProviderInstanceSharingProjection:
            return False
        try:
            policy = sharing.provider(
                snapshot.provider_id,
                snapshot.source_instance_id,
            )
        except (StopIteration, ValueError):
            return False
        return policy.remote_sharing_choice == REMOTE_SHARING_STATUS_ONLY

    def status_only_snapshot(snapshot):
        return replace(
            snapshot,
            account_label=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            model_count=0,
            estimated_cost_usd=None,
            cache_savings_usd=None,
        )

    quota_snapshots = ()
    if "quota" in settings.categories:
        quota_snapshots = tuple(
            status_only_snapshot(snapshot)
            for snapshot in state.snapshots
            if snapshot.state in {ProviderSourceState.READY, ProviderSourceState.STALE}
            and snapshot.lanes
            and shares_status(snapshot)
        )
    # The current bounded profile vocabulary is `never` or `status_only`.
    # Neither permits token counts, cost estimates, or cache-savings totals.
    machine_usage = ()
    return ProviderSyncPacket(
        schema_version=1,
        device_id=settings.device_id,
        generated_at=generated_at,
        quota_snapshots=quota_snapshots,
        machine_usage=machine_usage,
        categories=settings.categories,
    )


def _secret(credentials, account: str) -> bytes | None:
    try:
        result = credentials.get("sidepulse-sync", account)
    except Exception:
        return None
    encoded = getattr(result, "secret", None)
    if not getattr(result, "available", False) or not isinstance(encoded, str):
        return None
    try:
        value = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        return None
    return value if 24 <= len(value) <= 128 else None


class ProviderSyncRuntime:
    def __init__(
        self,
        *,
        settings_loader: Callable[[], ProviderSyncSettings | object],
        sharing_loader: Callable[[], ProviderInstanceSharingProjection | object]
        | None = None,
        credentials,
        local_directory: Path,
        fetcher: Callable[[ProviderSyncPeer], SftpFetchResult] = fetch_peer_packet,
        publisher: Callable[[bytes, Path], object] = publish_local_packet,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings_loader = settings_loader
        self._sharing_loader = sharing_loader
        self._credentials = credentials
        self._local_directory = Path(local_directory)
        self._fetcher = fetcher
        self._publisher = publisher
        self._clock = clock

    def _settings(self) -> ProviderSyncSettings:
        loaded = self._settings_loader()
        settings = getattr(loaded, "settings", loaded)
        if type(settings) is not ProviderSyncSettings:
            raise ValueError("invalid provider sync settings")
        return settings

    def _sharing(self) -> ProviderInstanceSharingProjection | None:
        return _load_sharing_projection(self._sharing_loader)

    def refresh(self, state: ProviderUsageState) -> ProviderSyncRefresh:
        refreshed_at = float(self._clock())
        settings = self._settings()
        if not settings.enabled:
            return ProviderSyncRefresh(False, None, (), None, (), refreshed_at)
        local_packet = build_local_sync_packet(
            state,
            settings,
            generated_at=refreshed_at,
            sharing=self._sharing(),
        )
        remotes = []
        health = []
        for peer in settings.peers:
            secret = _secret(self._credentials, peer.secret_account)
            if secret is None:
                health.append(
                    PeerSyncHealth(
                        peer.peer_id,
                        False,
                        "pairing_secret_missing",
                        None,
                    )
                )
                continue
            try:
                encoded_local = encode_signed_packet(local_packet, secret)
                self._publisher(
                    encoded_local,
                    self._local_directory / f"{peer.peer_id}.packet",
                )
            except Exception:
                health.append(
                    PeerSyncHealth(peer.peer_id, False, "local_publish_failed", None)
                )
                continue
            try:
                fetched = self._fetcher(peer)
            except Exception:
                fetched = SftpFetchResult(
                    peer.peer_id,
                    False,
                    None,
                    "sftp_unavailable",
                )
            if type(fetched) is not SftpFetchResult or not fetched.reachable:
                health.append(
                    PeerSyncHealth(
                        peer.peer_id,
                        False,
                        getattr(fetched, "reason", None) or "sftp_failed",
                        None,
                    )
                )
                continue
            try:
                remote = decode_signed_packet(
                    fetched.packet or b"", secret, now=refreshed_at
                )
            except StaleSyncPacketError:
                health.append(
                    PeerSyncHealth(peer.peer_id, False, "packet_stale", None)
                )
                continue
            except ValueError:
                health.append(
                    PeerSyncHealth(peer.peer_id, False, "packet_authentication_failed", None)
                )
                continue
            if remote.device_id != peer.peer_id:
                health.append(
                    PeerSyncHealth(peer.peer_id, False, "peer_identity_mismatch", None)
                )
                continue
            remotes.append(remote)
            # Cache the verified envelope locally so the Usage Center can
            # render "across synced Macs" without ever fetching -- the
            # window path re-verifies signature and freshness on read.
            try:
                self._publisher(
                    fetched.packet,
                    self._local_directory / f"{peer.peer_id}.remote.packet",
                )
            except Exception:
                pass
            health.append(
                PeerSyncHealth(peer.peer_id, True, None, remote.generated_at)
            )
        remote_packets = tuple(remotes)
        merged = merge_provider_sync(local_packet, remote_packets)
        return ProviderSyncRefresh(
            True,
            local_packet,
            remote_packets,
            merged,
            tuple(health),
            refreshed_at,
        )


def load_cached_merged_sync(
    state: ProviderUsageState,
    *,
    settings_loader: Callable[[], ProviderSyncSettings | object],
    sharing_loader: Callable[[], ProviderInstanceSharingProjection | object]
    | None = None,
    credentials,
    local_directory: Path,
    now: float | None = None,
) -> MergedProviderSync | None:
    """Merge the LOCAL usage state with already-synced peer documents.

    Strictly offline: reads only the verified remote envelopes the last
    sync pull cached beside the published packets. Signature and
    freshness are re-checked on read; any per-peer failure is skipped.
    Returns None unless sync is enabled with at least one peer, so the
    Usage Center's "across this Mac" line stays honest when sync is off.
    """
    current = time.time() if now is None else float(now)
    try:
        loaded = settings_loader()
        settings = getattr(loaded, "settings", loaded)
        if (
            type(settings) is not ProviderSyncSettings
            or not settings.enabled
            or settings.device_id is None
            or not settings.peers
        ):
            return None
        local_packet = build_local_sync_packet(
            state,
            settings,
            generated_at=current,
            sharing=_load_sharing_projection(sharing_loader),
        )
    except Exception:
        return None
    remotes = []
    directory = Path(local_directory)
    for peer in settings.peers:
        secret = _secret(credentials, peer.secret_account)
        if secret is None:
            continue
        cached = directory / f"{peer.peer_id}.remote.packet"
        try:
            if cached.stat().st_size > MAX_SYNC_PACKET_BYTES:
                continue
            remote = decode_signed_packet(cached.read_bytes(), secret, now=current)
        except (OSError, ValueError):
            continue
        if remote.device_id == peer.peer_id:
            remotes.append(remote)
    return merge_provider_sync(local_packet, tuple(remotes))


__all__ = [
    "PeerSyncHealth",
    "ProviderSyncRefresh",
    "ProviderSyncRuntime",
    "build_local_sync_packet",
    "load_cached_merged_sync",
]
