"""Pure canonical operator truth reduction for provider facts.

This module owns no clocks, I/O, provider adapters, controller state, or user
interface. Callers inject a validated clock sample and an already validated
provider fact batch. The reducer returns immutable, deterministic, bounded
operator truth and semantic edges.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import total_ordering
from typing import Final

from .capacity_types import SourceKey
from .provider_contracts import DiagnosticIdentifier
from .provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderFactBatch,
    ProviderRequestFact,
    ProviderRequestState,
    ProviderTerminalCause,
    ProviderWatermark,
    ProviderWorkFact,
    RequestKey,
    RequestKind,
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
    WatermarkOrder,
    WorkKey,
    WorkLifecycle,
    compare_watermarks,
    request_key_from_payload,
    request_key_to_payload,
    work_key_from_payload,
    work_key_to_payload,
)

MAX_CLOCK_DELTA_DIVERGENCE_SECONDS: Final = 5.0
TIMING_UNCERTAINTY_LEASE_SECONDS: Final = 3_600.0
# The presence horizon: something heard NOTHING for this long is history,
# not presence. It gates what the snapshot surfaces (menu rows, counts,
# LEDs, Screen Bar), which sources ELECT the globally-reported
# clock-continuity status, and the menu-bar title's counts. The catalog
# itself keeps entries until CANONICAL_WORK_RETENTION_SECONDS.
PRESENCE_HORIZON_SECONDS: Final = 3_600.0
# ACTIVE means HEARD FROM. Hooks die with the agent's process, so a
# killed session's work stays lifecycle-ACTIVE forever -- and every
# surface that read raw lifecycle said "1 working" long after the owner
# watched it finish. Four minutes of total hook+transcript silence is
# the line (transcript heartbeats keep long tool runs alive for
# claude/codex); a WAITING ask is exempt -- waiting on the OWNER is
# silence by design and must never expire into invisibility.
ACTIVE_SILENCE_SECONDS: Final = 240.0

# Providers whose hooks are SPARSE by design get a longer line. Hermes
# fires one pre_llm_call per turn and has no transcript heartbeats, so
# a flat four-minute window flipped it to "silent" mid-thought on any
# long turn -- the exact false-Completed upstream PR #20 fixed with
# per-provider expiry. Keyed by provider_id; absent means the default.
PROVIDER_ACTIVE_SILENCE_SECONDS: Final = {
    "hermes": 900.0,
}


def active_silence_seconds_for(provider_id: str | None) -> float:
    return PROVIDER_ACTIVE_SILENCE_SECONDS.get(
        provider_id or "", ACTIVE_SILENCE_SECONDS
    )


def active_work_went_silent(work, now_epoch: float | None) -> bool:
    """True when an ACTIVE work has been unheard past its provider's line."""
    if work.lifecycle is not WorkLifecycle.ACTIVE or now_epoch is None:
        return False
    provider_id = getattr(
        getattr(work.key, "source_key", None), "provider_id", None
    )
    return (
        now_epoch - work.watermark.occurred_at_epoch
        > active_silence_seconds_for(provider_id)
    )


# COMPLETED is a moment, not a state: "recently" has a clock in it.
# Without this line a finished session showed the done green (and the
# COMPLETED aggregate) until the presence horizon dropped the row -- up
# to an HOUR of "it's done!" for something the owner saw finish.
COMPLETED_RECENT_SECONDS: Final = 120.0

# The LIGHTS' own, shorter window: in an interactive session every
# assistant turn ends in a Stop, so a 120s whole-strip done-green after
# each turn read as "the strip is just green all the time." The strip
# gets a brief completion sweep; the menu rows, the title's unseen-done
# check, and the right-tip gauge keep carrying "something finished
# since you looked" for as long as that stays true.
COMPLETED_GLOW_SECONDS: Final = 20.0


def completed_work_no_longer_recent(work, now_epoch: float | None) -> bool:
    """True when a COMPLETED work finished past the recent window."""
    return (
        work.lifecycle is WorkLifecycle.COMPLETED
        and now_epoch is not None
        and now_epoch - work.watermark.occurred_at_epoch > COMPLETED_RECENT_SECONDS
    )
TIMING_RECOVERY_CONFIRMATIONS: Final = 2
MAX_CANONICAL_WORKS: Final = 1_000
# A work whose newest event is older than a day is history, not state:
# without an age bound the catalog accumulated every session ever seen,
# and a days-old session with no Stop sat in the Agent Browser labeled
# "active" forever. Skipped while the reduction is clock-quarantined
# (discontinuity or future-dated facts) so a distrusted wall clock can
# never mass-expire live work.
CANONICAL_WORK_RETENTION_SECONDS: Final = 24 * 3_600.0
MAX_CANONICAL_REQUESTS: Final = 1_000
MAX_EVENTS_PER_REDUCTION: Final = 2_000
MAX_REDUCER_DIAGNOSTICS: Final = 16
MAX_REDUCER_DIAGNOSTIC_COUNT: Final = 2_000
MAX_CANONICAL_SOURCES: Final = 1_000

_STATE_SCHEMA_VERSION: Final = 1
_EVENT_SCHEMA_MAJOR: Final = 1
_EVENT_SCHEMA_MINOR: Final = 0
_OPAQUE_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]*\Z")
_EVENT_FIELDS: Final = frozenset(
    {
        "version",
        "subject_kind",
        "subject_key",
        "transition_kind",
        "provider_watermark",
    }
)
_VERSION_FIELDS: Final = frozenset({"major", "minor"})
_WATERMARK_FIELDS: Final = frozenset(
    {
        "basis",
        "occurred_at_epoch",
        "event_token",
        "sequence",
        "tie_break_rank",
    }
)
_SOURCE_LOSS_HEALTH: Final = frozenset(
    {
        SourceHealth.UNAVAILABLE,
        SourceHealth.AUTH_REQUIRED,
        SourceHealth.ACCESS_DENIED,
        SourceHealth.RATE_LIMITED,
        SourceHealth.TIMED_OUT,
        SourceHealth.UNSUPPORTED,
    }
)


class OperatorStateValidationError(ValueError):
    """Canonical operator input failed closed at the pure boundary."""


def _is_finite_nonnegative_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0.0


def _source_sort_key(source: SourceKey) -> tuple[str, str, str, str]:
    return (
        source.provider_id,
        source.adapter_id,
        source.source_instance_id,
        source.capability_id,
    )


def _work_sort_key(key: WorkKey) -> tuple[tuple[str, str, str, str], str]:
    return (_source_sort_key(key.source_key), key.work_id.value)


def _request_sort_key(
    key: RequestKey,
) -> tuple[tuple[tuple[str, str, str, str], str], str]:
    return (_work_sort_key(key.work_key), key.request_id.value)


def _watermark_sort_key(
    watermark: ProviderWatermark,
) -> tuple[tuple[str, str, str, str], str, float, int, int, str]:
    return (
        _source_sort_key(watermark.source_key),
        watermark.basis.value,
        watermark.occurred_at_epoch,
        -1 if watermark.sequence is None else watermark.sequence,
        watermark.tie_break_rank,
        watermark.event_token.value,
    )


def _subject_sort_key(
    subject: WorkKey | RequestKey,
) -> tuple[object, ...]:
    if type(subject) is WorkKey:
        return (0, *_work_sort_key(subject))
    return (1, *_request_sort_key(subject))


class RequestPhase(str, Enum):
    LIVE_UNACKNOWLEDGED = "live_unacknowledged"
    LIVE_ACKNOWLEDGED = "live_acknowledged"
    STALE_HOLD = "stale_hold"
    RESOLVED = "resolved"
    UNKNOWN_EXPIRED = "unknown_expired"


class AcknowledgementEligibility(str, Enum):
    ELIGIBLE = "eligible"
    ALREADY_ACKNOWLEDGED = "already_acknowledged"
    STALE_HOLD = "stale_hold"
    RESOLVED = "resolved"
    NOT_ACTIONABLE = "not_actionable"


class InterruptionClass(str, Enum):
    ACTION_REQUIRED = "action_required"
    IMPORTANT_OUTCOME = "important_outcome"
    COURTESY = "courtesy"
    AMBIENT = "ambient"


