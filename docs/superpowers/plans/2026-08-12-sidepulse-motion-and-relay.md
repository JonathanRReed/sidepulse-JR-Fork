# SidePulse Screen Bar Motion and Relay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and superpowers:verification-before-completion. The controller uses superpowers:subagent-driven-development for task review. Do not commit because the repository owner has not authorized commits.

**Goal:** Make Screen Bar animation display-synchronized and visually smooth, refine the Alcove silhouette, and make relay mode complete a phase-continuous traversal across every physical and virtual LED.

**Architecture:** Extend the existing pure render policy with a driver decision, then let the AppKit surface own either one native display link or one timer fallback. Define relay speed as a full-line traversal and rotate each generated program from a controller-owned monotonic phase, so frequent status changes do not restart the baton.

**Tech Stack:** Python 3.10+, PyObjC/AppKit/QuartzCore, SidePulse LED DSL/WASM, pytest, Ruff.

## Global Constraints

- Preserve exactly two repetitions for transient failure signals and the current attention preamble priority.
- Do not change the static 4 Hz watcher or existing low-power and thermal caps except where required to select the driver.
- Normal active animation uses native display cadence only when the macOS 14 AppKit display-link API is available.
- Display-link creation failure must fall back to the current common-mode timer.
- At most one frame driver may be active per Screen Bar lifecycle.
- Alcove bracket radius is 8 points, clamped to half the bracket width and height.
- Relay speed means one complete LED-line traversal.
- A relay traversal visits every LED exactly once before wrapping.
- Physical and virtual output consume the same controller-owned elapsed relay phase, mapped to each surface's LED count.
- Do not add a production dependency.
- Do not commit, push, install, restart, deploy, write a mounted device, or mutate user state.
- Use strict RED before GREEN and record exact commands and output in each task report.

---

### Task 1: Pure display-driver and Alcove geometry policy

**Files:**
- Modify: `src/sidepulse/render_policy.py`
- Modify: `src/sidepulse/virtual_device.py`
- Create: `tests/test_screen_bar_motion.py`
- Test: `tests/test_render_policy.py`

**Interfaces:**
- Add `RenderDriverKind(str, Enum)` with `PAUSED`, `DISPLAY_LINK`, and `TIMER`.
- Add `RenderSchedule(driver: RenderDriverKind, cadence: RenderCadence)`.
- Add `choose_render_schedule(environment, animation_active, *, display_link_available) -> RenderSchedule`.
- Add `alcove_bracket_corner_radius(width: float, height: float) -> float`.

- [ ] **Step 1: Write failing pure policy tests**

Use literal table expectations that prove:

- hidden and sleeping surfaces return `PAUSED` with zero paint and sample cadence;
- nominal active animation with display-link availability returns `DISPLAY_LINK` and retains a 60 Hz source-sampling contract for compatibility metrics;
- nominal active animation without display-link availability returns `TIMER` at 60 Hz;
- low-power and fair, serious, and critical thermal states retain the exact existing timer caps;
- static output always uses `TIMER` at the existing static watcher cadence;
- bracket radii are `8.0` for ordinary Alcove brackets and clamp for dimensions smaller than 16 points.

