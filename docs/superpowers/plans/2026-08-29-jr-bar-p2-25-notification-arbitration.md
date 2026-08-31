# JR Bar P2.25 Notification Arbitration Extraction Implementation Plan

Status: notification-arbitration slice completed and verified on 2026-08-29.
Roadmap item P2.25 remains active because the other prioritized pure-decision
boundaries are separate slices.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the notification-arbitration decision boundary from `status_bar_legacy.py` into small pure helpers without changing delivery behavior, copy, interruption gating, or session-opening behavior.

**Architecture:** Keep AppKit delivery, Notification Center delegates, and controller-owned mutable state in the retained runtime. Move pure decisions into a dedicated module that accepts current state snapshots and returns bounded plans or resolved targets. Start with notification arbitration because it is still mixed into the controller while signal and presentation math already have extracted seams.

**Tech Stack:** Python 3.12+, PyObjC/AppKit retained host, `pytest`, existing interruption-policy and operator-state types.

**Spec:** `docs/superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md`

## Global Constraints

- Do not add production dependencies.
- Do not add new business behavior to `src/sidepulse/status_bar_legacy.py`.
- Preserve existing notification copy from `generic_notification_copy`.
- Preserve existing `ActionTokenBinding`, `SemanticEventKey`, `InterruptionClass`, `RequestKey`, and `WorkKey` contracts.
- Preserve the current completion-notification interruption gate, snooze gate, and work-key lookup behavior.
- Keep delivery side effects in the controller. The extracted module must stay pure.
- Use synthetic tests only. Do not require real Notification Center delivery.

---

## File Structure

- Create `src/sidepulse/notification_arbitration.py` for pure binding pruning, delivery planning, completion eligibility, and token-to-work-key resolution helpers.
- Create `tests/test_notification_arbitration.py` for source-of-truth decision tests using real domain types and no AppKit.
- Modify `src/sidepulse/status_bar_legacy.py` only to replace inline notification decision logic with calls to the new module.
- Modify `tests/test_architecture_ratchets.py` only if a semantic allowlist or import-boundary assertion needs to acknowledge the new module.
- Modify `docs/ARCHITECTURE.md` and `docs/LOCAL-VERIFICATION.md` only after code and tests are green.

### Task 1: Extract Pure Notification Binding And Delivery Planning

**Files:**
- Create: `src/sidepulse/notification_arbitration.py`
- Create: `tests/test_notification_arbitration.py`
- Modify: `src/sidepulse/status_bar_legacy.py`

**Interfaces:**
- Consumes: `ActionTokenBinding`, `InterruptionClass`, `RequestKey`, `SemanticEventKey`, `WorkKey`, `CanonicalOperatorState`, `generic_notification_copy`, `issue_action_token`, `resolve_action_token`
- Produces:
  - `prune_notification_action_bindings(bindings: dict[str, tuple[ActionTokenBinding, SemanticEventKey]], *, now: float, current_generation: int, max_bindings: int) -> dict[str, tuple[ActionTokenBinding, SemanticEventKey]]`
  - `plan_semantic_notification(*, event_key: SemanticEventKey, interruption_class: InterruptionClass, prefix: str, request_key: RequestKey | None, operator_generation: int, now: float, randomness: bytes, existing_bindings: dict[str, tuple[ActionTokenBinding, SemanticEventKey]], max_bindings: int, ttl_seconds: float) -> tuple[dict[str, tuple[ActionTokenBinding, SemanticEventKey]], str, str, str, dict[str, str]] | None`
  - `resolve_notification_work_key(*, presented_token: str, bindings: dict[str, tuple[ActionTokenBinding, SemanticEventKey]], current_generation: int, now: float) -> tuple[dict[str, tuple[ActionTokenBinding, SemanticEventKey]], WorkKey | None]`

- [x] **Step 1: Write failing pure-decision tests**

