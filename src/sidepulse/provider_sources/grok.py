"""Native Grok CLI credentials and Grok Build billing source."""

from __future__ import annotations

import json
import os
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..private_io import read_private_text
from ..provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    QuotaLane,
    QuotaUnit,
)
from .common import clean_string, epoch_from_value

GROK_BILLING_URL = "https://grok.com/grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig"
GROK_RESPONSE_MAX_BYTES = 1024 * 1024
GROK_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class GrokCredential:
    access_token: str
    account_label: str | None
    auth_mode: str | None
    expires_at: float | None
    principal_type: str | None

    def __repr__(self) -> str:
        return (
            "GrokCredential("
            f"account_label={self.account_label!r}, auth_mode={self.auth_mode!r}, "
            "token=<redacted>)"
        )


def default_grok_auth_path() -> Path:
    override = os.environ.get("GROK_HOME")
    root = Path(override).expanduser() if override else Path.home() / ".grok"
    return root / "auth.json"


def parse_auth_document(document: object) -> GrokCredential | None:
    if not isinstance(document, dict):
        return None
    oidc = []
    legacy = []
    for scope, value in document.items():
        if not isinstance(scope, str) or not isinstance(value, dict):
            continue
        token = clean_string(value.get("key"), maximum=64 * 1024)
        if token is None:
            continue
        row = (scope, value, token)
        if scope.startswith("https://auth.x.ai::"):
            oidc.append(row)
        elif scope == "https://accounts.x.ai/sign-in" or "/sign-in" in scope:
            legacy.append(row)
    candidates = oidc or legacy
    if not candidates:
        return None
    _scope, entry, token = candidates[-1]
    email = clean_string(entry.get("email"), maximum=300)
    first = clean_string(entry.get("first_name"), maximum=100)
    last = clean_string(entry.get("last_name"), maximum=100)
    name = " ".join(value for value in (first, last) if value) or None
    account = email or name or clean_string(entry.get("user_id"), maximum=200)
    return GrokCredential(
        access_token=token,
        account_label=account,
        auth_mode=clean_string(entry.get("auth_mode"), maximum=64),
        expires_at=epoch_from_value(entry.get("expires_at")),
        principal_type=clean_string(entry.get("principal_type"), maximum=64),
    )


def read_grok_credential(path: Path | None = None) -> GrokCredential | None:
    target = Path(path) if path is not None else default_grok_auth_path()
    try:
        document = json.loads(read_private_text(target, max_bytes=512 * 1024))
    except (OSError, ValueError):
        return None
    return parse_auth_document(document)


def _read_varint(data: bytes, index: int) -> tuple[int, int] | None:
    value = 0
    shift = 0
    while index < len(data) and shift <= 63:
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, index
        shift += 7
    return None


def _scan_protobuf(
    data: bytes,
    *,
    path: tuple[int, ...] = (),
    depth: int = 0,
) -> tuple[list[tuple[tuple[int, ...], float]], list[tuple[tuple[int, ...], int]]]:
    if depth > 8 or len(data) > GROK_RESPONSE_MAX_BYTES:
        return ([], [])
    floats: list[tuple[tuple[int, ...], float]] = []
    varints: list[tuple[tuple[int, ...], int]] = []
    index = 0
    while index < len(data):
        start = index
        key_result = _read_varint(data, index)
        if key_result is None:
            break
        key, index = key_result
        field = key >> 3
        wire = key & 7
        if field <= 0:
            index = start + 1
            continue
        field_path = (*path, field)
        if wire == 0:
            result = _read_varint(data, index)
            if result is None:
                break
            value, index = result
            varints.append((field_path, value))
        elif wire == 1:
            if index + 8 > len(data):
                break
            index += 8
        elif wire == 2:
            result = _read_varint(data, index)
            if result is None:
                break
            length, index = result
            end = index + length
            if length < 0 or end > len(data):
                break
            nested = data[index:end]
            child_floats, child_varints = _scan_protobuf(
                nested,
                path=field_path,
                depth=depth + 1,
            )
            floats.extend(child_floats)
            varints.extend(child_varints)
            index = end
        elif wire == 5:
            if index + 4 > len(data):
                break
            value = struct.unpack("<f", data[index : index + 4])[0]
            index += 4
            if value == value and value not in {float("inf"), float("-inf")}:
                floats.append((field_path, float(value)))
        else:
            index = start + 1
    return (floats, varints)


def _grpc_data_frames(data: bytes) -> tuple[bytes, ...]:
    frames: list[bytes] = []
    index = 0
    while index + 5 <= len(data):
        flags = data[index]
        length = int.from_bytes(data[index + 1 : index + 5], "big")
        start = index + 5
        end = start + length
        if end > len(data):
            return ()
        payload = data[start:end]
        if flags & 0x80:
            text = payload.decode("utf-8", errors="ignore").lower()
            if "grpc-status:" in text and "grpc-status: 0" not in text and "grpc-status:0" not in text:
                raise ValueError("grok grpc billing failed")
        else:
            frames.append(payload)
        index = end
    if index != len(data):
        return ()
    return tuple(frames)


