"""Pure root-menu projection for the compact SidePulse glance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

MAX_ROOT_MENU_ROWS = 15
MAX_WARNING_ROWS = 3


class MenuRowKind(str, Enum):
    STATUS = "status"
    ACTION = "action"
    SUBMENU = "submenu"
    TOGGLE = "toggle"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class MenuProjectionInputs:
    active_count: int
    needs_you_count: int
    ready_count: int
    usage_summary: str | None
    connected_device_count: int
    screen_bar_enabled: bool
    warning_rows: tuple[str, ...]
    setup_required: bool
    quiet_active: bool
    unseen_finished_count: int

    def __post_init__(self) -> None:
        for name in (
            "active_count",
            "needs_you_count",
            "ready_count",
            "connected_device_count",
            "unseen_finished_count",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.usage_summary is not None and (
            not isinstance(self.usage_summary, str)
            or len(self.usage_summary) > 160
            or "\x00" in self.usage_summary
        ):
            raise ValueError("invalid usage summary")
        if type(self.warning_rows) is not tuple or not all(
            isinstance(row, str) and row and len(row) <= 160 and "\x00" not in row
            for row in self.warning_rows
        ):
            raise ValueError("invalid warning rows")
        if not all(
            type(value) is bool
            for value in (self.screen_bar_enabled, self.setup_required, self.quiet_active)
        ):
            raise ValueError("menu flags must be booleans")


@dataclass(frozen=True, slots=True)
class MenuRow:
    key: str
    title: str
    kind: MenuRowKind
    action: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.key, str)
            or not self.key
            or not isinstance(self.title, str)
            or not self.title
            or not isinstance(self.kind, MenuRowKind)
            or (self.action is not None and not isinstance(self.action, str))
            or type(self.enabled) is not bool
        ):
            raise ValueError("invalid menu row")


@dataclass(frozen=True, slots=True)
class RootMenuProjection:
    rows: tuple[MenuRow, ...]

    def __post_init__(self) -> None:
        if (
            type(self.rows) is not tuple
            or not all(type(row) is MenuRow for row in self.rows)
            or len(self.rows) > MAX_ROOT_MENU_ROWS
            or len({row.key for row in self.rows}) != len(self.rows)
        ):
            raise ValueError("invalid root menu projection")


def _glance_title(inputs: MenuProjectionInputs) -> str:
    if inputs.active_count <= 0 and inputs.needs_you_count <= 0:
        if inputs.ready_count > 0:
            return f"{inputs.ready_count} ready for review"
        return "No agents active"
    parts = [f"{inputs.active_count} active"]
    if inputs.needs_you_count:
        count = inputs.needs_you_count
        parts.append(f"{count} {'needs' if count == 1 else 'need'} you")
    if inputs.ready_count:
        parts.append(f"{inputs.ready_count} ready")
    return " · ".join(parts)


def _warning_rows(rows: tuple[str, ...]) -> tuple[MenuRow, ...]:
    if len(rows) <= MAX_WARNING_ROWS:
        return tuple(
            MenuRow(
                key=f"warning:{index}",
                title=f"⚠ {title}",
                kind=MenuRowKind.WARNING,
                action="openDiagnostics:",
            )
            for index, title in enumerate(rows)
        )
    visible = rows[: MAX_WARNING_ROWS - 1]
    result = [
        MenuRow(
            key=f"warning:{index}",
            title=f"⚠ {title}",
            kind=MenuRowKind.WARNING,
            action="openDiagnostics:",
        )
        for index, title in enumerate(visible)
    ]
    result.append(
        MenuRow(
            key=f"warning:{MAX_WARNING_ROWS - 1}",
            title=f"{len(rows) - len(visible)} more issues…",
            kind=MenuRowKind.WARNING,
            action="openDiagnostics:",
        )
    )
    return tuple(result)


def project_root_menu(inputs: MenuProjectionInputs) -> RootMenuProjection:
    if type(inputs) is not MenuProjectionInputs:
        raise TypeError("inputs must be MenuProjectionInputs")

    rows: list[MenuRow] = [
        MenuRow("glance", _glance_title(inputs), MenuRowKind.STATUS, enabled=False),
        MenuRow(
            "agents",
            "Open Agent Browser…",
            MenuRowKind.ACTION,
            action="openAgentBrowser:",
        ),
        MenuRow(
            "usage",
            (
                f"Usage · {inputs.usage_summary}"
                if inputs.usage_summary
                else "Usage · Needs setup"
            ),
            MenuRowKind.SUBMENU,
        ),
        MenuRow(
            "devices",
            # One semantic row for the whole physical concern: devices,
            # the Screen Bar, brightness, keep-awake, calibration, timer.
            f"Hardware · {inputs.connected_device_count} connected",
            MenuRowKind.SUBMENU,
        ),
        MenuRow(
            "screen_bar",
            "Screen Bar",
            MenuRowKind.TOGGLE,
            action="toggleVirtualDevice:",
        ),
    ]
    rows.extend(_warning_rows(inputs.warning_rows))
    rows.append(
        MenuRow(
            "diagnostics",
            "Diagnostics…",
            MenuRowKind.ACTION,
            action="openWhyPanel:",
        )
    )
    if inputs.setup_required:
        rows.append(
            MenuRow("setup", "Finish Setup…", MenuRowKind.ACTION, action="openSetup:")
        )
    rows.extend(
        (
            MenuRow("settings", "Settings…", MenuRowKind.ACTION, action="openSettings:"),
            MenuRow(
                "quiet",
                "Resume Notifications" if inputs.quiet_active else "Quiet for an Hour",
                MenuRowKind.ACTION,
                action="toggleQuiet:",
            ),
        )
    )
    if inputs.unseen_finished_count:
        rows.append(
            MenuRow(
                "clear_finished",
                f"Clear Finished ({inputs.unseen_finished_count})",
                MenuRowKind.ACTION,
                action="clearCompleted:",
            )
        )
    rows.append(MenuRow("quit", "Quit JR-BAR", MenuRowKind.ACTION, action="quit:"))
    return RootMenuProjection(tuple(rows))


__all__ = [
    "MAX_ROOT_MENU_ROWS",
    "MAX_WARNING_ROWS",
    "MenuProjectionInputs",
    "MenuRow",
    "MenuRowKind",
    "RootMenuProjection",
    "project_root_menu",
]
