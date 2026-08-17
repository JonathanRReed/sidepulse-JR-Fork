"""Pure projection and fallback text for the native Usage Center."""

from __future__ import annotations

from dataclasses import dataclass

from .provider_usage_platform import ProviderSourceState, provider_descriptor
from .provider_usage_qol import format_reset_countdown, usage_totals
from .provider_usage_runtime import ProviderUsageState


@dataclass(frozen=True, slots=True)
class UsageCenterLane:
    title: str
    subtitle: str


@dataclass(frozen=True, slots=True)
class UsageCenterSection:
    provider_id: str
    title: str
    account: str | None
    status: str
    lanes: tuple[UsageCenterLane, ...]
    metrics: tuple[str, ...]
    action_label: str | None
    incident: str | None


@dataclass(frozen=True, slots=True)
class UsageCenterProjection:
    title: str
    subtitle: str
    sections: tuple[UsageCenterSection, ...]
    aggregate_metrics: tuple[str, ...]
    refreshing: bool


def _status_label(state: ProviderSourceState) -> str:
    return {
        ProviderSourceState.DISABLED: "Off",
        ProviderSourceState.READY: "Connected",
        ProviderSourceState.NEEDS_CONSENT: "Permission required",
        ProviderSourceState.NEEDS_SIGN_IN: "Sign-in required",
        ProviderSourceState.SOURCE_NOT_FOUND: "Source not found",
        ProviderSourceState.UNAVAILABLE: "Unavailable",
        ProviderSourceState.RATE_LIMITED: "Temporarily rate limited",
        ProviderSourceState.STALE: "Last known value",
        ProviderSourceState.ERROR: "Error",
        ProviderSourceState.UNSUPPORTED: "Unsupported",
    }[state]


def project_usage_center(
    state: ProviderUsageState,
    *,
    now: float,
) -> UsageCenterProjection:
    sections = []
    for snapshot in state.snapshots:
        lanes = []
        for lane in snapshot.lanes:
            remaining = (
                "remaining unknown"
                if lane.remaining_percent is None
                else f"{lane.remaining_percent:.0f}% left"
            )
            subtitle_parts = [format_reset_countdown(lane.reset_at, now=now)]
            if not lane.bindable:
                subtitle_parts.append("detail only")
            if snapshot.state is ProviderSourceState.STALE:
                subtitle_parts.append("stale")
            lanes.append(
                UsageCenterLane(
                    f"{lane.label} · {remaining}",
                    " · ".join(subtitle_parts),
                )
            )
        token_total = (
            snapshot.input_tokens
            + snapshot.cached_input_tokens
            + snapshot.output_tokens
        )
        metrics = []
        if token_total:
            metrics.append(f"{token_total:,} tokens")
        if snapshot.model_count:
            metrics.append(
                f"{snapshot.model_count} model"
                f"{'s' if snapshot.model_count != 1 else ''}"
            )
        if snapshot.estimated_cost_usd is not None:
            metrics.append(f"Estimated cost ${snapshot.estimated_cost_usd:.2f}")
        if snapshot.cache_savings_usd is not None:
            metrics.append(f"Cache savings ${snapshot.cache_savings_usd:.2f}")
        if snapshot.credits_remaining is not None:
            metrics.append(f"{snapshot.credits_remaining:g} credits left")
        sections.append(
            UsageCenterSection(
                snapshot.provider_id,
                provider_descriptor(snapshot.provider_id).label,
                snapshot.account_label,
                _status_label(snapshot.state),
                tuple(lanes),
                tuple(metrics),
                snapshot.action_label,
                snapshot.incident,
            )
        )
    totals = usage_totals(state.snapshots)
    aggregate = []
    token_total = (
        totals.input_tokens
        + totals.cached_input_tokens
        + totals.output_tokens
    )
    if token_total:
        aggregate.append(f"{token_total:,} tokens across this Mac")
    if totals.estimated_cost_usd is not None:
        aggregate.append(f"Estimated cost ${totals.estimated_cost_usd:.2f}")
    if totals.cache_savings_usd is not None:
        aggregate.append(f"Cache savings ${totals.cache_savings_usd:.2f}")
    subtitle = (
        "Refreshing provider usage…"
        if state.refreshing
        else "Quota windows, reset times, tokens, models, and estimates"
    )
    return UsageCenterProjection(
        "Usage Center",
        subtitle,
        tuple(sections),
        tuple(aggregate),
        state.refreshing,
    )


def usage_center_text(projection: UsageCenterProjection) -> str:
    lines = [projection.title, projection.subtitle]
    if projection.aggregate_metrics:
        lines.extend(("", *projection.aggregate_metrics))
    for section in projection.sections:
        lines.extend(("", section.title))
        if section.account:
            lines.append(section.account)
        lines.append(section.status)
        for lane in section.lanes:
            lines.append(f"  {lane.title}")
            lines.append(f"    {lane.subtitle}")
        for metric in section.metrics:
            lines.append(f"  {metric}")
        if section.incident:
            lines.append(f"  Incident: {section.incident}")
        if section.action_label:
            lines.append(f"  Action: {section.action_label}")
    return "\n".join(lines)


__all__ = [
    "UsageCenterLane",
    "UsageCenterProjection",
    "UsageCenterSection",
    "project_usage_center",
    "usage_center_text",
]
