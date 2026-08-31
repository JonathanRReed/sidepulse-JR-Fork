from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import sidepulse.provider_usage_controller_actions as actions
from sidepulse import provider_usage_sync_cache as sync_cache
from sidepulse.capacity_types import SourceKey
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.provider_facts import WorkIdentifier, WorkKey
from sidepulse.provider_instances import ProviderInstanceKey, ProviderInstanceProfile
from sidepulse.provider_usage_runtime import ProviderUsageState
from sidepulse.provider_usage_settings import default_provider_usage_settings
from sidepulse.provider_usage_sync import MergedProviderSync


class _Sender:
    def __init__(self, payload=None, identifier="", state=0) -> None:
        self._payload = payload
        self._identifier = identifier
        self._state = state

    def representedObject(self):
        return self._payload

    def identifier(self):
        return self._identifier

    def state(self):
        return self._state


class _Controller:
    def __init__(self, *, action_label="Retry") -> None:
        snapshot = SimpleNamespace(
            identity=("claude", "work"),
            action_label=action_label,
        )
        self.provider_usage_state = SimpleNamespace(snapshots=(snapshot,))
        self.refreshes = []
        self.opened = []

    def _request_provider_usage(self, **kwargs) -> None:
        self.refreshes.append(kwargs)

    def openProviderUsageCenter_(self, sender) -> None:
        self.opened.append(sender)


def test_sender_identity_and_refresh_scope_preserve_exact_instance():
    sender = _Sender(
        {"provider_id": "claude", "source_instance_id": "work"},
        "ignored",
    )

    assert actions.provider_action_identity(sender) == ("claude", "work")
    assert actions.provider_refresh_scope("claude", "work") == (
        ("claude", "work"),
    )
    assert actions.provider_refresh_scope("claude", "default") == ("claude",)


def test_generic_action_fallback_refreshes_and_opens_exact_instance(monkeypatch):
    controller = _Controller()
    sender = _Sender({"provider_id": "claude", "source_instance_id": "work"})
    monkeypatch.setattr(actions, "run_provider_usage_action", lambda *_args: False)

    actions.perform_provider_usage_action(
        controller,
        sender,
        open_center=True,
        log=lambda _message: None,
    )

    assert controller.refreshes == [
        {"force": True, "providers": (("claude", "work"),)}
    ]
    assert controller.opened == [sender]


def test_connect_action_is_armed_before_instance_scoped_claude_flow(monkeypatch):
    controller = _Controller(action_label="Reconnect Claude")
    sender = _Sender({"provider_id": "claude", "source_instance_id": "work"})
    connected = []
    monkeypatch.setattr(
        actions,
        "connect_claude_usage",
        lambda target, *, log, source_instance_id: connected.append(
            (target, log, source_instance_id)
        ),
    )

    def log(_message):
        return None

    actions.perform_provider_usage_action(
        controller,
        sender,
        open_center=False,
        log=log,
        wall_clock=lambda: 1234.5,
    )

    assert controller._sidepulse_reconnect_watch == ("claude", "work", 1234.5)
    assert connected == [(controller, log, "work")]
    assert controller.refreshes == []


def test_settings_snapshot_cache_projects_all_consumer_domains() -> None:
    settings = default_provider_usage_settings().with_profile(
        ProviderInstanceProfile(
            ProviderInstanceKey("claude", "work"),
            "Claude Work",
            open_session_action="terminal",
        )
    )
    service_updates = []
    controller = SimpleNamespace(
        _sidepulse_provider_usage_service=SimpleNamespace(
            note_settings_updated=service_updates.append,
        )
    )

    actions.apply_provider_usage_settings_snapshot(
        controller,
        settings,
        notify_service=True,
    )

    assert controller._sidepulse_provider_usage_settings_snapshot is settings
    assert controller._sidepulse_provider_presentation_settings.provider("claude")
    assert (
        controller._sidepulse_provider_instance_policies.visual.provider(
            "claude",
            "work",
        ).label
        == "Claude Work"
    )
    assert service_updates == [settings]


def test_provider_menu_toggle_updates_only_the_exact_instance() -> None:
    settings = default_provider_usage_settings().with_profile(
        ProviderInstanceProfile(
            ProviderInstanceKey("claude", "work"),
            "Claude Work",
        )
    )
    loaded = SimpleNamespace(settings=settings)
    writes = []
    service_updates = []
    controller = SimpleNamespace(
        _sidepulse_provider_usage_service=SimpleNamespace(
            note_settings_updated=service_updates.append,
        )
    )
    sender = _Sender(
        {"provider_id": "claude", "source_instance_id": "work"},
        state=0,
    )

    updated = actions.toggle_provider_menu_visibility(
        controller,
        sender,
        loader=lambda: loaded,
        saver=lambda value, *, loaded: writes.append((value, loaded)),
    )

    assert updated.preference("claude", "default").menu_visible is True
    assert updated.preference("claude", "work").menu_visible is False
    assert writes == [(updated, loaded)]
    assert controller._sidepulse_provider_usage_settings_snapshot is updated
    assert service_updates == [updated]


