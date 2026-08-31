# JR Bar P3.37 Configurable Global Actions Implementation Plan

**Goal:** Add one configurable global action that reveals the current ask or
the existing Agent Browser control surface without observing ordinary typing.

**Architecture:** An AppKit-free model owns chord validation, serialization,
conflicts, and status projection. A small main-thread Carbon adapter owns
registration and callback lifetime. The retained controller owns lifecycle and
routes both the visible menu command and registered shortcut through one action.
A bounded first-responder recorder is embedded in the existing Overview
Settings page.

**Design:**
`docs/superpowers/specs/2026-08-30-jr-bar-p3-37-configurable-global-actions-design.md`

**Batch rule:** Use focused red and green tests inside each task. Run Ruff,
`make fast`, the full suite, and source fingerprinting once after all tasks and
native receipts are stable.

## Authority and Evidence Boundary

- Edit source, tests, docs, and isolated source-native receipt artifacts.
- Do not add a production dependency.
- Do not commit, push, open a pull request, install, sign, notarize, package,
  publish, deploy, change permissions, or mutate another app's shortcuts.
- Do not use global or local event monitors or a CGEventTap.
- Treat source and source-native AppKit receipts as distinct from installed-app,
  cross-app conflict, hardware, and release evidence.

## Task 1: Pure Shortcut Model and Settings Persistence

**Ownership:**

- Create: `src/sidepulse/global_actions.py`
- Modify: `src/sidepulse/_settings_legacy.py`
- Modify: `src/sidepulse/settings.py`
- Create: `tests/test_global_actions.py`
- Modify: `tests/test_settings_compatibility.py`
- Modify only if required for direct Settings round trip:
  `tests/test_sidepulse.py`

### Step 1: Write failing pure-model tests

Cover:

- exact action and modifier enums;
- chord key-code and label bounds;
- at least one modifier and Command-or-Control requirement;
- pure Option-plus-Shift refusal;
- JR Bar reserved menu equivalents;
- duplicate normalized bindings;
- deterministic modifier-symbol formatting;
- strict parse and serialization of known fields;
- individual refusal of malformed or unknown persisted entries;
- `UNASSIGNED`, `ACTIVE`, `LOCAL_CONFLICT`, `UNSUPPORTED`,
  `REGISTRATION_REFUSED`, and `CLOSED` projections.

Run:

```bash
.venv/bin/python -m pytest -q tests/test_global_actions.py
```

Expected red: the global-actions module does not exist.

### Step 2: Implement the smallest pure model

Keep the module AppKit-free and immutable. Put no registration, event-loop,
window, logging, or settings-write side effect in the pure layer.

### Step 3: Add the owned settings collection

Add `global_action_shortcuts` with an empty default, include it in the owned
collection paths, and preserve the existing schema and unknown-field behavior.
New installs remain unassigned.

### Step 4: Verify Task 1

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_global_actions.py \
  tests/test_settings_compatibility.py \
  tests/test_sidepulse.py \
  -k 'global_action or shortcut or settings_unknown or newer_schema'
.venv/bin/ruff check \
  src/sidepulse/global_actions.py \
  src/sidepulse/_settings_legacy.py \
  src/sidepulse/settings.py \
  tests/test_global_actions.py \
  tests/test_settings_compatibility.py
git diff --check -- \
  src/sidepulse/global_actions.py \
  src/sidepulse/_settings_legacy.py \
  src/sidepulse/settings.py \
  tests/test_global_actions.py \
  tests/test_settings_compatibility.py
