"""Publish, fetch, authenticate, and merge cross-Mac provider usage."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .provider_usage_platform import ProviderSourceState
from .provider_usage_runtime import ProviderUsageState
from .provider_usage_sync import (
    MachineUsageObservation,
    MergedProviderSync,
    ProviderSyncPacket,
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


def build_local_sync_packet(
    state: ProviderUsageState,
    settings: ProviderSyncSettings,
    *,
    generated_at: float,
) -> ProviderSyncPacket:
    if type(state) is not ProviderUsageState or type(settings) is not ProviderSyncSettings:
        raise ValueError("invalid provider sync input")
    if settings.device_id is None:
        raise ValueError("provider sync has no device id")
    quota_snapshots = ()
    if "quota" in settings.categories:
        quota_snapshots = tuple(
            snapshot
            for snapshot in state.snapshots
            if snapshot.state in {ProviderSourceState.READY, ProviderSourceState.STALE}
            and snapshot.lanes
        )
    machine_usage = ()
    if "token_usage" in settings.categories:
        machine_usage = tuple(
            MachineUsageObservation(
                settings.device_id,
                snapshot.provider_id,
                snapshot.observed_at,
                snapshot.input_tokens,
                snapshot.cached_input_tokens,
                snapshot.output_tokens,
                snapshot.model_count,
                snapshot.estimated_cost_usd,
                snapshot.cache_savings_usd,
            )
            for snapshot in state.snapshots
            if snapshot.input_tokens
            or snapshot.cached_input_tokens
            or snapshot.output_tokens
            or snapshot.estimated_cost_usd is not None
            or snapshot.cache_savings_usd is not None
        )
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
        credentials,
        local_directory: Path,
        fetcher: Callable[[ProviderSyncPeer], SftpFetchResult] = fetch_peer_packet,
        publisher: Callable[[bytes, Path], object] = publish_local_packet,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings_loader = settings_loader
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

    def refresh(self, state: ProviderUsageState) -> ProviderSyncRefresh:
        refreshed_at = float(self._clock())
        settings = self._settings()
        if not settings.enabled:
            return ProviderSyncRefresh(False, None, (), None, (), refreshed_at)
        local_packet = build_local_sync_packet(
            state,
            settings,
            generated_at=refreshed_at,
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
                remote = decode_signed_packet(fetched.packet or b"", secret)
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


__all__ = [
    "PeerSyncHealth",
    "ProviderSyncRefresh",
    "ProviderSyncRuntime",
    "build_local_sync_packet",
]