- [ ] **Step 2: Run the focused tests and observe the expected missing-interface failures**

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_screen_bar_motion.py tests/test_render_policy.py -q`

Expected: collection or assertion failure because the schedule and geometry helpers do not exist.

- [ ] **Step 3: Implement the minimum pure policy**

`choose_render_schedule` must delegate all numeric cadence decisions to the existing `choose_render_cadence` function. It selects `DISPLAY_LINK` only for active, visible, awake, nominal, non-low-power output when the API is available. All constrained or static output uses `TIMER`. Paused cadence uses `PAUSED`.

`alcove_bracket_corner_radius` returns `max(0.0, min(8.0, width / 2.0, height / 2.0))`. Replace the independent bracket constant in the draw path with this helper while preserving the existing notch-bottom radius and Alcove width hysteresis.

- [ ] **Step 4: Run the focused tests and Ruff**

Run:

`ruff check src/sidepulse/render_policy.py src/sidepulse/virtual_device.py tests/test_screen_bar_motion.py tests/test_render_policy.py`

Run the focused pytest command from Step 2.

Expected: all selected checks pass.

- [ ] **Step 5: Write the task report**

Record the RED reason, GREEN counts, changed files, and the exact preserved cadence table. Do not claim installed visual acceptance.

### Task 2: AppKit display-link lifecycle

**Files:**
- Modify: `src/sidepulse/virtual_device.py`
- Modify: `tests/test_screen_bar_motion.py`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- `VirtualStatusDevice` owns `display_link` in addition to the existing `timer` fallback.
- Add private lifecycle helpers `_display_link_available()`, `_install_display_link()`, `_invalidate_frame_driver()`, and `_apply_render_schedule(schedule)`.
- The existing `redraw_` selector remains the single frame callback.

- [ ] **Step 1: Write failing lifecycle tests with complete AppKit doubles**

Cover these observable behaviors:

- nominal active output creates the display link through the current view, adds it to the current run loop in `NSRunLoopCommonModes`, and leaves `timer` unset;
- each callback samples and invalidates the view exactly once;
- low-power or thermal-constrained output invalidates the display link before installing the capped timer;
- display-link construction or run-loop registration failure installs the 60 Hz timer fallback;
- hide, display sleep, and teardown invalidate both possible drivers;
- wake or show installs one new driver, never two;
- moving to a newly built view invalidates the old link before binding the new view;
- static-frame demotion replaces the link with the static timer and a later program change promotes it back.

- [ ] **Step 2: Run the focused lifecycle tests and observe failure against timer-only code**

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_screen_bar_motion.py tests/test_sidepulse.py -q -k 'display_link or screen_bar_driver or visible_screen_bar_pauses or identical_frames_demote'`

- [ ] **Step 3: Implement lifecycle-safe display-link ownership**

On macOS 14 or newer, a view that responds to `displayLinkWithTarget_selector_` creates the link with selector `redraw:`. Add it to `NSRunLoop.currentRunLoop()` for `NSRunLoopCommonModes`. Native cadence remains uncapped by a requested frame rate. Keep `view.setRenderFps_(ACTIVE_RENDER_FPS)` so source sampling never falls behind display callbacks.

Every driver transition first invalidates the previous driver. `hide()`, screen sleep, and view teardown clear the driver reference and cadence state. Failure is caught once at the adapter boundary and immediately falls back to `NSTimer`; no retry loop or unbounded logging is added.

- [ ] **Step 4: Run focused and neighboring Screen Bar gates**

Run Ruff over owned files.

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_screen_bar_motion.py tests/test_render_policy.py tests/test_sidepulse.py -q -k 'ScreenBar or screen_bar or VirtualStatus or display_link or render or gradient or alcove'`

Expected: all selected tests pass, including existing sleep, gradient-cache, and common-mode timer fallback tests.

- [ ] **Step 5: Write the task report**

Include API availability handling, driver transition table, RED and GREEN receipts, and the remaining installed motion check.

### Task 3: Full-line relay timing and phase continuity

**Files:**
- Modify: `src/sidepulse/colors.py`
- Modify: `src/sidepulse/led_status.py`
- Modify: `src/sidepulse/status_bar.py`
- Create: `tests/test_relay_motion.py`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- Add `relay_step_ms(traversal_seconds: float, led_count: int) -> int`.
- Add `relay_phase_index(elapsed_seconds: float, traversal_seconds: float, led_count: int) -> int`.
- Add `relay_led_order(led_count: int, start_index: int) -> tuple[int, ...]`.
- Extend relay program generation with keyword-only `relay_elapsed_seconds: float = 0.0`.
- `SidePulseController` owns one monotonic `_relay_epoch` and supplies the same elapsed duration to every physical and virtual relay program generation.

- [ ] **Step 1: Write failing pure and temporal relay tests**

Use hand-derived expectations to prove:

- 1.6 seconds across 8 LEDs yields a 200 ms step and one 1.6 second traversal;
- 1.6 seconds across 2 LEDs yields an 800 ms step;
- orders starting at 0 and at 6 visit every 8-LED index exactly once before wrapping;
- phase indices at the start, midpoint, last step, exact wrap, and large elapsed values are correct;
- invalid or nonpositive traversal values use the existing normalized speed floor;
- zero LEDs produce no relay lines and one LED preserves the existing single-agent program.

Add real WASM temporal tests for two and eight LEDs. Parse the generated program at a literal epoch, step at hand-selected timestamps, and assert the brightest index set covers the full line within one traversal.

- [ ] **Step 2: Run the new relay tests and observe the per-LED-duration failure**

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_relay_motion.py tests/test_sidepulse.py -q -k relay`

