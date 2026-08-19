"""Authenticated, replay-safe cross-Mac provider usage packets."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass

from .provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
    provider_descriptor,
)

PROVIDER_SYNC_SCHEMA_VERSION = 1
MAX_SYNC_PACKET_BYTES = 2 * 1024 * 1024
_MAX_QUOTA_SNAPSHOTS = 32
_MAX_MACHINE_USAGE = 64
_DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_ALLOWED_CATEGORIES = frozenset({"quota", "token_usage", "agent_activity"})


@dataclass(frozen=True, slots=True)
class MachineUsageObservation:
    device_id: str
    provider_id: str
    observed_at: float
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    model_count: int
    estimated_cost_usd: float | None
    cache_savings_usd: float | None

    def __post_init__(self) -> None:
        provider_descriptor(self.provider_id)
        if (
            not isinstance(self.device_id, str)
            or _DEVICE_ID.fullmatch(self.device_id) is None
            or isinstance(self.observed_at, bool)
            or not isinstance(self.observed_at, (int, float))
            or not math.isfinite(float(self.observed_at))
            or float(self.observed_at) < 0.0
        ):
            raise ValueError("invalid machine usage observation")
        for value in (
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.model_count,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("invalid machine usage count")
        for value in (self.estimated_cost_usd, self.cache_savings_usd):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0.0
            ):
                raise ValueError("invalid machine usage estimate")
        object.__setattr__(self, "observed_at", float(self.observed_at))


@dataclass(frozen=True, slots=True)
class ProviderSyncPacket:
    schema_version: int
    device_id: str
    generated_at: float
    quota_snapshots: tuple[ProviderUsageSnapshot, ...]
    machine_usage: tuple[MachineUsageObservation, ...]
    categories: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.schema_version != PROVIDER_SYNC_SCHEMA_VERSION
            or not isinstance(self.device_id, str)
            or _DEVICE_ID.fullmatch(self.device_id) is None
            or isinstance(self.generated_at, bool)
            or not isinstance(self.generated_at, (int, float))
            or not math.isfinite(float(self.generated_at))
            or float(self.generated_at) < 0.0
            or type(self.quota_snapshots) is not tuple
            or len(self.quota_snapshots) > _MAX_QUOTA_SNAPSHOTS
            or not all(type(item) is ProviderUsageSnapshot for item in self.quota_snapshots)
            or type(self.machine_usage) is not tuple
            or len(self.machine_usage) > _MAX_MACHINE_USAGE
            or not all(type(item) is MachineUsageObservation for item in self.machine_usage)
            or type(self.categories) is not tuple
            or not self.categories
            or len(self.categories) != len(set(self.categories))
            or not set(self.categories).issubset(_ALLOWED_CATEGORIES)
        ):
            raise ValueError("invalid provider sync packet")
        if "quota" not in self.categories and self.quota_snapshots:
            raise ValueError("quota packet category is disabled")
        if "token_usage" not in self.categories and self.machine_usage:
            raise ValueError("token usage packet category is disabled")
        if any(item.device_id != self.device_id for item in self.machine_usage):
            raise ValueError("machine usage device mismatch")
        quota_keys = tuple(
            (item.provider_id, item.account_label or "")
            for item in self.quota_snapshots
        )
        if len(quota_keys) != len(set(quota_keys)):
            raise ValueError("duplicate quota snapshot in sync packet")
        usage_keys = tuple(
            (item.device_id, item.provider_id)
            for item in self.machine_usage
        )
        if len(usage_keys) != len(set(usage_keys)):
            raise ValueError("duplicate machine usage in sync packet")
        object.__setattr__(self, "generated_at", float(self.generated_at))


@dataclass(frozen=True, slots=True)
class MergedProviderSync:
    quota_snapshots: tuple[ProviderUsageSnapshot, ...]
    machine_usage: tuple[MachineUsageObservation, ...]
    total_input_tokens: int
    total_cached_input_tokens: int
    total_output_tokens: int
    total_estimated_cost_usd: float | None
    total_cache_savings_usd: float | None


def _lane_document(lane: UsageLane) -> dict[str, object]:
    return {
        "provider_id": lane.provider_id,
        "lane_id": lane.lane_id,
        "label": lane.label,
        "remaining_percent": lane.remaining_percent,
        "reset_at": lane.reset_at,
        "scope": lane.scope,
        "model": lane.model,
        "feature": lane.feature,
        "bindable": lane.bindable,
        "source_id": lane.source_id,
    }


def _snapshot_document(snapshot: ProviderUsageSnapshot) -> dict[str, object]:
    return {
        "provider_id": snapshot.provider_id,
        "account_label": snapshot.account_label,
        "observed_at": snapshot.observed_at,
        "state": snapshot.state.value,
        "reason_code": snapshot.reason_code,
        "action_label": snapshot.action_label,
        "lanes": [_lane_document(lane) for lane in snapshot.lanes],
        "input_tokens": snapshot.input_tokens,
        "cached_input_tokens": snapshot.cached_input_tokens,
        "output_tokens": snapshot.output_tokens,
        "model_count": snapshot.model_count,
        "estimated_cost_usd": snapshot.estimated_cost_usd,
        "cache_savings_usd": snapshot.cache_savings_usd,
        "credits_remaining": snapshot.credits_remaining,
        "incident": snapshot.incident,
    }


def _machine_document(item: MachineUsageObservation) -> dict[str, object]:
    return {
        "device_id": item.device_id,
        "provider_id": item.provider_id,
        "observed_at": item.observed_at,
        "input_tokens": item.input_tokens,
        "cached_input_tokens": item.cached_input_tokens,
        "output_tokens": item.output_tokens,
        "model_count": item.model_count,
        "estimated_cost_usd": item.estimated_cost_usd,
        "cache_savings_usd": item.cache_savings_usd,
    }


def _packet_document(packet: ProviderSyncPacket) -> dict[str, object]:
    return {
        "schema_version": packet.schema_version,
        "device_id": packet.device_id,
        "generated_at": packet.generated_at,
        "quota_snapshots": [
            _snapshot_document(snapshot) for snapshot in packet.quota_snapshots
        ],
        "machine_usage": [_machine_document(item) for item in packet.machine_usage],
        "categories": list(packet.categories),
    }


def _canonical_payload(packet: ProviderSyncPacket) -> bytes:
    return json.dumps(
        _packet_document(packet),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate_secret(secret: bytes) -> bytes:
    if not isinstance(secret, bytes) or not 24 <= len(secret) <= 128:
        raise ValueError("sync secret must contain 24 to 128 bytes")
    return secret


def encode_signed_packet(packet: ProviderSyncPacket, secret: bytes) -> bytes:
    payload = _canonical_payload(packet)
    signature = hmac.new(_validate_secret(secret), payload, hashlib.sha256).hexdigest()
    envelope = b'{"payload":' + payload + b',"signature":"' + signature.encode("ascii") + b'"}'
    if len(envelope) > MAX_SYNC_PACKET_BYTES:
        raise ValueError("sync packet exceeds size budget")
    return envelope


def _lane(value: object) -> UsageLane:
    if not isinstance(value, dict):
        raise ValueError("invalid sync lane")
    return UsageLane(
        provider_id=value.get("provider_id"),
        lane_id=value.get("lane_id"),
        label=value.get("label"),
        remaining_percent=value.get("remaining_percent"),
        reset_at=value.get("reset_at"),
        scope=value.get("scope"),
        model=value.get("model"),
        feature=value.get("feature"),
        bindable=value.get("bindable"),
        source_id=value.get("source_id"),
    )


def _snapshot(value: object) -> ProviderUsageSnapshot:
    if not isinstance(value, dict):
        raise ValueError("invalid sync snapshot")
    raw_lanes = value.get("lanes")
    if not isinstance(raw_lanes, list) or len(raw_lanes) > 64:
        raise ValueError("invalid sync snapshot lanes")
    return ProviderUsageSnapshot(
        provider_id=value.get("provider_id"),
        account_label=value.get("account_label"),
        observed_at=value.get("observed_at"),
        state=ProviderSourceState(value.get("state")),
        reason_code=value.get("reason_code"),
        action_label=value.get("action_label"),
        lanes=tuple(_lane(item) for item in raw_lanes),
        input_tokens=value.get("input_tokens", 0),
        cached_input_tokens=value.get("cached_input_tokens", 0),
        output_tokens=value.get("output_tokens", 0),
        model_count=value.get("model_count", 0),
        estimated_cost_usd=value.get("estimated_cost_usd"),
        cache_savings_usd=value.get("cache_savings_usd"),
        credits_remaining=value.get("credits_remaining"),
        incident=value.get("incident"),
    )


def _machine(value: object) -> MachineUsageObservation:
    if not isinstance(value, dict):
        raise ValueError("invalid sync machine usage")
    return MachineUsageObservation(
        device_id=value.get("device_id"),
        provider_id=value.get("provider_id"),
        observed_at=value.get("observed_at"),
        input_tokens=value.get("input_tokens", 0),
        cached_input_tokens=value.get("cached_input_tokens", 0),
        output_tokens=value.get("output_tokens", 0),
        model_count=value.get("model_count", 0),
        estimated_cost_usd=value.get("estimated_cost_usd"),
        cache_savings_usd=value.get("cache_savings_usd"),
    )


def _decode_packet_document(document: object) -> ProviderSyncPacket:
    if not isinstance(document, dict):
        raise ValueError("invalid sync payload")
    raw_quota = document.get("quota_snapshots")
    raw_usage = document.get("machine_usage")
    raw_categories = document.get("categories")
    if (
        not isinstance(raw_quota, list)
        or len(raw_quota) > _MAX_QUOTA_SNAPSHOTS
        or not isinstance(raw_usage, list)
        or len(raw_usage) > _MAX_MACHINE_USAGE
        or not isinstance(raw_categories, list)
    ):
        raise ValueError("invalid sync payload collections")
    return ProviderSyncPacket(
        schema_version=document.get("schema_version"),
        device_id=document.get("device_id"),
        generated_at=document.get("generated_at"),
        quota_snapshots=tuple(_snapshot(item) for item in raw_quota),
        machine_usage=tuple(_machine(item) for item in raw_usage),
        categories=tuple(raw_categories),
    )


def decode_signed_packet(data: bytes, secret: bytes) -> ProviderSyncPacket:
    if not isinstance(data, bytes) or len(data) > MAX_SYNC_PACKET_BYTES:
        raise ValueError("invalid sync envelope")
    try:
        envelope = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("invalid sync envelope") from None
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise ValueError("invalid sync envelope")
    signature = envelope.get("signature")
    if not isinstance(signature, str) or len(signature) != 64:
        raise ValueError("invalid sync signature")
    payload = envelope.get("payload")
    payload_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    expected = hmac.new(_validate_secret(secret), payload_bytes, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("sync signature mismatch")
    return _decode_packet_document(payload)


def merge_provider_sync(
    local: ProviderSyncPacket,
    remotes: tuple[ProviderSyncPacket, ...],
) -> MergedProviderSync:
    packets = (local, *remotes)
    quotas: dict[tuple[str, str], ProviderUsageSnapshot] = {}
    machine_usage: dict[tuple[str, str], MachineUsageObservation] = {}
    for packet in packets:
        for snapshot in packet.quota_snapshots:
            key = (snapshot.provider_id, snapshot.account_label or "")
            previous = quotas.get(key)
            if previous is None or snapshot.observed_at > previous.observed_at:
                quotas[key] = snapshot
        for observation in packet.machine_usage:
            key = (observation.device_id, observation.provider_id)
            previous = machine_usage.get(key)
            if previous is None or observation.observed_at > previous.observed_at:
                machine_usage[key] = observation
    quota_rows = tuple(
        quotas[key] for key in sorted(quotas, key=lambda item: (item[0], item[1]))
    )
    usage_rows = tuple(
        machine_usage[key]
        for key in sorted(machine_usage, key=lambda item: (item[0], item[1]))
    )
    cost_values = tuple(
        item.estimated_cost_usd
        for item in usage_rows
        if item.estimated_cost_usd is not None
    )
    savings_values = tuple(
        item.cache_savings_usd
        for item in usage_rows
        if item.cache_savings_usd is not None
    )
    return MergedProviderSync(
        quota_snapshots=quota_rows,
        machine_usage=usage_rows,
        total_input_tokens=sum(item.input_tokens for item in usage_rows),
        total_cached_input_tokens=sum(item.cached_input_tokens for item in usage_rows),
        total_output_tokens=sum(item.output_tokens for item in usage_rows),
        total_estimated_cost_usd=sum(cost_values) if cost_values else None,
        total_cache_savings_usd=sum(savings_values) if savings_values else None,
    )


__all__ = [
    "MAX_SYNC_PACKET_BYTES",
    "PROVIDER_SYNC_SCHEMA_VERSION",
    "MachineUsageObservation",
    "MergedProviderSync",
    "ProviderSyncPacket",
    "decode_signed_packet",
    "encode_signed_packet",
    "merge_provider_sync",
]
