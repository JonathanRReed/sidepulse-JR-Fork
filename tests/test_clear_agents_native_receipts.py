from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECEIPT_ROOT = (
    ROOT
    / ".superpowers"
    / "sdd"
    / "2026-08-30-jr-bar-p3-39-safe-clear-agents"
)
MANIFEST_PATH = RECEIPT_ROOT / "task-5-renders" / "manifest.json"
HARNESS_PATH = RECEIPT_ROOT / "render_clear_agents_receipts.py"
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
APPEARANCES = {
    "aqua": "NSAppearanceNameAqua",
    "dark_aqua": "NSAppearanceNameDarkAqua",
}
EXPECTED_COPY = {
    "preview": {
        "title": "Clear Agents?",
        "summary_fragment": "completed agents will leave the current presentation",
        "buttons": ("Cancel", "Clear Presented Agents"),
    },
    "protected_live_work": {
        "title": "Clear Agents?",
        "summary_fragment": "completed agent will leave the current presentation",
        "buttons": ("Cancel", "Clear Presented Agents"),
    },
    "stale": {
        "title": "Agents Changed",
        "summary_fragment": "Review the refreshed list before clearing",
        "buttons": ("Cancel", "Review Changes"),
    },
    "saving": {
        "title": "Clearing Presented Agents",
        "summary_fragment": "Current work is unchanged",
        "buttons": (),
    },
    "successful_receipt": {
        "title": "Agents Cleared",
        "summary_fragment": "completed agents left the current presentation",
        "buttons": ("Undo", "Done"),
    },
    "failure": {
        "title": "Could Not Clear Agents",
        "summary_fragment": "Nothing was cleared",
        "buttons": ("Cancel", "Try Again"),
    },
    "expired_undo": {
        "title": "Undo Expired",
        "summary_fragment": "five-minute Undo window has ended",
        "buttons": ("Done",),
    },
    "undone": {
        "title": "Clear Agents Undone",
        "summary_fragment": "completion receipts were removed",
        "buttons": ("Done",),
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_clear_agents_native_receipt_matrix_and_hashes_are_complete() -> None:
    manifest = _manifest()
    expected_pngs = {
        f"{appearance}-{state}.png"
        for appearance in APPEARANCES
        for state in STATES
    }
    renders = manifest["renders"]
    assert manifest["schema"] == "p3.39-task-5-clear-agents-native-receipt-v1"
    assert manifest["states"] == list(STATES)
    assert {render["png"] for render in renders} == expected_pngs
    assert len(renders) == 16
    assert {path.name for path in MANIFEST_PATH.parent.glob("*.png")} == expected_pngs
    assert manifest["integrity"]["render_count"] == 16
    assert manifest["integrity"]["source_file_count"] == len(manifest["source_files"])
    assert manifest["integrity"]["source_hash_mismatches"] == 0
    assert manifest["integrity"]["image_hash_mismatches"] == 0
    assert manifest["integrity"]["unexpected_png_count"] == 0
    for render in renders:
        assert _sha256(MANIFEST_PATH.parent / render["png"]) == render["image_sha256"]


def test_clear_agents_native_receipts_pin_current_production_and_harness_sources() -> None:
    manifest = _manifest()
    source_files = manifest["source_files"]
    for relative, metadata in source_files.items():
        assert _sha256(ROOT / relative) == metadata["sha256"]
    pinned = manifest["pinned_sha256"]
    assert pinned["production_clear_agents"] == _sha256(
        ROOT / "src/sidepulse/clear_agents.py"
    )
    assert pinned["production_clear_agents_popover"] == _sha256(
        ROOT / "src/sidepulse/clear_agents_popover.py"
    )
    assert pinned["production_status_bar_legacy"] == _sha256(
        ROOT / "src/sidepulse/status_bar_legacy.py"
    )
    assert pinned["production_clear_agents_store"] == _sha256(
        ROOT / "src/sidepulse/clear_agents_store.py"
    )
    assert pinned["receipt_harness"] == _sha256(HARNESS_PATH)
    assert "Source-only AppKit evidence" in manifest["source_only_disclaimer"]
    assert "installed-app" in manifest["source_only_disclaimer"]


def test_clear_agents_native_receipts_bind_visible_copy_and_buttons() -> None:
    for render in _manifest()["renders"]:
        expected = EXPECTED_COPY[render["state"]]
        assert render["native_appearance_name"] == APPEARANCES[render["appearance"]]
        assert render["shown"] is True
        assert render["visible_copy"]["title"] == expected["title"]
        assert expected["summary_fragment"] in render["visible_copy"]["summary"]
        assert render["visible_copy"]["preservation"].startswith(
            "Only exact local completion receipts change."
        )
        assert tuple(
            button["title"] for button in render["buttons"] if not button["hidden"]
        ) == expected["buttons"]
        assert all(
            button["enabled"] for button in render["buttons"] if not button["hidden"]
        )
        assert all(render["visual_inspection"].values())
    protected = next(
        render
        for render in _manifest()["renders"]
        if render["state"] == "protected_live_work" and render["appearance"] == "aqua"
    )
    assert "1 active, 1 waiting, 1 failed, and 2 other current" in protected[
        "visible_copy"
    ]["protected"]
    assert "2 remote or unkeyed completions stay visible" in protected["visible_copy"][
        "protected"
    ]
    expired = next(
        render
        for render in _manifest()["renders"]
        if render["state"] == "expired_undo" and render["appearance"] == "aqua"
    )
    assert expired["visible_copy"]["title"] == "Undo Expired"


def test_clear_agents_native_receipts_bind_accessibility_keyboard_and_geometry() -> None:
    for render in _manifest()["renders"]:
        accessibility = render["accessibility"]
        keyboard = render["keyboard"]
        layout = render["layout"]
        assert accessibility["root"]["role"] == "AXGroup"
        assert accessibility["summary"]["label"] == "Clear Agents status"
        assert accessibility["items"]["label"] == "Agents to clear from presentation"
        assert accessibility["protected"]["label"] == "Protected agent work"
        assert accessibility["preservation"]["label"] == "Preserved data"
        assert accessibility["preservation"]["value"].startswith(
            "Only exact local completion receipts change."
        )
        assert type(keyboard["closed"]) is bool
        assert keyboard["count"] == len(keyboard["order"])
        if keyboard["order"]:
            assert keyboard["first_responder"] == keyboard["order"][0]
        else:
            assert render["state"] == "saving"
            assert keyboard["first_responder"] == "root"
            assert keyboard["closed"] is False
        assert layout["pixel_scale"] == 4.0
        assert layout["image_pixels"]["width"] == 1680
        assert layout["image_pixels"]["height"] == 1440
        assert layout["visible_regions_in_bounds"] is True
        assert all(
            region["sampled_color_variants"] >= 2
            for region in layout["regions"].values()
            if region["visible"]
        )
