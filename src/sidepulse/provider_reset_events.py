"""Pure, durable delivery planning for provider-evidenced reset events."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum

from .provider_usage_qol import ResetEvent

RESET_DELIVERY_WINDOW_SECONDS = 300.0
RESET_DELIVERY_PRIORITY = {
    "idle": 10,
    "quota_warning": 20,
    "completion": 30,
    "reset": 40,
    "quota_exhaustion": 50,
    "failure": 60,
    "input": 70,
}


class ResetChannel(str, Enum):
    OVERLAY = "overlay"
    HARDWARE = "hardware"
    NOTIFICATION = "notification"
    SOUND = "sound"


class ResetChannelOutcome(str, Enum):
    DELIVERED = "delivered"
    SUPPRESSED = "suppressed"
    FAILED = "failed"
    DISCARDED = "discarded"


@dataclass(frozen=True, slots=True)
class ResetDeliverySettings:
    overlay: bool = True
    hardware: bool = True
    notification: bool = True
    sound: bool = True

    def enabled(self, channel: ResetChannel) -> bool:
        return bool(getattr(self, channel.value))


@dataclass(frozen=True, slots=True)
class ResetChannelReceipt:
    channel: ResetChannel
    outcome: ResetChannelOutcome
    reason: str
    recorded_at: float


@dataclass(frozen=True, slots=True)
class ResetDeliveryEvent:
    event_id: str
    provider_id: str
    account_id: str
    window_id: str
    reset_boundary: float
    opened_at: float
    settings: ResetDeliverySettings
    receipts: tuple[ResetChannelReceipt, ...] = ()

    @property
    def identity(self) -> tuple[str, str, str, float]:
        return (
            self.provider_id,
            self.account_id,
            self.window_id,
            self.reset_boundary,
        )


@dataclass(frozen=True, slots=True)
class ResetDeliveryState:
    events: tuple[ResetDeliveryEvent, ...] = ()


def _finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("time must be finite")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("time must be finite")
    return result


def begin_reset_delivery(
    state: ResetDeliveryState,
    event: ResetEvent,
    settings: ResetDeliverySettings,
    *,
    now: float,
) -> ResetDeliveryState:
    if type(state) is not ResetDeliveryState or type(event) is not ResetEvent:
        raise TypeError("typed reset delivery state and event required")
    if type(settings) is not ResetDeliverySettings:
        raise TypeError("typed reset delivery settings required")
    opened_at = _finite(now)
    if event.reset_boundary is None:
        raise ValueError("provider reset boundary evidence is required")
    boundary = _finite(event.reset_boundary)
    identity = (
        event.provider_id,
        event.source_instance_id,
        event.lane_id,
        boundary,
    )
    if any(item.identity == identity for item in state.events):
        return state
    receipts = tuple(
        ResetChannelReceipt(channel, ResetChannelOutcome.SUPPRESSED, "disabled", opened_at)
        for channel in ResetChannel
        if not settings.enabled(channel)
    )
    delivery = ResetDeliveryEvent(
        event.event_id,
        event.provider_id,
        event.source_instance_id,
        event.lane_id,
        boundary,
        opened_at,
        settings,
        receipts,
    )
    return ResetDeliveryState((*state.events, delivery))


def _latest(event: ResetDeliveryEvent, channel: ResetChannel) -> ResetChannelReceipt | None:
    return next(
        (receipt for receipt in reversed(event.receipts) if receipt.channel is channel),
        None,
    )


def _channel_terminal(event: ResetDeliveryEvent, channel: ResetChannel) -> bool:
    receipt = _latest(event, channel)
    if receipt is None:
        return False
    if receipt.outcome in {ResetChannelOutcome.DELIVERED, ResetChannelOutcome.DISCARDED}:
        return True
    return receipt.outcome is ResetChannelOutcome.SUPPRESSED and receipt.reason == "disabled"


def apply_reset_channel_receipt(
    state: ResetDeliveryState,
    event_id: str,
    channel: ResetChannel,
    outcome: ResetChannelOutcome,
    *,
    reason: str,
    now: float,
) -> ResetDeliveryState:
    recorded_at = _finite(now)
    if type(reason) is not str or not reason:
        raise ValueError("receipt reason is required")
    updated = []
    found = False
    for event in state.events:
        if event.event_id != event_id:
            updated.append(event)
            continue
        found = True
        if _channel_terminal(event, channel):
            updated.append(event)
            continue
        receipt = ResetChannelReceipt(channel, outcome, reason, recorded_at)
        updated.append(
            ResetDeliveryEvent(
                event.event_id,
                event.provider_id,
                event.account_id,
                event.window_id,
                event.reset_boundary,
                event.opened_at,
                event.settings,
                (*event.receipts, receipt),
            )
        )
    if not found:
        raise KeyError(event_id)
    return ResetDeliveryState(tuple(updated))


def _pending(event: ResetDeliveryEvent) -> tuple[ResetChannel, ...]:
    return tuple(channel for channel in ResetChannel if not _channel_terminal(event, channel))


def pending_reset_channels(
    state: ResetDeliveryState,
    event_id: str,
    *,
    now: float,
    record_discards: bool = False,
):
    current = _finite(now)
    event = next((item for item in state.events if item.event_id == event_id), None)
    if event is None:
        return (state, ()) if record_discards else ()
    pending = _pending(event)
    if current - event.opened_at < RESET_DELIVERY_WINDOW_SECONDS:
        return (state, pending) if record_discards else pending
    if not record_discards:
        return ()
    for channel in pending:
        state = apply_reset_channel_receipt(
            state,
            event_id,
            channel,
            ResetChannelOutcome.DISCARDED,
            reason="delivery_window_expired",
            now=current,
        )
    return state, ()


def reset_event_is_terminal(state: ResetDeliveryState, event_id: str) -> bool:
    event = next((item for item in state.events if item.event_id == event_id), None)
    return event is not None and not _pending(event)


def next_reset_retry_delay(
    state: ResetDeliveryState,
    *,
    now: float,
    interval_seconds: float = 15.0,
) -> float | None:
    current = _finite(now)
    interval = _finite(interval_seconds)
    if interval <= 0:
        raise ValueError("retry interval must be positive")
    remaining = tuple(
        event.opened_at + RESET_DELIVERY_WINDOW_SECONDS - current
        for event in state.events
        if _pending(event)
        and event.opened_at + RESET_DELIVERY_WINDOW_SECONDS > current
    )
    if not remaining:
        return None
    return min(interval, min(remaining))


def encode_reset_delivery_state(state: ResetDeliveryState) -> str:
    document = {
        "version": 1,
        "events": [
            {
                "event_id": event.event_id,
                "provider_id": event.provider_id,
                "account_id": event.account_id,
                "window_id": event.window_id,
                "reset_boundary": event.reset_boundary,
                "opened_at": event.opened_at,
                "settings": {
                    channel.value: event.settings.enabled(channel)
                    for channel in ResetChannel
                },
                "receipts": [
                    {
                        "channel": receipt.channel.value,
                        "outcome": receipt.outcome.value,
                        "reason": receipt.reason,
                        "recorded_at": receipt.recorded_at,
                    }
                    for receipt in event.receipts
                ],
            }
            for event in state.events
        ],
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def decode_reset_delivery_state(payload: str) -> ResetDeliveryState:
    document = json.loads(payload)
    if type(document) is not dict or document.get("version") != 1:
        raise ValueError("unsupported reset delivery document")
    events = []
    for item in document.get("events", ()):
        settings = ResetDeliverySettings(**item["settings"])
        receipts = tuple(
            ResetChannelReceipt(
                ResetChannel(receipt["channel"]),
                ResetChannelOutcome(receipt["outcome"]),
                receipt["reason"],
                _finite(receipt["recorded_at"]),
            )
            for receipt in item["receipts"]
        )
        events.append(
            ResetDeliveryEvent(
                item["event_id"],
                item["provider_id"],
                item["account_id"],
                item["window_id"],
                _finite(item["reset_boundary"]),
                _finite(item["opened_at"]),
                settings,
                receipts,
            )
        )
    return ResetDeliveryState(tuple(events))


__all__ = [
    "RESET_DELIVERY_PRIORITY",
    "RESET_DELIVERY_WINDOW_SECONDS",
    "ResetChannel",
    "ResetChannelOutcome",
    "ResetChannelReceipt",
    "ResetDeliveryEvent",
    "ResetDeliverySettings",
    "ResetDeliveryState",
    "apply_reset_channel_receipt",
    "begin_reset_delivery",
    "decode_reset_delivery_state",
    "encode_reset_delivery_state",
    "next_reset_retry_delay",
    "pending_reset_channels",
    "reset_event_is_terminal",
]
