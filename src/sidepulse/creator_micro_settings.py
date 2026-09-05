"""Serialize explicit device-connection choices and fence superseded saves."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CreatorMicroSettingsResult:
    generation: object
    enabled: bool
    saved: bool
    reason: str


def save_creator_micro_choice_async(target, enabled: bool) -> None:
    if type(enabled) is not bool:
        raise TypeError("enabled must be bool")
    generation = object()
    target._creator_micro_settings_generation = generation
    lock = getattr(target, "_creator_micro_settings_lock", None)
    if lock is None:
        lock = threading.Lock()
        target._creator_micro_settings_lock = lock

    def save() -> None:
        from .creator_micro_hidapi import DeviceIdentityError, HidApiTransport, select_unique_stable_serial
        from .integration_settings import load_integration_settings, save_integration_settings

        with lock:
            if target._creator_micro_settings_generation is not generation:
                return
            previous_enabled = False
            try:
                loaded = load_integration_settings()
                settings = loaded.settings
                previous_enabled = settings.creator_micro_enabled
                serial = settings.creator_micro_device_serial
                if enabled and serial is None:
                    serial = select_unique_stable_serial(HidApiTransport().enumerate())
                candidate = settings.with_creator_micro(enabled=enabled, device_serial=serial)
                if target._creator_micro_settings_generation is not generation:
                    return
                save_integration_settings(candidate, loaded=loaded)
                result = CreatorMicroSettingsResult(generation, enabled, True, "saved")
            except DeviceIdentityError as error:
                result = CreatorMicroSettingsResult(generation, previous_enabled, False, error.code)
            except Exception:
                result = CreatorMicroSettingsResult(generation, previous_enabled, False, "settings_save_failed")
            if target._creator_micro_settings_generation is generation:
                target.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "applyCreatorMicroSettings:", result, False,
                )

    threading.Thread(target=save, name="JRBarCreatorMicroSettings", daemon=True).start()


def apply_creator_micro_settings(target, result: object) -> None:
    if (
        type(result) is not CreatorMicroSettingsResult
        or result.generation is not getattr(target, "_creator_micro_settings_generation", None)
        or getattr(target, "_runtime_termination_started", False)
        or getattr(target, "_deck_runtime_stopping", False)
    ):
        return
    target._creator_micro_output_enabled = result.enabled
    toggle = getattr(target, "_creator_micro_output_toggle", None)
    if toggle is not None:
        toggle.setState_(1 if result.enabled else 0)
        toggle.setEnabled_(True)
    if result.saved:
        target.reconfigureDeckRuntime_(None)
    else:
        from .optional_integration_runtime import CreatorMicroOutputReceipt

        target.applyCreatorMicroOutputReceipt_(CreatorMicroOutputReceipt(False, result.reason))
