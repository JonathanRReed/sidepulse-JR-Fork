from __future__ import annotations

import ctypes
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from sidepulse.global_actions import GlobalActionID, ShortcutChord, ShortcutModifier
from sidepulse.global_hotkeys import (
    BackendHotkeyRegistration,
    CarbonBackendError,
    CarbonHotkeyBackend,
    GlobalHotkeyRegistry,
    HotkeyCleanupError,
    HotkeyPrepareResult,
    HotkeyRegistrationRefusal,
)

ACTION = GlobalActionID.REVEAL_CURRENT_ASK
COMMAND_K = ShortcutChord(
    key_code=40,
    key_label="K",
    modifiers=frozenset({ShortcutModifier.COMMAND}),
)
CONTROL_SHIFT_K = ShortcutChord(
    key_code=40,
    key_label="K",
    modifiers=frozenset({ShortcutModifier.CONTROL, ShortcutModifier.SHIFT}),
)


class FakeBackend:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []
        self.handler: Callable[[int], None] | None = None
        self.refused_key_codes: dict[int, int] = {}
        self.unregister_failures: dict[object, int] = {}
        self.remove_handler_failures = 0

    def install_handler(self, callback: Callable[[int], None]) -> object:
        assert self.handler is None
        self.handler = callback
        self.events.append(("install_handler",))
        return "handler-ref"

    def register_hotkey(
        self,
        chord: ShortcutChord,
        hotkey_id: int,
    ) -> BackendHotkeyRegistration | HotkeyRegistrationRefusal:
        self.events.append(("register", chord, hotkey_id))
        if status := self.refused_key_codes.get(chord.key_code):
            return HotkeyRegistrationRefusal.from_os_status(status)
        return BackendHotkeyRegistration(reference=f"hotkey-ref-{hotkey_id}")

    def unregister_hotkey(self, registration: BackendHotkeyRegistration) -> None:
        self.events.append(("unregister", registration.reference))
        failures = self.unregister_failures.get(registration.reference, 0)
        if failures:
            self.unregister_failures[registration.reference] = failures - 1
            raise RuntimeError("injected unregister failure with unbounded detail")

    def remove_handler(self, handler: object) -> None:
        self.events.append(("remove_handler", handler))
        if self.remove_handler_failures:
            self.remove_handler_failures -= 1
            raise RuntimeError("injected handler removal failure with unbounded detail")

    def emit(self, hotkey_id: int) -> None:
        assert self.handler is not None
        self.handler(hotkey_id)


def registry_with(
    backend: FakeBackend,
    *,
    queued: list[Callable[[], None]] | None = None,
    invoked: list[GlobalActionID] | None = None,
) -> GlobalHotkeyRegistry:
    dispatch_queue = queued if queued is not None else []
    invoked_actions = invoked if invoked is not None else []
    return GlobalHotkeyRegistry(
        backend=backend,
        main_thread_dispatch=dispatch_queue.append,
        on_action=invoked_actions.append,
    )


def prepared(result: HotkeyPrepareResult):
    assert result.accepted
    assert result.preparation is not None
    assert result.refusal is None
    return result.preparation


def registered_id(backend: FakeBackend, index: int = -1) -> int:
    registrations = [event for event in backend.events if event[0] == "register"]
    return int(registrations[index][2])


def test_registry_installs_one_handler_and_routes_only_the_registered_action_id() -> None:
    backend = FakeBackend()
    queued: list[Callable[[], None]] = []
    invoked: list[GlobalActionID] = []
    registry = registry_with(backend, queued=queued, invoked=invoked)

    first = registry.prepare({ACTION: COMMAND_K})
    registry.commit(prepared(first))
    action_hotkey_id = registered_id(backend)

    backend.emit(999_999)
    backend.emit(action_hotkey_id)

    assert backend.events.count(("install_handler",)) == 1
    assert len(queued) == 1
    assert invoked == []
    queued.pop()()
    assert invoked == [ACTION]


