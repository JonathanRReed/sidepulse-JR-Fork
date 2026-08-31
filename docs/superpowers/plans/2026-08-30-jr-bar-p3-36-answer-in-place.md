# JR Bar P3.36 Answer-in-Place Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add capability-gated answer-in-place controls with explicit send state, timeout, retry, cancellation, and jump fallback for active asks.

**Architecture:** Add one AppKit-free answer-state module plus one product-owned local answering capability surface. Reuse the existing typed operator-action path and the P3.35 announcer stack to render and drive inline answer controls without granting implicit provider mutation authority.

**Tech Stack:** Python 3.12, PyObjC/AppKit, pytest, Ruff, current provider-contract and runtime-scheduler infrastructure.

**Spec:** `docs/superpowers/specs/2026-08-30-jr-bar-p3-36-answer-in-place-design.md`

## Global Constraints

- Preserve the intentional dirty `main` worktree and every existing roadmap change.
- Do not commit, push, install, change permissions, publish, deploy, or mutate credentials.
- Do not add a production dependency.
- `ProductCapability.ANSWERING` remains a product-owned local surface in this tranche. Do not infer it from `questions` and do not bind it to a low-level mutation capability.
- `Mark Seen`, local triage acknowledgement, mailbox seen receipts, notification bindings, and physical LED state must remain independent from answer-in-place.
- All provider mutation work must run off the AppKit thread through a bounded timeout.
- A successful local send must not clear the ask until canonical provider observation resolves it.
- Reply text is bounded to one printable line and 280 characters.
- Run focused tests during tasks. Run `make fast` once the tranche stabilizes and one full suite only at the completed priority boundary.

---

### Task 1: Capability negotiation and pure answer state

**Files:**
- Create: `src/sidepulse/answer_in_place.py`
- Create: `tests/test_answer_in_place.py`
- Modify: `src/sidepulse/provider_contracts.py`
- Modify: `tests/test_provider_contracts.py`

**Interfaces:**
- Produces: `AnswerActionKind`, `AnswerAttemptState`, `AnswerCapability`, `AnswerAttempt`, `AnswerControlPlan`
- Produces: `answer_capability_for_request(contract: NegotiatedProviderContract | None, request_kind: RequestKind) -> AnswerCapability`
- Produces: `reconcile_answer_attempt(previous: AnswerAttempt | None, request_identity: AnnouncerAlertIdentity, generation: int) -> AnswerAttempt`
- Produces: `reduce_answer_intent(attempt: AnswerAttempt, action: AnswerActionKind, draft_text: str | None = None) -> AnswerAttempt`
- Produces: `project_answer_controls(request_kind: RequestKind, capability: AnswerCapability, attempt: AnswerAttempt) -> AnswerControlPlan`
- Produces: product-contract support for `ProductCapability.ANSWERING` bound only to `local.answer_in_place`

- [x] **Step 1: Write the failing pure and contract tests**

```python
def test_answering_local_surface_is_supported_and_exact():
    result = negotiate_provider_contract(
        _document(
            product_capabilities=[
                {"id": "answering", "supported": True, "binding": {
                    "kind": "local", "id": "local.answer_in_place",
                }},
            ]
        )
    )
    invocation = result.product_invocation_for("answering")
    assert invocation.local_runtime_surface.value == "local.answer_in_place"


def test_binary_request_projects_approve_and_deny_only():
    plan = project_answer_controls(
        RequestKind.PERMISSION,
        AnswerCapability(True, False, True, None, None),
        AnswerAttempt(AnnouncerAlertIdentity("request:test"), 1, AnswerAttemptState.IDLE, "", None),
    )
    assert plan.primary_actions == (AnswerActionKind.APPROVE, AnswerActionKind.DENY)
```

- [x] **Step 2: Run the focused red tests**

Run: `.venv/bin/python -m pytest -q tests/test_answer_in_place.py tests/test_provider_contracts.py -k "answer"`
Expected: fail because the module and local answering binding do not exist.

- [x] **Step 3: Implement the minimal contract and pure answer model**

```python
class AnswerActionKind(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    REPLY = "reply"
    CANCEL = "cancel"
    RETRY = "retry"
    JUMP = "jump"
```

- [x] **Step 4: Implement bounded projection and state transitions**

