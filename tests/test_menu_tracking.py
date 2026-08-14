from __future__ import annotations

from dataclasses import replace

import pytest
from AppKit import (
    NSApplication,
    NSMenu,
    NSMenuItem,
    NSSearchField,
    NSTableView,
    NSView,
    NSWindow,
)

from sidepulse.agent_browser import (
    AgentBrowserDocument,
    AgentBrowserProjection,
    ApprovedSearchLabel,
    SearchLabelSource,
)
from sidepulse.agent_browser_window import (
    AgentBrowserActionPayload,
    AgentBrowserOpenPayload,
    AgentBrowserWindowController,
    build_agent_root_items,
)
from sidepulse.capacity_types import SourceKey
from sidepulse.mailbox import MailboxSectionKind
from sidepulse.menu_tracking import (
    DeferredMenuPublication,
    ExactBoundarySchedule,
    MenuItemState,
    MenuPublicationKind,
    StableNativeMenuRegistry,
    VisitEvidenceKind,
    plan_menu_publication,
    visit_evidence_confirms_row,
)
from sidepulse.navigation_policy import OperatorActionDescriptor, OperatorActionKind
from sidepulse.provider_facts import (
    SourceFreshness,
    WorkIdentifier,
    WorkKey,
)


def _item(
    key: str,
    *,
    order: int = 0,
    parent: str | None = None,
    submenu: str | None = None,
    action: OperatorActionKind | None = None,
    title: str = "Agent",
    width: int = 80,
    height: int = 22,
) -> MenuItemState:
    return MenuItemState(
        item_key=key,
        parent_key=parent,
        order=order,
        submenu_key=submenu,
        action_kind=action,
        key_equivalent="",
        title=title,
        enabled=True,
        state=0,
        measured_width=width,
        measured_height=height,
        accessibility_label="Agent row",
        accessibility_value="Active",
        accessibility_help="Open actions",
    )


def test_identical_menu_has_no_publication() -> None:
    row = _item("row")
    publication = plan_menu_publication((row,), (row,), tracking=True)

    assert publication.kind is MenuPublicationKind.NO_CHANGE
    assert publication.patches == ()


def test_non_geometric_copy_and_state_patch_in_place() -> None:
    before = _item("row", title="Agent 9m")
    after = replace(
        before,
        title="Agent 8m",
        enabled=False,
        state=1,
        accessibility_value="Eight minutes remaining",
        accessibility_help="Waiting for a fresh source",
    )

    publication = plan_menu_publication((before,), (after,), tracking=True)

    assert publication.kind is MenuPublicationKind.PATCH_IN_PLACE
    assert publication.patches == (after,)


def test_title_geometry_change_defers_instead_of_moving_highlighted_row() -> None:
    before = _item("row", title="9m", width=20)
    after = replace(before, title="10m", measured_width=28)

    publication = plan_menu_publication((before,), (after,), tracking=True)

    assert publication.kind is MenuPublicationKind.DEFER_REBUILD
    assert publication.patches == ()


def test_each_structural_change_defers_during_tracking() -> None:
    first = _item("first", order=0, submenu="actions")
    second = _item("second", order=1)
    structural_variants = (
        (first,),
        (first, second, _item("inserted", order=2)),
        (replace(first, order=1), replace(second, order=0)),
        (replace(first, submenu_key="replacement"), second),
        (replace(first, action_kind=OperatorActionKind.OPEN), second),
        (replace(first, parent_key="other"), second),
        (replace(first, key_equivalent="o"), second),
        (replace(first, measured_height=24), second),
        (replace(first, accessibility_label="Different row"), second),
    )

    for current in structural_variants:
        publication = plan_menu_publication((first, second), current, tracking=True)
        assert publication.kind is MenuPublicationKind.DEFER_REBUILD
        assert publication.patches == ()


def test_one_hundred_row_copy_burst_patches_without_reordering() -> None:
    previous = tuple(_item(f"row:{index}", order=index) for index in range(100))
    current = tuple(
        replace(row, title=f"Agent {index}", accessibility_value=f"Row {index}") for index, row in enumerate(previous)
    )

    publication = plan_menu_publication(previous, current, tracking=True)

    assert publication.kind is MenuPublicationKind.PATCH_IN_PLACE
    assert publication.patches == current


