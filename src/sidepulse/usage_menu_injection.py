"""Menu-surgery helpers for the JR usage row's injection into the dropdown.

Extracted verbatim from provider_usage_status_bar (2026-08-26) for its
facade size ratchet. These are pure NSMenu manipulations: find the anchor
row, remove a leftover legacy usage card, and tidy separators after the
insert. The facade's build_menu wrapper is their only caller.
"""

from __future__ import annotations


def menu_index(menu, title_prefix: str) -> int:
    for position in range(menu.numberOfItems()):
        if str(menu.itemAtIndex_(position).title() or "").startswith(title_prefix):
            return position
    return -1


def remove_legacy_usage_item(menu, target) -> None:
    """Remove a stale legacy usage card if one is present in this menu.

    Mostly a no-op since the legacy build stopped constructing the card
    when the JR plane owns the row (jr_plane_owns_usage_menu_item); this
    still catches an item carried over from an older build of the menu.
    """
    item = getattr(target, "_usage_menu_item", None)
    if item is None:
        return
    try:
        index = menu.indexOfItem_(item)
    except Exception:
        index = -1
    if index >= 0:
        menu.removeItemAtIndex_(index)
    else:
        # The compact facade may have grouped the legacy view under its own
        # "Usage · …" parent row; removing only the nested item would leave
        # a second, empty usage row behind. Remove the whole parent.
        for parent_index in range(menu.numberOfItems()):
            parent = menu.itemAtIndex_(parent_index)
            submenu = parent.submenu()
            if submenu is None:
                continue
            try:
                nested = submenu.indexOfItem_(item)
            except Exception:
                nested = -1
            if nested >= 0:
                menu.removeItemAtIndex_(parent_index)
                break
    target._usage_menu_item = None
    target._usage_menu_view = None


def remove_redundant_separators(menu) -> None:
    index = menu.numberOfItems() - 1
    while index >= 0:
        item = menu.itemAtIndex_(index)
        previous = menu.itemAtIndex_(index - 1) if index > 0 else None
        if item.isSeparatorItem() and (
            index == 0
            or index == menu.numberOfItems() - 1
            or (previous is not None and previous.isSeparatorItem())
        ):
            menu.removeItemAtIndex_(index)
        index -= 1


__all__ = ["menu_index", "remove_legacy_usage_item", "remove_redundant_separators"]
