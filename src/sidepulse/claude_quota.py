"""Claude subscription quota: the live read, and the normalization boundary.

`windows_from_payload` stays pure and fixture-testable -- it is where the
schema's growth is absorbed (the flat `five_hour`/`seven_day` keys, the
per-model sub-caps, and the newer `limits[]` array with scoped weekly limits).

`fetch_windows` performs the actual read against the same OAuth usage endpoint
Claude Code itself uses, presenting Claude Code's own credential. It does not
discover that credential: obtaining one requires user consent and belongs to
`credentials`, which never raises a Keychain dialog on a background timer.
Called without a token this still fails closed, exactly as before.

Errors here carry reason *codes*, never response bodies. The body can contain
account identifiers, and these strings surface in the UI and in doctor output.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .capacity_types import CapacitySourceHealth, SourceHealthKind, SourceKey

MAX_CLAUDE_WINDOWS = 32
CLAUDE_REMOTE_QUOTA_UNSUPPORTED = "claude_remote_quota_unsupported"
CLAUDE_REMOTE_QUOTA_UNAUTHORIZED = "claude_remote_quota_unauthorized"
CLAUDE_REMOTE_QUOTA_RATE_LIMITED = "claude_remote_quota_rate_limited"
CLAUDE_REMOTE_QUOTA_SERVER_ERROR = "claude_remote_quota_server_error"
CLAUDE_REMOTE_QUOTA_NETWORK = "claude_remote_quota_network"
CLAUDE_REMOTE_QUOTA_NO_WINDOWS = "claude_remote_quota_no_windows"
CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN = "claude_remote_quota_needs_sign_in"

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_OAUTH_BETA_HEADER = "oauth-2025-04-20"
CLAUDE_CODE_VERSION_FALLBACK = "2.1.0"
CLAUDE_USAGE_TIMEOUT_SECONDS = 30.0
CLAUDE_USAGE_MAX_BYTES = 1024 * 1024
CLAUDE_QUOTA_SOURCE = SourceKey(
    provider_id="claude",
    adapter_id="quota",
    source_instance_id="oauth",
    capability_id="remote_quota_windows",
)
_PRODUCT_MODEL_LABELS = {
    "claude-opus": "Opus",
    "opus": "Opus",
    "claude-sonnet": "Sonnet",
    "sonnet": "Sonnet",
    "fable": "Fable",
}


class ClaudeQuotaUnavailableError(RuntimeError):
    pass


def unsupported_source_health(*, observed_at: float) -> CapacitySourceHealth:
    """Return bounded health for the unavailable trusted Claude source."""
    return CapacitySourceHealth(
        source=CLAUDE_QUOTA_SOURCE,
        kind=SourceHealthKind.UNSUPPORTED,
        observed_at=observed_at,
        last_attempt_at=observed_at,
        retry_at=None,
        reason_code=CLAUDE_REMOTE_QUOTA_UNSUPPORTED,
        has_last_known_good=False,
    )


@dataclass(frozen=True, slots=True)
class ClaudeOAuthCredential:
    """Claude Code's own OAuth credential, as it stores it."""

    access_token: str
    expires_at: float | None = None
    subscription_type: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return (
            "ClaudeOAuthCredential("
            f"subscription_type={self.subscription_type!r}, token=<redacted>)"
        )

    def is_expired(self, now: float) -> bool:
        return self.expires_at is not None and now >= self.expires_at


