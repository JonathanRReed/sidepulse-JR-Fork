from __future__ import annotations

from sidepulse.menu_projection import (
    MenuProjectionInputs,
    MenuRowKind,
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
        quiet_active=False,
        unseen_finished_count=1,
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
        "screen_bar",
        "warning:0",
        "warning:1",
        "diagnostics",
        "setup",
        "settings",
        "quiet",
        "clear_finished",
        "quit",
    ]
    assert plan.rows[0].title == "2 active · 1 needs you"
    assert plan.rows[2].title == "Usage · Claude 36% · Codex 71%"
    assert plan.rows[3].title == "Devices · 2 connected"
    assert plan.rows[3].kind is MenuRowKind.SUBMENU


def test_setup_and_clear_finished_hide_when_not_needed() -> None:
    plan = project_root_menu(
        inputs(
            setup_required=False,
            warning_rows=(),
            unseen_finished_count=0,
            active_count=0,
            needs_you_count=0,
        )
    )
    keys = {row.key for row in plan.rows}
    assert "setup" not in keys
    assert "clear_finished" not in keys
    assert plan.rows[0].title == "No agents active"


def test_warning_rows_are_bounded_and_actionable() -> None:
    plan = project_root_menu(inputs(warning_rows=tuple(str(i) for i in range(20))))
    warnings = [row for row in plan.rows if row.kind is MenuRowKind.WARNING]
    assert len(warnings) == 3
    assert warnings[-1].title == "18 more issues…"
    assert len(plan.rows) <= 15


def test_quiet_row_reflects_current_state() -> None:
    plan = project_root_menu(inputs(quiet_active=True))
    row = next(row for row in plan.rows if row.key == "quiet")
    assert row.title == "Resume Notifications"