```

## Task 2: Main-Thread Carbon Registration Adapter

**Ownership:**

- Create: `src/sidepulse/global_hotkeys.py`
- Create: `tests/test_global_hotkeys.py`
- Modify: `tests/test_unwired_modules_ratchet.py`
- Modify only if a lifecycle ratchet belongs there:
  `tests/test_status_bar_lifecycle_contract.py`

### Step 1: Write failing registry tests with a fake backend

Cover:

- one application handler installation;
- action-to-hotkey-ID mapping;
- exact named-action callback routing;
- injected main-thread dispatch;
- transactional rebind, including register-new before unregister-old;
- rollback when registration is refused;
- explicit prepare, commit, and rollback so the candidate remains inert until
  Settings persistence succeeds;
- clear and idempotent close;
- stale callbacks after rebind or close;
- no event text or unregistered-key input;
- source ratchet forbidding global monitors, local monitors, and CGEventTap.

Run:

```bash
.venv/bin/python -m pytest -q tests/test_global_hotkeys.py
```

Expected red: the registry module does not exist.

### Step 2: Implement the injectable registry

The public registry accepts a backend protocol. Keep normalized model objects
at the boundary and translate to Carbon integers only inside the backend.

### Step 3: Implement the production Carbon backend

Load Carbon lazily with `ctypes`. Bind exact signatures for application target,
handler install/removal, hotkey register/unregister, and bounded hotkey-ID
parameter extraction. Keep C callback references alive. Use non-exclusive
registration. Convert nonzero `OSStatus` values to bounded refusal results.

Do not call this backend at import time.

### Step 4: Run an isolated reversible source probe

In a fresh process, instantiate the backend, register one uncommon test chord,
verify the returned reference is owned, unregister it, and close the handler.
Do not synthesize ordinary key events or change a persisted setting.

Record the OS version, result, and exact cleanup receipt. If the local system
refuses the chord, record that result rather than weakening the adapter.

### Step 5: Verify Task 2

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_global_actions.py \
  tests/test_global_hotkeys.py \
  tests/test_unwired_modules_ratchet.py
.venv/bin/ruff check \
  src/sidepulse/global_actions.py \
  src/sidepulse/global_hotkeys.py \
  tests/test_global_actions.py \
  tests/test_global_hotkeys.py \
  tests/test_unwired_modules_ratchet.py
git diff --check -- \
  src/sidepulse/global_hotkeys.py \
  tests/test_global_hotkeys.py \
  tests/test_unwired_modules_ratchet.py
```

## Task 3: Controller Lifecycle, Menu, and Reveal Routing

**Ownership:**

- Modify: `src/sidepulse/status_bar_legacy.py`
- Modify: `src/sidepulse/answer_controller.py`
- Modify only if the presentation bridge needs a narrow public method:
  `src/sidepulse/announcer_presenter.py`
- Modify: `src/sidepulse/virtual_device.py`
- Modify: `tests/test_announcer_stack_wiring.py`
- Modify: `tests/test_status_bar_lifecycle_contract.py`
- Modify: `tests/test_every_menu_action_responds.py`
- Modify: `tests/test_architecture_ratchets.py`
- Modify: `tests/test_unwired_modules_ratchet.py`

### Step 1: Write failing action-routing tests

Cover:

- current actionable ask expands using current announcer generation and
  selected identity;
- an already expanded announcer collapses;
- unavailable Screen Bar opens Agent Browser without enabling Screen Bar;
- the mandatory `VirtualStatusDevice.can_present_announcer()` boundary reports
  enabled, visible-window, awake-display, full-screen, compact, Alcove, and
  termination suppression truth;
- each unavailable state falls back to Agent Browser instead of toggling an
  invisible announcer;
- no current ask opens Agent Browser;
- stale generations are never reused;
- action invocation does not mutate triage, seen receipts, mailbox,
  notification, answer attempt, completion tracking, or LED output;
- the visible menu selector resolves and invokes the same controller path.

### Step 2: Write failing lifecycle tests

Cover:

- persisted valid binding registers once after launch menu installation;
- repeated launch and settings refresh are idempotent;
- explicit rebind uses the transactional registry result;
- successful registration remains prepared and inert until Settings save
  succeeds;
- `SettingsConcurrentWriteError` and `SettingsWriteRefusedError` roll back the
  prepared registration and retain the previous live and durable binding;
- a failed clear likewise retains the previous live and durable binding;
- termination closes once before panel and worker teardown;
- late callbacks after termination cannot present UI;
- retained-controller ratchets shrink or remain stable.

### Step 3: Implement the smallest controller seam

