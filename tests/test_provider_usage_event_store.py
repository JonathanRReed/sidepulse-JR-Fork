from __future__ import annotations

import json
from pathlib import Path

from sidepulse.provider_reset_events import ResetDeliverySettings, ResetDeliveryState, begin_reset_delivery
from sidepulse.provider_usage_event_store import (
    load_reset_delivery_state,
    save_reset_delivery_state,
)
from sidepulse.provider_usage_qol import ResetEvent
from sidepulse.provider_usage_settings import (
    default_provider_usage_settings,
    load_provider_usage_settings,
    save_provider_usage_settings,
)


def test_pending_reset_delivery_round_trips_across_restart(tmp_path: Path):
    target = tmp_path / "events.json"
    state = begin_reset_delivery(
        ResetDeliveryState(),
        ResetEvent("codex:weekly:event", "codex", "weekly", "Weekly reset", 1000, "acct", 900),
        ResetDeliverySettings(),
        now=1000,
    )
    save_reset_delivery_state(state, target)
    assert load_reset_delivery_state(target) == state


def test_invalid_document_fails_closed(tmp_path: Path):
    target = tmp_path / "events.json"
    target.write_text(json.dumps({"schema_version": 99, "seen": ["bad"]}))
    assert load_reset_delivery_state(target) == ResetDeliveryState()


def test_reset_channel_preferences_are_durable_and_default_enabled(tmp_path: Path):
    target = tmp_path / "settings.json"
    settings = default_provider_usage_settings().with_reset_channel(
        "codex", "hardware", False
    )
    save_provider_usage_settings(settings, target)
    restored = load_provider_usage_settings(target)
    preference = restored.settings.preference("codex")
    assert preference.reset_overlay is True
    assert preference.reset_hardware is False
    assert preference.reset_notification is True
    assert preference.reset_sound is True
