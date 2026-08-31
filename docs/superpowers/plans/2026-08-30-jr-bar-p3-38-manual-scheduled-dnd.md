# JR Bar P3.38 Manual and Scheduled DND Implementation Plan

**Goal:** Add one truthful, durable DND projection that separates Mute, Dim,
Pause, Asks Only, and Fully Dark across manual overrides, a daily schedule, and
macOS Focus.

**Architecture:** An AppKit-free policy owns time, composition, parsing, and
status. A lazy public Focus adapter owns `INFocusStatusCenter`. A small
controller owns one transition timer and Settings transactions. Existing signal,
brightness, interrupt, Screen Bar, and hardware paths consume the same immutable
projection. One native card extends the current Focus pane.

**Design:**
`docs/superpowers/specs/2026-08-30-jr-bar-p3-38-manual-scheduled-dnd-design.md`

**Batch rule:** Use focused red and green tests inside each task. Run all-source
Ruff, `make fast`, the complete suite, and source fingerprinting only after all
implementation tasks, native receipts, and independent focused reviews are
stable.

## Authority and evidence boundary

- Edit source, tests, docs, packaging metadata, and isolated source-native
  receipt artifacts.
- Do not add a production dependency.
- Do not request or change Focus, Full Disk Access, notification, or other TCC
  permissions during development or source probes.
- Do not install, sign, notarize, package, publish, deploy, commit, push, or open
  a pull request.
- Keep source evidence separate from installed-app, live-Focus, physical-device,
  signing, and release evidence.

## Task 1: Pure DND policy and durable settings

**Ownership:**

- Create: `src/sidepulse/dnd_policy.py`
- Create: `src/sidepulse/local_time_boundary.py`
- Modify: `src/sidepulse/mailbox_preference_store.py`
- Modify: `src/sidepulse/_settings_legacy.py`
- Modify: `src/sidepulse/settings.py`
- Create: `tests/test_dnd_policy.py`
- Modify: `tests/test_mailbox_preference_store.py`
- Modify: `tests/test_settings_compatibility.py`
- Modify: `tests/test_settings_concurrency.py`

### Step 1: Write failing pure-policy tests

Cover:

- exact mode, source, display-admission, and outbound-admission enums;
- the five exact mode matrices;
- independent-axis composition across manual, schedule, coarse Focus, and named
  Focus contributions;
- temporary resume suppressing only the local schedule;
- same-day and overnight daily schedules;
- timezone-aware next transitions; the shared rule that spring-forward gaps
  advance to the first valid local second and fall-back folds choose the
  earliest valid epoch at or after the lower bound; clock and timezone changes;
  and launch after expiry;
- strict parsing and serialization;
- bounded overrides and malformed-entry refusal;
- exact summaries and return epochs;
- no canonical state or side effect in the pure layer.

Expected red: `sidepulse.dnd_policy` does not exist.

### Step 2: Implement the immutable model and schedule evaluator

Keep it AppKit-free. Extract the existing local-time resolver into the shared
pure module, retain the mailbox behavior byte-for-byte, and inject timezone-aware
wall time into DND policy. Do not read Settings, Focus, the filesystem, or timers
inside the policy module.

### Step 3: Add compatible Settings fields

Add the bounded scalar fields from the design, owned paths, lossless round trip,
unknown-field preservation, future-schema refusal, and compare-and-set behavior.
Reuse `focus_sync_enabled` as the persisted Follow macOS Focus enable bit. New
installations remain inactive.

### Step 4: Verify Task 1

Run the pure policy, Settings compatibility, and Settings concurrency files,
scoped Ruff, and scoped diff hygiene.

## Task 2: Public Focus adapter and lifecycle controller

**Ownership:**

- Create: `src/sidepulse/focus_status.py`
- Create: `src/sidepulse/dnd_controller.py`
- Modify: `packaging/build_macos_pkg.sh`
- Modify only if the produced plist contract lives elsewhere: packaging plist
  helpers or templates
