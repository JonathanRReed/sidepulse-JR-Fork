"""Strict private persistence for bounded delivery receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from .delivery_ledger import (
    MAX_DELIVERY_RECEIPTS,
    DeliveryChannel,
    DeliveryDiagnostic,
    DeliveryDisposition,
    DeliveryKey,
    DeliveryLedger,
    DeliveryLedgerRestore,
    DeliveryReceipt,
    DeliveryRestoreHealth,
    DeliverySummaryKey,
    DeliverySummaryKind,
    SummaryDigest,
)
from .operator_state import (
    SemanticEventKey,
    semantic_event_key_from_payload,
    semantic_event_key_to_payload,
)
from .private_io import atomic_private_write, read_private_text

_STORE_VERSION: Final = 1
_MAX_STORE_BYTES: Final = 262_144
_DOCUMENT_FIELDS: Final = frozenset({"receipts", "version"})
_RECEIPT_FIELDS: Final = frozenset(
    {
        "attempt_generation",
        "diagnostic",
        "disposition",
        "key",
        "recorded_at_epoch",
    }
)
_KEY_FIELDS: Final = frozenset({"channel", "stage", "subject", "subject_kind"})
_SUMMARY_FIELDS: Final = frozenset({"kind", "member_count", "member_digest"})


class _CorruptDeliveryStore(ValueError):
    pass


class _UnsupportedDeliveryStore(ValueError):
    pass


def load_delivery_ledger(path: Path) -> DeliveryLedgerRestore:
    """Load a private ledger, returning typed degraded health on failure."""
    try:
        raw = read_private_text(Path(path), max_bytes=_MAX_STORE_BYTES)
        document = _decode_document(raw)
        ledger = _ledger_from_document(document)
    except FileNotFoundError:
        return _degraded_restore(DeliveryRestoreHealth.MISSING)
    except _UnsupportedDeliveryStore:
        return _degraded_restore(DeliveryRestoreHealth.UNSUPPORTED)
    except OSError:
        return _degraded_restore(DeliveryRestoreHealth.UNAVAILABLE)
    except (RecursionError, TypeError, UnicodeError, ValueError):
        return _degraded_restore(DeliveryRestoreHealth.CORRUPT)
    return DeliveryLedgerRestore(ledger, DeliveryRestoreHealth.HEALTHY)


def save_delivery_ledger(path: Path, ledger: DeliveryLedger) -> None:
    """Atomically save one exact metadata-only delivery document."""
    if type(ledger) is not DeliveryLedger:
        raise ValueError("invalid delivery ledger")
    document = {
        "version": _STORE_VERSION,
        "receipts": [_receipt_to_payload(receipt) for receipt in ledger.receipts],
    }
    serialized = json.dumps(
        document,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    encoded = f"{serialized}\n"
    if len(encoded.encode("utf-8")) > _MAX_STORE_BYTES:
        raise ValueError("delivery ledger store exceeds maximum size")
    atomic_private_write(Path(path), encoded)


def _degraded_restore(health: DeliveryRestoreHealth) -> DeliveryLedgerRestore:
    return DeliveryLedgerRestore(DeliveryLedger(()), health)


def _strict_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _CorruptDeliveryStore
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _CorruptDeliveryStore


def _decode_document(raw: str) -> object:
    return json.loads(
        raw,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )


def _has_exact_fields(value: object, fields: frozenset[str]) -> bool:
    return type(value) is dict and frozenset(value) == fields


def _ledger_from_document(document: object) -> DeliveryLedger:
    if not _has_exact_fields(document, _DOCUMENT_FIELDS):
        raise _CorruptDeliveryStore
    version = document["version"]
    if type(version) is not int:
        raise _CorruptDeliveryStore
    if version != _STORE_VERSION:
        raise _UnsupportedDeliveryStore
    receipts = document["receipts"]
    if type(receipts) is not list or len(receipts) > MAX_DELIVERY_RECEIPTS:
        raise _CorruptDeliveryStore
    try:
        return DeliveryLedger(tuple(_receipt_from_payload(item) for item in receipts))
    except (TypeError, ValueError) as error:
        raise _CorruptDeliveryStore from error


def _receipt_to_payload(receipt: DeliveryReceipt) -> dict[str, object]:
    if type(receipt) is not DeliveryReceipt:
        raise ValueError("invalid delivery receipt")
    return {
        "key": _key_to_payload(receipt.key),
        "disposition": receipt.disposition.value,
        "recorded_at_epoch": float(receipt.recorded_at_epoch),
        "attempt_generation": receipt.attempt_generation,
        "diagnostic": (
            None if receipt.diagnostic is None else receipt.diagnostic.value
        ),
    }


def _receipt_from_payload(payload: object) -> DeliveryReceipt:
    if not _has_exact_fields(payload, _RECEIPT_FIELDS):
        raise _CorruptDeliveryStore
    disposition = payload["disposition"]
    diagnostic = payload["diagnostic"]
    if not (
        type(disposition) is str
        and (diagnostic is None or type(diagnostic) is str)
    ):
        raise _CorruptDeliveryStore
    return DeliveryReceipt(
        _key_from_payload(payload["key"]),
        DeliveryDisposition(disposition),
        payload["recorded_at_epoch"],
        payload["attempt_generation"],
        None if diagnostic is None else DeliveryDiagnostic(diagnostic),
    )


def _key_to_payload(key: DeliveryKey) -> dict[str, object]:
    if type(key) is not DeliveryKey:
        raise ValueError("invalid delivery key")
    if type(key.subject_key) is SemanticEventKey:
        subject_kind = "event"
        subject = semantic_event_key_to_payload(key.subject_key)
    else:
        subject_kind = "summary"
        subject = {
            "kind": key.subject_key.kind.value,
            "member_count": key.subject_key.member_count,
            "member_digest": key.subject_key.member_digest.value,
        }
    return {
        "subject_kind": subject_kind,
        "subject": subject,
        "channel": key.channel.value,
        "stage": key.stage,
    }


def _key_from_payload(payload: object) -> DeliveryKey:
    if not _has_exact_fields(payload, _KEY_FIELDS):
        raise _CorruptDeliveryStore
    subject_kind = payload["subject_kind"]
    channel = payload["channel"]
    if type(subject_kind) is not str or type(channel) is not str:
        raise _CorruptDeliveryStore
    if subject_kind == "event":
        subject = semantic_event_key_from_payload(payload["subject"])
        if subject is None:
            raise _CorruptDeliveryStore
    elif subject_kind == "summary":
        summary = payload["subject"]
        if not _has_exact_fields(summary, _SUMMARY_FIELDS):
            raise _CorruptDeliveryStore
        kind = summary["kind"]
        digest = summary["member_digest"]
        if type(kind) is not str or type(digest) is not str:
            raise _CorruptDeliveryStore
        subject = DeliverySummaryKey(
            DeliverySummaryKind(kind),
            summary["member_count"],
            SummaryDigest(digest),
        )
    else:
        raise _CorruptDeliveryStore
    return DeliveryKey(subject, DeliveryChannel(channel), payload["stage"])
