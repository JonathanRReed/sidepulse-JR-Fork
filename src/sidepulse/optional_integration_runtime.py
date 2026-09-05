"""Background-only wiring for optional read and discovery integrations."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_deck_compat import AgentDeckSnapshotService, SnapshotUpdate, read_snapshot
from .creator_micro_adapter import CreatorMicro2Adapter, SemanticState
from .creator_micro_hidapi import HidApiTransport
from .creator_micro_lighting import CreatorMicroBrightnessProfile, CreatorMicroLightFrame, creator_micro_light_frame
from .deck_control_settings import DeckControlSettings, load_deck_controls
from .deck_input_dispatch import DeckInputDispatch
from .models import AgentMode


@dataclass(frozen=True, slots=True)
class CreatorMicroDiscoveryReceipt:
    available: bool
    matching_collections: int = 0
    reason: str | None = None


class CreatorMicroDiscoveryService:
    """Run one read-only HID enumeration from an existing background worker."""

    def __init__(
        self,
        *,
        transport_factory: Callable[[], Any] = HidApiTransport,
        callback: Callable[[CreatorMicroDiscoveryReceipt], None] | None = None,
    ) -> None:
        self._transport_factory = transport_factory
        self._callback = callback
        self.receipt: CreatorMicroDiscoveryReceipt | None = None

    def start(self) -> bool:
        try:
            count = len(self._transport_factory().enumerate())
            result = CreatorMicroDiscoveryReceipt(count > 0, count, None if count else "no_device")
        except Exception:
            result = CreatorMicroDiscoveryReceipt(False, reason="transport_unavailable")
        self.receipt = result
        if self._callback is not None:
            self._callback(result)
        return True

    def close(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class CreatorMicroOutputReceipt:
    available: bool
    reason: str
    detail: str = ""


def creator_semantic_state(
    mode: AgentMode,
    *,
    signal: str | None = None,
) -> SemanticState:
    signals = {
        "quota_exhausted": SemanticState.QUOTA_EXHAUSTED,
        "quota_warning": SemanticState.QUOTA_WARNING,
        "reset": SemanticState.RESET,
    }
    activity = {
        AgentMode.WAITING_FOR_INPUT: SemanticState.INPUT_REQUIRED,
        AgentMode.BLOCKED_ERROR: SemanticState.FAILURE,
        AgentMode.WORKING: SemanticState.ACTIVE,
        AgentMode.TOOL_RUNNING: SemanticState.ACTIVE,
        AgentMode.LONG_TASK_PROGRESS: SemanticState.ACTIVE,
        AgentMode.COMPLETED: SemanticState.COMPLETED,
    }.get(mode, SemanticState.IDLE)
    return max((activity, signals.get(signal, SemanticState.IDLE)), key=lambda state: state.priority)


def _creator_output_adapter(approved_serial: str) -> CreatorMicro2Adapter:
    transport = HidApiTransport(approved_serial=approved_serial)
    devices = transport.enumerate()
    if not devices:
        raise OSError("Creator Micro 2 not found")
    transport.enable_writes()
    return CreatorMicro2Adapter(transport, devices[0])


class CreatorMicroOutputService:
    """One HID owner for latest-wins output and optional bounded input polling."""

    def __init__(
        self,
        *,
        adapter_factory: Callable[[], Any] = _creator_output_adapter,
        approved_serial: str | None = None,
        callback: Callable[[CreatorMicroOutputReceipt], None] | None = None,
        input_callback: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        if adapter_factory is _creator_output_adapter:
            if not approved_serial:
                raise ValueError("Creator Micro output requires an approved serial")
            self._adapter_factory = lambda: _creator_output_adapter(approved_serial)
        else:
            self._adapter_factory = adapter_factory
        self._callback = callback
        self._input_callback = input_callback
        self._condition = threading.Condition()
        self._pending: tuple[AgentMode, str | None, CreatorMicroLightFrame | None] | None = None
        self._closed = False
        self._busy = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        with self._condition:
            if self._closed or self._thread is not None:
                return False
            self._thread = threading.Thread(
                target=self._run,
                name="SidePulseCreatorMicroOutput",
                daemon=True,
            )
            self._thread.start()
            return True

    def submit(
        self, mode: AgentMode, *, signal: str | None = None,
        frame: CreatorMicroLightFrame | None = None,
    ) -> bool:
        if type(mode) is not AgentMode:
            raise TypeError("mode must be AgentMode")
        with self._condition:
            if self._closed:
                return False
            self._pending = (mode, signal, frame)
            self._condition.notify_all()
            return True

    def _publish(self, available: bool, reason: str, detail: str = "") -> None:
        if self._callback is not None:
            self._callback(CreatorMicroOutputReceipt(available, reason, detail[:256]))

    def _run(self) -> None:
        adapter = None
        try:
            adapter = self._adapter_factory()
            connected = adapter.connect()
            if connected.code != "connected":
                self._publish(False, connected.code, connected.detail)
                return
            negotiated = adapter.negotiate_capabilities()
            if negotiated.code != "capabilities_negotiated":
                self._publish(False, negotiated.code, negotiated.detail)
                return
            if "v.oai.thstatus" not in adapter.capabilities().methods:
                self._publish(False, "unsupported_firmware")
                return
            self._publish(True, "ready")
            while True:
                with self._condition:
                    if self._pending is None and not self._closed:
                        self._condition.wait(timeout=0.05 if self._input_callback else None)
                    if self._closed:
                        return
                    pending = self._pending
                    self._pending = None
                    self._busy = pending is not None
                if pending is not None:
                    mode, signal, frame = pending
                    state = creator_semantic_state(mode, signal=signal)
                    result = adapter.apply(state, frame.params()) if frame is not None else adapter.apply(state)
                    self._publish(result.code == "applied", result.code, result.detail)
                    if result.code != "applied":
                        return
                if self._input_callback is not None:
                    inputs = adapter.poll_inputs()
                    if adapter.conflict.active:
                        self._publish(False, "device_conflict")
                        return
                    if not adapter.connected:
                        self._publish(False, "transport_unavailable")
                        return
                    if inputs:
                        self._input_callback(inputs)
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()
                if adapter.conflict.active:
                    return
        except Exception:
            self._publish(False, "transport_unavailable")
        finally:
            if adapter is not None:
                adapter.close()
            with self._condition:
                self._busy = False
                self._pending = None
                self._closed = True
                self._condition.notify_all()

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._pending is not None or self._busy:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def wait_until_stopped(self, timeout: float) -> bool:
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        return thread is None or not thread.is_alive()


class OptionalIntegrationRuntime:
    """Load optional settings and configure services away from the AppKit thread."""

    def __init__(
        self,
        target: object,
        *,
        settings_loader: Callable[[], object],
        deck_settings_loader: Callable[[], DeckControlSettings] = load_deck_controls,
        agent_service_factory: Callable[..., object] = AgentDeckSnapshotService,
        creator_service_factory: Callable[..., object] = CreatorMicroOutputService,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._target = target
        self._settings_loader = settings_loader
        self._deck_settings_loader = deck_settings_loader
        self._agent_service_factory = agent_service_factory
        self._creator_service_factory = creator_service_factory
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._closed = False
        self._started = False
        self._configured = threading.Event()
        self._agent_service: object | None = None
        self._creator_service: object | None = None
        self._deck_dispatch: DeckInputDispatch | None = None

    def start(self) -> bool:
        with self._lock:
            if self._closed or self._started:
                return False
            self._started = True
        threading.Thread(
            target=self._configure,
            name="SidePulseOptionalIntegrations",
            daemon=True,
        ).start()
        return True

    def wait_until_configured(self, timeout: float | None = None) -> bool:
        return self._configured.wait(timeout)

    def _configure(self) -> None:
        try:
            loaded = self._settings_loader()
            settings = getattr(loaded, "settings", loaded)
            with self._lock:
                if self._closed:
                    return
                setattr(
                    self._target,
                    "_creator_micro_output_enabled",
                    getattr(settings, "creator_micro_enabled", False) is True,
                )
            try:
                controls = self._deck_settings_loader()
                controls_error = None
            except (OSError, ValueError, TypeError):
                controls = DeckControlSettings()
                controls_error = "Deck settings could not be read safely."
            with self._lock:
                if self._closed:
                    return
                if controls_error is None:
                    setattr(self._target, "_deck_control_settings", controls)
                    setattr(self._target, "_deck_control_settings_error", None)
                else:
                    setattr(self._target, "_deck_control_settings", None)
                    setattr(self._target, "_deck_control_settings_error", controls_error)
                self._deck_dispatch = DeckInputDispatch(self._target, controls)
            if getattr(settings, "agent_deck_enabled", False) is True:
                configured = getattr(settings, "agent_deck_snapshot_path", None)
                if isinstance(configured, str) and configured.strip():
                    path = Path(configured)
                    service = self._agent_service_factory(
                        enabled=True,
                        reader=lambda: read_snapshot(
                            path,
                            enabled=True,
                            now=self._wall_clock(),
                        ),
                        clock=self._monotonic,
                        callback=self._publish_agent_deck,
                    )
                    with self._lock:
                        if self._closed:
                            self._close_service(service)
                            return
                        self._agent_service = service
                        service.start()
            if getattr(settings, "creator_micro_enabled", False) is True:
                if getattr(settings, "agent_deck_enabled", False) is True:
                    self._publish_creator_receipt(
                        CreatorMicroOutputReceipt(False, "agent_deck_ownership")
                    )
                    return
                approved_serial = getattr(
                    settings,
                    "creator_micro_device_serial",
                    None,
                )
                if not isinstance(approved_serial, str) or not approved_serial.strip():
                    self._publish_creator_receipt(
                        CreatorMicroOutputReceipt(False, "device_identity_required")
                    )
                    return
                service = self._creator_service_factory(
                    approved_serial=approved_serial,
                    callback=self._publish_creator_receipt,
                    input_callback=self._deck_dispatch.receive if controls.enabled else None,
                )
                with self._lock:
                    if self._closed:
                        self._close_service(service)
                        return
                    self._creator_service = service
                    service.start()
        finally:
            self._configured.set()
            with self._lock:
                if not self._closed and getattr(self._target, "deck_settings_pane", None) is not None:
                    dispatch = getattr(self._target, "performSelectorOnMainThread_withObject_waitUntilDone_", None)
                    if callable(dispatch):
                        dispatch("applyDeckControlsLoaded:", getattr(self._target, "_deck_control_settings", None), False)

    def _publish_agent_deck(self, update: SnapshotUpdate) -> None:
        with self._lock:
            if self._closed:
                return
            replace_statuses = getattr(getattr(self._target, "monitor", None), "replace_external_statuses", None)
            if callable(replace_statuses):
                replace_statuses("agent-deck", update.statuses)

    def _publish_creator_receipt(self, receipt: CreatorMicroOutputReceipt) -> None:
        with self._lock:
            if self._closed:
                return
            setattr(self._target, "_creator_micro_output_receipt", receipt)
            dispatch = getattr(
                self._target,
                "performSelectorOnMainThread_withObject_waitUntilDone_",
                None,
            )
            if callable(dispatch):
                dispatch("applyCreatorMicroOutputReceipt:", receipt, False)

    def publish_creator_output(
        self,
        mode: AgentMode,
        *,
        signal: str | None = None,
    ) -> bool:
        with self._lock:
            if self._closed:
                return False
            service = self._creator_service
        submit = getattr(service, "submit", None)
        if not callable(submit):
            return False
        from .colors import ColorSettings

        colors = getattr(getattr(self._target, "settings", None), "colors", None)
        brightness_policy = getattr(self._target, "effective_brightness_for_device", None)
        brightness = brightness_policy(CreatorMicroBrightnessProfile()) / 255 if callable(brightness_policy) else 0.4
        frame = creator_micro_light_frame(
            creator_semantic_state(mode, signal=signal).value,
            colors=colors if type(colors) is ColorSettings else None,
            brightness=brightness,
            idle_off=not callable(brightness_policy),
        )
        return bool(submit(mode, signal=signal, frame=frame))

    @staticmethod
    def _close_service(service: object | None) -> None:
        close = getattr(service, "close", None)
        if callable(close):
            close()

    def revoke_deck_input(self) -> None:
        with self._lock:
            dispatch = self._deck_dispatch
        if dispatch is not None:
            dispatch.close()

    def wait_until_stopped(self, timeout: float) -> bool:
        with self._lock:
            service = self._creator_service
        wait = getattr(service, "wait_until_stopped", None)
        return service is None or bool(callable(wait) and wait(timeout))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            agent_service = self._agent_service
            creator_service = self._creator_service
            deck_dispatch = self._deck_dispatch
        if deck_dispatch is not None:
            deck_dispatch.close()
        self._close_service(agent_service)
        self._close_service(creator_service)
        replace_statuses = getattr(getattr(self._target, "monitor", None), "replace_external_statuses", None)
        if callable(replace_statuses) and agent_service is not None:
            replace_statuses("agent-deck", ())


def start_optional_integration_runtime(target: object) -> OptionalIntegrationRuntime:
    """Start the production runtime without doing settings or device I/O inline."""
    from .integration_settings import load_integration_settings

    runtime = OptionalIntegrationRuntime(target, settings_loader=load_integration_settings)
    runtime.start()
    return runtime


def set_creator_micro_output_enabled_async(target: object, enabled: bool) -> None:
    """Persist an explicit output choice and reconcile without blocking AppKit."""
    from .creator_micro_settings import save_creator_micro_choice_async

    save_creator_micro_choice_async(target, enabled)


__all__ = [
    "CreatorMicroDiscoveryReceipt",
    "CreatorMicroDiscoveryService",
    "CreatorMicroOutputReceipt",
    "CreatorMicroOutputService",
    "OptionalIntegrationRuntime",
    "creator_semantic_state",
    "set_creator_micro_output_enabled_async",
    "start_optional_integration_runtime",
]
