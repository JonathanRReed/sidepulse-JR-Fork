from __future__ import annotations

from datetime import date, datetime, time
from itertools import repeat
from zoneinfo import ZoneInfo

import pytest

from sidepulse.dnd_policy import (
    DEFAULT_DND_DIM_FRACTION,
    MAX_DND_OVERRIDE_SECONDS,
    DisplayAdmission,
    DndContribution,
    DndMode,
    DndOverride,
    DndSource,
    OutboundAdmission,
    compose_dnd_contributions,
    contribution_for_mode,
    evaluate_dnd_policy,
    evaluate_dnd_schedule,
    parse_dnd_settings,
    serialize_dnd_settings,
)
from sidepulse.local_time_boundary import resolve_local_epoch


@pytest.mark.parametrize(
    ("mode", "display", "brightness", "outbound", "effect_allowed"),
    (
        (DndMode.MUTE, DisplayAdmission.ALL, 1.0, OutboundAdmission.NONE, False),
        (DndMode.DIM, DisplayAdmission.ALL, 0.2, OutboundAdmission.ALL, True),
        (
            DndMode.PAUSE,
            DisplayAdmission.CRITICAL,
            1.0,
            OutboundAdmission.CRITICAL,
            True,
        ),
        (DndMode.ASKS_ONLY, DisplayAdmission.ASKS, 1.0, OutboundAdmission.ASKS, True),
        (DndMode.DARK, DisplayAdmission.NONE, 0.0, OutboundAdmission.NONE, False),
    ),
)
def test_exact_five_mode_matrix(
    mode: DndMode,
    display: DisplayAdmission,
    brightness: float,
    outbound: OutboundAdmission,
    effect_allowed: bool,
) -> None:
    contribution = contribution_for_mode(
        DndSource.MANUAL,
        mode,
        dim_fraction=0.2,
    )

    assert contribution.display_admission is display
    assert contribution.brightness_factor == brightness
    assert contribution.outbound_admission is outbound
    assert contribution.banner_allowed is effect_allowed
    assert contribution.audible_allowed is effect_allowed
    assert contribution.webhook_allowed is effect_allowed


def test_policy_vocabularies_are_exact_and_string_stable() -> None:
    assert tuple(item.value for item in DndMode) == (
        "mute",
        "dim",
        "pause",
        "asks_only",
        "dark",
    )
    assert tuple(item.value for item in DndSource) == (
        "manual",
        "schedule",
        "macos_focus",
        "named_focus",
    )
    assert tuple(item.value for item in DisplayAdmission) == (
        "none",
        "asks",
        "critical",
        "all",
    )
    assert tuple(item.value for item in OutboundAdmission) == (
        "none",
        "asks",
        "critical",
        "all",
    )


def test_dim_and_mute_compose_on_independent_axes() -> None:
    projection = compose_dnd_contributions(
        (
            contribution_for_mode(DndSource.SCHEDULE, DndMode.DIM, dim_fraction=0.2),
            contribution_for_mode(DndSource.MACOS_FOCUS, DndMode.MUTE),
        ),
        next_transition_epoch=1_800_000_000.0,
    )

    assert projection.active_sources == (
        DndSource.SCHEDULE,
        DndSource.MACOS_FOCUS,
    )
    assert projection.display_admission is DisplayAdmission.ALL
    assert projection.brightness_factor == 0.2
    assert projection.outbound_admission is OutboundAdmission.NONE
    assert not projection.banner_allowed
    assert not projection.audible_allowed
    assert not projection.webhook_allowed
    assert projection.summary == "DND: Scheduled Dim + macOS Focus Mute"
    assert projection.reason == (
        "Scheduled Dim and macOS Focus Mute compose on independent presentation axes."
    )
    assert projection.next_transition_epoch == 1_800_000_000.0


def test_empty_composition_is_exactly_off() -> None:
    projection = compose_dnd_contributions(())

    assert projection.active_sources == ()
    assert projection.display_admission is DisplayAdmission.ALL
    assert projection.brightness_factor == 1.0
    assert projection.outbound_admission is OutboundAdmission.ALL
    assert projection.banner_allowed
    assert projection.audible_allowed
    assert projection.webhook_allowed
    assert projection.summary == "DND: Off"
    assert projection.reason == "No DND source is active."
    assert projection.next_transition_epoch is None


