from __future__ import annotations

import ctypes
import errno
import json
import os
import socket
import stat
import struct
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .capacity_types import SourceKey
from .private_io import ensure_private_directory
from .provider_facts import EventToken
from .providers import default_state_dir

MAX_HINT_BYTES = 512
MAX_EVENT_BYTES = MAX_HINT_BYTES
# A wedged listener used to cost every hook the full timeout, invisibly,
# for as long as it stayed wedged. 30ms is ample for a loopback unix
# socket and bounds the damage; the circuit breaker below bounds it
# further. Measured baseline: median hook ~48ms total.
HOOK_EVENT_SEND_TIMEOUT_SECONDS = 0.03
# Events that mean "this work ENDED". These are the whole product -- a
# missed completion is the one failure we cannot ship -- so they are
# never suppressed by the breaker, only ever rate-limited by it.
TERMINAL_HOOK_EVENTS = frozenset(
    {
        "Stop",
        "StopFailure",
        "SubagentStop",
        "SessionEnd",
        "SessionStart",
        "Notification",
        "PermissionRequest",
    }
)
# Consecutive failures before we stop trying non-terminal sends, and how
# long that suppression lasts before we probe again.
HOOK_BREAKER_TRIP_AFTER = 3
HOOK_BREAKER_COOLDOWN_SECONDS = 30.0
STALE_SOCKET_PROBE_TIMEOUT_SECONDS = 0.2
PEER_READ_TIMEOUT_SECONDS = 0.5
MAX_CONCURRENT_PEERS = 8
SERVER_ACCEPT_TIMEOUT_SECONDS = 0.1
SERVER_STOP_TIMEOUT_SECONDS = 0.75

_Identity = tuple[int, int]
_HINT_FIELDS = frozenset(
    {
        "version",
        "provider_id",
        "adapter_id",
        "source_instance_id",
        "capability_id",
        "event_token",
    }
)
_RAW_EVENT_FIELDS = frozenset({"provider", "line"})
_RAW_EVENT_ACK = b"ok"
_RAW_EVENT_REJECT = b"no"


@dataclass(frozen=True, slots=True)
class ProviderRefreshHint:
    source_key: SourceKey
    event_token: EventToken

    def __post_init__(self) -> None:
        if not (
            type(self.source_key) is SourceKey
            and all(
                type(component) is str
                for component in (
                    self.source_key.provider_id,
                    self.source_key.adapter_id,
                    self.source_key.source_instance_id,
                    self.source_key.capability_id,
                )
            )
            and type(self.event_token) is EventToken
        ):
            raise ValueError("invalid provider refresh hint")


def _hint_to_payload(hint: ProviderRefreshHint) -> dict[str, object]:
    if type(hint) is not ProviderRefreshHint:
        raise ValueError("invalid provider refresh hint")
    return {
        "version": 1,
        "provider_id": hint.source_key.provider_id,
        "adapter_id": hint.source_key.adapter_id,
        "source_instance_id": hint.source_key.source_instance_id,
        "capability_id": hint.source_key.capability_id,
        "event_token": hint.event_token.value,
    }


def _strict_hint_object(
    pairs: list[tuple[object, object]],
) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("invalid provider refresh hint")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid provider refresh hint")


def _hint_from_wire(payload: bytes) -> ProviderRefreshHint | None:
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_hint_object,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, UnicodeError, ValueError):
        return None
    if type(document) is not dict or frozenset(document) != _HINT_FIELDS:
        return None
    if not (
        type(document["version"]) is int
        and document["version"] == 1
        and all(
            type(document[field]) is str
            for field in _HINT_FIELDS - {"version"}
        )
    ):
        return None
    try:
        return ProviderRefreshHint(
            SourceKey(
                document["provider_id"],
                document["adapter_id"],
                document["source_instance_id"],
                document["capability_id"],
            ),
            EventToken(document["event_token"]),
        )
    except ValueError:
        return None


