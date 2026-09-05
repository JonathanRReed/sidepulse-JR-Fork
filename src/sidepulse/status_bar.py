"""Public facade for SidePulse's single production AppKit controller.

All controller behavior lives in ``_status_bar_production``. This module keeps
legacy imports, monkeypatches, direct module execution, and source
introspection compatible without defining or rebinding another Objective-C
subclass. Small module-level adapters provide stable device identity and a
compact menu without adding business logic to the retained controller. The
adapters are installed only by ``sidepulse.application_composition``.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace as dataclass_replace
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

from . import _status_bar_production as _production
from .device_identity import DeviceKind, device_kind, normalize_device_label
from .device_inventory import DeviceIdentityCache
from .dnd_policy import DndMode, DndOverride, DndProjection, DndSource
from .menu_projection import (
    MenuProjectionInputs,
    _glance_title,
    project_dnd_submenu,
    project_root_menu,
)

_legacy = _production._legacy
JRStatusBarController = _production.JRStatusBarController
StatusBarController = JRStatusBarController

# Keep immutable references on the retained runtime module. importlib.reload()
# reuses this module object while the runtime already points at our wrappers;
# without these sentinels a reload would wrap a wrapper and recurse.
_ORIGINAL_DEVICE_ID_FOR_ROOT = getattr(
    _legacy,
    "_sidepulse_original_device_id_for_root",
    _legacy.device_id_for_root,
)
_ORIGINAL_PERSISTABLE_DEVICE_IDENTITY = getattr(
    _legacy,
    "_sidepulse_original_persistable_device_identity",
    _legacy.persistable_device_identity,
)
_ORIGINAL_BUILD_MENU = getattr(
    _legacy, "_sidepulse_original_build_menu", _legacy.build_menu
)
_DEVICE_IDENTITIES = None
_FACADE_INSTALLED = False
_LAST_DEVICE_REFRESH_REQUEST = float(
    getattr(_legacy, "_sidepulse_last_device_refresh_request", 0.0) or 0.0
)


def _device_identity_cache() -> DeviceIdentityCache:
    global _DEVICE_IDENTITIES
    if type(_DEVICE_IDENTITIES) is DeviceIdentityCache:
        return _DEVICE_IDENTITIES
    retained = getattr(_legacy, "_sidepulse_device_identity_cache", None)
    _DEVICE_IDENTITIES = (
        retained if type(retained) is DeviceIdentityCache else DeviceIdentityCache()
    )
    return _DEVICE_IDENTITIES


def _request_device_identity_refresh(now: float | None = None) -> None:
    global _LAST_DEVICE_REFRESH_REQUEST
    reference = time.monotonic() if now is None else float(now)
    if reference - _LAST_DEVICE_REFRESH_REQUEST < 15.0:
        return
    _LAST_DEVICE_REFRESH_REQUEST = reference
    _legacy._sidepulse_last_device_refresh_request = reference
    _device_identity_cache().request_refresh()


def device_id_for_root(root: Path) -> str:
    """Return the stable cached hardware key without blocking AppKit."""
    _request_device_identity_refresh()
    identity = _device_identity_cache().identity_for_mount(Path(root))
    return identity.key if identity is not None else _ORIGINAL_DEVICE_ID_FOR_ROOT(root)


def persistable_device_identity(device_id: str, path: str) -> bool:
    """Reject path ghosts once stable inventory owns that physical device."""
    if device_id == _legacy.VIRTUAL_DEVICE_ID or path == _legacy.VIRTUAL_DEVICE_ID:
        return True
    snapshot = _device_identity_cache().snapshot()
    if isinstance(device_id, str) and device_id.startswith("sidepulse:"):
        # A stable-keyed entry whose mount is now owned by a DIFFERENT
        # stable key is a ghost of a re-keyed device (e.g. a Pro that
        # was remembered as a Dot before STATUS.TXT serials corrected
        # the classification) -- keep the live key, drop the ghost.
        return not any(
            identity.mount_path == path and identity.key != device_id
            for identity in snapshot
        )
    if not snapshot:
        return _ORIGINAL_PERSISTABLE_DEVICE_IDENTITY(device_id, path)
    if any(identity.mount_path == path for identity in snapshot):
        return False
    kind = device_kind(Path(path).name, path)
    if kind is not DeviceKind.UNKNOWN and any(
        identity.kind is kind for identity in snapshot
    ):
        return False
    return _ORIGINAL_PERSISTABLE_DEVICE_IDENTITY(device_id, path)


def _safe_title(item) -> str:
    try:
        return str(item.title() or "")
    except Exception:
        return ""


def _menu_items(menu) -> list:
    try:
        return list(menu.itemArray())
    except Exception:
        return []


def _remove_item(menu, item) -> None:
    try:
        menu.removeItem_(item)
    except Exception:
        pass


def _connected_device_count(target) -> int:
    if not getattr(target, "_runtime_started", False):
        return 0
    try:
        devices = target.status_bar_devices(remember=False)
    except Exception:
        return 0
    return sum(
        bool(device.connected)
        for device in devices
        if device.device_id != _legacy.VIRTUAL_DEVICE_ID
    )


def _usage_summary(target) -> str | None:
    labels = getattr(target, "_usage_menu_labels", {}) or {}
    values: list[str] = []
    for provider in ("claude", "codex", "cursor", "grok", "devin", "antigravity"):
        label = labels.get(provider)
        if label is None:
            continue
        try:
            text = str(label.stringValue() or "").strip()
        except Exception:
            continue
        if text and "no reading" not in text.lower():
            values.append(text)
        if len(values) >= 2:
            break
    return " · ".join(values) if values else None


def _intake_warnings(target) -> tuple[str, ...]:
    report = getattr(target, "current_intake_report", None)
    if report is None:
        return ()
    warnings: list[str] = []
    try:
        # The intake alert row near the top of the menu already states
        # these two facts, in better words, with the RIGHT click target
        # (Setup) -- any_installed False guarantees that row exists, so
        # repeating the fact here as a second differently-worded warning
        # that opens Diagnostics instead was pure noise.
        if not report.any_installed:
            pass
        for provider in report.stuck_providers:
            warnings.append(f"{provider.label} hooks are silent")
        grok = next(
            (provider for provider in report.known if provider.provider == "grok"),
            None,
        )
        if (
            grok is not None
            and grok.installed
            and grok.event_accepted_at is None
            and not any("Grok" in warning for warning in warnings)
        ):
            warnings.append("Grok hooks may need reload")
    except Exception:
        return ()
    return tuple(dict.fromkeys(warnings))


def _mailbox_counts(target) -> tuple[int, int, int]:
    mailbox = getattr(target, "current_mailbox_projection", None)
    try:
        return (
            int(mailbox.active_count),
            int(mailbox.needs_you_count),
            int(mailbox.ready_count),
        )
    except Exception:
        return (0, 0, 0)


def _setup_required(target) -> bool:
    settings = getattr(target, "settings", None)
    completed = bool(getattr(settings, "setup_screen_completed", False))
    return not completed or bool(_intake_warnings(target))


def _clearable_presented_count(snapshot, target) -> int:
    try:
        count = _legacy.clearable_presented_count(snapshot, target)
    except Exception:
        return 0
    return count if type(count) is int and count >= 0 else 0


def _local_datetime(epoch: float | None) -> datetime | None:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch)).astimezone()
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def _dnd_menu_fields(
    target,
    *,
    now_epoch: float | None = None,
) -> tuple[
    DndMode | None,
    DndSource | None,
    tuple[DndSource, ...],
    str,
    datetime | None,
    bool,
    bool,
]:
    """Adapt the retained typed controller to the compact menu contract."""
    controller = getattr(target, "dnd_controller", None)
    projection = getattr(controller, "projection", None)
    if type(projection) is not DndProjection:
        return (None, None, (), "DND: Off", None, False, False)

    reference = time.time() if now_epoch is None else now_epoch
    override = None
    settings = getattr(target, "settings", None)
    dnd_settings = getattr(settings, "dnd_settings", None)
    if callable(dnd_settings):
        try:
            override = dnd_settings().override
        except Exception:
            override = None
    if type(override) is not DndOverride:
        override = None
    try:
        override_active = override is not None and override.active_at(reference)
    except (TypeError, ValueError):
        override_active = False

    if override_active and not override.resume:
        mode = override.mode
        source = DndSource.MANUAL
    else:
        contribution = next(
            (
                item
                for item in projection.contributions
                if item.mode is not None
            ),
            None,
        )
        mode = contribution.mode if contribution is not None else None
        source = contribution.source if contribution is not None else None

    active_sources = projection.active_sources
    if len(active_sources) > 1:
        return_time = _local_datetime(projection.next_transition_epoch)
    elif override_active and not override.resume:
        return_time = _local_datetime(override.until_epoch)
    else:
        return_time = _local_datetime(projection.next_transition_epoch)

    resume_available = DndSource.SCHEDULE in projection.active_sources
    return (
        mode,
        source,
        active_sources,
        projection.summary,
        return_time,
        override_active,
        resume_available,
    )


def _normalise_device_item_title(item) -> None:
    title = _safe_title(item)
    kind = device_kind(title, title)
    if kind in {DeviceKind.DOT, DeviceKind.PRO}:
        try:
            item.setTitle_(normalize_device_label(title, kind))
        except Exception:
            pass


def _install_dnd_menu(menu, target, inputs: MenuProjectionInputs, plan_by_key) -> None:
    items = _menu_items(menu)
    replaced_index = None
    for item in list(items):
        title = _safe_title(item)
        if title == "Quiet" or title.startswith("End Quiet") or title.startswith("DND:"):
            if replaced_index is None:
                replaced_index = items.index(item)
            _remove_item(menu, item)

    if replaced_index is None:
        current = _menu_items(menu)
        settings_index = next(
            (
                index
                for index, item in enumerate(current)
                if _safe_title(item) == "Settings…"
            ),
            len(current) - 1,
        )
        replaced_index = min(len(current), settings_index + 1)

    submenu = _legacy.NSMenu.alloc().init()
    submenu.setAutoenablesItems_(False)
    for row in project_dnd_submenu(inputs):
        item = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            row.title,
            row.action,
            "",
        )
        item.setTarget_(target)
        item.setEnabled_(row.enabled)
        submenu.addItem_(item)
    parent = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        plan_by_key["dnd"].title,
        None,
        "",
    )
    parent.setSubmenu_(submenu)
    menu.insertItem_atIndex_(parent, replaced_index)


def _install_clear_agents_action(menu, target, plan_by_key) -> None:
    items = _menu_items(menu)
    replaced_index = None
    for item in list(items):
        title = _safe_title(item)
        if title == "Clear Agents…":
            if replaced_index is None:
                replaced_index = items.index(item)
            _remove_item(menu, item)

    row = plan_by_key.get("clear_agents")
    if row is None:
        return
    if replaced_index is None:
        current = _menu_items(menu)
        replaced_index = next(
            (
                index
                for index, item in enumerate(current)
                if _safe_title(item).startswith("Quit ")
            ),
            len(current),
        )
    item = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        row.title,
        row.action,
        "",
    )
    item.setTarget_(target)
    item.setEnabled_(row.enabled)
    menu.insertItem_atIndex_(item, min(replaced_index, len(_menu_items(menu))))


def _install_quick_settings_menu(menu, target, inputs, plan_by_key) -> None:
    """Group the high-frequency controls without creating more root rows."""
    items = _menu_items(menu)
    movable = [
        item
        for item in items
        if _safe_title(item) == "Settings…" or _safe_title(item).startswith("DND:")
    ]
    insert_at = min((items.index(item) for item in movable), default=len(items))
    submenu = _legacy.NSMenu.alloc().init()
    submenu.setAutoenablesItems_(False)

    screen_bar = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Screen Bar",
        "toggleVirtualStatusDevice:",
        "",
    )
    screen_bar.setTarget_(target)
    screen_bar.setState_(1 if inputs.screen_bar_enabled else 0)
    submenu.addItem_(screen_bar)

    lighting = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Lighting…",
        "openColorsWindow:",
        "",
    )
    lighting.setTarget_(target)
    submenu.addItem_(lighting)
    submenu.addItem_(_legacy.NSMenuItem.separatorItem())
    for item in movable:
        _remove_item(menu, item)
        submenu.addItem_(item)

    parent = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        plan_by_key["quick_settings"].title,
        None,
        "",
    )
    parent.setSubmenu_(submenu)
    menu.insertItem_atIndex_(parent, min(insert_at, len(_menu_items(menu))))


def _compact_existing_menu(menu, snapshot, target):
    """Group implementation inventory into semantic root rows."""
    active, needs_you, ready = _mailbox_counts(target)
    (
        dnd_mode,
        dnd_source,
        dnd_active_sources,
        dnd_summary,
        dnd_return_time,
        dnd_override_active,
        dnd_resume_available,
    ) = _dnd_menu_fields(target)
    inputs = MenuProjectionInputs(
        active_count=active,
        needs_you_count=needs_you,
        ready_count=ready,
        usage_summary=_usage_summary(target),
        connected_device_count=_connected_device_count(target),
        screen_bar_enabled=bool(
            getattr(getattr(target, "settings", None), "virtual_status_device_enabled", False)
        ),
        warning_rows=_intake_warnings(target),
        setup_required=_setup_required(target),
        dnd_mode=dnd_mode,
        dnd_source=dnd_source,
        dnd_active_sources=dnd_active_sources,
        dnd_summary=dnd_summary,
        dnd_return_time=dnd_return_time,
        dnd_override_active=dnd_override_active,
        dnd_resume_available=dnd_resume_available,
        clearable_presented_count=_clearable_presented_count(snapshot, target),
    )
    plan = project_root_menu(inputs)
    plan_by_key = {row.key: row for row in plan.rows}

    items = _menu_items(menu)
    mailbox_item = next(
        (item for item in items if _safe_title(item).startswith("Agent Mailbox")),
        None,
    )
    if mailbox_item is not None:
        mailbox_item.setTitle_(plan_by_key["glance"].title)

    for item in list(items):
        title = _safe_title(item)
        if title.startswith("Tip:"):
            _remove_item(menu, item)
        elif title == "Why Is It Doing That?":
            item.setTitle_("Diagnostics…")
        elif title == "Setup…":
            if inputs.setup_required:
                item.setTitle_("Finish Setup…")
            else:
                _remove_item(menu, item)

    items = _menu_items(menu)
    usage_item = getattr(target, "_usage_menu_item", None)
    if usage_item is not None and usage_item in items:
        index = items.index(usage_item)
        _remove_item(menu, usage_item)
        parent = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            plan_by_key["usage"].title,
            None,
            "",
        )
        submenu = _legacy.NSMenu.alloc().init()
        submenu.setAutoenablesItems_(False)
        submenu.addItem_(usage_item)
        parent.setSubmenu_(submenu)
        menu.insertItem_atIndex_(parent, index)

    items = _menu_items(menu)
    device_items = []
    for item in items:
        title = _safe_title(item)
        if title == "Devices":
            # The legacy section header: the group's own "Hardware · N
            # connected" parent replaces it, and an orphaned "Devices"
            # label above a submenu named Hardware is two names for one
            # thing.
            _remove_item(menu, item)
            continue
        if (
            title
            in {
                "Profiles",
                "Timer",
                "Screen Bar",
                "Add Screen Bar",
                "Brightness",
                "Keep Awake With Lid Closed",
                "No devices yet",
                "Plug in a SidePulse Pro or SidePulse Dot, or add the Screen Bar below",
            }
            or title.startswith("Sleep warning:")
            or title.startswith("SidePulse Pro")
            or title.startswith("SidePulse Dot")
        ):
            _normalise_device_item_title(item)
            device_items.append(item)
    if device_items:
        first_index = min(_menu_items(menu).index(item) for item in device_items)
        submenu = _legacy.NSMenu.alloc().init()
        submenu.setAutoenablesItems_(False)
        for item in device_items:
            _remove_item(menu, item)
            submenu.addItem_(item)
        parent = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            plan_by_key["devices"].title,
            None,
            "",
        )
        parent.setSubmenu_(submenu)
        menu.insertItem_atIndex_(parent, first_index)

    _install_dnd_menu(menu, target, inputs, plan_by_key)
    _install_quick_settings_menu(menu, target, inputs, plan_by_key)
    _install_clear_agents_action(menu, target, plan_by_key)

    diagnostics_index = next(
        (
            index
            for index, item in enumerate(_menu_items(menu))
            if _safe_title(item) == "Diagnostics…"
        ),
        len(_menu_items(menu)),
    )
    for offset, warning in enumerate(
        row for row in plan.rows if row.key.startswith("warning:")
    ):
        item = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            warning.title,
            "openSetup:" if "hooks" in warning.title.lower() else "openWhyPanel:",
            "",
        )
        item.setTarget_(target)
        menu.insertItem_atIndex_(item, diagnostics_index + offset)

    previous_separator = True
    for item in list(_menu_items(menu)):
        try:
            separator = bool(item.isSeparatorItem())
        except Exception:
            separator = False
        if separator and previous_separator:
            _remove_item(menu, item)
            continue
        previous_separator = separator
    final_items = _menu_items(menu)
    if final_items:
        try:
            if final_items[-1].isSeparatorItem():
                _remove_item(menu, final_items[-1])
        except Exception:
            pass
    return menu


def build_menu(snapshot, state, target):
    _request_device_identity_refresh()
    menu = _ORIGINAL_BUILD_MENU(snapshot, state, target)
    return _compact_existing_menu(menu, snapshot, target)


_ORIGINAL_CANONICAL_ROOT_SNAPSHOT = getattr(
    _legacy,
    "_sidepulse_original_canonical_root_snapshot",
    _legacy._canonical_agent_root_snapshot,
)


def _compact_canonical_root_snapshot(snapshot, target, *, menu=None):
    """Keep open-menu patches in place under the compact root menu.

    The installed root menu carries the compact glance title on the mailbox
    summary row. The open-menu tracking path rebuilds fresh legacy items to
    diff against, and their legacy summary title would read as a layout
    change, downgrading every live patch (enabling an Open row while the
    user is looking at it) into a deferred rebuild.
    """
    native = _ORIGINAL_CANONICAL_ROOT_SNAPSHOT(snapshot, target, menu=menu)
    if native is None or menu is not None:
        return native
    states, items = native
    summary = next(
        (state for state in states if state.item_key == "agent-mailbox:summary"),
        None,
    )
    if summary is None:
        return native
    active, needs_you, ready = _mailbox_counts(target)
    glance = _glance_title(
        SimpleNamespace(
            active_count=active,
            needs_you_count=needs_you,
            ready_count=ready,
        )
    )
    width, height = _legacy._menu_copy_size(glance)
    updated = tuple(
        dataclass_replace(
            state,
            title=glance,
            measured_width=width,
            measured_height=height,
        )
        if state.item_key == "agent-mailbox:summary"
        else state
        for state in states
    )
    return updated, items


def install_status_bar_facade():
    """Install compact-menu and device adapters after production composition."""
    global _FACADE_INSTALLED
    if (
        _FACADE_INSTALLED
        and _legacy.StatusBarController is JRStatusBarController
        and _legacy.build_menu is build_menu
    ):
        return JRStatusBarController
    _production.install_status_bar_production()
    _legacy._sidepulse_original_device_id_for_root = _ORIGINAL_DEVICE_ID_FOR_ROOT
    _legacy._sidepulse_original_persistable_device_identity = (
        _ORIGINAL_PERSISTABLE_DEVICE_IDENTITY
    )
    _legacy._sidepulse_original_build_menu = _ORIGINAL_BUILD_MENU
    _legacy._sidepulse_original_canonical_root_snapshot = (
        _ORIGINAL_CANONICAL_ROOT_SNAPSHOT
    )
    cache = _device_identity_cache()
    _legacy._sidepulse_device_identity_cache = cache
    _legacy._canonical_agent_root_snapshot = _compact_canonical_root_snapshot
    _legacy.device_id_for_root = device_id_for_root
    _legacy.persistable_device_identity = persistable_device_identity
    _legacy.build_menu = build_menu
    _legacy.StatusBarController = JRStatusBarController
    _FACADE_INSTALLED = True
    return JRStatusBarController


class _StatusBarFacade(ModuleType):
    """Forward reads and monkeypatches to the retained runtime module."""

    def __getattr__(self, name: str):
        if hasattr(_production, name):
            return getattr(_production, name)
        return getattr(_legacy, name)

    def __setattr__(self, name: str, value) -> None:
        if name in {
            "__all__",
            "__class__",
            "__doc__",
            "__file__",
            "__loader__",
            "__name__",
            "__package__",
            "__path__",
            "__spec__",
        } or name.startswith("_facade_"):
            super().__setattr__(name, value)
            return
        setattr(_legacy, name, value)
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in {"__all__", "__class__"} or name.startswith("_facade_"):
            super().__delattr__(name)
            return
        if hasattr(_legacy, name):
            delattr(_legacy, name)
        if name in self.__dict__:
            super().__delattr__(name)

    def __dir__(self) -> list[str]:
        return sorted(
            set(super().__dir__()) | set(dir(_production)) | set(dir(_legacy))
        )


__all__ = tuple(
    sorted(
        {name for name in dir(_legacy) if not name.startswith("_")}
        | {
            "JRStatusBarController",
            "StatusBarController",
            "build_menu",
            "device_id_for_root",
            "install_status_bar_facade",
            "persistable_device_identity",
        }
    )
)
_facade_module = sys.modules[__name__]
_facade_module.__class__ = _StatusBarFacade
_facade_module.__file__ = _legacy.__file__


if __name__ == "__main__":
    raise SystemExit(_legacy.main())
