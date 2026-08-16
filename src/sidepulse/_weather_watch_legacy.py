"""Severe-weather alerts for the "flash on an emergency warning" signal.

Sources, chosen to need NO permission prompt and NO API key:
- Location: the user's manual lat/lon settings if set, otherwise a
  one-shot IP geolocation (ipapi.co) cached for the process's life.
  Deliberately NOT CoreLocation -- a Location prompt would mean another
  Info.plist key, another bundle re-sign, and another lost FDA grant.
- Alerts: api.weather.gov/alerts/active?point=lat,lon (NWS; requires a
  User-Agent, covers the US). Only Severe/Extreme alerts count -- the
  signal is "emergency warning", not "drizzle advisory".

Same quiet-failure contract as every watcher: anything that can't work
raises WeatherUnavailableError and the caller backs off.
"""

from __future__ import annotations

import json
import urllib.request

USER_AGENT = "SidePulse/1.0 (github.com/JonathanRReed/sidepulse-JR-Fork)"
ALERT_SEVERITIES = ("Severe", "Extreme")
_cached_ip_location: tuple[float, float] | None = None


class WeatherUnavailableError(RuntimeError):
    """Weather data can't be fetched right now; treat as no signal."""


def _get_json(url: str, timeout: float = 10.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise WeatherUnavailableError(str(exc)) from exc


def ip_location() -> tuple[float, float]:
    """Approximate (lat, lon) from the network address, cached for the
    run -- city-level is plenty for county-scale weather alerts."""
    global _cached_ip_location
    if _cached_ip_location is not None:
        return _cached_ip_location
    payload = _get_json("https://ipapi.co/json/")
    try:
        location = (float(payload["latitude"]), float(payload["longitude"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherUnavailableError(f"no location in IP lookup: {exc}") from exc
    _cached_ip_location = location
    return location


def alerts_from_payload(payload: object) -> list[tuple[str, str, str]]:
    """(id, severity, event) for every Severe/Extreme alert in an NWS
    alerts payload. Pure and fixture-testable."""
    if not isinstance(payload, dict):
        return []
    alerts: list[tuple[str, str, str]] = []
    for feature in payload.get("features") or []:
        try:
            properties = feature.get("properties") or {}
            severity = str(properties.get("severity") or "")
            if severity not in ALERT_SEVERITIES:
                continue
            identifier = str(feature.get("id") or properties.get("id") or "")
            event = str(properties.get("event") or "Weather alert")
        except AttributeError:
            continue
        if identifier:
            alerts.append((identifier, severity, event))
    return alerts


def active_alerts(latitude: float, longitude: float) -> list[tuple[str, str, str]]:
    payload = _get_json(
        f"https://api.weather.gov/alerts/active?point={latitude:.4f},{longitude:.4f}"
    )
    return alerts_from_payload(payload)
