from __future__ import annotations

import pytest
from Foundation import NSObject

import sidepulse.settings_category_runtime as settings_runtime
from sidepulse.provider_feature_settings import (
    ProviderInstancePolicyProjection,
    ProviderInstanceRetentionPolicy,
    ProviderInstanceRetentionProjection,
    ProviderInstanceSessionActionPolicy,
    ProviderInstanceSessionActionProjection,
    ProviderInstanceSharingPolicy,
    ProviderInstanceSharingProjection,
    ProviderInstanceVisualPolicy,
    ProviderInstanceVisualProjection,
    project_instance_policies,
)
from sidepulse.provider_instances import ProviderInstanceKey, ProviderInstanceProfile
from sidepulse.provider_usage_settings import default_provider_usage_settings
from sidepulse.settings_category_runtime import (
    MAX_PROVIDER_PROFILE_SETTINGS_ROWS,
    ProviderInstanceProfileSettingsModel,
    ProviderInstanceProfileSettingsRow,
    _native_usage_pane,
    build_provider_instance_profile_settings_model,
    provider_instance_profile_settings_row,
)


class _SettingsTarget(NSObject):
    pass


def _settings_model() -> ProviderInstanceProfileSettingsModel:
    settings = default_provider_usage_settings().with_profile(
        ProviderInstanceProfile(
            ProviderInstanceKey("claude", "work"),
            "Claude Work",
            color_override="#AABBCC",
            retention_days=30,
            remote_sharing_choice="status_only",
            open_session_action="terminal",
        )
    )
    return build_provider_instance_profile_settings_model(
        project_instance_policies(settings)
    )


def test_render_model_keeps_five_profile_choices_on_the_exact_instance() -> None:
    model = _settings_model()

    row = provider_instance_profile_settings_row(model, "claude", "work")

    assert type(row) is ProviderInstanceProfileSettingsRow
    assert (row.provider_id, row.source_instance_id, row.heading) == (
        "claude",
        "work",
        "Claude Work",
    )
    assert [field.key for field in row.fields] == [
        "label",
        "color_override",
        "retention_days",
        "remote_sharing_choice",
        "open_session_action",
    ]
    assert [field.control_kind for field in row.fields] == [
        "text",
        "color",
        "choice",
        "choice",
        "choice",
    ]
    assert {field.key: field.value for field in row.fields} == {
        "label": "Claude Work",
        "color_override": "#AABBCC",
        "retention_days": 30,
        "remote_sharing_choice": "status_only",
        "open_session_action": "terminal",
    }


def test_render_model_supplies_bounded_human_choices_for_discrete_controls() -> None:
    row = provider_instance_profile_settings_row(_settings_model(), "claude", "work")
    fields = {field.key: field for field in row.fields}

    assert [(choice.value, choice.label) for choice in fields["retention_days"].options] == [
        (0, "Don't keep history"),
        (7, "7 days"),
        (30, "30 days"),
        (90, "90 days"),
    ]
    assert [choice.value for choice in fields["remote_sharing_choice"].options] == [
        "never",
        "status_only",
    ]
    assert [choice.value for choice in fields["open_session_action"].options] == [
        "app",
        "terminal",
        "vscode",
    ]
    assert fields["label"].options == ()
    assert fields["color_override"].options == ()


def test_render_model_exposes_no_consent_or_credential_fields() -> None:
    row = provider_instance_profile_settings_row(_settings_model(), "claude", "work")
    rendered = repr(row).casefold()

    assert "consent" not in rendered
    assert "credential" not in rendered
    assert "token" not in rendered
    assert all(
        not hasattr(field, "consent_reference")
        and not hasattr(field, "credential_account_reference")
        for field in row.fields
    )


def test_render_model_rejects_policy_domains_with_different_instance_keys() -> None:
    projection = project_instance_policies(default_provider_usage_settings())
    mismatched = ProviderInstancePolicyProjection(
        visual=projection.visual,
        retention=projection.retention,
        sharing=ProviderInstanceSharingProjection(projection.sharing.providers[:-1]),
        session_action=projection.session_action,
    )

    with pytest.raises(ValueError, match="same exact provider instances"):
        build_provider_instance_profile_settings_model(mismatched)


