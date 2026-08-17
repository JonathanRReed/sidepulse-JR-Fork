"""Native Devin quota parser and bearer-token source."""

from __future__ import annotations

import time
from collections.abc import Callable
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

DEVIN_BASE_URL = "https://app.devin.ai"


def normalize_organization(raw: str | None) -> str | None:
    value = clean_string(raw, maximum=300)
    if value is None:
        return None
    value = value.strip("/")
    if "/org/" in value:
        value = "org/" + value.split("/org/", 1)[1].split("/", 1)[0]
    elif "/organizations/" in value:
        value = "organizations/" + value.split("/organizations/", 1)[1].split("/", 1)[0]
    if value.startswith(("org/", "organizations/")):
        return value
    if value.startswith(("org-", "org_")):
        return f"organizations/{value}"
    return f"org/{value}"


def _display_organization(value: str | None) -> str | None:
    if not value:
        return None
    return value.split("/", 1)[-1]


def _find_key(object_: object, predicate: Callable[[str], bool]) -> object | None:
    if isinstance(object_, dict):
        for key, value in object_.items():
            if isinstance(key, str) and predicate(key.lower()):
                return value
        for value in object_.values():
            found = _find_key(value, predicate)
            if found is not None:
                return found
    elif isinstance(object_, list):
        for value in object_:
            found = _find_key(value, predicate)
            if found is not None:
                return found
    return None


def _window_from_value(value: object) -> tuple[float, float | None] | None:
    if isinstance(value, dict):
        for key in (
            "used_percent",
            "usedPercent",
            "usage_percent",
            "usagePercent",
            "percent_used",
            "percentUsed",
            "percent",
        ):
            used = bounded_percent(value.get(key))
            if used is not None:
                reset = next(
                    (
                        epoch_from_value(item)
                        for name, item in value.items()
                        if "reset" in str(name).lower() and epoch_from_value(item) is not None
                    ),
                    None,
                )
                return (used, reset)
        for key in ("remaining_percent", "remainingPercent", "percent_remaining", "percentRemaining"):
            remaining = bounded_percent(value.get(key))
            if remaining is not None:
                return (100.0 - remaining, None)
        used = finite_number(
            value.get("used", value.get("usage", value.get("consumed")))
        )
        limit = finite_number(
            value.get("limit", value.get("quota", value.get("total")))
        )
        if used is not None and limit is not None and limit > 0:
            return (max(0.0, min(100.0, used / limit * 100.0)), None)
        for nested in value.values():
            window = _window_from_value(nested)
            if window is not None:
                return window
        return None
    used = bounded_percent(value)
    return (used, None) if used is not None else None


def _plan_name(payload: object) -> str | None:
    value = _find_key(
        payload,
        lambda key: key in {
            "plan_name",
            "planname",
            "plan",
            "tier",
            "subscription_tier",
            "subscriptiontier",
        },
    )
    text = clean_string(value, maximum=120)
    if text is None:
        return None
    return " ".join(part.capitalize() for part in text.replace("_", "-").split("-") if part)