def test_settings_snapshot_change_invalidates_merged_sync_for_old_sharing_policy(
    monkeypatch,
) -> None:
    monkeypatch.setattr(sync_cache, "_memo", None)
    monkeypatch.setattr(sync_cache, "_memo_generation", 0)
    state = ProviderUsageState((), 1000.0, 1100.0, False)
    merged = MergedProviderSync((), (), 0, 0, 0, None, None)
    status_only = default_provider_usage_settings().with_profile(
        ProviderInstanceProfile(
            ProviderInstanceKey("codex", "default"),
            "Codex",
            remote_sharing_choice="status_only",
        )
    )
    never = status_only.with_profile(
        ProviderInstanceProfile(
            ProviderInstanceKey("codex", "default"),
            "Codex",
            remote_sharing_choice="never",
        )
    )
    controller = SimpleNamespace()
    actions.apply_provider_usage_settings_snapshot(controller, status_only)
    sync_cache.refresh_cached_merged_sync(
        state,
        loader=lambda _state: merged,
        sharing_signature=(("codex", "default", "status_only"),),
        monotonic=lambda: 100.0,
    )
    assert sync_cache.cached_merged_sync(state, monotonic=lambda: 100.0) is merged

    actions.apply_provider_usage_settings_snapshot(controller, never)

    assert sync_cache.cached_merged_sync(state, monotonic=lambda: 100.0) is None


def test_nonsharing_settings_change_preserves_fresh_merged_sync(monkeypatch) -> None:
    monkeypatch.setattr(sync_cache, "_memo", None)
    monkeypatch.setattr(sync_cache, "_memo_generation", 0)
    state = ProviderUsageState((), 1000.0, 1100.0, False)
    merged = MergedProviderSync((), (), 0, 0, 0, None, None)
    first = default_provider_usage_settings().with_profile(
        ProviderInstanceProfile(
            ProviderInstanceKey("codex", "default"),
            "Codex",
            remote_sharing_choice="status_only",
        )
    )
    renamed = first.with_profile(
        ProviderInstanceProfile(
            ProviderInstanceKey("codex", "default"),
            "Codex Personal",
            remote_sharing_choice="status_only",
        )
    )
    controller = SimpleNamespace()
    actions.apply_provider_usage_settings_snapshot(controller, first)
    sync_cache.refresh_cached_merged_sync(
        state,
        loader=lambda _state: merged,
        sharing_signature=(("codex", "default", "status_only"),),
        monotonic=lambda: 100.0,
    )

    actions.apply_provider_usage_settings_snapshot(controller, renamed)

    assert sync_cache.cached_merged_sync(state, monotonic=lambda: 100.0) is merged


def test_profile_session_action_overrides_only_an_exact_nondefault_status() -> None:
    settings = default_provider_usage_settings().with_profile(
        ProviderInstanceProfile(
            ProviderInstanceKey("claude", "work"),
            "Claude Work",
            open_session_action="terminal",
        )
    )
    controller = SimpleNamespace()
    actions.apply_provider_usage_settings_snapshot(controller, settings)
    status = AgentStatus(
        provider="claude",
        agent_id="claude:session:one",
        display_name="Claude work",
        mode=AgentMode.WORKING,
        updated_at=datetime.now(timezone.utc),
        event_name="PreToolUse",
        session_id="one",
        cwd="/tmp",
        work_key=WorkKey(
            SourceKey("claude", "hook", "work", "sessions"),
            WorkIdentifier("work:one"),
        ),
    )

    assert actions.profile_session_action(controller, status, None) == "terminal"
    assert actions.profile_session_action(controller, status, "app") == "app"


def test_profile_control_update_saves_only_the_exact_instance() -> None:
    settings = default_provider_usage_settings().with_profile(
        ProviderInstanceProfile(
            ProviderInstanceKey("claude", "work"),
            "Claude Work",
        )
    )
    loaded = SimpleNamespace(settings=settings)
    writes = []
    sender = SimpleNamespace(
        representedObject=lambda: {
            "provider_id": "claude",
            "source_instance_id": "work",
            "field_key": "label",
            "value": "Claude Work",
        },
        stringValue=lambda: "Client Claude",
    )
    controller = SimpleNamespace()

    updated = actions.update_provider_instance_profile(
        controller,
        sender,
        loader=lambda: loaded,
        saver=lambda value, *, loaded: writes.append((value, loaded)),
    )

    assert updated.profile("claude", "work").label == "Client Claude"
    assert updated.profile("claude").label == "Claude"
    assert writes == [(updated, loaded)]
    assert controller._sidepulse_provider_usage_settings_snapshot is updated


def test_profile_popup_update_reads_selected_exact_choice() -> None:
    settings = default_provider_usage_settings().with_profile(
        ProviderInstanceProfile(ProviderInstanceKey("claude", "work"), "Claude Work")
    )
    selected = SimpleNamespace(
        representedObject=lambda: {
            "provider_id": "claude",
            "source_instance_id": "work",
            "field_key": "retention_days",
            "value": 30,
        }
    )
    sender = SimpleNamespace(
        representedObject=lambda: None,
        selectedItem=lambda: selected,
    )

    updated = actions.update_provider_instance_profile(
        SimpleNamespace(),
        sender,
        loader=lambda: SimpleNamespace(settings=settings),
        saver=lambda _value, *, loaded: None,
    )

    assert updated.profile("claude", "work").retention_days == 30
