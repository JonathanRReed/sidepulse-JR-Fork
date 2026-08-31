# JR Bar P3.36 answer-in-place design

## Goal

Let JR Bar answer an active ask from the Screen Bar or Agent Browser when, and
only when, the current provider instance declares a reviewed answering surface.
The feature must be explicit, capability-gated, generation-fenced, cancellable,
and fail closed to the existing jump-to-session path.

This is a source and native-UI tranche. It does not authorize silent provider
mutation, background retries after app quit, credential changes, new network
permissions, release claims, or automatic answering from LED state alone.

## Product boundary

P3.36 extends only the operator-facing ask surfaces:

- the expanded Screen Bar announcer for the currently selected ask;
- the Agent Browser urgent actions and detail actions;
- the controller-owned action runtime that performs one bounded answer attempt.

The physical LED, Screen Bar collapsed state, notification bindings, local
triage acknowledgement, mailbox seen receipts, and session-opening actions
remain independent. A successful answer-in-place attempt does not clear the
underlying ask until canonical provider observation confirms the request is no
longer actionable.

## Current constraints from the codebase

1. `ProductCapability.ANSWERING` exists, but
   `src/sidepulse/provider_contracts.py` currently maps it to no low-level
   capability. Answering is intentionally inert today.
2. The product already has one safe action path:
   `AgentBrowserActionPayload -> performAgentBrowserPayload_() -> typed
   controller action`. P3.36 should extend this path rather than create an
   unrelated mutation channel.
3. The Screen Bar announcer now owns a generation-fenced, exact-request UI
   surface. It is the right place for inline answer controls, but only in the
   expanded state.
4. Session opening already has a safe fallback path through reviewed
   navigation/session-open helpers. That remains the universal escape hatch.

## Selected approach

### A. Product-owned local answer surface plus pure answer state, selected

Add one product-owned local runtime surface for answering:
`local.answer_in_place`. Providers may explicitly declare that the product
supports answering for an exact source instance only through this local
surface. The product then resolves one exact request into a small immutable
answer plan, renders controls for that plan, and drives one controller-owned
attempt worker with timeout, cancellation, retry, and jump fallback.

Why this is selected:

- it preserves the product contract rule that low-level mutation authority is
  not inferred from a read capability;
- it keeps provider capability support explicit per source instance;
- it reuses the controller's existing typed action path and generation fences;
- it fails closed to Jump when no reviewed answer surface exists.

### B. Infer answering from question support, rejected

Rejected because `questions` is read-only meaning. The contract tests already
prove that `questions` must not imply `answering`.

### C. Bind answering directly to a low-level mutation capability, rejected

Rejected for now because no reviewed product binding exists and the current
contract layer intentionally rejects mutation authority in product invocations.
If a future provider exposes a reviewed low-level answer capability, that can
be added later behind the same product-level answer plan and runtime.

## Pure answer contract

Add one AppKit-free module, `sidepulse.answer_in_place`, with exact typed state:

```python
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


@dataclass(frozen=True, slots=True)
class AnswerCapability:
    supported: bool
    supports_reply_text: bool
    supports_binary_decision: bool
    invocation: ProductCapabilityInvocation | None
    disabled_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AnswerAttempt:
    request_identity: AnnouncerAlertIdentity
    generation: int
    state: AnswerAttemptState
    draft_text: str
    last_error: str | None


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
```

Pure operations:

- `answer_capability_for_request(contract, request_kind)`;
- `reconcile_answer_attempt(previous, request_identity, generation)`;
- `reduce_answer_intent(attempt, action, draft_text=None)`;
- `project_answer_controls(request_kind, capability, attempt)`.

Rules:

1. Every value validates its exact types and bounds.
2. Reply text is single-line for the Screen Bar and bounded to 280 printable
   characters.
3. Permission, approval, and review requests project binary Approve and Deny
   actions only.
4. General input requests project Reply only when the capability explicitly
   supports reply text; otherwise they expose Jump only.
5. Unsupported, stale, or mismatched attempts project no send action and keep
   Jump available.
6. `SENT` is optimistic local state only. Canonical provider observation still
   decides whether the ask cleared.

## Capability negotiation

P3.36 extends the product contract layer in one narrow way:

- `ProductCapability.ANSWERING` may bind to the product-owned local runtime
  surface `local.answer_in_place`.
