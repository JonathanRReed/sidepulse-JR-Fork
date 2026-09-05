"""Native controller for explicit Creator Micro keymap setup."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from AppKit import NSAlert, NSAlertFirstButtonReturn

from .creator_micro_keymap import KeymapPlan


@dataclass(frozen=True, slots=True)
class SetupPreview:
    """A reviewed plan bound to the approved device identity."""

    approved_serial: str
    plan: KeymapPlan


@dataclass(frozen=True, slots=True)
class SetupResult:
    generation: object
    operation: str
    code: str
    preview: SetupPreview | None = None
    runtime_was_stopped: bool = False


def _default_adapter_factory(approved_serial: str):
    from .creator_micro_adapter import CreatorMicro2Adapter
    from .creator_micro_hidapi import HidApiTransport

    transport = HidApiTransport(approved_serial=approved_serial)
    devices = transport.enumerate()
    if len(devices) != 1:
        raise OSError("approved Creator Micro 2 is unavailable")
    transport.enable_writes()
    return CreatorMicro2Adapter(transport, devices[0], rpc_max_bytes=132_096)


def _backup_path(approved_serial: str, backup_root: Path | None) -> Path:
    from .creator_micro_setup import device_backup_key
    from .integration_settings import default_integration_settings_path

    root = Path(backup_root) if backup_root is not None else default_integration_settings_path().parent
    return root / f"creator-micro-keymap-{device_backup_key(approved_serial)}.json"


def _current(target: object, generation: object) -> bool:
    return (
        getattr(target, "_creator_micro_setup_generation", None) is generation
        and getattr(target, "_deck_runtime_generation", None) is generation
        and not getattr(target, "_runtime_termination_started", False)
        and not getattr(target, "_deck_runtime_stopping", False)
    )


def _same_setup(target: object, generation: object) -> bool:
    return (
        getattr(target, "_creator_micro_setup_generation", None) is generation
        and not getattr(target, "_runtime_termination_started", False)
        and not getattr(target, "_deck_runtime_stopping", False)
    )


def _set_pending(target: object, pending: bool) -> None:
    pane = getattr(target, "deck_settings_pane", None)
    if pane is not None:
        pane.set_setup_pending(pending)


def _dispatch(target: object, result: SetupResult) -> None:
    if not _same_setup(target, result.generation):
        return
    target.performSelectorOnMainThread_withObject_waitUntilDone_(
        "applyCreatorMicroSetupResult:", result, False,
    )


def _validated_serial(settings_loader: Callable[[], object], expected_serial: str | None = None) -> tuple[str | None, str | None]:
    loaded = settings_loader()
    settings = loaded.settings
    serial = getattr(settings, "creator_micro_device_serial", None)
    if getattr(settings, "creator_micro_enabled", False) is not True or not isinstance(serial, str) or not serial.strip():
        return None, "connection_required"
    if getattr(settings, "agent_deck_enabled", False) is True:
        return None, "agent_deck_ownership"
    if expected_serial is not None and serial != expected_serial:
        return None, "approved_device_changed"
    return serial, None


def _start_operation(
    target: object,
    operation: str,
    *,
    preview: SetupPreview | None = None,
    settings_loader: Callable[[], object] | None = None,
    adapter_factory: Callable[[str], object] | None = None,
    setup_factory: Callable[..., object] | None = None,
    backup_root: Path | None = None,
) -> threading.Thread | None:
    if getattr(target, "_creator_micro_setup_busy", False):
        return None
    from .creator_micro_setup import CreatorMicroSetup
    from .integration_settings import load_integration_settings

    settings_loader = settings_loader or load_integration_settings
    adapter_factory = adapter_factory or _default_adapter_factory
    setup_factory = setup_factory or CreatorMicroSetup
    restart_owed = (
        getattr(target, "_sidepulse_optional_integration_runtime", None) is None
        and getattr(target, "_deck_runtime_generation", None) is not None
    )
    generation = object()
    target._creator_micro_setup_generation = generation
    target._deck_runtime_generation = generation
    target._creator_micro_setup_busy = True
    _set_pending(target, True)

    lock = getattr(target, "_deck_runtime_restart_lock", None)
    if lock is None:
        lock = threading.Lock()
        target._deck_runtime_restart_lock = lock

    def run() -> None:
        stopped_runtime = restart_owed
        result = SetupResult(generation, operation, "setup_failed", runtime_was_stopped=stopped_runtime)
        try:
            with lock:
                if not _current(target, generation):
                    return
                serial, error = _validated_serial(
                    settings_loader,
                    preview.approved_serial if preview is not None else None,
                )
                if error is not None:
                    result = SetupResult(generation, operation, error, runtime_was_stopped=stopped_runtime)
                    return
                current_runtime = getattr(target, "_sidepulse_optional_integration_runtime", None)
                if current_runtime is not None:
                    current_runtime.revoke_deck_input()
                    current_runtime.close()
                    if not current_runtime.wait_until_stopped(17.0):
                        result = SetupResult(generation, operation, "previous_owner_stopping")
                        return
                    if getattr(target, "_sidepulse_optional_integration_runtime", None) is current_runtime:
                        target._sidepulse_optional_integration_runtime = None
                # Setup also owns the gap before a queued normal runtime has
                # started. Cancelling its preview must resume that runtime.
                stopped_runtime = True
                if not _current(target, generation):
                    return
                adapter = adapter_factory(serial)
                try:
                    if not _current(target, generation):
                        return
                    receipt = adapter.connect()
                    if receipt.code != "connected":
                        result = SetupResult(generation, operation, receipt.code, runtime_was_stopped=stopped_runtime)
                        return
                    if not _current(target, generation):
                        return

                    def consent_is_current() -> bool:
                        if not _current(target, generation):
                            return False
                        try:
                            current_serial, current_error = _validated_serial(settings_loader, serial)
                        except Exception:
                            return False
                        return current_error is None and current_serial == serial

                    setup = setup_factory(
                        adapter,
                        serial,
                        _backup_path(serial, backup_root),
                        is_current=consent_is_current,
                    )
                    if operation == "inspect":
                        if not _current(target, generation):
                            return
                        plan = setup.inspect()
                        result = SetupResult(
                            generation,
                            operation,
                            "inspection_ready",
                            SetupPreview(serial, plan),
                            stopped_runtime,
                        )
                    elif operation == "apply":
                        receipt = setup.apply(preview.plan)
                        result = SetupResult(generation, operation, receipt.code, runtime_was_stopped=stopped_runtime)
                    else:
                        receipt = setup.restore()
                        result = SetupResult(generation, operation, receipt.code, runtime_was_stopped=stopped_runtime)
                finally:
                    try:
                        adapter.close()
                    except OSError:
                        pass
        except Exception:
            result = SetupResult(generation, operation, "setup_failed", runtime_was_stopped=stopped_runtime)
        finally:
            if _same_setup(target, generation):
                _dispatch(target, result)

    thread = threading.Thread(target=run, name=f"JRBarCreatorMicroSetup-{operation}", daemon=True)
    thread.start()
    return thread


def begin_creator_micro_inspection(target: object, **dependencies) -> threading.Thread | None:
    return _start_operation(target, "inspect", **dependencies)


def begin_creator_micro_apply(target: object, preview: SetupPreview, **dependencies) -> threading.Thread | None:
    if type(preview) is not SetupPreview:
        return None
    return _start_operation(target, "apply", preview=preview, **dependencies)


def _confirm_restore() -> bool:
    alert = NSAlert.alloc().init()
    alert.setMessageText_("Restore the Creator Micro 2 keymap?")
    alert.setInformativeText_(
        "JR-Bar will restore the private backup only if the device still has the keymap JR-Bar applied."
    )
    alert.addButtonWithTitle_("Restore keymap")
    alert.addButtonWithTitle_("Cancel")
    return alert.runModal() == NSAlertFirstButtonReturn


def begin_creator_micro_restore(
    target: object,
    *,
    confirm: Callable[[], bool] = _confirm_restore,
    **dependencies,
) -> threading.Thread | None:
    if getattr(target, "_creator_micro_setup_busy", False):
        return None
    if not confirm():
        return None
    return _start_operation(target, "restore", **dependencies)


def _preview_text(plan: KeymapPlan) -> str:
    changed = "\n".join(plan.changes) if plan.changes else "No device keys need to change."
    return (
        f"Active profile {plan.profile_index + 1}, layer {plan.layer_index + 1}:\n\n"
        f"{changed}\n\n"
        "The listed keys will replace their normal keystrokes with JR-Bar device inputs. "
        "Dial and joystick mappings stay unchanged."
    )


def apply_creator_micro_setup_result(
    target: object,
    result: object,
    *,
    alert_factory: Callable[[], object] | None = None,
) -> None:
    if (
        getattr(result, "generation", None) is not getattr(target, "_creator_micro_setup_generation", None)
        or getattr(target, "_runtime_termination_started", False)
        or getattr(target, "_deck_runtime_stopping", False)
    ):
        return
    target._creator_micro_setup_busy = False
    _set_pending(target, False)
    if getattr(target, "_deck_runtime_generation", None) is not result.generation:
        return

    pane = getattr(target, "deck_settings_pane", None)
    code = result.code
    messages = {
        "keymap_verified": "Creator Micro 2 keymap verified.",
        "already_configured": "Creator Micro 2 keymap is already configured.",
        "keymap_restored": "Creator Micro 2 keymap restored and verified.",
        "already_restored": "Creator Micro 2 keymap is already restored.",
        "connection_required": "Connect and approve Creator Micro 2 before setup.",
        "agent_deck_ownership": "Turn off Agent Deck snapshot compatibility before setup.",
        "approved_device_changed": "The approved Creator Micro 2 changed. Inspect it again.",
        "previous_owner_stopping": "Creator Micro 2 is still stopping. Try again in a moment.",
        "keymap_changed": "The device keymap changed. Inspect it again before applying.",
        "backup_failed": "The private backup could not be verified. No keymap was written.",
        "backup_invalid": "No valid private backup is available. No keymap was written.",
        "readback_mismatch": "The device did not verify the keymap write. The backup was kept.",
        "cancelled": "Creator Micro 2 setup was cancelled.",
    }
    if code != "inspection_ready":
        if getattr(result, "runtime_was_stopped", False) or getattr(
            target, "_creator_micro_setup_runtime_needs_restart", False
        ):
            target._creator_micro_setup_runtime_needs_restart = False
            target.reconfigureDeckRuntime_(None)
        if pane is not None:
            pane.set_status(messages.get(code, "Creator Micro 2 setup failed."))
        return

    preview = result.preview
    if type(preview) is not SetupPreview:
        if getattr(result, "runtime_was_stopped", False):
            target.reconfigureDeckRuntime_(None)
        if pane is not None:
            pane.set_status("Creator Micro 2 inspection did not return a valid preview.")
        return
    alert = alert_factory() if alert_factory is not None else NSAlert.alloc().init()
    alert.setMessageText_("Review Creator Micro 2 key changes")
    alert.setInformativeText_(_preview_text(preview.plan))
    alert.addButtonWithTitle_("Apply keymap")
    alert.addButtonWithTitle_("Cancel")
    if alert.runModal() == NSAlertFirstButtonReturn:
        target._creator_micro_setup_runtime_needs_restart = bool(result.runtime_was_stopped)
        target.beginCreatorMicroSetupApply_(preview)
    else:
        if getattr(result, "runtime_was_stopped", False):
            target.reconfigureDeckRuntime_(None)
        if pane is not None:
            pane.set_status("No keymap was written.")


__all__ = [
    "SetupPreview",
    "SetupResult",
    "apply_creator_micro_setup_result",
    "begin_creator_micro_apply",
    "begin_creator_micro_inspection",
    "begin_creator_micro_restore",
]
