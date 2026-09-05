from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from test_agent_deck_compat import NOW, payload

from sidepulse.agent_deck_compat import (
    CompatibilityReceipt,
    ReceiptReason,
    SnapshotUpdate,
    parse_snapshot,
    prioritized_statuses,
)
from sidepulse.models import AgentMode
from sidepulse.optional_integration_runtime import (
    CreatorMicroOutputService,
    OptionalIntegrationRuntime,
    creator_semantic_state,
)


class Monitor:
    def __init__(self):
        self.calls = []
        self.ready = threading.Event()

    def replace_external_statuses(self, source, statuses):
        self.calls.append((source, statuses))
        self.ready.set()


def test_default_off_runtime_does_not_construct_or_read_optional_sources():
    def forbidden(*_args, **_kwargs):
        raise AssertionError("disabled optional source was touched")

    target = SimpleNamespace(monitor=Monitor())
    settings = SimpleNamespace(agent_deck_enabled=False, creator_micro_enabled=False)
    runtime = OptionalIntegrationRuntime(
        target,
        settings_loader=lambda: settings,
        agent_service_factory=forbidden,
        creator_service_factory=forbidden,
    )

    runtime.start()
    assert runtime.wait_until_configured(1)
    runtime.close()
    assert target.monitor.calls == []


def test_close_does_not_wait_for_settings_io_or_publish_after_loader_returns():
    loading = threading.Event()
    release_loader = threading.Event()
    ui_calls = []
    deck_loads = []
    target = SimpleNamespace(
        _creator_micro_output_enabled="current",
        _deck_control_settings="current",
        deck_settings_pane=object(),
        performSelectorOnMainThread_withObject_waitUntilDone_=lambda *args: ui_calls.append(args),
    )

    def load_settings():
        loading.set()
        assert release_loader.wait(1)
        return SimpleNamespace(agent_deck_enabled=False, creator_micro_enabled=True)

    runtime = OptionalIntegrationRuntime(
        target,
        settings_loader=load_settings,
        deck_settings_loader=lambda: deck_loads.append(True),
    )
    runtime.start()
    assert loading.wait(1)

    runtime.close()
    assert not release_loader.is_set()
    release_loader.set()
    assert runtime.wait_until_configured(1)

    assert target._creator_micro_output_enabled == "current"
    assert target._deck_control_settings == "current"
    assert deck_loads == []
    assert ui_calls == []


def test_close_does_not_wait_for_blocked_deck_settings_io():
    loading = threading.Event()
    release_loader = threading.Event()
    target = SimpleNamespace()

    def load_deck_settings():
        loading.set()
        assert release_loader.wait(1)
        return SimpleNamespace(enabled=False)

    runtime = OptionalIntegrationRuntime(
        target,
        settings_loader=lambda: SimpleNamespace(
            agent_deck_enabled=False,
            creator_micro_enabled=False,
        ),
        deck_settings_loader=load_deck_settings,
    )
    runtime.start()
    assert loading.wait(1)

    runtime.close()
    assert not release_loader.is_set()
    release_loader.set()
    assert runtime.wait_until_configured(1)


def test_enabled_agent_deck_projects_statuses_into_canonical_monitor():
    monitor = Monitor()
    target = SimpleNamespace(monitor=monitor)
    observations = parse_snapshot(payload(), now=NOW)
    expected = prioritized_statuses(observations)

    class Service:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def start(self):
            self.callback(
                SnapshotUpdate(
                    1,
                    1.0,
                    CompatibilityReceipt(True, True, True, ReceiptReason.OK, observations=observations),
                    expected,
                )
            )
            return True

        def close(self):
            pass

    settings = SimpleNamespace(
        agent_deck_enabled=True,
        agent_deck_snapshot_path="/tmp/deck.json",
        creator_micro_enabled=False,
    )
    runtime = OptionalIntegrationRuntime(
        target,
        settings_loader=lambda: settings,
        agent_service_factory=Service,
        creator_service_factory=lambda: (_ for _ in ()).throw(AssertionError("creator source touched")),
    )

    runtime.start()
    assert monitor.ready.wait(1)
    runtime.close()
    assert monitor.calls == [("agent-deck", expected), ("agent-deck", ())]