def test_prepared_candidate_is_inert_until_commit() -> None:
    backend = FakeBackend()
    queued: list[Callable[[], None]] = []
    registry = registry_with(backend, queued=queued)

    candidate = registry.prepare({ACTION: COMMAND_K})
    candidate_id = registered_id(backend)
    backend.emit(candidate_id)

    assert candidate.accepted
    assert queued == []

    registry.commit(prepared(candidate))
    backend.emit(candidate_id)
    assert len(queued) == 1


def test_commit_registers_replacement_before_unregistering_previous_binding() -> None:
    backend = FakeBackend()
    registry = registry_with(backend)
    registry.rebind({ACTION: COMMAND_K})
    backend.events.clear()

    candidate = registry.prepare({ACTION: CONTROL_SHIFT_K})

    assert [event[0] for event in backend.events] == ["register"]
    registry.commit(prepared(candidate))
    assert [event[0] for event in backend.events] == ["register", "unregister"]
    assert registry.active_bindings == {ACTION: CONTROL_SHIFT_K}


def test_registration_refusal_rolls_back_candidates_and_preserves_live_binding() -> None:
    backend = FakeBackend()
    registry = registry_with(backend)
    registry.rebind({ACTION: COMMAND_K})
    original_id = registered_id(backend)
    backend.refused_key_codes[CONTROL_SHIFT_K.key_code] = -9878
    backend.events.clear()

    result = registry.prepare({ACTION: CONTROL_SHIFT_K})

    assert not result.accepted
    assert result.preparation is None
    assert result.refusal == HotkeyRegistrationRefusal.from_os_status(-9878)
    assert registry.active_bindings == {ACTION: COMMAND_K}
    assert backend.events == [("register", CONTROL_SHIFT_K, original_id + 1)]


def test_rollback_unregisters_candidate_and_preserves_previous_route() -> None:
    backend = FakeBackend()
    queued: list[Callable[[], None]] = []
    registry = registry_with(backend, queued=queued)
    registry.rebind({ACTION: COMMAND_K})
    original_id = registered_id(backend)

    result = registry.prepare({ACTION: CONTROL_SHIFT_K})
    candidate_id = registered_id(backend)
    registry.rollback(prepared(result))
    backend.emit(candidate_id)
    backend.emit(original_id)

    assert registry.active_bindings == {ACTION: COMMAND_K}
    assert ("unregister", f"hotkey-ref-{candidate_id}") in backend.events
    assert len(queued) == 1


def test_rebind_fences_callback_queued_by_the_previous_registration() -> None:
    backend = FakeBackend()
    queued: list[Callable[[], None]] = []
    invoked: list[GlobalActionID] = []
    registry = registry_with(backend, queued=queued, invoked=invoked)
    registry.rebind({ACTION: COMMAND_K})
    stale_id = registered_id(backend)
    backend.emit(stale_id)
    assert len(queued) == 1

    registry.rebind({ACTION: CONTROL_SHIFT_K})
    queued.pop()()
    backend.emit(stale_id)

    assert invoked == []
    assert queued == []


def test_clear_and_close_unregister_owned_references_exactly_once() -> None:
    backend = FakeBackend()
    registry = registry_with(backend)
    registry.rebind({ACTION: COMMAND_K})
    first_ref = f"hotkey-ref-{registered_id(backend)}"

    registry.clear()
    registry.clear()
    registry.rebind({ACTION: CONTROL_SHIFT_K})
    second_ref = f"hotkey-ref-{registered_id(backend)}"
    registry.close()
    registry.close()

    assert backend.events.count(("unregister", first_ref)) == 1
    assert backend.events.count(("unregister", second_ref)) == 1
    assert backend.events.count(("remove_handler", "handler-ref")) == 1
    assert registry.active_bindings == {}
    assert registry.closed