```python
def test_prune_notification_bindings_drops_expired_and_stale_generations() -> None:
    retained = prune_notification_action_bindings(
        bindings,
        now=1_800_000_100.0,
        current_generation=9,
        max_bindings=4,
    )
    assert set(retained) == {fresh_a.token, fresh_b.token}


def test_plan_semantic_notification_returns_delivery_payload_and_updated_bindings() -> None:
    planned = plan_semantic_notification(
        event_key=event_key,
        interruption_class=InterruptionClass.COURTESY,
        prefix="completion",
        request_key=None,
        operator_generation=4,
        now=1_800_000_000.0,
        randomness=b"x" * 32,
        existing_bindings={},
        max_bindings=8,
        ttl_seconds=300.0,
    )
    assert planned is not None
    bindings, notification_id, title, body, metadata = planned
    assert notification_id.startswith("completion.")
    assert title == PRODUCT_DISPLAY_NAME
    assert "finished" in body
    assert metadata["action_token"] in bindings


def test_resolve_notification_work_key_fails_closed_for_expired_or_unknown_tokens() -> None:
    updated, work_key = resolve_notification_work_key(
        presented_token="wrong",
        bindings={binding.token: (binding, event_key)},
        current_generation=4,
        now=1_800_000_500.0,
    )
    assert work_key is None
    assert binding.token not in updated
```

- [x] **Step 2: Run the new tests and confirm they fail**

Run: `./.venv/bin/python -m pytest -q tests/test_notification_arbitration.py`
Expected: FAIL because the module and functions do not exist yet.

- [x] **Step 3: Add the new pure helper module**

```python
def prune_notification_action_bindings(...):
    ...


def plan_semantic_notification(...):
    ...


def resolve_notification_work_key(...):
    ...
```

- [x] **Step 4: Rewire the controller methods to use the helpers**

```python
planned = plan_semantic_notification(...)
if planned is None:
    return False
self._notification_action_bindings, notification_id, title, body, metadata = planned
delivered = self._notification_client_for_use().deliver(
    notification_id,
    title,
    body,
    metadata,
)
```

- [x] **Step 5: Run the narrow notification tests**

Run: `./.venv/bin/python -m pytest -q tests/test_notification_arbitration.py tests/test_interrupt_budget.py tests/test_sidepulse.py -k "notification or completion_notification"`
Expected: PASS

### Task 2: Extract Completion Notification Eligibility

**Files:**
- Modify: `src/sidepulse/notification_arbitration.py`
- Modify: `src/sidepulse/status_bar_legacy.py`
- Modify: `tests/test_notification_arbitration.py`
- Modify: `tests/test_sidepulse.py`

**Interfaces:**
- Consumes: `status_snoozed`
- Produces:
  - `should_post_completion_notification(*, status_present: bool, completion_notifications_enabled: bool, may_interrupt: bool, snoozed: bool, event_present: bool) -> bool`

- [x] **Step 1: Write the failing eligibility tests**

```python
def test_completion_notification_requires_all_gates() -> None:
    assert should_post_completion_notification(
        status_present=True,
        completion_notifications_enabled=True,
        may_interrupt=True,
        snoozed=False,
        event_present=True,
    ) is True
    assert should_post_completion_notification(
        status_present=True,
        completion_notifications_enabled=True,
        may_interrupt=True,
        snoozed=True,
        event_present=True,
    ) is False
```

- [x] **Step 2: Implement the pure helper and simplify the controller**

```python
if not should_post_completion_notification(...):
    return
```

- [x] **Step 3: Run the focused completion-notification tests**

Run: `./.venv/bin/python -m pytest -q tests/test_notification_arbitration.py tests/test_interrupt_budget.py tests/test_capacity_consumer_authority.py tests/test_claude_capacity_plane.py`
Expected: PASS

### Task 3: Verification, Ratchets, And Docs

**Files:**
- Modify: `tests/test_architecture_ratchets.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/LOCAL-VERIFICATION.md`

**Interfaces:**
- Consumes: completed Task 1 and Task 2 exports
- Produces: source and verification receipt updates for the extracted notification boundary

- [x] **Step 1: Add or update the architecture ratchet if needed**

```python
def test_notification_arbitration_module_is_import_pure() -> None:
    ...
```

- [x] **Step 2: Run the focused extraction tranche**

Run: `./.venv/bin/python -m pytest -q tests/test_notification_arbitration.py tests/test_architecture_ratchets.py tests/test_interrupt_budget.py tests/test_status_bar_lifecycle_contract.py`
Expected: PASS

- [x] **Step 3: Run the canonical fast gate**

Run: `make fast`
Expected: PASS

- [x] **Step 4: Update architecture and local verification receipts**

```markdown
- notification arbitration is now a pure decision module
- delivery and AppKit opening behavior remain controller-owned
```

- [x] **Step 5: Run the complete suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS
