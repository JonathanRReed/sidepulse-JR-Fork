from __future__ import annotations

import io
import json
import socket
import stat
from pathlib import Path

import pytest

from sidepulse import hook_client
from sidepulse.hook_ingress_protocol import (
    HOOK_INGRESS_SOCKET_NAME,
    MAX_HOOK_INGRESS_PAYLOAD_BYTES,
    HookIngressDisposition,
    HookIngressRequest,
    candidate_hook_ingress_socket_paths,
    decode_hook_ingress_request,
    decode_hook_ingress_response,
    encode_hook_ingress_request,
    encode_hook_ingress_response,
    submit_hook_ingress,
)


class _FakeSocket:
    def __init__(self, response: bytes | BaseException) -> None:
        self.response = response
        self.timeout: float | None = None
        self.connected: str | None = None
        self.sent = b""
        self.shutdown_how: int | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, path: str) -> None:
        self.connected = path
        if isinstance(self.response, BaseException):
            raise self.response

    def sendall(self, payload: bytes) -> None:
        self.sent += payload

    def shutdown(self, how: int) -> None:
        self.shutdown_how = how

    def recv(self, _maximum: int) -> bytes:
        assert isinstance(self.response, bytes)
        return self.response

    def close(self) -> None:
        self.closed = True


class _ReceiveFailureSocket(_FakeSocket):
    def __init__(self) -> None:
        super().__init__(b"")

    def recv(self, _maximum: int) -> bytes:
        raise TimeoutError("acknowledgement delayed")


def _request(payload: str = "{}") -> HookIngressRequest:
    return HookIngressRequest("claude", "/tmp/state/claude.jsonl", payload)


def test_request_repr_never_contains_payload() -> None:
    request = _request('{"prompt":"private body"}')

    assert "private body" not in repr(request)


@pytest.mark.parametrize(
    ("provider", "log_path", "payload"),
    [
        ("unknown", "/tmp/state/unknown.jsonl", "{}"),
        ("claude", "relative.jsonl", "{}"),
        ("claude", "/tmp/state/claude\x00.jsonl", "{}"),
        ("claude", "/tmp/state/claude.jsonl", "x" * (MAX_HOOK_INGRESS_PAYLOAD_BYTES + 1)),
    ],
)
def test_request_rejects_invalid_outer_values(
    provider: str,
    log_path: str,
    payload: str,
) -> None:
    with pytest.raises(ValueError, match="invalid hook ingress request"):
        HookIngressRequest(provider, log_path, payload)


def test_protocol_round_trip_preserves_escaped_json_without_outer_copy() -> None:
    payload = '{"tool_input":{"command":"printf \\\"a\\\\nb\\\""}}\n'
    request = _request(payload)

    encoded = encode_hook_ingress_request(request)
    decoded = decode_hook_ingress_request(encoded)

    assert decoded == request
    assert decoded is not request


def test_protocol_rejects_truncated_duplicate_and_unknown_headers() -> None:
    encoded = encode_hook_ingress_request(_request())
    magic_size = encoded.index(b"{")
    header_end = encoded.index(b"}", magic_size) + 1
    header = json.loads(encoded[magic_size:header_end])

    assert decode_hook_ingress_request(encoded[:-1]) is None
    duplicate = encoded[:magic_size] + b'{"log_path":"/tmp/a","log_path":"/tmp/b","provider":"claude","version":1}' + encoded[header_end:]
    assert decode_hook_ingress_request(duplicate) is None
    header["extra"] = True
    changed = json.dumps(header, separators=(",", ":")).encode()
    unknown = encoded[:magic_size] + changed + encoded[header_end:]
    assert decode_hook_ingress_request(unknown) is None


@pytest.mark.parametrize(
    "disposition",
    [
        HookIngressDisposition.ACCEPTED,
        HookIngressDisposition.REFUSED_FULL,
        HookIngressDisposition.REFUSED_CLOSED,
        HookIngressDisposition.REFUSED_INVALID,
    ],
)
def test_response_tokens_are_exact_and_round_trip(
    disposition: HookIngressDisposition,
) -> None:
    encoded = encode_hook_ingress_response(disposition)
    assert encoded.endswith(b"\n")
    assert decode_hook_ingress_response(encoded) is disposition
    assert decode_hook_ingress_response(encoded + b"extra") is HookIngressDisposition.UNAVAILABLE