def test_contribution_collection_is_bounded_and_source_unique() -> None:
    duplicate = contribution_for_mode(DndSource.MANUAL, DndMode.MUTE)

    with pytest.raises(ValueError, match="duplicate DND source"):
        compose_dnd_contributions((duplicate, duplicate))
    with pytest.raises(ValueError, match="bounded"):
        compose_dnd_contributions(
            tuple(
                DndContribution(
                    source=DndSource.NAMED_FOCUS,
                    mode=None,
                    display_admission=DisplayAdmission.ALL,
                    brightness_factor=1.0,
                    outbound_admission=OutboundAdmission.ALL,
                )
                for _ in range(5)
            )
        )
    with pytest.raises(ValueError, match="bounded"):
        compose_dnd_contributions(repeat(duplicate))


@pytest.mark.parametrize(
    ("start", "end", "at", "active", "next_local"),
    (
        (9 * 60, 17 * 60, (2026, 8, 12, 12, 0), True, (2026, 8, 12, 17, 0)),
        (9 * 60, 17 * 60, (2026, 8, 12, 18, 0), False, (2026, 8, 13, 9, 0)),
        (22 * 60, 7 * 60, (2026, 8, 12, 23, 0), True, (2026, 8, 13, 7, 0)),
        (22 * 60, 7 * 60, (2026, 8, 13, 6, 0), True, (2026, 8, 13, 7, 0)),
        (22 * 60, 7 * 60, (2026, 8, 13, 12, 0), False, (2026, 8, 13, 22, 0)),
    ),
)
def test_same_day_and_overnight_schedule_boundaries(
    start: int,
    end: int,
    at: tuple[int, int, int, int, int],
    active: bool,
    next_local: tuple[int, int, int, int, int],
) -> None:
    zone = ZoneInfo("America/Chicago")
    schedule = parse_dnd_settings(
        {
            "dnd_schedule_enabled": True,
            "dnd_schedule_start_minutes": start,
            "dnd_schedule_end_minutes": end,
            "dnd_schedule_mode": "dark",
        }
    ).schedule
    now = datetime(*at, tzinfo=zone).timestamp()

    result = evaluate_dnd_schedule(schedule, now=now, local_timezone=zone)

    assert result.active is active
    assert result.next_transition_epoch == datetime(
        *next_local,
        tzinfo=zone,
    ).timestamp()


def test_disabled_schedule_has_no_transition() -> None:
    parsed = parse_dnd_settings({})

    result = evaluate_dnd_schedule(
        parsed.schedule,
        now=1_800_000_000.0,
        local_timezone=ZoneInfo("UTC"),
    )

    assert not result.active
    assert result.next_transition_epoch is None


def test_gap_advances_to_first_valid_local_second() -> None:
    zone = ZoneInfo("America/New_York")
    resolved = resolve_local_epoch(
        date(2026, 3, 8),
        time(2, 30),
        zone,
    )

    assert resolved == datetime(2026, 3, 8, 3, 0, tzinfo=zone).timestamp()


def test_fold_chooses_earliest_epoch_at_or_after_lower_bound() -> None:
    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0).timestamp()
    second = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1).timestamp()

    assert resolve_local_epoch(date(2026, 11, 1), time(1, 30), zone) == first
    assert (
        resolve_local_epoch(
            date(2026, 11, 1),
            time(1, 30),
            zone,
            not_before_epoch=first + 1.0,
        )
        == second
    )
    assert (
        resolve_local_epoch(
            date(2026, 11, 1),
            time(1, 30),
            zone,
            not_before_epoch=second + 1.0,
        )
        is None
    )


