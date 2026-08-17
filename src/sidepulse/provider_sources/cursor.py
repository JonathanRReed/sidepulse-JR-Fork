"""Native Cursor.app authentication and Cursor usage-summary source."""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from ..provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    QuotaLane,
    QuotaUnit,
)
from .common import (
    ProviderSourceError,
    bounded_percent,
    clean_string,
    epoch_from_value,
    finite_number,
    json_request,
)

CURSOR_BASE_URL = "https://cursor.com"
CURSOR_DB_MAX_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CursorAppSession:
    access_token: str
    user_id: str
    email: str | None
    expires_at: float

    def __repr__(self) -> str:
        return (
            "CursorAppSession("
            f"user_id={self.user_id!r}, email={self.email!r}, token=<redacted>)"
        )


def default_cursor_db_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Cursor"
        / "User"
        / "globalStorage"
        / "state.vscdb"
    )


def _jwt_payload(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    encoded = parts[1].replace("-", "+").replace("_", "/")
    encoded += "=" * ((4 - len(encoded) % 4) % 4)
    try:
        value = json.loads(base64.b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _decode_sqlite_value(value: object) -> str | None:
    if isinstance(value, str):
        return clean_string(value, maximum=64 * 1024)
    if isinstance(value, bytes):
        for encoding in ("utf-8", "utf-16-le"):
            try:
                decoded = value.decode(encoding)
            except UnicodeDecodeError:
                continue
            cleaned = clean_string(decoded, maximum=64 * 1024)
            if cleaned:
                return cleaned
    return None


def read_cursor_app_token(
    path: Path | None = None,
    *,
    now: float | None = None,
) -> CursorAppSession | None:
    target = Path(path) if path is not None else default_cursor_db_path()
    try:
        info = target.stat()
    except OSError:
        return None
    if not target.is_file() or info.st_size <= 0 or info.st_size > CURSOR_DB_MAX_BYTES:
        return None
    uri = f"file:{quote(str(target.absolute()))}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=0.25)
        try:
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1",
                ("cursorAuth/accessToken",),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    token = _decode_sqlite_value(row[0])
    if token is None:
        return None
    payload = _jwt_payload(token)
    if payload is None:
        return None
    subject = clean_string(payload.get("sub"), maximum=300)
    expiration = finite_number(payload.get("exp"))
    if subject is None or expiration is None:
        return None
    user_id = subject.split("|")[-1].strip().lower()
    if not user_id or any(not (character.isalnum() or character in "._-") for character in user_id):
        return None
    reference = time.time() if now is None else float(now)
    if expiration <= reference + 60.0:
        return None
    return CursorAppSession(
        access_token=token,
        user_id=user_id,
        email=clean_string(payload.get("email"), maximum=300),
        expires_at=expiration,
    )


def cursor_cookie_header(session: CursorAppSession) -> str:
    if type(session) is not CursorAppSession:
        raise TypeError("session must be CursorAppSession")
    return f"WorkosCursorSessionToken={session.user_id}%3A%3A{session.access_token}"


def _money_lane(
    lane_id: str,
    label: str,
    payload: object,
    *,
    reset_at: float | None,
) -> QuotaLane | None:
    if not isinstance(payload, dict) or payload.get("enabled") is False:
        return None
    used = finite_number(payload.get("used"))
    limit = finite_number(payload.get("limit"))
    remaining = finite_number(payload.get("remaining"))
    if used is None and remaining is None:
        return None
    return QuotaLane(
        provider_id="cursor",
        lane_id=lane_id,
        label=label,
        remaining=(remaining / 100.0 if remaining is not None else None),
        used=(used / 100.0 if used is not None else None),
        total=(limit / 100.0 if limit is not None and limit > 0 else None),
        unit=QuotaUnit.USD,
        reset_at=reset_at,
        source="cursor-usage-summary",
        feature=lane_id,
        bindable=False,
    )


def parse_usage_summary(
    payload: object,
    *,
    observed_at: float,
    account_label: str | None = None,
) -> ProviderUsageSnapshot:
    if not isinstance(payload, dict):
        raise ValueError("invalid Cursor usage summary")
    reset_at = epoch_from_value(payload.get("billingCycleEnd"))
    individual = payload.get("individualUsage")
    individual = individual if isinstance(individual, dict) else {}
    plan = individual.get("plan")
    plan = plan if isinstance(plan, dict) else {}
    lanes: list[QuotaLane] = []
    total_used = bounded_percent(plan.get("totalPercentUsed"))
    if total_used is None:
        used = finite_number(plan.get("used"))
        limit = finite_number(plan.get("limit"))
        if used is not None and limit and limit > 0:
            total_used = max(0.0, min(100.0, used / limit * 100.0))
    if total_used is not None:
        lanes.append(
            QuotaLane(
                provider_id="cursor",
                lane_id="included-plan",
                label="Included plan",
                remaining=100.0 - total_used,
                used=total_used,
                total=100.0,
                unit=QuotaUnit.PERCENT,
                reset_at=reset_at,
                source="cursor-usage-summary",
                bindable=True,
            )
        )
    for field, lane_id, label in (
        ("autoPercentUsed", "auto-composer", "Auto + Composer"),
        ("apiPercentUsed", "api-models", "API models"),
    ):
        used = bounded_percent(plan.get(field))
        if used is None:
            continue
        lanes.append(
            QuotaLane(
                provider_id="cursor",
                lane_id=lane_id,
                label=label,
                remaining=100.0 - used,
                used=used,
                total=100.0,
                unit=QuotaUnit.PERCENT,
                reset_at=reset_at,
                source="cursor-usage-summary",
                feature=lane_id,
                bindable=False,
            )
        )
    for lane in (
        _money_lane(
            "on-demand",
            "On-demand",
            individual.get("onDemand"),
            reset_at=reset_at,
        ),
        _money_lane(
            "individual-cap",
            "Individual cap",
            individual.get("overall"),
            reset_at=reset_at,
        ),
    ):
        if lane is not None:
            lanes.append(lane)
    team = payload.get("teamUsage")
    if isinstance(team, dict):
        for lane in (
            _money_lane("team-on-demand", "Team on-demand", team.get("onDemand"), reset_at=reset_at),
            _money_lane("team-pool", "Team pool", team.get("pooled"), reset_at=reset_at),
        ):
            if lane is not None:
                lanes.append(lane)
    if not lanes:
        raise ValueError("Cursor usage summary has no supported values")
    return ProviderUsageSnapshot(
        provider_id="cursor",
        state=ProviderSourceState.READY,
        observed_at=observed_at,
        source_label="Cursor.app local auth + usage-summary",
        account_label=account_label,
        reason_code=None,
        action=None,
        lanes=tuple(lanes),
        token_usage=None,
        credits=None,
        incident=None,
    )


def collect_cursor_usage(
    *,
    now: float | None = None,
    db_path: Path | None = None,
    opener=None,
) -> ProviderUsageSnapshot:
    observed_at = time.time() if now is None else float(now)
    session = read_cursor_app_token(db_path, now=observed_at)
    if session is None:
        return ProviderUsageSnapshot(
            provider_id="cursor",
            state=ProviderSourceState.NEEDS_CONSENT,
            observed_at=observed_at,
            source_label="Cursor.app local auth",
            account_label=None,
            reason_code="cursor_app_session_unavailable",
            action="Enable Cursor browser source or sign in to Cursor.app",
            lanes=(),
            token_usage=None,
            credits=None,
            incident=None,
        )
    headers = {
        "Accept": "application/json",
        "Cookie": cursor_cookie_header(session),
        "User-Agent": "SidePulse/0.2.2",
    }
    try:
        payload = json_request(
            f"{CURSOR_BASE_URL}/api/usage-summary",
            headers=headers,
            opener=opener,
        )
        return parse_usage_summary(
            payload,
            observed_at=observed_at,
            account_label=session.email or session.user_id,
        )
    except (ProviderSourceError, ValueError) as exc:
        reason = exc.reason_code if isinstance(exc, ProviderSourceError) else "cursor_parse_failed"
        action = exc.action if isinstance(exc, ProviderSourceError) else "Retry Cursor usage"
        return ProviderUsageSnapshot(
            provider_id="cursor",
            state=(
                ProviderSourceState.NEEDS_SIGN_IN
                if reason == "unauthorized"
                else ProviderSourceState.FAILED
            ),
            observed_at=observed_at,
            source_label="Cursor usage-summary",
            account_label=session.email or session.user_id,
            reason_code=reason,
            action=("Sign in to Cursor.app again" if reason == "unauthorized" else action),
            lanes=(),
            token_usage=None,
            credits=None,
            incident=None,
        )


__all__ = [
    "CursorAppSession",
    "collect_cursor_usage",
    "cursor_cookie_header",
    "default_cursor_db_path",
    "parse_usage_summary",
    "read_cursor_app_token",
]