def test_candidate_socket_paths_try_xdg_then_standard_without_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    home.mkdir()
    xdg.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    assert candidate_hook_ingress_socket_paths() == (
        xdg / "sidepulse" / "agent-monitor" / HOOK_INGRESS_SOCKET_NAME,
        home / ".local" / "state" / "sidepulse" / "agent-monitor" / HOOK_INGRESS_SOCKET_NAME,
    )

    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    assert candidate_hook_ingress_socket_paths() == (
        home / ".local" / "state" / "sidepulse" / "agent-monitor" / HOOK_INGRESS_SOCKET_NAME,
    )


def test_submit_uses_one_tight_timeout_and_stops_after_explicit_response(
    tmp_path: Path,
) -> None:
    target = tmp_path / HOOK_INGRESS_SOCKET_NAME
    target.touch(mode=0o600)
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    fake = _FakeSocket(encode_hook_ingress_response(HookIngressDisposition.ACCEPTED))

    result = submit_hook_ingress(
        _request(),
        socket_path=target,
        timeout_seconds=0.03,
        socket_factory=lambda *_args: fake,
        require_socket_leaf=False,
    )

    assert result is HookIngressDisposition.ACCEPTED
    assert fake.timeout == 0.03
    assert fake.connected == str(target)
    assert fake.shutdown_how == socket.SHUT_WR
    assert decode_hook_ingress_request(fake.sent) == _request()
    assert fake.closed


def test_submit_returns_unavailable_without_leaking_exception_text(tmp_path: Path) -> None:
    target = tmp_path / HOOK_INGRESS_SOCKET_NAME
    fake = _FakeSocket(OSError("private path detail"))

    result = submit_hook_ingress(
        _request(),
        socket_path=target,
        socket_factory=lambda *_args: fake,
        require_socket_leaf=False,
    )

    assert result is HookIngressDisposition.UNAVAILABLE
    assert fake.closed


def test_submit_does_not_fallback_after_connected_submission_loses_ack(
    tmp_path: Path,
) -> None:
    target = tmp_path / HOOK_INGRESS_SOCKET_NAME
    fake = _ReceiveFailureSocket()

    disposition = submit_hook_ingress(
        _request(),
        socket_path=target,
        socket_factory=lambda *_args: fake,
        require_socket_leaf=False,
    )
    fallback: list[object] = []
    result = hook_client.run_hook_client(
        "claude",
        Path("/tmp/state/claude.jsonl"),
        "{}",
        submit=lambda _request_value: disposition,
        fallback=lambda *_args: fallback.append(object()),
    )

    assert disposition is HookIngressDisposition.SUBMISSION_AMBIGUOUS
    assert result == 0
    assert fallback == []
    assert fake.sent
    assert fake.closed


def test_client_falls_back_only_when_ingress_is_unavailable() -> None:
    fallback: list[tuple[str, Path, str]] = []

    assert (
        hook_client.run_hook_client(
            "claude",
            Path("/tmp/state/claude.jsonl"),
            '{"hook_event_name":"Stop"}',
            submit=lambda _request_value: HookIngressDisposition.UNAVAILABLE,
            fallback=lambda provider, path, payload: fallback.append((provider, path, payload)),
        )
        == 0
    )

    assert fallback == [
        ("claude", Path("/tmp/state/claude.jsonl"), '{"hook_event_name":"Stop"}')
    ]


def test_client_rejects_oversized_payload_without_fallback() -> None:
    fallback: list[object] = []

    result = hook_client.run_hook_client(
        "claude",
        Path("/tmp/state/claude.jsonl"),
        "x" * (MAX_HOOK_INGRESS_PAYLOAD_BYTES + 1),
        submit=lambda _request_value: pytest.fail("invalid request reached ingress"),
        fallback=lambda *_args: fallback.append(object()),
    )

    assert result == 0
    assert fallback == []


