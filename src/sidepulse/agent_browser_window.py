"""Native, injected Agent Browser shell.

The controller renders already-projected, content-safe browser documents and
already-authorized operator action descriptors.  It does not read providers,
files, devices, credentials, or the network, and it never creates authority
from legacy display rows.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSButton,
    NSEventModifierFlagCommand,
    NSMenu,
    NSMenuItem,
    NSScrollView,
    NSSearchField,
    NSTableColumn,
    NSTableView,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSIndexSet, NSObject

from .agent_browser import AgentBrowserProjection
from .mailbox import MailboxSectionKind
from .navigation_policy import OperatorActionDescriptor, OperatorActionKind
from .provider_facts import WorkKey

SNOOZE_PRESETS: Final = (
    ("15-minutes", "Snooze 15 Minutes"),
    ("1-hour", "Snooze 1 Hour"),
    ("tomorrow", "Snooze Until Tomorrow Morning"),
)
_SNOOZE_PRESET_KEYS: Final = frozenset(key for key, _title in SNOOZE_PRESETS)


@dataclass(frozen=True, slots=True)
class AgentBrowserActionPayload:
    work_key: WorkKey
    generation: int
    kind: OperatorActionKind
    snooze_preset: str | None = None

    def __post_init__(self) -> None:
        if not (
            type(self.work_key) is WorkKey
            and type(self.generation) is int
            and self.generation >= 0
            and type(self.kind) is OperatorActionKind
            and (
                self.snooze_preset is None
                or (self.kind is OperatorActionKind.SNOOZE and self.snooze_preset in _SNOOZE_PRESET_KEYS)
            )
        ):
            raise ValueError("invalid agent browser action payload")


@dataclass(frozen=True, slots=True)
class AgentBrowserOpenPayload:
    generation: int
    shelf: MailboxSectionKind | None = None
    family_key: WorkKey | None = None

    def __post_init__(self) -> None:
        if not (
            type(self.generation) is int
            and self.generation >= 0
            and (self.shelf is None or type(self.shelf) is MailboxSectionKind)
            and (self.family_key is None or type(self.family_key) is WorkKey)
            and not (self.shelf is not None and self.family_key is not None)
        ):
            raise ValueError("invalid agent browser open payload")


def build_agent_root_items(
    projection: AgentBrowserProjection,
    *,
    actions_by_work_key: Mapping[WorkKey, tuple[OperatorActionDescriptor, ...]],
    target: object | None,
    shelf: MailboxSectionKind | None = None,
    family_key: WorkKey | None = None,
) -> tuple[NSMenuItem, ...]:
    """Build the injected canonical mailbox prefix for the tracked root."""
    if type(projection) is not AgentBrowserProjection:
        raise ValueError("invalid agent browser projection")
    scope = AgentBrowserOpenPayload(projection.generation, shelf, family_key)
    urgent = tuple(row for row in projection.rows if row.actionable)[:3]
    summary = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        (
            # active_count, not total_count: total is every retained
            # family including completed and idle ones, and printing it
            # as "active" is how this header claimed 24 agents were
            # working while one was.
            f"Agent Mailbox · {projection.active_count} active · "
            f"{sum(row.actionable for row in projection.rows)} need you"
        ),
        None,
        "",
    )
    summary.setEnabled_(False)
    items: list[NSMenuItem] = [summary]
    for row in urgent:
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"{row.safe_family_label} · {row.lifecycle_label}",
            None,
            "",
        )
        submenu = NSMenu.alloc().init()
        submenu.setAutoenablesItems_(False)
        descriptors = actions_by_work_key.get(row.work_key, ())
        if not (
            type(descriptors) is tuple
            and all(type(descriptor) is OperatorActionDescriptor for descriptor in descriptors)
        ):
            raise ValueError("invalid urgent action descriptors")
        for descriptor in descriptors:
            if descriptor.kind is OperatorActionKind.SNOOZE and descriptor.enabled:
                for preset, title in SNOOZE_PRESETS:
                    action = _root_action_item(
                        title=title,
                        target=target,
                        descriptor=descriptor,
                        payload=AgentBrowserActionPayload(
                            row.work_key,
                            projection.generation,
                            descriptor.kind,
                            preset,
                        ),
                    )
                    submenu.addItem_(action)
                continue
            action = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                descriptor.title,
                "performBrowserAction:",
                descriptor.key_equivalent,
            )
            action.setTarget_(target)
            action.setEnabled_(descriptor.enabled)
            action.setRepresentedObject_(
                AgentBrowserActionPayload(
                    row.work_key,
                    projection.generation,
                    descriptor.kind,
                )
            )
            if descriptor.disabled_reason:
                action.setToolTip_(descriptor.disabled_reason)
            submenu.addItem_(action)
        item.setSubmenu_(submenu)
        items.append(item)
    overflow_count = sum(row.actionable for row in projection.rows) - len(urgent)
    if overflow_count > 0:
        overflow = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"{overflow_count} more…",
            "openAgentBrowser:",
            "",
        )
        overflow.setTarget_(target)
        overflow.setRepresentedObject_(
            AgentBrowserOpenPayload(
                projection.generation,
                shelf=(shelf or MailboxSectionKind.NEEDS_YOU)
                if family_key is None
                else None,
                family_key=family_key,
            )
        )
        overflow.setEnabled_(True)
        items.append(overflow)
    browser = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Open Agent Browser…",
        "openAgentBrowser:",
        "",
    )
    browser.setTarget_(target)
    browser.setRepresentedObject_(scope)
    browser.setEnabled_(True)
    items.append(browser)
    return tuple(items)


def _root_action_item(
    *,
    title: str,
    target: object | None,
    descriptor: OperatorActionDescriptor,
    payload: AgentBrowserActionPayload,
) -> NSMenuItem:
    action = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title,
        "performBrowserAction:",
        descriptor.key_equivalent,
    )
    action.setTarget_(target)
    action.setEnabled_(descriptor.enabled)
    action.setRepresentedObject_(payload)
    if descriptor.disabled_reason:
        action.setToolTip_(descriptor.disabled_reason)
    return action


def _button(title: str, target: object, action: str) -> NSButton:
    button = NSButton.alloc().initWithFrame_(((0.0, 0.0), (96.0, 30.0)))
    button.setTitle_(title)
    button.setBezelStyle_(NSBezelStyleRounded)
    button.setTarget_(target)
    button.setAction_(action)
    return button


# The window's key vocabulary, mapped from AppKit function-key characters
# to handle_key_command's names. Arrow keys are listed for completeness but
# rarely reach the window: a focused table consumes them natively.
_KEY_NAMES = {
    "\uf700": "up",
    "\uf701": "down",
    "\r": "return",
    "\x03": "enter",
    "\x1b": "escape",
}


class _AgentBrowserWindow(NSWindow):
    """Routes bare keys to the controller's bounded command vocabulary.

    handle_key_command existed, tested, with no keyDown_ ever calling it
    (wired 2026-08-26): Return-to-open, Escape-to-close, and Cmd-F all
    read as designed keyboard flow in the tests while the shipped window
    only ever beeped.
    """

    def keyDown_(self, event):
        controller = self.delegate()
        characters = str(event.charactersIgnoringModifiers() or "")
        key = _KEY_NAMES.get(characters[:1])
        if (
            controller is not None
            and key is not None
            and not event.modifierFlags() & NSEventModifierFlagCommand
            and controller.handle_key_command(key)
        ):
            return
        objc.super(_AgentBrowserWindow, self).keyDown_(event)

    def performKeyEquivalent_(self, event):
        controller = self.delegate()
        characters = str(event.charactersIgnoringModifiers() or "")
        if (
            controller is not None
            and event.modifierFlags() & NSEventModifierFlagCommand
            and controller.handle_key_command(characters, command=True)
        ):
            return True
        return objc.super(_AgentBrowserWindow, self).performKeyEquivalent_(event)


class AgentBrowserWindowController(NSObject):
    """One reusable ordinary window backed only by injected pure documents."""

    def init(self):
        self = objc.super(AgentBrowserWindowController, self).init()
        if self is None:
            return None

        self.projection = AgentBrowserProjection(0, (), 0, 0, 0, None)
        self.actions_by_work_key: dict[WorkKey, tuple[OperatorActionDescriptor, ...]] = {}
        self.action_handler: Callable[[AgentBrowserActionPayload], object] | None = None
        self.query_handler: Callable[..., AgentBrowserProjection] | None = None
        self.visit_handler: Callable[[WorkKey], object] | None = None
        self.worker_scope: WorkKey | None = None
        self.shelf_scope: MailboxSectionKind | None = None
        self.last_selected_work_key: WorkKey | None = None
        self._selected_work_key: WorkKey | None = None
        self._suppress_visit = False
        self._search_selection_range = (0, 0)
        self._build_window()
        return self

    def _build_window(self) -> None:
        width, height = 720.0, 520.0
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        self.window = _AgentBrowserWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0.0, 0.0), (width, height)),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("Agent Browser")
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)

        root = NSView.alloc().initWithFrame_(((0.0, 0.0), (width, height)))
        self.window.setContentView_(root)

        self.search_field = NSSearchField.alloc().initWithFrame_(((20.0, height - 50.0), (width - 40.0, 28.0)))
        self.search_field.setPlaceholderString_("Search agents")
        self.search_field.setDelegate_(self)
        root.addSubview_(self.search_field)

        self.table_view = NSTableView.alloc().init()
        self.table_view.setHeaderView_(None)
        self.table_view.setRowHeight_(30.0)
        column = NSTableColumn.alloc().initWithIdentifier_("agent")
        column.setWidth_(width - 42.0)
        self.table_view.addTableColumn_(column)
        self.table_view.setDataSource_(self)
        self.table_view.setDelegate_(self)
        self.table_view.setTarget_(self)
        self.table_view.setDoubleAction_("expandSelectedFamily:")

        self.scroll_view = NSScrollView.alloc().initWithFrame_(((20.0, 62.0), (width - 40.0, height - 122.0)))
        self.scroll_view.setDocumentView_(self.table_view)
        self.scroll_view.setHasVerticalScroller_(True)
        root.addSubview_(self.scroll_view)

        self.open_button = _button("Open", self, "openSelected:")
        self.open_button.setFrameOrigin_((20.0, 18.0))
        root.addSubview_(self.open_button)
        self.actions_button = _button("Actions", self, "showActions:")
        self.actions_button.setFrameOrigin_((124.0, 18.0))
        root.addSubview_(self.actions_button)
        self.workers_button = _button("Workers", self, "expandSelectedFamily:")
        self.workers_button.setFrameOrigin_((228.0, 18.0))
        root.addSubview_(self.workers_button)
        self.error_label = NSTextField.labelWithString_("")
        self.error_label.setFrame_(((332.0, 23.0), (250.0, 20.0)))
        self.error_label.setLineBreakMode_(4)
        root.addSubview_(self.error_label)
        self.close_button = _button("Close", self, "closeBrowser:")
        self.close_button.setFrameOrigin_((width - 116.0, 18.0))
        root.addSubview_(self.close_button)

    @property
    def selected_work_key(self) -> WorkKey | None:
        return self._selected_work_key

    def open_with_projection(
        self,
        projection: AgentBrowserProjection,
        *,
        actions_by_work_key: Mapping[WorkKey, tuple[OperatorActionDescriptor, ...]] | None = None,
        error_message: str | None = None,
        show: bool = True,
    ) -> NSWindow:
        self._set_actions(actions_by_work_key or {})
        self.projection = self._validated_projection(projection)
        self.set_operator_error(error_message)
        preferred = self.last_selected_work_key
        if preferred is None or not self._contains_work_key(preferred):
            preferred = self.projection.selected_work_key
        if preferred is None and self.projection.rows:
            preferred = self.projection.rows[0].work_key
        self.table_view.reloadData()
        self.select_work_key(preferred, notify_visit=False)
        self.focusSearch_(None)
        if show:
            from .window_presentation import present_window

            present_window(self.window)
        return self.window

    def publish_projection(
        self,
        projection: AgentBrowserProjection,
        *,
        actions_by_work_key: Mapping[
            WorkKey, tuple[OperatorActionDescriptor, ...]
        ]
        | None = None,
        error_message: str | None = None,
    ) -> None:
        """Replace rows without disturbing window, focus, edit, or scroll state."""
        projection = self._validated_projection(projection)
        if actions_by_work_key is not None:
            self._set_actions(actions_by_work_key)
        self.set_operator_error(error_message)
        was_key = bool(self.window.isKeyWindow())
        first_responder = self.window.firstResponder()
        selected = self.selected_work_key
        search_range = self._current_search_range()
        scroll_origin = self.scroll_view.contentView().bounds().origin

        self.projection = projection
        self.table_view.reloadData()
        self.select_work_key(selected, notify_visit=False)
        self.scroll_view.contentView().scrollToPoint_(scroll_origin)
        self.scroll_view.reflectScrolledClipView_(self.scroll_view.contentView())
        if first_responder is not None:
            self.window.makeFirstResponder_(first_responder)
        self._restore_search_range(search_range)
        if was_key and not self.window.isKeyWindow():
            self.window.makeKeyWindow()

    def set_operator_error(self, message: str | None) -> None:
        safe = message if type(message) is str and len(message) <= 256 else ""
        self.error_label.setStringValue_(safe)
        self.error_label.setToolTip_(safe or None)

    def _validated_projection(
        self,
        projection: AgentBrowserProjection,
    ) -> AgentBrowserProjection:
        if type(projection) is not AgentBrowserProjection:
            raise ValueError("invalid browser projection")
        return projection

    def _set_actions(
        self,
        actions_by_work_key: Mapping[WorkKey, tuple[OperatorActionDescriptor, ...]],
    ) -> None:
        validated: dict[WorkKey, tuple[OperatorActionDescriptor, ...]] = {}
        for work_key, descriptors in actions_by_work_key.items():
            if not (
                type(work_key) is WorkKey
                and type(descriptors) is tuple
                and all(type(item) is OperatorActionDescriptor for item in descriptors)
            ):
                raise ValueError("invalid browser action descriptors")
            kinds = tuple(item.kind for item in descriptors)
            if len(kinds) != len(set(kinds)):
                raise ValueError("duplicate browser action kind")
            validated[work_key] = descriptors
        self.actions_by_work_key = validated

    def _contains_work_key(self, work_key: WorkKey) -> bool:
        return any(row.work_key == work_key for row in self.projection.rows)

    def select_work_key(
        self,
        work_key: WorkKey | None,
        *,
        notify_visit: bool,
    ) -> None:
        row_index = next(
            (index for index, row in enumerate(self.projection.rows) if row.work_key == work_key),
            -1,
        )
        # AppKit synchronously emits selectionDidChange for the programmatic
        # selection below. Suppress that delegate copy and publish the one
        # deliberate focus-evidence edge ourselves.
        self._suppress_visit = True
        try:
            if row_index < 0:
                self.table_view.deselectAll_(None)
                self._selected_work_key = None
            else:
                self.table_view.selectRowIndexes_byExtendingSelection_(
                    NSIndexSet.indexSetWithIndex_(row_index),
                    False,
                )
                self.table_view.scrollRowToVisible_(row_index)
                self._selected_work_key = self.projection.rows[row_index].work_key
                self.last_selected_work_key = self._selected_work_key
                if notify_visit and self.visit_handler is not None:
                    self.visit_handler(self._selected_work_key)
        finally:
            self._suppress_visit = False
        self._update_button_state()

    def _update_button_state(self) -> None:
        actions = self.actions_by_work_key.get(self.selected_work_key, ())
        selected = next(
            (
                row
                for row in self.projection.rows
                if row.work_key == self.selected_work_key
            ),
            None,
        )
        can_open = any(descriptor.kind is OperatorActionKind.OPEN and descriptor.enabled for descriptor in actions)
        self.open_button.setEnabled_(can_open)
        self.actions_button.setEnabled_(bool(actions))
        self.workers_button.setEnabled_(
            selected is not None and selected.worker_count > 0
        )

    def numberOfRowsInTableView_(self, _table_view) -> int:
        return len(self.projection.rows)

    def tableView_objectValueForTableColumn_row_(self, _table_view, _column, row):
        document = self.projection.rows[row]
        suffix = f" · {document.lifecycle_label}"
        if document.worker_count:
            suffix += f" · {document.worker_count} workers"
        return f"{document.provider_label.text} · {document.safe_family_label}{suffix}"

    def tableViewSelectionDidChange_(self, _notification) -> None:
        index = self.table_view.selectedRow()
        if index < 0 or index >= len(self.projection.rows):
            self._selected_work_key = None
        else:
            self._selected_work_key = self.projection.rows[index].work_key
            self.last_selected_work_key = self._selected_work_key
            if not self._suppress_visit and self.visit_handler is not None:
                self.visit_handler(self._selected_work_key)
        self._update_button_state()

    def controlTextDidChange_(self, _notification) -> None:
        if self.query_handler is None:
            return
        projected = self.query_handler(
            str(self.search_field.stringValue()),
            family_key=self.worker_scope,
            selected_work_key=self.selected_work_key,
        )
        self.publish_projection(projected)

    def _current_search_range(self) -> tuple[int, int]:
        editor = self.search_field.currentEditor()
        if editor is not None:
            value = tuple(editor.selectedRange())
            self._search_selection_range = (int(value[0]), int(value[1]))
        return self._search_selection_range

    def _restore_search_range(self, value: tuple[int, int]) -> None:
        self._search_selection_range = value
        editor = self.search_field.currentEditor()
        if editor is not None:
            editor.setSelectedRange_(value)

    @objc.IBAction
    def moveDown_(self, _sender) -> None:
        self._move_selection(1)

    @objc.IBAction
    def moveUp_(self, _sender) -> None:
        self._move_selection(-1)

    def _move_selection(self, delta: int) -> None:
        if not self.projection.rows:
            return
        current = self.table_view.selectedRow()
        if current < 0:
            target = 0 if delta > 0 else len(self.projection.rows) - 1
        else:
            target = max(0, min(len(self.projection.rows) - 1, current + delta))
        self.select_work_key(
            self.projection.rows[target].work_key,
            notify_visit=True,
        )

    def handle_key_command(self, key: str, *, command: bool = False) -> bool:
        """Dispatch the browser's bounded keyboard command vocabulary."""
        normalized = key.casefold() if type(key) is str else ""
        if command:
            if normalized != "f":
                return False
            self.focusSearch_(None)
            return True
        if normalized == "down":
            self.moveDown_(None)
            return True
        if normalized == "up":
            self.moveUp_(None)
            return True
        if normalized in {"return", "enter"}:
            return self.openSelected_(None)
        if normalized == "escape":
            self.cancelOperation_(None)
            return True
        return False

    @objc.IBAction
    def performFindPanelAction_(self, _sender) -> None:
        self.focusSearch_(None)

    @objc.IBAction
    def focusSearch_(self, _sender) -> None:
        self.window.makeFirstResponder_(self.search_field)
        self._restore_search_range(self._search_selection_range)

    @objc.IBAction
    def openSelected_(self, _sender) -> bool:
        descriptor = next(
            (
                item
                for item in self.actions_by_work_key.get(
                    self.selected_work_key,
                    (),
                )
                if item.kind is OperatorActionKind.OPEN and item.enabled
            ),
            None,
        )
        if descriptor is None or self.selected_work_key is None:
            return False
        payload = AgentBrowserActionPayload(
            self.selected_work_key,
            self.projection.generation,
            OperatorActionKind.OPEN,
        )
        return self._dispatch_payload(payload)

    @objc.IBAction
    def expandSelectedFamily_(self, _sender) -> bool:
        selected = next(
            (
                row
                for row in self.projection.rows
                if row.work_key == self.selected_work_key
                and row.worker_count > 0
            ),
            None,
        )
        if selected is None or self.query_handler is None:
            return False
        self.worker_scope = selected.work_key
        projected = self.query_handler(
            str(self.search_field.stringValue()),
            family_key=self.worker_scope,
            selected_work_key=None,
        )
        self.publish_projection(projected)
        return True

    def action_menu_for_selected_row(self) -> NSMenu:
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        work_key = self.selected_work_key
        if work_key is None:
            return menu
        for descriptor in self.actions_by_work_key.get(work_key, ()):
            if descriptor.kind is OperatorActionKind.SNOOZE and descriptor.enabled:
                for preset, title in SNOOZE_PRESETS:
                    self._append_action_item(
                        menu,
                        title=title,
                        descriptor=descriptor,
                        work_key=work_key,
                        snooze_preset=preset,
                    )
                continue
            self._append_action_item(
                menu,
                title=descriptor.title,
                descriptor=descriptor,
                work_key=work_key,
                snooze_preset=None,
            )
        return menu

    def _append_action_item(
        self,
        menu: NSMenu,
        *,
        title: str,
        descriptor: OperatorActionDescriptor,
        work_key: WorkKey,
        snooze_preset: str | None,
    ) -> None:
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title,
            "performBrowserAction:",
            descriptor.key_equivalent,
        )
        item.setTarget_(self)
        item.setEnabled_(descriptor.enabled)
        item.setRepresentedObject_(
            AgentBrowserActionPayload(
                work_key,
                self.projection.generation,
                descriptor.kind,
                snooze_preset,
            )
        )
        if descriptor.disabled_reason:
            item.setToolTip_(descriptor.disabled_reason)
        menu.addItem_(item)

    @objc.IBAction
    def showActions_(self, _sender) -> NSMenu:
        menu = self.action_menu_for_selected_row()
        if menu.numberOfItems() and self.actions_button.window() is not None:
            menu.popUpMenuPositioningItem_atLocation_inView_(
                None,
                (0.0, self.actions_button.bounds().size.height),
                self.actions_button,
            )
        return menu

    @objc.IBAction
    def performBrowserAction_(self, sender) -> bool:
        payload = sender.representedObject()
        if type(payload) is not AgentBrowserActionPayload:
            return False
        return self._dispatch_payload(payload)

    def _dispatch_payload(self, payload: AgentBrowserActionPayload) -> bool:
        if payload.generation != self.projection.generation or not self._contains_work_key(payload.work_key):
            return False
        descriptor = next(
            (
                item
                for item in self.actions_by_work_key.get(payload.work_key, ())
                if item.kind is payload.kind and item.enabled
            ),
            None,
        )
        if descriptor is None:
            return False
        if payload.kind is OperatorActionKind.SNOOZE:
            if payload.snooze_preset not in _SNOOZE_PRESET_KEYS:
                return False
        elif payload.snooze_preset is not None:
            return False
        if self.visit_handler is not None:
            self.visit_handler(payload.work_key)
        if self.action_handler is not None:
            self.action_handler(payload)
        return True

    def cancelOperation_(self, _sender) -> str:
        if self.search_field.stringValue():
            self.search_field.setStringValue_("")
            self.controlTextDidChange_(None)
            return "query-cleared"
        if self.worker_scope is not None:
            self.worker_scope = None
            self.controlTextDidChange_(None)
            return "worker-scope-exited"
        self.closeBrowser_(None)
        return "window-closed"

    @objc.IBAction
    def closeBrowser_(self, _sender) -> None:
        self.window.orderOut_(None)
        self.windowWillClose_(None)

    def windowWillClose_(self, _notification) -> None:
        self.last_selected_work_key = self.selected_work_key
        self.search_field.setStringValue_("")
        self._search_selection_range = (0, 0)
        self.worker_scope = None
        self.shelf_scope = None