def test_render_model_refuses_more_rows_than_the_settings_surface_can_bound() -> None:
    identities = tuple(
        ("claude", f"profile-{index}")
        for index in range(MAX_PROVIDER_PROFILE_SETTINGS_ROWS + 1)
    )
    projection = ProviderInstancePolicyProjection(
        visual=ProviderInstanceVisualProjection(
            tuple(
                ProviderInstanceVisualPolicy(provider, source, source, None)
                for provider, source in identities
            )
        ),
        retention=ProviderInstanceRetentionProjection(
            tuple(
                ProviderInstanceRetentionPolicy(provider, source, 7)
                for provider, source in identities
            )
        ),
        sharing=ProviderInstanceSharingProjection(
            tuple(
                ProviderInstanceSharingPolicy(provider, source, "never")
                for provider, source in identities
            )
        ),
        session_action=ProviderInstanceSessionActionProjection(
            tuple(
                ProviderInstanceSessionActionPolicy(provider, source, "app")
                for provider, source in identities
            )
        ),
    )

    with pytest.raises(ValueError, match="too many provider profiles"):
        build_provider_instance_profile_settings_model(projection)


def _rendered_settings_target():
    target = _SettingsTarget.alloc().init()
    target._sidepulse_provider_usage_settings_snapshot = (
        default_provider_usage_settings().with_profile(
            ProviderInstanceProfile(
                ProviderInstanceKey("claude", "work"),
                "Claude Work",
                color_override="#AABBCC",
                retention_days=30,
                remote_sharing_choice="status_only",
                open_session_action="terminal",
            )
        )
    )
    _native_usage_pane(target)
    return target


def test_native_usage_pane_renders_five_accessible_controls_for_one_exact_instance() -> None:
    target = _rendered_settings_target()
    controls = target._sidepulse_provider_profile_settings_controls

    expected = {
        "label": "Claude Work",
        "color_override": "#AABBCC",
        "retention_days": 30,
        "remote_sharing_choice": "status_only",
        "open_session_action": "terminal",
    }
    for field_key, value in expected.items():
        control = controls[("claude", "work", field_key)]
        assert str(control.action()) == "updateProviderInstanceProfile:"
        assert control.target() is target
        assert control.representedObject() == {
            "provider_id": "claude",
            "source_instance_id": "work",
            "field_key": field_key,
            "value": value,
        }
        assert control.accessibilityLabel().startswith("Claude Work, ")
        assert control.accessibilityHelp()

    assert controls[("claude", "work", "label")].stringValue() == "Claude Work"
    assert controls[("claude", "work", "color_override")].stringValue() == "#AABBCC"
    for field_key in (
        "retention_days",
        "remote_sharing_choice",
        "open_session_action",
    ):
        popup = controls[("claude", "work", field_key)]
        assert popup.selectedItem().representedObject() == popup.representedObject()


def test_native_usage_pane_bounds_profile_cards_and_every_choice_payload() -> None:
    target = _rendered_settings_target()
    settings = target._sidepulse_provider_usage_settings_snapshot
    cards = target._sidepulse_provider_profile_settings_cards
    controls = target._sidepulse_provider_profile_settings_controls

    assert len(cards) == len(settings.providers)
    assert len(cards) <= MAX_PROVIDER_PROFILE_SETTINGS_ROWS
    assert len(controls) == len(cards) * 5
    work_card = cards[("claude", "work")]
    assert work_card.accessibilityRole() == "AXGroup"
    assert work_card.accessibilityLabel() == (
        "Claude Work provider profile, Claude, work"
    )

    for (provider_id, source_instance_id, field_key), control in controls.items():
        payload = control.representedObject()
        assert payload["provider_id"] == provider_id
        assert payload["source_instance_id"] == source_instance_id
        assert payload["field_key"] == field_key
        assert "consent" not in repr(payload).casefold()
        assert "credential" not in repr(payload).casefold()
        if field_key not in {"label", "color_override"}:
            for item in (
                control.itemAtIndex_(index)
                for index in range(control.numberOfItems())
            ):
                option_payload = item.representedObject()
                assert option_payload["provider_id"] == provider_id
                assert option_payload["source_instance_id"] == source_instance_id
                assert option_payload["field_key"] == field_key


