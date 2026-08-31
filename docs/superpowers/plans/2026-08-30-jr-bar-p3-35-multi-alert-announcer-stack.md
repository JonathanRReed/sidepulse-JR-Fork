# JR Bar P3.35 Multi-Alert Announcer Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans`
> to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Give simultaneous Screen Bar asks a stable, keyboard-accessible,
surface-local announcer stack with truthful counts and a passive collapsed
state.

**Architecture:** Add one AppKit-free stack reducer and projector, then render
its immutable plan through a focused native panel owned by
`VirtualStatusDevice`. The status controller owns state and exact session
routing. Seen receipts stay in memory and apply only to the Screen Bar.

**Tech Stack:** Python 3.12, PyObjC/AppKit, pytest, Ruff, native macOS
accessibility APIs.

**Spec:**
`docs/superpowers/specs/2026-08-30-jr-bar-p3-35-multi-alert-announcer-stack-design.md`

## Global Constraints

- Preserve the intentional dirty `main` worktree and every existing roadmap
  change. The user's standing approval explicitly permits work on this checkout.
- Do not commit, push, install, change permissions, publish, deploy, or mutate
  credentials. Task reports must say that no commit was created.
- Do not add a production dependency.
- Mark Seen is an in-memory Screen-Bar-local receipt. It must not mutate local
  triage, canonical request acknowledgement, mailbox or completion receipts,
  Notification Center bindings, or physical LED state.
- Keep stable visual order by first-seen sequence. Priority may choose the
  default selection but may not reorder surviving items.
- Project one alert per live canonical request whose work is present in the
  already-filtered actionable attention rows. Attach question text or an exact
  open route only from a current status with the same request key. Prefer
  canonical `RequestKey`;
  use a bounded product-owned fallback only when canonical state is unavailable
  and a legacy actionable row has no request key. Never classify identity or
  priority from question text.
- Preserve the current 40-character source cap, 80-character question cap,
  140-character collapsed text cap, and visible count cap of `99+`.
- The collapsed pill remains passive and non-key. The expanded presenter is a
  borderless nonactivating `NSPanel`; only explicit pointer expansion may make
  it key-capable, and no app-activation API may be called.
- Preserve disabled, terminating, sleeping, fullscreen, Alcove, and compact
  suppression. Suppression must not clear stack state or seen receipts.
- Increment generation on every reconciliation, even when content is
  equivalent, and on every accepted presentation intent.
- Reduce Motion replaces show, hide, resize, and selection animation with an
  immediate final state.
- Use project-native controls, system typography, semantic colors, a 4 and 8
  point rhythm, visible focus, exact state text, and no color-only meaning.
- No screen capture, provider read, disk I/O, or hardware write may occur in a
  render, keyboard, pointer, or animation callback.
- Run focused tests during tasks. Run `make fast` once the tranche stabilizes
  and one full suite only at this completed priority boundary.
- Because commits are not authorized, each implementer owns only the files in
  its task. The controller records before snapshots in this plan's SDD
  workspace and gives each reviewer a task-scoped no-index diff package.
- Tasks execute sequentially. A later task that names a previously owned file
  may extend it only for that task's stated receipt or integration purpose.

---

### Task 1: Pure alert identity, reducer, and projection

**Files:**

- Create: `src/sidepulse/announcer_stack.py`
- Create: `tests/test_announcer_stack.py`

**Interfaces:**

- Produces the immutable enums and dataclasses in the spec:
  `AnnouncerAlertPriority`, `AnnouncerStackVisibility`,
  `AnnouncerStackAction`, `AnnouncerAlertIdentity`, `AnnouncerAlert`,
  `AnnouncerStackState`, `AnnouncerStackPlan`, and `AnnouncerStackIntent`.
- `AnnouncerStackPlan.generation` exactly equals the state generation used to
  project it, so native controls never infer generation from a callback.
- Produces reconciliation, navigation, Mark Seen, intent reduction, and
  projection functions named in the spec.
- Consumes optional current-snapshot `CanonicalOperatorState`, already-filtered actionable attention
  rows, and current snapshot statuses. It projects one alert per live canonical
  request, uses attention rows to preserve existing work visibility filters,
  and attaches question text only from an exact request-key status match.

