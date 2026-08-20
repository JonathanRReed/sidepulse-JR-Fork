from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.models import HookEvent
from sidepulse.operator_state import (
    BootIdentifier,
    ClockSample,
    TransitionKind,
    empty_operator_state,
    reduce_operator_state,
)
from sidepulse.provider_adapters import (
    InertProviderRecord,
    NormalizedProviderRecord,
    NotificationKind,
    ProviderEventName,
    minimize_hook_event,
    normalized_provider_record_from_payload,
    normalized_provider_record_to_payload,
    provider_facts_for_record,
)
from sidepulse.provider_contracts import negotiate_provider_contract
from sidepulse.provider_facts import (
    NextActor,
    ObservationAuthority,
    ProviderRequestState,
    ProviderTerminalCause,
    RequestKind,
    SourceFreshness,
    SourceHealth,
    WorkLifecycle,
)

_EPOCH = 1_800_000_000.0

_OFFICIAL_CURSOR_STOP = {
    "conversation_id": "2db7c731-6f6c-4a60-b92c-6c7af563341f",
    "generation_id": "5a65e8d5-cd1f-4a4a-883b-dd5d17dc7862",
    "model": "claude-4-sonnet",
    "model_id": "claude-4-sonnet",
    "model_params": [{"id": "temperature", "value": "0.2"}],
    "hook_event_name": "stop",
    "cursor_version": "1.7.8",
    "workspace_roots": [
        "/Users/private-company/alpha",
        "/Users/private-company/beta",
    ],
    "user_email": "private-account@example.test",
    "transcript_path": "/Users/private-company/transcripts/cursor.jsonl",
    "status": "completed",
    "loop_count": 0,
}

_OFFICIAL_HERMES_TURN_END = {
    "hook_event_name": "on_session_end",
    "tool_name": None,
    "tool_input": None,
    "session_id": "sess_abc123",
    "cwd": "/Users/private-company/hermes",
    "extra": {
        "task_id": "task-private-01",
        "turn_id": "turn_abc123",
        "completed": True,
        "failed": False,
        "interrupted": False,
        "turn_exit_reason": "text_response(stop)",
        "model": "claude-sonnet-4-6",
        "platform": "cli",
    },
}


def _official_event(
    provider: str,
    event_name: str,
    payload: dict[str, object],
) -> HookEvent:
    return HookEvent(
        provider=provider,
        logged_at=datetime.fromtimestamp(_EPOCH, UTC),
        event_name=event_name,
        raw=payload,
        session_id=(
            payload.get("session_id")
            if type(payload.get("session_id")) is str
            else None
        ),
    )


def _source(
    provider: str = "codex",
    *,
    adapter: str = "hooks",
    instance: str = "source:local-01",
    capability: str = "live_agent_events",
) -> SourceKey:
    return SourceKey(provider, adapter, instance, capability)


def _contract(
    provider: str = "codex",
    *,
    adapter: str = "hooks",
    instance: str = "source:local-01",
    include_requests: bool = True,
):
    capabilities = [
        {"id": "live_agent_events", "versions": [{"major": 1, "minor": 0}]},
    ]
    if include_requests:
        capabilities.append(
            {"id": "actionable_requests", "versions": [{"major": 1, "minor": 0}]}
        )
    return negotiate_provider_contract(
        {
            "schema_version": {"major": 1, "minor": 0},
            "provider_id": provider,
            "adapter_id": adapter,
            "source_instance_id": instance,
            "capabilities": capabilities,
        }
    )


def _event(
    provider: str = "codex",
    event_name: str = "PreToolUse",
    *,
    epoch: float = _EPOCH,
    session_id: object = "session:01",
    agent_id: object = None,
    turn_id: object = "turn:01",
    raw: dict[str, object] | None = None,
    cwd: object = None,
    tool_name: object = None,
    message: object = None,
) -> HookEvent:
    return HookEvent(
        provider=provider,
        logged_at=datetime.fromtimestamp(epoch, UTC),
        event_name=event_name,
        raw={"event_id": f"event:{int(epoch)}", **(raw or {})},
        session_id=session_id,  # type: ignore[arg-type]
        turn_id=turn_id,  # type: ignore[arg-type]
        agent_id=agent_id,  # type: ignore[arg-type]
        cwd=cwd,  # type: ignore[arg-type]
        tool_name=tool_name,  # type: ignore[arg-type]
        message=message,  # type: ignore[arg-type]
    )


def _normalize(
    event: HookEvent,
    *,
    source: SourceKey | None = None,
    authority: ObservationAuthority = ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    include_requests: bool = True,
) -> NormalizedProviderRecord | InertProviderRecord:
    actual_source = source or _source(event.provider)
    return minimize_hook_event(
        event,
        source_key=actual_source,
        contract=_contract(
            actual_source.provider_id,
            adapter=actual_source.adapter_id,
            instance=actual_source.source_instance_id,
            include_requests=include_requests,
        ),
        observation_authority=authority,
    )


def _batch(
    event: HookEvent,
    *,
    source: SourceKey | None = None,
    authority: ObservationAuthority = ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    include_requests: bool = True,
):
    actual_source = source or _source(event.provider)
    contract = _contract(
        actual_source.provider_id,
        adapter=actual_source.adapter_id,
        instance=actual_source.source_instance_id,
        include_requests=include_requests,
    )
    normalized = minimize_hook_event(
        event,
        source_key=actual_source,
        contract=contract,
        observation_authority=authority,
    )
    return normalized, provider_facts_for_record(
        normalized,
        contract=contract,
        observation_authority=authority,
        observed_at_epoch=event.logged_at.timestamp() + 0.25,
    )


