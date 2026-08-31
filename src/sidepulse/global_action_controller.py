"""Narrow controller adapters for the Reveal Current Ask global action."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

import objc

from .announcer_stack import (
    AnnouncerStackAction,
    AnnouncerStackIntent,
    AnnouncerStackVisibility,
    project_announcer_stack,
    reconcile_announcer_stack,
)
from .global_actions import (
    GlobalActionID,
    PersistedShortcutRefusal,
    ShortcutChord,
    format_shortcut,
    parse_global_action_shortcuts,
    serialize_global_action_shortcuts,
)
from .global_hotkeys import HotkeyCleanupError, HotkeyRegistrationRefusal
from .models import AgentStatus
from .operator_state import CanonicalOperatorState
from .settings import SettingsConcurrentWriteError, SettingsWriteRefusedError


class _Registry(Protocol):
    active_bindings: dict[GlobalActionID, ShortcutChord]
    closed: bool

    def prepare(self, bindings): ...

    def commit(self, preparation) -> None: ...

    def rollback(self, preparation) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class GlobalActionChangeResult:
    applied: bool
    refusal: HotkeyRegistrationRefusal | None = None
    save_failure: str | None = None
    persisted_refusals: tuple[PersistedShortcutRefusal, ...] = ()


class GlobalActionLifecycleCoordinator:
    """Own launch, durable binding transactions, and callback teardown."""

    def __init__(
        self,
        *,
        registry_factory: Callable[[Callable[[GlobalActionID], None]], _Registry],
        settings_getter: Callable[[], object],
        settings_setter: Callable[[object], None],
        settings_saver: Callable[[object], None],
        action_handler: Callable[[GlobalActionID], None],
    ) -> None:
        dependencies = (
            registry_factory,
            settings_getter,
            settings_setter,
            settings_saver,
            action_handler,
        )
        if not all(callable(dependency) for dependency in dependencies):
            raise ValueError("invalid global action lifecycle dependency")
        self._registry_factory = registry_factory
        self._settings_getter = settings_getter
        self._settings_setter = settings_setter
        self._settings_saver = settings_saver
        self._action_handler = action_handler
        self._registry: _Registry | None = None
        self._requested_bindings: dict[GlobalActionID, ShortcutChord] | None = None
        self._recovery_preparation: object | None = None
        self._persisted_refusals: tuple[PersistedShortcutRefusal, ...] = ()
        self._closed = False

    @property
    def registry(self) -> _Registry | None:
        return self._registry

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def persisted_refusals(self) -> tuple[PersistedShortcutRefusal, ...]:
        return self._persisted_refusals

    def _receive_action(self, action: GlobalActionID) -> None:
        if not self._closed and type(action) is GlobalActionID:
            self._action_handler(action)

    @staticmethod
    def _bindings(settings: object) -> dict[GlobalActionID, ShortcutChord]:
        parsed = parse_global_action_shortcuts(
            getattr(settings, "global_action_shortcuts", {})
        )
        return dict(parsed.bindings)

    def launch(self) -> GlobalActionChangeResult:
        if self._closed:
            return GlobalActionChangeResult(False, save_failure="closed")
        if self._registry is None:
            self._registry = self._registry_factory(self._receive_action)
        return self.refresh_from_settings()

    def refresh_from_settings(self) -> GlobalActionChangeResult:
        registry = self._registry
        if registry is None or self._closed:
            return GlobalActionChangeResult(False, save_failure="closed")
        recovery = self._recover_registry(registry)
        if recovery is not None:
            return recovery
        parsed = parse_global_action_shortcuts(
            getattr(self._settings_getter(), "global_action_shortcuts", {})
        )
        bindings = dict(parsed.bindings)
        self._persisted_refusals = parsed.refusals
        if (
            self._requested_bindings == bindings
            and registry.active_bindings == bindings
        ):
            return GlobalActionChangeResult(
                True,
                persisted_refusals=self._persisted_refusals,
            )
        result = registry.prepare(bindings)
        if result.preparation is None:
            return GlobalActionChangeResult(
                False,
                refusal=result.refusal,
                persisted_refusals=self._persisted_refusals,
            )
        try:
            registry.commit(result.preparation)
        except HotkeyCleanupError:
            return self._rollback_result(
                registry,
                result.preparation,
                "commit_cleanup",
            )
        applied = registry.active_bindings == bindings
        if applied:
            self._requested_bindings = dict(bindings)
        return GlobalActionChangeResult(
            applied,
            save_failure=None if applied else "registration_mismatch",
            persisted_refusals=self._persisted_refusals,
        )

    def set_shortcut(
        self,
        action: GlobalActionID,
        chord: ShortcutChord,
    ) -> GlobalActionChangeResult:
        if type(action) is not GlobalActionID or type(chord) is not ShortcutChord:
            raise ValueError("invalid global action shortcut edit")
        settings = self._settings_getter()
        parsed = parse_global_action_shortcuts(
            getattr(settings, "global_action_shortcuts", {})
        )
        self._persisted_refusals = parsed.refusals
        current = dict(parsed.bindings)
        current[action] = chord
        return self._persist_bindings(current)

    def clear_shortcut(self, action: GlobalActionID) -> GlobalActionChangeResult:
        if type(action) is not GlobalActionID:
            raise ValueError("invalid global action shortcut clear")
        settings = self._settings_getter()
        parsed = parse_global_action_shortcuts(
            getattr(settings, "global_action_shortcuts", {})
        )
        self._persisted_refusals = parsed.refusals
        current = dict(parsed.bindings)
        current.pop(action, None)
        return self._persist_bindings(current)

    def _persist_bindings(
        self,
        bindings: dict[GlobalActionID, ShortcutChord],
    ) -> GlobalActionChangeResult:
        registry = self._registry
        if registry is None or self._closed:
            return GlobalActionChangeResult(False, save_failure="closed")
        recovery = self._recover_registry(registry)
        if recovery is not None:
            return recovery
        settings = self._settings_getter()
        if self._bindings(settings) == bindings and registry.active_bindings == bindings:
            return GlobalActionChangeResult(
                True,
                persisted_refusals=self._persisted_refusals,
            )
        prepared = registry.prepare(bindings)
        if prepared.preparation is None:
            return GlobalActionChangeResult(False, refusal=prepared.refusal)
        raw = getattr(settings, "global_action_shortcuts", {})
        encoded = dict(raw) if type(raw) is dict else {}
        for action in GlobalActionID:
            encoded.pop(action.value, None)
        encoded.update(serialize_global_action_shortcuts(bindings))
        candidate = replace(settings, global_action_shortcuts=encoded)
        try:
            self._settings_saver(candidate)
        except SettingsConcurrentWriteError:
            return self._rollback_result(
                registry,
                prepared.preparation,
                "concurrent_write",
            )
        except SettingsWriteRefusedError:
            return self._rollback_result(
                registry,
                prepared.preparation,
                "write_refused",
            )
        try:
            registry.commit(prepared.preparation)
        except HotkeyCleanupError:
            restore_failure = None
            try:
                self._settings_saver(settings)
            except SettingsConcurrentWriteError:
                restore_failure = "restore_concurrent_write"
            except SettingsWriteRefusedError:
                restore_failure = "restore_write_refused"
            failure = "commit_cleanup"
            if restore_failure is not None:
                failure = f"{failure}_{restore_failure}"
            return self._rollback_result(
                registry,
                prepared.preparation,
                failure,
            )
        self._settings_setter(candidate)
        self._requested_bindings = dict(bindings)
        self._persisted_refusals = parse_global_action_shortcuts(
            encoded
        ).refusals
        return GlobalActionChangeResult(
            True,
            persisted_refusals=self._persisted_refusals,
        )

    def _recover_registry(
        self,
        registry: _Registry,
    ) -> GlobalActionChangeResult | None:
        preparation = self._recovery_preparation
        if preparation is None:
            return None
        try:
            registry.rollback(preparation)
        except HotkeyCleanupError:
            return GlobalActionChangeResult(
                False,
                save_failure="rollback_cleanup",
                persisted_refusals=self._persisted_refusals,
            )
        self._recovery_preparation = None
        return None

    def _rollback_result(
        self,
        registry: _Registry,
        preparation: object,
        failure: str,
    ) -> GlobalActionChangeResult:
        try:
            registry.rollback(preparation)
        except HotkeyCleanupError:
            self._recovery_preparation = preparation
            failure = f"{failure}_rollback_cleanup"
        return GlobalActionChangeResult(
            False,
            save_failure=failure,
            persisted_refusals=self._persisted_refusals,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        registry = self._registry
        if registry is not None:
            registry.close()


@objc.IBAction
def performRevealCurrentAsk_(controller, _sender) -> bool:
    """Toggle the current announcer, or reveal the existing Agent Browser."""
    lifecycle = getattr(controller, "global_action_lifecycle", None)
    if getattr(controller, "_runtime_termination_started", False) or bool(
        getattr(lifecycle, "closed", False)
    ):
        return False
    snapshot = getattr(controller, "last_snapshot", None)
    projection = getattr(controller, "current_attention_projection", None)
    operator_state = (
        getattr(snapshot, "operator_state", None) if snapshot is not None else None
    )
    if type(operator_state) is not CanonicalOperatorState:
        operator_state = None
    actionable_rows = tuple(
        getattr(projection, "actionable_attention", ()) if projection is not None else ()
    )
    current_statuses = tuple(
        status
        for status in (
            getattr(snapshot, "statuses", ()) if snapshot is not None else ()
        )
        if type(status) is AgentStatus
    )
    state = reconcile_announcer_stack(
        controller._announcer_stack_state,
        operator_state,
        actionable_rows,
        current_statuses,
    )
    plan = project_announcer_stack(
        state,
        operator_state,
        actionable_rows,
        current_statuses,
    )
    selected = state.selected_identity
    if (
        selected is None
        or plan.total_actionable_count == 0
        or not controller.virtual_status_device.can_present_announcer()
    ):
        return bool(controller.openAgentBrowser_(None))

    controller._announcer_stack_state = state
    presentation = controller.answer_controller.present(
        state,
        plan,
        operator_state,
        actionable_rows,
        current_statuses,
    )
    controller._announcer_status_routes = controller.answer_controller.routes
    controller._announcer_requests_by_identity = (
        controller.answer_controller.requests_by_identity
    )
    controller._announcer_stack_context = controller.answer_controller.context
    action = (
        AnnouncerStackAction.COLLAPSE
        if plan.visibility is AnnouncerStackVisibility.EXPANDED
        else AnnouncerStackAction.EXPAND
    )
    controller._handle_announcer_stack_intent(
        AnnouncerStackIntent(action, presentation.plan.generation, selected)
    )
    return True


def reveal_current_ask_menu_title(settings: object) -> str:
    parsed = parse_global_action_shortcuts(
        getattr(settings, "global_action_shortcuts", {})
    )
    chord = parsed.binding_for(GlobalActionID.REVEAL_CURRENT_ASK)
    return (
        "Reveal Current Ask"
        if chord is None
        else f"Reveal Current Ask  {format_shortcut(chord)}"
    )


__all__ = [
    "GlobalActionChangeResult",
    "GlobalActionLifecycleCoordinator",
    "performRevealCurrentAsk_",
    "reveal_current_ask_menu_title",
]