def test_enabled_creator_micro_discovery_runs_off_the_caller():
    calls = []
    configured = threading.Event()

    class CreatorService:
        def __init__(self, **kwargs):
            assert kwargs["approved_serial"] == "CM2-123"

        def start(self):
            calls.append(threading.current_thread().name)
            configured.set()
            return True

        def close(self):
            pass

    settings = SimpleNamespace(
        agent_deck_enabled=False,
        creator_micro_enabled=True,
        creator_micro_device_serial="CM2-123",
    )
    runtime = OptionalIntegrationRuntime(
        SimpleNamespace(monitor=Monitor()),
        settings_loader=lambda: settings,
        agent_service_factory=lambda **_kwargs: None,
        creator_service_factory=CreatorService,
    )
    caller = threading.current_thread().name

    runtime.start()
    assert configured.wait(1)
    runtime.close()
    assert calls and calls[0] != caller


def test_agent_deck_enablement_blocks_creator_micro_output_ownership():
    target = SimpleNamespace(monitor=Monitor())
    settings = SimpleNamespace(
        agent_deck_enabled=True,
        agent_deck_snapshot_path=None,
        creator_micro_enabled=True,
        creator_micro_device_serial="CM2-123",
    )
    runtime = OptionalIntegrationRuntime(
        target,
        settings_loader=lambda: settings,
        agent_service_factory=lambda **_kwargs: None,
        creator_service_factory=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("creator output started while Agent Deck was enabled")
        ),
    )

    runtime.start()
    assert runtime.wait_until_configured(1)
    runtime.close()
    assert target._creator_micro_output_receipt.reason == "agent_deck_ownership"


def test_enabled_creator_micro_without_approved_identity_fails_closed():
    target = SimpleNamespace(monitor=Monitor())
    settings = SimpleNamespace(
        agent_deck_enabled=False,
        creator_micro_enabled=True,
        creator_micro_device_serial=None,
    )
    runtime = OptionalIntegrationRuntime(
        target,
        settings_loader=lambda: settings,
        creator_service_factory=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("identity-less Creator Micro output started")
        ),
    )

    runtime.start()
    assert runtime.wait_until_configured(1)
    runtime.close()
    assert target._creator_micro_output_receipt.reason == "device_identity_required"


@pytest.mark.parametrize(
    "mode,signal,want",
    [
        (AgentMode.WAITING_FOR_INPUT, None, "input_required"),
        (AgentMode.BLOCKED_ERROR, None, "failure"),
        (AgentMode.WORKING, None, "active"),
        (AgentMode.COMPLETED, None, "completed"),
        (AgentMode.IDLE_READY, None, "idle"),
        (AgentMode.IDLE_READY, "quota_exhausted", "quota_exhausted"),
        (AgentMode.IDLE_READY, "quota_warning", "quota_warning"),
        (AgentMode.IDLE_READY, "reset", "reset"),
        (AgentMode.WAITING_FOR_INPUT, "quota_exhausted", "input_required"),
        (AgentMode.BLOCKED_ERROR, "reset", "failure"),
        (AgentMode.WORKING, "quota_warning", "active"),
        (AgentMode.WORKING, "reset", "reset"),
    ],
)
def test_creator_semantic_mapping_is_explicit(mode, signal, want):
    assert creator_semantic_state(mode, signal=signal).value == want


def test_output_service_negotiates_before_writes_and_stops_on_conflict():
    calls, receipts = [], []

    class Adapter:
        conflict = SimpleNamespace(active=False)

        def connect(self):
            calls.append("connect")
            return SimpleNamespace(code="connected", detail="")

        def negotiate_capabilities(self):
            calls.append("negotiate")
            return SimpleNamespace(code="capabilities_negotiated", detail="v.oai.thstatus")

        def capabilities(self):
            return SimpleNamespace(methods=frozenset({"v.oai.thstatus"}))

        def apply(self, state):
            calls.append(state.value)
            self.conflict.active = True
            return SimpleNamespace(code="device_conflict", detail="foreign response")

        def close(self):
            calls.append("close")

    service = CreatorMicroOutputService(adapter_factory=Adapter, callback=receipts.append)
    service.start()
    service.submit(AgentMode.WORKING)
    assert service.wait_until_idle(1)
    service.submit(AgentMode.IDLE_READY)
    service.close()

    assert calls == ["connect", "negotiate", "active", "close"]
    assert receipts[-1].reason == "device_conflict"


