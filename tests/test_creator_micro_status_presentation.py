from __future__ import annotations

from types import SimpleNamespace

from sidepulse.deck_status_bar import install_deck_status_bar
from sidepulse.settings_window import _build_devices_pane


class _BaseController:
    def set_settings_message(self, message) -> None:
        self.messages.append(message)


Controller = install_deck_status_bar(_BaseController)


def _controller():
    controller = Controller()
    controller._runtime_started = False
    controller._device_discovery_cache = None
    controller.messages = []
    return controller


def test_creator_micro_receipt_updates_cached_hidden_devices_status() -> None:
    controller = _controller()
    controller.current_settings_pane = "profile"
    controller._creator_micro_output_receipt = SimpleNamespace(reason="timeout")
    _build_devices_pane(controller)
    status = controller._creator_micro_output_status_label
    assert status.stringValue() == "Status: timeout"

    controller.applyCreatorMicroOutputReceipt_(SimpleNamespace(reason="ready"))

    assert status.stringValue() == "Status: ready"
    assert controller.messages == []


def test_receipt_before_devices_pane_builds_latest_status_and_ownership_help() -> None:
    controller = _controller()
    controller.applyCreatorMicroOutputReceipt_(SimpleNamespace(reason="device_conflict"))

    pane, _controls = _build_devices_pane(controller)

    assert controller._creator_micro_output_status_label.stringValue() == (
        "Status: device conflict"
    )
    text = "\n".join(
        str(view.stringValue())
        for view in _all_views(pane)
        if hasattr(view, "stringValue")
    )
    assert "Close Work Louder Input" in text
    assert "remove the device connection in Codex Micro" in text
    assert "before enabling JR-Bar" in text


def _all_views(root):
    yield root
    for child in root.subviews():
        yield from _all_views(child)
