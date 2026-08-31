from __future__ import annotations

import plistlib
from pathlib import Path

from sidepulse.app_bundle import (
    APP_BUNDLE_IDENTIFIER,
    APP_BUNDLE_NAME,
    APP_EXECUTABLE_NAME,
)
from sidepulse.cli import build_parser, build_sidepulse_parser
from sidepulse.device_identity import DeviceKind, normalize_device_label
from sidepulse.install import OPENCLAW_HOOK_MD
from sidepulse.product_identity import PRODUCT_DISPLAY_NAME

ROOT = Path(__file__).resolve().parents[1]


def test_product_display_name_is_central_and_compatibility_identity_is_stable() -> None:
    assert PRODUCT_DISPLAY_NAME == "JR Bar"
    assert APP_BUNDLE_NAME == "SidePulse.app"
    assert APP_EXECUTABLE_NAME == "SidePulse"
    assert APP_BUNDLE_IDENTIFIER == "io.sidepulse.app"
    assert normalize_device_label("ignored", DeviceKind.PRO) == "SidePulse Pro"
    assert normalize_device_label("ignored", DeviceKind.DOT) == "SidePulse Dot"


def test_cli_uses_jr_bar_display_name_and_preserves_command_name() -> None:
    parser = build_sidepulse_parser()

    assert parser.prog == "sidepulse"
    assert "JR Bar" in parser.format_help()
    assert "SidePulse command line tools" not in parser.format_help()
    assert PRODUCT_DISPLAY_NAME in build_parser().format_help()


def test_ios_display_name_changes_without_changing_url_scheme() -> None:
    plist_path = ROOT / "ios" / "SidePulse" / "SidePulse" / "Info.plist"
    with plist_path.open("rb") as handle:
        metadata = plistlib.load(handle)

    assert metadata["CFBundleDisplayName"] == PRODUCT_DISPLAY_NAME
    assert metadata["CFBundleIdentifier"] == "$(PRODUCT_BUNDLE_IDENTIFIER)"
    assert metadata["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["sidepulse"]


def test_macos_package_sets_display_name_without_renaming_bundle() -> None:
    script = (ROOT / "packaging" / "build_macos_pkg.sh").read_text(encoding="utf-8")

    assert ":CFBundleDisplayName string $PRODUCT_DISPLAY_NAME" in script
    assert "PRODUCT_DISPLAY_NAME=\"JR Bar\"" in script
    assert "--name SidePulse" in script
    assert 'APP_ID="io.sidepulse.app"' in script


def test_current_product_copy_has_no_retired_jr_bar_spelling() -> None:
    current_surfaces = (
        ROOT / "README.md",
        ROOT / "CHANGELOG.md",
        ROOT / "docs" / "FEATURE-MATRIX.md",
        ROOT / "ios" / "SidePulse" / "README.md",
    )

    for path in current_surfaces:
        text = path.read_text(encoding="utf-8")
        assert "JR-BAR" not in text, path
        assert PRODUCT_DISPLAY_NAME in text, path


def test_ios_visible_copy_uses_shared_display_name_and_preserves_hardware_name() -> None:
    ios_source = ROOT / "ios" / "SidePulse" / "SidePulse"
    app_model = (ios_source / "AppModel.swift").read_text(encoding="utf-8")
    content = (ios_source / "ContentView.swift").read_text(encoding="utf-8")
    shortcut = (ios_source / "WriteLEDsIntent.swift").read_text(encoding="utf-8")
    ios_tools = ROOT / "ios" / "SidePulse" / "tools"
    tool_identity = (ios_tools / "product_identity.py").read_text(encoding="utf-8")
    apns = (ios_tools / "apns_client.py").read_text(
        encoding="utf-8"
    )
    send_push = (ios_tools / "send_push.py").read_text(encoding="utf-8")
    server = (ios_tools / "server.py").read_text(encoding="utf-8")

    assert 'static let displayName = "JR Bar"' in app_model
    for retired_copy in (
        '.navigationTitle("SidePulse")',
        'Label("SidePulse",',
        'Text("SidePulse will',
        'Text("3. SidePulse will',
        '"Write SidePulse LEDS.LED"',
        '"title": "SidePulse"',
    ):
        assert retired_copy not in content + shortcut + apns + send_push + server
    assert "ProductIdentity.displayName" in content
    assert "ProductIdentity.displayName" in shortcut
    assert 'PRODUCT_DISPLAY_NAME = "JR Bar"' in tool_identity
    assert "from product_identity import PRODUCT_DISPLAY_NAME" in apns
    assert '"title": PRODUCT_DISPLAY_NAME' in apns
    assert "PRODUCT_DISPLAY_NAME" in send_push
    assert "PRODUCT_DISPLAY_NAME" in server
    assert "SidePulse Dot" in content


def test_openclaw_copy_uses_jr_bar_but_keeps_compatibility_ownership_marker() -> None:
    hook_document = OPENCLAW_HOOK_MD.format(name="sidepulse-status")

    assert "# JR Bar Status" in hook_document
    assert "Forwards agent activity to JR Bar" in hook_document
    assert "JR Bar agent monitor" in hook_document
    assert "Managed\nby SidePulse" in hook_document
