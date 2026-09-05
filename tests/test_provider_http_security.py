from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from sidepulse.provider_usage_collectors import ProviderHttpError, _default_http_json


class _RedirectHandler(BaseHTTPRequestHandler):
    redirect_target = ""
    received_headers: ClassVar[dict[str, str]] = {}

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", self.redirect_target)
            self.end_headers()
            return
        type(self).received_headers = dict(self.headers.items())
        payload = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format, *_args):
        return


def _server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_credential_headers_are_refused_on_cross_origin_redirect():
    source = _server()
    target = _server()
    try:
        _RedirectHandler.received_headers = {}
        _RedirectHandler.redirect_target = (
            f"http://127.0.0.1:{target.server_port}/target"
        )
        with pytest.raises(ProviderHttpError) as error:
            _default_http_json(
                "GET",
                f"http://127.0.0.1:{source.server_port}/redirect",
                headers={
                    "Authorization": "Bearer fixture-secret",
                    "X-Codeium-Csrf-Token": "fixture-csrf",
                    "x-xai-token-auth": "fixture-provider-token",
                },
            )
        assert error.value.reason == "credential_redirect_refused"
        assert _RedirectHandler.received_headers == {}
    finally:
        source.shutdown()
        target.shutdown()


def test_credential_headers_survive_same_origin_redirect():
    server = _server()
    try:
        _RedirectHandler.received_headers = {}
        _RedirectHandler.redirect_target = (
            f"http://127.0.0.1:{server.server_port}/target"
        )
        result = _default_http_json(
            "GET",
            f"http://127.0.0.1:{server.server_port}/redirect",
            headers={"Authorization": "Bearer fixture-secret"},
        )
        assert result == {"ok": True}
        assert _RedirectHandler.received_headers["Authorization"] == (
            "Bearer fixture-secret"
        )
    finally:
        server.shutdown()
