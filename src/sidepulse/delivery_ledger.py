"""Pure, bounded delivery identity and restart-deduplication state."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .operator_state import SemanticEventKey, semantic_event_key_to_payload

MAX_DELIVERY_RECEIPTS: Final = 512
MAX_DELIVERY_STAGE: Final = 4

_SUMMARY_DIGEST: Final = re.compile(r"[0-9a-f]{64}\Z")


class DeliveryValidationError(ValueError):
    """Delivery state failed closed at its pure typed boundary."""


class DeliveryTransitionError(ValueError):
    """A receipt attempted an illegal delivery-state transition."""


class DeliveryChannel(str, Enum):
    MAILBOX_CUE = "mailbox_cue"
    STATUS_ITEM_CUE = "status_item_cue"
    SCREEN_BAR_CUE = "screen_bar_cue"
    HARDWARE_CUE = "hardware_cue"
    SYSTEM_NOTIFICATION = "system_notification"
    SOUND = "sound"
    HISTORY_FACT = "history_fact"


class DeliveryDisposition(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    SUPPRESSED_QUIET = "suppressed_quiet"
    SUPPRESSED_POLICY = "suppressed_policy"
    DISABLED = "disabled"
    FAILED = "failed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class DeliveryDiagnostic(str, Enum):
    DELIVERY_FAILED = "delivery_failed"
    CHANNEL_UNAVAILABLE = "channel_unavailable"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"


class DeliveryRestoreHealth(str, Enum):
    HEALTHY = "healthy"
    MISSING = "missing"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


class DeliverySummaryKind(str, Enum):
    QUIET_EXIT = "quiet_exit"


@dataclass(frozen=True, order=True, slots=True)
class SummaryDigest:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _SUMMARY_DIGEST.fullmatch(self.value) is None:
            raise DeliveryValidationError("invalid delivery summary digest")


@dataclass(frozen=True, order=True, slots=True)
class DeliverySummaryKey:
    kind: DeliverySummaryKind
    member_count: int
    member_digest: SummaryDigest

    def __post_init__(self) -> None:
        if not (
            type(self.kind) is DeliverySummaryKind
            and type(self.member_count) is int
            and 1 <= self.member_count <= MAX_DELIVERY_RECEIPTS
            and type(self.member_digest) is SummaryDigest
        ):
            raise DeliveryValidationError("invalid delivery summary key")


@dataclass(frozen=True, order=True, slots=True)
class DeliveryKey:
    subject_key: SemanticEventKey | DeliverySummaryKey
    channel: DeliveryChannel
    stage: int

    def __post_init__(self) -> None:
        if not (
            type(self.subject_key) in {SemanticEventKey, DeliverySummaryKey}
            and type(self.channel) is DeliveryChannel
            and type(self.stage) is int
            and 0 <= self.stage <= MAX_DELIVERY_STAGE
        ):
            raise DeliveryValidationError("invalid delivery key")


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    key: DeliveryKey
    disposition: DeliveryDisposition
    recorded_at_epoch: float
    attempt_generation: int
    diagnostic: DeliveryDiagnostic | None

    def __post_init__(self) -> None:
        if not (
            type(self.key) is DeliveryKey
            and type(self.disposition) is DeliveryDisposition
            and type(self.recorded_at_epoch) in {int, float}
            and math.isfinite(self.recorded_at_epoch)
            and self.recorded_at_epoch >= 0.0
            and type(self.attempt_generation) is int
            and self.attempt_generation >= 0
            and (
                self.diagnostic is None
                or type(self.diagnostic) is DeliveryDiagnostic
            )
        ):
            raise DeliveryValidationError("invalid delivery receipt")


@dataclass(frozen=True, slots=True)
class DeliveryLedger:
    receipts: tuple[DeliveryReceipt, ...]

    def __post_init__(self) -> None:
        if not (
            type(self.receipts) is tuple
            and len(self.receipts) <= MAX_DELIVERY_RECEIPTS
            and all(type(receipt) is DeliveryReceipt for receipt in self.receipts)
        ):
            raise DeliveryValidationError("invalid delivery ledger")
        if len({receipt.key for receipt in self.receipts}) != len(self.receipts):
            raise DeliveryValidationError("duplicate delivery key")
        ordered = tuple(sorted(self.receipts, key=_receipt_sort_key))
        if ordered != self.receipts:
            object.__setattr__(self, "receipts", ordered)


@dataclass(frozen=True, slots=True)
class DeliveryLedgerRestore:
    ledger: DeliveryLedger
    health: DeliveryRestoreHealth

    def __post_init__(self) -> None:
        if not (
            type(self.ledger) is DeliveryLedger
            and type(self.health) is DeliveryRestoreHealth
        ):
            raise DeliveryValidationError("invalid delivery ledger restore")


def _event_scalar(key: SemanticEventKey) -> str:
    return json.dumps(
        semantic_event_key_to_payload(key),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _subject_sort_key(
    subject: SemanticEventKey | DeliverySummaryKey,
) -> tuple[object, ...]:
    if type(subject) is SemanticEventKey:
        return ("event", _event_scalar(subject))
    return (
        "summary",
        subject.kind.value,
        subject.member_count,
        subject.member_digest.value,
    )


def _delivery_key_sort_key(key: DeliveryKey) -> tuple[object, ...]:
    return (*_subject_sort_key(key.subject_key), key.channel.value, key.stage)


def _receipt_sort_key(receipt: DeliveryReceipt) -> tuple[object, ...]:
    return _delivery_key_sort_key(receipt.key)


def _retention_key(receipt: DeliveryReceipt) -> tuple[object, ...]:
    return (
        float(receipt.recorded_at_epoch),
        receipt.attempt_generation,
        _delivery_key_sort_key(receipt.key),
    )


def _transition_allowed(
    previous: DeliveryReceipt,
    current: DeliveryReceipt,
) -> bool:
    if previous.disposition is DeliveryDisposition.PENDING:
        return current.disposition is not DeliveryDisposition.PENDING
    if previous.disposition is DeliveryDisposition.FAILED:
        return (
            current.disposition is DeliveryDisposition.PENDING
            and current.attempt_generation > previous.attempt_generation
        )
    if previous.disposition in {
        DeliveryDisposition.SUPPRESSED_QUIET,
        DeliveryDisposition.SUPPRESSED_POLICY,
    }:
        return current.disposition is DeliveryDisposition.SUPERSEDED
    return False


def record_delivery(
    ledger: DeliveryLedger,
    receipt: DeliveryReceipt,
) -> DeliveryLedger:
    """Record one receipt without merging independent channels or stages."""
    if type(ledger) is not DeliveryLedger or type(receipt) is not DeliveryReceipt:
        raise DeliveryValidationError("invalid delivery record input")
    previous = next(
        (item for item in ledger.receipts if item.key == receipt.key),
        None,
    )
    if previous is not None:
        if previous == receipt:
            return ledger
        if not _transition_allowed(previous, receipt):
            raise DeliveryTransitionError("illegal delivery transition")
        retained = [item for item in ledger.receipts if item.key != receipt.key]
        retained.append(receipt)
        return DeliveryLedger(tuple(retained))

    retained = [*ledger.receipts, receipt]
    if len(retained) > MAX_DELIVERY_RECEIPTS:
        retained.remove(min(retained, key=_retention_key))
    return DeliveryLedger(tuple(retained))


def delivery_disposition(
    ledger: DeliveryLedger,
    key: DeliveryKey,
) -> DeliveryDisposition | None:
    """Return the exact disposition for one channel and stage identity."""
    if type(ledger) is not DeliveryLedger or type(key) is not DeliveryKey:
        raise DeliveryValidationError("invalid delivery lookup")
    for receipt in ledger.receipts:
        if receipt.key == key:
            return receipt.disposition
    return None


def pending_quiet_summary_keys(
    ledger: DeliveryLedger,
) -> tuple[SemanticEventKey, ...]:
    """Select unique provider events still awaiting one quiet-exit summary."""
    if type(ledger) is not DeliveryLedger:
        raise DeliveryValidationError("invalid delivery ledger")
    keys = {
        receipt.key.subject_key
        for receipt in ledger.receipts
        if (
            receipt.disposition is DeliveryDisposition.SUPPRESSED_QUIET
            and type(receipt.key.subject_key) is SemanticEventKey
        )
    }
    return tuple(sorted(keys))


def quiet_summary_key(
    event_keys: tuple[SemanticEventKey, ...],
) -> DeliverySummaryKey | None:
    """Build one content-free, input-order-independent quiet-exit identity."""
    if not (
        type(event_keys) is tuple
        and len(event_keys) <= MAX_DELIVERY_RECEIPTS
        and all(type(key) is SemanticEventKey for key in event_keys)
    ):
        raise DeliveryValidationError("invalid quiet summary members")
    if not event_keys:
        return None
    members = tuple(sorted(set(event_keys)))
    encoded = json.dumps(
        [semantic_event_key_to_payload(key) for key in members],
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return DeliverySummaryKey(
        DeliverySummaryKind.QUIET_EXIT,
        len(members),
        SummaryDigest(hashlib.sha256(encoded).hexdigest()),
    )
