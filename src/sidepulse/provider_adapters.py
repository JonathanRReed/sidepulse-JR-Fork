"""Content-free ingress minimization and static provider fact adapters.

This module accepts the legacy ``HookEvent`` only at its outer boundary. It
copies a small allowlist of typed identity and ordering fields into immutable
records, then maps provider-declared event names through static first-party
tables. It performs no discovery, I/O, plugin loading, persistence, provider
invocation, state reduction, or text classification.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final

from .capacity_types import CapacityValidationError, SourceKey
from .models import _CODEX_TRANSCRIPT_USAGE_LIMIT_PROVENANCE, HookEvent
from .provider_contracts import (
    AdapterIdentifier,
    CapabilityIdentifier,
    ContractStatus,
    ContractValidationError,
    DiagnosticIdentifier,
    NegotiatedProviderContract,
    ProviderIdentifier,
    SchemaVersion,
    SourceInstanceIdentifier,
)
from .provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderFactBatch,
    ProviderFactDiagnostic,
    ProviderFactValidationError,
    ProviderRequestFact,
    ProviderRequestState,
    ProviderTerminalCause,
    ProviderWatermark,
    ProviderWorkFact,
    RequestIdentifier,
    RequestKey,
    RequestKind,
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
    WorkLifecycle,
)

MAX_PROVIDER_SEQUENCE: Final = (1 << 63) - 1
_LIVE_EVENTS_CAPABILITY: Final = "live_agent_events"
_ACTIONABLE_REQUESTS_CAPABILITY: Final = "actionable_requests"
_RECORD_SCHEMA_VERSION: Final = SchemaVersion(1, 0)
_VERSION_FIELDS: Final = frozenset({"major", "minor"})
_SOURCE_FIELDS: Final = frozenset(
    {"provider_id", "adapter_id", "source_instance_id", "capability_id"}
)
_NORMALIZED_FIELDS: Final = frozenset(
    {
        "version",
        "record_kind",
        *_SOURCE_FIELDS,
        "event_name",
        "occurred_at_epoch",
        "event_token",
        "provider_work_id",
        "provider_request_id",
        "parent_work_id",
        "safe_label",
        "notification_kind",
        "sequence",
    }
)
_INERT_FIELDS: Final = frozenset(
    {
        "version",
        "record_kind",
        *_SOURCE_FIELDS,
        "occurred_at_epoch",
        "diagnostic_id",
        "diagnostic_count",
    }
)
_PRODUCT_PROVIDER_LABELS: Final = {
    "codex": "Codex",
    "claude": "Claude",
    "devin": "Devin",
    "grok": "Grok",
    "cursor": "Cursor",
    "hermes": "Hermes",
    "openclaw": "OpenClaw",
    "opencode": "OpenCode",
    "antigravity": "Antigravity",
    "kiro": "Kiro",
}
_INERT_DIAGNOSTIC_IDS: Final = frozenset(
    {
        "invalid_hook_event",
        "invalid_event_time",
        "contract_not_observable",
        "source_identity_mismatch",
        "unknown_notification_kind",
        "unknown_provider_event",
        "invalid_provider_identity",
        "invalid_provider_outcome",
    }
)
_OPAQUE_PROVIDER_IDENTITY: Final = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._~:-]*\Z",
    re.ASCII,
)
_CREDENTIAL_SHAPED_IDENTITY: Final = re.compile(
    r"(?:^|[._~:-])(?:api[_-]?key|bearer|password|passwd|secret|sk|token)"
    r"(?:$|[._~:-])",
    re.ASCII | re.IGNORECASE,
)
_CURSOR_CONVERSATION_HASH_DOMAIN: Final = b"sidepulse.cursor.conversation.v1\0"
_ANTIGRAVITY_ENVELOPE_KEY: Final = "antigravity"


class ProviderAdapterValidationError(ValueError):
    """A normalized provider record failed closed."""


class ProviderEventName(str, Enum):
    SESSION_START = "session_start"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    POST_TOOL_USE_FAILURE = "post_tool_use_failure"
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_DENIED = "permission_denied"
    NOTIFICATION = "notification"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    STOP = "stop"
    STOP_FAILURE = "stop_failure"
    STOP_INTERRUPTED = "stop_interrupted"
    STOP_INCOMPLETE = "stop_incomplete"
    SESSION_END = "session_end"
    SESSION_FINALIZE = "session_finalize"
    API_REQUEST_ERROR = "api_request_error"


class NotificationKind(str, Enum):
    PERMISSION_REQUEST = "permission_request"
    INPUT_REQUIRED = "input_required"
    WORK_COMPLETED = "work_completed"
    WORK_FAILED = "work_failed"


@dataclass(frozen=True, slots=True)
class NormalizedProviderRecord:
    source_key: SourceKey
    event_name: ProviderEventName
    occurred_at_epoch: float
    event_token: EventToken
    provider_work_id: WorkIdentifier | None
    provider_request_id: RequestIdentifier | None
    parent_work_id: WorkIdentifier | None
    safe_label: str
    notification_kind: NotificationKind | None
    sequence: int | None
    terminal_cause: ProviderTerminalCause = ProviderTerminalCause.NONE

    def __post_init__(self) -> None:
        sequence_valid = self.sequence is None or (
            type(self.sequence) is int and 0 <= self.sequence <= MAX_PROVIDER_SEQUENCE
        )
        if not (
            _valid_source_key(self.source_key)
            and type(self.event_name) is ProviderEventName
            and _finite_nonnegative(self.occurred_at_epoch)
            and type(self.event_token) is EventToken
            and _optional_exact(self.provider_work_id, WorkIdentifier)
            and _optional_exact(self.provider_request_id, RequestIdentifier)
            and _optional_exact(self.parent_work_id, WorkIdentifier)
            and type(self.safe_label) is str
            and self.safe_label == _safe_label(self.source_key, self.provider_work_id)
            and _optional_exact(self.notification_kind, NotificationKind)
            and sequence_valid
            and type(self.terminal_cause) is ProviderTerminalCause
            and _record_matches_provider_table(self)
        ):
            raise ProviderAdapterValidationError("invalid normalized provider record")
        if self.parent_work_id is not None and self.parent_work_id == self.provider_work_id:
            raise ProviderAdapterValidationError("invalid normalized provider parent")
        if self.terminal_cause is ProviderTerminalCause.CODEX_USAGE_LIMIT and not (
            self.source_key.provider_id == "codex"
            and self.event_name is ProviderEventName.STOP_FAILURE
        ):
            raise ProviderAdapterValidationError("invalid normalized terminal cause")
        object.__setattr__(self, "occurred_at_epoch", float(self.occurred_at_epoch))


@dataclass(frozen=True, slots=True)
class InertProviderRecord:
    source_key: SourceKey
    occurred_at_epoch: float
    diagnostic: ProviderFactDiagnostic

    def __post_init__(self) -> None:
        if not (
            _valid_source_key(self.source_key)
            and _finite_nonnegative(self.occurred_at_epoch)
            and type(self.diagnostic) is ProviderFactDiagnostic
            and self.diagnostic.identifier.value in _INERT_DIAGNOSTIC_IDS
        ):
            raise ProviderAdapterValidationError("invalid inert provider record")
        object.__setattr__(self, "occurred_at_epoch", float(self.occurred_at_epoch))


@dataclass(frozen=True, slots=True)
class _EventRule:
    event_name: ProviderEventName
    lifecycle: WorkLifecycle | None
    next_actor: NextActor
    tie_break_rank: int
    request_state: ProviderRequestState | None = None
    request_kind: RequestKind = RequestKind.UNKNOWN


def _rule(
    event_name: ProviderEventName,
    lifecycle: WorkLifecycle | None,
    next_actor: NextActor,
    tie_break_rank: int,
    *,
    request_state: ProviderRequestState | None = None,
    request_kind: RequestKind = RequestKind.UNKNOWN,
) -> _EventRule:
    return _EventRule(
        event_name,
        lifecycle,
        next_actor,
        tie_break_rank,
        request_state,
        request_kind,
    )


_SESSION_START = _rule(
    ProviderEventName.SESSION_START,
    WorkLifecycle.IDLE,
    NextActor.NONE,
    10,
)
_USER_PROMPT = _rule(
    ProviderEventName.USER_PROMPT_SUBMIT,
    WorkLifecycle.ACTIVE,
    NextActor.PROVIDER,
    20,
)
_PRE_TOOL = _rule(
    ProviderEventName.PRE_TOOL_USE,
    WorkLifecycle.ACTIVE,
    NextActor.PROVIDER,
    30,
)
_POST_TOOL = _rule(
    ProviderEventName.POST_TOOL_USE,
    WorkLifecycle.ACTIVE,
    NextActor.PROVIDER,
    40,
    request_state=ProviderRequestState.RESOLVED,
    request_kind=RequestKind.PERMISSION,
)
_FAILURE = _rule(
    ProviderEventName.POST_TOOL_USE_FAILURE,
    WorkLifecycle.FAILED,
    NextActor.NONE,
    90,
    request_state=ProviderRequestState.RESOLVED,
    request_kind=RequestKind.PERMISSION,
)
_PERMISSION_REQUEST = _rule(
    ProviderEventName.PERMISSION_REQUEST,
    WorkLifecycle.WAITING,
    NextActor.USER,
    70,
    request_state=ProviderRequestState.LIVE,
    request_kind=RequestKind.PERMISSION,
)
_PERMISSION_DENIED = _rule(
    ProviderEventName.PERMISSION_DENIED,
    WorkLifecycle.FAILED,
    NextActor.NONE,
    91,
    request_state=ProviderRequestState.RESOLVED,
    request_kind=RequestKind.PERMISSION,
)
_NOTIFICATION = _rule(
    ProviderEventName.NOTIFICATION,
    None,
    NextActor.UNKNOWN,
    50,
)
_PRE_COMPACT = _rule(
    ProviderEventName.PRE_COMPACT,
    WorkLifecycle.ACTIVE,
    NextActor.PROVIDER,
    25,
)
_POST_COMPACT = _rule(
    ProviderEventName.POST_COMPACT,
    WorkLifecycle.ACTIVE,
    NextActor.PROVIDER,
    26,
)
_SUBAGENT_START = _rule(
    ProviderEventName.SUBAGENT_START,
    WorkLifecycle.ACTIVE,
    NextActor.PROVIDER,
    27,
)
_SUBAGENT_STOP = _rule(
    ProviderEventName.SUBAGENT_STOP,
    WorkLifecycle.COMPLETED,
    NextActor.NONE,
    80,
)
_STOP = _rule(
    ProviderEventName.STOP,
    WorkLifecycle.COMPLETED,
    NextActor.NONE,
    81,
    request_state=ProviderRequestState.RESOLVED,
    request_kind=RequestKind.UNKNOWN,
)
_STOP_FAILURE = _rule(
    ProviderEventName.STOP_FAILURE,
    WorkLifecycle.FAILED,
    NextActor.NONE,
    92,
    request_state=ProviderRequestState.RESOLVED,
    request_kind=RequestKind.UNKNOWN,
)
_STOP_INTERRUPTED = _rule(
    ProviderEventName.STOP_INTERRUPTED,
    WorkLifecycle.UNKNOWN,
    NextActor.NONE,
    85,
    request_state=ProviderRequestState.RESOLVED,
    request_kind=RequestKind.UNKNOWN,
)
_STOP_INCOMPLETE = _rule(
    ProviderEventName.STOP_INCOMPLETE,
    WorkLifecycle.UNKNOWN,
    NextActor.NONE,
    84,
    request_state=ProviderRequestState.RESOLVED,
    request_kind=RequestKind.UNKNOWN,
)
_SESSION_END = _rule(
    ProviderEventName.SESSION_END,
    WorkLifecycle.COMPLETED,
    NextActor.NONE,
    82,
    request_state=ProviderRequestState.RESOLVED,
    request_kind=RequestKind.UNKNOWN,
)
_SESSION_FINALIZE = _rule(
    ProviderEventName.SESSION_FINALIZE,
    WorkLifecycle.UNKNOWN,
    NextActor.NONE,
    86,
    request_state=ProviderRequestState.RESOLVED,
    request_kind=RequestKind.UNKNOWN,
)
_API_REQUEST_ERROR = _rule(
    ProviderEventName.API_REQUEST_ERROR,
    WorkLifecycle.ACTIVE,
    NextActor.PROVIDER,
    45,
)


# These are complete static ingress tables. Native aliases are listed only for
# providers whose installed hook schemas use them. Canonical aliases are also
# admitted for those providers because the existing parser normalizes before it
# creates HookEvent.
_PROVIDER_EVENT_RULES: Final[dict[str, dict[str, _EventRule]]] = {
    "codex": {
        "SessionStart": _SESSION_START,
        "UserPromptSubmit": _USER_PROMPT,
        "PreToolUse": _PRE_TOOL,
        "PostToolUse": _POST_TOOL,
        "PermissionRequest": _PERMISSION_REQUEST,
        "PreCompact": _PRE_COMPACT,
        "PostCompact": _POST_COMPACT,
        "SubagentStart": _SUBAGENT_START,
        "SubagentStop": _SUBAGENT_STOP,
        "Stop": _STOP,
        "StopFailure": _STOP_FAILURE,
    },
    "claude": {
        "SessionStart": _SESSION_START,
        "UserPromptSubmit": _USER_PROMPT,
        "PreToolUse": _PRE_TOOL,
        "PostToolUse": _POST_TOOL,
        "PostToolUseFailure": _FAILURE,
        "PermissionRequest": _PERMISSION_REQUEST,
        "Notification": _NOTIFICATION,
        "PreCompact": _PRE_COMPACT,
        "PostCompact": _POST_COMPACT,
        "SubagentStop": _SUBAGENT_STOP,
        "Stop": _STOP,
        "SessionEnd": _SESSION_END,
    },
    "devin": {
        "SessionStart": _SESSION_START,
        "UserPromptSubmit": _USER_PROMPT,
        "PreToolUse": _PRE_TOOL,
        "PostToolUse": _POST_TOOL,
        "PermissionRequest": _PERMISSION_REQUEST,
        "PostCompaction": _POST_COMPACT,
        "PostCompact": _POST_COMPACT,
        "Stop": _STOP,
        "SessionEnd": _SESSION_END,
    },
    "grok": {
        "SessionStart": _SESSION_START,
        "UserPromptSubmit": _USER_PROMPT,
        "PreToolUse": _PRE_TOOL,
        "PostToolUse": _POST_TOOL,
        "PostToolUseFailure": _FAILURE,
        "PermissionDenied": _PERMISSION_DENIED,
        "Notification": _NOTIFICATION,
        "PreCompact": _PRE_COMPACT,
        "PostCompact": _POST_COMPACT,
        "SubagentStart": _SUBAGENT_START,
        "SubagentStop": _SUBAGENT_STOP,
        "Stop": _STOP,
        "StopFailure": _STOP_FAILURE,
        "SessionEnd": _SESSION_END,
    },
    "cursor": {
        "sessionStart": _SESSION_START,
        "beforeSubmitPrompt": _USER_PROMPT,
        "preToolUse": _PRE_TOOL,
        "postToolUse": _POST_TOOL,
        "postToolUseFailure": _FAILURE,
        "beforeShellExecution": _PRE_TOOL,
        "afterShellExecution": _POST_TOOL,
        "beforeMCPExecution": _PRE_TOOL,
        "afterMCPExecution": _POST_TOOL,
        "subagentStart": _SUBAGENT_START,
        "subagentStop": _SUBAGENT_STOP,
        "preCompact": _PRE_COMPACT,
        "stop": _STOP,
        "stop:error": _STOP_FAILURE,
        "stop:aborted": _STOP_INTERRUPTED,
        "sessionEnd": _SESSION_END,
        "SessionStart": _SESSION_START,
        "UserPromptSubmit": _USER_PROMPT,
        "PreToolUse": _PRE_TOOL,
        "PostToolUse": _POST_TOOL,
        "PostToolUseFailure": _FAILURE,
        "SubagentStart": _SUBAGENT_START,
        "SubagentStop": _SUBAGENT_STOP,
        "PreCompact": _PRE_COMPACT,
        "Stop": _STOP,
        "StopFailure": _STOP_FAILURE,
        "StopInterrupted": _STOP_INTERRUPTED,
        "SessionEnd": _SESSION_END,
    },
    "hermes": {
        "on_session_start": _SESSION_START,
        "pre_llm_call": _USER_PROMPT,
        "pre_tool_call": _PRE_TOOL,
        "post_tool_call": _POST_TOOL,
        "subagent_start": _SUBAGENT_START,
        "subagent_stop": _SUBAGENT_STOP,
        "on_session_end": _SESSION_END,
        "on_session_end:completed": _STOP,
        "on_session_end:failed": _STOP_FAILURE,
        "on_session_end:interrupted": _STOP_INTERRUPTED,
        "on_session_end:incomplete": _STOP_INCOMPLETE,
        "on_session_finalize": _SESSION_FINALIZE,
        "api_request_error": _API_REQUEST_ERROR,
        "SessionStart": _SESSION_START,
        "UserPromptSubmit": _USER_PROMPT,
        "PreToolUse": _PRE_TOOL,
        "PostToolUse": _POST_TOOL,
        "SubagentStart": _SUBAGENT_START,
        "SubagentStop": _SUBAGENT_STOP,
        "SessionEnd": _SESSION_END,
        "HermesTurnEnd": _SESSION_END,
        "Stop": _STOP,
        "StopFailure": _STOP_FAILURE,
        "StopInterrupted": _STOP_INTERRUPTED,
        "StopIncomplete": _STOP_INCOMPLETE,
        "SessionFinalize": _SESSION_FINALIZE,
        "ApiRequestError": _API_REQUEST_ERROR,
    },
    "openclaw": {
        "SessionStart": _SESSION_START,
        "UserPromptSubmit": _USER_PROMPT,
        "Stop": _STOP,
        "SessionEnd": _SESSION_END,
    },
    "opencode": {
        "SessionStart": _SESSION_START,
        "UserPromptSubmit": _USER_PROMPT,
        "PreToolUse": _PRE_TOOL,
        "PostToolUse": _POST_TOOL,
        "PostToolUseFailure": _FAILURE,
        "PermissionRequest": _PERMISSION_REQUEST,
        "Notification": _NOTIFICATION,
        "PreCompact": _PRE_COMPACT,
        "PostCompact": _POST_COMPACT,
        "Stop": _STOP,
        "StopFailure": _STOP_FAILURE,
        "SessionEnd": _SESSION_END,
    },
    # Only the three names the installed Antigravity envelope emits, plus the
    # two outcome variants _antigravity_event_rule refines Stop into.
    # Antigravity's native PreInvocation/PostInvocation/PreToolUse names are
    # absent on purpose: two of them are never registered, and the third is a
    # config key that the envelope always replaces before ingest.
    #
    # There is deliberately no PostToolUseFailure row. Antigravity's PostToolUse
    # carries an `error` field that is just the tool's own exit status ("exit
    # status 1"), and its loop keeps running afterwards -- an agent whose test
    # command exits non-zero has NOT failed. Mapping that to a FAILED lifecycle
    # would blink the blocked light, which blinks until dealt with, for a
    # routine failing test. Antigravity establishes failure at Stop, via
    # terminationReason, and that is the only place this provider reads it.
    "antigravity": {
        "UserPromptSubmit": _USER_PROMPT,
        "PostToolUse": _POST_TOOL,
        "Stop": _STOP,
        "StopFailure": _STOP_FAILURE,
        "StopIncomplete": _STOP_INCOMPLETE,
    },
    # Kiro installs exactly the five lifecycle hooks its agent files
    # support; there is no ask- or failure-shaped event to map.
    "kiro": {
        "SessionStart": _SESSION_START,
        "UserPromptSubmit": _USER_PROMPT,
        "PreToolUse": _PRE_TOOL,
        "PostToolUse": _POST_TOOL,
        "Stop": _STOP,
    },
}

_NOTIFICATION_KINDS: Final[dict[str, dict[str, NotificationKind]]] = {
    "claude": {
        "permission_prompt": NotificationKind.PERMISSION_REQUEST,
        "input_required": NotificationKind.INPUT_REQUIRED,
        "completed": NotificationKind.WORK_COMPLETED,
        "failed": NotificationKind.WORK_FAILED,
    },
    "grok": {
        "permission_prompt": NotificationKind.PERMISSION_REQUEST,
        "input_required": NotificationKind.INPUT_REQUIRED,
        "completed": NotificationKind.WORK_COMPLETED,
        "failed": NotificationKind.WORK_FAILED,
    },
    "opencode": {
        "permission_prompt": NotificationKind.PERMISSION_REQUEST,
        "input_required": NotificationKind.INPUT_REQUIRED,
        "completed": NotificationKind.WORK_COMPLETED,
        "failed": NotificationKind.WORK_FAILED,
    },
}


def _record_matches_provider_table(record: NormalizedProviderRecord) -> bool:
    if (
        record.source_key.adapter_id != "hooks"
        or record.source_key.capability_id != _LIVE_EVENTS_CAPABILITY
    ):
        return False
    rules = _PROVIDER_EVENT_RULES.get(record.source_key.provider_id)
    if rules is None or not any(
        rule.event_name is record.event_name for rule in rules.values()
    ):
        return False
    if record.event_name is not ProviderEventName.NOTIFICATION:
        return record.notification_kind is None
    if record.notification_kind is None:
        return True
    return record.notification_kind in _NOTIFICATION_KINDS.get(
        record.source_key.provider_id,
        {},
    ).values()

_EVENT_ID_FIELDS: Final = ("event_id", "eventId", "hook_event_id", "hookEventId")
_REQUEST_ID_FIELDS: Final = (
    "request_id",
    "requestId",
    "permission_request_id",
    "permissionRequestId",
)
_SEQUENCE_FIELDS: Final = ("sequence", "event_sequence", "eventSequence")
_NOTIFICATION_KIND_FIELDS: Final = ("notification_type", "notificationType")


def _finite_nonnegative(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0.0


def _optional_exact(value: object, expected: type[object]) -> bool:
    return value is None or type(value) is expected


def _valid_source_key(value: object) -> bool:
    if type(value) is not SourceKey:
        return False
    components = (
        value.provider_id,
        value.adapter_id,
        value.source_instance_id,
        value.capability_id,
    )
    if not all(type(component) is str for component in components):
        return False
    try:
        ProviderIdentifier(value.provider_id)
        AdapterIdentifier(value.adapter_id)
        SourceInstanceIdentifier(value.source_instance_id)
        CapabilityIdentifier(value.capability_id)
    except ContractValidationError:
        return False
    return True


def _safe_label(source_key: SourceKey, work_id: WorkIdentifier | None) -> str:
    provider_label = _PRODUCT_PROVIDER_LABELS.get(source_key.provider_id, "Provider")
    if work_id is None:
        return provider_label
    return f"{provider_label} {work_id.value}"


def _diagnostic(identifier: str) -> ProviderFactDiagnostic:
    return ProviderFactDiagnostic(DiagnosticIdentifier(identifier), 1)


def _inert(source_key: SourceKey, occurred_at_epoch: float, identifier: str) -> InertProviderRecord:
    return InertProviderRecord(source_key, occurred_at_epoch, _diagnostic(identifier))


def _event_epoch(record: HookEvent) -> float | None:
    if type(record.logged_at) is not datetime:
        return None
    try:
        if record.logged_at.tzinfo is None or record.logged_at.utcoffset() is None:
            return None
        epoch = record.logged_at.timestamp()
    except (OverflowError, OSError, ValueError):
        return None
    return float(epoch) if _finite_nonnegative(epoch) else None


def _first_raw_value(raw: object, fields: tuple[str, ...]) -> object | None:
    if type(raw) is not dict:
        return None
    for field in fields:
        if field in raw:
            return raw[field]
    return None


def _work_identifier(value: object) -> WorkIdentifier | None:
    if type(value) is not str:
        return None
    try:
        return WorkIdentifier(value)
    except ProviderFactValidationError:
        return None


def _opaque_provider_identity(value: object, *, max_bytes: int) -> bool:
    if type(value) is not str:
        return False
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return bool(
        encoded
        and len(encoded) <= max_bytes
        and _OPAQUE_PROVIDER_IDENTITY.fullmatch(value)
        and not _CREDENTIAL_SHAPED_IDENTITY.search(value)
    )


def _cursor_work_identity(record: HookEvent) -> tuple[bool, WorkIdentifier | None]:
    raw = record.raw
    if type(raw) is not dict or "conversation_id" not in raw:
        return True, _work_identifier(
            record.agent_id if record.agent_id is not None else record.session_id
        )
    conversation_id = raw["conversation_id"]
    if not _opaque_provider_identity(conversation_id, max_bytes=128):
        return False, None
    for alias in ("session_id", "sessionId"):
        if alias in raw and (
            type(raw[alias]) is not str or raw[alias] != conversation_id
        ):
            return False, None
    if record.session_id is not None and (
        type(record.session_id) is not str or record.session_id != conversation_id
    ):
        return False, None
    if record.agent_id is not None:
        return False, None
    digest = hashlib.sha256()
    digest.update(_CURSOR_CONVERSATION_HASH_DOMAIN)
    digest.update(conversation_id.encode("ascii"))
    return True, WorkIdentifier(digest.hexdigest())


def _hermes_work_identity(record: HookEvent) -> tuple[bool, WorkIdentifier | None]:
    raw = record.raw
    raw_session = raw.get("session_id") if type(raw) is dict else None
    if type(raw) is dict and "session_id" in raw:
        if (
            not _opaque_provider_identity(raw_session, max_bytes=64)
            or record.session_id != raw_session
        ):
            return False, None
    work_value = record.agent_id if record.agent_id is not None else record.session_id
    if work_value is not None and not _opaque_provider_identity(work_value, max_bytes=64):
        return False, None
    work_id = _work_identifier(work_value)
    return (work_value is None or work_id is not None), work_id


def _antigravity_work_identity(record: HookEvent) -> tuple[bool, WorkIdentifier | None]:
    """conversationId is the ONLY name Antigravity gives a unit of work.

    Its payload has no session_id, no turn id and no agent id, so this is the
    single field the whole provider hangs on. It is read straight out of the
    installed bridge's envelope and held to the same opaque-identifier rule as
    every other provider's identity: a value that is not a plain bounded token
    -- or that looks like a credential -- fails the event closed rather than
    becoming a row label the ledger would then display.
    """
    envelope = _antigravity_envelope(record)
    if envelope is None or "conversationId" not in envelope:
        return True, None
    if not _opaque_provider_identity(envelope["conversationId"], max_bytes=64):
        return False, None
    return True, _work_identifier(envelope["conversationId"])


def _provider_work_identity(
    provider: str,
    record: HookEvent,
) -> tuple[bool, WorkIdentifier | None]:
    if provider == "cursor":
        return _cursor_work_identity(record)
    if provider == "hermes":
        return _hermes_work_identity(record)
    if provider == "antigravity":
        return _antigravity_work_identity(record)
    work_value = record.agent_id if record.agent_id is not None else record.session_id
    return True, _work_identifier(work_value)


def _request_identifier(value: object) -> RequestIdentifier | None:
    if type(value) is not str:
        return None
    try:
        return RequestIdentifier(value)
    except ProviderFactValidationError:
        return None


def _event_token(value: object) -> EventToken | None:
    if type(value) is not str:
        return None
    try:
        return EventToken(value)
    except ProviderFactValidationError:
        return None


def _sequence(raw: object) -> int | None:
    value = _first_raw_value(raw, _SEQUENCE_FIELDS)
    if type(value) is int and 0 <= value <= MAX_PROVIDER_SEQUENCE:
        return value
    return None


def _derived_event_token(
    source_key: SourceKey,
    event_name: ProviderEventName,
    occurred_at_epoch: float,
    work_id: WorkIdentifier | None,
    request_id: RequestIdentifier | None,
    sequence: int | None,
) -> EventToken:
    digest = hashlib.sha256()
    values = (
        source_key.provider_id,
        source_key.adapter_id,
        source_key.source_instance_id,
        source_key.capability_id,
        event_name.value,
        format(occurred_at_epoch, ".6f"),
        "" if work_id is None else work_id.value,
        "" if request_id is None else request_id.value,
        "" if sequence is None else str(sequence),
    )
    for value in values:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return EventToken(digest.hexdigest())


def _contract_matches_source(
    source_key: SourceKey,
    contract: NegotiatedProviderContract,
) -> bool:
    if type(contract) is not NegotiatedProviderContract:
        return False
    if contract.status not in {ContractStatus.SUPPORTED, ContractStatus.PARTIAL}:
        return False
    if (
        contract.provider_id.value != source_key.provider_id
        or contract.adapter_id.value != source_key.adapter_id
        or contract.source_instance_id.value != source_key.source_instance_id
    ):
        return False
    return any(
        capability.identifier.value == source_key.capability_id
        for capability in contract.observation_capabilities
    )


def _has_observation_capability(
    contract: NegotiatedProviderContract,
    capability_id: str,
) -> bool:
    return any(
        capability.identifier.value == capability_id
        for capability in contract.observation_capabilities
    )


def _notification_kind(
    provider: str,
    raw: object,
) -> tuple[bool, NotificationKind | None]:
    present = type(raw) is dict and any(
        field in raw for field in _NOTIFICATION_KIND_FIELDS
    )
    value = _first_raw_value(raw, _NOTIFICATION_KIND_FIELDS)
    if type(value) is not str:
        return present, None
    return present, _NOTIFICATION_KINDS.get(provider, {}).get(value)


def _native_event_name(raw: object) -> str | None:
    if type(raw) is not dict:
        return None
    value = raw.get("hook_event_name")
    return value if type(value) is str else None


def _cursor_event_rule(record: HookEvent) -> tuple[_EventRule | None, str | None]:
    rules = _PROVIDER_EVENT_RULES["cursor"]
    rule = rules.get(record.event_name)
    native = _native_event_name(record.raw)
    if native != "stop" and record.event_name != "stop":
        return rule, None
    raw = record.raw
    if type(raw) is not dict or "status" not in raw:
        return rule, None
    status = raw["status"]
    if type(status) is not str:
        return None, "invalid_provider_outcome"
    status_rule = {
        "completed": _STOP,
        "error": _STOP_FAILURE,
        "aborted": _STOP_INTERRUPTED,
    }.get(status)
    if status_rule is None:
        return None, "invalid_provider_outcome"
    return status_rule, None


def _hermes_outcome_value(
    raw: dict[object, object],
    field: str,
) -> tuple[bool, bool, object]:
    extra = raw.get("extra")
    top_present = field in raw
    extra_present = type(extra) is dict and field in extra
    if top_present and extra_present:
        top_value = raw[field]
        extra_value = extra[field]
        valid = (
            type(top_value) is bool
            and type(extra_value) is bool
            and top_value is extra_value
        )
        return True, valid, top_value if valid else None
    if top_present:
        value = raw[field]
        return True, type(value) is bool, value
    if extra_present:
        value = extra[field]
        return True, type(value) is bool, value
    return False, False, None


def _hermes_event_rule(record: HookEvent) -> tuple[_EventRule | None, str | None]:
    rules = _PROVIDER_EVENT_RULES["hermes"]
    rule = rules.get(record.event_name)
    native = _native_event_name(record.raw)
    if native != "on_session_end" and record.event_name != "HermesTurnEnd":
        return rule, None
    raw = record.raw
    if type(raw) is not dict:
        return rule, None
    values = tuple(
        _hermes_outcome_value(raw, field)
        for field in ("completed", "failed", "interrupted")
    )
    if not any(present for present, _valid, _value in values):
        return (_STOP_INCOMPLETE if native == "on_session_end" else rule), None
    if not all(present and valid for present, valid, _value in values):
        return None, "invalid_provider_outcome"
    completed, failed, interrupted = (
        value for _present, _valid, value in values
    )
    if sum((completed, failed, interrupted)) > 1:
        return None, "invalid_provider_outcome"
    if completed:
        return _STOP, None
    if failed:
        return _STOP_FAILURE, None
    if interrupted:
        return _STOP_INTERRUPTED, None
    return _STOP_INCOMPLETE, None


def _antigravity_envelope(record: HookEvent) -> dict[object, object] | None:
    raw = record.raw
    if type(raw) is not dict:
        return None
    envelope = raw.get(_ANTIGRAVITY_ENVELOPE_KEY)
    return envelope if type(envelope) is dict else None


def _antigravity_event_rule(record: HookEvent) -> tuple[_EventRule | None, str | None]:
    """Refine Antigravity's single Stop event into its real outcome.

    Antigravity fires one `Stop` when the execution loop terminates and says
    why in `terminationReason`. Its own documentation lists the values with an
    "e.g.", so the set is explicitly OPEN -- an unlisted reason is a reason
    this build has not seen, not a malformed payload, and it still means the
    loop ended. That is why an unrecognized string lands on STOP_INCOMPLETE
    (lifecycle UNKNOWN, no next actor) rather than being dropped: dropping a
    Stop would leave the work ACTIVE in the ledger forever. A non-string, by
    contrast, really is malformed and fails closed.

    `fullyIdle` is honoured for the reason this project already learned once:
    a parent is not finished while work it started is still running. When
    Antigravity says background tasks remain, COMPLETED would be a lie.
    """
    rules = _PROVIDER_EVENT_RULES["antigravity"]
    rule = rules.get(record.event_name)
    if record.event_name != "Stop":
        return rule, None
    envelope = _antigravity_envelope(record)
    if envelope is None:
        return rule, None

    fully_idle = envelope.get("fullyIdle")
    if "fullyIdle" in envelope and type(fully_idle) is not bool:
        return None, "invalid_provider_outcome"

    reason = envelope.get("terminationReason")
    if reason is not None and type(reason) is not str:
        return None, "invalid_provider_outcome"
    error = envelope.get("error")
    if error is not None and type(error) is not str:
        return None, "invalid_provider_outcome"

    if reason == "error" or (not reason and error):
        return _STOP_FAILURE, None
    if fully_idle is False:
        return _STOP_INCOMPLETE, None
    if not reason or reason == "model_stop":
        return _STOP, None
    return _STOP_INCOMPLETE, None


def _provider_event_rule(record: HookEvent) -> tuple[_EventRule | None, str | None]:
    if record.provider == "cursor":
        return _cursor_event_rule(record)
    if record.provider == "hermes":
        return _hermes_event_rule(record)
    if record.provider == "antigravity":
        return _antigravity_event_rule(record)
    return _PROVIDER_EVENT_RULES.get(record.provider, {}).get(record.event_name), None


def minimize_hook_event(
    record: HookEvent,
    *,
    source_key: SourceKey,
    contract: NegotiatedProviderContract,
    observation_authority: ObservationAuthority,
) -> NormalizedProviderRecord | InertProviderRecord:
    """Copy only allowlisted typed scalars from one legacy hook event."""
    if not _valid_source_key(source_key):
        raise ProviderAdapterValidationError("invalid source key")
    if type(observation_authority) is not ObservationAuthority:
        raise ProviderAdapterValidationError("invalid observation authority")
    if type(record) is not HookEvent:
        return _inert(source_key, 0.0, "invalid_hook_event")

    occurred_at = _event_epoch(record)
    if occurred_at is None:
        return _inert(source_key, 0.0, "invalid_event_time")
    if not _contract_matches_source(source_key, contract) or (
        source_key.capability_id != _LIVE_EVENTS_CAPABILITY
    ):
        return _inert(source_key, occurred_at, "contract_not_observable")
    if type(record.provider) is not str or record.provider != source_key.provider_id:
        return _inert(source_key, occurred_at, "source_identity_mismatch")
    if type(record.event_name) is not str:
        return _inert(source_key, occurred_at, "unknown_provider_event")

    rule, outcome_diagnostic = _provider_event_rule(record)
    if outcome_diagnostic is not None:
        return _inert(source_key, occurred_at, outcome_diagnostic)
    if rule is None:
        return _inert(source_key, occurred_at, "unknown_provider_event")

    identity_valid, work_id = _provider_work_identity(source_key.provider_id, record)
    if not identity_valid:
        return _inert(source_key, occurred_at, "invalid_provider_identity")
    parent_id = None
    if record.agent_id is not None:
        candidate_parent = _work_identifier(record.session_id)
        if candidate_parent != work_id:
            parent_id = candidate_parent
    request_id = _request_identifier(_first_raw_value(record.raw, _REQUEST_ID_FIELDS))
    sequence = _sequence(record.raw)
    token = _event_token(_first_raw_value(record.raw, _EVENT_ID_FIELDS))
    if token is None:
        token = _derived_event_token(
            source_key,
            rule.event_name,
            occurred_at,
            work_id,
            request_id,
            sequence,
        )
    notification_kind = None
    if rule.event_name is ProviderEventName.NOTIFICATION:
        notification_present, notification_kind = _notification_kind(
            source_key.provider_id,
            record.raw,
        )
        if notification_present and notification_kind is None:
            return _inert(source_key, occurred_at, "unknown_notification_kind")
    return NormalizedProviderRecord(
        source_key=source_key,
        event_name=rule.event_name,
        occurred_at_epoch=occurred_at,
        event_token=token,
        provider_work_id=work_id,
        provider_request_id=request_id,
        parent_work_id=parent_id,
        safe_label=_safe_label(source_key, work_id),
        notification_kind=notification_kind,
        sequence=sequence,
        terminal_cause=(
            ProviderTerminalCause.CODEX_USAGE_LIMIT
            if (
                source_key.provider_id == "codex"
                and rule.event_name is ProviderEventName.STOP_FAILURE
                and record._terminal_provenance
                is _CODEX_TRANSCRIPT_USAGE_LIMIT_PROVENANCE
            )
            else ProviderTerminalCause.NONE
        ),
    )


def _rule_for_record(record: NormalizedProviderRecord) -> _EventRule:
    rules = _PROVIDER_EVENT_RULES[record.source_key.provider_id]
    return next(rule for rule in rules.values() if rule.event_name is record.event_name)


def _notification_rule(kind: NotificationKind) -> _EventRule:
    if kind is NotificationKind.PERMISSION_REQUEST:
        return _rule(
            ProviderEventName.NOTIFICATION,
            WorkLifecycle.WAITING,
            NextActor.USER,
            71,
            request_state=ProviderRequestState.LIVE,
            request_kind=RequestKind.PERMISSION,
        )
    if kind is NotificationKind.INPUT_REQUIRED:
        return _rule(
            ProviderEventName.NOTIFICATION,
            WorkLifecycle.WAITING,
            NextActor.USER,
            72,
            request_state=ProviderRequestState.LIVE,
            request_kind=RequestKind.INPUT,
        )
    if kind is NotificationKind.WORK_COMPLETED:
        return _rule(
            ProviderEventName.NOTIFICATION,
            WorkLifecycle.COMPLETED,
            NextActor.NONE,
            83,
        )
    return _rule(
        ProviderEventName.NOTIFICATION,
        WorkLifecycle.FAILED,
        NextActor.NONE,
        93,
    )


def _watermark(
    record: NormalizedProviderRecord | InertProviderRecord,
) -> ProviderWatermark:
    if type(record) is NormalizedProviderRecord:
        sequence = record.sequence
        token = record.event_token
        rank = _rule_for_record(record).tie_break_rank
        if record.notification_kind is not None:
            rank = _notification_rule(record.notification_kind).tie_break_rank
    else:
        sequence = None
        rank = 0
        digest = hashlib.sha256(
            (
                f"{record.source_key.provider_id}\0{record.source_key.adapter_id}\0"
                f"{record.source_key.source_instance_id}\0{record.source_key.capability_id}\0"
                f"{record.occurred_at_epoch:.6f}\0{record.diagnostic.identifier.value}"
            ).encode()
        ).hexdigest()
        token = EventToken(digest)
    return ProviderWatermark(
        source_key=record.source_key,
        basis=(
            WatermarkBasis.PROVIDER_SEQUENCE
            if sequence is not None
            else WatermarkBasis.PROVIDER_EVENT_ID
        ),
        occurred_at_epoch=record.occurred_at_epoch,
        event_token=token,
        sequence=sequence,
        tie_break_rank=rank,
    )


def provider_facts_for_record(
    record: NormalizedProviderRecord | InertProviderRecord,
    *,
    contract: NegotiatedProviderContract,
    observation_authority: ObservationAuthority,
    observed_at_epoch: float,
) -> ProviderFactBatch:
    """Map one minimized record to source-scoped canonical facts."""
    if type(record) not in {NormalizedProviderRecord, InertProviderRecord}:
        raise ProviderAdapterValidationError("invalid provider record")
    if type(observation_authority) is not ObservationAuthority:
        raise ProviderAdapterValidationError("invalid observation authority")
    if not _finite_nonnegative(observed_at_epoch):
        raise ProviderAdapterValidationError("invalid observation time")
    if not _contract_matches_source(record.source_key, contract):
        raise ProviderAdapterValidationError("record and contract source mismatch")

    watermark = _watermark(record)
    if type(record) is InertProviderRecord:
        return ProviderFactBatch(
            source_key=record.source_key,
            observation_authority=observation_authority,
            source_health=SourceHealth.PARTIAL,
            source_freshness=SourceFreshness.PARTIAL,
            observed_at_epoch=observed_at_epoch,
            watermark=watermark,
            work_facts=(),
            request_facts=(),
            diagnostics=(record.diagnostic,),
        )

    rule = _rule_for_record(record)
    if record.notification_kind is not None:
        rule = _notification_rule(record.notification_kind)
    work_key = (
        None
        if record.provider_work_id is None
        else WorkKey(record.source_key, record.provider_work_id)
    )
    diagnostics: list[ProviderFactDiagnostic] = []
    work_facts: tuple[ProviderWorkFact, ...] = ()
    request_facts: tuple[ProviderRequestFact, ...] = ()

    if rule.lifecycle is not None:
        if work_key is None:
            diagnostics.append(_diagnostic("missing_work_identity"))
        else:
            parent_key = (
                None
                if record.parent_work_id is None
                else WorkKey(record.source_key, record.parent_work_id)
            )
            work_facts = (
                ProviderWorkFact(
                    key=work_key,
                    lifecycle=rule.lifecycle,
                    watermark=watermark,
                    safe_label=record.safe_label,
                    parent_key=parent_key,
                    next_actor=rule.next_actor,
                    terminal_cause=(
                        ProviderTerminalCause.CODEX_USAGE_LIMIT
                        if record.terminal_cause is ProviderTerminalCause.CODEX_USAGE_LIMIT
                        else ProviderTerminalCause.NONE
                    ),
                ),
            )

    if rule.request_state is not None:
        if record.provider_request_id is None:
            if rule.request_state is ProviderRequestState.LIVE:
                diagnostics.append(_diagnostic("missing_request_identity"))
        elif work_key is None:
            if not diagnostics:
                diagnostics.append(_diagnostic("missing_work_identity"))
        elif not _has_observation_capability(contract, _ACTIONABLE_REQUESTS_CAPABILITY):
            diagnostics.append(_diagnostic("request_capability_unavailable"))
        elif observation_authority < ObservationAuthority.DIRECT_PROVIDER_OBSERVATION:
            diagnostics.append(_diagnostic("insufficient_request_authority"))
        else:
            request_facts = (
                ProviderRequestFact(
                    key=RequestKey(work_key, record.provider_request_id),
                    state=rule.request_state,
                    request_kind=rule.request_kind,
                    next_actor=(
                        NextActor.USER
                        if rule.request_state is ProviderRequestState.LIVE
                        else NextActor.NONE
                    ),
                    watermark=watermark,
                ),
            )

    if observation_authority is ObservationAuthority.UNTRUSTED_HINT:
        work_facts = ()
        request_facts = ()
        diagnostics = [_diagnostic("insufficient_observation_authority")]

    partial = bool(diagnostics)
    return ProviderFactBatch(
        source_key=record.source_key,
        observation_authority=observation_authority,
        source_health=SourceHealth.PARTIAL if partial else SourceHealth.HEALTHY,
        source_freshness=SourceFreshness.PARTIAL if partial else SourceFreshness.FRESH,
        observed_at_epoch=observed_at_epoch,
        watermark=watermark,
        work_facts=work_facts,
        request_facts=request_facts,
        diagnostics=tuple(diagnostics),
    )


def _version_payload() -> dict[str, object]:
    return {
        "major": _RECORD_SCHEMA_VERSION.major,
        "minor": _RECORD_SCHEMA_VERSION.minor,
    }


def _source_payload(source_key: SourceKey) -> dict[str, object]:
    return {
        "provider_id": source_key.provider_id,
        "adapter_id": source_key.adapter_id,
        "source_instance_id": source_key.source_instance_id,
        "capability_id": source_key.capability_id,
    }


def normalized_provider_record_to_payload(
    record: NormalizedProviderRecord | InertProviderRecord,
) -> dict[str, object]:
    """Encode exactly one content-free normalized record schema."""
    if type(record) is NormalizedProviderRecord:
        return {
            "version": _version_payload(),
            "record_kind": "normalized",
            **_source_payload(record.source_key),
            "event_name": record.event_name.value,
            "occurred_at_epoch": record.occurred_at_epoch,
            "event_token": record.event_token.value,
            "provider_work_id": (
                None if record.provider_work_id is None else record.provider_work_id.value
            ),
            "provider_request_id": (
                None
                if record.provider_request_id is None
                else record.provider_request_id.value
            ),
            "parent_work_id": (
                None if record.parent_work_id is None else record.parent_work_id.value
            ),
            "safe_label": record.safe_label,
            "notification_kind": (
                None if record.notification_kind is None else record.notification_kind.value
            ),
            "sequence": record.sequence,
        }
    if type(record) is InertProviderRecord:
        return {
            "version": _version_payload(),
            "record_kind": "inert",
            **_source_payload(record.source_key),
            "occurred_at_epoch": record.occurred_at_epoch,
            "diagnostic_id": record.diagnostic.identifier.value,
            "diagnostic_count": record.diagnostic.count,
        }
    raise ProviderAdapterValidationError("invalid provider record")


def _has_exact_fields(payload: dict[object, object], fields: frozenset[str]) -> bool:
    if len(payload) != len(fields):
        return False
    return all(type(key) is str and key in fields for key in payload)


def _valid_version(payload: object) -> bool:
    return (
        type(payload) is dict
        and _has_exact_fields(payload, _VERSION_FIELDS)
        and type(payload["major"]) is int
        and type(payload["minor"]) is int
        and payload["major"] == _RECORD_SCHEMA_VERSION.major
        and payload["minor"] == _RECORD_SCHEMA_VERSION.minor
    )


def _source_from_payload(payload: dict[object, object]) -> SourceKey | None:
    if not all(type(payload[field]) is str for field in _SOURCE_FIELDS):
        return None
    try:
        provider = ProviderIdentifier(payload["provider_id"])
        adapter = AdapterIdentifier(payload["adapter_id"])
        instance = SourceInstanceIdentifier(payload["source_instance_id"])
        capability = CapabilityIdentifier(payload["capability_id"])
        return SourceKey(provider.value, adapter.value, instance.value, capability.value)
    except (CapacityValidationError, ContractValidationError):
        return None


def _optional_work_identifier(value: object) -> WorkIdentifier | bool | None:
    if value is None:
        return None
    if type(value) is not str:
        return False
    try:
        return WorkIdentifier(value)
    except ProviderFactValidationError:
        return False


def _optional_request_identifier(value: object) -> RequestIdentifier | bool | None:
    if value is None:
        return None
    if type(value) is not str:
        return False
    try:
        return RequestIdentifier(value)
    except ProviderFactValidationError:
        return False


def normalized_provider_record_from_payload(
    payload: object,
) -> NormalizedProviderRecord | InertProviderRecord | None:
    """Decode only exact built-in-dict content-free v1 record shapes."""
    if type(payload) is not dict:
        return None
    kind = payload.get("record_kind")
    if type(kind) is not str:
        return None
    expected_fields = (
        _NORMALIZED_FIELDS
        if kind == "normalized"
        else _INERT_FIELDS
        if kind == "inert"
        else None
    )
    if expected_fields is None or not _has_exact_fields(payload, expected_fields):
        return None
    if not _valid_version(payload["version"]):
        return None
    source_key = _source_from_payload(payload)
    occurred_at = payload["occurred_at_epoch"]
    if source_key is None or not _finite_nonnegative(occurred_at):
        return None

    try:
        if kind == "inert":
            diagnostic_id = payload["diagnostic_id"]
            diagnostic_count = payload["diagnostic_count"]
            if type(diagnostic_id) is not str or type(diagnostic_count) is not int:
                return None
            return InertProviderRecord(
                source_key,
                occurred_at,
                ProviderFactDiagnostic(
                    DiagnosticIdentifier(diagnostic_id),
                    diagnostic_count,
                ),
            )

        event_name = payload["event_name"]
        event_token = payload["event_token"]
        safe_label = payload["safe_label"]
        notification_kind = payload["notification_kind"]
        sequence = payload["sequence"]
        if not (
            type(event_name) is str
            and type(event_token) is str
            and type(safe_label) is str
            and (notification_kind is None or type(notification_kind) is str)
            and (sequence is None or type(sequence) is int)
        ):
            return None
        work_id = _optional_work_identifier(payload["provider_work_id"])
        request_id = _optional_request_identifier(payload["provider_request_id"])
        parent_id = _optional_work_identifier(payload["parent_work_id"])
        if work_id is False or request_id is False or parent_id is False:
            return None
        return NormalizedProviderRecord(
            source_key=source_key,
            event_name=ProviderEventName(event_name),
            occurred_at_epoch=occurred_at,
            event_token=EventToken(event_token),
            provider_work_id=work_id,
            provider_request_id=request_id,
            parent_work_id=parent_id,
            safe_label=safe_label,
            notification_kind=(
                None if notification_kind is None else NotificationKind(notification_kind)
            ),
            sequence=sequence,
        )
    except (
        ContractValidationError,
        ProviderAdapterValidationError,
        ProviderFactValidationError,
        ValueError,
    ):
        return None
