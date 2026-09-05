from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from sidepulse.effect_assignment_store import (
    EffectAssignmentCache,
    EffectAssignmentStore,
)
from sidepulse.effect_pack_store import EffectPackStore
from sidepulse.effect_studio import (
    AssignmentScope,
    ColorVisionMode,
    StudioSurface,
    SyntheticScenario,
    build_surface_simulations,
)
from sidepulse.effect_studio_preview import deterministic_preview_samples
from sidepulse.effect_studio_window import (
    EffectStudioWindowController,
    load_effect_studio_catalog,
)
from sidepulse.product_identity import PRODUCT_DISPLAY_NAME

ROOT = Path(__file__).resolve().parents[1]


def _community_pack() -> dict[str, object]:
    return {
        "id": "window-lab",
        "name": "Window Lab",
        "version": 2,
        "effects": [
            {
                "id": "soft-arrival",
                "label": "Soft Arrival",
                "description": "A quiet completion arrival.",
                "meaning": "completion",
                "surfaces": ["screen_bar", "sidepulse_dot"],
                "energy": "low",
            }
        ],
        "safety": {"data_only": True, "network": False},
        "accessibility": {"reduced_motion": True, "high_contrast": True},
        "license": {
            "spdx_id": "MIT",
            "label": "MIT License",
            "source_url": "https://example.com/window-lab",
        },
    }


def test_catalog_merges_installed_data_only_pack_with_license_metadata(tmp_path) -> None:
    store = EffectPackStore(tmp_path / "packs")
    assert store.install(_community_pack()).accepted

    catalog = load_effect_studio_catalog(store)

    assert any(row.effect_id == "pack:window-lab:soft-arrival" for row in catalog.rows)
    assert tuple(pack.pack_id for pack in catalog.packs) == ("window-lab",)
    assert catalog.packs[0].license_spdx_id == "MIT"
    assert catalog.packs[0].source_url == "https://example.com/window-lab"
    assert catalog.store_status == "1 installed data-only pack"


def test_preview_frame_is_stable_scenario_aware_and_motion_safe(tmp_path) -> None:
    catalog = load_effect_studio_catalog(EffectPackStore(tmp_path / "packs"))
    row = next(row for row in catalog.rows if row.effect_id == "pulse")
    simulation = next(
        item
        for item in build_surface_simulations("pulse", catalog.registry)
        if item.surface is StudioSurface.SCREEN_BAR
    )

    first = deterministic_preview_samples(
        row,
        simulation,
        SyntheticScenario.ONE_AGENT,
    )
    repeated = deterministic_preview_samples(
        row,
        simulation,
        SyntheticScenario.ONE_AGENT,
    )
    failure = deterministic_preview_samples(
        row,
        simulation,
        SyntheticScenario.FAILURE,
    )
    reduced_simulation = next(
        item
        for item in build_surface_simulations(
            "pulse",
            catalog.registry,
            reduce_motion=True,
        )
        if item.surface is StudioSurface.SCREEN_BAR
    )
    reduced = deterministic_preview_samples(
        row,
        reduced_simulation,
        SyntheticScenario.ONE_AGENT,
    )

    assert first == repeated
    assert first != failure
    assert len(first) == 12
    assert len(set(reduced)) == 1
    assert reduced_simulation.rendered_effect_id == "none"


class _PhysicalPreviewRuntime:
    def __init__(self) -> None:
        self.started = []
        self.released = []

    def devices(self):
        from sidepulse.effect_studio_physical_preview import PhysicalPreviewDevice

        return (
            PhysicalPreviewDevice("preview-device-1", "Desk SidePulse", 8),
        )

    def availability(self, effect_id, preview_device_id, *, reduce_motion, registry):
        from sidepulse.effect_studio_physical_preview import PhysicalPreviewAvailability

        return PhysicalPreviewAvailability(True, "Ready for a temporary preview")

    def start(self, **kwargs):
        from sidepulse.effect_studio_physical_preview import PhysicalPreviewStartReceipt

        self.started.append(kwargs)
        session_id = f"session-{len(self.started)}"
        return PhysicalPreviewStartReceipt(
            True,
            session_id,
            "Previewing, not saved",
            10.0,
            None,
        )

    def release(self, reason):
        self.released.append(reason)
        return True


