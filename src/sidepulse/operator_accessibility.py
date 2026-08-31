"""Pure, bounded accessibility semantics for operator surfaces.

This module owns no AppKit objects, observers, clocks, persistence, or delivery.
It translates reviewed canonical and projection records into product-owned text
and returns exact transition announcements for a native adapter to publish.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Final

from .agent_browser import AgentBrowserDocument
from .mailbox import MailboxRow
from .navigation_policy import OperatorActionDescriptor
from .operator_state import (
    PRESENCE_HORIZON_SECONDS,
    CanonicalOperatorEvent,
    CanonicalOperatorState,
    InterruptionClass,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
    active_work_went_silent,
    projection_now_epoch,
    semantic_event_key_to_payload,
)
from .presentation_policy import (
    FiniteCueState,
    GlanceOverrideReason,
    GlanceSemantic,
    ResolvedGlance,
)
from .product_identity import PRODUCT_DISPLAY_NAME
from .provider_facts import SourceFreshness, WorkKey, WorkLifecycle


def _present_primary_works(state: CanonicalOperatorState) -> tuple:
    """Primary works heard within the presence horizon.

    Counts built from raw lifecycle said "1 working" in the menu bar for
    a session dead for 21 hours, while the Agent Browser beside it
    already called the same work stale. Presence, not history, is what a
    count in shared menu-bar space may claim.
    """
    clock = state.last_clock
    if clock is None:
        return tuple(work for work in state.works if work.parent_key is None)
    horizon = clock.wall_epoch - PRESENCE_HORIZON_SECONDS
    return tuple(
        work
        for work in state.works
        if work.parent_key is None
        and work.watermark.occurred_at_epoch >= horizon
    )


MAX_ACCESSIBILITY_LABEL_LENGTH: Final = 256
MAX_ACCESSIBILITY_VALUE_LENGTH: Final = 512
MAX_ACCESSIBILITY_HELP_LENGTH: Final = 256
MAX_ACCESSIBILITY_ANNOUNCEMENT_KEY_LENGTH: Final = 128
MAX_ACCESSIBILITY_ANNOUNCEMENT_TEXT_LENGTH: Final = 256
MAX_FOCUS_KEY_LENGTH: Final = 128
MAX_TEXT_SELECTION_COMPONENT: Final = (1 << 63) - 1
MAX_ANNOUNCED_EVENT_KEYS: Final = 2_000

_PRODUCT_PROVIDER_LABELS: Final = {
    "antigravity": "Antigravity",
    "claude": "Claude",
    "codex": "Codex",
    "cursor": "Cursor",
    "devin": "Devin",
    "grok": "Grok",
    "hermes": "Hermes",
    "kiro": "Kiro",
    "openclaw": "OpenClaw",
    "opencode": "OpenCode",
}
_LIFECYCLE_LABELS: Final = {
    WorkLifecycle.IDLE: "Idle",
    WorkLifecycle.ACTIVE: "Active",
    WorkLifecycle.WAITING: "Waiting",
    WorkLifecycle.COMPLETED: "Completed",
    WorkLifecycle.FAILED: "Failed",
    WorkLifecycle.UNKNOWN: "Unknown",
}
_FRESHNESS_LABELS: Final = {
    SourceFreshness.FRESH: "Source fresh",
    SourceFreshness.STALE: "Source stale",
    SourceFreshness.TIMING_UNCERTAIN: "Source timing uncertain",
    SourceFreshness.PARTIAL: "Source partially available",
    SourceFreshness.UNAVAILABLE: "Source unavailable",
    SourceFreshness.RESTORED: "Source restored",
}
_GLANCE_HEADLINES: Final = {
    GlanceSemantic.ATTENTION: "Needs your attention",
    GlanceSemantic.FRESH_FAILURE: "New failure",
    GlanceSemantic.FRESH_COMPLETION: "Agent completed",
    GlanceSemantic.ACTIVE: "Agents active",
    GlanceSemantic.UNRESOLVED_FAILURE: "Failure needs review",
    GlanceSemantic.CAPACITY: "Capacity status available",
    GlanceSemantic.REST: "No agents need attention",
}
_DISABLED_REASONS: Final = frozenset(
    {
        "Not available",
        "Source is stale",
        "Target changed",
        "Multiple sources match",
    }
)
_TEXT_SCALE_BY_PERCENT: Final = {
    100: 1.0,
    125: 1.25,
    150: 1.5,
    175: 1.75,
    200: 2.0,
}
_FOCUS_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]*\Z")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_LONG_OPAQUE = re.compile(r"\b[0-9a-f]{24,}\b", re.IGNORECASE)
_PRIVATE_MARKER = re.compile(
    r"(?:^|[\s._~:/\\-])"
    r"(?:api[_-]?key|authorization|bearer|command|cookie|credential|email|"
    r"message|password|passwd|path|private[_-]?key|prompt|raw[_-]?error|"
    r"secret|session[_-]?id|token|transcript|url)"
    r"(?:$|[\s._~:/\\-])",
    re.IGNORECASE,
)


def _private_shaped_text(value: str) -> bool:
    lowered = value.casefold()
    return bool(
        _EMAIL.search(value)
        or _UUID.search(value)
        or _LONG_OPAQUE.search(value)
        or _PRIVATE_MARKER.search(value)
        or "://" in lowered
        or lowered.startswith(("/", "~/"))
        or "/users/" in lowered
        or "/home/" in lowered
        or "rm -rf" in lowered
        or "traceback:" in lowered
        or "$(" in value
        or "`" in value
        or re.search(r"\b[A-Za-z]:[\\/]", value) is not None
    )


def _valid_public_text(value: object, *, maximum: int) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= maximum
        and value == value.strip()
        and value.isprintable()
        and not _private_shaped_text(value)
    )


def _valid_focus_key(value: object, *, required: bool) -> bool:
    if value is None:
        return not required
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_FOCUS_KEY_LENGTH
        and _FOCUS_KEY.fullmatch(value) is not None
        and not _private_shaped_text(value)
    )


@dataclass(frozen=True, slots=True)
class AccessibilityText:
    label: str
    value: str
    help: str

    def __post_init__(self) -> None:
        if not (
            _valid_public_text(self.label, maximum=MAX_ACCESSIBILITY_LABEL_LENGTH)
            and _valid_public_text(self.value, maximum=MAX_ACCESSIBILITY_VALUE_LENGTH)
            and _valid_public_text(self.help, maximum=MAX_ACCESSIBILITY_HELP_LENGTH)
        ):
            raise ValueError("invalid accessibility text")


@dataclass(frozen=True, slots=True)
class FocusSnapshot:
    window_key: str
    control_key: str | None
    selected_work_key: WorkKey | None
    text_selection: tuple[int, int] | None
    sidebar_key: str | None

    def __post_init__(self) -> None:
        selection_valid = self.text_selection is None or (
            type(self.text_selection) is tuple
            and len(self.text_selection) == 2
            and all(
                type(component) is int and 0 <= component <= MAX_TEXT_SELECTION_COMPONENT
                for component in self.text_selection
            )
        )
        if not (
            _valid_focus_key(self.window_key, required=True)
            and _valid_focus_key(self.control_key, required=False)
            and (self.selected_work_key is None or type(self.selected_work_key) is WorkKey)
            and selection_valid
            and _valid_focus_key(self.sidebar_key, required=False)
        ):
            raise ValueError("invalid focus snapshot")


class AnnouncementPriority(str, Enum):
    ACTIONABLE = "actionable"
    ERROR = "error"
    OUTCOME = "outcome"
    SUCCESS = "success"


@dataclass(frozen=True, slots=True)
class AccessibilityAnnouncement:
    key: str
    text: str
    priority: AnnouncementPriority

    def __post_init__(self) -> None:
        if not (
            _valid_public_text(
                self.key,
                maximum=MAX_ACCESSIBILITY_ANNOUNCEMENT_KEY_LENGTH,
            )
            and _valid_public_text(
                self.text,
                maximum=MAX_ACCESSIBILITY_ANNOUNCEMENT_TEXT_LENGTH,
            )
            and type(self.priority) is AnnouncementPriority
        ):
            raise ValueError("invalid accessibility announcement")


def status_item_accessibility(
    state: CanonicalOperatorState,
    glance: ResolvedGlance,
    *,
    finite_cues: FiniteCueState | None = None,
) -> AccessibilityText:
    """Return one stable status-item name and a never-blank semantic value."""
    if type(state) is not CanonicalOperatorState or type(glance) is not ResolvedGlance:
        return _status_fallback()
    headline = _GLANCE_HEADLINES.get(glance.semantic)
    if headline is None:
        return _status_fallback()

    details: list[str] = []
    # Main agents only, in EVERY number on this surface. One main session
    # fans out to 100+ Task workers; counting works at every depth is how
    # this line read "Active: 34" with one main agent running. A worker
    # also never asks for the user, so its requests are not "requests
    # need you" either.
    primary_works = _present_primary_works(state)
    primary_work_keys = frozenset(work.key for work in primary_works)
    primary_requests = tuple(
        request for request in state.requests if request.key.work_key in primary_work_keys
    )
    live_unacknowledged = sum(request.phase is RequestPhase.LIVE_UNACKNOWLEDGED for request in primary_requests)
    acknowledged = sum(request.phase is RequestPhase.LIVE_ACKNOWLEDGED for request in primary_requests)
    stale_holds = sum(request.phase is RequestPhase.STALE_HOLD for request in primary_requests)
    if live_unacknowledged:
        details.append(
            "1 request needs you" if live_unacknowledged == 1 else f"{live_unacknowledged} requests need you"
        )
    if acknowledged:
        details.append(f"Acknowledged locally: {acknowledged}")
    if stale_holds:
        details.append(f"Stale request held: {stale_holds}")

    now_epoch = projection_now_epoch(state)
    active = sum(
        work.lifecycle is WorkLifecycle.ACTIVE
        and not active_work_went_silent(work, now_epoch)
        for work in primary_works
    )
    failures = sum(work.lifecycle is WorkLifecycle.FAILED for work in primary_works)
    if active:
        details.append(f"Active: {active}")
    if failures:
        details.append(f"Failed: {failures}")

    freshness_values = {work.source_freshness for work in state.works if type(work.source_freshness) is SourceFreshness}
    freshness_values.update(
        request.source_freshness for request in state.requests if type(request.source_freshness) is SourceFreshness
    )
    for freshness in (
        SourceFreshness.STALE,
        SourceFreshness.TIMING_UNCERTAIN,
        SourceFreshness.PARTIAL,
        SourceFreshness.UNAVAILABLE,
        SourceFreshness.RESTORED,
    ):
        if freshness in freshness_values:
            details.append(_FRESHNESS_LABELS[freshness])

    if glance.override_reason is GlanceOverrideReason.SHARED_SPACE_PRIVACY:
        details.append("Quiet presentation")
    if glance.cue is not None or (
        type(finite_cues) is FiniteCueState and (finite_cues.active is not None or finite_cues.pending is not None)
    ):
        details.append("Brief status cue")
    if type(finite_cues) is FiniteCueState and finite_cues.overflowed is True:
        details.append("Additional updates waiting")

    return AccessibilityText(
        PRODUCT_DISPLAY_NAME,
        _bounded_join((headline, *details)),
        f"Open {PRODUCT_DISPLAY_NAME} status",
    )


#: Longest menu-bar title this app will publish. The menu bar is shared,
#: finite space owned by macOS: anything longer is truncated mid-word by
#: AppKit, and a number that ends in an ellipsis is not a ledger.
MAX_STATUS_ITEM_TITLE_LENGTH: Final = 24


def status_item_title(
    state: CanonicalOperatorState,
    glance: ResolvedGlance,
) -> str:
    """The short ledger the MENU BAR shows, beside its icon.

    This is not the screen-reader value. Both were the same string, and
    the screen-reader value is a comma-joined sentence built to be read
    aloud in full -- headline, every request phase, every count, every
    source-freshness caveat, quiet-presentation and cue notes. Rendered
    as a title it produced, verbatim from a live screenshot:

        "Agents active, Active: 34, Source partially availabl…"

    -- a sentence cut off mid-word in the menu bar. VoiceOver still gets
    the whole sentence; the eye gets one number and what it counts.
    """
    if type(state) is not CanonicalOperatorState or type(glance) is not ResolvedGlance:
        return ""
    primary_works = _present_primary_works(state)
    primary_keys = frozenset(work.key for work in primary_works)
    needs_you = sum(
        request.phase is RequestPhase.LIVE_UNACKNOWLEDGED
        for request in state.requests
        if request.key.work_key in primary_keys
    )
    if needs_you:
        return "1 needs you" if needs_you == 1 else f"{needs_you} need you"
    failed = sum(work.lifecycle is WorkLifecycle.FAILED for work in primary_works)
    if failed:
        return "1 failed" if failed == 1 else f"{failed} failed"
    now_epoch = projection_now_epoch(state)
    active = sum(
        work.lifecycle is WorkLifecycle.ACTIVE
        and not active_work_went_silent(work, now_epoch)
        for work in primary_works
    )
    if active:
        return f"{active} working"
    headline = _GLANCE_HEADLINES.get(glance.semantic)
    if headline is None or len(headline) > MAX_STATUS_ITEM_TITLE_LENGTH:
        return ""
    return headline


def mailbox_row_accessibility(
    row: MailboxRow,
    *,
    pinned: bool = False,
    watched: bool = False,
    snoozed_until: float | None = None,
    woke: bool = False,
    acknowledged_locally: bool = False,
    disabled_reason: str | None = None,
) -> AccessibilityText:
    """Describe one canonical mailbox row without depending on color."""
    if type(row) is not MailboxRow or type(row.work_key) is not WorkKey:
        return _row_fallback(disabled_reason=disabled_reason)
    values = _row_values(
        lifecycle=row.lifecycle,
        actionable=row.actionable,
        pinned=pinned,
        watched=watched,
        snoozed=(snoozed_until is not None),
        snoozed_until=snoozed_until,
        woke=woke,
        acknowledged=acknowledged_locally,
        source_freshness=row.source_freshness,
        timing_uncertain=row.timing_uncertain,
        worker_count=row.worker_count,
    )
    return AccessibilityText(
        _family_label(row.work_key, row.safe_label),
        _bounded_join(values),
        _row_help(disabled_reason),
    )


def browser_row_accessibility(
    row: AgentBrowserDocument,
    *,
    lifecycle: WorkLifecycle,
    snoozed_until: float | None = None,
    disabled_reason: str | None = None,
) -> AccessibilityText:
    """Describe one browser row with explicit canonical lifecycle authority."""
    if type(row) is not AgentBrowserDocument or type(row.work_key) is not WorkKey:
        return _row_fallback(disabled_reason=disabled_reason)
    values = _row_values(
        lifecycle=lifecycle if type(lifecycle) is WorkLifecycle else None,
        actionable=row.actionable,
        pinned=row.pinned,
        watched=row.watched,
        snoozed=row.snoozed,
        snoozed_until=snoozed_until,
        woke=row.woke,
        acknowledged=row.acknowledged,
        source_freshness=row.source_freshness,
        timing_uncertain=row.timing_uncertain,
        worker_count=row.worker_count,
    )
    return AccessibilityText(
        _family_label(row.work_key, row.safe_family_label),
        _bounded_join(values),
        _row_help(disabled_reason),
    )


def action_accessibility(descriptor: OperatorActionDescriptor) -> AccessibilityText:
    """Translate one shared action descriptor into full native control text."""
    if type(descriptor) is not OperatorActionDescriptor:
        return AccessibilityText("Agent action", "Unavailable", "Not available")
    title = descriptor.title
    if not _valid_public_text(title, maximum=MAX_ACCESSIBILITY_LABEL_LENGTH):
        return AccessibilityText("Agent action", "Unavailable", "Not available")
    if descriptor.enabled:
        value = "Available"
        if descriptor.key_equivalent:
            value = f"Available, keyboard shortcut {descriptor.key_equivalent.upper()}"
        return AccessibilityText(title, value, f"Activate {title}")
    return AccessibilityText(
        title,
        "Unavailable",
        _normalized_disabled_reason(descriptor.disabled_reason),
    )


def announcement_for_transition(
    event: CanonicalOperatorEvent,
    *,
    announced_event_keys: frozenset[SemanticEventKey] = frozenset(),
    quiet: bool = False,
    acknowledged_locally: bool = False,
) -> AccessibilityAnnouncement | None:
    """Return one fresh edge announcement, deduplicated by exact semantic key."""
    if not (
        type(event) is CanonicalOperatorEvent
        and type(announced_event_keys) is frozenset
        and len(announced_event_keys) <= MAX_ANNOUNCED_EVENT_KEYS
        and all(type(key) is SemanticEventKey for key in announced_event_keys)
        and type(quiet) is bool
        and type(acknowledged_locally) is bool
    ):
        return None
    if quiet or event.source_freshness is not SourceFreshness.FRESH or event.key in announced_event_keys:
        return None

    announcement: tuple[str, AnnouncementPriority] | None = None
    if (
        event.kind is TransitionKind.REQUEST_OPENED
        and event.interruption_class is InterruptionClass.ACTION_REQUIRED
        and not acknowledged_locally
    ):
        announcement = (
            "An agent needs your attention",
            AnnouncementPriority.ACTIONABLE,
        )
    elif event.kind is TransitionKind.FAILED:
        announcement = ("An agent failed", AnnouncementPriority.ERROR)
    elif event.kind is TransitionKind.REQUEST_RESOLVED:
        announcement = (
            "An agent request was resolved",
            AnnouncementPriority.OUTCOME,
        )
    elif event.kind is TransitionKind.COMPLETED:
        announcement = ("An agent completed", AnnouncementPriority.SUCCESS)
    if announcement is None:
        return None
    return AccessibilityAnnouncement(
        _announcement_key(event.key),
        announcement[0],
        announcement[1],
    )


def normalize_semantic_text_scale(value: object) -> float:
    """Normalize exact percentage choices to a system-font scale multiplier."""
    if type(value) is not int:
        return 1.0
    return _TEXT_SCALE_BY_PERCENT.get(value, 1.0)


def _status_fallback() -> AccessibilityText:
    return AccessibilityText(
        PRODUCT_DISPLAY_NAME,
        "Status unavailable",
        f"Open {PRODUCT_DISPLAY_NAME} status",
    )


def _provider_label(work_key: WorkKey) -> str:
    return _PRODUCT_PROVIDER_LABELS.get(work_key.source_key.provider_id, "Provider")


def _family_label(work_key: WorkKey, candidate: object) -> str:
    provider = _provider_label(work_key)
    expected = f"{provider} {work_key.work_id.value}"
    if (
        type(candidate) is str
        and candidate == expected
        and _valid_public_text(candidate, maximum=MAX_ACCESSIBILITY_LABEL_LENGTH)
    ):
        return candidate
    return f"{provider} agent family"


def _row_values(
    *,
    lifecycle: WorkLifecycle | None,
    actionable: object,
    pinned: object,
    watched: object,
    snoozed: object,
    snoozed_until: object,
    woke: object,
    acknowledged: object,
    source_freshness: object,
    timing_uncertain: object,
    worker_count: object,
) -> tuple[str, ...]:
    values = [_LIFECYCLE_LABELS.get(lifecycle, "Lifecycle unavailable")]
    if actionable is True:
        values.append("Needs you")
    if pinned is True:
        values.append("Pinned")
    if watched is True:
        values.append("Watching")
    if snoozed is True:
        deadline = _format_snooze_deadline(snoozed_until)
        values.append("Snoozed" if deadline is None else f"Snoozed until {deadline}")
    if woke is True:
        values.append("Woke")
    if acknowledged is True:
        values.append("Acknowledged locally")

    if timing_uncertain is True:
        values.append(_FRESHNESS_LABELS[SourceFreshness.TIMING_UNCERTAIN])
    else:
        values.append(_FRESHNESS_LABELS.get(source_freshness, "Source status unavailable"))

    if type(worker_count) is int and 0 <= worker_count <= 1_000:
        values.append("1 worker" if worker_count == 1 else f"{worker_count} workers")
    else:
        values.append("Worker count unavailable")
    return tuple(values)


def _format_snooze_deadline(value: object) -> str | None:
    if not (type(value) in {int, float} and math.isfinite(value) and 0.0 <= float(value) <= 253_402_300_799.0):
        return None
    try:
        deadline = datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return deadline.strftime("%Y-%m-%d %H:%M UTC")


def _normalized_disabled_reason(reason: object) -> str:
    return reason if type(reason) is str and reason in _DISABLED_REASONS else "Not available"


def _row_help(disabled_reason: object) -> str:
    if disabled_reason is None:
        return "Open actions for this agent family"
    return f"Open unavailable. {_normalized_disabled_reason(disabled_reason)}"


def _row_fallback(*, disabled_reason: object) -> AccessibilityText:
    return AccessibilityText(
        "Agent family",
        "Lifecycle unavailable, Source status unavailable, Worker count unavailable",
        _row_help(disabled_reason),
    )


def _bounded_join(values: tuple[str, ...] | list[str]) -> str:
    retained: list[str] = []
    for value in values:
        candidate = ", ".join((*retained, value))
        if len(candidate) > MAX_ACCESSIBILITY_VALUE_LENGTH:
            break
        retained.append(value)
    return ", ".join(retained) if retained else "Status unavailable"


def _announcement_key(event_key: SemanticEventKey) -> str:
    payload = semantic_event_key_to_payload(event_key)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    digest = hashlib.blake2s(encoded, digest_size=15).digest()
    token = base64.b32encode(digest).decode("ascii").rstrip("=").casefold()
    return f"announcement:{token}"
