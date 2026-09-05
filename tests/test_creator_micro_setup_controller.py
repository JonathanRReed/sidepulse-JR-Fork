from __future__ import annotations

from types import SimpleNamespace

from sidepulse.creator_micro_adapter import Receipt
from sidepulse.creator_micro_keymap import KeymapPlan
from sidepulse.creator_micro_setup_controller import (
    SetupPreview,
    apply_creator_micro_setup_result,
    begin_creator_micro_apply,
    begin_creator_micro_inspection,
    begin_creator_micro_restore,
)


def _plan() -> KeymapPlan:
    return KeymapPlan("{}", "{}", "a", "b", ("Key 0: KC_A -> KV_OAI_AG00; replaces its normal keystroke with a JR-Bar device input.",), 0, 1)


class _Runtime:
    def __init__(self, calls, stopped=True):
        self.calls = calls
        self.stopped = stopped

    def revoke_deck_input(self):
        self.calls.append("revoke")

    def close(self):
        self.calls.append("close-runtime")

    def wait_until_stopped(self, timeout):
        self.calls.append("wait-runtime")
        return self.stopped


class _Adapter:
    def __init__(self, calls):
        self.calls = calls

    def connect(self):
        self.calls.append("connect-setup")
        return Receipt("connected")

    def close(self):
        self.calls.append("close-setup")


class _Setup:
    def __init__(self, calls, **_kwargs):
        self.calls = calls

    def inspect(self):
        self.calls.append("inspect")
        return _plan()

    def restore(self):
        self.calls.append("restore")
        return Receipt("keymap_restored")


def _loaded(serial="approved", *, agent_deck=False, enabled=True):
    return SimpleNamespace(settings=SimpleNamespace(
        creator_micro_enabled=enabled,
        creator_micro_device_serial=serial,
        agent_deck_enabled=agent_deck,
    ))


def _target(calls, runtime=None):
    target = SimpleNamespace(
        _sidepulse_optional_integration_runtime=runtime,
        _runtime_termination_started=False,
        _deck_runtime_stopping=False,
        reconfigureDeckRuntime_=lambda sender: calls.append("restart"),
        performSelectorOnMainThread_withObject_waitUntilDone_=lambda selector, value, wait: calls.append((selector, value)),
    )
    return target


def test_inspection_revokes_and_stops_runtime_before_opening_setup_owner(tmp_path):
    calls = []
    target = _target(calls)
    target._sidepulse_optional_integration_runtime = _Runtime(calls)

    thread = begin_creator_micro_inspection(
        target,
        settings_loader=lambda: _loaded(),
        adapter_factory=lambda serial: _Adapter(calls),
        setup_factory=lambda adapter, serial, backup, **kwargs: _Setup(calls),
        backup_root=tmp_path,
    )
    thread.join(1)

    assert calls[:6] == ["revoke", "close-runtime", "wait-runtime", "connect-setup", "inspect", "close-setup"]
    selector, result = calls[6]
    assert selector == "applyCreatorMicroSetupResult:"
    assert result.code == "inspection_ready"
    assert result.preview.plan is _plan() or result.preview.plan == _plan()
    assert target._sidepulse_optional_integration_runtime is None


def test_inspection_never_opens_setup_owner_when_runtime_does_not_stop(tmp_path):
    calls = []
    target = _target(calls, _Runtime(calls, stopped=False))

    thread = begin_creator_micro_inspection(
        target,
        settings_loader=lambda: _loaded(),
        adapter_factory=lambda serial: calls.append("opened"),
        backup_root=tmp_path,
    )
    thread.join(1)

    assert "opened" not in calls
    assert calls[-1][1].code == "previous_owner_stopping"


def test_inspection_refuses_disabled_connection_and_competing_snapshot_owner(tmp_path):
    for loaded in (_loaded(enabled=False), _loaded(agent_deck=True)):
        calls = []
        target = _target(calls, _Runtime(calls))
        thread = begin_creator_micro_inspection(
            target,
            settings_loader=lambda loaded=loaded: loaded,
            adapter_factory=lambda serial: calls.append("opened"),
            backup_root=tmp_path,
        )
        thread.join(1)
        assert "opened" not in calls
        assert "revoke" not in calls
        assert calls[-1][1].code in {"connection_required", "agent_deck_ownership"}