- It remains invalid to bind `answering` directly to a low-level mutation
  capability in this tranche.
- Negotiation must preserve exact provider, adapter, and source-instance
  identity in the resulting `ProductCapabilityInvocation`.

This keeps the contract honest. The product can say "I know how to answer this
source instance locally" without pretending the provider granted generic
mutation authority.

## Runtime and failure model

The controller owns one bounded in-memory answer runtime keyed by exact request
identity. It must never run in AppKit callbacks.

Runtime rules:

1. Every attempt captures the exact `ProductCapabilityInvocation`,
   request identity, action kind, optional reply text, and source generation.
2. The worker timeout is fixed at 10 seconds.
3. Timeout transitions the attempt to `TIMED_OUT`, preserves the draft, and
   exposes Retry and Jump.
4. Failure transitions the attempt to `FAILED`, preserves the draft, and
   exposes Retry and Jump.
5. Cancel transitions a locally pending attempt to `CANCELLED`. It must never
   claim provider-side rollback if the handler already completed.
6. A fresh reconcile for a different generation or different selected request
   invalidates the old attempt. Stale completion or timeout callbacks must be
   ignored.
7. Restart loses local attempt state. The canonical ask remains visible until
   the provider clears it.

The initial product-owned handler registry is in-process and explicit:

```python
def answer_request(
    invocation: ProductCapabilityInvocation,
    *,
    request_kind: RequestKind,
    answer_kind: AnswerActionKind,
    reply_text: str | None,
) -> None:
    raise NotImplementedError
```

If no exact handler exists for the invocation, the runtime refuses the attempt
without side effects and projects Jump.

## UI contract

### Screen Bar

The collapsed announcer remains unchanged. The expanded announcer may show one
answer row below the question and above the footer only when the selected ask
is answerable.

UI rules:

- binary asks render `Approve`, `Deny`, and `Jump`;
- reply asks render one compact native text field plus `Send` and `Jump`;
- `SENDING` replaces send controls with a progress label plus `Cancel`;
- `FAILED` and `TIMED_OUT` show the exact failure status plus `Retry` and
  `Jump`;
- `SENT` shows `Sent, waiting for source confirmation` and keeps `Jump`.

The panel stays nonactivating and keeps the same pointer-authorization rule as
P3.35. Editing reply text is allowed only after explicit expansion.

### Agent Browser

The Agent Browser gets the same typed answer actions through its existing
payload path. Urgent rows may show `Approve`, `Deny`, `Reply`, or `Jump` only
when the projected answer plan says they are valid. Disabled reasons remain
tooltips, not implied state.

### Fallback behavior

Every answerable surface keeps Jump available. Unsupported asks and any failed,
timed-out, or cancelled send state must still offer Jump to the live session.

## State and acknowledgement semantics

P3.36 does not replace acknowledgement:

- `I'm on It` still records local triage only.
- `Mark Seen` still remains Screen-Bar-local only.
- A successful answer attempt is a different state from acknowledgement and
  must not write triage, mailbox, notification, or LED receipts.
- Only canonical provider observation resolving the request clears the amber ask
  semantics.

## Verification requirements

Required tests and receipts:

1. Pure contract tests for capability gating, request-kind mapping, reply-text
   bounds, state transitions, stale-generation refusal, timeout, retry, cancel,
   and sent-waits-for-provider semantics.
2. Provider-contract tests proving `answering` can bind only to
   `local.answer_in_place` in this tranche and still preserves exact identity.
3. Screen Bar native tests for binary and reply controls, editability,
   nonactivating behavior, stale callback refusal, visible sending and failure
   states, and Jump fallback.
4. Agent Browser/controller tests for payload routing, timeout/failure handling,
   retry/cancel behavior, and proof that answer actions never call triage,
   mailbox seen, notification, or LED acknowledgement paths.
5. One focused source-native receipt for Screen Bar answer states in Aqua and
   Dark Aqua, plus one documentation update that preserves source-only evidence
   boundaries.

## Out of scope

- Background automatic retries after restart.
- Global hotkeys.
- Provider-specific network protocols that lack a reviewed local answer handler.
- Multi-line reply editors or transcript drafting.
- Physical LED semantics changes.
- Installed-app, signing, notarization, packaging, publication, or release
  claims.
