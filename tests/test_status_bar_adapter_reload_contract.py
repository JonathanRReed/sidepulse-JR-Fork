from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "sidepulse" / "status_bar.py"


def test_status_bar_adapters_preserve_originals_on_the_runtime_module() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    for marker in (
        "_sidepulse_original_build_menu",
        "_sidepulse_original_device_id_for_root",
        "_sidepulse_original_persistable_device_identity",
        "_sidepulse_device_identity_cache",
    ):
        assert marker in source


def test_status_bar_reload_does_not_replace_originals_with_its_own_wrappers() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert 'getattr(_legacy, "_sidepulse_original_build_menu", _legacy.build_menu)' in source
    assert 'getattr(\n    _legacy,\n    "_sidepulse_original_device_id_for_root"' in source
    assert 'getattr(\n    _legacy,\n    "_sidepulse_original_persistable_device_identity"' in source
    assert "_legacy._sidepulse_original_build_menu = _ORIGINAL_BUILD_MENU" in source
    assert "_legacy._sidepulse_device_identity_cache = _DEVICE_IDENTITIES" in source