def test_output_service_reports_unsupported_firmware_without_applying():
    class Adapter:
        conflict = SimpleNamespace(active=False)

        def connect(self):
            return SimpleNamespace(code="connected", detail="")

        def negotiate_capabilities(self):
            return SimpleNamespace(code="capabilities_negotiated", detail="")

        def capabilities(self):
            return SimpleNamespace(methods=frozenset())

        def apply(self, _state):
            raise AssertionError("unsupported firmware received output")

        def close(self):
            pass

    receipts = []
    service = CreatorMicroOutputService(adapter_factory=Adapter, callback=receipts.append)
    service.start()
    service.submit(AgentMode.WORKING)
    assert service.wait_until_idle(1)
    service.close()
    assert receipts[-1].reason == "unsupported_firmware"


def test_input_is_delivered_while_output_is_idle_on_the_same_transport_owner():
    from collections import deque

    from sidepulse.creator_micro_adapter import CreatorMicro2Adapter, CreatorMicro2Framer, RpcStreamDecoder

    received, ready = threading.Event(), threading.Event()
    inputs, owners = [], set()

    class Transport:
        def __init__(self):
            self.reads = deque()
            self.decoder = RpcStreamDecoder()

        def open(self, **_kwargs):
            owners.add(threading.get_ident())

        def write(self, report):
            owners.add(threading.get_ident())
            for message in self.decoder.feed(report):
                self.reads.extend(CreatorMicro2Framer.encode_message(
                    {"jsonrpc": "2.0", "id": message["id"], "result": {"ok": 1}}
                ))

        def read(self, **_kwargs):
            owners.add(threading.get_ident())
            return self.reads.popleft() if self.reads else None

        def close(self):
            owners.add(threading.get_ident())

    transport = Transport()
    adapter = CreatorMicro2Adapter(transport, {
        "vendor_id": 0x303A, "product_id": 0x8297, "usage_page": 0xFF00, "usage": 1,
    })

    def on_input(batch):
        inputs.extend(batch)
        received.set()

    service = CreatorMicroOutputService(
        adapter_factory=lambda: adapter,
        callback=lambda receipt: ready.set() if receipt.reason == "ready" else None,
        input_callback=on_input,
    )
    try:
        service.start()
        assert ready.wait(1)
        transport.reads.extend(CreatorMicro2Framer.encode_message({
            "jsonrpc": "2.0", "m": "v.oai.hid", "p": {"k": "AG03", "act": 1},
        }))
        assert received.wait(1)
        service.submit(AgentMode.WORKING)
        assert service.wait_until_idle(1)
    finally:
        service.close()
    assert inputs == [{"method": "v.oai.hid", "params": {"k": "AG03", "act": 1}}]
    assert len(owners) == 1
    assert threading.get_ident() not in owners


def test_runtime_wires_saved_macros_and_revokes_delivery_when_disabled():
    from sidepulse.deck_actions import DeckAction
    from sidepulse.deck_actions_macos import MacDeckActionExecutor
    from sidepulse.deck_control_settings import DeckControlSettings

    batches, opened = [], []
    target = SimpleNamespace(
        performSelectorOnMainThread_withObject_waitUntilDone_=lambda selector, payload, wait: batches.append(payload),
    )

    class Service:
        def __init__(self, **kwargs):
            self.receive = kwargs["input_callback"]

        def start(self):
            self.receive([{"method": "v.oai.hid", "params": {"k": "AG03", "act": 1}}])

        def close(self):
            pass

    runtime = OptionalIntegrationRuntime(
        target,
        settings_loader=lambda: SimpleNamespace(
            agent_deck_enabled=False, creator_micro_enabled=True, creator_micro_device_serial="CM2-123",
        ),
        deck_settings_loader=lambda: DeckControlSettings(
            enabled=True, bindings=((3, DeckAction("open_usage")),),
        ),
        creator_service_factory=Service,
    )
    runtime.start()
    assert runtime.wait_until_configured(1)
    assert len(batches) == 1
    executor = MacDeckActionExecutor(open_usage=lambda: opened.append("usage"))
    assert batches[0].owner.deliver(batches[0], executor)[0].success
    assert opened == ["usage"]
    runtime.close()
    assert batches[0].owner.deliver(batches[0], executor) == ()