class _PoisonMapping(Mapping[object, object]):
    def __getitem__(self, key: object) -> object:
        raise AssertionError("poison mapping was read")

    def __iter__(self) -> Iterator[object]:
        raise AssertionError("poison mapping was traversed")

    def __len__(self) -> int:
        raise AssertionError("poison mapping length was read")

    def __repr__(self) -> str:
        raise AssertionError("poison mapping was represented")


class _ExplosiveDict(dict[object, object]):
    def __iter__(self) -> Iterator[object]:
        raise AssertionError("dict subclass was traversed")

    def __getitem__(self, key: object) -> object:
        raise AssertionError("dict subclass was read")


class _StringSubclass(str):
    pass


def test_minimization_drops_all_private_copy_and_never_traverses_unknown_values() -> None:
    """Retaining or reflecting ingress copy would persist secrets outside the hook boundary."""
    sentinels = (
        "sk-private-raw",
        "secret-message",
        "/private/company/cwd",
        "secret-tool-input",
        "secret-command",
        "secret-response",
        "private-account@example.test",
        "/private/transcript.jsonl",
    )
    raw = {
        "event_id": "event:safe-01",
        "request_id": "request:safe-01",
        "raw": sentinels[0],
        "message": sentinels[1],
        "cwd": sentinels[2],
        "tool_input": sentinels[3],
        "command": sentinels[4],
        "response": sentinels[5],
        "account_label": sentinels[6],
        "transcript_path": sentinels[7],
        "future_payload": _PoisonMapping(),
    }
    event = _event(
        event_name="PermissionRequest",
        raw=raw,
        cwd=sentinels[2],
        tool_name=sentinels[4],
        message=sentinels[1],
    )

    normalized = _normalize(event)

    assert type(normalized) is NormalizedProviderRecord
    rendered = repr(normalized)
    payload_rendered = repr(normalized_provider_record_to_payload(normalized))
    for sentinel in sentinels:
        assert sentinel not in rendered
        assert sentinel not in payload_rendered
    assert normalized.provider_request_id is not None
    assert normalized.provider_request_id.value == "request:safe-01"


def test_raw_mapping_subclasses_are_ignored_without_execution() -> None:
    """Treating arbitrary mappings as provider data could execute attacker-controlled methods."""
    event = _event(event_name="Stop")
    object.__setattr__(event, "raw", _ExplosiveDict(event.raw))

    normalized = _normalize(event)

    assert type(normalized) is NormalizedProviderRecord
    assert normalized.event_name is ProviderEventName.STOP
    assert normalized.sequence is None


def test_official_cursor_stop_hashes_identity_and_discards_workspace_copy() -> None:
    """Persisting Cursor's conversation or workspace fields would cross the private boundary."""
    normalized, batch = _batch(
        _official_event("cursor", "stop", dict(_OFFICIAL_CURSOR_STOP))
    )

    assert type(normalized) is NormalizedProviderRecord
    assert normalized.event_name.value == "stop"
    assert normalized.provider_work_id is not None
    assert (
        normalized.provider_work_id.value
        == "27ce7b1041ec353270cba707d3b4a7128b13160e7097b1b35aba6b61bace5a14"
    )
    assert batch.work_facts[0].lifecycle is WorkLifecycle.COMPLETED
    persisted = repr(normalized_provider_record_to_payload(normalized))
    for forbidden in (
        _OFFICIAL_CURSOR_STOP["conversation_id"],
        *_OFFICIAL_CURSOR_STOP["workspace_roots"],
        _OFFICIAL_CURSOR_STOP["user_email"],
        _OFFICIAL_CURSOR_STOP["transcript_path"],
        "workspace_roots",
    ):
        assert forbidden not in persisted


@pytest.mark.parametrize(
    ("status", "event_name", "lifecycle"),
    [
        ("completed", "stop", WorkLifecycle.COMPLETED),
        ("error", "stop_failure", WorkLifecycle.FAILED),
        ("aborted", "stop_interrupted", WorkLifecycle.UNKNOWN),
    ],
)
def test_cursor_stop_uses_only_documented_typed_status(
    status: str,
    event_name: str,
    lifecycle: WorkLifecycle,
) -> None:
    """A generic stop rule would collapse Cursor errors and aborts into green completion."""
    payload = {**_OFFICIAL_CURSOR_STOP, "status": status}
    normalized, batch = _batch(_official_event("cursor", "stop", payload))

    assert type(normalized) is NormalizedProviderRecord
    assert normalized.event_name.value == event_name
    assert batch.work_facts[0].lifecycle is lifecycle
    assert batch.request_facts == ()


@pytest.mark.parametrize(
    "conversation_id",
    [
        "sk-live-private-token",
        "/Users/private-company/project",
        "conversation\nid",
        "c" * 129,
        17,
    ],
)
def test_cursor_rejects_nonopaque_official_conversation_identity(
    conversation_id: object,
) -> None:
    """Hashing malformed or credential-shaped input would launder it into durable identity."""
    payload = {**_OFFICIAL_CURSOR_STOP, "conversation_id": conversation_id}
    normalized, batch = _batch(_official_event("cursor", "stop", payload))

    assert type(normalized) is InertProviderRecord
    assert normalized.diagnostic.identifier.value == "invalid_provider_identity"
    assert batch.work_facts == ()
    assert batch.request_facts == ()


