"""The serve endpoint: persisted truth, loopback, read-only."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from sidepulse.serve import _ServeHandler, build_serve_document


def test_document_is_assembled_from_persisted_state() -> None:
    document = build_serve_document()
    assert document["schema_version"] == 1
    assert "agents" in document and "usage" in document


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
            payload = json.loads(response.read().decode("utf-8"))
            assert payload["schema_version"] == 1
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/etc/passwd", timeout=5)
            raise AssertionError("unexpected 200")
        except urllib.error.HTTPError as error:
            assert error.code == 404
    finally:
        server.shutdown()
        server.server_close()
