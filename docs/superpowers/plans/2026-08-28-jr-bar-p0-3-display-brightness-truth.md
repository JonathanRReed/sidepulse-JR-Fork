# JR Bar P0.3 Display Brightness Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every behavior change and superpowers:verification-before-completion before closing this tranche.

**Goal:** Make auto-brightness follow the active macOS display truthfully without treating an unreadable display as full brightness.

**Architecture:** Resolve the current main display on every read. Reject inactive or sleeping displays. Read the selected display with DisplayServices first. Only when that fails for a built-in display, run one bounded IOKit registry query and parse `BrightnessMilliNits`. Raise the existing unavailable error when neither source applies.

**Tech Stack:** Python 3, ctypes, Quartz through PyObjC, IOKit registry via `/usr/sbin/ioreg`, pytest, Ruff

**Spec:** `docs/superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md`

## Verified Basis

- Upstream SidePulse PR 36 is open at commit `3b2a3716fa9609552070aee126d44ad954c81396` and prefers `DisplayServicesGetBrightness` over `BrightnessMilliNits`.
- The installed macOS SDK documents `CGMainDisplayID`, `CGDisplayIsActive`, `CGDisplayIsAsleep`, and `CGDisplayIsBuiltin`.
- Direct local reproduction on macOS 27.0 reported CoreDisplay `1.000000` and DisplayServices `0.382354` for active built-in display 1.
- The same reproduction found `BrightnessMilliNits` value `381794` and max `1599999`. It also found the misleading raw `brightness` value `32768/65536`.

## Global Constraints

- Do not add a production dependency or request a new permission.
- Do not cache the display identifier across calls. Menu-bar ownership can move between displays.
- Never apply the built-in panel's IOKit fallback to an external display.
- Never turn unavailable, invalid, inactive, or sleeping readings into `1.0`.
- Preserve the existing `DisplayBrightnessUnavailableError` contract so callers retain manual brightness.
- Keep all subprocess work bounded by an explicit timeout.
- Do not commit, push, package, install, or mutate display settings.

---

### Task 1: Lock the truth contract with failing tests

**Files:**
- Create: `tests/test_display_brightness.py`

**Interfaces:**
- Consumes: `current_screen_brightness_fraction()` and `DisplayBrightnessUnavailableError`.
- Produces: Tests for source priority, state transitions, display transitions, external-display isolation, parser behavior, and unavailable outcomes.

- [x] **Step 1: Prove DisplayServices authority**

Assert a valid DisplayServices result for the current active display is returned without invoking the IOKit fallback.

- [x] **Step 2: Prove bounded built-in fallback**

Assert a failed DisplayServices read can use `BrightnessMilliNits` for a built-in display, including valid ratio parsing, clamping, missing keys, and zero maximum.

- [x] **Step 3: Prove external and sleep safety**

Assert an unsupported external display does not query the built-in fallback. Assert inactive and sleeping displays skip both readers and raise unavailable.

- [x] **Step 4: Prove display transitions**

Return different main-display identifiers on consecutive calls and assert each DisplayServices call receives the current identifier.

- [x] **Step 5: Run the focused tests red**

Run: `.venv/bin/pytest tests/test_display_brightness.py -q`

Expected: Tests fail because the current module still uses CoreDisplay and has no truthful fallback contract.

### Task 2: Implement the ordered readers

**Files:**
- Modify: `src/sidepulse/display_brightness.py`
- Test: `tests/test_display_brightness.py`

**Interfaces:**
- Produces: `display_services_brightness_fraction(display_id)`, `ioreg_nits_fraction(text)`, and the revised `current_screen_brightness_fraction()`.
- Invariant: A fraction is finite and between 0.0 and 1.0 before it is accepted.

- [x] **Step 1: Add the DisplayServices reader**

Load the private framework lazily, declare the `ctypes` signature, and return `None` for load errors, nonzero status, NaN, infinity, or out-of-range values.

- [x] **Step 2: Add the IOKit registry parser and bounded query**

Parse only the `BrightnessMilliNits` dictionary. Run `/usr/sbin/ioreg -rc AppleARMBacklight` with captured text, no check exception, and a short timeout.

- [x] **Step 3: Add display state and type guards**

Resolve `CGMainDisplayID()` on every call. Reject inactive or sleeping displays. Use IOKit only if `CGDisplayIsBuiltin(display_id)` is true.

- [x] **Step 4: Preserve the caller contract**

Return the first truthful fraction. Otherwise raise `DisplayBrightnessUnavailableError`, allowing existing call sites to retain manual brightness and report the unavailable state.

- [x] **Step 5: Run focused tests green**

Run: `.venv/bin/pytest tests/test_display_brightness.py tests/test_confident_nothings.py -q`

Expected: All tests pass.

### Task 3: Verify direct behavior and close the tranche

**Files:**
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-p0-3-display-brightness-truth.md`

**Interfaces:**
- Consumes: Passing implementation and direct local readings.
- Produces: P0.3 completion receipt and P0.4 as the active tranche.

- [x] **Step 1: Run focused and broad tests**

Run the new focused suite, existing brightness and honesty tests, and the canonical broad regression suite.

- [x] **Step 2: Run static and repository gates**

Run changed-file Ruff, the secret scan, release version validation, and `git diff --check`.

- [x] **Step 3: Compare the production function live**

On the current Mac, record the selected display state and compare the production function with a direct DisplayServices call. Do not change system brightness or display arrangement.

- [x] **Step 4: Review and record**

Obtain an independent bounded review, resolve findings, update the completion contract and this checklist, and do not commit.

## Completion Receipt

- All 19 new brightness-contract expectations failed before implementation and passed afterward.
- DisplayServices is now authoritative, IOKit registry data is a built-in-only fallback, and no trustworthy source raises the existing unavailable error instead of returning full brightness.
- Active, asleep, built-in, external, invalid native values, and main-display transitions are covered.
- The production function and an independent direct DisplayServices call both returned `0.382354` on active built-in display 1, with zero measured delta.
- The investigation also removed import-order-dependent automatic Screen Bar widths and the obsolete `232` reset fallback. Both settings construction and the actual “Use Automatic Size” action now use the `260` design authority.
- Focused brightness and settings scope: 59 passed.
- Broad regression gate: 911 passed and 7 subtests passed.
- Clean-file Ruff and legacy undefined-name lint passed. The 571-file secret scan, release version validation, and `git diff --check` passed.
- Independent review and late-delta re-review reported no findings.