def test_timezone_change_recomputes_real_schedule_transition() -> None:
    now = datetime(2026, 8, 12, 12, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    schedule = parse_dnd_settings(
        {
            "dnd_schedule_enabled": True,
            "dnd_schedule_start_minutes": 22 * 60,
            "dnd_schedule_end_minutes": 7 * 60,
            "dnd_schedule_mode": "dark",
        }
    ).schedule

    chicago = evaluate_dnd_schedule(
        schedule,
        now=now,
        local_timezone=ZoneInfo("America/Chicago"),
    )
    los_angeles = evaluate_dnd_schedule(
        schedule,
        now=now,
        local_timezone=ZoneInfo("America/Los_Angeles"),
    )

    assert chicago.next_transition_epoch != los_angeles.next_transition_epoch


def test_clock_change_recomputes_activity_from_wall_truth() -> None:
    zone = ZoneInfo("UTC")
    schedule = parse_dnd_settings(
        {
            "dnd_schedule_enabled": True,
            "dnd_schedule_start_minutes": 9 * 60,
            "dnd_schedule_end_minutes": 17 * 60,
            "dnd_schedule_mode": "pause",
        }
    ).schedule

    before = evaluate_dnd_schedule(
        schedule,
        now=datetime(2026, 8, 12, 8, 0, tzinfo=zone).timestamp(),
        local_timezone=zone,
    )
    after_jump = evaluate_dnd_schedule(
        schedule,
        now=datetime(2026, 8, 12, 12, 0, tzinfo=zone).timestamp(),
        local_timezone=zone,
    )

    assert not before.active
    assert before.next_transition_epoch == datetime(
        2026, 8, 12, 9, 0, tzinfo=zone
    ).timestamp()
    assert after_jump.active
    assert after_jump.next_transition_epoch == datetime(
        2026, 8, 12, 17, 0, tzinfo=zone
    ).timestamp()


def test_schedule_next_transition_uses_resolved_spring_gap_boundary() -> None:
    zone = ZoneInfo("America/New_York")
    schedule = parse_dnd_settings(
        {
            "dnd_schedule_enabled": True,
            "dnd_schedule_start_minutes": 2 * 60 + 30,
            "dnd_schedule_end_minutes": 4 * 60,
            "dnd_schedule_mode": "dim",
        }
    ).schedule

    before = evaluate_dnd_schedule(
        schedule,
        now=datetime(2026, 3, 8, 1, 0, tzinfo=zone).timestamp(),
        local_timezone=zone,
    )
    after_gap = evaluate_dnd_schedule(
        schedule,
        now=datetime(2026, 3, 8, 3, 15, tzinfo=zone).timestamp(),
        local_timezone=zone,
    )

    assert not before.active
    assert before.next_transition_epoch == datetime(
        2026, 3, 8, 3, 0, tzinfo=zone
    ).timestamp()
    assert after_gap.active
    assert after_gap.next_transition_epoch == datetime(
        2026, 3, 8, 4, 0, tzinfo=zone
    ).timestamp()


def test_schedule_next_transition_uses_first_fall_fold_boundary() -> None:
    zone = ZoneInfo("America/New_York")
    schedule = parse_dnd_settings(
        {
            "dnd_schedule_enabled": True,
            "dnd_schedule_start_minutes": 1 * 60 + 30,
            "dnd_schedule_end_minutes": 2 * 60 + 30,
            "dnd_schedule_mode": "asks_only",
        }
    ).schedule

    before = evaluate_dnd_schedule(
        schedule,
        now=datetime(2026, 11, 1, 0, 30, tzinfo=zone).timestamp(),
        local_timezone=zone,
    )

    assert not before.active
    assert before.next_transition_epoch == datetime(
        2026,
        11,
        1,
        1,
        30,
        tzinfo=zone,
        fold=0,
    ).timestamp()


def test_temporary_resume_suppresses_only_schedule() -> None:
    zone = ZoneInfo("UTC")
    now = datetime(2026, 8, 12, 23, 0, tzinfo=zone).timestamp()
    parsed = parse_dnd_settings(
        {
            "dnd_schedule_enabled": True,
            "dnd_schedule_start_minutes": 22 * 60,
            "dnd_schedule_end_minutes": 7 * 60,
            "dnd_schedule_mode": "dark",
            "dnd_override_mode": "resume",
            "dnd_override_created_epoch": now - 60.0,
            "dnd_override_until_epoch": now + 3_600.0,
            "dnd_focus_mode": "mute",
        }
    )

    projection = evaluate_dnd_policy(
        schedule=parsed.schedule,
        override=parsed.override,
        dim_fraction=parsed.dim_fraction,
        focus_mode=parsed.focus_mode,
        macos_focus_active=True,
        now=now,
        local_timezone=zone,
    )

    assert projection.active_sources == (DndSource.MACOS_FOCUS,)
    assert projection.outbound_admission is OutboundAdmission.NONE
    assert projection.next_transition_epoch == now + 3_600.0


def test_named_focus_tightens_axes_only_while_public_focus_is_active() -> None:
    zone = ZoneInfo("UTC")
    now = 1_800_000_000.0
    override = DndOverride(DndMode.DIM, False, now - 10.0, now + 300.0)
    named = contribution_for_mode(DndSource.NAMED_FOCUS, DndMode.ASKS_ONLY)

    active = evaluate_dnd_policy(
        schedule=parse_dnd_settings({}).schedule,
        override=override,
        dim_fraction=0.25,
        focus_mode=DndMode.MUTE,
        macos_focus_active=True,
        named_focus=named,
        now=now,
        local_timezone=zone,
    )
    expired = evaluate_dnd_policy(
        schedule=parse_dnd_settings({}).schedule,
        override=override,
        dim_fraction=0.25,
        focus_mode=DndMode.MUTE,
        macos_focus_active=True,
        named_focus=named,
        now=now + 301.0,
        local_timezone=zone,
    )

    assert active.active_sources == (
        DndSource.MANUAL,
        DndSource.MACOS_FOCUS,
        DndSource.NAMED_FOCUS,
    )
    assert active.brightness_factor == 0.25
    assert active.display_admission is DisplayAdmission.ASKS
    assert active.outbound_admission is OutboundAdmission.NONE
    assert active.next_transition_epoch == now + 300.0
    assert expired.active_sources == (
        DndSource.MACOS_FOCUS,
        DndSource.NAMED_FOCUS,
    )
    assert expired.display_admission is DisplayAdmission.ASKS
    assert expired.outbound_admission is OutboundAdmission.NONE
    assert expired.next_transition_epoch is None


def test_named_focus_detail_cannot_activate_dnd_without_public_active_truth() -> None:
    now = 1_800_000_000.0
    named = contribution_for_mode(DndSource.NAMED_FOCUS, DndMode.DARK)

    projection = evaluate_dnd_policy(
        schedule=parse_dnd_settings({}).schedule,
        override=None,
        dim_fraction=0.25,
        focus_mode=DndMode.PAUSE,
        macos_focus_active=False,
        named_focus=named,
        now=now,
        local_timezone=ZoneInfo("UTC"),
    )

    assert projection.active_sources == ()
    assert projection.summary == "DND: Off"
    assert projection.display_admission is DisplayAdmission.ALL
    assert projection.brightness_factor == 1.0
    assert projection.outbound_admission is OutboundAdmission.ALL


def test_launch_after_override_expiry_ignores_it_without_replay() -> None:
    now = 1_800_000_000.0
    override = DndOverride(DndMode.MUTE, False, now - 3_600.0, now - 1.0)

    projection = evaluate_dnd_policy(
        schedule=parse_dnd_settings({}).schedule,
        override=override,
        dim_fraction=DEFAULT_DND_DIM_FRACTION,
        focus_mode=DndMode.PAUSE,
        now=now,
        local_timezone=ZoneInfo("UTC"),
    )

    assert projection.summary == "DND: Off"
    assert projection.next_transition_epoch is None


def test_strict_settings_parse_is_lossless_for_valid_fields() -> None:
    raw = {
        "dnd_schedule_enabled": True,
        "dnd_schedule_start_minutes": 1,
        "dnd_schedule_end_minutes": 1439,
        "dnd_schedule_mode": "asks_only",
        "dnd_dim_fraction": 0.35,
        "dnd_override_mode": "pause",
        "dnd_override_created_epoch": 1_800_000_000.0,
        "dnd_override_until_epoch": 1_800_003_600.0,
        "dnd_focus_mode": "dim",
    }

    parsed = parse_dnd_settings(raw)

    assert parsed.refusals == ()
    assert serialize_dnd_settings(parsed) == raw


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("dnd_schedule_enabled", 1),
        ("dnd_schedule_start_minutes", True),
        ("dnd_schedule_start_minutes", -1),
        ("dnd_schedule_end_minutes", 1440),
        ("dnd_schedule_mode", "unknown"),
        ("dnd_dim_fraction", True),
        ("dnd_dim_fraction", float("nan")),
        ("dnd_dim_fraction", 0.0),
        ("dnd_focus_mode", "resume"),
    ),
)
def test_malformed_scalar_is_individually_refused(field: str, value: object) -> None:
    raw = {
        "dnd_schedule_enabled": True,
        "dnd_schedule_start_minutes": 1320,
        "dnd_schedule_end_minutes": 420,
        "dnd_schedule_mode": "dark",
        "dnd_dim_fraction": 0.2,
        "dnd_focus_mode": "pause",
    }
    raw[field] = value

    parsed = parse_dnd_settings(raw)

    assert tuple(item.field for item in parsed.refusals) == (field,)
    assert serialize_dnd_settings(parsed)[field] != value
    assert parsed.schedule.enabled is (False if field == "dnd_schedule_enabled" else True)