class TransitionKind(str, Enum):
    BECAME_ACTIVE = "became_active"
    BECAME_IDLE = "became_idle"
    REQUEST_OPENED = "request_opened"
    REQUEST_RESOLVED = "request_resolved"
    COMPLETED = "completed"
    FAILED = "failed"
    SOURCE_DEGRADED = "source_degraded"
    SOURCE_RECOVERED = "source_recovered"


class InvalidationDomain(str, Enum):
    LIFECYCLE = "lifecycle"
    MAILBOX = "mailbox"
    COMPLETION = "completion"
    DELIVERY = "delivery"
    CAPACITY = "capacity"
    SOURCE_HEALTH = "source_health"


@total_ordering
@dataclass(frozen=True, slots=True)
class SemanticEventKey:
    subject_key: WorkKey | RequestKey
    transition_kind: TransitionKind
    provider_watermark: ProviderWatermark

    def __post_init__(self) -> None:
        if type(self.subject_key) not in {WorkKey, RequestKey}:
            raise OperatorStateValidationError("invalid semantic event key")
        source = (
            self.subject_key.source_key
            if type(self.subject_key) is WorkKey
            else self.subject_key.work_key.source_key
        )
        if not (
            type(self.transition_kind) is TransitionKind
            and type(self.provider_watermark) is ProviderWatermark
            and self.provider_watermark.source_key == source
        ):
            raise OperatorStateValidationError("invalid semantic event key")

    def _ordering_key(self) -> tuple[object, ...]:
        return (
            *_subject_sort_key(self.subject_key),
            self.transition_kind.value,
            *_watermark_sort_key(self.provider_watermark),
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticEventKey):
            return NotImplemented
        return self._ordering_key() < other._ordering_key()


@dataclass(frozen=True, slots=True)
class CanonicalWorkTruth:
    key: WorkKey
    lifecycle: WorkLifecycle
    watermark: ProviderWatermark
    observation_authority: ObservationAuthority
    source_health: SourceHealth
    source_freshness: SourceFreshness
    next_actor: NextActor
    safe_label: str
    parent_key: WorkKey | None
    request_keys: tuple[RequestKey, ...]
    timing_uncertain: bool

    def __post_init__(self) -> None:
        if not (
            type(self.key) is WorkKey
            and type(self.lifecycle) is WorkLifecycle
            and type(self.watermark) is ProviderWatermark
            and self.watermark.source_key == self.key.source_key
            and type(self.observation_authority) is ObservationAuthority
            and type(self.source_health) is SourceHealth
            and type(self.source_freshness) is SourceFreshness
            and type(self.next_actor) is NextActor
            and type(self.safe_label) is str
            and 1 <= len(self.safe_label) <= 128
            and self.safe_label.isprintable()
            and type(self.timing_uncertain) is bool
            and type(self.request_keys) is tuple
            and all(type(key) is RequestKey and key.work_key == self.key for key in self.request_keys)
        ):
            raise OperatorStateValidationError("invalid canonical work truth")
        if self.parent_key is not None and (
            type(self.parent_key) is not WorkKey
            or self.parent_key.source_key != self.key.source_key
            or self.parent_key == self.key
        ):
            raise OperatorStateValidationError("invalid canonical work truth")
        request_keys = tuple(sorted(self.request_keys, key=_request_sort_key))
        if len(request_keys) != len(set(request_keys)):
            raise OperatorStateValidationError("invalid canonical work truth")
        object.__setattr__(self, "request_keys", request_keys)


@dataclass(frozen=True, slots=True)
class CanonicalRequestTruth:
    key: RequestKey
    phase: RequestPhase
    request_kind: RequestKind
    next_actor: NextActor
    watermark: ProviderWatermark
    source_freshness: SourceFreshness
    acknowledgement_eligibility: AcknowledgementEligibility
    semantic_event_key: SemanticEventKey
    opened_at_epoch: float | None
    eligible_elapsed_seconds: float
    _observation_authority: ObservationAuthority = field(
        default=ObservationAuthority.RESTORED_LAST_KNOWN,
        repr=False,
    )

    def __post_init__(self) -> None:
        opened_valid = self.opened_at_epoch is None or _is_finite_nonnegative_number(
            self.opened_at_epoch
        )
        if not (
            type(self.key) is RequestKey
            and type(self.phase) is RequestPhase
            and type(self.request_kind) is RequestKind
            and type(self.next_actor) is NextActor
            and type(self.watermark) is ProviderWatermark
            and self.watermark.source_key == self.key.work_key.source_key
            and type(self.source_freshness) is SourceFreshness
            and type(self.acknowledgement_eligibility) is AcknowledgementEligibility
            and type(self.semantic_event_key) is SemanticEventKey
            and self.semantic_event_key.subject_key == self.key
            and opened_valid
            and _is_finite_nonnegative_number(self.eligible_elapsed_seconds)
            and type(self._observation_authority) is ObservationAuthority
        ):
            raise OperatorStateValidationError("invalid canonical request truth")
        if self.opened_at_epoch is not None:
            object.__setattr__(self, "opened_at_epoch", float(self.opened_at_epoch))
        object.__setattr__(
            self,
            "eligible_elapsed_seconds",
            float(self.eligible_elapsed_seconds),
        )


@dataclass(frozen=True, slots=True)
class CanonicalOperatorEvent:
    key: SemanticEventKey
    subject_key: WorkKey | RequestKey
    kind: TransitionKind
    interruption_class: InterruptionClass
    occurred_at_epoch: float
    source_freshness: SourceFreshness

    def __post_init__(self) -> None:
        if not (
            type(self.key) is SemanticEventKey
            and type(self.subject_key) in {WorkKey, RequestKey}
            and self.key.subject_key == self.subject_key
            and type(self.kind) is TransitionKind
            and self.key.transition_kind is self.kind
            and type(self.interruption_class) is InterruptionClass
            and _is_finite_nonnegative_number(self.occurred_at_epoch)
            and type(self.source_freshness) is SourceFreshness
        ):
            raise OperatorStateValidationError("invalid canonical operator event")
        object.__setattr__(self, "occurred_at_epoch", float(self.occurred_at_epoch))


@dataclass(frozen=True, order=True, slots=True)
class BootIdentifier:
    value: str

    def __post_init__(self) -> None:
        if not (
            type(self.value) is str
            and 1 <= len(self.value) <= 64
            and _OPAQUE_IDENTIFIER.fullmatch(self.value) is not None
        ):
            raise OperatorStateValidationError("invalid boot identifier")


@dataclass(frozen=True, slots=True)
class ClockSample:
    wall_epoch: float
    monotonic_seconds: float
    boot_id: BootIdentifier

    def __post_init__(self) -> None:
        if not (
            _is_finite_nonnegative_number(self.wall_epoch)
            and _is_finite_nonnegative_number(self.monotonic_seconds)
            and type(self.boot_id) is BootIdentifier
        ):
            raise OperatorStateValidationError("invalid clock sample")
        object.__setattr__(self, "wall_epoch", float(self.wall_epoch))
        object.__setattr__(self, "monotonic_seconds", float(self.monotonic_seconds))


class ClockContinuityStatus(str, Enum):
    STABLE = "stable"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ClockContinuityState:
    status: ClockContinuityStatus
    uncertain_since_monotonic: float | None
    recovery_confirmations: int

    def __post_init__(self) -> None:
        since_valid = self.uncertain_since_monotonic is None or (
            _is_finite_nonnegative_number(self.uncertain_since_monotonic)
        )
        if not (
            type(self.status) is ClockContinuityStatus
            and since_valid
            and type(self.recovery_confirmations) is int
            and 0 <= self.recovery_confirmations <= TIMING_RECOVERY_CONFIRMATIONS
        ):
            raise OperatorStateValidationError("invalid clock continuity")
        if (
            self.status is ClockContinuityStatus.UNCERTAIN
            and self.uncertain_since_monotonic is None
        ) or (
            self.status is ClockContinuityStatus.STABLE
            and self.uncertain_since_monotonic is not None
        ):
            raise OperatorStateValidationError("invalid clock continuity")
        if self.uncertain_since_monotonic is not None:
            object.__setattr__(
                self,
                "uncertain_since_monotonic",
                float(self.uncertain_since_monotonic),
            )