- [x] **Step 1: Write failing table-driven tests**

Cover compact sorted-key canonical identity, deterministic legacy fallback,
invalid or malformed rows, one-work and multiple-request cardinality, existing
subagent filtering, exact status-key question attachment, content-free fallback,
all five priorities, stable surviving order, deterministic append order,
selection preservation, highest-priority unseen selection, previous and next
wrapping, stale-intent refusal, priority-aware seen advancement, priority-map
refresh without visual reorder, all-seen hiding, resolution
pruning, restart behavior, exact counts, `99+`, copy caps, single-line
normalization, frozen values, and generation increments on both equivalent
reconciliation and accepted intents.
Also assert every projected plan copies the exact current state generation.

The stable-order case must prove that a newer permission ask can become selected
without moving ahead of an older input ask in `plan.alerts`.

- [x] **Step 2: Run the pure tests and record the expected red result**

```bash
.venv/bin/python -m pytest -q \
  tests/test_announcer_stack.py
```

Expected: the new module import fails or the new stack assertions fail because
only the text projection exists.

- [x] **Step 3: Implement exact identity and bounded row projection**

Canonical identity must include the full source instance, work, and request
key as compact, sorted-key JSON from `request_key_to_payload`, prefixed with
`request:v1:`. Legacy identity may use provider, agent, session, semantic
event, and opened-at timestamp. Normalize labels and questions only for
presentation, not identity.

Canonical request kind determines priority on the normal path. Include every
request in `LIVE_UNACKNOWLEDGED` or `LIVE_ACKNOWLEDGED` phase with a user next
actor for work present in the filtered actionable rows. Join bounded question
text only when a current snapshot status has the exact `request_key`. Other
canonical requests display `Needs your input`. If canonical state is
unavailable, map legacy `PermissionRequest`, plan approval, review, and general
input through a closed constant table. Unknown actionable rows remain visible
at `UNKNOWN` priority.

- [x] **Step 4: Implement reconciliation and intent reduction**

Retain surviving order and sequence, append new identities, prune resolved
identities, seen receipts, and priority entries, refresh the current immutable
priority map, preserve a surviving selection, and choose the highest-priority
unseen selection when needed. Mark Seen uses that map immediately without
reordering the stable identity list. Reject stale intents by exact generation
and selected identity. `OPEN` is a validated no-presentation-change intent; the
controller performs the side effect later.

- [x] **Step 5: Implement collapsed and expanded projection**

One unseen ask uses the direct `Source: question` form. Multiple asks use
`Source needs you · N asks`. All-seen active stacks project `HIDDEN` while
retaining their alerts for reconciliation. Accessibility text names source,
semantic type, position, count, question, and the independent LED behavior.

- [x] **Step 6: Preserve the old compatibility formatter unchanged**

`project_announcer_content(...)` remains a separate stateless compatibility
formatter with its existing direct and additional-ask wording. Do not route
production stack presentation through it and do not change its tests in this
task.

- [x] **Step 7: Run focused tests and Ruff**

```bash
.venv/bin/python -m pytest -q \
  tests/test_announcer_stack.py \
  tests/test_announcer_content.py
.venv/bin/ruff check \
  src/sidepulse/announcer_stack.py \
  tests/test_announcer_stack.py \
  tests/test_announcer_content.py
```

Expected: pass with no warning or collection noise.

---

### Task 2: Native collapsed and expanded announcer panel

**Files:**

- Create: `src/sidepulse/announcer_stack_view.py`
- Create: `tests/test_announcer_stack_view.py`
- Modify: `src/sidepulse/virtual_device.py`
- Modify: `tests/test_screen_bar_motion.py`
- Modify: `tests/test_settings_accessibility.py`

**Interfaces:**

- Produces `AnnouncerStackPanel`, a focused native presenter for one immutable
  `AnnouncerStackPlan` plus one `Callable[[AnnouncerStackIntent], None]`.
- Produces `VirtualStatusDevice.set_announcer_stack(plan, intent_handler)`.
- Preserves `set_announcer_text(...)` as a collapsed compatibility adapter.

