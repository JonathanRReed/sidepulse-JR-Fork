# SidePulse Stabilization-First Implementation Plan

> **Historical.** Executed in 2026-08. The `runtime_truth` plane this plan
> introduces was deleted 2026-08-26 in the 0.5.0 coalescence — do not
> re-implement from this document.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SidePulse truthfully report agent activity and provider usage, maintain one stable device and runtime identity, isolate tests from the host Mac, simplify the menu and settings, and improve the Screen Bar without adding another integration or settings pane.

**Architecture:** Preserve the existing Python provider, hardware, and persistence core while extracting pure truth, identity, provider-usage, menu-projection, and Screen Bar geometry modules. Keep AppKit as a host, but stop adding business logic to `status_bar_legacy.py`. Deliver four sequential pull requests so each tranche leaves a usable application.

**Tech Stack:** Python 3.10–3.13, PyObjC/AppKit, pytest, macOS launchd, provider-owned local files and consented browser sources, SidePulse LED DSL.

## Global Constraints

- `SessionStart` never means working.
- Grok hooks installed after a session starts must report reload-required until a real prompt/tool event arrives.
- T3 Code and Alcove are the only external application integrations.
- CodexBar must not be imported, launched, queried, configured, or required.
- Exactly one process owns `events.sock`: foreground or LaunchAgent.
- Physical device identity must not be the current mount path.
- Tests must never write the real settings file, launchd domain, `/Volumes`, provider configs, Keychain, or network.
- Provider failures must be actionable states, never flattened to `no reading`.
- Provider and filesystem work stays off the AppKit main thread.
- Critical visual states differ by motion or shape as well as color.
- Do not grow `status_bar_legacy.py`; new logic belongs in focused modules.

---

## Pull Request 1: Runtime truth, one process, and hard test isolation

### Task 1: Runtime truth model

**Files:**
- Create: `src/sidepulse/runtime_truth.py`
- Create: `tests/test_runtime_truth.py`

**Interfaces:**
- Produces: `HookTruth`, `HookTruthState`, `ProcessTruth`, `ProcessOwner`, `classify_hook_truth()`, `classify_process_truth()`.

- [ ] Write failing tests for not-configured, Grok reload-required, awaiting first activity, idle after observed activity, working, stale, foreground ownership, LaunchAgent ownership, and ownership conflict.
- [ ] Run `pytest tests/test_runtime_truth.py -q` and confirm the missing module fails the tests.
- [ ] Implement immutable enums/dataclasses and pure classifiers.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Duplicate hook event suppression

**Files:**
- Create: `src/sidepulse/hook_dedupe.py`
- Create: `tests/test_hook_dedupe.py`
- Modify: `src/sidepulse/hook.py`

**Interfaces:**
- Produces: `HookEventDeduplicator.accept(event_token: str) -> bool`.
- Consumes: normalized provider event tokens before JSONL append.

- [ ] Write failing tests proving the same token is accepted once across separate instances and a bounded history evicts old tokens.
- [ ] Implement an owner-private, flock-protected, bounded JSON sidecar.
- [ ] Call it before `write_normalized_hook_record()` appends a line.
- [ ] Verify duplicate `session_start` records are not appended twice.

### Task 3: Collapse the final controller wrapper and remove CodexBar runtime code

**Files:**
- Modify: `src/sidepulse/_status_bar_production.py`
- Replace: `src/sidepulse/status_bar.py`
- Modify: `src/sidepulse/integration_cli.py`
- Modify: `src/sidepulse/_integration_settings_legacy.py`
- Modify: `src/sidepulse/resources/integration_compatibility.json`
- Delete: `src/sidepulse/codexbar_compat.py`
- Delete: `src/sidepulse/_codexbar_compat_legacy.py`
- Update tests that currently require CodexBar.

**Interfaces:**
- `_status_bar_production.JRStatusBarController` becomes the only PyObjC subclass over the retained controller.
- `status_bar.py` becomes a thin module facade.
- `sidepulse integrations` exposes T3 Code only.

- [ ] Write source-contract tests proving there is one controller subclass and no runtime CodexBar import.
- [ ] Move the existing T3 service lifecycle into the production controller.
- [ ] Remove CodexBar settings, CLI, diagnostics, package imports, and compatibility manifest data.
- [ ] Replace the public facade without rebinding another Objective-C class.
- [ ] Verify import/reload and source-introspection contracts.

### Task 4: Honest setup and process ownership

**Files:**
- Create: `src/sidepulse/setup_truth.py`
- Modify: `src/sidepulse/doctor.py`
- Modify: `src/sidepulse/status_bar.py` or extracted menu projection module
- Test: `tests/test_setup_truth.py`, `tests/test_doctor.py`

- [ ] Write failing tests for installed-but-unloaded LaunchAgent, foreground owner, launchd owner, and socket conflict.
- [ ] Make Doctor report plist presence, loaded job, process owner, and socket owner as separate facts.
- [ ] Add Grok reload guidance and a T3-detected-but-disabled action row.
- [ ] Ensure source-checkout setup chooses foreground mode unless the user explicitly switches ownership.

---

## Pull Request 2: Stable device identity and compact menu

### Task 5: Stable hardware identity and migration

**Files:**
- Create: `src/sidepulse/device_identity.py`
- Create: `src/sidepulse/device_inventory.py`
- Modify: `src/sidepulse/status_bar.py`
- Modify: `src/sidepulse/settings.py`
- Test: `tests/test_device_identity.py`, `tests/test_device_inventory.py`

