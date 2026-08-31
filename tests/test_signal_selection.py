from __future__ import annotations

import pytest

from sidepulse.dnd_policy import DisplayAdmission
from sidepulse.signal_selection import (
    SIGNAL_CLAIM_PRECEDENCE,
    SignalClaimKey,
    select_active_led_display_kind,
)

EXPECTED_CLAIMS = (
    ("test", "signal_test", False, DisplayAdmission.ALL),
    ("escalation", "escalation", False, DisplayAdmission.ASKS),
    ("weather", "weather", False, DisplayAdmission.ALL),
    ("low_battery", "low_battery", False, DisplayAdmission.CRITICAL),
    ("failure", "failure", False, DisplayAdmission.CRITICAL),
    ("quota", "quota_alert", True, DisplayAdmission.ALL),
    ("reminders", "reminders", True, DisplayAdmission.ALL),
    ("completion", "completion", True, DisplayAdmission.ALL),
    ("reset_celebration", "reset_celebration", True, DisplayAdmission.ALL),
    ("connection", "connection_notice", True, DisplayAdmission.ALL),
    ("peek", "peek", True, DisplayAdmission.ALL),
    ("all_clear", "all_clear", True, DisplayAdmission.ALL),
    ("calendar", "calendar", True, DisplayAdmission.ALL),
    ("battery_selected_or_preview", "battery", False, DisplayAdmission.ALL),
    ("timer", "timer", False, DisplayAdmission.ALL),
    ("studio", "studio", False, DisplayAdmission.ALL),
    ("quota_runway", "quota_runway", False, DisplayAdmission.ALL),
    ("charging_idle", "battery", False, DisplayAdmission.ALL),
)


def _select(
    active: set[SignalClaimKey],
    *,
    signal_policy: str | None = None,
    display_admission: DisplayAdmission = DisplayAdmission.ALL,
    default_claim_admission: DisplayAdmission = DisplayAdmission.ALL,
) -> str | None:
    return select_active_led_display_kind(
        evaluate=lambda key: key in active,
        signal_policy=signal_policy,
        default_display_kind="agent",
        display_admission=display_admission,
        default_claim_admission=default_claim_admission,
    )


def test_signal_claim_precedence_pins_the_exact_current_order_and_metadata() -> None:
    assert tuple(
        (
            spec.key.value,
            spec.display_kind,
            spec.muted_by_asks_only,
            spec.claim_admission,
        )
        for spec in SIGNAL_CLAIM_PRECEDENCE
    ) == EXPECTED_CLAIMS


@pytest.mark.parametrize(
    ("earlier_index", "later_index"),
    tuple(
        (earlier_index, later_index)
        for earlier_index in range(len(SIGNAL_CLAIM_PRECEDENCE))
        for later_index in range(earlier_index + 1, len(SIGNAL_CLAIM_PRECEDENCE))
    ),
)
def test_every_earlier_active_claim_wins_over_every_later_active_claim(
    earlier_index: int,
    later_index: int,
) -> None:
    earlier = SIGNAL_CLAIM_PRECEDENCE[earlier_index]
    later = SIGNAL_CLAIM_PRECEDENCE[later_index]

    assert _select({earlier.key, later.key}) == earlier.display_kind


def test_evaluation_stops_immediately_after_the_first_active_claim() -> None:
    evaluated: list[SignalClaimKey] = []
    winning_key = SignalClaimKey.WEATHER

    def evaluate(key: SignalClaimKey) -> bool:
        evaluated.append(key)
        return key is winning_key

    assert (
        select_active_led_display_kind(
            evaluate=evaluate,
            signal_policy=None,
            default_display_kind="agent",
        )
        == "weather"
    )
    assert evaluated == [
        SignalClaimKey.TEST,
        SignalClaimKey.ESCALATION,
        SignalClaimKey.WEATHER,
    ]


@pytest.mark.parametrize(
    "claim_key",
    [spec.key for spec in SIGNAL_CLAIM_PRECEDENCE if spec.muted_by_asks_only],
)
def test_asks_only_skips_exactly_the_existing_courtesy_claims(
    claim_key: SignalClaimKey,
) -> None:
    assert _select({claim_key}, signal_policy="asks_only") == "agent"


