"""A wedged listener must not slow every hook -- but must never cost a completion.

The send timeout used to be paid in full on every hook for as long as
the app stayed wedged, invisibly. The breaker bounds that. The
exemption list is the non-obvious half: under load a healthy-but-busy
server times out too, and suppressing terminal events there would drop
the completions this product exists to deliver.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidepulse.ipc import (
    HOOK_BREAKER_TRIP_AFTER,
    HOOK_EVENT_SEND_TIMEOUT_SECONDS,
    TERMINAL_HOOK_EVENTS,
    _HookSendBreaker,
)


@pytest.fixture
def breaker(tmp_path: Path) -> _HookSendBreaker:
    """Its own sentinel file: state is per-machine, not per-process."""
    return _HookSendBreaker(path=tmp_path / "breaker.json")


def test_timeout_is_tight_enough_to_be_invisible() -> None:
    assert HOOK_EVENT_SEND_TIMEOUT_SECONDS <= 0.05


def test_ordinary_sends_stop_after_repeated_failure(breaker) -> None:
    now = 100.0
    for _ in range(HOOK_BREAKER_TRIP_AFTER):
        assert breaker.should_attempt("PreToolUse", now) is True
        breaker.record(delivered=False, now=now)
    assert breaker.should_attempt("PreToolUse", now) is False


def test_terminal_events_are_always_attempted(breaker) -> None:
    now = 100.0
    for _ in range(HOOK_BREAKER_TRIP_AFTER * 5):
        breaker.record(delivered=False, now=now)
    for event in sorted(TERMINAL_HOOK_EVENTS):
        assert breaker.should_attempt(event, now) is True, event
    # ...while ordinary chatter stays suppressed.
    assert breaker.should_attempt("PostToolUse", now) is False


def test_one_success_closes_the_breaker(breaker) -> None:
    now = 100.0
    for _ in range(HOOK_BREAKER_TRIP_AFTER):
        breaker.record(delivered=False, now=now)
    assert breaker.should_attempt("PreToolUse", now) is False
    breaker.record(delivered=True, now=now)
    assert breaker.should_attempt("PreToolUse", now) is True


def test_suppression_expires_on_its_own(breaker) -> None:
    for _ in range(HOOK_BREAKER_TRIP_AFTER):
        breaker.record(delivered=False, now=100.0)
    assert breaker.should_attempt("PreToolUse", 100.0) is False
    # Expiry is wall-clock: rewind the sentinel past the cooldown.
    import json as _json

    data = _json.loads(breaker.sentinel_path().read_text())
    data["since"] -= 10_000.0
    breaker.sentinel_path().write_text(_json.dumps(data))
    assert breaker.should_attempt("PreToolUse", 100.0) is True


def test_dropped_sends_are_counted_not_silent(breaker) -> None:
    for _ in range(HOOK_BREAKER_TRIP_AFTER):
        breaker.record(delivered=False, now=100.0)
    breaker.should_attempt("PreToolUse", 100.0)
    assert breaker.suppressed_sends >= 1



def test_state_survives_a_new_process(tmp_path: Path) -> None:
    """The whole reason it is a file: every hook is a fresh process."""
    path = tmp_path / "breaker.json"
    first = _HookSendBreaker(path=path)
    for _ in range(HOOK_BREAKER_TRIP_AFTER):
        first.record(delivered=False, now=100.0)
    # A brand new instance -- as a new hook process would be.
    assert _HookSendBreaker(path=path).should_attempt("PreToolUse", 100.0) is False
