from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sidepulse.dnd_policy import (
    DndMode,
    DndOverride,
    DndSource,
    compose_dnd_contributions,
    contribution_for_mode,
)
from sidepulse.settings import AgentMonitorSettings

ROOT = Path(__file__).resolve().parents[1]
STATUS_BAR = ROOT / "src" / "sidepulse" / "status_bar.py"
MENU_PROJECTION = ROOT / "src" / "sidepulse" / "menu_projection.py"


class _FakeMenuItem:
    def __init__(self, title: str = "", action: str | None = None) -> None:
        self._title = title
        self.action = action
        self.target = None
        self.enabled = None

    @classmethod
    def alloc(cls):
        return cls()

    def initWithTitle_action_keyEquivalent_(self, title, action, key):
        self._title = title
        self.action = action
        return self

    def title(self):
        return self._title

    def setTarget_(self, target) -> None:
        self.target = target

    def setEnabled_(self, enabled) -> None:
        self.enabled = enabled


class _FakeMenu:
    def __init__(self, *items: _FakeMenuItem) -> None:
        self.items = list(items)

    def itemArray(self):
        return tuple(self.items)

    def removeItem_(self, item) -> None:
        self.items.remove(item)

    def insertItem_atIndex_(self, item, index) -> None:
        self.items.insert(index, item)


def _source() -> str:
    return STATUS_BAR.read_text(encoding="utf-8")


def test_status_bar_wires_cached_device_identity_without_diskutil_on_menu_thread() -> None:
    source = _source()
    tree = ast.parse(source)
    build_menu = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build_menu"
    )
    calls = {
        node.func.attr
        for node in ast.walk(build_menu)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "device_id_for_root" in source
    assert "DeviceIdentityCache" in source
    assert "diskutil" not in source
    assert "subprocess" not in source
    assert "run" not in calls


def test_root_menu_groups_devices_and_removes_permanent_tip() -> None:
    source = _source()
    # 2026-08-21: the group grew from devices-only into the whole
    # physical concern -- one "Hardware" root row.
    assert '"Keep Awake With Lid Closed",' in source
    assert '"Brightness",' in source
    assert '"Screen Bar",' in source
    assert 'title.startswith("SidePulse Pro")' in source
    assert 'title.startswith("SidePulse Dot")' in source
    assert 'title.startswith("Tip:")' in source
    assert 'item.setTitle_("Diagnostics…")' in source
    assert 'plan_by_key["devices"].title' in source


def test_compact_adapter_carries_typed_dnd_state_and_removes_legacy_quiet() -> None:
    source = _source()

    assert "DndMode" in source
    assert "DndSource" in source
    assert "dnd_return_time" in source
    assert "dnd_active_sources" in source
    assert "dnd_summary" in source
    assert "dnd_override_active" in source
    assert "dnd_resume_available" in source
    assert "project_dnd_submenu" in source
    assert 'title == "Quiet"' in source
    assert 'title.startswith("End Quiet")' in source
    assert "target_quiet_active" not in source


def test_compact_dnd_submenu_routes_through_retained_controller_selectors() -> None:
    source = MENU_PROJECTION.read_text(encoding="utf-8")
    for selector in (
        "setDndMuteForHour:",
        "setDndDimForHour:",
        "setDndPauseForHour:",
        "setDndAsksOnlyForHour:",
        "setDndDarkForHour:",
        "resumeDndUntilNextChange:",
        "endDndOverride:",
        "openDndSettings:",
    ):
        assert selector in source


def test_compact_menu_projects_one_safe_clear_agents_action() -> None:
    source = _source()

    assert "_legacy.clearable_presented_count(snapshot, target)" in source
    assert 'title == "Clear Agents…"' in source
    assert 'plan_by_key.get("clear_agents")' in source
    assert "_install_clear_agents_action(menu, target, plan_by_key)" in source
    assert "_unseen_finished_count" not in source


def test_clearable_presented_count_adapter_is_exception_safe(monkeypatch) -> None:
    from sidepulse import status_bar

    monkeypatch.setattr(
        status_bar._legacy,
        "clearable_presented_count",
        lambda snapshot, target: 4,
        raising=False,
    )
    assert status_bar._clearable_presented_count(object(), object()) == 4

    def fail(snapshot, target):
        raise RuntimeError("projection unavailable")

    monkeypatch.setattr(
        status_bar._legacy,
        "clearable_presented_count",
        fail,
        raising=False,
    )
    assert status_bar._clearable_presented_count(object(), object()) == 0


def test_clear_agents_compaction_replaces_duplicate_rows_once(monkeypatch) -> None:
    from sidepulse import status_bar

    monkeypatch.setattr(status_bar._legacy, "NSMenuItem", _FakeMenuItem)
    target = object()
    menu = _FakeMenu(
        _FakeMenuItem("Settings…"),
        _FakeMenuItem("Clear Agents…", "clearAgents:"),
        _FakeMenuItem("Clear Agents…", "clearAgents:"),
        _FakeMenuItem("Quit JR-BAR", "quit:"),
    )
    row = SimpleNamespace(title="Clear Agents…", action="clearAgents:", enabled=True)

    status_bar._install_clear_agents_action(menu, target, {"clear_agents": row})

    matches = [item for item in menu.items if item.title() == "Clear Agents…"]
    assert len(matches) == 1
    assert matches[0].action == "clearAgents:"
    assert matches[0].target is target
    assert menu.items.index(matches[0]) < next(
        index for index, item in enumerate(menu.items) if item.title().startswith("Quit ")
    )


def test_clear_agents_compaction_removes_action_when_nothing_is_clearable(
    monkeypatch,
) -> None:
    from sidepulse import status_bar

    monkeypatch.setattr(status_bar._legacy, "NSMenuItem", _FakeMenuItem)
    menu = _FakeMenu(
        _FakeMenuItem("Clear Agents…", "clearAgents:"),
        _FakeMenuItem("Quit JR-BAR", "quit:"),
    )

    status_bar._install_clear_agents_action(menu, object(), {})

    assert [item.title() for item in menu.items] == ["Quit JR-BAR"]


def test_compact_adapter_prefers_manual_override_mode_and_exact_expiry() -> None:
    from sidepulse import status_bar

    now = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc).timestamp()
    override = DndOverride.for_mode(
        DndMode.MUTE,
        created_epoch=now - 60.0,
        until_epoch=now + 3_600.0,
    )
    projection = compose_dnd_contributions(
        (
            contribution_for_mode(DndSource.MANUAL, DndMode.MUTE),
            contribution_for_mode(DndSource.SCHEDULE, DndMode.DIM),
        ),
        next_transition_epoch=now + 600.0,
    )
    target = SimpleNamespace(
        dnd_controller=SimpleNamespace(projection=projection),
        settings=AgentMonitorSettings().with_dnd_override(override),
    )

    (
        mode,
        source,
        active_sources,
        summary,
        return_time,
        override_active,
        resume_available,
    ) = status_bar._dnd_menu_fields(target, now_epoch=now)

    assert mode is DndMode.MUTE
    assert source is DndSource.MANUAL
    assert active_sources == (DndSource.MANUAL, DndSource.SCHEDULE)
    assert summary == "DND: Manual Mute + Scheduled Dim"
    assert return_time is not None
    assert return_time.timestamp() == now + 600.0
    assert override_active is True
    assert resume_available is True


