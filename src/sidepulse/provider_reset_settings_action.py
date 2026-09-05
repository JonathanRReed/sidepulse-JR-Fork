"""Persistence action for one exact provider reset delivery choice."""

from __future__ import annotations


def toggle_provider_reset_setting(controller, sender, *, log) -> None:
    from .provider_usage_controller_actions import apply_provider_usage_settings_snapshot
    from .provider_usage_settings import (
        load_provider_usage_settings,
        save_provider_usage_settings,
    )
    from .settings_category_runtime import refresh_native_usage_summary

    payload = sender.representedObject()
    if not isinstance(payload, dict):
        return
    provider_id = str(payload.get("provider_id") or "")
    source_instance_id = str(payload.get("source_instance_id") or "default")
    field = str(payload.get("field") or "")
    enabled = bool(sender.state())
    if not provider_id:
        return
    loaded = load_provider_usage_settings()
    try:
        if field == "reset_celebrations":
            updated = loaded.settings.with_reset_celebrations(
                provider_id,
                enabled,
                source_instance_id=source_instance_id,
            )
        elif field.startswith("reset_"):
            updated = loaded.settings.with_reset_channel(
                provider_id,
                field.removeprefix("reset_"),
                enabled,
                source_instance_id=source_instance_id,
            )
        else:
            raise ValueError("invalid provider reset setting")
        save_provider_usage_settings(updated, loaded=loaded)
    except Exception as exc:
        log(f"provider reset setting: {exc}")
        sender.setState_(0 if enabled else 1)
        return
    controller._menu_signature = None
    apply_provider_usage_settings_snapshot(controller, updated, notify_service=True)
    refresh_native_usage_summary(controller)


__all__ = ["toggle_provider_reset_setting"]


def deliver_pending_reset_events(controller, *, legacy) -> None:
    import time

    from .provider_reset_events import (
        ResetDeliveryState,
        apply_reset_channel_receipt,
        pending_reset_channels,
    )
    from .provider_usage_feedback import deliver_reset_channels

    state = getattr(controller, "_sidepulse_reset_delivery_state", ResetDeliveryState())
    original = state
    epoch_now = time.time()
    monotonic_now = time.monotonic()
    for event in tuple(state.events):
        state, channels = pending_reset_channels(
            state,
            event.event_id,
            now=epoch_now,
            record_discards=True,
        )
        if not channels:
            continue
        receipts = deliver_reset_channels(
            controller,
            event,
            channels,
            now=epoch_now,
            monotonic_now=monotonic_now,
            log=legacy.log_status_bar,
        )
        for receipt in receipts:
            state = apply_reset_channel_receipt(
                state,
                event.event_id,
                receipt.channel,
                receipt.outcome,
                reason=receipt.reason,
                now=receipt.recorded_at,
            )
    controller._sidepulse_reset_delivery_state = state
    if state != original:
        controller._persist_reset_delivery_state()
    controller._schedule_reset_delivery_retry(epoch_now)


__all__.append("deliver_pending_reset_events")
