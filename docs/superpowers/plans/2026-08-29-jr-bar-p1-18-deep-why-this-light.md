# JR Bar P1.18 Deep Why This Light Implementation Plan

> **For parallel workers:** REQUIRED SUB-SKILLS: Use
> `superpowers:test-driven-development`, `superpowers:executing-plans`, and
> `superpowers:verification-before-completion` for the files you own. You are
> not alone in the worktree. Do not revert or rewrite another worker's edits.

**Goal:** Make the local Why Is It Doing That panel explain the current light
from authoritative, content-free state: source age, selected semantic signal,
winning priority, currently suppressed cues, Scene availability, surface role,
Focus/DND decision, Reduce Motion substitution, and source-labeled output timing.

**Architecture:** Keep `DecisionTrace` as the proven explanation of the
first-match semantic ladder. Add one independent, immutable context snapshot
for policy, surface, suppression, and timing facts. The retained controller
projects only already-cached runtime values into that snapshot. A pure
formatter appends the fixed explanation section. Production output timing comes
from the existing local-health snapshot and retains whether it measured the
Screen Bar callback or physical hardware-write path. A small AppKit-agnostic text
update helper preserves the reader's selection and scroll position while the
open panel refreshes.

**Tech Stack:** Python 3.11+, standard-library dataclasses and enums, existing
AppKit window, pytest, Ruff.

**Spec:**
`docs/superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md`

## Global constraints

- Reuse canonical presentation, finite-cue, Focus, accessibility, device, and
  local-health owners. Do not re-run signal selection or perform I/O.
- Store only bounded enums, counts, booleans, and numeric ages or durations.
  Never store prompt text, transcript content, tool names, paths, URLs,
  provider payloads, device ids, session ids, Focus names, or credentials.
- Suppression describes only the bounded current presentation plan. It is not
  effect history, which remains P4.54.
- Scenes do not exist until P4.45. P1.18 must render `Unavailable`, not invent
  an inactive Scene or add Scene configuration early.
- The panel is global. Device role must describe the active surface scope,
  never imply that controller-wide timings belong to a particular device.
- An unreadable Focus state is `Unavailable`, not `Off`. Focus/DND wording must
  distinguish observation availability, policy, and the actual decision.
- Reduce Motion reports whether motion was substituted with a static signal.
  It must not claim substitution when no motion was requested.
- Output timing reuses the existing local-health and performance snapshots and
  labels Screen Bar callback and physical hardware-write samples separately.
  Controller refresh duration is never relabeled as output timing. No second
  telemetry store, background uploader, or network path is allowed.
- Keep the readout selectable, non-editable, vertically scrollable, and stable
  during refresh. Selection and scroll position must be clamped and restored.
- No production dependency, permission change, helper installation, commit,
  push, packaging, deployment, publication, or release mutation is authorised.

---

### Task 1: Define the immutable explanation context

**Owner:** Parallel lane A

**Files:**
- Create: `src/sidepulse/why_light_context.py`
- Create: `tests/test_why_light_context.py`

- [x] Write literal, failing tests for each typed state and explicit unknown.
- [x] Prove formatter output is fixed-shape, bounded, and content-free.
- [x] Model semantic, priority, source age, current suppressions, Scene
  availability, surface role, Focus/DND decision, Reduce Motion substitution,
  and renderer timing without importing AppKit.
- [x] Run the focused pure tests through one complete red-green-refactor cycle.

### Task 2: Project canonical runtime facts without I/O

**Owner:** Parallel lane B

**Files:**
- Create: `src/sidepulse/why_light_projection.py`
- Create: `tests/test_why_light_projection.py`

- [x] Write failing tests for semantic priority, source freshness, bounded cue
  suppression, Focus unavailable/off/active policy, surface scope, Scene
  unavailable, and Reduce Motion substitution.
- [x] Accept already-cached primitive facts as inputs. Do not read settings,
  Focus databases, devices, providers, files, Keychain, or network state.
