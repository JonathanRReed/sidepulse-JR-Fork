"""Strict, content-free local export documents for operator history and diagnostics."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Final

from .operator_history import OperatorHistoryDay

EXPORT_VERSION: Final = 1
HISTORY_EXPORT_DOCUMENT: Final = "sidepulse-history-export"
DEBUG_EXPORT_DOCUMENT: Final = "sidepulse-debug-export"
MAX_HISTORY_EXPORT_BYTES: Final = 2 * 1024 * 1024
MAX_DEBUG_EXPORT_BYTES: Final = 512 * 1024
MAX_DEBUG_COUNT: Final = 1_000_000

_APP_VERSION: Final = re.compile(r"(?:dev|[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?)\Z")
_UUID: Final = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
_LONG_HEX_IDENTIFIER: Final = re.compile(r"[0-9a-fA-F]{24,}\Z")
_PRIVATE_PARTS: Final = (
    "accountid",
    "agentid",
    "apikey",
    "authorization",
    "body",
    "command",
    "content",
    "cookie",
    "credential",
    "email",
    "identity",
    "message",
    "navigation",
    "password",
    "path",
    "payload",
    "privatekey",
    "prompt",
    "rawerror",
    "requestid",
    "response",
    "secret",
    "sessionid",
    "token",
    "toolargument",
    "transcript",
    "userid",
    "workid",
)
_BUILD_TRUST_VALUES: Final = frozenset(
    {
        "ad_hoc_signed",
        "developer_id_signed",
        "development_wrapper",
        "packaged_verified",
        "source_checkout",
        "unknown",
        "unverified",
        "verified",
    }
)
_PROVIDER_HEALTH_VALUES: Final = frozenset(
    {
        "access_denied",
        "auth_required",
        "healthy",
        "partial",
        "rate_limited",
        "timed_out",
        "unavailable",
        "unsupported",
    }
)
_DELIVERY_DISPOSITION_VALUES: Final = frozenset(
    {
        "delivered",
        "disabled",
        "expired",
        "failed",
        "pending",
        "superseded",
        "suppressed_policy",
        "suppressed_quiet",
    }
)
_DEVICE_HEALTH_VALUES: Final = frozenset(
    {
        "degraded",
        "disconnected",
        "healthy",
        "not_updating",
        "unavailable",
        "unknown",
        "unsupported",
        "write_failed",
    }
)
_HISTORY_HEALTH_VALUES: Final = frozenset(
    {
        "corrupt",
        "disabled",
        "healthy",
        "missing",
        "unavailable",
        "unsupported",
    }
)


class ExportValidationError(ValueError):
    """An export failed closed before any bytes reached a destination."""


def _finite_epoch(value: object) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0.0:
        raise ExportValidationError("invalid export generated_at")
    return float(value)


def _normalized(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _private_shaped(value: str) -> bool:
    normalized = _normalized(value)
    return (
        any(part in normalized for part in _PRIVATE_PARTS)
        or _UUID.fullmatch(value) is not None
        or _LONG_HEX_IDENTIFIER.fullmatch(value) is not None
        or "@" in value
        or "://" in value
        or value.startswith(("/", "~", "\\"))
    )


def _validate_history_provider(value: str) -> None:
    if _private_shaped(value):
        raise ExportValidationError("private-shaped history provider")


def _row_identity(row: OperatorHistoryDay) -> tuple[str, int, str]:
    return (
        row.day_key,
        row.timezone_offset_minutes,
        row.provider_id.value,
    )


@dataclass(frozen=True, slots=True)
class HistoryExportV1:
    generated_at: float
    retention_days: int
    days: tuple[OperatorHistoryDay, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", _finite_epoch(self.generated_at))
        if type(self.retention_days) is not int or self.retention_days not in {
            0,
            7,
            30,
            90,
        }:
            raise ExportValidationError("invalid history export retention")
        if not (type(self.days) is tuple and all(type(row) is OperatorHistoryDay for row in self.days)):
            raise ExportValidationError("invalid history export days")
        if self.retention_days == 0 and self.days:
            raise ExportValidationError("disabled history export contains days")
        identities = [_row_identity(row) for row in self.days]
        if len(set(identities)) != len(identities):
            raise ExportValidationError("duplicate history export day")
        for row in self.days:
            _validate_history_provider(row.provider_id.value)


def _validate_count_pairs(
    value: object,
    *,
    allowlist: frozenset[str],
) -> tuple[tuple[str, int], ...]:
    if type(value) is not tuple:
        raise ExportValidationError("debug count collection is not allowlisted")
    labels: set[str] = set()
    selected: list[tuple[str, int]] = []
    for item in value:
        if not (type(item) is tuple and len(item) == 2 and type(item[0]) is str):
            raise ExportValidationError("debug count entry is not allowlisted")
        label, count = item
        if label not in allowlist:
            raise ExportValidationError("debug count label is not allowlisted")
        if label in labels:
            raise ExportValidationError("duplicate debug count label")
        if type(count) is not int or not 0 <= count <= MAX_DEBUG_COUNT:
            raise ExportValidationError("invalid debug count")
        labels.add(label)
        selected.append((label, count))
    return tuple(sorted(selected))


@dataclass(frozen=True, slots=True)
class DebugExportV1:
    generated_at: float
    app_version: str
    build_trust: str
    provider_health_counts: tuple[tuple[str, int], ...]
    delivery_disposition_counts: tuple[tuple[str, int], ...]
    device_health_counts: tuple[tuple[str, int], ...]
    history_health: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", _finite_epoch(self.generated_at))
        if not (
            type(self.app_version) is str
            and 1 <= len(self.app_version) <= 64
            and _APP_VERSION.fullmatch(self.app_version) is not None
            and not _private_shaped(self.app_version)
        ):
            raise ExportValidationError("invalid debug app_version")
        if type(self.build_trust) is not str or self.build_trust not in _BUILD_TRUST_VALUES:
            raise ExportValidationError("debug build trust is not allowlisted")
        if type(self.history_health) is not str or self.history_health not in _HISTORY_HEALTH_VALUES:
            raise ExportValidationError("debug history health is not allowlisted")
        object.__setattr__(
            self,
            "provider_health_counts",
            _validate_count_pairs(
                self.provider_health_counts,
                allowlist=_PROVIDER_HEALTH_VALUES,
            ),
        )
        object.__setattr__(
            self,
            "delivery_disposition_counts",
            _validate_count_pairs(
                self.delivery_disposition_counts,
                allowlist=_DELIVERY_DISPOSITION_VALUES,
            ),
        )
        object.__setattr__(
            self,
            "device_health_counts",
            _validate_count_pairs(
                self.device_health_counts,
                allowlist=_DEVICE_HEALTH_VALUES,
            ),
        )


def _day_payload(row: OperatorHistoryDay) -> dict[str, object]:
    return {
        "acknowledged": row.acknowledged,
        "active_duration_bands": list(row.active_duration_bands),
        "attention_wait_bands": list(row.attention_wait_bands),
        "completed": row.completed,
        "coverage": row.coverage.value,
        "day_key": row.day_key,
        "device_recoveries": row.device_recoveries,
        "failed": row.failed,
        "needs_user": row.needs_user,
        "primary_count": row.primary_count,
        "provider_id": row.provider_id.value,
        "sample_count": row.sample_count,
        "source_recoveries": row.source_recoveries,
        "started": row.started,
        "timezone_offset_minutes": row.timezone_offset_minutes,
        "worker_count": row.worker_count,
    }


def _encode_document(document: dict[str, object], *, max_bytes: int) -> bytes:
    try:
        payload = (
            json.dumps(
                document,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ExportValidationError("export document is not encodable") from error
    if len(payload) > max_bytes:
        raise ExportValidationError("export exceeds maximum size")
    return payload


def encode_history_export(export: HistoryExportV1) -> bytes:
    """Encode only the disclosed daily history fields as versioned JSON."""
    if type(export) is not HistoryExportV1:
        raise ExportValidationError("invalid history export object")
    ordered = sorted(export.days, key=_row_identity)
    return _encode_document(
        {
            "days": [_day_payload(row) for row in ordered],
            "document": HISTORY_EXPORT_DOCUMENT,
            "generated_at": export.generated_at,
            "retention_days": export.retention_days,
            "version": EXPORT_VERSION,
        },
        max_bytes=MAX_HISTORY_EXPORT_BYTES,
    )


def encode_debug_export(export: DebugExportV1) -> bytes:
    """Encode bounded product-owned technical counts without reading runtime data."""
    if type(export) is not DebugExportV1:
        raise ExportValidationError("invalid debug export object")
    return _encode_document(
        {
            "app_version": export.app_version,
            "build_trust": export.build_trust,
            "delivery_disposition_counts": [list(item) for item in export.delivery_disposition_counts],
            "device_health_counts": [list(item) for item in export.device_health_counts],
            "document": DEBUG_EXPORT_DOCUMENT,
            "generated_at": export.generated_at,
            "history_health": export.history_health,
            "provider_health_counts": [list(item) for item in export.provider_health_counts],
            "version": EXPORT_VERSION,
        },
        max_bytes=MAX_DEBUG_EXPORT_BYTES,
    )


__all__ = [
    "DEBUG_EXPORT_DOCUMENT",
    "HISTORY_EXPORT_DOCUMENT",
    "MAX_DEBUG_EXPORT_BYTES",
    "MAX_HISTORY_EXPORT_BYTES",
    "DebugExportV1",
    "ExportValidationError",
    "HistoryExportV1",
    "encode_debug_export",
    "encode_history_export",
]
