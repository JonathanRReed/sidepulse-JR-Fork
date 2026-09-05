"""Private-network, glance-only HTTPS listener.

The general status server remains loopback-only. This listener is deliberately
separate and exposes only the signed ``/glance.json`` projection on an
explicit private or link-local IP literal.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import socket
import ssl
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .phone_glance import (
    PhoneGlancePolicy,
    PhoneGlanceRefused,
    encode_phone_glance,
)
from .product_identity import PRODUCT_DISPLAY_NAME

GLANCE_DEFAULT_PORT = 8738
_MAX_CONCURRENT_CONNECTIONS = 8
_MAX_SECRET_BYTES = 4_096
_MAX_ACCESS_TOKEN_BYTES = 4_096
_MAX_SEQUENCE = (1 << 63) - 1
_INSTANCE_ID = re.compile(r"[0-9a-f]{32}\Z")
_SCOPE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}\Z")
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)
_LINK_LOCAL_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)


class PrivateGlanceBindRefused(ValueError):
    """The requested listener address is not an allowed private IP literal."""


def validate_bind_address(value: str) -> str:
    """Return an allowed IP literal, refusing names, wildcards, and public IPs."""
    if type(value) is not str or not value:
        raise PrivateGlanceBindRefused("explicit private IP literal required")
    address_text = value
    scope_id: str | None = None
    if "%" in value:
        if value.count("%") != 1:
            raise PrivateGlanceBindRefused("invalid IPv6 scope")
        address_text, scope_id = value.split("%", 1)
        if (
            not scope_id
            or scope_id.startswith("25")
            or _SCOPE_ID.fullmatch(scope_id) is None
        ):
            raise PrivateGlanceBindRefused("invalid IPv6 scope")
    try:
        address = ipaddress.ip_address(address_text)
    except ValueError as exc:
        raise PrivateGlanceBindRefused("explicit private IP literal required") from exc
    if scope_id is not None and (
        not isinstance(address, ipaddress.IPv6Address) or not address.is_link_local
    ):
        raise PrivateGlanceBindRefused("scope requires an IPv6 link-local address")
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise PrivateGlanceBindRefused("private or link-local IP literal required")
    networks = _PRIVATE_NETWORKS + _LINK_LOCAL_NETWORKS
    if not any(address in network for network in networks):
        raise PrivateGlanceBindRefused("private or link-local IP literal required")
    normalized = str(address)
    return f"{normalized}%{scope_id}" if scope_id is not None else normalized


@dataclass(frozen=True, slots=True, repr=False)
class GlanceServerConfiguration:
    bind_address: str
    home: Path | None = None
    glance_secret: bytes = field(repr=False, default=b"")
    access_token: bytes = field(repr=False, default=b"")
    glance_source_id: str = "sidepulse"
    glance_instance_id: str = field(default_factory=lambda: secrets.token_hex(16))

    def __post_init__(self) -> None:
        normalized = validate_bind_address(self.bind_address)
        object.__setattr__(self, "bind_address", normalized)
        if self.home is not None and not isinstance(self.home, Path):
            raise ValueError("invalid glance home")
        if (
            type(self.glance_secret) is not bytes
            or not self.glance_secret
            or len(self.glance_secret) > _MAX_SECRET_BYTES
        ):
            raise ValueError("invalid glance secret")
        if (
            type(self.access_token) is not bytes
            or not 24 <= len(self.access_token) <= _MAX_ACCESS_TOKEN_BYTES
            or any(byte < 33 or byte > 126 for byte in self.access_token)
        ):
            raise ValueError("invalid glance access token")
        if hmac.compare_digest(self.glance_secret, self.access_token):
            raise ValueError("glance access token and signing secret must be distinct")
        PhoneGlancePolicy(self.glance_source_id)
        if (
            type(self.glance_instance_id) is not str
            or _INSTANCE_ID.fullmatch(self.glance_instance_id) is None
        ):
            raise ValueError("invalid glance instance id")
        PhoneGlancePolicy(self.effective_source_id)

    @property
    def effective_source_id(self) -> str:
        suffix = f":{self.glance_instance_id}"
        return f"{self.glance_source_id[: 128 - len(suffix)]}{suffix}"


class _GlanceSequence:
    def __init__(self, *, start: int) -> None:
        if type(start) is not int or not 0 <= start < _MAX_SEQUENCE:
            raise ValueError("invalid glance sequence start")
        self._value = start
        self._lock = threading.Lock()

    def next(self) -> int | None:
        with self._lock:
            if self._value >= _MAX_SEQUENCE:
                return None
            self._value += 1
            return self._value


class _GlanceOnlyHandler(BaseHTTPRequestHandler):
    server_version = PRODUCT_DISPLAY_NAME

    def do_GET(self) -> None:
        if self.path != "/glance.json":
            self._send_not_found()
            return
        configuration = getattr(self.server, "glance_configuration", None)
        sequence = getattr(self.server, "glance_sequence", None)
        if not isinstance(configuration, GlanceServerConfiguration) or not isinstance(
            sequence, _GlanceSequence
        ):
            self._send_failure(503)
            return
        if not self._authenticated(configuration.access_token):
            self._send_authentication_required()
            return
        next_sequence = sequence.next()
        if next_sequence is None:
            self._send_failure(503)
            return
        try:
            from .serve import build_phone_glance_projection

            policy = PhoneGlancePolicy(configuration.effective_source_id)
            envelope = build_phone_glance_projection(
                policy,
                signer=lambda payload: hmac.new(
                    configuration.glance_secret, payload, hashlib.sha256
                ).hexdigest(),
                sequence=next_sequence,
                home=configuration.home,
            )
            payload = encode_phone_glance(envelope, max_bytes=policy.max_bytes)
        except (PhoneGlanceRefused, ValueError, OSError):
            self._send_failure(503)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_not_found(self) -> None:
        self._send_failure(404)

    def _send_failure(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authenticated(self, expected: bytes) -> bool:
        supplied = self.headers.get("Authorization")
        if not isinstance(supplied, str) or not supplied.startswith("Bearer "):
            return False
        try:
            candidate = supplied[7:].encode("ascii")
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(candidate, expected)

    def _send_authentication_required(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", "Bearer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_HEAD = _send_not_found
    do_POST = _send_not_found
    do_PUT = _send_not_found
    do_DELETE = _send_not_found
    do_OPTIONS = _send_not_found
    do_PATCH = _send_not_found

    def log_message(self, *_args) -> None:
        """Quiet by design; the glance client polls this endpoint."""


class _GlanceHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address, handler, configuration: GlanceServerConfiguration) -> None:
        self.glance_configuration = configuration
        self.glance_sequence = _GlanceSequence(start=0)
        self._slots = threading.BoundedSemaphore(_MAX_CONCURRENT_CONNECTIONS)
        super().__init__(address, handler, bind_and_activate=False)

    def process_request(self, request, client_address) -> None:
        # Admission precedes TLS and HTTP reads, including authentication.
        if not self._slots.acquire(blocking=False):
            try:
                request.close()
            except OSError:
                pass
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            # Socket cleanup can run more than once after a dispatch error.
            # Only the admitted worker owns this release.
            self._slots.release()

    def get_request(self):
        connection, address = super().get_request()
        # TLS handshakes run on the request thread, not the accept loop.
        connection.settimeout(5.0)
        return connection, address

    def handle_error(self, request, client_address) -> None:
        """Malformed or failed connections must not amplify request details into logs."""


class _GlanceHTTPServerV6(_GlanceHTTPServer):
    address_family = socket.AF_INET6


def _verify_tls_bind_identity(
    context: ssl.SSLContext, certificate: Path, bind_address: str
) -> None:
    """Check the loaded identity against the IP without opening a socket.

    The self-check trusts the supplied chain only in memory. Real clients still
    need their own trusted CA. Scope IDs route IPv6, but are not part of an IP SAN.
    """
    verifier = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    verifier.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    verifier.load_verify_locations(cafile=certificate)
    server_input, server_output = ssl.MemoryBIO(), ssl.MemoryBIO()
    client_input, client_output = ssl.MemoryBIO(), ssl.MemoryBIO()
    server = context.wrap_bio(server_input, server_output, server_side=True)
    client = verifier.wrap_bio(
        client_input, client_output, server_hostname=bind_address.split("%", 1)[0]
    )
    while True:
        try:
            client.do_handshake()
            return
        except ssl.SSLWantReadError:
            server_input.write(client_output.read())
        try:
            server.do_handshake()
        except ssl.SSLWantReadError:
            pass
        response = server_output.read()
        if not response:
            raise ValueError("TLS identity self-check made no progress")
        client_input.write(response)


def create_glance_server(
    *,
    bind_address: str,
    port: int = GLANCE_DEFAULT_PORT,
    home: Path | None = None,
    glance_secret: bytes,
    access_token: bytes,
    glance_source_id: str = "sidepulse",
    tls_cert: Path | None = None,
    tls_key: Path | None = None,
) -> ThreadingHTTPServer:
    """Require a usable TLS identity before binding the private listener."""
    configuration = GlanceServerConfiguration(
        bind_address=bind_address,
        home=home,
        glance_secret=glance_secret,
        access_token=access_token,
        glance_source_id=glance_source_id,
    )
    if tls_cert is None or tls_key is None:
        raise ValueError("TLS certificate and private key are required")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # An encrypted key must fail without an interactive password prompt.
    context.load_cert_chain(tls_cert, tls_key, password="")
    _verify_tls_bind_identity(context, tls_cert, configuration.bind_address)
    server_class = _GlanceHTTPServerV6 if ":" in configuration.bind_address else _GlanceHTTPServer
    server = server_class(
        (configuration.bind_address, int(port)), _GlanceOnlyHandler, configuration
    )
    try:
        server.socket = context.wrap_socket(
            server.socket, server_side=True, do_handshake_on_connect=False
        )
        server.server_bind()
        server.server_activate()
    except BaseException:
        server.server_close()
        raise
    return server


def glance_serve(
    *,
    bind_address: str,
    port: int = GLANCE_DEFAULT_PORT,
    glance_secret: bytes,
    access_token: bytes,
    glance_source_id: str = "sidepulse",
    tls_cert: Path | None = None,
    tls_key: Path | None = None,
) -> None:
    """Run the private glance listener until interrupted."""
    server = create_glance_server(
        bind_address=bind_address,
        port=port,
        glance_secret=glance_secret,
        access_token=access_token,
        glance_source_id=glance_source_id,
        tls_cert=tls_cert,
        tls_key=tls_key,
    )
    display_host = f"[{bind_address}]" if ":" in bind_address else bind_address
    print(f"sidepulse glance: https://{display_host}:{int(port)}/glance.json")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = [
    "GLANCE_DEFAULT_PORT",
    "GlanceServerConfiguration",
    "PrivateGlanceBindRefused",
    "create_glance_server",
    "glance_serve",
    "validate_bind_address",
]
