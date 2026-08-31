"""Pure precedence selection for per-device LED signals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .dnd_policy import DisplayAdmission


class SignalClaimKey(str, Enum):
    """Stable identities for live signal facts supplied by the runtime."""

    TEST = "test"
    ESCALATION = "escalation"
    WEATHER = "weather"
    LOW_BATTERY = "low_battery"
    FAILURE = "failure"
    QUOTA = "quota"
    REMINDERS = "reminders"
    COMPLETION = "completion"
    RESET_CELEBRATION = "reset_celebration"
    CONNECTION = "connection"
    PEEK = "peek"
    ALL_CLEAR = "all_clear"
    CALENDAR = "calendar"
    BATTERY_SELECTED_OR_PREVIEW = "battery_selected_or_preview"
    TIMER = "timer"
    STUDIO = "studio"
    QUOTA_RUNWAY = "quota_runway"
    CHARGING_IDLE = "charging_idle"


@dataclass(frozen=True, slots=True)
class SignalClaimSpec:
    """One signal claim's output, device muting, and DND capability."""

    key: SignalClaimKey
    display_kind: str
    muted_by_asks_only: bool = False
    claim_admission: DisplayAdmission = DisplayAdmission.ALL


SIGNAL_CLAIM_PRECEDENCE: tuple[SignalClaimSpec, ...] = (
    SignalClaimSpec(SignalClaimKey.TEST, "signal_test"),
    SignalClaimSpec(
        SignalClaimKey.ESCALATION,
        "escalation",
        claim_admission=DisplayAdmission.ASKS,
    ),
    SignalClaimSpec(SignalClaimKey.WEATHER, "weather"),
    SignalClaimSpec(
        SignalClaimKey.LOW_BATTERY,
        "low_battery",
        claim_admission=DisplayAdmission.CRITICAL,
    ),
    SignalClaimSpec(
        SignalClaimKey.FAILURE,
        "failure",
        claim_admission=DisplayAdmission.CRITICAL,
    ),
    SignalClaimSpec(SignalClaimKey.QUOTA, "quota_alert", True),
    SignalClaimSpec(SignalClaimKey.REMINDERS, "reminders", True),
    SignalClaimSpec(SignalClaimKey.COMPLETION, "completion", True),
    SignalClaimSpec(SignalClaimKey.RESET_CELEBRATION, "reset_celebration", True),
    SignalClaimSpec(SignalClaimKey.CONNECTION, "connection_notice", True),
    SignalClaimSpec(SignalClaimKey.PEEK, "peek", True),
    SignalClaimSpec(SignalClaimKey.ALL_CLEAR, "all_clear", True),
    SignalClaimSpec(SignalClaimKey.CALENDAR, "calendar", True),
    SignalClaimSpec(SignalClaimKey.BATTERY_SELECTED_OR_PREVIEW, "battery"),
    SignalClaimSpec(SignalClaimKey.TIMER, "timer"),
    SignalClaimSpec(SignalClaimKey.STUDIO, "studio"),
    SignalClaimSpec(SignalClaimKey.QUOTA_RUNWAY, "quota_runway"),
    SignalClaimSpec(SignalClaimKey.CHARGING_IDLE, "battery"),
)

_DISPLAY_ADMISSION_RANK = {
    DisplayAdmission.NONE: 0,
    DisplayAdmission.ASKS: 1,
    DisplayAdmission.CRITICAL: 2,
    DisplayAdmission.ALL: 3,
}


def _admits_claim(
    display_admission: DisplayAdmission,
    claim_admission: DisplayAdmission,
) -> bool:
    if type(display_admission) is not DisplayAdmission:
        raise ValueError("display admission must be typed")
    if type(claim_admission) is not DisplayAdmission:
        raise ValueError("claim admission must be typed")
    return (
        _DISPLAY_ADMISSION_RANK[display_admission]
        >= _DISPLAY_ADMISSION_RANK[claim_admission]
    )


def select_active_led_display_kind(
    *,
    evaluate: Callable[[SignalClaimKey], bool],
    signal_policy: str | None,
    default_display_kind: str,
    display_admission: DisplayAdmission = DisplayAdmission.ALL,
    default_claim_admission: DisplayAdmission = DisplayAdmission.ALL,
) -> str | None:
    """Return the first active claim admitted by both policy layers."""

    asks_only = signal_policy == "asks_only"
    for claim in SIGNAL_CLAIM_PRECEDENCE:
        if not _admits_claim(display_admission, claim.claim_admission):
            continue
        if asks_only and claim.muted_by_asks_only:
            continue
        if evaluate(claim.key):
            return claim.display_kind
    return (
        default_display_kind
        if _admits_claim(display_admission, default_claim_admission)
        else None
    )


__all__ = [
    "SIGNAL_CLAIM_PRECEDENCE",
    "SignalClaimKey",
    "SignalClaimSpec",
    "select_active_led_display_kind",
]
