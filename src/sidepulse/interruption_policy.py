"""Pure semantic interruption routing over canonical operator truth.

This module plans delivery identities and finite presentation candidates. It
does not mutate canonical truth, record ledger receipts, deliver notifications,
play sound, write hardware, inspect settings, or perform any I/O.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from .delivery_ledger import (
    MAX_DELIVERY_RECEIPTS,
    DeliveryChannel,
    DeliveryDisposition,
    DeliveryKey,
    DeliveryLedger,
    DeliverySummaryKey,
    delivery_disposition,
    pending_quiet_summary_keys,
    quiet_summary_key,
)
from .local_triage import LocalTriageState
from .operator_state import (
    CanonicalOperatorEvent,
    CanonicalRequestTruth,
    InterruptionClass,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
    classify_operator_event,
    semantic_event_key_to_payload,
)
from .presentation_policy import (
    FiniteCue,
    FiniteCueBudget,
    GlanceSemantic,
    valid_finite_cue,
)
from .provider_facts import NextActor, RequestKey, RequestKind, SourceFreshness

MAX_INTERRUPTION_EVENTS: Final = 2_000
MAX_INTERRUPTION_REQUESTS: Final = 1_000
MAX_ACTION_TOKEN_TTL_SECONDS: Final = 3_600.0
ACTION_TOKEN_RANDOM_BYTES_MIN: Final = 32
ACTION_TOKEN_RANDOM_BYTES_MAX: Final = 64

_ESCALATION_STAGE_SECONDS: Final = (0.0, 30.0, 120.0, 300.0)
_PURE_DISPOSITIONS: Final = frozenset(
    {
        DeliveryDisposition.PENDING,
        DeliveryDisposition.SUPPRESSED_QUIET,
        DeliveryDisposition.SUPPRESSED_POLICY,
    }
)
_SUPPRESSIBLE_CHANNELS: Final = frozenset(
    {
        DeliveryChannel.STATUS_ITEM_CUE,
        DeliveryChannel.SCREEN_BAR_CUE,
        DeliveryChannel.HARDWARE_CUE,
        DeliveryChannel.SYSTEM_NOTIFICATION,
        DeliveryChannel.SOUND,
    }
)
_VISUAL_CHANNELS: Final = frozenset(
    {
        DeliveryChannel.STATUS_ITEM_CUE,
        DeliveryChannel.SCREEN_BAR_CUE,
        DeliveryChannel.HARDWARE_CUE,
    }
)
_CHANNEL_ORDER: Final = tuple(DeliveryChannel)
_STATIC_CHANNELS: Final = (
    DeliveryChannel.MAILBOX_CUE,
    DeliveryChannel.HISTORY_FACT,
)
_VISUAL_STAGE_CHANNELS: Final = (
    DeliveryChannel.STATUS_ITEM_CUE,
    DeliveryChannel.SCREEN_BAR_CUE,
    DeliveryChannel.HARDWARE_CUE,
)
_ACTION_STAGE_CHANNELS: Final = {
    0: _CHANNEL_ORDER,
    1: _VISUAL_STAGE_CHANNELS,
    2: _VISUAL_STAGE_CHANNELS,
    3: (
        DeliveryChannel.STATUS_ITEM_CUE,
        DeliveryChannel.SCREEN_BAR_CUE,
        DeliveryChannel.HARDWARE_CUE,
        DeliveryChannel.SYSTEM_NOTIFICATION,
        DeliveryChannel.SOUND,
    ),
}
_CLASS_CHANNELS: Final = {
    InterruptionClass.IMPORTANT_OUTCOME: _CHANNEL_ORDER,
    InterruptionClass.COURTESY: tuple(channel for channel in _CHANNEL_ORDER if channel is not DeliveryChannel.SOUND),
    InterruptionClass.AMBIENT: _STATIC_CHANNELS,
}
_PROVIDER_LABELS: Final = {
    "codex": "Codex",
    "claude": "Claude",
    "devin": "Devin",
    "grok": "Grok",
    "cursor": "Cursor",
    "hermes": "Hermes",
    "openclaw": "OpenClaw",
    "opencode": "OpenCode",
    "kiro": "Kiro",
}
_ACTION_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_TERMINAL_SUMMARY_OUTCOMES: Final = frozenset(
    {
        DeliveryDisposition.DELIVERED,
        DeliveryDisposition.SUPPRESSED_QUIET,
        DeliveryDisposition.SUPPRESSED_POLICY,
        DeliveryDisposition.DISABLED,
        DeliveryDisposition.EXPIRED,
        DeliveryDisposition.SUPERSEDED,
    }
)
_HARD_CUE_BUDGET: Final = FiniteCueBudget()
_CUE_PRIORITY: Final = {
    GlanceSemantic.ATTENTION: 0,
    GlanceSemantic.FRESH_FAILURE: 1,
    GlanceSemantic.FRESH_COMPLETION: 2,
}


class InterruptionPolicyValidationError(ValueError):
    """Pure interruption input failed closed."""


class QuietReason(str, Enum):
    USER_QUIET = "user-quiet"
    FOCUS = "focus"


@dataclass(frozen=True, slots=True)
class QuietState:
    active: bool
    reasons: frozenset[QuietReason]
    began_at: float | None

    def __post_init__(self) -> None:
        valid_reasons = type(self.reasons) is frozenset and all(type(reason) is QuietReason for reason in self.reasons)
        began_valid = self.began_at is None or _valid_epoch(self.began_at)
        if not (
            type(self.active) is bool
            and valid_reasons
            and began_valid
            and (
                (self.active and bool(self.reasons) and self.began_at is not None)
                or (not self.active and not self.reasons and self.began_at is None)
            )
        ):
            raise InterruptionPolicyValidationError("invalid quiet state")
        if self.began_at is not None:
            object.__setattr__(self, "began_at", float(self.began_at))


@dataclass(frozen=True, slots=True)
class QuietSummary:
    action_required: int
    important_outcomes: int
    courtesy: int
    oldest_event_at: float | None

    def __post_init__(self) -> None:
        counts = (self.action_required, self.important_outcomes, self.courtesy)
        total = sum(counts) if all(type(count) is int for count in counts) else -1
        if not (
            all(type(count) is int and count >= 0 for count in counts)
            and total <= MAX_DELIVERY_RECEIPTS
            and ((total == 0 and self.oldest_event_at is None) or (total > 0 and _valid_epoch(self.oldest_event_at)))
        ):
            raise InterruptionPolicyValidationError("invalid quiet summary")
        if self.oldest_event_at is not None:
            object.__setattr__(self, "oldest_event_at", float(self.oldest_event_at))


@dataclass(frozen=True, slots=True)
class ChannelDeliveryPlan:
    channel: DeliveryChannel
    stage: int
    disposition: DeliveryDisposition

    def __post_init__(self) -> None:
        if not (
            type(self.channel) is DeliveryChannel
            and type(self.stage) is int
            and 0 <= self.stage <= 4
            and type(self.disposition) is DeliveryDisposition
            and self.disposition in _PURE_DISPOSITIONS
        ):
            raise InterruptionPolicyValidationError("invalid channel delivery plan")


@dataclass(frozen=True, slots=True)
class InterruptionRoute:
    event_key: SemanticEventKey
    interruption_class: InterruptionClass
    request_key: RequestKey | None
    deliveries: tuple[ChannelDeliveryPlan, ...]
    static_visibility_required: bool

    def __post_init__(self) -> None:
        if not (
            type(self.event_key) is SemanticEventKey
            and type(self.interruption_class) is InterruptionClass
            and (self.request_key is None or type(self.request_key) is RequestKey)
            and type(self.deliveries) is tuple
            and all(type(item) is ChannelDeliveryPlan for item in self.deliveries)
            and type(self.static_visibility_required) is bool
        ):
            raise InterruptionPolicyValidationError("invalid interruption route")
        if self.request_key is not None and self.event_key.subject_key != self.request_key:
            raise InterruptionPolicyValidationError("invalid interruption route")
        identities = tuple((item.channel, item.stage) for item in self.deliveries)
        if len(identities) != len(set(identities)):
            raise InterruptionPolicyValidationError("duplicate channel delivery plan")


@dataclass(frozen=True, slots=True)
class _QuietMember:
    event_key: SemanticEventKey
    interruption_class: InterruptionClass
    occurred_at_epoch: float

    def __post_init__(self) -> None:
        if not (
            type(self.event_key) is SemanticEventKey
            and type(self.interruption_class) is InterruptionClass
            and _valid_epoch(self.occurred_at_epoch)
        ):
            raise InterruptionPolicyValidationError("invalid quiet member")
        object.__setattr__(self, "occurred_at_epoch", float(self.occurred_at_epoch))


@dataclass(frozen=True, slots=True)
class _PendingQuietExit:
    summary_key: DeliverySummaryKey
    summary: QuietSummary
    member_event_keys: tuple[SemanticEventKey, ...]

    def __post_init__(self) -> None:
        if not (
            type(self.summary_key) is DeliverySummaryKey
            and type(self.summary) is QuietSummary
            and type(self.member_event_keys) is tuple
            and 1 <= len(self.member_event_keys) <= MAX_DELIVERY_RECEIPTS
            and all(type(key) is SemanticEventKey for key in self.member_event_keys)
            and len(set(self.member_event_keys)) == len(self.member_event_keys)
            and self.summary_key.member_count == len(self.member_event_keys)
        ):
            raise InterruptionPolicyValidationError("invalid pending quiet exit")


@dataclass(frozen=True, slots=True)
class InterruptionState:
    quiet_summary: QuietSummary
    last_quiet_exit_epoch: float | None
    _quiet_members: tuple[_QuietMember, ...] = field(default=(), repr=False)
    _quiet_reasons: frozenset[QuietReason] = field(
        default_factory=frozenset,
        repr=False,
    )
    _pending_quiet_exit: _PendingQuietExit | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not (
            type(self.quiet_summary) is QuietSummary
            and (self.last_quiet_exit_epoch is None or _valid_epoch(self.last_quiet_exit_epoch))
            and type(self._quiet_members) is tuple
            and len(self._quiet_members) <= MAX_DELIVERY_RECEIPTS
            and all(type(item) is _QuietMember for item in self._quiet_members)
            and type(self._quiet_reasons) is frozenset
            and all(type(reason) is QuietReason for reason in self._quiet_reasons)
            and (self._pending_quiet_exit is None or type(self._pending_quiet_exit) is _PendingQuietExit)
        ):
            raise InterruptionPolicyValidationError("invalid interruption state")
        keys = tuple(item.event_key for item in self._quiet_members)
        if len(keys) != len(set(keys)):
            raise InterruptionPolicyValidationError("duplicate quiet member")
        if self.last_quiet_exit_epoch is not None:
            object.__setattr__(
                self,
                "last_quiet_exit_epoch",
                float(self.last_quiet_exit_epoch),
            )


@dataclass(frozen=True, slots=True)
class QuietExitRoute:
    summary_key: DeliverySummaryKey
    summary: QuietSummary
    deliveries: tuple[ChannelDeliveryPlan, ...]
    supersede_keys: tuple[DeliveryKey, ...]

    def __post_init__(self) -> None:
        if not (
            type(self.summary_key) is DeliverySummaryKey
            and type(self.summary) is QuietSummary
            and type(self.deliveries) is tuple
            and all(type(item) is ChannelDeliveryPlan for item in self.deliveries)
            and all(item.channel is DeliveryChannel.SYSTEM_NOTIFICATION for item in self.deliveries)
            and type(self.supersede_keys) is tuple
            and len(self.supersede_keys) <= MAX_DELIVERY_RECEIPTS
            and all(type(key) is DeliveryKey for key in self.supersede_keys)
        ):
            raise InterruptionPolicyValidationError("invalid quiet exit route")
        if len(set(self.supersede_keys)) != len(self.supersede_keys):
            raise InterruptionPolicyValidationError("duplicate quiet supersession")


@dataclass(frozen=True, slots=True)
class InterruptionPlan:
    routes: tuple[InterruptionRoute, ...]
    next_escalation_epoch: float | None
    quiet_exit_summary: QuietSummary | None
    state: InterruptionState
    quiet_exit_route: QuietExitRoute | None = None

    def __post_init__(self) -> None:
        if not (
            type(self.routes) is tuple
            and len(self.routes) <= MAX_INTERRUPTION_EVENTS
            and all(type(route) is InterruptionRoute for route in self.routes)
            and (self.next_escalation_epoch is None or _valid_epoch(self.next_escalation_epoch))
            and (self.quiet_exit_summary is None or type(self.quiet_exit_summary) is QuietSummary)
            and type(self.state) is InterruptionState
            and (self.quiet_exit_route is None or type(self.quiet_exit_route) is QuietExitRoute)
        ):
            raise InterruptionPolicyValidationError("invalid interruption plan")
        keys = tuple(route.event_key for route in self.routes)
        if len(keys) != len(set(keys)):
            raise InterruptionPolicyValidationError("duplicate interruption route")
        if self.next_escalation_epoch is not None:
            object.__setattr__(
                self,
                "next_escalation_epoch",
                float(self.next_escalation_epoch),
            )


@dataclass(frozen=True, slots=True)
class GenericNotificationCopy:
    title: str
    body: str

    def __post_init__(self) -> None:
        if not (
            type(self.title) is str
            and type(self.body) is str
            and 1 <= len(self.title) <= 32
            and 1 <= len(self.body) <= 96
            and self.title.isprintable()
            and self.body.isprintable()
        ):
            raise InterruptionPolicyValidationError("invalid notification copy")


@dataclass(frozen=True, slots=True)
class ActionTokenBinding:
    token: str
    event_fingerprint: str
    operator_generation: int
    expires_at_epoch: float

    def __post_init__(self) -> None:
        if not (
            type(self.token) is str
            and _ACTION_TOKEN.fullmatch(self.token) is not None
            and type(self.event_fingerprint) is str
            and _FINGERPRINT.fullmatch(self.event_fingerprint) is not None
            and type(self.operator_generation) is int
            and self.operator_generation >= 0
            and _valid_epoch(self.expires_at_epoch)
        ):
            raise InterruptionPolicyValidationError("invalid action token binding")
        object.__setattr__(self, "expires_at_epoch", float(self.expires_at_epoch))


@dataclass(frozen=True, slots=True)
class FiniteCueBatch:
    cues: tuple[FiniteCue, ...]
    overflowed: bool
    overflow_count: int

    def __post_init__(self) -> None:
        if not (
            type(self.cues) is tuple
            and len(self.cues) <= _HARD_CUE_BUDGET.max_active + _HARD_CUE_BUDGET.max_pending
            and all(valid_finite_cue(cue) for cue in self.cues)
            and len({cue.event_key for cue in self.cues}) == len(self.cues)
            and type(self.overflowed) is bool
            and type(self.overflow_count) is int
            and self.overflow_count >= 0
            and self.overflowed == (self.overflow_count > 0)
        ):
            raise InterruptionPolicyValidationError("invalid finite cue batch")


def plan_interruptions(
    *,
    events: Iterable[CanonicalOperatorEvent],
    requests: Iterable[CanonicalRequestTruth],
    local_triage: LocalTriageState,
    quiet: QuietState,
    ledger: DeliveryLedger,
    previous: InterruptionState,
    now: float,
) -> InterruptionPlan:
    """Plan exact channel-stage work without recording or delivering it."""
    event_tuple = _validated_events(events)
    request_tuple = _validated_requests(requests)
    if not (
        type(local_triage) is LocalTriageState
        and type(quiet) is QuietState
        and type(ledger) is DeliveryLedger
        and type(previous) is InterruptionState
    ):
        raise InterruptionPolicyValidationError("invalid interruption input")
    if not _valid_epoch(now):
        raise InterruptionPolicyValidationError("invalid interruption time")
    now_epoch = float(now)
    if quiet.began_at is not None and quiet.began_at > now_epoch:
        raise InterruptionPolicyValidationError("invalid quiet time")

    request_by_key = {request.key: request for request in request_tuple}
    event_by_key = {event.key: event for event in event_tuple}
    acknowledged = {item.request_key for item in local_triage.acknowledgements}
    known_subjects = {
        receipt.key.subject_key for receipt in ledger.receipts if type(receipt.key.subject_key) is SemanticEventKey
    }

    routes: dict[SemanticEventKey, InterruptionRoute] = {}
    deadlines: list[float] = []
    for event in event_tuple:
        if event.interruption_class is InterruptionClass.ACTION_REQUIRED:
            request = request_by_key.get(event.subject_key) if type(event.subject_key) is RequestKey else None
            route, deadline = _plan_action_route(
                event_key=event.key,
                interruption_class=event.interruption_class,
                event_freshness=event.source_freshness,
                request=request,
                acknowledged=acknowledged,
                quiet=quiet,
                ledger=ledger,
                now=now_epoch,
            )
        else:
            route = _plan_event_route(event, quiet=quiet, ledger=ledger)
            deadline = None
        routes[event.key] = route
        if deadline is not None:
            deadlines.append(deadline)

    for request in request_tuple:
        if len(routes) >= MAX_INTERRUPTION_EVENTS:
            break
        event_key = request.semantic_event_key
        if event_key in event_by_key or event_key not in known_subjects:
            continue
        if event_key.transition_kind is not TransitionKind.REQUEST_OPENED:
            continue
        route, deadline = _plan_action_route(
            event_key=event_key,
            interruption_class=InterruptionClass.ACTION_REQUIRED,
            event_freshness=request.source_freshness,
            request=request,
            acknowledged=acknowledged,
            quiet=quiet,
            ledger=ledger,
            now=now_epoch,
        )
        routes[event_key] = route
        if deadline is not None:
            deadlines.append(deadline)

    ordered_routes = tuple(routes[key] for key in sorted(routes))
    members = {item.event_key: item for item in previous._quiet_members}
    if _user_quiet_without_focus(quiet):
        for route in ordered_routes:
            if any(delivery.disposition is DeliveryDisposition.SUPPRESSED_QUIET for delivery in route.deliveries):
                members.setdefault(
                    route.event_key,
                    _QuietMember(
                        route.event_key,
                        route.interruption_class,
                        route.event_key.provider_watermark.occurred_at_epoch,
                    ),
                )
        members = _bounded_quiet_members(members)

    prior_user_quiet = QuietReason.USER_QUIET in previous._quiet_reasons or _summary_count(previous.quiet_summary) > 0
    current_user_quiet = QuietReason.USER_QUIET in quiet.reasons
    pending_exit = previous._pending_quiet_exit
    last_exit = previous.last_quiet_exit_epoch
    live_summary = _summary_from_members(tuple(members.values()))

    if prior_user_quiet and not current_user_quiet:
        if pending_exit is None:
            suppressed_keys = pending_quiet_summary_keys(ledger)[:MAX_DELIVERY_RECEIPTS]
            pending_exit = _pending_exit_from_keys(
                suppressed_keys,
                members=members,
            )
        last_exit = now_epoch
        members = {}
        live_summary = _empty_summary()

    quiet_exit_route, pending_exit = _plan_quiet_exit(
        pending_exit,
        quiet=quiet,
        ledger=ledger,
    )
    state = InterruptionState(
        quiet_summary=live_summary,
        last_quiet_exit_epoch=last_exit,
        _quiet_members=tuple(members[key] for key in sorted(members)),
        _quiet_reasons=quiet.reasons,
        _pending_quiet_exit=pending_exit,
    )
    return InterruptionPlan(
        routes=ordered_routes,
        next_escalation_epoch=min(deadlines) if deadlines else None,
        quiet_exit_summary=(quiet_exit_route.summary if quiet_exit_route is not None else None),
        state=state,
        quiet_exit_route=quiet_exit_route,
    )


def generic_notification_copy(
    route: InterruptionRoute | QuietExitRoute,
) -> GenericNotificationCopy:
    """Return bounded product-owned copy with no source display content."""
    if type(route) is QuietExitRoute:
        count = route.summary_key.member_count
        suffix = "update" if count == 1 else "updates"
        return GenericNotificationCopy("SidePulse", f"SidePulse has {count} {suffix}")
    if type(route) is not InterruptionRoute:
        raise InterruptionPolicyValidationError("invalid notification route")
    subject = route.event_key.subject_key
    source = subject.source_key if type(subject) is not RequestKey else subject.work_key.source_key
    provider = _PROVIDER_LABELS.get(source.provider_id, "Provider")
    article = "An" if provider[:1].upper() in "AEIOU" else "A"
    if route.interruption_class is InterruptionClass.ACTION_REQUIRED:
        body = f"{article} {provider} session needs you"
    elif route.interruption_class in {
        InterruptionClass.IMPORTANT_OUTCOME,
        InterruptionClass.COURTESY,
    }:
        body = f"{article} {provider} session finished"
    else:
        body = "SidePulse has 1 update"
    return GenericNotificationCopy("SidePulse", body)


def issue_action_token(
    *,
    randomness: bytes,
    event_key: SemanticEventKey,
    operator_generation: int,
    now: float,
    ttl_seconds: float = 300.0,
) -> ActionTokenBinding:
    """Bind caller-supplied randomness to one in-memory event fingerprint."""
    if not (
        type(randomness) is bytes
        and ACTION_TOKEN_RANDOM_BYTES_MIN <= len(randomness) <= ACTION_TOKEN_RANDOM_BYTES_MAX
        and type(event_key) is SemanticEventKey
        and type(operator_generation) is int
        and operator_generation >= 0
        and _valid_epoch(now)
        and _valid_positive_duration(ttl_seconds)
        and float(ttl_seconds) <= MAX_ACTION_TOKEN_TTL_SECONDS
    ):
        raise InterruptionPolicyValidationError("invalid action token input")
    fingerprint = _event_fingerprint(event_key)
    expires_at = float(now) + float(ttl_seconds)
    binding_payload = json.dumps(
        {
            "event_fingerprint": fingerprint,
            "expires_at_epoch": expires_at,
            "operator_generation": operator_generation,
        },
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    token = (
        base64.urlsafe_b64encode(hmac.new(randomness, binding_payload, hashlib.sha256).digest())
        .decode("ascii")
        .rstrip("=")
    )
    return ActionTokenBinding(
        token=token,
        event_fingerprint=fingerprint,
        operator_generation=operator_generation,
        expires_at_epoch=expires_at,
    )


def resolve_action_token(
    binding: ActionTokenBinding,
    *,
    presented_token: str,
    candidate_event_keys: Iterable[SemanticEventKey],
    current_generation: int,
    now: float,
) -> SemanticEventKey | None:
    """Fail closed unless one current exact event matches the opaque binding."""
    if not (
        type(binding) is ActionTokenBinding
        and type(presented_token) is str
        and type(current_generation) is int
        and current_generation >= 0
        and _valid_epoch(now)
        and current_generation == binding.operator_generation
        and float(now) < binding.expires_at_epoch
        and hmac.compare_digest(presented_token, binding.token)
    ):
        return None
    try:
        candidates = tuple(candidate_event_keys)
    except TypeError:
        return None
    if not (
        len(candidates) <= MAX_INTERRUPTION_EVENTS
        and all(type(key) is SemanticEventKey for key in candidates)
        and len(candidates) == len(set(candidates))
    ):
        return None
    matches = tuple(
        key
        for key in candidates
        if hmac.compare_digest(
            _event_fingerprint(key),
            binding.event_fingerprint,
        )
    )
    return matches[0] if len(matches) == 1 else None


def action_token_metadata(binding: ActionTokenBinding) -> dict[str, str]:
    """Return the exact content-free Notification Center user-info payload."""
    if type(binding) is not ActionTokenBinding:
        raise InterruptionPolicyValidationError("invalid action token binding")
    return {"action_token": binding.token}


def plan_finite_cues(plan: InterruptionPlan) -> FiniteCueBatch:
    """Admit at most one active and one pending presentation-owned cue."""
    if type(plan) is not InterruptionPlan:
        raise InterruptionPolicyValidationError("invalid finite cue plan")
    candidates: list[FiniteCue] = []
    for route in plan.routes:
        if not any(
            delivery.channel in _VISUAL_CHANNELS and delivery.disposition is DeliveryDisposition.PENDING
            for delivery in route.deliveries
        ):
            continue
        cue = _finite_cue_for_route(route)
        if cue is not None:
            candidates.append(cue)
    candidates.sort(
        key=lambda cue: (
            _CUE_PRIORITY[cue.semantic],
            cue.event_key,
        )
    )
    admitted_count = _HARD_CUE_BUDGET.max_active + _HARD_CUE_BUDGET.max_pending
    admitted = tuple(candidates[:admitted_count])
    overflow_count = max(0, len(candidates) - len(admitted))
    return FiniteCueBatch(admitted, overflow_count > 0, overflow_count)


def _plan_event_route(
    event: CanonicalOperatorEvent,
    *,
    quiet: QuietState,
    ledger: DeliveryLedger,
) -> InterruptionRoute:
    channels = _CLASS_CHANNELS[event.interruption_class]
    deliveries = (
        _plan_channels(event.key, channels, stage=0, quiet=quiet, ledger=ledger)
        if event.source_freshness is SourceFreshness.FRESH
        else ()
    )
    return InterruptionRoute(
        event.key,
        event.interruption_class,
        None,
        deliveries,
        False,
    )


def _plan_action_route(
    *,
    event_key: SemanticEventKey,
    interruption_class: InterruptionClass,
    event_freshness: SourceFreshness,
    request: CanonicalRequestTruth | None,
    acknowledged: set[RequestKey],
    quiet: QuietState,
    ledger: DeliveryLedger,
    now: float,
) -> tuple[InterruptionRoute, float | None]:
    if not _current_explicit_request(event_key, request):
        return (
            InterruptionRoute(event_key, interruption_class, None, (), False),
            None,
        )
    assert request is not None
    if request.phase in {RequestPhase.RESOLVED, RequestPhase.UNKNOWN_EXPIRED}:
        return (
            InterruptionRoute(event_key, interruption_class, request.key, (), False),
            None,
        )
    if request.phase is RequestPhase.STALE_HOLD or (
        event_freshness is not SourceFreshness.FRESH or request.source_freshness is not SourceFreshness.FRESH
    ):
        return (
            InterruptionRoute(event_key, interruption_class, request.key, (), True),
            None,
        )
    locally_acknowledged = request.key in acknowledged or request.phase is RequestPhase.LIVE_ACKNOWLEDGED
    if locally_acknowledged:
        deliveries = _plan_channels(
            event_key,
            _STATIC_CHANNELS,
            stage=0,
            quiet=quiet,
            ledger=ledger,
        )
        return (
            InterruptionRoute(
                event_key,
                interruption_class,
                request.key,
                deliveries,
                True,
            ),
            None,
        )

    stage = _escalation_stage(request.eligible_elapsed_seconds)
    deliveries = _plan_channels(
        event_key,
        _ACTION_STAGE_CHANNELS[stage],
        stage=stage,
        quiet=quiet,
        ledger=ledger,
    )
    if stage > 0:
        deliveries = (
            *_plan_channels(
                event_key,
                _STATIC_CHANNELS,
                stage=0,
                quiet=quiet,
                ledger=ledger,
            ),
            *deliveries,
        )
    next_deadline = _next_escalation_epoch(
        request.eligible_elapsed_seconds,
        now=now,
    )
    return (
        InterruptionRoute(
            event_key,
            interruption_class,
            request.key,
            deliveries,
            True,
        ),
        next_deadline,
    )


def _current_explicit_request(
    event_key: SemanticEventKey,
    request: CanonicalRequestTruth | None,
) -> bool:
    return (
        type(request) is CanonicalRequestTruth
        and request.semantic_event_key == event_key
        and request.request_kind is not RequestKind.UNKNOWN
        and request.next_actor is NextActor.USER
    )


def _plan_channels(
    event_key: SemanticEventKey,
    channels: tuple[DeliveryChannel, ...],
    *,
    stage: int,
    quiet: QuietState,
    ledger: DeliveryLedger,
) -> tuple[ChannelDeliveryPlan, ...]:
    plans: list[ChannelDeliveryPlan] = []
    for channel in channels:
        key = DeliveryKey(event_key, channel, stage)
        if delivery_disposition(ledger, key) is not None:
            continue
        disposition = _planned_disposition(channel, quiet=quiet)
        plans.append(ChannelDeliveryPlan(channel, stage, disposition))
    return tuple(plans)


def _planned_disposition(
    channel: DeliveryChannel,
    *,
    quiet: QuietState,
) -> DeliveryDisposition:
    if channel not in _SUPPRESSIBLE_CHANNELS or not quiet.active:
        return DeliveryDisposition.PENDING
    if QuietReason.FOCUS in quiet.reasons:
        return DeliveryDisposition.SUPPRESSED_POLICY
    return DeliveryDisposition.SUPPRESSED_QUIET


def _escalation_stage(elapsed: float) -> int:
    if elapsed >= _ESCALATION_STAGE_SECONDS[3]:
        return 3
    if elapsed >= _ESCALATION_STAGE_SECONDS[2]:
        return 2
    if elapsed >= _ESCALATION_STAGE_SECONDS[1]:
        return 1
    return 0


def _next_escalation_epoch(elapsed: float, *, now: float) -> float | None:
    for boundary in _ESCALATION_STAGE_SECONDS[1:]:
        if elapsed < boundary:
            return now + boundary - elapsed
    return None


def _bounded_quiet_members(
    members: dict[SemanticEventKey, _QuietMember],
) -> dict[SemanticEventKey, _QuietMember]:
    ordered = sorted(
        members.values(),
        key=lambda item: (item.occurred_at_epoch, item.event_key),
    )[:MAX_DELIVERY_RECEIPTS]
    return {item.event_key: item for item in ordered}


def _summary_from_members(members: tuple[_QuietMember, ...]) -> QuietSummary:
    if not members:
        return _empty_summary()
    return QuietSummary(
        action_required=sum(item.interruption_class is InterruptionClass.ACTION_REQUIRED for item in members),
        important_outcomes=sum(item.interruption_class is InterruptionClass.IMPORTANT_OUTCOME for item in members),
        courtesy=sum(item.interruption_class is InterruptionClass.COURTESY for item in members),
        oldest_event_at=min(item.occurred_at_epoch for item in members),
    )


def _pending_exit_from_keys(
    event_keys: tuple[SemanticEventKey, ...],
    *,
    members: dict[SemanticEventKey, _QuietMember],
) -> _PendingQuietExit | None:
    selected: list[_QuietMember] = []
    for key in event_keys:
        member = members.get(key)
        if member is None:
            member = _QuietMember(
                key,
                classify_operator_event(key.transition_kind),
                key.provider_watermark.occurred_at_epoch,
            )
        if member.interruption_class is InterruptionClass.AMBIENT:
            continue
        selected.append(member)
    selected_keys = tuple(item.event_key for item in selected)
    summary_key = quiet_summary_key(selected_keys)
    if summary_key is None:
        return None
    return _PendingQuietExit(
        summary_key,
        _summary_from_members(tuple(selected)),
        selected_keys,
    )


def _plan_quiet_exit(
    pending: _PendingQuietExit | None,
    *,
    quiet: QuietState,
    ledger: DeliveryLedger,
) -> tuple[QuietExitRoute | None, _PendingQuietExit | None]:
    if pending is None:
        return None, None
    summary_delivery_key = DeliveryKey(
        pending.summary_key,
        DeliveryChannel.SYSTEM_NOTIFICATION,
        0,
    )
    disposition = delivery_disposition(ledger, summary_delivery_key)
    deliveries: tuple[ChannelDeliveryPlan, ...] = ()
    supersede_keys: tuple[DeliveryKey, ...] = ()
    if disposition is None:
        deliveries = (
            ChannelDeliveryPlan(
                DeliveryChannel.SYSTEM_NOTIFICATION,
                0,
                _planned_disposition(
                    DeliveryChannel.SYSTEM_NOTIFICATION,
                    quiet=quiet,
                ),
            ),
        )
    elif disposition in _TERMINAL_SUMMARY_OUTCOMES:
        member_keys = set(pending.member_event_keys)
        supersede_keys = tuple(
            receipt.key
            for receipt in ledger.receipts
            if receipt.disposition is DeliveryDisposition.SUPPRESSED_QUIET
            and type(receipt.key.subject_key) is SemanticEventKey
            and receipt.key.subject_key in member_keys
        )
        if not supersede_keys:
            return None, None
    return (
        QuietExitRoute(
            pending.summary_key,
            pending.summary,
            deliveries,
            supersede_keys,
        ),
        pending,
    )


def _finite_cue_for_route(route: InterruptionRoute) -> FiniteCue | None:
    if route.interruption_class is InterruptionClass.ACTION_REQUIRED:
        semantic = GlanceSemantic.ATTENTION
        duration = 0.24
    elif route.interruption_class is InterruptionClass.IMPORTANT_OUTCOME:
        semantic = GlanceSemantic.FRESH_FAILURE
        duration = 0.45
    elif route.interruption_class is InterruptionClass.COURTESY:
        semantic = GlanceSemantic.FRESH_COMPLETION
        duration = 0.4
    else:
        return None
    cue = FiniteCue(
        event_key=f"event-{_event_fingerprint(route.event_key)}",
        semantic=semantic,
        repetitions=_HARD_CUE_BUDGET.max_repetitions,
        duration_seconds=duration,
    )
    return cue if valid_finite_cue(cue) else None


def _event_fingerprint(key: SemanticEventKey) -> str:
    encoded = json.dumps(
        semantic_event_key_to_payload(key),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_events(
    events: Iterable[CanonicalOperatorEvent],
) -> tuple[CanonicalOperatorEvent, ...]:
    try:
        values = tuple(events)
    except TypeError as error:
        raise InterruptionPolicyValidationError("invalid interruption events") from error
    if not (len(values) <= MAX_INTERRUPTION_EVENTS and all(type(event) is CanonicalOperatorEvent for event in values)):
        raise InterruptionPolicyValidationError("invalid interruption events")
    if len({event.key for event in values}) != len(values):
        raise InterruptionPolicyValidationError("duplicate event key")
    return tuple(sorted(values, key=lambda event: event.key))


def _validated_requests(
    requests: Iterable[CanonicalRequestTruth],
) -> tuple[CanonicalRequestTruth, ...]:
    try:
        values = tuple(requests)
    except TypeError as error:
        raise InterruptionPolicyValidationError("invalid interruption requests") from error
    if not (
        len(values) <= MAX_INTERRUPTION_REQUESTS and all(type(request) is CanonicalRequestTruth for request in values)
    ):
        raise InterruptionPolicyValidationError("invalid interruption requests")
    if len({request.key for request in values}) != len(values):
        raise InterruptionPolicyValidationError("duplicate request key")
    return tuple(sorted(values, key=lambda request: request.key))


def _user_quiet_without_focus(quiet: QuietState) -> bool:
    return QuietReason.USER_QUIET in quiet.reasons and QuietReason.FOCUS not in quiet.reasons


def _summary_count(summary: QuietSummary) -> int:
    return summary.action_required + summary.important_outcomes + summary.courtesy


def _empty_summary() -> QuietSummary:
    return QuietSummary(0, 0, 0, None)


def _valid_epoch(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and float(value) >= 0.0


def _valid_positive_duration(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and float(value) > 0.0