@dataclass(frozen=True, order=True, slots=True)
class _SourceTimingState:
    source_key: SourceKey
    uncertain_since_monotonic: float
    recovery_confirmations: int

    def __post_init__(self) -> None:
        if not (
            type(self.source_key) is SourceKey
            and _is_finite_nonnegative_number(self.uncertain_since_monotonic)
            and type(self.recovery_confirmations) is int
            and 0 <= self.recovery_confirmations < TIMING_RECOVERY_CONFIRMATIONS
        ):
            raise OperatorStateValidationError("invalid source timing state")
        object.__setattr__(
            self,
            "uncertain_since_monotonic",
            float(self.uncertain_since_monotonic),
        )


@dataclass(frozen=True, slots=True)
class CanonicalOperatorState:
    schema_version: int
    generation: int
    works: tuple[CanonicalWorkTruth, ...]
    requests: tuple[CanonicalRequestTruth, ...]
    source_watermarks: tuple[tuple[SourceKey, ProviderWatermark], ...]
    timing_uncertain_sources: tuple[SourceKey, ...]
    clock_continuity: ClockContinuityState
    last_clock: ClockSample | None
    _source_timing: tuple[_SourceTimingState, ...] = field(
        default=(),
        repr=False,
    )

    def __post_init__(self) -> None:
        if not (
            type(self.schema_version) is int
            and self.schema_version == _STATE_SCHEMA_VERSION
            and type(self.generation) is int
            and self.generation >= 0
            and type(self.works) is tuple
            and len(self.works) <= MAX_CANONICAL_WORKS
            and all(type(work) is CanonicalWorkTruth for work in self.works)
            and type(self.requests) is tuple
            and len(self.requests) <= MAX_CANONICAL_REQUESTS
            and all(type(request) is CanonicalRequestTruth for request in self.requests)
            and type(self.source_watermarks) is tuple
            and len(self.source_watermarks) <= MAX_CANONICAL_SOURCES
            and type(self.timing_uncertain_sources) is tuple
            and len(self.timing_uncertain_sources) <= MAX_CANONICAL_SOURCES
            and all(type(source) is SourceKey for source in self.timing_uncertain_sources)
            and type(self.clock_continuity) is ClockContinuityState
            and (self.last_clock is None or type(self.last_clock) is ClockSample)
            and type(self._source_timing) is tuple
            and len(self._source_timing) <= MAX_CANONICAL_SOURCES
            and all(type(item) is _SourceTimingState for item in self._source_timing)
        ):
            raise OperatorStateValidationError("invalid canonical operator state")

        works = tuple(sorted(self.works, key=lambda item: _work_sort_key(item.key)))
        requests = tuple(
            sorted(self.requests, key=lambda item: _request_sort_key(item.key))
        )
        if len({work.key for work in works}) != len(works) or len(
            {request.key for request in requests}
        ) != len(requests):
            raise OperatorStateValidationError("invalid canonical operator state")

        source_watermarks: list[tuple[SourceKey, ProviderWatermark]] = []
        for item in self.source_watermarks:
            if not (
                type(item) is tuple
                and len(item) == 2
                and type(item[0]) is SourceKey
                and type(item[1]) is ProviderWatermark
                and item[1].source_key == item[0]
            ):
                raise OperatorStateValidationError("invalid canonical operator state")
            source_watermarks.append(item)
        source_watermarks.sort(key=lambda item: _source_sort_key(item[0]))
        if len({source for source, _ in source_watermarks}) != len(source_watermarks):
            raise OperatorStateValidationError("invalid canonical operator state")

        uncertain_sources = tuple(
            sorted(self.timing_uncertain_sources, key=_source_sort_key)
        )
        if len(set(uncertain_sources)) != len(uncertain_sources):
            raise OperatorStateValidationError("invalid canonical operator state")

        source_timing = tuple(
            sorted(self._source_timing, key=lambda item: _source_sort_key(item.source_key))
        )
        if len({item.source_key for item in source_timing}) != len(source_timing):
            raise OperatorStateValidationError("invalid canonical operator state")
        timing_by_source = {item.source_key: item for item in source_timing}
        if not timing_by_source and uncertain_sources:
            since = self.clock_continuity.uncertain_since_monotonic
            if since is None:
                raise OperatorStateValidationError("invalid canonical operator state")
            timing_by_source = {
                source: _SourceTimingState(
                    source,
                    since,
                    min(
                        self.clock_continuity.recovery_confirmations,
                        TIMING_RECOVERY_CONFIRMATIONS - 1,
                    ),
                )
                for source in uncertain_sources
            }
        if set(timing_by_source) != set(uncertain_sources):
            raise OperatorStateValidationError("invalid canonical operator state")

        object.__setattr__(self, "works", works)
        object.__setattr__(self, "requests", requests)
        object.__setattr__(self, "source_watermarks", tuple(source_watermarks))
        object.__setattr__(self, "timing_uncertain_sources", uncertain_sources)
        object.__setattr__(
            self,
            "_source_timing",
            tuple(timing_by_source[source] for source in uncertain_sources),
        )


@dataclass(frozen=True, slots=True)
class ReducerDiagnostic:
    identifier: DiagnosticIdentifier
    count: int = 1

    def __post_init__(self) -> None:
        if not (
            type(self.identifier) is DiagnosticIdentifier
            and type(self.count) is int
            and 1 <= self.count <= MAX_REDUCER_DIAGNOSTIC_COUNT
        ):
            raise OperatorStateValidationError("invalid reducer diagnostic")


@dataclass(frozen=True, slots=True)
class ReductionResult:
    state: CanonicalOperatorState
    events: tuple[CanonicalOperatorEvent, ...]
    invalidations: frozenset[InvalidationDomain]
    diagnostics: tuple[ReducerDiagnostic, ...]

    def __post_init__(self) -> None:
        if not (
            type(self.state) is CanonicalOperatorState
            and type(self.events) is tuple
            and len(self.events) <= MAX_EVENTS_PER_REDUCTION
            and all(type(event) is CanonicalOperatorEvent for event in self.events)
            and type(self.invalidations) is frozenset
            and all(type(domain) is InvalidationDomain for domain in self.invalidations)
            and type(self.diagnostics) is tuple
            and len(self.diagnostics) <= MAX_REDUCER_DIAGNOSTICS
            and all(type(item) is ReducerDiagnostic for item in self.diagnostics)
        ):
            raise OperatorStateValidationError("invalid reduction result")


def empty_operator_state() -> CanonicalOperatorState:
    """Return the exact empty v1 canonical reducer state."""
    return CanonicalOperatorState(
        schema_version=_STATE_SCHEMA_VERSION,
        generation=0,
        works=(),
        requests=(),
        source_watermarks=(),
        timing_uncertain_sources=(),
        clock_continuity=ClockContinuityState(
            ClockContinuityStatus.STABLE,
            None,
            0,
        ),
        last_clock=None,
    )


def classify_operator_event(kind: TransitionKind) -> InterruptionClass:
    """Classify a semantic edge exactly once for downstream delivery policy."""
    if type(kind) is not TransitionKind:
        raise OperatorStateValidationError("invalid transition kind")
    if kind is TransitionKind.REQUEST_OPENED:
        return InterruptionClass.ACTION_REQUIRED
    if kind is TransitionKind.FAILED:
        return InterruptionClass.IMPORTANT_OUTCOME
    if kind in {TransitionKind.COMPLETED, TransitionKind.SOURCE_RECOVERED}:
        return InterruptionClass.COURTESY
    return InterruptionClass.AMBIENT


def _has_exact_fields(payload: dict[object, object], fields: frozenset[str]) -> bool:
    if len(payload) != len(fields):
        return False
    for key in payload:
        if type(key) is not str or key not in fields:
            return False
    return True


