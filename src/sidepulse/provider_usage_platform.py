"""Typed contracts for SidePulse-owned provider usage and quota facts.

This module is deliberately pure. Provider files, credentials, browsers,
network calls, refresh scheduling, persistence, and AppKit live outside it.
Every non-ready source state carries a reason and an action so the UI never
collapses an authentication or configuration problem into ``no reading``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum

MAX_QUOTA_LANES = 64
MAX_MODELS = 128


class ProviderSourceState(str, Enum):
    DISABLED = "disabled"
    READY = "ready"
    PARTIAL = "partial"
    NEEDS_CONSENT = "needs_consent"
    NEEDS_SIGN_IN = "needs_sign_in"
    NOT_DETECTED = "not_detected"
    UNSUPPORTED = "unsupported"
    STALE = "stale"
    FAILED = "failed"


class QuotaUnit(str, Enum):
    PERCENT = "percent"
    CREDITS = "credits"
    USD = "usd"
    TOKENS = "tokens"
    REQUESTS = "requests"


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    provider_id: str
    label: str
    source_order: tuple[str, ...]
    default_enabled: bool = True

    def __post_init__(self) -> None:
        if not (
            isinstance(self.provider_id, str)
            and self.provider_id
            and self.provider_id.isascii()
            and isinstance(self.label, str)
            and self.label
            and type(self.source_order) is tuple
            and self.source_order
            and len(self.source_order) == len(set(self.source_order))
            and all(isinstance(value, str) and value for value in self.source_order)
            and type(self.default_enabled) is bool
        ):
            raise ValueError("invalid provider descriptor")


@dataclass(frozen=True, slots=True)
class QuotaLane:
    provider_id: str
    lane_id: str
    label: str
    remaining: float | None
    used: float | None
    total: float | None
    unit: QuotaUnit
    reset_at: float | None
    source: str
    scope: str = "all"
    model: str | None = None
    feature: str | None = None
    bindable: bool = False

    def __post_init__(self) -> None:
        if not (
            isinstance(self.provider_id, str)
            and self.provider_id
            and isinstance(self.lane_id, str)
            and self.lane_id
            and len(self.lane_id) <= 160
            and isinstance(self.label, str)
            and self.label
            and len(self.label) <= 160
            and isinstance(self.unit, QuotaUnit)
            and isinstance(self.source, str)
            and self.source
            and isinstance(self.scope, str)
            and self.scope
            and type(self.bindable) is bool
        ):
            raise ValueError("invalid quota lane")
        for name in ("remaining", "used", "total", "reset_at"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0.0
            ):
                raise ValueError(f"invalid quota lane {name}")
            if value is not None:
                object.__setattr__(self, name, float(value))
        if self.unit is QuotaUnit.PERCENT:
            for name in ("remaining", "used", "total"):
                value = getattr(self, name)
                if value is not None and value > 100.0:
                    raise ValueError("percentage quota values must be <= 100")
        if self.total is not None and self.total <= 0.0:
            raise ValueError("quota total must be positive")
        if self.remaining is not None and self.total is not None and self.remaining > self.total:
            raise ValueError("quota remaining exceeds total")
        if self.used is not None and self.total is not None and self.used > self.total:
            raise ValueError("quota used exceeds total")
        if self.model is not None and (not self.model or len(self.model) > 160):
            raise ValueError("invalid quota model")
        if self.feature is not None and (not self.feature or len(self.feature) > 160):
            raise ValueError("invalid quota feature")
        if (self.model is not None or self.feature is not None) and self.bindable:
            raise ValueError("scoped quota lanes cannot bind global hardware policy")

    @property
    def remaining_fraction(self) -> float | None:
        if self.remaining is None:
            return None
        if self.unit is QuotaUnit.PERCENT:
            return max(0.0, min(1.0, self.remaining / 100.0))
        if self.total is None or self.total <= 0.0:
            return None
        return max(0.0, min(1.0, self.remaining / self.total))


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    cached_input_tokens: int
    cache_creation_tokens: int
    output_tokens: int
    models: tuple[str, ...]
    estimated_cost_usd: float | None
    estimated_cache_savings_usd: float | None
    pricing_coverage: float | None
    pricing_table_version: str | None
    pricing_as_of: str | None

    def __post_init__(self) -> None:
        counts = (
            self.input_tokens,
            self.cached_input_tokens,
            self.cache_creation_tokens,
            self.output_tokens,
        )
        if not all(type(value) is int and value >= 0 for value in counts):
            raise ValueError("token counts must be nonnegative integers")
        if (
            type(self.models) is not tuple
            or len(self.models) > MAX_MODELS
            or len(self.models) != len(set(self.models))
            or not all(isinstance(value, str) and value and len(value) <= 200 for value in self.models)
        ):
            raise ValueError("invalid token model set")
        for name in ("estimated_cost_usd", "estimated_cache_savings_usd"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0.0
            ):
                raise ValueError(f"invalid {name}")
            if value is not None:
                object.__setattr__(self, name, float(value))
        if self.pricing_coverage is not None and (
            isinstance(self.pricing_coverage, bool)
            or not isinstance(self.pricing_coverage, (int, float))
            or not math.isfinite(float(self.pricing_coverage))
            or not 0.0 <= float(self.pricing_coverage) <= 1.0
        ):
            raise ValueError("invalid pricing coverage")
        if self.pricing_coverage is not None:
            object.__setattr__(self, "pricing_coverage", float(self.pricing_coverage))
        if self.estimated_cost_usd is not None and not (
            self.pricing_table_version and self.pricing_as_of
        ):
            raise ValueError("priced usage requires a versioned pricing table")

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cached_input_tokens
            + self.cache_creation_tokens
            + self.output_tokens
        )

    @property
    def model_count(self) -> int:
        return len(self.models)


@dataclass(frozen=True, slots=True)
class ProviderUsageSnapshot:
    provider_id: str
    state: ProviderSourceState
    observed_at: float
    source_label: str
    account_label: str | None
    reason_code: str | None
    action: str | None
    lanes: tuple[QuotaLane, ...]
    token_usage: TokenUsage | None
    credits: float | None
    incident: str | None

    def __post_init__(self) -> None:
        if not (
            isinstance(self.provider_id, str)
            and self.provider_id
            and isinstance(self.state, ProviderSourceState)
            and not isinstance(self.observed_at, bool)
            and isinstance(self.observed_at, (int, float))
            and math.isfinite(float(self.observed_at))
            and float(self.observed_at) >= 0.0
            and isinstance(self.source_label, str)
            and self.source_label
            and type(self.lanes) is tuple
            and len(self.lanes) <= MAX_QUOTA_LANES
            and all(type(lane) is QuotaLane and lane.provider_id == self.provider_id for lane in self.lanes)
            and (self.token_usage is None or type(self.token_usage) is TokenUsage)
        ):
            raise ValueError("invalid provider usage snapshot")
        object.__setattr__(self, "observed_at", float(self.observed_at))
        lane_ids = tuple(lane.lane_id for lane in self.lanes)
        if len(lane_ids) != len(set(lane_ids)):
            raise ValueError("duplicate provider quota lane")
        if self.account_label is not None and (
            not isinstance(self.account_label, str) or not self.account_label or len(self.account_label) > 200
        ):
            raise ValueError("invalid provider account label")
        if self.reason_code is not None and (
            not isinstance(self.reason_code, str) or not self.reason_code or len(self.reason_code) > 160
        ):
            raise ValueError("invalid provider reason")
        if self.action is not None and (
            not isinstance(self.action, str) or not self.action or len(self.action) > 200
        ):
            raise ValueError("invalid provider action")
        if self.incident is not None and (
            not isinstance(self.incident, str) or not self.incident or len(self.incident) > 300
        ):
            raise ValueError("invalid provider incident")
        if self.credits is not None and (
            isinstance(self.credits, bool)
            or not isinstance(self.credits, (int, float))
            or not math.isfinite(float(self.credits))
            or float(self.credits) < 0.0
        ):
            raise ValueError("invalid provider credits")
        if self.credits is not None:
            object.__setattr__(self, "credits", float(self.credits))
        if self.state is ProviderSourceState.READY:
            if self.reason_code is not None or self.action is not None:
                raise ValueError("ready provider state cannot carry remediation")
        elif self.state is ProviderSourceState.DISABLED:
            if self.action is None:
                raise ValueError("disabled provider state requires an enable action")
        elif self.reason_code is None or self.action is None:
            raise ValueError("nonready provider state requires a reason and action")
        if self.state in {
            ProviderSourceState.NEEDS_CONSENT,
            ProviderSourceState.NEEDS_SIGN_IN,
            ProviderSourceState.NOT_DETECTED,
            ProviderSourceState.UNSUPPORTED,
            ProviderSourceState.FAILED,
        } and self.lanes:
            raise ValueError("untrusted source state cannot publish fresh quota lanes")

    @property
    def available(self) -> bool:
        return self.state in {
            ProviderSourceState.READY,
            ProviderSourceState.PARTIAL,
            ProviderSourceState.STALE,
        }


DEFAULT_PROVIDER_DESCRIPTORS = (
    ProviderDescriptor(
        "codex",
        "Codex",
        ("codex-local-auth", "codex-rollout-usage", "codex-app-server"),
    ),
    ProviderDescriptor(
        "claude",
        "Claude",
        ("claude-local-transcripts", "claude-keychain-oauth", "claude-browser-consent"),
    ),
    ProviderDescriptor(
        "cursor",
        "Cursor",
        ("cursor-app-auth", "cursor-browser-consent"),
    ),
    ProviderDescriptor(
        "devin",
        "Devin",
        ("devin-manual-token", "devin-browser-consent"),
    ),
    ProviderDescriptor(
        "grok",
        "Grok",
        ("grok-auth-json", "grok-browser-consent", "grok-local-sessions"),
    ),
    ProviderDescriptor(
        "antigravity",
        "Antigravity",
        ("antigravity-local-server", "agy-local-server", "antigravity-oauth"),
    ),
    ProviderDescriptor(
        "openai-api",
        "OpenAI API",
        ("openai-admin-key",),
        default_enabled=False,
    ),
)

_PROVIDER_LABELS = {row.provider_id: row.label for row in DEFAULT_PROVIDER_DESCRIPTORS}


def provider_label(provider_id: str) -> str:
    return _PROVIDER_LABELS.get(provider_id, provider_id.replace("-", " ").title())


def retain_last_known_good(
    previous: ProviderUsageSnapshot | None,
    current: ProviderUsageSnapshot,
) -> ProviderUsageSnapshot:
    if type(current) is not ProviderUsageSnapshot:
        raise TypeError("current must be ProviderUsageSnapshot")
    if previous is None or previous.provider_id != current.provider_id:
        return current
    if type(previous) is not ProviderUsageSnapshot:
        raise TypeError("previous must be ProviderUsageSnapshot")
    if current.state in {ProviderSourceState.READY, ProviderSourceState.PARTIAL}:
        return current
    if not previous.available or not previous.lanes and previous.token_usage is None:
        return current
    return replace(
        previous,
        state=ProviderSourceState.STALE,
        reason_code=current.reason_code or "last_known_good",
        action=current.action or f"Refresh {provider_label(current.provider_id)} usage",
        source_label=previous.source_label,
        incident=current.incident or previous.incident,
    )


def most_constrained_summary(
    snapshots: tuple[ProviderUsageSnapshot, ...],
) -> str | None:
    if type(snapshots) is not tuple or not all(
        type(snapshot) is ProviderUsageSnapshot for snapshot in snapshots
    ):
        raise TypeError("snapshots must be ProviderUsageSnapshot values")
    candidates: list[tuple[float, ProviderUsageSnapshot, QuotaLane]] = []
    for snapshot in snapshots:
        if snapshot.state not in {ProviderSourceState.READY, ProviderSourceState.PARTIAL}:
            continue
        for lane in snapshot.lanes:
            fraction = lane.remaining_fraction
            if fraction is not None:
                candidates.append((fraction, snapshot, lane))
    if not candidates:
        return None
    _fraction, snapshot, lane = min(
        candidates,
        key=lambda item: (item[0], item[1].provider_id, item[2].lane_id),
    )
    remaining = lane.remaining
    if remaining is None:
        return None
    value = f"{remaining:.0f}%" if lane.unit is QuotaUnit.PERCENT else f"{remaining:g}"
    return f"{provider_label(snapshot.provider_id)} {lane.label} {value}"


__all__ = [
    "DEFAULT_PROVIDER_DESCRIPTORS",
    "MAX_MODELS",
    "MAX_QUOTA_LANES",
    "ProviderDescriptor",
    "ProviderSourceState",
    "ProviderUsageSnapshot",
    "QuotaLane",
    "QuotaUnit",
    "TokenUsage",
    "most_constrained_summary",
    "provider_label",
    "retain_last_known_good",
]
