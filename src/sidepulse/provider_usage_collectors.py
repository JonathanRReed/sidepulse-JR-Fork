"""Bounded first-party provider usage collectors.

Each collector owns one ordered, provider-specific source boundary. It returns
an actionable ``ProviderUsageSnapshot`` and never reduces an authentication,
permission, or transport failure to a generic "no reading" row.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import subprocess
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from .provider_usage_parsers import (
    parse_antigravity_usage,
    parse_cursor_usage,
    parse_devin_usage,
    parse_grok_usage,
    parse_openai_api_usage,
)
import base64
import shutil
from .provider_usage_platform import ProviderSourceState, ProviderUsageSnapshot, UsageLane
from .provider_usage_settings import ProviderPreference

HTTP_TIMEOUT_SECONDS = 20.0
HTTP_MAX_BYTES = 2 * 1024 * 1024
CURSOR_TOKEN_MAX_BYTES = 64 * 1024
GROK_AUTH_MAX_BYTES = 256 * 1024


class ProviderHttpError(RuntimeError):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = int(status)
        self.reason = str(reason)


@dataclass(frozen=True, slots=True)
class _Credential:
    available: bool
    secret: str | None
    reason: str | None = None


def _default_http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: object = None,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> object:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderHttpError(0, "invalid_url")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ProviderHttpError(0, "cleartext_refused")
    payload = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = Request(url, data=payload, headers=request_headers, method=method)
    context = None
    if parsed.scheme == "https" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        context = ssl._create_unverified_context()
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            data = response.read(HTTP_MAX_BYTES + 1)
            status = int(getattr(response, "status", 200))
    except HTTPError as error:
        raise ProviderHttpError(error.code, "http_error") from None
    except (URLError, OSError, TimeoutError, ValueError):
        raise ProviderHttpError(0, "network_error") from None
    if status < 200 or status >= 300:
        raise ProviderHttpError(status, "http_error")
    if len(data) > HTTP_MAX_BYTES:
        raise ProviderHttpError(status, "response_too_large")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProviderHttpError(status, "invalid_json") from None


def _failure(
    provider_id: str,
    *,
    observed_at: float,
    state: ProviderSourceState,
    reason: str,
    action: str,
) -> ProviderUsageSnapshot:
    return ProviderUsageSnapshot(
        provider_id=provider_id,
        account_label=None,
        observed_at=observed_at,
        state=state,
        reason_code=reason,
        action_label=action,
        lanes=(),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
    )


#: Who actually owns each sign-in. "Reconnect X" is only honest where
#: this app can repair the credential itself; where a CLI owns it, the
#: row must name the command that works (2026-08-27: a rejected Grok
#: token showed "Reconnect Grok", which repairs nothing).
_AUTH_ACTION_BY_PROVIDER: dict[str, str] = {
    "grok": "Run grok login",
    "codex": "Run codex login",
    "antigravity": "Open Antigravity or run agy",
}


def _auth_action(provider_id: str) -> str:
    return _AUTH_ACTION_BY_PROVIDER.get(
        provider_id,
        f"Reconnect {provider_id.replace('-', ' ').title()}",
    )


def _http_failure(provider_id: str, observed_at: float, error: ProviderHttpError) -> ProviderUsageSnapshot:
    if error.status in {401, 403}:
        return _failure(
            provider_id,
            observed_at=observed_at,
            state=ProviderSourceState.NEEDS_SIGN_IN,
            reason="authentication_required",
            action=_auth_action(provider_id),
        )
    if error.status == 429:
        return _failure(
            provider_id,
            observed_at=observed_at,
            state=ProviderSourceState.RATE_LIMITED,
            reason="rate_limited",
            action="Retry later",
        )
    return _failure(
        provider_id,
        observed_at=observed_at,
        state=ProviderSourceState.UNAVAILABLE,
        reason="network_unavailable",
        action="Retry",
    )


def _valid_secret(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or "\x00" in candidate or len(candidate.encode("utf-8")) > 64 * 1024:
        return None
    return candidate


def _credential(credentials, provider_id: str, account: str) -> _Credential:
    try:
        result = credentials.get(provider_id, account)
    except Exception:
        return _Credential(False, None, "keychain_unavailable")
    secret = _valid_secret(getattr(result, "secret", None))
    return _Credential(
        bool(getattr(result, "available", False) and secret),
        secret,
        getattr(result, "reason", None),
    )


def _cursor_db_path(home: Path) -> Path:
    return home / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def _read_cursor_token(path: Path) -> str | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if not path.is_file() or path.is_symlink() or info.st_size > 128 * 1024 * 1024:
        return None
    uri = f"file:{quote(str(path))}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=1.0) as connection:
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1",
                ("cursorAuth/accessToken",),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    if not row:
        return None
    raw = row[0]
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > CURSOR_TOKEN_MAX_BYTES:
        return None
    try:
        decoded = json.loads(raw)
    except ValueError:
        decoded = raw
    if isinstance(decoded, dict):
        decoded = decoded.get("accessToken") or decoded.get("token")
    return _valid_secret(decoded)


def collect_cursor(
    preference: ProviderPreference,
    *,
    home: Path,
    observed_at: float,
    credentials=None,
    http_json: Callable[..., object] = _default_http_json,
) -> ProviderUsageSnapshot:
    token = _read_cursor_token(_cursor_db_path(Path(home)))
    if token is None and credentials is not None:
        # The staged Import flow stores a pasted session here; without
        # this read the import claimed success and changed nothing.
        stored = _credential(credentials, "cursor", "token")
        if stored.available and stored.secret:
            token = stored.secret
    if token is None:
        return _failure(
            "cursor",
            observed_at=observed_at,
            state=(
                ProviderSourceState.SOURCE_NOT_FOUND
                if preference.browser_sources
                else ProviderSourceState.NEEDS_CONSENT
            ),
            reason=(
                "browser_session_not_imported"
                if preference.browser_sources
                else "browser_consent_required"
            ),
            action=(
                "Import Cursor browser session"
                if preference.browser_sources
                else "Enable Cursor browser access"
            ),
        )
    def _fetch(bearer: str):
        headers = {"Authorization": f"Bearer {bearer}"}
        account = http_json(
            "GET",
            "https://cursor.com/api/auth/me",
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        usage = http_json(
            "GET",
            "https://cursor.com/api/usage-summary",
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        return account, usage

    try:
        account, usage = _fetch(token)
    except ProviderHttpError as error:
        # The Cursor app's own database token wins by default, but a
        # STALE one made the imported credential unreachable: Reconnect
        # cleared the store, the user pasted a fresh key, and the
        # collector went right back to the stale database token -- 401
        # forever (audit, 2026-08-26). On an auth rejection, try the
        # imported credential before reporting failure.
        stored = (
            _credential(credentials, "cursor", "token")
            if credentials is not None
            else None
        )
        fallback = (
            stored.secret
            if stored is not None and stored.available and stored.secret
            else None
        )
        if (
            error.status in (401, 403)
            and fallback is not None
            and fallback != token
        ):
            try:
                account, usage = _fetch(fallback)
            except ProviderHttpError as retry_error:
                return _http_failure("cursor", observed_at, retry_error)
        else:
            return _http_failure("cursor", observed_at, error)
    if not isinstance(usage, dict):
        return _failure(
            "cursor",
            observed_at=observed_at,
            state=ProviderSourceState.ERROR,
            reason="invalid_provider_response",
            action="Retry",
        )
    payload = dict(usage)
    payload["account"] = account if isinstance(account, dict) else {}
    try:
        return parse_cursor_usage(payload, observed_at=observed_at)
    except ValueError:
        return _failure(
            "cursor",
            observed_at=observed_at,
            state=ProviderSourceState.ERROR,
            reason="invalid_provider_response",
            action="Retry",
        )


#: Devin's web app is what issues these session tokens, and the API
#: rejects a request that does not look like it came from that app.
DEVIN_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)


def collect_devin(
    preference: ProviderPreference,
    *,
    observed_at: float,
    credentials,
    http_json: Callable[..., object] = _default_http_json,
) -> ProviderUsageSnapshot:
    token = _credential(credentials, "devin", "token")
    organization = preference.option("organization")
    organization_id = preference.option("organization_id")
    secret = token.secret if token.available else None

    if secret is None and (organization_id or organization) and preference.browser_sources:
        try:
            from .browser_session_import import import_devin_session
            session = import_devin_session(Path.home())
            if session is not None and session.token:
                secret = session.token
        except Exception:
            pass

    if secret is None:
        return _failure(
            "devin",
            observed_at=observed_at,
            state=(
                ProviderSourceState.SOURCE_NOT_FOUND
                if preference.browser_sources
                else ProviderSourceState.NEEDS_CONSENT
            ),
            reason=(
                "browser_session_not_imported"
                if preference.browser_sources
                else "browser_consent_required"
            ),
            action=(
                "Import Devin browser session"
                if preference.browser_sources
                else "Enable Devin browser access"
            ),
        )
    if not organization and not organization_id:
        return _failure(
            "devin",
            observed_at=observed_at,
            state=ProviderSourceState.SOURCE_NOT_FOUND,
            reason="organization_required",
            action="Choose Devin organization",
        )
    # The internal id is the path segment the endpoint actually answers
    # on; the slug form is kept as a fallback. quote(safe="") used to
    # escape the slash in "org/<slug>" into %2F, so the only org shape
    # settings could hold was one this URL could never express.
    endpoint = (
        "https://app.devin.ai/api/"
        f"{quote(organization_id or organization, safe='/')}"
        "/billing/quota/usage"
    )
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": DEVIN_BROWSER_USER_AGENT,
        "Authorization": f"Bearer {secret}",
    }
    if organization_id:
        # Without this the endpoint answers 401 for a perfectly valid
        # session token -- confirmed live against a real account.
        headers["x-cog-org-id"] = organization_id
    try:
        payload = http_json(
            "GET",
            endpoint,
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except ProviderHttpError as error:
        if error.status == 401 and preference.browser_sources:
            try:
                from .browser_session_import import import_devin_session
                session = import_devin_session(Path.home())
                if session is not None and session.token and session.token != secret:
                    secret = session.token
                    if session.organization:
                        organization = session.organization
                    if session.internal_organization_id:
                        organization_id = session.internal_organization_id
                    if hasattr(credentials, "set"):
                        try:
                            credentials.set("devin", "token", secret)
                        except Exception:
                            pass
                    endpoint = (
                        "https://app.devin.ai/api/"
                        f"{quote(organization_id or organization, safe='/')}"
                        "/billing/quota/usage"
                    )
                    headers["Authorization"] = f"Bearer {secret}"
                    if organization_id:
                        headers["x-cog-org-id"] = organization_id
                    payload = http_json(
                        "GET",
                        endpoint,
                        headers=headers,
                        timeout=HTTP_TIMEOUT_SECONDS,
                    )
                else:
                    return _http_failure("devin", observed_at, error)
            except Exception:
                return _http_failure("devin", observed_at, error)
        else:
            return _http_failure("devin", observed_at, error)
    if not isinstance(payload, dict):
        return _failure(
            "devin",
            observed_at=observed_at,
            state=ProviderSourceState.ERROR,
            reason="invalid_provider_response",
            action="Retry",
        )
    document = dict(payload)
    document.setdefault("organization", organization or organization_id)
    try:
        return parse_devin_usage(document, observed_at=observed_at)
    except ValueError:
        return _failure(
            "devin",
            observed_at=observed_at,
            state=ProviderSourceState.ERROR,
            reason="invalid_provider_response",
            action="Retry",
        )


def _read_grok_auth(home: Path, observed_at: float) -> tuple[str, str | None] | None:
    path = Path(home) / ".grok" / "auth.json"
    try:
        if path.is_symlink() or path.stat().st_size > GROK_AUTH_MAX_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    candidates: list[tuple[int, str, str | None]] = []
    for key, value in tuple(payload.items())[:64]:
        if not isinstance(value, dict):
            continue
        token = _valid_secret(value.get("key"))
        expiry = value.get("expires_at")
        if token is None or (
            not isinstance(expiry, bool)
            and isinstance(expiry, (int, float))
            and math.isfinite(float(expiry))
            and float(expiry) <= observed_at
        ):
            continue
        priority = 0 if str(key).startswith("https://auth.x.ai::") else 1
        email = value.get("email")
        candidates.append((priority, token, str(email).strip() if email else None))
    if not candidates:
        return None
    _priority, token, email = min(candidates, key=lambda item: item[0])
    return token, email


def collect_grok(
    preference: ProviderPreference,
    *,
    home: Path,
    observed_at: float,
    credentials,
    http_json: Callable[..., object] = _default_http_json,
) -> ProviderUsageSnapshot:
    auth = _read_grok_auth(Path(home), observed_at)
    if auth is None:
        stored = _credential(credentials, "grok", "token")
        auth = (stored.secret, None) if stored.available and stored.secret else None
    if auth is None:
        return _failure(
            "grok",
            observed_at=observed_at,
            state=ProviderSourceState.NEEDS_SIGN_IN,
            reason="authentication_required",
            action="Run grok login",
        )
    token, email = auth
    try:
        payload = http_json(
            "GET",
            "https://cli-chat-proxy.grok.com/v1/billing?format=credits",
            headers={
                "Authorization": f"Bearer {token}",
                "x-xai-token-auth": "xai-grok-cli",
                "Accept": "application/json",
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except ProviderHttpError as error:
        return _http_failure("grok", observed_at, error)
    if not isinstance(payload, dict):
        return _failure(
            "grok",
            observed_at=observed_at,
            state=ProviderSourceState.ERROR,
            reason="invalid_provider_response",
            action="Retry",
        )
    document = dict(payload)
    if email:
        document["email"] = email
    try:
        return parse_grok_usage(document, observed_at=observed_at)
    except ValueError:
        return _failure(
            "grok",
            observed_at=observed_at,
            state=ProviderSourceState.ERROR,
            reason="invalid_provider_response",
            action="Retry",
        )


def _validated_loopback_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    return value.rstrip("/")


def _discover_antigravity_endpoint(
    command_runner: Callable[[list[str], float], str] | None = None,
) -> tuple[str | None, str | None]:
    try:
        if command_runner is not None:
            output = command_runner(["ps", "-eo", "pid,args"], 1.5)
        else:
            output = subprocess.run(
                ["ps", "-eo", "pid,args"],
                capture_output=True,
                text=True,
                timeout=1.5,
            ).stdout
    except Exception:
        return None, None

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        pid_str, cmd = parts[0], parts[1]
        if not pid_str.isdigit():
            continue
        if "python" in cmd.lower() or "pytest" in cmd.lower():
            continue
        if any(marker in cmd for marker in ("language_server_macos", "language_server")):
            if "agy_acp_server" in cmd or "localharness_external" in cmd:
                continue
            csrf = None
            csrf_match = re.search(r"--csrf[-_]token[=\s]+([^\s]+)", cmd)
            if csrf_match:
                csrf = csrf_match.group(1)
            try:
                if command_runner is not None:
                    lsof_out = command_runner(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", pid_str], 1.5)
                else:
                    lsof_out = subprocess.run(
                        ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", pid_str],
                        capture_output=True,
                        text=True,
                        timeout=1.5,
                    ).stdout
                ports = re.findall(r":(\d+)\s+\(LISTEN\)", lsof_out)
                if ports:
                    return f"http://127.0.0.1:{ports[0]}", csrf
            except Exception:
                continue
    return None, None


def collect_antigravity(
    preference: ProviderPreference,
    *,
    observed_at: float,
    http_json: Callable[..., object] = _default_http_json,
    command_runner: Callable[[list[str], float], str] | None = None,
    home: Path | None = None,
) -> ProviderUsageSnapshot:
    endpoint = _validated_loopback_endpoint(preference.option("endpoint"))
    csrf_token = preference.option("csrf_token")
    if endpoint is None:
        discovered_endpoint, discovered_csrf = _discover_antigravity_endpoint(command_runner)
        if discovered_endpoint is not None:
            endpoint = discovered_endpoint
            if not csrf_token:
                csrf_token = discovered_csrf
    if endpoint is not None:
        url = endpoint + "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
        headers = {"Connect-Protocol-Version": "1"}
        if csrf_token:
            headers["X-Codeium-Csrf-Token"] = csrf_token
        try:
            payload = http_json(
                "POST",
                url,
                headers=headers,
                body={
                    "ideName": "antigravity",
                    "extensionName": "antigravity",
                    "locale": "en",
                    "ideVersion": "unknown",
                },
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except ProviderHttpError as error:
            return _http_failure("antigravity", observed_at, error)
        try:
            return parse_antigravity_usage(payload, observed_at=observed_at)
        except ValueError:
            return _failure(
                "antigravity",
                observed_at=observed_at,
                state=ProviderSourceState.ERROR,
                reason="invalid_provider_response",
                action="Retry",
            )

    gemini_dir = (home or Path.home()) / ".gemini"
    creds_path = gemini_dir / "oauth_creds.json"
    account_label = None
    if creds_path.is_file():
        try:
            with creds_path.open("r", encoding="utf-8") as f:
                cdata = json.load(f)
            id_token = cdata.get("id_token")
            if id_token and isinstance(id_token, str) and "." in id_token:
                parts = id_token.split(".")
                if len(parts) >= 2:
                    pad = -len(parts[1]) % 4
                    payload_json = base64.urlsafe_b64decode(parts[1] + ("=" * pad))
                    payload = json.loads(payload_json)
                    account_label = payload.get("email")
            if not account_label:
                account_label = cdata.get("email")
        except Exception:
            pass

    summaries_path = gemini_dir / "antigravity-cli" / "conversation_summaries.db"
    cli_configured = creds_path.is_file() or summaries_path.is_file() or (shutil.which("agy") is not None)
    if cli_configured and (account_label or creds_path.is_file()):
        return ProviderUsageSnapshot(
            provider_id="antigravity",
            account_label=account_label or "Google Account",
            observed_at=observed_at,
            state=ProviderSourceState.READY,
            reason_code=None,
            action_label=None,
            lanes=(
                UsageLane(
                    provider_id="antigravity",
                    lane_id="cli",
                    label="Antigravity CLI",
                    remaining_percent=100.0,
                    reset_at=None,
                    scope="session",
                    model="Gemini 3.8 Flash",
                    feature=None,
                    bindable=True,
                    source_id="antigravity-oauth",
                ),
            ),
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            model_count=1,
            estimated_cost_usd=None,
            cache_savings_usd=None,
            credits_remaining=None,
            incident=None,
        )

    return _failure(
        "antigravity",
        observed_at=observed_at,
        state=ProviderSourceState.SOURCE_NOT_FOUND,
        reason="local_server_not_found",
        action="Open Antigravity or run agy",
    )


def collect_openai_api(
    preference: ProviderPreference,
    *,
    observed_at: float,
    credentials,
    http_json: Callable[..., object] = _default_http_json,
) -> ProviderUsageSnapshot:
    del preference
    credential = _credential(credentials, "openai-api", "admin-key")
    if not credential.available:
        return _failure(
            "openai-api",
            observed_at=observed_at,
            state=ProviderSourceState.NEEDS_SIGN_IN,
            reason="authentication_required",
            action="Add OpenAI Admin key",
        )
    start_time = max(0, int(observed_at - 30 * 24 * 60 * 60))
    query = urlencode({"start_time": start_time, "bucket_width": "1d", "limit": 31})
    headers = {"Authorization": f"Bearer {credential.secret}"}
    try:
        usage = http_json(
            "GET",
            f"https://api.openai.com/v1/organization/usage/completions?{query}",
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        costs = http_json(
            "GET",
            f"https://api.openai.com/v1/organization/costs?{query}",
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except ProviderHttpError as error:
        return _http_failure("openai-api", observed_at, error)
    try:
        return parse_openai_api_usage(
            {"usage": usage, "costs": costs}, observed_at=observed_at
        )
    except ValueError:
        return _failure(
            "openai-api",
            observed_at=observed_at,
            state=ProviderSourceState.ERROR,
            reason="invalid_provider_response",
            action="Retry",
        )


def collect_opencode(
    preference: ProviderPreference,
    *,
    observed_at: float,
    home: Path | None = None,
) -> ProviderUsageSnapshot:
    del preference
    root = (home or Path.home()) / ".local" / "share" / "opencode"
    db_path = root / "opencode.db"
    auth_path = root / "auth.json"
    if not db_path.is_file() and not auth_path.is_file():
        return _failure(
            "opencode",
            observed_at=observed_at,
            state=ProviderSourceState.SOURCE_NOT_FOUND,
            reason="opencode_data_not_found",
            action="Open OpenCode",
        )

    account_label = None
    if auth_path.is_file():
        try:
            with auth_path.open("r", encoding="utf-8") as f:
                auth_data = json.load(f)
                if isinstance(auth_data, dict):
                    for p_key in ("github-copilot", "google", "opencode"):
                        if p_key in auth_data:
                            account_label = p_key
                            break
                    if not account_label and auth_data:
                        account_label = next(iter(auth_data.keys()))
        except Exception:
            pass

    is_rate_limited = False
    rate_limit_reset_at = None
    remaining_percent = 100.0
    state = ProviderSourceState.READY
    reason_code = None
    action_label = None
    input_tokens = 0
    output_tokens = 0
    model_count = 0

    if db_path.is_file():
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = con.execute(
                "SELECT time_created, data FROM message WHERE data LIKE '%FreeUsageLimitError%' OR data LIKE '%Rate limit exceeded%' ORDER BY time_created DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                err_time_ms, _err_data = row
                err_epoch = err_time_ms / 1000.0 if err_time_ms > 1e10 else float(err_time_ms)
                cooldown_window = 3600.0
                elapsed = observed_at - err_epoch
                if 0 <= elapsed < cooldown_window:
                    is_rate_limited = True
                    rate_limit_reset_at = err_epoch + cooldown_window
                    remaining_percent = max(0.0, min(100.0, (elapsed / cooldown_window) * 100.0))
                    state = ProviderSourceState.RATE_LIMITED
                    reason_code = "rate_limit_exceeded"
                    action_label = "Open OpenCode"

            session_cursor = con.execute(
                "SELECT SUM(tokens_input), SUM(tokens_output), COUNT(DISTINCT model) FROM session"
            )
            s_row = session_cursor.fetchone()
            if s_row:
                input_tokens = int(s_row[0] or 0)
                output_tokens = int(s_row[1] or 0)
                model_count = int(s_row[2] or 0)
            con.close()
        except Exception:
            pass

    lane_label = "Free Tier" if (is_rate_limited or not account_label or "free" in account_label.lower()) else f"{account_label.title()} Quota"
    lane = UsageLane(
        provider_id="opencode",
        lane_id="free-tier",
        label=lane_label,
        remaining_percent=remaining_percent,
        reset_at=rate_limit_reset_at,
        scope="session",
        model=None,
        feature=None,
        bindable=True,
        source_id="opencode-db",
    )

    return ProviderUsageSnapshot(
        provider_id="opencode",
        account_label=account_label or "Free Tier",
        observed_at=observed_at,
        state=state,
        reason_code=reason_code,
        action_label=action_label,
        lanes=(lane,),
        input_tokens=input_tokens,
        cached_input_tokens=0,
        output_tokens=output_tokens,
        model_count=max(1, model_count),
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
    )


__all__ = [
    "ProviderHttpError",
    "collect_antigravity",
    "collect_cursor",
    "collect_devin",
    "collect_grok",
    "collect_opencode",
    "collect_openai_api",
]