def test_deferred_publication_keeps_only_latest_state_and_rebuilds_once() -> None:
    initial = (_item("first"),)
    deferred = DeferredMenuPublication(initial)
    second = (_item("first"), _item("second", order=1))
    latest = (_item("latest"),)

    assert deferred.publish(second, tracking=True).kind is MenuPublicationKind.DEFER_REBUILD
    assert deferred.publish(latest, tracking=True).kind is MenuPublicationKind.DEFER_REBUILD
    assert deferred.take_deferred_after_close() == latest
    assert deferred.take_deferred_after_close() is None


def test_woke_edge_is_retained_until_the_deferred_state_is_published() -> None:
    initial = (_item("row", title="Snoozed"),)
    woke = (
        replace(
            initial[0],
            title="Woke",
            measured_width=52,
            accessibility_value="Woke",
        ),
    )
    deferred = DeferredMenuPublication(initial)

    deferred.publish(woke, tracking=True, irreversible_item_keys=frozenset({"row"}))
    superseding = (replace(initial[0], title="Active"),)
    deferred.publish(superseding, tracking=True)

    pending = deferred.take_deferred_after_close()
    assert pending is not None
    assert pending[0].title == "Woke"
    deferred.mark_published(pending)
    assert deferred.irreversible_item_keys == frozenset()


def test_exact_boundary_schedule_rejects_early_and_stale_callbacks() -> None:
    schedule = ExactBoundarySchedule()
    first = schedule.replace(101.25)
    second = schedule.replace(102.5)

    assert schedule.callback_due(first, now_epoch=103.0) is False
    assert schedule.callback_due(second, now_epoch=102.49) is False
    assert schedule.callback_due(second, now_epoch=102.5) is True
    assert schedule.callback_due(second, now_epoch=104.0) is False


def test_exact_next_copy_boundary_is_preserved_without_bucket_rounding() -> None:
    schedule = ExactBoundarySchedule()
    token = schedule.replace(1_800_000_012.345678)

    assert token.deadline_epoch == 1_800_000_012.345678
    assert schedule.deadline_epoch == 1_800_000_012.345678


def test_visit_requires_reveal_plus_real_row_focus_or_activation() -> None:
    assert not visit_evidence_confirms_row(
        VisitEvidenceKind.ROOT_OPEN,
        revealed=False,
    )
    assert not visit_evidence_confirms_row(
        VisitEvidenceKind.SHELF_REVEALED,
        revealed=True,
    )
    assert not visit_evidence_confirms_row(
        VisitEvidenceKind.ROW_FOCUSED,
        revealed=False,
    )
    assert visit_evidence_confirms_row(
        VisitEvidenceKind.ROW_FOCUSED,
        revealed=True,
    )
    assert visit_evidence_confirms_row(
        VisitEvidenceKind.ROW_ACTIVATED,
        revealed=True,
    )
    assert visit_evidence_confirms_row(
        VisitEvidenceKind.BROWSER_ROW_FOCUSED,
        revealed=True,
    )


def test_native_registry_patches_real_item_without_replacing_highlighted_identity() -> None:
    before = _item("row", title="Agent 9m")
    after = replace(before, title="Agent 8m", state=1)
    native = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(before.title, None, "")
    registry = StableNativeMenuRegistry()
    registry.install((before,), {"row": native})

    publication = registry.publish((after,), tracking=True)

    assert publication.kind is MenuPublicationKind.PATCH_IN_PLACE
    assert registry.item_for_key("row") is native
    assert native.title() == "Agent 8m"
    assert native.state() == 1


def test_native_registry_defers_custom_view_copy_and_coalesces_latest_rebuild() -> None:
    before = _item("row", title="Agent 9m")
    native = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(before.title, None, "")
    native.setView_(NSView.alloc().init())
    registry = StableNativeMenuRegistry()
    registry.install((before,), {"row": native})

    first = (replace(before, title="Agent 8m"),)
    latest = (replace(before, title="Agent 7m"),)
    assert registry.publish(first, tracking=True).kind is MenuPublicationKind.DEFER_REBUILD
    assert registry.publish(latest, tracking=True).kind is MenuPublicationKind.DEFER_REBUILD
    assert native.title() == "Agent 9m"
    assert registry.take_deferred_after_close() == latest
    assert registry.take_deferred_after_close() is None


def test_native_registry_defers_geometry_change_and_preserves_item_identity() -> None:
    before = _item("row", title="9m", width=20)
    after = replace(before, title="10m", measured_width=28)
    native = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(before.title, None, "")
    registry = StableNativeMenuRegistry()
    registry.install((before,), {"row": native})

    publication = registry.publish((after,), tracking=True)

    assert publication.kind is MenuPublicationKind.DEFER_REBUILD
    assert registry.item_for_key("row") is native
    assert native.title() == "9m"