def semantic_event_key_to_payload(key: SemanticEventKey) -> dict[str, object]:
    """Encode one content-free semantic event identity into the exact v1 shape."""
    if type(key) is not SemanticEventKey:
        raise OperatorStateValidationError("invalid semantic event key")
    subject_kind = "work" if type(key.subject_key) is WorkKey else "request"
    subject_payload = (
        work_key_to_payload(key.subject_key)
        if type(key.subject_key) is WorkKey
        else request_key_to_payload(key.subject_key)
    )
    watermark = key.provider_watermark
    return {
        "version": {"major": _EVENT_SCHEMA_MAJOR, "minor": _EVENT_SCHEMA_MINOR},
        "subject_kind": subject_kind,
        "subject_key": subject_payload,
        "transition_kind": key.transition_kind.value,
        "provider_watermark": {
            "basis": watermark.basis.value,
            "occurred_at_epoch": watermark.occurred_at_epoch,
            "event_token": watermark.event_token.value,
            "sequence": watermark.sequence,
            "tie_break_rank": watermark.tie_break_rank,
        },
    }


def semantic_event_key_from_payload(payload: object) -> SemanticEventKey | None:
    """Decode only the exact built-in-dict v1 semantic event key shape."""
    if type(payload) is not dict or not _has_exact_fields(payload, _EVENT_FIELDS):
        return None
    version = payload["version"]
    if type(version) is not dict or not _has_exact_fields(version, _VERSION_FIELDS):
        return None
    if not (
        type(version["major"]) is int
        and type(version["minor"]) is int
        and version["major"] == _EVENT_SCHEMA_MAJOR
        and version["minor"] == _EVENT_SCHEMA_MINOR
    ):
        return None
    subject_kind = payload["subject_kind"]
    if type(subject_kind) is not str or subject_kind not in {"work", "request"}:
        return None
    subject = (
        work_key_from_payload(payload["subject_key"])
        if subject_kind == "work"
        else request_key_from_payload(payload["subject_key"])
    )
    if subject is None:
        return None
    transition_value = payload["transition_kind"]
    watermark_payload = payload["provider_watermark"]
    if not (
        type(transition_value) is str
        and type(watermark_payload) is dict
        and _has_exact_fields(watermark_payload, _WATERMARK_FIELDS)
    ):
        return None
    basis_value = watermark_payload["basis"]
    occurred_at = watermark_payload["occurred_at_epoch"]
    token_value = watermark_payload["event_token"]
    sequence = watermark_payload["sequence"]
    rank = watermark_payload["tie_break_rank"]
    if not (
        type(basis_value) is str
        and type(occurred_at) in {int, float}
        and type(token_value) is str
        and (sequence is None or type(sequence) is int)
        and type(rank) is int
    ):
        return None
    source = subject.source_key if type(subject) is WorkKey else subject.work_key.source_key
    try:
        watermark = ProviderWatermark(
            source,
            WatermarkBasis(basis_value),
            occurred_at,
            EventToken(token_value),
            sequence,
            rank,
        )
        return SemanticEventKey(
            subject,
            TransitionKind(transition_value),
            watermark,
        )
    except (ValueError, OperatorStateValidationError):
        return None


def _clock_discontinuous(previous: ClockSample | None, current: ClockSample) -> bool:
    if previous is None:
        return False
    if current.boot_id != previous.boot_id:
        return True
    monotonic_delta = current.monotonic_seconds - previous.monotonic_seconds
    wall_delta = current.wall_epoch - previous.wall_epoch
    # Wall AHEAD of monotonic is SLEEP, not distrust: macOS monotonic time
    # pauses while the machine sleeps, so every nap over the tolerance used
    # to read as a "discontinuity" and quarantined every source on wake.
    # The wall clock is exactly as trustworthy after a nap as before it.
    # Only backwards motion -- either clock running in reverse, or the wall
    # falling BEHIND monotonic (a wall clock stepped backwards) -- is
    # evidence the wall clock cannot be trusted for ordering.
    return (
        monotonic_delta < 0.0
        or wall_delta < 0.0
        or (monotonic_delta - wall_delta) > MAX_CLOCK_DELTA_DIVERGENCE_SECONDS
    )


def _future_dated(batch: ProviderFactBatch, clock: ClockSample) -> bool:
    latest_allowed = clock.wall_epoch + MAX_CLOCK_DELTA_DIVERGENCE_SECONDS
    if batch.observed_at_epoch > latest_allowed or batch.watermark.occurred_at_epoch > latest_allowed:
        return True
    return any(
        fact.watermark.occurred_at_epoch > latest_allowed
        for fact in (*batch.work_facts, *batch.request_facts)
    )


def _source_loss(batch: ProviderFactBatch) -> bool:
    return (
        batch.source_freshness
        not in {SourceFreshness.FRESH, SourceFreshness.RESTORED}
        or batch.source_health in _SOURCE_LOSS_HEALTH
    )


def _restored(batch: ProviderFactBatch) -> bool:
    return (
        batch.observation_authority is ObservationAuthority.RESTORED_LAST_KNOWN
        or batch.source_freshness is SourceFreshness.RESTORED
    )


def _codex_usage_limit_can_close_direct_work(
    existing: CanonicalWorkTruth | None,
    batch: ProviderFactBatch,
    fact: ProviderWorkFact,
) -> bool:
    """Allow one typed Codex transcript terminal to close older live direct work."""
    return (
        existing is not None
        and batch.source_key
        == SourceKey("codex", "hooks", "global", "live_agent_events")
        and batch.observation_authority is ObservationAuthority.FALLBACK_OBSERVATION
        and batch.request_facts == ()
        and len(batch.work_facts) == 1
        and fact.terminal_cause is ProviderTerminalCause.CODEX_USAGE_LIMIT
        and fact.lifecycle is WorkLifecycle.FAILED
        and fact.next_actor is NextActor.NONE
        and existing.observation_authority
        is ObservationAuthority.DIRECT_PROVIDER_OBSERVATION
        and existing.lifecycle in {WorkLifecycle.ACTIVE, WorkLifecycle.WAITING}
        and existing.source_freshness is SourceFreshness.FRESH
        and not existing.timing_uncertain
        and compare_watermarks(fact.watermark, existing.watermark)
        is WatermarkOrder.NEWER
    )


def _timing_lease_expired(
    entry: _SourceTimingState,
    clock: ClockSample,
) -> bool:
    """True when the reason for this source's quarantine has expired.

    A timing quarantine is a statement about ONE event: the clock jumped, or a
    fact arrived dated in the future. `uncertain_since_monotonic` is re-stamped
    to now every time either recurs, so an entry that has carried the same
    stamp for a full lease is an entry whose cause has demonstrably not
    recurred for that long on a monotonic clock that never went backwards and
    never changed boot.

    This only ever RELAXES the corroboration a healthy, fresh, direct
    observation has to supply: past the lease one such batch is enough where
    two were required. It cannot release a source that is still losing --
    `_source_loss` is answered before this is asked -- so a source-loss hold
    still expires into "no longer known" rather than into "live".
    """
    return (
        clock.monotonic_seconds - entry.uncertain_since_monotonic
        >= TIMING_UNCERTAINTY_LEASE_SECONDS
    )


def _recovery_eligible(
    batch: ProviderFactBatch,
    retained: ProviderWatermark | None,
) -> bool:
    if not (
        batch.observation_authority
        >= ObservationAuthority.DIRECT_PROVIDER_OBSERVATION
        and batch.source_health is SourceHealth.HEALTHY
        and batch.source_freshness is SourceFreshness.FRESH
    ):
        return False
    return retained is None or compare_watermarks(batch.watermark, retained) in {
        WatermarkOrder.EQUAL,
        WatermarkOrder.NEWER,
    }


def _known_sources(previous: CanonicalOperatorState) -> set[SourceKey]:
    sources = {source for source, _ in previous.source_watermarks}
    sources.update(work.key.source_key for work in previous.works)
    sources.update(request.key.work_key.source_key for request in previous.requests)
    return sources


@dataclass(frozen=True, slots=True)
class _ContinuityDecision:
    continuity: ClockContinuityState
    uncertain_sources: frozenset[SourceKey]
    source_timing: tuple[_SourceTimingState, ...]
    semantic_allowed: bool
    metadata_allowed: bool
    clock_quarantine: bool