- [x] **Step 1: Write failing native boundary tests**

Cover hidden, collapsed, and expanded states; passive collapsed focus;
explicitly keyable expansion; one-item click open; multiple-item click expand;
real Previous, Next, Open, Mark Seen, and Close controls; key mappings; exact
intent generation and identity; stale controls; accessibility metadata;
visible focus; Aqua and Dark semantic colors; one shared suppression predicate;
fullscreen reconciliation; cleanup; failed animation fallback; and Reduce
Motion substitution.

Use the existing real-window-presentation fixture where the window contract is
the assertion. Mocks may isolate AppKit calls, but tests must also exercise a
real source-created native panel.

- [x] **Step 2: Run the native slice and record the expected red result**

```bash
.venv/bin/python -m pytest -q \
  tests/test_announcer_stack_view.py \
  tests/test_screen_bar_motion.py \
  tests/test_settings_accessibility.py \
  -k "announcer"
```

Expected: the view module and typed virtual-device method do not exist.

- [x] **Step 3: Build the native presenter outside `virtual_device.py`**

Use one `NSPanel` subclass with borderless and
`NSWindowStyleMaskNonactivatingPanel` style masks. `canBecomeMainWindow` is
always false. `canBecomeKeyWindow` is true only after explicit expansion. The
collapsed root refuses first responder. Expansion calls `makeFirstResponder_`
and `makeKeyWindow` but no app-activation API. Collapse, suppression, and
termination resign key status. Keep the current all-Spaces and
fullscreen-auxiliary behavior, Screen Bar window level, transparency, and top
anchoring.

The collapsed pill remains one combined accessible element. Expanded content
uses native text fields and buttons, bounded 320 to 460 point width, project
spacing, semantic colors, and non-color priority copy. The question wraps in a
native field for at most two visible lines, then truncates at the tail. The
collapsed root exposes `AXButton`; the expanded card exposes `AXGroup`; and
every native control has the exact accessibility label from the spec.

- [x] **Step 4: Add generation-fenced pointer and keyboard intents**

Left and Up emit Previous, Right and Down emit Next, Return emits Open, Space or
`d` emits Mark Seen, and Escape emits Collapse. Tab stays native button
navigation. Every intent copies `plan.generation` and the selected identity
visible when the control was projected. Never infer generation from a callback
attribute or default it to zero.

- [x] **Step 5: Preserve motion and Reduce Motion behavior**

The typed collapsed presentation may remain immediate or reuse the current
top-anchored arrival. Expansion, collapse, and resize may use at most 180
milliseconds of ease-out motion. Selection replaces content in place. AppKit
failure and Reduce Motion both apply the final state immediately. The existing
compatibility pill keeps its current entrance effect.

- [x] **Step 6: Add the typed `VirtualStatusDevice` adapter**

Store one presenter and forward immutable plans and one announcer-only handler.
Do not source that handler from the Screen Bar drawing view. Add one
`_announcer_allowed()` predicate covering enabled, terminating, sleeping,
visible host window, `_fullscreen_hidden`, Alcove, and compact states. Ordinary
sync and fullscreen reconciliation both use `_sync_announcer`, which applies
that predicate. Hidden or suppressed presentation must resign key status.
Termination must clear the handler, close the panel, and release the presenter.
Do not change physical program or the Screen Bar's separate click-handler path.

- [x] **Step 7: Run native focused tests and Ruff**

```bash
.venv/bin/python -m pytest -q \
  tests/test_announcer_stack_view.py \
  tests/test_screen_bar_motion.py \
  tests/test_settings_accessibility.py \
  -k "announcer"
.venv/bin/ruff check \
  src/sidepulse/announcer_stack_view.py \
  src/sidepulse/virtual_device.py \
  tests/test_announcer_stack_view.py \
  tests/test_screen_bar_motion.py \
  tests/test_settings_accessibility.py
```

Expected: pass with no warning or collection noise.

---

### Task 3: Controller ownership, exact routing, and Screen Bar receipts

**Files:**

