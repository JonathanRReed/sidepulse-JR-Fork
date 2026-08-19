"""Pure stable identity and migration rules for SidePulse devices.

A mount path is attachment metadata, not identity. This module prefers a
hardware serial, then a volume UUID, then a disk identifier. Raw identifiers
are hashed before persistence so settings and diagnostics never expose a
serial number.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType


class DeviceKind(str, Enum):
    DOT = "dot"
    PRO = "pro"
    SCREEN_BAR = "screen_bar"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DeviceHardwareFacts:
    mount_path: str
    product_name: str
    volume_uuid: str | None = None
    disk_identifier: str | None = None
    serial_number: str | None = None
    connected: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.mount_path, str) or not self.mount_path.strip():
            raise ValueError("mount_path must be nonempty")
        if not isinstance(self.product_name, str) or not self.product_name.strip():
            raise ValueError("product_name must be nonempty")
        for name in ("volume_uuid", "disk_identifier", "serial_number"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 256
                or "\x00" in value
            ):
                raise ValueError(f"invalid {name}")
        if type(self.connected) is not bool:
            raise ValueError("connected must be a boolean")


@dataclass(frozen=True, slots=True)
class StableDeviceIdentity:
    key: str
    kind: DeviceKind
    label: str
    mount_path: str
    connected: bool
    evidence: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.key, str)
            or not self.key.startswith("sidepulse:")
            or not isinstance(self.kind, DeviceKind)
            or not isinstance(self.label, str)
            or not self.label
            or not isinstance(self.mount_path, str)
            or not isinstance(self.evidence, str)
            or type(self.connected) is not bool
        ):
            raise ValueError("invalid stable device identity")


@dataclass(frozen=True, slots=True)
class RememberedDeviceRow:
    device_id: str
    name: str
    path: str
    preferences: Mapping[str, object] = field(default_factory=dict)
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (self.device_id, self.name, self.path)
        ):
            raise ValueError("invalid remembered device row")
        if not isinstance(self.preferences, Mapping):
            raise ValueError("preferences must be a mapping")
        if isinstance(self.updated_at, bool) or not isinstance(
            self.updated_at, (int, float)
        ):
            raise ValueError("updated_at must be numeric")
        object.__setattr__(
            self,
            "preferences",
            MappingProxyType(dict(self.preferences)),
        )
        object.__setattr__(self, "updated_at", float(self.updated_at))


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def device_kind(product_name: str, mount_path: str = "") -> DeviceKind:
    combined = _NON_ALNUM.sub("", f"{product_name} {mount_path}".lower())
    if "screenbar" in combined or mount_path == "screen-bar":
        return DeviceKind.SCREEN_BAR
    if "sidepulsepro" in combined:
        return DeviceKind.PRO
    if "sidepulsedot" in combined:
        return DeviceKind.DOT
    if combined.endswith("sidepulse") or combined == "sidepulse":
        return DeviceKind.DOT
    return DeviceKind.UNKNOWN


def normalize_device_label(raw: str, kind: DeviceKind) -> str:
    if kind is DeviceKind.SCREEN_BAR:
        return "Screen Bar"
    if kind is DeviceKind.PRO:
        return "SidePulse Pro"
    if kind is DeviceKind.DOT:
        return "SidePulse Dot"
    cleaned = " ".join(str(raw).split()).strip()
    return cleaned or "SidePulse"


def _digest(namespace: str, value: str) -> str:
    payload = f"sidepulse-device-v1\0{namespace}\0{value.strip().lower()}".encode()
    return hashlib.sha256(payload).hexdigest()[:24]


def _valid_physical_mount(
    path: str,
    trusted_mount_root: Path = Path("/Volumes"),
) -> bool:
    try:
        candidate = Path(path).expanduser().absolute()
        root = Path(trusted_mount_root).expanduser().absolute()
    except (OSError, TypeError, ValueError):
        return False
    return candidate != root and root in candidate.parents


def derive_device_identity(
    facts: DeviceHardwareFacts,
    *,
    trusted_mount_root: Path = Path("/Volumes"),
) -> StableDeviceIdentity | None:
    if type(facts) is not DeviceHardwareFacts:
        raise TypeError("facts must be DeviceHardwareFacts")
    kind = device_kind(facts.product_name, facts.mount_path)
    if kind is DeviceKind.SCREEN_BAR:
        return StableDeviceIdentity(
            key="sidepulse:screen-bar",
            kind=kind,
            label="Screen Bar",
            mount_path="screen-bar",
            connected=facts.connected,
            evidence="virtual",
        )
    if kind is DeviceKind.UNKNOWN or not _valid_physical_mount(
        facts.mount_path,
        trusted_mount_root,
    ):
        return None

    candidates = (
        ("serial", facts.serial_number),
        ("volume", facts.volume_uuid),
        ("disk", facts.disk_identifier),
        ("legacy", facts.mount_path),
    )
    for evidence, value in candidates:
        if isinstance(value, str) and value.strip():
            return StableDeviceIdentity(
                key=f"sidepulse:{kind.value}:{evidence}:{_digest(evidence, value)}",
                kind=kind,
                label=normalize_device_label(facts.product_name, kind),
                mount_path=facts.mount_path,
                connected=facts.connected,
                evidence=evidence,
            )
    return None


def _row_kind(row: RememberedDeviceRow) -> DeviceKind:
    if row.device_id == "sidepulse:screen-bar" or row.path == "screen-bar":
        return DeviceKind.SCREEN_BAR
    return device_kind(row.name, row.path)


def _row_is_eligible(row: RememberedDeviceRow) -> bool:
    kind = _row_kind(row)
    return kind is DeviceKind.SCREEN_BAR or (
        kind is not DeviceKind.UNKNOWN and _valid_physical_mount(row.path)
    )


def _merge_preferences(rows: list[RememberedDeviceRow]) -> dict[str, object]:
    result: dict[str, object] = {}
    for row in sorted(rows, key=lambda item: item.updated_at):
        result.update(dict(row.preferences))
    return result


def migrate_remembered_devices(
    rows: tuple[RememberedDeviceRow, ...],
    live: tuple[StableDeviceIdentity, ...],
) -> tuple[RememberedDeviceRow, ...]:
    if type(rows) is not tuple or not all(
        type(row) is RememberedDeviceRow for row in rows
    ):
        raise TypeError("rows must be a tuple of RememberedDeviceRow")
    if type(live) is not tuple or not all(
        type(identity) is StableDeviceIdentity for identity in live
    ):
        raise TypeError("live must be a tuple of StableDeviceIdentity")

    eligible = [row for row in rows if _row_is_eligible(row)]
    rows_by_kind: dict[DeviceKind, list[RememberedDeviceRow]] = {}
    for row in eligible:
        rows_by_kind.setdefault(_row_kind(row), []).append(row)

    live_by_kind: dict[DeviceKind, list[StableDeviceIdentity]] = {}
    for identity in live:
        live_by_kind.setdefault(identity.kind, []).append(identity)

    migrated: list[RememberedDeviceRow] = []
    consumed_kinds: set[DeviceKind] = set()

    for kind, identities in live_by_kind.items():
        remembered = rows_by_kind.get(kind, [])
        consumed_kinds.add(kind)
        if len(identities) == 1:
            identity = identities[0]
            migrated.append(
                RememberedDeviceRow(
                    device_id=identity.key,
                    name=identity.label,
                    path=identity.mount_path,
                    preferences=_merge_preferences(remembered),
                    updated_at=max(
                        (row.updated_at for row in remembered),
                        default=0.0,
                    ),
                )
            )
            continue

        for identity in sorted(identities, key=lambda item: item.key):
            exact = [row for row in remembered if row.path == identity.mount_path]
            migrated.append(
                RememberedDeviceRow(
                    device_id=identity.key,
                    name=identity.label,
                    path=identity.mount_path,
                    preferences=_merge_preferences(exact),
                    updated_at=max(
                        (row.updated_at for row in exact),
                        default=0.0,
                    ),
                )
            )

    for kind, remembered in rows_by_kind.items():
        if kind in consumed_kinds or not remembered:
            continue
        newest = max(remembered, key=lambda item: item.updated_at)
        if kind is DeviceKind.SCREEN_BAR:
            key = "sidepulse:screen-bar"
            path = "screen-bar"
        else:
            key = f"sidepulse:{kind.value}:legacy:{_digest('legacy', newest.path)}"
            path = newest.path
        migrated.append(
            RememberedDeviceRow(
                device_id=key,
                name=normalize_device_label(newest.name, kind),
                path=path,
                preferences=_merge_preferences(remembered),
                updated_at=newest.updated_at,
            )
        )

    unique: dict[str, RememberedDeviceRow] = {}
    for row in sorted(migrated, key=lambda item: (item.updated_at, item.device_id)):
        unique[row.device_id] = row
    return tuple(sorted(unique.values(), key=lambda item: (item.name, item.device_id)))


__all__ = [
    "DeviceHardwareFacts",
    "DeviceKind",
    "RememberedDeviceRow",
    "StableDeviceIdentity",
    "derive_device_identity",
    "device_kind",
    "migrate_remembered_devices",
    "normalize_device_label",
]