def parse_quota_payload(
    payload: object,
    *,
    organization: str | None,
    observed_at: float,
) -> ProviderUsageSnapshot:
    normalized_org = normalize_organization(organization)
    if isinstance(payload, dict):
        daily_value = payload.get("daily_percentage")
        weekly_value = payload.get("weekly_percentage")
    else:
        daily_value = weekly_value = None
    daily = (
        (bounded_percent(daily_value), epoch_from_value(payload.get("daily_reset_at")))
        if isinstance(payload, dict) and bounded_percent(daily_value) is not None
        else _window_from_value(
            _find_key(payload, lambda key: "daily" in key or key == "day")
        )
    )
    weekly = (
        (bounded_percent(weekly_value), epoch_from_value(payload.get("weekly_reset_at")))
        if isinstance(payload, dict) and bounded_percent(weekly_value) is not None
        else _window_from_value(
            _find_key(payload, lambda key: "weekly" in key or "week" in key)
        )
    )
    lanes: list[QuotaLane] = []
    for lane_id, label, window in (
        ("daily", "Daily", daily),
        ("weekly", "Weekly", weekly),
    ):
        if window is None or window[0] is None:
            continue
        used, reset_at = window
        lanes.append(
            QuotaLane(
                provider_id="devin",
                lane_id=lane_id,
                label=label,
                remaining=max(0.0, 100.0 - used),
                used=used,
                total=100.0,
                unit=QuotaUnit.PERCENT,
                reset_at=reset_at,
                source="devin-quota-api",
                bindable=True,
            )
        )
    if not lanes:
        raise ValueError("Devin payload has no quota windows")
    balance = None
    if isinstance(payload, dict):
        balance = finite_number(payload.get("overage_balance"))
        if balance is None:
            cents = finite_number(payload.get("overage_balance_cents"))
            balance = cents / 100.0 if cents is not None else None
    return ProviderUsageSnapshot(
        provider_id="devin",
        state=ProviderSourceState.READY,
        observed_at=observed_at,
        source_label="Devin quota API",
        account_label=_display_organization(normalized_org),
        reason_code=None,
        action=None,
        lanes=tuple(lanes),
        token_usage=None,
        credits=balance,
        incident=None,
    )


def _candidate_paths(organization: str) -> tuple[str, ...]:
    normalized = normalize_organization(organization)
    if normalized is None:
        return ()
    values = [f"{normalized}/billing/quota/usage"]
    if normalized.startswith("org/"):
        values.append(f"{normalized.split('/', 1)[1]}/billing/quota/usage")
    elif normalized.startswith("organizations/"):
        values.append(f"{normalized}/billing/quota/usage")
    return tuple(dict.fromkeys(values))


def collect_devin_usage(
    *,
    bearer_token: str | None,
    organization: str | None,
    now: float | None = None,
    opener=None,
) -> ProviderUsageSnapshot:
    observed_at = time.time() if now is None else float(now)
    token = clean_string(bearer_token, maximum=16 * 1024)
    normalized_org = normalize_organization(organization)
    if token is None:
        return ProviderUsageSnapshot(
            provider_id="devin",
            state=ProviderSourceState.NEEDS_CONSENT,
            observed_at=observed_at,
            source_label="Devin credentials",
            account_label=_display_organization(normalized_org),
            reason_code="devin_session_required",
            action="Enable Devin browser source or add a bearer token",
            lanes=(),
            token_usage=None,
            credits=None,
            incident=None,
        )
    if normalized_org is None:
        return ProviderUsageSnapshot(
            provider_id="devin",
            state=ProviderSourceState.PARTIAL,
            observed_at=observed_at,
            source_label="Devin credentials",
            account_label=None,
            reason_code="devin_organization_required",
            action="Set the Devin organization",
            lanes=(),
            token_usage=None,
            credits=None,
            incident=None,
        )
    last_error: ProviderSourceError | None = None
    for path in _candidate_paths(normalized_org):
        try:
            payload = json_request(
                f"{DEVIN_BASE_URL}/api/{quote(path, safe='/_-')}",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "SidePulse/0.2.2",
                },
                opener=opener,
            )
            return parse_quota_payload(
                payload,
                organization=normalized_org,
                observed_at=observed_at,
            )
        except ProviderSourceError as exc:
            last_error = exc
            if exc.reason_code == "unauthorized":
                break
        except ValueError:
            continue
    reason = last_error.reason_code if last_error else "devin_quota_unavailable"
    return ProviderUsageSnapshot(
        provider_id="devin",
        state=(
            ProviderSourceState.NEEDS_SIGN_IN
            if reason == "unauthorized"
            else ProviderSourceState.FAILED
        ),
        observed_at=observed_at,
        source_label="Devin quota API",
        account_label=_display_organization(normalized_org),
        reason_code=reason,
        action=("Reconnect Devin" if reason == "unauthorized" else "Retry Devin usage"),
        lanes=(),
        token_usage=None,
        credits=None,
        incident=None,
    )


__all__ = [
    "collect_devin_usage",
    "normalize_organization",
    "parse_quota_payload",
]
