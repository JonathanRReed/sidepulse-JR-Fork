from sidepulse.core_state import CoreDomain, StateDelta
from sidepulse.refresh_admission import admit_refresh


def delta(*domains: CoreDomain, urgent: bool = False) -> StateDelta:
    return StateDelta(
        1,
        1,
        2 if domains else 1,
        frozenset(domains),
        urgent,
    )


def test_noop_is_skipped_until_a_heartbeat_or_dynamic_display() -> None:
    quiet = admit_refresh(
        delta(),
        first_observation=False,
        heartbeat_due=False,
        dynamic_display=False,
        forced=False,
    )
    heartbeat = admit_refresh(
        delta(),
        first_observation=False,
        heartbeat_due=True,
        dynamic_display=False,
        forced=False,
    )
    dynamic = admit_refresh(
        delta(),
        first_observation=False,
        heartbeat_due=False,
        dynamic_display=True,
        forced=False,
    )

    assert quiet.admitted is False
    assert quiet.reason == "noop"
    assert heartbeat.reason == "heartbeat"
    assert heartbeat.admitted is True
    assert dynamic.reason == "dynamic-display"
    assert dynamic.admitted is True


def test_urgent_change_and_explicit_force_are_never_dropped() -> None:
    urgent = admit_refresh(
        delta(CoreDomain.ATTENTION, urgent=True),
        first_observation=False,
        heartbeat_due=False,
        dynamic_display=False,
        forced=False,
    )
    forced = admit_refresh(
        delta(),
        first_observation=False,
        heartbeat_due=False,
        dynamic_display=False,
        forced=True,
    )

    assert urgent.reason == "urgent"
    assert urgent.admitted is True
    assert forced.reason == "forced"
    assert forced.admitted is True


def test_only_menu_relevant_domains_request_menu_work() -> None:
    presentation = admit_refresh(
        delta(CoreDomain.PRESENTATION),
        first_observation=False,
        heartbeat_due=False,
        dynamic_display=False,
        forced=False,
    )
    agents = admit_refresh(
        delta(CoreDomain.AGENTS),
        first_observation=False,
        heartbeat_due=False,
        dynamic_display=False,
        forced=False,
    )

    assert presentation.update_menu is False
    assert agents.update_menu is True
