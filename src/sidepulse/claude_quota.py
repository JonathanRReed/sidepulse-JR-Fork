"""Claude subscription quota: the live read, and the normalization boundary.

`windows_from_payload` stays pure and fixture-testable -- it is where the
schema's growth is absorbed (the flat `five_hour`/`seven_day` keys, the
per-model sub-caps, and the newer `limits[]` array with scoped weekly limits).

`capacity_evidence_from_windows` is the second half of that boundary: it maps
those labelled windows onto lanes the capacity policy already declared, and
drops anything it cannot name. Together they mean a schema change can add a
window without inventing a lane, and can never widen what reaches a consumer.

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

from .capacity_sources import (
    EvidenceMetricKind,
    SupportedCapacityEvidence,
    SupportedLaneEvidence,
)
from .capacity_types import (
    ObservationState,
    QuotaEffect,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from .reset_policy import parse_reset_epoch

MAX_CLAUDE_WINDOWS = 32
CLAUDE_REMOTE_QUOTA_UNSUPPORTED = "claude_remote_quota_unsupported"
CLAUDE_REMOTE_QUOTA_UNAUTHORIZED = "claude_remote_quota_unauthorized"
CLAUDE_REMOTE_QUOTA_RATE_LIMITED = "claude_remote_quota_rate_limited"
CLAUDE_REMOTE_QUOTA_SERVER_ERROR = "claude_remote_quota_server_error"
CLAUDE_REMOTE_QUOTA_NETWORK = "claude_remote_quota_network"
CLAUDE_REMOTE_QUOTA_NO_WINDOWS = "claude_remote_quota_no_windows"
CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN = "claude_remote_quota_needs_sign_in"
#: A 400/401 that is NOT invalid_grant: the request or the client was
#: refused, which says nothing about whether the sign-in still works.
CLAUDE_REMOTE_QUOTA_REFRESH_REJECTED = "claude_remote_quota_refresh_rejected"

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
#: Verified against the installed CodexBar binary (2026-08-27): the
#: token host is platform.claude.com, NOT console.anthropic.com.
CLAUDE_OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
#: Claude Code's own public OAuth client (PKCE, no secret) -- the same
#: id CodexBar uses to renew the shared sign-in.
CLAUDE_CODE_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_OAUTH_BETA_HEADER = "oauth-2025-04-20"
CLAUDE_CODE_VERSION_FALLBACK = "2.1.0"
# 10, not 30: this endpoint answers in well under a second when it
# answers at all, and the one time it hung for the full window the user
# was staring at "Last known value" for half a minute AFTER a reconnect
# click had already said "refreshing now" (live, 2026-08-26). A hung
# read that resolves 20 seconds sooner retries 20 seconds sooner.
CLAUDE_USAGE_TIMEOUT_SECONDS = 10.0
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
# The only translation from a provider label to a declared lane identity.
# A window whose label is not in here is dropped rather than force-fitted:
# "Fable only" or a brand-new window must not silently land in the weekly
# lane and be read as the weekly ceiling.
CLAUDE_LANE_IDENTITIES = {
    "5-hour": ("five-hour", None, QuotaEffect.ALL_WORKLOADS),
    "weekly": ("weekly", None, QuotaEffect.ALL_WORKLOADS),
    "Opus only": ("weekly", "opus", QuotaEffect.MODEL),
    "Sonnet only": ("weekly", "sonnet", QuotaEffect.MODEL),
}
# One machine holds one Claude Code credential at a time, so this scopes reset
# continuity to "the subscription currently signed in". It is deliberately NOT
# an account identifier and nothing derived from the token ever reaches it --
# switching accounts fails closed (the old cycle disputes) rather than
# carrying one account's reset boundary onto another's.
CLAUDE_ACCOUNT_SCOPE = "claude-consumer"
# The one authentication mode the `anthropic-consumer` policy declares. It
# rides along on every observation so an account binding can be exact about
# which credential produced the reading; `test_claude_capacity_plane` pins it
# against the policy so the two cannot drift apart.
CLAUDE_AUTH_MODE = "consumer"


class ClaudeQuotaUnavailableError(RuntimeError):
    pass


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

    Renewal is possible from here -- see `refresh_claude_payload`, which
    does what CodexBar does: refresh with Claude Code's own public client
    and WRITE THE ROTATED TOKENS BACK so `claude` keeps working. This
    predicate only answers "is the stored access token usable as-is".
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


def refreshable_credential(raw: object) -> bool:
    """True when the Keychain payload holds a refresh token at all."""
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        payload = json.loads(raw)
    except ValueError:
        return False
    oauth = payload.get("claudeAiOauth") if isinstance(payload, dict) else None
    refresh = oauth.get("refreshToken") if isinstance(oauth, dict) else None
    return isinstance(refresh, str) and bool(refresh.strip())


def _oauth_error_code(body: bytes) -> str | None:
    """The OAuth 2.0 `error` field, lowercased, or None.

    Never returns the description: the body can name the account, and
    this module's contract is reason codes only, never server prose.
    """
    try:
        document = json.loads(bytes(body).decode("utf-8"))
    except (AttributeError, UnicodeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    code = document.get("error")
    if isinstance(code, dict):  # {"error": {"type": ...}} shape
        code = code.get("type")
    return code.strip().lower() if isinstance(code, str) else None


def refresh_token_expired(raw: object, now: float) -> bool:
    """True when the REFRESH token itself is spent.

    Claude Code stores `refreshTokenExpiresAt` (ms) beside the access
    token. Only when that has passed is a human sign-in genuinely
    required -- an expired ACCESS token is just renewal work
    (2026-08-27: a live credential had 20 days of refresh validity left
    while the app was telling the owner to sign in again).
    """
    if not isinstance(raw, str) or not raw.strip():
        return True
    try:
        payload = json.loads(raw)
    except ValueError:
        return True
    oauth = payload.get("claudeAiOauth") if isinstance(payload, dict) else None
    if not isinstance(oauth, dict):
        return True
    value = oauth.get("refreshTokenExpiresAt")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        # Absent is not evidence of expiry -- let the server decide.
        return False
    seconds = float(value) / 1000.0 if float(value) > 1e11 else float(value)
    return math.isfinite(seconds) and now >= seconds


def post_form_via_apple_stack(
    url: str,
    fields: dict[str, str],
    *,
    timeout: float,
) -> tuple[int, bytes]:
    """POST a form through NSURLSession, not urllib.

    The token endpoint sits behind Cloudflare, which fingerprints the
    client: urllib is answered with 429s and finally a 1010 ban, while
    the same request through Apple's networking stack succeeds
    (verified live 2026-08-27, and it is why CodexBar -- a URLSession
    app -- never hit this wall). The app is already PyObjC, so the
    honest fix is to use the platform's own client.
    """
    import threading
    from urllib.parse import urlencode

    from Foundation import (
        NSURL,
        NSData,
        NSMutableURLRequest,
        NSURLSession,
    )

    body = urlencode(fields).encode("utf-8")
    request = NSMutableURLRequest.requestWithURL_(NSURL.URLWithString_(url))
    request.setHTTPMethod_("POST")
    request.setValue_forHTTPHeaderField_(
        "application/x-www-form-urlencoded; charset=utf-8", "Content-Type"
    )
    request.setValue_forHTTPHeaderField_("application/json", "Accept")
    request.setHTTPBody_(NSData.dataWithBytes_length_(body, len(body)))

    finished = threading.Event()
    outcome: dict[str, object] = {}

    def handler(data, response, error) -> None:
        outcome["status"] = (
            int(response.statusCode()) if response is not None else 0
        )
        outcome["body"] = bytes(data) if data is not None else b""
        outcome["error"] = error is not None
        finished.set()

    NSURLSession.sharedSession().dataTaskWithRequest_completionHandler_(
        request, handler
    ).resume()
    if not finished.wait(max(1.0, float(timeout))):
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_NETWORK)
    if outcome.get("error") or not outcome.get("status"):
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_NETWORK)
    return int(outcome["status"]), bytes(outcome["body"])


def refresh_claude_payload(
    raw: str,
    *,
    now: float,
    poster=None,
    timeout: float = CLAUDE_USAGE_TIMEOUT_SECONDS,
) -> tuple[str, ClaudeOAuthCredential]:
    """Renew Claude Code's own rotating sign-in.

    POSTs the stored refresh token to Anthropic's token endpoint under
    Claude Code's own public client id and returns the REBUILT Keychain
    payload (every unrelated field preserved) plus the new credential.

    THE CONTRACT THAT KEEPS `claude` ALIVE: the refresh token rotates on
    use. The caller MUST write the returned payload back to the Keychain
    item -- holding the new tokens privately would strand Claude Code on
    a dead refresh token and sign the user out of their actual tooling.

    NOTE ON PRECEDENT (corrected 2026-08-27): CodexBar does NOT do this.
    For Claude-CLI-owned credentials it DELEGATES refresh back to the
    CLI (running `claude /status` in a PTY and watching the Keychain
    item's stamp change), and direct-refreshes only its own credentials
    into its own cache -- so it never writes that item. We diverge
    deliberately: no PTY subprocess and no hard dependency on `claude`
    being installed, at the cost of owning the write-back above. Their
    delegated path is reported unreliable in steipete/CodexBar#1287.
    Failures raise ClaudeQuotaUnavailableError with a reason code only.
    """
    try:
        payload = json.loads(raw)
    except ValueError:
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN) from None
    oauth = payload.get("claudeAiOauth") if isinstance(payload, dict) else None
    refresh_token = oauth.get("refreshToken") if isinstance(oauth, dict) else None
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN)

    transport = poster or post_form_via_apple_stack
    try:
        status, body = transport(
            CLAUDE_OAUTH_TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token.strip(),
                "client_id": CLAUDE_CODE_OAUTH_CLIENT_ID,
            },
            timeout=timeout,
        )
    except ClaudeQuotaUnavailableError:
        raise
    except Exception:
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_NETWORK) from None
    if status in {400, 401}:
        # ONLY invalid_grant is terminal. The same statuses also carry
        # invalid_request / unsupported_grant_type / invalid_client --
        # transient or client-side faults that say nothing about the
        # sign-in. Treating every 400 as "go re-login" is what wedges a
        # provider behind a terminal gate for an hour (CodexBar makes
        # exactly this distinction in refreshFailureDisposition).
        # 403 is not here either: that is Cloudflare refusing the
        # CLIENT, not the credential.
        if _oauth_error_code(body) == "invalid_grant":
            raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN)
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_REFRESH_REJECTED)
    if status == 429:
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_RATE_LIMITED)
    if status != 200:
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_SERVER_ERROR)
    try:
        answer = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_SERVER_ERROR) from None
    access = answer.get("access_token") if isinstance(answer, dict) else None
    if not isinstance(access, str) or not access.strip():
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_SERVER_ERROR)
    rotated = answer.get("refresh_token")
    expires_in = answer.get("expires_in")
    expires_at_ms = None
    if (
        not isinstance(expires_in, bool)
        and isinstance(expires_in, (int, float))
        and math.isfinite(float(expires_in))
        and expires_in > 0
    ):
        expires_at_ms = int((now + float(expires_in)) * 1000.0)
    if expires_at_ms is None:
        # Without a lifetime we would write back the OLD expiresAt, read
        # it as already expired next cycle, and refresh again forever --
        # burning a refresh-token rotation every pass. Refuse instead.
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_SERVER_ERROR)
    rebuilt = dict(payload)
    rebuilt_oauth = dict(oauth)
    rebuilt_oauth["accessToken"] = access.strip()
    if isinstance(rotated, str) and rotated.strip():
        rebuilt_oauth["refreshToken"] = rotated.strip()
    if expires_at_ms is not None:
        rebuilt_oauth["expiresAt"] = expires_at_ms
    rebuilt["claudeAiOauth"] = rebuilt_oauth
    credential = ClaudeOAuthCredential(
        access_token=access.strip(),
        expires_at=expires_at_ms / 1000.0 if expires_at_ms is not None else None,
        subscription_type=(
            rebuilt_oauth.get("subscriptionType")
            if isinstance(rebuilt_oauth.get("subscriptionType"), str)
            else None
        ),
    )
    return json.dumps(rebuilt, separators=(",", ":")), credential


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


def _lane_evidence(
    descriptor,
    window: object,
    *,
    observed_at: float,
) -> SupportedLaneEvidence | None:
    """Map one labelled window onto one already-declared lane, or nothing."""
    if not isinstance(window, dict):
        return None
    identity = CLAUDE_LANE_IDENTITIES.get(window.get("label"))
    if identity is None:
        return None
    window_id, model, effect = identity
    lane = next(
        (
            candidate
            for candidate in descriptor.lanes
            if candidate.key.window == window_id
            and candidate.key.model == model
            and candidate.key.effect is effect
        ),
        None,
    )
    if lane is None:
        return None

    utilization = window.get("utilization")
    if (
        isinstance(utilization, bool)
        or not isinstance(utilization, (int, float))
        or not math.isfinite(float(utilization))
    ):
        return None

    # `parse_reset_epoch` is the one place that accepts both the ISO string
    # and the epoch/millisecond forms this endpoint has used, and it returns
    # None for anything that is not a credible future boundary. A window with
    # no such boundary stays honestly reset-less: UNKNOWN carries no epoch, so
    # nothing downstream can render a countdown we did not observe.
    reset_epoch = parse_reset_epoch(window.get("resets_at"), now=observed_at)
    minutes = window.get("window_minutes")
    return SupportedLaneEvidence(
        key=lane.key,
        metric_kind=EvidenceMetricKind.PERCENT_USED,
        # `windows_from_payload` already bounds this; bounding again keeps one
        # out-of-range window from raising and taking the whole batch --
        # including the sub-cap the owner needs -- down with it.
        percent=max(0.0, min(100.0, float(utilization))),
        state=ObservationState.OBSERVED,
        reset_state=(
            ResetState.FUTURE if reset_epoch is not None else ResetState.UNKNOWN
        ),
        reset_epoch=reset_epoch,
        window_minutes=(
            float(minutes)
            if not isinstance(minutes, bool)
            and isinstance(minutes, (int, float))
            and math.isfinite(float(minutes))
            and float(minutes) > 0.0
            else None
        ),
    )


def capacity_evidence_from_windows(
    descriptor,
    windows,
    *,
    observed_at: float,
) -> SupportedCapacityEvidence:
    """Project fetched windows onto the declared lanes of one exact source.

    The descriptor owns lane identity; this only decides which declared lane
    each provider label refers to. Utilization is percent USED here and stays
    used-first all the way into `SupportedLaneEvidence` -- the single
    conversion to remaining belongs to `capacity_sources`, so there is exactly
    one place that can invert it wrongly.
    """
    if descriptor.source != CLAUDE_QUOTA_SOURCE:
        raise ValueError("claude capacity descriptor mismatch")
    matched: dict[object, SupportedLaneEvidence] = {}
    for window in windows or ():
        evidence = _lane_evidence(descriptor, window, observed_at=observed_at)
        if evidence is not None:
            matched.setdefault(evidence.key, evidence)
    # Declaration order, not response order: the payload has moved windows
    # around between schema versions, and the ledger's reading order is a
    # product decision (shortest window first, then the sub-caps).
    return SupportedCapacityEvidence(
        source=CLAUDE_QUOTA_SOURCE,
        health_kind=SourceHealthKind.HEALTHY,
        lanes=tuple(
            matched[lane.key] for lane in descriptor.lanes if lane.key in matched
        ),
        account_discriminator=CLAUDE_ACCOUNT_SCOPE,
        has_last_known_good=False,
        auth_mode=CLAUDE_AUTH_MODE,
    )
