"""Privacy-safe provider reset controls for the native Usage settings pane."""

from __future__ import annotations

from typing import NamedTuple

from .provider_account_identity import (
    configured_user_alias,
    project_provider_account_identity,
)


class ProviderResetChannel(NamedTuple):
    key: str
    label: str
    enabled: bool


class ProviderResetSettingsRow(NamedTuple):
    provider_id: str
    source_instance_id: str
    heading: str
    master_enabled: bool
    channels: tuple[ProviderResetChannel, ...]


class ProviderResetSettingsModel(NamedTuple):
    rows: tuple[ProviderResetSettingsRow, ...]


_CHANNELS = (
    ("reset_overlay", "Screen overlay"),
    ("reset_hardware", "Hardware lighting"),
    ("reset_notification", "Notification"),
    ("reset_sound", "Sound"),
)


def _safe_label(preference) -> str:
    alias = configured_user_alias(
        provider_id=preference.provider_id,
        source_instance_id=preference.source_instance_id,
        visual_label=preference.label,
    )
    return project_provider_account_identity(
        provider_id=preference.provider_id,
        source_instance_id=preference.source_instance_id,
        account_label=None,
        user_alias=alias,
    ).primary_label


def _control_id(row: ProviderResetSettingsRow, field: str) -> str:
    identity = project_provider_account_identity(
        provider_id=row.provider_id,
        source_instance_id=row.source_instance_id,
        account_label=None,
    )
    return f"provider-reset:{row.provider_id}:{identity.collision_suffix}:{field}"


def build_provider_reset_settings_model(settings) -> ProviderResetSettingsModel:
    from .provider_usage_settings import ProviderUsageSettings

    if type(settings) is not ProviderUsageSettings:
        raise TypeError("expected ProviderUsageSettings")
    return ProviderResetSettingsModel(
        tuple(
            ProviderResetSettingsRow(
                preference.provider_id,
                preference.source_instance_id,
                _safe_label(preference),
                preference.reset_celebrations,
                tuple(ProviderResetChannel(key, label, getattr(preference, key)) for key, label in _CHANNELS),
            )
            for preference in settings.providers
        )
    )


def render_provider_reset_settings(target, stack, settings, ui) -> tuple:
    model = build_provider_reset_settings_model(settings)
    outer, inner = ui.make_card("Reset Celebrations")
    inner.addArrangedSubview_(
        ui.make_wrapping_label(
            "Choose which providers may celebrate a confirmed quota reset, "
            "then choose exactly where each celebration appears.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    boxes = []
    for row in model.rows:
        inner.addArrangedSubview_(ui.make_label(row.heading, size=12.0))
        controls = (
            ("reset_celebrations", "Celebrate confirmed resets", row.master_enabled),
            *((channel.key, channel.label, channel.enabled) for channel in row.channels),
        )
        channel_stack = ui.make_stack(orientation="horizontal", spacing=ui.SPACE_S)
        for field, label, enabled in controls:
            box = ui.make_checkbox(label, target, "toggleProviderResetSetting:")
            box.setIdentifier_(_control_id(row, field))
            box.setRepresentedObject_(
                {
                    "provider_id": row.provider_id,
                    "source_instance_id": row.source_instance_id,
                    "field": field,
                }
            )
            box.setState_(1 if enabled else 0)
            if field != "reset_celebrations":
                box.setEnabled_(row.master_enabled)
                channel_stack.addArrangedSubview_(box)
            else:
                inner.addArrangedSubview_(box)
            boxes.append(box)
        channel_stack.addArrangedSubview_(ui.make_hspacer())
        inner.addArrangedSubview_(channel_stack)
    stack.addArrangedSubview_(outer)
    return tuple(boxes)


def sync_provider_reset_checkboxes(target, settings) -> None:
    preferences = {preference.identity: preference for preference in settings.providers}
    for box in getattr(target, "_sidepulse_provider_reset_boxes", ()):
        payload = box.representedObject()
        if not isinstance(payload, dict):
            continue
        identity = (
            str(payload.get("provider_id") or ""),
            str(payload.get("source_instance_id") or "default"),
        )
        preference = preferences.get(identity)
        field = str(payload.get("field") or "")
        if preference is None or not hasattr(preference, field):
            continue
        box.setState_(1 if getattr(preference, field) else 0)
        if field != "reset_celebrations":
            box.setEnabled_(preference.reset_celebrations)


__all__ = [
    "ProviderResetChannel",
    "ProviderResetSettingsModel",
    "ProviderResetSettingsRow",
    "build_provider_reset_settings_model",
    "render_provider_reset_settings",
    "sync_provider_reset_checkboxes",
]
