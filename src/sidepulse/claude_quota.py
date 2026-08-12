"""Official Claude plan-limit utilization, the CodexBar way.

``GET https://api.anthropic.com/api/oauth/usage`` with the user's own
Claude Code OAuth token returns the REAL 5-hour and weekly window
utilization -- no estimation, no transcript math (CodexBar finding #4;
their citation: ClaudeOAuthUsageFetcher.swift).

Token sources, in order:
1. ``~/.claude/.credentials.json`` (``claudeAiOauth.accessToken``) --
   present on some installs, silent to read.
2. The ``Claude Code-credentials`` Keychain item via ``security``.
   Reading it from a background app triggers a ONE-TIME macOS prompt,
   which is why the whole feature is opt-in (Profile pane toggle) and
   this module must never be called before the user turned it on.

The token is used solely against Anthropic's own API and never logged.
Same quiet-failure contract as every watcher: any problem raises
ClaudeQuotaUnavailableError and the caller backs off.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA_HEADER = "oauth-2025-04-20"
CREDENTIALS_PATH = Path("~/.claude/.credentials.json").expanduser()
KEYCHAIN_SERVICE = "Claude Code-credentials"


class ClaudeQuotaUnavailableError(RuntimeError):
    pass


def _token_from_file() -> str | None:
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
    except (OSError, ValueError):
        return None
    oauth = data.get("claudeAiOauth")
    if isinstance(oauth, dict):
        token = oauth.get("accessToken")
        if isinstance(token, str) and token:
            return token
    return None


def _token_from_keychain() -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    # The keychain item stores the whole credentials JSON blob.
    try:
        data = json.loads(raw)
    except ValueError:
        return raw or None
    oauth = data.get("claudeAiOauth")
    if isinstance(oauth, dict):
        token = oauth.get("accessToken")
        if isinstance(token, str) and token:
            return token
    return None


def access_token() -> str:
    token = _token_from_file() or _token_from_keychain()
    if not token:
        raise ClaudeQuotaUnavailableError("no Claude Code OAuth token found")
    return token


def fetch_windows(token: str | None = None, timeout: float = 10.0) -> list[dict]:
    """[{label, utilization (0-100), resets_at iso|None}, ...] for every
    window the endpoint reports (5h, weekly, per-model carve-outs)."""
    bearer = token or access_token()
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {bearer}",
            "anthropic-beta": OAUTH_BETA_HEADER,
            "User-Agent": "SidePulse (Claude-Code companion)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise ClaudeQuotaUnavailableError(str(exc)) from exc
    return windows_from_payload(payload)


def windows_from_payload(payload: object) -> list[dict]:
    """Pure and fixture-testable, tolerant of the schema's growth: known
    top-level windows plus the newer ``limits[]`` array."""
    if not isinstance(payload, dict):
        return []
    windows: list[dict] = []

    def add(label: str, entry: object) -> None:
        if not isinstance(entry, dict):
            return
        utilization = entry.get("utilization")
        if not isinstance(utilization, (int, float)):
            return
        windows.append(
            {
                "label": label,
                "utilization": max(0.0, min(100.0, float(utilization))),
                "resets_at": entry.get("resets_at")
                if isinstance(entry.get("resets_at"), str)
                else None,
            }
        )

    add("5-hour", payload.get("five_hour"))
    add("weekly", payload.get("seven_day"))
    add("weekly Opus", payload.get("seven_day_opus"))
    limits = payload.get("limits")
    if isinstance(limits, list):
        for entry in limits:
            if not isinstance(entry, dict):
                continue
            scope = entry.get("scope")
            name = None
            if isinstance(scope, dict):
                model = scope.get("model")
                if isinstance(model, dict):
                    name = model.get("display_name")
            add(str(name or entry.get("name") or "limit"), entry)
    return windows


def summary_line(windows: list[dict]) -> str | None:
    if not windows:
        return None
    parts = [
        f"{window['label']} {window['utilization']:.0f}%"
        for window in windows[:3]
    ]
    return "Claude plan: " + " · ".join(parts)
