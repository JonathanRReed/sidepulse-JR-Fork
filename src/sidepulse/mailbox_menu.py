"""The dropdown's Agent Mailbox item, extracted whole from the monolith.

Extracted 2026-08-26 to honor the legacy-can-only-shrink ratchet while
the audit-fix wave added its (small, unavoidable) claims and re-sync
glue there. The body is verbatim; every monolith global it used arrives
through the function-level import below -- the same cycle-dodging
pattern setup_window.py and the architecture doc bless.
"""

from __future__ import annotations


def build_agent_mailbox_menu_item(snapshot, target):
    from AppKit import NSMenu, NSMenuItem

    from .status_bar_legacy import (
        _MAILBOX_MAX_WORKERS_PER_ROLLUP,
        _MAILBOX_SECTION_TITLES,
        AttentionProjection,
        MailboxSectionKind,
        _add_mailbox_empty_teaching,
        _mailbox_display_status,
        _mailbox_row_suffix,
        _mailbox_source_rows,
        _mailbox_workers_by_parent,
        build_session_menu_item,
        build_worker_rollup_item,
        colors_module,
        disabled_menu_item,
        mailbox_projection_for_menu,
        project_attention,
        provider_spec,
    )

    mailbox = mailbox_projection_for_menu(snapshot, target)
    attention = getattr(target, "current_attention_projection", None)
    if not isinstance(attention, AttentionProjection):
        attention = project_attention(snapshot, target.settings)
    sources = _mailbox_source_rows(attention)
    workers_by_parent, orphan_workers = _mailbox_workers_by_parent(sources)
    display_sources = [status for status in sources.values() if not status.is_subagent]
    identity: dict[str, str] = {}
    if len(display_sources) > 1:
        menu_colors = getattr(getattr(target, "settings", None), "colors", None)
        # Brand-anchored, like the LED/Screen Bar paths (audit V3): the
        # dropdown's dots were the last surface still hashing agent ids
        # into the abstract palette -- "it's purple for some reason when
        # Claude's running", in the menu the owner looks at most.
        if menu_colors is not None:
            identity = colors_module.provider_identity_colors_for_agents(
                [(status.agent_id, status.provider) for status in display_sources],
                colors=menu_colors,
            )
        else:
            identity = colors_module.identity_colors_for_agents(
                [status.agent_id for status in display_sources],
                groups=colors_module.identity_groups_for_statuses(
                    display_sources, menu_colors
                ),
            )

    summary = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        (
            f"Agent Mailbox · {mailbox.active_count} active · "
            f"{mailbox.needs_you_count} need you · {mailbox.ready_count} ready"
        ),
        None,
        "",
    )
    mailbox_menu = NSMenu.alloc().init()
    mailbox_menu.setAutoenablesItems_(False)
    has_rows = any(section.rows for section in mailbox.sections)
    for section in mailbox.sections:
        shelf_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            _MAILBOX_SECTION_TITLES[section.kind], None, ""
        )
        shelf_menu = NSMenu.alloc().init()
        shelf_menu.setAutoenablesItems_(False)
        if not section.rows:
            if not has_rows and section.kind == MailboxSectionKind.RECENT:
                _add_mailbox_empty_teaching(shelf_menu, target)
        provider_order: list[str] = []
        for row in section.rows:
            if row.provider not in provider_order:
                provider_order.append(row.provider)
        show_provider_headers = len(provider_order) > 1
        rows = (
            tuple(
                row
                for provider in provider_order
                for row in section.rows
                if row.provider == provider
            )
            if show_provider_headers
            else section.rows
        )
        last_provider_header = None
        for row in rows:
            if show_provider_headers and row.provider != last_provider_header:
                last_provider_header = row.provider
                try:
                    provider_title = provider_spec(row.provider).label
                except ValueError:
                    provider_title = row.provider.title()
                shelf_menu.addItem_(disabled_menu_item(provider_title))
            display_source = sources.get(row.agent_id)
            navigation_source = sources.get(row.navigation_agent_id or "")
            dot_color = None
            if display_source is None:
                display_source = navigation_source
            if navigation_source is None:
                navigation_source = display_source
            if display_source is None or navigation_source is None:
                synthetic = disabled_menu_item(
                    f"{row.display_name}{_mailbox_row_suffix(row)}"
                )
                shelf_menu.addItem_(synthetic)
            else:
                rendered = _mailbox_display_status(
                    row, display_source, navigation_source
                )
                if getattr(target, "settings", None) is not None:
                    dot_color = target.settings.colors.session_color(row.agent_id)
                row_item = build_session_menu_item(
                    rendered,
                    snapshot.collected_at,
                    target,
                    identity_color=dot_color or identity.get(row.agent_id),
                    title_suffix=_mailbox_row_suffix(row),
                )
                row_item.setRepresentedObject_(navigation_source)
                shelf_menu.addItem_(row_item)
            children = (
                orphan_workers
                if row.agent_id == "sidepulse:mailbox:background-agents"
                else workers_by_parent.get(row.agent_id, [])
            )
            if children:
                shelf_menu.addItem_(
                    build_worker_rollup_item(
                        children,
                        snapshot.collected_at,
                        target,
                        dot_color or identity.get(row.agent_id),
                        max_visible=_MAILBOX_MAX_WORKERS_PER_ROLLUP,
                    )
                )
        if section.overflow_count:
            shelf_menu.addItem_(disabled_menu_item(f"{section.overflow_count} more"))
        shelf_item.setSubmenu_(shelf_menu)
        mailbox_menu.addItem_(shelf_item)
    summary.setSubmenu_(mailbox_menu)
    return summary
