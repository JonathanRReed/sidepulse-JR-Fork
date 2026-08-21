"""``sidepulse serve``: the indicator as a machine-readable endpoint.

CodexBar's ``serve`` spawned its whole integration ecosystem -- Stream
Deck, Waybar, KDE widgets -- because a local JSON endpoint is the one
surface every other tool can consume. This is SidePulse's: a loopback
HTTP server over the app's own persisted state files, read fresh per
request so it never needs the app's process (or even the app running --
it serves the last persisted truth with its timestamps, and honesty
lives in those timestamps).

    GET /status.json   agents (works catalog) + provider usage snapshots

Loopback only, read only, no query parameters, nothing written.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .provider_usage_store import default_provider_usage_state_path
from .providers import default_state_dir

SERVE_DEFAULT_PORT = 8737


def _read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_serve_document(home: Path | None = None) -> dict:
    """The endpoint's whole payload, assembled from persisted truth."""
    latest = _read_json(default_state_dir(home) / "latest.json")
    usage = _read_json(default_provider_usage_state_path(home))
    document: dict[str, object] = {"schema_version": 1}
    if isinstance(latest, dict):
        document["agents"] = {
            "generation": latest.get("generation"),
            "last_clock": latest.get("last_clock"),
            "works": latest.get("works", []),
        }
    else:
        document["agents"] = None
    document["usage"] = usage if isinstance(usage, dict) else None
    return document


class _ServeHandler(BaseHTTPRequestHandler):
    server_version = "SidePulse"

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] not in ("/", "/status.json"):
            self.send_error(404)
            return
        payload = json.dumps(
            build_serve_document(), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        """Quiet by design; integrators poll this."""


def serve(*, port: int = SERVE_DEFAULT_PORT) -> None:
    """Blocking loopback server; Ctrl-C stops it."""
    server = ThreadingHTTPServer(("127.0.0.1", int(port)), _ServeHandler)
    print(f"sidepulse serve: http://127.0.0.1:{int(port)}/status.json")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = ["SERVE_DEFAULT_PORT", "build_serve_document", "serve"]
