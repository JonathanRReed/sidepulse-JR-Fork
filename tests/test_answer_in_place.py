from __future__ import annotations

import pytest

from sidepulse.announcer_stack import AnnouncerAlertIdentity
from sidepulse.answer_in_place import (
    ANSWER_IN_PLACE_RUNTIME_SURFACE,
    MAX_ANSWER_REPLY_LENGTH,
    AnswerActionKind,
    AnswerAttempt,
    AnswerAttemptState,
    answer_capability_for_request,
    project_answer_controls,
    reconcile_answer_attempt,
    reduce_answer_intent,
)
from sidepulse.provider_contracts import (
    ProductCapability,
    ProductCapabilityInvocation,
    negotiate_provider_contract,
)
from sidepulse.provider_facts import RequestKind


def _version(major: int, minor: int) -> dict[str, int]:
    return {"major": major, "minor": minor}


def _document(
    *,
    product_capabilities: object | None = None,
) -> dict[str, object]:
    return {
        "schema_version": _version(1, 0),
        "provider_id": "codex",
        "adapter_id": "hooks",
        "source_instance_id": "source:local-01",
        "capabilities": [],
        "product_capabilities": [] if product_capabilities is None else product_capabilities,
    }


def _supported_contract() -> object:
    return negotiate_provider_contract(
        _document(
            product_capabilities=[
                {
                    "id": "answering",
                    "supported": True,
                    "binding": {
                        "kind": "local",
                        "id": "local.answer_in_place",
                    },
                }
            ]
        )
    )


def _attempt(
    *,
    state: AnswerAttemptState = AnswerAttemptState.IDLE,
    draft_text: str = "",
    last_error: str | None = None,
    generation: int = 4,
) -> AnswerAttempt:
    return AnswerAttempt(
        request_identity=AnnouncerAlertIdentity("request:test"),
        generation=generation,
        state=state,
        draft_text=draft_text,
        last_error=last_error,
    )


def test_answer_capability_uses_exact_local_surface_for_binary_and_reply_requests() -> None:
    contract = _supported_contract()

    permission = answer_capability_for_request(contract, RequestKind.PERMISSION)
    reply = answer_capability_for_request(contract, RequestKind.INPUT)

    assert permission.supported is True
    assert permission.supports_binary_decision is True
    assert permission.supports_reply_text is False
    assert type(permission.invocation) is ProductCapabilityInvocation
    assert permission.invocation.product_capability is ProductCapability.ANSWERING
    assert permission.invocation.local_runtime_surface == ANSWER_IN_PLACE_RUNTIME_SURFACE
    assert reply.supported is True
    assert reply.supports_binary_decision is False
    assert reply.supports_reply_text is True


def test_answer_capability_fails_closed_for_unknown_request_or_missing_support() -> None:
    unsupported = answer_capability_for_request(None, RequestKind.PERMISSION)
    unknown = answer_capability_for_request(_supported_contract(), RequestKind.UNKNOWN)

    assert unsupported.supported is False
    assert unsupported.disabled_reason == "Jump to session"
    assert unknown.supported is False
    assert unknown.disabled_reason == "Jump to session"


def test_reconcile_answer_attempt_preserves_matching_identity_and_resets_stale_generation() -> None:
    current = _attempt(state=AnswerAttemptState.CANCELLED, draft_text="Need details")

    same = reconcile_answer_attempt(current, current.request_identity, current.generation)
    fresh = reconcile_answer_attempt(current, current.request_identity, current.generation + 1)

    assert same is current
    assert fresh == AnswerAttempt(
        request_identity=current.request_identity,
        generation=current.generation + 1,
        state=AnswerAttemptState.IDLE,
        draft_text="",
        last_error=None,
    )