def test_equal_schedule_boundaries_are_refused_as_one_schedule() -> None:
    parsed = parse_dnd_settings(
        {
            "dnd_schedule_enabled": True,
            "dnd_schedule_start_minutes": 420,
            "dnd_schedule_end_minutes": 420,
            "dnd_schedule_mode": "mute",
        }
    )

    assert not parsed.schedule.enabled
    assert tuple(item.field for item in parsed.refusals) == (
        "dnd_schedule_start_minutes",
        "dnd_schedule_end_minutes",
    )


@pytest.mark.parametrize(
    "raw_override",
    (
        {
            "dnd_override_mode": "mute",
            "dnd_override_created_epoch": None,
            "dnd_override_until_epoch": 2.0,
        },
        {
            "dnd_override_mode": "unknown",
            "dnd_override_created_epoch": 1.0,
            "dnd_override_until_epoch": 2.0,
        },
        {
            "dnd_override_mode": "mute",
            "dnd_override_created_epoch": True,
            "dnd_override_until_epoch": 2.0,
        },
        {
            "dnd_override_mode": "mute",
            "dnd_override_created_epoch": 2.0,
            "dnd_override_until_epoch": 1.0,
        },
        {
            "dnd_override_mode": "mute",
            "dnd_override_created_epoch": 1.0,
            "dnd_override_until_epoch": 1.0 + MAX_DND_OVERRIDE_SECONDS + 1.0,
        },
    ),
)
def test_malformed_or_overlong_override_is_refused_as_one_entry(
    raw_override: dict[str, object],
) -> None:
    parsed = parse_dnd_settings(raw_override)

    assert parsed.override is None
    assert tuple(item.field for item in parsed.refusals) == ("dnd_override",)
    serialized = serialize_dnd_settings(parsed)
    assert serialized["dnd_override_mode"] is None
    assert serialized["dnd_override_created_epoch"] is None
    assert serialized["dnd_override_until_epoch"] is None


def test_models_reject_wrong_types_and_nonfinite_values() -> None:
    with pytest.raises(ValueError):
        DndOverride(DndMode.MUTE, False, 1.0, float("inf"))
    with pytest.raises(ValueError):
        contribution_for_mode(DndSource.MANUAL, DndMode.DIM, dim_fraction=True)
    with pytest.raises(ValueError):
        compose_dnd_contributions((), next_transition_epoch=float("nan"))


def test_pure_policy_does_not_import_appkit_or_own_stateful_authorities() -> None:
    import sidepulse.dnd_policy as policy

    source = open(policy.__file__, encoding="utf-8").read()
    assert "AppKit" not in source
    assert "Foundation" not in source
    assert "load_settings" not in source
    assert "Path(" not in source
    assert "threading" not in source
    assert "Timer" not in source
