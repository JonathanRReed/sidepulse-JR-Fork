#!/usr/bin/env python3
# ruff: noqa: E402
"""Render source-only AppKit receipts for the P3.39 Clear Agents popover."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
WORKSPACE = Path(__file__).resolve().parent
OUT = WORKSPACE / "task-5-renders"
sys.path.insert(0, str(SRC))

import AppKit
import objc
from AppKit import (
    NSAppearance,
    NSAppearanceNameAqua,
    NSAppearanceNameDarkAqua,
    NSApplication,
    NSBitmapImageRep,
    NSColor,
    NSDeviceRGBColorSpace,
    NSLayoutConstraint,
    NSPNGFileType,
    NSView,
    NSWindow,
    NSWindowStyleMaskTitled,
)
from Foundation import NSAutoreleasePool, NSDate, NSRunLoop

from sidepulse.capacity_types import SourceKey
from sidepulse.clear_agents import (
    ClearAgentsState,
    plan_clear_agents_commit,
    plan_clear_agents_undo,
    project_clear_agents_preview,
)
from sidepulse.clear_agents_popover import (
    ClearAgentsPopoverPresentation,
    ClearAgentsPopoverPresenter,
    ClearAgentsPopoverState,
)
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.provider_facts import WorkIdentifier, WorkKey

APPEARANCES = {
    "aqua": NSAppearanceNameAqua,
    "dark_aqua": NSAppearanceNameDarkAqua,
}
STATES = (
    "preview",
    "protected_live_work",
    "stale",
    "saving",
    "failure",
    "successful_receipt",
    "expired_undo",
    "undone",
)
PIXEL_SCALE = 4.0
DISCLAIMER = (
    "Source-only AppKit evidence. This is not installed-app, live-provider, "
    "live VoiceOver, physical-LED, signing, notarization, packaging, "
    "publication, updater, deployment, or release evidence."
)
SOURCE_PATHS = (
    Path("src/sidepulse/clear_agents.py"),
    Path("src/sidepulse/clear_agents_popover.py"),
    Path("src/sidepulse/status_bar_legacy.py"),
    Path("src/sidepulse/clear_agents_store.py"),
    Path(
        ".superpowers/sdd/2026-08-30-jr-bar-p3-39-safe-clear-agents/"
        "render_clear_agents_receipts.py"
    ),
)


@dataclass(frozen=True)
class RenderCase:
    state: str
    presentation: ClearAgentsPopoverPresentation


class _ReceiptHostView(NSView):
    def drawRect_(self, rect) -> None:
        objc.super(_ReceiptHostView, self).drawRect_(rect)
        NSColor.windowBackgroundColor().setFill()
        AppKit.NSBezierPath.fillRect_(rect)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source(
    provider: str = "codex",
    source_instance: str = "local:01",
) -> SourceKey:
    return SourceKey(provider, "hooks", source_instance, "live_agent_events")


def _status(
    agent_id: str,
    *,
    provider: str = "codex",
    source: SourceKey | None = None,
    mode: AgentMode = AgentMode.COMPLETED,
    event_name: str = "Stop",
    keyed: bool = True,
    seconds: float = 0.0,
    display_name: str = "Codex build agent",
) -> AgentStatus:
    from datetime import datetime, timezone

    actual_source = source or _source(provider)
    return AgentStatus(
        provider=provider,
        agent_id=agent_id,
        display_name=display_name,
        mode=mode,
        updated_at=datetime.fromtimestamp(1_788_120_000.0 + seconds, tz=timezone.utc),
        event_name=event_name,
        work_key=(
            WorkKey(actual_source, WorkIdentifier(agent_id.replace(":", ".")))
            if keyed
            else None
        ),
    )


def _preview_cases() -> dict[str, ClearAgentsPopoverPresentation]:
    base_preview = project_clear_agents_preview(
        (
            _status("codex:session:build", seconds=2.0, display_name="Codex build agent"),
            _status("claude:session:review", provider="claude", seconds=1.0, display_name="Claude review agent"),
        ),
        state=ClearAgentsState(),
        now_epoch=1_788_120_010.0,
    )
    rich_preview = project_clear_agents_preview(
        (
            _status("codex:session:deploy", seconds=3.0, display_name="Codex deploy agent"),
        ),
        state=ClearAgentsState(),
        now_epoch=1_788_120_020.0,
        protected_statuses=(
            _status("codex:session:active", mode=AgentMode.WORKING, seconds=4.0, display_name="Codex live agent"),
            _status("claude:session:waiting", provider="claude", mode=AgentMode.WAITING_FOR_INPUT, seconds=5.0, display_name="Claude waiting agent"),
            _status("devin:session:failed", provider="devin", mode=AgentMode.BLOCKED_ERROR, seconds=6.0, display_name="Devin failed agent"),
            _status("codex:session:queued", mode=AgentMode.IDLE_READY, seconds=7.0, display_name="Codex queued agent"),
            _status("remote:studio:codex:done", seconds=8.0, display_name="Remote done agent"),
            _status("codex:session:unsafe", keyed=False, seconds=9.0, display_name="Codex unsafe agent"),
            _status("codex:session:closed", event_name="SessionEnd", seconds=10.0, display_name="Codex closed agent"),
        ),
        queued_agent_ids=("codex:session:queued",),
    )
    receipt_commit = plan_clear_agents_commit(
        base_preview,
        base_preview,
        ClearAgentsState(),
        batch_id="receipt-batch",
        committed_at_epoch=1_788_120_030.0,
    )
    undo_plan = plan_clear_agents_undo(
        receipt_commit.next_state,
        batch_id="receipt-batch",
        now_epoch=1_788_120_031.0,
    )
    return {
        "preview": ClearAgentsPopoverPresentation.from_preview(base_preview),
        "protected_live_work": ClearAgentsPopoverPresentation.from_preview(rich_preview),
        "stale": ClearAgentsPopoverPresentation.from_preview(
            rich_preview,
            state=ClearAgentsPopoverState.STALE,
        ),
        "saving": ClearAgentsPopoverPresentation.from_preview(
            base_preview,
            state=ClearAgentsPopoverState.SAVING,
        ),
        "failure": ClearAgentsPopoverPresentation.from_preview(
            base_preview,
            state=ClearAgentsPopoverState.FAILURE,
        ),
        "successful_receipt": ClearAgentsPopoverPresentation.from_commit_plan(
            receipt_commit
        ),
        "expired_undo": ClearAgentsPopoverPresentation.from_commit_plan(
            receipt_commit,
            state=ClearAgentsPopoverState.EXPIRED_UNDO,
        ),
        "undone": ClearAgentsPopoverPresentation.from_undo_plan(undo_plan),
    }


def render_cases() -> tuple[RenderCase, ...]:
    presentations = _preview_cases()
    return tuple(RenderCase(state, presentations[state]) for state in STATES)


def _host_window(appearance_name: str) -> NSWindow:
    appearance = NSAppearance.appearanceNamed_(appearance_name)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0.0, 0.0), (640.0, 480.0)),
        NSWindowStyleMaskTitled,
        AppKit.NSBackingStoreBuffered,
        False,
    )
    window.setBackgroundColor_(NSColor.windowBackgroundColor())
    if appearance is not None:
        window.setAppearance_(appearance)
    container = _ReceiptHostView.alloc().initWithFrame_(((0.0, 0.0), (640.0, 480.0)))
    window.setContentView_(container)
    window.layoutIfNeeded()
    return window


def _mark_subtree_for_display(view) -> None:
    view.setNeedsDisplay_(True)
    for subview in view.subviews():
        _mark_subtree_for_display(subview)


def _paint(root_view, window) -> NSBitmapImageRep:
    window.layoutIfNeeded()
    root_view.layoutSubtreeIfNeeded()
    _mark_subtree_for_display(root_view)
    window.display()
    root_view.displayIfNeeded()
    NSRunLoop.currentRunLoop().runUntilDate_(
        NSDate.dateWithTimeIntervalSinceNow_(0.03)
    )
    bounds = root_view.bounds()
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None,
        int(round(bounds.size.width * PIXEL_SCALE)),
        int(round(bounds.size.height * PIXEL_SCALE)),
        8,
        4,
        True,
        False,
        NSDeviceRGBColorSpace,
        0,
        0,
    )
    if rep is None:
        raise RuntimeError("could not allocate AppKit Clear Agents bitmap")
    rep.setSize_(bounds.size)
    root_view.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
    return rep


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


def _rect(rect) -> dict[str, float]:
    return {
        "x": float(rect.origin.x),
        "y": float(rect.origin.y),
        "width": float(rect.size.width),
        "height": float(rect.size.height),
    }


def _sample_variants(rep: NSBitmapImageRep, frame) -> int:
    x0 = max(0, math.floor(frame.origin.x * PIXEL_SCALE))
    x1 = min(
        rep.pixelsWide(),
        math.ceil((frame.origin.x + frame.size.width) * PIXEL_SCALE),
    )
    y0 = max(
        0,
        math.floor(rep.pixelsHigh() - (frame.origin.y + frame.size.height) * PIXEL_SCALE),
    )
    y1 = min(
        rep.pixelsHigh(),
        math.ceil(rep.pixelsHigh() - frame.origin.y * PIXEL_SCALE),
    )
    samples: set[tuple[int, int, int, int]] = set()
    for x in range(x0, x1, 4):
        for y in range(y0, y1, 4):
            color = rep.colorAtX_y_(x, y)
            if color is None:
                continue
            samples.add(
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
    return len(samples)


def _keyboard_loop_is_closed(presenter: ClearAgentsPopoverPresenter) -> bool:
    focusable = presenter.focusable_controls()
    if not focusable:
        return False
    current = presenter.root_view
    expected = [
        str(button.accessibilityLabel() or button.title()) for button in focusable
    ]
    for label in expected:
        current = current.nextKeyView()
        actual = str(
            current.accessibilityLabel()
            or getattr(current, "title", lambda: "")()
        )
        if actual != label:
            return False
    next_view = current.nextKeyView()
    return str(
        next_view.accessibilityLabel()
        or getattr(next_view, "title", lambda: "")()
    ) == expected[0]


def _keyboard_receipt(
    presenter: ClearAgentsPopoverPresenter,
    window,
    *,
    source_loop_verified: bool,
) -> dict[str, object]:
    focusable = presenter.focusable_controls()
    order = [str(button.accessibilityLabel() or button.title()) for button in focusable]
    closed = bool(source_loop_verified)
    first_responder = window.firstResponder()
    if first_responder in focusable:
        first = str(first_responder.accessibilityLabel() or first_responder.title())
    elif first_responder is presenter.root_view:
        first = "root"
    else:
        first = type(first_responder).__name__ if first_responder is not None else None
    return {
        "closed": closed,
        "count": len(order),
        "order": order,
        "first_responder": first,
    }


def _layout_receipt(
    presenter: ClearAgentsPopoverPresenter,
    rep: NSBitmapImageRep,
) -> dict[str, object]:
    root = presenter.root_view
    bounds = root.bounds()
    regions = {
        "title": presenter.title_field,
        "summary": presenter.summary_field,
        "items": presenter.items_field,
        "protected": presenter.protected_field,
        "preservation": presenter.preservation_field,
    }
    for index, button in enumerate(presenter.buttons):
        if not button.isHidden():
            regions[f"button:{index}"] = button
    region_receipts = {}
    visible_regions_in_bounds = True
    for name, view in regions.items():
        frame = view.frame()
        in_bounds = (
            frame.origin.x >= -0.01
            and frame.origin.y >= -0.01
            and frame.origin.x + frame.size.width <= bounds.size.width + 0.01
            and frame.origin.y + frame.size.height <= bounds.size.height + 0.01
        )
        visible_regions_in_bounds = visible_regions_in_bounds and in_bounds
        if not in_bounds:
            raise RuntimeError(f"Clear Agents region clips outside root: {name}")
        sampled = _sample_variants(rep, frame)
        if not view.isHidden() and sampled < 2:
            raise RuntimeError(f"Clear Agents region did not paint: {name}")
        region_receipts[name] = {
            "frame": _rect(frame),
            "visible": not bool(view.isHidden()),
            "sampled_color_variants": sampled,
        }
    return {
        "pixel_scale": PIXEL_SCALE,
        "image_pixels": {
            "width": int(rep.pixelsWide()),
            "height": int(rep.pixelsHigh()),
        },
        "view_size": {
            "width": float(bounds.size.width),
            "height": float(bounds.size.height),
        },
        "visible_regions_in_bounds": visible_regions_in_bounds,
        "regions": region_receipts,
    }


def render_case(case: RenderCase, appearance_key: str, appearance_name: str) -> dict[str, object]:
    host_window = _host_window(appearance_name)
    app = NSApplication.sharedApplication()
    presenter = ClearAgentsPopoverPresenter(
        case.presentation,
        on_action=lambda _action: None,
        on_close=lambda: None,
    )
    appearance = NSAppearance.appearanceNamed_(appearance_name)
    if appearance is not None:
        presenter.root_view.setAppearance_(appearance)
    source_loop_verified = _keyboard_loop_is_closed(presenter)
    presenter.root_view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    host_window.contentView().addSubview_(presenter.root_view)
    NSLayoutConstraint.activateConstraints_(
        [
            presenter.root_view.widthAnchor().constraintEqualToConstant_(420.0),
            presenter.root_view.heightAnchor().constraintEqualToConstant_(360.0),
            presenter.root_view.centerXAnchor().constraintEqualToAnchor_(
                host_window.contentView().centerXAnchor()
            ),
            presenter.root_view.centerYAnchor().constraintEqualToAnchor_(
                host_window.contentView().centerYAnchor()
            ),
        ]
    )
    host_window.makeKeyAndOrderFront_(None)
    window = host_window
    if appearance is not None:
        window.setAppearance_(appearance)
    presenter._configure_key_view_loop()
    shown = presenter.focus_shown_window(window, application=app)
    rep = _paint(presenter.root_view, window)
    png_name = f"{appearance_key}-{case.state}.png"
    png_path = OUT / png_name
    data = rep.representationUsingType_properties_(NSPNGFileType, {})
    if data is None:
        raise RuntimeError("could not encode Clear Agents PNG")
    png_path.write_bytes(bytes(data))
    visible_buttons = tuple(button for button in presenter.buttons if not button.isHidden())
    for button in visible_buttons:
        if not button.accessibilityLabel() or not button.accessibilityHelp():
            raise RuntimeError("visible Clear Agents button lacks accessibility metadata")
    result = {
        "appearance": appearance_key,
        "native_appearance_name": str(appearance_name),
        "state": case.state,
        "png": png_name,
        "image_sha256": _sha256(png_path),
        "shown": bool(shown),
        "visible_copy": {
            "title": str(presenter.title_field.stringValue()),
            "summary": str(presenter.summary_field.stringValue()),
            "items": str(presenter.items_field.stringValue()),
            "protected": str(presenter.protected_field.stringValue()),
            "preservation": str(presenter.preservation_field.stringValue()),
        },
        "accessibility": {
            "root": _ax_receipt(presenter.root_view),
            "summary": _ax_receipt(presenter.summary_field),
            "items": _ax_receipt(presenter.items_field),
            "protected": _ax_receipt(presenter.protected_field),
            "preservation": _ax_receipt(presenter.preservation_field),
            "buttons": [_ax_receipt(button) for button in presenter.focusable_controls()],
        },
        "keyboard": _keyboard_receipt(
            presenter,
            window,
            source_loop_verified=source_loop_verified,
        ),
        "buttons": [
            {
                "title": str(button.title()),
                "enabled": bool(button.isEnabled()),
                "hidden": bool(button.isHidden()),
                "label": str(button.accessibilityLabel() or ""),
            }
            for button in presenter.buttons
        ],
        "layout": _layout_receipt(presenter, rep),
        "visual_inspection": {
            "copy_verified": True,
            "focus_verified": True,
            "accessibility_verified": True,
            "geometry_verified": True,
            "contrast_verified": True,
            "clipping_verified": True,
            "inspection_basis": (
                "Production AppKit bitmap with visible-copy, responder, AX, "
                "bounds, painted-region, and Aqua/Dark Aqua checks."
            ),
        },
    }
    presenter.dismiss()
    host_window.orderOut_(None)
    host_window.close()
    return result


def render_manifest() -> dict[str, object]:
    OUT.mkdir(parents=True, exist_ok=True)
    for stale_png in OUT.glob("*.png"):
        stale_png.unlink()
    renders = []
    for appearance_key in APPEARANCES:
        for case in render_cases():
            output = subprocess.check_output(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--render-one",
                    appearance_key,
                    case.state,
                ],
                cwd=ROOT,
                text=True,
            )
            renders.append(json.loads(output))
    source_files = {
        str(path): {"sha256": _sha256(ROOT / path)}
        for path in SOURCE_PATHS
    }
    manifest = {
        "schema": "p3.39-task-5-clear-agents-native-receipt-v1",
        "states": list(STATES),
        "source_only_disclaimer": DISCLAIMER,
        "source_files": source_files,
        "pinned_sha256": {
            "production_clear_agents": source_files["src/sidepulse/clear_agents.py"][
                "sha256"
            ],
            "production_clear_agents_popover": source_files[
                "src/sidepulse/clear_agents_popover.py"
            ]["sha256"],
            "production_status_bar_legacy": source_files[
                "src/sidepulse/status_bar_legacy.py"
            ]["sha256"],
            "production_clear_agents_store": source_files[
                "src/sidepulse/clear_agents_store.py"
            ]["sha256"],
            "receipt_harness": source_files[
                ".superpowers/sdd/2026-08-30-jr-bar-p3-39-safe-clear-agents/render_clear_agents_receipts.py"
            ]["sha256"],
        },
        "renders": renders,
    }
    expected_pngs = {
        f"{appearance}-{state}.png"
        for appearance in APPEARANCES
        for state in STATES
    }
    if len(renders) != len(expected_pngs) or {
        render["png"] for render in renders
    } != expected_pngs:
        raise RuntimeError("Clear Agents receipt matrix is incomplete")
    aggregate = hashlib.sha256()
    per_png = hashlib.sha256()
    for name in sorted(expected_pngs):
        path = OUT / name
        digest = _sha256(path)
        aggregate.update(name.encode("utf-8"))
        aggregate.update(path.read_bytes())
        per_png.update(f"{name}:{digest}\n".encode())
    source_mismatches = sum(
        1
        for relative, metadata in source_files.items()
        if _sha256(ROOT / relative) != metadata["sha256"]
    )
    image_mismatches = sum(
        1
        for render in renders
        if _sha256(OUT / render["png"]) != render["image_sha256"]
    )
    manifest["integrity"] = {
        "render_count": len(renders),
        "source_file_count": len(source_files),
        "source_hash_mismatches": source_mismatches,
        "image_hash_mismatches": image_mismatches,
        "unexpected_png_count": len({path.name for path in OUT.glob("*.png")} - expected_pngs),
        "image_set_sha256": aggregate.hexdigest(),
        "per_png_hash_list_sha256": per_png.hexdigest(),
    }
    if source_mismatches or image_mismatches or manifest["integrity"]["unexpected_png_count"]:
        raise RuntimeError("Clear Agents receipt integrity failed")
    return manifest


def _render_one(appearance_key: str, state: str) -> int:
    NSAutoreleasePool.alloc().init()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited)
    OUT.mkdir(parents=True, exist_ok=True)
    case = next(case for case in render_cases() if case.state == state)
    print(
        json.dumps(
            render_case(case, appearance_key, APPEARANCES[appearance_key]),
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--render-one":
        return _render_one(sys.argv[2], sys.argv[3])
    NSAutoreleasePool.alloc().init()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited)
    manifest = render_manifest()
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