def _raw_event_from_wire(payload: bytes) -> tuple[str, dict] | None:
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_hint_object,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, UnicodeError, ValueError):
        return None
    if (
        type(document) is not dict
        or frozenset(document) != _RAW_EVENT_FIELDS
        or type(document["provider"]) is not str
        or type(document["line"]) is not dict
    ):
        return None
    return document["provider"], document["line"]


def _identity(info: os.stat_result) -> _Identity:
    return info.st_dev, info.st_ino


def _directory_open_flags() -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    return flags


class _SocketPathGuard:
    """Hold and revalidate the no-follow parent of one Unix socket path."""

    def __init__(self, target: Path) -> None:
        self.target = target.absolute()
        self.parent = self.target.parent
        self.name = self.target.name
        if not self.name or self.name in {".", ".."}:
            raise OSError(f"refusing invalid socket path: {self.target}")

        parent_info = self.parent.lstat()
        if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
            raise OSError(f"refusing non-directory socket parent: {self.parent}")
        if parent_info.st_uid != os.geteuid():
            raise OSError(f"refusing socket parent owned by another user: {self.parent}")

        self.descriptor = os.open(self.parent, _directory_open_flags())
        try:
            opened = os.fstat(self.descriptor)
            if not stat.S_ISDIR(opened.st_mode) or _identity(opened) != _identity(parent_info):
                raise OSError(f"socket parent changed while opening: {self.parent}")
            self.parent_identity = _identity(opened)
            self.assert_parent()
        except Exception:
            os.close(self.descriptor)
            raise

    def close(self) -> None:
        descriptor = self.descriptor
        self.descriptor = -1
        if descriptor >= 0:
            os.close(descriptor)

    def assert_parent(self) -> None:
        current = self.parent.lstat()
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or current.st_uid != os.geteuid()
            or _identity(current) != self.parent_identity
        ):
            raise OSError(f"socket parent changed during operation: {self.parent}")

    def leaf(self) -> os.stat_result | None:
        try:
            return os.stat(
                self.name,
                dir_fd=self.descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None

    def socket_leaf(self) -> os.stat_result:
        self.assert_parent()
        info = self.owned_socket_leaf()
        self.assert_parent()
        return info

    def owned_socket_leaf(self) -> os.stat_result:
        """Validate the leaf through the held parent, even after a path swap."""
        info = self.leaf()
        if info is None:
            raise FileNotFoundError(self.target)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISSOCK(info.st_mode):
            raise OSError(f"refusing unexpected socket path: {self.target}")
        if info.st_nlink != 1:
            raise OSError(f"refusing multiply-linked socket path: {self.target}")
        return info

    def assert_socket_identity(self, expected: _Identity) -> os.stat_result:
        info = self.socket_leaf()
        if _identity(info) != expected:
            raise OSError(f"socket path changed during operation: {self.target}")
        return info

    def assert_absent(self) -> None:
        self.assert_parent()
        if self.leaf() is not None:
            raise OSError(f"socket path appeared during operation: {self.target}")
        self.assert_parent()

    def chmod_socket(self, expected: _Identity, mode: int) -> None:
        self.assert_socket_identity(expected)
        os.chmod(
            self.name,
            mode,
            dir_fd=self.descriptor,
            follow_symlinks=False,
        )
        self.assert_socket_identity(expected)

    def unlink_socket(self, expected: _Identity) -> None:
        self.assert_socket_identity(expected)
        self.unlink_owned_socket(expected)
        self.assert_parent()

    def unlink_owned_socket(self, expected: _Identity) -> None:
        info = self.owned_socket_leaf()
        if _identity(info) != expected:
            raise OSError(f"socket path changed during operation: {self.target}")
        os.unlink(self.name, dir_fd=self.descriptor)
        if self.leaf() is not None:
            raise OSError(f"socket path reappeared during operation: {self.target}")


def _set_darwin_thread_directory(descriptor: int) -> None:
    """Set or clear only the calling Darwin thread's working directory."""
    try:
        change_directory = ctypes.CDLL(None, use_errno=True).pthread_fchdir_np
    except AttributeError as error:
        raise OSError(errno.ENOTSUP, "thread-relative directory is unavailable") from error
    change_directory.argtypes = (ctypes.c_int,)
    change_directory.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = int(change_directory(descriptor))
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number == 0 and result > 0:
            error_number = result
        raise OSError(
            error_number or errno.EIO,
            "thread-relative directory change failed",
        )


def _bind_socket_in_guard(
    server: socket.socket,
    guard: _SocketPathGuard,
) -> _Identity:
    """Bind one pathname socket relative to the already-open guarded parent."""
    if sys.platform == "darwin":
        _set_darwin_thread_directory(guard.descriptor)
        bound_identity: _Identity | None = None
        try:
            server.bind(guard.name)
            bound_identity = _identity(guard.owned_socket_leaf())
        finally:
            # Darwin's per-thread fchdir syscall uses -1 to clear the override.
            try:
                _set_darwin_thread_directory(-1)
            except OSError:
                if bound_identity is not None:
                    guard.unlink_owned_socket(bound_identity)
                raise
        if bound_identity is None:
            raise OSError("descriptor-relative socket bind did not create a leaf")
        return bound_identity

    if sys.platform.startswith("linux"):
        descriptor_path = Path("/proc/self/fd") / str(guard.descriptor)
        if not descriptor_path.exists():
            raise OSError(errno.ENOTSUP, "descriptor-relative bind is unavailable")
        server.bind(str(descriptor_path / guard.name))
        return _identity(guard.owned_socket_leaf())

    raise OSError(errno.ENOTSUP, "descriptor-relative bind is unavailable")


def _open_existing_socket_path(target: Path) -> tuple[_SocketPathGuard, _Identity]:
    guard = _SocketPathGuard(target)
    try:
        return guard, _identity(guard.socket_leaf())
    except Exception:
        guard.close()
        raise


def _existing_socket_refuses_connections(
    guard: _SocketPathGuard,
    expected: _Identity,
) -> bool:
    """True only for an unchanged socket inode with no listener attached."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(STALE_SOCKET_PROBE_TIMEOUT_SECONDS)
    try:
        guard.assert_socket_identity(expected)
        probe.connect(str(guard.target))
        guard.assert_socket_identity(expected)
    except ConnectionRefusedError:
        guard.assert_socket_identity(expected)
        return True
    except OSError:
        # Timeout, disappearance, replacement, and other ambiguous failures are
        # not proof of staleness. Keep the existing inode intact.
        return False
    finally:
        probe.close()
    return False


def peer_effective_uid(connection: socket.socket) -> int:
    """Return the effective UID authenticated by one Unix socket peer."""
    get_peer_id = getattr(connection, "getpeereid", None)
    if get_peer_id is not None:
        uid, _gid = get_peer_id()
        return int(uid)

    local_peer_credentials = getattr(socket, "LOCAL_PEERCRED", None)
    if local_peer_credentials is not None:
        # macOS exposes LOCAL_PEERCRED but not socket.getpeereid(). The
        # returned xucred starts with cr_version then cr_uid.
        credentials = connection.getsockopt(0, local_peer_credentials, 76)
        if len(credentials) < 8:
            raise OSError("short Unix peer credentials")
        _version, uid = struct.unpack_from("=II", credentials)
        return int(uid)

    peer_credentials = getattr(socket, "SO_PEERCRED", None)
    if peer_credentials is not None:
        credentials = connection.getsockopt(
            socket.SOL_SOCKET,
            peer_credentials,
            struct.calcsize("3i"),
        )
        _pid, uid, _gid = struct.unpack("3i", credentials)
        return int(uid)
    raise OSError("Unix peer credentials are unavailable")


def _same_uid_peer(
    connection: socket.socket,
    reader: Callable[[socket.socket], int] | None = None,
) -> bool:
    try:
        return (reader or peer_effective_uid)(connection) == os.geteuid()
    except (OSError, TypeError, ValueError):
        return False


def default_event_socket_path() -> Path:
    return default_state_dir() / "events.sock"


def default_latest_state_path() -> Path:
    return default_state_dir() / "latest.json"


class _HookSendBreaker:
    """Stop paying the timeout on every hook when nobody is listening.

    State lives in a FILE, not memory: every hook invocation is its own
    short-lived process, so an in-memory counter would reset on each one
    and never trip. The sentinel is one small write on failure and one
    stat on the happy path.

    Terminal events are exempt and always attempted -- under load a
    healthy-but-busy server times out too, and suppressing completions
    there would drop the very thing this product exists to deliver.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self.suppressed_sends = 0

    def sentinel_path(self) -> Path:
        if self._path is not None:
            return self._path
        return default_state_dir() / "hook-send-breaker.json"

    def _read(self) -> tuple[int, float]:
        try:
            data = json.loads(self.sentinel_path().read_text())
            return int(data.get("failures", 0)), float(data.get("since", 0.0))
        except (OSError, ValueError, TypeError):
            return 0, 0.0

    def should_attempt(self, event_name: str | None, now: float) -> bool:
        if event_name in TERMINAL_HOOK_EVENTS:
            return True
        failures, since = self._read()
        if failures >= HOOK_BREAKER_TRIP_AFTER and (
            time.time() - since < HOOK_BREAKER_COOLDOWN_SECONDS
        ):
            self.suppressed_sends += 1
            return False
        return True

    def record(self, *, delivered: bool, now: float) -> None:
        path = self.sentinel_path()
        if delivered:
            try:
                path.unlink()
            except OSError:
                pass
            return
        failures, since = self._read()
        wall = time.time()
        if failures == 0 or wall - since >= HOOK_BREAKER_COOLDOWN_SECONDS:
            failures, since = 0, wall
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"failures": failures + 1, "since": since}),
                encoding="utf-8",
            )
        except OSError:
            pass


