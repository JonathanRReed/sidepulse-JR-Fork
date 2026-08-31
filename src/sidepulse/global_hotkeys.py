"""Transactional global-hotkey registration behind a lazy Carbon boundary."""

from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .global_actions import (
    GlobalActionID,
    ShortcutChord,
    ShortcutModifier,
    normalized_shortcut,
    validate_global_action_bindings,
)

_CARBON_PATH = "/System/Library/Frameworks/Carbon.framework/Carbon"
_EVENT_NOT_HANDLED_STATUS = -9874
_HOTKEY_SIGNATURE = int.from_bytes(b"JRBR", byteorder="big")


@dataclass(frozen=True, slots=True)
class BackendHotkeyRegistration:
    """Opaque reference retained until the backend unregisters it."""

    reference: object


@dataclass(frozen=True, slots=True)
class HotkeyRegistrationRefusal:
    """Bounded, presentation-safe registration refusal."""

    os_status: int
    message: str

    @classmethod
    def from_os_status(cls, status: int) -> HotkeyRegistrationRefusal:
        numeric_status = int(status)
        return cls(
            os_status=numeric_status,
            message=f"macOS refused the shortcut registration (OSStatus {numeric_status}).",
        )


class HotkeyBackend(Protocol):
    def install_handler(self, callback: Callable[[int], None]) -> object: ...

    def register_hotkey(
        self,
        chord: ShortcutChord,
        hotkey_id: int,
    ) -> BackendHotkeyRegistration | HotkeyRegistrationRefusal: ...

    def unregister_hotkey(self, registration: BackendHotkeyRegistration) -> None: ...

    def remove_handler(self, handler: object) -> None: ...


@dataclass(frozen=True, slots=True)
class HotkeyPreparation:
    """Opaque handle for one prepared, not-yet-live binding change."""

    _owner: object
    _serial: int


@dataclass(frozen=True, slots=True)
class HotkeyPrepareResult:
    preparation: HotkeyPreparation | None = None
    refusal: HotkeyRegistrationRefusal | None = None

    @property
    def accepted(self) -> bool:
        return self.preparation is not None and self.refusal is None


class HotkeyCleanupError(RuntimeError):
    """Bounded retry signal for native resources that remain owned."""

    def __init__(
        self,
        operation: str,
        failure_count: int,
        *,
        preparation: HotkeyPreparation | None = None,
        refusal: HotkeyRegistrationRefusal | None = None,
    ) -> None:
        super().__init__(
            f"Global hotkey {operation} cleanup failed for "
            f"{failure_count} owned resource(s)."
        )
        self.operation = operation
        self.failure_count = failure_count
        self.preparation = preparation
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class _RegistrationRecord:
    action: GlobalActionID
    chord: ShortcutChord
    hotkey_id: int
    registration: BackendHotkeyRegistration


@dataclass(slots=True)
class _PreparedState:
    handle: HotkeyPreparation
    target_bindings: dict[GlobalActionID, ShortcutChord]
    retained: dict[GlobalActionID, _RegistrationRecord]
    candidates: dict[GlobalActionID, _RegistrationRecord]
    committable: bool = True


@dataclass(frozen=True, slots=True)
class _CleanupOutcome:
    succeeded: tuple[_RegistrationRecord, ...]
    failed: tuple[_RegistrationRecord, ...]


