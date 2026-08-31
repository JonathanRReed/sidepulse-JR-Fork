#!/usr/bin/env python3
from __future__ import annotations

import hmac
import html
import json
import os
import re
from typing import Any

import uvicorn
from apns_client import APNsClient, APNsConfig, APNsConfigError, APNsResponse
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from patterns import PATTERNS, pattern_names
from product_identity import PRODUCT_DISPLAY_NAME

DEFAULT_PORT = 8787
MAX_REQUEST_BODY_BYTES = 4096
MAX_DEVICE_TOKEN_LENGTH = 256
ALLOWED_ENVELOPE_KEYS = frozenset({"device_token", "pattern"})
DEVICE_TOKEN_PATTERN = re.compile(rf"[A-Za-z0-9_-]{{1,{MAX_DEVICE_TOKEN_LENGTH}}}\Z", re.ASCII)
FIXED_APNS_HEADERS = {
    "apns-push-type": "background",
    "apns-priority": "5",
}


app = FastAPI(title=f"{PRODUCT_DISPLAY_NAME} Push Server", version="2.0")


async def require_bearer_auth(authorization: str | None = Header(default=None)) -> None:
    shared_secret = shared_secret_from_env()
    if not shared_secret:
        raise HTTPException(status_code=503, detail="Server mutation is disabled")

    expected = f"Bearer {shared_secret}"
    if authorization is not None and hmac.compare_digest(authorization, expected):
        return

    raise HTTPException(status_code=401, detail="Unauthorized")


@app.on_event("shutdown")
async def shutdown() -> None:
    client = getattr(app.state, "apns_client", None)
    if client is not None and hasattr(client, "aclose"):
        await client.aclose()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "sidepulse-push"}


@app.get("/v1/patterns")
async def patterns() -> dict[str, Any]:
    return {
        "patterns": [PATTERNS[name].as_public_dict() for name in finite_pattern_names()],
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return index_html()


@app.post("/v1/push")
async def push(
    request: Request,
    _: None = Depends(require_bearer_auth),
) -> dict[str, Any]:
    envelope = await read_bounded_json_object(request)
    validate_envelope_keys(envelope)
    pattern = require_finite_catalog_pattern(envelope.get("pattern"))
    device_token = resolve_bounded_device_token(envelope)

    payload = {
        "aps": {"content-available": 1},
        "pattern": pattern,
    }
    response = await send_apns(device_token, payload, FIXED_APNS_HEADERS)
    return response_payload(response, extra={"pattern": pattern, "known_pattern": True})


def shared_secret_from_env() -> str:
    return os.environ.get("SIDEPULSE_SHARED_SECRET", "").strip()


async def read_bounded_json_object(request: Request) -> dict[str, Any]:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
        if declared_size < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_size > MAX_REQUEST_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Request body is too large")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Request body is too large")
        body.extend(chunk)

    try:
        loaded = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    if not isinstance(loaded, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    return loaded


def validate_envelope_keys(envelope: dict[str, Any]) -> None:
    if set(envelope) - ALLOWED_ENVELOPE_KEYS:
        raise HTTPException(status_code=400, detail="Only pattern and device_token are accepted")


def finite_pattern_names() -> list[str]:
    return [name for name in pattern_names() if catalog_pattern_is_finite(name)]


def catalog_pattern_is_finite(name: str) -> bool:
    for line in PATTERNS[name].leds.splitlines():
        tokens = line.strip().split()
        if len(tokens) == 1 and tokens[0].casefold() == "repeat":
            return False
    return True


def require_finite_catalog_pattern(value: Any) -> str:
    if not isinstance(value, str) or value not in PATTERNS or not catalog_pattern_is_finite(value):
        raise HTTPException(status_code=400, detail="Unknown or nonfinite pattern")
    return value


def resolve_bounded_device_token(envelope: dict[str, Any]) -> str:
    if "device_token" in envelope:
        value = envelope["device_token"]
        if not valid_device_token(value):
            raise HTTPException(status_code=400, detail="Invalid device token")
        return value

    configured = os.environ.get("SIDEPULSE_DEVICE_TOKEN", "")
    if not configured:
        raise HTTPException(status_code=400, detail="Missing device token")
    if not valid_device_token(configured):
        raise HTTPException(status_code=503, detail="Configured device token is invalid")
    return configured


def valid_device_token(value: Any) -> bool:
    return isinstance(value, str) and DEVICE_TOKEN_PATTERN.fullmatch(value) is not None


async def send_apns(device_token: str, payload: dict[str, Any], headers: dict[str, str]) -> APNsResponse:
    client = await get_apns_client()
    try:
        return await client.send(device_token, payload, headers=headers)
    except (APNsConfigError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def get_apns_client() -> APNsClient:
    client = getattr(app.state, "apns_client", None)
    if client is not None:
        return client

    client = APNsClient(APNsConfig.from_env())
    app.state.apns_client = client
    return client


def response_payload(response: APNsResponse, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": response.ok,
        "status_code": response.status_code,
        "apns_id": response.apns_id,
        "response": response.body,
    }
    if extra:
        payload.update(extra)

    if not response.ok:
        raise HTTPException(status_code=502, detail=payload)

    return payload


def index_html() -> str:
    options = "\n".join(f"<li><code>{html.escape(name)}</code></li>" for name in finite_pattern_names())
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{PRODUCT_DISPLAY_NAME} Push Server</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; max-width: 760px; color: #111827; }}
    code {{ background: #f1f5f9; border-radius: 4px; padding: 0.15rem 0.3rem; }}
  </style>
</head>
<body>
  <h1>{PRODUCT_DISPLAY_NAME} Push Server</h1>
  <p>Mutation is limited to authenticated JSON requests for finite catalog patterns.</p>
  <p>Configure credentials in the server environment. This page never renders them.</p>
  <h2>Available push patterns</h2>
  <ul>{options}</ul>
</body>
</html>"""


def main() -> int:
    host = os.environ.get("SIDEPULSE_SERVER_HOST", "127.0.0.1")
    port = int(os.environ.get("SIDEPULSE_SERVER_PORT", str(DEFAULT_PORT)))
    print(f"Serving {PRODUCT_DISPLAY_NAME} push server at http://{host}:{port}")
    uvicorn.run("server:app", host=host, port=port, log_level=os.environ.get("SIDEPULSE_LOG_LEVEL", "info"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
