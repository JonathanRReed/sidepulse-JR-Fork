"""Bounded, freshness-aware severe-weather network facade."""

from __future__ import annotations

import json
import math
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from . import _weather_watch_legacy as _legacy

WEATHER_RESPONSE_MAX_BYTES = 1024 * 1024
IP_LOCATION_TTL_SECONDS = 60 * 60.0
WEATHER_TIMEOUT_SECONDS = 10.0
_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/geo+json",
        "application/problem+json",
    }
)


@dataclass(frozen=True, slots=True)
class CachedIPLocation:
    latitude: float
    longitude: float
    expires_at: float


_cached_location: CachedIPLocation | None = None
_monotonic: Callable[[], float] = time.monotonic


def _response_content_type(response) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get_content_type()
    except AttributeError:
        value = headers.get("Content-Type") if hasattr(headers, "get") else None
        if isinstance(value, str):
            value = value.split(";", 1)[0].strip().casefold()
    return value.casefold() if isinstance(value, str) else None


def _get_json(
    url: str,
    timeout: float = WEATHER_TIMEOUT_SECONDS,
    *,
    opener=urllib.request.urlopen,
) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _legacy.USER_AGENT,
            "Accept": "application/geo+json, application/json",
        },
    )
    try:
        with opener(request, timeout=max(0.1, float(timeout))) as response:
            content_type = _response_content_type(response)
            if content_type is not None and content_type not in _ALLOWED_CONTENT_TYPES:
                raise _legacy.WeatherUnavailableError("unexpected_content_type")
            body = response.read(WEATHER_RESPONSE_MAX_BYTES + 1)
    except _legacy.WeatherUnavailableError:
        raise
    except Exception as exc:
        raise _legacy.WeatherUnavailableError("network_unavailable") from exc
    if len(body) > WEATHER_RESPONSE_MAX_BYTES:
        raise _legacy.WeatherUnavailableError("response_too_large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise _legacy.WeatherUnavailableError("malformed_response") from exc
    if not isinstance(payload, dict):
        raise _legacy.WeatherUnavailableError("malformed_response")
    return payload


def _coordinate(value: object, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise _legacy.WeatherUnavailableError("invalid_location")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _legacy.WeatherUnavailableError("invalid_location") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise _legacy.WeatherUnavailableError("invalid_location")
    return number


def invalidate_ip_location() -> None:
    global _cached_location
    _cached_location = None


def ip_location() -> tuple[float, float]:
    """City-level network location with an explicit one-hour freshness bound."""
    global _cached_location
    now = _monotonic()
    cached = _cached_location
    if cached is not None and now < cached.expires_at:
        return cached.latitude, cached.longitude
    payload = _get_json("https://ipapi.co/json/")
    latitude = _coordinate(payload.get("latitude"), minimum=-90.0, maximum=90.0)
    longitude = _coordinate(
        payload.get("longitude"),
        minimum=-180.0,
        maximum=180.0,
    )
    _cached_location = CachedIPLocation(
        latitude,
        longitude,
        now + IP_LOCATION_TTL_SECONDS,
    )
    return latitude, longitude


def active_alerts(
    latitude: float,
    longitude: float,
) -> list[tuple[str, str, str]]:
    lat = _coordinate(latitude, minimum=-90.0, maximum=90.0)
    lon = _coordinate(longitude, minimum=-180.0, maximum=180.0)
    payload = _get_json(
        f"https://api.weather.gov/alerts/active?point={lat:.4f},{lon:.4f}"
    )
    return _legacy.alerts_from_payload(payload)


_legacy.WEATHER_RESPONSE_MAX_BYTES = WEATHER_RESPONSE_MAX_BYTES
_legacy.IP_LOCATION_TTL_SECONDS = IP_LOCATION_TTL_SECONDS
_legacy._get_json = _get_json
_legacy.ip_location = ip_location
_legacy.invalidate_ip_location = invalidate_ip_location
_legacy.active_alerts = active_alerts

for _name in dir(_legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_legacy, _name)

__all__ = tuple(sorted(name for name in globals() if not name.startswith("_")))