class GlobalHotkeyRegistry:
    """Main-thread owner for active and transactionally prepared shortcuts."""

    def __init__(
        self,
        *,
        backend: HotkeyBackend,
        main_thread_dispatch: Callable[[Callable[[], None]], None],
        on_action: Callable[[GlobalActionID], None],
        is_main_thread: Callable[[], bool] | None = None,
    ) -> None:
        self._backend = backend
        self._main_thread_dispatch = main_thread_dispatch
        self._on_action = on_action
        self._is_main_thread = is_main_thread or (
            lambda: threading.current_thread() is threading.main_thread()
        )
        self._owner = object()
        self._next_hotkey_id = 1
        self._next_preparation_serial = 1
        self._active: dict[GlobalActionID, _RegistrationRecord] = {}
        self._routes: dict[int, GlobalActionID] = {}
        self._pending: _PreparedState | None = None
        self._closed = False
        self._closing = False
        self._require_main_thread()
        self._handler = backend.install_handler(self._receive_hotkey)

    @property
    def active_bindings(self) -> dict[GlobalActionID, ShortcutChord]:
        return {action: record.chord for action, record in self._active.items()}

    @property
    def closed(self) -> bool:
        return self._closed

    def prepare(
        self,
        bindings: Mapping[GlobalActionID, ShortcutChord],
    ) -> HotkeyPrepareResult:
        self._require_open_main_thread()
        if self._pending is not None:
            raise RuntimeError("a global hotkey change is already prepared")
        target = self._normalized_bindings(bindings)
        retained: dict[GlobalActionID, _RegistrationRecord] = {}
        candidates: dict[GlobalActionID, _RegistrationRecord] = {}

        for action, chord in sorted(target.items(), key=lambda item: item[0].value):
            current = self._active.get(action)
            if current is not None and normalized_shortcut(current.chord) == normalized_shortcut(chord):
                retained[action] = _RegistrationRecord(
                    action=action,
                    chord=chord,
                    hotkey_id=current.hotkey_id,
                    registration=current.registration,
                )
                continue

            hotkey_id = self._allocate_hotkey_id()
            registered = self._backend.register_hotkey(chord, hotkey_id)
            if isinstance(registered, HotkeyRegistrationRefusal):
                cleanup = self._attempt_unregister_records(candidates.values())
                if cleanup.failed:
                    handle = self._new_preparation_handle()
                    self._pending = _PreparedState(
                        handle=handle,
                        target_bindings=target,
                        retained=retained,
                        candidates={record.action: record for record in cleanup.failed},
                        committable=False,
                    )
                    raise HotkeyCleanupError(
                        "prepare",
                        len(cleanup.failed),
                        preparation=handle,
                        refusal=registered,
                    )
                return HotkeyPrepareResult(refusal=registered)
            candidates[action] = _RegistrationRecord(
                action=action,
                chord=chord,
                hotkey_id=hotkey_id,
                registration=registered,
            )

        handle = self._new_preparation_handle()
        self._pending = _PreparedState(
            handle=handle,
            target_bindings=target,
            retained=retained,
            candidates=candidates,
        )
        return HotkeyPrepareResult(preparation=handle)

    def commit(self, preparation: HotkeyPreparation) -> None:
        self._require_open_main_thread()
        pending = self._pending_for(preparation)
        if not pending.committable:
            raise RuntimeError("global hotkey preparation can only be rolled back")
        next_active = {**pending.retained, **pending.candidates}
        previous = tuple(
            record
            for action, record in self._active.items()
            if action not in pending.retained
        )

        cleanup = self._attempt_unregister_records(previous)
        for record in cleanup.succeeded:
            self._active.pop(record.action, None)
            self._routes.pop(record.hotkey_id, None)
        if cleanup.failed:
            raise HotkeyCleanupError(
                "commit",
                len(cleanup.failed),
                preparation=preparation,
            )

        self._active = next_active
        self._routes = {
            record.hotkey_id: action for action, record in next_active.items()
        }
        self._pending = None

    def rollback(self, preparation: HotkeyPreparation) -> None:
        self._require_open_main_thread()
        pending = self._pending_for(preparation)
        pending.committable = False
        cleanup = self._attempt_unregister_records(pending.candidates.values())
        pending.candidates = {record.action: record for record in cleanup.failed}
        if cleanup.failed:
            raise HotkeyCleanupError(
                "rollback",
                len(cleanup.failed),
                preparation=preparation,
            )
        self._pending = None

    def rebind(
        self,
        bindings: Mapping[GlobalActionID, ShortcutChord],
    ) -> HotkeyPrepareResult:
        result = self.prepare(bindings)
        if result.preparation is not None:
            self.commit(result.preparation)
        return result

    def clear(self) -> HotkeyPrepareResult:
        return self.rebind({})

    def close(self) -> None:
        if self._closed:
            return
        self._require_main_thread()
        self._closing = True
        failure_count = 0
        pending = self._pending
        if pending is not None:
            pending.committable = False
            pending.retained = {}
            candidate_cleanup = self._attempt_unregister_records(
                pending.candidates.values()
            )
            pending.candidates = {
                record.action: record for record in candidate_cleanup.failed
            }
            failure_count += len(candidate_cleanup.failed)

        active_cleanup = self._attempt_unregister_records(self._active.values())
        for record in active_cleanup.succeeded:
            self._active.pop(record.action, None)
            self._routes.pop(record.hotkey_id, None)
        failure_count += len(active_cleanup.failed)

        candidates_remain = pending is not None and bool(pending.candidates)
        if not self._active and not candidates_remain:
            try:
                self._backend.remove_handler(self._handler)
            except Exception:
                failure_count += 1
            else:
                self._pending = None
                self._closed = True
                self._closing = False

        if failure_count:
            raise HotkeyCleanupError("close", failure_count)

    def _normalized_bindings(
        self,
        bindings: Mapping[GlobalActionID, ShortcutChord],
    ) -> dict[GlobalActionID, ShortcutChord]:
        validate_global_action_bindings(bindings)
        normalized = dict(bindings)
        if any(type(action) is not GlobalActionID for action in normalized):
            raise ValueError("global hotkey bindings require known action identifiers")
        return normalized

    def _allocate_hotkey_id(self) -> int:
        hotkey_id = self._next_hotkey_id
        self._next_hotkey_id += 1
        if self._next_hotkey_id > 0xFFFFFFFF:
            self._next_hotkey_id = 1
        return hotkey_id

    def _new_preparation_handle(self) -> HotkeyPreparation:
        handle = HotkeyPreparation(self._owner, self._next_preparation_serial)
        self._next_preparation_serial += 1
        return handle

    def _pending_for(self, preparation: HotkeyPreparation) -> _PreparedState:
        pending = self._pending
        if (
            type(preparation) is not HotkeyPreparation
            or preparation._owner is not self._owner
            or pending is None
            or preparation != pending.handle
        ):
            raise RuntimeError("global hotkey preparation is stale or foreign")
        return pending

    def _attempt_unregister_records(
        self,
        records: Iterable[_RegistrationRecord],
    ) -> _CleanupOutcome:
        succeeded: list[_RegistrationRecord] = []
        failed: list[_RegistrationRecord] = []
        for record in records:
            try:
                self._backend.unregister_hotkey(record.registration)
            except Exception:
                failed.append(record)
            else:
                succeeded.append(record)
        return _CleanupOutcome(tuple(succeeded), tuple(failed))

    def _receive_hotkey(self, hotkey_id: int) -> None:
        if self._closed or self._closing or hotkey_id not in self._routes:
            return
        self._main_thread_dispatch(
            lambda hotkey_id=hotkey_id: self._invoke_if_current(hotkey_id)
        )

    def _invoke_if_current(self, hotkey_id: int) -> None:
        if self._closed or self._closing:
            return
        action = self._routes.get(hotkey_id)
        if action is not None:
            self._on_action(action)

    def _require_main_thread(self) -> None:
        if not self._is_main_thread():
            raise RuntimeError("global hotkey registry mutations require the main thread")

    def _require_open_main_thread(self) -> None:
        self._require_main_thread()
        if self._closed or self._closing:
            raise RuntimeError("global hotkey registry is closed")


