from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sidepulse.dnd_policy import DndMode, DndSource
from sidepulse.menu_projection import (
    MenuProjectionInputs,
    MenuRowKind,
    project_dnd_submenu,
    project_root_menu,
)


def inputs(**changes) -> MenuProjectionInputs:
    values = dict(
        active_count=2,
        needs_you_count=1,
        ready_count=0,
        usage_summary="Claude 36% · Codex 71%",
        connected_device_count=2,
        screen_bar_enabled=True,
        warning_rows=("Grok hooks need reload", "Claude usage needs permission"),
        setup_required=True,
        dnd_mode=None,
        dnd_source=None,
        dnd_active_sources=(),
        dnd_summary="DND: Off",
        dnd_return_time=None,
        dnd_override_active=False,
        dnd_resume_available=False,
        clearable_presented_count=1,
    )
    values.update(changes)
    return MenuProjectionInputs(**values)


def test_root_menu_is_compact_and_semantic() -> None:
    plan = project_root_menu(inputs())
    assert len(plan.rows) <= 15
    assert [row.key for row in plan.rows] == [
        "glance",
        "agents",
        "usage",
        "devices",
        "warning:0",
        "warning:1",
        "diagnostics",
        "setup",
        "dnd",
        "quick_settings",
        "clear_agents",
        "quit",
    ]
    assert plan.rows[0].title == "1 needs you · 2 active"
    assert plan.rows[2].title == "Usage · Claude 36% · Codex 71%"
    assert plan.rows[3].title == "Hardware · 2 connected · Screen Bar on"
    assert plan.rows[3].kind is MenuRowKind.SUBMENU
    quick = next(row for row in plan.rows if row.key == "quick_settings")
    assert quick.title == "Quick Settings"
    assert quick.kind is MenuRowKind.SUBMENU


def test_glance_omits_zero_counts_and_leads_with_attention() -> None:
    plan = project_root_menu(inputs(active_count=0, needs_you_count=2, ready_count=3))

    assert plan.rows[0].title == "2 need you · 3 ready"


def test_hardware_summary_names_screen_bar_without_a_physical_device() -> None:
    plan = project_root_menu(
        inputs(connected_device_count=0, screen_bar_enabled=False)
    )

    hardware = next(row for row in plan.rows if row.key == "devices")
    assert hardware.title == "Hardware · Screen Bar off"


def test_setup_and_clear_agents_hide_when_not_needed() -> None:
    plan = project_root_menu(
        inputs(
            setup_required=False,
            warning_rows=(),
            clearable_presented_count=0,
            active_count=0,
            needs_you_count=0,
        )
    )
    keys = {row.key for row in plan.rows}
    assert "setup" not in keys
    assert "clear_agents" not in keys
    assert plan.rows[0].title == "No agents active"


def test_clear_agents_uses_preview_action_without_exposing_a_count() -> None:
    plan = project_root_menu(inputs(clearable_presented_count=3))
    row = next(row for row in plan.rows if row.key == "clear_agents")

    assert row.title == "Clear Agents…"
    assert row.action == "clearAgents:"
    assert row.kind is MenuRowKind.ACTION
    assert len(plan.rows) <= 15


@pytest.mark.parametrize("value", (-1, 1.0, True))
def test_clearable_presented_count_must_be_a_nonnegative_integer(value: object) -> None:
    with pytest.raises(ValueError):
        inputs(clearable_presented_count=value)


def test_warning_rows_are_bounded_and_actionable() -> None:
    plan = project_root_menu(inputs(warning_rows=tuple(str(i) for i in range(20))))
    warnings = [row for row in plan.rows if row.kind is MenuRowKind.WARNING]
    assert len(warnings) == 3
    assert warnings[-1].title == "18 more issues…"
    assert len(plan.rows) <= 15


def test_dnd_row_shows_off_without_reviving_the_quiet_boolean() -> None:
    plan = project_root_menu(inputs())
    row = next(row for row in plan.rows if row.key == "dnd")

    assert row.title == "DND: Off"
    assert row.kind is MenuRowKind.SUBMENU
    assert all(row.key != "quiet" for row in plan.rows)


def test_dnd_row_shows_temporary_mode_with_exact_return_time() -> None:
    return_time = datetime(2026, 8, 30, 22, 5, tzinfo=timezone.utc)
    plan = project_root_menu(
        inputs(
            dnd_mode=DndMode.MUTE,
            dnd_source=DndSource.MANUAL,
            dnd_active_sources=(DndSource.MANUAL,),
            dnd_summary="DND: Manual Mute",
            dnd_return_time=return_time,
            dnd_override_active=True,
        )
    )
    row = next(row for row in plan.rows if row.key == "dnd")

    assert row.title == "DND: Mute until 10:05 PM"
    assert len(plan.rows) <= 15


