"""A private address must not turn bearer authentication into plaintext."""

from __future__ import annotations

import socket
import ssl
import subprocess
import threading
import urllib.error
import urllib.request
from contextlib import ExitStack

import pytest

from sidepulse import glance_server


def test_glance_refuses_missing_tls_before_opening_a_listener(monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(
        glance_server,
        "_GlanceHTTPServer",
        lambda *args: opened.append(args),
    )

    with pytest.raises(ValueError, match="TLS"):
        glance_server.create_glance_server(
            bind_address="192.168.1.20",
            glance_secret=b"synthetic-signing-secret",
            access_token=b"synthetic-independent-access-token",
        )

    assert opened == []


def test_glance_rejects_unloadable_tls_before_opening_a_listener(
    tmp_path, monkeypatch
) -> None:
    opened = []
    monkeypatch.setattr(
        glance_server,
        "_GlanceHTTPServer",
        lambda *args: opened.append(args),
    )

    with pytest.raises(OSError):
        glance_server.create_glance_server(
            bind_address="192.168.1.20",
            glance_secret=b"synthetic-signing-secret",
            access_token=b"synthetic-independent-access-token",
            tls_cert=tmp_path / "missing-certificate.pem",
            tls_key=tmp_path / "missing-key.pem",
        )

    assert opened == []


@pytest.mark.parametrize("bind_address", ["192.168.1.21", "fc00::2", "fe80::2%en0"])
def test_glance_rejects_certificate_for_another_ip_before_binding(
    monkeypatch, glance_tls_material, bind_address
) -> None:
    opened = []

    def refuse_open(*args):
        opened.append(args)
        raise AssertionError("listener opened before checking its TLS identity")

    monkeypatch.setattr(glance_server, "_GlanceHTTPServer", refuse_open)
    monkeypatch.setattr(glance_server, "_GlanceHTTPServerV6", refuse_open)
    certificate, private_key = glance_tls_material
    with pytest.raises(ssl.SSLCertVerificationError):
        glance_server.create_glance_server(
            bind_address=bind_address,
            glance_secret=b"synthetic-signing-secret",
            access_token=b"synthetic-independent-access-token",
            tls_cert=certificate,
            tls_key=private_key,
        )
    assert opened == []


@pytest.mark.parametrize("bind_address", ["192.168.1.20", "fc00::1", "fe80::1%en0"])
def test_tls_identity_accepts_matching_ip_san_without_treating_scope_as_identity(
    monkeypatch, glance_tls_material, bind_address
) -> None:
    class LoopbackTestServer(glance_server._GlanceHTTPServer):
        def __init__(self, _address, handler, configuration):
            super().__init__(("127.0.0.1", 0), handler, configuration)

    monkeypatch.setattr(glance_server, "_GlanceHTTPServer", LoopbackTestServer)
    monkeypatch.setattr(glance_server, "_GlanceHTTPServerV6", LoopbackTestServer)
    certificate, private_key = glance_tls_material
    server = glance_server.create_glance_server(
        bind_address=bind_address,
        glance_secret=b"synthetic-signing-secret",
        access_token=b"synthetic-independent-access-token",
        tls_cert=certificate,
        tls_key=private_key,
    )
    try:
        assert server.glance_configuration.bind_address == bind_address
        assert isinstance(server.socket, ssl.SSLSocket)
    finally:
        server.server_close()


@pytest.fixture
def secure_glance_listener(tmp_path, monkeypatch, glance_tls_material):
    class LoopbackTestServer(glance_server._GlanceHTTPServer):
        def __init__(self, _address, handler, configuration):
            super().__init__(("127.0.0.1", 0), handler, configuration)

    monkeypatch.setattr(glance_server, "_GlanceHTTPServer", LoopbackTestServer)
    certificate, private_key = glance_tls_material
    server = glance_server.create_glance_server(
        bind_address="192.168.1.20",
        home=tmp_path,
        glance_secret=b"synthetic-signing-secret",
        access_token=b"synthetic-independent-access-token",
        tls_cert=certificate,
        tls_key=private_key,
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server, ssl.create_default_context(cafile=certificate)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_tls_listener_requires_bearer_and_client_trust(secure_glance_listener) -> None:
    server, trust = secure_glance_listener
    url = f"https://127.0.0.1:{server.server_address[1]}/glance.json"
    for token in (None, "wrong-token"):
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), context=trust, timeout=2
            )
        assert denied.value.code == 401

    authorized = urllib.request.Request(
        url, headers={"Authorization": "Bearer synthetic-independent-access-token"}
    )
    with urllib.request.urlopen(authorized, context=trust, timeout=2) as response:
        assert response.status == 200
    with pytest.raises(urllib.error.URLError) as untrusted:
        urllib.request.urlopen(authorized, timeout=2)
    assert isinstance(untrusted.value.reason, ssl.SSLCertVerificationError)