def test_cursor_rejects_ambiguous_identity_fields() -> None:
    """Competing identity fields must not let input order choose the persisted work key."""
    payload = {
        **_OFFICIAL_CURSOR_STOP,
        "session_id": "different-session",
    }
    normalized, batch = _batch(_official_event("cursor", "stop", payload))

    assert type(normalized) is InertProviderRecord
    assert normalized.diagnostic.identifier.value == "invalid_provider_identity"
    assert batch.work_facts == ()


def test_cursor_accepts_documented_session_alias_only_when_it_matches() -> None:
    """Cursor documents session_id as the conversation ID on sessionStart."""
    conversation_id = _OFFICIAL_CURSOR_STOP["conversation_id"]
    payload = {
        **_OFFICIAL_CURSOR_STOP,
        "hook_event_name": "sessionStart",
        "session_id": conversation_id,
    }
    payload.pop("status")
    payload.pop("loop_count")

    normalized = _normalize(_official_event("cursor", "sessionStart", payload))

    assert type(normalized) is NormalizedProviderRecord
    assert normalized.event_name is ProviderEventName.SESSION_START
    assert normalized.provider_work_id is not None
    assert normalized.provider_work_id.value == (
        "27ce7b1041ec353270cba707d3b4a7128b13160e7097b1b35aba6b61bace5a14"
    )


@pytest.mark.parametrize(
    ("outcome", "event_name", "lifecycle"),
    [
        (
            {"completed": True, "failed": False, "interrupted": False},
            "stop",
            WorkLifecycle.COMPLETED,
        ),
        (
            {"completed": False, "failed": True, "interrupted": False},
            "stop_failure",
            WorkLifecycle.FAILED,
        ),
        (
            {"completed": False, "failed": False, "interrupted": True},
            "stop_interrupted",
            WorkLifecycle.UNKNOWN,
        ),
        (
            {"completed": False, "failed": False, "interrupted": False},
            "stop_incomplete",
            WorkLifecycle.UNKNOWN,
        ),
    ],
)
def test_official_hermes_turn_outcomes_stay_distinct(
    outcome: dict[str, bool],
    event_name: str,
    lifecycle: WorkLifecycle,
) -> None:
    """Hermes turn outcome booleans, not event-name heuristics, own terminal semantics."""
    payload = dict(_OFFICIAL_HERMES_TURN_END)
    payload["extra"] = {**_OFFICIAL_HERMES_TURN_END["extra"], **outcome}
    normalized, batch = _batch(
        _official_event("hermes", "on_session_end", payload)
    )

    assert type(normalized) is NormalizedProviderRecord
    assert normalized.event_name.value == event_name
    assert batch.work_facts[0].lifecycle is lifecycle
    persisted = repr(normalized_provider_record_to_payload(normalized))
    for forbidden in (
        payload["cwd"],
        payload["extra"]["task_id"],
        payload["extra"]["turn_exit_reason"],
        "extra",
    ):
        assert forbidden not in persisted


def test_hermes_reduced_exit_shape_cannot_claim_completion() -> None:
    """Reduced CLI exit payloads omit outcome truth and must remain unknown."""
    payload = {
        "hook_event_name": "on_session_end",
        "tool_name": None,
        "tool_input": None,
        "session_id": "sess_abc123",
        "cwd": "/Users/private-company/hermes",
        "extra": {"platform": "cli", "reason": "exit"},
    }

    normalized, batch = _batch(
        _official_event("hermes", "on_session_end", payload)
    )

    assert type(normalized) is NormalizedProviderRecord
    assert normalized.event_name is ProviderEventName.STOP_INCOMPLETE
    assert batch.work_facts[0].lifecycle is WorkLifecycle.UNKNOWN


def test_hermes_finalize_and_api_error_preserve_provider_meaning() -> None:
    """Teardown and a failed provider attempt are neither ordinary completed turns."""
    finalize_payload = {
        "hook_event_name": "on_session_finalize",
        "tool_name": None,
        "tool_input": None,
        "session_id": "sess_abc123",
        "cwd": "/Users/private-company/hermes",
        "extra": {"platform": "cli", "reason": "shutdown"},
    }
    error_payload = {
        "hook_event_name": "api_request_error",
        "tool_name": None,
        "tool_input": None,
        "session_id": "sess_abc123",
        "cwd": "/Users/private-company/hermes",
        "extra": {
            "api_request_id": "request-private-01",
            "retryable": True,
            "reason": "rate_limit",
            "error": "private provider error copy",
            "request": {"messages": "private request copy"},
        },
    }

    finalize, finalize_batch = _batch(
        _official_event("hermes", "on_session_finalize", finalize_payload)
    )
    api_error, api_error_batch = _batch(
        _official_event("hermes", "api_request_error", error_payload)
    )

    assert type(finalize) is NormalizedProviderRecord
    assert finalize.event_name.value == "session_finalize"
    assert finalize_batch.work_facts[0].lifecycle is WorkLifecycle.UNKNOWN
    assert type(api_error) is NormalizedProviderRecord
    assert api_error.event_name.value == "api_request_error"
    assert api_error_batch.work_facts[0].lifecycle is WorkLifecycle.ACTIVE
    assert "private provider error copy" not in repr(api_error)
    assert "private request copy" not in repr(api_error_batch)


