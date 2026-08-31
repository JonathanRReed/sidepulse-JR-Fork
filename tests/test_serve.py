"""The serve endpoint: persisted truth, loopback, read-only."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from sidepulse.local_api_contract import LocalAPIRequest, ReplayGuard
from sidepulse.phone_glance import PhoneGlancePolicy, receive_phone_glance
from sidepulse.provider_usage_store import default_provider_usage_state_path
from sidepulse.providers import default_state_dir
from sidepulse.serve import (
    _read_json,
    _ServeHandler,
    build_authenticated_local_api_response,
    build_phone_glance_projection,
    build_serve_document,
    create_serve_server,
)

PRIVATE_SENTINELS = (
    "PRIVATE_ACCOUNT_LABEL",
    "PRIVATE_ACTION_LABEL",
    "PRIVATE_COST",
    "PRIVATE_CREDIT",
    "PRIVATE_INCIDENT_TEXT",
    "PRIVATE_LANE_LABEL",
    "PRIVATE_MODEL_NAME",
    "PRIVATE_SOURCE_ID",
    "PRIVATE_SESSION_LABEL",
    "PRIVATE_WORK_ID",
    "PRIVATE_REQUEST_ID",
    "PRIVATE_MESSAGE_TEXT",
)


def _write_private_state(home: Path) -> None:
    latest_path = default_state_dir(home) / "latest.json"
    latest_path.parent.mkdir(parents=True)
    latest_path.write_text(
        json.dumps(
            {
                "version": 2,
                "generation": 17,
                "last_clock": {"message": "PRIVATE_MESSAGE_TEXT"},
                "works": [
                    {
                        "key": {"work_id": "PRIVATE_WORK_ID"},
                        "lifecycle": "active",
                        "source_health": "healthy",
                        "source_freshness": "fresh",
                        "next_actor": "provider",
                        "safe_label": "PRIVATE_SESSION_LABEL",
                        "request_keys": [{"request_id": "PRIVATE_REQUEST_ID"}],
                        "timing_uncertain": False,
                    },
                    {
                        "lifecycle": "waiting",
                        "source_health": "partial",
                        "source_freshness": "stale",
                        "next_actor": "user",
                        "safe_label": "PRIVATE_SESSION_LABEL",
                        "timing_uncertain": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    usage_path = default_provider_usage_state_path(home)
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "refreshed_at": 1000.0,
                "next_refresh_at": 1060.0,
                "snapshots": [
                    {
                        "provider_id": "claude",
                        "account_label": "PRIVATE_ACCOUNT_LABEL",
                        "observed_at": 999.0,
                        "state": "ready",
                        "reason_code": None,
                        "action_label": "PRIVATE_ACTION_LABEL",
                        "lanes": [
                            {
                                "provider_id": "claude",
                                "lane_id": "weekly",
                                "label": "PRIVATE_LANE_LABEL",
                                "remaining_percent": 25.5,
                                "reset_at": 2000.0,
                                "scope": "all",
                                "model": "PRIVATE_MODEL_NAME",
                                "feature": None,
                                "bindable": True,
                                "source_id": "PRIVATE_SOURCE_ID",
                            },
                            {
                                "provider_id": "claude",
                                "lane_id": "session",
                                "label": "PRIVATE_LANE_LABEL",
                                "remaining_percent": 80.0,
                                "reset_at": 1500.0,
                                "scope": "all",
                                "model": None,
                                "feature": None,
                                "bindable": True,
                                "source_id": "PRIVATE_SOURCE_ID",
                            },
                        ],
                        "input_tokens": 123,
                        "cached_input_tokens": 45,
                        "output_tokens": 67,
                        "model_count": 2,
                        "estimated_cost_usd": "PRIVATE_COST",
                        "cache_savings_usd": "PRIVATE_COST",
                        "credits_remaining": "PRIVATE_CREDIT",
                        "incident": "PRIVATE_INCIDENT_TEXT",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_document_is_assembled_from_persisted_state() -> None:
    document = build_serve_document()
    assert document["schema_version"] == 2
    assert document["privacy"] == "redacted"
    assert "agents" in document and "usage" in document


def test_document_rebuilds_an_exact_redacted_public_schema(tmp_path: Path) -> None:
    _write_private_state(tmp_path)

    document = build_serve_document(tmp_path)

    assert document == {
        "schema_version": 2,
        "privacy": "redacted",
        "agents": {
            "generation": 17,
            "work_count": 2,
            "lifecycle_counts": {"active": 1, "waiting": 1},
            "next_actor_counts": {"provider": 1, "user": 1},
            "source_health_counts": {"healthy": 1, "partial": 1},
            "source_freshness_counts": {"fresh": 1, "stale": 1},
            "timing_uncertain_count": 1,
        },
        "usage": {
            "refreshed_at": 1000.0,
            "next_refresh_at": 1060.0,
            "providers": [
                {
                    "provider_id": "claude",
                    "observed_at": 999.0,
                    "state": "ready",
                    "quota": {
                        "window_count": 2,
                        "remaining_percent": 25.5,
                        "next_reset_at": 1500.0,
                    },
                }
            ],
        },
    }
    encoded = json.dumps(document, sort_keys=True)
    assert all(sentinel not in encoded for sentinel in PRIVATE_SENTINELS)


def test_future_persisted_schemas_fail_closed(tmp_path: Path) -> None:
    latest_path = default_state_dir(tmp_path) / "latest.json"
    latest_path.parent.mkdir(parents=True)
    latest_path.write_text(
        json.dumps(
            {
                "version": 3,
                "generation": 1,
                "works": [{"safe_label": "PRIVATE_SESSION_LABEL"}],
            }
        ),
        encoding="utf-8",
    )
    usage_path = default_provider_usage_state_path(tmp_path)
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "snapshots": [{"account_label": "PRIVATE_ACCOUNT_LABEL"}],
            }
        ),
        encoding="utf-8",
    )

    document = build_serve_document(tmp_path)

    assert document["agents"] is None
    assert document["usage"] is None
    assert all(
        sentinel not in json.dumps(document, sort_keys=True)
        for sentinel in PRIVATE_SENTINELS
    )


def test_unknown_public_values_are_omitted(tmp_path: Path) -> None:
    _write_private_state(tmp_path)
    latest_path = default_state_dir(tmp_path) / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    latest["works"] = [
        {
            "lifecycle": "future-private-state",
            "source_health": "future-private-health",
            "source_freshness": "future-private-freshness",
            "next_actor": "future-private-actor",
            "safe_label": "PRIVATE_SESSION_LABEL",
            "timing_uncertain": False,
        }
    ]
    latest_path.write_text(json.dumps(latest), encoding="utf-8")
    usage_path = default_provider_usage_state_path(tmp_path)
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    usage["snapshots"] = [
        {
            "provider_id": "private-provider",
            "account_label": "PRIVATE_ACCOUNT_LABEL",
            "observed_at": 1000.0,
            "state": "future-private-state",
            "lanes": [],
        }
    ]
    usage_path.write_text(json.dumps(usage), encoding="utf-8")

    document = build_serve_document(tmp_path)

    assert document["agents"]["work_count"] == 0
    assert document["usage"]["providers"] == []
    assert all(
        sentinel not in json.dumps(document, sort_keys=True)
        for sentinel in PRIVATE_SENTINELS
    )


def test_oversized_state_files_fail_closed(tmp_path: Path, monkeypatch) -> None:
    from sidepulse import serve

    _write_private_state(tmp_path)
    monkeypatch.setattr(serve, "_MAX_STATE_BYTES", 1)

    document = build_serve_document(tmp_path)

    assert document["agents"] is None
    assert document["usage"] is None


def test_symlinked_state_files_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "private-target.json"
    target.write_text(
        json.dumps({"safe_label": "PRIVATE_SESSION_LABEL"}), encoding="utf-8"
    )
    latest_path = default_state_dir(tmp_path) / "latest.json"
    latest_path.parent.mkdir(parents=True)
    latest_path.symlink_to(target)
    usage_path = default_provider_usage_state_path(tmp_path)
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.symlink_to(target)

    assert _read_json(latest_path) is None
    assert _read_json(usage_path) is None
    document = build_serve_document(tmp_path)
    assert document["agents"] is None
    assert document["usage"] is None


def test_endpoint_serves_json_and_404s_elsewhere() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ServeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/status.json", timeout=5
        ) as response:
            assert response.status == 200
            assert response.headers["Server"].startswith("JR Bar ")
            payload = json.loads(response.read().decode("utf-8"))
            assert payload["schema_version"] == 2
            assert payload["privacy"] == "redacted"
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/etc/passwd", timeout=5)
            raise AssertionError("unexpected 200")
        except urllib.error.HTTPError as error:
            assert error.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_glance_endpoint_is_disabled_by_default() -> None:
    server = create_serve_server(port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/glance.json", timeout=5
            )
        assert raised.value.code == 404
        assert raised.value.headers["Cache-Control"] == "no-store"
    finally:
        server.shutdown()
        server.server_close()


def test_signed_glance_endpoint_returns_only_the_existing_envelope(
    tmp_path: Path,
) -> None:
    secret = b"in-memory-glance-secret"
    server = create_serve_server(
        port=0,
        home=tmp_path,
        glance_secret=secret,
        glance_source_id="phone",
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/glance.json", timeout=5
        ) as response:
            encoded = response.read()
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
        envelope = json.loads(encoded.decode("utf-8"))
        assert set(envelope) == {
            "source_id",
            "sequence",
            "observed_at",
            "payload",
            "signed_body",
            "signature",
        }
        assert envelope["source_id"] == "phone"
        assert envelope["sequence"] == 1
        assert envelope["payload"] == {
            "status": "unknown",
            "outcome": "unavailable",
        }
        signed_body = envelope["signed_body"]
        unsigned = base64.urlsafe_b64decode(
            signed_body + "=" * (-len(signed_body) % 4)
        )
        assert hmac.compare_digest(
            hmac.new(secret, unsigned, hashlib.sha256).hexdigest(),
            envelope["signature"],
        )
        assert json.loads(unsigned) == {
            key: envelope[key]
            for key in ("source_id", "sequence", "observed_at", "payload")
        }
        assert len(encoded) <= 8 * 1024
        assert secret not in encoded
    finally:
        server.shutdown()
        server.server_close()


def test_glance_sequence_is_increasing_and_bounded(tmp_path: Path) -> None:
    server = create_serve_server(
        port=0,
        home=tmp_path,
        glance_secret=b"in-memory-glance-secret",
        glance_source_id="phone",
        glance_sequence_limit=2,
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        sequences = []
        for _ in range(2):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/glance.json", timeout=5
            ) as response:
                sequences.append(json.loads(response.read())["sequence"])
        assert sequences == [1, 2]
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/glance.json", timeout=5)
        assert raised.value.code == 503
    finally:
        server.shutdown()
        server.server_close()


def test_cli_phone_glance_is_opt_in_and_keeps_the_secret_out_of_arguments(
    monkeypatch,
    capsys,
) -> None:
    from sidepulse.cli import PHONE_GLANCE_SECRET_ENV, build_sidepulse_parser, cmd_serve

    parser = build_sidepulse_parser()
    disabled = parser.parse_args(["serve"])
    enabled = parser.parse_args(
        ["serve", "--phone-glance", "--phone-glance-source-id", "phone"]
    )
    calls = []
    monkeypatch.setattr("sidepulse.serve.serve", lambda **kwargs: calls.append(kwargs))
    monkeypatch.delenv(PHONE_GLANCE_SECRET_ENV, raising=False)

    assert disabled.phone_glance is False
    assert cmd_serve(enabled) == 2
    assert calls == []
    missing_output = capsys.readouterr()
    assert PHONE_GLANCE_SECRET_ENV in missing_output.err

    monkeypatch.setenv(PHONE_GLANCE_SECRET_ENV, "private-test-secret")
    assert cmd_serve(enabled) == 0
    assert calls == [
        {
            "port": 8737,
            "glance_secret": b"private-test-secret",
            "glance_source_id": "phone",
        }
    ]
    output = capsys.readouterr()
    assert "private-test-secret" not in output.out + output.err


@pytest.mark.parametrize(
    "address",
    (
        "0.0.0.0",
        "::",
        "127.0.0.1",
        "::1",
        "8.8.8.8",
        "192.0.2.1",
        "100.64.0.1",
        "localhost",
        "*",
    ),
)
def test_glance_listener_rejects_non_private_ip_literals(address: str) -> None:
    from sidepulse.glance_server import PrivateGlanceBindRefused, validate_bind_address

    with pytest.raises(PrivateGlanceBindRefused):
        validate_bind_address(address)


def test_glance_listener_accepts_private_and_link_local_ip_literals() -> None:
    from sidepulse.glance_server import validate_bind_address

    assert validate_bind_address("10.20.30.40") == "10.20.30.40"
    assert validate_bind_address("192.168.1.20") == "192.168.1.20"
    assert validate_bind_address("169.254.20.10") == "169.254.20.10"
    assert validate_bind_address("fd12:3456:789a::20") == "fd12:3456:789a::20"
    assert validate_bind_address("fe80::20") == "fe80::20"
    assert validate_bind_address("fe80::20%en0") == "fe80::20%en0"


@pytest.mark.parametrize(
    "address",
    (
        "fe80::20%",
        "fe80::20%en0%extra",
        "fe80::20%bad scope",
        "fe80::20%en0/other",
        "fe80::20%25en0",
        "fd12:3456:789a::20%en0",
    ),
)
def test_glance_listener_rejects_unsafe_or_inapplicable_ipv6_scopes(
    address: str,
) -> None:
    from sidepulse.glance_server import PrivateGlanceBindRefused, validate_bind_address

    with pytest.raises(PrivateGlanceBindRefused):
        validate_bind_address(address)


def test_glance_cli_requires_explicit_private_bind_and_secret(monkeypatch, capsys) -> None:
    from sidepulse.cli import PHONE_GLANCE_SECRET_ENV, build_parser, cmd_glance

    parser = build_parser()
    args = parser.parse_args(
        ["glance", "--bind-address", "192.168.1.20", "--port", "8738"]
    )
    monkeypatch.delenv(PHONE_GLANCE_SECRET_ENV, raising=False)
    calls = []
    monkeypatch.setattr(
        "sidepulse.glance_server.glance_serve", lambda **kwargs: calls.append(kwargs)
    )

    assert cmd_glance(args) == 2
    assert calls == []
    assert PHONE_GLANCE_SECRET_ENV in capsys.readouterr().err

    monkeypatch.setenv(PHONE_GLANCE_SECRET_ENV, "private-test-secret")
    assert cmd_glance(args) == 0
    assert calls == [
        {
            "bind_address": "192.168.1.20",
            "port": 8738,
            "glance_secret": b"private-test-secret",
            "glance_source_id": "sidepulse",
        }
    ]


@pytest.mark.parametrize("port", ("0", "65536", "-1"))
def test_glance_cli_rejects_ports_outside_the_listener_range(port: str) -> None:
    from sidepulse.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["glance", "--bind-address", "192.168.1.20", "--port", port]
        )


def test_glance_listener_has_no_status_or_query_routes(tmp_path: Path) -> None:
    from sidepulse.glance_server import (
        GlanceServerConfiguration,
        _GlanceOnlyHandler,
        _GlanceSequence,
    )

    secret = b"in-memory-glance-secret"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GlanceOnlyHandler)
    server.glance_configuration = GlanceServerConfiguration(
        bind_address="192.168.1.20",
        home=tmp_path,
        glance_secret=secret,
    )
    server.glance_sequence = _GlanceSequence(start=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/glance.json", timeout=5
        ) as response:
            assert response.status == 200
            assert set(json.loads(response.read())) == {
                "source_id",
                "sequence",
                "observed_at",
                "payload",
                "signed_body",
                "signature",
            }
        for path in ("/", "/status.json", "/other", "/glance.json?x=1"):
            with pytest.raises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)
            assert raised.value.code == 404
        for method in ("HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"):
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/glance.json", method=method
            )
            with pytest.raises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=5)
            assert raised.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_create_glance_server_serves_glance_but_not_status(
    tmp_path: Path, monkeypatch
) -> None:
    from sidepulse import glance_server

    class LoopbackTestServer(glance_server._GlanceHTTPServer):
        def __init__(self, _address, handler, configuration) -> None:
            super().__init__(("127.0.0.1", 0), handler, configuration)

    monkeypatch.setattr(glance_server, "_GlanceHTTPServer", LoopbackTestServer)
    server = glance_server.create_glance_server(
        bind_address="192.168.1.20",
        port=0,
        home=tmp_path,
        glance_secret=b"in-memory-glance-secret",
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/glance.json", timeout=5
        ) as response:
            assert response.status == 200
            assert json.loads(response.read())["payload"] == {
                "status": "unknown",
                "outcome": "unavailable",
            }
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/status.json", timeout=5
            )
        assert raised.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_private_listener_source_is_stable_per_instance_and_changes_on_restart() -> None:
    from sidepulse.glance_server import GlanceServerConfiguration

    first = GlanceServerConfiguration(
        bind_address="192.168.1.20",
        glance_secret=b"in-memory-glance-secret",
        glance_source_id="phone",
        glance_instance_id="a" * 32,
    )
    restarted = GlanceServerConfiguration(
        bind_address="192.168.1.20",
        glance_secret=b"in-memory-glance-secret",
        glance_source_id="phone",
        glance_instance_id="b" * 32,
    )

    assert first.effective_source_id == first.effective_source_id
    assert first.effective_source_id == f"phone:{'a' * 32}"
    assert restarted.effective_source_id == f"phone:{'b' * 32}"
    assert restarted.effective_source_id != first.effective_source_id


def test_private_listener_sequence_has_no_practical_one_million_request_exhaustion() -> None:
    from sidepulse.glance_server import _GlanceSequence

    sequence = _GlanceSequence(start=1_000_000)

    assert sequence.next() == 1_000_001
    assert sequence.next() == 1_000_002


def test_in_process_integrations_are_authenticated_and_only_reuse_redacted_projection(
    tmp_path: Path,
) -> None:
    _write_private_state(tmp_path)
    secret = b"local-integration-test-key"
    request = LocalAPIRequest(
        client_id="streamdeck",
        capability="agents.read",
        nonce="n-1",
        issued_at=1000.0,
        expires_at=1020.0,
    ).sign(secret)
    guard = ReplayGuard()

    response = build_authenticated_local_api_response(
        request.encode(),
        secret=secret,
        replay_guard=guard,
        home=tmp_path,
        now=1001.0,
    )
    response_document = json.loads(response.encode())

    assert response_document["capability"] == "agents.read"
    assert response_document["data"] == {
        "agents": build_serve_document(tmp_path)["agents"]
    }
    with pytest.raises(ValueError, match="replayed"):
        build_authenticated_local_api_response(
            request,
            secret=secret,
            replay_guard=guard,
            home=tmp_path,
            now=1001.0,
        )

    def sign(payload: bytes) -> str:
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    glance = build_phone_glance_projection(
        PhoneGlancePolicy("mac", include_message=True, include_capacity=True),
        signer=sign,
        sequence=7,
        home=tmp_path,
        observed_at=1001.0,
    )
    assert glance.payload == {
        "status": "working",
        "outcome": "attention",
        "capacity": {"remaining_percent": 25.5, "reset_at": 1500.0},
    }
    assert receive_phone_glance(
        glance,
        PhoneGlancePolicy("mac", include_capacity=True),
        verifier=lambda payload, tag: hmac.compare_digest(sign(payload), tag),
        now=1001.0,
    ) == glance
    encoded = response.encode() + json.dumps(dict(glance.payload)).encode()
    assert all(sentinel.encode() not in encoded for sentinel in PRIVATE_SENTINELS)
