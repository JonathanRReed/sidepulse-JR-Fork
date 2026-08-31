"""Bounded Waybar client for JR Bar's loopback, redacted status API.

The client accepts either the public ``/status.json`` document or the v1
``status.read`` capability envelope. It renders only aggregate fields that are
already part of the redacted contract. Credentials are read from an environment
variable, sent only in an HTTP header, and never included in diagnostics.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any, Final, TextIO
from urllib.parse import urlsplit

from .local_api_contract import CONTRACT_VERSION, MAX_RESPONSE_BYTES
from .serve import SERVE_SCHEMA_VERSION

DEFAULT_WAYBAR_URL: Final = "http://127.0.0.1:8737/status.json"
WAYBAR_TOKEN_ENV: Final = "SIDEPULSE_WAYBAR_TOKEN"
DEFAULT_TIMEOUT_SECONDS: Final = 1.5
MAX_TIMEOUT_SECONDS: Final = 10.0
MAX_BEARER_TOKEN_BYTES: Final = 4_096
MAX_WAYBAR_OUTPUT_BYTES: Final = 4_096
_MAX_WORKS: Final = 1_000
_MAX_PROVIDERS: Final = 32
_LOCAL_API_RESPONSE_FIELDS: Final = frozenset(
    {"version", "capability", "generated_at", "privacy", "data", "error"}
)
_SERVE_DOCUMENT_FIELDS: Final = frozenset(
    {"schema_version", "privacy", "agents", "usage"}
)
_LIFECYCLES: Final = frozenset(
    {"idle", "active", "waiting", "completed", "failed", "unknown"}
)
_NEXT_ACTORS: Final = frozenset({"user", "provider", "none", "unknown"})
_SOURCE_HEALTH: Final = frozenset(
    {
        "healthy",
        "partial",
        "unavailable",
        "auth_required",
        "access_denied",
        "rate_limited",
        "timed_out",
        "unsupported",
    }
)
_SOURCE_FRESHNESS: Final = frozenset(
    {"fresh", "stale", "timing_uncertain", "partial", "unavailable", "restored"}
)
_BEARER_TOKEN: Final = re.compile(r"[A-Za-z0-9._~+/=-]+\Z")


class WaybarClientError(ValueError):
    """The local API response could not be consumed safely."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep bearer credentials on the exact loopback endpoint selected."""

    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _duplicate_key_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WaybarClientError("invalid local API response")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise WaybarClientError("invalid local API response")


def _finite_number(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _counts(value: object, allowed: frozenset[str], work_count: int) -> dict[str, int]:
    if type(value) is not dict:
        raise WaybarClientError("invalid local API response")
    result: dict[str, int] = {}
    for name in allowed:
        count = value.get(name, 0)
        if type(count) is not int or not 0 <= count <= _MAX_WORKS:
            raise WaybarClientError("invalid local API response")
        result[name] = count
    if sum(result.values()) != work_count:
        raise WaybarClientError("invalid local API response")
    return result


def _agent_projection(
    value: object,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]] | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise WaybarClientError("invalid local API response")
    generation = value.get("generation")
    work_count = value.get("work_count")
    timing_uncertain = value.get("timing_uncertain_count")
    if (
        type(generation) is not int
        or generation < 0
        or type(work_count) is not int
        or not 0 <= work_count <= _MAX_WORKS
        or type(timing_uncertain) is not int
        or not 0 <= timing_uncertain <= work_count
    ):
        raise WaybarClientError("invalid local API response")
    lifecycle = _counts(value.get("lifecycle_counts"), _LIFECYCLES, work_count)
    next_actor = _counts(value.get("next_actor_counts"), _NEXT_ACTORS, work_count)
    health = _counts(value.get("source_health_counts"), _SOURCE_HEALTH, work_count)
    _counts(value.get("source_freshness_counts"), _SOURCE_FRESHNESS, work_count)
    return lifecycle, next_actor, health


