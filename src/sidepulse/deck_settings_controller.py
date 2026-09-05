"""Controller adapters for asynchronously saving Agent Deck mappings."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace

from AppKit import NSModalResponseOK, NSOpenPanel
from Foundation import NSBundle

from .deck_control_settings import DeckControlSettings, save_deck_controls
from .deck_settings_pane import _app_label


@dataclass(frozen=True, slots=True)
class DeckSettingsApplyResult:
    generation: int
    previous: DeckControlSettings
    candidate: DeckControlSettings
    error: str | None = None
    paused_on_failure: bool = False

    def __post_init__(self) -> None:
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("invalid deck settings result")
        if type(self.previous) is not DeckControlSettings or type(self.candidate) is not DeckControlSettings:
            raise ValueError("invalid deck settings result")
        if self.error is not None and (type(self.error) is not str or not self.error):
            raise ValueError("invalid deck settings error")
        if type(self.paused_on_failure) is not bool or (self.paused_on_failure and self.error is None):
            raise ValueError("invalid deck settings pause state")


def _start_background(target):
    thread = threading.Thread(target=target, name="sidepulse-deck-settings-save", daemon=True)
    thread.start()
    return thread


def _error_message(error: Exception, *, paused: bool) -> str:
    if isinstance(error, ValueError) and "changed" in str(error).lower():
        message = "Deck settings changed. Reload before saving."
    else:
        message = "Could not save device actions. The previous settings are unchanged."
    if paused:
        message += " Device actions remain paused."
    return message


def _submit_save(controller: object, candidate: DeckControlSettings, previous: DeckControlSettings) -> None:
    if getattr(controller, "_deck_settings_save_in_flight", False):
        return
    generation = int(getattr(controller, "_deck_settings_save_generation", 0)) + 1
    controller._deck_settings_save_generation = generation
    controller._deck_settings_save_in_flight = True
    pane = getattr(controller, "deck_settings_pane", None)
    if pane is None:
        controller._deck_settings_save_in_flight = False
        return
    pane.set_save_pending(True)
    pane.set_status("Saving device actions…")
    paused = previous.enabled and not candidate.enabled

    def worker() -> None:
        try:
            save_deck_controls(candidate, expected=previous)
            result = DeckSettingsApplyResult(generation, previous, candidate)
        except Exception as error:
            result = DeckSettingsApplyResult(
                generation,
                previous,
                candidate,
                error=_error_message(error, paused=paused),
                paused_on_failure=paused,
            )
        controller.performSelectorOnMainThread_withObject_waitUntilDone_("applyDeckSettingsResult:", result, False)

    _start_background(worker)


def toggle_deck_controls(controller: object, sender: object) -> None:
    previous = getattr(controller, "_deck_control_settings", None)
    if type(previous) is not DeckControlSettings:
        controller.deck_settings_pane.set_status("Device action settings are unavailable.")
        return
    candidate = replace(previous, enabled=bool(sender.state()))
    if previous.enabled and not candidate.enabled:
        runtime = getattr(controller, "_sidepulse_optional_integration_runtime", None)
        if runtime is None:
            runtime = getattr(controller, "deck_runtime", None)
        revoke = getattr(runtime, "revoke_deck_input", None)
        if callable(revoke):
            revoke()
    _submit_save(controller, candidate, previous)


def save_deck_mapping(controller: object, _sender: object) -> None:
    previous = getattr(controller, "_deck_control_settings", None)
    if type(previous) is not DeckControlSettings:
        controller.deck_settings_pane.set_status("Device action settings are unavailable.")
        return
    try:
        key, action = controller.deck_settings_pane.selected_mapping()
        candidate = previous.with_binding(key, action)
    except (TypeError, ValueError) as error:
        controller.deck_settings_pane.set_status(str(error))
        return
    _submit_save(controller, candidate, previous)


def choose_deck_app(controller: object, _sender: object) -> None:
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(True)
    panel.setCanChooseDirectories_(False)
    panel.setAllowsMultipleSelection_(False)
    panel.setAllowedFileTypes_(["app"])
    panel.setPrompt_("Choose Application")
    if panel.runModal() != NSModalResponseOK:
        return
    urls = panel.URLs()
    if not urls:
        return
    bundle = NSBundle.bundleWithURL_(urls[0])
    bundle_id = str(bundle.bundleIdentifier() or "") if bundle is not None else ""
    if not bundle_id:
        controller.deck_settings_pane.set_status("That application has no bundle identifier.")
        return
    pane = controller.deck_settings_pane
    pane.selected_bundle_id = bundle_id
    pane.selected_app_name = str(urls[0].lastPathComponent() or bundle_id)
    pane.application_button.setTitle_(_app_label(bundle_id, pane.selected_app_name))
    pane.application_button.setToolTip_(bundle_id)
    pane.application_button.setAccessibilityHelp_(bundle_id)
    pane.set_status("Application selected. Choose Save mapping to apply it.")


def apply_deck_settings_result(controller: object, payload: DeckSettingsApplyResult) -> None:
    if type(payload) is not DeckSettingsApplyResult:
        raise ValueError("invalid deck settings apply result")
    if payload.generation != getattr(controller, "_deck_settings_save_generation", None):
        return
    controller._deck_settings_save_in_flight = False
    pane = getattr(controller, "deck_settings_pane", None)
    if payload.error is not None:
        if pane is not None:
            pane.refresh(payload.previous)
            pane.set_save_pending(False)
            pane.set_status(payload.error)
        return
    controller._deck_control_settings = payload.candidate
    if pane is not None:
        pane.refresh(payload.candidate)
        pane.set_save_pending(False)
        pane.set_status("Device actions saved.")
    if not getattr(controller, "_runtime_termination_started", False):
        controller.reconfigureDeckRuntime_(payload.candidate)


__all__ = [
    "DeckSettingsApplyResult",
    "apply_deck_settings_result",
    "choose_deck_app",
    "save_deck_mapping",
    "toggle_deck_controls",
]