def _work_key(value: str) -> WorkKey:
    return WorkKey(
        SourceKey("codex", "hooks", "local:test", "live_agent_events"),
        WorkIdentifier(value),
    )


def _document(value: str, *, actionable: bool = False) -> AgentBrowserDocument:
    provider = ApprovedSearchLabel("Codex", SearchLabelSource.PROVIDER)
    return AgentBrowserDocument(
        work_key=_work_key(value),
        provider_label=provider,
        safe_family_label=f"Codex {value}",
        search_labels=(provider,),
        lifecycle_label="needs you" if actionable else "active",
        actionable=actionable,
        request_phase=None,
        source_freshness=SourceFreshness.FRESH,
        worker_count=0,
        pinned=False,
        watched=False,
        snoozed=False,
        woke=False,
        acknowledged=False,
        timing_uncertain=False,
    )


def _projection(*documents: AgentBrowserDocument, generation: int = 7) -> AgentBrowserProjection:
    return AgentBrowserProjection(
        generation=generation,
        rows=documents,
        total_count=len(documents),
        scoped_count=len(documents),
        selected_work_key=None,
    )


def _action(
    kind: OperatorActionKind,
    title: str,
    *,
    enabled: bool = True,
) -> OperatorActionDescriptor:
    return OperatorActionDescriptor(
        kind=kind,
        title=title,
        enabled=enabled,
        disabled_reason=None if enabled else "Not available",
        key_equivalent="",
    )


@pytest.fixture(scope="module", autouse=True)
def _application() -> None:
    NSApplication.sharedApplication()


def test_agent_browser_uses_one_reusable_native_window_and_standard_controls() -> None:
    controller = AgentBrowserWindowController.alloc().init()
    window = controller.window

    assert isinstance(window, NSWindow)
    assert isinstance(controller.search_field, NSSearchField)
    assert isinstance(controller.table_view, NSTableView)
    assert controller.open_with_projection(_projection(_document("one")), show=False) is window
    assert controller.open_with_projection(_projection(_document("two")), show=False) is window


def test_injected_root_items_cap_urgent_rows_and_keep_action_depth_shallow() -> None:
    rows = tuple(_document(f"urgent:{index}", actionable=True) for index in range(5))
    actions = {
        row.work_key: (
            _action(OperatorActionKind.OPEN, "Open"),
            _action(OperatorActionKind.ACKNOWLEDGE, "I'm on It"),
        )
        for row in rows
    }

    items = build_agent_root_items(
        _projection(*rows),
        actions_by_work_key=actions,
        target=None,
    )

    assert items[0].title() == "Agent Mailbox · 5 active · 5 need you"
    urgent = items[1:4]
    assert len(urgent) == 3
    assert [item.title() for item in urgent] == [
        "Codex urgent:0 · needs you",
        "Codex urgent:1 · needs you",
        "Codex urgent:2 · needs you",
    ]
    for row, item in zip(rows, urgent, strict=False):
        assert item.submenu().numberOfItems() == 2
        for index in range(item.submenu().numberOfItems()):
            action = item.submenu().itemAtIndex_(index)
            assert action.submenu() is None
            assert type(action.representedObject()) is AgentBrowserActionPayload
            assert action.representedObject().work_key == row.work_key

    browser = items[5]
    assert browser.title() == "Open Agent Browser..."
    assert browser.isEnabled()
    assert type(browser.representedObject()) is AgentBrowserOpenPayload
    assert browser.representedObject().generation == 7


def test_injected_root_exposes_enabled_exact_shelf_overflow() -> None:
    rows = tuple(
        _document(f"urgent:{index}", actionable=True) for index in range(5)
    )
    projection = AgentBrowserProjection(7, rows, 5, 5, rows[0].work_key)

    items = build_agent_root_items(
        projection,
        actions_by_work_key={
            row.work_key: (_action(OperatorActionKind.OPEN, "Open"),)
            for row in rows
        },
        target=None,
    )

    overflow = items[-2]
    assert overflow.title() == "2 more..."
    assert overflow.isEnabled()
    assert overflow.representedObject() == AgentBrowserOpenPayload(
        7,
        shelf=MailboxSectionKind.NEEDS_YOU,
    )
    assert items[-1].title() == "Open Agent Browser..."


