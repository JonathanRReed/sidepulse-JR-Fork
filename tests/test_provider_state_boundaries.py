from __future__ import annotations

import ast
import json
from pathlib import Path

from sidepulse.provider_usage_runtime import (
    ProviderUsageState,
    RefreshPublicationOutcome,
    RefreshPublicationReceipt,
)
from sidepulse.provider_usage_settings import (
    default_provider_usage_settings,
    save_provider_usage_settings,
)
from sidepulse.provider_usage_store import save_provider_usage_state
from sidepulse.provider_usage_sync_settings import (
    default_provider_sync_settings,
    save_provider_sync_settings,
)

ROOT = Path(__file__).parents[1]

_RUNTIME_CACHE_FIELDS = frozenset(
    {
        "action_label",
        "incident",
        "lanes",
        "next_refresh_at",
        "observed_at",
        "reason_code",
        "refreshed_at",
        "snapshots",
        "state",
    }
)
_PERMISSION_FIELDS = frozenset(
    {"browser", "consents", "domains", "fields", "granted_at", "profile"}
)
_CAPABILITY_FIELDS = frozenset(
    {"capabilities", "capability_id", "product_capabilities", "supported"}
)
_DURABLE_SETTING_FIELDS = frozenset(
    {"browser_sources", "enabled", "menu_display", "options", "peers"}
)


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        result = {str(key) for key in value}
        for item in value.values():
            result.update(_keys(item))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for item in value:
            result.update(_keys(item))
        return result
    return set()


def _direct_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_durable_provider_settings_do_not_serialize_cache_permission_or_capability_state(
    tmp_path: Path,
) -> None:
    usage_path = tmp_path / "usage-settings.json"
    sync_path = tmp_path / "sync-settings.json"
    save_provider_usage_settings(default_provider_usage_settings(), usage_path)
    save_provider_sync_settings(default_provider_sync_settings(), sync_path)

    forbidden = _RUNTIME_CACHE_FIELDS | _PERMISSION_FIELDS | _CAPABILITY_FIELDS
    for path in (usage_path, sync_path):
        assert _keys(json.loads(path.read_text(encoding="utf-8"))).isdisjoint(
            forbidden
        )


def test_runtime_usage_cache_does_not_serialize_settings_permissions_or_capabilities(
    tmp_path: Path,
) -> None:
    path = tmp_path / "usage-cache.json"
    save_provider_usage_state(ProviderUsageState((), 1000.0, 1120.0, False), path)

    keys = _keys(json.loads(path.read_text(encoding="utf-8")))
    assert keys.isdisjoint(
        _DURABLE_SETTING_FIELDS | _PERMISSION_FIELDS | _CAPABILITY_FIELDS
    )


def test_provider_state_modules_keep_direct_ownership_boundaries() -> None:
    usage_settings = _direct_imports(
        ROOT / "src" / "sidepulse" / "provider_usage_settings.py"
    )
    usage_store = _direct_imports(
        ROOT / "src" / "sidepulse" / "provider_usage_store.py"
    )
    consent = _direct_imports(
        ROOT / "src" / "sidepulse" / "provider_browser_consent.py"
    )

    assert not usage_settings.intersection(
        {
            "provider_browser_consent",
            "provider_usage_runtime",
            "provider_usage_store",
        }
    )
    assert not usage_store.intersection(
        {"provider_browser_consent", "provider_usage_settings"}
    )
    assert not consent.intersection(
        {"provider_usage_runtime", "provider_usage_settings", "provider_usage_store"}
    )


def test_refresh_receipt_is_revision_bound_and_content_free() -> None:
    receipt = RefreshPublicationReceipt(
        sequence=1,
        settings_revision=7,
        outcome=RefreshPublicationOutcome.SUPERSEDED,
    )

    assert receipt.settings_revision == 7
    assert set(receipt.__slots__) == {
        "sequence",
        "settings_revision",
        "outcome",
        "error_code",
    }
