"""Pure, exact presentation receipts for the Clear Agents action.

The types in this module contain no transcript, prompt, path, or credential
content.  A receipt acknowledges one source-bound completion event.  It never
changes the canonical agent or operator state that produced that event.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum

from .capacity_types import SourceKey
from .models import AgentMode, AgentStatus, provider_label
from .provider_facts import WorkKey

MAX_AGENT_ID_LENGTH = 512
MAX_EVENT_NAME_LENGTH = 96
MAX_BATCH_ID_LENGTH = 128
MAX_PRESENTATION_ROWS = 2_048
MAX_COMPLETION_RECEIPTS = 1_024
MAX_CLEAR_TARGETS = 512
MAX_PREVIEW_ITEMS = 20
UNDO_WINDOW_SECONDS = 5 * 60.0

PRESERVATION_FACTS = (
    "History and transcripts stay.",
    "Hooks, credentials, settings, and Other Macs stay.",
    "Live asks, failures, queued work, and active agents stay.",
)


class ClearAgentsRefusal(str, Enum):
    INVALID = "invalid"
    EMPTY = "empty"
    STALE_PREVIEW = "stale_preview"
    WRONG_BATCH = "wrong_batch"
    EXPIRED = "expired"
    REPEATED = "repeated"
    STALE_UNDO = "stale_undo"


class ClearAgentsPlanError(ValueError):
    """A bounded, content-free refusal suitable for controller branching."""

    def __init__(self, reason: ClearAgentsRefusal) -> None:
        if type(reason) is not ClearAgentsRefusal:
            raise TypeError("reason must be ClearAgentsRefusal")
        self.reason = reason
        super().__init__(reason.value)


def _finite_nonnegative(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0.0


def _valid_generation(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


def _valid_identity_text(value: object, *, limit: int) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= limit
        and value.isprintable()
        and "\x00" not in value
        and "\n" not in value
        and "\r" not in value
        and "/" not in value
        and "\\" not in value
    )


def _as_epoch(value: datetime) -> float:
    if type(value) is not datetime:
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    try:
        epoch = value.timestamp()
    except (OverflowError, OSError, ValueError) as error:
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID) from error
    if not _finite_nonnegative(epoch):
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)
    return float(epoch)


@dataclass(frozen=True, order=True, slots=True)
class CompletionPresentationKey:
    source_key: SourceKey
    agent_id: str
    event_name: str
    completed_at_epoch: float

    def __post_init__(self) -> None:
        if not (
            type(self.source_key) is SourceKey
            and _valid_identity_text(self.agent_id, limit=MAX_AGENT_ID_LENGTH)
            and _valid_identity_text(self.event_name, limit=MAX_EVENT_NAME_LENGTH)
            and self.event_name != "SessionEnd"
            and _finite_nonnegative(self.completed_at_epoch)
        ):
            raise ValueError("invalid completion presentation key")
        object.__setattr__(self, "completed_at_epoch", float(self.completed_at_epoch))


@dataclass(frozen=True, slots=True)
class CompletionPresentationReceipt:
    key: CompletionPresentationKey
    acknowledged_at_epoch: float

    def __post_init__(self) -> None:
        if not (
            type(self.key) is CompletionPresentationKey
            and _finite_nonnegative(self.acknowledged_at_epoch)
        ):
            raise ValueError("invalid completion presentation receipt")
        object.__setattr__(
            self, "acknowledged_at_epoch", float(self.acknowledged_at_epoch)
        )


@dataclass(frozen=True, slots=True)
class ClearAgentsBatchReceipt:
    batch_id: str
    newly_added_keys: tuple[CompletionPresentationKey, ...]
    committed_at_epoch: float
    undo_deadline_epoch: float
    commit_generation: int
    undone: bool = False

    def __post_init__(self) -> None:
        if not (
            _valid_identity_text(self.batch_id, limit=MAX_BATCH_ID_LENGTH)
            and type(self.newly_added_keys) is tuple
            and 1 <= len(self.newly_added_keys) <= MAX_CLEAR_TARGETS
            and all(
                type(key) is CompletionPresentationKey
                for key in self.newly_added_keys
            )
            and tuple(sorted(set(self.newly_added_keys))) == self.newly_added_keys
            and _finite_nonnegative(self.committed_at_epoch)
            and _finite_nonnegative(self.undo_deadline_epoch)
            and self.undo_deadline_epoch - self.committed_at_epoch
            == UNDO_WINDOW_SECONDS
            and _valid_generation(self.commit_generation)
            and self.commit_generation > 0
            and type(self.undone) is bool
        ):
            raise ValueError("invalid Clear Agents batch receipt")
        object.__setattr__(self, "committed_at_epoch", float(self.committed_at_epoch))
        object.__setattr__(
            self, "undo_deadline_epoch", float(self.undo_deadline_epoch)
        )


@dataclass(frozen=True, slots=True)
class ClearAgentsState:
    generation: int = 0
    receipts: tuple[CompletionPresentationReceipt, ...] = ()
    latest_batch: ClearAgentsBatchReceipt | None = None

    def __post_init__(self) -> None:
        if not (
            _valid_generation(self.generation)
            and type(self.receipts) is tuple
            and len(self.receipts) <= MAX_COMPLETION_RECEIPTS
            and all(
                type(receipt) is CompletionPresentationReceipt
                for receipt in self.receipts
            )
            and tuple(sorted(self.receipts, key=lambda receipt: receipt.key))
            == self.receipts
            and len({receipt.key for receipt in self.receipts}) == len(self.receipts)
            and (
                self.latest_batch is None
                or type(self.latest_batch) is ClearAgentsBatchReceipt
            )
        ):
            raise ValueError("invalid Clear Agents state")
        if self.latest_batch is not None:
            batch_keys = set(self.latest_batch.newly_added_keys)
            receipt_keys = {receipt.key for receipt in self.receipts}
            batch_membership_is_valid = (
                batch_keys.isdisjoint(receipt_keys)
                if self.latest_batch.undone
                else batch_keys.issubset(receipt_keys)
            )
            if (
                self.latest_batch.commit_generation > self.generation
                or not batch_membership_is_valid
            ):
                raise ValueError("invalid Clear Agents state batch")

    @property
    def acknowledged_keys(self) -> frozenset[CompletionPresentationKey]:
        return frozenset(receipt.key for receipt in self.receipts)


@dataclass(frozen=True, slots=True)
class ClearAgentsProtectedSignature:
    source_key: SourceKey | None
    agent_id: str
    mode: AgentMode
    event_name: str
    updated_at_epoch: float
    is_remote: bool

    def __post_init__(self) -> None:
        if not (
            (self.source_key is None or type(self.source_key) is SourceKey)
            and _valid_identity_text(self.agent_id, limit=MAX_AGENT_ID_LENGTH)
            and type(self.mode) is AgentMode
            and _valid_identity_text(self.event_name, limit=MAX_EVENT_NAME_LENGTH)
            and _finite_nonnegative(self.updated_at_epoch)
            and type(self.is_remote) is bool
        ):
            raise ValueError("invalid protected presentation signature")
        object.__setattr__(self, "updated_at_epoch", float(self.updated_at_epoch))


@dataclass(frozen=True, slots=True)
class ClearAgentsFence:
    state_generation: int
    clearable_keys: tuple[CompletionPresentationKey, ...]
    protected_signatures: tuple[ClearAgentsProtectedSignature, ...]

    def __post_init__(self) -> None:
        if not (
            _valid_generation(self.state_generation)
            and type(self.clearable_keys) is tuple
            and len(self.clearable_keys) <= MAX_CLEAR_TARGETS
            and all(
                type(key) is CompletionPresentationKey for key in self.clearable_keys
            )
            and tuple(sorted(set(self.clearable_keys))) == self.clearable_keys
            and type(self.protected_signatures) is tuple
            and len(self.protected_signatures) <= MAX_PRESENTATION_ROWS
            and all(
                type(signature) is ClearAgentsProtectedSignature
                for signature in self.protected_signatures
            )
            and tuple(
                sorted(self.protected_signatures, key=_protected_signature_sort_key)
            )
            == self.protected_signatures
        ):
            raise ValueError("invalid Clear Agents fence")


@dataclass(frozen=True, slots=True)
class ClearAgentsPreviewItem:
    key: CompletionPresentationKey
    safe_label: str

    def __post_init__(self) -> None:
        if not (
            type(self.key) is CompletionPresentationKey
            and _valid_preview_label(self.safe_label)
        ):
            raise ValueError("invalid Clear Agents preview item")


@dataclass(frozen=True, slots=True)
class ClearAgentsProtectedCounts:
    active: int = 0
    waiting: int = 0
    failed: int = 0
    queued: int = 0
    remote_completions: int = 0
    unkeyed_local_completions: int = 0
    other: int = 0

    def __post_init__(self) -> None:
        if not all(
            type(value) is int and 0 <= value <= MAX_PRESENTATION_ROWS
            for value in (
                self.active,
                self.waiting,
                self.failed,
                self.queued,
                self.remote_completions,
                self.unkeyed_local_completions,
                self.other,
            )
        ):
            raise ValueError("invalid protected counts")

    @property
    def total(self) -> int:
        return sum(
            (
                self.active,
                self.waiting,
                self.failed,
                self.queued,
                self.remote_completions,
                self.unkeyed_local_completions,
                self.other,
            )
        )


@dataclass(frozen=True, slots=True)
class ClearAgentsPreview:
    fence: ClearAgentsFence
    items: tuple[ClearAgentsPreviewItem, ...]
    clearable_count: int
    hidden_item_count: int
    protected_counts: ClearAgentsProtectedCounts
    preservation_facts: tuple[str, ...] = PRESERVATION_FACTS

    def __post_init__(self) -> None:
        if not (
            type(self.fence) is ClearAgentsFence
            and type(self.items) is tuple
            and len(self.items) <= MAX_PREVIEW_ITEMS
            and all(type(item) is ClearAgentsPreviewItem for item in self.items)
            and type(self.clearable_count) is int
            and self.clearable_count == len(self.fence.clearable_keys)
            and type(self.hidden_item_count) is int
            and self.hidden_item_count == self.clearable_count - len(self.items)
            and self.hidden_item_count >= 0
            and type(self.protected_counts) is ClearAgentsProtectedCounts
            and self.preservation_facts == PRESERVATION_FACTS
        ):
            raise ValueError("invalid Clear Agents preview")

    @property
    def clearable_keys(self) -> tuple[CompletionPresentationKey, ...]:
        return self.fence.clearable_keys


@dataclass(frozen=True, slots=True)
class ClearAgentsCommitPlan:
    previous_state: ClearAgentsState
    next_state: ClearAgentsState
    batch_receipt: ClearAgentsBatchReceipt

    def __post_init__(self) -> None:
        if not (
            type(self.previous_state) is ClearAgentsState
            and type(self.next_state) is ClearAgentsState
            and type(self.batch_receipt) is ClearAgentsBatchReceipt
            and self.next_state.latest_batch == self.batch_receipt
            and self.next_state.generation == self.previous_state.generation + 1
        ):
            raise ValueError("invalid Clear Agents commit plan")

    @property
    def cleared_count(self) -> int:
        return len(self.batch_receipt.newly_added_keys)


@dataclass(frozen=True, slots=True)
class ClearAgentsUndoPlan:
    previous_state: ClearAgentsState
    next_state: ClearAgentsState
    batch_receipt: ClearAgentsBatchReceipt

    def __post_init__(self) -> None:
        if not (
            type(self.previous_state) is ClearAgentsState
            and type(self.next_state) is ClearAgentsState
            and type(self.batch_receipt) is ClearAgentsBatchReceipt
            and self.next_state.latest_batch == self.batch_receipt
            and self.batch_receipt.undone
            and self.next_state.generation == self.previous_state.generation + 1
        ):
            raise ValueError("invalid Clear Agents Undo plan")

    @property
    def restored_count(self) -> int:
        return len(self.batch_receipt.newly_added_keys)


def _valid_preview_label(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 80
        and value.isprintable()
        and "\x00" not in value
        and "\n" not in value
        and "\r" not in value
        and "/" not in value
        and "\\" not in value
    )


def _protected_signature_sort_key(
    signature: ClearAgentsProtectedSignature,
) -> tuple[str, str, str, str, str, str, float]:
    source = signature.source_key
    return (
        "1" if signature.is_remote else "0",
        source.provider_id if source is not None else "",
        source.adapter_id if source is not None else "",
        source.source_instance_id if source is not None else "",
        signature.agent_id,
        f"{signature.mode.value}:{signature.event_name}",
        signature.updated_at_epoch,
    )


def _exact_completion_key(status: AgentStatus) -> CompletionPresentationKey | None:
    if (
        type(status) is not AgentStatus
        or status.mode is not AgentMode.COMPLETED
        or status.event_name == "SessionEnd"
        or type(status.work_key) is not WorkKey
        or type(status.work_key.source_key) is not SourceKey
        or status.provider != status.work_key.source_key.provider_id
        or not _valid_identity_text(status.agent_id, limit=MAX_AGENT_ID_LENGTH)
        or not _valid_identity_text(status.event_name, limit=MAX_EVENT_NAME_LENGTH)
    ):
        return None
    return CompletionPresentationKey(
        source_key=status.work_key.source_key,
        agent_id=status.agent_id,
        event_name=status.event_name,
        completed_at_epoch=_as_epoch(status.updated_at),
    )


def completion_presentation_key(
    status: AgentStatus,
) -> CompletionPresentationKey | None:
    """Return an exact clearable key, or ``None`` when identity is unsafe."""

    return _exact_completion_key(status)


def _safe_preview_label(status: AgentStatus) -> str:
    label = status.display_name.strip() if type(status.display_name) is str else ""
    if _valid_preview_label(label):
        return label
    fallback = provider_label(status.provider) if type(status.provider) is str else "Agent"
    return fallback if _valid_preview_label(fallback) else "Agent"


def _is_remote(status: AgentStatus) -> bool:
    return status.agent_id.startswith("remote:")


def _protected_signature(
    status: AgentStatus, *, is_remote: bool
) -> ClearAgentsProtectedSignature:
    if type(status) is not AgentStatus:
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)
    source_key = (
        status.work_key.source_key
        if type(status.work_key) is WorkKey
        and type(status.work_key.source_key) is SourceKey
        else None
    )
    return ClearAgentsProtectedSignature(
        source_key=source_key,
        agent_id=status.agent_id,
        mode=status.mode,
        event_name=status.event_name,
        updated_at_epoch=_as_epoch(status.updated_at),
        is_remote=is_remote,
    )


def _bounded_statuses(statuses: Iterable[AgentStatus]) -> tuple[AgentStatus, ...]:
    try:
        rows = tuple(statuses)
    except (TypeError, MemoryError) as error:
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID) from error
    if len(rows) > MAX_PRESENTATION_ROWS or not all(
        type(status) is AgentStatus for status in rows
    ):
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)
    return rows


def _counts_for(
    protected: tuple[tuple[AgentStatus, bool, bool], ...]
) -> ClearAgentsProtectedCounts:
    counts = {
        "active": 0,
        "waiting": 0,
        "failed": 0,
        "queued": 0,
        "remote_completions": 0,
        "unkeyed_local_completions": 0,
        "other": 0,
    }
    active_modes = {
        AgentMode.WORKING,
        AgentMode.TOOL_RUNNING,
        AgentMode.LONG_TASK_PROGRESS,
    }
    for status, is_remote, is_queued in protected:
        if is_remote and status.mode is AgentMode.COMPLETED:
            counts["remote_completions"] += 1
        elif is_queued:
            counts["queued"] += 1
        elif (
            not is_remote
            and status.mode is AgentMode.COMPLETED
            and status.event_name != "SessionEnd"
            and _exact_completion_key(status) is None
        ):
            counts["unkeyed_local_completions"] += 1
        elif status.mode in active_modes:
            counts["active"] += 1
        elif status.mode is AgentMode.WAITING_FOR_INPUT:
            counts["waiting"] += 1
        elif status.mode is AgentMode.BLOCKED_ERROR:
            counts["failed"] += 1
        else:
            counts["other"] += 1
    return ClearAgentsProtectedCounts(**counts)


def project_clear_agents_preview(
    statuses: Iterable[AgentStatus],
    *,
    state: ClearAgentsState,
    now_epoch: float,
    protected_statuses: Iterable[AgentStatus] = (),
    queued_agent_ids: Iterable[str] = (),
) -> ClearAgentsPreview:
    """Project exact clear targets and a semantic stale-confirmation fence.

    ``statuses`` are the already-reviewed local presentation candidates.
    ``protected_statuses`` are rows, including remote rows, that the caller has
    already excluded from local clearing.  Queue identity is explicit because
    ``AgentMode`` intentionally has no inferred queued state.
    """

    if type(state) is not ClearAgentsState or not _finite_nonnegative(now_epoch):
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)
    local_rows = _bounded_statuses(statuses)
    forced_protected_rows = _bounded_statuses(protected_statuses)
    try:
        queued_ids = frozenset(queued_agent_ids)
    except (TypeError, MemoryError) as error:
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID) from error
    if len(queued_ids) > MAX_PRESENTATION_ROWS or not all(
        _valid_identity_text(value, limit=MAX_AGENT_ID_LENGTH) for value in queued_ids
    ):
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)

    acknowledged = state.acknowledged_keys
    targets: dict[CompletionPresentationKey, AgentStatus] = {}
    protected: list[tuple[AgentStatus, bool, bool]] = []
    for status in local_rows:
        remote = _is_remote(status)
        queued = status.agent_id in queued_ids
        key = None if remote or queued else _exact_completion_key(status)
        if key is not None:
            if key not in acknowledged:
                targets.setdefault(key, status)
            continue
        protected.append((status, remote, queued))
    protected.extend(
        (
            status,
            _is_remote(status),
            status.agent_id in queued_ids,
        )
        for status in forced_protected_rows
    )
    if len(targets) > MAX_CLEAR_TARGETS:
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)

    keys = tuple(sorted(targets))
    display_keys = tuple(
        sorted(
            keys,
            key=lambda key: (
                -key.completed_at_epoch,
                key.source_key,
                key.agent_id,
                key.event_name,
            ),
        )
    )
    items = tuple(
        ClearAgentsPreviewItem(key=key, safe_label=_safe_preview_label(targets[key]))
        for key in display_keys[:MAX_PREVIEW_ITEMS]
    )
    signatures = tuple(
        sorted(
            (
                _protected_signature(status, is_remote=remote)
                for status, remote, _queued in protected
            ),
            key=_protected_signature_sort_key,
        )
    )
    fence = ClearAgentsFence(
        state_generation=state.generation,
        clearable_keys=keys,
        protected_signatures=signatures,
    )
    return ClearAgentsPreview(
        fence=fence,
        items=items,
        clearable_count=len(keys),
        hidden_item_count=len(keys) - len(items),
        protected_counts=_counts_for(tuple(protected)),
    )


def _latest_live_undo_keys(
    state: ClearAgentsState, *, now_epoch: float
) -> frozenset[CompletionPresentationKey]:
    batch = state.latest_batch
    if (
        batch is None
        or batch.undone
        or state.generation != batch.commit_generation
        or now_epoch < batch.committed_at_epoch
        or now_epoch > batch.undo_deadline_epoch
    ):
        return frozenset()
    return frozenset(batch.newly_added_keys)


def _retained_receipts_after_commit(
    current_state: ClearAgentsState,
    additions: tuple[CompletionPresentationReceipt, ...],
    *,
    current_target_keys: tuple[CompletionPresentationKey, ...],
    committed_at_epoch: float,
) -> tuple[CompletionPresentationReceipt, ...]:
    """Retain protected receipts, then the newest bounded acknowledgements."""

    combined = {
        receipt.key: receipt for receipt in (*current_state.receipts, *additions)
    }
    protected_keys = frozenset(current_target_keys) | _latest_live_undo_keys(
        current_state,
        now_epoch=committed_at_epoch,
    )
    protected = tuple(
        combined[key] for key in sorted(protected_keys) if key in combined
    )
    if len(protected) > MAX_COMPLETION_RECEIPTS:
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)
    remaining = tuple(
        receipt
        for receipt in combined.values()
        if receipt.key not in protected_keys
    )
    newest = tuple(
        sorted(
            remaining,
            key=lambda receipt: (receipt.acknowledged_at_epoch, receipt.key),
            reverse=True,
        )[: MAX_COMPLETION_RECEIPTS - len(protected)]
    )
    return tuple(sorted((*protected, *newest), key=lambda receipt: receipt.key))


def plan_clear_agents_commit(
    preview: ClearAgentsPreview,
    freshly_projected_preview: ClearAgentsPreview,
    current_state: ClearAgentsState,
    *,
    batch_id: str,
    committed_at_epoch: float,
) -> ClearAgentsCommitPlan:
    """Plan one atomic receipt-overlay commit after authoritative reprojection."""

    if not (
        type(preview) is ClearAgentsPreview
        and type(freshly_projected_preview) is ClearAgentsPreview
        and type(current_state) is ClearAgentsState
        and _valid_identity_text(batch_id, limit=MAX_BATCH_ID_LENGTH)
        and _finite_nonnegative(committed_at_epoch)
    ):
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)
    if preview.fence != freshly_projected_preview.fence:
        raise ClearAgentsPlanError(ClearAgentsRefusal.STALE_PREVIEW)
    if current_state.generation != preview.fence.state_generation:
        raise ClearAgentsPlanError(ClearAgentsRefusal.STALE_PREVIEW)
    if not preview.clearable_keys:
        raise ClearAgentsPlanError(ClearAgentsRefusal.EMPTY)

    acknowledged = current_state.acknowledged_keys
    new_keys = tuple(key for key in preview.clearable_keys if key not in acknowledged)
    if not new_keys:
        raise ClearAgentsPlanError(ClearAgentsRefusal.EMPTY)
    next_generation = current_state.generation + 1
    if not _valid_generation(next_generation):
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)
    committed_at = float(committed_at_epoch)
    batch = ClearAgentsBatchReceipt(
        batch_id=batch_id,
        newly_added_keys=new_keys,
        committed_at_epoch=committed_at,
        undo_deadline_epoch=committed_at + UNDO_WINDOW_SECONDS,
        commit_generation=next_generation,
    )
    additions = tuple(
        CompletionPresentationReceipt(
            key=key,
            acknowledged_at_epoch=committed_at,
        )
        for key in new_keys
    )
    retained_receipts = _retained_receipts_after_commit(
        current_state,
        additions,
        current_target_keys=freshly_projected_preview.clearable_keys,
        committed_at_epoch=committed_at,
    )
    if not set(new_keys).issubset({receipt.key for receipt in retained_receipts}):
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)
    next_state = ClearAgentsState(
        generation=next_generation,
        receipts=retained_receipts,
        latest_batch=batch,
    )
    return ClearAgentsCommitPlan(current_state, next_state, batch)


def plan_clear_agents_undo(
    current_state: ClearAgentsState,
    *,
    batch_id: str,
    now_epoch: float,
) -> ClearAgentsUndoPlan:
    """Remove only receipts added by the latest still-current Clear batch."""

    if not (
        type(current_state) is ClearAgentsState
        and _valid_identity_text(batch_id, limit=MAX_BATCH_ID_LENGTH)
        and _finite_nonnegative(now_epoch)
    ):
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)
    batch = current_state.latest_batch
    if batch is None or batch.batch_id != batch_id:
        raise ClearAgentsPlanError(ClearAgentsRefusal.WRONG_BATCH)
    if batch.undone:
        raise ClearAgentsPlanError(ClearAgentsRefusal.REPEATED)
    if current_state.generation != batch.commit_generation:
        raise ClearAgentsPlanError(ClearAgentsRefusal.STALE_UNDO)
    if float(now_epoch) < batch.committed_at_epoch:
        raise ClearAgentsPlanError(ClearAgentsRefusal.STALE_UNDO)
    if float(now_epoch) > batch.undo_deadline_epoch:
        raise ClearAgentsPlanError(ClearAgentsRefusal.EXPIRED)
    receipt_keys = current_state.acknowledged_keys
    if not set(batch.newly_added_keys).issubset(receipt_keys):
        raise ClearAgentsPlanError(ClearAgentsRefusal.STALE_UNDO)
    next_generation = current_state.generation + 1
    if not _valid_generation(next_generation):
        raise ClearAgentsPlanError(ClearAgentsRefusal.INVALID)
    restored = frozenset(batch.newly_added_keys)
    undone_batch = replace(batch, undone=True)
    next_state = ClearAgentsState(
        generation=next_generation,
        receipts=tuple(
            receipt for receipt in current_state.receipts if receipt.key not in restored
        ),
        latest_batch=undone_batch,
    )
    return ClearAgentsUndoPlan(current_state, next_state, undone_batch)


__all__ = [
    "MAX_AGENT_ID_LENGTH",
    "MAX_BATCH_ID_LENGTH",
    "MAX_CLEAR_TARGETS",
    "MAX_COMPLETION_RECEIPTS",
    "MAX_EVENT_NAME_LENGTH",
    "MAX_PRESENTATION_ROWS",
    "MAX_PREVIEW_ITEMS",
    "PRESERVATION_FACTS",
    "UNDO_WINDOW_SECONDS",
    "ClearAgentsBatchReceipt",
    "ClearAgentsCommitPlan",
    "ClearAgentsFence",
    "ClearAgentsPlanError",
    "ClearAgentsPreview",
    "ClearAgentsPreviewItem",
    "ClearAgentsProtectedCounts",
    "ClearAgentsProtectedSignature",
    "ClearAgentsRefusal",
    "ClearAgentsState",
    "ClearAgentsUndoPlan",
    "CompletionPresentationKey",
    "CompletionPresentationReceipt",
    "completion_presentation_key",
    "plan_clear_agents_commit",
    "plan_clear_agents_undo",
    "project_clear_agents_preview",
]