def test_dnd_row_shows_scheduled_mode_with_exact_return_time() -> None:
    return_time = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc)
    plan = project_root_menu(
        inputs(
            dnd_mode=DndMode.DARK,
            dnd_source=DndSource.SCHEDULE,
            dnd_active_sources=(DndSource.SCHEDULE,),
            dnd_summary="DND: Scheduled Fully Dark",
            dnd_return_time=return_time,
            dnd_resume_available=True,
        )
    )
    row = next(row for row in plan.rows if row.key == "dnd")

    assert row.title == "DND: Fully Dark, scheduled until 7:00 AM"
    assert len(plan.rows) <= 15


def test_dnd_submenu_exposes_all_modes_and_bounded_contextual_actions() -> None:
    plan = project_dnd_submenu(
        inputs(dnd_resume_available=True, dnd_override_active=True)
    )

    assert [(row.title, row.action) for row in plan] == [
        ("Mute for One Hour", "setDndMuteForHour:"),
        ("Dim for One Hour", "setDndDimForHour:"),
        ("Pause for One Hour", "setDndPauseForHour:"),
        ("Asks Only for One Hour", "setDndAsksOnlyForHour:"),
        ("Fully Dark for One Hour", "setDndDarkForHour:"),
        ("Resume Schedule Until Next Change", "resumeDndUntilNextChange:"),
        ("End Temporary Override", "endDndOverride:"),
        ("DND Settings…", "openDndSettings:"),
    ]
    assert len(plan) == 8


def test_dnd_submenu_hides_inapplicable_resume_and_end_override() -> None:
    plan = project_dnd_submenu(inputs())
    keys = {row.key for row in plan}

    assert "dnd:resume" not in keys
    assert "dnd:end_override" not in keys
    assert "dnd:settings" in keys


def test_dnd_row_names_all_mixed_sources_and_next_exact_change() -> None:
    return_time = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc)
    plan = project_root_menu(
        inputs(
            dnd_mode=DndMode.DIM,
            dnd_source=DndSource.SCHEDULE,
            dnd_active_sources=(DndSource.SCHEDULE, DndSource.MACOS_FOCUS),
            dnd_summary="DND: Scheduled Dim + macOS Focus Mute",
            dnd_return_time=return_time,
            dnd_resume_available=True,
        )
    )
    row = next(row for row in plan.rows if row.key == "dnd")

    assert row.title == "DND: Scheduled Dim + macOS Focus Mute until 7:00 AM"
    assert len(plan.rows) <= 15


def test_dnd_row_names_manual_and_focus_instead_of_only_first_contribution() -> None:
    return_time = datetime(2026, 8, 30, 22, 5, tzinfo=timezone.utc)
    plan = project_root_menu(
        inputs(
            dnd_mode=DndMode.DIM,
            dnd_source=DndSource.MANUAL,
            dnd_active_sources=(DndSource.MANUAL, DndSource.MACOS_FOCUS),
            dnd_summary="DND: Manual Dim + macOS Focus Pause",
            dnd_return_time=return_time,
            dnd_override_active=True,
        )
    )
    row = next(row for row in plan.rows if row.key == "dnd")

    assert row.title == "DND: Manual Dim + macOS Focus Pause until 10:05 PM"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("dnd_mode", "mute"),
        ("dnd_source", "manual"),
        ("dnd_active_sources", [DndSource.MANUAL]),
        ("dnd_summary", ""),
        ("dnd_return_time", datetime(2026, 8, 30, 22, 5)),
        ("dnd_override_active", 1),
        ("dnd_resume_available", 0),
    ),
)
def test_dnd_menu_inputs_refuse_untyped_state(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        inputs(**{field: value})


def test_dnd_menu_inputs_require_mode_and_source_together() -> None:
    with pytest.raises(ValueError):
        inputs(dnd_mode=DndMode.MUTE)
    with pytest.raises(ValueError):
        inputs(dnd_source=DndSource.SCHEDULE)


def test_dnd_menu_inputs_require_primary_source_in_active_sources() -> None:
    with pytest.raises(ValueError):
        inputs(dnd_mode=DndMode.MUTE, dnd_source=DndSource.MANUAL)
