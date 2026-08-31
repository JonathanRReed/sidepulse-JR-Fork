# P2.25 Completion Visibility and Announcer Batch Plan

> **For Codex:** Use test-driven development and parallel workers for the two
> independent pure modules. Workers own new modules and tests only. The main
> agent owns shared-controller integration, docs, and batch verification. Do
> not commit, push, package, install, publish, change permissions, or add a
> dependency.

**Goal:** Finish P2.25 by extracting acknowledgement/completion visibility and
announcer content together, then verify them as one batch instead of running a
complete suite after each small seam.

**Verification cadence:** Each worker runs its red and focused pure tests plus
Ruff. Integration uses focused controller and UI tests. `make fast` and the
complete fingerprinted suite run once after both seams are integrated.

**Status:** Complete on 2026-08-29.

## Lane A: Completion visibility and acknowledgement policy

**Files:**
- Create: `src/sidepulse/completion_visibility.py`
- Create: `tests/test_completion_visibility.py`
- Later integration by main: `src/sidepulse/status_bar_legacy.py`,
  `src/sidepulse/status_bar.py`, and focused tests.

- [x] Select recent clearable completions from current and stale rows with one
  deterministic row per agent id, current rows winning.
- [x] Select unseen completions with SessionEnd, subagent, age, cleared,
  menu-open, and attended-completion exclusions expressed as pure inputs.
- [x] Plan the bounded seen-id set for a menu visit without mutating controller
  state.
- [x] Plan clear-finished state for cleared ids, retained order, and seen ids
  without performing refresh or persistence.
- [x] Keep datetime and monotonic `now` values caller-supplied.
- [x] Retain menu visit timestamps, activity-ledger writes, settings/service
  notifications, controller assignment, signature invalidation, and refresh in
  adapters.

## Lane B: Announcer content policy

**Files:**
- Create: `src/sidepulse/announcer_content.py`
- Create: `tests/test_announcer_content.py`
- Later integration by main: `src/sidepulse/virtual_device.py`,
  `src/sidepulse/status_bar_legacy.py`, and focused tests.

- [x] Project immutable bounded announcer content from actionable attention
  rows without importing AppKit.
- [x] Preserve the current primary session name/question wording and fallback.
- [x] Report the total actionable count and disclose additional asks in the
  bounded single-line pill instead of silently omitting them.
- [x] Keep Screen Bar view mutation, click handling, window geometry, motion,
  and hardware rendering in existing adapters.

## Shared integration and batch gate

- [x] Register both modules in the pure-production ratchet.
- [x] Delegate retained completion and announcer policy without adding new
  retained controller business methods.
- [x] Run focused completion, mailbox, menu projection, attention, virtual
  device, accessibility, lifecycle, and composition tests.
- [x] Obtain one independent findings-first review over the combined batch.
- [x] Run Ruff, `git diff --check`, and `make fast` once for the batch.
- [x] Run one complete suite against a stable before/after source/test
  fingerprint.
- [x] Update architecture, local verification, and completion receipts with
  source-only limitations.

## Acceptance criteria

- Completion eligibility, unseen selection, visit acknowledgement, and clear
  state each have a pure deterministic owner.
- Duplicate current/stale rows cannot double-count one completion.
- Announcer copy never hides that more actionable asks exist.
- Shared controller code performs side effects only after consuming pure plans.
- The combined batch passes focused, fast, independent-review, complete-suite,
  and fingerprint gates once.

## Completion receipt

- New pure policy and architecture coverage passed 31 tests in 3.13 seconds.
- Focused completion, mailbox, menu, freshness, motion, lifecycle, and
  composition integration passed 136 tests in 4.20 seconds.
- A review-found `projection=None` Screen Bar regression received a direct
  controller test; the post-fix gate passed 32 tests in 2.91 seconds, and the
  independent rereview reported no findings.
- `make fast` passed in 17.95 seconds with 96 contract tests, 139 fixture and
  schema tests, and 298 focused tests.
- The complete suite passed 6,905 tests plus 7 subtests in 294.05 seconds with
  the four known Python multiprocessing fork warnings.
- All 498 bound `src/` and `tests/` files retained fingerprint
  `2fdb3ac85451c835c6451c2934f782434c82d810a28505aa2df2dedae653dc05`
  before and after the complete run.
- These receipts cover source behavior only. They do not prove installed-app
  menu behavior, Notification Center presentation, Screen Bar visual quality,
  physical LEDs, packaging, signing, notarization, publication, or release
  readiness.
