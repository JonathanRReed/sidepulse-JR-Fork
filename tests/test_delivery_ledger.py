from __future__ import annotations

from dataclasses import replace

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.delivery_ledger import (
    MAX_DELIVERY_RECEIPTS,
    DeliveryChannel,
    DeliveryDiagnostic,
    DeliveryDisposition,
    DeliveryKey,
    DeliveryLedger,
    DeliveryReceipt,
    DeliverySummaryKey,
    DeliverySummaryKind,
    DeliveryTransitionError,
    DeliveryValidationError,
    SummaryDigest,
    delivery_disposition,
    pending_quiet_summary_keys,
    quiet_summary_key,
    record_delivery,
)
from sidepulse.operator_state import SemanticEventKey, TransitionKind
from sidepulse.provider_facts import (
    EventToken,
    ProviderWatermark,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
)

NOW = 1_786_536_000.0


def _event(
    suffix: str = "001",
    *,
    transition: TransitionKind = TransitionKind.COMPLETED,
) -> SemanticEventKey:
    source = SourceKey("codex", "hooks", "local:01", "live_agent_events")
    work = WorkKey(source, WorkIdentifier(f"work:{suffix}"))
    return SemanticEventKey(
        work,
        transition,
        ProviderWatermark(
            source,
            WatermarkBasis.PROVIDER_SEQUENCE,
            NOW + int(suffix),
            EventToken(f"event:{suffix}"),
            int(suffix),
            0,
        ),
    )


def _key(
    suffix: str = "001",
    *,
    channel: DeliveryChannel = DeliveryChannel.SYSTEM_NOTIFICATION,
    stage: int = 0,
) -> DeliveryKey:
    return DeliveryKey(_event(suffix), channel, stage)


def _receipt(
    disposition: DeliveryDisposition,
    *,
    key: DeliveryKey | None = None,
    recorded_at: float = NOW,
    attempt_generation: int = 0,
    diagnostic: DeliveryDiagnostic | None = None,
) -> DeliveryReceipt:
    return DeliveryReceipt(
        key or _key(),
        disposition,
        recorded_at,
        attempt_generation,
        diagnostic,
    )


@pytest.mark.parametrize(
    "next_disposition",
    tuple(disposition for disposition in DeliveryDisposition if disposition is not DeliveryDisposition.PENDING),
)
def test_pending_may_transition_to_each_nonpending_disposition(
    next_disposition: DeliveryDisposition,
) -> None:
    pending = _receipt(DeliveryDisposition.PENDING)
    ledger = record_delivery(DeliveryLedger(()), pending)

    updated = record_delivery(
        ledger,
        replace(
            pending,
            disposition=next_disposition,
            recorded_at_epoch=NOW + 1.0,
        ),
    )

    assert delivery_disposition(updated, pending.key) is next_disposition


def test_failed_may_retry_only_with_strictly_greater_attempt_generation() -> None:
    failed = _receipt(
        DeliveryDisposition.FAILED,
        attempt_generation=7,
        diagnostic=DeliveryDiagnostic.DELIVERY_FAILED,
    )
    ledger = DeliveryLedger((failed,))

    retried = record_delivery(
        ledger,
        _receipt(
            DeliveryDisposition.PENDING,
            attempt_generation=8,
            recorded_at=NOW + 1.0,
        ),
    )

    assert retried.receipts == (
        _receipt(
            DeliveryDisposition.PENDING,
            attempt_generation=8,
            recorded_at=NOW + 1.0,
        ),
    )
    for generation in (6, 7):
        with pytest.raises(DeliveryTransitionError):
            record_delivery(
                ledger,
                _receipt(
                    DeliveryDisposition.PENDING,
                    attempt_generation=generation,
                    recorded_at=NOW + 1.0,
                ),
            )
    assert ledger == DeliveryLedger((failed,))


@pytest.mark.parametrize(
    "suppressed",
    (DeliveryDisposition.SUPPRESSED_QUIET, DeliveryDisposition.SUPPRESSED_POLICY),
)
def test_suppressed_receipt_may_become_superseded(
    suppressed: DeliveryDisposition,
) -> None:
    original = _receipt(suppressed)

    result = record_delivery(
        DeliveryLedger((original,)),
        replace(
            original,
            disposition=DeliveryDisposition.SUPERSEDED,
            recorded_at_epoch=NOW + 1.0,
        ),
    )

    assert delivery_disposition(result, original.key) is DeliveryDisposition.SUPERSEDED