- Create: `tests/test_focus_status.py`
- Create: `tests/test_dnd_controller.py`
- Modify: `tests/test_packaging_contract.py`
- Modify: `tests/test_app_bundle_security.py`
- Modify: `tests/test_status_bar_lifecycle_contract.py`
- Modify: `tests/test_unwired_modules_ratchet.py`

### Step 1: Write failing adapter and controller tests

Cover:

- lazy import on supported and unsupported macOS versions;
- typed authorization and active, inactive, or unavailable observations;
- no authorization request without the explicit method;
- source probes never call the request method;
- `NSFocusStatusUsageDescription` in static packaging source and the executable
  produced-app Info.plist smoke;
- one launch, observer set, transition timer, and close;
- injected wake, sleep, screen sleep/wake, activation, clock, and timezone
  notifications;
- generation-fenced timer, Focus, authorization, and save callbacks;
- save rollback preserving Settings, projection, controls, and timer;
- public coarse Focus plus optional named detail composition;
- missing named detail preserving public active truth.

### Step 2: Implement the lazy public Focus boundary

Use `INFocusStatusCenter` on macOS 12 or later. Do not depend on undocumented
KVO or a Focus-specific notification. Keep the current private named-mode reader
as optional detail only.

### Step 3: Implement the controller

Inject clocks, timers, Settings save, Focus observation, and refresh. Own one
projection and one deadline. Keep AppKit controls out of this module.

### Step 4: Run a read-only source probe

In a fresh process, read public Focus authorization and coarse status if the API
exists. Record the OS and result. Do not request authorization or mutate TCC.

### Step 5: Verify Task 2

Run the adapter, controller, packaging, lifecycle, and unwired ratchet files,
scoped Ruff, `py_compile`, and diff hygiene.

## Task 3: Runtime policy integration

**Ownership:**

- Modify: `src/sidepulse/signal_selection.py`
- Modify: `src/sidepulse/brightness_policy.py`
- Modify: `src/sidepulse/signals.py`
- Modify: `src/sidepulse/notification_arbitration.py`
- Modify: `src/sidepulse/status_bar_legacy.py`
- Modify: `src/sidepulse/virtual_device.py`
- Modify: `src/sidepulse/why_light_context.py`
- Modify: `src/sidepulse/local_health.py`
- Modify: `tests/test_signal_selection.py`
- Modify: `tests/test_brightness_policy.py`
- Modify: `tests/test_notification_arbitration.py`
- Modify: `tests/test_announcer_stack_wiring.py`
- Modify: `tests/test_screen_bar_motion.py`
- Modify: `tests/test_status_bar_lifecycle_contract.py`
- Modify: `tests/test_architecture_ratchets.py`
- Modify: `tests/test_why_light_context.py`
- Modify: `tests/test_why_light_wiring.py`
- Modify: `tests/test_local_health.py`
- Modify: `tests/test_local_health_wiring.py`

### Step 1: Write failing cross-surface policy tests

For every mode, cover standing agent state, ask, failure, low battery, courtesy
claim, completion, notification banner, chime, notification webhook, physical
brightness, Screen Bar brightness, announcer, gauges, and menu ledger.

Also cover:

- Dim plus Mute composing on separate axes;
- distinct banner, audio, and notification-webhook grants, including a Mute
  path whose visuals remain live while all three outbound effects are refused;
- zero staying zero past Screen Bar and escalation floors;
- Pause admitting critical but not routine output;
- Asks Only admitting only current asks and their escalation;
- Fully Dark suppressing all presentation without mutating canonical truth;
- current standing truth returning after DND without replaying finite effects;
- remote sync, ingestion, history, and persistence remaining active;
- bounded mode and return-time facts in Why This Light and local health without
  content, unbounded collections, or output-shape drift;
- old quiet selectors delegating to durable Mute overrides;
- controller and facade shrink-only ratchets.

