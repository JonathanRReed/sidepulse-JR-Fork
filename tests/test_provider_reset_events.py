from __future__ import annotations

from dataclasses import replace

import pytest

from sidepulse.provider_reset_events import (
    RESET_DELIVERY_PRIORITY,
    ResetChannel,
    ResetChannelOutcome,
    ResetDeliverySettings,
    ResetDeliveryState,
    apply_reset_channel_receipt,
    begin_reset_delivery,
    decode_reset_delivery_state,
    encode_reset_delivery_state,
    next_reset_retry_delay,
    pending_reset_channels,
    reset_event_is_terminal,
)
from sidepulse.provider_usage_qol import ResetEvent


def _event(event_id: str = "claude:acct:weekly:boundary") -> ResetEvent:
    return ResetEvent(
        event_id=event_id,
        provider_id="claude",
        lane_id="weekly",
        label="Weekly reset",
        occurred_at=1_000.0,
        source_instance_id="acct",
        reset_boundary=900.0,
    )


def test_each_channel_is_independently_enabled_and_receipted() -> None:
    state = begin_reset_delivery(
        ResetDeliveryState(),
        _event(),
        ResetDeliverySettings(
            overlay=True,
            hardware=False,
            notification=True,
            sound=False,
        ),
        now=1_000.0,
    )

    assert pending_reset_channels(state, _event().event_id, now=1_000.0) == (
        ResetChannel.OVERLAY,
        ResetChannel.NOTIFICATION,
    )
    receipts = state.events[0].receipts
    assert [(item.channel, item.outcome, item.reason) for item in receipts] == [
        (ResetChannel.HARDWARE, ResetChannelOutcome.SUPPRESSED, "disabled"),
        (ResetChannel.SOUND, ResetChannelOutcome.SUPPRESSED, "disabled"),
    ]


def test_suppressed_and_failed_enabled_channels_retry_through_299_seconds() -> None:
    state = begin_reset_delivery(
        ResetDeliveryState(), _event(), ResetDeliverySettings(), now=1_000.0
    )
    state = apply_reset_channel_receipt(
        state,
        _event().event_id,
        ResetChannel.OVERLAY,
        ResetChannelOutcome.SUPPRESSED,
        reason="display_suppressed",
        now=1_001.0,
    )
    state = apply_reset_channel_receipt(
        state,
        _event().event_id,
        ResetChannel.HARDWARE,
        ResetChannelOutcome.FAILED,
        reason="device_busy",
        now=1_001.0,
    )

    assert ResetChannel.OVERLAY in pending_reset_channels(
        state, _event().event_id, now=1_299.0
    )
    assert ResetChannel.HARDWARE in pending_reset_channels(
        state, _event().event_id, now=1_299.0
    )
    assert not reset_event_is_terminal(state, _event().event_id)


def test_pending_channels_are_discarded_at_exactly_300_seconds() -> None:
    state = begin_reset_delivery(
        ResetDeliveryState(), _event(), ResetDeliverySettings(), now=1_000.0
    )

    state, pending = pending_reset_channels(
        state, _event().event_id, now=1_300.0, record_discards=True
    )

    assert pending == ()
    assert reset_event_is_terminal(state, _event().event_id)
    assert all(
        receipt.outcome is ResetChannelOutcome.DISCARDED
        and receipt.reason == "delivery_window_expired"
        for receipt in state.events[0].receipts
    )


def test_duplicate_provider_account_window_boundary_is_not_reopened() -> None:
    state = begin_reset_delivery(
        ResetDeliveryState(), _event(), ResetDeliverySettings(), now=1_000.0
    )
    duplicate = replace(_event("different-untrusted-id"))
    state2 = begin_reset_delivery(
        state, duplicate, ResetDeliverySettings(), now=1_001.0
    )

    assert state2 == state


def test_pending_delivery_survives_a_persistence_round_trip() -> None:
    state = begin_reset_delivery(
        ResetDeliveryState(), _event(), ResetDeliverySettings(), now=1_000.0
    )
    restored = decode_reset_delivery_state(encode_reset_delivery_state(state))

    assert restored == state
    assert pending_reset_channels(restored, _event().event_id, now=1_299.0)


def test_visual_suppression_leaves_nonvisual_fallback_pending() -> None:
    state = begin_reset_delivery(
        ResetDeliveryState(), _event(), ResetDeliverySettings(), now=1_000.0
    )
    state = apply_reset_channel_receipt(
        state,
        _event().event_id,
        ResetChannel.OVERLAY,
        ResetChannelOutcome.SUPPRESSED,
        reason="display_suppressed",
        now=1_001.0,
    )

    pending = pending_reset_channels(state, _event().event_id, now=1_001.0)
    assert any(
        channel in pending
        for channel in (
            ResetChannel.HARDWARE,
            ResetChannel.NOTIFICATION,
            ResetChannel.SOUND,
        )
    )


def test_seen_requires_terminal_delivery_or_expiry_not_an_attempt() -> None:
    state = begin_reset_delivery(
        ResetDeliveryState(), _event(), ResetDeliverySettings(), now=1_000.0
    )
    state = apply_reset_channel_receipt(
        state,
        _event().event_id,
        ResetChannel.NOTIFICATION,
        ResetChannelOutcome.DELIVERED,
        reason="posted",
        now=1_001.0,
    )

    assert not reset_event_is_terminal(state, _event().event_id)


def test_reset_delivery_priority_has_the_required_ordering() -> None:
    assert RESET_DELIVERY_PRIORITY["input"] > RESET_DELIVERY_PRIORITY["reset"]
    assert RESET_DELIVERY_PRIORITY["failure"] > RESET_DELIVERY_PRIORITY["reset"]
    assert RESET_DELIVERY_PRIORITY["quota_exhaustion"] > RESET_DELIVERY_PRIORITY["reset"]
    assert RESET_DELIVERY_PRIORITY["reset"] > RESET_DELIVERY_PRIORITY["completion"]
    assert RESET_DELIVERY_PRIORITY["reset"] > RESET_DELIVERY_PRIORITY["quota_warning"]
    assert RESET_DELIVERY_PRIORITY["reset"] > RESET_DELIVERY_PRIORITY["idle"]


def test_retry_delay_runs_independently_and_reaches_exact_expiry() -> None:
    state = begin_reset_delivery(
        ResetDeliveryState(), _event(), ResetDeliverySettings(), now=1_000.0
    )
    assert next_reset_retry_delay(state, now=1_001.0) == 15.0
    assert next_reset_retry_delay(state, now=1_299.9) == pytest.approx(0.1)
    assert next_reset_retry_delay(state, now=1_300.0) is None