- Modify: `src/sidepulse/status_bar_legacy.py`
- Create: `tests/test_announcer_stack_wiring.py`
- Modify: `tests/test_sidepulse.py`
- Modify: `tests/test_status_bar_lifecycle_contract.py`
- Modify: `tests/test_unwired_modules_ratchet.py`
- Modify: `tests/test_architecture_ratchets.py`

**Interfaces:**

- `StatusBarController` owns one `AnnouncerStackState` initialized empty.
- `sync_virtual_status_device()` reconciles the current snapshot's optional
  canonical operator state, actionable attention, and current snapshot
  statuses, projects one plan, and calls
  `set_announcer_stack(plan, handler)`.
- A controller intent handler validates generation and exact identity, reduces
  presentation actions, and routes only current Open intents to the existing
  `open_session(status, None, remember=False)` path.

- [x] **Step 1: Write failing wiring and independence tests**

Cover empty projection, multiple requests under one work, legacy fallback when
canonical state is unavailable, stable order across reordered snapshots,
permission default selection without visual reorder, exact open routing,
resolved request pruning, Mark Seen resync, all-seen hiding, new-request
reannouncement, stale callback refusal after an equivalent refresh, controller
termination, and `projection=None` compatibility.

Add explicit spies proving Mark Seen never calls local triage, canonical
acknowledgement, completion visits, mailbox receipts, Notification Center
bindings, `sync_leds`, or hardware workers. Then resolve the request through a
new projection and prove normal LED reconciliation remains independent.

- [x] **Step 2: Run the wiring slice and record the expected red result**

```bash
.venv/bin/python -m pytest -q \
  tests/test_announcer_stack_wiring.py \
  tests/test_sidepulse.py \
  tests/test_status_bar_lifecycle_contract.py \
  tests/test_unwired_modules_ratchet.py \
  -k "announcer or virtual_status"
```

Expected: the controller has no stack state, intent handler, or typed device
wiring.

- [x] **Step 3: Initialize and reconcile controller-owned state**

Keep state lifetime inside the active controller instance. Do not persist seen
identities. Reconcile from the current `last_snapshot.operator_state`, the
already-computed actionable projection, and current `last_snapshot.statuses`;
do not use the retained `current_operator_state` cache to decide canonical
availability and do not recollect provider data. Store an exact
identity-to-current actionable status route for the current generation so Open
never searches stale global status. When a canonical request has no exact
matching attention row, route Open through its owning work's actionable status;
if no safe current route exists, disable Open for that item.

- [x] **Step 4: Handle typed intents**

Validate exact type, generation, and selected identity. Reduce Expand,
Collapse, Previous, Next, and Mark Seen through the pure contract, then resync
only the virtual announcer presentation. For Open, verify the selected identity
still maps to an actionable row and call the existing session opener. Invalid or
stale intents are no-ops.

- [x] **Step 5: Replace production text wiring with typed plan wiring**

Production `sync_virtual_status_device()` must call `reconcile_announcer_stack`
and `set_announcer_stack`.
Retain `project_announcer_content` and `set_announcer_text` only for public and
test compatibility, not the production stack path. Preserve the Screen Bar's
existing direct click-to-oldest-ask route.

- [x] **Step 6: Verify lifecycle cleanup and architecture wiring**

Termination clears the identity map and state after closing the virtual device.
The unwired-module ratchet must prove `announcer_stack` and
`announcer_stack_view` are imported by production source. Update the existing
architecture ratchet in this task from the old production
`project_announcer_content` call to the new reconciliation and typed device
calls. The old formatter remains covered only as a compatibility adapter.

- [x] **Step 7: Run controller tests and Ruff**

```bash
.venv/bin/python -m pytest -q \
  tests/test_announcer_stack.py \
  tests/test_announcer_stack_view.py \
  tests/test_announcer_stack_wiring.py \
  tests/test_sidepulse.py \
  tests/test_status_bar_lifecycle_contract.py \
  tests/test_architecture_ratchets.py \
  tests/test_unwired_modules_ratchet.py \
  -k "announcer or virtual_status"
.venv/bin/ruff check \
  src/sidepulse/announcer_stack.py \
  src/sidepulse/announcer_stack_view.py \
  src/sidepulse/status_bar_legacy.py \
  src/sidepulse/virtual_device.py \
  tests/test_announcer_stack.py \
  tests/test_announcer_stack_view.py \
  tests/test_announcer_stack_wiring.py \
  tests/test_architecture_ratchets.py
```