def test_close_rolls_back_a_prepared_candidate_and_fences_late_callbacks() -> None:
    backend = FakeBackend()
    queued: list[Callable[[], None]] = []
    invoked: list[GlobalActionID] = []
    registry = registry_with(backend, queued=queued, invoked=invoked)
    registry.rebind({ACTION: COMMAND_K})
    active_id = registered_id(backend)
    backend.emit(active_id)
    pending = registry.prepare({ACTION: CONTROL_SHIFT_K})
    pending_id = registered_id(backend)

    registry.close()
    queued.pop()()
    backend.emit(active_id)
    backend.emit(pending_id)

    assert pending.accepted
    assert invoked == []
    assert queued == []
    assert backend.events.count(("unregister", f"hotkey-ref-{active_id}")) == 1
    assert backend.events.count(("unregister", f"hotkey-ref-{pending_id}")) == 1


def test_same_bindings_are_an_idempotent_prepare_and_commit() -> None:
    backend = FakeBackend()
    registry = registry_with(backend)
    registry.rebind({ACTION: COMMAND_K})
    backend.events.clear()

    result = registry.prepare({ACTION: COMMAND_K})
    registry.commit(prepared(result))

    assert backend.events == []
    assert registry.active_bindings == {ACTION: COMMAND_K}


def test_failed_commit_keeps_old_binding_and_preparation_retryable() -> None:
    backend = FakeBackend()
    queued: list[Callable[[], None]] = []
    registry = registry_with(backend, queued=queued)
    registry.rebind({ACTION: COMMAND_K})
    old_id = registered_id(backend)
    old_ref = f"hotkey-ref-{old_id}"
    backend.unregister_failures[old_ref] = 1
    result = registry.prepare({ACTION: CONTROL_SHIFT_K})
    handle = prepared(result)
    candidate_id = registered_id(backend)

    with pytest.raises(HotkeyCleanupError, match="commit cleanup failed") as raised:
        registry.commit(handle)

    assert raised.value.preparation == handle
    assert raised.value.failure_count == 1
    assert len(str(raised.value)) <= 160
    assert registry.active_bindings == {ACTION: COMMAND_K}
    backend.emit(candidate_id)
    backend.emit(old_id)
    assert len(queued) == 1

    registry.commit(handle)
    assert registry.active_bindings == {ACTION: CONTROL_SHIFT_K}
    assert backend.events.count(("unregister", old_ref)) == 2


def test_failed_rollback_keeps_candidate_inert_and_preparation_retryable() -> None:
    backend = FakeBackend()
    queued: list[Callable[[], None]] = []
    registry = registry_with(backend, queued=queued)
    registry.rebind({ACTION: COMMAND_K})
    result = registry.prepare({ACTION: CONTROL_SHIFT_K})
    handle = prepared(result)
    candidate_id = registered_id(backend)
    candidate_ref = f"hotkey-ref-{candidate_id}"
    backend.unregister_failures[candidate_ref] = 1

    with pytest.raises(HotkeyCleanupError, match="rollback cleanup failed") as raised:
        registry.rollback(handle)

    assert raised.value.preparation == handle
    assert registry.active_bindings == {ACTION: COMMAND_K}
    backend.emit(candidate_id)
    assert queued == []

    registry.rollback(handle)
    assert registry.active_bindings == {ACTION: COMMAND_K}
    assert backend.events.count(("unregister", candidate_ref)) == 2


def test_failed_clear_surfaces_preparation_for_durable_rollback() -> None:
    backend = FakeBackend()
    registry = registry_with(backend)
    registry.rebind({ACTION: COMMAND_K})
    old_ref = f"hotkey-ref-{registered_id(backend)}"
    backend.unregister_failures[old_ref] = 1

    with pytest.raises(HotkeyCleanupError, match="commit cleanup failed") as raised:
        registry.clear()

    assert raised.value.preparation is not None
    assert registry.active_bindings == {ACTION: COMMAND_K}
    registry.rollback(raised.value.preparation)
    assert registry.active_bindings == {ACTION: COMMAND_K}
    assert backend.events.count(("unregister", old_ref)) == 1