def credential_from_keychain_payload(raw: object) -> ClaudeOAuthCredential | None:
    """Parse the Keychain blob Claude Code writes. Absence is not an error."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    oauth = payload.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token.strip():
        return None
    raw_expiry = oauth.get("expiresAt")
    expires_at: float | None = None
    if not isinstance(raw_expiry, bool) and isinstance(raw_expiry, (int, float)):
        value = float(raw_expiry)
        if math.isfinite(value) and value > 0.0:
            # Claude Code stores milliseconds since the epoch.
            expires_at = value / 1000.0 if value > 1e11 else value
    subscription = oauth.get("subscriptionType")
    return ClaudeOAuthCredential(
        access_token=token.strip(),
        expires_at=expires_at,
        subscription_type=subscription if isinstance(subscription, str) else None,
    )


def credential_needs_sign_in(raw: object) -> bool:
    """True when Claude Code holds a refresh token but no usable access token.

    Observed on the owner's machine: `accessToken` empty, `expiresAt` 0, a
    valid `refreshToken` present. Claude Code mints access tokens on demand
    rather than caching them.

    We deliberately do NOT perform that refresh ourselves. The refresh token
    rotates on use, so minting our own token would invalidate the one Claude
    Code holds and break the user's `claude` login -- trading a status
    readout for their actual tooling. Refresh belongs to the app that owns
    the credential; our job is to say so clearly and re-read afterwards.
    """
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        payload = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    oauth = payload.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return False
    token = oauth.get("accessToken")
    has_access = isinstance(token, str) and bool(token.strip())
    refresh = oauth.get("refreshToken")
    has_refresh = isinstance(refresh, str) and bool(refresh.strip())
    return has_refresh and not has_access


def _claude_code_user_agent() -> str:
    """Identify honestly as the client whose credential we are presenting."""
    return f"claude-code/{CLAUDE_CODE_VERSION_FALLBACK}"


def _default_opener(request, timeout: float):
    from urllib.request import urlopen

    return urlopen(request, timeout=timeout)


def fetch_windows(
    *,
    access_token: str | None = None,
    opener=None,
    timeout: float = CLAUDE_USAGE_TIMEOUT_SECONDS,
) -> list[dict]:
    """Read the live per-window quota for a Claude subscription.

    Called with no credential this still fails closed, exactly as before --
    the caller is responsible for obtaining consent and a token first (see
    `credentials.read_keychain_secret`, which never prompts in background).

    Failures raise with a reason *code*, never a server body: the response can
    contain account identifiers, and this string reaches the UI and doctor.
    """
    if not isinstance(access_token, str) or not access_token.strip():
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_UNSUPPORTED)

    from urllib.error import HTTPError, URLError
    from urllib.request import Request

    request = Request(
        CLAUDE_USAGE_URL,
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token.strip()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": CLAUDE_OAUTH_BETA_HEADER,
            "User-Agent": _claude_code_user_agent(),
        },
    )
    try:
        with (opener or _default_opener)(request, timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_SERVER_ERROR)
            body = response.read(CLAUDE_USAGE_MAX_BYTES + 1)
    except HTTPError as error:
        if error.code == 401:
            raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_UNAUTHORIZED) from None
        if error.code == 429:
            raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_RATE_LIMITED) from None
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_SERVER_ERROR) from None
    except (URLError, OSError, ValueError):
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_NETWORK) from None

    if len(body) > CLAUDE_USAGE_MAX_BYTES:
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_SERVER_ERROR)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_SERVER_ERROR) from None

    windows = windows_from_payload(payload)
    if not windows:
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_NO_WINDOWS)
    return windows


def _product_model_label(model: object) -> str | None:
    if not isinstance(model, dict):
        return None
    candidates = (model.get("id"), model.get("display_name"))
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        normalized = "-".join(candidate.strip().lower().split())
        label = _PRODUCT_MODEL_LABELS.get(normalized)
        if label is not None:
            return label
    return None


def windows_from_payload(payload: object) -> list[dict]:
    """Pure and fixture-testable, tolerant of the schema's growth: known
    top-level windows plus the newer ``limits[]`` array."""
    if not isinstance(payload, dict):
        return []
    windows: list[dict] = []

    def add(
        label: str,
        entry: object,
        semantic_minutes: int | None = None,
        *,
        percent_key: str = "utilization",
    ) -> bool:
        if not isinstance(entry, dict) or len(windows) >= MAX_CLAUDE_WINDOWS:
            return False
        utilization = entry.get(percent_key)
        if (
            isinstance(utilization, bool)
            or not isinstance(utilization, (int, float))
            or not math.isfinite(float(utilization))
        ):
            return False
        minutes = entry.get("window_minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
            seconds = entry.get("limit_window_seconds", entry.get("window_seconds"))
            minutes = (
                seconds / 60.0
                if not isinstance(seconds, bool)
                and isinstance(seconds, (int, float))
                and math.isfinite(float(seconds))
                else None
            )
        if isinstance(minutes, (int, float)) and not math.isfinite(float(minutes)):
            minutes = None
        if not isinstance(minutes, (int, float)):
            minutes = semantic_minutes
        windows.append(
            {
                "label": label,
                "utilization": max(0.0, min(100.0, float(utilization))),
                "window_minutes": (
                    max(1, int(round(float(minutes))))
                    if isinstance(minutes, (int, float)) and float(minutes) > 0.0
                    else None
                ),
                "resets_at": (
                    entry.get("resets_at", entry.get("reset_at"))
                    if not isinstance(
                        entry.get("resets_at", entry.get("reset_at")), bool
                    )
                    and isinstance(
                        entry.get("resets_at", entry.get("reset_at")),
                        (str, int, float),
                    )
                    else None
                ),
            }
        )
        return True

    add("5-hour", payload.get("five_hour"), 5 * 60)
    add("weekly", payload.get("seven_day"), 7 * 24 * 60)
    add("Sonnet only", payload.get("seven_day_sonnet"), 7 * 24 * 60)
    add("Opus only", payload.get("seven_day_opus"), 7 * 24 * 60)
    limits = payload.get("limits")
    if isinstance(limits, list):
        seen_scopes: set[str] = set()
        for entry in limits:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind")
            group = entry.get("group")
            if kind is None and group is None:
                scope = entry.get("scope")
                label = None
                if isinstance(scope, dict):
                    model = scope.get("model")
                    label = _product_model_label(model)
                if label is None:
                    raw_name = entry.get("name")
                    label = _PRODUCT_MODEL_LABELS.get(
                        "-".join(raw_name.strip().lower().split())
                        if isinstance(raw_name, str)
                        else ""
                    )
                add(label or "limit", entry)
                continue
            if kind != "weekly_scoped" or group != "weekly":
                continue
            scope = entry.get("scope")
            model = scope.get("model") if isinstance(scope, dict) else None
            if not isinstance(model, dict):
                continue
            name = _product_model_label(model)
            model_id = model.get("id")
            normalized_id = (
                "-".join(model_id.strip().lower().split())
                if isinstance(model_id, str)
                else ""
            )
            normalized = normalized_id or (name or "").lower()
            if (
                not name
                or normalized in {"all-models", "all_models"}
                or normalized.endswith("-all-models")
                or name.lower() == "all models"
                or normalized in seen_scopes
            ):
                continue
            seen_scopes.add(normalized)
            add(
                f"{name} only",
                entry,
                7 * 24 * 60,
                percent_key="percent",
            )

    for key in (
        "seven_day_routines",
        "seven_day_claude_routines",
        "claude_routines",
        "routines",
        "routine",
        "seven_day_cowork",
        "cowork",
    ):
        if add("Daily Routines", payload.get(key), 7 * 24 * 60):
            break
    return windows


def summary_line(windows: list[dict]) -> str | None:
    if not windows:
        return None
    parts = [
        f"{window['label']} {window['utilization']:.0f}%"
        for window in windows[:3]
    ]
    return "Claude plan: " + " · ".join(parts)
