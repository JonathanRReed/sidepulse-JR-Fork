# JR Bar P1.16 Power Hold Choices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Use the
> Impeccable clarification and craft-floor guidance for the Power settings UI,
> and the macOS build/run workflow for the rendered gate. Do not commit, push,
> deploy, install helpers, or request permissions in this tranche.

**Goal:** Let the owner choose independently whether agent work prevents
automatic Mac sleep, whether an active hold also keeps displays awake, whether
agent holds continue on battery, and what closed-lid policy applies.

**Architecture:** Keep one `KeepAwakeController` for ordinary agent-driven
system holds and one `ClosedLidAwakeController` for the existing stronger
closed-lid path. A small pure power-policy module owns deterministic caffeinate
flag construction and user-facing policy projections. New settings persist the
ordinary agent hold and display assertion separately. Runtime setters replace
an active caffeinate child when display policy changes without changing the
underlying activity, battery, grace, or closed-lid decisions. The Power pane is
extracted from the legacy settings-window monolith into a dedicated AppKit
module and presents four clearly separated decisions.

**External evidence:** SidePulse PR
[#31](https://github.com/inteliwear/sidepulse/pull/31) identifies `caffeinate`
`-d` as `PreventUserIdleDisplaySleep` and removes it so an agent can keep the
system awake without pinning the display on. JR Bar already removed `-d` from
its ordinary hold but still has it in the closed-lid command. This tranche keeps
display sleep as the default and adds an explicit owner choice instead of
hard-coding either behavior.

**Tech Stack:** Python 3.11+, standard library subprocess control, existing
AppKit/PyObjC settings primitives, pytest, Ruff, shell verification.

**Spec:**
`docs/superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md`

## Global Constraints

- Keep display sleep allowed by default. Do not synthesize user activity or
  request a permission merely because agent work is active.
- Do not merge ordinary agent holds with the privileged closed-lid helper.
  Closed-lid `pmset disablesleep` remains separately opt-in and fail-safe.
- A positive low-battery reading may still override an agent hold. Unknown
  battery state must not silently release it.
- Changing the display choice while a hold is live must replace the caffeinate
  assertion promptly and must not leave two children running.
- Preserve historical settings documents and compatibility identifiers. New
  booleans default safely when absent and survive unrelated saves.
- Use existing native controls, spacing, help text, and settings feedback.
  Labels must describe the actual effect, not implementation flags.
- Do not add a production dependency.
- Source and rendered development-app checks do not prove signed installed-app,
  sleep/wake hardware, closed-lid thermal behavior, or release readiness.

---

### Task 1: Pure command and power-choice contracts

**Files:**
- Create: `src/sidepulse/power_policy.py`
- Create: `tests/test_power_policy.py`
- Modify: `src/sidepulse/keep_awake.py`
- Modify: `src/sidepulse/lid_sleep.py`

**Interfaces:**
- Produces: `configure_caffeinate_display_assertion(command,
  keep_display_awake) -> tuple[str, ...]` and a small immutable projection that
  names the four independent choices.
- Consumes: the existing ordinary `-ims` and closed-lid `-imsu -t ...`
  command shapes.

- [x] **Step 1: Write failing command-matrix tests**

  Cover ordinary and closed-lid commands with display sleep allowed and display
  held awake, removal of an inherited `-d`, preservation of `-i`, `-m`, `-s`,
  `-u`, `-t`, and `-w`, invalid command input, and no mutation of the caller's
  sequence.

- [x] **Step 2: Run the focused tests and verify they fail**

  Run: `.venv/bin/python -m pytest -q tests/test_power_policy.py`

- [x] **Step 3: Implement the minimal pure flag compiler**

  Default commands must be display-sleep-safe. The compiler may add `d` only
  when the explicit display choice is true, and must produce one canonical
  option bundle rather than duplicate assertions.

- [x] **Step 4: Run the pure policy tests**

  Expected: pass with no AppKit or settings import required.

### Task 2: Durable independent settings

**Files:**
- Modify: `src/sidepulse/_settings_legacy.py`
- Modify: `src/sidepulse/settings.py`
- Create: `tests/test_power_settings.py`
- Modify: `tests/test_settings_compatibility.py`

**Interfaces:**
- Produces: `agent_keep_awake_enabled: bool = True`,
  `keep_display_awake: bool = False`, `with_agent_keep_awake_enabled()`, and
  `with_keep_display_awake()`.
- Preserves: `keep_awake_on_battery` and `closed_lid_awake_policy` retain their
  current names and meanings.

- [x] **Step 1: Write failing default, round-trip, and migration tests**

  Prove display sleep is allowed by default, the agent system hold remains on by
  default, both settings round-trip independently, absent keys migrate to those
  defaults, invalid non-boolean values fall back safely, and an unrelated save
  retains both values.

- [x] **Step 2: Run settings tests and verify they fail**

  Run: `.venv/bin/python -m pytest -q tests/test_power_settings.py tests/test_settings_compatibility.py`

- [x] **Step 3: Add the two lossless settings fields**

  Update dataclass defaults, immutable setters, encoder, and loader. Do not
  reinterpret the battery or closed-lid keys and do not discard unknown readable
  extensions.

- [x] **Step 4: Run settings and schema tests**

  Run: `.venv/bin/python -m pytest -q tests/test_power_settings.py tests/test_settings_compatibility.py tests/test_settings_schema_coverage.py tests/test_keep_awake_battery.py`

### Task 3: Live controller replacement without policy coupling

**Files:**
- Modify: `src/sidepulse/keep_awake.py`
- Modify: `src/sidepulse/lid_sleep.py`
- Modify: `src/sidepulse/status_bar_legacy.py`
- Create: `tests/test_power_hold_runtime.py`
- Modify: `tests/test_lid_watchdog.py`

**Interfaces:**
- Produces: `set_keep_display_awake()` on both controllers.
- Consumes: `set_enabled()` for the ordinary agent hold and existing battery,
  grace, `holding_requested`, closed-lid policy, helper, and watchdog behavior.

- [x] **Step 1: Write failing runtime-transition tests**

  Prove an active ordinary hold changes from `-ims` to `-dims` with exactly one
  old-child termination and one replacement; prove the closed-lid child changes
  from `-imsu` to `-dimsu` without dropping its helper ownership or watchdog;
  prove disabling ordinary agent keep-awake does not rewrite the closed-lid
  policy; and prove battery yield does not alter the stored display choice.

- [x] **Step 2: Run the runtime tests and verify they fail**

  Run: `.venv/bin/python -m pytest -q tests/test_power_hold_runtime.py tests/test_lid_watchdog.py`

- [x] **Step 3: Implement bounded child replacement and controller wiring**

  `StatusBarController.sync_keep_awake()` reads the four persisted choices on
  every relevant tick. A display-policy change terminates only the caffeinate
  child whose flags are stale; the same update immediately recreates it if that
  controller should still hold. The closed-lid system-disable and renewal state
  remain intact during that child replacement.

- [x] **Step 4: Run runtime, battery, lid, and lifecycle tests**

  Run: `.venv/bin/python -m pytest -q tests/test_power_hold_runtime.py tests/test_lid_watchdog.py tests/test_keep_awake_battery.py tests/test_sidepulse.py -k 'keep_awake or closed_lid'`

### Task 4: Clarify and extract the Power settings pane

**Files:**
- Create: `src/sidepulse/power_settings_pane.py`
- Modify: `src/sidepulse/settings_window.py`
- Create: `tests/test_power_settings_pane.py`
- Modify: `tests/test_settings_window_injection_ratchet.py`

**Interfaces:**
- Produces: one dedicated builder and retained native action target for power
  settings.
- Consumes: existing `native_ui` cards, rows, switches, policy popup, message
  area, save function, and live `sync_keep_awake()` method.

- [x] **Step 1: Load the Impeccable craft floor immediately before UI edits**

  Preserve the incumbent native Operate-mode UI. Use persistent visible labels,
  nearby consequence text, and four non-overlapping concepts: Agent Work,
  Display, Battery, and Closed Lid.

- [x] **Step 2: Write failing pane, action, accessibility, and shrink tests**

  Prove the pane exposes the two new switches with visible and accessibility
  labels, updates and saves each exact setting, refreshes the live hold, keeps
  the existing battery and closed-lid controls, retains its action target, and
  makes `settings_window.py` shrink rather than increasing its audited ceiling.

- [x] **Step 3: Extract and implement the pane**

  Recommended visible copy:

  - `Keep Mac awake while agents work`
  - `Keep displays awake during holds`
  - `Continue agent holds on battery`
  - `Closed-lid policy`

  Helper text must state that turning off the display choice still keeps the Mac
  working, and that the low-battery threshold can release an unplugged hold.

- [x] **Step 4: Run pane and architecture tests**

  Run: `.venv/bin/python -m pytest -q tests/test_power_settings_pane.py tests/test_settings_window_injection_ratchet.py tests/test_architecture_ratchets.py`

- [x] **Step 5: Run the Impeccable detector once**

  Run:
  `node /Users/jonathanreed/.agents/skills/impeccable/scripts/detect.mjs --json src/sidepulse/power_settings_pane.py src/sidepulse/settings_window.py`

  Resolve valid findings in one batch. Do not run an open-ended polish loop.

### Task 5: Rendered AppKit verification and documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/FEATURE-MATRIX.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/UPSTREAM-SYNC.md`

- [x] **Step 1: Discover and use the repository's existing macOS run path**

  Confirm repository shape, existing run scripts, process name, and source-app
  authority. Do not create a SwiftPM or Xcode workflow for this Python/AppKit
  product. Launch only through the project-owned development runner.

- [x] **Step 2: Inspect one bounded rendered pass**

  Open Settings > Power in the source-built app. Verify the four decisions are
  visually separate and scannable, switches reflect saved state, help text fits,
  the titled Power window appears, distinct saved states render without changing
  battery or closed-lid choices, and relaunch restores settings. Capture
  source-app screenshots if the control surface is inspectable. Do not install
  the helper or change system permissions.

- [x] **Step 3: Fix rendered defects in one batch and confirm once**

  Follow the Impeccable bounded-pass ceiling. If the source runner or GUI is
  unavailable, record the exact blocker instead of claiming rendered proof.

- [x] **Step 4: Document the four choices and proof boundaries**

  Explain that ordinary agent hold prevents automatic system sleep, display hold
  is optional and off by default, battery may release the ordinary hold, and the
  closed-lid helper remains separately controlled and stronger.

### Task 6: Canonical verification and independent review

**Files:**
- Modify: `docs/superpowers/plans/2026-08-29-jr-bar-p1-16-power-hold-choices.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`

- [x] **Step 1: Run focused static and behavior gates**

  Run Ruff on every changed Python file, compile the changed source, run all
  Task 1 through 4 suites, and run `git diff --check`.

  Receipt on 2026-08-29: targeted Ruff passed for
  `power_policy.py`, `keep_awake.py`, `lid_sleep.py`,
  `_settings_legacy.py`, `power_settings_pane.py`, `settings_window.py`, and
  the new focused tests; `py_compile` passed for those modules plus
  `status_bar_legacy.py`; `git diff --check` passed; focused suites reported
  `56 passed in 0.86s` plus `18 passed, 826 deselected in 2.94s`. Raw
  `ruff check src/sidepulse/status_bar_legacy.py` still reports 98 pre-existing
  import-order and unused-import findings in the legacy monolith.

- [x] **Step 2: Run the canonical repository gate**

  Run: `./scripts/verify.sh --no-bootstrap`

  Post-review receipt on 2026-08-29: `6448 passed, 4 warnings, 7 subtests
  passed`. Dependency policy, the 571-file tracked secret scan, repository Ruff
  gate, version validation, package builds, Twine checks, clean-install
  verification, and SBOM generation passed. The script ended with `JR Bar
  verification passed`.

- [x] **Step 3: Obtain one independent read-only review**

  Review caffeinate flags, child replacement, battery separation, closed-lid
  helper and watchdog preservation, settings compatibility, native action
  ownership, accessible copy, monolith shrink, and proof language. Resolve every
  valid correctness, power, lifecycle, settings, or UI finding and rerun affected
  gates.

- [x] **Step 4: Record exact receipts and advance only after closure**

  Mark P1.16 complete only with focused and canonical counts, rendered evidence
  or an exact external blocker, independent-review outcome, and explicit signed
  installed-app, real sleep/wake, closed-lid hardware, and release boundaries.
  Then advance the completion contract to P1.17.

## Self-review

- Spec coverage: ordinary agent system hold, display assertion, battery choice,
  and closed-lid policy are independently persisted, rendered, and applied.
- Architecture: command policy and settings actions leave the legacy status and
  settings-window monoliths smaller, not larger.
- Safety: default permits display sleep; no permission or helper mutation occurs;
  low-battery yield and closed-lid watchdog remain authoritative.
- Placeholder scan: no deferred implementation placeholder or fake UI state is
  part of the plan.

## Closure Receipts

- Focused behavior: 57 power, settings, runtime, pane, compatibility, lid,
  battery, injection, and architecture tests passed. The controller slice added
  60 passing keep-awake and closed-lid tests.
- Static checks: Ruff passed for every extracted or directly changed P1.16
  source and test file, changed source compiled, and `git diff --check` passed.
  `status_bar_legacy.py` is 782,421 bytes and `settings_window.py` is 214,363
  bytes, both below their audited ceilings.
- Rendered source UI: an isolated AppKit process showed four Power cards,
  default switch states, visible help, and accessibility names. A fresh process
  using the same isolated settings store restored agent hold off and display
  hold on. No helper or permission was changed. The installed signed app stayed
  running and was not replaced.
- Impeccable: the required one-time detector reported no findings.
- Independent review: one valid `--` delimiter finding was fixed with a child
  argument regression test; the rereview reported no findings. A proposed
  unknown-field issue was rejected because the public lossless settings facade
  already preserves it and the cited test passes.
- Canonical post-review gate: 6,448 tests and 7 subtests passed with four known
  Python 3.13 `fork()` deprecation warnings. Dependency policy, the 571-file
  tracked secret scan, repository Ruff gate, version validation, distributions,
  Twine metadata, clean-wheel install, SBOM, and final verification passed.
- Boundaries: no actual display sleep or wake, battery transition, closed-lid
  thermal run, privileged helper mutation, physical device continuity, signed
  installed-app Power interaction, artifact signing, notarization, installation,
  publication, or release is claimed.
