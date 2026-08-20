"""The master brightness dial: one click, both surfaces.

"Last night it was really bright" -- the only controls were per-device
sliders buried in Settings. The dial is a single scale composed into
BOTH effective-brightness paths (ambient and signal), reachable from the
dropdown's Brightness submenu, clamped so dim is never off-in-disguise.
"""

from __future__ import annotations

from types import SimpleNamespace

from test_sidepulse import isolate_controller

from sidepulse._settings_legacy import AgentMonitorSettings
from sidepulse.status_bar_legacy import BRIGHTNESS_PRESET_CHOICES, StatusBarDevice


def _device() -> StatusBarDevice:
    from pathlib import Path

    return StatusBarDevice(
        device_id="sidepulse:pro:serial:test",
        name="Test Pro",
        root=Path("/tmp/nonexistent-test-device"),
        target=Path("/tmp/nonexistent-test-device/LEDS.LED"),
        connected=True,
        display="agent",
        brightness=200,
    )


def test_scale_round_trips_and_clamps(tmp_path) -> None:
    import json

    from sidepulse._settings_legacy import load_settings

    settings = AgentMonitorSettings().with_global_brightness_scale(0.5)
    assert settings.global_brightness_scale == 0.5
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings.to_dict()))
    assert load_settings(path).global_brightness_scale == 0.5
    assert AgentMonitorSettings().with_global_brightness_scale(0.0).global_brightness_scale == 0.05
    assert AgentMonitorSettings().with_global_brightness_scale(9.0).global_brightness_scale == 1.0


def test_the_dial_scales_both_brightness_paths(request) -> None:
    case = SimpleNamespace(
        addCleanup=lambda fn, *a, **k: request.addfinalizer(lambda: fn(*a, **k)),
    )
    isolate_controller(case)
    controller = case.controller
    device = _device()

    full_ambient = controller.effective_brightness_for_device(device)
    full_signal = controller.effective_signal_brightness_for_device(device)

    controller.settings = controller.settings.with_global_brightness_scale(0.5)
    assert controller.effective_brightness_for_device(device) <= full_ambient * 0.55
    assert controller.effective_signal_brightness_for_device(device) <= full_signal * 0.55


def test_the_menu_action_persists_the_preset(request) -> None:
    case = SimpleNamespace(
        addCleanup=lambda fn, *a, **k: request.addfinalizer(lambda: fn(*a, **k)),
    )
    isolate_controller(case)
    controller = case.controller
    controller.refresh_ = lambda *_args: None  # the isolated guard forbids
    label, value = BRIGHTNESS_PRESET_CHOICES[0]
    assert label == "Dim"
    sender = SimpleNamespace(representedObject=lambda: value)
    controller.setGlobalBrightness_(sender)
    assert abs(controller.settings.global_brightness_scale - value) < 0.001
    # And nonsense from a stale menu is refused, not crashed on.
    controller.setGlobalBrightness_(SimpleNamespace(representedObject=lambda: None))
    assert abs(controller.settings.global_brightness_scale - value) < 0.001
