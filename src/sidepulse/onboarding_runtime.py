"""Consent-first first-run behavior shared by the provider controller facade."""

from __future__ import annotations

import math


def refresh_setup_window(controller, legacy) -> None:
    if controller.setup_window is None:
        return
    for key in ("launch_status", "eject_status", "sleep_status"):
        legacy.set_field_value(controller.setup_fields.get(key), "Optional")
    legacy.set_field_value(
        controller.setup_fields.get("eject_status"),
        "Choose a specific volume in Devices",
    )
    legacy.set_field_value(controller.setup_fields.get("fda_status"), "Not requested")
    for provider in legacy.HOOK_PROVIDERS:
        legacy.set_field_value(
            controller.setup_fields.get(f"setup_{provider}_status"),
            "Connect only if you choose",
        )


def run_first_launch_setup(controller, legacy) -> None:
    from .provider_usage_controller_actions import (
        apply_provider_usage_settings_snapshot,
    )
    from .provider_usage_settings import (
        load_provider_usage_settings,
        save_provider_usage_settings,
    )

    def is_on(key: str) -> bool:
        return legacy.checkbox_is_on(controller.setup_buttons.get(key))

    messages = []
    errors = []
    try:
        controller.set_virtual_status_device(is_on("screen_bar"))
        settings = controller.settings.with_link_screen_bar_to_hardware(is_on("matched_lighting"))
        settings = settings.with_sleep_dim_enabled(is_on("sleep_dimming"))
        settings = settings.with_idle_auto_off_enabled(is_on("idle_off"))
        controller.settings = settings
        legacy.save_settings(settings)
        messages.append("Software lighting preferences saved.")
    except Exception as exc:
        errors.append(f"Lighting preferences failed: {exc}")

    try:
        loaded = load_provider_usage_settings()
        updated = loaded.settings.with_menu_flag("privacy_mode", is_on("privacy_mode"))
        for preference in updated.providers:
            provider_id, source_instance_id = preference.identity
            updated = updated.with_reset_celebrations(
                provider_id,
                is_on("reset_celebrations"),
                source_instance_id=source_instance_id,
            )
            for field in (
                "reset_overlay",
                "reset_hardware",
                "reset_notification",
                "reset_sound",
            ):
                updated = updated.with_reset_channel(
                    provider_id,
                    field.removeprefix("reset_"),
                    is_on(field),
                    source_instance_id=source_instance_id,
                )
        save_provider_usage_settings(updated, loaded=loaded)
        apply_provider_usage_settings_snapshot(controller, updated, notify_service=True)
        messages.append("Privacy and reset channels saved.")
    except Exception as exc:
        errors.append(f"Provider preferences failed: {exc}")

    if is_on("launch"):
        try:
            if not legacy.launch_agent_installed():
                legacy.install_launch_agent(start=False)
            messages.append("Run at Login installed.")
        except Exception as exc:
            errors.append(f"Run at Login failed: {exc}")
    if is_on("sleep_helper"):
        try:
            if not legacy.sleep_helper_installed():
                legacy.open_terminal_setup_command(legacy.sleep_helper_install_command())
                errors.append("Finish the sleep-prevention setup in Terminal.")
        except Exception as exc:
            errors.append(f"Sleep prevention failed: {exc}")

    if errors:
        legacy.set_field_value(controller.setup_fields.get("message"), "  ".join(errors))
        return
    controller.complete_first_launch_setup("  ".join(messages) or "Setup saved.")


def set_sleep_dim(controller, sender, legacy) -> None:
    controller.settings = controller.settings.with_sleep_dim_enabled(bool(sender.state()))
    legacy.save_settings(controller.settings)
    from .lighting_settings_pane import refresh_brightness_behavior_controls

    refresh_brightness_behavior_controls(controller)
    controller.refresh_(None)


def set_idle_auto_off(controller, sender, legacy) -> None:
    controller.settings = controller.settings.with_idle_auto_off_enabled(bool(sender.state()))
    legacy.save_settings(controller.settings)
    from .lighting_settings_pane import refresh_brightness_behavior_controls

    refresh_brightness_behavior_controls(controller)
    controller.refresh_(None)


def _numeric_field_value(sender) -> float | None:
    try:
        value = float(str(sender.stringValue()).strip())
    except (AttributeError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def set_sleep_dim_percentage(controller, sender, legacy) -> bool:
    percentage = _numeric_field_value(sender)
    if percentage is None:
        legacy.set_field_value(
            sender,
            f"{round(controller.settings.sleep_dim_fraction * 100):g}",
        )
        controller.set_settings_message("Enter a sleep brightness from 5% to 100%.")
        return False
    controller.settings = controller.settings.with_sleep_dim_fraction(percentage / 100.0)
    legacy.save_settings(controller.settings)
    normalized = round(controller.settings.sleep_dim_fraction * 100)
    legacy.set_field_value(sender, f"{normalized:g}")
    controller.set_settings_message(f"Sleep brightness: {normalized:g}%.")
    controller.refresh_(None)
    return True


def set_idle_auto_off_timeout(controller, sender, legacy) -> bool:
    minutes = _numeric_field_value(sender)
    if minutes is None:
        legacy.set_field_value(
            sender,
            f"{controller.settings.idle_auto_off_after_minutes:g}",
        )
        controller.set_settings_message("Enter an idle timeout from 5 to 1,440 minutes.")
        return False
    controller.settings = controller.settings.with_idle_auto_off_after_minutes(minutes)
    legacy.save_settings(controller.settings)
    normalized = controller.settings.idle_auto_off_after_minutes
    legacy.set_field_value(sender, f"{normalized:g}")
    controller.set_settings_message(f"Idle auto-off: {normalized:g} minutes.")
    controller.refresh_(None)
    return True


__all__ = [
    "refresh_setup_window",
    "run_first_launch_setup",
    "set_idle_auto_off",
    "set_idle_auto_off_timeout",
    "set_sleep_dim",
    "set_sleep_dim_percentage",
]
