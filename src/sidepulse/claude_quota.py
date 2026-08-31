"""Claude subscription quota: the live read, and the normalization boundary.

`windows_from_payload` stays pure and fixture-testable -- it is where the
schema's growth is absorbed (the flat `five_hour`/`seven_day` keys, the
per-model sub-caps, and the newer `limits[]` array with scoped weekly limits).

`capacity_evidence_from_windows` is the second half of that boundary: it maps
those labelled windows onto lanes the capacity policy already declared, and
drops anything it cannot name. Together they mean a schema change can add a
window without inventing a lane, and can never widen what reaches a consumer.

`fetch_windows` performs the actual read against the same OAuth usage endpoint
Claude Code itself uses, presenting a current access token. It does not
discover, renew, or mutate that externally owned credential: obtaining one
requires user consent and belongs to `credentials`, which never raises a
Keychain dialog on a background timer. Called without a token this still fails
closed, exactly as before.

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
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
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

    JR Bar treats this as an action for Claude Code itself. It never
    consumes the refresh token or changes Claude Code's Keychain item.
    This predicate only answers "is the stored access token usable as-is".
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


_REDIRECT_GUARD_CLASS = None


def _redirect_guard_class():
    """Build the redirect delegate on first use.

    Defined lazily so this module stays importable without PyObjC --
    the parsers and reason codes here run in headless tests.
    """
    global _REDIRECT_GUARD_CLASS
    if _REDIRECT_GUARD_CLASS is not None:
        return _REDIRECT_GUARD_CLASS

    import objc
    from Foundation import NSObject

    class _SameOriginRedirectGuard(NSObject):
        """Refuse cross-origin redirects on a request carrying a bearer
        token. A 30x to another host would hand the Authorization header
        to whoever answered it. CodexBar's ProviderHTTPClient blocks
        exactly this; urllib follows such redirects silently, which is
        one more reason token requests do not belong on it.
        """

        def initWithOrigin_(self, origin):
            this = objc.super(_SameOriginRedirectGuard, self).init()
            if this is None:
                return None
            this._origin = tuple(origin)
            return this

        def URLSession_task_willPerformHTTPRedirection_newRequest_completionHandler_(
            self, _session, _task, _response, request, handler
        ):
            try:
                url = request.URL()
                origin = (
                    str(url.scheme() or ""),
                    str(url.host() or ""),
                    int(url.port().intValue()) if url.port() is not None else None,
                )
            except Exception:
                handler(None)
                return
            handler(request if origin == self._origin else None)

    _REDIRECT_GUARD_CLASS = _SameOriginRedirectGuard
    return _REDIRECT_GUARD_CLASS


def _origin_of(url: str) -> tuple:
    from Foundation import NSURL

    parsed = NSURL.URLWithString_(url)
    return (
        str(parsed.scheme() or ""),
        str(parsed.host() or ""),
        int(parsed.port().intValue()) if parsed.port() is not None else None,
    )


def request_via_apple_stack(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float,
) -> tuple[int, bytes]:
    """One HTTP round trip through NSURLSession, same-origin only.

    Ephemeral configuration so a token response never reaches the
    on-disk URL cache, and the timeout is set on the REQUEST as well as
    the wait, so an expired wait does not leave a task running.
    """
    import threading

    from Foundation import (
        NSURL,
        NSData,
        NSMutableURLRequest,
        NSURLSession,
        NSURLSessionConfiguration,
    )

    request = NSMutableURLRequest.requestWithURL_(NSURL.URLWithString_(url))
    request.setHTTPMethod_(method)
    for key, value in headers.items():
        request.setValue_forHTTPHeaderField_(value, key)
    if body is not None:
        request.setHTTPBody_(NSData.dataWithBytes_length_(body, len(body)))
    request.setTimeoutInterval_(float(timeout))

    guard = _redirect_guard_class().alloc().initWithOrigin_(_origin_of(url))
    session = NSURLSession.sessionWithConfiguration_delegate_delegateQueue_(
        NSURLSessionConfiguration.ephemeralSessionConfiguration(), guard, None
    )
    finished = threading.Event()
    outcome: dict[str, object] = {}

    def handler(data, response, error) -> None:
        outcome["status"] = int(response.statusCode()) if response is not None else 0
        outcome["body"] = bytes(data) if data is not None else b""
        outcome["error"] = error is not None
        finished.set()

    task = session.dataTaskWithRequest_completionHandler_(request, handler)
    task.resume()
    try:
        if not finished.wait(max(1.0, float(timeout)) + 1.0):
            try:
                task.cancel()
            except Exception:
                pass
            raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_NETWORK)
        if outcome.get("error") or not outcome.get("status"):
            raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_NETWORK)
        return int(outcome["status"]), bytes(outcome["body"])
    finally:
        try:
            session.finishTasksAndInvalidate()
        except Exception:
            pass


def _claude_code_user_agent() -> str:
    """Identify honestly as the client whose credential we are presenting."""
    return f"claude-code/{CLAUDE_CODE_VERSION_FALLBACK}"


def _default_opener(request, timeout: float):
    from urllib.request import urlopen

    return urlopen(request, timeout=timeout)


def fetch_windows(
    *,
    access_token: str | None = None,
    requester=None,
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

    # This call carries a bearer token, so it must not follow a redirect
    # to another host, and it must use the platform network stack.
    transport = requester or request_via_apple_stack
    try:
        status, body = transport(
            CLAUDE_USAGE_URL,
            method="GET",
            headers={
                "Authorization": f"Bearer {access_token.strip()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "anthropic-beta": CLAUDE_OAUTH_BETA_HEADER,
                "User-Agent": _claude_code_user_agent(),
            },
            timeout=timeout,
        )
    except ClaudeQuotaUnavailableError:
        raise
    except Exception:
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_NETWORK) from None
    if status == 401:
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_UNAUTHORIZED)
    if status == 429:
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_RATE_LIMITED)
    if status != 200:
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_SERVER_ERROR)

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