```python
def project_answer_controls(request_kind, capability, attempt):
    if not capability.supported:
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
```

- [x] **Step 5: Run focused tests and Ruff**

Run: `.venv/bin/python -m pytest -q tests/test_answer_in_place.py tests/test_provider_contracts.py -k "answer"`
Expected: pass.

Run: `.venv/bin/ruff check src/sidepulse/answer_in_place.py src/sidepulse/provider_contracts.py tests/test_answer_in_place.py tests/test_provider_contracts.py`
Expected: pass.

---

### Task 2: Typed payloads and native answer controls

**Files:**
- Modify: `src/sidepulse/announcer_stack_view.py`
- Modify: `src/sidepulse/agent_browser_window.py`
- Modify: `tests/test_announcer_stack_view.py`
- Modify: `tests/test_sidepulse.py`

**Interfaces:**
- Consumes: `AnswerControlPlan` from Task 1
- Produces: native Screen Bar answer controls and typed Agent Browser action payloads for answer verbs
- Produces: explicit states for idle, sending, failed, timed out, cancelled, and sent

- [x] **Step 1: Write the failing native and payload tests**

```python
def test_expanded_announcer_renders_approve_deny_and_jump_for_permission():
    panel = AnnouncerStackPanel()
    permission_plan = AnswerControlPlan(
        request_identity=AnnouncerAlertIdentity("request:0"),
        generation=17,
        capability=AnswerCapability(True, False, True, None, None),
        state=AnswerAttemptState.IDLE,
        draft_text="",
        primary_actions=(AnswerActionKind.APPROVE, AnswerActionKind.DENY, AnswerActionKind.JUMP),
        secondary_actions=(),
        status_text=None,
        can_edit_reply=False,
        can_send=True,
        can_cancel=False,
    )
    panel.update(_plan(AnnouncerStackVisibility.EXPANDED), lambda _intent: None, center_x=500.0, top_y=700.0, answer_plan=permission_plan)
    assert tuple(button.title() for button in panel.answer_buttons) == ("Approve", "Deny", "Jump")
```

- [x] **Step 2: Run the focused red tests**

Run: `.venv/bin/python -m pytest -q tests/test_announcer_stack_view.py tests/test_sidepulse.py -k "answer or announcer"`
Expected: fail because the answer controls and payloads do not exist.

- [x] **Step 3: Add typed payloads and native controls without changing the collapsed state**

```python
@dataclass(frozen=True, slots=True)
class AgentBrowserAnswerPayload:
    work_key: WorkKey
    generation: int
    request_identity: str
    action: AnswerActionKind
```

- [x] **Step 4: Render the answer states and keep Jump available**

```python
if answer_plan.state is AnswerAttemptState.SENDING:
    show_status("Sending…")
    show_cancel_button()
```

- [x] **Step 5: Run focused tests and Ruff**

Run: `.venv/bin/python -m pytest -q tests/test_announcer_stack_view.py tests/test_sidepulse.py -k "answer or announcer"`
Expected: pass.

Run: `.venv/bin/ruff check src/sidepulse/announcer_stack_view.py src/sidepulse/agent_browser_window.py tests/test_announcer_stack_view.py tests/test_sidepulse.py`
Expected: pass or no new finding in retained legacy files.

---

### Task 3: Controller runtime, timeout, retry, and cancellation

**Files:**
- Create: `src/sidepulse/answer_runtime.py`
- Create: `tests/test_answer_runtime.py`
- Modify: `src/sidepulse/status_bar_legacy.py`
- Modify: `tests/test_announcer_stack_wiring.py`
- Modify: `tests/test_unwired_modules_ratchet.py`

**Interfaces:**
- Consumes: `AnswerControlPlan`, typed payloads, and `ProductCapabilityInvocation`
- Produces: one controller-owned answer runtime with exact request identity, timeout, retry, cancel, and stale callback fences
- Produces: `performAgentBrowserPayload_()` and announcer intent handling for answer actions

- [x] **Step 1: Write the failing controller and runtime tests**