def test_hermes_rejects_ambiguous_outcome_and_nonopaque_identity() -> None:
    """Malformed terminal truth and path identity must fail closed without a lifecycle fact."""
    ambiguous = dict(_OFFICIAL_HERMES_TURN_END)
    ambiguous["extra"] = {
        **_OFFICIAL_HERMES_TURN_END["extra"],
        "completed": True,
        "failed": True,
    }
    invalid_identity = dict(_OFFICIAL_HERMES_TURN_END)
    invalid_identity["session_id"] = "/Users/private-company/hermes"

    ambiguous_record, ambiguous_batch = _batch(
        _official_event("hermes", "on_session_end", ambiguous)
    )
    identity_record, identity_batch = _batch(
        _official_event("hermes", "on_session_end", invalid_identity)
    )

    assert type(ambiguous_record) is InertProviderRecord
    assert ambiguous_record.diagnostic.identifier.value == "invalid_provider_outcome"
    assert ambiguous_batch.work_facts == ()
    assert type(identity_record) is InertProviderRecord
    assert identity_record.diagnostic.identifier.value == "invalid_provider_identity"
    assert identity_batch.work_facts == ()


# Literal conformance fixtures intentionally do not import the legacy registry.
# A provider-specific table regression must fail this suite even if that registry changes.
_CONFORMANCE = (
    # Codex
    ("codex", "SessionStart", "session_start", WorkLifecycle.IDLE, False),
    ("codex", "UserPromptSubmit", "user_prompt_submit", WorkLifecycle.ACTIVE, False),
    ("codex", "PreToolUse", "pre_tool_use", WorkLifecycle.ACTIVE, False),
    ("codex", "PostToolUse", "post_tool_use", WorkLifecycle.ACTIVE, False),
    ("codex", "PermissionRequest", "permission_request", WorkLifecycle.WAITING, True),
    ("codex", "PreCompact", "pre_compact", WorkLifecycle.ACTIVE, False),
    ("codex", "PostCompact", "post_compact", WorkLifecycle.ACTIVE, False),
    ("codex", "SubagentStart", "subagent_start", WorkLifecycle.ACTIVE, False),
    ("codex", "SubagentStop", "subagent_stop", WorkLifecycle.COMPLETED, False),
    ("codex", "Stop", "stop", WorkLifecycle.COMPLETED, False),
    # Claude
    ("claude", "SessionStart", "session_start", WorkLifecycle.IDLE, False),
    ("claude", "UserPromptSubmit", "user_prompt_submit", WorkLifecycle.ACTIVE, False),
    ("claude", "PreToolUse", "pre_tool_use", WorkLifecycle.ACTIVE, False),
    ("claude", "PostToolUse", "post_tool_use", WorkLifecycle.ACTIVE, False),
    ("claude", "PostToolUseFailure", "post_tool_use_failure", WorkLifecycle.ACTIVE, False),
    ("claude", "PermissionRequest", "permission_request", WorkLifecycle.WAITING, True),
    ("claude", "Notification", "notification", None, False),
    ("claude", "PreCompact", "pre_compact", WorkLifecycle.ACTIVE, False),
    ("claude", "PostCompact", "post_compact", WorkLifecycle.ACTIVE, False),
    ("claude", "SubagentStop", "subagent_stop", WorkLifecycle.COMPLETED, False),
    ("claude", "Stop", "stop", WorkLifecycle.COMPLETED, False),
    ("claude", "SessionEnd", "session_end", WorkLifecycle.COMPLETED, False),
    # Devin
    ("devin", "SessionStart", "session_start", WorkLifecycle.IDLE, False),
    ("devin", "UserPromptSubmit", "user_prompt_submit", WorkLifecycle.ACTIVE, False),
    ("devin", "PreToolUse", "pre_tool_use", WorkLifecycle.ACTIVE, False),
    ("devin", "PostToolUse", "post_tool_use", WorkLifecycle.ACTIVE, False),
    ("devin", "PermissionRequest", "permission_request", WorkLifecycle.WAITING, True),
    ("devin", "PostCompaction", "post_compact", WorkLifecycle.ACTIVE, False),
    ("devin", "Stop", "stop", WorkLifecycle.COMPLETED, False),
    ("devin", "SessionEnd", "session_end", WorkLifecycle.COMPLETED, False),
    # Grok
    ("grok", "SessionStart", "session_start", WorkLifecycle.IDLE, False),
    ("grok", "UserPromptSubmit", "user_prompt_submit", WorkLifecycle.ACTIVE, False),
    ("grok", "PreToolUse", "pre_tool_use", WorkLifecycle.ACTIVE, False),
    ("grok", "PostToolUse", "post_tool_use", WorkLifecycle.ACTIVE, False),
    ("grok", "PostToolUseFailure", "post_tool_use_failure", WorkLifecycle.ACTIVE, False),
    ("grok", "PermissionDenied", "permission_denied", WorkLifecycle.FAILED, False),
    ("grok", "Notification", "notification", None, False),
    ("grok", "PreCompact", "pre_compact", WorkLifecycle.ACTIVE, False),
    ("grok", "PostCompact", "post_compact", WorkLifecycle.ACTIVE, False),
    ("grok", "SubagentStart", "subagent_start", WorkLifecycle.ACTIVE, False),
    ("grok", "SubagentStop", "subagent_stop", WorkLifecycle.COMPLETED, False),
    ("grok", "Stop", "stop", WorkLifecycle.COMPLETED, False),
    ("grok", "StopFailure", "stop_failure", WorkLifecycle.FAILED, False),
    ("grok", "SessionEnd", "session_end", WorkLifecycle.COMPLETED, False),
    # Cursor native names
    ("cursor", "sessionStart", "session_start", WorkLifecycle.IDLE, False),
    ("cursor", "beforeSubmitPrompt", "user_prompt_submit", WorkLifecycle.ACTIVE, False),
    ("cursor", "preToolUse", "pre_tool_use", WorkLifecycle.ACTIVE, False),
    ("cursor", "postToolUse", "post_tool_use", WorkLifecycle.ACTIVE, False),
    ("cursor", "postToolUseFailure", "post_tool_use_failure", WorkLifecycle.ACTIVE, False),
    ("cursor", "beforeShellExecution", "pre_tool_use", WorkLifecycle.ACTIVE, False),
    ("cursor", "afterShellExecution", "post_tool_use", WorkLifecycle.ACTIVE, False),
    ("cursor", "beforeMCPExecution", "pre_tool_use", WorkLifecycle.ACTIVE, False),
    ("cursor", "afterMCPExecution", "post_tool_use", WorkLifecycle.ACTIVE, False),
    ("cursor", "subagentStart", "subagent_start", WorkLifecycle.ACTIVE, False),
    ("cursor", "subagentStop", "subagent_stop", WorkLifecycle.COMPLETED, False),
    ("cursor", "preCompact", "pre_compact", WorkLifecycle.ACTIVE, False),
    ("cursor", "stop", "stop", WorkLifecycle.COMPLETED, False),
    ("cursor", "sessionEnd", "session_end", WorkLifecycle.COMPLETED, False),
    # Hermes native names
    ("hermes", "on_session_start", "session_start", WorkLifecycle.IDLE, False),
    ("hermes", "pre_llm_call", "user_prompt_submit", WorkLifecycle.ACTIVE, False),
    ("hermes", "pre_tool_call", "pre_tool_use", WorkLifecycle.ACTIVE, False),
    ("hermes", "post_tool_call", "post_tool_use", WorkLifecycle.ACTIVE, False),
    ("hermes", "subagent_start", "subagent_start", WorkLifecycle.ACTIVE, False),
    ("hermes", "subagent_stop", "subagent_stop", WorkLifecycle.COMPLETED, False),
    ("hermes", "on_session_end", "session_end", WorkLifecycle.COMPLETED, False),
    # OpenClaw
    ("openclaw", "SessionStart", "session_start", WorkLifecycle.IDLE, False),
    ("openclaw", "UserPromptSubmit", "user_prompt_submit", WorkLifecycle.ACTIVE, False),
    ("openclaw", "Stop", "stop", WorkLifecycle.COMPLETED, False),
    ("openclaw", "SessionEnd", "session_end", WorkLifecycle.COMPLETED, False),
    # OpenCode plugin bridge canonical events
    ("opencode", "SessionStart", "session_start", WorkLifecycle.IDLE, False),
    ("opencode", "UserPromptSubmit", "user_prompt_submit", WorkLifecycle.ACTIVE, False),
    ("opencode", "PreToolUse", "pre_tool_use", WorkLifecycle.ACTIVE, False),
    ("opencode", "PostToolUse", "post_tool_use", WorkLifecycle.ACTIVE, False),
    ("opencode", "PostToolUseFailure", "post_tool_use_failure", WorkLifecycle.ACTIVE, False),
    ("opencode", "PermissionRequest", "permission_request", WorkLifecycle.WAITING, True),
    ("opencode", "Notification", "notification", None, False),
    ("opencode", "PreCompact", "pre_compact", WorkLifecycle.ACTIVE, False),
    ("opencode", "PostCompact", "post_compact", WorkLifecycle.ACTIVE, False),
    ("opencode", "Stop", "stop", WorkLifecycle.COMPLETED, False),
    ("opencode", "StopFailure", "stop_failure", WorkLifecycle.FAILED, False),
    ("opencode", "SessionEnd", "session_end", WorkLifecycle.COMPLETED, False),
)