HOOK_SEND_BREAKER = _HookSendBreaker()


def send_refresh_hint(
    hint: ProviderRefreshHint,
    *,
    socket_path: Path | None = None,
    timeout: float = HOOK_EVENT_SEND_TIMEOUT_SECONDS,
    event_name: str | None = None,
) -> bool:
    """Send one hint, unless the breaker says nobody is listening.

    Terminal events ignore the breaker entirely -- a missed completion
    is the one failure this product cannot have, so it is always worth
    the attempt even when the app is wedged.
    """
    now = time.monotonic()
    if not HOOK_SEND_BREAKER.should_attempt(event_name, now):
        return False
    delivered = _send_refresh_hint_once(
        hint, socket_path=socket_path, timeout=timeout
    )
    HOOK_SEND_BREAKER.record(delivered=delivered, now=now)
    return delivered


def _send_refresh_hint_once(
    hint: ProviderRefreshHint,
    *,
    socket_path: Path | None = None,
    timeout: float = HOOK_EVENT_SEND_TIMEOUT_SECONDS,
) -> bool:
    if type(hint) is not ProviderRefreshHint:
        return False
    target = (socket_path or default_event_socket_path()).expanduser()
    payload = json.dumps(
        _hint_to_payload(hint),
        separators=(",", ":"),
        ensure_ascii=True,
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_HINT_BYTES:
        return False
    try:
        guard, expected = _open_existing_socket_path(target)
    except OSError:
        return False

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(target))
        guard.assert_socket_identity(expected)
        if not _same_uid_peer(client):
            return False
        client.sendall(payload)
        return True
    except OSError:
        return False
    finally:
        client.close()
        guard.close()