Expected: pass with no warning or collection noise.

---

### Task 4: Native rendering and interaction receipts

**Files:**

- Create or Modify: a plan-owned render harness under this plan's SDD workspace
- Modify only if a render exposes a production UI defect:
  `src/sidepulse/announcer_stack_view.py`
- Modify only if a render exposes a virtual-device presentation defect:
  `src/sidepulse/virtual_device.py`
- Modify: `tests/test_announcer_stack_view.py`
- Modify: `tests/test_settings_accessibility.py`
- Modify: `docs/LOCAL-VERIFICATION.md`

**Interfaces:**

- Produces source-render evidence for collapsed and expanded states in Aqua and
  Dark Aqua.
- Produces keyboard, focus, accessibility, and Reduce Motion receipts without
  claiming installed-app or live-provider proof.

- [x] **Step 1: Load Impeccable's craft floor immediately before UI QA**

Apply the incumbent visual identity, system controls, 4 and 8 point rhythm,
explicit state text, visible focus, no color-only meaning, semantic colors, and
bounded visual-QA passes.

- [x] **Step 2: Render the real source-created native panel**

Capture these states through `AnnouncerStackPanel`, not a recreated mock:

1. one collapsed ask;
2. multiple collapsed asks with truthful count and highest-priority source;
3. expanded unseen selection at `1 of N`;
4. expanded seen selection with the independent LED help;
5. expanded keyboard focus on each control;
6. expanded Reduce Motion state.

Render each presentation in `NSAppearanceNameAqua` and
`NSAppearanceNameDarkAqua`. The focus presentation produces one receipt per
native control, so the minimum matrix is 20 PNGs: five ordinary states times
two appearances, plus five focused controls times two appearances. Keep PNGs
and a JSON manifest in the plan-owned SDD workspace.

- [x] **Step 3: Inspect every image**

Inspect for clipping, contrast, stable notch anchoring, count truth, priority
copy, selected position, seen wording, focus rings, button affordance, and
non-color distinctions. Repair production UI defects, regenerate all affected
receipts, and record the exact corrected image names.

- [x] **Step 4: Verify keyboard and accessibility through the native object**

Exercise Previous, Next, Open, Mark Seen, Escape, and Tab with the real window
and assert exact typed intents. Read the native accessibility label, value,
help, roles, and button names. Verify the collapsed state never becomes key and
the expanded state does so only after explicit expansion.

- [x] **Step 5: Document the source receipt and its limits**

Record the render matrix, keyboard checks, accessibility checks, appearance
names, Reduce Motion substitution, and artifact paths. State that installed-app
focus, VoiceOver, live provider requests, and physical LEDs remain external.

- [x] **Step 6: Run the native receipt slice and Ruff**

```bash
.venv/bin/python -m pytest -q \
  tests/test_announcer_stack_view.py \
  tests/test_settings_accessibility.py \
  tests/test_screen_bar_motion.py \
  -k "announcer"
.venv/bin/ruff check \
  src/sidepulse/announcer_stack_view.py \
  tests/test_announcer_stack_view.py \
  tests/test_settings_accessibility.py
```

Expected: pass with no warning or collection noise.

---

### Task 5: Prior art, product documentation, and compatibility audit

**Files:**

- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/FEATURE-MATRIX.md`
- Modify: `docs/PRIOR-ART.md`
- Modify: `docs/VISION.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`
- Modify: this plan

**Interfaces:**

- Records exact P3.35 architecture, behavior, provenance, and remaining evidence
  boundaries.
- Produces a compatibility audit proving older single-announcer consumers and
  independent physical-light semantics remain intact.

- [x] **Step 1: Update exact architecture and product behavior**

Document the pure stack owner, controller-owned state, passive and expanded
native states, typed intents, stable order, priority selection, in-memory seen
receipts, and independent LED semantics. Keep user-facing language direct and
use `Mark Seen` for the action.

- [x] **Step 2: Record exact prior-art snapshots and limits**

Add the T3 Code, CodexBar, upstream SidePulse, SidePulse fleet fork, and
T3Notch snapshots listed in the design. State which behavior was reimplemented,
which upstream behavior was negative evidence, and that T3Notch remained
concept-only because no license was published when inspected.

- [x] **Step 3: Audit compatibility and ownership**

Search production source and tests for every announcer setter, click path,
Screen Bar suppression branch, local triage acknowledgement, completion visit,
mailbox seen receipt, notification binding, and physical LED acknowledgement.
Add focused regression coverage for any unpinned boundary. Do not broaden into
P3.36 answer-in-place.

- [x] **Step 4: Update completion contract and plan receipts**

Record P3.35 as active through Task 6, name P3.36 as the next priority after
source closure, and preserve installed-app, live provider, physical hardware,
signing, notarization, and publication as separate evidence rows. Task 6 moves
the completed vertical tranche to P3.35 only after its batch gates pass.

- [x] **Step 5: Run documentation and compatibility checks**

```bash
git diff --check
.venv/bin/python -m pytest -q \
  tests/test_announcer_content.py \
  tests/test_announcer_stack.py \
  tests/test_announcer_stack_view.py \
  tests/test_announcer_stack_wiring.py \
  tests/test_screen_bar_motion.py \
  tests/test_settings_accessibility.py \
  tests/test_status_bar_lifecycle_contract.py \
  tests/test_unwired_modules_ratchet.py
```

Expected: pass with no warning or collection noise.

---

### Task 6: Batch gates, independent review, and final receipts

**Files:**

- Modify: this plan
- Modify: this plan's SDD ledger and reports
- Modify only if review requires it: files already owned by Tasks 1 through 5

**Interfaces:**

- Produces one stable-source P3.35 batch receipt.
- Produces an independent findings-first whole-tranche review.

- [x] **Step 1: Bind the source under test**

Create a sorted manifest and SHA-256 fingerprint for every file under `src/`
and `tests/`. Record the fingerprint before the batch gates and verify the same
manifest and fingerprint afterward. If source changes, discard the receipts and
restart this step.

- [x] **Step 2: Run lint before proposing final changes**

```bash
.venv/bin/ruff check src tests
```

Expected: pass.

- [x] **Step 3: Run the fast change gate once**

```bash
make fast
```

Expected: every contract, fixture, focused, secret, import, compile,
dependency, version, and diff-hygiene stage passes.

- [x] **Step 4: Run one warning-hardened complete suite**

```bash
.venv/bin/python -m pytest -q \
  -W error::pytest.PytestUnhandledThreadExceptionWarning
```

Expected: pass. Record known unrelated warnings separately and do not convert
them into a clean-output claim.

- [x] **Step 5: Verify stable source and native receipts**

Recompute the manifest and fingerprint. Confirm every expected Aqua and Dark
Aqua PNG and the JSON manifest exists and corresponds to the bound source.
Visually inspect at least one collapsed and one expanded receipt in each
appearance after the final code change.

- [x] **Step 6: Dispatch one broad findings-first review**

Give the reviewer the spec, plan, SDD ledger, task reports, task-scoped diff
packages, focused and batch receipts, source fingerprint, and native render
manifest. Require findings-first review of correctness, stale intent behavior,
focus, accessibility, lifecycle cleanup, acknowledgement independence,
physical-light isolation, tests, and documentation claims.

If review finds Critical or Important defects, dispatch one bounded fix owner
for the complete finding list, run the affected tests, regenerate affected
native receipts, and run one scoped rereview. Do not rerun `make fast` or the
full suite until the fix wave stabilizes. Then repeat Steps 1 through 5 once.

- [x] **Step 7: Close source receipts without overstating evidence**

Record changed files, exact commands, counts, timings, fingerprint, render
artifacts, review verdict, rulings, and remaining risks. Do not claim installed
application, live provider, VoiceOver, physical LED, signing, notarization,
deployment, or release proof.
