"""Privacy-minimizing, SSRF-resistant webhook delivery."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import ssl
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping
from urllib.parse import SplitResult, urlsplit

MAX_WEBHOOK_PAYLOAD_BYTES = 8 * 1024
MAX_WEBHOOK_RESPONSE_BYTES = 4 * 1024
MAX_WEBHOOK_HEADER_BYTES = 16 * 1024
MAX_WEBHOOK_ADDRESSES = 8
WEBHOOK_TIMEOUT_SECONDS = 8.0
ALLOWED_WEBHOOK_PORTS = frozenset({443, 8443})
_EVENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SAFE_STRING_FIELDS = frozenset({"event", "headline", "status", "kind"})
_SAFE_NUMBER_FIELDS = frozenset(
    {
        "stage",
        "ask_count",
        "oldest_ask_seconds",
        "completed_count",
        "duration_seconds",
        "minutes",
    }
)


class WebhookReason(str, Enum):
    DELIVERED = "delivered"
    INVALID_URL = "invalid_url"
    INSECURE_SCHEME = "insecure_scheme"
    FORBIDDEN_DESTINATION = "forbidden_destination"
    RESOLUTION_FAILED = "resolution_failed"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    CONNECTION_FAILED = "connection_failed"
    TLS_FAILED = "tls_failed"
    RESPONSE_TOO_LARGE = "response_too_large"
    REDIRECT_REFUSED = "redirect_refused"
    REMOTE_REJECTED = "remote_rejected"
    MALFORMED_RESPONSE = "malformed_response"


@dataclass(frozen=True, slots=True)
class WebhookEndpoint:
    url: str
    host: str
    port: int
    request_target: str
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WebhookResult:
    reason: WebhookReason
    status: int | None = None

    @property
    def delivered(self) -> bool:
        return self.reason is WebhookReason.DELIVERED


class WebhookValidationError(ValueError):
    def __init__(self, reason: WebhookReason):
        self.reason = reason
        super().__init__(reason.value)


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return bool(address.is_global)


def _parse_endpoint(url: str) -> SplitResult:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise WebhookValidationError(WebhookReason.INVALID_URL) from exc
    if parsed.scheme.lower() != "https":
        raise WebhookValidationError(WebhookReason.INSECURE_SCHEME)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise WebhookValidationError(WebhookReason.INVALID_URL)
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise WebhookValidationError(WebhookReason.INVALID_URL) from exc
    if port not in ALLOWED_WEBHOOK_PORTS:
        raise WebhookValidationError(WebhookReason.FORBIDDEN_DESTINATION)
    return parsed


def validate_webhook_url(
    url: str,
    *,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
) -> WebhookEndpoint:
    text = str(url).strip()
    parsed = _parse_endpoint(text)
    host = parsed.hostname or ""
    port = parsed.port or 443
    try:
        records = resolver(
            host,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except (OSError, socket.gaierror) as exc:
        raise WebhookValidationError(WebhookReason.RESOLUTION_FAILED) from exc
    addresses = []
    for record in records[:MAX_WEBHOOK_ADDRESSES]:
        try:
            address = str(record[4][0])
        except (IndexError, TypeError):
            continue
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise WebhookValidationError(WebhookReason.RESOLUTION_FAILED)
    if not all(_public_address(address) for address in addresses):
        raise WebhookValidationError(WebhookReason.FORBIDDEN_DESTINATION)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    return WebhookEndpoint(text, host, port, target, tuple(addresses))


def sanitize_webhook_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Keep product-owned event facts, never session/provider/user labels."""
    safe: dict[str, object] = {}
    for key, value in payload.items():
        if key in _SAFE_NUMBER_FIELDS:
            if isinstance(value, bool):
                safe[key] = value
            elif isinstance(value, (int, float)):
                safe[key] = value
            continue
        if key not in _SAFE_STRING_FIELDS or not isinstance(value, str):
            continue
        collapsed = " ".join(
            "".join(character for character in value if character.isprintable()).split()
        )
        if not collapsed:
            continue
        if key == "event":
            if _EVENT.fullmatch(collapsed) is None:
                continue
            safe[key] = collapsed
        else:
            safe[key] = collapsed[:96]
    if "event" not in safe:
        safe["event"] = "sidepulse.event"
    return safe