def send_hook_event(
    _provider: str,
    _line: dict,
    *,
    socket_path: Path | None = None,
    timeout: float = HOOK_EVENT_SEND_TIMEOUT_SECONDS,
) -> bool:
    payload = json.dumps(
        {"provider": _provider, "line": _line},
        separators=(",", ":"),
        ensure_ascii=True,
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_EVENT_BYTES:
        return False
    target = (socket_path or default_event_socket_path()).expanduser()
    try:
        guard, expected = _open_existing_socket_path(target)
    except OSError:
        return False
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(target))
        guard.assert_socket_identity(expected)
        if not _same_uid_peer(client):
            return False
        client.sendall(payload)
        try:
            client.shutdown(socket.SHUT_WR)
        except OSError:
            return False
        try:
            ack = client.recv(len(_RAW_EVENT_ACK))
        except OSError:
            return False
        return ack == _RAW_EVENT_ACK
    except OSError:
        return False
    finally:
        client.close()
        guard.close()


def another_instance_alive(socket_path: Path | None = None, timeout: float = 0.3) -> bool:
    """True when a LIVE server owns the hook socket. A stale socket
    file (crashed instance) refuses the connection and reads as dead --
    HookEventServer.start() unlinks and rebinds over it exactly as
    before. This is the single-instance probe: a second SidePulse used
    to steal the socket, and quitting it unlinked the path and
    permanently deafened the survivor."""
    target = (socket_path or default_event_socket_path()).expanduser()
    try:
        guard, expected = _open_existing_socket_path(target)
    except OSError:
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(timeout)
    try:
        probe.connect(str(target))
        guard.assert_socket_identity(expected)
        return _same_uid_peer(probe)
    except OSError:
        return False
    finally:
        probe.close()
        guard.close()


