"""Private-network, glance-only HTTP listener.

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
_MAX_SECRET_BYTES = 4_096
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
        super().__init__(address, handler)


class _GlanceHTTPServerV6(_GlanceHTTPServer):
    address_family = socket.AF_INET6


def create_glance_server(
    *,
    bind_address: str,
    port: int = GLANCE_DEFAULT_PORT,
    home: Path | None = None,
    glance_secret: bytes,
    glance_source_id: str = "sidepulse",
) -> ThreadingHTTPServer:
    """Create a glance-only listener after validating its exact bind address."""
    configuration = GlanceServerConfiguration(
        bind_address=bind_address,
        home=home,
        glance_secret=glance_secret,
        glance_source_id=glance_source_id,
    )
    server_class = _GlanceHTTPServerV6 if ":" in configuration.bind_address else _GlanceHTTPServer
    return server_class((configuration.bind_address, int(port)), _GlanceOnlyHandler, configuration)


def glance_serve(
    *,
    bind_address: str,
    port: int = GLANCE_DEFAULT_PORT,
    glance_secret: bytes,
    glance_source_id: str = "sidepulse",
) -> None:
    """Run the private glance listener until interrupted."""
    server = create_glance_server(
        bind_address=bind_address,
        port=port,
        glance_secret=glance_secret,
        glance_source_id=glance_source_id,
    )
    display_host = f"[{bind_address}]" if ":" in bind_address else bind_address
    print(f"sidepulse glance: http://{display_host}:{int(port)}/glance.json")
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