def test_profile_save_refreshes_cached_model_card_and_committed_control_payloads() -> None:
    target = _rendered_settings_target()
    sender = target._sidepulse_provider_profile_settings_controls[
        ("claude", "work", "label")
    ]
    sender.setStringValue_("Client Claude")
    committed = target._sidepulse_provider_usage_settings_snapshot.with_profile(
        ProviderInstanceProfile(
            ProviderInstanceKey("claude", "work"),
            "Client Claude",
            color_override="#DDEEFF",
            retention_days=7,
            remote_sharing_choice="never",
            open_session_action="vscode",
        )
    )

    saved = settings_runtime.save_provider_instance_profile_setting(
        target,
        sender,
        updater=lambda _target, _sender: committed,
        log=lambda _message: None,
    )

    assert saved is True
    row = provider_instance_profile_settings_row(
        target._sidepulse_provider_profile_settings_model,
        "claude",
        "work",
    )
    assert row.heading == "Client Claude"
    card = target._sidepulse_provider_profile_settings_cards[("claude", "work")]
    assert card.arrangedSubviews()[0].stringValue() == "Client Claude"
    assert card.accessibilityLabel() == (
        "Client Claude provider profile, Claude, work"
    )
    controls = target._sidepulse_provider_profile_settings_controls
    expected = {
        "label": "Client Claude",
        "color_override": "#DDEEFF",
        "retention_days": 7,
        "remote_sharing_choice": "never",
        "open_session_action": "vscode",
    }
    for field_key, value in expected.items():
        control = controls[("claude", "work", field_key)]
        assert control.representedObject()["value"] == value
        assert control.accessibilityLabel().startswith("Client Claude, ")
    assert controls[("claude", "work", "label")].stringValue() == "Client Claude"
    assert controls[("claude", "work", "color_override")].stringValue() == (
        "#DDEEFF"
    )
    for field_key in (
        "retention_days",
        "remote_sharing_choice",
        "open_session_action",
    ):
        control = controls[("claude", "work", field_key)]
        assert control.selectedItem().representedObject()["value"] == expected[field_key]


def test_profile_text_save_failure_restores_current_committed_snapshot() -> None:
    target = _rendered_settings_target()
    committed = target._sidepulse_provider_usage_settings_snapshot.with_profile(
        ProviderInstanceProfile(
            ProviderInstanceKey("claude", "work"),
            "Committed Claude",
            color_override="#112233",
            retention_days=7,
            remote_sharing_choice="never",
            open_session_action="app",
        )
    )
    target._sidepulse_provider_usage_settings_snapshot = committed
    sender = target._sidepulse_provider_profile_settings_controls[
        ("claude", "work", "label")
    ]
    sender.setStringValue_("Unsaved Draft")

    saved = settings_runtime.save_provider_instance_profile_setting(
        target,
        sender,
        updater=lambda _target, _sender: (_ for _ in ()).throw(OSError("disk full")),
        log=lambda _message: None,
    )

    assert saved is False
    assert sender.stringValue() == "Committed Claude"
    assert sender.representedObject()["value"] == "Committed Claude"
    card = target._sidepulse_provider_profile_settings_cards[("claude", "work")]
    assert card.arrangedSubviews()[0].stringValue() == "Committed Claude"
    assert card.accessibilityLabel() == (
        "Committed Claude provider profile, Claude, work"
    )


def test_profile_popup_save_failure_restores_current_committed_selection() -> None:
    target = _rendered_settings_target()
    committed = target._sidepulse_provider_usage_settings_snapshot.with_profile(
        ProviderInstanceProfile(
            ProviderInstanceKey("claude", "work"),
            "Claude Work",
            color_override="#AABBCC",
            retention_days=7,
            remote_sharing_choice="never",
            open_session_action="app",
        )
    )
    target._sidepulse_provider_usage_settings_snapshot = committed
    sender = target._sidepulse_provider_profile_settings_controls[
        ("claude", "work", "retention_days")
    ]
    sender.selectItemAtIndex_(3)
    assert sender.selectedItem().representedObject()["value"] == 90

    saved = settings_runtime.save_provider_instance_profile_setting(
        target,
        sender,
        updater=lambda _target, _sender: (_ for _ in ()).throw(OSError("disk full")),
        log=lambda _message: None,
    )

    assert saved is False
    assert sender.representedObject()["value"] == 7
    assert sender.selectedItem().representedObject()["value"] == 7