def parse_billing_response(
    data: bytes,
    *,
    observed_at: float,
    account_label: str | None = None,
) -> ProviderUsageSnapshot:
    frames = _grpc_data_frames(data)
    if not frames and data:
        frames = (data,)
    floats: list[tuple[tuple[int, ...], float]] = []
    varints: list[tuple[tuple[int, ...], int]] = []
    for frame in frames:
        child_floats, child_varints = _scan_protobuf(frame)
        floats.extend(child_floats)
        varints.extend(child_varints)
    used_candidates = [
        value
        for path, value in floats
        if path and path[-1] == 1 and 0.0 <= value <= 100.0
    ]
    if not used_candidates:
        used_candidates = [value for _path, value in floats if 0.0 <= value <= 100.0]
    used = used_candidates[0] if used_candidates else None
    future_resets = sorted(
        float(value)
        for _path, value in varints
        if 1_700_000_000 <= value <= 2_100_000_000 and value > observed_at
    )
    reset_at = future_resets[0] if future_resets else None
    if used is None:
        raise ValueError("Grok billing payload has no usage percentage")
    return ProviderUsageSnapshot(
        provider_id="grok",
        state=ProviderSourceState.READY,
        observed_at=observed_at,
        source_label="Grok Build billing",
        account_label=account_label,
        reason_code=None,
        action=None,
        lanes=(
            QuotaLane(
                provider_id="grok",
                lane_id="included-credits",
                label="Included credits",
                remaining=max(0.0, 100.0 - used),
                used=used,
                total=100.0,
                unit=QuotaUnit.PERCENT,
                reset_at=reset_at,
                source="grok-build-billing",
                bindable=True,
            ),
        ),
        token_usage=None,
        credits=None,
        incident=None,
    )


def collect_grok_usage(
    *,
    now: float | None = None,
    auth_path: Path | None = None,
    opener=None,
) -> ProviderUsageSnapshot:
    observed_at = time.time() if now is None else float(now)
    credential = read_grok_credential(auth_path)
    if credential is None:
        return ProviderUsageSnapshot(
            provider_id="grok",
            state=ProviderSourceState.NEEDS_SIGN_IN,
            observed_at=observed_at,
            source_label="Grok CLI auth",
            account_label=None,
            reason_code="grok_login_not_found",
            action="Run grok login",
            lanes=(),
            token_usage=None,
            credits=None,
            incident=None,
        )
    if credential.expires_at is not None and credential.expires_at <= observed_at + 60.0:
        return ProviderUsageSnapshot(
            provider_id="grok",
            state=ProviderSourceState.NEEDS_SIGN_IN,
            observed_at=observed_at,
            source_label="Grok CLI auth",
            account_label=credential.account_label,
            reason_code="grok_login_expired",
            action="Run grok login",
            lanes=(),
            token_usage=None,
            credits=None,
            incident=None,
        )
    request = Request(
        GROK_BILLING_URL,
        method="POST",
        data=b"\x00\x00\x00\x00\x00",
        headers={
            "Authorization": f"Bearer {credential.access_token}",
            "Origin": "https://grok.com",
            "Referer": "https://grok.com/?_s=usage",
            "Accept": "*/*",
            "Content-Type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "x-user-agent": "connect-es/2.1.1",
            "User-Agent": "SidePulse/0.2.2",
        },
    )
    try:
        with (opener or urlopen)(request, timeout=GROK_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", 200))
            if status != 200:
                raise ValueError(f"http_{status}")
            body = response.read(GROK_RESPONSE_MAX_BYTES + 1)
        if len(body) > GROK_RESPONSE_MAX_BYTES:
            raise ValueError("response_too_large")
        return parse_billing_response(
            body,
            observed_at=observed_at,
            account_label=credential.account_label,
        )
    except HTTPError as exc:
        reason = "unauthorized" if exc.code in {401, 403} else f"http_{exc.code}"
    except (URLError, OSError, TimeoutError):
        reason = "network_unavailable"
    except ValueError as exc:
        reason = str(exc)[:120] or "grok_parse_failed"
    return ProviderUsageSnapshot(
        provider_id="grok",
        state=(
            ProviderSourceState.NEEDS_SIGN_IN
            if reason == "unauthorized"
            else ProviderSourceState.FAILED
        ),
        observed_at=observed_at,
        source_label="Grok Build billing",
        account_label=credential.account_label,
        reason_code=reason,
        action=("Run grok login" if reason == "unauthorized" else "Retry Grok usage"),
        lanes=(),
        token_usage=None,
        credits=None,
        incident=None,
    )


__all__ = [
    "GrokCredential",
    "collect_grok_usage",
    "default_grok_auth_path",
    "parse_auth_document",
    "parse_billing_response",
    "read_grok_credential",
]
