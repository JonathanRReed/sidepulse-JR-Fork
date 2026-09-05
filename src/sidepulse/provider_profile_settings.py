"""Typed provider-profile models shared by the native Settings host."""

from __future__ import annotations

from typing import Literal, NamedTuple

from .provider_account_identity import (
    configured_user_alias,
    project_provider_account_identity,
)

MAX_PROVIDER_PROFILE_SETTINGS_ROWS = 128

ProviderProfileSettingsChoice = NamedTuple(  # noqa: UP014
    "ProviderProfileSettingsChoice",
    [("value", str | int | None), ("label", str)],
)
ProviderProfileSettingsField = NamedTuple(  # noqa: UP014
    "ProviderProfileSettingsField",
    [
        (
            "key",
            Literal[
                "label",
                "color_override",
                "retention_days",
                "remote_sharing_choice",
                "open_session_action",
            ],
        ),
        ("label", str),
        ("control_kind", Literal["text", "color", "choice"]),
        ("value", str | int | None),
        ("options", tuple[ProviderProfileSettingsChoice, ...]),
        ("help_text", str),
    ],
)
ProviderInstanceProfileSettingsRow = NamedTuple(  # noqa: UP014
    "ProviderInstanceProfileSettingsRow",
    [
        ("provider_id", str),
        ("source_instance_id", str),
        ("heading", str),
        ("fields", tuple[ProviderProfileSettingsField, ...]),
    ],
)
ProviderInstanceProfileSettingsModel = NamedTuple(  # noqa: UP014
    "ProviderInstanceProfileSettingsModel",
    [("rows", tuple[ProviderInstanceProfileSettingsRow, ...])],
)
_RETENTION_SETTINGS_CHOICES = (
    ProviderProfileSettingsChoice(0, "Don't keep history"),
    ProviderProfileSettingsChoice(7, "7 days"),
    ProviderProfileSettingsChoice(30, "30 days"),
    ProviderProfileSettingsChoice(90, "90 days"),
)
_REMOTE_SHARING_SETTINGS_CHOICES = (
    ProviderProfileSettingsChoice("never", "This Mac only"),
    ProviderProfileSettingsChoice("status_only", "Quota status only"),
)
_OPEN_SESSION_SETTINGS_CHOICES = (
    ProviderProfileSettingsChoice("app", "Provider app"),
    ProviderProfileSettingsChoice("terminal", "Terminal"),
    ProviderProfileSettingsChoice("vscode", "Visual Studio Code"),
)


def safe_provider_instance_label(
    provider_id: str,
    source_instance_id: str,
    visual_label: str | None,
    *,
    privacy_mode: bool = False,
    account_number: int | None = None,
) -> str:
    alias = configured_user_alias(
        provider_id=provider_id,
        source_instance_id=source_instance_id,
        visual_label=visual_label,
    )
    identity = project_provider_account_identity(
        provider_id=provider_id,
        source_instance_id=source_instance_id,
        account_label=None,
        user_alias=alias,
        privacy_mode=privacy_mode,
    )
    if privacy_mode and account_number is not None:
        return f"{identity.primary_label} Account {account_number}"
    return identity.primary_label


def safe_provider_instance_control_id(
    provider_id: str,
    source_instance_id: str,
    field_key: str,
) -> str:
    identity = project_provider_account_identity(
        provider_id=provider_id,
        source_instance_id=source_instance_id,
        account_label=None,
    )
    return f"provider-profile:{provider_id}:{identity.collision_suffix}:{field_key}"


def build_provider_instance_profile_settings_model(
    policies,
    *,
    privacy_mode: bool = False,
) -> ProviderInstanceProfileSettingsModel:
    """Build the privacy-safe, AppKit-independent instance settings model."""
    from .provider_feature_settings import ProviderInstancePolicyProjection

    if type(policies) is not ProviderInstancePolicyProjection:
        raise TypeError("expected ProviderInstancePolicyProjection")
    visual_items = policies.visual.providers
    if len(visual_items) > MAX_PROVIDER_PROFILE_SETTINGS_ROWS:
        raise ValueError("too many provider profiles for Settings")
    identities = {item.identity for item in visual_items}
    if any(
        {item.identity for item in projection.providers} != identities
        for projection in (
            policies.retention,
            policies.sharing,
            policies.session_action,
        )
    ):
        raise ValueError("profile policy domains must contain the same exact provider instances")

    provider_counts: dict[str, int] = {}
    for visual in visual_items:
        provider_counts[visual.provider_id] = provider_counts.get(visual.provider_id, 0) + 1
    provider_ordinals: dict[str, int] = {}
    rows = []
    for visual in visual_items:
        provider_id, source_instance_id = visual.identity
        provider_ordinals[provider_id] = provider_ordinals.get(provider_id, 0) + 1
        safe_label = safe_provider_instance_label(
            provider_id,
            source_instance_id,
            visual.label,
            privacy_mode=privacy_mode,
            account_number=(
                provider_ordinals[provider_id]
                if provider_counts[provider_id] > 1
                else None
            ),
        )
        retention = policies.retention.provider(provider_id, source_instance_id)
        sharing = policies.sharing.provider(provider_id, source_instance_id)
        session_action = policies.session_action.provider(provider_id, source_instance_id)
        rows.append(
            ProviderInstanceProfileSettingsRow(
                provider_id,
                source_instance_id,
                safe_label,
                (
                    ProviderProfileSettingsField(
                        "label", "Name", "text", safe_label, (),
                        "A local name for this exact provider account or profile.",
                    ),
                    ProviderProfileSettingsField(
                        "color_override", "Accent", "color", visual.color_override, (),
                        "Use a custom accent, or keep the provider's default color.",
                    ),
                    ProviderProfileSettingsField(
                        "retention_days", "Usage history", "choice",
                        retention.retention_days, _RETENTION_SETTINGS_CHOICES,
                        "Choose how long this Mac keeps percentage history for this instance.",
                    ),
                    ProviderProfileSettingsField(
                        "remote_sharing_choice", "Remote sharing", "choice",
                        sharing.remote_sharing_choice, _REMOTE_SHARING_SETTINGS_CHOICES,
                        "Choose whether other configured Macs can receive quota status.",
                    ),
                    ProviderProfileSettingsField(
                        "open_session_action", "Open session with", "choice",
                        session_action.open_session_action, _OPEN_SESSION_SETTINGS_CHOICES,
                        "Choose how JR-Bar opens work from this exact instance.",
                    ),
                ),
            )
        )
    return ProviderInstanceProfileSettingsModel(tuple(rows))


def provider_instance_profile_settings_row(
    model: ProviderInstanceProfileSettingsModel,
    provider_id: str,
    source_instance_id: str = "default",
) -> ProviderInstanceProfileSettingsRow:
    """Return one exact row, never a provider-level fallback."""
    if type(model) is not ProviderInstanceProfileSettingsModel:
        raise TypeError("expected ProviderInstanceProfileSettingsModel")
    return next(
        row
        for row in model.rows
        if (row.provider_id, row.source_instance_id) == (provider_id, source_instance_id)
    )
