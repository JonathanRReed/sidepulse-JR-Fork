"""Pure, bounded presentation models for canonical capacity truth.

This module formats already-normalized capacity, refresh, history, and forecast
records. It owns no source work, persistence, AppKit objects, timers, or
provider parsing.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final

from .capacity_authority import CapacityProjection, LaneAuthority
from .capacity_calibration import ReleasedForecast
from .capacity_forecast import ForecastRefusalCode
from .capacity_history import (
    MAX_CAPACITY_HISTORY_SAMPLES,
    NO_OBSERVATION,
    CapacityHistorySummary,
    HistoryInterval,
    NoObservationInterval,
)
from .capacity_refresh import (
    RefreshCoordinatorSnapshot,
    RefreshDecision,
    RefreshDecisionKind,
    RefreshDecisionReason,
    RefreshSourceState,
    RefreshStatusKind,
)
from .capacity_types import (
    CapacitySnapshot,
    CapacitySourceHealth,
    CapacityValue,
    LaneApplicability,
    ObservationState,
    QuotaHorizon,
    ResetFact,
    ResetState,
    SourceHealthKind,
)
from .reset_policy import derive_reset_countdown

MAX_CAPACITY_CARD_ROWS: Final = 2
MAX_CAPACITY_HISTORY_SUMMARIES: Final = 3
MAX_CAPACITY_PRESENTATION_TEXT: Final = 256

_FALLBACK_STATES: Final = frozenset({ObservationState.STALE, ObservationState.LAST_KNOWN_GOOD})
_DIRECT_HEALTH_KINDS: Final = frozenset(
    {
        SourceHealthKind.HEALTHY,
        SourceHealthKind.REFRESHING,
        SourceHealthKind.COOLDOWN,
    }
)
_APPLICABILITY_ORDER: Final = {
    LaneApplicability.APPLICABLE: 0,
    LaneApplicability.AMBIGUOUS: 1,
    LaneApplicability.INAPPLICABLE: 2,
}
_PROVIDER_NAMES: Final = {
    "anthropic": "Anthropic",
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "codex": "Codex",
    "cursor": "Cursor",
    "devin": "Devin",
    "hermes": "Hermes",
    "openai": "OpenAI",
    "openclaw": "OpenClaw",
}
_UNSAFE_SEMANTIC_COPY: Final = re.compile(
    r"(?:[/\\]|://|@|\b(?:api[ _-]?key|bearer|credential|exception|password|"
    r"raw[ _-]?error|secret|token|traceback)\b)",
    re.IGNORECASE,
)


def _bounded_text(value: object, *, allow_empty: bool = False) -> bool:
    return (
        type(value) is str
        and (allow_empty or bool(value))
        and len(value) <= MAX_CAPACITY_PRESENTATION_TEXT
        and value.isprintable()
    )


def _validated_now(now: object) -> float:
    if type(now) not in {int, float}:
        raise ValueError("now must be a finite nonnegative timestamp")
    value = float(now)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("now must be a finite nonnegative timestamp")
    return value


def _provider_name(provider_id: str) -> str:
    known = _PROVIDER_NAMES.get(provider_id)
    if known is not None:
        return known
    words = re.sub(r"[._-]+", " ", provider_id).strip()
    text = words.title() or "Provider"
    return text[:64].rstrip() or "Provider"


def _safe_semantic_name(authority: LaneAuthority) -> str:
    name = authority.lane.semantic_name
    if _UNSAFE_SEMANTIC_COPY.search(name) is None:
        return name
    return {
        QuotaHorizon.SHORT: "Short window",
        QuotaHorizon.LONG: "Long window",
        QuotaHorizon.OTHER: "Capacity window",
    }[authority.lane.horizon]


def _percent_number(value: float) -> str:
    if value == 0.0:
        return "0%"
    if 0.0 < value < 1.0:
        return "<1%"
    return f"{value:.0f}%"


def _relative_suffix(observed_at: object, now: float) -> str | None:
    if type(observed_at) not in {int, float}:
        return None
    observed = float(observed_at)
    if not math.isfinite(observed) or observed < 0.0 or observed > now:
        return None
    age = now - observed
    if age <= 30.0:
        return "just now"
    if age < 60.0:
        return f"{int(math.ceil(age))}s ago"
    if age < 3_600.0:
        return f"{max(1, int(age // 60.0))}m ago"
    if age < 86_400.0:
        return f"{max(1, int(age // 3_600.0))}h ago"
    return f"{max(1, int(age // 86_400.0))}d ago"


def _duration_until(deadline: object, now: float) -> str | None:
    if type(deadline) not in {int, float}:
        return None
    boundary = float(deadline)
    if not math.isfinite(boundary) or boundary < 0.0:
        return None
    seconds = boundary - now
    if seconds <= 0.0:
        return "now"
    total_minutes = max(1, int(math.ceil(seconds / 60.0)))
    days, day_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(day_minutes, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


@dataclass(frozen=True, slots=True)
class CapacityCardRowModel:
    provider: str
    semantic_name: str
    remaining_text: str
    reset_text: str
    freshness_text: str
    stale: bool

    def __post_init__(self) -> None:
        if not (
            _bounded_text(self.provider)
            and _bounded_text(self.semantic_name)
            and _bounded_text(self.remaining_text)
            and _bounded_text(self.reset_text)
            and _bounded_text(self.freshness_text)
            and type(self.stale) is bool
        ):
            raise ValueError("invalid capacity card row")


@dataclass(frozen=True, slots=True)
class CapacityCardModel:
    heading: str
    rows: tuple[CapacityCardRowModel, ...]
    status_text: str | None

    def __post_init__(self) -> None:
        if not (
            self.heading == "Capacity"
            and type(self.rows) is tuple
            and all(type(row) is CapacityCardRowModel for row in self.rows)
            and (self.status_text is None or _bounded_text(self.status_text))
        ):
            raise ValueError("invalid capacity card")
        if len(self.rows) > MAX_CAPACITY_CARD_ROWS:
            raise ValueError("capacity card accepts at most two rows")
        if bool(self.rows) == (self.status_text is not None):
            raise ValueError("capacity card rows and status are mutually exclusive")


@dataclass(frozen=True, slots=True)
class CapacityLaneDetailModel:
    provider: str
    semantic_name: str
    remaining_text: str
    reset_text: str
    freshness_text: str
    source_health_text: str
    applicability_text: str
    refusal_text: str | None
    binds: bool
    stale: bool

    def __post_init__(self) -> None:
        text = (
            self.provider,
            self.semantic_name,
            self.remaining_text,
            self.reset_text,
            self.freshness_text,
            self.source_health_text,
            self.applicability_text,
        )
        if not (
            all(_bounded_text(value) for value in text)
            and (self.refusal_text is None or _bounded_text(self.refusal_text))
            and type(self.binds) is bool
            and type(self.stale) is bool
        ):
            raise ValueError("invalid capacity detail row")


@dataclass(frozen=True, slots=True)
class CapacityApplicabilityGroupModel:
    applicability: LaneApplicability
    label: str
    rows: tuple[CapacityLaneDetailModel, ...]

    def __post_init__(self) -> None:
        if not (
            type(self.applicability) is LaneApplicability
            and _bounded_text(self.label)
            and type(self.rows) is tuple
            and bool(self.rows)
            and all(type(row) is CapacityLaneDetailModel for row in self.rows)
        ):
            raise ValueError("invalid capacity applicability group")


@dataclass(frozen=True, slots=True)
class CapacityProviderDetailModel:
    provider: str
    groups: tuple[CapacityApplicabilityGroupModel, ...]

    def __post_init__(self) -> None:
        if not (
            _bounded_text(self.provider)
            and type(self.groups) is tuple
            and bool(self.groups)
            and all(type(group) is CapacityApplicabilityGroupModel for group in self.groups)
        ):
            raise ValueError("invalid capacity provider detail")


@dataclass(frozen=True, slots=True)
class CapacitySourceHealthRowModel:
    provider: str
    status_text: str
    last_success_text: str
    last_attempt_text: str
    cooldown_text: str | None
    has_last_known_good: bool

    def __post_init__(self) -> None:
        if not (
            _bounded_text(self.provider)
            and _bounded_text(self.status_text)
            and _bounded_text(self.last_success_text)
            and _bounded_text(self.last_attempt_text)
            and (self.cooldown_text is None or _bounded_text(self.cooldown_text))
            and type(self.has_last_known_good) is bool
        ):
            raise ValueError("invalid capacity source health row")


@dataclass(frozen=True, slots=True)
class CapacityDetailSnapshot:
    """Typed capacity and refresh snapshots retained in their own clock domains."""

    capacity: CapacitySnapshot
    refresh: RefreshCoordinatorSnapshot | None
    refresh_now: float | None = None

    def __post_init__(self) -> None:
        if not (
            type(self.capacity) is CapacitySnapshot
            and (self.refresh is None or type(self.refresh) is RefreshCoordinatorSnapshot)
            and (
                self.refresh_now is None
                or (
                    type(self.refresh_now) in {int, float}
                    and math.isfinite(self.refresh_now)
                    and self.refresh_now >= 0.0
                )
            )
        ):
            raise ValueError("invalid capacity detail snapshot")
        if (self.refresh is None) != (self.refresh_now is None):
            raise ValueError("capacity refresh snapshot and clock must be provided together")
        if self.refresh is not None:
            assert self.refresh_now is not None
            if self.refresh_now < self.refresh.observed_at:
                raise ValueError("capacity refresh clock precedes snapshot")
            object.__setattr__(self, "refresh_now", float(self.refresh_now))


@dataclass(frozen=True, slots=True)
class CapacityHistorySummaryInput:
    interval: HistoryInterval
    summary: CapacityHistorySummary

    def __post_init__(self) -> None:
        if not (type(self.interval) is HistoryInterval and type(self.summary) is CapacityHistorySummary):
            raise ValueError("invalid capacity history summary input")


@dataclass(frozen=True, slots=True)
class CapacityHistoryPresentation:
    enabled: bool
    summaries: tuple[CapacityHistorySummaryInput, ...]

    def __post_init__(self) -> None:
        if not (
            type(self.enabled) is bool
            and type(self.summaries) is tuple
            and all(type(summary) is CapacityHistorySummaryInput for summary in self.summaries)
        ):
            raise ValueError("invalid capacity history presentation")
        if len(self.summaries) > MAX_CAPACITY_HISTORY_SUMMARIES:
            raise ValueError("capacity history accepts at most three summaries")
        intervals = tuple(item.interval for item in self.summaries)
        if len(intervals) != len(set(intervals)):
            raise ValueError("capacity history ranges must be unique")
        if not self.enabled and self.summaries:
            raise ValueError("disabled capacity history cannot carry summaries")


@dataclass(frozen=True, slots=True)
class CapacityHistoryRowModel:
    label: str
    summary_text: str
    observed_sample_count: int
    confirmed_reset_cycle_count: int

    def __post_init__(self) -> None:
        if not (
            _bounded_text(self.label)
            and _bounded_text(self.summary_text)
            and type(self.observed_sample_count) is int
            and 0 <= self.observed_sample_count <= MAX_CAPACITY_HISTORY_SAMPLES
            and type(self.confirmed_reset_cycle_count) is int
            and 0 <= self.confirmed_reset_cycle_count <= self.observed_sample_count
        ):
            raise ValueError("invalid capacity history row")


@dataclass(frozen=True, slots=True)
class CapacityForecastStatusModel:
    available: bool
    status_text: str
    refusal_text: str | None
    earliest_exhaustion_epoch: float | None
    latest_exhaustion_epoch: float | None

    def __post_init__(self) -> None:
        numeric = (self.earliest_exhaustion_epoch, self.latest_exhaustion_epoch)
        if not (
            type(self.available) is bool
            and _bounded_text(self.status_text)
            and (self.refusal_text is None or _bounded_text(self.refusal_text))
            and all(
                value is None or (type(value) is float and math.isfinite(value) and value >= 0.0) for value in numeric
            )
        ):
            raise ValueError("invalid capacity forecast status")
        if self.available:
            if (
                self.refusal_text is not None
                or self.earliest_exhaustion_epoch is None
                or self.latest_exhaustion_epoch is None
            ):
                raise ValueError("available capacity forecast lacks released bounds")
            if self.earliest_exhaustion_epoch > self.latest_exhaustion_epoch:
                raise ValueError("invalid capacity forecast status")
        elif self.refusal_text is None or any(value is not None for value in numeric):
            raise ValueError("unavailable capacity forecast carries released bounds")


@dataclass(frozen=True, slots=True)
class CapacityDetailModel:
    heading: str
    providers: tuple[CapacityProviderDetailModel, ...]
    source_health: tuple[CapacitySourceHealthRowModel, ...]
    forecast: CapacityForecastStatusModel
    history_enabled: bool
    history: tuple[CapacityHistoryRowModel, ...]

    def __post_init__(self) -> None:
        if not (
            self.heading == "Capacity Details"
            and type(self.providers) is tuple
            and all(type(provider) is CapacityProviderDetailModel for provider in self.providers)
            and type(self.source_health) is tuple
            and all(type(health) is CapacitySourceHealthRowModel for health in self.source_health)
            and type(self.forecast) is CapacityForecastStatusModel
            and type(self.history_enabled) is bool
            and type(self.history) is tuple
            and all(type(row) is CapacityHistoryRowModel for row in self.history)
            and (self.history_enabled or not self.history)
        ):
            raise ValueError("invalid capacity detail")


@dataclass(frozen=True, slots=True)
class CapacityAccessibilityChildModel:
    label: str
    value: str
    help: str
    countdown_announcement_minute: int | None

    def __post_init__(self) -> None:
        if not (
            _bounded_text(self.label)
            and _bounded_text(self.value)
            and _bounded_text(self.help)
            and (
                self.countdown_announcement_minute is None
                or (type(self.countdown_announcement_minute) is int and self.countdown_announcement_minute >= 0)
            )
        ):
            raise ValueError("invalid capacity accessibility child")


@dataclass(frozen=True, slots=True)
class CapacityAccessibilityGroupModel:
    label: str
    value: str
    help: str
    children: tuple[CapacityAccessibilityChildModel, ...]

    def __post_init__(self) -> None:
        if not (
            self.label == "Capacity"
            and _bounded_text(self.value)
            and _bounded_text(self.help)
            and type(self.children) is tuple
            and len(self.children) <= MAX_CAPACITY_CARD_ROWS
            and all(type(child) is CapacityAccessibilityChildModel for child in self.children)
        ):
            raise ValueError("invalid capacity accessibility group")


@dataclass(frozen=True, slots=True)
class ManualRefreshStatusModel:
    text: str
    can_request: bool
    announcement_minute: int

    def __post_init__(self) -> None:
        if not (
            _bounded_text(self.text)
            and type(self.can_request) is bool
            and type(self.announcement_minute) is int
            and self.announcement_minute >= 0
        ):
            raise ValueError("invalid manual refresh status")


def format_remaining(value: CapacityValue) -> str:
    """Format only typed percent-remaining truth."""
    if type(value) is not CapacityValue:
        raise TypeError("value must be CapacityValue")
    state = value.state
    if state is ObservationState.NULL:
        return "No usage value reported"
    if state is ObservationState.UNAVAILABLE:
        return "Unavailable"
    if state is ObservationState.PARTIAL and value.remaining is None:
        return "Partial"
    if value.remaining is None:
        return "Unavailable"
    text = f"{_percent_number(value.remaining)} left"
    return f"{text}, partial" if state is ObservationState.PARTIAL else text


def format_reset(reset: ResetFact, now: float) -> str:
    """Format one typed reset state without parsing source text."""
    reference = _validated_now(now)
    if type(reset) is not ResetFact:
        raise TypeError("reset must be ResetFact")
    countdown = derive_reset_countdown(reset, now=reference)
    if countdown.state is ResetState.DUE:
        return "Resets now"
    if countdown.state is ResetState.UNKNOWN:
        return "Reset unknown"
    if countdown.state is ResetState.UNAVAILABLE:
        return "Reset unavailable"
    if countdown.state is ResetState.DISPUTED:
        return "Reset disputed"
    if countdown.state is ResetState.STALE:
        return "Reset stale"
    if countdown.state is not ResetState.FUTURE:
        return "Reset unavailable"
    assert countdown.days is not None
    assert countdown.hours is not None
    assert countdown.minutes is not None
    if countdown.days:
        duration = f"{countdown.days}d {countdown.hours}h" if countdown.hours else f"{countdown.days}d"
    elif countdown.hours:
        duration = f"{countdown.hours}h {countdown.minutes}m" if countdown.minutes else f"{countdown.hours}h"
    else:
        duration = f"{countdown.minutes}m"
    return f"Resets in {duration}"


def format_freshness(
    observed_at: float,
    health: CapacitySourceHealth,
    now: float,
) -> str:
    """Format bounded relative freshness and explicit stale fallback truth."""
    reference = _validated_now(now)
    if type(health) is not CapacitySourceHealth:
        raise TypeError("health must be CapacitySourceHealth")
    suffix = _relative_suffix(observed_at, reference)
    if suffix is None:
        return "Update time unavailable"
    text = f"Updated {suffix}"
    stale = health.kind is SourceHealthKind.STALE or (
        health.has_last_known_good and health.kind not in _DIRECT_HEALTH_KINDS
    )
    return f"{text}, stale" if stale else text


def _freshness_for_authority(authority: LaneAuthority, now: float) -> str:
    text = format_freshness(
        authority.lane.observed_at,
        authority.lane.source_health,
        now,
    )
    if authority.freshness in _FALLBACK_STATES and not text.endswith(", stale"):
        return f"{text}, stale"
    return text


def _card_status(projection: CapacityProjection) -> str:
    if not projection.detail_lanes:
        return "No capacity sources"
    states = {authority.lane.value.state for authority in projection.detail_lanes}
    if ObservationState.PARTIAL in states:
        return "Capacity partial"
    if ObservationState.NULL in states:
        return "Usage unavailable"
    return "Capacity unavailable"


def build_capacity_card(
    projection: CapacityProjection,
    now: float,
) -> CapacityCardModel:
    """Project zero to two canonical binding lanes into compact copy."""
    reference = _validated_now(now)
    if type(projection) is not CapacityProjection:
        raise TypeError("projection must be CapacityProjection")
    if len(projection.binding_lanes) > MAX_CAPACITY_CARD_ROWS:
        raise ValueError("capacity card accepts at most two rows")
    detail_by_key: dict[object, LaneAuthority] = {}
    for authority in projection.detail_lanes:
        if type(authority) is not LaneAuthority or authority.lane.key in detail_by_key:
            raise ValueError("invalid capacity detail authority")
        detail_by_key[authority.lane.key] = authority
    binding_keys: set[object] = set()
    for authority in projection.binding_lanes:
        if (
            type(authority) is not LaneAuthority
            or authority.bindable is not True
            or detail_by_key.get(authority.lane.key) != authority
            or authority.lane.key in binding_keys
        ):
            raise ValueError("invalid capacity binding authority")
        binding_keys.add(authority.lane.key)
    rows = tuple(
        CapacityCardRowModel(
            provider=_provider_name(authority.provider_name),
            semantic_name=_safe_semantic_name(authority),
            remaining_text=format_remaining(authority.lane.value),
            reset_text=format_reset(authority.lane.reset, reference),
            freshness_text=_freshness_for_authority(authority, reference),
            stale=authority.freshness in _FALLBACK_STATES,
        )
        for authority in projection.binding_lanes
    )
    return CapacityCardModel(
        heading="Capacity",
        rows=rows,
        status_text=None if rows else _card_status(projection),
    )


_HEALTH_TEXT: Final = {
    SourceHealthKind.HEALTHY: "Healthy",
    SourceHealthKind.REFRESHING: "Refreshing",
    SourceHealthKind.COOLDOWN: "Cooling down",
    SourceHealthKind.SIGN_IN_REQUIRED: "Sign in required",
    SourceHealthKind.ACCESS_DENIED: "Access denied",
    SourceHealthKind.TIMED_OUT: "Timed out",
    SourceHealthKind.UNSUPPORTED: "Unsupported",
    SourceHealthKind.PARTIAL: "Partial",
    SourceHealthKind.FAILED: "Failed",
    SourceHealthKind.STALE: "Stale",
}

_REFRESH_HEALTH_TEXT: Final = {
    RefreshStatusKind.IDLE: "Waiting for first refresh",
    RefreshStatusKind.HEALTHY: "Healthy",
    RefreshStatusKind.REFRESHING: "Refreshing",
    RefreshStatusKind.COOLDOWN: "Cooling down",
    RefreshStatusKind.DISABLED: "Disabled",
    RefreshStatusKind.UNSUPPORTED: "Unsupported",
    RefreshStatusKind.FAILED: "Failed",
    RefreshStatusKind.TIMED_OUT: "Timed out",
    RefreshStatusKind.SIGN_IN_REQUIRED: "Sign in required",
    RefreshStatusKind.ACCESS_DENIED: "Access denied",
}

_APPLICABILITY_TEXT: Final = {
    LaneApplicability.APPLICABLE: "Applies now",
    LaneApplicability.AMBIGUOUS: "Execution context needed",
    LaneApplicability.INAPPLICABLE: "Not applicable",
}

_APPLICABILITY_GROUP_TEXT: Final = {
    LaneApplicability.APPLICABLE: "Applicable",
    LaneApplicability.AMBIGUOUS: "Needs context",
    LaneApplicability.INAPPLICABLE: "Not applicable",
}

_REFUSAL_TEXT: Final = {
    "source_out_of_context": "Outside the current execution context",
    "unknown_effect": "Scope is not supported",
    "model_unknown": "Selected model is unavailable",
    "model_mismatch": "Different model selected",
    "feature_unknown": "Selected feature is unavailable",
    "feature_mismatch": "Different feature selected",
    "usage_missing": "No usage value reported",
    "usage_unavailable": "Usage is unavailable",
    "usage_partial": "Source observation is partial",
    "usage_invalid": "Usage observation is invalid",
    "source_refreshing": "Source is refreshing",
    "source_cooldown": "Source is cooling down",
    "source_sign_in_required": "Sign in is required",
    "source_access_denied": "Source access was denied",
    "source_timed_out": "Source timed out",
    "source_unsupported": "Source is unsupported",
    "source_partial": "Source observation is partial",
    "source_failed": "Source refresh failed",
    "source_stale": "Source observation is stale",
}


def _lane_detail(
    authority: LaneAuthority,
    *,
    binding_keys: frozenset[object],
    now: float,
) -> CapacityLaneDetailModel:
    refusal = authority.refusal_code
    return CapacityLaneDetailModel(
        provider=_provider_name(authority.provider_name),
        semantic_name=_safe_semantic_name(authority),
        remaining_text=format_remaining(authority.lane.value),
        reset_text=format_reset(authority.lane.reset, now),
        freshness_text=_freshness_for_authority(authority, now),
        source_health_text=_HEALTH_TEXT[authority.lane.source_health.kind],
        applicability_text=_APPLICABILITY_TEXT[authority.applicability],
        refusal_text=(None if refusal is None else _REFUSAL_TEXT.get(refusal, "Capacity is unavailable")),
        binds=authority.lane.key in binding_keys,
        stale=authority.freshness in _FALLBACK_STATES,
    )


def _provider_details(
    projection: CapacityProjection,
    *,
    now: float,
) -> tuple[CapacityProviderDetailModel, ...]:
    binding_keys = frozenset(authority.lane.key for authority in projection.binding_lanes)
    by_provider: dict[str, dict[LaneApplicability, list[tuple[object, CapacityLaneDetailModel]]]] = {}
    for authority in sorted(projection.detail_lanes, key=lambda item: item.lane.key):
        provider_id = authority.provider_name
        groups = by_provider.setdefault(provider_id, {})
        groups.setdefault(authority.applicability, []).append(
            (
                authority.lane.key,
                _lane_detail(authority, binding_keys=binding_keys, now=now),
            )
        )

    providers: list[CapacityProviderDetailModel] = []
    for provider_id in sorted(by_provider):
        grouped = by_provider[provider_id]
        groups = tuple(
            CapacityApplicabilityGroupModel(
                applicability=applicability,
                label=_APPLICABILITY_GROUP_TEXT[applicability],
                rows=tuple(row for _key, row in sorted(grouped[applicability], key=lambda item: item[0])),
            )
            for applicability in sorted(grouped, key=lambda item: _APPLICABILITY_ORDER[item])
        )
        providers.append(
            CapacityProviderDetailModel(
                provider=_provider_name(provider_id),
                groups=groups,
            )
        )
    return tuple(providers)


def _refresh_health_row(
    state: RefreshSourceState,
    *,
    now: float,
) -> CapacitySourceHealthRowModel:
    success_suffix = None if state.last_success_at is None else _relative_suffix(state.last_success_at, now)
    attempt_suffix = None if state.last_attempt_at is None else _relative_suffix(state.last_attempt_at, now)
    cooldown = None if state.retry_at is None else _duration_until(state.retry_at, now)
    return CapacitySourceHealthRowModel(
        provider=_provider_name(state.key.source.provider_id),
        status_text=_REFRESH_HEALTH_TEXT[state.status],
        last_success_text=(
            "No successful observation"
            if state.last_success_at is None
            else (f"Last success {success_suffix}" if success_suffix is not None else "Last success time unavailable")
        ),
        last_attempt_text=(
            "No refresh attempt"
            if state.last_attempt_at is None
            else (f"Last attempt {attempt_suffix}" if attempt_suffix is not None else "Last attempt time unavailable")
        ),
        cooldown_text=(
            f"Cooldown ends in {cooldown}"
            if cooldown not in {None, "now"}
            else ("Cooldown ends now" if cooldown == "now" else None)
        ),
        has_last_known_good=state.has_last_known_good,
    )


def _capacity_health_row(
    health: CapacitySourceHealth,
    *,
    now: float,
) -> CapacitySourceHealthRowModel:
    success_suffix = _relative_suffix(health.observed_at, now) if health.kind is SourceHealthKind.HEALTHY else None
    attempt_suffix = None if health.last_attempt_at is None else _relative_suffix(health.last_attempt_at, now)
    cooldown = None if health.retry_at is None else _duration_until(health.retry_at, now)
    if health.kind is SourceHealthKind.HEALTHY:
        last_success_text = (
            f"Last success {success_suffix}" if success_suffix is not None else "Last success time unavailable"
        )
    elif health.has_last_known_good:
        last_success_text = "Last successful observation retained"
    else:
        last_success_text = "No successful observation"
    return CapacitySourceHealthRowModel(
        provider=_provider_name(health.source.provider_id),
        status_text=_HEALTH_TEXT[health.kind],
        last_success_text=last_success_text,
        last_attempt_text=(
            "No refresh attempt"
            if health.last_attempt_at is None
            else (f"Last attempt {attempt_suffix}" if attempt_suffix is not None else "Last attempt time unavailable")
        ),
        cooldown_text=(
            f"Cooldown ends in {cooldown}"
            if cooldown not in {None, "now"}
            else ("Cooldown ends now" if cooldown == "now" else None)
        ),
        has_last_known_good=health.has_last_known_good,
    )


def _source_health_rows(
    snapshot: CapacitySnapshot,
    *,
    now: float,
    refresh: RefreshCoordinatorSnapshot | None,
    refresh_now: float | None,
) -> tuple[CapacitySourceHealthRowModel, ...]:
    health_by_source = {health.source: health for health in snapshot.source_health}
    for lane in snapshot.lanes:
        health_by_source.setdefault(lane.source_health.source, lane.source_health)

    rows: list[CapacitySourceHealthRowModel] = []
    covered_sources: set[object] = set()
    if refresh is not None:
        assert refresh_now is not None
        lane_refresh_keys = frozenset(
            (lane.key.source, lane.key.pool, lane.account_discriminator) for lane in snapshot.lanes
        )
        lane_sources = frozenset(lane.key.source for lane in snapshot.lanes)
        health_sources = frozenset(health_by_source)
        refresh_sources: set[object] = set()
        for state in refresh.sources:
            refresh_key = (
                state.key.source,
                state.key.pool,
                state.key.account_discriminator,
            )
            if state.key.source in refresh_sources:
                raise ValueError("multiple refresh scopes for capacity source")
            if state.key.source not in health_sources or (
                state.key.source in lane_sources and refresh_key not in lane_refresh_keys
            ):
                raise ValueError("refresh source does not match capacity snapshot")
            rows.append(_refresh_health_row(state, now=refresh_now))
            refresh_sources.add(state.key.source)
            covered_sources.add(state.key.source)
    for source in sorted(health_by_source):
        if source in covered_sources:
            continue
        health = health_by_source[source]
        rows.append(_capacity_health_row(health, now=now))
    return tuple(rows)


_FORECAST_REFUSAL_TEXT: Final = {
    ForecastRefusalCode.INVALID_INPUT: "Forecast input is invalid",
    ForecastRefusalCode.INVALID_CLOCK: "Forecast clock is unavailable",
    ForecastRefusalCode.HISTORY_CONSENT_REQUIRED: "Capacity history is off",
    ForecastRefusalCode.HISTORY_TOO_LARGE: "Forecast history is unavailable",
    ForecastRefusalCode.NO_ACCOUNT_DISCRIMINATOR: "Account continuity is unavailable",
    ForecastRefusalCode.IDENTITY_CHANGED: "Account continuity changed",
    ForecastRefusalCode.HISTORY_OUT_OF_ORDER: "History order is uncertain",
    ForecastRefusalCode.DUPLICATE_TIMESTAMP_CONFLICT: "History timestamps conflict",
    ForecastRefusalCode.CROSS_LANE_HISTORY: "Lane continuity changed",
    ForecastRefusalCode.CROSS_ACCOUNT_HISTORY: "Account continuity changed",
    ForecastRefusalCode.NONMONOTONIC_USAGE: "Observed capacity is not monotonic",
    ForecastRefusalCode.INTERVAL_UNBOUNDED: "Observation interval is too large",
    ForecastRefusalCode.INSUFFICIENT_CYCLE_ELAPSED: "More cycle time is required",
    ForecastRefusalCode.INSUFFICIENT_SLOPES: "More observations are required",
    ForecastRefusalCode.INSUFFICIENT_SLOPE_COVERAGE: "More observation coverage is required",
    ForecastRefusalCode.SOURCE_PARTIAL: "Source observation is partial",
    ForecastRefusalCode.SOURCE_STALE: "Source observation is stale",
    ForecastRefusalCode.SOURCE_UNAVAILABLE: "Source observation is unavailable",
    ForecastRefusalCode.RESET_UNKNOWN: "Reset time is unknown",
    ForecastRefusalCode.RESET_DISPUTED: "Reset time is disputed",
    ForecastRefusalCode.RESET_NOT_FUTURE: "Reset is not in the future",
    ForecastRefusalCode.RESET_UNSTABLE: "Reset continuity is uncertain",
    ForecastRefusalCode.NO_POSITIVE_BURN: "No declining capacity trend was observed",
    ForecastRefusalCode.RUNWAY_UNBOUNDED: "Forecast range is unbounded",
    ForecastRefusalCode.EXHAUSTION_NOT_BEFORE_RESET: "Capacity lasts through reset",
    ForecastRefusalCode.AUTHORITY_MISSING: "Forecast release authority is missing",
    ForecastRefusalCode.AUTHORITY_WITHHELD: "Forecast release is not authorized",
    ForecastRefusalCode.RELEASE_AUTHORITY_REVOKED: "Forecast release was revoked",
    ForecastRefusalCode.AUTHORITY_EXPIRED: "Forecast release authority expired",
    ForecastRefusalCode.AUTHORITY_NOT_YET_VALID: "Forecast release authority is not active",
    ForecastRefusalCode.AUTHORITY_MISMATCHED: "Forecast release scope does not match",
    ForecastRefusalCode.CALIBRATION_SAMPLE_MISMATCH: "Forecast calibration scope does not match",
    ForecastRefusalCode.CALIBRATION_INSUFFICIENT: "Forecast calibration is insufficient",
    ForecastRefusalCode.BASELINE_NOT_BEATEN: "Forecast did not beat its baseline",
    ForecastRefusalCode.FALSE_WARNING_REGRESSED: "Forecast warning quality is insufficient",
    ForecastRefusalCode.MISS_RATE_REGRESSED: "Forecast miss quality is insufficient",
    ForecastRefusalCode.FORECAST_UNAVAILABLE: "Forecast evidence is unavailable",
}


def _forecast_status(forecast_view: ReleasedForecast | None) -> CapacityForecastStatusModel:
    if forecast_view is None:
        return CapacityForecastStatusModel(
            False,
            "Forecast unavailable",
            "No released forecast",
            None,
            None,
        )
    if type(forecast_view) is not ReleasedForecast:
        raise TypeError("forecast_view must be ReleasedForecast or None")
    if forecast_view.refusal_code is not None:
        return CapacityForecastStatusModel(
            False,
            "Forecast unavailable",
            _FORECAST_REFUSAL_TEXT.get(
                forecast_view.refusal_code,
                "Forecast evidence is unavailable",
            ),
            None,
            None,
        )
    assert forecast_view.earliest_exhaustion_epoch is not None
    assert forecast_view.latest_exhaustion_epoch is not None
    return CapacityForecastStatusModel(
        True,
        "Forecast available",
        None,
        float(forecast_view.earliest_exhaustion_epoch),
        float(forecast_view.latest_exhaustion_epoch),
    )


_HISTORY_LABEL: Final = {
    HistoryInterval.DAY: "Day",
    HistoryInterval.SEVEN_DAYS: "7 days",
    HistoryInterval.THIRTY_DAYS: "30 days",
}


def _validate_history_summary(summary: CapacityHistorySummary) -> None:
    if not (
        type(summary.observed_sample_count) is int
        and 0 <= summary.observed_sample_count <= MAX_CAPACITY_HISTORY_SAMPLES
        and type(summary.confirmed_reset_cycle_count) is int
        and 0 <= summary.confirmed_reset_cycle_count <= summary.observed_sample_count
        and type(summary.no_observation_intervals) is tuple
        and len(summary.no_observation_intervals) <= 31
        and all(type(interval) is NoObservationInterval for interval in summary.no_observation_intervals)
    ):
        raise ValueError("invalid capacity history summary")
    minimum = summary.minimum_remaining
    maximum = summary.maximum_remaining
    if summary.observed_sample_count == 0:
        if minimum is not NO_OBSERVATION or maximum is not NO_OBSERVATION:
            raise ValueError("invalid empty capacity history summary")
        return
    if not (
        type(minimum) in {int, float}
        and type(maximum) in {int, float}
        and math.isfinite(float(minimum))
        and math.isfinite(float(maximum))
        and 0.0 <= float(minimum) <= float(maximum) <= 100.0
    ):
        raise ValueError("invalid observed capacity history summary")


def _history_rows(
    presentation: CapacityHistoryPresentation | None,
) -> tuple[bool, tuple[CapacityHistoryRowModel, ...]]:
    if presentation is None:
        return False, ()
    if type(presentation) is not CapacityHistoryPresentation:
        raise TypeError("history_summary must be CapacityHistoryPresentation or None")
    if not presentation.enabled:
        return False, ()
    rows: list[CapacityHistoryRowModel] = []
    for item in sorted(presentation.summaries, key=lambda value: value.interval.days):
        summary = item.summary
        _validate_history_summary(summary)
        if summary.observed_sample_count == 0:
            text = "No observation"
        else:
            minimum = float(summary.minimum_remaining)
            maximum = float(summary.maximum_remaining)
            observations = (
                "1 observation"
                if summary.observed_sample_count == 1
                else f"{summary.observed_sample_count} observations"
            )
            resets = (
                "1 confirmed reset"
                if summary.confirmed_reset_cycle_count == 1
                else f"{summary.confirmed_reset_cycle_count} confirmed resets"
            )
            text = f"{observations}, {resets}, {_percent_number(minimum)} to {_percent_number(maximum)} left"
        rows.append(
            CapacityHistoryRowModel(
                label=_HISTORY_LABEL[item.interval],
                summary_text=text,
                observed_sample_count=summary.observed_sample_count,
                confirmed_reset_cycle_count=summary.confirmed_reset_cycle_count,
            )
        )
    return True, tuple(rows)


def _validate_snapshot_projection(
    snapshot: CapacitySnapshot,
    projection: CapacityProjection,
) -> None:
    snapshot_lanes = {lane.key: lane for lane in snapshot.lanes}
    detail_lanes = {authority.lane.key: authority.lane for authority in projection.detail_lanes}
    binding_keys = {authority.lane.key for authority in projection.binding_lanes}
    if (
        snapshot_lanes != detail_lanes
        or not binding_keys.issubset(detail_lanes)
        or len(detail_lanes) != len(projection.detail_lanes)
    ):
        raise ValueError("capacity projection does not match snapshot")


def build_capacity_detail(
    snapshot: CapacitySnapshot | CapacityDetailSnapshot,
    projection: CapacityProjection,
    history_summary: CapacityHistoryPresentation | None,
    forecast_view: ReleasedForecast | None,
    now: float,
) -> CapacityDetailModel:
    """Build a complete immutable detail projection without source work."""
    reference = _validated_now(now)
    if type(snapshot) is CapacitySnapshot:
        capacity_snapshot = snapshot
        refresh_snapshot = None
        refresh_now = None
    elif type(snapshot) is CapacityDetailSnapshot:
        capacity_snapshot = snapshot.capacity
        refresh_snapshot = snapshot.refresh
        refresh_now = snapshot.refresh_now
    else:
        raise TypeError("snapshot must be CapacitySnapshot or CapacityDetailSnapshot")
    if type(projection) is not CapacityProjection:
        raise TypeError("projection must be CapacityProjection")
    _validate_snapshot_projection(capacity_snapshot, projection)
    history_enabled, history = _history_rows(history_summary)
    return CapacityDetailModel(
        heading="Capacity Details",
        providers=_provider_details(projection, now=reference),
        source_health=_source_health_rows(
            capacity_snapshot,
            now=reference,
            refresh=refresh_snapshot,
            refresh_now=refresh_now,
        ),
        forecast=_forecast_status(forecast_view),
        history_enabled=history_enabled,
        history=history,
    )


def format_refresh_outcome(decision: RefreshDecision, now: float) -> str:
    """Format one typed manual-refresh decision without exposing its key."""
    reference = _validated_now(now)
    if type(decision) is not RefreshDecision:
        raise TypeError("decision must be RefreshDecision")
    if decision.kind is RefreshDecisionKind.START:
        return "Refreshing"
    if decision.kind is RefreshDecisionKind.DISABLED:
        return "Capacity refresh disabled"
    if decision.kind is RefreshDecisionKind.UNSUPPORTED:
        return "Capacity refresh unsupported"
    duration = _duration_until(decision.retry_at, reference)
    if decision.kind is RefreshDecisionKind.QUEUED_FOR_COOLDOWN:
        return f"Refresh queued for {duration or 'cooldown'}"
    if decision.reason is RefreshDecisionReason.IN_FLIGHT:
        return "Refresh already in progress"
    if decision.reason is RefreshDecisionReason.ALREADY_QUEUED:
        return f"Refresh already queued for {duration or 'cooldown'}"
    if decision.reason is RefreshDecisionReason.COOLDOWN:
        return f"Capacity refresh cooling down for {duration or 'cooldown'}"
    if decision.reason is RefreshDecisionReason.NO_QUEUED_REFRESH:
        return "No queued capacity refresh"
    return "Refresh request coalesced"


def build_manual_refresh_status(
    decision: RefreshDecision,
    now: float,
) -> ManualRefreshStatusModel:
    """Build pure button/status semantics without assigning a native role."""
    reference = _validated_now(now)
    if type(decision) is not RefreshDecision:
        raise TypeError("decision must be RefreshDecision")
    return ManualRefreshStatusModel(
        text=format_refresh_outcome(decision, reference),
        can_request=decision.kind not in {RefreshDecisionKind.DISABLED, RefreshDecisionKind.UNSUPPORTED},
        announcement_minute=int(reference // 60.0),
    )


def build_capacity_card_accessibility(
    card: CapacityCardModel,
    now: float,
) -> CapacityAccessibilityGroupModel:
    """Describe one accessibility group and visual-order lane children."""
    reference = _validated_now(now)
    if type(card) is not CapacityCardModel:
        raise TypeError("card must be CapacityCardModel")
    children = tuple(
        CapacityAccessibilityChildModel(
            label=f"{row.provider}, {row.semantic_name}",
            value=", ".join((row.remaining_text, row.reset_text, row.freshness_text)),
            help="Capacity limit details",
            countdown_announcement_minute=(int(reference // 60.0) if row.reset_text.startswith("Resets") else None),
        )
        for row in card.rows
    )
    value = (
        card.status_text
        if card.status_text is not None
        else ("1 capacity limit" if len(children) == 1 else f"{len(children)} capacity limits")
    )
    return CapacityAccessibilityGroupModel(
        label="Capacity",
        value=value,
        help="Capacity status",
        children=children,
    )


__all__ = [
    "MAX_CAPACITY_CARD_ROWS",
    "MAX_CAPACITY_HISTORY_SUMMARIES",
    "MAX_CAPACITY_PRESENTATION_TEXT",
    "CapacityAccessibilityChildModel",
    "CapacityAccessibilityGroupModel",
    "CapacityApplicabilityGroupModel",
    "CapacityCardModel",
    "CapacityCardRowModel",
    "CapacityDetailModel",
    "CapacityDetailSnapshot",
    "CapacityForecastStatusModel",
    "CapacityHistoryPresentation",
    "CapacityHistoryRowModel",
    "CapacityHistorySummaryInput",
    "CapacityLaneDetailModel",
    "CapacityProviderDetailModel",
    "CapacitySourceHealthRowModel",
    "ManualRefreshStatusModel",
    "build_capacity_card",
    "build_capacity_card_accessibility",
    "build_capacity_detail",
    "build_manual_refresh_status",
    "format_freshness",
    "format_refresh_outcome",
    "format_remaining",
    "format_reset",
]