def _capacity_percentage(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise WaybarClientError("invalid local API response")
    providers = value.get("providers")
    if type(providers) is not list or len(providers) > _MAX_PROVIDERS:
        raise WaybarClientError("invalid local API response")
    remaining: list[float] = []
    for provider in providers:
        if type(provider) is not dict or type(provider.get("quota")) is not dict:
            raise WaybarClientError("invalid local API response")
        raw = provider["quota"].get("remaining_percent")
        if raw is None:
            continue
        if not _finite_number(raw) or float(raw) > 100.0:
            raise WaybarClientError("invalid local API response")
        remaining.append(float(raw))
    if not remaining:
        return None
    return min(100, int(math.floor(min(remaining) + 0.5)))


def unavailable_waybar_document() -> dict[str, object]:
    return {
        "text": "JR unavailable",
        "tooltip": "JR Bar status is unavailable.",
        "class": ["sidepulse", "unavailable"],
    }


def build_waybar_document(status: Mapping[str, object]) -> dict[str, object]:
    """Project a validated redacted status into Waybar's custom-module JSON."""
    if (
        not isinstance(status, Mapping)
        or status.get("schema_version") != SERVE_SCHEMA_VERSION
        or status.get("privacy") != "redacted"
    ):
        raise WaybarClientError("invalid local API response")
    agents = _agent_projection(status.get("agents"))
    if agents is None:
        return unavailable_waybar_document()
    lifecycle, next_actor, health = agents
    active = lifecycle["active"]
    waiting = lifecycle["waiting"]
    failed = lifecycle["failed"]
    needs_user = next_actor["user"]
    degraded = sum(count for name, count in health.items() if name != "healthy")

    if failed:
        state, text = "failed", f"JR failed {failed}"
    elif needs_user:
        state, text = "attention", f"JR attention {needs_user}"
    elif degraded:
        state, text = "degraded", f"JR degraded {degraded}"
    elif active:
        state, text = "working", f"JR working {active}"
    elif waiting:
        state, text = "waiting", f"JR waiting {waiting}"
    elif lifecycle["unknown"]:
        state, text = "unknown", "JR unknown"
    else:
        state, text = "idle", "JR idle"

    percentage = _capacity_percentage(status.get("usage"))
    tooltip_lines = [
        "JR Bar",
        f"Active: {active}",
        f"Waiting: {waiting}",
        f"Needs attention: {needs_user}",
        f"Failed: {failed}",
    ]
    result: dict[str, object] = {
        "text": text,
        "tooltip": "\n".join(tooltip_lines),
        "class": ["sidepulse", state],
    }
    if percentage is not None:
        tooltip_lines.append(f"Capacity remaining: {percentage}%")
        result["tooltip"] = "\n".join(tooltip_lines)
        result["percentage"] = percentage
    return result


def _direct_status_document(document: object) -> dict[str, object]:
    if type(document) is not dict:
        raise WaybarClientError("invalid local API response")
    if set(document) == _LOCAL_API_RESPONSE_FIELDS:
        generated_at = document.get("generated_at")
        data = document.get("data")
        if (
            document.get("version") != CONTRACT_VERSION
            or document.get("capability") != "status.read"
            or document.get("privacy") != "redacted"
            or document.get("error") is not None
            or not _finite_number(generated_at)
            or type(data) is not dict
            or set(data) != {"status"}
        ):
            raise WaybarClientError("invalid local API response")
        document = data["status"]
    if type(document) is not dict or set(document) != _SERVE_DOCUMENT_FIELDS:
        raise WaybarClientError("invalid local API response")
    if (
        type(document.get("schema_version")) is not int
        or document.get("schema_version") != SERVE_SCHEMA_VERSION
        or document.get("privacy") != "redacted"
    ):
        raise WaybarClientError("invalid local API response")
    build_waybar_document(document)
    return document


def decode_status_response(raw: bytes) -> dict[str, object]:
    """Decode only the bounded direct or ``status.read`` response schemas."""
    if type(raw) is not bytes or not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise WaybarClientError("invalid local API response")
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_duplicate_key_guard,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, WaybarClientError):
        raise WaybarClientError("invalid local API response") from None
    return _direct_status_document(document)


def _validated_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        loopback = host is not None and ipaddress.ip_address(host).is_loopback
    except (TypeError, ValueError):
        raise WaybarClientError("invalid local API URL") from None
    if (
        parsed.scheme != "http"
        or not loopback
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/status.json"
        or parsed.query
        or parsed.fragment
    ):
        raise WaybarClientError("invalid local API URL")
    return value


def _validated_timeout(value: float) -> float:
    if (
        type(value) not in {int, float}
        or not math.isfinite(float(value))
        or not 0.05 <= float(value) <= MAX_TIMEOUT_SECONDS
    ):
        raise WaybarClientError("invalid local API timeout")
    return float(value)


def _authorization_header(token: str | None) -> str | None:
    if token is None or token == "":
        return None
    if (
        type(token) is not str
        or not 1 <= len(token.encode("utf-8")) <= MAX_BEARER_TOKEN_BYTES
        or _BEARER_TOKEN.fullmatch(token) is None
    ):
        raise WaybarClientError("invalid local API credential")
    return f"Bearer {token}"


def fetch_status_document(
    url: str = DEFAULT_WAYBAR_URL,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    bearer_token: str | None = None,
) -> dict[str, object]:
    """Fetch one bounded response from an exact loopback status endpoint."""
    endpoint = _validated_url(url)
    request = urllib.request.Request(
        endpoint,
        headers={"Accept": "application/json"},
        method="GET",
    )
    authorization = _authorization_header(bearer_token)
    if authorization is not None:
        request.add_header("Authorization", authorization)
    try:
        with _OPENER.open(request, timeout=_validated_timeout(timeout)) as response:
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                raise WaybarClientError("local API unavailable")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except WaybarClientError:
        raise
    except (OSError, urllib.error.HTTPError, urllib.error.URLError, ValueError):
        raise WaybarClientError("local API unavailable") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise WaybarClientError("invalid local API response")
    return decode_status_response(raw)


def _write_waybar(output: TextIO, document: Mapping[str, object]) -> None:
    encoded = json.dumps(
        document,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > MAX_WAYBAR_OUTPUT_BYTES:
        raise WaybarClientError("invalid Waybar output")
    output.write(encoded + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sidepulse-waybar",
        description="Render JR Bar's redacted loopback status as Waybar JSON.",
    )
    parser.add_argument("--url", default=DEFAULT_WAYBAR_URL)
    parser.add_argument("--timeout", default=DEFAULT_TIMEOUT_SECONDS, type=float)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Print exactly one Waybar JSON record, including on local API failure."""
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    environment = os.environ if environ is None else environ
    args = build_parser().parse_args(argv)
    try:
        status = fetch_status_document(
            args.url,
            timeout=args.timeout,
            bearer_token=environment.get(WAYBAR_TOKEN_ENV),
        )
        _write_waybar(output, build_waybar_document(status))
        return 0
    except (OSError, TypeError, UnicodeError, ValueError):
        _write_waybar(output, unavailable_waybar_document())
        errors.write("sidepulse-waybar: local API unavailable\n")
        return 1


__all__ = [
    "DEFAULT_WAYBAR_URL",
    "WAYBAR_TOKEN_ENV",
    "WaybarClientError",
    "build_parser",
    "build_waybar_document",
    "decode_status_response",
    "fetch_status_document",
    "main",
    "unavailable_waybar_document",
]


if __name__ == "__main__":
    raise SystemExit(main())
