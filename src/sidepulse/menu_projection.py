"""Pure root-menu projection for the compact SidePulse glance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .dnd_policy import DndMode, DndSource
from .product_identity import PRODUCT_DISPLAY_NAME

MAX_ROOT_MENU_ROWS = 15
MAX_WARNING_ROWS = 3

_DND_MODE_LABELS = {
    DndMode.MUTE: "Mute",
    DndMode.DIM: "Dim",
    DndMode.PAUSE: "Pause",
    DndMode.ASKS_ONLY: "Asks Only",
    DndMode.DARK: "Fully Dark",
}


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
    dnd_mode: DndMode | None
    dnd_source: DndSource | None
    dnd_active_sources: tuple[DndSource, ...]
    dnd_summary: str
    dnd_return_time: datetime | None
    dnd_override_active: bool
    dnd_resume_available: bool
    clearable_presented_count: int

    def __post_init__(self) -> None:
        for name in (
            "active_count",
            "needs_you_count",
            "ready_count",
            "connected_device_count",
            "clearable_presented_count",
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
            for value in (
                self.screen_bar_enabled,
                self.setup_required,
                self.dnd_override_active,
                self.dnd_resume_available,
            )
        ):
            raise ValueError("menu flags must be booleans")
        if self.dnd_mode is not None and type(self.dnd_mode) is not DndMode:
            raise ValueError("DND menu mode must be typed")
        if self.dnd_source is not None and type(self.dnd_source) is not DndSource:
            raise ValueError("DND menu source must be typed")
        if (self.dnd_mode is None) != (self.dnd_source is None):
            raise ValueError("DND menu mode and source must be present together")
        if (
            type(self.dnd_active_sources) is not tuple
            or len(self.dnd_active_sources) > 4
            or not all(type(source) is DndSource for source in self.dnd_active_sources)
            or len(set(self.dnd_active_sources)) != len(self.dnd_active_sources)
        ):
            raise ValueError("DND menu active sources must be bounded and typed")
        if self.dnd_source is not None and self.dnd_source not in self.dnd_active_sources:
            raise ValueError("DND menu primary source must be active")
        if (
            type(self.dnd_summary) is not str
            or not 1 <= len(self.dnd_summary) <= 256
            or "\x00" in self.dnd_summary
            or not self.dnd_summary.startswith("DND: ")
        ):
            raise ValueError("DND menu summary must be bounded policy text")
        if self.dnd_return_time is not None:
            if type(self.dnd_return_time) is not datetime:
                raise ValueError("DND return time must be a datetime")
            try:
                offset = self.dnd_return_time.utcoffset()
            except (OverflowError, ValueError):
                offset = None
            if offset is None:
                raise ValueError("DND return time must include a time zone")


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


def _exact_time(value: datetime) -> str:
    hour = value.hour % 12 or 12
    suffix = "AM" if value.hour < 12 else "PM"
    return f"{hour}:{value.minute:02d} {suffix}"


def _dnd_title(inputs: MenuProjectionInputs) -> str:
    mode = inputs.dnd_mode
    if mode is None:
        return "DND: Off"
    if len(inputs.dnd_active_sources) > 1:
        title = inputs.dnd_summary
        if inputs.dnd_return_time is not None:
            title += f" until {_exact_time(inputs.dnd_return_time)}"
        return title
    title = f"DND: {_DND_MODE_LABELS[mode]}"
    return_time = inputs.dnd_return_time
    if inputs.dnd_source is DndSource.SCHEDULE:
        title += ", scheduled"
    elif inputs.dnd_source is DndSource.MACOS_FOCUS:
        title += ", macOS Focus"
    elif inputs.dnd_source is DndSource.NAMED_FOCUS:
        title += ", Focus detail"
    if return_time is not None:
        title += f" until {_exact_time(return_time)}"
    return title


def project_dnd_submenu(inputs: MenuProjectionInputs) -> tuple[MenuRow, ...]:
    """Project the bounded DND actions without any AppKit dependency."""
    if type(inputs) is not MenuProjectionInputs:
        raise TypeError("inputs must be MenuProjectionInputs")
    rows = [
        MenuRow(
            "dnd:mute",
            "Mute for One Hour",
            MenuRowKind.ACTION,
            action="setDndMuteForHour:",
        ),
        MenuRow(
            "dnd:dim",
            "Dim for One Hour",
            MenuRowKind.ACTION,
            action="setDndDimForHour:",
        ),
        MenuRow(
            "dnd:pause",
            "Pause for One Hour",
            MenuRowKind.ACTION,
            action="setDndPauseForHour:",
        ),
        MenuRow(
            "dnd:asks_only",
            "Asks Only for One Hour",
            MenuRowKind.ACTION,
            action="setDndAsksOnlyForHour:",
        ),
        MenuRow(
            "dnd:dark",
            "Fully Dark for One Hour",
            MenuRowKind.ACTION,
            action="setDndDarkForHour:",
        ),
    ]
    if inputs.dnd_resume_available:
        rows.append(
            MenuRow(
                "dnd:resume",
                "Resume Schedule Until Next Change",
                MenuRowKind.ACTION,
                action="resumeDndUntilNextChange:",
            )
        )
    if inputs.dnd_override_active:
        rows.append(
            MenuRow(
                "dnd:end_override",
                "End Temporary Override",
                MenuRowKind.ACTION,
                action="endDndOverride:",
            )
        )
    rows.append(
        MenuRow(
            "dnd:settings",
            "DND Settings…",
            MenuRowKind.ACTION,
            action="openDndSettings:",
        )
    )
    return tuple(rows)


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
                "dnd",
                _dnd_title(inputs),
                MenuRowKind.SUBMENU,
            ),
        )
    )
    if inputs.clearable_presented_count:
        rows.append(
            MenuRow(
                "clear_agents",
                "Clear Agents…",
                MenuRowKind.ACTION,
                action="clearAgents:",
            )
        )
    rows.append(
        MenuRow(
            "quit",
            f"Quit {PRODUCT_DISPLAY_NAME}",
            MenuRowKind.ACTION,
            action="quit:",
        )
    )
    return RootMenuProjection(tuple(rows))


__all__ = [
    "MAX_ROOT_MENU_ROWS",
    "MAX_WARNING_ROWS",
    "MenuProjectionInputs",
    "MenuRow",
    "MenuRowKind",
    "RootMenuProjection",
    "project_dnd_submenu",
    "project_root_menu",
]
