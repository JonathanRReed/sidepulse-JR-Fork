"""Pure, bounded answer-in-place state and projection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Final

from .announcer_content import _single_line
from .announcer_stack import AnnouncerAlertIdentity
from .provider_contracts import (
    ContractValidationError,
    LocalRuntimeSurfaceIdentifier,
    NegotiatedProviderContract,
    ProductCapability,
    ProductCapabilityInvocation,
)
from .provider_facts import RequestKind

MAX_ANSWER_REPLY_LENGTH: Final[int] = 280
ANSWER_IN_PLACE_RUNTIME_SURFACE: Final[LocalRuntimeSurfaceIdentifier] = (
    LocalRuntimeSurfaceIdentifier("local.answer_in_place")
)
_BINARY_REQUEST_KINDS: Final[frozenset[RequestKind]] = frozenset(
    {
        RequestKind.PERMISSION,
        RequestKind.APPROVAL,
        RequestKind.REVIEW,
    }
)
_STATUS_SENDING: Final[str] = "Sending…"
_STATUS_TIMED_OUT: Final[str] = "Timed out"
_STATUS_CANCELLED: Final[str] = "Cancelled"
_STATUS_WAITING_FOR_SOURCE: Final[str] = "Sent, waiting for source confirmation"
_STATUS_UNSUPPORTED: Final[str] = "Jump to session"


class AnswerActionKind(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    REPLY = "reply"
    CANCEL = "cancel"
    RETRY = "retry"
    JUMP = "jump"


class AnswerAttemptState(str, Enum):
    IDLE = "idle"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


def _bounded_single_line_text(value: str, *, allow_empty: bool) -> str:
    normalized = _single_line(value)
    bounded = normalized[:MAX_ANSWER_REPLY_LENGTH]
    if not bounded.isprintable():
        raise ValueError("invalid answer text")
    if not allow_empty and not bounded:
        raise ValueError("invalid answer text")
    return bounded


@dataclass(frozen=True, slots=True)
class AnswerCapability:
    supported: bool
    supports_reply_text: bool
    supports_binary_decision: bool
    invocation: ProductCapabilityInvocation | None
    disabled_reason: str | None = None

    def __post_init__(self) -> None:
        if not all(
            type(value) is bool
            for value in (
                self.supported,
                self.supports_reply_text,
                self.supports_binary_decision,
            )
        ):
            raise ValueError("invalid answer capability")
        if self.disabled_reason is not None:
            if type(self.disabled_reason) is not str:
                raise ValueError("invalid answer capability")
            object.__setattr__(
                self,
                "disabled_reason",
                _bounded_single_line_text(self.disabled_reason, allow_empty=False),
            )
        if self.invocation is not None and type(self.invocation) is not ProductCapabilityInvocation:
            raise ValueError("invalid answer capability")
        if self.supported:
            if type(self.invocation) is not ProductCapabilityInvocation:
                raise ValueError("invalid answer capability")
            if not (self.supports_reply_text or self.supports_binary_decision):
                raise ValueError("invalid answer capability")
            if self.disabled_reason is not None:
                raise ValueError("invalid answer capability")
            if self.invocation.product_capability is not ProductCapability.ANSWERING:
                raise ValueError("invalid answer capability")
            if self.invocation.local_runtime_surface != ANSWER_IN_PLACE_RUNTIME_SURFACE:
                raise ValueError("invalid answer capability")
            if (
                self.invocation.capability_id is not None
                or self.invocation.capability_version is not None
            ):
                raise ValueError("invalid answer capability")
        else:
            if self.invocation is not None:
                raise ValueError("invalid answer capability")
            if self.supports_reply_text or self.supports_binary_decision:
                raise ValueError("invalid answer capability")


@dataclass(frozen=True, slots=True)
class AnswerAttempt:
    request_identity: AnnouncerAlertIdentity
    generation: int
    state: AnswerAttemptState
    draft_text: str
    last_error: str | None

    def __post_init__(self) -> None:
        if not (
            type(self.request_identity) is AnnouncerAlertIdentity
            and type(self.generation) is int
            and self.generation >= 0
            and type(self.state) is AnswerAttemptState
            and type(self.draft_text) is str
        ):
            raise ValueError("invalid answer attempt")
        normalized_draft = _bounded_single_line_text(self.draft_text, allow_empty=True)
        if self.draft_text != normalized_draft:
            raise ValueError("invalid answer attempt")
        if self.last_error is not None:
            if type(self.last_error) is not str:
                raise ValueError("invalid answer attempt")
            normalized_error = _bounded_single_line_text(
                self.last_error,
                allow_empty=False,
            )
            if self.last_error != normalized_error:
                raise ValueError("invalid answer attempt")


@dataclass(frozen=True, slots=True)
class AnswerControlPlan:
    request_identity: AnnouncerAlertIdentity
    generation: int
    capability: AnswerCapability
    state: AnswerAttemptState
    draft_text: str
    primary_actions: tuple[AnswerActionKind, ...]
    secondary_actions: tuple[AnswerActionKind, ...]
    status_text: str | None
    can_edit_reply: bool
    can_send: bool
    can_cancel: bool

    def __post_init__(self) -> None:
        if not (
            type(self.request_identity) is AnnouncerAlertIdentity
            and type(self.generation) is int
            and self.generation >= 0
            and type(self.capability) is AnswerCapability
            and type(self.state) is AnswerAttemptState
            and type(self.draft_text) is str
            and type(self.primary_actions) is tuple
            and type(self.secondary_actions) is tuple
            and all(type(action) is AnswerActionKind for action in self.primary_actions)
            and all(type(action) is AnswerActionKind for action in self.secondary_actions)
            and len(set(self.primary_actions)) == len(self.primary_actions)
            and len(set(self.secondary_actions)) == len(self.secondary_actions)
            and set(self.primary_actions).isdisjoint(self.secondary_actions)
            and all(
                type(value) is bool
                for value in (self.can_edit_reply, self.can_send, self.can_cancel)
            )
        ):
            raise ValueError("invalid answer control plan")
        normalized_draft = _bounded_single_line_text(self.draft_text, allow_empty=True)
        if self.draft_text != normalized_draft:
            raise ValueError("invalid answer control plan")
        if self.status_text is not None:
            if type(self.status_text) is not str:
                raise ValueError("invalid answer control plan")
            normalized_status = _bounded_single_line_text(
                self.status_text,
                allow_empty=False,
            )
            if self.status_text != normalized_status:
                raise ValueError("invalid answer control plan")


def _unsupported_capability(reason: str = _STATUS_UNSUPPORTED) -> AnswerCapability:
    return AnswerCapability(
        supported=False,
        supports_reply_text=False,
        supports_binary_decision=False,
        invocation=None,
        disabled_reason=reason,
    )


def answer_capability_for_request(
    contract: NegotiatedProviderContract | None,
    request_kind: RequestKind,
) -> AnswerCapability:
    if type(request_kind) is not RequestKind:
        return _unsupported_capability()
    if type(contract) is not NegotiatedProviderContract:
        return _unsupported_capability()
    if request_kind is RequestKind.UNKNOWN:
        return _unsupported_capability()
    declaration = contract.product_capability(ProductCapability.ANSWERING)
    if not declaration.supported or declaration.binding is None:
        return _unsupported_capability()
    try:
        invocation = contract.product_invocation_for(ProductCapability.ANSWERING)
    except ContractValidationError:
        return _unsupported_capability()
    supports_reply_text = request_kind is RequestKind.INPUT
    supports_binary_decision = request_kind in _BINARY_REQUEST_KINDS
    if not (supports_reply_text or supports_binary_decision):
        return _unsupported_capability()
    if invocation.local_runtime_surface != ANSWER_IN_PLACE_RUNTIME_SURFACE:
        return _unsupported_capability()
    return AnswerCapability(
        supported=True,
        supports_reply_text=supports_reply_text,
        supports_binary_decision=supports_binary_decision,
        invocation=invocation,
    )


def reconcile_answer_attempt(
    previous: AnswerAttempt | None,
    request_identity: AnnouncerAlertIdentity,
    generation: int,
) -> AnswerAttempt:
    if type(request_identity) is not AnnouncerAlertIdentity:
        raise ValueError("invalid answer attempt identity")
    if type(generation) is not int or generation < 0:
        raise ValueError("invalid answer attempt generation")
    if (
        type(previous) is AnswerAttempt
        and previous.request_identity == request_identity
        and previous.generation == generation
    ):
        return previous
    return AnswerAttempt(
        request_identity=request_identity,
        generation=generation,
        state=AnswerAttemptState.IDLE,
        draft_text="",
        last_error=None,
    )


def reduce_answer_intent(
    attempt: AnswerAttempt,
    action: AnswerActionKind,
    draft_text: str | None = None,
) -> AnswerAttempt:
    if type(attempt) is not AnswerAttempt:
        raise ValueError("invalid answer attempt")
    if type(action) is not AnswerActionKind:
        return attempt
    next_attempt = attempt
    if draft_text is not None:
        if type(draft_text) is not str:
            return attempt
        next_attempt = replace(
            next_attempt,
            draft_text=_bounded_single_line_text(draft_text, allow_empty=True),
        )
    if action in {
        AnswerActionKind.APPROVE,
        AnswerActionKind.DENY,
        AnswerActionKind.REPLY,
    }:
        return replace(next_attempt, state=AnswerAttemptState.SENDING, last_error=None)
    if action is AnswerActionKind.CANCEL:
        if next_attempt.state is not AnswerAttemptState.SENDING:
            return next_attempt
        return replace(next_attempt, state=AnswerAttemptState.CANCELLED, last_error=None)
    if action is AnswerActionKind.RETRY:
        if next_attempt.state not in {
            AnswerAttemptState.FAILED,
            AnswerAttemptState.TIMED_OUT,
            AnswerAttemptState.CANCELLED,
        }:
            return next_attempt
        return replace(next_attempt, state=AnswerAttemptState.SENDING, last_error=None)
    return next_attempt


def project_answer_controls(
    request_kind: RequestKind,
    capability: AnswerCapability,
    attempt: AnswerAttempt,
) -> AnswerControlPlan:
    if (
        type(request_kind) is not RequestKind
        or type(capability) is not AnswerCapability
        or type(attempt) is not AnswerAttempt
    ):
        raise ValueError("invalid answer control projection")
    if (
        not capability.supported
        or request_kind is RequestKind.UNKNOWN
        or (
            request_kind is RequestKind.INPUT
            and not capability.supports_reply_text
        )
        or (
            request_kind in _BINARY_REQUEST_KINDS
            and not capability.supports_binary_decision
        )
    ):
        return AnswerControlPlan(
            request_identity=attempt.request_identity,
            generation=attempt.generation,
            capability=capability,
            state=attempt.state,
            draft_text=attempt.draft_text,
            primary_actions=(AnswerActionKind.JUMP,),
            secondary_actions=(),
            status_text=capability.disabled_reason,
            can_edit_reply=False,
            can_send=False,
            can_cancel=False,
        )
    if attempt.state is AnswerAttemptState.SENDING:
        return AnswerControlPlan(
            request_identity=attempt.request_identity,
            generation=attempt.generation,
            capability=capability,
            state=attempt.state,
            draft_text=attempt.draft_text,
            primary_actions=(AnswerActionKind.CANCEL, AnswerActionKind.JUMP),
            secondary_actions=(),
            status_text=_STATUS_SENDING,
            can_edit_reply=False,
            can_send=False,
            can_cancel=True,
        )
    if attempt.state is AnswerAttemptState.FAILED:
        return AnswerControlPlan(
            request_identity=attempt.request_identity,
            generation=attempt.generation,
            capability=capability,
            state=attempt.state,
            draft_text=attempt.draft_text,
            primary_actions=(AnswerActionKind.RETRY, AnswerActionKind.JUMP),
            secondary_actions=(),
            status_text=attempt.last_error or "Send failed",
            can_edit_reply=False,
            can_send=False,
            can_cancel=False,
        )
    if attempt.state is AnswerAttemptState.TIMED_OUT:
        return AnswerControlPlan(
            request_identity=attempt.request_identity,
            generation=attempt.generation,
            capability=capability,
            state=attempt.state,
            draft_text=attempt.draft_text,
            primary_actions=(AnswerActionKind.RETRY, AnswerActionKind.JUMP),
            secondary_actions=(),
            status_text=attempt.last_error or _STATUS_TIMED_OUT,
            can_edit_reply=False,
            can_send=False,
            can_cancel=False,
        )
    if attempt.state is AnswerAttemptState.SENT:
        return AnswerControlPlan(
            request_identity=attempt.request_identity,
            generation=attempt.generation,
            capability=capability,
            state=attempt.state,
            draft_text=attempt.draft_text,
            primary_actions=(AnswerActionKind.JUMP,),
            secondary_actions=(),
            status_text=_STATUS_WAITING_FOR_SOURCE,
            can_edit_reply=False,
            can_send=False,
            can_cancel=False,
        )
    if request_kind in _BINARY_REQUEST_KINDS:
        return AnswerControlPlan(
            request_identity=attempt.request_identity,
            generation=attempt.generation,
            capability=capability,
            state=attempt.state,
            draft_text=attempt.draft_text,
            primary_actions=(
                AnswerActionKind.APPROVE,
                AnswerActionKind.DENY,
                AnswerActionKind.JUMP,
            ),
            secondary_actions=(),
            status_text=(
                _STATUS_CANCELLED
                if attempt.state is AnswerAttemptState.CANCELLED
                else None
            ),
            can_edit_reply=False,
            can_send=True,
            can_cancel=False,
        )
    return AnswerControlPlan(
        request_identity=attempt.request_identity,
        generation=attempt.generation,
        capability=capability,
        state=attempt.state,
        draft_text=attempt.draft_text,
        primary_actions=(AnswerActionKind.REPLY, AnswerActionKind.JUMP),
        secondary_actions=(),
        status_text=(
            _STATUS_CANCELLED
            if attempt.state is AnswerAttemptState.CANCELLED
            else None
        ),
        can_edit_reply=True,
        can_send=bool(attempt.draft_text),
        can_cancel=False,
    )


__all__ = [
    "ANSWER_IN_PLACE_RUNTIME_SURFACE",
    "MAX_ANSWER_REPLY_LENGTH",
    "AnswerActionKind",
    "AnswerAttempt",
    "AnswerAttemptState",
    "AnswerCapability",
    "AnswerControlPlan",
    "answer_capability_for_request",
    "project_answer_controls",
    "reconcile_answer_attempt",
    "reduce_answer_intent",
]