def test_native_window_shows_four_surfaces_scenarios_and_guarded_hardware(tmp_path) -> None:
    store = EffectPackStore(tmp_path / "packs")
    store.install(_community_pack())
    controller = EffectStudioWindowController.alloc().init()
    preview = _PhysicalPreviewRuntime()

    window = controller.open(store=store, physical_preview=preview, show=False)

    assert window.title() == f"{PRODUCT_DISPLAY_NAME} Effect Studio"
    assert tuple(controller.surface_status_fields) == tuple(StudioSurface)
    assert controller.scenario_popup.numberOfItems() == len(SyntheticScenario)
    assert controller.physical_preview_button.isEnabled() is False
    assert "not saved" in controller.physical_preview_status.stringValue()
    assert controller.physical_device_popup.numberOfItems() == 1
    controller.physical_preview_consent.setState_(1)
    controller.physicalPreviewConsentChanged_(None)
    assert controller.physical_preview_button.isEnabled() is True
    controller.physicalPreviewRequested_(None)
    assert controller.physical_preview_status.stringValue().startswith(
        "Previewing, not saved"
    )
    assert preview.started[0]["consent_granted"] is True
    controller.closeStudio_(None)
    assert preview.released
    assert controller.table_view.numberOfRows() == len(controller.catalog.rows)
    assert "schema-validated" in controller.accessibility_field.stringValue()


def test_native_window_ignores_stale_physical_preview_release_callback(tmp_path) -> None:
    controller = EffectStudioWindowController.alloc().init()
    preview = _PhysicalPreviewRuntime()
    controller.open(
        store=EffectPackStore(tmp_path / "packs"),
        physical_preview=preview,
        show=False,
    )
    controller.physical_preview_consent.setState_(1)
    controller.physicalPreviewConsentChanged_(None)
    controller.physicalPreviewRequested_(None)

    controller.physicalPreviewDidRelease_({})
    assert controller._physical_preview_session_id == "session-1"

    controller.physicalPreviewDidRelease_({"session_id": "session-stale"})

    assert controller._physical_preview_session_id == "session-1"
    assert controller.physical_preview_consent.state() == 1
    assert controller.physical_preview_button.title() == "Stop preview"

    controller.physicalPreviewDidRelease_({"session_id": "session-1"})
    assert controller._physical_preview_session_id is None
    assert controller.physical_preview_consent.state() == 0


def test_search_filter_releases_preview_when_selection_changes(tmp_path) -> None:
    controller = EffectStudioWindowController.alloc().init()
    preview = _PhysicalPreviewRuntime()
    controller.open(
        store=EffectPackStore(tmp_path / "packs"),
        physical_preview=preview,
        show=False,
    )
    controller.select_effect("pulse")
    controller.physical_preview_consent.setState_(1)
    controller.physicalPreviewConsentChanged_(None)
    controller.physicalPreviewRequested_(None)
    controller.search_field.setStringValue_("rainbow")

    controller.controlTextDidChange_(
        SimpleNamespace(object=lambda: controller.search_field)
    )

    assert controller.selected_effect_id == "rainbow"
    assert controller._physical_preview_session_id is None
    assert preview.released[-1].value == "selection_changed"


def test_native_window_exposes_comparison_timeline_and_every_assignment_scope(
    tmp_path,
) -> None:
    pack_store = EffectPackStore(tmp_path / "packs")
    assignment_store = EffectAssignmentStore(tmp_path / "assignments.json")
    cache = EffectAssignmentCache()
    controller = EffectStudioWindowController.alloc().init()

    controller.open(
        store=pack_store,
        assignment_store=assignment_store,
        assignment_cache=cache,
        show=False,
    )

    assert controller.scope_popup.numberOfItems() == len(AssignmentScope)
    assert controller.color_vision_popup.numberOfItems() == len(ColorVisionMode)
    assert controller.timeline_slider.minValue() == 0.0
    assert controller.timeline_slider.maxValue() == 22.0
    assert controller.before_label.stringValue() == "Before · saved or default"
    assert controller.after_label.stringValue().startswith("After ·")
    assert controller.pause_button.isEnabled()
    assert controller.replay_button.isEnabled()
    assert tuple(button.title() for button in controller.pack_action_buttons) == (
        "Import",
        "Export",
        "Duplicate",
        "Rename",
    )