def test_superseded_or_terminating_work_does_not_dispatch_success(tmp_path):
    calls = []
    target = _target(calls)

    def factory(_serial):
        target._creator_micro_setup_generation = object()
        return _Adapter(calls)

    thread = begin_creator_micro_inspection(
        target,
        settings_loader=lambda: _loaded(),
        adapter_factory=factory,
        setup_factory=lambda adapter, serial, backup, **kwargs: _Setup(calls),
        backup_root=tmp_path,
    )
    thread.join(1)
    assert not [item for item in calls if isinstance(item, tuple)]
    assert "connect-setup" not in calls
    assert "close-setup" in calls


def test_adapter_closes_before_restart_lock_can_open_another_owner(tmp_path):
    import threading

    calls = []
    lock = threading.Lock()
    target = _target(calls)
    target._deck_runtime_restart_lock = lock

    class Adapter(_Adapter):
        def close(self):
            assert lock.locked()
            calls.append("close-setup")

    thread = begin_creator_micro_inspection(
        target,
        settings_loader=lambda: _loaded(),
        adapter_factory=lambda serial: Adapter(calls),
        setup_factory=lambda adapter, serial, backup, **kwargs: _Setup(calls),
        backup_root=tmp_path,
    )
    thread.join(1)
    assert calls.index("close-setup") < next(index for index, item in enumerate(calls) if isinstance(item, tuple))


def test_stale_deck_generation_callback_only_clears_busy_state():
    calls = []
    target = _target(calls)
    target.deck_settings_pane = SimpleNamespace(
        set_setup_pending=lambda value: calls.append(("pending", value)),
        set_status=lambda value: calls.append(("status", value)),
    )
    target.reconfigureDeckRuntime_ = lambda sender: calls.append("restart")
    target.beginCreatorMicroSetupApply_ = lambda preview: calls.append("apply")
    generation = object()
    target._creator_micro_setup_generation = generation
    target._deck_runtime_generation = object()
    target._creator_micro_setup_busy = True
    result = SimpleNamespace(
        generation=generation,
        code="inspection_ready",
        preview=SetupPreview("device", _plan()),
        operation="inspect",
        runtime_was_stopped=True,
    )

    apply_creator_micro_setup_result(target, result)

    assert target._creator_micro_setup_busy is False
    assert calls == [("pending", False)]


def test_apply_consent_check_observes_settings_revocation_before_flash(tmp_path):
    calls = []
    loaded = [_loaded()]
    target = _target(calls)
    plan = _plan()

    class Setup(_Setup):
        def apply(self, candidate):
            loaded[0] = _loaded(enabled=False)
            assert candidate is plan
            assert self.is_current() is False
            calls.append("cancelled-before-write")
            return Receipt("cancelled")

        def __init__(self, calls, **kwargs):
            super().__init__(calls)
            self.is_current = kwargs["is_current"]

    thread = begin_creator_micro_inspection(
        target,
        settings_loader=lambda: loaded[0],
        adapter_factory=lambda serial: _Adapter(calls),
        setup_factory=lambda adapter, serial, backup, **kwargs: _Setup(calls),
        backup_root=tmp_path,
    )
    thread.join(1)
    inspected = calls[-1][1]
    apply_creator_micro_setup_result(
        target,
        inspected,
        alert_factory=lambda: SimpleNamespace(
            setMessageText_=lambda value: None,
            setInformativeText_=lambda value: None,
            addButtonWithTitle_=lambda value: None,
            runModal=lambda: 1001,
        ),
    )
    calls.clear()
    loaded[0] = _loaded()
    thread = begin_creator_micro_apply(
        target,
        SetupPreview("approved", plan),
        settings_loader=lambda: loaded[0],
        adapter_factory=lambda serial: _Adapter(calls),
        setup_factory=lambda adapter, serial, backup, **kwargs: Setup(calls, **kwargs),
        backup_root=tmp_path,
    )
    thread.join(1)
    assert "cancelled-before-write" in calls


