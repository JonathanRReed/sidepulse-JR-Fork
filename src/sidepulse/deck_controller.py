"""AppKit coordinator for explicit device actions, using existing JR-Bar routes."""

from __future__ import annotations

import threading

from .deck_actions_macos import MacDeckActionExecutor
from .deck_input_dispatch import DeckInputBatch


def apply_deck_input(target, batch: object) -> None:
    if type(batch) is not DeckInputBatch or getattr(target, "_runtime_termination_started", False):
        return
    executor = MacDeckActionExecutor(
        reveal_current_ask=lambda: target.performRevealCurrentAsk_(None),
        open_agent_browser=lambda: target.openAgentBrowser_(None),
        open_usage=lambda: target.openProviderUsageCenter_(None),
    )
    receipts = batch.owner.deliver(batch, executor)
    if not receipts:
        return
    target._deck_action_receipt = receipts[-1]
    if getattr(target, "current_settings_pane", None) == "devices":
        code = receipts[-1].code
        message = {
            "accessibility_not_trusted": "Allow JR-Bar in macOS Accessibility settings to use app shortcuts.",
            "target_not_frontmost": "Switch to the mapped app before using its shortcut.",
            "target_not_running": "Open the mapped app before using its shortcut.",
            "app_not_found": "The mapped app is not installed. Choose it again in Devices settings.",
        }.get(code, f"Device action: {code.replace('_', ' ')}.")
        target.set_settings_message(message)


def reconfigure_deck_runtime(target, *, runtime_factory=None) -> threading.Thread:
    """Revoke input now; replace a stopped HID owner from a background worker."""
    from .optional_integration_runtime import CreatorMicroOutputReceipt, start_optional_integration_runtime

    factory = runtime_factory or start_optional_integration_runtime
    generation = object()
    target._deck_runtime_generation = generation
    lifecycle = _lifecycle_lock(target)
    lock = getattr(target, "_deck_runtime_restart_lock", None)
    if lock is None:
        lock = threading.Lock()
        target._deck_runtime_restart_lock = lock
    old = getattr(target, "_sidepulse_optional_integration_runtime", None)
    if old is not None:
        old.revoke_deck_input()

    def restart() -> None:
        with lock:
            if target._deck_runtime_generation is not generation:
                return
            current = getattr(target, "_sidepulse_optional_integration_runtime", None)
            if current is not None:
                current.close()
                if not current.wait_until_stopped(17.0):
                    target.performSelectorOnMainThread_withObject_waitUntilDone_(
                        "applyCreatorMicroOutputReceipt:",
                        CreatorMicroOutputReceipt(False, "previous_owner_stopping"), False,
                    )
                    return
            with lifecycle:
                if (
                    target._deck_runtime_generation is not generation
                    or getattr(target, "_runtime_termination_started", False)
                    or getattr(target, "_deck_runtime_stopping", False)
                ):
                    return
                target._sidepulse_optional_integration_runtime = factory(target)

    thread = threading.Thread(target=restart, name="JRBarDeckReconfigure", daemon=True)
    thread.start()
    return thread


def _lifecycle_lock(target):
    lock = getattr(target, "_deck_runtime_lifecycle_lock", None)
    if lock is None:
        lock = threading.RLock()
        target._deck_runtime_lifecycle_lock = lock
    return lock


def stop_deck_runtime_reconfiguration(target) -> None:
    with _lifecycle_lock(target):
        target._deck_runtime_stopping = True
        target._deck_runtime_generation = object()
        runtime = getattr(target, "_sidepulse_optional_integration_runtime", None)
    if runtime is not None:
        runtime.revoke_deck_input()