```python
def test_timeout_preserves_draft_and_exposes_retry_without_clearing_ask():
    runtime = AnswerRuntime(timeout_seconds=10.0, monotonic=lambda: 10.0)
    runtime.submit(
        ProductCapabilityInvocation(
            product_capability=ProductCapability.ANSWERING,
            provider_id=ProviderIdentifier("codex"),
            adapter_id=AdapterIdentifier("hooks"),
            source_instance_id=SourceInstanceIdentifier("source:main"),
            local_runtime_surface=LocalRuntimeSurfaceIdentifier("local.answer_in_place"),
        ),
        request_identity=AnnouncerAlertIdentity("request:0"),
        generation=4,
        action=AnswerActionKind.APPROVE,
        reply_text=None,
    )
    assert runtime.snapshot(AnnouncerAlertIdentity("request:0")).state is AnswerAttemptState.TIMED_OUT
```

- [x] **Step 2: Run the focused red tests**

Run: `.venv/bin/python -m pytest -q tests/test_answer_runtime.py tests/test_announcer_stack_wiring.py -k "answer"`
Expected: fail because the runtime and controller wiring do not exist.

- [x] **Step 3: Implement the bounded runtime off the AppKit thread**

```python
def submit(self, invocation, *, request_identity, action, reply_text):
    self._worker.submit(invocation, request_identity, action, reply_text)
```

- [x] **Step 4: Wire the controller and preserve fail-closed boundaries**

```python
if action.kind is AnswerActionKind.JUMP:
    return self.open_session(route, None, remember=False)
if not capability.supported:
    return False
```

- [x] **Step 5: Run focused tests and Ruff**

Run: `.venv/bin/python -m pytest -q tests/test_answer_runtime.py tests/test_announcer_stack_wiring.py tests/test_unwired_modules_ratchet.py -k "answer"`
Expected: pass.

Run: `.venv/bin/ruff check src/sidepulse/answer_runtime.py src/sidepulse/status_bar_legacy.py tests/test_answer_runtime.py tests/test_announcer_stack_wiring.py tests/test_unwired_modules_ratchet.py`
Expected: pass or no new finding in retained legacy files.

---

### Task 4: Receipts, docs, and tranche gates

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/FEATURE-MATRIX.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `docs/VISION.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`
- Modify: this plan
- Modify: this plan's SDD ledger and reports

**Interfaces:**
- Produces: exact P3.36 source receipts and evidence boundaries
- Produces: one batch gate for the completed tranche

- [x] **Step 1: Update docs and source-only limits**

```markdown
P3.36 adds capability-gated answer-in-place controls with timeout, retry,
cancellation, and jump fallback. These are source and isolated source-AppKit
receipts only.
```

- [x] **Step 2: Render and inspect focused native receipts**

Result: `.venv/bin/python .superpowers/sdd/2026-08-30-jr-bar-p3-36-answer-in-place/render_answer_in_place_receipts.py`
rendered and inspected 16 PNGs covering 8 answer states in Aqua and Dark Aqua.
The aggregate image-set SHA-256 is
`5d868719abaff1ac901fd2f4eabde86e99f1c4752cd266f44869f0faa27bd466`, and the
production view SHA-256 is
`7f615bfe9efb47e6ca867eb8baa22f3d20d6e1967ed0fc22d3f5db48dfc8c93c`.

- [x] **Step 3: Run tranche-focused verification**

Result: `96` focused P3.36 tests passed with `939` deselected. Ruff on
`src/` and `tests/` passed, and `git diff --check` passed.

- [x] **Step 4: Run batch gates once**

Result: `make fast` passed in `18.99` seconds with 112 contract, 150 fixture,
and 539 focused tests, plus lint, secret scan, import, compile, dependency,
version, and diff gates.

Result: `.venv/bin/python -m pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning`
passed as the final full suite with 7,359 tests plus 7 subtests in
158.40 seconds, and the four known multiprocessing fork deprecation warnings
remained unrelated warnings.

- [x] **Step 5: Close the tranche**

```text
Record the exact fingerprint, focused test counts, batch timings, render artifacts,
and review verdict. Do not claim installed-app, live-provider, signing, notarization,
packaging, publication, or release proof.
```

Final closeout: the 543-file `src/` and `tests/` fingerprint is
`5f6dba0b9c99f0757b16c1f4b9c744ccffb61b295bf6e0fa80a6287d811880d5`, and the
final independent rereview found no Critical or Important issues.
