"""Shared bounded parsing and HTTP helpers for provider usage sources."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024


class ProviderSourceError(RuntimeError):
    def __init__(self, reason_code: str, action: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.action = action


def clean_string(value: object, *, maximum: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or "\x00" in cleaned:
        return None
    return cleaned


def finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def bounded_percent(value: object) -> float | None:
    number = finite_number(value)
    if number is None:
        return None
    if 0.0 <= number <= 1.0:
        number *= 100.0
    return max(0.0, min(100.0, number))


def epoch_from_value(value: object) -> float | None:
    number = finite_number(value)
    if number is not None:
        if number <= 0.0:
            return None
        return number / 1000.0 if number > 10_000_000_000 else number
    text = clean_string(value, maximum=100)
    if text is None:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        stamp = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def slug(value: str, *, fallback: str = "lane") -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:120] or fallback


def json_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    opener=None,
) -> Any:
    if method not in {"GET", "POST"}:
        raise ValueError("unsupported provider HTTP method")
    request = Request(url, data=body, method=method, headers=headers or {})
    try:
        with (opener or urlopen)(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            if status != 200:
                raise ProviderSourceError(
                    f"http_{status}",
                    "Retry provider usage",
                )
            payload = response.read(max_bytes + 1)
    except HTTPError as exc:
        reason = (
            "unauthorized"
            if exc.code in {401, 403}
            else "rate_limited"
            if exc.code == 429
            else f"http_{exc.code}"
        )
        action = "Sign in again" if exc.code in {401, 403} else "Retry provider usage"
        raise ProviderSourceError(reason, action) from None
    except (URLError, OSError, TimeoutError):
        raise ProviderSourceError("network_unavailable", "Retry provider usage") from None
    if len(payload) > max_bytes:
        raise ProviderSourceError("response_too_large", "Retry provider usage")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProviderSourceError("invalid_json", "Retry provider usage") from None


__all__ = [
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "ProviderSourceError",
    "bounded_percent",
    "clean_string",
    "epoch_from_value",
    "finite_number",
    "json_request",
    "slug",
]
