"""Owner-mediated pairing documents for authenticated cross-Mac usage sync."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PAIRING_DOCUMENT_SCHEMA_VERSION = 1
_PAIRING_SECRET_BYTES = 32
_MAX_PAIRING_DOCUMENT_BYTES = 16 * 1024
_DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SECRET_ACCOUNT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class ImportedPairing:
    local_device_id: str
    peer_id: str
    secret_account: str

    def __post_init__(self) -> None:
        if (
            _DEVICE_ID.fullmatch(self.local_device_id or "") is None
            or _DEVICE_ID.fullmatch(self.peer_id or "") is None
            or _SECRET_ACCOUNT.fullmatch(self.secret_account or "") is None
        ):
            raise ValueError("invalid imported pairing metadata")


def _validate_identity(local_device_id: str, peer_id: str, secret_account: str) -> None:
    if (
        not isinstance(local_device_id, str)
        or _DEVICE_ID.fullmatch(local_device_id) is None
        or not isinstance(peer_id, str)
        or _DEVICE_ID.fullmatch(peer_id) is None
        or local_device_id == peer_id
        or not isinstance(secret_account, str)
        or _SECRET_ACCOUNT.fullmatch(secret_account) is None
    ):
        raise ValueError("invalid pairing identity")


def _write_owner_private(target: Path, text: str) -> Path:
    target = Path(target).expanduser().absolute()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        data = text.encode("utf-8")
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, target)
    os.chmod(target, 0o600)
    return target


def export_pairing_document(
    *,
    local_device_id: str,
    peer_id: str,
    secret_account: str,
    target: Path,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> Path:
    _validate_identity(local_device_id, peer_id, secret_account)
    secret = random_bytes(_PAIRING_SECRET_BYTES)
    if not isinstance(secret, bytes) or len(secret) != _PAIRING_SECRET_BYTES:
        raise ValueError("invalid pairing secret generator")
    document = {
        "schema_version": PAIRING_DOCUMENT_SCHEMA_VERSION,
        "local_device_id": local_device_id,
        "peer_id": peer_id,
        "secret_account": secret_account,
        "shared_secret_b64": base64.b64encode(secret).decode("ascii"),
    }
    return _write_owner_private(
        Path(target),
        json.dumps(document, separators=(",", ":"), sort_keys=True),
    )


def _read_owner_private(target: Path) -> dict[str, object]:
    target = Path(target).expanduser().absolute()
    info = target.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_size > _MAX_PAIRING_DOCUMENT_BYTES
    ):
        raise ValueError("pairing document must be an owner-private regular file")
    descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        data = os.read(descriptor, _MAX_PAIRING_DOCUMENT_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > _MAX_PAIRING_DOCUMENT_BYTES:
        raise ValueError("pairing document exceeds size budget")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("invalid pairing document") from None
    if not isinstance(document, dict):
        raise ValueError("invalid pairing document")
    return document


def import_pairing_document(target: Path, *, credentials) -> ImportedPairing:
    document = _read_owner_private(target)
    if set(document) != {
        "schema_version",
        "local_device_id",
        "peer_id",
        "secret_account",
        "shared_secret_b64",
    } or document.get("schema_version") != PAIRING_DOCUMENT_SCHEMA_VERSION:
        raise ValueError("unsupported pairing document")
    local_device_id = document.get("local_device_id")
    peer_id = document.get("peer_id")
    secret_account = document.get("secret_account")
    _validate_identity(local_device_id, peer_id, secret_account)
    encoded = document.get("shared_secret_b64")
    if not isinstance(encoded, str) or len(encoded) > 128:
        raise ValueError("invalid pairing secret")
    try:
        secret = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        raise ValueError("invalid pairing secret") from None
    if len(secret) != _PAIRING_SECRET_BYTES:
        raise ValueError("invalid pairing secret")
    credentials.set(
        "sidepulse-sync",
        secret_account,
        base64.b64encode(secret).decode("ascii"),
    )
    return ImportedPairing(local_device_id, peer_id, secret_account)


__all__ = [
    "PAIRING_DOCUMENT_SCHEMA_VERSION",
    "ImportedPairing",
    "export_pairing_document",
    "import_pairing_document",
]
