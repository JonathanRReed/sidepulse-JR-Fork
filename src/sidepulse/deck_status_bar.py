"""Thin native selector host for Agent Deck controls."""

from __future__ import annotations

import objc


def install_deck_status_bar(base):
    class JRDeckStatusBarController(base):
        def applyCreatorMicroOutputReceipt_(self, receipt) -> None:
            self._creator_micro_output_receipt = receipt
            status_label = getattr(self, "_creator_micro_output_status_label", None)
            if status_label is not None:
                status_label.setStringValue_(
                    f"Status: {receipt.reason.replace('_', ' ')}"
                )
            if getattr(self, "current_settings_pane", None) == "devices":
                label = {
                    "ready": "Creator Micro 2 ready.",
                    "unsupported_firmware": "Creator Micro 2 firmware does not expose agent-status output.",
                    "device_conflict": "Creator Micro 2 stopped after detecting conflicting device traffic.",
                    "agent_deck_ownership": "Creator Micro 2 stays with Agent Deck while snapshot compatibility is enabled.",
                }.get(receipt.reason, f"Creator Micro 2: {receipt.reason.replace('_', ' ')}.")
                self.set_settings_message(label)

        @objc.IBAction
        def toggleCreatorMicroOutput_(self, sender) -> None:
            from .optional_integration_runtime import set_creator_micro_output_enabled_async

            enabled = bool(sender.state())
            self._creator_micro_output_toggle = sender
            sender.setEnabled_(False)
            self._creator_micro_output_enabled = enabled
            if not enabled:
                runtime = getattr(self, "_sidepulse_optional_integration_runtime", None)
                if runtime is not None:
                    runtime.revoke_deck_input()
            set_creator_micro_output_enabled_async(self, enabled)

        def applyCreatorMicroSettings_(self, payload) -> None:
            from .creator_micro_settings import apply_creator_micro_settings

            apply_creator_micro_settings(self, payload)

        def applyDeckInput_(self, payload) -> None:
            from .deck_controller import apply_deck_input

            apply_deck_input(self, payload)

        @objc.IBAction
        def toggleDeckControls_(self, sender) -> None:
            from .deck_settings_controller import toggle_deck_controls

            toggle_deck_controls(self, sender)

        @objc.IBAction
        def saveDeckMapping_(self, sender) -> None:
            from .deck_settings_controller import save_deck_mapping

            save_deck_mapping(self, sender)

        @objc.IBAction
        def chooseDeckApp_(self, sender) -> None:
            from .deck_settings_controller import choose_deck_app

            choose_deck_app(self, sender)

        @objc.IBAction
        def deckMappingSelectionChanged_(self, sender) -> None:
            from .deck_settings_pane import deck_mapping_selection_changed

            deck_mapping_selection_changed(self, sender)

        def applyDeckSettingsResult_(self, payload) -> None:
            from .deck_settings_controller import apply_deck_settings_result

            apply_deck_settings_result(self, payload)

        def reconfigureDeckRuntime_(self, _sender) -> None:
            from .deck_controller import reconfigure_deck_runtime

            reconfigure_deck_runtime(self)

        def applyDeckControlsLoaded_(self, payload) -> None:
            from .deck_control_settings import DeckControlSettings

            if (
                getattr(self, "_runtime_termination_started", False)
                or getattr(self, "_deck_settings_save_in_flight", False)
            ):
                return
            pane = getattr(self, "deck_settings_pane", None)
            if (
                pane is not None and type(payload) is DeckControlSettings
                and payload is getattr(self, "_deck_control_settings", None)
            ):
                pane.refresh(payload)
                pane.enable_checkbox.setEnabled_(True)
                pane.save_button.setEnabled_(True)

        @objc.IBAction
        def inspectCreatorMicroSetup_(self, _sender) -> None:
            from .creator_micro_setup_controller import begin_creator_micro_inspection

            begin_creator_micro_inspection(self)

        @objc.IBAction
        def restoreCreatorMicroKeymap_(self, _sender) -> None:
            from .creator_micro_setup_controller import begin_creator_micro_restore

            begin_creator_micro_restore(self)

        def beginCreatorMicroSetupApply_(self, preview) -> None:
            from .creator_micro_setup_controller import begin_creator_micro_apply

            begin_creator_micro_apply(self, preview)

        def applyCreatorMicroSetupResult_(self, result) -> None:
            from .creator_micro_setup_controller import apply_creator_micro_setup_result

            apply_creator_micro_setup_result(self, result)

    return JRDeckStatusBarController