### Step 2: Integrate one immutable projection

Collect it once per refresh generation. Pass explicit axes to pure selection,
brightness, and interrupt functions. Do not reload Settings or Focus from render
or notification effect sites.

### Step 3: Verify Task 3

Run the affected pure and controller files, all relevant lifecycle and
architecture ratchets, scoped Ruff, `py_compile`, and diff hygiene.

## Task 4: Native Settings card and root-menu control

**Ownership:**

- Create: `src/sidepulse/dnd_settings_pane.py`
- Modify: `src/sidepulse/settings_window.py`
- Modify: `src/sidepulse/settings_navigation.py` only if an existing anchor needs
  a stable alias, never to add a category
- Modify: `src/sidepulse/menu_projection.py`
- Modify: `src/sidepulse/status_bar.py`
- Modify: `src/sidepulse/status_bar_legacy.py`
- Create: `tests/test_dnd_settings_pane.py`
- Modify: `tests/test_settings_accessibility.py`
- Modify: `tests/test_settings_navigation.py`
- Modify: `tests/test_every_menu_action_responds.py`
- Modify: `tests/test_menu_projection.py`
- Modify: `tests/test_compact_menu_wiring.py`
- Modify: `tests/test_settings_window_injection_ratchet.py`
- Modify: `tests/test_unwired_modules_ratchet.py`

### Step 1: Write failing presentation and interaction tests

Cover:

- the Focus pane receives one DND card and no new category;
- schedule, times, mode, Dim fraction, Focus mode, authorization, temporary
  actions, status, and exact return time;
- all five one-hour menu actions;
- production compact-menu adapter inputs for DND mode, source, and return time;
- `DND: Off`, temporary exact-return, and scheduled exact-return titles while
  retaining the compact root row budget;
- temporary Resume, End Override, and DND Settings routing;
- retained refresh without rebuilding controls or preserving stale callbacks;
- save refusal and authorization denial keeping the previous controls;
- exact labels, values, help, descriptions, key-view loop, disabled states, and
  non-color status distinctions;
- menu and Settings selectors resolve through one controller path;
- no old standalone Quiet item remains.

### Step 2: Implement the bounded native card and menu

Reuse `native_ui` factories and current Focus-pane ownership. Extract the card
instead of growing `settings_window.py` beyond its shrink-only ceiling. Update
the `status_bar.py` production compact-menu adapter so it carries the same typed
DND state instead of translating back to the old Quiet boolean.

### Step 3: Verify Task 4

Run pane, accessibility, navigation, menu projection, compact-menu wiring,
menu-action, lifecycle, and injection ratchets, scoped Ruff, `py_compile`, and
diff hygiene.

## Task 5: Native receipts and product documentation

**Ownership:**

- Create:
  `.superpowers/sdd/2026-08-30-jr-bar-p3-38-manual-scheduled-dnd/`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/FEATURE-MATRIX.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `docs/VISION.md`
- Modify:
  `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`
- Modify: this plan

### Step 1: Render source-native receipts

Render off, manual Mute, scheduled Dim, Focus Pause, Asks Only override,
scheduled Fully Dark, temporary Resume, and Focus unavailable in Aqua and Dark
Aqua through the production DND pane and menu projection. Bind source and image
SHA-256 values in a deterministic manifest.

Inspect exact copy, active source, mode, return time, control state, focus,
keyboard order, accessibility readback, geometry, contrast, clipping, and
deterministic rerendering.

### Step 2: Record source-only limits

Document that P3.38 does not prove live Focus authorization or transition,
every locale or DST case, installed-app VoiceOver, physical devices, signing,
notarization, packaging, publication, updater behavior, or release readiness.

### Step 3: Verify Task 5

Run the harness twice, require exact manifest matches, inspect every PNG, run
focused receipt tests, Ruff, `py_compile`, receipt validation, and doc diff
hygiene.

## Task 6: Consolidated review and batch gates

