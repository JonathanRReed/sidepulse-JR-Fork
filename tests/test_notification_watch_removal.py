from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from sidepulse.settings import AgentMonitorSettings, load_settings, save_settings


def test_legacy_foreign_notification_preferences_migrate_inertly(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "notification_blinks_enabled": True,
                "notification_app_colors": {
                    "com.apple.MobileSMS": "#34C759",
                },
                "signal_styles": {
                    "notification": {
                        "color": "#34C759",
                        "pattern": "blink",
                        "speed_seconds": 0.3,
                        "intensity": 1.0,
                    },
                },
                "completion_notification_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(path)

    assert not hasattr(settings, "notification_blinks_enabled")
    assert not hasattr(settings, "notification_app_colors")
    assert "notification" not in settings.signal_styles
    assert settings.completion_notification_enabled is False

    save_settings(settings, path)
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert "notification_blinks_enabled" not in migrated
    assert "notification_app_colors" not in migrated
    assert "notification" not in migrated["signal_styles"]
    assert migrated["completion_notification_enabled"] is False


def test_trusted_controller_has_no_foreign_notification_watcher_lifecycle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from sidepulse import status_bar

    monkeypatch.setattr(
        status_bar,
        "default_settings_path",
        lambda: tmp_path / "settings.json",
    )
    controller = status_bar.StatusBarController.alloc().init()

    assert not hasattr(status_bar.StatusBarController, "pollNotifications_")
    assert not hasattr(status_bar.StatusBarController, "notificationsChecked_")
    assert not hasattr(controller, "notification_watch_timer")
    assert not hasattr(controller, "notification_record_cursor")
    assert not hasattr(controller, "notification_watch_retry_at")
    assert not hasattr(controller, "notification_poll_in_flight")


def test_private_usernoted_watcher_is_not_packaged() -> None:
    assert importlib.util.find_spec("sidepulse.notification_watch") is None


def test_application_launch_schedules_no_foreign_notification_poll(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from sidepulse import status_bar

    selectors: list[str] = []

    class TimerAPI:
        @staticmethod
        def scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            _interval,
            _target,
            selector,
            _user_info,
            _repeats,
        ):
            selectors.append(selector)
            return SimpleNamespace(invalidate=lambda: None)

    class ThreadAPI:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self) -> None:
            return None

    button = SimpleNamespace(
        setTitle_=lambda _value: None,
        setImage_=lambda _value: None,
        setToolTip_=lambda _value: None,
    )
    status_item = SimpleNamespace(button=lambda: button)
    system_bar = SimpleNamespace(
        statusItemWithLength_=lambda _length: status_item,
    )
    class NotificationClient:
        def __init__(self) -> None:
            self.delegates: list[object] = []
            self.authorization_requests = 0

        def set_delegate(self, delegate) -> bool:
            self.delegates.append(delegate)
            return True

        def request_authorization(self, _completion=None) -> bool:
            self.authorization_requests += 1
            return True

    notification_client = NotificationClient()

    monkeypatch.setattr(
        status_bar,
        "default_settings_path",
        lambda: tmp_path / "settings.json",
    )
    monkeypatch.setattr(status_bar, "NSTimer", TimerAPI)
    monkeypatch.setattr(
        status_bar,
        "NSStatusBar",
        SimpleNamespace(systemStatusBar=lambda: system_bar),
    )
    monkeypatch.setattr(
        status_bar,
        "NSApp",
        SimpleNamespace(setActivationPolicy_=lambda _policy: None),
    )
    monkeypatch.setattr(status_bar, "image_for_symbol", lambda _symbol, _label: None)
    monkeypatch.setattr(status_bar.threading, "Thread", ThreadAPI)
    controller = status_bar.StatusBarController.alloc().init()
    controller.notification_client = notification_client
    controller.start_event_server = lambda: None
    controller.replay_debug_logs = lambda: None
    controller.refresh_ = lambda _sender: None
    controller.show_setup_window_if_needed = lambda: None
    controller.virtual_status_device = SimpleNamespace(
        show=lambda: None,
        hide=lambda: None,
    )

    controller.applicationDidFinishLaunching_(None)

    assert "pollNotifications:" not in selectors
    assert selectors == ["refresh:", "pollLid:"]
    assert notification_client.delegates == [controller]
    assert notification_client.authorization_requests == 0


def test_sidepulse_owned_completion_notifications_remain_configurable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    configured = AgentMonitorSettings().with_completion_notification_enabled(False)

    save_settings(configured, path)

    reloaded = load_settings(path)
    assert reloaded.completion_notification_enabled is False
    assert hasattr(reloaded, "with_completion_notification_enabled")
