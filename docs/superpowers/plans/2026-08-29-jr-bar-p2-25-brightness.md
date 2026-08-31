# P2.25 Brightness Policy Extraction Plan

> **For Codex:** Implement this tranche with the test-driven-development and subagent-driven-development workflows. Keep display-brightness reads, Focus observation, time-of-day lookup, settings persistence, refresh triggering, and hardware writes at their current boundaries. Do not commit, push, package, install, publish, change permissions, or add a dependency.

**Goal:** Extract JR Bar's ambient and signal brightness math into one AppKit-free policy module without changing the current visible brightness behavior for physical devices or the Screen Bar.

**Architecture:** A new `brightness_policy.py` module will own the pure multiplication order, escalation visibility floor, Screen Bar minimum-glow floor, signal-path focus-off rule, and optional trace metadata. The retained controller will keep live fact collection, including auto-brightness reads, idle timing, Focus state, night-hour checks, refresh scheduling, and device selection, then pass resolved scalars into the pure policy functions. This keeps the runtime trigger path unchanged while making the decision layer testable and reusable for future explainability work.

**Tech Stack:** Python 3.10+, frozen dataclasses, existing settings and brightness normalization helpers, pytest, Ruff, AppKit only in adapters.

**Spec:** `docs/superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md`

**Status:** Complete on 2026-08-29.

---

## Global constraints

- Preserve the current ambient order exactly: `base * idle * focus * night * global * escalation`, then escalation floor, then Screen Bar minimum-glow floor, then brightness normalization.
- Preserve the current signal path exactly: configured device brightness times the master brightness dial and escalation boost, with an explicit Focus "turn off" rule still forcing zero.
- Keep auto-brightness reads, Focus observation, night-hour lookup, and refresh triggering in the retained controller and worker path.
- Do not change settings schema, saved values, UI copy, preview behavior, notification behavior, or hardware command scheduling.
- Keep receipts source-only unless a later installed-app or hardware pass is explicitly run.

## Task 1: Pin the current brightness contract in failing pure tests

**Files:**
- Create: `src/sidepulse/brightness_policy.py`
- Create: `tests/test_brightness_policy.py`

- [x] Add tests for ambient brightness multiplication order, including compounded idle and Focus dimming.
- [x] Add tests for signal brightness preserving the explicit split from ambient dimming.
- [x] Add tests for escalation floor application, Screen Bar minimum-glow floor application, and the ordering between those floors.
- [x] Add tests for Focus-off forcing signal brightness to zero while ambient Screen Bar floor still only guards non-zero values.
- [x] Add trace-shape tests only for behavior needed by downstream explainability, not for implementation trivia.
- [x] Run `./.venv/bin/python -m pytest -q tests/test_brightness_policy.py` and record the expected red state.

Expected red receipt: collection first failed with `ModuleNotFoundError: No module named 'sidepulse.brightness_policy'`.

## Task 2: Rewire the retained controller to the pure policy

**Files:**
- Modify: `src/sidepulse/status_bar_legacy.py`
- Modify focused controller tests only where a direct runtime contract is missing.

- [x] Keep manual-vs-auto base selection in the retained controller.
- [x] Keep idle timeout, active Focus resolution, and night schedule resolution in the retained controller.
- [x] Replace inline ambient and signal brightness math with calls into the pure module.
- [x] Preserve current normalization, device-specific floors, and signal semantics exactly.
- [x] Do not move worker submission, display-environment results, refresh thresholds, settings writes, or LED program generation into the pure module.

## Task 3: Ratchet, verify, and document

**Files:**
- Modify: `tests/test_architecture_ratchets.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`
- Modify: this plan with the final receipt.

- [x] Add `brightness_policy.py` to the pure-production-module ratchet.
- [x] Add a source contract proving the retained controller delegates brightness policy instead of re-owning the multiplication and floor logic inline.
- [x] Run Ruff over changed Python files and `git diff --check`.
- [x] Run focused brightness-policy, controller, runtime-worker, settings, and architecture tests.
- [x] Run `make fast`.
- [x] Obtain an independent findings-first review and fix any confirmed defects.
- [x] Run the complete suite against a stable before and after source or test fingerprint.
- [x] Update architecture, local verification, completion-contract, and tranche receipts with exact results and source-only limitations.

## Acceptance criteria

- One AppKit-free module owns brightness policy decisions for ambient and signal paths.
- The retained controller still owns fact gathering, settings, refresh, and hardware side effects.
- Ambient brightness preserves the current multiplication order and both floors exactly.
- Signal brightness preserves the current "cut through ambient dimming" semantics and Focus-off override.
- Focused tests, Ruff, the canonical fast gate, independent review, complete suite, and fingerprint stability pass.
- No installed-app, physical-device, packaging, signing, notarization, publication, or release claim is made from this tranche alone.

## Completion receipt

- The required red first failed with `ModuleNotFoundError: No module named 'sidepulse.brightness_policy'`.
- `brightness_policy.py` now owns the exact ambient multiplication order, escalation visibility floor, Screen Bar minimum-glow floor, signal minimum visibility floor, and signal Focus turn-off override.
- The retained controller still owns manual-versus-auto base selection, display-brightness reads, idle timing, Focus observation, night-hour checks, refresh triggering, and hardware writes.
- The pure brightness contract passed 13 tests. The focused policy,
  controller, global-brightness, display-brightness, architecture, Focus,
  Screen Bar, and device-brightness gate passed 82 tests in 2.56 seconds.
- `make fast` passed in 13.69 seconds with 95 contract tests, 139 fixture and
  schema tests, and 298 focused tests.
- `git diff --check` passed for the tranche files.
- An independent bounded static review found no parity, trace, purity, or
  controller-ownership defect.
- The frozen-tree complete suite passed 6,891 tests plus 7 subtests in 321.58
  seconds with the four known Python multiprocessing fork warnings.
- The bound `src/` and `tests/` fingerprint covered 494 source/test files and
  remained `418660d98ec4743110bf8665c035dd82cb3b7952a8212d30b5aee1a81bbefe1f`
  before and after the complete run.
- Source-only limitation: no installed-app, physical-device, packaging, signing, notarization, publication, or release verification was performed by this tranche.