def test_failed_close_retains_failed_resources_and_retries_independent_cleanup() -> None:
    backend = FakeBackend()
    queued: list[Callable[[], None]] = []
    invoked: list[GlobalActionID] = []
    registry = registry_with(backend, queued=queued, invoked=invoked)
    registry.rebind({ACTION: COMMAND_K})
    active_id = registered_id(backend)
    active_ref = f"hotkey-ref-{active_id}"
    backend.emit(active_id)
    pending = registry.prepare({ACTION: CONTROL_SHIFT_K})
    pending_id = registered_id(backend)
    pending_ref = f"hotkey-ref-{pending_id}"
    backend.unregister_failures.update({active_ref: 1, pending_ref: 1})

    with pytest.raises(HotkeyCleanupError, match="close cleanup failed") as raised:
        registry.close()

    assert raised.value.preparation is None
    assert raised.value.failure_count == 2
    assert not registry.closed
    assert registry.active_bindings == {ACTION: COMMAND_K}
    assert backend.events.count(("unregister", active_ref)) == 1
    assert backend.events.count(("unregister", pending_ref)) == 1
    assert ("remove_handler", "handler-ref") not in backend.events
    queued.pop()()
    assert invoked == []

    registry.close()
    assert pending.accepted
    assert registry.closed
    assert registry.active_bindings == {}
    assert backend.events.count(("unregister", active_ref)) == 2
    assert backend.events.count(("unregister", pending_ref)) == 2
    assert backend.events.count(("remove_handler", "handler-ref")) == 1


def test_close_does_not_retry_resources_cleaned_before_an_independent_failure() -> None:
    backend = FakeBackend()
    registry = registry_with(backend)
    registry.rebind({ACTION: COMMAND_K})
    active_ref = f"hotkey-ref-{registered_id(backend)}"
    registry.prepare({ACTION: CONTROL_SHIFT_K})
    pending_ref = f"hotkey-ref-{registered_id(backend)}"
    backend.unregister_failures[pending_ref] = 1

    with pytest.raises(HotkeyCleanupError, match="close cleanup failed"):
        registry.close()

    assert backend.events.count(("unregister", active_ref)) == 1
    assert backend.events.count(("unregister", pending_ref)) == 1
    registry.close()
    assert backend.events.count(("unregister", active_ref)) == 1
    assert backend.events.count(("unregister", pending_ref)) == 2
    assert registry.closed


def test_failed_handler_removal_keeps_close_retryable_without_double_unregister() -> None:
    backend = FakeBackend()
    registry = registry_with(backend)
    registry.rebind({ACTION: COMMAND_K})
    active_ref = f"hotkey-ref-{registered_id(backend)}"
    backend.remove_handler_failures = 1

    with pytest.raises(HotkeyCleanupError, match="close cleanup failed"):
        registry.close()

    assert not registry.closed
    assert registry.active_bindings == {}
    assert backend.events.count(("unregister", active_ref)) == 1
    assert backend.events.count(("remove_handler", "handler-ref")) == 1

    registry.close()
    assert registry.closed
    assert backend.events.count(("unregister", active_ref)) == 1
    assert backend.events.count(("remove_handler", "handler-ref")) == 2


def test_mutations_must_run_on_the_injected_main_thread() -> None:
    backend = FakeBackend()
    on_main_thread = True
    registry = GlobalHotkeyRegistry(
        backend=backend,
        main_thread_dispatch=lambda callback: callback(),
        on_action=lambda _action: None,
        is_main_thread=lambda: on_main_thread,
    )
    on_main_thread = False

    with pytest.raises(RuntimeError, match="main thread"):
        registry.prepare({ACTION: COMMAND_K})


class FakeCFunction:
    def __init__(self, implementation: Callable[..., object]) -> None:
        self.implementation = implementation
        self.calls: list[tuple[object, ...]] = []
        self.argtypes: list[object] | None = None
        self.restype: object | None = None

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        return self.implementation(*args)


class FakeEventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