def test_reduce_answer_intent_normalizes_reply_text_and_transitions_send_retry_cancel() -> None:
    attempt = _attempt()
    raw_text = "  Please\nreview this carefully  " + ("x" * 400)

    sending = reduce_answer_intent(
        attempt,
        AnswerActionKind.REPLY,
        draft_text=raw_text,
    )
    cancelled = reduce_answer_intent(sending, AnswerActionKind.CANCEL)
    retried = reduce_answer_intent(cancelled, AnswerActionKind.RETRY)

    assert sending.state is AnswerAttemptState.SENDING
    assert sending.last_error is None
    assert sending.draft_text.startswith("Please review this carefully")
    assert "\n" not in sending.draft_text
    assert len(sending.draft_text) == MAX_ANSWER_REPLY_LENGTH
    assert cancelled.state is AnswerAttemptState.CANCELLED
    assert cancelled.draft_text == sending.draft_text
    assert retried.state is AnswerAttemptState.SENDING


def test_project_answer_controls_for_binary_idle_state_keeps_jump_available() -> None:
    capability = answer_capability_for_request(_supported_contract(), RequestKind.PERMISSION)

    plan = project_answer_controls(RequestKind.PERMISSION, capability, _attempt())

    assert plan.primary_actions == (
        AnswerActionKind.APPROVE,
        AnswerActionKind.DENY,
        AnswerActionKind.JUMP,
    )
    assert plan.can_send is True
    assert plan.can_cancel is False
    assert plan.status_text is None


def test_project_answer_controls_for_reply_state_tracks_send_and_recovery_states() -> None:
    capability = answer_capability_for_request(_supported_contract(), RequestKind.INPUT)

    idle = project_answer_controls(RequestKind.INPUT, capability, _attempt())
    drafted = project_answer_controls(
        RequestKind.INPUT,
        capability,
        _attempt(draft_text="Ship it"),
    )
    sending = project_answer_controls(
        RequestKind.INPUT,
        capability,
        _attempt(state=AnswerAttemptState.SENDING, draft_text="Ship it"),
    )
    failed = project_answer_controls(
        RequestKind.INPUT,
        capability,
        _attempt(
            state=AnswerAttemptState.FAILED,
            draft_text="Ship it",
            last_error="Provider refused",
        ),
    )
    timed_out = project_answer_controls(
        RequestKind.INPUT,
        capability,
        _attempt(state=AnswerAttemptState.TIMED_OUT, draft_text="Ship it"),
    )
    sent = project_answer_controls(
        RequestKind.INPUT,
        capability,
        _attempt(state=AnswerAttemptState.SENT, draft_text="Ship it"),
    )
    cancelled = project_answer_controls(
        RequestKind.INPUT,
        capability,
        _attempt(state=AnswerAttemptState.CANCELLED, draft_text="Ship it"),
    )

    assert idle.primary_actions == (AnswerActionKind.REPLY, AnswerActionKind.JUMP)
    assert idle.can_edit_reply is True
    assert idle.can_send is False
    assert drafted.can_send is True
    assert sending.primary_actions == (AnswerActionKind.CANCEL, AnswerActionKind.JUMP)
    assert sending.can_cancel is True
    assert sending.status_text == "Sending…"
    assert failed.primary_actions == (AnswerActionKind.RETRY, AnswerActionKind.JUMP)
    assert failed.status_text == "Provider refused"
    assert timed_out.primary_actions == (AnswerActionKind.RETRY, AnswerActionKind.JUMP)
    assert timed_out.status_text == "Timed out"
    assert sent.primary_actions == (AnswerActionKind.JUMP,)
    assert sent.status_text == "Sent, waiting for source confirmation"
    assert cancelled.primary_actions == (AnswerActionKind.REPLY, AnswerActionKind.JUMP)
    assert cancelled.status_text == "Cancelled"


def test_answer_attempt_rejects_non_normalized_draft_text() -> None:
    with pytest.raises(ValueError, match="invalid answer attempt"):
        _attempt(draft_text="two\nlines")


def test_project_answer_controls_fail_closed_when_capability_is_not_supported() -> None:
    unsupported = answer_capability_for_request(None, RequestKind.INPUT)

    plan = project_answer_controls(RequestKind.INPUT, unsupported, _attempt(draft_text="Reply"))

    assert plan.primary_actions == (AnswerActionKind.JUMP,)
    assert plan.can_send is False
    assert plan.can_edit_reply is False
    assert plan.status_text == "Jump to session"