def test_injected_root_expands_snooze_presets_without_nested_action_menu() -> None:
    row = _document("quiet", actionable=True)
    snooze = _action(OperatorActionKind.SNOOZE, "Snooze")

    items = build_agent_root_items(
        _projection(row),
        actions_by_work_key={row.work_key: (snooze,)},
        target=None,
    )

    submenu = items[1].submenu()
    assert [
        submenu.itemAtIndex_(index).title()
        for index in range(submenu.numberOfItems())
    ] == [
        "Snooze 15 Minutes",
        "Snooze 1 Hour",
        "Snooze Until Tomorrow",
    ]
    for index in range(submenu.numberOfItems()):
        action = submenu.itemAtIndex_(index)
        assert action.submenu() is None
        assert action.representedObject().snooze_preset is not None


def test_injected_root_browser_payload_can_scope_exact_shelf_or_family() -> None:
    row = _document("scope")

    family_items = build_agent_root_items(
        _projection(row),
        actions_by_work_key={},
        target=None,
        family_key=row.work_key,
    )
    shelf_items = build_agent_root_items(
        _projection(row),
        actions_by_work_key={},
        target=None,
        shelf=MailboxSectionKind.NEEDS_YOU,
    )

    assert family_items[-1].representedObject().family_key == row.work_key
    assert family_items[-1].representedObject().shelf is None
    assert shelf_items[-1].representedObject().shelf is MailboxSectionKind.NEEDS_YOU
    assert shelf_items[-1].representedObject().family_key is None


def test_background_projection_preserves_selected_work_key_and_search_selection() -> None:
    first = _document("first")
    second = _document("second")
    controller = AgentBrowserWindowController.alloc().init()
    controller.open_with_projection(_projection(first, second), show=False)
    controller.select_work_key(second.work_key, notify_visit=False)
    controller.search_field.setStringValue_("codex")
    controller.search_field.currentEditor().setSelectedRange_((1, 3))

    controller.publish_projection(_projection(second, first, generation=8))

    assert controller.selected_work_key == second.work_key
    assert tuple(controller.search_field.currentEditor().selectedRange()) == (1, 3)


def test_close_clears_query_and_worker_scope_but_keeps_last_selection_in_memory() -> None:
    selected = _document("selected")
    controller = AgentBrowserWindowController.alloc().init()
    controller.open_with_projection(_projection(selected), show=False)
    controller.select_work_key(selected.work_key, notify_visit=False)
    controller.search_field.setStringValue_("active")
    controller.worker_scope = selected.work_key

    controller.windowWillClose_(None)

    assert controller.search_field.stringValue() == ""
    assert controller.worker_scope is None
    assert controller.last_selected_work_key == selected.work_key


def test_worker_button_enters_exact_selected_family_scope() -> None:
    family = replace(_document("family"), worker_count=2)
    worker = _document("worker")
    controller = AgentBrowserWindowController.alloc().init()
    calls: list[WorkKey | None] = []

    def project(_text, *, family_key, selected_work_key):
        calls.append(family_key)
        return _projection(worker)

    controller.query_handler = project
    controller.open_with_projection(_projection(family), show=False)

    assert controller.workers_button.isEnabled()
    assert controller.expandSelectedFamily_(None) is True
    assert controller.worker_scope == family.work_key
    assert calls == [family.work_key]
    assert controller.projection.rows == (worker,)


def test_keyboard_flow_moves_opens_focuses_and_escapes_in_order() -> None:
    first = _document("first")
    second = _document("second")
    activated: list[AgentBrowserActionPayload] = []
    controller = AgentBrowserWindowController.alloc().init()
    controller.action_handler = activated.append
    actions = {
        first.work_key: (_action(OperatorActionKind.OPEN, "Open"),),
        second.work_key: (_action(OperatorActionKind.OPEN, "Open"),),
    }
    controller.open_with_projection(
        _projection(first, second),
        actions_by_work_key=actions,
        show=False,
    )

    controller.moveDown_(None)
    assert controller.selected_work_key == second.work_key
    controller.openSelected_(None)
    assert activated[-1].work_key == second.work_key
    assert activated[-1].kind is OperatorActionKind.OPEN

    controller.focusSearch_(None)
    assert controller.window.firstResponder() in {
        controller.search_field,
        controller.search_field.currentEditor(),
    }

    controller.search_field.setStringValue_("active")
    controller.worker_scope = first.work_key
    assert controller.cancelOperation_(None) == "query-cleared"
    assert controller.cancelOperation_(None) == "worker-scope-exited"
    assert controller.cancelOperation_(None) == "window-closed"