def test_preview_text_names_exact_changes_and_apply_dispatches_bound_preview():
    calls = []
    target = _target(calls)
    target.reconfigureDeckRuntime_ = lambda sender: calls.append("restart")
    target.deck_settings_pane = SimpleNamespace(set_setup_pending=lambda value: calls.append(("pending", value)), set_status=lambda value: calls.append(("status", value)))
    preview = SetupPreview("bound-device", _plan())
    result = SimpleNamespace(generation=object(), code="inspection_ready", preview=preview, operation="inspect", runtime_was_stopped=True)
    target._creator_micro_setup_generation = result.generation
    target._deck_runtime_generation = result.generation

    class Alert:
        def setMessageText_(self, value): calls.append(("message", value))
        def setInformativeText_(self, value): calls.append(("info", value))
        def addButtonWithTitle_(self, value): calls.append(("button", value))
        def runModal(self): return 1000

    target.beginCreatorMicroSetupApply_ = lambda value: calls.append(("apply", value))
    apply_creator_micro_setup_result(target, result, alert_factory=Alert)

    info = next(item[1] for item in calls if isinstance(item, tuple) and item[0] == "info")
    assert "Key 0: KC_A -> KV_OAI_AG00" in info
    assert "normal keystrokes" in info
    assert "Dial and joystick mappings stay unchanged." in info
    assert ("apply", preview) in calls
    assert "restart" not in calls
    assert target._creator_micro_setup_runtime_needs_restart is True


def test_cancelled_preview_does_not_start_apply():
    calls = []
    target = _target(calls)
    target.deck_settings_pane = SimpleNamespace(set_setup_pending=lambda value: None, set_status=lambda value: None)
    result = SimpleNamespace(generation=object(), code="inspection_ready", preview=SetupPreview("device", _plan()), operation="inspect")
    target._creator_micro_setup_generation = result.generation
    target._deck_runtime_generation = result.generation

    class Alert:
        def setMessageText_(self, value): pass
        def setInformativeText_(self, value): pass
        def addButtonWithTitle_(self, value): pass
        def runModal(self): return 1001

    target.beginCreatorMicroSetupApply_ = lambda value: calls.append("apply")
    apply_creator_micro_setup_result(target, result, alert_factory=Alert)
    assert "apply" not in calls


def test_restore_requires_confirmation_before_starting_background_work(tmp_path):
    calls = []
    target = _target(calls)
    thread = begin_creator_micro_restore(
        target,
        confirm=lambda: False,
        settings_loader=lambda: _loaded(),
        adapter_factory=lambda serial: calls.append("opened"),
        backup_root=tmp_path,
    )
    assert thread is None
    assert "opened" not in calls


def test_setup_resumes_enabled_runtime_even_when_it_took_over_a_pending_start(tmp_path):
    calls = []
    target = _target(calls)
    target._deck_runtime_generation = object()
    thread = begin_creator_micro_inspection(
        target, settings_loader=lambda: _loaded(),
        adapter_factory=lambda serial: _Adapter(calls),
        setup_factory=lambda adapter, serial, backup, **kwargs: _Setup(calls),
        backup_root=tmp_path,
    )
    thread.join(1)
    result = calls[-1][1]
    assert result.code == "inspection_ready"
    assert result.runtime_was_stopped


def test_rejected_setup_does_not_cancel_an_unstarted_normal_runtime_forever(tmp_path):
    calls = []
    target = _target(calls)
    target._deck_runtime_generation = object()
    thread = begin_creator_micro_inspection(
        target, settings_loader=lambda: _loaded(enabled=False), backup_root=tmp_path,
    )
    thread.join(1)
    result = calls[-1][1]
    assert result.code == "connection_required"
    assert result.runtime_was_stopped