@pytest.mark.parametrize(
    ("provider", "native_event", "canonical_event", "lifecycle", "opens_request"),
    _CONFORMANCE,
)
def test_static_provider_event_conformance_is_exact(
    provider: str,
    native_event: str,
    canonical_event: str,
    lifecycle: WorkLifecycle | None,
    opens_request: bool,
) -> None:
    """A generic event-name branch could grant unsupported semantics to one provider."""
    raw = {"request_id": "request:01"} if opens_request else {}
    normalized, batch = _batch(_event(provider, native_event, raw=raw))

    assert type(normalized) is NormalizedProviderRecord
    assert normalized.event_name.value == canonical_event
    assert normalized.source_key == _source(provider)
    assert batch.source_key == normalized.source_key
    assert tuple(fact.lifecycle for fact in batch.work_facts) == (
        () if lifecycle is None else (lifecycle,)
    )
    assert len(batch.request_facts) == int(opens_request)
    if opens_request:
        request = batch.request_facts[0]
        assert request.state is ProviderRequestState.LIVE
        assert request.request_kind is RequestKind.PERMISSION
        assert request.key.work_key.source_key == _source(provider)
    for fact in (*batch.work_facts, *batch.request_facts):
        fact_source = (
            fact.key.source_key
            if hasattr(fact.key, "source_key")
            else fact.key.work_key.source_key
        )
        assert fact_source.source_instance_id == "source:local-01"


