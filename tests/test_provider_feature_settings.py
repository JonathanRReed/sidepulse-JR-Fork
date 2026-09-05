from __future__ import annotations

import pytest

import sidepulse.provider_feature_settings as feature_settings
from sidepulse.provider_feature_settings import (
    ProviderCollectionSettings,
    ProviderPresentationSettings,
    ProviderSettingsChangeReceipt,
    ProviderSettingsChangeTracker,
    ProviderSyncSettingsProjection,
    project_provider_feature_settings,
)
from sidepulse.provider_usage_settings import default_provider_usage_settings
from sidepulse.provider_usage_sync_settings import default_provider_sync_settings


def test_projection_separates_collection_presentation_and_sync_settings() -> None:
    usage = (
        default_provider_usage_settings()
        .with_enabled("cursor", False)
        .with_menu_visible("devin", False)
    )
    sync = default_provider_sync_settings().with_device_id("mac-mini")

    projection = project_provider_feature_settings(usage, sync)

    assert type(projection.collection) is ProviderCollectionSettings
    assert type(projection.presentation) is ProviderPresentationSettings
    assert type(projection.sync) is ProviderSyncSettingsProjection
    assert projection.collection.provider("cursor").enabled is False
    assert projection.presentation.provider("devin").menu_visible is False
    assert projection.presentation.menu.privacy_mode is False
    assert projection.sync.device_id == "mac-mini"


def test_projections_are_immutable_and_do_not_cross_boundaries() -> None:
    projection = project_provider_feature_settings(
        default_provider_usage_settings(),
        default_provider_sync_settings(),
    )

    with pytest.raises(AttributeError):
        projection.collection.providers = ()  # type: ignore[misc]
    assert not hasattr(projection.collection, "menu_display")
    assert not hasattr(projection.collection, "sync")
    assert not hasattr(projection.presentation, "peers")
    assert not hasattr(projection.sync, "menu_display")


def test_reset_channel_defaults_reach_the_presentation_projection() -> None:
    projection = project_provider_feature_settings(
        default_provider_usage_settings(),
        default_provider_sync_settings(),
    )
    feature = projection.presentation.provider("codex")
    assert feature.reset_overlay is True
    assert feature.reset_hardware is True
    assert feature.reset_notification is True
    assert feature.reset_sound is True


def test_change_receipt_is_exact_bounded_and_monotonic() -> None:
    initial = project_provider_feature_settings(
        default_provider_usage_settings(),
        default_provider_sync_settings(),
    )
    changed = project_provider_feature_settings(
        default_provider_usage_settings()
        .with_enabled("cursor", False)
        .with_menu_flag("show_cost", False),
        default_provider_sync_settings(),
        previous=initial,
    )

    assert changed.receipt.revision == initial.receipt.revision + 1
    assert changed.receipt.changed_feature_ids == frozenset(
        {
            "collection.cursor.enabled",
            "presentation.menu.show_cost",
        }
    )
    assert len(changed.receipt.changed_feature_ids) <= ProviderSettingsChangeReceipt.MAX_FEATURE_IDS


def test_privacy_mode_is_a_presentation_only_setting() -> None:
    initial = project_provider_feature_settings(
        default_provider_usage_settings(),
        default_provider_sync_settings(),
    )
    changed = project_provider_feature_settings(
        default_provider_usage_settings().with_menu_flag("privacy_mode", True),
        default_provider_sync_settings(),
        previous=initial,
    )

    assert changed.presentation.menu.privacy_mode is True
    assert "presentation.menu.privacy_mode" in changed.receipt.changed_feature_ids
    assert not hasattr(changed.collection, "privacy_mode")


def test_change_receipt_rejects_non_monotonic_revision() -> None:
    initial = project_provider_feature_settings(
        default_provider_usage_settings(),
        default_provider_sync_settings(),
    )

    with pytest.raises(ValueError, match="monotonic"):
        project_provider_feature_settings(
            default_provider_usage_settings(),
            default_provider_sync_settings(),
            previous=initial,
            revision=initial.receipt.revision,
        )


def test_tracker_advances_revision_and_reports_empty_receipt_for_unchanged_values() -> None:
    tracker = ProviderSettingsChangeTracker()
    settings = default_provider_usage_settings()
    sync = default_provider_sync_settings()

    first = tracker.update(settings, sync)
    second = tracker.observe(settings, sync)

    assert first.receipt.revision == 0
    assert second.receipt.revision == 1
    assert second.receipt.changed_feature_ids == frozenset()
    assert tracker.projection is second


def test_collection_options_and_sync_changes_have_domain_ids() -> None:
    before = project_provider_feature_settings(
        default_provider_usage_settings(),
        default_provider_sync_settings(),
    )
    after = project_provider_feature_settings(
        default_provider_usage_settings().with_option("devin", "organization", "org"),
        default_provider_sync_settings().with_device_id("mac-mini"),
        previous=before,
    )

    assert after.receipt.changed_feature_ids == frozenset(
        {"collection.devin.options", "sync.device_id"}
    )


def test_instance_policy_projection_keeps_domains_exact_and_private() -> None:
    settings = default_provider_usage_settings()

    projection = feature_settings.project_instance_policies(settings)

    assert type(projection) is feature_settings.ProviderInstancePolicyProjection
    assert type(projection.visual) is feature_settings.ProviderInstanceVisualProjection
    assert type(projection.retention) is feature_settings.ProviderInstanceRetentionProjection
    assert type(projection.sharing) is feature_settings.ProviderInstanceSharingProjection
    assert type(projection.session_action) is feature_settings.ProviderInstanceSessionActionProjection
    assert projection.visual.provider("claude").label == "Claude"
    assert projection.visual.provider("claude").color_override is None
    assert projection.retention.provider("claude").retention_days == 7
    assert projection.sharing.provider("claude").remote_sharing_choice == "never"
    assert projection.session_action.provider("claude").open_session_action == "app"
    assert not hasattr(projection.visual.provider("claude"), "consent_reference")
    assert not hasattr(projection.visual.provider("claude"), "credential_account_reference")
    assert not hasattr(projection.retention.provider("claude"), "consent_reference")
    assert not hasattr(projection.sharing.provider("claude"), "credential_account_reference")
