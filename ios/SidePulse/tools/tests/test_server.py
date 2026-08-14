from __future__ import annotations

import asyncio
import copy
import os
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

from apns_client import APNsResponse  # noqa: E402
from server import app  # noqa: E402

AUTH_HEADERS = {
    "authorization": "Bearer secret",
    "content-type": "application/json",
}
FINITE_PATTERNS = ("off", "green_pulse_2", "success", "error")
UNBOUNDED_PATTERNS = ("working", "waiting", "white_breathe")


class FakeAPNsClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send(
        self,
        device_token: str,
        payload: dict[str, object],
        *,
        headers: dict[str, str] | None = None,
    ) -> APNsResponse:
        await asyncio.sleep(0)
        self.calls.append(
            {
                "device_token": device_token,
                "payload": copy.deepcopy(payload),
                "headers": dict(headers or {}),
            }
        )
        return APNsResponse(status_code=200, apns_id="test-apns-id", body={"sent": True})

    async def aclose(self) -> None:
        pass


class ServerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeAPNsClient()
        app.state.apns_client = self.fake
        self.env = patch.dict(
            os.environ,
            {
                "SIDEPULSE_SHARED_SECRET": "secret",
                "SIDEPULSE_DEVICE_TOKEN": "env-device-token",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        if hasattr(app.state, "apns_client"):
            delattr(app.state, "apns_client")

    def assert_rejected_without_apns(self, response: httpx.Response, status_code: int) -> None:
        self.assertEqual(response.status_code, status_code, response.text)
        self.assertEqual(self.fake.calls, [])

    def test_missing_shared_secret_fails_closed(self) -> None:
        with patch.dict(os.environ, {"SIDEPULSE_SHARED_SECRET": ""}, clear=False):
            with TestClient(app) as client:
                response = client.post(
                    "/v1/push",
                    headers=AUTH_HEADERS,
                    json={"pattern": "green_pulse_2"},
                )

        self.assert_rejected_without_apns(response, 503)

    def test_missing_bearer_auth_is_rejected(self) -> None:
        with TestClient(app) as client:
            response = client.post("/v1/push", json={"pattern": "green_pulse_2"})

        self.assert_rejected_without_apns(response, 401)

    def test_query_secret_cannot_authenticate(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/v1/push?key=secret",
                headers={"content-type": "application/json"},
                json={"pattern": "green_pulse_2"},
            )

        self.assert_rejected_without_apns(response, 401)

    def test_cross_origin_form_is_rejected(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/v1/push",
                headers={
                    "authorization": "Bearer secret",
                    "content-type": "application/x-www-form-urlencoded",
                    "origin": "https://attacker.invalid",
                },
                content="pattern=green_pulse_2",
            )

        self.assert_rejected_without_apns(response, 415)

    def test_arbitrary_text_is_rejected(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/v1/push",
                headers={
                    "authorization": "Bearer secret",
                    "content-type": "text/plain",
                },
                content="#00ff00 280ms pulse",
            )

        self.assert_rejected_without_apns(response, 415)

    def test_oversized_stream_is_rejected_before_json_parsing(self) -> None:
        def oversized_invalid_json() -> Iterator[bytes]:
            yield b'{"pattern":"green_pulse_2","padding":"'
            yield b"x" * 8192

        with TestClient(app) as client:
            response = client.post(
                "/v1/push",
                headers=AUTH_HEADERS,
                content=oversized_invalid_json(),
            )

        self.assert_rejected_without_apns(response, 413)

    def test_raw_and_legacy_routes_are_unavailable(self) -> None:
        fixtures = (
            (
                "/v1/push/raw?device_token=query-token",
                AUTH_HEADERS,
                '{"aps":{"content-available":1}}',
            ),
            (
                "/push?key=secret&device_token=query-token",
                {"content-type": "application/x-www-form-urlencoded"},
                "pattern=green_pulse_2",
            ),
            (
                "/push?key=secret&device_token=query-token",
                {"content-type": "text/plain"},
                "#00ff00 280ms pulse",
            ),
        )

        with TestClient(app) as client:
            for path, headers, content in fixtures:
                with self.subTest(path=path, content_type=headers["content-type"]):
                    response = client.post(path, headers=headers, content=content)
                    self.assertEqual(response.status_code, 404, response.text)

        self.assertEqual(self.fake.calls, [])

    def test_raw_authority_fields_are_rejected(self) -> None:
        fixtures: tuple[tuple[str, object], ...] = (
            ("leds", "#00ff00 280ms pulse"),
            ("LEDS.LED", "#00ff00 280ms pulse"),
            ("LEDS.TXT", "#00ff00 280ms pulse"),
            ("text", "#00ff00 280ms pulse"),
            ("payload", {"aps": {"alert": "arbitrary"}}),
            ("apns", {"push_type": "alert", "priority": 10}),
            ("alert", "arbitrary"),
            ("data", {"custom": "arbitrary"}),
            ("custom", "arbitrary"),
        )

        with TestClient(app) as client:
            for key, value in fixtures:
                with self.subTest(key=key):
                    response = client.post(
                        "/v1/push",
                        headers=AUTH_HEADERS,
                        json={"pattern": "green_pulse_2", key: value},
                    )
                    self.assertEqual(response.status_code, 400, response.text)

        self.assertEqual(self.fake.calls, [])

    def test_unknown_and_unbounded_patterns_are_rejected(self) -> None:
        fixtures: tuple[object, ...] = (
            "unknown",
            "",
            " green_pulse_2 ",
            None,
            42,
            *UNBOUNDED_PATTERNS,
        )

        with TestClient(app) as client:
            for pattern in fixtures:
                with self.subTest(pattern=pattern):
                    response = client.post(
                        "/v1/push",
                        headers=AUTH_HEADERS,
                        json={"pattern": pattern},
                    )
                    self.assertEqual(response.status_code, 400, response.text)

        self.assertEqual(self.fake.calls, [])

    def test_device_token_query_and_headers_are_not_trusted(self) -> None:
        with patch.dict(os.environ, {"SIDEPULSE_DEVICE_TOKEN": ""}, clear=False):
            with TestClient(app) as client:
                response = client.post(
                    "/v1/push?device_token=query-token",
                    headers={**AUTH_HEADERS, "x-side-device-token": "header-token"},
                    json={"pattern": "green_pulse_2"},
                )

        self.assert_rejected_without_apns(response, 400)

    def test_invalid_body_device_tokens_are_rejected_instead_of_falling_back(self) -> None:
        fixtures: tuple[object, ...] = (
            "",
            "token/with/path",
            "token\nwith-control",
            "x" * 257,
            None,
            42,
        )

        with TestClient(app) as client:
            for device_token in fixtures:
                with self.subTest(device_token=device_token):
                    response = client.post(
                        "/v1/push",
                        headers=AUTH_HEADERS,
                        json={"pattern": "green_pulse_2", "device_token": device_token},
                    )
                    self.assertEqual(response.status_code, 400, response.text)

        self.assertEqual(self.fake.calls, [])

    def test_invalid_configured_device_token_fails_closed(self) -> None:
        with patch.dict(os.environ, {"SIDEPULSE_DEVICE_TOKEN": "token/with/path"}, clear=False):
            with TestClient(app) as client:
                response = client.post(
                    "/v1/push",
                    headers=AUTH_HEADERS,
                    json={"pattern": "green_pulse_2"},
                )

        self.assert_rejected_without_apns(response, 503)

    def test_authenticated_finite_catalog_patterns_use_fixed_payload(self) -> None:
        with TestClient(app) as client:
            for pattern in FINITE_PATTERNS:
                with self.subTest(pattern=pattern):
                    response = client.post(
                        "/v1/push",
                        headers={
                            "authorization": "Bearer secret",
                            "content-type": "application/json; charset=utf-8",
                        },
                        content=f'{{"pattern":"{pattern}"}}',
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["pattern"], pattern)

        self.assertEqual(len(self.fake.calls), len(FINITE_PATTERNS))
        for pattern, call in zip(FINITE_PATTERNS, self.fake.calls):
            self.assertEqual(call["device_token"], "env-device-token")
            self.assertEqual(
                call["payload"],
                {"aps": {"content-available": 1}, "pattern": pattern},
            )
            self.assertEqual(
                call["headers"],
                {"apns-push-type": "background", "apns-priority": "5"},
            )

    def test_authenticated_body_device_token_is_accepted(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/v1/push",
                headers=AUTH_HEADERS,
                json={"pattern": "success", "device_token": "explicit-token_1"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.fake.calls[0]["device_token"], "explicit-token_1")

    def test_patterns_endpoint_exposes_only_finite_push_intents(self) -> None:
        with TestClient(app) as client:
            response = client.get("/v1/patterns")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            tuple(pattern["name"] for pattern in response.json()["patterns"]),
            FINITE_PATTERNS,
        )

    def test_index_never_renders_configured_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SIDEPULSE_SHARED_SECRET": "html-secret-sentinel",
                "SIDEPULSE_DEVICE_TOKEN": "html-token-sentinel",
            },
            clear=False,
        ):
            with TestClient(app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("html-secret-sentinel", response.text)
        self.assertNotIn("html-token-sentinel", response.text)


class ConcurrentServerTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fake = FakeAPNsClient()
        app.state.apns_client = self.fake
        self.env = patch.dict(
            os.environ,
            {
                "SIDEPULSE_SHARED_SECRET": "secret",
                "SIDEPULSE_DEVICE_TOKEN": "env-device-token",
            },
            clear=False,
        )
        self.env.start()

    async def asyncTearDown(self) -> None:
        self.env.stop()
        if hasattr(app.state, "apns_client"):
            delattr(app.state, "apns_client")

    async def test_mocked_concurrent_finite_pattern_sends(self) -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            responses = await asyncio.gather(
                *[
                    client.post(
                        "/v1/push",
                        headers=AUTH_HEADERS,
                        json={"pattern": "green_pulse_2"},
                    )
                    for _ in range(40)
                ]
            )

        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(len(self.fake.calls), 40)
