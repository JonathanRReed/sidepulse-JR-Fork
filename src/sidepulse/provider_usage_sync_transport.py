"""Strict SSH/SFTP transport for authenticated provider sync packets."""

from __future__ import annotations

import os
import secrets
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .provider_usage_sync import MAX_SYNC_PACKET_BYTES
from .provider_usage_sync_settings import ProviderSyncPeer

SFTP_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class SftpFetchResult:
    peer_id: str
    reachable: bool
    packet: bytes | None
    reason: str | None

    def __repr__(self) -> str:
        held = f"<{len(self.packet)} bytes>" if self.packet is not None else "None"
        return (
            "SftpFetchResult("
            f"peer_id={self.peer_id!r}, reachable={self.reachable!r}, "
            f"packet={held}, reason={self.reason!r})"
        )


def _safe_owned_regular(path: Path, *, private: bool) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return bool(
        stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and info.st_uid == os.getuid()
        and (not private or not info.st_mode & 0o077)
    )


def _batch_quote(value: str) -> str:
    if any(character in value for character in ('"', "\n", "\r", "\x00")):
        raise ValueError("unsafe SFTP path")
    return f'"{value}"'


def build_sftp_fetch_command(
    peer: ProviderSyncPeer,
    output_path: Path,
    batch_path: Path,
) -> list[str]:
    if type(peer) is not ProviderSyncPeer:
        raise ValueError("invalid provider sync peer")
    output = str(Path(output_path).absolute())
    batch = str(Path(batch_path).absolute())
    _batch_quote(output)
    _batch_quote(batch)
    return [
        "/usr/bin/sftp",
        "-q",
        "-b",
        batch,
        "-oBatchMode=yes",
        "-oStrictHostKeyChecking=yes",
        "-oConnectTimeout=10",
        "-oConnectionAttempts=1",
        f"-oUserKnownHostsFile={peer.known_hosts}",
        f"-oIdentityFile={peer.identity_file}",
        peer.host,
    ]


def publish_local_packet(packet: bytes, target: Path) -> Path:
    if not isinstance(packet, bytes) or not packet or len(packet) > MAX_SYNC_PACKET_BYTES:
        raise ValueError("invalid provider sync packet")
    target = Path(target).expanduser().absolute()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(packet)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short provider sync write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, target)
    os.chmod(target, 0o600)
    return target


def fetch_peer_packet(
    peer: ProviderSyncPeer,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> SftpFetchResult:
    if type(peer) is not ProviderSyncPeer:
        raise ValueError("invalid provider sync peer")
    known_hosts = Path(peer.known_hosts)
    identity = Path(peer.identity_file)
    if not _safe_owned_regular(known_hosts, private=False):
        return SftpFetchResult(peer.peer_id, False, None, "unsafe_known_hosts")
    if not _safe_owned_regular(identity, private=True):
        return SftpFetchResult(peer.peer_id, False, None, "unsafe_identity_file")
    with tempfile.TemporaryDirectory(prefix="sidepulse-provider-sync-") as directory:
        root = Path(directory)
        output = root / "remote.packet"
        batch = root / "fetch.batch"
        try:
            batch.write_text(
                f"get {_batch_quote(peer.remote_path)} {_batch_quote(str(output))}\n",
                encoding="utf-8",
            )
            batch.chmod(0o600)
            command = build_sftp_fetch_command(peer, output, batch)
            completed = runner(
                command,
                timeout=SFTP_TIMEOUT_SECONDS,
                check=False,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return SftpFetchResult(peer.peer_id, False, None, "sftp_unavailable")
        if getattr(completed, "returncode", 1) != 0:
            return SftpFetchResult(peer.peer_id, False, None, "sftp_failed")
        try:
            info = output.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_size <= 0
                or info.st_size > MAX_SYNC_PACKET_BYTES
            ):
                raise OSError("invalid fetched packet")
            descriptor = os.open(
                output,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                packet = os.read(descriptor, MAX_SYNC_PACKET_BYTES + 1)
            finally:
                os.close(descriptor)
        except OSError:
            return SftpFetchResult(peer.peer_id, False, None, "invalid_remote_packet")
        if len(packet) > MAX_SYNC_PACKET_BYTES:
            return SftpFetchResult(peer.peer_id, False, None, "remote_packet_too_large")
        return SftpFetchResult(peer.peer_id, True, packet, None)


__all__ = [
    "SFTP_TIMEOUT_SECONDS",
    "SftpFetchResult",
    "build_sftp_fetch_command",
    "fetch_peer_packet",
    "publish_local_packet",
]