def test_assignment_actions_persist_then_refresh_the_runtime_cache(tmp_path) -> None:
    assignment_store = EffectAssignmentStore(tmp_path / "assignments.json")
    cache = EffectAssignmentCache()
    controller = EffectStudioWindowController.alloc().init()
    controller.open(
        store=EffectPackStore(tmp_path / "packs"),
        assignment_store=assignment_store,
        assignment_cache=cache,
        show=False,
    )
    controller.select_effect("pulse")
    controller.set_assignment_draft(AssignmentScope.PROVIDER, "codex")

    saved = controller.save_assignment()

    assert saved.effect_id == "pulse"
    assert cache.snapshot().assignments == (saved,)
    assert assignment_store.load().document.assignments == (saved,)
    assert controller.assignment_status_field.stringValue() == "Saved"

    controller.select_effect("rainbow")
    assert controller.assignment_status_field.stringValue() == "Unsaved changes"
    controller.reset_current_assignment()
    assert cache.snapshot().assignments == ()
    assert controller.assignment_status_field.stringValue() == "Using default"


def test_timeline_can_scrub_pause_replay_and_simulate_color_vision(tmp_path) -> None:
    controller = EffectStudioWindowController.alloc().init()
    controller.open(
        store=EffectPackStore(tmp_path / "packs"),
        assignment_store=EffectAssignmentStore(tmp_path / "assignments.json"),
        assignment_cache=EffectAssignmentCache(),
        show=False,
    )

    controller.scrub_timeline(4.0)
    assert controller.selected_scenario is SyntheticScenario.ASKING
    controller.set_color_vision_mode(ColorVisionMode.MONOCHROMACY)
    controller.replay_timeline()
    assert controller.timeline_paused is False
    controller.pause_timeline()

    assert controller.selected_scenario is SyntheticScenario.ONE_AGENT
    assert controller.color_vision_mode is ColorVisionMode.MONOCHROMACY
    assert controller.timeline_paused is True
    assert controller.timeline_slider.doubleValue() == 0.0


def test_pack_management_actions_use_the_validated_owner_private_store(tmp_path) -> None:
    source_store = EffectPackStore(tmp_path / "source-packs")
    source_store.install(_community_pack())
    import_path = tmp_path / "window-lab.json"
    source_store.export("window-lab", import_path)
    store = EffectPackStore(tmp_path / "packs")
    controller = EffectStudioWindowController.alloc().init()
    controller.open(
        store=store,
        assignment_store=EffectAssignmentStore(tmp_path / "assignments.json"),
        assignment_cache=EffectAssignmentCache(),
        show=False,
    )
    imported = controller.import_pack(import_path)
    controller.select_pack("window-lab")

    duplicated = controller.duplicate_selected_pack("window-copy", "Window Copy")
    controller.select_pack("window-copy")
    renamed = controller.rename_selected_pack("window-renamed", "Window Renamed")
    export_path = tmp_path / "exported.json"
    controller.select_pack("window-renamed")
    controller.export_selected_pack(export_path)

    assert imported.accepted
    assert duplicated.accepted
    assert renamed.accepted
    assert tuple(pack.pack_id for pack in store.list()) == (
        "window-lab",
        "window-renamed",
    )
    assert export_path.read_bytes() == store.canonical_export("window-renamed")


def test_native_studio_is_reachable_from_the_production_menu() -> None:
    lighting = ast.parse(
        (ROOT / "src" / "sidepulse" / "lighting_settings_pane.py").read_text(
            encoding="utf-8"
        )
    )
    production = ast.parse(
        (ROOT / "src" / "sidepulse" / "_status_bar_production.py").read_text(
            encoding="utf-8"
        )
    )
    menu_strings = {
        node.value
        for node in ast.walk(lighting)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    methods = {
        node.name: node
        for node in ast.walk(production)
        if isinstance(node, ast.FunctionDef)
    }

    assert "Open Effect Studio…" in menu_strings
    assert "openEffectStudio:" in menu_strings
    method = methods["openEffectStudio_"]
    calls = {
        node.func.attr
        for node in ast.walk(method)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "open" in calls
    open_calls = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "open"
    ]
    assert len(open_calls) == 1
    assert {keyword.arg for keyword in open_calls[0].keywords} == {
        "assignment_cache",
        "physical_preview",
    }
