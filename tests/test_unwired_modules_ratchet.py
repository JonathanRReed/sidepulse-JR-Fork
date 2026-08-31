"""Unreachable code is how this project keeps losing features.

Every serious bug found in this round was reachability, not logic: all six
blend modes unreachable, two log janitors with zero callers, a 1,139-line
capacity presentation layer nothing imports, and a threshold detector I
duplicated because I did not find the one that already existed.

So the set of unwired modules is pinned. Adding to it requires editing this
list, which is the moment to ask "why is this not reachable yet?". Removing
from it -- by wiring a module up -- is always allowed.

The list is empty now, and it stayed empty by deciding each entry rather
than re-describing it. `capacity_view` and `capacity_history_store` were
wired -- the "Why Is It Doing That?" panel says which capacity window was
refused and why, and remembers how the numbers moved. `provider_runtime`,
`delivery_ledger_store` and `reply_classifier` were deleted: the first two
were second implementations of jobs the shipped code already does
(`capacity_refresh` plus the status bar's own workers, and the activity
ledger's own store), and the third classified message replies for an inbox
this product does not have.

KNOWN THIS RATCHET CANNOT SEE: it measures IMPORTS, not CALLS. A module
imported at the top of a live file passes even when nothing ever calls into
it. `delivery_ledger` was the live example -- `interruption_policy` imported
it for a planner nothing invoked, so a ~700-line delivery-planning subsystem
read as reachable here while being as dormant as anything this list ever
held. The owner decided it on 2026-08-26: the planner, the ledger, and the
quiet plane were deleted, and `interruption_policy` shrank to the
notification-identity surface the app actually calls.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "src" / "sidepulse"

# module -> why it is not reachable yet. Empty is the goal state, not an
# accident: an entry here is a decision deferred, and the deferral is what
# cost this project its blend modes, its log janitors and a 1,139-line
# presentation layer.
KNOWN_UNWIRED: dict[str, str] = {}

# Legitimate separate entry points -- not imported by the app by design.
# Fixture ownership is a build/test provenance gate, not shipped runtime work.
ENTRY_POINTS = {
    "__init__",
    "__main__",
    "cli",
    "doctor",
    "hook",
    "hook_entry",
    "provider_fixture_ownership",
    "waybar_client",
}


@cache
def _imported_siblings(path: Path) -> frozenset[str]:
    """Sibling modules this file imports, in any of the shapes we use.

    String matching missed `from . import claude_quota` and reported eight
    live modules as dead, so this parses instead of guessing.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return frozenset()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module is None:
                # from . import a, b
                names.update(alias.name for alias in node.names)
            elif node.level == 1 and node.module:
                names.add(node.module.split(".")[0])
            elif node.module and node.module.startswith("sidepulse."):
                names.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("sidepulse."):
                    names.add(alias.name.split(".")[1])
    return frozenset(names)


def _module_importers(name: str) -> set[str]:
    return {
        path.stem
        for path in SRC.glob("*.py")
        if path.stem != name and name in _imported_siblings(path)
    }


