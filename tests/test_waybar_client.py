"""Waybar consumes only JR Bar's bounded, redacted local status projection."""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sidepulse.waybar_client import (
    DEFAULT_WAYBAR_URL,
    WAYBAR_TOKEN_ENV,
    WaybarClientError,
    build_waybar_document,
    decode_status_response,
    fetch_status_document,
    main,
)


def _status_document() -> dict[str, object]:
    return {
        "schema_version": 2,
        "privacy": "redacted",
        "agents": {
            "generation": 17,
            "work_count": 3,
            "lifecycle_counts": {"active": 1, "waiting": 1, "failed": 1},
            "next_actor_counts": {"provider": 2, "user": 1},
            "source_health_counts": {"healthy": 2, "partial": 1},
            "source_freshness_counts": {"fresh": 2, "stale": 1},
            "timing_uncertain_count": 0,
        },
        "usage": {
            "refreshed_at": 1_000.0,
            "next_refresh_at": 1_060.0,
            "providers": [
                {
                    "provider_id": "claude",
                    "observed_at": 999.0,
                    "state": "ready",
                    "quota": {
                        "window_count": 1,
                        "remaining_percent": 25.5,
                        "next_reset_at": 1_500.0,
                    },
                }
            ],
        },
    }


def test_waybar_projection_is_stable_and_uses_only_aggregate_fields() -> None:
    status = _status_document()
    status["private_sentinel"] = "do-not-render"
    usage = status["usage"]
    assert isinstance(usage, dict)
    usage["private_sentinel"] = "do-not-render"

    projection = build_waybar_document(status)

    assert projection == {
        "text": "JR failed 1",
        "tooltip": (
            "JR Bar\n"
            "Active: 1\n"
            "Waiting: 1\n"
            "Needs attention: 1\n"
            "Failed: 1\n"
            "Capacity remaining: 26%"
        ),
        "class": ["sidepulse", "failed"],
        "percentage": 26,
    }
    assert "do-not-render" not in json.dumps(projection)


def test_response_decoder_accepts_direct_and_capability_enveloped_contracts() -> None:
    status = _status_document()
    direct = decode_status_response(json.dumps(status).encode())
    enveloped = decode_status_response(
        json.dumps(
            {
                "version": 1,
                "capability": "status.read",
                "generated_at": 1_001.0,
                "privacy": "redacted",
                "data": {"status": status},
                "error": None,
            }
        ).encode()
    )

    assert direct == status
    assert enveloped == status

    for change in (
        {"privacy": "private"},
        {"version": 1, "capability": "usage.read", "data": {"status": status}},
    ):
        payload = {**status, **change}
        if "version" in change:
            payload = {
                "version": change["version"],
                "capability": change["capability"],
                "generated_at": 1_001.0,
                "privacy": "redacted",
                "data": change["data"],
                "error": None,
            }
        with pytest.raises(WaybarClientError, match="invalid local API response"):
            decode_status_response(json.dumps(payload).encode())


def test_http_client_is_loopback_only_bounded_and_sends_token_only_as_a_header() -> None:
    token = "private-bearer-sentinel"
    received: dict[str, str] = {}
    payload = json.dumps(_status_document()).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received["path"] = self.path
            received["authorization"] = self.headers.get("Authorization", "")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        document = fetch_status_document(
            f"http://127.0.0.1:{server.server_address[1]}/status.json",
            timeout=0.5,
            bearer_token=token,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert document == _status_document()
    assert received == {
        "path": "/status.json",
        "authorization": f"Bearer {token}",
    }

    for url in (
        "https://127.0.0.1:8737/status.json",
        "http://example.com/status.json",
        "http://user:secret@127.0.0.1:8737/status.json",
        "http://127.0.0.1:8737/status.json?token=secret",
    ):
        with pytest.raises(WaybarClientError, match="invalid local API URL"):
            fetch_status_document(url)


def test_cli_emits_one_json_line_and_never_echoes_credentials_or_server_body() -> None:
    token = "private-bearer-sentinel"
    response_body = f'{{"error":"{token}"}}'.encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        code = main(
            [
                "--url",
                f"http://127.0.0.1:{server.server_address[1]}/status.json",
                "--timeout",
                "0.5",
            ],
            environ={WAYBAR_TOKEN_ENV: token},
            stdout=stdout,
            stderr=stderr,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert code == 1
    assert json.loads(stdout.getvalue()) == {
        "class": ["sidepulse", "unavailable"],
        "text": "JR unavailable",
        "tooltip": "JR Bar status is unavailable.",
    }
    assert stdout.getvalue().count("\n") == 1
    assert stderr.getvalue() == "sidepulse-waybar: local API unavailable\n"
    assert token not in stdout.getvalue() + stderr.getvalue()
    assert DEFAULT_WAYBAR_URL == "http://127.0.0.1:8737/status.json"