def test_bind_identity_accepts_a_private_ca_issued_server_leaf(
    tmp_path, glance_tls_material
) -> None:
    ca_certificate, ca_key = glance_tls_material
    key = tmp_path / "server.key"
    request = tmp_path / "server.csr"
    certificate = tmp_path / "server.crt"
    extensions = tmp_path / "server.ext"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=IP:192.168.1.20\n",
        encoding="ascii",
    )
    for arguments in (
        [
            "req", "-new", "-newkey", "rsa:2048", "-nodes", "-batch",
            "-subj", "/CN=synthetic-server", "-keyout", str(key),
            "-out", str(request),
        ],
        [
            "x509", "-req", "-in", str(request), "-CA", str(ca_certificate),
            "-CAkey", str(ca_key), "-set_serial", "1", "-days", "1",
            "-extfile", str(extensions), "-out", str(certificate),
        ],
    ):
        subprocess.run(
            ["/usr/bin/openssl", *arguments],
            check=True, capture_output=True, timeout=20,
        )
    key.chmod(0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key, password="")

    # This is only the startup identity check. A real client must separately
    # trust the issuing CA; no system trust settings are changed here.
    glance_server._verify_tls_bind_identity(context, certificate, "192.168.1.20")


def test_plaintext_and_abandoned_handshake_cannot_block_tls_requests(
    secure_glance_listener,
) -> None:
    server, trust = secure_glance_listener
    address = server.server_address
    # Keep one TCP peer silent while a separate, valid TLS request completes.
    with socket.create_connection(address, timeout=2) as stalled:
        request = urllib.request.Request(
            f"https://127.0.0.1:{address[1]}/glance.json",
            headers={"Authorization": "Bearer synthetic-independent-access-token"},
        )
        with urllib.request.urlopen(request, context=trust, timeout=2) as response:
            assert response.status == 200
        stalled.sendall(
            b"GET /glance.json HTTP/1.0\r\n"
            b"Authorization: Bearer synthetic-independent-access-token\r\n\r\n"
        )
        try:
            response = stalled.recv(8192)
        except ConnectionResetError:
            response = b""
        assert b"200 OK" not in response
        assert b"signed_body" not in response


@pytest.mark.parametrize("token", [b"a" * 24 + b"\n", b" " * 24, b"a" * 24 + b"\xff"])
def test_glance_access_token_must_be_safe_for_one_http_header(token) -> None:
    with pytest.raises(ValueError, match="access token"):
        glance_server.GlanceServerConfiguration(
            bind_address="192.168.1.20",
            glance_secret=b"synthetic-signing-secret",
            access_token=token,
        )


def test_stalled_connections_are_capped_and_capacity_recovers(
    secure_glance_listener, monkeypatch,
) -> None:
    server, trust = secure_glance_listener
    limit = getattr(glance_server, "_MAX_CONCURRENT_CONNECTIONS", 8)
    changed = threading.Condition()
    active = 0
    finished = 0
    original = server.process_request_thread

    def observed_request(request, address):
        nonlocal active, finished
        with changed:
            active += 1
            changed.notify_all()
        try:
            original(request, address)
        finally:
            with changed:
                active -= 1
                finished += 1
                changed.notify_all()

    monkeypatch.setattr(server, "process_request_thread", observed_request)
    with ExitStack() as peers:
        for _ in range(limit):
            peers.enter_context(socket.create_connection(server.server_address, timeout=2))
        with changed:
            assert changed.wait_for(lambda: active == limit, timeout=2)
        with socket.create_connection(server.server_address, timeout=1) as excess:
            try:
                closed = excess.recv(1) == b""
            except ConnectionResetError:
                closed = True
            except TimeoutError:
                closed = False
            assert closed, "excess unauthenticated connection was admitted"
        with changed:
            assert active == limit

    with changed:
        assert changed.wait_for(lambda: finished == limit, timeout=2)
    request = urllib.request.Request(
        f"https://127.0.0.1:{server.server_address[1]}/glance.json",
        headers={"Authorization": "Bearer synthetic-independent-access-token"},
    )
    with urllib.request.urlopen(request, context=trust, timeout=2) as response:
        assert response.status == 200


def test_failed_worker_start_does_not_leak_connection_capacity(
    secure_glance_listener, monkeypatch,
) -> None:
    server, trust = secure_glance_listener
    attempts = 0

    def failed_start(_thread):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("synthetic thread-start failure")

    with monkeypatch.context() as failure:
        failure.setattr(threading.Thread, "start", failed_start)
        failure.setattr(server, "handle_error", lambda *_args: None)
        for _ in range(glance_server._MAX_CONCURRENT_CONNECTIONS + 2):
            with socket.create_connection(server.server_address, timeout=2) as peer:
                try:
                    assert peer.recv(1) == b""
                except ConnectionResetError:
                    pass
        assert attempts == glance_server._MAX_CONCURRENT_CONNECTIONS + 2

    request = urllib.request.Request(
        f"https://127.0.0.1:{server.server_address[1]}/glance.json",
        headers={"Authorization": "Bearer synthetic-independent-access-token"},
    )
    with urllib.request.urlopen(request, context=trust, timeout=2) as response:
        assert response.status == 200


def test_failed_requests_do_not_emit_unbounded_tracebacks(
    secure_glance_listener, capsys,
) -> None:
    server, _trust = secure_glance_listener
    try:
        raise ValueError("synthetic untrusted request detail")
    except ValueError:
        server.handle_error(None, ("127.0.0.1", 1))
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""