### Step 1: Run one combined focused P3.38 tranche

Run all new DND and Focus files plus affected Settings, signal, brightness,
notification, Screen Bar, menu, accessibility, lifecycle, architecture,
packaging, and unwired-module tests.

### Step 2: Run findings-first independent review

Review exact mode distinctions, schedule and DST behavior, public/private Focus
boundaries, permission language, Settings rollback, stale callbacks, timer
ownership, signal and interruption admission, zero-floor safety, finite-effect
non-replay, menu and Settings truth, accessibility, and facade ratchets.

Fix every validated Critical or Important finding, rerun the affected slices,
then repeat the combined focused tranche.

### Step 3: Bind the stable source

Create the sorted SHA-256 manifest for non-generated `src/` and `tests/` files.
Record file count and aggregate fingerprint before broad gates.

### Step 4: Run broad gates once

```bash
.venv/bin/ruff check src tests
make fast
.venv/bin/python -m pytest -q
```

Recompute the source/test fingerprint and require an exact match.

### Step 5: Close P3.38

Record changed files, focused and broad commands, counts, timings, fingerprint,
native hashes, the read-only public Focus receipt, review verdict, and remaining
limits. Advance the completion contract to P3.39 only after every source gate is
green.

## Final closeout evidence

Tasks 1 through 6 are implemented and verified in the shared source tree. The
final expanded effect-site rereview found no remaining issue. The final combined
runtime aggregate, including the asynchronous post-transition finite-cue
regression, passed 750 tests in 15.86 seconds. The DND Settings pane, compact
menu, controller, and lifecycle final rereviews also found no remaining issue.

The source-AppKit harness produced 16 inspected PNGs for eight states in Aqua
and Dark Aqua. Two complete runs matched. The focused receipt suite passed 40
tests in 3.49 seconds. Exact SHA-256 values are:

- manifest:
  `1e364f70181495b01d33746b7d53423b00a2093601ce2b2284bcc5f7842bed26`;
- aggregate image set:
  `38d5e61971a27ed950fc5a450b64f33db3b5f93c57c59d2a48dc4d796992b6b5`;
- sorted per-PNG hash list:
  `3a447a044181e2f47ab6f906f861de726aebba1fd7d5d9a7f89a68ac53aade9a`;
- production DND Settings pane:
  `13e306df11fdfab540f1e498c4be3894ceba7b8208ae240d911fac8cf6fe8366`;
- production menu projection:
  `2ebdfc23f9c6cd839f42670e27336622892fd7ba9bd0addc8317fc28450346dc`;
- production status-bar adapter:
  `2fa701e4e3b20246b9f2a8fe4fc403c77b618c9d86eeeec0eea0aa090e7301db`;
- receipt harness:
  `ecf51dbf0e51b7262ee079e7e79623b891ba654339b44f3acb51d067405ed982`.

A fresh macOS 27.0 source process called only the public Focus `observe()`
boundary and returned authorization `not_determined` with activity
`unavailable`. It did not request Focus authorization or change TCC state.

The final combined P3.38 tranche passed 1,759 tests plus 7 subtests in 364.65
seconds. All-source Ruff passed. The post-fix `make fast` passed in 59.71 seconds
with 113 contract tests, 150 fixture tests, and 542 focused tests. The complete
suite passed 7,754 tests plus 7 subtests in 422.41 seconds with four known
multiprocessing fork warnings. All 555 bound `src/` and `tests/` files retained
fingerprint
`ad795efd9a755e4293d2dbe344a8feb95ee21b1e6f00d7c5df3c2b10f0b77270`
before and after the broad gates. P3.38 is closed in source, and the completion
contract advances to P3.39 safe Clear Agents.

The current receipts do not prove live Focus authorization or a real Focus
transition, every locale or DST boundary, installed-app VoiceOver, physical
hardware or Screen Bar output, signing, notarization, packaging, publication,
updater behavior, or release readiness.