def encode_webhook_payload(payload: Mapping[str, object]) -> bytes:
    encoded = json.dumps(
        sanitize_webhook_payload(payload),
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_WEBHOOK_PAYLOAD_BYTES:
        raise WebhookValidationError(WebhookReason.PAYLOAD_TOO_LARGE)
    return encoded


def _host_header(endpoint: WebhookEndpoint) -> str:
    return endpoint.host if endpoint.port == 443 else f"{endpoint.host}:{endpoint.port}"


def _request_bytes(endpoint: WebhookEndpoint, payload: bytes) -> bytes:
    header = (
        f"POST {endpoint.request_target} HTTP/1.1\r\n"
        f"Host: {_host_header(endpoint)}\r\n"
        "User-Agent: SidePulse/1\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Accept: application/json\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    return header + payload


def _read_response(stream) -> tuple[int, int | None]:
    consumed = 0
    status_line = stream.readline(4097)
    consumed += len(status_line)
    if not status_line or len(status_line) > 4096:
        raise WebhookValidationError(WebhookReason.MALFORMED_RESPONSE)
    try:
        protocol, status_text, _reason = status_line.decode("iso-8859-1").split(" ", 2)
        status = int(status_text)
    except (UnicodeError, ValueError) as exc:
        raise WebhookValidationError(WebhookReason.MALFORMED_RESPONSE) from exc
    if not protocol.startswith("HTTP/") or not 100 <= status <= 599:
        raise WebhookValidationError(WebhookReason.MALFORMED_RESPONSE)

    content_length = None
    while True:
        line = stream.readline(4097)
        consumed += len(line)
        if consumed > MAX_WEBHOOK_HEADER_BYTES or len(line) > 4096:
            raise WebhookValidationError(WebhookReason.RESPONSE_TOO_LARGE)
        if line in {b"\r\n", b"\n", b""}:
            break
        try:
            name, value = line.decode("iso-8859-1").split(":", 1)
        except (UnicodeError, ValueError) as exc:
            raise WebhookValidationError(WebhookReason.MALFORMED_RESPONSE) from exc
        if name.strip().casefold() == "content-length":
            try:
                content_length = int(value.strip())
            except ValueError as exc:
                raise WebhookValidationError(WebhookReason.MALFORMED_RESPONSE) from exc
            if content_length < 0 or content_length > MAX_WEBHOOK_RESPONSE_BYTES:
                raise WebhookValidationError(WebhookReason.RESPONSE_TOO_LARGE)
    return status, content_length


def deliver_webhook(
    endpoint: WebhookEndpoint,
    payload: Mapping[str, object],
    *,
    timeout: float = WEBHOOK_TIMEOUT_SECONDS,
    socket_factory: Callable[..., socket.socket] = socket.create_connection,
    ssl_context: ssl.SSLContext | None = None,
) -> WebhookResult:
    try:
        encoded = encode_webhook_payload(payload)
    except WebhookValidationError as exc:
        return WebhookResult(exc.reason)
    context = ssl_context or ssl.create_default_context()
    last_reason = WebhookReason.CONNECTION_FAILED
    for address in endpoint.addresses:
        raw = None
        secure = None
        try:
            raw = socket_factory((address, endpoint.port), timeout=timeout)
            raw.settimeout(timeout)
            secure = context.wrap_socket(raw, server_hostname=endpoint.host)
            secure.sendall(_request_bytes(endpoint, encoded))
            stream = secure.makefile("rb")
            try:
                status, _content_length = _read_response(stream)
            finally:
                stream.close()
            if 200 <= status < 300:
                return WebhookResult(WebhookReason.DELIVERED, status)
            if 300 <= status < 400:
                return WebhookResult(WebhookReason.REDIRECT_REFUSED, status)
            return WebhookResult(WebhookReason.REMOTE_REJECTED, status)
        except ssl.SSLError:
            last_reason = WebhookReason.TLS_FAILED
        except WebhookValidationError as exc:
            return WebhookResult(exc.reason)
        except (OSError, TimeoutError):
            last_reason = WebhookReason.CONNECTION_FAILED
        finally:
            if secure is not None:
                try:
                    secure.close()
                except OSError:
                    pass
            elif raw is not None:
                try:
                    raw.close()
                except OSError:
                    pass
    return WebhookResult(last_reason)