@pytest.mark.parametrize(
    "claim_key",
    [spec.key for spec in SIGNAL_CLAIM_PRECEDENCE if not spec.muted_by_asks_only],
)
def test_asks_only_preserves_critical_pinned_and_ambient_claims(
    claim_key: SignalClaimKey,
) -> None:
    expected = next(
        spec.display_kind for spec in SIGNAL_CLAIM_PRECEDENCE if spec.key is claim_key
    )
    assert _select({claim_key}, signal_policy="asks_only") == expected


def test_asks_only_does_not_evaluate_muted_claims() -> None:
    evaluated: list[SignalClaimKey] = []

    def evaluate(key: SignalClaimKey) -> bool:
        evaluated.append(key)
        if key is SignalClaimKey.QUOTA:
            raise AssertionError("muted claim was evaluated")
        return key is SignalClaimKey.BATTERY_SELECTED_OR_PREVIEW

    assert (
        select_active_led_display_kind(
            evaluate=evaluate,
            signal_policy="asks_only",
            default_display_kind="agent",
        )
        == "battery"
    )
    assert SignalClaimKey.QUOTA not in evaluated


def test_no_active_claim_returns_the_supplied_default() -> None:
    assert _select(set()) == "agent"


@pytest.mark.parametrize(
    ("display_admission", "claim_key", "expected"),
    (
        (DisplayAdmission.ALL, SignalClaimKey.WEATHER, "weather"),
        (DisplayAdmission.CRITICAL, SignalClaimKey.WEATHER, None),
        (DisplayAdmission.CRITICAL, SignalClaimKey.LOW_BATTERY, "low_battery"),
        (DisplayAdmission.CRITICAL, SignalClaimKey.FAILURE, "failure"),
        (DisplayAdmission.CRITICAL, SignalClaimKey.ESCALATION, "escalation"),
        (DisplayAdmission.ASKS, SignalClaimKey.FAILURE, None),
        (DisplayAdmission.ASKS, SignalClaimKey.ESCALATION, "escalation"),
        (DisplayAdmission.NONE, SignalClaimKey.ESCALATION, None),
    ),
)
def test_display_admission_filters_claim_capabilities_before_evaluation(
    display_admission: DisplayAdmission,
    claim_key: SignalClaimKey,
    expected: str | None,
) -> None:
    evaluated: list[SignalClaimKey] = []

    result = select_active_led_display_kind(
        evaluate=lambda key: evaluated.append(key) is None and key is claim_key,
        signal_policy=None,
        default_display_kind="agent",
        display_admission=display_admission,
        default_claim_admission=DisplayAdmission.ALL,
    )

    assert result == expected
    if expected is None:
        assert claim_key not in evaluated


@pytest.mark.parametrize(
    ("display_admission", "standing_admission", "expected"),
    (
        (DisplayAdmission.ALL, DisplayAdmission.ALL, "agent"),
        (DisplayAdmission.CRITICAL, DisplayAdmission.ALL, None),
        (DisplayAdmission.CRITICAL, DisplayAdmission.CRITICAL, "agent"),
        (DisplayAdmission.CRITICAL, DisplayAdmission.ASKS, "agent"),
        (DisplayAdmission.ASKS, DisplayAdmission.CRITICAL, None),
        (DisplayAdmission.ASKS, DisplayAdmission.ASKS, "agent"),
        (DisplayAdmission.NONE, DisplayAdmission.ASKS, None),
    ),
)
def test_standing_agent_truth_uses_its_current_semantic_capability(
    display_admission: DisplayAdmission,
    standing_admission: DisplayAdmission,
    expected: str | None,
) -> None:
    assert (
        _select(
            set(),
            display_admission=display_admission,
            default_claim_admission=standing_admission,
        )
        == expected
    )


def test_evaluator_exception_is_not_swallowed() -> None:
    failure = RuntimeError("claim failed")

    def evaluate(_key: SignalClaimKey) -> bool:
        raise failure

    with pytest.raises(RuntimeError, match="claim failed") as raised:
        select_active_led_display_kind(
            evaluate=evaluate,
            signal_policy=None,
            default_display_kind="agent",
        )

    assert raised.value is failure
