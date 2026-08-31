from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECEIPT_ROOT = (
    ROOT
    / ".superpowers"
    / "sdd"
    / "2026-08-30-jr-bar-p3-38-manual-scheduled-dnd"
)
MANIFEST_PATH = RECEIPT_ROOT / "task-5-renders" / "manifest.json"
HARNESS_PATH = RECEIPT_ROOT / "render_dnd_receipts.py"
STATES = (
    "off",
    "manual_mute",
    "scheduled_dim",
    "focus_pause",
    "asks_only_override",
    "scheduled_fully_dark",
    "temporary_resume",
    "focus_unavailable",
)
APPEARANCES = {
    "aqua": "NSAppearanceNameAqua",
    "dark_aqua": "NSAppearanceNameDarkAqua",
}
EXPECTED_POLICY = {
    "off": ([], [], None),
    "manual_mute": (["manual"], ["mute"], 1_800_003_600.0),
    "scheduled_dim": (["schedule"], ["dim"], 1_800_018_000.0),
    "focus_pause": (["macos_focus"], ["pause"], None),
    "asks_only_override": (["manual"], ["asks_only"], 1_800_007_200.0),
    "scheduled_fully_dark": (["schedule"], ["dark"], 1_800_018_000.0),
    "temporary_resume": ([], [], 1_800_018_000.0),
    "focus_unavailable": ([], [], None),
}
EXPECTED_MENU_TITLES = {
    "off": "DND: Off",
    "manual_mute": "DND: Mute until 3:00 AM",
    "scheduled_dim": "DND: Dim, scheduled until 7:00 AM",
    "focus_pause": "DND: Pause, macOS Focus",
    "asks_only_override": "DND: Asks Only until 4:00 AM",
    "scheduled_fully_dark": "DND: Fully Dark, scheduled until 7:00 AM",
    "temporary_resume": "DND: Off",
    "focus_unavailable": "DND: Off",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_dnd_native_receipt_matrix_and_hashes_are_complete() -> None:
    manifest = _manifest()
    expected_pngs = {
        f"{appearance}-{state}.png"
        for appearance in APPEARANCES
        for state in STATES
    }
    renders = manifest["renders"]
    assert manifest["schema"] == "p3.38-task-5-native-dnd-receipt-v1"
    assert manifest["states"] == list(STATES)
    assert {render["png"] for render in renders} == expected_pngs
    assert len(renders) == 16
    assert {path.name for path in (MANIFEST_PATH.parent).glob("*.png")} == expected_pngs
    assert manifest["integrity"]["render_count"] == 16
    assert manifest["integrity"]["source_hash_mismatches"] == 0
    assert manifest["integrity"]["image_hash_mismatches"] == 0
    assert manifest["integrity"]["unexpected_png_count"] == 0
    for render in renders:
        assert _sha256(MANIFEST_PATH.parent / render["png"]) == render["image_sha256"]


def test_dnd_native_receipts_pin_current_production_and_harness_sources() -> None:
    manifest = _manifest()
    source_files = manifest["source_files"]
    for relative, metadata in source_files.items():
        assert _sha256(ROOT / relative) == metadata["sha256"]
    pinned = manifest["pinned_sha256"]
    assert pinned["production_dnd_settings_pane"] == _sha256(
        ROOT / "src/sidepulse/dnd_settings_pane.py"
    )
    assert pinned["production_menu_projection"] == _sha256(
        ROOT / "src/sidepulse/menu_projection.py"
    )
    assert pinned["production_menu_adapter"] == _sha256(
        ROOT / "src/sidepulse/status_bar.py"
    )
    assert pinned["receipt_harness"] == _sha256(HARNESS_PATH)
    assert "Source-only AppKit evidence" in manifest["source_only_disclaimer"]
    assert "live Focus authorization" in manifest["source_only_disclaimer"]


def test_dnd_native_receipts_bind_policy_controls_focus_and_menu_truth() -> None:
    for render in _manifest()["renders"]:
        state = render["state"]
        appearance = render["appearance"]
        sources, modes, deadline = EXPECTED_POLICY[state]
        assert render["native_appearance_name"] == APPEARANCES[appearance]
        assert render["policy"]["active_sources"] == sources
        assert render["policy"]["active_modes"] == modes
        assert render["policy"]["next_transition_epoch"] == deadline
        assert render["menu"]["root_title"] == EXPECTED_MENU_TITLES[state]
        assert render["menu"]["native_parent"]["title"] == EXPECTED_MENU_TITLES[
            state
        ]
        assert [
            row["title"] for row in render["menu"]["native_submenu"]
        ] == [row["title"] for row in render["menu"]["projected_submenu"]]
        assert all(
            row["target_is_receipt_controller"]
            for row in render["menu"]["native_submenu"]
        )
        assert render["controls"]["resume"]["enabled"] is (
            state in {"scheduled_dim", "scheduled_fully_dark"}
        )
        assert render["controls"]["end_override"]["enabled"] is (
            state in {"manual_mute", "asks_only_override", "temporary_resume"}
        )
        if state == "focus_pause":
            assert render["focus"]["activity"] == "active"
            assert render["focus"]["status_copy"] == (
                "Focus status is allowed and a Focus is active."
            )
        if state == "focus_unavailable":
            assert render["focus"]["authorization"] == "unavailable"
            assert render["focus"]["authorization_button_visible"] is True
            assert render["focus"]["authorization_button_enabled"] is False
        assert render["focus"]["authorization_requested"] is False


def test_dnd_native_receipts_bind_accessibility_keyboard_and_geometry() -> None:
    for render in _manifest()["renders"]:
        status = render["settings_copy"]["status"]
        status_ax = render["settings_copy"]["status_ax"]
        assert status_ax["role"] == "AXStaticText"
        assert status_ax["label"] == "Do Not Disturb status"
        assert status_ax["value"] == status
        assert status_ax["help"]
        assert render["keyboard"]["closed"] is True
        assert render["keyboard"]["count"] == 29
        assert render["keyboard"]["order"][:9] == [
            "status",
            "schedule_enabled",
            "schedule_start",
            "schedule_end",
            "schedule_mode",
            "dim_fraction",
            "follow_focus",
            "focus_mode",
            "focus_authorization",
        ]
        assert render["keyboard"]["order"][9:14] == [
            "temporary_mute",
            "temporary_dim",
            "temporary_pause",
            "temporary_asks_only",
            "temporary_dark",
        ]
        assert render["first_responder"] == "status"
        assert render["layout"]["pixel_scale"] == 4.0
        assert render["layout"]["image_pixels"]["width"] == 2480
        assert render["layout"]["visible_regions_in_bounds"] is True
        assert all(
            region["sampled_color_variants"] >= 2
            for region in render["layout"]["regions"].values()
        )
        for name, control in render["controls"].items():
            if name == "status":
                continue
            assert control["ax"]["label"]
            assert control["ax"]["help"]
