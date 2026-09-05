#!/usr/bin/env python3
# ruff: noqa: E402
"""Render deterministic source-native AppKit receipts for P3.38 DND.

The harness constructs the production DND Settings pane, isolates its real
Do Not Disturb card for bitmap capture, and runs the same typed state through
the production compact-menu adapter and native NSMenu installer. All settings,
Focus observations, clocks, and callbacks are in memory. The installed app is
not started and no Focus authorization, Full Disk Access, TCC, device, or user
settings mutation is attempted.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
WORKSPACE = Path(__file__).resolve().parent
OUT = WORKSPACE / "task-5-renders"
sys.path.insert(0, str(SRC))

os.environ["TZ"] = "America/Chicago"
time.tzset()

import AppKit
import objc
from AppKit import (
    NSAppearance,
    NSAppearanceNameAqua,
    NSAppearanceNameDarkAqua,
    NSApplication,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSControl,
    NSDatePicker,
    NSDeviceRGBColorSpace,
    NSLayoutConstraint,
    NSMenu,
    NSMenuItem,
    NSPNGFileType,
    NSPopUpButton,
    NSSwitch,
    NSTextField,
    NSTimeZone,
    NSView,
    NSWindow,
    NSWindowStyleMaskTitled,
)
from Foundation import NSAutoreleasePool, NSDate, NSObject, NSRunLoop

from sidepulse import dnd_settings_pane, status_bar
from sidepulse.dnd_policy import (
    DndMode,
    DndOverride,
    DndProjection,
    DndSource,
    compose_dnd_contributions,
    contribution_for_mode,
)
from sidepulse.focus_status import (
    FocusActivity,
    FocusAuthorization,
    FocusStatusObservation,
)
from sidepulse.menu_projection import (
    MenuProjectionInputs,
    project_dnd_submenu,
    project_root_menu,
)
from sidepulse.settings import AgentMonitorSettings

APPEARANCES = {
    "aqua": NSAppearanceNameAqua,
    "dark_aqua": NSAppearanceNameDarkAqua,
}
EXPECTED_STATES = (
    "off",
    "manual_mute",
    "scheduled_dim",
    "focus_pause",
    "asks_only_override",
    "scheduled_fully_dark",
    "temporary_resume",
    "focus_unavailable",
)
NOW_EPOCH = 1_800_000_000.0  # 2027-01-15 02:00:00 America/Chicago.
ONE_HOUR = 3_600.0
FIVE_HOURS = 18_000.0
RECEIPT_PIXEL_SCALE = 4.0
HOST_WIDTH = 700.0
HOST_HEIGHT = 1_080.0
MIN_REGION_VARIANTS = 2
DISCLAIMER = (
    "Source-only AppKit evidence. This is not installed-app, live Focus "
    "authorization or transition, live VoiceOver, every locale or DST case, "
    "physical-device, signing, notarization, packaging, publication, updater, "
    "deployment, or release evidence."
)
SOURCE_PATHS = (
    Path("src/sidepulse/dnd_settings_pane.py"),
    Path("src/sidepulse/dnd_policy.py"),
    Path("src/sidepulse/dnd_controller.py"),
    Path("src/sidepulse/focus_status.py"),
    Path("src/sidepulse/menu_projection.py"),
    Path("src/sidepulse/status_bar.py"),
    Path("src/sidepulse/native_ui.py"),
    Path("src/sidepulse/settings.py"),
    Path("src/sidepulse/_settings_legacy.py"),
    Path(
        ".superpowers/sdd/2026-08-30-jr-bar-p3-38-manual-scheduled-dnd/"
        "render_dnd_receipts.py"
    ),
)


@dataclass(frozen=True, slots=True)
class _StateSpec:
    settings: AgentMonitorSettings
    projection: DndProjection
    observation: FocusStatusObservation
    focus_summary: str
    expected_menu_title: str
    expected_status_prefix: str
    expected_status_has_next_change: bool
    expected_resume_enabled: bool
    expected_end_override_enabled: bool
    expected_authorization_visible: bool
    expected_authorization_enabled: bool


class _ReceiptTarget(NSObject):
    """In-memory target for the production Settings and menu selectors."""

    def initWithSpec_(self, spec):
        self = objc.super(_ReceiptTarget, self).init()
        if self is None:
            return None
        self.settings = spec.settings
        self.dnd_controller = SimpleNamespace(
            projection=spec.projection,
            focus_observation=spec.observation,
        )
        self._receipt_focus_summary = spec.focus_summary
        self.settings_fields = {}
        self.settings_buttons = {}
        self.receipt_actions = []
        return self

    def active_focus_summary(self) -> str:
        return self._receipt_focus_summary

    @objc.python_method
    def _record(self, name: str, sender) -> None:
        identifier = str(getattr(sender, "identifier", lambda: "")() or "")
        self.receipt_actions.append((name, identifier))

    @objc.IBAction
    def toggleDndSchedule_(self, sender):
        self._record("toggle_schedule", sender)

    @objc.IBAction
    def setDndScheduleStartTime_(self, sender):
        self._record("schedule_start", sender)

    @objc.IBAction
    def setDndScheduleEndTime_(self, sender):
        self._record("schedule_end", sender)

    @objc.IBAction
    def setDndScheduleMode_(self, sender):
        self._record("schedule_mode", sender)

    @objc.IBAction
    def setDndDimFraction_(self, sender):
        self._record("dim_fraction", sender)

    @objc.IBAction
    def toggleFocusSync_(self, sender):
        self._record("follow_focus", sender)

    @objc.IBAction
    def setDndFocusMode_(self, sender):
        self._record("focus_mode", sender)

    @objc.IBAction
    def requestDndFocusAuthorization_(self, sender):
        self._record("focus_authorization", sender)

    @objc.IBAction
    def startDndOneHour_(self, sender):
        self._record("one_hour", sender)

    @objc.IBAction
    def resumeDndUntilNextChange_(self, sender):
        self._record("resume", sender)

    @objc.IBAction
    def endDndOverride_(self, sender):
        self._record("end_override", sender)

    @objc.IBAction
    def toggleNightWarmth_(self, sender):
        self._record("night_warmth", sender)

    @objc.IBAction
    def setNightDimFraction_(self, sender):
        self._record("night_dim", sender)

    @objc.IBAction
    def applyTimeboxShortcuts_(self, sender):
        self._record("timebox", sender)

    @objc.IBAction
    def setFocusDimRule_(self, sender):
        self._record("named_focus_dim", sender)

    @objc.IBAction
    def setFocusProfileRule_(self, sender):
        self._record("named_focus_profile", sender)

    @objc.IBAction
    def setFocusSignalPolicy_(self, sender):
        self._record("named_focus_signal", sender)

    @objc.IBAction
    def openFullDiskAccessSettings_(self, sender):
        self._record("open_fda", sender)

    @objc.IBAction
    def revealFocusBinaryInFinder_(self, sender):
        self._record("reveal_fda_target", sender)

    @objc.IBAction
    def setDndMuteForHour_(self, sender):
        self._record("menu_mute", sender)

    @objc.IBAction
    def setDndDimForHour_(self, sender):
        self._record("menu_dim", sender)

    @objc.IBAction
    def setDndPauseForHour_(self, sender):
        self._record("menu_pause", sender)

    @objc.IBAction
    def setDndAsksOnlyForHour_(self, sender):
        self._record("menu_asks", sender)

    @objc.IBAction
    def setDndDarkForHour_(self, sender):
        self._record("menu_dark", sender)

    @objc.IBAction
    def openDndSettings_(self, sender):
        self._record("open_dnd_settings", sender)


class _ReceiptHostView(NSView):
    def drawRect_(self, rect) -> None:
        objc.super(_ReceiptHostView, self).drawRect_(rect)
        NSColor.windowBackgroundColor().setFill()
        NSBezierPath.fillRect_(rect)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state_spec(state: str) -> _StateSpec:
    base = AgentMonitorSettings().with_dnd_dim_fraction(0.25)
    authorized_inactive = FocusStatusObservation(
        FocusAuthorization.AUTHORIZED,
        FocusActivity.INACTIVE,
    )
    if state == "off":
        return _StateSpec(
            base,
            compose_dnd_contributions(()),
            authorized_inactive,
            "No Focus is active.",
            "DND: Off",
            "DND: Off",
            False,
            False,
            False,
            False,
            False,
        )
    if state == "manual_mute":
        override = DndOverride.for_mode(
            DndMode.MUTE,
            created_epoch=NOW_EPOCH - 60.0,
            until_epoch=NOW_EPOCH + ONE_HOUR,
        )
        return _StateSpec(
            base.with_dnd_override(override),
            compose_dnd_contributions(
                (contribution_for_mode(DndSource.MANUAL, DndMode.MUTE),),
                next_transition_epoch=override.until_epoch,
            ),
            authorized_inactive,
            "No Focus is active.",
            "DND: Mute until 3:00 AM",
            "DND: Manual Mute",
            True,
            False,
            True,
            False,
            False,
        )
    if state in {"scheduled_dim", "scheduled_fully_dark"}:
        mode = DndMode.DIM if state == "scheduled_dim" else DndMode.DARK
        mode_label = "Dim" if mode is DndMode.DIM else "Fully Dark"
        settings = base.with_dnd_schedule(
            enabled=True,
            start_minutes=22 * 60,
            end_minutes=7 * 60,
            mode=mode,
        )
        return _StateSpec(
            settings,
            compose_dnd_contributions(
                (contribution_for_mode(DndSource.SCHEDULE, mode, dim_fraction=0.25),),
                next_transition_epoch=NOW_EPOCH + FIVE_HOURS,
            ),
            authorized_inactive,
            "No Focus is active.",
            f"DND: {mode_label}, scheduled until 7:00 AM",
            f"DND: Scheduled {mode_label}",
            True,
            True,
            False,
            False,
            False,
        )
    if state == "focus_pause":
        settings = base.with_focus_sync_enabled(True).with_dnd_focus_mode(
            DndMode.PAUSE
        )
        return _StateSpec(
            settings,
            compose_dnd_contributions(
                (contribution_for_mode(DndSource.MACOS_FOCUS, DndMode.PAUSE),)
            ),
            FocusStatusObservation(
                FocusAuthorization.AUTHORIZED,
                FocusActivity.ACTIVE,
            ),
            "A public macOS Focus is active.",
            "DND: Pause, macOS Focus",
            "DND: macOS Focus Pause",
            False,
            False,
            False,
            False,
            False,
        )
    if state == "asks_only_override":
        override = DndOverride.for_mode(
            DndMode.ASKS_ONLY,
            created_epoch=NOW_EPOCH - 60.0,
            until_epoch=NOW_EPOCH + 2 * ONE_HOUR,
        )
        return _StateSpec(
            base.with_dnd_override(override),
            compose_dnd_contributions(
                (contribution_for_mode(DndSource.MANUAL, DndMode.ASKS_ONLY),),
                next_transition_epoch=override.until_epoch,
            ),
            authorized_inactive,
            "No Focus is active.",
            "DND: Asks Only until 4:00 AM",
            "DND: Manual Asks Only",
            True,
            False,
            True,
            False,
            False,
        )
    if state == "temporary_resume":
        override = DndOverride.for_resume(
            created_epoch=NOW_EPOCH - 60.0,
            until_epoch=NOW_EPOCH + FIVE_HOURS,
        )
        settings = base.with_dnd_schedule(
            enabled=True,
            start_minutes=22 * 60,
            end_minutes=7 * 60,
            mode=DndMode.DARK,
        ).with_dnd_override(override)
        return _StateSpec(
            settings,
            compose_dnd_contributions(
                (),
                next_transition_epoch=override.until_epoch,
            ),
            authorized_inactive,
            "No Focus is active.",
            "DND: Off",
            "DND: Off",
            True,
            False,
            True,
            False,
            False,
        )
    if state == "focus_unavailable":
        settings = base.with_focus_sync_enabled(True).with_dnd_focus_mode(
            DndMode.PAUSE
        )
        return _StateSpec(
            settings,
            compose_dnd_contributions(()),
            FocusStatusObservation(
                FocusAuthorization.UNAVAILABLE,
                FocusActivity.UNAVAILABLE,
            ),
            "Public Focus status is unavailable.",
            "DND: Off",
            "DND: Off",
            False,
            False,
            False,
            True,
            False,
        )
    raise ValueError(f"unknown receipt state: {state}")


def _rect(rect) -> dict[str, float]:
    return {
        "x": round(float(rect.origin.x), 3),
        "y": round(float(rect.origin.y), 3),
        "width": round(float(rect.size.width), 3),
        "height": round(float(rect.size.height), 3),
    }


def _ax_value(element, selector: str):
    try:
        value = getattr(element, selector)()
    except Exception:
        return None
    return None if value is None else str(value)


def _ax_receipt(element) -> dict[str, str | None]:
    return {
        "role": _ax_value(element, "accessibilityRole"),
        "label": _ax_value(element, "accessibilityLabel"),
        "value": _ax_value(element, "accessibilityValue"),
        "help": _ax_value(element, "accessibilityHelp"),
    }


def _selected_popup(popup: NSPopUpButton) -> dict[str, str]:
    item = popup.selectedItem()
    return {
        "title": str(item.title()),
        "value": str(item.representedObject() or ""),
    }


def _root_rect(view, root):
    return view.convertRect_toView_(view.bounds(), root)


def _visible(view) -> bool:
    current = view
    while current is not None:
        if current.isHidden():
            return False
        current = current.superview()
    return True


def _all_descendants(root) -> list[object]:
    descendants = []

    def visit(view) -> None:
        descendants.append(view)
        for child in view.subviews():
            visit(child)

    visit(root)
    return descendants


def _visible_text(root) -> list[str]:
    result: list[str] = []
    for view in _all_descendants(root):
        if not _visible(view):
            continue
        value = None
        getter = getattr(view, "stringValue", None)
        if callable(getter):
            candidate = getter()
            if isinstance(candidate, str) and candidate:
                value = candidate
        if value is None:
            getter = getattr(view, "title", None)
            if callable(getter):
                candidate = getter()
                if isinstance(candidate, str) and candidate:
                    value = candidate
        if value is not None and value not in result:
            result.append(value)
    return result


def _find_dnd_root(pane) -> object:
    document = pane.documentView()
    if document is None:
        raise RuntimeError("production DND pane has no document view")
    matches = [
        view
        for view in _all_descendants(document)
        if isinstance(view, NSTextField) and str(view.stringValue()) == "Do Not Disturb"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one production DND title, found {len(matches)}")
    root = matches[0].superview()
    if root is None:
        raise RuntimeError("production DND title has no card root")
    return root


def _mark_subtree_for_display(view) -> None:
    view.setNeedsDisplay_(True)
    for subview in view.subviews():
        _mark_subtree_for_display(subview)


def _paint(root, host, window) -> NSBitmapImageRep:
    window.layoutIfNeeded()
    host.layoutSubtreeIfNeeded()
    root.layoutSubtreeIfNeeded()
    _mark_subtree_for_display(root)
    window.display()
    root.displayIfNeeded()
    NSRunLoop.currentRunLoop().runUntilDate_(
        NSDate.dateWithTimeIntervalSinceNow_(0.03)
    )
    _mark_subtree_for_display(root)
    window.display()
    root.displayIfNeeded()
    capture = root.convertRect_toView_(root.bounds(), host)
    host_bounds = host.bounds()
    if not (
        capture.origin.x >= -0.01
        and capture.origin.y >= -0.01
        and capture.origin.x + capture.size.width <= host_bounds.size.width + 0.01
        and capture.origin.y + capture.size.height <= host_bounds.size.height + 0.01
    ):
        raise RuntimeError(
            f"production DND card is not fully visible in receipt host: {_rect(capture)}"
        )
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None,
        int(round(capture.size.width * RECEIPT_PIXEL_SCALE)),
        int(round(capture.size.height * RECEIPT_PIXEL_SCALE)),
        8,
        4,
        True,
        False,
        NSDeviceRGBColorSpace,
        0,
        0,
    )
    if rep is None:
        raise RuntimeError("could not allocate AppKit DND bitmap")
    rep.setSize_(capture.size)
    host.cacheDisplayInRect_toBitmapImageRep_(capture, rep)
    return rep


def _sample_variants(rep: NSBitmapImageRep, frame, root_height: float) -> int:
    scale = RECEIPT_PIXEL_SCALE
    x0 = max(0, math.floor(frame.origin.x * scale))
    x1 = min(rep.pixelsWide(), math.ceil((frame.origin.x + frame.size.width) * scale))
    y0 = max(
        0,
        math.floor((root_height - frame.origin.y - frame.size.height) * scale),
    )
    y1 = min(
        rep.pixelsHigh(),
        math.ceil((root_height - frame.origin.y) * scale),
    )
    colors: set[tuple[int, int, int, int]] = set()
    for x in range(x0, x1, 4):
        for y in range(y0, y1, 4):
            color = rep.colorAtX_y_(x, y)
            if color is None:
                continue
            colors.add(
                tuple(
                    int(round(float(component) * 255.0))
                    for component in (
                        color.redComponent(),
                        color.greenComponent(),
                        color.blueComponent(),
                        color.alphaComponent(),
                    )
                )
            )
    return len(colors)


def _region_receipts(root, rep: NSBitmapImageRep) -> dict[str, dict[str, object]]:
    bounds = root.bounds()
    regions: dict[str, dict[str, object]] = {}
    index = 0
    for view in _all_descendants(root):
        if view is root or not _visible(view):
            continue
        text = ""
        if isinstance(view, NSTextField):
            text = str(view.stringValue() or "")
        elif isinstance(view, NSControl):
            getter = getattr(view, "title", None)
            text = str(getter() or "") if callable(getter) else ""
        if not text:
            continue
        frame = _root_rect(view, root)
        alignment = view.alignmentRectForFrame_(frame)
        if frame.size.width <= 0.0 or frame.size.height <= 0.0:
            raise RuntimeError(f"visible DND element has empty geometry: {text!r}")
        if not (
            alignment.origin.x >= -0.01
            and alignment.origin.y >= -0.01
            and alignment.origin.x + alignment.size.width
            <= bounds.size.width + 0.01
            and alignment.origin.y + alignment.size.height
            <= bounds.size.height + 0.01
        ):
            raise RuntimeError(f"visible DND element clips outside the card: {text!r}")
        variants = _sample_variants(rep, frame, float(bounds.size.height))
        if variants < MIN_REGION_VARIANTS:
            raise RuntimeError(f"visible DND element did not paint: {text!r}")
        regions[f"{index}:{type(view).__name__}:{text}"] = {
            "frame": _rect(frame),
            "alignment_rect": _rect(alignment),
            "sampled_color_variants": variants,
        }
        index += 1
    return regions


def _control_receipt(control, root) -> dict[str, object]:
    frame = _root_rect(control, root)
    result = {
        "class": f"{type(control).__module__}.{type(control).__name__}",
        "frame": _rect(frame),
        "visible": _visible(control),
        "enabled": bool(control.isEnabled()),
        "ax": _ax_receipt(control),
    }
    title = getattr(control, "title", None)
    if callable(title):
        result["title"] = str(title() or "")
    if isinstance(control, NSSwitch):
        result["state"] = int(control.state())
    if isinstance(control, NSPopUpButton):
        result["selection"] = _selected_popup(control)
    if isinstance(control, NSDatePicker):
        result["minutes"] = dnd_settings_pane.minutes_from_time_picker(control)
    return result


def _dnd_control_map(fields: dict[str, object], buttons: dict[str, object]):
    controls = {
        "status": fields["dnd_status_label"],
        "schedule_enabled": buttons["dnd_schedule_enabled"],
        "schedule_start": fields["dnd_schedule_start_time"],
        "schedule_end": fields["dnd_schedule_end_time"],
        "schedule_mode": fields["dnd_schedule_mode"],
        "dim_fraction": fields["dnd_dim_fraction"],
        "follow_focus": buttons["focus_sync_enabled"],
        "focus_mode": fields["dnd_focus_mode"],
        "focus_authorization": buttons["dnd_focus_authorization"],
        "resume": buttons["dnd_resume"],
        "end_override": buttons["dnd_end_override"],
    }
    controls.update(
        {
            f"temporary_{mode.value}": buttons[f"dnd_temporary_mode:{mode.value}"]
            for mode in DndMode
        }
    )
    return controls


def _keyboard_receipt(fields, buttons) -> dict[str, object]:
    names = {
        id(value): key
        for key, value in (*fields.items(), *buttons.items())
        if key != "dnd_keyboard_order"
    }
    names.update(
        {id(value): key for key, value in _dnd_control_map(fields, buttons).items()}
    )
    order = tuple(fields["dnd_keyboard_order"])
    if len(order) != len({id(control) for control in order}):
        raise RuntimeError("production DND key-view order has duplicate controls")
    observed = []
    current = order[0]
    for _ in order:
        name = names.get(id(current))
        if name is None:
            raise RuntimeError(
                f"production DND key-view order contains an unnamed {type(current).__name__}"
            )
        observed.append(name)
        current = current.nextKeyView()
    if current is not order[0] or tuple(order) != tuple(fields["dnd_keyboard_order"]):
        raise RuntimeError("production DND key-view loop is not closed")
    return {
        "count": len(order),
        "closed": True,
        "order": observed,
    }


def _menu_receipt(target, spec: _StateSpec) -> dict[str, object]:
    (
        mode,
        source,
        active_sources,
        summary,
        return_time,
        override_active,
        resume_available,
    ) = status_bar._dnd_menu_fields(target, now_epoch=NOW_EPOCH)
    inputs = MenuProjectionInputs(
        active_count=0,
        needs_you_count=0,
        ready_count=0,
        usage_summary=None,
        connected_device_count=0,
        screen_bar_enabled=False,
        warning_rows=(),
        setup_required=False,
        dnd_mode=mode,
        dnd_source=source,
        dnd_active_sources=active_sources,
        dnd_summary=summary,
        dnd_return_time=return_time,
        dnd_override_active=override_active,
        dnd_resume_available=resume_available,
        clearable_presented_count=0,
    )
    root_plan = project_root_menu(inputs)
    plan_by_key = {row.key: row for row in root_plan.rows}
    submenu_plan = project_dnd_submenu(inputs)
    if plan_by_key["dnd"].title != spec.expected_menu_title:
        raise RuntimeError(
            f"menu title mismatch: expected={spec.expected_menu_title!r}, "
            f"actual={plan_by_key['dnd'].title!r}"
        )

    native_menu = NSMenu.alloc().init()
    native_menu.setAutoenablesItems_(False)
    native_menu.addItem_(
        NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Settings…",
            None,
            "",
        )
    )
    status_bar._install_dnd_menu(native_menu, target, inputs, plan_by_key)
    parents = [
        item
        for item in native_menu.itemArray()
        if str(item.title()).startswith("DND:")
    ]
    if len(parents) != 1:
        raise RuntimeError(f"native menu contains {len(parents)} DND parents")
    parent = parents[0]
    submenu = parent.submenu()
    if submenu is None:
        raise RuntimeError("native DND parent has no submenu")
    native_rows = [
        {
            "title": str(item.title()),
            "action": str(item.action() or ""),
            "enabled": bool(item.isEnabled()),
            "target_is_receipt_controller": item.target() is target,
        }
        for item in submenu.itemArray()
    ]
    planned_rows = [
        {
            "key": row.key,
            "title": row.title,
            "action": row.action,
            "enabled": row.enabled,
        }
        for row in submenu_plan
    ]
    if [row["title"] for row in native_rows] != [row["title"] for row in planned_rows]:
        raise RuntimeError("native DND submenu does not match its production projection")
    if not all(row["target_is_receipt_controller"] for row in native_rows):
        raise RuntimeError("native DND submenu lost the retained target")
    return {
        "typed_inputs": {
            "mode": None if mode is None else mode.value,
            "source": None if source is None else source.value,
            "active_sources": [item.value for item in active_sources],
            "summary": summary,
            "return_epoch": None if return_time is None else return_time.timestamp(),
            "return_local_iso": None if return_time is None else return_time.isoformat(),
            "override_active": override_active,
            "resume_available": resume_available,
        },
        "root_title": plan_by_key["dnd"].title,
        "projected_submenu": planned_rows,
        "native_parent": {
            "class": f"{type(parent).__module__}.{type(parent).__name__}",
            "title": str(parent.title()),
            "has_submenu": True,
        },
        "native_submenu": native_rows,
    }


def _validate_controls(spec: _StateSpec, fields, buttons) -> None:
    status = str(fields["dnd_status_label"].stringValue())
    if not status.startswith(spec.expected_status_prefix):
        raise RuntimeError(
            f"DND status mismatch: expected prefix={spec.expected_status_prefix!r}, "
            f"actual={status!r}"
        )
    if ("Next change:" in status) is not spec.expected_status_has_next_change:
        raise RuntimeError(f"DND next-change visibility mismatch: {status!r}")
    if bool(buttons["dnd_resume"].isEnabled()) is not spec.expected_resume_enabled:
        raise RuntimeError("DND Resume enabled state is incorrect")
    if (
        bool(buttons["dnd_end_override"].isEnabled())
        is not spec.expected_end_override_enabled
    ):
        raise RuntimeError("DND End Override enabled state is incorrect")
    authorization = buttons["dnd_focus_authorization"]
    if (not bool(authorization.isHidden())) is not spec.expected_authorization_visible:
        raise RuntimeError("Focus authorization visibility is incorrect")
    if bool(authorization.isEnabled()) is not spec.expected_authorization_enabled:
        raise RuntimeError("Focus authorization enabled state is incorrect")
    if fields["dnd_status_label"].accessibilityValue() != status:
        raise RuntimeError("DND status accessibility value is stale")
    if fields["dnd_status_label"].accessibilityLabel() != "Do Not Disturb status":
        raise RuntimeError("DND status accessibility label changed")
    for name, control in _dnd_control_map(fields, buttons).items():
        if name == "status":
            continue
        ax = _ax_receipt(control)
        if not ax["label"] or not ax["help"]:
            raise RuntimeError(f"DND control lacks accessibility metadata: {name}")


def render_case(appearance_key: str, state: str) -> dict[str, object]:
    spec = _state_spec(state)
    target = _ReceiptTarget.alloc().initWithSpec_(spec)
    dnd_settings_pane.focus_sync.configured_focus_modes = lambda: ()
    pane, fields, buttons = dnd_settings_pane.build_dnd_settings_pane(target)
    target.settings_fields = fields
    target.settings_buttons = buttons
    dnd_settings_pane.refresh_dnd_settings_controls(target, now_epoch=NOW_EPOCH)

    appearance = NSAppearance.appearanceNamed_(APPEARANCES[appearance_key])
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0.0, 0.0), (HOST_WIDTH, HOST_HEIGHT)),
        NSWindowStyleMaskTitled,
        NSBackingStoreBuffered,
        False,
    )
    window.setAppearance_(appearance)
    window.setBackgroundColor_(NSColor.windowBackgroundColor())
    host = _ReceiptHostView.alloc().initWithFrame_(
        ((0.0, 0.0), (HOST_WIDTH, HOST_HEIGHT))
    )
    host.setAppearance_(appearance)
    pane.setAppearance_(appearance)
    pane.setTranslatesAutoresizingMaskIntoConstraints_(False)
    host.addSubview_(pane)
    window.setContentView_(host)
    NSLayoutConstraint.activateConstraints_(
        [
            pane.leadingAnchor().constraintEqualToAnchor_constant_(
                host.leadingAnchor(), 20.0
            ),
            pane.trailingAnchor().constraintEqualToAnchor_constant_(
                host.trailingAnchor(), -20.0
            ),
            pane.topAnchor().constraintEqualToAnchor_constant_(
                host.topAnchor(), 20.0
            ),
            pane.bottomAnchor().constraintEqualToAnchor_constant_(
                host.bottomAnchor(), -20.0
            ),
        ]
    )
    try:
        window.layoutIfNeeded()
        host.layoutSubtreeIfNeeded()
        pane.documentView().layoutSubtreeIfNeeded()
        root = _find_dnd_root(pane)
        root.setAppearance_(appearance)
        window.layoutIfNeeded()
        host.layoutSubtreeIfNeeded()
        root.layoutSubtreeIfNeeded()
        if not window.makeFirstResponder_(fields["dnd_status_label"]):
            raise RuntimeError("could not focus the production DND status")

        _validate_controls(spec, fields, buttons)
        keyboard = _keyboard_receipt(fields, buttons)
        menu = _menu_receipt(target, spec)
        rep = _paint(root, host, window)
        OUT.mkdir(parents=True, exist_ok=True)
        filename = f"{appearance_key}-{state}.png"
        path = OUT / filename
        data = rep.representationUsingType_properties_(NSPNGFileType, {})
        if data is None or not data.writeToFile_atomically_(str(path), True):
            raise RuntimeError(f"could not write {path}")

        controls = {
            name: _control_receipt(control, root)
            for name, control in _dnd_control_map(fields, buttons).items()
        }
        focus_status = str(fields["dnd_focus_authorization_status"].stringValue())
        projection = spec.projection
        parsed = spec.settings.dnd_settings()
        effective_appearance = str(root.effectiveAppearance().name())
        expected_appearance = str(APPEARANCES[appearance_key])
        if effective_appearance != expected_appearance:
            raise RuntimeError(
                f"DND appearance mismatch: {effective_appearance!r}"
            )
        return {
            "state": state,
            "appearance": appearance_key,
            "native_appearance_name": effective_appearance,
            "png": filename,
            "image_sha256": _sha256(path),
            "provenance": {
                "builder": "sidepulse.dnd_settings_pane.build_dnd_settings_pane",
                "refresh": "sidepulse.dnd_settings_pane.refresh_dnd_settings_controls",
                "menu_adapter": "sidepulse.status_bar._dnd_menu_fields",
                "menu_projection": (
                    "sidepulse.menu_projection.project_root_menu + "
                    "project_dnd_submenu"
                ),
                "native_menu_installer": "sidepulse.status_bar._install_dnd_menu",
                "pane_class": f"{type(pane).__module__}.{type(pane).__name__}",
                "card_class": f"{type(root).__module__}.{type(root).__name__}",
                "synthetic_settings_ui": False,
                "in_memory_controller_truth": True,
            },
            "policy": {
                "summary": projection.summary,
                "reason": projection.reason,
                "mode": None if projection.mode is None else projection.mode.value,
                "source": None if projection.source is None else projection.source.value,
                "active_modes": [mode.value for mode in projection.active_modes],
                "active_sources": [source.value for source in projection.active_sources],
                "next_transition_epoch": projection.next_transition_epoch,
                "display_admission": projection.display_admission.value,
                "brightness_factor": projection.brightness_factor,
                "outbound_admission": projection.outbound_admission.value,
                "banner_allowed": projection.banner_allowed,
                "audible_allowed": projection.audible_allowed,
                "webhook_allowed": projection.webhook_allowed,
            },
            "persisted_controls": {
                "schedule": {
                    "enabled": parsed.schedule.enabled,
                    "start_minutes": parsed.schedule.start_minutes,
                    "end_minutes": parsed.schedule.end_minutes,
                    "mode": parsed.schedule.mode.value,
                },
                "dim_fraction": parsed.dim_fraction,
                "follow_focus": spec.settings.focus_sync_enabled,
                "focus_mode": parsed.focus_mode.value,
                "override": (
                    None
                    if parsed.override is None
                    else {
                        "mode": (
                            None
                            if parsed.override.mode is None
                            else parsed.override.mode.value
                        ),
                        "resume": parsed.override.resume,
                        "created_epoch": parsed.override.created_epoch,
                        "until_epoch": parsed.override.until_epoch,
                    }
                ),
            },
            "focus": {
                "authorization": spec.observation.authorization.value,
                "activity": spec.observation.activity.value,
                "summary": spec.focus_summary,
                "status_copy": focus_status,
                "authorization_button_visible": not bool(
                    buttons["dnd_focus_authorization"].isHidden()
                ),
                "authorization_button_enabled": bool(
                    buttons["dnd_focus_authorization"].isEnabled()
                ),
                "authorization_requested": False,
            },
            "settings_copy": {
                "ordered_visible_text": _visible_text(root),
                "status": str(fields["dnd_status_label"].stringValue()),
                "status_ax": _ax_receipt(fields["dnd_status_label"]),
            },
            "controls": controls,
            "keyboard": keyboard,
            "first_responder": "status",
            "menu": menu,
            "layout": {
                "card_points": _rect(root.bounds()),
                "image_pixels": {
                    "width": int(rep.pixelsWide()),
                    "height": int(rep.pixelsHigh()),
                },
                "pixel_scale": RECEIPT_PIXEL_SCALE,
                "visible_regions_in_bounds": True,
                "regions": _region_receipts(root, rep),
            },
        }
    finally:
        window.close()


def _render_one(appearance: str, state: str) -> int:
    pool = NSAutoreleasePool.alloc().init()
    try:
        NSTimeZone.setDefaultTimeZone_(
            NSTimeZone.timeZoneWithName_("America/Chicago")
        )
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited)
        receipt = render_case(appearance, state)
        print(json.dumps(receipt, sort_keys=True), flush=True)
    except BaseException:
        traceback.print_exc()
        sys.stderr.flush()
        os._exit(1)
    _ = pool
    os._exit(0)


def _validate_manifest(manifest: dict[str, object]) -> dict[str, object]:
    expected_names = {
        f"{appearance}-{state}.png"
        for appearance in APPEARANCES
        for state in EXPECTED_STATES
    }
    renders = manifest["renders"]
    if not isinstance(renders, list):
        raise RuntimeError("DND manifest renders are invalid")
    actual_names = {receipt["png"] for receipt in renders}
    if len(renders) != 16 or actual_names != expected_names:
        raise RuntimeError(
            f"DND render matrix mismatch: count={len(renders)}, "
            f"missing={sorted(expected_names - actual_names)}, "
            f"unexpected={sorted(actual_names - expected_names)}"
        )
    source_files = manifest["source_files"]
    if not isinstance(source_files, dict):
        raise RuntimeError("DND manifest source files are invalid")
    source_mismatches = sum(
        _sha256(ROOT / relative) != metadata["sha256"]
        for relative, metadata in source_files.items()
    )
    image_mismatches = sum(
        _sha256(OUT / receipt["png"]) != receipt["image_sha256"]
        for receipt in renders
    )
    unexpected_pngs = {path.name for path in OUT.glob("*.png")} - expected_names
    if source_mismatches or image_mismatches or unexpected_pngs:
        raise RuntimeError(
            "DND receipt integrity failed: "
            f"source_mismatches={source_mismatches}, "
            f"image_mismatches={image_mismatches}, "
            f"unexpected_pngs={sorted(unexpected_pngs)}"
        )
    aggregate = hashlib.sha256()
    per_image = hashlib.sha256()
    for filename in sorted(expected_names):
        digest = _sha256(OUT / filename)
        aggregate.update(filename.encode("utf-8"))
        aggregate.update((OUT / filename).read_bytes())
        per_image.update(f"{filename}:{digest}\n".encode())
    return {
        "render_count": len(renders),
        "source_file_count": len(source_files),
        "source_hash_mismatches": source_mismatches,
        "image_hash_mismatches": image_mismatches,
        "unexpected_png_count": len(unexpected_pngs),
        "image_set_sha256": aggregate.hexdigest(),
        "per_png_hash_list_sha256": per_image.hexdigest(),
    }


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--render-one":
        return _render_one(sys.argv[2], sys.argv[3])
    OUT.mkdir(parents=True, exist_ok=True)
    renders = []
    for appearance in APPEARANCES:
        for state in EXPECTED_STATES:
            output = subprocess.check_output(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--render-one",
                    appearance,
                    state,
                ],
                cwd=ROOT,
                text=True,
            )
            lines = [line for line in output.splitlines() if line.strip()]
            if not lines:
                raise RuntimeError(
                    f"DND receipt worker produced no output: {appearance}/{state}"
                )
            renders.append(json.loads(lines[-1]))
    harness_path = Path(__file__).resolve()
    manifest: dict[str, object] = {
        "schema": "p3.38-task-5-native-dnd-receipt-v1",
        "source_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_only_disclaimer": DISCLAIMER,
        "fixed_clock": {
            "epoch": NOW_EPOCH,
            "timezone": "America/Chicago",
        },
        "production_render_path": (
            "build_dnd_settings_pane -> _make_dnd_card -> native_ui.make_card/"
            "make_row/make_switch_row -> refresh_dnd_settings_controls -> "
            "production AppKit bitmap"
        ),
        "production_menu_path": (
            "status_bar._dnd_menu_fields -> menu_projection.project_root_menu/"
            "project_dnd_submenu -> status_bar._install_dnd_menu -> native NSMenu"
        ),
        "appearances": {key: str(value) for key, value in APPEARANCES.items()},
        "states": list(EXPECTED_STATES),
        "source_files": {
            str(path): {"sha256": _sha256(ROOT / path)} for path in SOURCE_PATHS
        },
        "pinned_sha256": {
            "production_dnd_settings_pane": _sha256(
                ROOT / "src/sidepulse/dnd_settings_pane.py"
            ),
            "production_menu_projection": _sha256(
                ROOT / "src/sidepulse/menu_projection.py"
            ),
            "production_menu_adapter": _sha256(
                ROOT / "src/sidepulse/status_bar.py"
            ),
            "receipt_harness": _sha256(harness_path),
        },
        "renders": renders,
    }
    manifest["integrity"] = _validate_manifest(manifest)
    manifest_path = OUT / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    integrity = manifest["integrity"]
    print(
        json.dumps(
            {
                "appearances": len(APPEARANCES),
                "states": len(EXPECTED_STATES),
                "renders": integrity["render_count"],
                "source_files": integrity["source_file_count"],
                "source_hash_mismatches": integrity["source_hash_mismatches"],
                "image_hash_mismatches": integrity["image_hash_mismatches"],
                "unexpected_png_count": integrity["unexpected_png_count"],
                "image_set_sha256": integrity["image_set_sha256"],
                "per_png_hash_list_sha256": integrity[
                    "per_png_hash_list_sha256"
                ],
                "manifest": str(manifest_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