class HookEventServer:
    def __init__(
        self,
        on_hint: Callable[[ProviderRefreshHint], None],
        *,
        socket_path: Path | None = None,
        peer_uid_reader: Callable[[socket.socket], int] | None = None,
        on_legacy_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.on_hint = on_hint
        # Version skew is a REAL failure mode, not a theoretical one: a
        # hook script lives in the user's provider config and can lag
        # arbitrarily far behind the app. When one still speaks the
        # pre-hint wire format we must never fail silently -- the app
        # went deaf to every live agent event for an hour that way.
        self.on_legacy_hook = on_legacy_hook
        self.socket_path = (socket_path or default_event_socket_path()).expanduser()
        self.peer_uid_reader = peer_uid_reader
        self.socket: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.running = False
        self._lifecycle_lock = threading.RLock()
        self._peer_lock = threading.Lock()
        self._peer_slots = threading.BoundedSemaphore(MAX_CONCURRENT_PEERS)
        self._connections: set[socket.socket] = set()
        self._workers: set[threading.Thread] = set()
        self._path_guard: _SocketPathGuard | None = None
        self._bound_identity: _Identity | None = None

    def start(self) -> Path:
        with self._lifecycle_lock:
            accept_thread_alive = self.thread is not None and self.thread.is_alive()
            with self._peer_lock:
                peer_thread_alive = any(worker.is_alive() for worker in self._workers)
            if (
                self.running
                or self.socket is not None
                or self._path_guard is not None
                or accept_thread_alive
                or peer_thread_alive
            ):
                raise OSError("IPC server is already running")

            ensure_private_directory(self.socket_path.parent)
            guard = _SocketPathGuard(self.socket_path)
            server: socket.socket | None = None
            bound_identity: _Identity | None = None
            try:
                existing = guard.leaf()
                if existing is not None:
                    expected = _identity(guard.socket_leaf())
                    if not _existing_socket_refuses_connections(guard, expected):
                        raise OSError(f"refusing to replace live or unproven socket: {self.socket_path}")
                    guard.unlink_socket(expected)

                guard.assert_absent()
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                bound_identity = _bind_socket_in_guard(server, guard)
                guard.assert_parent()
                guard.chmod_socket(bound_identity, 0o600)
                server.listen(16)
                server.settimeout(SERVER_ACCEPT_TIMEOUT_SECONDS)
                guard.assert_socket_identity(bound_identity)

                self._peer_slots = threading.BoundedSemaphore(MAX_CONCURRENT_PEERS)
                self._connections.clear()
                self._workers.clear()
                self._path_guard = guard
                self._bound_identity = bound_identity
                self.socket = server
                self.running = True
                self.thread = threading.Thread(
                    target=self._serve,
                    name="sidepulse-ipc-accept",
                    daemon=True,
                )
                self.thread.start()
                return self.socket_path
            except Exception:
                if server is not None:
                    server.close()
                if bound_identity is not None:
                    try:
                        guard.unlink_owned_socket(bound_identity)
                    except OSError:
                        pass
                guard.close()
                raise

    def stop(self) -> None:
        with self._lifecycle_lock:
            self.running = False
            server = self.socket
            self.socket = None
            accept_thread = self.thread
            if server is not None:
                try:
                    server.close()
                except OSError:
                    pass
            with self._peer_lock:
                connections = tuple(self._connections)

        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass

        deadline = time.monotonic() + SERVER_STOP_TIMEOUT_SECONDS
        current = threading.current_thread()
        if accept_thread is not None and accept_thread is not current:
            accept_thread.join(max(0.0, deadline - time.monotonic()))

        with self._peer_lock:
            workers = tuple(self._workers)
        for worker in workers:
            if worker is current:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(remaining)

        with self._lifecycle_lock:
            guard = self._path_guard
            expected = self._bound_identity
            self._path_guard = None
            self._bound_identity = None
            if self.thread is not None and not self.thread.is_alive():
                self.thread = None
            if guard is not None:
                if expected is not None:
                    try:
                        guard.unlink_owned_socket(expected)
                    except OSError:
                        pass
                guard.close()

    def _serve(self) -> None:
        while True:
            with self._lifecycle_lock:
                server = self.socket
                if not self.running or server is None:
                    return
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return

            with self._lifecycle_lock:
                if not self.running or self.socket is not server:
                    connection.close()
                    return
                if not self._peer_slots.acquire(blocking=False):
                    connection.close()
                    continue
                worker = threading.Thread(
                    target=self._serve_peer,
                    args=(connection,),
                    name="sidepulse-ipc-peer",
                    daemon=True,
                )
                with self._peer_lock:
                    self._connections.add(connection)
                    self._workers.add(worker)
                worker.start()

    def _serve_peer(self, connection: socket.socket) -> None:
        try:
            with connection:
                self._handle_connection(connection)
        finally:
            with self._peer_lock:
                self._connections.discard(connection)
                self._workers.discard(threading.current_thread())
            self._peer_slots.release()

    def _handle_connection(self, connection: socket.socket) -> None:
        try:
            connection.settimeout(PEER_READ_TIMEOUT_SECONDS)
        except OSError:
            return
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = connection.recv(65536)
            except (TimeoutError, OSError):
                return
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_HINT_BYTES:
                return
            chunks.append(chunk)

        if not _same_uid_peer(connection, self.peer_uid_reader):
            return

        hint = _hint_from_wire(b"".join(chunks))
        if self.running and hint is not None:
            self.on_hint(hint)
            return
        raw_event = _raw_event_from_wire(b"".join(chunks))
        if self.running and raw_event is not None:
            provider, line = raw_event
            if provider not in {"codex", "claude", "devin", "grok", "cursor", "hermes", "openclaw"}:
                try:
                    connection.sendall(_RAW_EVENT_REJECT)
                except OSError:
                    pass
                return
            # A pre-hint hook. Its payload is not the authority (the
            # registered log is), so we cannot ingest it directly -- but
            # we can wake the app AND make the skew visible instead of
            # dropping the event into silence.
            notify = self.on_legacy_hook
            if notify is not None:
                try:
                    notify(provider)
                except Exception:
                    pass
            try:
                connection.sendall(_RAW_EVENT_REJECT)
            except OSError:
                pass
