"""The hidden main menu: dead keyboard shortcuts come back to life.

An accessory-policy (menu-bar) app never SHOWS a main menu, but AppKit
still routes key equivalents through one -- and this app never installed
one, so Cmd-C/Cmd-V could not touch a settings text field, Cmd-W closed
nothing, and Cmd-Z undid nothing, in every window the app owns (wired
2026-08-26). Every item targets nil so the responder chain decides, which
is exactly the native behavior: Cmd-W closes whichever window is key.
"""

from __future__ import annotations

from AppKit import NSApp, NSMenu, NSMenuItem


def _item(title: str, action: str, key: str) -> NSMenuItem:
    return NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)


def build_main_menu() -> NSMenu:
    main = NSMenu.alloc().init()

    # The first slot is the app menu by AppKit contract; it never renders
    # for an accessory app, it only anchors key routing.
    app_slot = NSMenuItem.alloc().init()
    app_menu = NSMenu.alloc().init()
    app_menu.addItem_(_item("Quit SidePulse", "terminate:", "q"))
    app_slot.setSubmenu_(app_menu)
    main.addItem_(app_slot)

    file_slot = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("File", None, "")
    file_menu = NSMenu.alloc().initWithTitle_("File")
    file_menu.addItem_(_item("Close Window", "performClose:", "w"))
    file_slot.setSubmenu_(file_menu)
    main.addItem_(file_slot)

    edit_slot = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Edit", None, "")
    edit_menu = NSMenu.alloc().initWithTitle_("Edit")
    edit_menu.addItem_(_item("Undo", "undo:", "z"))
    edit_menu.addItem_(_item("Redo", "redo:", "Z"))
    edit_menu.addItem_(NSMenuItem.separatorItem())
    edit_menu.addItem_(_item("Cut", "cut:", "x"))
    edit_menu.addItem_(_item("Copy", "copy:", "c"))
    edit_menu.addItem_(_item("Paste", "paste:", "v"))
    edit_menu.addItem_(_item("Select All", "selectAll:", "a"))
    edit_slot.setSubmenu_(edit_menu)
    main.addItem_(edit_slot)
    return main


def install_main_menu() -> None:
    NSApp.setMainMenu_(build_main_menu())