def _continuity_from_source_timing(
    source_timing: dict[SourceKey, _SourceTimingState],
    *,
    stable_confirmations: int = 0,
    live_sources: frozenset[SourceKey] | None = None,
) -> ClockContinuityState:
    # The GLOBAL report is elected only by sources that can still speak.
    # A quiescent source's timing entry (a dead session's) keeps its
    # per-source gating but must not pin the whole system "uncertain,
    # 0 confirmations" for the full hour lease after every wake -- live
    # sources recover in two batches and their entries are deleted, so
    # the old max() only ever surfaced the dead sources' zeros.
    driving = (
        source_timing
        if live_sources is None
        else {
            source: entry
            for source, entry in source_timing.items()
            if source in live_sources
        }
    )
    if not driving:
        return ClockContinuityState(
            ClockContinuityStatus.STABLE,
            None,
            stable_confirmations,
        )
    return ClockContinuityState(
        ClockContinuityStatus.UNCERTAIN,
        min(item.uncertain_since_monotonic for item in driving.values()),
        max(item.recovery_confirmations for item in driving.values()),
    )


def _continuity_decision(
    source_timing: dict[SourceKey, _SourceTimingState],
    *,
    semantic_allowed: bool,
    metadata_allowed: bool,
    clock_quarantine: bool,
    stable_confirmations: int = 0,
    live_sources: frozenset[SourceKey] | None = None,
) -> _ContinuityDecision:
    ordered_timing = tuple(
        source_timing[source]
        for source in sorted(source_timing, key=_source_sort_key)
    )
    return _ContinuityDecision(
        continuity=_continuity_from_source_timing(
            source_timing,
            stable_confirmations=stable_confirmations,
            live_sources=live_sources,
        ),
        uncertain_sources=frozenset(source_timing),
        source_timing=ordered_timing,
        semantic_allowed=semantic_allowed,
        metadata_allowed=metadata_allowed,
        clock_quarantine=clock_quarantine,
    )


def _determine_continuity(
    previous: CanonicalOperatorState,
    batch: ProviderFactBatch,
    clock: ClockSample,
    retained_watermark: ProviderWatermark | None,
    diagnostics: dict[str, int],
) -> _ContinuityDecision:
    source_timing = {item.source_key: item for item in previous._source_timing}
    live_sources = frozenset(
        source
        for source, watermark in previous.source_watermarks
        if clock.wall_epoch - watermark.occurred_at_epoch
        <= PRESENCE_HORIZON_SECONDS
    ) | {batch.source_key}

    if _clock_discontinuous(previous.last_clock, clock):
        affected = _known_sources(previous) | {batch.source_key}
        source_timing = {
            source: _SourceTimingState(source, clock.monotonic_seconds, 0)
            for source in affected
        }
        diagnostics["clock_discontinuity"] = 1
        return _continuity_decision(
            source_timing,
            live_sources=live_sources,
            semantic_allowed=False,
            metadata_allowed=False,
            clock_quarantine=True,
        )

    if _future_dated(batch, clock):
        source_timing[batch.source_key] = _SourceTimingState(
            batch.source_key,
            clock.monotonic_seconds,
            0,
        )
        diagnostics["future_fact_quarantined"] = 1
        return _continuity_decision(
            source_timing,
            live_sources=live_sources,
            semantic_allowed=False,
            metadata_allowed=False,
            clock_quarantine=True,
        )

    # Quiescent sources age out by lease on ANY reduction: the lease used
    # to be checked only when the quarantined source itself sent a batch,
    # so a source that never sends again (a dead session's) held its entry
    # -- and global clock continuity -- hostage forever after a restart.
    expired_quiescent = [
        source
        for source, entry in source_timing.items()
        if source != batch.source_key and _timing_lease_expired(entry, clock)
    ]
    if expired_quiescent:
        for source in expired_quiescent:
            del source_timing[source]
        diagnostics["timing_quarantine_lease_expired_quiescent"] = len(
            expired_quiescent
        )

    source_entry = source_timing.get(batch.source_key)
    if source_entry is None:
        if _restored(batch) or _source_loss(batch):
            source_timing[batch.source_key] = _SourceTimingState(
                batch.source_key,
                clock.monotonic_seconds,
                0,
            )
            return _continuity_decision(
                source_timing,
                semantic_allowed=_restored(batch),
                metadata_allowed=True,
                clock_quarantine=False,
            )
        return _continuity_decision(
            source_timing,
            live_sources=live_sources,
            semantic_allowed=True,
            metadata_allowed=True,
            clock_quarantine=False,
            stable_confirmations=(
                previous.clock_continuity.recovery_confirmations
                if not source_timing
                else 0
            ),
        )

    # None of the branches below clears the confirmations already earned. They
    # used to. That conflated "this batch does not confirm recovery" with "this
    # batch disproves it" -- and a non-confirming batch is routine: any hook
    # record without a request identity arrives `SourceFreshness.PARTIAL`, which
    # `_source_loss` reads as loss. Interleaved with clean ones it reset the
    # counter faster than the counter could climb, so `recovery_confirmations`
    # sat at 0 forever and no source ever left quarantine. Evidence AGAINST
    # continuity -- a clock jump, a future-dated fact -- re-stamps the entry at
    # zero above, which is where that belongs. The bar is still two
    # corroborating direct observations with no contrary evidence in between;
    # it is no longer two in a row with no routine partial in between, which is
    # a different requirement and was never the intended one.
    if _restored(batch):
        return _continuity_decision(
            source_timing,
            live_sources=live_sources,
            semantic_allowed=True,
            metadata_allowed=True,
            clock_quarantine=False,
        )
    if _source_loss(batch):
        # Deliberately ahead of the lease: a source that is still losing is not
        # a source whose quarantine has expired. The lease below only ever
        # releases one on the strength of a healthy, fresh, direct observation.
        return _continuity_decision(
            source_timing,
            live_sources=live_sources,
            semantic_allowed=False,
            metadata_allowed=True,
            clock_quarantine=False,
        )
    if _timing_lease_expired(source_entry, clock):
        del source_timing[batch.source_key]
        diagnostics["timing_quarantine_lease_expired"] = 1
        return _continuity_decision(
            source_timing,
            live_sources=live_sources,
            semantic_allowed=True,
            metadata_allowed=True,
            clock_quarantine=False,
            stable_confirmations=(
                TIMING_RECOVERY_CONFIRMATIONS if not source_timing else 0
            ),
        )
    if not _recovery_eligible(batch, retained_watermark):
        return _continuity_decision(
            source_timing,
            live_sources=live_sources,
            semantic_allowed=False,
            metadata_allowed=False,
            clock_quarantine=False,
        )

    confirmations = source_entry.recovery_confirmations + 1
    allowed = confirmations >= TIMING_RECOVERY_CONFIRMATIONS
    if allowed:
        del source_timing[batch.source_key]
    else:
        source_timing[batch.source_key] = replace(
            source_entry,
            recovery_confirmations=confirmations,
        )
    return _continuity_decision(
        source_timing,
        semantic_allowed=allowed,
        metadata_allowed=allowed,
        clock_quarantine=False,
        stable_confirmations=(
            TIMING_RECOVERY_CONFIRMATIONS if allowed and not source_timing else 0
        ),
    )


def _source_authority(
    works: dict[WorkKey, CanonicalWorkTruth],
    source: SourceKey,
) -> ObservationAuthority | None:
    authorities = [
        work.observation_authority
        for work in works.values()
        if work.key.source_key == source
        and work.source_freshness is SourceFreshness.FRESH
        and not work.timing_uncertain
    ]
    return max(authorities) if authorities else None


def _is_degradation(current: SourceHealth, candidate: SourceHealth) -> bool:
    return current is SourceHealth.HEALTHY and candidate is not SourceHealth.HEALTHY


def _work_transition(
    previous: CanonicalWorkTruth | None,
    lifecycle: WorkLifecycle,
) -> TransitionKind | None:
    if previous is not None and previous.lifecycle is lifecycle:
        return None
    if lifecycle in {WorkLifecycle.ACTIVE, WorkLifecycle.WAITING}:
        return TransitionKind.BECAME_ACTIVE
    if lifecycle is WorkLifecycle.IDLE:
        return TransitionKind.BECAME_IDLE if previous is not None else None
    if lifecycle is WorkLifecycle.COMPLETED:
        return TransitionKind.COMPLETED
    if lifecycle is WorkLifecycle.FAILED:
        return TransitionKind.FAILED
    return None