@dataclass
class FakeCarbonLibrary:
    register_status: int = 0
    unregister_statuses: tuple[int, ...] = ()
    remove_handler_statuses: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        self.installed_callback: object | None = None
        self.registered_signature = 0
        self.event_hotkey_id = 0
        self.event_signature_override: int | None = None
        self._unregister_statuses = list(self.unregister_statuses)
        self._remove_handler_statuses = list(self.remove_handler_statuses)
        self.GetApplicationEventTarget = FakeCFunction(lambda: 101)
        self.InstallEventHandler = FakeCFunction(self._install_handler)
        self.RemoveEventHandler = FakeCFunction(self._remove_handler)
        self.RegisterEventHotKey = FakeCFunction(self._register_hotkey)
        self.UnregisterEventHotKey = FakeCFunction(self._unregister_hotkey)
        self.GetEventParameter = FakeCFunction(self._get_event_parameter)

    def _install_handler(self, *args: object) -> int:
        self.installed_callback = args[1]
        out_handler = ctypes.cast(args[5], ctypes.POINTER(ctypes.c_void_p))
        out_handler[0] = ctypes.c_void_p(202)
        return 0

    def _register_hotkey(self, *args: object) -> int:
        if self.register_status == 0:
            self.registered_signature = int(args[2].signature)  # type: ignore[attr-defined]
            self.event_hotkey_id = int(args[2].id)  # type: ignore[attr-defined]
            out_hotkey = ctypes.cast(args[5], ctypes.POINTER(ctypes.c_void_p))
            out_hotkey[0] = ctypes.c_void_p(303)
        return self.register_status

    def _get_event_parameter(self, *args: object) -> int:
        assert args[4] == ctypes.sizeof(FakeEventHotKeyID)
        out_size = ctypes.cast(args[5], ctypes.POINTER(ctypes.c_ulong))
        out_size[0] = ctypes.c_ulong(ctypes.sizeof(FakeEventHotKeyID))
        out_hotkey_id = ctypes.cast(args[6], ctypes.POINTER(FakeEventHotKeyID))
        out_hotkey_id[0] = FakeEventHotKeyID(
            signature=(
                self.registered_signature
                if self.event_signature_override is None
                else self.event_signature_override
            ),
            id=self.event_hotkey_id,
        )
        return 0

    def _unregister_hotkey(self, _hotkey: object) -> int:
        if self._unregister_statuses:
            return self._unregister_statuses.pop(0)
        return 0

    def _remove_handler(self, _handler: object) -> int:
        if self._remove_handler_statuses:
            return self._remove_handler_statuses.pop(0)
        return 0


def test_carbon_library_is_lazy_and_binds_sdk_widths_before_installing() -> None:
    library = FakeCarbonLibrary()
    loads: list[str] = []
    backend = CarbonHotkeyBackend(
        library_loader=lambda path: loads.append(path) or library,
        platform="darwin",
    )

    assert loads == []
    handler = backend.install_handler(lambda _hotkey_id: None)

    assert loads == ["/System/Library/Frameworks/Carbon.framework/Carbon"]
    assert handler is not None
    assert library.GetApplicationEventTarget.argtypes == []
    assert library.GetApplicationEventTarget.restype is ctypes.c_void_p
    assert library.InstallEventHandler.argtypes is not None
    assert library.InstallEventHandler.argtypes[2] is ctypes.c_ulong
    assert library.GetEventParameter.argtypes is not None
    assert library.GetEventParameter.argtypes[4] is ctypes.c_ulong
    assert library.GetEventParameter.argtypes[5] == ctypes.POINTER(ctypes.c_ulong)
    assert library.RegisterEventHotKey.argtypes is not None
    assert library.RegisterEventHotKey.argtypes[:2] == [ctypes.c_uint32, ctypes.c_uint32]
    assert library.RegisterEventHotKey.argtypes[4] is ctypes.c_uint32


def test_carbon_backend_translates_normalized_chord_and_uses_nonexclusive_options() -> None:
    library = FakeCarbonLibrary()
    backend = CarbonHotkeyBackend(library_loader=lambda _path: library, platform="darwin")
    handler = backend.install_handler(lambda _hotkey_id: None)
    all_modifiers = ShortcutChord(
        key_code=40,
        key_label="K",
        modifiers=frozenset(ShortcutModifier),
    )

    registration = backend.register_hotkey(all_modifiers, 17)

    assert isinstance(registration, BackendHotkeyRegistration)
    register_call = library.RegisterEventHotKey.calls[0]
    assert register_call[0] == 40
    assert register_call[1] == 6912
    assert register_call[2].id == 17
    assert register_call[3] == 101
    assert register_call[4] == 0
    backend.unregister_hotkey(registration)
    backend.remove_handler(handler)
    assert len(library.UnregisterEventHotKey.calls) == 1
    assert len(library.RemoveEventHandler.calls) == 1