@pytest.mark.parametrize(
    "disposition",
    [
        HookIngressDisposition.ACCEPTED,
        HookIngressDisposition.REFUSED_FULL,
        HookIngressDisposition.REFUSED_CLOSED,
        HookIngressDisposition.REFUSED_INVALID,
        HookIngressDisposition.SUBMISSION_AMBIGUOUS,
    ],
)
def test_client_never_retries_an_explicit_admission_outcome_out_of_order(
    disposition: HookIngressDisposition,
) -> None:
    fallback: list[object] = []

    result = hook_client.run_hook_client(
        "claude",
        Path("/tmp/state/claude.jsonl"),
        "{}",
        submit=lambda _request_value: disposition,
        fallback=lambda *_args: fallback.append(object()),
    )

    assert result == 0
    assert fallback == []


def test_main_reads_stdin_once_and_cursor_always_returns_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Buffer(io.BytesIO):
        reads = 0

        def read(self, *args, **kwargs):
            self.reads += 1
            return super().read(*args, **kwargs)

    class _Input:
        def __init__(self, value: bytes) -> None:
            self.buffer = _Buffer(value)

    source = _Input(b'{"hook_event_name":"stop"}')
    output = io.StringIO()
    seen: list[str] = []
    monkeypatch.setattr(hook_client.sys, "stdin", source)
    monkeypatch.setattr(hook_client.sys, "stdout", output)
    monkeypatch.setattr(
        hook_client,
        "run_hook_client",
        lambda provider, path, payload: seen.append(f"{provider}:{path}:{payload}") or 0,
    )

    assert hook_client.main(["--provider", "cursor", "--log", "/tmp/cursor.jsonl"]) == 0

    assert source.buffer.reads == 1
    assert seen == ['cursor:/tmp/cursor.jsonl:{"hook_event_name":"stop"}']
    assert output.getvalue() == "{}\n"


def test_main_bounds_stdin_before_client_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Buffer(io.BytesIO):
        def __init__(self, value: bytes) -> None:
            super().__init__(value)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1, *args, **kwargs):
            self.read_sizes.append(size)
            return super().read(size, *args, **kwargs)

    class _Input:
        def __init__(self, value: bytes) -> None:
            self.buffer = _Buffer(value)

    source = _Input(b"x" * (MAX_HOOK_INGRESS_PAYLOAD_BYTES + 2))
    monkeypatch.setattr(hook_client.sys, "stdin", source)
    monkeypatch.setattr(
        hook_client,
        "run_hook_client",
        lambda *_args: pytest.fail("oversized stdin reached client admission"),
    )

    assert hook_client.hook_client_main("claude", Path("/tmp/claude.jsonl")) == 0

    assert source.buffer.read_sizes == [MAX_HOOK_INGRESS_PAYLOAD_BYTES + 1]


def test_main_caps_multibyte_stdin_by_encoded_bytes_before_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Buffer(io.BytesIO):
        def __init__(self, value: bytes) -> None:
            super().__init__(value)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1, *args, **kwargs):
            self.read_sizes.append(size)
            return super().read(size, *args, **kwargs)

    class _Input:
        def __init__(self, value: bytes) -> None:
            self.buffer = _Buffer(value)

    source = _Input("é".encode() * MAX_HOOK_INGRESS_PAYLOAD_BYTES)
    monkeypatch.setattr(hook_client.sys, "stdin", source)
    monkeypatch.setattr(
        hook_client,
        "run_hook_client",
        lambda *_args: pytest.fail("multibyte oversized stdin reached client admission"),
    )

    assert hook_client.hook_client_main("claude", Path("/tmp/claude.jsonl")) == 0

    assert source.buffer.read_sizes == [MAX_HOOK_INGRESS_PAYLOAD_BYTES + 1]


def test_main_rejects_invalid_utf8_before_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Input:
        buffer = io.BytesIO(b"\xff")

    monkeypatch.setattr(hook_client.sys, "stdin", _Input())
    monkeypatch.setattr(
        hook_client,
        "run_hook_client",
        lambda *_args: pytest.fail("invalid UTF-8 reached client admission"),
    )

    assert hook_client.hook_client_main("claude", Path("/tmp/claude.jsonl")) == 0