def test_unknown_event_is_inert_partial_and_text_cannot_classify_it() -> None:
    """Unknown provider copy must not create lifecycle, request, failure, or completion truth."""
    normalized, batch = _batch(
        _event(
            "claude",
            "FutureEvent",
            raw={"request_id": "request:01", "notification_type": "permission_prompt"},
            message="permission required, done, complete?",
        )
    )

    assert type(normalized) is InertProviderRecord
    assert normalized.diagnostic.identifier.value == "unknown_provider_event"
    assert batch.work_facts == ()
    assert batch.request_facts == ()
    assert batch.source_health is SourceHealth.PARTIAL
    assert batch.source_freshness is SourceFreshness.PARTIAL


def test_question_phrases_paths_and_raw_failures_do_not_override_typed_events() -> None:
    """Message and payload heuristics would reintroduce phantom asks and failures."""
    stop, stop_batch = _batch(
        _event(
            "claude",
            "Stop",
            message="Would you like me to continue?",
            raw={"last_assistant_message": "Would you like me to continue?"},
            cwd="/private/permission-needed/complete",
        )
    )
    post, post_batch = _batch(
        _event(
            "claude",
            "PostToolUse",
            raw={"tool_response": {"error": "permission denied"}},
            message="failed?",
        )
    )

    assert type(stop) is NormalizedProviderRecord
    assert stop_batch.work_facts[0].lifecycle is WorkLifecycle.COMPLETED
    assert stop_batch.request_facts == ()
    assert type(post) is NormalizedProviderRecord
    assert post_batch.work_facts[0].lifecycle is WorkLifecycle.ACTIVE
    assert post_batch.request_facts == ()


@pytest.mark.parametrize("message", ["permission needed", "done", "complete"])
def test_notification_text_is_semantically_inert_without_typed_kind(message: str) -> None:
    """Notification prose is private display copy, not canonical provider truth."""
    normalized, batch = _batch(
        _event("claude", "Notification", raw={"message": message}, message=message)
    )

    assert type(normalized) is NormalizedProviderRecord
    assert normalized.notification_kind is None
    assert batch.work_facts == ()
    assert batch.request_facts == ()
    assert batch.source_health is SourceHealth.HEALTHY


def test_unknown_typed_notification_kind_is_inert_partial() -> None:
    """A future typed notification value must stay visible without gaining semantics."""
    normalized, batch = _batch(
        _event(
            "claude",
            "Notification",
            raw={"notification_type": "future_provider_kind"},
        )
    )

    assert type(normalized) is InertProviderRecord
    assert normalized.diagnostic.identifier.value == "unknown_notification_kind"
    assert batch.work_facts == ()
    assert batch.request_facts == ()
    assert batch.source_health is SourceHealth.PARTIAL


def test_allowlisted_typed_notification_requires_exact_request_identity() -> None:
    """A typed notification without a request key must not open an unanswerable ask."""
    with_id, with_id_batch = _batch(
        _event(
            "claude",
            "Notification",
            raw={
                "notification_type": "permission_prompt",
                "request_id": "request:notification-01",
            },
        )
    )
    without_id, without_id_batch = _batch(
        _event(
            "claude",
            "Notification",
            raw={"notification_type": "permission_prompt"},
        )
    )

    assert type(with_id) is NormalizedProviderRecord
    assert with_id.notification_kind is NotificationKind.PERMISSION_REQUEST
    assert len(with_id_batch.request_facts) == 1
    assert type(without_id) is NormalizedProviderRecord
    assert without_id.notification_kind is NotificationKind.PERMISSION_REQUEST
    assert without_id_batch.request_facts == ()
    assert without_id_batch.source_health is SourceHealth.PARTIAL


def test_permission_request_without_provider_request_id_creates_no_request() -> None:
    """Synthesizing a request identity from work or text would make unsafe actions durable."""
    normalized, batch = _batch(_event("codex", "PermissionRequest"))

    assert type(normalized) is NormalizedProviderRecord
    assert normalized.provider_request_id is None
    assert batch.work_facts[0].lifecycle is WorkLifecycle.WAITING
    assert batch.request_facts == ()
    assert batch.source_health is SourceHealth.PARTIAL
    assert tuple(item.identifier.value for item in batch.diagnostics) == (
        "missing_request_identity",
    )


def test_transcript_fallback_cannot_open_an_actionable_request() -> None:
    """Fallback observations do not have authority to create an operator action."""
    normalized, batch = _batch(
        _event("codex", "PermissionRequest", raw={"request_id": "request:01"}),
        authority=ObservationAuthority.FALLBACK_OBSERVATION,
    )

    assert type(normalized) is NormalizedProviderRecord
    assert batch.work_facts[0].lifecycle is WorkLifecycle.WAITING
    assert batch.request_facts == ()
    assert batch.source_health is SourceHealth.PARTIAL
    assert tuple(item.identifier.value for item in batch.diagnostics) == (
        "insufficient_request_authority",
    )