Add one action selector and extract coordination if the retained controller
would otherwise grow beyond its shrink-only boundary. Reuse
`AnswerController`, the existing announcer intent reducer, the existing virtual
device presenter, and existing Agent Browser presentation. Do not create a new
window or second ask model.

### Step 4: Verify Task 3

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_global_hotkeys.py \
  tests/test_announcer_stack_wiring.py \
  tests/test_status_bar_lifecycle_contract.py \
  tests/test_every_menu_action_responds.py \
  tests/test_architecture_ratchets.py \
  tests/test_unwired_modules_ratchet.py \
  tests/test_sidepulse.py \
  -k 'global_action or reveal_current or announcer or lifecycle or menu_action'
.venv/bin/ruff check \
  src/sidepulse/global_actions.py \
  src/sidepulse/global_hotkeys.py \
  src/sidepulse/answer_controller.py \
  src/sidepulse/status_bar_legacy.py \
  src/sidepulse/virtual_device.py \
  src/sidepulse/announcer_presenter.py \
  tests/test_global_hotkeys.py \
  tests/test_announcer_stack_wiring.py \
  tests/test_status_bar_lifecycle_contract.py \
  tests/test_every_menu_action_responds.py
git diff --check
```

## Task 4: Overview Settings Recorder and Status

**Ownership:**

- Create: `src/sidepulse/global_action_settings_pane.py`
- Modify: `src/sidepulse/settings_window.py`
- Modify only if the facade delegates through it:
  `src/sidepulse/settings_window_controls.py`
- Create: `tests/test_global_action_settings_pane.py`
- Modify: `tests/test_settings_accessibility.py`
- Modify: `tests/test_settings_window_injection_ratchet.py`
- Modify: `tests/test_settings_navigation.py`

### Step 1: Write failing recorder model and AppKit tests

Cover:

- unset, recording, active, conflict, refused, and cleared states;
- Record moves first responder to the recorder;
- Escape cancels and Delete or Backspace clears only while recording;
- ordinary unmodified typing is rejected and delegated outside recording;
- success, cancel, clear, conflict, registration refusal, and save failure exit
  recording and restore the prior first responder or focus Record Shortcut;
- non-recording `keyDown_` delegates to `super` without consuming text input;
- valid modified `keyDown_` creates one candidate;
- local conflict and registration refusal preserve the previous binding;
- exact accessibility label, value, help, status, and key-view loop;
- repeated refresh updates retained controls and callbacks without stale state;
- no new Settings category or page is added.

### Step 2: Implement the bounded AppKit row

Embed the row in Overview. Keep the recorder's AppKit ownership inside the
pane, expose only candidate, clear, and retry callbacks, and keep the committed
binding in controller/settings state. Do not install any event monitor.

### Step 3: Verify Task 4

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_global_action_settings_pane.py \
  tests/test_settings_accessibility.py \
  tests/test_settings_window_injection_ratchet.py \
  tests/test_settings_navigation.py \
  tests/test_global_actions.py \
  tests/test_global_hotkeys.py
.venv/bin/ruff check \
  src/sidepulse/global_action_settings_pane.py \
  src/sidepulse/settings_window.py \
  src/sidepulse/settings_window_controls.py \
  tests/test_global_action_settings_pane.py \
  tests/test_settings_accessibility.py
git diff --check
```

## Task 5: Native Receipts and Product Documentation

**Ownership:**

- Create:
  `.superpowers/sdd/2026-08-30-jr-bar-p3-37-configurable-global-actions/`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/FEATURE-MATRIX.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `docs/VISION.md`
- Modify:
  `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`
- Modify: this plan

### Step 1: Render source-native Settings receipts

Render unset, recording, active, local-conflict, registration-refused, and
cleared states in Aqua and Dark Aqua through the production pane. Bind source
and image SHA-256 values in a deterministic manifest.

For each receipt, inspect and assert:

- heading, action label, chord text, Record and Clear states;
- status and conflict text;
- focus ring and recording prompt;
- accessibility role, label, value, and help;
- deterministic rerendering;
- no clipped or overlapping content.

### Step 2: Record source-only limits

