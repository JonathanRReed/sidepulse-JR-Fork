from __future__ import annotations

import os
import socket
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse import ipc
from sidepulse.capacity_types import SourceKey
from sidepulse.ipc import HookEventServer, ProviderRefreshHint, send_refresh_hint
from sidepulse.private_io import ensure_private_directory
from sidepulse.provider_facts import EventToken

SOURCE = SourceKey("codex", "hooks", "global", "live_agent_events")


def _hint(token: str = "event:test") -> ProviderRefreshHint:
    return ProviderRefreshHint(SOURCE, EventToken(token))


@pytest.fixture
def socket_root() -> Path:
    # Darwin's sockaddr_un path limit is much shorter than pytest's default
    # per-test temporary path. Keep every real socket inside a short temp root.
    with tempfile.TemporaryDirectory(prefix="sp-ipc-", dir="/tmp") as directory:
        yield Path(directory)


def _socket_identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino


def test_start_refuses_to_unlink_a_live_peer_socket(socket_root: Path) -> None:
    socket_path = socket_root / "state" / "events.sock"
    received: list[ProviderRefreshHint] = []
    received_event = threading.Event()

    def observe_hint(hint: ProviderRefreshHint) -> None:
        received.append(hint)
        received_event.set()

    first = HookEventServer(
        observe_hint,
        socket_path=socket_path,
    )
    second = HookEventServer(lambda _hint_value: None, socket_path=socket_path)
    first.start()
    original_identity = _socket_identity(socket_path)

    try:
        with pytest.raises(OSError, match=r"live|owned|socket"):
            second.start()

        assert _socket_identity(socket_path) == original_identity
        assert send_refresh_hint(
            _hint("event:survivor"),
            socket_path=socket_path,
            timeout=0.5,
        )
        assert received_event.wait(1.0)
        assert received == [_hint("event:survivor")]
    finally:
        second.stop()
        first.stop()


def test_start_refuses_a_multiply_linked_socket(socket_root: Path) -> None:
    state = socket_root / "state"
    state.mkdir()
    live_path = state / "live.sock"
    target = state / "events.sock"
    peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    peer.bind(str(live_path))
    peer.listen(1)
    os.link(live_path, target)
    server = HookEventServer(lambda _hint_value: None, socket_path=target)

    try:
        with pytest.raises(OSError, match=r"link|live|socket"):
            server.start()
        assert target.lstat().st_nlink == 2
        assert _socket_identity(target) == _socket_identity(live_path)
    finally:
        server.stop()
        peer.close()
        for path in (target, live_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def test_start_reclaims_an_unchanged_stale_socket(socket_root: Path) -> None:
    socket_path = socket_root / "state" / "events.sock"
    socket_path.parent.mkdir()
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(socket_path))
    stale.close()
    stale_identity = _socket_identity(socket_path)
    received = threading.Event()
    server = HookEventServer(
        lambda _hint_value: received.set(),
        socket_path=socket_path,
    )

    try:
        server.start()
        assert _socket_identity(socket_path) != stale_identity
        assert send_refresh_hint(
            _hint("event:stale-recovered"),
            socket_path=socket_path,
            timeout=0.5,
        )
        assert received.wait(0.75)
    finally:
        server.stop()


def test_start_fails_closed_when_parent_is_swapped_before_binding(
    socket_root: Path,
) -> None:
    parent = socket_root / "state"
    held_parent = socket_root / "state-held"
    outside = socket_root / "outside"
    outside.mkdir()
    socket_path = parent / "events.sock"
    real_ensure = ensure_private_directory

    def swap_parent(path: Path) -> Path:
        result = real_ensure(path)
        parent.rename(held_parent)
        parent.symlink_to(outside, target_is_directory=True)
        return result

    server = HookEventServer(lambda _hint_value: None, socket_path=socket_path)
    error: OSError | None = None
    try:
        with patch("sidepulse.ipc.ensure_private_directory", side_effect=swap_parent):
            try:
                server.start()
            except OSError as exc:
                error = exc

        assert error is not None
        assert list(outside.iterdir()) == []
    finally:
        server.stop()
        if parent.is_symlink():
            parent.unlink()


