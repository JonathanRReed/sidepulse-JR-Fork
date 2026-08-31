from __future__ import annotations

import tempfile
import time
from pathlib import Path
from threading import Event

import pytest

from sidepulse import ipc as ipc_module
from sidepulse import providers
from sidepulse.capacity_types import SourceKey
from sidepulse.ipc import (
    HookEventServer,
    ProviderRefreshHint,
    send_hook_event,
    send_refresh_hint,
)
from sidepulse.provider_facts import EventToken
from sidepulse.providers import default_state_dir


@pytest.fixture
def short_root():
    with tempfile.TemporaryDirectory(prefix="sp-xdg-", dir="/tmp") as directory:
        yield Path(directory)


def _hint() -> ProviderRefreshHint:
    return ProviderRefreshHint(
        SourceKey("claude", "hooks", "global", "live_agent_events"),
        EventToken("event:xdg-split"),
    )


def _split_state_environment(
    tmp_path: Path,
    monkeypatch,
) -> tuple[Path, Path]:
    home = tmp_path / "home"
    xdg_state = tmp_path / "xdg-state"
    home.mkdir()
    xdg_state.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state))
    return home, xdg_state


def test_candidate_state_dirs_try_xdg_then_standard_home_without_duplicates(
    short_root: Path,
    monkeypatch,
) -> None:
    home, xdg_state = _split_state_environment(short_root, monkeypatch)

    assert providers.candidate_state_dirs() == (
        xdg_state / "sidepulse" / "agent-monitor",
        home / ".local" / "state" / "sidepulse" / "agent-monitor",
    )

    standard_state_home = home / ".local" / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(standard_state_home))
    assert providers.candidate_state_dirs() == (
        standard_state_home / "sidepulse" / "agent-monitor",
    )


def test_candidate_state_dirs_with_explicit_home_ignore_process_xdg(
    short_root: Path,
    monkeypatch,
) -> None:
    home, _xdg_state = _split_state_environment(short_root, monkeypatch)

    assert providers.candidate_state_dirs(home) == (
        home / ".local" / "state" / "sidepulse" / "agent-monitor",
    )


def test_refresh_hint_reaches_standard_state_socket_when_hook_has_xdg_state(
    short_root: Path,
    monkeypatch,
) -> None:
    home, _xdg_state = _split_state_environment(short_root, monkeypatch)
    received: list[ProviderRefreshHint] = []
    callback = Event()

    def receive(hint: ProviderRefreshHint) -> None:
        received.append(hint)
        callback.set()

    server = HookEventServer(
        receive,
        socket_path=default_state_dir(home) / "events.sock",
    )
    server.start()
    try:
        delivered = send_refresh_hint(
            _hint(),
            event_name="SessionStart",
            timeout=0.5,
        )

        assert delivered
        assert callback.wait(1.0)
        assert received == [_hint()]
    finally:
        server.stop()


def test_legacy_hook_wakes_standard_state_socket_when_hook_has_xdg_state(
    short_root: Path,
    monkeypatch,
) -> None:
    home, _xdg_state = _split_state_environment(short_root, monkeypatch)
    received: list[str] = []
    callback = Event()

    def receive_legacy(provider: str) -> None:
        received.append(provider)
        callback.set()

    server = HookEventServer(
        lambda _hint_value: None,
        socket_path=default_state_dir(home) / "events.sock",
        on_legacy_hook=receive_legacy,
    )
    server.start()
    try:
        delivered = send_hook_event(
            "claude",
            {"hook_event_name": "SessionStart", "session_id": "xdg-split"},
            timeout=0.5,
        )

        assert not delivered
        assert callback.wait(1.0)
        assert received == ["claude"]
    finally:
        server.stop()


def test_explicit_refresh_hint_path_never_probes_fallback(
    short_root: Path,
    monkeypatch,
) -> None:
    home, xdg_state = _split_state_environment(short_root, monkeypatch)
    callback = Event()
    server = HookEventServer(
        lambda _hint_value: callback.set(),
        socket_path=default_state_dir(home) / "events.sock",
    )
    server.start()
    try:
        assert not send_refresh_hint(
            _hint(),
            socket_path=xdg_state / "missing.sock",
            event_name="SessionStart",
            timeout=0.5,
        )
        assert not callback.wait(0.1)
    finally:
        server.stop()


def test_explicit_legacy_hook_path_never_probes_fallback(
    short_root: Path,
    monkeypatch,
) -> None:
    home, xdg_state = _split_state_environment(short_root, monkeypatch)
    callback = Event()
    server = HookEventServer(
        lambda _hint_value: None,
        socket_path=default_state_dir(home) / "events.sock",
        on_legacy_hook=lambda _provider: callback.set(),
    )
    server.start()
    try:
        assert not send_hook_event(
            "claude",
            {"hook_event_name": "SessionStart", "session_id": "explicit"},
            socket_path=xdg_state / "missing.sock",
            timeout=0.5,
        )
        assert not callback.wait(0.1)
    finally:
        server.stop()


def test_breaker_suppression_on_xdg_candidate_does_not_block_fallback(
    short_root: Path,
    monkeypatch,
) -> None:
    home, _xdg_state = _split_state_environment(short_root, monkeypatch)
    xdg_socket, _fallback_socket = ipc_module.candidate_event_socket_paths()
    breaker = ipc_module._HookSendBreaker()
    for _index in range(ipc_module.HOOK_BREAKER_TRIP_AFTER):
        breaker.record(
            delivered=False,
            now=time.monotonic(),
            socket_path=xdg_socket,
        )
    monkeypatch.setattr(ipc_module, "HOOK_SEND_BREAKER", breaker)
    callback = Event()
    server = HookEventServer(
        lambda _hint_value: callback.set(),
        socket_path=default_state_dir(home) / "events.sock",
    )
    server.start()
    try:
        assert send_refresh_hint(
            _hint(),
            event_name="PostToolUse",
            timeout=0.5,
        )
        assert callback.wait(1.0)
    finally:
        server.stop()


def test_refresh_hint_stops_after_first_responding_candidate(
    short_root: Path,
    monkeypatch,
) -> None:
    _home, _xdg_state = _split_state_environment(short_root, monkeypatch)
    xdg_socket, fallback_socket = ipc_module.candidate_event_socket_paths()
    xdg_callback = Event()
    fallback_callback = Event()
    xdg_server = HookEventServer(
        lambda _hint_value: xdg_callback.set(),
        socket_path=xdg_socket,
    )
    fallback_server = HookEventServer(
        lambda _hint_value: fallback_callback.set(),
        socket_path=fallback_socket,
    )
    xdg_server.start()
    fallback_server.start()
    try:
        assert send_refresh_hint(
            _hint(),
            event_name="SessionStart",
            timeout=0.5,
        )
        assert xdg_callback.wait(1.0)
        assert not fallback_callback.wait(0.1)
    finally:
        fallback_server.stop()
        xdg_server.stop()


def test_legacy_hook_stops_after_first_responding_candidate(
    short_root: Path,
    monkeypatch,
) -> None:
    _home, _xdg_state = _split_state_environment(short_root, monkeypatch)
    xdg_socket, fallback_socket = ipc_module.candidate_event_socket_paths()
    xdg_callback = Event()
    fallback_callback = Event()
    xdg_server = HookEventServer(
        lambda _hint_value: None,
        socket_path=xdg_socket,
        on_legacy_hook=lambda _provider: xdg_callback.set(),
    )
    fallback_server = HookEventServer(
        lambda _hint_value: None,
        socket_path=fallback_socket,
        on_legacy_hook=lambda _provider: fallback_callback.set(),
    )
    xdg_server.start()
    fallback_server.start()
    try:
        assert not send_hook_event(
            "claude",
            {"hook_event_name": "SessionStart", "session_id": "first"},
            timeout=0.5,
        )
        assert xdg_callback.wait(1.0)
        assert not fallback_callback.wait(0.1)
    finally:
        fallback_server.stop()
        xdg_server.stop()


def test_single_instance_probe_finds_standard_socket_when_process_has_xdg_state(
    short_root: Path,
    monkeypatch,
) -> None:
    home, _xdg_state = _split_state_environment(short_root, monkeypatch)
    server = HookEventServer(
        lambda _hint_value: None,
        socket_path=default_state_dir(home) / "events.sock",
    )
    server.start()
    try:
        assert ipc_module.another_instance_alive(timeout=0.5)
    finally:
        server.stop()
