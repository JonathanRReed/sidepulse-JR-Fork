"""Pure priority and coalescing policy for physical LED writes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .presentation_policy import GlanceSemantic, ResolvedGlance, valid_finite_cue
from .runtime_scheduler import RuntimeWorkPriority

_EXPLICIT_DISPLAYS = frozenset({"signal_test"})
_URGENT_DISPLAYS = frozenset(
    {"failure", "escalation", "weather", "low_battery"}
)
_IMPORTANT_DISPLAYS = frozenset(
    {
        "quota_alert",
        "reminders",
        "completion",
        "reset_celebration",
        "connection_notice",
        "peek",
        "all_clear",
        "calendar",
    }
)
_URGENT_SEMANTICS = frozenset(
    {
        GlanceSemantic.ATTENTION,
        GlanceSemantic.FRESH_FAILURE,
        GlanceSemantic.UNRESOLVED_FAILURE,
    }
)


@dataclass(frozen=True, slots=True)
class HardwareWritePolicy:
    priority: RuntimeWorkPriority
    coalesce_identity: str


def hardware_write_policy(
    display_kind: str,
    resolved_glance: ResolvedGlance | None,
) -> HardwareWritePolicy:
    """Protect explicit and important states while keeping ordinary frames latest-wins."""

    if type(display_kind) is not str or not display_kind or len(display_kind) > 64:
        raise ValueError("invalid hardware display kind")
    normalized = display_kind.strip().casefold().replace("_", "-")
    if display_kind in _EXPLICIT_DISPLAYS:
        return HardwareWritePolicy(
            RuntimeWorkPriority.EXPLICIT,
            f"preview-{normalized}",
        )

    if isinstance(resolved_glance, ResolvedGlance) and valid_finite_cue(
        resolved_glance.cue
    ):
        cue = resolved_glance.cue
        assert cue is not None
        digest = hashlib.sha256(cue.event_key.encode("utf-8")).hexdigest()[:20]
        priority = (
            RuntimeWorkPriority.URGENT
            if cue.semantic in _URGENT_SEMANTICS
            else RuntimeWorkPriority.IMPORTANT
        )
        return HardwareWritePolicy(priority, f"cue-{digest}")

    if display_kind in _URGENT_DISPLAYS:
        return HardwareWritePolicy(
            RuntimeWorkPriority.URGENT,
            f"signal-{normalized}",
        )
    if display_kind in _IMPORTANT_DISPLAYS:
        return HardwareWritePolicy(
            RuntimeWorkPriority.IMPORTANT,
            f"signal-{normalized}",
        )
    if (
        isinstance(resolved_glance, ResolvedGlance)
        and resolved_glance.semantic in _URGENT_SEMANTICS
    ):
        return HardwareWritePolicy(
            RuntimeWorkPriority.URGENT,
            f"semantic-{resolved_glance.semantic.value.replace('_', '-')}",
        )
    if (
        isinstance(resolved_glance, ResolvedGlance)
        and resolved_glance.semantic is GlanceSemantic.FRESH_COMPLETION
    ):
        return HardwareWritePolicy(
            RuntimeWorkPriority.IMPORTANT,
            "semantic-fresh-completion",
        )
    return HardwareWritePolicy(RuntimeWorkPriority.COALESCIBLE, "latest")


def hardware_coalesce_key(device_key: str, identity: str) -> str:
    """Join already opaque device and semantic identities for the worker."""

    if type(device_key) is not str or type(identity) is not str:
        raise ValueError("invalid hardware coalescing identity")
    return f"{device_key}:{identity}"
