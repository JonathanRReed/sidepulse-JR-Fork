# P2.25 Effect Selection Extraction Plan

> **For Codex:** Implement this tranche with the test-driven-development and subagent-driven-development workflows. Keep AppKit construction, target/action dispatch, persistence, refresh, and preview delivery at their current boundaries. Do not commit, push, package, install, publish, change permissions, or add a dependency.

**Goal:** Give blend modes, feel presets, preview scenarios, and per-provider animations one AppKit-free catalog and one validated selection policy, so new effects no longer require duplicated choice lists and ad hoc payload parsing across the controller, Agents pane, and Color Studio.

**Architecture:** A new `effect_selection.py` module will own immutable option descriptors and pure payload-to-selection plans. `settings_window_controls.py` and `settings_window.py` will render those descriptors into native popups. The retained controller and Studio action object will consume typed plans, then perform only settings assignment, persistence, refresh, message, and preview side effects. Compatibility popup helpers remain importable through explicit delegation rather than duplicate catalogs in `status_bar_legacy.py`.

**Tech Stack:** Python 3.10+, frozen dataclasses and enums, existing `ColorSettings` and color constants, pytest, Ruff, AppKit only in view adapters.

**Status:** Complete on 2026-08-29.

---

## Task 1: Pin the pure option and selection contract in a red test

**Files:**
- Create: `src/sidepulse/effect_selection.py`
- Create: `tests/test_effect_selection.py`

**Interfaces:**
- `EffectOption(value: str, label: str, description: str = "")`
- `EffectSelectionDisposition`: `INVALID`, `NO_CHANGE`, and `APPLY`
- `EffectSelectionPlan(disposition, value, colors, provider=None)`
- Immutable `BLEND_MODE_OPTIONS`, `COLOR_PRESET_OPTIONS`, `PREVIEW_SCENARIO_OPTIONS`, and `PROVIDER_ANIMATION_OPTIONS`
- `selected_option_index(options, value) -> int | None`
- `preview_scenario_from_payload(payload) -> str | None`
- `plan_color_preset_selection(colors, payload) -> EffectSelectionPlan`
- `plan_blend_mode_selection(colors, payload) -> EffectSelectionPlan`
- `plan_provider_animation_selection(colors, payload) -> EffectSelectionPlan`

- [x] Write exact catalog tests that pin order, persisted value, label, description, and the explicit Custom preset row.
- [x] Write invalid-shape tests for `None`, non-dicts, missing keys, non-string values, unknown values, missing providers, and unknown motions.
- [x] Write tests proving Custom is a valid `NO_CHANGE` preset selection, while valid presets, blend modes, and provider motions return `APPLY` plans with the expected immutable `ColorSettings` result.
- [x] Write tests proving preview scenario selection validates but never mutates settings.
- [x] Write reverse-selection tests for known, unknown, and duplicate values.
- [x] Run `./.venv/bin/python -m pytest -q tests/test_effect_selection.py` and record the expected module-not-found red state.

## Task 2: Rewire every current global effect picker to the shared catalog

**Files:**
- Modify: `src/sidepulse/settings_window_controls.py`
- Modify: `src/sidepulse/settings_window.py`
- Modify: `src/sidepulse/status_bar_legacy.py`
- Modify: `tests/test_effect_selection.py`
- Modify focused UI/controller tests only where a direct contract is missing.

- [x] Build global blend, preset, and preview-scenario popups from the pure option tuples.
- [x] Build both provider-animation surfaces, Agents and Color Studio, from the same provider-animation tuple.
- [x] Use one reverse-selection helper or one stable represented-object adapter so the two panes cannot disagree after either pane changes a provider motion.
- [x] Rewire `setPreviewScenario_`, `setColorPreset_`, `setBlendMode_`, `setAgentAnimation_`, and `SidePulseStudioActions.apply_provider_animation` to the pure validators/plans.
- [x] Preserve current Custom behavior, full preset refresh, blend description and tooltip updates, saved settings, Screen Bar or physical preview updates, general refresh, and user-facing status copy.
- [x] Remove the duplicate catalog implementations from `status_bar_legacy.py`. Preserve compatibility helper names through explicit imports or delegating wrappers only if current callers require them.
- [x] Do not move AppKit objects, file writes, Notification Center behavior, hardware writes, or preview delivery into the pure module.
- [x] Run the pure tests plus focused Color Studio and wiring coverage:
  `./.venv/bin/python -m pytest -q tests/test_effect_selection.py tests/test_color_animation_studio.py tests/test_wave5_wiring.py tests/test_studio_defects.py -k "preset or blend or animation or preview"`.

## Task 3: Ratchet, render, review, and verify

**Files:**
- Modify: `tests/test_architecture_ratchets.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`
- Modify: this plan with the final receipt.

- [x] Add `effect_selection.py` to the pure-production-module ratchet.
- [x] Add a source contract that blocks option-order loops and effect payload validation from returning to the retained controller.
- [x] Run Ruff over every changed Python file and `git diff --check`.
- [x] Run focused architecture, lifecycle, composition, signal, notification, Color Studio, and effect-selection tests.
- [x] Render the relevant source AppKit pane in an isolated process and verify option order, selected state, Agents-to-Studio synchronization, descriptions, and Reduce Motion behavior. Do not interact with or replace the stale installed app.
- [x] Run `make fast`.
- [x] Obtain an independent findings-first review and fix every confirmed issue.
- [x] Run the complete suite against a stable before/after source/test fingerprint.
- [x] Update architecture, local verification, completion-contract, and tranche receipts with exact results and source-only limitations.

## Acceptance criteria

- Every current global effect choice is described once in an AppKit-free catalog.
- All four selection families fail closed on malformed represented objects.
- Agents and Color Studio use the same provider-motion choices and restore the same selected motion.
- The retained controller no longer owns effect option ordering or ad hoc mutation validation.
- AppKit, persistence, refresh, and preview side effects remain at their existing boundaries.
- Focused tests, isolated source-AppKit rendering, Ruff, the canonical fast gate, independent review, the complete suite, and fingerprint stability pass.
- Receipts remain source-only and do not claim installed-app, physical-device, packaging, signing, notarization, publication, or release proof.

## Completion receipt

- The required red test first failed with `ModuleNotFoundError` for
  `sidepulse.effect_selection`.
- The pure selector and popup contract reached 43 passing tests after the
  adversarial test-gap pass added shared popup round-trip coverage.
- The isolated source-AppKit effect, Color Studio, defect, and accessibility
  run passed 123 tests. The post-review focused effect/UI run passed 119 tests
  with 62 deselections.
- `make fast` passed in 16.96 seconds with 93 contract, 139 fixture and schema,
  and 298 focused tests. Ruff, import smoke, tracked-secret scanning, bytecode,
  dependency policy, version, and diff hygiene all passed.
- The complete suite passed 6,872 tests plus 7 subtests in 293.31 seconds with
  four known Python multiprocessing fork warnings.
- The before and after manifests covered 492 source/test files and both hashed
  to `f21ec6dcd7c55646afe1fdb2b7b379663db341a48526b21001d40c6f8a767040`.
- An independent static findings-first review returned no defects. AppKit was
  verified only through isolated source tests. The stale installed app and
  physical hardware were not touched, so this is not installed-app, animation-
  quality, packaging, signing, notarization, publication, or release proof.
