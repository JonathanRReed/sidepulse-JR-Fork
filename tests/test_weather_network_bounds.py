import json

import pytest

from sidepulse import weather_watch


class _Headers:
    def __init__(self, content_type: str) -> None:
        self._content_type = content_type

    def get_content_type(self) -> str:
        return self._content_type


class _Response:
    def __init__(self, payload: bytes, content_type: str = "application/json") -> None:
        self.payload = payload
        self.headers = _Headers(content_type)
        self.requested = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, requested: int) -> bytes:
        self.requested = requested
        return self.payload[:requested]


def test_weather_response_is_bounded_and_requires_json_content_type() -> None:
    response = _Response(json.dumps({"features": []}).encode())
    payload = weather_watch._get_json(
        "https://api.weather.gov/alerts/active",
        opener=lambda *_args, **_kwargs: response,
    )

    assert payload == {"features": []}
    assert response.requested == weather_watch.WEATHER_RESPONSE_MAX_BYTES + 1

    with pytest.raises(weather_watch.WeatherUnavailableError, match="unexpected_content_type"):
        weather_watch._get_json(
            "https://api.weather.gov/alerts/active",
            opener=lambda *_args, **_kwargs: _Response(b"{}", "text/html"),
        )


def test_weather_response_larger_than_the_bound_is_refused() -> None:
    response = _Response(b"{" + b" " * weather_watch.WEATHER_RESPONSE_MAX_BYTES)

    with pytest.raises(weather_watch.WeatherUnavailableError, match="response_too_large"):
        weather_watch._get_json(
            "https://api.weather.gov/alerts/active",
            opener=lambda *_args, **_kwargs: response,
        )


def test_ip_location_expires_and_can_be_invalidated(monkeypatch) -> None:
    weather_watch.invalidate_ip_location()
    now = [100.0]
    calls = []

    monkeypatch.setattr(weather_watch, "_monotonic", lambda: now[0])

    def get_json(_url: str):
        calls.append(now[0])
        return {"latitude": 32.9 + len(calls), "longitude": -96.8}

    monkeypatch.setattr(weather_watch, "_get_json", get_json)

    first = weather_watch.ip_location()
    assert weather_watch.ip_location() == first
    assert len(calls) == 1

    now[0] += weather_watch.IP_LOCATION_TTL_SECONDS + 1
    second = weather_watch.ip_location()
    assert second != first
    assert len(calls) == 2

    weather_watch.invalidate_ip_location()
    weather_watch.ip_location()
    assert len(calls) == 3


def test_weather_coordinates_fail_closed() -> None:
    for latitude, longitude in ((91, 0), (0, 181), (float("nan"), 0)):
        with pytest.raises(weather_watch.WeatherUnavailableError, match="invalid_location"):
            weather_watch.active_alerts(latitude, longitude)