def _event(
    subject: WorkKey | RequestKey,
    kind: TransitionKind,
    watermark: ProviderWatermark,
    freshness: SourceFreshness,
) -> CanonicalOperatorEvent:
    key = SemanticEventKey(subject, kind, watermark)
    return CanonicalOperatorEvent(
        key,
        subject,
        kind,
        classify_operator_event(kind),
        watermark.occurred_at_epoch,
        freshness,
    )


def _request_actionable(fact: ProviderRequestFact) -> bool:
    return fact.request_kind is not RequestKind.UNKNOWN and fact.next_actor is NextActor.USER


def _retained_request_is_live(request: CanonicalRequestTruth) -> bool:
    return (
        request.request_kind is not RequestKind.UNKNOWN
        and request.semantic_event_key.transition_kind is TransitionKind.REQUEST_OPENED
        and request.phase is not RequestPhase.RESOLVED
    )


def _request_eligibility(
    phase: RequestPhase,
    *,
    actionable: bool,
) -> AcknowledgementEligibility:
    if phase is RequestPhase.LIVE_UNACKNOWLEDGED:
        return (
            AcknowledgementEligibility.ELIGIBLE
            if actionable
            else AcknowledgementEligibility.NOT_ACTIONABLE
        )
    if phase is RequestPhase.LIVE_ACKNOWLEDGED:
        return AcknowledgementEligibility.ALREADY_ACKNOWLEDGED
    if phase is RequestPhase.STALE_HOLD:
        return AcknowledgementEligibility.STALE_HOLD
    if phase is RequestPhase.RESOLVED:
        return AcknowledgementEligibility.RESOLVED
    return AcknowledgementEligibility.NOT_ACTIONABLE


def _new_request_truth(
    fact: ProviderRequestFact,
    *,
    previous: CanonicalRequestTruth | None,
    source_freshness: SourceFreshness,
    restored: bool,
    acknowledged: bool,
    clock: ClockSample,
    observation_authority: ObservationAuthority,
) -> tuple[CanonicalRequestTruth, TransitionKind | None]:
    transition: TransitionKind | None = None
    if fact.state is ProviderRequestState.RESOLVED:
        phase = RequestPhase.RESOLVED
        transition = (
            TransitionKind.REQUEST_RESOLVED
            if previous is not None and previous.phase is not RequestPhase.RESOLVED
            else None
        )
        semantic_key = SemanticEventKey(
            fact.key,
            TransitionKind.REQUEST_RESOLVED,
            fact.watermark,
        )
        opened_at = previous.opened_at_epoch if previous is not None else None
        eligible = previous.eligible_elapsed_seconds if previous is not None else 0.0
    elif fact.state is ProviderRequestState.UNKNOWN or not _request_actionable(fact):
        phase = RequestPhase.UNKNOWN_EXPIRED
        semantic_key = (
            previous.semantic_event_key
            if previous is not None
            else SemanticEventKey(
                fact.key,
                TransitionKind.REQUEST_OPENED,
                fact.watermark,
            )
        )
        opened_at = previous.opened_at_epoch if previous is not None else None
        eligible = previous.eligible_elapsed_seconds if previous is not None else 0.0
    else:
        reopening = previous is None or previous.phase in {
            RequestPhase.RESOLVED,
            RequestPhase.UNKNOWN_EXPIRED,
        }
        if restored or source_freshness is not SourceFreshness.FRESH:
            phase = RequestPhase.STALE_HOLD
        else:
            phase = (
                RequestPhase.LIVE_ACKNOWLEDGED
                if acknowledged
                else RequestPhase.LIVE_UNACKNOWLEDGED
            )
        if reopening:
            semantic_key = SemanticEventKey(
                fact.key,
                TransitionKind.REQUEST_OPENED,
                fact.watermark,
            )
            opened_at = fact.watermark.occurred_at_epoch
            eligible = (
                max(0.0, clock.wall_epoch - opened_at)
                if phase is RequestPhase.LIVE_UNACKNOWLEDGED
                else 0.0
            )
            if not restored and phase in {
                RequestPhase.LIVE_UNACKNOWLEDGED,
                RequestPhase.LIVE_ACKNOWLEDGED,
            }:
                transition = TransitionKind.REQUEST_OPENED
        else:
            semantic_key = previous.semantic_event_key
            opened_at = previous.opened_at_epoch
            eligible = previous.eligible_elapsed_seconds

    return (
        CanonicalRequestTruth(
            key=fact.key,
            phase=phase,
            request_kind=fact.request_kind,
            next_actor=fact.next_actor,
            watermark=fact.watermark,
            source_freshness=source_freshness,
            acknowledgement_eligibility=_request_eligibility(
                phase,
                actionable=_request_actionable(fact),
            ),
            semantic_event_key=semantic_key,
            opened_at_epoch=opened_at,
            eligible_elapsed_seconds=eligible,
            _observation_authority=observation_authority,
        ),
        transition,
    )


def _remove_parent_cycles(
    works: dict[WorkKey, CanonicalWorkTruth],
    diagnostics: dict[str, int],
) -> None:
    cycle_members: set[WorkKey] = set()
    for origin in sorted(works, key=_work_sort_key):
        positions: dict[WorkKey, int] = {}
        path: list[WorkKey] = []
        current: WorkKey | None = origin
        while current is not None and current in works:
            if current in positions:
                cycle_members.update(path[positions[current] :])
                break
            positions[current] = len(path)
            path.append(current)
            current = works[current].parent_key
    for key in cycle_members:
        works[key] = replace(works[key], parent_key=None)
    if cycle_members:
        diagnostics["parent_cycle_removed"] = len(cycle_members)


def _diagnostic_tuple(items: dict[str, int]) -> tuple[ReducerDiagnostic, ...]:
    return tuple(
        ReducerDiagnostic(
            DiagnosticIdentifier(identifier),
            min(count, MAX_REDUCER_DIAGNOSTIC_COUNT),
        )
        for identifier, count in sorted(items.items())[:MAX_REDUCER_DIAGNOSTICS]
        if count > 0
    )


def _event_sort_key(event: CanonicalOperatorEvent) -> tuple[object, ...]:
    return event.key._ordering_key()


def _advance_elapsed(
    previous: CanonicalOperatorState,
    clock: ClockSample,
) -> float:
    if previous.last_clock is None:
        return 0.0
    if _clock_discontinuous(previous.last_clock, clock):
        return 0.0
    return max(0.0, clock.monotonic_seconds - previous.last_clock.monotonic_seconds)


def _source_freshness_for_request(
    request: CanonicalRequestTruth,
    works: dict[WorkKey, CanonicalWorkTruth],
    batch: ProviderFactBatch,
    metadata_applies: bool,
) -> SourceFreshness:
    work = works.get(request.key.work_key)
    if work is not None:
        return work.source_freshness
    if metadata_applies and request.key.work_key.source_key == batch.source_key:
        return batch.source_freshness
    return request.source_freshness