Expected: assertions show the current 1.6-second setting requires 12.8 seconds on eight LEDs or that the missing phase interfaces cannot be imported.

- [ ] **Step 3: Implement the pure traversal contract**

Compute the per-LED delay from full traversal duration divided by LED count. Derive the current index from `relay_elapsed_seconds`, then rotate LED indices before assigning delays. Preserve agent color alternation, brightness, attention preamble, program byte cap, and line-count cap.

Phase math is modulo the LED count and derives from elapsed monotonic time. No clock calls belong in `colors.py`.

- [ ] **Step 4: Add controller-owned phase continuity**

Initialize `_relay_epoch` once in controller initialization. For every relay program generation, compute one `relay_elapsed_seconds = time.monotonic() - _relay_epoch`. Feed that same elapsed duration to the physical controller and virtual Screen Bar. Each renderer maps the shared traversal fraction to its own LED count, so two-LED and eight-LED surfaces remain semantically phase-aligned. Preserve the existing physical write completion timestamp as the virtual parse anchor when available.

Add a production-path regression that generates relay output, advances the controller clock beyond LED 1, changes an agent status so the program is rebuilt, and proves the rebuilt program starts at the expected current phase rather than LED 0. Add a two-versus-eight LED assertion that both starts represent the same traversal fraction.

- [ ] **Step 5: Run relay, color, device, and projection gates**

Run Ruff over owned files.

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_relay_motion.py tests/test_sidepulse.py -q -k 'relay or round_robin or led_program or virtual_status or projection_contract'`

Expected: all selected checks pass and temporal tests cover both hardware sizes.

- [ ] **Step 6: Write the task report**

Record the prior 12.8-second traversal, the new exact traversal timing, rebuild continuity, program bounds, and remaining physical-device acceptance.

### Task 4: Motion and relay integration verification

**Files:**
- Modify only if a regression test exposes a source defect in Tasks 1 through 3.
- Report: `.superpowers/sdd/2026-08-12-sidepulse-motion-and-relay/task-4-report.md`

- [ ] **Step 1: Run the complete source gates under the hardware guard**

Run:

`ruff check src tests`

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/ -q`

Run:

`git diff --check`

The test harness must reject writes to `/Volumes/SidePulsePro`, `/Volumes/SidePulseDot`, and `/Volumes/SIDEPULSE`. If any selected test attempts a live path, fix or isolate that test before rerunning. Do not disable the guard.

- [ ] **Step 2: Review motion invariants from the final diff**

Confirm one frame driver at a time, native display cadence only in the nominal path, timer fallback in constrained and unavailable paths, time-based smoothing, clamped 8-point Alcove radius, full-line relay timing, phase continuity, and shared physical/virtual program generation.

- [ ] **Step 3: Record the source receipt and installed boundary**

List exact commands, counts, changed files, and any skipped test. State clearly that actual motion quality, notch fit, and mounted-device traversal remain unverified until the candidate is built and observed.