def _called_names(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            calls.append(node.func.attr)
    return tuple(calls)


def test_no_new_module_becomes_unreachable() -> None:
    """A module nothing imports is a feature nobody can use."""
    unwired = {
        path.stem
        for path in SRC.glob("*.py")
        if path.stem not in ENTRY_POINTS and not _module_importers(path.stem)
    }
    surprises = sorted(unwired - set(KNOWN_UNWIRED))
    assert not surprises, (
        f"these modules are unreachable and undeclared: {surprises}. "
        "Wire them up, or add them to KNOWN_UNWIRED with the reason."
    )


def test_the_unwired_list_does_not_go_stale() -> None:
    """Wiring a module up should retire it from this list."""
    still_unwired = {
        name for name in KNOWN_UNWIRED if not _module_importers(name)
    }
    now_wired = sorted(set(KNOWN_UNWIRED) - still_unwired)
    assert not now_wired, (
        f"{now_wired} are wired up now -- remove them from KNOWN_UNWIRED"
    )


def test_every_listed_module_actually_exists() -> None:
    missing = sorted(
        name for name in KNOWN_UNWIRED if not (SRC / f"{name}.py").exists()
    )
    assert not missing, f"KNOWN_UNWIRED names modules that are gone: {missing}"


def test_announcer_stack_modules_are_wired_to_production_owners() -> None:
    assert "status_bar_legacy" in _module_importers("announcer_stack")
    assert "virtual_device" in _module_importers("announcer_stack")
    assert "virtual_device" in _module_importers("announcer_stack_view")


def test_answer_in_place_runtime_is_wired_to_controller_and_screen_bar() -> None:
    assert "status_bar_legacy" in _module_importers("answer_runtime")
    assert "status_bar_legacy" in _module_importers("answer_controller")
    assert "answer_controller" in _module_importers("answer_runtime")
    assert "answer_controller" in _module_importers("answer_in_place")
    assert "virtual_device" in _module_importers("answer_in_place")
    assert "virtual_device" in _module_importers("announcer_presenter")


def test_global_action_settings_pane_is_reachable_from_settings_window() -> None:
    assert "settings_window" in _module_importers("global_action_settings_pane")


def test_cmd_effects_dispatches_through_the_runtime_owner(monkeypatch) -> None:
    from sidepulse import cli, effect_cli

    calls: list[tuple[object, object, object]] = []

    def dispatch(args, *, store=None, stdout=None, stderr=None):
        calls.append((args.action, args.source, args.json))
        return 37

    monkeypatch.setattr(effect_cli, "dispatch_effect_command", dispatch)

    result = cli.cmd_effects(
        SimpleNamespace(
            action="install",
            source=Path("/tmp/calm-pack.json"),
            json=True,
        )
    )

    assert result == 37
    assert calls == [("install", Path("/tmp/calm-pack.json"), True)]


def test_build_phone_glance_projection_uses_the_serve_document_owner(
    monkeypatch,
) -> None:
    from sidepulse import serve
    from sidepulse.phone_glance import PhoneGlancePolicy

    home = Path("/tmp/sidepulse-home")
    calls: dict[str, object] = {}

    def build_serve_document(value):
        calls["home"] = value
        return {
            "schema_version": 2,
            "privacy": "redacted",
            "agents": {
                "generation": 9,
                "work_count": 2,
                "lifecycle_counts": {"active": 1, "waiting": 1},
                "next_actor_counts": {"provider": 1, "user": 1},
                "source_health_counts": {"healthy": 1, "partial": 1},
                "source_freshness_counts": {"fresh": 1, "stale": 1},
                "timing_uncertain_count": 0,
            },
            "usage": {
                "refreshed_at": 1_000.0,
                "next_refresh_at": 1_060.0,
                "providers": [
                    {
                        "provider_id": "claude",
                        "observed_at": 999.0,
                        "state": "ready",
                        "quota": {
                            "window_count": 2,
                            "remaining_percent": 25.5,
                            "next_reset_at": 1_500.0,
                        },
                    }
                ],
            },
        }

    def build_phone_glance(glance, policy, signer):
        calls["glance"] = glance
        calls["policy"] = policy
        calls["signature"] = signer(b"payload")
        return {"status": glance.status, "outcome": glance.outcome}

    monkeypatch.setattr(serve, "build_serve_document", build_serve_document)
    monkeypatch.setattr(serve, "build_phone_glance", build_phone_glance)

    policy = PhoneGlancePolicy("sidepulse", include_message=True, include_capacity=True)
    result = serve.build_phone_glance_projection(
        policy,
        signer=lambda payload: "signed:" + payload.decode("utf-8"),
        sequence=7,
        home=home,
        observed_at=1_001.0,
    )

    assert calls["home"] == home
    assert calls["policy"] is policy
    assert calls["signature"] == "signed:payload"
    glance = calls["glance"]
    assert glance.status == "working"
    assert glance.outcome == "attention"
    assert glance.capacity == {"remaining_percent": 25.5, "reset_at": 1_500.0}
    assert result == {"status": "working", "outcome": "attention"}


def test_status_bar_ambient_bar_uses_the_screen_consumer_runtime(monkeypatch) -> None:
    from sidepulse import _status_bar_production as production

    calls: dict[str, object] = {}

    def ambient(controller, *, reduce_motion, brightness):
        calls["controller"] = controller
        calls["reduce_motion"] = reduce_motion
        calls["brightness"] = brightness
        return SimpleNamespace(
            screen_bar_program=SimpleNamespace(dsl="ambient dsl"),
            accessibility_text="ambient text",
        )

    monkeypatch.setattr(production, "active_screen_bar_ambient_presentation", ambient)
    monkeypatch.setitem(
        production.JRStatusBarController._ambient_bar.__globals__,
        "active_screen_bar_ambient_presentation",
        ambient,
    )
    monkeypatch.setattr(
        production._legacy,
        "apply_brightness",
        lambda dsl, brightness: f"{dsl}@{brightness}",
    )

    controller = SimpleNamespace(
        _accessibility_display_preferences=SimpleNamespace(reduce_motion=True),
    )
    setter_calls: list[tuple[str, object]] = []

    def setter(program, presentation):
        setter_calls.append((program, presentation))

    result = production.JRStatusBarController._ambient_bar(controller, setter, 0.75)

    assert result is True
    assert calls == {
        "controller": controller,
        "reduce_motion": True,
        "brightness": 0.75,
    }
    assert setter_calls[0][0] == "ambient dsl@0.75"
    assert setter_calls[0][1].dsl == "ambient dsl"
    assert controller._ambient_accessibility_text == "ambient text"


def test_status_bar_hardware_sync_uses_the_hardware_consumer_runtime(monkeypatch) -> None:
    from sidepulse import _status_bar_production as production
    from sidepulse import status_bar_legacy as legacy

    calls: dict[str, object] = {}

    def ambient(controller, *, device_id, led_count, reduce_motion, brightness):
        calls["controller"] = controller
        calls["device_id"] = device_id
        calls["led_count"] = led_count
        calls["reduce_motion"] = reduce_motion
        calls["brightness"] = brightness
        return SimpleNamespace(
            program="ambient hardware program",
            led_state=legacy.LedDisplayState.WORKING,
        )

    monkeypatch.setattr(
        production,
        "active_hardware_ambient_presentation",
        ambient,
    )
    monkeypatch.setitem(
        production.JRStatusBarController._sync_hardware_device.__globals__,
        "active_hardware_ambient_presentation",
        ambient,
    )

    records: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    class Performance:
        def record(self, *args, **kwargs):
            records.append((self, args, kwargs))

    device = legacy.StatusBarDevice(
        device_id="device-1",
        name="Device One",
        root=Path("/tmp"),
        target=Path("/tmp/LEDS.LED"),
        connected=True,
        display=legacy.LED_DISPLAY_AGENT,
    )
    request = legacy.HardwareWriteRequest(
        device=device,
        mode=legacy.AgentMode.IDLE_READY,
        battery_snapshot=None,
        statuses=(),
        projection=None,
        relay_elapsed_seconds=0.0,
        accessibility_preferences=None,
        display_kind=legacy.LED_DISPLAY_AGENT,
        write_priority=legacy.RuntimeWorkPriority.COALESCIBLE,
        coalesce_identity="ambient-test",
    )

    class DeviceController:
        brightness = 0.42

        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def sync_program(self, program, led_state):
            self.calls.append((program, led_state))
            return legacy.LedStatusWrite(
                state=legacy.LedDisplayState.WORKING,
                target=device.target,
                program=program,
                changed=True,
                error=None,
            )

    device_controller = DeviceController()
    controller = SimpleNamespace(
        last_led_display_kind_by_device={},
        settings=SimpleNamespace(),
        agent_controller_for_device=lambda _device: device_controller,
        effective_signal_brightness_for_device=lambda _device: 1.0,
        _runtime_worker_monotonic=lambda: 123.0,
        _performance=lambda: Performance(),
        reset_led_controllers_for_device=lambda _device_id: None,
    )

    result = production.JRStatusBarController._sync_hardware_device(controller, request)

    assert calls == {
        "controller": controller,
        "device_id": "device-1",
        "led_count": legacy.led_count_for_target(device.target),
        "reduce_motion": False,
        "brightness": 0.42,
    }
    assert device_controller.calls == [
        ("ambient hardware program", legacy.LedDisplayState.WORKING)
    ]
    assert result.label == "Device One Ambient effect"
    assert result.agent_display_rendered is False
    assert result.completed_at == 123.0
    assert records and records[0][1][0] == "hardware_render"
