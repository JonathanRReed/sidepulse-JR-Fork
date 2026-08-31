"""Native Effect Studio and Preview Lab.

The window consumes the pure projections in :mod:`sidepulse.effect_studio`
and validated manifests from :mod:`sidepulse.effect_pack_store`. Explicit user
actions may persist data-only packs or scoped assignments. On-screen preview
rendering never executes pack content or talks to hardware. A separate,
explicit-consent adapter may temporarily submit one compiled preview through
the production hardware writer, without persisting settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSFont,
    NSLineBreakByTruncatingTail,
    NSLineBreakByWordWrapping,
    NSOnState,
    NSOpenPanel,
    NSPopUpButton,
    NSSavePanel,
    NSScrollView,
    NSSearchField,
    NSSlider,
    NSTableColumn,
    NSTableView,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSIndexSet, NSObject, NSTimer

from .effect_assignment_store import (
    EffectAssignmentCache,
    EffectAssignmentDocument,
    EffectAssignmentRecord,
    EffectAssignmentStore,
    EffectAssignmentStoreError,
)
from .effect_pack_store import (
    EffectPackStore,
    EffectPackStoreError,
    PackMutationReceipt,
)
from .effect_packs import EffectPackError, registry_with_pack
from .effect_registry import EFFECT_REGISTRY, EffectRegistry
from .effect_studio import (
    DEFAULT_SYNTHETIC_SCENARIOS,
    AssignmentScope,
    ColorVisionMode,
    GalleryPackProjection,
    GalleryRow,
    PolicyDecision,
    StudioSurface,
    SyntheticScenario,
    build_gallery_index,
    build_gallery_rows,
    build_surface_simulations,
    build_synthetic_timeline,
    project_why_effect,
)
from .effect_studio_physical_preview import PreviewReleaseReason
from .effect_studio_preview import (
    PREVIEW_SAMPLE_LIMIT,
    PreviewSample,
    deterministic_preview_samples,
)
from .product_identity import PRODUCT_DISPLAY_NAME
from .window_presentation import activate_app, present_window

WINDOW_WIDTH: Final = 1280.0
WINDOW_HEIGHT: Final = 820.0

SURFACE_TITLES: Final = {
    StudioSurface.SCREEN_BAR: "Screen Bar",
    StudioSurface.SIDEPULSE_PRO: "SidePulse Pro",
    StudioSurface.SIDEPULSE_DOT: "SidePulse Dot",
    StudioSurface.GLANCE_LIGHT: "Glance Light",
}

SCENARIO_TITLES: Final = {
    SyntheticScenario.ONE_AGENT: "One agent",
    SyntheticScenario.SEVERAL_AGENTS: "Several agents",
    SyntheticScenario.ASKING: "Asking",
    SyntheticScenario.FAILURE: "Failure",
    SyntheticScenario.HANDOFF: "Handoff",
    SyntheticScenario.COMPLETION: "Completion",
    SyntheticScenario.QUOTA_RESET: "Quota reset",
    SyntheticScenario.DND: "Do Not Disturb",
    SyntheticScenario.LOW_POWER: "Low power",
    SyntheticScenario.SLEEP: "Sleep",
    SyntheticScenario.LID_TRANSITION: "Lid transition",
    SyntheticScenario.REMOTE_FLEET_CHANGE: "Remote fleet change",
}

SCOPE_TITLES: Final = {
    AssignmentScope.GLOBAL: "Global default",
    AssignmentScope.SEMANTIC: "Semantic state",
    AssignmentScope.PROVIDER: "Provider",
    AssignmentScope.PROVIDER_INSTANCE: "Provider instance",
    AssignmentScope.PROJECT: "Project",
    AssignmentScope.DEVICE: "Device",
    AssignmentScope.SCENE: "Scene",
}

COLOR_VISION_TITLES: Final = {
    ColorVisionMode.STANDARD: "Standard color",
    ColorVisionMode.PROTANOPIA: "Protanopia simulation",
    ColorVisionMode.DEUTERANOPIA: "Deuteranopia simulation",
    ColorVisionMode.TRITANOPIA: "Tritanopia simulation",
    ColorVisionMode.MONOCHROMACY: "Monochrome simulation",
}

_DEFAULT_EFFECT_BY_FAMILY: Final = {
    "working": "pulse",
    "asking": "alert",
    "completion": "notification",
    "failure": "alert",
    "recovery": "notification",
    "notification": "notification",
    "quota": "none",
    "environment": "none",
    "idle": "none",
    "transition": "pulse",
}

@dataclass(frozen=True, slots=True)
class EffectStudioCatalog:
    """One bounded, validated catalog suitable for native presentation."""

    registry: EffectRegistry
    rows: tuple[GalleryRow, ...]
    packs: tuple[GalleryPackProjection, ...]
    store_status: str

    def __post_init__(self) -> None:
        if not (
            isinstance(self.registry, EffectRegistry)
            and type(self.rows) is tuple
            and all(type(row) is GalleryRow for row in self.rows)
            and type(self.packs) is tuple
            and all(type(pack) is GalleryPackProjection for pack in self.packs)
            and type(self.store_status) is str
            and self.store_status
        ):
            raise ValueError("invalid Effect Studio catalog")


def load_effect_studio_catalog(
    store: EffectPackStore | None = None,
) -> EffectStudioCatalog:
    """Read validated local packs and merge them into a preview-only registry."""

    selected_store = EffectPackStore() if store is None else store
    if not isinstance(selected_store, EffectPackStore):
        raise TypeError("store must be EffectPackStore")
    try:
        packs = selected_store.list()
        registry = EFFECT_REGISTRY
        for pack in packs:
            registry = registry_with_pack(registry, pack)
        projections = build_gallery_index(packs)
        status = (
            f"{len(projections)} installed data-only pack"
            f"{'s' if len(projections) != 1 else ''}"
            if projections
            else "No installed community packs"
        )
    except (EffectPackError, EffectPackStoreError, OSError, ValueError):
        registry = EFFECT_REGISTRY
        projections = ()
        status = "Installed gallery unavailable; built-in effects remain available"
    return EffectStudioCatalog(
        registry=registry,
        rows=build_gallery_rows(registry),
        packs=projections,
        store_status=status,
    )


def _label(
    text: str,
    frame,
    *,
    font_size: float = 13.0,
    bold: bool = False,
) -> NSTextField:
    field = NSTextField.labelWithString_(text)
    field.setFrame_(frame)
    field.setFont_(
        NSFont.boldSystemFontOfSize_(font_size)
        if bold
        else NSFont.systemFontOfSize_(font_size)
    )
    field.setLineBreakMode_(NSLineBreakByTruncatingTail)
    return field


def _multiline_label(text: str, frame, *, font_size: float = 12.0) -> NSTextField:
    field = _label(text, frame, font_size=font_size)
    field.setLineBreakMode_(NSLineBreakByWordWrapping)
    field.setUsesSingleLineMode_(False)
    field.setMaximumNumberOfLines_(0)
    return field


def _button(title: str, frame, target: object, action: str) -> NSButton:
    button = NSButton.alloc().initWithFrame_(frame)
    button.setTitle_(title)
    button.setBezelStyle_(NSBezelStyleRounded)
    button.setTarget_(target)
    button.setAction_(action)
    return button


class EffectStudioWindowController(NSObject):
    """Reusable native window backed by typed Studio projections and stores."""

    def init(self):
        self = objc.super(EffectStudioWindowController, self).init()
        if self is None:
            return None
        self.catalog = EffectStudioCatalog(
            EFFECT_REGISTRY,
            build_gallery_rows(EFFECT_REGISTRY),
            (),
            "Gallery not loaded",
        )
        self.filtered_rows = self.catalog.rows
        self.selected_effect_id = (
            self.filtered_rows[0].effect_id if self.filtered_rows else None
        )
        self.selected_scenario = SyntheticScenario.ONE_AGENT
        self.color_vision_mode = ColorVisionMode.STANDARD
        self.timeline_paused = True
        self.timeline_timer = None
        self.effect_pack_store = EffectPackStore()
        self.assignment_store = EffectAssignmentStore()
        self.assignment_cache = EffectAssignmentCache()
        self.assignment_document = EffectAssignmentDocument()
        self.assignment_scope = AssignmentScope.GLOBAL
        self.assignment_target_id: str | None = None
        self._assignment_draft_dirty = False
        self.selected_pack_id: str | None = None
        self.physical_preview = None
        self.physical_preview_devices = ()
        self._physical_preview_session_id: str | None = None
        self.surface_dot_views: dict[StudioSurface, tuple[NSView, ...]] = {}
        self.surface_before_dot_views: dict[StudioSurface, tuple[NSView, ...]] = {}
        self.surface_status_fields: dict[StudioSurface, NSTextField] = {}
        self._build_window()
        self._apply_catalog(self.catalog)
        return self

    def _build_window(self) -> None:
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0.0, 0.0), (WINDOW_WIDTH, WINDOW_HEIGHT)),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_(f"{PRODUCT_DISPLAY_NAME} Effect Studio")
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        self.window.center()

        root = NSView.alloc().initWithFrame_(
            ((0.0, 0.0), (WINDOW_WIDTH, WINDOW_HEIGHT))
        )
        root.setAccessibilityLabel_("Effect Studio and Preview Lab")
        self.window.setContentView_(root)

        title = _label(
            "Effect Studio",
            ((20.0, 774.0), (300.0, 28.0)),
            font_size=22.0,
            bold=True,
        )
        root.addSubview_(title)
        subtitle = _label(
            "Browse and compare safe synthetic effects.",
            ((20.0, 752.0), (294.0, 20.0)),
            font_size=12.0,
        )
        root.addSubview_(subtitle)

        self.search_field = NSSearchField.alloc().initWithFrame_(
            ((20.0, 714.0), (294.0, 28.0))
        )
        self.search_field.setPlaceholderString_("Search effects and meanings")
        self.search_field.setDelegate_(self)
        self.search_field.setAccessibilityLabel_("Search effect gallery")
        root.addSubview_(self.search_field)

        self.table_view = NSTableView.alloc().init()
        self.table_view.setHeaderView_(None)
        self.table_view.setRowHeight_(28.0)
        column = NSTableColumn.alloc().initWithIdentifier_("effect")
        column.setWidth_(292.0)
        self.table_view.addTableColumn_(column)
        self.table_view.setDataSource_(self)
        self.table_view.setDelegate_(self)
        self.table_view.setAccessibilityLabel_("Effect gallery")

        self.gallery_scroll = NSScrollView.alloc().initWithFrame_(
            ((20.0, 284.0), (294.0, 420.0))
        )
        self.gallery_scroll.setDocumentView_(self.table_view)
        self.gallery_scroll.setHasVerticalScroller_(True)
        root.addSubview_(self.gallery_scroll)

        pack_heading = _label(
            "Data-only community gallery",
            ((20.0, 254.0), (294.0, 20.0)),
            font_size=12.0,
            bold=True,
        )
        root.addSubview_(pack_heading)
        self.pack_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            ((20.0, 220.0), (294.0, 28.0)),
            False,
        )
        self.pack_popup.setTarget_(self)
        self.pack_popup.setAction_("packChanged:")
        self.pack_popup.setAccessibilityLabel_("Installed effect packs")
        root.addSubview_(self.pack_popup)
        self.pack_info_field = _multiline_label(
            "",
            ((20.0, 118.0), (294.0, 96.0)),
            font_size=11.0,
        )
        self.pack_info_field.setSelectable_(True)
        root.addSubview_(self.pack_info_field)
        pack_action_frames = (
            ((20.0, 80.0), (140.0, 28.0)),
            ((168.0, 80.0), (140.0, 28.0)),
            ((20.0, 44.0), (140.0, 28.0)),
            ((168.0, 44.0), (140.0, 28.0)),
        )
        self.pack_action_buttons = tuple(
            _button(
                title,
                pack_action_frames[index],
                self,
                action,
            )
            for index, (title, action) in enumerate(
                (
                    ("Import", "importPack:"),
                    ("Export", "exportPack:"),
                    ("Duplicate", "duplicatePack:"),
                    ("Rename", "renamePack:"),
                )
            )
        )
        for button in self.pack_action_buttons:
            root.addSubview_(button)

        scenario_label = _label(
            "Synthetic scenario",
            ((338.0, 750.0), (140.0, 20.0)),
            font_size=12.0,
            bold=True,
        )
        root.addSubview_(scenario_label)
        self.scenario_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            ((338.0, 716.0), (190.0, 28.0)),
            False,
        )
        self.scenario_popup.addItemsWithTitles_(
            [SCENARIO_TITLES[scenario] for scenario in DEFAULT_SYNTHETIC_SCENARIOS]
        )
        self.scenario_popup.setTarget_(self)
        self.scenario_popup.setAction_("scenarioChanged:")
        self.scenario_popup.setAccessibilityLabel_("Synthetic preview scenario")
        root.addSubview_(self.scenario_popup)

        self.reduce_motion_button = NSButton.alloc().initWithFrame_(
            ((1042.0, 716.0), (210.0, 28.0))
        )
        self.reduce_motion_button.setButtonType_(NSButtonTypeSwitch)
        self.reduce_motion_button.setTitle_("Preview Reduce Motion")
        self.reduce_motion_button.setTarget_(self)
        self.reduce_motion_button.setAction_("reduceMotionChanged:")
        self.reduce_motion_button.setAccessibilityHelp_(
            "Uses each effect's declared reduced-motion fallback."
        )
        root.addSubview_(self.reduce_motion_button)

        self.color_vision_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            ((548.0, 716.0), (220.0, 28.0)),
            False,
        )
        self.color_vision_popup.addItemsWithTitles_(
            [COLOR_VISION_TITLES[mode] for mode in ColorVisionMode]
        )
        self.color_vision_popup.setTarget_(self)
        self.color_vision_popup.setAction_("colorVisionChanged:")
        self.color_vision_popup.setAccessibilityLabel_("Color vision simulation")
        root.addSubview_(self.color_vision_popup)

        self.timeline_slider = NSSlider.alloc().initWithFrame_(
            ((338.0, 680.0), (430.0, 24.0))
        )
        self.timeline_slider.setMinValue_(0.0)
        self.timeline_slider.setMaxValue_(22.0)
        self.timeline_slider.setContinuous_(True)
        self.timeline_slider.setTarget_(self)
        self.timeline_slider.setAction_("timelineScrubbed:")
        self.timeline_slider.setAccessibilityLabel_("Synthetic timeline cursor")
        root.addSubview_(self.timeline_slider)
        self.pause_button = _button(
            "Pause",
            ((780.0, 678.0), (72.0, 28.0)),
            self,
            "pauseTimeline:",
        )
        self.replay_button = _button(
            "Replay",
            ((858.0, 678.0), (72.0, 28.0)),
            self,
            "replayTimeline:",
        )
        root.addSubview_(self.pause_button)
        root.addSubview_(self.replay_button)

        self.timeline_field = _label(
            "",
            ((912.0, 684.0), (340.0, 20.0)),
            font_size=11.0,
        )
        self.timeline_field.setAlignment_(2)
        root.addSubview_(self.timeline_field)

        preview_heading = _label(
            "Side-by-side surface preview",
            ((338.0, 646.0), (300.0, 22.0)),
            font_size=14.0,
            bold=True,
        )
        root.addSubview_(preview_heading)
        self.before_label = _label(
            "Before · saved or default",
            ((828.0, 646.0), (190.0, 20.0)),
            font_size=11.0,
        )
        self.after_label = _label(
            "After · saved",
            ((1032.0, 646.0), (220.0, 20.0)),
            font_size=11.0,
        )
        root.addSubview_(self.before_label)
        root.addSubview_(self.after_label)
        self._build_surface_cards(root)

        self.effect_heading = _label(
            "",
            ((338.0, 478.0), (914.0, 26.0)),
            font_size=18.0,
            bold=True,
        )
        root.addSubview_(self.effect_heading)
        self.effect_detail_field = _multiline_label(
            "",
            ((338.0, 374.0), (440.0, 98.0)),
            font_size=12.0,
        )
        self.effect_detail_field.setSelectable_(True)
        root.addSubview_(self.effect_detail_field)
        self.why_field = _multiline_label(
            "",
            ((796.0, 374.0), (456.0, 98.0)),
            font_size=12.0,
        )
        self.why_field.setSelectable_(True)
        root.addSubview_(self.why_field)

        assignment_heading = _label(
            "Assignment",
            ((338.0, 338.0), (160.0, 22.0)),
            font_size=14.0,
            bold=True,
        )
        root.addSubview_(assignment_heading)
        self.scope_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            ((338.0, 302.0), (180.0, 28.0)),
            False,
        )
        self.scope_popup.addItemsWithTitles_(
            [SCOPE_TITLES[scope] for scope in AssignmentScope]
        )
        self.scope_popup.setTarget_(self)
        self.scope_popup.setAction_("assignmentScopeChanged:")
        root.addSubview_(self.scope_popup)
        self.assignment_target_field = NSTextField.alloc().initWithFrame_(
            ((530.0, 302.0), (250.0, 28.0))
        )
        self.assignment_target_field.setPlaceholderString_(
            "No target for global default"
        )
        self.assignment_target_field.setDelegate_(self)
        self.assignment_target_field.setEnabled_(False)
        root.addSubview_(self.assignment_target_field)
        self.save_assignment_button = _button(
            "Save assignment",
            ((794.0, 302.0), (132.0, 28.0)),
            self,
            "saveAssignment:",
        )
        self.reset_assignment_button = _button(
            "Reset scope",
            ((936.0, 302.0), (112.0, 28.0)),
            self,
            "resetAssignment:",
        )
        self.restore_defaults_button = _button(
            "Restore defaults",
            ((1058.0, 302.0), (190.0, 28.0)),
            self,
            "restoreDefaults:",
        )
        root.addSubview_(self.save_assignment_button)
        root.addSubview_(self.reset_assignment_button)
        root.addSubview_(self.restore_defaults_button)
        self.assignment_status_field = _multiline_label(
            "Using default",
            ((338.0, 250.0), (914.0, 44.0)),
            font_size=11.0,
        )
        root.addSubview_(self.assignment_status_field)
        assignment_help = _multiline_label(
            "Targets are bounded identifiers. Provider instance uses "
            "provider:instance. Asking and failure always retain the alert safeguard.",
            ((338.0, 204.0), (914.0, 40.0)),
            font_size=11.0,
        )
        root.addSubview_(assignment_help)

        accessibility_heading = _label(
            "Accessibility and safety",
            ((338.0, 176.0), (260.0, 22.0)),
            font_size=14.0,
            bold=True,
        )
        root.addSubview_(accessibility_heading)
        self.accessibility_field = _multiline_label(
            "",
            ((338.0, 82.0), (914.0, 88.0)),
            font_size=12.0,
        )
        self.accessibility_field.setSelectable_(True)
        root.addSubview_(self.accessibility_field)

        self.physical_device_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            ((338.0, 38.0), (180.0, 30.0)),
            False,
        )
        self.physical_device_popup.setTarget_(self)
        self.physical_device_popup.setAction_("physicalPreviewDeviceChanged:")
        self.physical_device_popup.setAccessibilityLabel_(
            "Physical preview device"
        )
        root.addSubview_(self.physical_device_popup)

        self.physical_preview_consent = NSButton.alloc().initWithFrame_(
            ((526.0, 38.0), (220.0, 30.0))
        )
        self.physical_preview_consent.setButtonType_(NSButtonTypeSwitch)
        self.physical_preview_consent.setTitle_("Allow temporary device preview")
        self.physical_preview_consent.setTarget_(self)
        self.physical_preview_consent.setAction_(
            "physicalPreviewConsentChanged:"
        )
        self.physical_preview_consent.setAccessibilityHelp_(
            "Writes only this preview through the existing hardware writer, "
            "then restores the committed output within 30 seconds."
        )
        root.addSubview_(self.physical_preview_consent)

        self.physical_preview_button = _button(
            "Preview on hardware",
            ((754.0, 38.0), (214.0, 30.0)),
            self,
            "physicalPreviewRequested:",
        )
        self.physical_preview_button.setEnabled_(False)
        self.physical_preview_button.setToolTip_(
            "Requires a connected supported device and explicit consent."
        )
        root.addSubview_(self.physical_preview_button)
        self.physical_preview_status = _multiline_label(
            "Physical preview is unavailable. Previewing is not saved.",
            ((980.0, 28.0), (166.0, 48.0)),
            font_size=11.0,
        )
        root.addSubview_(self.physical_preview_status)

        close_button = _button(
            "Close",
            ((1156.0, 28.0), (96.0, 30.0)),
            self,
            "closeStudio:",
        )
        root.addSubview_(close_button)

    def _build_surface_cards(self, root: NSView) -> None:
        card_width = 220.0
        card_gap = 10.0
        origin_x = 338.0
        for index, surface in enumerate(StudioSurface):
            x = origin_x + index * (card_width + card_gap)
            card = NSView.alloc().initWithFrame_(((x, 510.0), (card_width, 126.0)))
            card.setWantsLayer_(True)
            card.layer().setCornerRadius_(8.0)
            card.layer().setBorderWidth_(1.0)
            card.layer().setBorderColor_(NSColor.separatorColor().CGColor())
            root.addSubview_(card)
            heading = _label(
                SURFACE_TITLES[surface],
                ((10.0, 98.0), (200.0, 20.0)),
                font_size=13.0,
                bold=True,
            )
            card.addSubview_(heading)
            status = _multiline_label(
                "",
                ((10.0, 76.0), (200.0, 20.0)),
                font_size=10.0,
            )
            card.addSubview_(status)
            self.surface_status_fields[surface] = status
            dot_count = min(
                PREVIEW_SAMPLE_LIMIT,
                24 if surface in {StudioSurface.SCREEN_BAR, StudioSurface.SIDEPULSE_PRO} else 2,
            )
            if surface is StudioSurface.GLANCE_LIGHT:
                dot_count = 1
            dot_size = 8.0 if dot_count > 2 else 14.0
            gap = 3.0
            total_width = dot_count * dot_size + max(0, dot_count - 1) * gap
            start_x = max(52.0, (card_width - total_width) / 2.0)
            before_caption = _label(
                "Before",
                ((10.0, 48.0), (42.0, 16.0)),
                font_size=9.0,
            )
            after_caption = _label(
                "After",
                ((10.0, 18.0), (42.0, 16.0)),
                font_size=9.0,
            )
            card.addSubview_(before_caption)
            card.addSubview_(after_caption)
            rows = []
            for y in (50.0, 20.0):
                dots: list[NSView] = []
                for dot_index in range(dot_count):
                    dot = NSView.alloc().initWithFrame_(
                        (
                            (start_x + dot_index * (dot_size + gap), y),
                            (dot_size, dot_size),
                        )
                    )
                    dot.setWantsLayer_(True)
                    dot.layer().setCornerRadius_(dot_size / 2.0)
                    card.addSubview_(dot)
                    dots.append(dot)
                rows.append(tuple(dots))
            self.surface_before_dot_views[surface] = rows[0]
            self.surface_dot_views[surface] = rows[1]

    def open(
        self,
        *,
        store: EffectPackStore | None = None,
        assignment_store: EffectAssignmentStore | None = None,
        assignment_cache: EffectAssignmentCache | None = None,
        physical_preview=None,
        show: bool = True,
    ) -> NSWindow:
        self.effect_pack_store = EffectPackStore() if store is None else store
        self.assignment_store = (
            EffectAssignmentStore() if assignment_store is None else assignment_store
        )
        if not isinstance(self.effect_pack_store, EffectPackStore) or not isinstance(
            self.assignment_store, EffectAssignmentStore
        ):
            raise TypeError("Studio stores must use typed store owners")
        if assignment_cache is None:
            restored = self.assignment_store.load()
            self.assignment_cache = EffectAssignmentCache(restored.document)
        elif isinstance(assignment_cache, EffectAssignmentCache):
            self.assignment_cache = assignment_cache
        else:
            raise TypeError("assignment_cache must be EffectAssignmentCache")
        self.assignment_document = self.assignment_cache.snapshot()
        self._release_physical_preview(PreviewReleaseReason.REPLACED)
        self.physical_preview = physical_preview
        self._reload_physical_preview_devices()
        catalog = load_effect_studio_catalog(self.effect_pack_store)
        self.assignment_cache.replace(
            self.assignment_document,
            registry=catalog.registry,
        )
        self._apply_catalog(catalog)
        if show:
            present_window(self.window)
            activate_app()
        return self.window

    def _reload_physical_preview_devices(self) -> None:
        self.physical_device_popup.removeAllItems()
        runtime = self.physical_preview
        try:
            devices = tuple(runtime.devices()) if runtime is not None else ()
        except Exception:
            devices = ()
        self.physical_preview_devices = devices
        if devices:
            self.physical_device_popup.addItemsWithTitles_(
                [device.name for device in devices]
            )
            self.physical_device_popup.setEnabled_(True)
            self.physical_preview_consent.setEnabled_(True)
        else:
            self.physical_device_popup.addItemWithTitle_(
                "No connected SidePulse device"
            )
            self.physical_device_popup.setEnabled_(False)
            self.physical_preview_consent.setEnabled_(False)
            self.physical_preview_consent.setState_(0)
        self._refresh_physical_preview_controls()

    def _selected_physical_preview_device(self):
        index = self.physical_device_popup.indexOfSelectedItem()
        if not 0 <= index < len(self.physical_preview_devices):
            return None
        return self.physical_preview_devices[index]

    def _refresh_physical_preview_controls(self) -> None:
        if self._physical_preview_session_id is not None:
            self.physical_preview_button.setTitle_("Stop preview")
            self.physical_preview_button.setEnabled_(True)
            return
        self.physical_preview_button.setTitle_("Preview on hardware")
        runtime = self.physical_preview
        device = self._selected_physical_preview_device()
        row = self._selected_row()
        if runtime is None or device is None or row is None:
            self.physical_preview_button.setEnabled_(False)
            self.physical_preview_status.setStringValue_(
                "Connect a supported device. Previewing is not saved."
            )
            return
        try:
            availability = runtime.availability(
                row.effect_id,
                device.preview_device_id,
                reduce_motion=self._reduce_motion_enabled(),
                registry=self.catalog.registry,
            )
        except Exception:
            self.physical_preview_button.setEnabled_(False)
            self.physical_preview_status.setStringValue_(
                "Physical preview unavailable. Previewing is not saved."
            )
            return
        consent = self.physical_preview_consent.state() == NSOnState
        self.physical_preview_button.setEnabled_(availability.available and consent)
        self.physical_preview_status.setStringValue_(
            "Ready. Previewing is not saved."
            if availability.available
            else f"{availability.reason}. Previewing is not saved."
        )

    def _release_physical_preview(self, reason: PreviewReleaseReason) -> bool:
        runtime = self.physical_preview
        active = self._physical_preview_session_id is not None
        try:
            released = bool(runtime.release(reason)) if runtime is not None else False
        except Exception:
            released = False
        self._physical_preview_session_id = None
        if hasattr(self, "physical_preview_consent"):
            self.physical_preview_consent.setState_(0)
            self.physical_preview_status.setStringValue_(
                "Preview ended. Committed output restored."
                if active or released
                else "Previewing is not saved."
            )
            self._refresh_physical_preview_controls()
        return active or released

    def _apply_catalog(self, catalog: EffectStudioCatalog) -> None:
        if type(catalog) is not EffectStudioCatalog:
            raise TypeError("catalog must be EffectStudioCatalog")
        previous = self.selected_effect_id
        self.catalog = catalog
        self.filtered_rows = catalog.rows
        self.selected_effect_id = (
            previous
            if previous is not None
            and any(row.effect_id == previous for row in self.filtered_rows)
            else (self.filtered_rows[0].effect_id if self.filtered_rows else None)
        )
        self.table_view.reloadData()
        self._select_effect_id(self.selected_effect_id)
        self._reload_pack_popup()
        self._refresh_detail()

    def _select_effect_id(self, effect_id: str | None) -> None:
        index = next(
            (
                index
                for index, row in enumerate(self.filtered_rows)
                if row.effect_id == effect_id
            ),
            -1,
        )
        if index < 0:
            self.table_view.deselectAll_(None)
            return
        self.table_view.selectRowIndexes_byExtendingSelection_(
            NSIndexSet.indexSetWithIndex_(index),
            False,
        )
        self.table_view.scrollRowToVisible_(index)

    def _reload_pack_popup(self) -> None:
        self.pack_popup.removeAllItems()
        self.pack_popup.addItemWithTitle_("Built-in JR Bar effects")
        self.pack_popup.addItemsWithTitles_(
            [pack.name for pack in self.catalog.packs]
        )
        index = next(
            (
                index + 1
                for index, pack in enumerate(self.catalog.packs)
                if pack.pack_id == self.selected_pack_id
            ),
            0,
        )
        if index == 0:
            self.selected_pack_id = None
        self.pack_popup.selectItemAtIndex_(index)
        self._refresh_pack_info()

    def _refresh_pack_info(self) -> None:
        index = self.pack_popup.indexOfSelectedItem()
        if index <= 0:
            self.selected_pack_id = None
            self.pack_info_field.setStringValue_(
                f"{self.catalog.store_status}. Built-in effects are part of the "
                f"{PRODUCT_DISPLAY_NAME} source tree. Installed packs are validated "
                "JSON data and never execute code."
            )
            for button in self.pack_action_buttons[1:]:
                button.setEnabled_(False)
            return
        pack = self.catalog.packs[index - 1]
        self.selected_pack_id = pack.pack_id
        license_text = (
            f"{pack.license_label} ({pack.license_spdx_id})"
            if pack.license_label and pack.license_spdx_id
            else "No license metadata supplied"
        )
        links = tuple(
            value for value in (pack.source_url, pack.attribution_url) if value
        )
        link_text = "\n".join(links) if links else "No source or attribution URL"
        self.pack_info_field.setStringValue_(
            f"{pack.effect_count} effects · {license_text}\n{link_text}"
        )
        for button in self.pack_action_buttons[1:]:
            button.setEnabled_(True)

    def _selected_row(self) -> GalleryRow | None:
        return next(
            (
                row
                for row in self.filtered_rows
                if row.effect_id == self.selected_effect_id
            ),
            None,
        )

    def _reduce_motion_enabled(self) -> bool:
        return self.reduce_motion_button.state() == NSOnState

    def _current_assignment_target(self) -> str | None:
        if self.assignment_scope is AssignmentScope.GLOBAL:
            return None
        return self.assignment_target_id

    def _direct_assignment(self) -> EffectAssignmentRecord | None:
        return self.assignment_document.assignment_for(
            self.assignment_scope,
            self._current_assignment_target(),
        )

    def _default_effect_id(self, row: GalleryRow) -> str:
        family = row.semantic_family.value
        if (
            self.assignment_scope is AssignmentScope.SEMANTIC
            and self.assignment_target_id in _DEFAULT_EFFECT_BY_FAMILY
        ):
            family = self.assignment_target_id
        candidate = _DEFAULT_EFFECT_BY_FAMILY.get(family, "none")
        if self.catalog.registry.get(candidate) is None:
            return row.effect_id
        return candidate

    def _comparison_before_row(self, selected: GalleryRow) -> GalleryRow:
        assignment = self._direct_assignment()
        effect_id = (
            assignment.effect_id
            if assignment is not None
            else self._default_effect_id(selected)
        )
        return next(
            (row for row in self.catalog.rows if row.effect_id == effect_id),
            selected,
        )

    @staticmethod
    def _set_dot_samples(
        dots: tuple[NSView, ...],
        samples: tuple[PreviewSample, ...],
    ) -> None:
        for dot, sample in zip(dots, samples, strict=True):
            color = NSColor.colorWithCalibratedHue_saturation_brightness_alpha_(
                sample.hue,
                sample.saturation,
                sample.brightness,
                1.0,
            )
            dot.layer().setBackgroundColor_(color.CGColor())

    def _refresh_assignment_status(self) -> None:
        assignment = self._direct_assignment()
        if assignment is None:
            status = (
                "Unsaved changes"
                if self._assignment_draft_dirty
                else "Using default"
            )
        elif assignment.effect_id == self.selected_effect_id:
            status = "Saved"
        else:
            status = "Unsaved changes"
        self.assignment_status_field.setStringValue_(status)
        self.after_label.setStringValue_(f"After · {status.lower()}")

    def _refresh_detail(self) -> None:
        row = self._selected_row()
        if row is None:
            self.effect_heading.setStringValue_("No matching effects")
            self.effect_detail_field.setStringValue_(
                "Change the search to browse the validated effect registry."
            )
            self.why_field.setStringValue_("")
            self.accessibility_field.setStringValue_("")
            for surface in StudioSurface:
                self.surface_status_fields[surface].setStringValue_("No selection")
                for dot in (
                    *self.surface_before_dot_views[surface],
                    *self.surface_dot_views[surface],
                ):
                    dot.layer().setBackgroundColor_(
                        NSColor.disabledControlTextColor().CGColor()
                    )
            self._refresh_physical_preview_controls()
            return

        reduce_motion = self._reduce_motion_enabled()
        before_row = self._comparison_before_row(row)
        before_simulations = build_surface_simulations(
            before_row.effect_id,
            self.catalog.registry,
            reduce_motion=reduce_motion,
        )
        after_simulations = build_surface_simulations(
            row.effect_id,
            self.catalog.registry,
            reduce_motion=reduce_motion,
        )
        timeline = build_synthetic_timeline(
            cursor_seconds=self.timeline_slider.doubleValue(),
            paused=self.timeline_paused,
            reduce_motion=reduce_motion,
            color_vision_mode=self.color_vision_mode,
        )
        selected_event = min(
            timeline.events,
            key=lambda event: abs(event.offset_seconds - timeline.cursor_seconds),
        )
        self.timeline_field.setStringValue_(
            f"{'Paused' if timeline.paused else 'Playing'} at "
            f"{timeline.cursor_seconds:.1f}s of "
            f"{timeline.duration_seconds:.0f}s · {selected_event.agent_count} "
            f"agent{'s' if selected_event.agent_count != 1 else ''}"
        )

        for before_simulation, after_simulation in zip(
            before_simulations,
            after_simulations,
            strict=True,
        ):
            rendered_suffix = (
                f"\nReduce Motion: {after_simulation.rendered_effect_id}"
                if after_simulation.rendered_effect_id
                != after_simulation.requested_effect_id
                else ""
            )
            self.surface_status_fields[after_simulation.surface].setStringValue_(
                (
                    f"Supported · {after_simulation.led_count} LEDs"
                    if after_simulation.supported
                    else "Not supported by this effect"
                )
                + rendered_suffix
            )
            before_samples = deterministic_preview_samples(
                before_row,
                before_simulation,
                self.selected_scenario,
                color_vision_mode=self.color_vision_mode,
            )
            after_samples = deterministic_preview_samples(
                row,
                after_simulation,
                self.selected_scenario,
                color_vision_mode=self.color_vision_mode,
            )
            self._set_dot_samples(
                self.surface_before_dot_views[before_simulation.surface],
                before_samples,
            )
            self._set_dot_samples(
                self.surface_dot_views[after_simulation.surface],
                after_samples,
            )

        surfaces = ", ".join(SURFACE_TITLES[value] for value in row.supported_surfaces)
        self.effect_heading.setStringValue_(row.label)
        self.effect_detail_field.setStringValue_(
            f"{row.purpose}\n\nWhen it runs: {row.when_it_runs}\n"
            f"Family: {row.semantic_family.value.title()}\n"
            f"Surfaces: {surfaces or 'No physical surface declared'}"
        )
        decisions = (
            (PolicyDecision.REDUCE_MOTION_SUBSTITUTE,)
            if reduce_motion
            else (PolicyDecision.ROUTE_WINNER,)
        )
        why = project_why_effect(
            row.effect_id,
            priority=50,
            policy_decisions=decisions,
            registry=self.catalog.registry,
        )
        self.why_field.setStringValue_(
            "Why this effect?\n"
            f"Meaning: {why.meaning}\n"
            f"Synthetic source: {self.selected_scenario.value}\n"
            f"Priority: {why.priority}\n"
            f"Policy: {', '.join(value.value for value in why.policy_decisions)}\n"
            "Freshness: synthetic preview, not a live signal"
        )
        fallback = (
            "No motion substitution needed"
            if row.reduce_motion_effect_id == row.effect_id
            else f"{row.label} → {row.reduce_motion_effect_id}"
        )
        parameters = ", ".join(row.parameters) if row.parameters else "None"
        self.accessibility_field.setStringValue_(
            f"Reduce Motion: {fallback}. Status and meaning remain written in text, "
            "so color is never the only cue. Energy: "
            f"{row.energy}. Safety class: {row.safety}. Parameters: {parameters}.\n"
            "Community entries are schema-validated, accessibility-declared, "
            "license-visible data only. No pack code or network behavior is loaded."
        )
        self._refresh_assignment_status()
        self._refresh_physical_preview_controls()

    def select_effect(self, effect_id: str) -> None:
        if type(effect_id) is not str or not any(
            row.effect_id == effect_id for row in self.catalog.rows
        ):
            raise ValueError("effect is not in the Studio registry")
        self.selected_effect_id = effect_id
        self._assignment_draft_dirty = True
        self._select_effect_id(effect_id)
        self._refresh_detail()

    def set_assignment_draft(
        self,
        scope: AssignmentScope,
        target_id: str | None,
    ) -> None:
        if type(scope) is not AssignmentScope:
            raise TypeError("scope must be AssignmentScope")
        if self.selected_effect_id is None:
            raise ValueError("an effect must be selected")
        assignment = EffectAssignmentRecord.create(
            self.selected_effect_id,
            scope,
            target_id,
            registry=self.catalog.registry,
        )
        self.assignment_scope = assignment.scope
        self.assignment_target_id = assignment.target_id
        self.scope_popup.selectItemAtIndex_(tuple(AssignmentScope).index(scope))
        self.assignment_target_field.setEnabled_(scope is not AssignmentScope.GLOBAL)
        self.assignment_target_field.setStringValue_(assignment.target_id or "")
        self.assignment_target_field.setPlaceholderString_(
            "No target for global default"
            if scope is AssignmentScope.GLOBAL
            else "Required bounded target identifier"
        )
        saved = self._direct_assignment()
        if saved is not None and not self._assignment_draft_dirty:
            self.selected_effect_id = saved.effect_id
            self._select_effect_id(saved.effect_id)
        self._refresh_detail()

    def _validated_assignment_target(
        self,
        *,
        effect_id: str | None = None,
    ) -> str | None:
        selected_effect_id = effect_id or self.selected_effect_id
        if selected_effect_id is None:
            raise EffectAssignmentStoreError("an effect must be selected")
        target = (
            None
            if self.assignment_scope is AssignmentScope.GLOBAL
            else str(self.assignment_target_field.stringValue())
        )
        return EffectAssignmentRecord.create(
            selected_effect_id,
            self.assignment_scope,
            target,
            registry=self.catalog.registry,
        ).target_id

    def _persist_assignments(self, document: EffectAssignmentDocument) -> None:
        self.assignment_store.save(document)
        self.assignment_document = document
        self.assignment_cache.replace(document, registry=self.catalog.registry)

    def save_assignment(self) -> EffectAssignmentRecord:
        if self.selected_effect_id is None:
            raise EffectAssignmentStoreError("an effect must be selected")
        target = self._validated_assignment_target()
        assignment = EffectAssignmentRecord.create(
            self.selected_effect_id,
            self.assignment_scope,
            target,
            registry=self.catalog.registry,
        )
        self._persist_assignments(
            self.assignment_document.with_assignment(assignment)
        )
        self.assignment_target_id = assignment.target_id
        self._assignment_draft_dirty = False
        self._refresh_detail()
        return assignment

    def reset_current_assignment(self) -> EffectAssignmentDocument:
        target = self._validated_assignment_target(effect_id="alert")
        document = self.assignment_document.without_assignment(
            self.assignment_scope,
            target,
        )
        self._persist_assignments(document)
        self.assignment_target_id = target
        self._assignment_draft_dirty = False
        row = self._selected_row()
        if row is not None:
            default_effect = self._default_effect_id(row)
            if any(
                candidate.effect_id == default_effect
                for candidate in self.catalog.rows
            ):
                self.selected_effect_id = default_effect
                self._select_effect_id(default_effect)
        self._refresh_detail()
        return document

    def restore_default_assignments(self) -> EffectAssignmentDocument:
        document = EffectAssignmentDocument()
        self._persist_assignments(document)
        self.assignment_scope = AssignmentScope.GLOBAL
        self.assignment_target_id = None
        self.scope_popup.selectItemAtIndex_(0)
        self.assignment_target_field.setStringValue_("")
        self.assignment_target_field.setEnabled_(False)
        self._assignment_draft_dirty = False
        row = self._selected_row()
        if row is not None:
            default_effect = self._default_effect_id(row)
            if any(
                candidate.effect_id == default_effect
                for candidate in self.catalog.rows
            ):
                self.selected_effect_id = default_effect
                self._select_effect_id(default_effect)
        self._refresh_detail()
        return document

    def scrub_timeline(self, cursor_seconds: float) -> None:
        if isinstance(cursor_seconds, bool) or not isinstance(
            cursor_seconds,
            (int, float),
        ):
            raise TypeError("timeline cursor must be a number")
        cursor = float(cursor_seconds)
        if not self.timeline_slider.minValue() <= cursor <= self.timeline_slider.maxValue():
            raise ValueError("timeline cursor is out of bounds")
        self._release_physical_preview(PreviewReleaseReason.SELECTION_CHANGED)
        self.pause_timeline()
        self.timeline_slider.setDoubleValue_(cursor)
        scenario_index = min(
            len(DEFAULT_SYNTHETIC_SCENARIOS) - 1,
            max(0, int(round(cursor / 2.0))),
        )
        self.selected_scenario = DEFAULT_SYNTHETIC_SCENARIOS[scenario_index]
        self.scenario_popup.selectItemAtIndex_(scenario_index)
        self._refresh_detail()

    def set_color_vision_mode(self, mode: ColorVisionMode) -> None:
        if type(mode) is not ColorVisionMode:
            raise TypeError("mode must be ColorVisionMode")
        self.color_vision_mode = mode
        self.color_vision_popup.selectItemAtIndex_(tuple(ColorVisionMode).index(mode))
        self._refresh_detail()

    def pause_timeline(self) -> None:
        timer = self.timeline_timer
        if timer is not None:
            timer.invalidate()
            self.timeline_timer = None
        self.timeline_paused = True
        self._refresh_detail()

    def replay_timeline(self) -> None:
        self._release_physical_preview(PreviewReleaseReason.SELECTION_CHANGED)
        self.pause_timeline()
        self.timeline_slider.setDoubleValue_(0.0)
        self.selected_scenario = DEFAULT_SYNTHETIC_SCENARIOS[0]
        self.scenario_popup.selectItemAtIndex_(0)
        self.timeline_paused = False
        self.timeline_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.25,
            self,
            "advanceTimeline:",
            None,
            True,
        )
        self._refresh_detail()

    def _reload_catalog_after_pack_mutation(self, pack_id: str | None) -> None:
        self.selected_pack_id = pack_id
        catalog = load_effect_studio_catalog(self.effect_pack_store)
        self.assignment_cache.replace(
            self.assignment_document,
            registry=catalog.registry,
        )
        self._apply_catalog(catalog)

    def select_pack(self, pack_id: str | None) -> None:
        if pack_id is not None and not any(
            pack.pack_id == pack_id for pack in self.catalog.packs
        ):
            raise ValueError("effect pack is not installed")
        self.selected_pack_id = pack_id
        self._reload_pack_popup()

    def import_pack(self, source: Path) -> PackMutationReceipt:
        receipt = self.effect_pack_store.install(Path(source))
        self._reload_catalog_after_pack_mutation(
            receipt.pack_id if receipt.accepted else self.selected_pack_id
        )
        return receipt

    def export_selected_pack(self, target: Path) -> Path:
        if self.selected_pack_id is None:
            raise EffectPackStoreError("select an installed pack to export")
        return self.effect_pack_store.export(self.selected_pack_id, Path(target))

    def duplicate_selected_pack(
        self,
        new_pack_id: str,
        new_name: str,
    ) -> PackMutationReceipt:
        if self.selected_pack_id is None:
            raise EffectPackStoreError("select an installed pack to duplicate")
        receipt = self.effect_pack_store.duplicate(
            self.selected_pack_id,
            new_pack_id,
            new_name,
        )
        self._reload_catalog_after_pack_mutation(
            receipt.pack_id if receipt.accepted else self.selected_pack_id
        )
        return receipt

    def rename_selected_pack(
        self,
        new_pack_id: str,
        new_name: str,
    ) -> PackMutationReceipt:
        if self.selected_pack_id is None:
            raise EffectPackStoreError("select an installed pack to rename")
        receipt = self.effect_pack_store.rename(
            self.selected_pack_id,
            new_pack_id,
            new_name,
        )
        self._reload_catalog_after_pack_mutation(
            receipt.pack_id if receipt.accepted else self.selected_pack_id
        )
        return receipt

    def numberOfRowsInTableView_(self, _table_view) -> int:
        return len(self.filtered_rows)

    def tableView_objectValueForTableColumn_row_(self, _table_view, _column, row):
        item = self.filtered_rows[row]
        source = (
            "Community"
            if item.catalog.startswith("pack:")
            else item.semantic_family.value.title()
        )
        return f"{source} · {item.label}"

    def tableViewSelectionDidChange_(self, _notification) -> None:
        index = self.table_view.selectedRow()
        selected_effect_id = (
            self.filtered_rows[index].effect_id
            if 0 <= index < len(self.filtered_rows)
            else None
        )
        if selected_effect_id != self.selected_effect_id:
            self._release_physical_preview(
                PreviewReleaseReason.SELECTION_CHANGED
            )
            self._assignment_draft_dirty = True
        self.selected_effect_id = selected_effect_id
        self._refresh_detail()

    def controlTextDidChange_(self, notification) -> None:
        changed = notification.object() if notification is not None else None
        if changed is self.assignment_target_field:
            value = str(self.assignment_target_field.stringValue())
            self.assignment_target_id = value or None
            self._refresh_detail()
            return
        query = str(self.search_field.stringValue())
        self.filtered_rows = build_gallery_rows(self.catalog.registry, query=query)
        previous_effect_id = self.selected_effect_id
        selected_effect_id = previous_effect_id
        if not any(row.effect_id == previous_effect_id for row in self.filtered_rows):
            selected_effect_id = (
                self.filtered_rows[0].effect_id if self.filtered_rows else None
            )
        if selected_effect_id != previous_effect_id:
            self._release_physical_preview(PreviewReleaseReason.SELECTION_CHANGED)
        self.selected_effect_id = selected_effect_id
        self.table_view.reloadData()
        self.selected_effect_id = selected_effect_id
        self._select_effect_id(self.selected_effect_id)
        self._refresh_detail()

    @objc.IBAction
    def scenarioChanged_(self, _sender) -> None:
        self._release_physical_preview(PreviewReleaseReason.SELECTION_CHANGED)
        index = self.scenario_popup.indexOfSelectedItem()
        if 0 <= index < len(DEFAULT_SYNTHETIC_SCENARIOS):
            self.pause_timeline()
            self.selected_scenario = DEFAULT_SYNTHETIC_SCENARIOS[index]
            self.timeline_slider.setDoubleValue_(index * 2.0)
        self._refresh_detail()

    @objc.IBAction
    def reduceMotionChanged_(self, _sender) -> None:
        self._release_physical_preview(PreviewReleaseReason.SELECTION_CHANGED)
        self._refresh_detail()

    @objc.IBAction
    def physicalPreviewDeviceChanged_(self, _sender) -> None:
        self._release_physical_preview(PreviewReleaseReason.SELECTION_CHANGED)
        self._refresh_physical_preview_controls()

    @objc.IBAction
    def physicalPreviewConsentChanged_(self, _sender) -> None:
        if self.physical_preview_consent.state() != NSOnState:
            self._release_physical_preview(
                PreviewReleaseReason.SELECTION_CHANGED
            )
        self._refresh_physical_preview_controls()

    @objc.IBAction
    def packChanged_(self, _sender) -> None:
        self._refresh_pack_info()

    @objc.IBAction
    def assignmentScopeChanged_(self, _sender) -> None:
        index = self.scope_popup.indexOfSelectedItem()
        if not 0 <= index < len(AssignmentScope):
            return
        self.assignment_scope = tuple(AssignmentScope)[index]
        enabled = self.assignment_scope is not AssignmentScope.GLOBAL
        self.assignment_target_field.setEnabled_(enabled)
        if not enabled:
            self.assignment_target_id = None
            self.assignment_target_field.setStringValue_("")
            self.assignment_target_field.setPlaceholderString_(
                "No target for global default"
            )
        else:
            value = str(self.assignment_target_field.stringValue())
            self.assignment_target_id = value or None
            self.assignment_target_field.setPlaceholderString_(
                "Required bounded target identifier"
            )
        assignment = self._direct_assignment()
        if assignment is not None:
            self.selected_effect_id = assignment.effect_id
            self._select_effect_id(assignment.effect_id)
            self._assignment_draft_dirty = False
        self._refresh_detail()

    @objc.IBAction
    def saveAssignment_(self, _sender) -> None:
        try:
            self.save_assignment()
        except (EffectAssignmentStoreError, OSError, TypeError, ValueError) as error:
            self._show_error("Assignment was not saved", str(error))

    @objc.IBAction
    def resetAssignment_(self, _sender) -> None:
        try:
            self.reset_current_assignment()
        except (EffectAssignmentStoreError, OSError, TypeError, ValueError) as error:
            self._show_error("Assignment was not reset", str(error))

    @objc.IBAction
    def restoreDefaults_(self, _sender) -> None:
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Restore all default effect assignments?")
        alert.setInformativeText_(
            "This removes every saved Studio assignment. Built-in effects and "
            "installed data-only packs are not removed."
        )
        alert.addButtonWithTitle_("Restore Defaults")
        alert.addButtonWithTitle_("Cancel")
        if alert.runModal() != NSAlertFirstButtonReturn:
            return
        try:
            self.restore_default_assignments()
        except (EffectAssignmentStoreError, OSError, TypeError, ValueError) as error:
            self._show_error("Defaults were not restored", str(error))

    @objc.IBAction
    def timelineScrubbed_(self, sender) -> None:
        self.scrub_timeline(sender.doubleValue())

    @objc.IBAction
    def pauseTimeline_(self, _sender) -> None:
        self.pause_timeline()

    @objc.IBAction
    def replayTimeline_(self, _sender) -> None:
        self.replay_timeline()

    @objc.IBAction
    def advanceTimeline_(self, _timer) -> None:
        cursor = min(
            self.timeline_slider.maxValue(),
            self.timeline_slider.doubleValue() + 0.25,
        )
        self.timeline_slider.setDoubleValue_(cursor)
        scenario_index = min(
            len(DEFAULT_SYNTHETIC_SCENARIOS) - 1,
            max(0, int(round(cursor / 2.0))),
        )
        self.selected_scenario = DEFAULT_SYNTHETIC_SCENARIOS[scenario_index]
        self.scenario_popup.selectItemAtIndex_(scenario_index)
        if cursor >= self.timeline_slider.maxValue():
            self.pause_timeline()
            return
        self._refresh_detail()

    @objc.IBAction
    def colorVisionChanged_(self, _sender) -> None:
        index = self.color_vision_popup.indexOfSelectedItem()
        if 0 <= index < len(ColorVisionMode):
            self.set_color_vision_mode(tuple(ColorVisionMode)[index])

    @staticmethod
    def _show_error(message: str, detail: str) -> None:
        alert = NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.setInformativeText_(detail)
        alert.addButtonWithTitle_("OK")
        alert.runModal()

    def _selected_pack_identity(self) -> tuple[str, str] | None:
        if self.selected_pack_id is None:
            return None
        return next(
            (
                (pack.pack_id, pack.name)
                for pack in self.catalog.packs
                if pack.pack_id == self.selected_pack_id
            ),
            None,
        )

    def _prompt_pack_identity(
        self,
        title: str,
        pack_id: str,
        name: str,
    ) -> tuple[str, str] | None:
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(
            "Pack identifiers use lowercase letters, numbers, periods, "
            "underscores, or hyphens."
        )
        alert.addButtonWithTitle_(title)
        alert.addButtonWithTitle_("Cancel")
        accessory = NSView.alloc().initWithFrame_(((0.0, 0.0), (320.0, 66.0)))
        identifier_field = NSTextField.alloc().initWithFrame_(
            ((0.0, 36.0), (320.0, 24.0))
        )
        identifier_field.setStringValue_(pack_id)
        identifier_field.setPlaceholderString_("Pack identifier")
        name_field = NSTextField.alloc().initWithFrame_(
            ((0.0, 4.0), (320.0, 24.0))
        )
        name_field.setStringValue_(name)
        name_field.setPlaceholderString_("Display name")
        accessory.addSubview_(identifier_field)
        accessory.addSubview_(name_field)
        alert.setAccessoryView_(accessory)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return None
        return str(identifier_field.stringValue()), str(name_field.stringValue())

    def _show_pack_receipt(self, receipt: PackMutationReceipt, verb: str) -> None:
        if receipt.accepted:
            self.pack_info_field.setStringValue_(
                f"{verb} data-only pack {receipt.pack_id}. "
                "The gallery and runtime registry are refreshed."
            )
            return
        self._show_error(
            f"Pack was not {verb.lower()}",
            receipt.reason or "The validated store refused the operation.",
        )

    @objc.IBAction
    def importPack_(self, _sender) -> None:
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setAllowedFileTypes_(["json"])
        if panel.runModal() != NSAlertFirstButtonReturn or panel.URL() is None:
            return
        try:
            receipt = self.import_pack(Path(str(panel.URL().path())))
            self._show_pack_receipt(receipt, "Imported")
        except (EffectPackError, EffectPackStoreError, OSError, TypeError, ValueError) as error:
            self._show_error("Pack was not imported", str(error))

    @objc.IBAction
    def exportPack_(self, _sender) -> None:
        if self.selected_pack_id is None:
            return
        panel = NSSavePanel.savePanel()
        panel.setNameFieldStringValue_(f"{self.selected_pack_id}.json")
        panel.setAllowedFileTypes_(["json"])
        if panel.runModal() != NSAlertFirstButtonReturn or panel.URL() is None:
            return
        try:
            self.export_selected_pack(Path(str(panel.URL().path())))
        except (EffectPackStoreError, OSError, TypeError, ValueError) as error:
            self._show_error("Pack was not exported", str(error))

    @objc.IBAction
    def duplicatePack_(self, _sender) -> None:
        identity = self._selected_pack_identity()
        if identity is None:
            return
        values = self._prompt_pack_identity(
            "Duplicate",
            f"{identity[0]}-copy",
            f"{identity[1]} Copy",
        )
        if values is None:
            return
        try:
            receipt = self.duplicate_selected_pack(*values)
            self._show_pack_receipt(receipt, "Duplicated")
        except (EffectPackStoreError, OSError, TypeError, ValueError) as error:
            self._show_error("Pack was not duplicated", str(error))

    @objc.IBAction
    def renamePack_(self, _sender) -> None:
        identity = self._selected_pack_identity()
        if identity is None:
            return
        values = self._prompt_pack_identity("Rename", *identity)
        if values is None:
            return
        try:
            receipt = self.rename_selected_pack(*values)
            self._show_pack_receipt(receipt, "Renamed")
        except (EffectPackStoreError, OSError, TypeError, ValueError) as error:
            self._show_error("Pack was not renamed", str(error))

    @objc.IBAction
    def physicalPreviewRequested_(self, _sender) -> None:
        if self._physical_preview_session_id is not None:
            self._release_physical_preview(PreviewReleaseReason.CLOSE)
            return
        runtime = self.physical_preview
        device = self._selected_physical_preview_device()
        row = self._selected_row()
        if runtime is None or device is None or row is None:
            self._refresh_physical_preview_controls()
            return
        try:
            receipt = runtime.start(
                effect_id=row.effect_id,
                preview_device_id=device.preview_device_id,
                consent_granted=(
                    self.physical_preview_consent.state() == NSOnState
                ),
                duration_seconds=10.0,
                reduce_motion=self._reduce_motion_enabled(),
                scenario=self.selected_scenario,
                registry=self.catalog.registry,
            )
        except Exception:
            self._release_physical_preview(PreviewReleaseReason.ERROR)
            self.physical_preview_status.setStringValue_(
                "Preview failed. Committed output restored."
            )
            return
        if not receipt.accepted:
            self._physical_preview_session_id = None
            self.physical_preview_status.setStringValue_(
                f"{receipt.status_label}. Previewing is not saved."
            )
            self._refresh_physical_preview_controls()
            return
        self._physical_preview_session_id = receipt.session_id
        self.physical_preview_status.setStringValue_(
            f"{receipt.status_label} · {receipt.duration_seconds:.0f}s max"
        )
        self._refresh_physical_preview_controls()

    @objc.IBAction
    def physicalPreviewDidRelease_(self, payload) -> None:
        session_id = str(payload.get("session_id") or "") if payload else ""
        if not session_id or session_id != self._physical_preview_session_id:
            return
        self._physical_preview_session_id = None
        self.physical_preview_consent.setState_(0)
        self.physical_preview_status.setStringValue_(
            "Preview ended. Committed output restored."
        )
        self._refresh_physical_preview_controls()

    @objc.IBAction
    def closeStudio_(self, _sender) -> None:
        self.pause_timeline()
        self._release_physical_preview(PreviewReleaseReason.CLOSE)
        self.window.performClose_(None)

    def windowWillClose_(self, notification) -> None:
        if notification.object() is self.window:
            self.pause_timeline()
            self._release_physical_preview(PreviewReleaseReason.CLOSE)


__all__ = [
    "PREVIEW_SAMPLE_LIMIT",
    "SCENARIO_TITLES",
    "SURFACE_TITLES",
    "EffectStudioCatalog",
    "EffectStudioWindowController",
    "PreviewSample",
    "deterministic_preview_samples",
    "load_effect_studio_catalog",
]