def test_codex_usage_limit_stop_failure_closes_active_work_without_private_copy() -> None:
    source = _source()
    active_record, active_batch = _batch(
        _event("codex", "PreToolUse", epoch=_EPOCH),
        source=source,
        authority=ObservationAuthority.FALLBACK_OBSERVATION,
    )
    failure_record, failure_batch = _batch(
        _event(
            "codex",
            "StopFailure",
            epoch=_EPOCH + 1.0,
            raw={
                "source": "codex-transcripts",
                "error": "private-usage-limit-error",
            },
        ),
        source=source,
        authority=ObservationAuthority.FALLBACK_OBSERVATION,
    )
    assert type(active_record) is NormalizedProviderRecord
    assert type(failure_record) is NormalizedProviderRecord

    active = reduce_operator_state(
        empty_operator_state(),
        active_batch,
        clock=ClockSample(_EPOCH, 100.0, BootIdentifier("boot:01")),
    )
    failed = reduce_operator_state(
        active.state,
        failure_batch,
        clock=ClockSample(_EPOCH + 1.0, 101.0, BootIdentifier("boot:01")),
    )

    assert failure_record.event_name is ProviderEventName.STOP_FAILURE
    assert failure_batch.observation_authority is ObservationAuthority.FALLBACK_OBSERVATION
    assert failure_batch.work_facts[0].lifecycle is WorkLifecycle.FAILED
    assert failure_batch.work_facts[0].next_actor is NextActor.NONE
    assert failure_batch.work_facts[0].terminal_cause is ProviderTerminalCause.NONE
    assert failure_batch.request_facts == ()
    assert failed.state.works[0].lifecycle is WorkLifecycle.FAILED
    assert failed.state.works[0].request_keys == ()
    assert all(event.kind is not TransitionKind.COMPLETED for event in failed.events)
    assert "private-usage-limit-error" not in repr(failure_record)
    assert "private-usage-limit-error" not in repr(failure_batch)


def test_fallback_completion_cannot_override_newer_direct_active_truth() -> None:
    """Authority must outrank arrival time for one exact source-scoped work key."""
    source = _source()
    direct_record, direct_batch = _batch(
        _event("codex", "PreToolUse", epoch=_EPOCH),
        source=source,
        authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    )
    fallback_record, fallback_batch = _batch(
        _event("codex", "Stop", epoch=_EPOCH + 10.0),
        source=source,
        authority=ObservationAuthority.FALLBACK_OBSERVATION,
    )
    assert type(direct_record) is NormalizedProviderRecord
    assert type(fallback_record) is NormalizedProviderRecord

    active = reduce_operator_state(
        empty_operator_state(),
        direct_batch,
        clock=ClockSample(_EPOCH, 100.0, BootIdentifier("boot:01")),
    )
    fallback = reduce_operator_state(
        active.state,
        fallback_batch,
        clock=ClockSample(_EPOCH + 10.0, 110.0, BootIdentifier("boot:01")),
    )

    assert fallback.state.works[0].lifecycle is WorkLifecycle.ACTIVE
    assert TransitionKind.COMPLETED not in tuple(event.kind for event in fallback.events)


def test_source_and_contract_identity_must_match_exactly() -> None:
    """A mismatched source envelope could attribute one provider's event to another source."""
    source = _source("codex", instance="source:expected")
    normalized = minimize_hook_event(
        _event("claude", "Stop"),
        source_key=source,
        contract=_contract("codex", instance="source:expected"),
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    )

    assert type(normalized) is InertProviderRecord
    assert normalized.source_key == source
    assert normalized.diagnostic.identifier.value == "source_identity_mismatch"


def test_source_identity_rejects_scalar_subclasses_at_ingress() -> None:
    """A value-equal scalar subclass must not cross the exact source-key boundary."""
    source = SourceKey(
        _StringSubclass("codex"),
        "hooks",
        "source:local-01",
        "live_agent_events",
    )

    with pytest.raises(ValueError, match="invalid source key"):
        minimize_hook_event(
            _event("codex", "Stop"),
            source_key=source,
            contract=_contract(),
            observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        )


def test_missing_live_event_capability_keeps_record_inert() -> None:
    """An identity-only contract must not grant lifecycle observation authority."""
    source = _source()
    contract = negotiate_provider_contract(
        {
            "schema_version": {"major": 1, "minor": 0},
            "provider_id": "codex",
            "adapter_id": "hooks",
            "source_instance_id": "source:local-01",
            "capabilities": [],
        }
    )
    normalized = minimize_hook_event(
        _event("codex", "Stop"),
        source_key=source,
        contract=contract,
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    )

    assert type(normalized) is InertProviderRecord
    assert normalized.diagnostic.identifier.value == "contract_not_observable"


