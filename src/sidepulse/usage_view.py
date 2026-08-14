"""Immutable provider usage presentation shared by menu and Settings."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace

from .capacity_types import (
    CapacityUnit,
    CapacityValue,
    ObservationState,
    QuotaEffect,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    ResetState,
    SourceKey,
)
from .freshness import FUTURE_CLOCK_SKEW_SECONDS
from .reset_policy import format_reset_countdown, parse_reset_epoch

MAX_SOURCE_TEXT_LENGTH = 120


def _numeric(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _window_minutes(entry: Mapping) -> int | None:
    direct = _numeric(entry.get("window_minutes"))
    if direct is None:
        seconds = _numeric(
            entry.get("limit_window_seconds", entry.get("window_seconds"))
        )
        direct = seconds / 60.0 if seconds is not None else None
    if direct is None or direct <= 0.0:
        return None
    return max(1, int(round(direct)))


def _reset_value(entry: Mapping):
    for key in ("resets_at", "reset_at"):
        value = entry.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (str, int, float)):
            return value
    return None


@dataclass(frozen=True, slots=True)
class UsageWindowViewModel:
    lane_key: QuotaLaneKey
    provider_title: str
    label: str
    window_minutes: int | None
    capacity: CapacityValue
    reset_at: str | int | float | None = None
    reset_epoch: float | None = None
    reset_state: ResetState = ResetState.UNKNOWN

    @property
    def provider_id(self) -> str:
        """Temporary provider-only projection derived from canonical identity."""
        return self.lane_key.source.provider_id

    @property
    def usage_known(self) -> bool:
        return self.capacity.remaining is not None

    @property
    def reset_known(self) -> bool:
        return (
            self.reset_state is ResetState.FUTURE
            and _numeric(self.reset_epoch) is not None
        )

    @property
    def percent_remaining(self) -> float | None:
        return self.capacity.remaining

    @property
    def percent_used(self) -> float | None:
        """Compatibility-only inverse for legacy controller consumers."""
        remaining = self.capacity.remaining
        if remaining is None:
            return None
        return 100.0 - remaining

    def reset_text(self, now: float) -> str | None:
        if not self.reset_known:
            return None
        return format_reset_countdown(self.reset_epoch, now=now)

    @property
    def duration_text(self) -> str:
        minutes = self.window_minutes
        if minutes is None:
            return "limit"
        if minutes % (7 * 24 * 60) == 0:
            return f"{minutes // (24 * 60)}d"
        if minutes % 60 == 0:
            return f"{minutes // 60}h"
        return f"{minutes}m"

    @property
    def compact_text(self) -> str:
        duration = self.duration_text
        label = self.label.strip()
        generic = label.lower() in {
            "",
            "primary",
            "secondary",
            "five_hour",
            "seven_day",
            "5-hour",
            "weekly",
            "limit",
        }
        prefix = "" if generic or duration == "limit" else f"{label} "
        if duration == "limit" and label:
            duration = label
        remaining = self.capacity.remaining
        if remaining is None:
            return f"{prefix}{duration}".strip()
        return f"{prefix}{duration} {remaining:.0f}% left"


@dataclass(frozen=True, slots=True)
class LocalActivitySection:
    """Local aggregate evidence, kept separate from provider quota lanes."""

    summary_text: str | None = None
    detail_text: str | None = None
    partial: bool = False
    source_text: str | None = None


@dataclass(frozen=True, slots=True)
class CostEstimateSection:
    """Optional local estimate copy that never participates in quota authority."""

    text: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderUsageViewModel:
    provider_id: str
    provider_title: str
    windows: tuple[UsageWindowViewModel, ...]
    last_success_at: float | None
    stale: bool
    missing: bool
    refreshing: bool
    error_text: str | None
    local_activity: LocalActivitySection = LocalActivitySection()
    cost_estimate: CostEstimateSection = CostEstimateSection()

    def __post_init__(self) -> None:
        if self.stale:
            object.__setattr__(
                self,
                "windows",
                tuple(
                    replace(window, reset_state=ResetState.STALE)
                    for window in self.windows
                ),
            )

    @property
    def summary_text(self) -> str | None:
        return self.local_activity.summary_text

    @property
    def detail_text(self) -> str | None:
        return self.local_activity.detail_text

    @property
    def partial(self) -> bool:
        return self.local_activity.partial

    @property
    def source_text(self) -> str | None:
        return self.local_activity.source_text

    @property
    def _usage_text(self) -> str:
        window_text = " · ".join(window.compact_text for window in self.windows)
        return window_text or self.summary_text or "No usage yet"

    @property
    def menu_line(self) -> str:
        if self.missing:
            if self.refreshing:
                state = "Loading..."
            elif self.error_text:
                state = self.error_text
            else:
                state = "Not loaded"
            return f"{self.provider_title} · {state}"

        parts = [self.provider_title, self._usage_text]
        if self.refreshing:
            parts.append("refreshing")
        if self.partial:
            parts.append("partial")
        if self.source_text:
            parts.append(self.source_text)
        if self.stale:
            parts.append("stale")
        if self.error_text:
            parts.append(self.error_text)
        return " · ".join(parts)

    @property
    def settings_text(self) -> str:
        parts = []
        if self.summary_text:
            parts.append(self.summary_text)
        window_text = " · ".join(window.compact_text for window in self.windows)
        if window_text:
            parts.append(window_text)
        if not parts:
            parts.append("No usage yet")
        if self.detail_text:
            parts.append(self.detail_text)
        if self.cost_estimate.text:
            parts.append(self.cost_estimate.text)
        if self.partial:
            parts.append("Partial")
        if self.source_text:
            parts.append(self.source_text)
        if self.stale:
            parts.append("Stale")
        if self.error_text:
            parts.append(self.error_text)
        if self.missing:
            # Same rule as `menu_line`: with nothing to show, the reason IS
            # the line. Appending it after "No usage yet" read as two
            # different explanations for one empty card.
            parts = [self.error_text or ("Loading..." if self.refreshing else "Not loaded")]
        return " · ".join(parts)


def _elapsed(now: float, timestamp: float) -> float:
    age = float(now) - float(timestamp)
    if age >= 0.0:
        return age
    if -age <= FUTURE_CLOCK_SKEW_SECONDS:
        return 0.0
    return float("inf")


def _bounded_source_text(value) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(str(value).split())
    if not collapsed:
        return None
    path_pattern = re.compile(
        r"(?:^|\s)(?:file://|~[/\\]|/(?:Users|home|var|tmp|Volumes|private|opt|etc|Library)(?:/|$)|[A-Za-z]:\\)",
        re.IGNORECASE,
    )
    safe_parts = [
        part.strip()
        for part in re.split(r"\s+·\s+", collapsed)
        if part.strip() and not path_pattern.search(part)
    ]
    text = " · ".join(safe_parts)
    if not text:
        return None
    if len(text) > MAX_SOURCE_TEXT_LENGTH:
        return f"{text[: MAX_SOURCE_TEXT_LENGTH - 3].rstrip()}..."
    return text


def source_text_for_coverage(coverage) -> str | None:
    if coverage is None:
        return None
    prefix = "Local transcripts"
    if coverage.status.value == "missing":
        return f"{prefix} missing"
    file_word = "file" if coverage.files_discovered == 1 else "files"
    if coverage.status.value == "failed":
        if coverage.files_discovered > 0:
            return _bounded_source_text(
                f"{prefix} · {coverage.files_discovered} {file_word} · failed"
            )
        return _bounded_source_text(f"{prefix} · failed")
    if coverage.status.value == "partial":
        return _bounded_source_text(
            f"{prefix} · {coverage.files_discovered} {file_word} · partial"
        )
    return _bounded_source_text(
        f"{prefix} · {coverage.files_discovered} {file_word}"
    )


def _capacity_from_legacy_used(value) -> CapacityValue:
    used = _numeric(value)
    if used is None:
        return CapacityValue(
            CapacityUnit.PERCENT_REMAINING,
            None,
            ObservationState.NULL,
        )
    remaining = 100.0 - max(0.0, min(100.0, used))
    state = (
        ObservationState.OBSERVED_ZERO
        if remaining == 0.0
        else ObservationState.OBSERVED
    )
    return CapacityValue(CapacityUnit.PERCENT_REMAINING, remaining, state)


def _quota_source_key(provider_id: str) -> SourceKey:
    from .providers import negotiated_provider_sources

    matches = tuple(
        row.source_key
        for row in negotiated_provider_sources()
        if row.source_key.provider_id == provider_id
        and row.source_key.capability_id == "remote_quota_windows"
        and row.observation_invocation_allowed
    )
    if len(matches) == 1:
        return matches[0]
    return SourceKey(
        str(provider_id),
        "legacy",
        "unspecified",
        "remote_quota_windows",
    )


def adapt_legacy_usage_windows(
    provider_id: str,
    provider_title: str,
    windows,
    *,
    now: float,
    source_key: SourceKey | None = None,
) -> tuple[UsageWindowViewModel, ...]:
    """Convert legacy used-first mappings once into typed remaining-first rows."""
    normalized: list[UsageWindowViewModel] = []
    canonical_source = source_key or _quota_source_key(str(provider_id))
    if canonical_source.provider_id != str(provider_id):
        raise ValueError("usage source does not match provider")
    for index, entry in enumerate(windows or ()):
        if not isinstance(entry, Mapping):
            continue
        used_value = entry.get("used_percent")
        if _numeric(used_value) is None:
            used_value = entry.get("utilization")
        capacity = _capacity_from_legacy_used(used_value)
        reset_at = _reset_value(entry)
        reset_epoch = parse_reset_epoch(reset_at, now=now)
        if capacity.remaining is None and reset_epoch is None:
            continue
        normalized.append(
            UsageWindowViewModel(
                lane_key=QuotaLaneKey(
                    canonical_source,
                    f"legacy:{index}",
                    "unspecified",
                    None,
                    (
                        f"minutes-{_window_minutes(entry)}"
                        if _window_minutes(entry) is not None
                        else "unspecified"
                    ),
                    QuotaEffect.ALL_WORKLOADS,
                ),
                provider_title=str(provider_title),
                label=str(entry.get("label") or entry.get("name") or "limit"),
                window_minutes=_window_minutes(entry),
                capacity=capacity,
                reset_at=reset_at,
                reset_epoch=reset_epoch,
                reset_state=(
                    ResetState.FUTURE
                    if reset_epoch is not None
                    else ResetState.UNKNOWN
                ),
            )
        )
    return tuple(normalized)


def adapt_capacity_observations(
    provider_id: str,
    provider_title: str,
    observations,
    *,
    reset_decisions: Mapping[QuotaLaneKey, object] | None = None,
) -> tuple[UsageWindowViewModel, ...]:
    """Project typed capacity observations without restoring untrusted resets."""
    decisions = reset_decisions or {}
    normalized: list[UsageWindowViewModel] = []
    for observation in observations or ():
        if not isinstance(observation, QuotaLaneObservation):
            continue
        decision = decisions.get(observation.key)
        reset = getattr(decision, "reset", observation.reset)
        if not isinstance(reset, ResetFact):
            reset = observation.reset
        trusted = bool(getattr(decision, "forecast_eligible", False))
        reset_state = reset.state
        if reset_state is ResetState.FUTURE and not trusted:
            reset_state = ResetState.DISPUTED
        normalized.append(
            UsageWindowViewModel(
                lane_key=observation.key,
                provider_title=str(provider_title),
                label=observation.semantic_name,
                window_minutes=(
                    max(1, int(round(reset.window_minutes)))
                    if reset.window_minutes is not None
                    else None
                ),
                capacity=observation.value,
                reset_at=reset.reset_epoch,
                reset_epoch=reset.reset_epoch,
                reset_state=reset_state,
            )
        )
    return tuple(normalized)


def build_provider_usage_view(
    provider_id: str,
    provider_title: str,
    windows,
    *,
    last_success_at: float | None = None,
    now: float,
    refreshing: bool = False,
    error_text: str | None = None,
    summary_text: str | None = None,
    detail_text: str | None = None,
    partial: bool = False,
    source_text: str | None = None,
    local_activity: LocalActivitySection | None = None,
    cost_estimate: CostEstimateSection | None = None,
    reset_now: float | None = None,
    stale_after_seconds: float = 300.0,
    stale_evidence: bool = False,
    capacity_observations=(),
    reset_decisions: Mapping[QuotaLaneKey, object] | None = None,
    source_key: SourceKey | None = None,
) -> ProviderUsageViewModel:
    reset_reference = now if reset_now is None else reset_now
    normalized = (
        adapt_capacity_observations(
            provider_id,
            provider_title,
            capacity_observations,
            reset_decisions=reset_decisions,
        )
        if capacity_observations
        else adapt_legacy_usage_windows(
            provider_id,
            provider_title,
            windows,
            now=reset_reference,
            source_key=source_key,
        )
    )
    if local_activity is None:
        activity = LocalActivitySection(
            summary_text=summary_text,
            detail_text=detail_text,
            partial=bool(partial),
            source_text=_bounded_source_text(source_text),
        )
    else:
        activity = LocalActivitySection(
            summary_text=local_activity.summary_text,
            detail_text=local_activity.detail_text,
            partial=bool(local_activity.partial),
            source_text=_bounded_source_text(local_activity.source_text),
        )
    estimate = cost_estimate or CostEstimateSection()
    known_data = bool(
        normalized
        or activity.summary_text
        or activity.detail_text
        or activity.source_text
        or estimate.text
        or last_success_at is not None
    )
    missing = not known_data
    error = str(error_text).strip() if error_text else None
    # `stale_evidence` is the freshness the AUTHORITY layer decided, not one
    # derived from a clock. A reading it forgave as "old but real" -- a source
    # that is STALE, or one that failed while still holding a last-known-good
    # -- arrives with a fresh `last_success_at` (the refresh itself worked) and
    # no error, so every clock-derived test called it current.
    stale = bool(
        known_data
        and (
            bool(stale_evidence)
            or error is not None
            or last_success_at is None
            or _elapsed(now, last_success_at) >= float(stale_after_seconds)
        )
    )
    return ProviderUsageViewModel(
        provider_id=str(provider_id),
        provider_title=str(provider_title),
        windows=tuple(normalized),
        last_success_at=last_success_at,
        stale=stale,
        missing=missing,
        refreshing=bool(refreshing),
        error_text=error,
        local_activity=activity,
        cost_estimate=estimate,
    )