def reduce_operator_state(
    previous: CanonicalOperatorState,
    batch: ProviderFactBatch,
    *,
    clock: ClockSample,
    acknowledged_requests: frozenset[RequestKey] = frozenset(),
) -> ReductionResult:
    """Reduce one provider batch into deterministic bounded canonical truth."""
    if type(previous) is not CanonicalOperatorState:
        raise OperatorStateValidationError("invalid operator state")
    if type(batch) is not ProviderFactBatch:
        raise OperatorStateValidationError("invalid provider fact batch")
    if type(clock) is not ClockSample:
        raise OperatorStateValidationError("invalid clock sample")
    if type(acknowledged_requests) is not frozenset or not all(
        type(key) is RequestKey for key in acknowledged_requests
    ):
        raise OperatorStateValidationError("invalid acknowledged requests")
    if len(acknowledged_requests) > MAX_CANONICAL_REQUESTS:
        raise OperatorStateValidationError("invalid acknowledged requests")

    diagnostics: dict[str, int] = {}
    previous_works = {work.key: work for work in previous.works}
    works = dict(previous_works)
    previous_requests = {request.key: request for request in previous.requests}
    requests = dict(previous_requests)
    source_watermarks = dict(previous.source_watermarks)
    retained_source_watermark = source_watermarks.get(batch.source_key)

    decision = _determine_continuity(
        previous,
        batch,
        clock,
        retained_source_watermark,
        diagnostics,
    )
    restored_batch = _restored(batch)
    batch_order = (
        WatermarkOrder.NEWER
        if retained_source_watermark is None
        else compare_watermarks(batch.watermark, retained_source_watermark)
    )
    work_source_authority = _source_authority(previous_works, batch.source_key)
    request_source_authorities = [
        request._observation_authority
        for request in previous_requests.values()
        if request.key.work_key.source_key == batch.source_key
        and request.source_freshness is SourceFreshness.FRESH
    ]
    current_source_authority = max(
        (
            *(() if work_source_authority is None else (work_source_authority,)),
            *request_source_authorities,
        ),
        default=None,
    )
    authority_blocked = (
        current_source_authority is not None
        and batch.observation_authority < current_source_authority
    )
    typed_terminal_exception = any(
        _codex_usage_limit_can_close_direct_work(
            previous_works.get(fact.key),
            batch,
            fact,
        )
        for fact in batch.work_facts
    )
    semantic_allowed = decision.semantic_allowed and (
        batch_order is WatermarkOrder.NEWER
        or (
            batch_order is WatermarkOrder.EQUAL
            and (
                restored_batch
                or decision.continuity.recovery_confirmations
                >= TIMING_RECOVERY_CONFIRMATIONS
            )
        )
    )
    if authority_blocked and not typed_terminal_exception:
        semantic_allowed = False
        diagnostics["lower_authority_fact_ignored"] = 1
    elif batch_order is WatermarkOrder.OLDER:
        diagnostics["older_batch_ignored"] = 1

    metadata_applies = decision.metadata_allowed and batch_order in {
        WatermarkOrder.EQUAL,
        WatermarkOrder.NEWER,
    }
    if authority_blocked:
        metadata_applies = any(
            _is_degradation(work.source_health, batch.source_health)
            for work in previous_works.values()
            if work.key.source_key == batch.source_key
        )

    health_events: list[CanonicalOperatorEvent] = []
    if metadata_applies:
        for key, work in tuple(works.items()):
            if key.source_key != batch.source_key:
                continue
            old_health = work.source_health
            freshness = (
                work.source_freshness
                if authority_blocked and batch.source_freshness is SourceFreshness.FRESH
                else batch.source_freshness
            )
            works[key] = replace(
                work,
                source_health=batch.source_health,
                source_freshness=freshness,
            )
            if old_health is SourceHealth.HEALTHY and batch.source_health is not SourceHealth.HEALTHY:
                health_events.append(
                    _event(
                        key,
                        TransitionKind.SOURCE_DEGRADED,
                        batch.watermark,
                        freshness,
                    )
                )
            elif old_health is not SourceHealth.HEALTHY and batch.source_health is SourceHealth.HEALTHY:
                health_events.append(
                    _event(
                        key,
                        TransitionKind.SOURCE_RECOVERED,
                        batch.watermark,
                        freshness,
                    )
                )

    work_events: dict[WorkKey, CanonicalOperatorEvent] = {}
    if semantic_allowed:
        for fact in batch.work_facts:
            existing = previous_works.get(fact.key)
            order = (
                WatermarkOrder.NEWER
                if existing is None
                else compare_watermarks(fact.watermark, existing.watermark)
            )
            if order is WatermarkOrder.EQUAL and existing is not None:
                continue
            if order is not WatermarkOrder.NEWER:
                continue
            if (
                existing is not None
                and existing.source_freshness is SourceFreshness.FRESH
                and not existing.timing_uncertain
                and batch.observation_authority < existing.observation_authority
                and not _codex_usage_limit_can_close_direct_work(
                    existing,
                    batch,
                    fact,
                )
            ):
                diagnostics["lower_authority_fact_ignored"] = (
                    diagnostics.get("lower_authority_fact_ignored", 0) + 1
                )
                continue
            works[fact.key] = CanonicalWorkTruth(
                key=fact.key,
                lifecycle=fact.lifecycle,
                watermark=fact.watermark,
                observation_authority=batch.observation_authority,
                source_health=batch.source_health,
                source_freshness=batch.source_freshness,
                next_actor=fact.next_actor,
                safe_label=fact.safe_label,
                parent_key=fact.parent_key,
                request_keys=existing.request_keys if existing is not None else (),
                timing_uncertain=fact.key.source_key in decision.uncertain_sources,
            )
            if fact.terminal_cause is ProviderTerminalCause.CODEX_USAGE_LIMIT:
                for request_key in tuple(requests):
                    if request_key.work_key == fact.key:
                        del requests[request_key]
            transition = _work_transition(existing, fact.lifecycle)
            if transition is not None and not restored_batch:
                work_events[fact.key] = _event(
                    fact.key,
                    transition,
                    fact.watermark,
                    batch.source_freshness,
                )

    request_events: dict[RequestKey, CanonicalOperatorEvent] = {}
    if semantic_allowed:
        for fact in batch.request_facts:
            existing = previous_requests.get(fact.key)
            order = (
                WatermarkOrder.NEWER
                if existing is None
                else compare_watermarks(fact.watermark, existing.watermark)
            )
            if order is WatermarkOrder.EQUAL and existing is not None:
                continue
            if order is not WatermarkOrder.NEWER:
                continue
            owner = previous_works.get(fact.key.work_key)
            if (
                (
                    owner is not None
                    and owner.source_freshness is SourceFreshness.FRESH
                    and not owner.timing_uncertain
                    and batch.observation_authority < owner.observation_authority
                )
                or (
                    existing is not None
                    and existing.source_freshness is SourceFreshness.FRESH
                    and batch.observation_authority < existing._observation_authority
                )
            ):
                diagnostics["lower_authority_fact_ignored"] = (
                    diagnostics.get("lower_authority_fact_ignored", 0) + 1
                )
                continue
            request, transition = _new_request_truth(
                fact,
                previous=existing,
                source_freshness=batch.source_freshness,
                restored=restored_batch,
                acknowledged=fact.key in acknowledged_requests,
                clock=clock,
                observation_authority=batch.observation_authority,
            )
            requests[fact.key] = request
            if transition is not None:
                request_events[fact.key] = _event(
                    fact.key,
                    transition,
                    fact.watermark,
                    batch.source_freshness,
                )

    if (
        decision.metadata_allowed
        and not authority_blocked
        and batch_order is WatermarkOrder.NEWER
    ):
        source_watermarks[batch.source_key] = batch.watermark
    elif retained_source_watermark is None and restored_batch and decision.metadata_allowed:
        source_watermarks[batch.source_key] = batch.watermark

    uncertain_sources = set(decision.uncertain_sources)
    for key, work in tuple(works.items()):
        uncertain = key.source_key in uncertain_sources
        freshness = work.source_freshness
        if uncertain and decision.clock_quarantine:
            freshness = SourceFreshness.TIMING_UNCERTAIN
        elif not uncertain and freshness in {
            SourceFreshness.TIMING_UNCERTAIN,
            SourceFreshness.RESTORED,
        }:
            freshness = SourceFreshness.FRESH
        works[key] = replace(
            work,
            source_freshness=freshness,
            timing_uncertain=uncertain,
        )

    elapsed_delta = _advance_elapsed(previous, clock)
    source_timing = {item.source_key: item for item in decision.source_timing}
    for key, request in tuple(requests.items()):
        source = key.work_key.source_key
        provider_live = _retained_request_is_live(request)
        freshness = _source_freshness_for_request(
            request,
            works,
            batch,
            metadata_applies,
        )
        if source in uncertain_sources:
            timing = source_timing[source]
            lease_expired = (
                clock.monotonic_seconds - timing.uncertain_since_monotonic
                >= TIMING_UNCERTAINTY_LEASE_SECONDS
            )
            if decision.clock_quarantine:
                freshness = SourceFreshness.TIMING_UNCERTAIN
            phase = (
                RequestPhase.UNKNOWN_EXPIRED
                if provider_live and lease_expired
                else RequestPhase.STALE_HOLD
                if provider_live
                else request.phase
            )
        elif provider_live:
            phase = (
                RequestPhase.LIVE_ACKNOWLEDGED
                if key in acknowledged_requests
                else RequestPhase.LIVE_UNACKNOWLEDGED
            )
            if freshness in {
                SourceFreshness.TIMING_UNCERTAIN,
                SourceFreshness.RESTORED,
            }:
                freshness = SourceFreshness.FRESH
        else:
            phase = request.phase

        eligible = request.eligible_elapsed_seconds
        old = previous_requests.get(key)
        if (
            old is not None
            and old.phase is RequestPhase.LIVE_UNACKNOWLEDGED
            and phase is RequestPhase.LIVE_UNACKNOWLEDGED
            and source not in uncertain_sources
        ):
            eligible += elapsed_delta
        actionable = request.request_kind is not RequestKind.UNKNOWN and request.next_actor is NextActor.USER
        requests[key] = replace(
            request,
            phase=phase,
            source_freshness=freshness,
            acknowledgement_eligibility=_request_eligibility(
                phase,
                actionable=actionable,
            ),
            eligible_elapsed_seconds=eligible,
        )

    _remove_parent_cycles(works, diagnostics)

    # Gated on "no clock quarantine" (no discontinuity, no future-dated
    # facts) rather than global STABLE continuity: per-source timing
    # quarantines linger for sources that never send again (a dead grok
    # session's source has nobody left to confirm recovery), and a work
    # that has been silent for a full day is retirable under any
    # per-source lease -- only a distrusted wall clock forbids it.
    if not decision.clock_quarantine:
        horizon = clock.wall_epoch - CANONICAL_WORK_RETENTION_SECONDS
        expired = [
            key
            for key, work in works.items()
            if work.watermark.occurred_at_epoch < horizon
        ]
        if expired:
            diagnostics["canonical_work_retired"] = len(expired)
            for key in expired:
                del works[key]
            requests = {
                key: request
                for key, request in requests.items()
                if key.work_key in works
            }

    sorted_work_keys = sorted(works, key=_work_sort_key)
    if len(sorted_work_keys) > MAX_CANONICAL_WORKS:
        diagnostics["canonical_work_limit"] = len(sorted_work_keys) - MAX_CANONICAL_WORKS
        # Evict by recency, never by name: the survivors are the most
        # recently heard works (epoch ties broken by the canonical key),
        # then laid out in canonical order so the state stays
        # deterministic. A lexical cut could retire the newest live work
        # while an alphabetically earlier dead one survived.
        survivors = set(
            sorted(
                works,
                key=lambda key: (
                    -works[key].watermark.occurred_at_epoch,
                    _work_sort_key(key),
                ),
            )[:MAX_CANONICAL_WORKS]
        )
        sorted_work_keys = [key for key in sorted_work_keys if key in survivors]
    works = {key: works[key] for key in sorted_work_keys}

    # NOTE: requests are deliberately NOT filtered to surviving works --
    # a request-only truth (no canonical work yet) is a real state the
    # reducer retains; see test_request_only_truth_retains_authority_*.
    sorted_request_keys = sorted(requests, key=_request_sort_key)
    if len(sorted_request_keys) > MAX_CANONICAL_REQUESTS:
        diagnostics["canonical_request_limit"] = (
            len(sorted_request_keys) - MAX_CANONICAL_REQUESTS
        )
        surviving_requests = set(
            sorted(
                requests,
                key=lambda key: (
                    -requests[key].watermark.occurred_at_epoch,
                    _request_sort_key(key),
                ),
            )[:MAX_CANONICAL_REQUESTS]
        )
        sorted_request_keys = [
            key for key in sorted_request_keys if key in surviving_requests
        ]
    requests = {key: requests[key] for key in sorted_request_keys}

    requests_by_work: dict[WorkKey, list[RequestKey]] = {}
    for request_key in requests:
        requests_by_work.setdefault(request_key.work_key, []).append(request_key)
    for key, work in tuple(works.items()):
        linked = tuple(sorted(requests_by_work.get(key, ()), key=_request_sort_key))
        works[key] = replace(work, request_keys=linked)

    if len(source_watermarks) > MAX_CANONICAL_SOURCES:
        retained_sources = sorted(source_watermarks, key=_source_sort_key)[
            :MAX_CANONICAL_SOURCES
        ]
        diagnostics["canonical_source_limit"] = (
            len(source_watermarks) - MAX_CANONICAL_SOURCES
        )
        source_watermarks = {
            source: source_watermarks[source] for source in retained_sources
        }
    uncertain_sources.intersection_update(
        set(source_watermarks)
        | {work.key.source_key for work in works.values()}
        | {request.key.work_key.source_key for request in requests.values()}
        | {batch.source_key}
    )
    if len(uncertain_sources) > MAX_CANONICAL_SOURCES:
        uncertain_sources = set(
            sorted(uncertain_sources, key=_source_sort_key)[:MAX_CANONICAL_SOURCES]
        )
    source_timing = {
        source: timing
        for source, timing in source_timing.items()
        if source in uncertain_sources
    }

    events = [
        *health_events,
        *(event for key, event in work_events.items() if key in works),
        *(event for key, event in request_events.items() if key in requests),
    ]
    unique_events = {event.key: event for event in events}
    sorted_events = sorted(unique_events.values(), key=_event_sort_key)
    if len(sorted_events) > MAX_EVENTS_PER_REDUCTION:
        diagnostics["canonical_event_limit"] = (
            len(sorted_events) - MAX_EVENTS_PER_REDUCTION
        )
        sorted_events = sorted_events[:MAX_EVENTS_PER_REDUCTION]

    candidate_state = CanonicalOperatorState(
        schema_version=_STATE_SCHEMA_VERSION,
        generation=previous.generation,
        works=tuple(works.values()),
        requests=tuple(requests.values()),
        source_watermarks=tuple(source_watermarks.items()),
        timing_uncertain_sources=tuple(uncertain_sources),
        clock_continuity=decision.continuity,
        last_clock=clock,
        _source_timing=tuple(source_timing.values()),
    )
    state = (
        previous
        if candidate_state == previous
        else replace(candidate_state, generation=previous.generation + 1)
    )

    invalidations: set[InvalidationDomain] = set()
    old_work_map = {work.key: work for work in previous.works}
    new_work_map = {work.key: work for work in state.works}
    if any(
        key not in old_work_map
        or old_work_map[key].lifecycle != work.lifecycle
        or old_work_map[key].parent_key != work.parent_key
        for key, work in new_work_map.items()
    ) or set(old_work_map) != set(new_work_map):
        invalidations.add(InvalidationDomain.LIFECYCLE)
    if any(
        key not in old_work_map
        or old_work_map[key].source_health != work.source_health
        or old_work_map[key].source_freshness != work.source_freshness
        or old_work_map[key].timing_uncertain != work.timing_uncertain
        for key, work in new_work_map.items()
    ) or previous.timing_uncertain_sources != state.timing_uncertain_sources:
        invalidations.add(InvalidationDomain.SOURCE_HEALTH)

    old_request_map = {request.key: request for request in previous.requests}
    new_request_map = {request.key: request for request in state.requests}
    if old_request_map != new_request_map or any(
        old_work_map.get(key) is not None
        and old_work_map[key].request_keys != work.request_keys
        for key, work in new_work_map.items()
    ):
        invalidations.add(InvalidationDomain.MAILBOX)
    if any(
        event.kind in {TransitionKind.COMPLETED, TransitionKind.FAILED}
        for event in sorted_events
    ):
        invalidations.add(InvalidationDomain.COMPLETION)
    if sorted_events:
        invalidations.add(InvalidationDomain.DELIVERY)

    return ReductionResult(
        state=state,
        events=tuple(sorted_events),
        invalidations=frozenset(invalidations),
        diagnostics=_diagnostic_tuple(diagnostics),
    )