def test_master_device_switch_uses_serialized_reconfiguration_instead_of_starting_a_second_owner(monkeypatch):
    from sidepulse import integration_settings
    from sidepulse.optional_integration_runtime import set_creator_micro_output_enabled_async

    settings = integration_settings.IntegrationSettings(creator_micro_enabled=True, creator_micro_device_serial="CM2")
    saved, calls, ready = [], [], threading.Event()
    monkeypatch.setattr(integration_settings, "load_integration_settings", lambda: SimpleNamespace(settings=settings))
    monkeypatch.setattr(integration_settings, "save_integration_settings", lambda value, **kwargs: saved.append(value))

    def dispatch(selector, payload, wait):
        calls.append(selector)
        assert payload.enabled is False
        ready.set()

    target = SimpleNamespace(performSelectorOnMainThread_withObject_waitUntilDone_=dispatch)
    set_creator_micro_output_enabled_async(target, False)
    assert ready.wait(1)
    assert not saved[0].creator_micro_enabled
    assert calls == ["applyCreatorMicroSettings:"]


def test_master_device_settings_last_intent_wins_during_a_slow_save(monkeypatch):
    from sidepulse import integration_settings
    from sidepulse.optional_integration_runtime import set_creator_micro_output_enabled_async

    current = [integration_settings.IntegrationSettings(creator_micro_device_serial="CM2")]
    first_saving, finish_first, done = threading.Event(), threading.Event(), threading.Event()
    receipts, saves = [], []
    monkeypatch.setattr(integration_settings, "load_integration_settings", lambda: SimpleNamespace(settings=current[0]))

    def save(value, **kwargs):
        saves.append(value.creator_micro_enabled)
        if len(saves) == 1:
            first_saving.set()
            assert finish_first.wait(1)
        current[0] = value

    def dispatch(selector, payload, wait):
        receipts.append(payload)
        done.set()

    monkeypatch.setattr(integration_settings, "save_integration_settings", save)
    target = SimpleNamespace(performSelectorOnMainThread_withObject_waitUntilDone_=dispatch)
    set_creator_micro_output_enabled_async(target, True)
    assert first_saving.wait(1)
    set_creator_micro_output_enabled_async(target, False)
    finish_first.set()
    assert done.wait(1)
    assert current[0].creator_micro_enabled is False
    assert saves == [True, False]
    assert len(receipts) == 1 and receipts[0].enabled is False


def test_creator_output_uses_the_same_user_colors_and_brightness_policy_as_other_devices():
    from sidepulse.colors import ColorSettings
    from sidepulse.deck_control_settings import DeckControlSettings

    frames, brightness_targets = [], []

    class Service:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def submit(self, mode, *, signal, frame=None):
            frames.append(frame)
            return True

        def close(self):
            pass

    def brightness(device):
        brightness_targets.append(device.device_id)
        return 51

    target = SimpleNamespace(
        settings=SimpleNamespace(colors=ColorSettings().with_mode_color("working", "#123456")),
        effective_brightness_for_device=brightness,
    )
    runtime = OptionalIntegrationRuntime(
        target,
        settings_loader=lambda: SimpleNamespace(
            agent_deck_enabled=False, creator_micro_enabled=True, creator_micro_device_serial="CM2",
        ),
        deck_settings_loader=DeckControlSettings,
        creator_service_factory=Service,
    )
    runtime.start()
    assert runtime.wait_until_configured(1)
    assert runtime.publish_creator_output(AgentMode.WORKING)
    runtime.close()
    assert brightness_targets == ["creator-micro"]
    assert frames[0].color == 0x123456
    assert frames[0].brightness == 0.2
