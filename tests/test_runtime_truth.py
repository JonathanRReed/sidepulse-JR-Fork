from __future__ import annotations

from sidepulse.runtime_truth import (
    HookTruthInputs,
    HookTruthState,
    ProcessOwner,
    ProcessTruthInputs,
    classify_hook_truth,
    classify_process_truth,
)


def hook_inputs(**changes):
    values = dict(
        provider="grok",
        hooks_installed=True,
        session_exists=True,
        session_started_at=100.0,
        hooks_installed_at=50.0,
        last_event_at=120.0,
        last_event_name="UserPromptSubmit",
        last_active_event_at=120.0,
        lifecycle="active",
        now=125.0,
        stale_after=60.0,
    )
    values.update(changes)
    return HookTruthInputs(**values)


def test_not_configured_is_distinct_from_idle() -> None:
    truth = classify_hook_truth(
        hook_inputs(hooks_installed=False, session_exists=False)
    )
    assert truth.state is HookTruthState.NOT_CONFIGURED
    assert truth.action == "Connect Grok"


def test_grok_hooks_installed_mid_session_require_reload_until_activity_arrives() -> None:
    truth = classify_hook_truth(
        hook_inputs(
            session_started_at=100.0,
            hooks_installed_at=110.0,
            last_event_at=100.0,
            last_event_name="SessionStart",
            last_active_event_at=None,
            lifecycle="idle",
            now=120.0,
        )
    )
    assert truth.state is HookTruthState.RELOAD_REQUIRED
    assert truth.action == "Reload Grok hooks"


def test_session_start_only_is_awaiting_activity_not_idle() -> None:
    truth = classify_hook_truth(
        hook_inputs(
            last_event_at=100.0,
            last_event_name="SessionStart",
            last_active_event_at=None,
            lifecycle="idle",
            now=105.0,
        )
    )
    assert truth.state is HookTruthState.AWAITING_ACTIVITY
    assert "awaiting" in truth.summary.lower()


def test_observed_active_event_can_settle_to_idle() -> None:
    truth = classify_hook_truth(
        hook_inputs(lifecycle="idle", last_active_event_at=118.0)
    )
    assert truth.state is HookTruthState.IDLE


def test_active_and_waiting_lifecycles_are_truthful() -> None:
    working = classify_hook_truth(hook_inputs(lifecycle="active"))
    waiting = classify_hook_truth(hook_inputs(lifecycle="waiting"))
    assert working.state is HookTruthState.WORKING
    assert waiting.state is HookTruthState.NEEDS_INPUT


def test_old_source_is_stale_before_lifecycle_is_trusted() -> None:
    truth = classify_hook_truth(
        hook_inputs(last_event_at=10.0, last_active_event_at=10.0, now=100.0)
    )
    assert truth.state is HookTruthState.STALE
    assert truth.action == "Check Grok hooks"


def process_inputs(**changes):
    values = dict(
        plist_installed=False,
        launch_agent_loaded=False,
        launch_agent_pid=None,
        foreground_pid=None,
        socket_owner_pid=None,
    )
    values.update(changes)
    return ProcessTruthInputs(**values)


def test_foreground_process_owns_sidepulse() -> None:
    truth = classify_process_truth(
        process_inputs(foreground_pid=42, socket_owner_pid=42)
    )
    assert truth.owner is ProcessOwner.FOREGROUND
    assert truth.healthy is True


def test_launch_agent_process_owns_sidepulse() -> None:
    truth = classify_process_truth(
        process_inputs(
            plist_installed=True,
            launch_agent_loaded=True,
            launch_agent_pid=55,
            socket_owner_pid=55,
        )
    )
    assert truth.owner is ProcessOwner.LAUNCH_AGENT
    assert truth.healthy is True


def test_installed_but_unloaded_launch_agent_is_not_running() -> None:
    truth = classify_process_truth(process_inputs(plist_installed=True))
    assert truth.owner is ProcessOwner.NONE
    assert "not loaded" in truth.summary.lower()


def test_foreground_and_launch_agent_conflict() -> None:
    truth = classify_process_truth(
        process_inputs(
            plist_installed=True,
            launch_agent_loaded=True,
            launch_agent_pid=55,
            foreground_pid=42,
            socket_owner_pid=42,
        )
    )
    assert truth.owner is ProcessOwner.CONFLICT
    assert truth.healthy is False
    assert truth.action == "Choose one SidePulse process"
