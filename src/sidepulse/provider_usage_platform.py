"""Pure provider-accounting domain model and source selection policy."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Final

from .provider_instances import ProviderInstanceError, ProviderInstanceKey

_PROVIDER_ID = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
_LANE_ID = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}\Z")
_SOURCE_INSTANCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]{0,63}\Z")
_MAX_LANES: Final = 64
DEFAULT_SOURCE_INSTANCE_ID: Final = "default"


class ProviderSourceState(str, Enum):
    DISABLED = "disabled"
    READY = "ready"
    NEEDS_CONSENT = "needs_consent"
    NEEDS_SIGN_IN = "needs_sign_in"
    SOURCE_NOT_FOUND = "source_not_found"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    STALE = "stale"
    ERROR = "error"
    UNSUPPORTED = "unsupported"


_ACTIONABLE_STATES = frozenset(
    {
        ProviderSourceState.NEEDS_CONSENT,
        ProviderSourceState.NEEDS_SIGN_IN,
        ProviderSourceState.SOURCE_NOT_FOUND,
        ProviderSourceState.UNAVAILABLE,
        ProviderSourceState.RATE_LIMITED,
        ProviderSourceState.ERROR,
    }
)


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    provider_id: str
    label: str
    source_order: tuple[str, ...]
    supports_browser_sources: bool
    supports_local_tokens: bool
    supports_quota: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.provider_id, str)
            or _PROVIDER_ID.fullmatch(self.provider_id) is None
            or not isinstance(self.label, str)
            or not self.label.strip()
            or type(self.source_order) is not tuple
            or not self.source_order
            or len(self.source_order) != len(set(self.source_order))
            or not all(
                isinstance(source, str)
                and _LANE_ID.fullmatch(source) is not None
                for source in self.source_order
            )
            or type(self.supports_browser_sources) is not bool
            or type(self.supports_local_tokens) is not bool
            or type(self.supports_quota) is not bool
        ):
            raise ValueError("invalid provider descriptor")


_PROVIDER_DESCRIPTORS: Final = (
    ProviderDescriptor(
        "codex",
        "Codex",
        ("codex-auth", "codex-rollouts", "codex-app-server"),
        False,
        True,
        True,
    ),
    ProviderDescriptor(
        "claude",
        "Claude",
        ("claude-keychain", "claude-oauth", "claude-transcripts", "claude-browser"),
        True,
        True,
        True,
    ),
    ProviderDescriptor(
        "cursor",
        "Cursor",
        ("cursor-app-auth", "cursor-account-api", "cursor-browser"),
        True,
        False,
        True,
    ),
    ProviderDescriptor(
        "devin",
        "Devin",
        ("devin-keychain", "devin-browser", "devin-account-api"),
        True,
        False,
        True,
    ),
    ProviderDescriptor(
        "grok",
        "Grok",
        ("grok-auth", "grok-rpc", "grok-billing", "grok-browser"),
        True,
        True,
        True,
    ),
    ProviderDescriptor(
        "antigravity",
        "Antigravity",
        ("antigravity-app", "agy-server", "antigravity-oauth"),
        False,
        True,
        True,
    ),
    ProviderDescriptor(
        "opencode",
        "OpenCode",
        ("opencode-db", "opencode-auth"),
        False,
        True,
        True,
    ),
    ProviderDescriptor(
        "openai-api",
        "OpenAI API",
        ("openai-admin-key", "openai-usage-api"),
        False,
        True,
        True,
    ),
)


def provider_descriptors() -> tuple[ProviderDescriptor, ...]:
    return _PROVIDER_DESCRIPTORS


def provider_descriptor(provider_id: str) -> ProviderDescriptor:
    for descriptor in _PROVIDER_DESCRIPTORS:
        if descriptor.provider_id == provider_id:
            return descriptor
    raise ValueError(f"unsupported native provider: {provider_id}")


@dataclass(frozen=True, slots=True)
class UsageLane:
    provider_id: str
    lane_id: str
    label: str
    remaining_percent: float | None
    reset_at: float | None
    scope: str
    model: str | None
    feature: str | None
    bindable: bool
    source_id: str

    def __post_init__(self) -> None:
        if (
            _PROVIDER_ID.fullmatch(self.provider_id or "") is None
            or _LANE_ID.fullmatch(self.lane_id or "") is None
            or not isinstance(self.label, str)
            or not self.label.strip()
            or not isinstance(self.scope, str)
            or not self.scope.strip()
            or not isinstance(self.source_id, str)
            or _LANE_ID.fullmatch(self.source_id) is None
            or type(self.bindable) is not bool
        ):
            raise ValueError("invalid usage lane")
        if self.remaining_percent is not None and (
            isinstance(self.remaining_percent, bool)
            or not isinstance(self.remaining_percent, (int, float))
            or not math.isfinite(float(self.remaining_percent))
            or not 0.0 <= float(self.remaining_percent) <= 100.0
        ):
            raise ValueError("remaining_percent must be between 0 and 100")
        if self.reset_at is not None and (
            isinstance(self.reset_at, bool)
            or not isinstance(self.reset_at, (int, float))
            or not math.isfinite(float(self.reset_at))
            or float(self.reset_at) < 0.0
        ):
            raise ValueError("reset_at must be a finite nonnegative timestamp")
        if self.model is not None and (
            not isinstance(self.model, str) or not self.model.strip()
        ):
            raise ValueError("model must be a nonempty string")
        if self.feature is not None and (
            not isinstance(self.feature, str) or not self.feature.strip()
        ):
            raise ValueError("feature must be a nonempty string")
        object.__setattr__(
            self,
            "remaining_percent",
            None if self.remaining_percent is None else float(self.remaining_percent),
        )
        object.__setattr__(
            self,
            "reset_at",
            None if self.reset_at is None else float(self.reset_at),
        )


@dataclass(frozen=True, slots=True)
class ProviderUsageSnapshot:
    provider_id: str
    account_label: str | None
    observed_at: float
    state: ProviderSourceState
    reason_code: str | None
    action_label: str | None
    lanes: tuple[UsageLane, ...]
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    model_count: int
    estimated_cost_usd: float | None
    cache_savings_usd: float | None
    credits_remaining: float | None
    incident: str | None
    source_instance_id: str = DEFAULT_SOURCE_INSTANCE_ID

    def __post_init__(self) -> None:
        provider_descriptor(self.provider_id)
        try:
            instance_key = ProviderInstanceKey(
                self.provider_id,
                self.source_instance_id,
            )
        except ProviderInstanceError as exc:
            raise ValueError("invalid provider usage snapshot instance") from exc
        if (
            isinstance(self.observed_at, bool)
            or not isinstance(self.observed_at, (int, float))
            or not math.isfinite(float(self.observed_at))
            or float(self.observed_at) < 0.0
            or type(self.state) is not ProviderSourceState
            or not isinstance(self.source_instance_id, str)
            or _SOURCE_INSTANCE_ID.fullmatch(self.source_instance_id) is None
            or type(self.lanes) is not tuple
            or len(self.lanes) > _MAX_LANES
            or not all(
                type(lane) is UsageLane and lane.provider_id == self.provider_id
                for lane in self.lanes
            )
        ):
            raise ValueError("invalid provider usage snapshot")
        if len({lane.lane_id for lane in self.lanes}) != len(self.lanes):
            raise ValueError("duplicate usage lane")
        counts = (
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.model_count,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("usage counts must be nonnegative integers")
        for name in ("estimated_cost_usd", "cache_savings_usd", "credits_remaining"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0.0
            ):
                raise ValueError(f"{name} must be a finite nonnegative value")
        if self.state in _ACTIONABLE_STATES and not (
            isinstance(self.action_label, str) and self.action_label.strip()
        ):
            raise ValueError("actionable provider state requires an action")
        if self.state is ProviderSourceState.READY and self.reason_code is not None:
            raise ValueError("ready provider state cannot carry a failure reason")
        if self.state is ProviderSourceState.STALE and not self.lanes:
            raise ValueError("stale provider state requires last-known-good lanes")
        if self.reason_code is not None and (
            not isinstance(self.reason_code, str)
            or _LANE_ID.fullmatch(self.reason_code) is None
        ):
            raise ValueError("invalid provider reason code")
        object.__setattr__(self, "observed_at", float(self.observed_at))
        object.__setattr__(
            self,
            "source_instance_id",
            instance_key.source_instance_id.value,
        )

    @property
    def identity(self) -> tuple[str, str]:
        """The exact non-display key for this provider usage snapshot."""
        return self.provider_id, self.source_instance_id


def _model_from_lane(lane_id: str, label: str) -> str | None:
    text = f"{lane_id} {label}".lower()
    for model in ("fable", "opus", "sonnet", "spark", "gemini", "gpt"):
        if model in text:
            return model
    return None


def normalize_dynamic_lane(
    *,
    provider_id: str,
    lane_id: str,
    label: str,
    remaining_percent: float | None,
    reset_at: float | None,
    source_id: str,
    known_lane_ids: set[str] | frozenset[str],
    scope: str = "all",
    model: str | None = None,
    feature: str | None = None,
) -> UsageLane:
    known = lane_id in known_lane_ids
    inferred_model = model or _model_from_lane(lane_id, label)
    return UsageLane(
        provider_id=provider_id,
        lane_id=lane_id,
        label=label,
        remaining_percent=remaining_percent,
        reset_at=reset_at,
        scope=scope,
        model=inferred_model,
        feature=feature,
        bindable=known,
        source_id=source_id,
    )


def select_authoritative_snapshot(
    candidates: tuple[ProviderUsageSnapshot, ...],
    *,
    last_known_good: ProviderUsageSnapshot | None = None,
) -> ProviderUsageSnapshot:
    if type(candidates) is not tuple or not candidates:
        if last_known_good is None:
            raise ValueError("provider source selection requires a candidate")
        return replace(
            last_known_good,
            state=ProviderSourceState.STALE,
            reason_code="source_unavailable",
            action_label="Retry",
        )
    provider_ids = {candidate.provider_id for candidate in candidates}
    if len(provider_ids) != 1:
        raise ValueError("provider source candidates must share one provider")

    for candidate in candidates:
        if candidate.state is ProviderSourceState.READY:
            return candidate

    for candidate in candidates:
        if candidate.state is ProviderSourceState.STALE:
            return candidate

    if last_known_good is not None:
        if last_known_good.provider_id not in provider_ids:
            raise ValueError("last-known-good provider does not match candidates")
        failure = candidates[0]
        return replace(
            last_known_good,
            observed_at=failure.observed_at,
            state=ProviderSourceState.STALE,
            reason_code=failure.reason_code or "source_unavailable",
            action_label=failure.action_label or "Retry",
        )

    return candidates[0]


def most_constrained_lane(snapshot: ProviderUsageSnapshot) -> UsageLane | None:
    eligible = tuple(
        lane
        for lane in snapshot.lanes
        if lane.bindable and lane.remaining_percent is not None
    )
    return min(eligible, key=lambda lane: lane.remaining_percent) if eligible else None


def provider_usage_identity(snapshot: ProviderUsageSnapshot) -> tuple[str, str]:
    """Return the composite identity used by stores, sync, and projections."""
    if type(snapshot) is not ProviderUsageSnapshot:
        raise ValueError("invalid provider usage snapshot")
    return snapshot.identity


def provider_status_line(snapshot: ProviderUsageSnapshot) -> str:
    label = provider_descriptor(snapshot.provider_id).label
    state_text = {
        ProviderSourceState.DISABLED: "off",
        ProviderSourceState.READY: "ready",
        ProviderSourceState.NEEDS_CONSENT: "permission required",
        ProviderSourceState.NEEDS_SIGN_IN: "sign-in required",
        ProviderSourceState.SOURCE_NOT_FOUND: "source not found",
        ProviderSourceState.UNAVAILABLE: "unavailable",
        ProviderSourceState.RATE_LIMITED: "temporarily rate limited",
        ProviderSourceState.STALE: "stale",
        ProviderSourceState.ERROR: "error",
        ProviderSourceState.UNSUPPORTED: "unsupported",
    }[snapshot.state]
    if snapshot.state in {ProviderSourceState.READY, ProviderSourceState.STALE}:
        lane = most_constrained_lane(snapshot)
        if lane is not None:
            state_text = f"{lane.label} {lane.remaining_percent:.0f}% left"
            if snapshot.state is ProviderSourceState.STALE:
                state_text += " · stale"
    return f"{label} · {state_text}"


__all__ = [
    "DEFAULT_SOURCE_INSTANCE_ID",
    "ProviderDescriptor",
    "ProviderSourceState",
    "ProviderUsageSnapshot",
    "UsageLane",
    "most_constrained_lane",
    "normalize_dynamic_lane",
    "provider_descriptor",
    "provider_descriptors",
    "provider_status_line",
    "provider_usage_identity",
    "select_authoritative_snapshot",
]