def test_keyboard_command_dispatch_matches_native_shortcuts() -> None:
    first = _document("first")
    second = _document("second")
    activated: list[AgentBrowserActionPayload] = []
    controller = AgentBrowserWindowController.alloc().init()
    controller.action_handler = activated.append
    controller.open_with_projection(
        _projection(first, second),
        actions_by_work_key={
            first.work_key: (_action(OperatorActionKind.OPEN, "Open"),),
            second.work_key: (_action(OperatorActionKind.OPEN, "Open"),),
        },
        show=False,
    )

    assert controller.handle_key_command("down") is True
    assert controller.selected_work_key == second.work_key
    assert controller.handle_key_command("return") is True
    assert activated[-1].work_key == second.work_key
    assert controller.handle_key_command("f", command=True) is True
    assert controller.handle_key_command("x", command=True) is False
    controller.search_field.setStringValue_("codex")
    assert controller.handle_key_command("escape") is True
    assert controller.search_field.stringValue() == ""


def test_keyboard_focus_evidence_records_one_visit_and_root_open_records_none() -> None:
    first = _document("first")
    second = _document("second")
    visits: list[WorkKey] = []
    controller = AgentBrowserWindowController.alloc().init()
    controller.visit_handler = visits.append

    controller.open_with_projection(_projection(first, second), show=False)
    assert visits == []
    controller.moveDown_(None)
    assert visits == [second.work_key]


def test_action_menu_is_shallow_and_uses_exact_typed_payload_allowlist() -> None:
    row = _document("actions", actionable=True)
    controller = AgentBrowserWindowController.alloc().init()
    controller.open_with_projection(
        _projection(row),
        actions_by_work_key={
            row.work_key: (
                _action(OperatorActionKind.OPEN, "Open"),
                _action(OperatorActionKind.WATCH, "Watch"),
                _action(OperatorActionKind.PIN, "Pin"),
                _action(OperatorActionKind.SNOOZE, "Snooze"),
                _action(OperatorActionKind.ACKNOWLEDGE, "I'm on It"),
            )
        },
        show=False,
    )
    controller.select_work_key(row.work_key, notify_visit=False)

    menu = controller.action_menu_for_selected_row()

    assert isinstance(menu, NSMenu)
    assert [menu.itemAtIndex_(index).title() for index in range(menu.numberOfItems())] == [
        "Open",
        "Watch",
        "Pin",
        "Snooze 15 Minutes",
        "Snooze 1 Hour",
        "Snooze Until Tomorrow",
        "I'm on It",
    ]
    for index in range(menu.numberOfItems()):
        item = menu.itemAtIndex_(index)
        assert item.submenu() is None
        assert type(item.representedObject()) is AgentBrowserActionPayload
        assert item.representedObject().work_key == row.work_key
        assert item.representedObject().generation == 7


def test_action_activation_revalidates_generation_and_current_descriptor() -> None:
    row = _document("generation")
    activated: list[AgentBrowserActionPayload] = []
    controller = AgentBrowserWindowController.alloc().init()
    controller.action_handler = activated.append
    controller.open_with_projection(
        _projection(row),
        actions_by_work_key={row.work_key: (_action(OperatorActionKind.WATCH, "Watch"),)},
        show=False,
    )
    controller.select_work_key(row.work_key, notify_visit=False)
    stale_item = controller.action_menu_for_selected_row().itemAtIndex_(0)

    controller.publish_projection(_projection(row, generation=8))
    assert controller.performBrowserAction_(stale_item) is False
    assert activated == []

    current_item = controller.action_menu_for_selected_row().itemAtIndex_(0)
    assert controller.performBrowserAction_(current_item) is True
    assert len(activated) == 1


def test_background_projection_replaces_current_action_descriptors_atomically() -> None:
    row = _document("eligibility")
    controller = AgentBrowserWindowController.alloc().init()
    controller.open_with_projection(
        _projection(row),
        actions_by_work_key={
            row.work_key: (_action(OperatorActionKind.WATCH, "Watch"),)
        },
        show=False,
    )
    controller.select_work_key(row.work_key, notify_visit=False)

    controller.publish_projection(
        _projection(row, generation=8),
        actions_by_work_key={
            row.work_key: (
                _action(
                    OperatorActionKind.WATCH,
                    "Watch",
                    enabled=False,
                ),
            )
        },
    )

    item = controller.action_menu_for_selected_row().itemAtIndex_(0)
    assert item.isEnabled() is False
    assert controller.performBrowserAction_(item) is False