def test_compact_adapter_fails_closed_without_the_typed_controller_protocol() -> None:
    from sidepulse import status_bar

    assert status_bar._dnd_menu_fields(SimpleNamespace()) == (
        None,
        None,
        (),
        "DND: Off",
        None,
        False,
        False,
    )


def test_compact_adapter_preserves_scheduled_and_focus_summary() -> None:
    from sidepulse import status_bar

    now = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc).timestamp()
    projection = compose_dnd_contributions(
        (
            contribution_for_mode(DndSource.SCHEDULE, DndMode.DIM),
            contribution_for_mode(DndSource.MACOS_FOCUS, DndMode.MUTE),
        ),
        next_transition_epoch=now + 600.0,
    )
    target = SimpleNamespace(
        dnd_controller=SimpleNamespace(projection=projection),
        settings=AgentMonitorSettings(),
    )

    fields = status_bar._dnd_menu_fields(target, now_epoch=now)

    assert fields[2] == (DndSource.SCHEDULE, DndSource.MACOS_FOCUS)
    assert fields[3] == "DND: Scheduled Dim + macOS Focus Mute"
    assert fields[4] is not None and fields[4].timestamp() == now + 600.0
    assert fields[6] is True


def test_compact_adapter_preserves_manual_and_focus_summary() -> None:
    from sidepulse import status_bar

    now = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc).timestamp()
    override = DndOverride.for_mode(
        DndMode.DIM,
        created_epoch=now - 60.0,
        until_epoch=now + 3_600.0,
    )
    projection = compose_dnd_contributions(
        (
            contribution_for_mode(DndSource.MANUAL, DndMode.DIM),
            contribution_for_mode(DndSource.MACOS_FOCUS, DndMode.PAUSE),
        ),
        next_transition_epoch=now + 600.0,
    )
    target = SimpleNamespace(
        dnd_controller=SimpleNamespace(projection=projection),
        settings=AgentMonitorSettings().with_dnd_override(override),
    )

    fields = status_bar._dnd_menu_fields(target, now_epoch=now)

    assert fields[2] == (DndSource.MANUAL, DndSource.MACOS_FOCUS)
    assert fields[3] == "DND: Manual Dim + macOS Focus Pause"
    assert fields[4] is not None and fields[4].timestamp() == now + 600.0


def test_menu_compaction_does_not_define_another_objc_controller() -> None:
    classes = {
        node.name
        for node in ast.walk(ast.parse(_source()))
        if isinstance(node, ast.ClassDef)
    }
    assert classes == {"_StatusBarFacade"}