- [x] Return the immutable context model and reject or normalize malformed,
  unbounded, or content-bearing inputs.
- [x] Run the focused pure tests through one complete red-green-refactor cycle.

### Task 3: Preserve panel reading position during live refresh

**Owner:** Parallel lane C

**Files:**
- Create: `src/sidepulse/why_panel.py`
- Create: `tests/test_why_panel.py`

- [x] Write failing tests proving a text refresh preserves a valid selected
  range and visible scroll origin, clamps both after shorter output, and still
  updates simple test doubles.
- [x] Implement a defensive helper around the existing selectable text view.
  It must tolerate unavailable AppKit selectors and avoid changing editability.
- [x] Run the focused tests through one complete red-green-refactor cycle.

### Task 4: Wire the retained and production controllers

**Owner:** Primary agent after collecting Tasks 1 through 3

**Files:**
- Modify: `src/sidepulse/status_bar_legacy.py`
- Modify: `src/sidepulse/_status_bar_production.py`
- Modify: `tests/test_first_run_honesty.py`
- Modify: `tests/test_status_bar_production_boundary.py`
- Create or modify: `tests/test_why_light_wiring.py`

- [x] Write failing integration tests before controller changes.
- [x] Capture Focus observation availability alongside cached ids so unavailable
  cannot silently become inactive.
- [x] Project source age from cached visible source facts, the selected semantic
  and priority from the resolved glance, current finite-cue suppression from
  the bounded coordinator state, surface role from configured live surfaces,
  Scene as unavailable, and Reduce Motion from the actual substitution path.
- [x] Let the production facade add source-labeled output timing from
  `LocalHealthSnapshot` after recording the current refresh.
- [x] Append the pure context text to the existing trace and capacity sections.
- [x] Replace direct open-panel text rewrites with the preserving helper.
- [x] Prove the projection triggers no new I/O and leaks no content-bearing id.

### Task 5: Verify the rendered panel and documentation

**Owner:** Primary agent

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/FEATURE-MATRIX.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/UPSTREAM-SYNC.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`

- [x] Run focused context, projection, controller, policy, accessibility,
  pipeline, and local-health suites.
- [x] Run Ruff, compile checks, `git diff --check`, and the complete direct
  source gate. The wrapper was not used because this `.venv` lacks `pip`; no
  dependency or environment mutation was authorized.
- [x] Render the real AppKit panel from source. Inspect hierarchy, long-body
  scrolling, selectable text, explicit unknowns, and refresh stability.
- [x] Keep installed-app and physical-hardware claims separate unless the exact
  rebuilt candidate is installed and observed with the required permissions.
- [x] Obtain one independent review with no unresolved semantic, privacy,
  performance, lifecycle, or UI finding.
- [x] Mark every receipt above only after direct evidence exists, then advance
  the completion contract to P1.19.

## Closure evidence

- Focused source verification passed 674 P1.18 and active-controller contract
  tests, followed by 3 notification-authorization lifecycle tests.
- The stable-tree complete suite passed 6,552 tests plus 7 subtests, with four
  known Python 3.12 multiprocessing fork deprecation warnings.
- Ruff, compileall, dependency policy, tracked-file secret scanning over 571
  files, release-version validation, six architecture ratchets, and
  `git diff --check` passed. The verified `src/` and `tests/` fingerprint was
  `252d59cb956db3dd6226fd7c99af82fa8b315c25b6decb4470821610696bd98a`.
- A 620 by 660 point isolated AppKit preview rendered 1,631 characters. Paging,
  full selection and copying, accessibility label and help, selection clamping,
  and scroll restoration were observed before the preview was terminated.
- Independent rereview found no unresolved P1.18 finding after source-specific
  timing labels, the production context keyword, the final provider subclass,
  notification start recovery, post-refresh sampling, and Focus freshness were
  corrected and retested.
- These receipts prove source behavior only. They do not prove a sealed
  installed app, physical LEDs, Screen Bar performance under Instruments,
  packaging, signing, notarization, installation, deployment, publication, or
  release readiness.
