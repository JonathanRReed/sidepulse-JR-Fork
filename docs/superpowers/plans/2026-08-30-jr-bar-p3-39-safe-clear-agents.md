# JR Bar P3.39 Safe Clear Agents implementation plan

Date: 2026-08-30

Design: `docs/superpowers/specs/2026-08-30-jr-bar-p3-39-safe-clear-agents-design.md`

## Batch strategy

Implement independent model, persistence, native UI, and controller lanes in
parallel. Each lane runs only its narrow tests. Collect and integrate all lanes,
then run one combined P3.39 tranche. Run Ruff, `make fast`, and the complete
suite only after review repairs at the P3.39 boundary.

## Task 1: exact receipt model

Ownership:

- `src/sidepulse/clear_agents.py`
- `tests/test_clear_agents.py`

Work:

1. Add bounded exact completion keys, receipt state, preview, fence, batch
   receipt, commit plan, and Undo plan.
2. Enforce completed-only, local-only, source-bound, and content-free identity
   rules by requiring a validated `status.work_key.source_key`.
3. Protect current active, waiting, failed, queued, remote, and unkeyed local
   presentation instead of guessing fallback identity.
4. Refuse stale preview, empty commit, wrong batch, repeated Undo, and expired
   Undo.
5. Prove newer events and cross-source collisions reappear or remain untouched,
   while unkeyed completions stay visible.

Focused gate:

```bash
.venv/bin/python -m pytest -q tests/test_clear_agents.py
.venv/bin/ruff check src/sidepulse/clear_agents.py tests/test_clear_agents.py
```

## Task 2: strict receipt persistence

Ownership:

- `src/sidepulse/clear_agents_store.py`
- `tests/test_clear_agents_store.py`

Work:

1. Add exact-field versioned decode and bounded encode for `clear-agents.json`.
2. Use the existing private atomic-write boundary.
3. Return typed healthy, missing, corrupt, unsupported, and unavailable restore
   results without exposing paths or raw errors.
4. Cover permissions, symlink refusal, malformed data, caps, deterministic
   round-trip, and atomic replace failure.

Focused gate:

```bash
.venv/bin/python -m pytest -q tests/test_clear_agents_store.py tests/test_private_io.py
.venv/bin/ruff check src/sidepulse/clear_agents_store.py tests/test_clear_agents_store.py
```

## Task 3: native preview, receipt, and Undo surface

Ownership:

- `src/sidepulse/clear_agents_popover.py`
- `tests/test_clear_agents_popover.py`

Work:

1. Build a project-native AppKit popover for preview, saving, stale, failure,
   receipt, expired Undo, and undone states.
2. Keep displayed labels bounded and content-safe.
3. Wire native accessibility labels/help, explicit enabled state, and keyboard
   order without defining another production controller. Explicitly activate
   the app, make the shown popover key, install the first responder, and handle
   Return, Tab, Shift-Tab, and Escape in the root view.
4. Add a source-AppKit render harness after integration.

Focused gate:

```bash
.venv/bin/python -m pytest -q tests/test_clear_agents_popover.py
.venv/bin/ruff check src/sidepulse/clear_agents_popover.py tests/test_clear_agents_popover.py
```

## Task 4: completion and controller integration

Ownership:

- `src/sidepulse/completion_visibility.py`
- `src/sidepulse/menu_projection.py`
- `src/sidepulse/status_bar_legacy.py`
- `src/sidepulse/status_bar.py`
- affected completion, menu, lifecycle, architecture, and integration tests

Work:

1. Load the strict receipt state with the other local operator state.
2. Replace session-ID suppression with exact completion keys everywhere.
3. Remove `ClearFinishedPlan`, `plan_clear_finished`, `clearFinished_`, the
   reactivation heuristic, and `cleared_session_ids`.
4. Replace the legacy and projected root actions with `Clear Agents...`,
   remove the stray `clearCompleted:` selector path, and preserve the 15-row
   budget.
5. Drive root visibility from a separate exact-receipt-backed
   `clearable_presented_count`, not the menu-visit unread count.
6. Project, show, revalidate, and commit the preview through one controller
   coordinator with a dedicated or identity-discriminated popover close path.
7. Fence every async confirm and Undo result with a controller-owned operation
   generation, ignoring late callbacks.
8. Route both mailbox-row filtering and unseen-completion filtering through the
   exact completion-receipt helper.
9. Persist commit and Undo through the serial writer, adopt only after success,
   invalidate memoized presentation state, and refresh once.
10. Keep mailbox stable order, mailbox menu-visit seen state, announcer, local
    request triage, history, notifications, webhooks, effects, power, settings,
    credentials, hooks, and remote state untouched.

Focused gate:

```bash
.venv/bin/python -m pytest -q \
  tests/test_completion_visibility.py \
  tests/test_menu_projection.py \
  tests/test_compact_menu_wiring.py \
  tests/test_status_bar_lifecycle_contract.py \
  tests/test_architecture_ratchets.py \
  tests/test_announcer_stack_wiring.py \
  tests/test_sidepulse.py -k 'clear or completion or mailbox or persistence'
```

## Task 5: independent review and native receipts

1. Review pure invariants, store safety, controller races, destructive-scope
   exclusions, UI accessibility, and effect non-regression independently.
2. Repair all Critical and Important findings and rerun only affected focused
   tests.
3. Render preview, protected-live-work, stale refresh, successful receipt,
   failure, and Undo states in Aqua and Dark Aqua.
4. Record source and image fingerprints, visible copy, and inspection notes.

## Task 6: P3.39 batch gate and closeout

Combined tranche:

```bash
.venv/bin/python -m pytest -q \
  tests/test_clear_agents.py \
  tests/test_clear_agents_store.py \
  tests/test_clear_agents_popover.py \
  tests/test_completion_visibility.py \
  tests/test_mailbox.py \
  tests/test_local_triage.py \
  tests/test_operator_triage_store.py \
  tests/test_activity_ledger.py \
  tests/test_announcer_stack.py \
  tests/test_announcer_stack_wiring.py \
  tests/test_menu_projection.py \
  tests/test_compact_menu_wiring.py \
  tests/test_status_bar_lifecycle_contract.py \
  tests/test_architecture_ratchets.py \
  tests/test_interrupt_budget.py \
  tests/test_power_hold_runtime.py \
  tests/test_screen_bar_pipeline.py \
  tests/test_sidepulse.py -k 'clear or completion or mailbox or announcer or persistence'
```

Boundary gates after review repairs:

```bash
.venv/bin/ruff check src tests
make fast
.venv/bin/python -m pytest -q
git diff --check
```

Close only after the design acceptance contract, native source receipts, final
review, and source gates pass. Preserve P1.23 and P2.32 external release gates
and advance the completion contract to P3.40 without making installed-app or
release claims.