def test_start_bind_is_anchored_when_parent_swaps_inside_bind(
    socket_root: Path,
) -> None:
    parent = socket_root / "state"
    held_parent = socket_root / "state-held"
    outside = socket_root / "outside"
    outside.mkdir()
    socket_path = parent / "events.sock"
    real_socket = socket.socket
    original_cwd = os.getcwd()
    bind_addresses: list[str] = []
    bind_thread_cwds: list[str] = []
    other_thread_cwds: list[str] = []

    class BindSwapSocket:
        def __init__(self, *args, **kwargs):
            self.inner = real_socket(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def bind(self, address):
            parent.rename(held_parent)
            parent.symlink_to(outside, target_is_directory=True)
            bind_addresses.append(address)
            bind_thread_cwds.append(os.getcwd())
            observer = threading.Thread(target=lambda: other_thread_cwds.append(os.getcwd()))
            observer.start()
            observer.join(timeout=1.0)
            assert not observer.is_alive()
            return self.inner.bind(address)

    server = HookEventServer(lambda _hint_value: None, socket_path=socket_path)
    try:
        with (
            patch("sidepulse.ipc.socket.socket", side_effect=BindSwapSocket),
            pytest.raises(OSError, match=r"parent|changed"),
        ):
            server.start()

        assert list(outside.iterdir()) == []
        assert list(held_parent.iterdir()) == []
        assert bind_addresses == [socket_path.name]
        assert len(bind_thread_cwds) == 1
        assert Path(bind_thread_cwds[0]).samefile(held_parent)
        assert other_thread_cwds == [original_cwd]
        assert os.getcwd() == original_cwd
    finally:
        server.stop()
        for directory in (outside, held_parent):
            leaked = directory / socket_path.name
            try:
                leaked.unlink()
            except FileNotFoundError:
                pass
        if parent.is_symlink():
            parent.unlink()


def test_start_cleans_bound_inode_when_thread_directory_reset_fails(
    socket_root: Path,
) -> None:
    socket_path = socket_root / "state" / "events.sock"
    real_set_thread_directory = ipc._set_darwin_thread_directory

    def report_reset_failure(descriptor: int) -> None:
        real_set_thread_directory(descriptor)
        if descriptor == -1:
            raise OSError("simulated reset failure after restoring the caller")

    server = HookEventServer(lambda _hint_value: None, socket_path=socket_path)
    try:
        with (
            patch(
                "sidepulse.ipc._set_darwin_thread_directory",
                side_effect=report_reset_failure,
            ),
            pytest.raises(OSError, match="simulated reset failure"),
        ):
            server.start()

        assert list(socket_path.parent.iterdir()) == []
    finally:
        server.stop()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def test_stop_preserves_a_replacement_socket_inode(socket_root: Path) -> None:
    socket_path = socket_root / "state" / "events.sock"
    server = HookEventServer(lambda _hint_value: None, socket_path=socket_path)
    server.start()
    socket_path.unlink()
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    replacement.bind(str(socket_path))
    replacement.listen(1)
    replacement_identity = _socket_identity(socket_path)

    try:
        server.stop()
        assert _socket_identity(socket_path) == replacement_identity
    finally:
        replacement.close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def test_client_leaf_swap_fails_before_sending_to_replacement_peer(
    socket_root: Path,
) -> None:
    socket_path = socket_root / "state" / "events.sock"
    server = HookEventServer(lambda _hint_value: None, socket_path=socket_path)
    server.start()
    real_socket = socket.socket
    replacement: socket.socket | None = None

    class SwappingClient:
        def __init__(self, *args, **kwargs):
            self.inner = real_socket(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def connect(self, address):
            nonlocal replacement
            socket_path.unlink()
            replacement = real_socket(socket.AF_UNIX, socket.SOCK_STREAM)
            replacement.bind(str(socket_path))
            replacement.listen(1)
            return self.inner.connect(address)

    try:
        with patch("sidepulse.ipc.socket.socket", side_effect=SwappingClient):
            sent = send_refresh_hint(
                _hint("event:redirected"),
                socket_path=socket_path,
                timeout=0.5,
            )

        assert sent is False
        assert replacement is not None
        replacement.settimeout(0.5)
        connection, _ = replacement.accept()
        with connection:
            connection.settimeout(0.5)
            assert connection.recv(65536) == b""
    finally:
        server.stop()
        if replacement is not None:
            replacement.close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def test_silent_peer_does_not_block_a_later_valid_peer(socket_root: Path) -> None:
    socket_path = socket_root / "state" / "events.sock"
    received = threading.Event()
    server = HookEventServer(
        lambda _hint_value: received.set(),
        socket_path=socket_path,
    )
    server.start()
    original_handle = server._handle_connection
    silent_started = threading.Event()

    def observe_connection(connection: socket.socket) -> None:
        silent_started.set()
        original_handle(connection)

    server._handle_connection = observe_connection
    silent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    silent.settimeout(0.5)
    silent.connect(str(socket_path))
    assert silent_started.wait(1.0)

    try:
        assert send_refresh_hint(
            _hint("event:later-valid"),
            socket_path=socket_path,
            timeout=0.5,
        )
        assert received.wait(0.75)
    finally:
        silent.close()
        server.stop()


def test_stop_closes_stalled_peers_and_joins_server_threads(socket_root: Path) -> None:
    socket_path = socket_root / "state" / "events.sock"
    server = HookEventServer(lambda _hint_value: None, socket_path=socket_path)
    server.start()
    original_handle = server._handle_connection
    entered = 0
    entered_lock = threading.Lock()
    all_peers_started = threading.Event()

    def observe_connection(connection: socket.socket) -> None:
        nonlocal entered
        with entered_lock:
            entered += 1
            if entered == 4:
                all_peers_started.set()
        original_handle(connection)

    server._handle_connection = observe_connection
    silent_peers = []
    for _index in range(4):
        peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        peer.settimeout(0.5)
        peer.connect(str(socket_path))
        silent_peers.append(peer)
    assert all_peers_started.wait(1.0)

    server.stop()
    try:
        assert server.thread is None or not server.thread.is_alive()
        assert not any(worker.is_alive() for worker in server._workers)
        assert server._connections == set()
    finally:
        for peer in silent_peers:
            peer.close()
