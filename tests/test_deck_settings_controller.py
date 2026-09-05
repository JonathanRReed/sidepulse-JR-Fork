from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import sidepulse.deck_settings_controller as controller_module
from sidepulse.deck_actions import DeckAction
from sidepulse.deck_control_settings import DeckControlSettings
from sidepulse.deck_settings_controller import (
    DeckSettingsApplyResult,
    apply_deck_settings_result,
    save_deck_mapping,
    toggle_deck_controls,
)


@dataclass
class _Pane:
    candidate: DeckAction | None
    key: int = 4
    refreshed: DeckControlSettings | None = None
    status: str = ""
    pending: bool = False

    def selected_mapping(self):
        return self.key, self.candidate

    def refresh(self, settings):
        self.refreshed = settings

    def set_status(self, value):
        self.status = value

    def set_save_pending(self, value):
        self.pending = value


class _Controller:
    def __init__(self, settings, pane):
        self._deck_control_settings = settings
        self.deck_settings_pane = pane
        self.results = []
        self.reconfigured = []
        self._runtime_termination_started = False
        self.deck_runtime = SimpleNamespace(revoke_deck_input=lambda: None)

    def performSelectorOnMainThread_withObject_waitUntilDone_(self, selector, payload, wait):
        self.results.append((selector, payload, wait))

    def reconfigureDeckRuntime_(self, settings):
        self.reconfigured.append(settings)


def _run_now(target):
    target()
    return SimpleNamespace()


def test_save_mapping_persists_candidate_against_cached_settings_off_appkit(monkeypatch) -> None:
    previous = DeckControlSettings(enabled=True)
    pane = _Pane(DeckAction("open_usage"), key=7)
    controller = _Controller(previous, pane)
    saved = []
    monkeypatch.setattr(controller_module, "_start_background", _run_now)
    monkeypatch.setattr(
        controller_module,
        "save_deck_controls",
        lambda settings, *, expected: saved.append((settings, expected)),
    )

    save_deck_mapping(controller, None)

    candidate = previous.with_binding(7, DeckAction("open_usage"))
    assert saved == [(candidate, previous)]
    result = controller.results[0][1]
    assert controller.results[0][0::2] == ("applyDeckSettingsResult:", False)
    assert result.previous == previous
    assert result.candidate == candidate
    assert result.generation == 1


def test_toggle_persists_only_the_enabled_change(monkeypatch) -> None:
    action = DeckAction("reveal_current_ask")
    previous = DeckControlSettings(bindings=((2, action),))
    pane = _Pane(None)
    controller = _Controller(previous, pane)
    sender = SimpleNamespace(state=lambda: 1)
    saved = []
    monkeypatch.setattr(controller_module, "_start_background", _run_now)
    monkeypatch.setattr(
        controller_module,
        "save_deck_controls",
        lambda settings, *, expected: saved.append((settings, expected)),
    )

    toggle_deck_controls(controller, sender)

    assert saved == [(DeckControlSettings(True, ((2, action),)), previous)]


def test_apply_success_adopts_settings_refreshes_card_and_reconfigures_runtime() -> None:
    previous = DeckControlSettings()
    candidate = DeckControlSettings(True, ((0, DeckAction("open_agent_browser")),))
    pane = _Pane(None)
    controller = _Controller(previous, pane)

    controller._deck_settings_save_generation = 1
    controller._deck_settings_save_in_flight = True
    apply_deck_settings_result(
        controller, DeckSettingsApplyResult(1, previous, candidate)
    )

    assert controller._deck_control_settings == candidate
    assert pane.refreshed == candidate
    assert pane.status == "Device actions saved."
    assert controller.reconfigured == [candidate]


def test_apply_failure_preserves_cached_settings_and_reports_bounded_error() -> None:
    previous = DeckControlSettings(True)
    pane = _Pane(None)
    controller = _Controller(previous, pane)
    controller._deck_settings_save_generation = 1
    controller._deck_settings_save_in_flight = True

    apply_deck_settings_result(
        controller,
        DeckSettingsApplyResult(
            1,
            previous,
            DeckControlSettings(False),
            error="Deck settings changed. Reload before saving. Device actions remain paused.",
            paused_on_failure=True,
        ),
    )

    assert controller._deck_control_settings == previous
    assert pane.refreshed == previous
    assert pane.status == "Deck settings changed. Reload before saving. Device actions remain paused."
    assert controller.reconfigured == []


def test_duplicate_submit_is_ignored_while_save_is_in_flight(monkeypatch) -> None:
    previous = DeckControlSettings()
    pane = _Pane(DeckAction("open_usage"))
    controller = _Controller(previous, pane)
    queued = []
    monkeypatch.setattr(controller_module, "_start_background", queued.append)

    save_deck_mapping(controller, None)
    save_deck_mapping(controller, None)

    assert len(queued) == 1
    assert pane.pending is True


def test_stale_result_does_not_replace_newer_save_or_refresh_rebuilt_pane() -> None:
    previous = DeckControlSettings()
    current = DeckControlSettings(True)
    pane = _Pane(None)
    controller = _Controller(current, pane)
    controller._deck_settings_save_generation = 2
    controller._deck_settings_save_in_flight = True

    apply_deck_settings_result(
        controller,
        DeckSettingsApplyResult(1, previous, DeckControlSettings(False)),
    )

    assert controller._deck_control_settings == current
    assert pane.refreshed is None
    assert controller._deck_settings_save_in_flight is True


def test_success_without_open_pane_updates_cache_but_termination_skips_restart() -> None:
    previous = DeckControlSettings()
    candidate = DeckControlSettings(True)
    controller = _Controller(previous, None)
    controller._deck_settings_save_generation = 1
    controller._deck_settings_save_in_flight = True
    controller._runtime_termination_started = True

    apply_deck_settings_result(
        controller, DeckSettingsApplyResult(1, previous, candidate)
    )

    assert controller._deck_control_settings == candidate
    assert controller.reconfigured == []


def test_disabling_revokes_input_before_background_save(monkeypatch) -> None:
    previous = DeckControlSettings(True)
    pane = _Pane(None)
    controller = _Controller(previous, pane)
    events = []
    controller.deck_runtime = SimpleNamespace(
        revoke_deck_input=lambda: events.append("revoked")
    )
    monkeypatch.setattr(
        controller_module, "_start_background", lambda worker: events.append("queued")
    )

    toggle_deck_controls(controller, SimpleNamespace(state=lambda: 0))

    assert events == ["revoked", "queued"]
