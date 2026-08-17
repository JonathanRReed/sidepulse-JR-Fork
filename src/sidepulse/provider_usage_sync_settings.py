"""Versioned configuration for authenticated peer-to-peer provider sync."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

PROVIDER_SYNC_SETTINGS_SCHEMA_VERSION = 1
_ALLOWED_CATEGORIES = ("quota", "token_usage", "agent_activity")
_DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_HOST = re.compile(
    r"(?:[A-Za-z0-9._-]{1,64}@)?[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\Z"
)
_REMOTE_PATH = re.compile(r"[A-Za-z0-9_./~:-]{1,1024}\Z")
_SECRET_ACCOUNT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class ProviderSyncSettingsError(ValueError):
    pass


class ProviderSyncSettingsWriteRefusedError(ProviderSyncSettingsError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderSyncPeer:
    peer_id: str
    host: str
    remote_path: str
    known_hosts: str
    identity_file: str
    secret_account: str

    def __post_init__(self) -> None:
        if (
            _DEVICE_ID.fullmatch(self.peer_id or "") is None
            or _HOST.fullmatch(self.host or "") is None
            or self.host.startswith("-")
            or _REMOTE_PATH.fullmatch(self.remote_path or "") is None
            or not self.remote_path.startswith(("~/", "/"))
            or not isinstance(self.known_hosts, str)
            or not Path(self.known_hosts).is_absolute()
            or "\x00" in self.known_hosts
            or "\n" in self.known_hosts
            or "\r" in self.known_hosts
            or not isinstance(self.identity_file, str)
            or not Path(self.identity_file).is_absolute()
            or "\x00" in self.identity_file
            or "\n" in self.identity_file
            or "\r" in self.identity_file
            or _SECRET_ACCOUNT.fullmatch(self.secret_account or "") is None
        ):
            raise ProviderSyncSettingsError("invalid provider sync peer")


@dataclass(frozen=True, slots=True)
class ProviderSyncSettings:
    schema_version: int
    enabled: bool
    device_id: str | None
    categories: tuple[str, ...]
    peers: tuple[ProviderSyncPeer, ...]

    def __post_init__(self) -> None:
        if (
            self.schema_version != PROVIDER_SYNC_SETTINGS_SCHEMA_VERSION
            or type(self.enabled) is not bool
            or (
                self.device_id is not None
                and _DEVICE_ID.fullmatch(self.device_id) is None
            )
            or type(self.categories) is not tuple
            or not self.categories
            or len(self.categories) != len(set(self.categories))
            or not set(self.categories).issubset(_ALLOWED_CATEGORIES)
            or type(self.peers) is not tuple
            or not all(type(peer) is ProviderSyncPeer for peer in self.peers)
            or len({peer.peer_id for peer in self.peers}) != len(self.peers)
        ):
            raise ProviderSyncSettingsError("invalid provider sync settings")
        if self.enabled and self.device_id is None:
            raise ProviderSyncSettingsError("enabled provider sync requires a device id")

    def with_enabled(self, enabled: bool) -> ProviderSyncSettings:
        if type(enabled) is not bool:
            raise ProviderSyncSettingsError("enabled must be a boolean")
        if enabled and self.device_id is None:
            raise ProviderSyncSettingsError("set a device id before enabling sync")
        return replace(self, enabled=enabled)

    def with_device_id(self, device_id: str) -> ProviderSyncSettings:
        if not isinstance(device_id, str) or _DEVICE_ID.fullmatch(device_id) is None:
            raise ProviderSyncSettingsError("invalid sync device id")
        return replace(self, device_id=device_id)

    def with_categories(self, categories: tuple[str, ...]) -> ProviderSyncSettings:
        return replace(self, categories=tuple(categories))

    def with_peer(
        self,
        *,
        peer_id: str,
        host: str,
        remote_path: str,
        known_hosts: str,
        identity_file: str,
        secret_account: str,
    ) -> ProviderSyncSettings:
        peer = ProviderSyncPeer(
            peer_id,
            host,
            remote_path,
            known_hosts,
            identity_file,
            secret_account,
        )
        peers = [current for current in self.peers if current.peer_id != peer_id]
        peers.append(peer)
        return replace(self, peers=tuple(sorted(peers, key=lambda item: item.peer_id)))

    def without_peer(self, peer_id: str) -> ProviderSyncSettings:
        return replace(
            self,
            peers=tuple(peer for peer in self.peers if peer.peer_id != peer_id),
        )


@dataclass(frozen=True, slots=True)
class LoadedProviderSyncSettings:
    settings: ProviderSyncSettings
    read_only: bool
    unknown_fields: tuple[tuple[str, object], ...]


def default_provider_sync_settings() -> ProviderSyncSettings:
    return ProviderSyncSettings(
        PROVIDER_SYNC_SETTINGS_SCHEMA_VERSION,
        False,
        None,
        ("quota", "token_usage"),
        (),
    )


def default_provider_sync_settings_path(home: Path | None = None) -> Path:
    base = Path.home() if home is None else Path(home)
    return base / ".config" / "sidepulse" / "provider-sync.json"


def _peer_from_document(value: object) -> ProviderSyncPeer | None:
    if not isinstance(value, dict):
        return None
    try:
        return ProviderSyncPeer(
            peer_id=value.get("peer_id"),
            host=value.get("host"),
            remote_path=value.get("remote_path"),
            known_hosts=value.get("known_hosts"),
            identity_file=value.get("identity_file"),
            secret_account=value.get("secret_account"),
        )
    except (ProviderSyncSettingsError, TypeError, ValueError):
        return None


def load_provider_sync_settings(
    path: Path | None = None,
    *,
    reader: Callable[[Path], str] | None = None,
) -> LoadedProviderSyncSettings:
    target = default_provider_sync_settings_path() if path is None else Path(path)
    read = reader
    if read is None:
        from .private_io import read_private_text

        read = read_private_text
    defaults = default_provider_sync_settings()
    try:
        document = json.loads(read(target))
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return LoadedProviderSyncSettings(defaults, False, ())
    if not isinstance(document, dict):
        return LoadedProviderSyncSettings(defaults, True, ())
    version = document.get("settings_schema_version")
    if type(version) is not int or version > PROVIDER_SYNC_SETTINGS_SCHEMA_VERSION:
        return LoadedProviderSyncSettings(defaults, True, tuple(document.items()))
    peers = []
    raw_peers = document.get("peers")
    if isinstance(raw_peers, list):
        for value in raw_peers[:64]:
            peer = _peer_from_document(value)
            if peer is not None and peer.peer_id not in {item.peer_id for item in peers}:
                peers.append(peer)
    raw_categories = document.get("categories", defaults.categories)
    categories = (
        tuple(value for value in raw_categories if value in _ALLOWED_CATEGORIES)
        if isinstance(raw_categories, list)
        else defaults.categories
    )
    if not categories:
        categories = defaults.categories
    raw_device = document.get("device_id")
    device_id = (
        raw_device
        if isinstance(raw_device, str) and _DEVICE_ID.fullmatch(raw_device)
        else None
    )
    enabled = document.get("enabled", False)
    if type(enabled) is not bool or (enabled and device_id is None):
        enabled = False
    settings = ProviderSyncSettings(
        PROVIDER_SYNC_SETTINGS_SCHEMA_VERSION,
        enabled,
        device_id,
        categories,
        tuple(sorted(peers, key=lambda item: item.peer_id)),
    )
    unknown = tuple(
        (key, value)
        for key, value in document.items()
        if key not in {
            "settings_schema_version",
            "enabled",
            "device_id",
            "categories",
            "peers",
        }
    )
    return LoadedProviderSyncSettings(settings, False, unknown)


def save_provider_sync_settings(
    settings: ProviderSyncSettings,
    path: Path | None = None,
    *,
    loaded: LoadedProviderSyncSettings | None = None,
    writer: Callable[[Path, str], object] | None = None,
) -> Path:
    if type(settings) is not ProviderSyncSettings:
        raise ProviderSyncSettingsError("invalid provider sync settings")
    if loaded is not None and loaded.read_only:
        raise ProviderSyncSettingsWriteRefusedError("provider sync settings are read-only")
    document = dict(loaded.unknown_fields if loaded is not None else ())
    document.update(
        {
            "settings_schema_version": PROVIDER_SYNC_SETTINGS_SCHEMA_VERSION,
            "enabled": settings.enabled,
            "device_id": settings.device_id,
            "categories": list(settings.categories),
            "peers": [
                {
                    "peer_id": peer.peer_id,
                    "host": peer.host,
                    "remote_path": peer.remote_path,
                    "known_hosts": peer.known_hosts,
                    "identity_file": peer.identity_file,
                    "secret_account": peer.secret_account,
                }
                for peer in settings.peers
            ],
        }
    )
    target = default_provider_sync_settings_path() if path is None else Path(path)
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    write = writer
    if write is None:
        from .private_io import atomic_private_write

        write = atomic_private_write
    write(target, text)
    return target


__all__ = [
    "LoadedProviderSyncSettings",
    "PROVIDER_SYNC_SETTINGS_SCHEMA_VERSION",
    "ProviderSyncPeer",
    "ProviderSyncSettings",
    "ProviderSyncSettingsError",
    "ProviderSyncSettingsWriteRefusedError",
    "default_provider_sync_settings",
    "default_provider_sync_settings_path",
    "load_provider_sync_settings",
    "save_provider_sync_settings",
]
