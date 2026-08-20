"""v2 latest-state codec for per-source clock timing.

The v2 document historically reconstructed per-source quarantine stamps
from the GLOBAL clock_continuity's uncertain_since. The live-source
election severed that: the global status can be STABLE while quiescent
sources still hold timing entries -- the app's own latest.json failed
its own validator and every restart lost its warm start. Timing now
travels explicitly; documents from the broken window HEAL by dropping
the quarantine detail instead of the whole restore.
"""

from __future__ import annotations

from .operator_state import (
    BootIdentifier,
    ClockContinuityState,
    ClockContinuityStatus,
    ClockSample,
    _SourceTimingState,
)


def _has_exact_fields(payload: object, fields: frozenset) -> bool:
    return type(payload) is dict and set(payload) == fields


def source_timing_payloads(state, source_key_to_payload) -> list[dict]:
    return [
        {
            "source_key": source_key_to_payload(item.source_key),
            "uncertain_since_monotonic": item.uncertain_since_monotonic,
            "recovery_confirmations": item.recovery_confirmations,
        }
        for item in state._source_timing
    ]


def source_timing_from_payload(payload, source_key_from_payload) -> tuple:
    if type(payload) is not list or len(payload) > 1_000:
        return ()
    entries = []
    for item in payload:
        if type(item) is not dict:
            continue
        source = source_key_from_payload(item.get("source_key"))
        since = item.get("uncertain_since_monotonic")
        confirmations = item.get("recovery_confirmations")
        if (
            source is not None
            and isinstance(since, (int, float))
            and not isinstance(since, bool)
            and type(confirmations) is int
        ):
            entries.append(_SourceTimingState(source, float(since), confirmations))
    return tuple(entries)


_CLOCK_CONTINUITY_FIELDS = frozenset(
    {"status", "uncertain_since_monotonic", "recovery_confirmations"}
)

_CLOCK_FIELDS = frozenset({"wall_epoch", "monotonic_seconds", "boot_id"})

def clock_continuity_to_payload(
    continuity: ClockContinuityState,
) -> dict[str, object]:
    return {
        "status": continuity.status.value,
        "uncertain_since_monotonic": continuity.uncertain_since_monotonic,
        "recovery_confirmations": continuity.recovery_confirmations,
    }

def clock_continuity_from_payload(payload: object) -> ClockContinuityState:
    if not _has_exact_fields(payload, _CLOCK_CONTINUITY_FIELDS):
        raise ValueError("invalid clock continuity")
    since = payload["uncertain_since_monotonic"]
    confirmations = payload["recovery_confirmations"]
    if not (
        type(payload["status"]) is str
        and (since is None or type(since) in {int, float})
        and type(confirmations) is int
    ):
        raise ValueError("invalid clock continuity")
    return ClockContinuityState(
        ClockContinuityStatus(payload["status"]),
        since,
        confirmations,
    )

def clock_to_payload(clock: ClockSample) -> dict[str, object]:
    return {
        "wall_epoch": clock.wall_epoch,
        "monotonic_seconds": clock.monotonic_seconds,
        "boot_id": clock.boot_id.value,
    }

def clock_from_payload(payload: object) -> ClockSample | None:
    if payload is None:
        return None
    if not _has_exact_fields(payload, _CLOCK_FIELDS):
        raise ValueError("invalid clock sample")
    if not (
        type(payload["wall_epoch"]) in {int, float}
        and type(payload["monotonic_seconds"]) in {int, float}
        and type(payload["boot_id"]) is str
    ):
        raise ValueError("invalid clock sample")
    return ClockSample(
        payload["wall_epoch"],
        payload["monotonic_seconds"],
        BootIdentifier(payload["boot_id"]),
    )
