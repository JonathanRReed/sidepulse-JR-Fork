"""Exact-instance actions for the provider Usage Center and menu.

The AppKit status-bar facade exposes selectors only. Identity parsing,
refresh scoping, reconnect arming, and action routing live here so those
rules have one testable owner.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .provider_browser_access import run_provider_usage_action
from .provider_feature_settings import (
    ProviderInstancePolicyProjection,
    project_instance_policies,
    project_presentation_settings,
)
from .provider_instances import ProviderInstanceProfile
from .provider_usage_feedback import connect_claude_usage
from .provider_usage_settings import (
    ProviderUsageSettings,
    load_provider_usage_settings,
    save_provider_usage_settings,
)
from .provider_usage_sync_cache import (
    invalidate_cached_merged_sync,
    sharing_projection_signature,
)
from .session_actions import resolve_profile_session_action_for_status


def apply_provider_usage_settings_snapshot(
    controller,
    settings: ProviderUsageSettings,
    *,
    notify_service: bool = False,
) -> None:
    """Publish one durable snapshot and its privacy-safe consumer views."""

    if type(settings) is not ProviderUsageSettings:
        raise TypeError("expected ProviderUsageSettings")
    policies = project_instance_policies(settings)
    sharing_signature = sharing_projection_signature(policies.sharing)
    previous_sharing_signature = getattr(
        controller,
        "_sidepulse_provider_sync_sharing_signature",
        None,
    )
    if previous_sharing_signature != sharing_signature:
        invalidate_cached_merged_sync(sharing_signature=sharing_signature)
    controller._sidepulse_provider_sync_sharing_signature = sharing_signature
    controller._sidepulse_provider_usage_settings_snapshot = settings
    controller._sidepulse_provider_presentation_settings = (
        project_presentation_settings(settings)
    )
    controller._sidepulse_provider_instance_policies = policies
    if notify_service:
        service = getattr(controller, "_sidepulse_provider_usage_service", None)
        notify = getattr(service, "note_settings_updated", None)
        if callable(notify):
            notify(settings)


def profile_session_action(controller, status, action: str | None) -> str | None:
    """Apply only an exact nondefault profile override before legacy routing."""

    if action is not None:
        return action
    policies = getattr(controller, "_sidepulse_provider_instance_policies", None)
    if type(policies) is not ProviderInstancePolicyProjection:
        return None
    resolution = resolve_profile_session_action_for_status(
        policies.session_action,
        status,
    )
    return resolution.action if resolution.has_override else None


_PROFILE_FIELD_KEYS = frozenset(
    {
        "label",
        "color_override",
        "retention_days",
        "remote_sharing_choice",
        "open_session_action",
    }
)


def _profile_control_payload(sender) -> dict[str, object]:
    selected = getattr(sender, "selectedItem", None)
    selected_item = selected() if callable(selected) else None
    represented = getattr(selected_item or sender, "representedObject", None)
    payload = represented() if callable(represented) else None
    if not isinstance(payload, dict):
        raise ValueError("missing provider profile control payload")
    return payload


def update_provider_instance_profile(
    controller,
    sender,
    *,
    loader=load_provider_usage_settings,
    saver=save_provider_usage_settings,
) -> ProviderUsageSettings:
    """Validate and persist one exact non-secret provider profile choice."""

    payload = _profile_control_payload(sender)
    provider_id = payload.get("provider_id")
    source_instance_id = payload.get("source_instance_id")
    field_key = payload.get("field_key")
    if (
        type(provider_id) is not str
        or type(source_instance_id) is not str
        or field_key not in _PROFILE_FIELD_KEYS
    ):
        raise ValueError("invalid provider profile control payload")
    value = payload.get("value")
    if field_key in {"label", "color_override"}:
        string_value = getattr(sender, "stringValue", None)
        if not callable(string_value):
            raise ValueError("provider profile text control has no value")
        value = str(string_value()).strip()
        if field_key == "color_override":
            value = value.upper() or None

    loaded = loader()
    profile = loaded.settings.profile(provider_id, source_instance_id)
    values = {
        "label": profile.label,
        "color_override": profile.color_override,
        "retention_days": profile.retention_days,
        "remote_sharing_choice": profile.remote_sharing_choice,
        "open_session_action": profile.open_session_action,
    }
    values[field_key] = value
    updated_profile = ProviderInstanceProfile(
        profile.key,
        values["label"],
        color_override=values["color_override"],
        retention_days=values["retention_days"],
        remote_sharing_choice=values["remote_sharing_choice"],
        open_session_action=values["open_session_action"],
    )
    updated = loaded.settings.with_profile(updated_profile)
    saver(updated, loaded=loaded)
    apply_provider_usage_settings_snapshot(
        controller,
        updated,
        notify_service=True,
    )
    controller._menu_signature = None
    return updated


def provider_action_identity(sender) -> tuple[str, str]:
    represented = getattr(sender, "representedObject", None)
    payload = represented() if callable(represented) else None
    if isinstance(payload, dict):
        return (
            str(payload.get("provider_id") or ""),
            str(payload.get("source_instance_id") or "default"),
        )
    identifier = getattr(sender, "identifier", None)
    return (
        str(identifier() or "") if callable(identifier) else "",
        "default",
    )


def toggle_provider_menu_visibility(
    controller,
    sender,
    *,
    loader=load_provider_usage_settings,
    saver=save_provider_usage_settings,
) -> ProviderUsageSettings:
    """Persist menu visibility for the exact represented provider instance."""

    provider_id, source_instance_id = provider_action_identity(sender)
    state = getattr(sender, "state", None)
    if not provider_id or not callable(state):
        raise ValueError("invalid provider menu control")
    loaded = loader()
    updated = loaded.settings.with_menu_visible(
        provider_id,
        bool(state()),
        source_instance_id=source_instance_id,
    )
    saver(updated, loaded=loaded)
    controller._menu_signature = None
    apply_provider_usage_settings_snapshot(
        controller,
        updated,
        notify_service=True,
    )
    return updated


def provider_refresh_scope(
    provider_id: str,
    source_instance_id: str = "default",
) -> tuple[str | tuple[str, str], ...]:
    if source_instance_id == "default":
        return (provider_id,)
    return ((provider_id, source_instance_id),)


def provider_action_label(
    controller,
    provider_id: str,
    source_instance_id: str = "default",
) -> str:
    state = getattr(controller, "provider_usage_state", None)
    snapshot = next(
        (
            item
            for item in getattr(state, "snapshots", ())
            if item.identity == (provider_id, source_instance_id)
        ),
        None,
    )
    return str(getattr(snapshot, "action_label", "") or "")


def claude_action_wants_connect(
    controller,
    source_instance_id: str = "default",
) -> bool:
    label = provider_action_label(
        controller,
        "claude",
        source_instance_id,
    ).lower()
    return "connect" in label or not label


def connect_claude_usage_action(
    controller,
    source_instance_id: str,
    *,
    log,
    wall_clock: Callable[[], float] = time.time,
) -> None:
    controller._sidepulse_reconnect_watch = (
        "claude",
        source_instance_id,
        wall_clock(),
    )
    connect_claude_usage(
        controller,
        log=log,
        source_instance_id=source_instance_id,
    )


def perform_provider_usage_action(
    controller,
    sender,
    *,
    open_center: bool,
    log,
    wall_clock: Callable[[], float] = time.time,
) -> None:
    provider_id, source_instance_id = provider_action_identity(sender)
    if provider_id == "claude" and claude_action_wants_connect(
        controller,
        source_instance_id,
    ):
        connect_claude_usage_action(
            controller,
            source_instance_id,
            log=log,
            wall_clock=wall_clock,
        )
        return
    if provider_id and run_provider_usage_action(
        controller,
        provider_id,
        source_instance_id,
    ):
        return
    if provider_id:
        controller._request_provider_usage(
            force=True,
            providers=provider_refresh_scope(provider_id, source_instance_id),
        )
    if open_center:
        controller.openProviderUsageCenter_(sender)


__all__ = [
    "apply_provider_usage_settings_snapshot",
    "claude_action_wants_connect",
    "connect_claude_usage_action",
    "perform_provider_usage_action",
    "profile_session_action",
    "provider_action_identity",
    "provider_action_label",
    "provider_refresh_scope",
    "toggle_provider_menu_visibility",
    "update_provider_instance_profile",
]
