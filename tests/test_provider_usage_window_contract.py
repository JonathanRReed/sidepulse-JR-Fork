from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "sidepulse" / "provider_usage_window.py"


def test_usage_window_is_a_thin_appkit_projection_host():
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    assert "ProviderUsageWindowController" in names
    assert "refresh" in names
    assert "show" in names
    assert "project_usage_center" in source
    for forbidden in (
        "urlopen(",
        "subprocess.run(",
        "sqlite3.connect(",
        "read_text(",
        "read_bytes(",
    ):
        assert forbidden not in source


def test_usage_window_passes_cached_merged_sync_and_never_fetches():
    # The "across synced Macs" line renders from LOCAL cached documents
    # only (2026-08-26): the window imports the TTL-memoized cache, not
    # the SFTP runtime or transport.
    source = MODULE.read_text(encoding="utf-8")
    assert "merged_sync=cached_merged_sync(state)" in source
    assert "provider_usage_sync_cache" in source
    assert "provider_usage_sync_runtime" not in source
    assert "provider_usage_sync_transport" not in source


def test_usage_window_survives_its_own_close_button():
    # A code-created NSWindow defaults to released-when-closed; this
    # window is cached and refreshed forever, so closing it once made
    # every later Connect click a dead-object SIGTRAP (2026-08-20, three
    # crash reports in one morning).
    source = MODULE.read_text(encoding="utf-8")
    assert "setReleasedWhenClosed_(False)" in source