Document that P3.37 proves source-native recorder behavior, injected registry
lifecycle, isolated source registration, and controller routing. It does not
prove complete cross-app conflicts, all keyboard layouts, installed-app focus,
VoiceOver speech, signing, notarization, packaging, publication, or release
readiness.

### Step 3: Verify Task 5 artifacts

Run the receipt harness twice and require exact source and image matches. Run
Ruff and `py_compile` on the harness, plus `git diff --check` on the owned docs.

## Task 6: Consolidated Review and Batch Gates

### Step 1: Run the combined focused tranche

```bash
.venv/bin/python -m pytest -q \
  tests/test_global_actions.py \
  tests/test_global_hotkeys.py \
  tests/test_global_action_settings_pane.py \
  tests/test_announcer_stack_wiring.py \
  tests/test_status_bar_lifecycle_contract.py \
  tests/test_every_menu_action_responds.py \
  tests/test_settings_accessibility.py \
  tests/test_settings_compatibility.py \
  tests/test_settings_navigation.py \
  tests/test_architecture_ratchets.py \
  tests/test_unwired_modules_ratchet.py
```

### Step 2: Run findings-first independent review

Review:

- no ordinary typing interception;
- Carbon callback and reference lifetime;
- main-thread ownership;
- transactional rebind and rollback;
- honest conflict language;
- settings compatibility and retained-control refresh;
- exact current ask routing and no side effects;
- lifecycle close ordering and stale callbacks;
- accessibility, keyboard operation, and native layout;
- controller and settings-facade ratchets.

Fix every validated Critical or Important finding and rerun only the affected
focused slices before repeating the combined focused tranche.

### Step 3: Bind the stable source

Create a sorted SHA-256 manifest for non-generated files under `src/` and
`tests/`. Record its file count and aggregate fingerprint before broad gates.

### Step 4: Run the broad gates once

```bash
.venv/bin/ruff check src tests
make fast
.venv/bin/python -m pytest -q
```

Recompute the source/test fingerprint and require an exact match.

### Step 5: Close P3.37

P3.37 closed in source on 2026-08-30. The combined focused command passed 219
tests in 7.74 seconds, all-source Ruff passed, and `git diff --check` passed. A
final independent whole-tranche review found no concrete correctness, security,
regression, architecture, or missing-test issue; its independent focused slice
passed 204 tests with 12 deselections.

The final native receipt set contains 16 inspected PNGs across eight states in
Aqua and Dark Aqua. Its image-set SHA-256 is
`dad8ea282d5733a9fe06951db345cedb7a10268a8b68b1a1f0c4d64f69483abf`, its
manifest SHA-256 is
`19b63294992173d14f79f820247486970118a8ce83f5512dda0d4838792f49a1`, its
production-pane SHA-256 is
`1dcd3fbfee3a3344669c3a12879afd96284399e824fe117dd4b39a5373dd068f`, and its
harness SHA-256 is
`2c2a6833719a6926a5b967b21d32884c328b20519684db553f6f128b81b27d3a`.

A fresh macOS 27.0 process registered and unregistered an uncommon
all-modifier F18 chord and removed the Carbon application handler. It did not
synthesize input or mutate Settings. The first complete-suite run exposed two
stale test contracts, both reproduced in isolation. After the test-only repair,
the two affected files passed 88 tests, `make fast` passed in 23.67 seconds with
113 contract, 150 fixture, and 540 focused tests, and the complete suite passed
7,509 tests plus 7 subtests in 229.53 seconds with four known multiprocessing
fork deprecation warnings. All 550 non-generated files under `src/` and `tests/`
retained fingerprint
`199d313bdcd05e5a02f8d7d26d21f4fcbe05af464f4ea959047eb9ac6dfd5fe0` before
and after the complete run.

This closeout proves source-native recording, injected registry lifecycle, one
reversible source Carbon registration, durable controller routing, and native
state rendering. It does not prove every cross-application conflict, every
keyboard layout, installed-app focus, VoiceOver speech, signing, notarization,
packaging, publication, updater behavior, or release readiness. The completion
contract now points to P3.38 manual and scheduled DND.