def test_normalized_and_inert_record_codecs_are_exact_and_round_trip() -> None:
    """An additive or coercive persistence codec could smuggle private provider payloads."""
    normalized = _normalize(
        _event(
            "claude",
            "PermissionRequest",
            raw={"request_id": "request:01", "sequence": 7},
        )
    )
    inert = _normalize(_event("claude", "FutureEvent"))
    assert type(normalized) is NormalizedProviderRecord
    assert type(inert) is InertProviderRecord

    normalized_payload = normalized_provider_record_to_payload(normalized)
    inert_payload = normalized_provider_record_to_payload(inert)

    assert normalized_payload == {
        "version": {"major": 1, "minor": 0},
        "record_kind": "normalized",
        "provider_id": "claude",
        "adapter_id": "hooks",
        "source_instance_id": "source:local-01",
        "capability_id": "live_agent_events",
        "event_name": "permission_request",
        "occurred_at_epoch": _EPOCH,
        "event_token": "event:1800000000",
        "provider_work_id": "session:01",
        "provider_request_id": "request:01",
        "parent_work_id": None,
        "safe_label": "Claude session:01",
        "notification_kind": None,
        "sequence": 7,
    }
    assert inert_payload == {
        "version": {"major": 1, "minor": 0},
        "record_kind": "inert",
        "provider_id": "claude",
        "adapter_id": "hooks",
        "source_instance_id": "source:local-01",
        "capability_id": "live_agent_events",
        "occurred_at_epoch": _EPOCH,
        "diagnostic_id": "unknown_provider_event",
        "diagnostic_count": 1,
    }
    assert normalized_provider_record_from_payload(normalized_payload) == normalized
    assert normalized_provider_record_from_payload(inert_payload) == inert
    assert normalized_provider_record_from_payload(
        {**inert_payload, "diagnostic_id": "provider_supplied_copy"}
    ) is None


@pytest.mark.parametrize(
    "poison",
    [
        None,
        [],
        _PoisonMapping(),
        _ExplosiveDict(),
        {"version": {"major": 2, "minor": 0}},
    ],
)
def test_record_decoder_rejects_nonexact_or_executable_containers(poison: object) -> None:
    """Decoding arbitrary mapping shapes could execute code or accept unknown schemas."""
    assert normalized_provider_record_from_payload(poison) is None


def test_record_decoder_rejects_extra_missing_invalid_and_secret_shaped_fields() -> None:
    """Strict scalar validation must fail closed without retaining invalid persisted copy."""
    record = _normalize(_event("codex", "PreToolUse"))
    assert type(record) is NormalizedProviderRecord
    valid = normalized_provider_record_to_payload(record)
    missing = dict(valid)
    missing.pop("event_token")
    extra = {**valid, "message": "private prompt"}
    invalid_event = {**valid, "event_name": "FutureEvent"}
    secret_id = {**valid, "provider_work_id": "work:Bearer_secret"}
    bool_sequence = {**valid, "sequence": True}
    version_subclass = {**valid, "version": _ExplosiveDict(valid["version"])}
    record_kind_subclass = {**valid, "record_kind": _StringSubclass("normalized")}

    for payload in (
        missing,
        extra,
        invalid_event,
        secret_id,
        bool_sequence,
        version_subclass,
        record_kind_subclass,
    ):
        assert normalized_provider_record_from_payload(payload) is None


def test_record_decoder_rejects_cross_provider_event_and_notification_pairs() -> None:
    """A globally known event must not bypass its provider-specific conformance table."""
    record = _normalize(_event("codex", "PreToolUse"))
    assert type(record) is NormalizedProviderRecord
    valid = normalized_provider_record_to_payload(record)

    for payload in (
        {**valid, "event_name": "post_tool_use_failure"},
        {
            **valid,
            "event_name": "notification",
            "notification_kind": "permission_request",
        },
        {**valid, "provider_id": "future-provider"},
        {**valid, "adapter_id": "transcripts"},
        {**valid, "capability_id": "actionable_requests"},
    ):
        assert normalized_provider_record_from_payload(payload) is None


def test_reducer_composition_emits_typed_edges_and_ignores_out_of_order_records() -> None:
    """Adapters and the reducer must compose without a second lifecycle interpretation."""
    _, active_batch = _batch(_event("codex", "PreToolUse", epoch=_EPOCH))
    _, request_batch = _batch(
        _event(
            "codex",
            "PermissionRequest",
            epoch=_EPOCH + 1.0,
            raw={"request_id": "request:01"},
        )
    )
    _, completed_batch = _batch(_event("codex", "Stop", epoch=_EPOCH + 2.0))
    _, old_batch = _batch(_event("codex", "PreToolUse", epoch=_EPOCH + 0.5))

    active = reduce_operator_state(
        empty_operator_state(),
        active_batch,
        clock=ClockSample(_EPOCH, 100.0, BootIdentifier("boot:01")),
    )
    requested = reduce_operator_state(
        active.state,
        request_batch,
        clock=ClockSample(_EPOCH + 1.0, 101.0, BootIdentifier("boot:01")),
    )
    completed = reduce_operator_state(
        requested.state,
        completed_batch,
        clock=ClockSample(_EPOCH + 2.0, 102.0, BootIdentifier("boot:01")),
    )
    old = reduce_operator_state(
        completed.state,
        old_batch,
        clock=ClockSample(_EPOCH + 3.0, 103.0, BootIdentifier("boot:01")),
    )

    assert TransitionKind.BECAME_ACTIVE in tuple(event.kind for event in active.events)
    assert TransitionKind.REQUEST_OPENED in tuple(event.kind for event in requested.events)
    assert TransitionKind.COMPLETED in tuple(event.kind for event in completed.events)
    assert old.state.works[0].lifecycle is WorkLifecycle.COMPLETED
    assert old.events == ()