@pytest.mark.parametrize(
    "original",
    (
        DeliveryDisposition.DELIVERED,
        DeliveryDisposition.DISABLED,
        DeliveryDisposition.EXPIRED,
        DeliveryDisposition.SUPERSEDED,
    ),
)
@pytest.mark.parametrize(
    "replacement",
    tuple(DeliveryDisposition),
)
def test_terminal_receipts_reject_every_nonidentical_replacement(
    original: DeliveryDisposition,
    replacement: DeliveryDisposition,
) -> None:
    first = _receipt(original)
    second = replace(
        first,
        disposition=replacement,
        recorded_at_epoch=NOW + 1.0,
    )

    with pytest.raises(DeliveryTransitionError):
        record_delivery(DeliveryLedger((first,)), second)


@pytest.mark.parametrize(
    ("original", "replacement"),
    (
        (DeliveryDisposition.PENDING, DeliveryDisposition.PENDING),
        (DeliveryDisposition.FAILED, DeliveryDisposition.DELIVERED),
        (DeliveryDisposition.FAILED, DeliveryDisposition.FAILED),
        (DeliveryDisposition.SUPPRESSED_QUIET, DeliveryDisposition.DELIVERED),
        (DeliveryDisposition.SUPPRESSED_POLICY, DeliveryDisposition.PENDING),
    ),
)
def test_other_nonidentical_transitions_are_illegal_and_preserve_ledger(
    original: DeliveryDisposition,
    replacement: DeliveryDisposition,
) -> None:
    first = _receipt(original)
    ledger = DeliveryLedger((first,))

    with pytest.raises(DeliveryTransitionError):
        record_delivery(
            ledger,
            replace(
                first,
                disposition=replacement,
                recorded_at_epoch=NOW + 1.0,
            ),
        )

    assert ledger.receipts == (first,)


def test_identical_receipt_is_idempotent() -> None:
    receipt = _receipt(DeliveryDisposition.DELIVERED)
    ledger = DeliveryLedger((receipt,))

    assert record_delivery(ledger, receipt) is ledger


@pytest.mark.parametrize("original", tuple(DeliveryDisposition))
@pytest.mark.parametrize("replacement", tuple(DeliveryDisposition))
def test_full_transition_matrix_matches_the_exact_state_machine(
    original: DeliveryDisposition,
    replacement: DeliveryDisposition,
) -> None:
    first = _receipt(original)
    if replacement is original:
        assert record_delivery(DeliveryLedger((first,)), first) == DeliveryLedger(
            (first,)
        )
        return

    generation = 1 if (
        original is DeliveryDisposition.FAILED
        and replacement is DeliveryDisposition.PENDING
    ) else 0
    second = replace(
        first,
        disposition=replacement,
        recorded_at_epoch=NOW + 1.0,
        attempt_generation=generation,
    )
    allowed = (
        original is DeliveryDisposition.PENDING
        or (
            original is DeliveryDisposition.FAILED
            and replacement is DeliveryDisposition.PENDING
        )
        or (
            original
            in {
                DeliveryDisposition.SUPPRESSED_QUIET,
                DeliveryDisposition.SUPPRESSED_POLICY,
            }
            and replacement is DeliveryDisposition.SUPERSEDED
        )
    )

    if allowed:
        assert record_delivery(DeliveryLedger((first,)), second).receipts == (second,)
    else:
        with pytest.raises(DeliveryTransitionError):
            record_delivery(DeliveryLedger((first,)), second)


def test_channel_and_stage_are_independent_delivery_identities() -> None:
    event = _event()
    notification = DeliveryKey(event, DeliveryChannel.SYSTEM_NOTIFICATION, 0)
    mailbox = DeliveryKey(event, DeliveryChannel.MAILBOX_CUE, 0)
    history = DeliveryKey(event, DeliveryChannel.HISTORY_FACT, 0)
    later_stage = DeliveryKey(event, DeliveryChannel.SYSTEM_NOTIFICATION, 1)
    ledger = DeliveryLedger(())
    for receipt in (
        _receipt(
            DeliveryDisposition.FAILED,
            key=notification,
            diagnostic=DeliveryDiagnostic.DELIVERY_FAILED,
        ),
        _receipt(DeliveryDisposition.DELIVERED, key=mailbox),
        _receipt(DeliveryDisposition.DELIVERED, key=history),
        _receipt(DeliveryDisposition.PENDING, key=later_stage),
    ):
        ledger = record_delivery(ledger, receipt)

    assert delivery_disposition(ledger, notification) is DeliveryDisposition.FAILED
    assert delivery_disposition(ledger, mailbox) is DeliveryDisposition.DELIVERED
    assert delivery_disposition(ledger, history) is DeliveryDisposition.DELIVERED
    assert delivery_disposition(ledger, later_stage) is DeliveryDisposition.PENDING
    assert delivery_disposition(
        ledger,
        DeliveryKey(event, DeliveryChannel.SOUND, 0),
    ) is None