**Interfaces:**
- Produces: `PhysicalDeviceIdentity`, `derive_device_identity()`, `merge_remounted_devices()`, `migrate_remembered_devices()`.

- [ ] Write failing tests for volume UUID identity, disk-id fallback, remount merging, transient-path rejection, ghost pruning, and preserving device preferences.
- [ ] Implement identity independent of mount path.
- [ ] Migrate existing path-based rows to stable keys.
- [ ] Fix name disambiguation so `SidePulse Dot` never becomes `SidePulse Dot Dot`.

### Task 6: Compact menu projection

**Files:**
- Create: `src/sidepulse/menu_projection.py`
- Modify: `src/sidepulse/status_bar.py`
- Test: `tests/test_menu_projection.py`

- [ ] Write failing tests limiting the root menu to a compact set of semantic rows.
- [ ] Group physical devices under one `Devices` submenu.
- [ ] Collapse capacity to one `Usage` row and move Profiles/Timer into relevant submenus or windows.
- [ ] Show actionable warning rows for silent hooks, permission-required usage, disconnected T3, and process conflict.
- [ ] Remove the permanent tip row and hide Setup when the system is healthy.

---

## Pull Request 3: Native provider accounting and actionable Usage Center

### Task 7: Provider-source health and CLI

**Files:**
- Create: `src/sidepulse/provider_usage_platform.py`
- Create: `src/sidepulse/provider_usage_cli.py`
- Modify: `src/sidepulse/cli_entry.py`
- Test: `tests/test_provider_usage_platform.py`, `tests/test_provider_usage_cli.py`

**Interfaces:**
- Produces: provider descriptors, account/source health, dynamic quota lanes, `sidepulse providers status|enable|configure|refresh`.

- [ ] Write failing tests for source ordering, dynamic lanes, stale last-known-good, permission-required states, and CLI routing.
- [ ] Implement the provider registry for Codex, Claude, Cursor, Devin, Grok, Antigravity, and OpenAI API.
- [ ] Ensure every failure includes an action and reason code.

### Task 8: First-party source implementations

**Files:**
- Create focused modules under `src/sidepulse/provider_sources/`.
- Reuse existing bounded parsers in `usage_stats.py`, `claude_quota.py`, and provider adapters.
- Test provider fixtures under `tests/provider_fixtures/`.

- [ ] Codex: local auth, app-server/rollout quota lanes, credits, reset times, tokens, models.
- [ ] Claude: explicit Keychain consent, OAuth usage, dynamic scoped limits including Fable, local tokens/cache savings.
- [ ] Cursor: read-only local auth plus explicit browser fallback.
- [ ] Devin: manual encrypted token or consented Chromium localStorage.
- [ ] Grok: `~/.grok/auth.json`, billing endpoint/CLI facts, local activity.
- [ ] Antigravity: local loopback quota server and model pools.
- [ ] OpenAI API: optional organization/project usage with an encrypted admin key.

### Task 9: Usage Center and reset quality-of-life

**Files:**
- Create: `src/sidepulse/usage_center_projection.py`
- Modify the existing capacity/settings surface without adding a new sidebar destination.
- Test: `tests/test_usage_center_projection.py`, reset tests.

- [ ] Show exact source state instead of `no reading`.
- [ ] Show every quota lane, remaining percentage, reset time, model count, token breakdown, credits, estimated price, cache savings, and source freshness.
- [ ] Add finite deduplicated weekly-reset celebration and upward-only threshold notifications.
- [ ] Preserve unknown provider lanes in detail view without letting them trigger hardware alerts.

---

## Pull Request 4: Consolidated UI and Screen Bar redesign

### Task 10: Settings information architecture

**Files:**
- Create focused pane builders under `src/sidepulse/settings_panes/`.
- Modify: `src/sidepulse/settings_window.py` only to delegate.
- Test: settings navigation and layout contracts.

- [ ] Consolidate to Overview, Agents & Providers, Usage, Devices & Screen Bar, Appearance & Motion, Notifications & Focus, Advanced & Diagnostics.
- [ ] Make Setup and Settings call the same installer/service.
- [ ] Render errors as expanding callouts below the relevant row.
- [ ] Keep Color Studio and animation editing discoverable under Appearance & Motion.

### Task 11: Screen Bar geometry and state language

**Files:**
- Create: `src/sidepulse/screen_bar_geometry.py`
- Modify: `src/sidepulse/virtual_device.py` and `src/sidepulse/screen_bar_pipeline.py` through focused delegates.
- Test: geometry, cadence, Alcove following, reduced motion, state language.

- [ ] Implement a 4–6 point rounded luminous band with bounded glow.
- [ ] Remove the full-width one-pixel line.
- [ ] Use a floating pill on notchless/external displays.
- [ ] Follow Alcove center/width with a two-point gap when permission and measurement are valid.
- [ ] Distinguish connected-silent, idle, working, needs-input, completed, failed, and quota-warning by motion or shape.
- [ ] Ensure preview and production use the same geometry and rendering pipeline.

### Task 12: Final audit and merge sequence

- [ ] Run focused tests for each tranche before opening its PR.
- [ ] Merge PR 1, create PR 2 from updated `main`, and repeat through PR 4.
- [ ] Run the portable suite and source/package contracts on the final exact head.
- [ ] Leave Instruments/signing/notarization metrics for the owner’s Mac test pass as explicitly requested.
- [ ] Update docs so implemented, unsupported, and Mac-validation-required features are not conflated.
