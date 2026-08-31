"""The dropdown's Activity Ledger item, extracted from the monolith
for ratchet headroom (2026-08-26). Body verbatim; globals arrive via
the function-level legacy import, the blessed cycle-dodge."""

from __future__ import annotations


def build_activity_ledger_menu_item(snapshot, target):
    from AppKit import NSMenu, NSMenuItem

    from .away_summary import AwaySummaryPolicy, AwaySummaryValidationError, project_away_summary
    from .status_bar_legacy import (
        MAX_ACTIVITY_MENU_ROWS,
        ActivityLedger,
        _activity_boundary_text,
        _activity_row_item,
        _activity_statuses_by_agent,
        disabled_menu_item,
        time,
    )

    # `callable(getattr(...))`, the way this menu already treats
    # `active_focus_summary`: several tests build the dropdown against a
    # stand-in target, and a section that answers "what did I miss" must
    # never be the reason the whole menu fails to build.
    restore = getattr(target, "ensure_activity_ledger", None)
    if not callable(restore):
        return None
    ledger = restore()
    if type(ledger) is not ActivityLedger or not ledger.entries:
        return None
    now_epoch = time.time()
    settings = getattr(target, "settings", None)
    retention_days = getattr(settings, "operator_history_retention_days", 0)
    away_projection = None
    if retention_days in {1, 7, 30, 90}:
        store = getattr(target, "operator_history_store", None)
        try:
            away_projection = project_away_summary(
                ledger,
                getattr(store, "state", ()),
                AwaySummaryPolicy(True, retention_days),
                now_epoch,
            )
        except AwaySummaryValidationError:
            away_projection = None
        else:
            retained = tuple(
                entry
                for entry in ledger.entries
                if entry.occurred_at_epoch >= away_projection.retention_cutoff_epoch
            )
            unseen = tuple(
                entry
                for entry in retained
                if entry.occurred_at_epoch > away_projection.live_unread_watermark
            )
            seen = tuple(entry for entry in retained if entry not in unseen)
            title = (
                f"Since you were away · {away_projection.live_unread_count}"
                if away_projection.has_unseen
                else "Since you were away"
            )
    if away_projection is None:
        unseen = ledger.unseen
        seen = tuple(entry for entry in ledger.entries if entry not in unseen)
        retained = ledger.entries
        title = f"Since you left · {len(unseen)}" if unseen else "Recent activity"
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
    submenu = NSMenu.alloc().init()
    submenu.setAutoenablesItems_(False)
    if away_projection is not None:
        for sentence in away_projection.summary_sentences:
            submenu.addItem_(disabled_menu_item(sentence))
        submenu.addItem_(NSMenuItem.separatorItem())
    submenu.addItem_(disabled_menu_item(_activity_boundary_text(ledger, now_epoch)))
    submenu.addItem_(NSMenuItem.separatorItem())

    statuses_by_agent = _activity_statuses_by_agent(snapshot)
    visible_unseen = unseen[:MAX_ACTIVITY_MENU_ROWS]
    for entry in visible_unseen:
        submenu.addItem_(
            _activity_row_item(entry, now_epoch, statuses_by_agent, target)
        )
    remaining = MAX_ACTIVITY_MENU_ROWS - len(visible_unseen)
    visible_seen = seen[:remaining] if remaining > 0 else ()
    if visible_seen:
        if visible_unseen:
            submenu.addItem_(NSMenuItem.separatorItem())
            submenu.addItem_(disabled_menu_item("Earlier"))
        for entry in visible_seen:
            submenu.addItem_(
                _activity_row_item(entry, now_epoch, statuses_by_agent, target)
            )
    hidden = len(retained) - len(visible_unseen) - len(visible_seen)
    if hidden > 0:
        submenu.addItem_(disabled_menu_item(f"{hidden} more"))
    item.setSubmenu_(submenu)
    return item