class CarbonBackendError(RuntimeError):
    def __init__(self, operation: str, status: int | None = None) -> None:
        suffix = "" if status is None else f" (OSStatus {int(status)})"
        super().__init__(f"Carbon {operation} failed{suffix}")
        self.operation = operation
        self.os_status = None if status is None else int(status)


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_EventHandlerCallback = ctypes.CFUNCTYPE(
    ctypes.c_int32,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
)


@dataclass(slots=True)
class _CarbonHandlerReference:
    pointer: int
    callback: Any
    alive: bool = True


@dataclass(slots=True)
class _CarbonHotkeyReference:
    pointer: int
    alive: bool = True


def _four_char_code(value: bytes) -> int:
    if len(value) != 4:
        raise ValueError("Carbon four-character codes must contain four bytes")
    return int.from_bytes(value, byteorder="big")


_CARBON_MODIFIERS = {
    ShortcutModifier.COMMAND: 1 << 8,
    ShortcutModifier.SHIFT: 1 << 9,
    ShortcutModifier.OPTION: 1 << 11,
    ShortcutModifier.CONTROL: 1 << 12,
}


class CarbonHotkeyBackend:
    """Lazy ctypes bridge for the non-exclusive Carbon hotkey API."""

    def __init__(
        self,
        *,
        library_loader: Callable[[str], Any] | None = None,
        platform: str | None = None,
    ) -> None:
        self._library_loader = library_loader or ctypes.CDLL
        self._platform = platform or sys.platform
        self._library: Any | None = None
        self._event_target: int | None = None
        self._handler: _CarbonHandlerReference | None = None
        self._registrations: dict[int, _CarbonHotkeyReference] = {}

    def install_handler(self, callback: Callable[[int], None]) -> object:
        if self._handler is not None and self._handler.alive:
            raise RuntimeError("Carbon application handler is already installed")
        library = self._load_library()
        target = library.GetApplicationEventTarget()
        if not target:
            raise CarbonBackendError("GetApplicationEventTarget")

        c_callback = _EventHandlerCallback(self._event_callback(callback))
        event_type = _EventTypeSpec(
            eventClass=_four_char_code(b"keyb"),
            eventKind=5,
        )
        out_handler = ctypes.c_void_p()
        status = library.InstallEventHandler(
            target,
            c_callback,
            1,
            ctypes.byref(event_type),
            None,
            ctypes.byref(out_handler),
        )
        if status != 0:
            raise CarbonBackendError("InstallEventHandler", status)
        if not out_handler.value:
            raise CarbonBackendError("InstallEventHandler returned no reference")

        self._event_target = int(target)
        self._handler = _CarbonHandlerReference(
            pointer=int(out_handler.value),
            callback=c_callback,
        )
        return self._handler

    def register_hotkey(
        self,
        chord: ShortcutChord,
        hotkey_id: int,
    ) -> BackendHotkeyRegistration | HotkeyRegistrationRefusal:
        library = self._load_library()
        if self._event_target is None or self._handler is None or not self._handler.alive:
            raise RuntimeError("Carbon application handler is not installed")
        if type(hotkey_id) is not int or not 1 <= hotkey_id <= 0xFFFFFFFF:
            raise ValueError("Carbon hotkey ID must be a nonzero UInt32")
        normalized_shortcut(chord)
        modifiers = sum(
            carbon_value
            for modifier, carbon_value in _CARBON_MODIFIERS.items()
            if modifier in chord.modifiers
        )
        identity = _EventHotKeyID(signature=_HOTKEY_SIGNATURE, id=hotkey_id)
        out_hotkey = ctypes.c_void_p()
        status = library.RegisterEventHotKey(
            chord.key_code,
            modifiers,
            identity,
            self._event_target,
            0,
            ctypes.byref(out_hotkey),
        )
        if status != 0:
            return HotkeyRegistrationRefusal.from_os_status(status)
        if not out_hotkey.value:
            raise CarbonBackendError("RegisterEventHotKey returned no reference")
        reference = _CarbonHotkeyReference(pointer=int(out_hotkey.value))
        self._registrations[reference.pointer] = reference
        return BackendHotkeyRegistration(reference=reference)

    def unregister_hotkey(self, registration: BackendHotkeyRegistration) -> None:
        if type(registration) is not BackendHotkeyRegistration or not isinstance(
            registration.reference,
            _CarbonHotkeyReference,
        ):
            raise ValueError("registration is not owned by the Carbon backend")
        reference = registration.reference
        if not reference.alive:
            return
        status = self._load_library().UnregisterEventHotKey(
            ctypes.c_void_p(reference.pointer)
        )
        if status != 0:
            raise CarbonBackendError("UnregisterEventHotKey", status)
        reference.alive = False
        self._registrations.pop(reference.pointer, None)

    def remove_handler(self, handler: object) -> None:
        if handler is not self._handler or not isinstance(handler, _CarbonHandlerReference):
            raise ValueError("handler is not owned by the Carbon backend")
        if not handler.alive:
            return
        if self._registrations:
            raise RuntimeError("Carbon handler cannot close while hotkeys remain registered")
        status = self._load_library().RemoveEventHandler(ctypes.c_void_p(handler.pointer))
        if status != 0:
            raise CarbonBackendError("RemoveEventHandler", status)
        handler.alive = False
        handler.callback = None
        self._handler = None
        self._event_target = None

    def _load_library(self) -> Any:
        if self._library is not None:
            return self._library
        if self._platform != "darwin":
            raise RuntimeError("Carbon global hotkeys are available only on macOS")
        library = self._library_loader(_CARBON_PATH)
        self._bind_signatures(library)
        self._library = library
        return library

    def _bind_signatures(self, library: Any) -> None:
        library.GetApplicationEventTarget.argtypes = []
        library.GetApplicationEventTarget.restype = ctypes.c_void_p
        library.InstallEventHandler.argtypes = [
            ctypes.c_void_p,
            _EventHandlerCallback,
            ctypes.c_ulong,
            ctypes.POINTER(_EventTypeSpec),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        library.InstallEventHandler.restype = ctypes.c_int32
        library.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        library.RemoveEventHandler.restype = ctypes.c_int32
        library.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            _EventHotKeyID,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        library.RegisterEventHotKey.restype = ctypes.c_int32
        library.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        library.UnregisterEventHotKey.restype = ctypes.c_int32
        library.GetEventParameter.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.c_void_p,
        ]
        library.GetEventParameter.restype = ctypes.c_int32

    def _event_callback(
        self,
        callback: Callable[[int], None],
    ) -> Callable[[object, object, object], int]:
        def handle_event(
            _handler_call: object,
            event: object,
            _user_data: object,
        ) -> int:
            library = self._library
            if library is None:
                return _EVENT_NOT_HANDLED_STATUS
            identity = _EventHotKeyID()
            actual_size = ctypes.c_ulong()
            status = library.GetEventParameter(
                event,
                _four_char_code(b"----"),
                _four_char_code(b"hkid"),
                None,
                ctypes.sizeof(identity),
                ctypes.byref(actual_size),
                ctypes.byref(identity),
            )
            if (
                status != 0
                or actual_size.value != ctypes.sizeof(identity)
                or identity.signature != _HOTKEY_SIGNATURE
            ):
                return _EVENT_NOT_HANDLED_STATUS
            try:
                callback(int(identity.id))
            except Exception:
                return _EVENT_NOT_HANDLED_STATUS
            return 0

        return handle_event