def test_only_delivered_receipts_make_a_delivered_claim() -> None:
    ledger = DeliveryLedger(())
    for index, disposition in enumerate(DeliveryDisposition):
        ledger = record_delivery(
            ledger,
            _receipt(
                disposition,
                key=_key(f"{index + 1:03d}"),
                diagnostic=(
                    DeliveryDiagnostic.DELIVERY_FAILED
                    if disposition is DeliveryDisposition.FAILED
                    else None
                ),
            ),
        )

    delivered = tuple(
        receipt.key
        for receipt in ledger.receipts
        if delivery_disposition(ledger, receipt.key) is DeliveryDisposition.DELIVERED
    )

    assert delivered == (_key("002"),)


def test_pending_quiet_summary_keys_deduplicate_and_sort_provider_events() -> None:
    third = _event("003")
    first = _event("001")
    ledger = DeliveryLedger(
        (
            _receipt(
                DeliveryDisposition.SUPPRESSED_QUIET,
                key=DeliveryKey(third, DeliveryChannel.SOUND, 0),
            ),
            _receipt(
                DeliveryDisposition.SUPPRESSED_QUIET,
                key=DeliveryKey(first, DeliveryChannel.SYSTEM_NOTIFICATION, 0),
            ),
            _receipt(
                DeliveryDisposition.SUPPRESSED_QUIET,
                key=DeliveryKey(first, DeliveryChannel.SOUND, 1),
            ),
            _receipt(
                DeliveryDisposition.SUPPRESSED_POLICY,
                key=DeliveryKey(_event("002"), DeliveryChannel.SOUND, 0),
            ),
        )
    )

    assert pending_quiet_summary_keys(ledger) == (first, third)


def test_quiet_summary_key_is_deterministic_deduplicated_and_order_independent() -> None:
    first = _event("001")
    second = _event("002")

    forward = quiet_summary_key((first, second, first))
    reverse = quiet_summary_key((second, first))

    assert forward == reverse
    assert forward is not None
    assert forward.kind is DeliverySummaryKind.QUIET_EXIT
    assert forward.member_count == 2
    assert forward.member_digest == SummaryDigest(
        "a1bcc65c7abcef81bb6090f48e1dc6b876e18b7236f4bde88f1d26d7a99aef6e"
    )


def test_empty_quiet_summary_is_refused_without_an_identity() -> None:
    assert quiet_summary_key(()) is None


def test_summary_identity_is_not_a_provider_semantic_event() -> None:
    summary = quiet_summary_key((_event(),))
    assert type(summary) is DeliverySummaryKey
    assert type(summary) is not SemanticEventKey
    assert summary is not None

    summary_delivery = DeliveryKey(summary, DeliveryChannel.SYSTEM_NOTIFICATION, 0)

    assert type(summary_delivery.subject_key) is DeliverySummaryKey


def test_ledger_retains_the_newest_512_keys_in_deterministic_key_order() -> None:
    ledger = DeliveryLedger(())
    for index in reversed(range(MAX_DELIVERY_RECEIPTS + 1)):
        suffix = f"{index:03d}"
        ledger = record_delivery(
            ledger,
            _receipt(
                DeliveryDisposition.DELIVERED,
                key=_key(suffix),
                recorded_at=NOW + index,
            ),
        )

    assert len(ledger.receipts) == MAX_DELIVERY_RECEIPTS
    assert tuple(
        receipt.key.subject_key.subject_key.work_id.value
        for receipt in ledger.receipts
    ) == tuple(f"work:{index:03d}" for index in range(1, 513))


def test_delivery_types_reject_invalid_stage_digest_receipts_and_oversize_summary() -> None:
    with pytest.raises(DeliveryValidationError):
        DeliveryKey(_event(), DeliveryChannel.SOUND, 5)
    with pytest.raises(DeliveryValidationError):
        DeliveryKey(_event(), DeliveryChannel.SOUND, True)
    with pytest.raises(DeliveryValidationError):
        SummaryDigest("A" * 64)
    with pytest.raises(DeliveryValidationError):
        DeliveryReceipt(_key(), DeliveryDisposition.PENDING, float("nan"), 0, None)
    with pytest.raises(DeliveryValidationError):
        DeliveryReceipt(_key(), DeliveryDisposition.PENDING, NOW, -1, None)
    with pytest.raises(DeliveryValidationError):
        quiet_summary_key(tuple(_event(f"{index:03d}") for index in range(513)))