def test_carbon_registration_failure_is_a_bounded_refusal() -> None:
    library = FakeCarbonLibrary(register_status=-9878)
    backend = CarbonHotkeyBackend(library_loader=lambda _path: library, platform="darwin")
    handler = backend.install_handler(lambda _hotkey_id: None)

    result = backend.register_hotkey(COMMAND_K, 1)

    assert result == HotkeyRegistrationRefusal.from_os_status(-9878)
    assert len(result.message) <= 160
    backend.remove_handler(handler)


def test_carbon_handler_extracts_only_the_bounded_owned_hotkey_id() -> None:
    library = FakeCarbonLibrary()
    received: list[int] = []
    backend = CarbonHotkeyBackend(library_loader=lambda _path: library, platform="darwin")
    handler = backend.install_handler(received.append)
    registration = backend.register_hotkey(COMMAND_K, 17)
    assert isinstance(registration, BackendHotkeyRegistration)
    assert callable(library.installed_callback)

    status = library.installed_callback(None, ctypes.c_void_p(404), None)
    library.event_signature_override = 0
    ignored_status = library.installed_callback(None, ctypes.c_void_p(404), None)

    assert status == 0
    assert ignored_status != 0
    assert received == [17]
    assert len(library.GetEventParameter.calls) == 2
    backend.unregister_hotkey(registration)
    backend.remove_handler(handler)


def test_carbon_unregister_failure_retains_owned_reference_for_retry() -> None:
    library = FakeCarbonLibrary(unregister_statuses=(-9878, 0))
    backend = CarbonHotkeyBackend(library_loader=lambda _path: library, platform="darwin")
    handler = backend.install_handler(lambda _hotkey_id: None)
    registration = backend.register_hotkey(COMMAND_K, 17)
    assert isinstance(registration, BackendHotkeyRegistration)
    reference = registration.reference

    with pytest.raises(CarbonBackendError, match="UnregisterEventHotKey") as raised:
        backend.unregister_hotkey(registration)

    assert raised.value.os_status == -9878
    assert reference.alive  # type: ignore[attr-defined]
    assert reference.pointer in backend._registrations  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="hotkeys remain registered"):
        backend.remove_handler(handler)

    backend.unregister_hotkey(registration)
    assert not reference.alive  # type: ignore[attr-defined]
    assert reference.pointer not in backend._registrations  # type: ignore[attr-defined]
    backend.remove_handler(handler)


def test_carbon_handler_removal_failure_retains_callback_and_target_for_retry() -> None:
    library = FakeCarbonLibrary(remove_handler_statuses=(-9879, 0))
    backend = CarbonHotkeyBackend(library_loader=lambda _path: library, platform="darwin")
    handler = backend.install_handler(lambda _hotkey_id: None)

    with pytest.raises(CarbonBackendError, match="RemoveEventHandler") as raised:
        backend.remove_handler(handler)

    assert raised.value.os_status == -9879
    assert handler.alive  # type: ignore[attr-defined]
    assert handler.callback is not None  # type: ignore[attr-defined]
    assert backend._handler is handler
    assert backend._event_target == 101

    backend.remove_handler(handler)
    assert not handler.alive  # type: ignore[attr-defined]
    assert handler.callback is None  # type: ignore[attr-defined]
    assert backend._handler is None
    assert backend._event_target is None


def test_registry_source_cannot_observe_ordinary_keys_or_event_text() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src" / "sidepulse" / "global_hotkeys.py"
    ).read_text(encoding="utf-8")

    forbidden = (
        "addGlobalMonitorForEvents",
        "addLocalMonitorForEvents",
        "CGEventTap",
        "charactersIgnoringModifiers",
    )
    assert [token for token in forbidden if token in source] == []
