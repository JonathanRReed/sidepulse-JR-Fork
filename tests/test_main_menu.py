"""The hidden main menu: shortcut routing for an accessory app.

Cmd-C/Cmd-V/Cmd-W/Cmd-Z were dead in every window the app owns because
no main menu was ever installed -- AppKit routes key equivalents through
one even when the accessory policy never renders it (wired 2026-08-26).
"""

from __future__ import annotations

from sidepulse.main_menu import build_main_menu


def _flatten(menu):
    rows = []
    for index in range(menu.numberOfItems()):
        item = menu.itemAtIndex_(index)
        submenu = item.submenu()
        if submenu is not None:
            rows.extend(_flatten(submenu))
        elif not item.isSeparatorItem():
            rows.append((str(item.action() or ""), str(item.keyEquivalent() or "")))
    return rows


def test_main_menu_carries_the_standard_editing_and_window_shortcuts() -> None:
    rows = _flatten(build_main_menu())

    assert ("performClose:", "w") in rows
    assert ("undo:", "z") in rows
    assert ("redo:", "Z") in rows
    assert ("cut:", "x") in rows
    assert ("copy:", "c") in rows
    assert ("paste:", "v") in rows
    assert ("selectAll:", "a") in rows
    assert ("terminate:", "q") in rows


def test_settings_is_reachable_without_the_status_menu() -> None:
    """Command-comma must route to the existing settings action."""
    rows = _flatten(build_main_menu())
    assert ("openSettings:", ",") in rows


def test_every_item_targets_the_responder_chain_not_a_fixed_object() -> None:
    """Nil targets are the point: Cmd-W must close whichever window is
    key, and Cmd-C must reach whichever field has focus."""

    def targets(menu):
        for index in range(menu.numberOfItems()):
            item = menu.itemAtIndex_(index)
            submenu = item.submenu()
            if submenu is not None:
                yield from targets(submenu)
            elif not item.isSeparatorItem():
                yield item.target()

    assert all(target is None for target in targets(build_main_menu()))
