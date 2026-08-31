# P2.25 Device Targeting Cleanup Plan

> **For Codex:** Use test-driven development and the subagent-driven workflow.
> Keep discovery, settings persistence, device writes, and keepalive I/O at their
> current boundaries. Do not commit, push, package, install, publish, change
> permissions, or add a dependency.

**Goal:** Remove the obsolete single-device selection path and make the current
multi-device targeting architecture explicit and regression-proof.

**Evidence correction:** The planned pure `keep/clear/fallback` selector would
only serve `StatusBarController.ensure_device_selection()`. Repo-wide source,
selector-style, packaging, and test searches found no caller for that method or
for `preferred_status_bar_device()` and `status_bar_device_sort_key()`. The live
runtime already publishes a bounded device inventory, projects every connected
candidate through `status_bar_devices()`, creates one agent and battery
controller per device id, and submits one bounded hardware command per physical
device. Extracting the unreachable selector would preserve dead policy instead
of improving the active path.

**Architecture:** Delete the unused single-target controller fields, chooser,
priority table, and scalar display-kind state. Keep `current_led_targets()` as a
thin view over the live per-device controller maps because keepalive and device
health use it. Keep discovery, path existence checks, remembered-device
persistence, controller reset, and hardware writes unchanged. Add syntax and
behavior ratchets that forbid reintroducing one preferred physical target and
prove multiple connected devices retain distinct targets and write slots.

**Status:** Complete on 2026-08-29.

---

## Task 1: Pin the obsolete boundary in red tests

**Files:**
- Modify: `tests/test_architecture_ratchets.py`
- Modify: `tests/test_sidepulse.py`

- [x] Add a source ratchet that rejects `ensure_device_selection`,
  `preferred_status_bar_device`, `status_bar_device_sort_key`,
  `STATUS_BAR_DEVICE_PRIORITY`, scalar `self.led_controller` and
  `self.battery_led_controller` assignments, and scalar
  `self.last_led_display_kind` state in the retained controller.
- [x] Add a focused controller test proving `current_led_targets()` returns
  distinct live targets from the per-device maps, ignores disappeared mount
  parents, and does not invent a preferred fallback.
- [x] Reuse the existing bounded hardware-sync contract that submits distinct
  commands for multiple physical devices.
- [x] Run the new tests before production changes and record the expected red
  state.

## Task 2: Delete the unreachable single-target path

**Files:**
- Modify: `src/sidepulse/status_bar_legacy.py`

- [x] Remove scalar agent and battery LED controller construction.
- [x] Remove scalar controller resets and reads from the live target view.
- [x] Remove `ensure_device_selection()` and the unused singular
  `current_led_target()` compatibility method.
- [x] Remove the preferred-device helper functions and priority constant.
- [x] Remove the unused scalar `last_led_display_kind` field.
- [x] Do not change discovery, inventory publication, remembered-device
  persistence, per-device controller construction, target assignment, worker
  keys, command priority, calibration suppression, or write delivery.

## Task 3: Verify and document

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`
- Modify: this plan with the final receipt.

- [x] Run focused device inventory, identity, settings, keepalive, runtime
  worker, hardware-write, architecture, lifecycle, and composition tests.
- [x] Run Ruff, `git diff --check`, and `make fast`.
- [x] Obtain an independent static findings-first review.
- [x] Run the complete suite against a stable before/after source/test
  fingerprint.
- [x] Record source-only limitations. Do not claim installed-app, hot-plug,
  physical-device, packaging, signing, notarization, publication, or release
  proof.

### Bounded implementation receipt

- Required red: the new architecture ratchet failed because
  `ensure_device_selection` and `current_led_target` still existed; the paired
  live-target behavior test passed.
- After the cleanup, both new tests passed.
- Focused inventory, identity, projection, settings, keepalive, runtime worker,
  hardware policy, lifecycle, composition, and architecture checks passed:
  `90 passed`.
- Repository-rule Ruff passed, `git diff --check` passed, and `make fast`
  passed with `94` contract tests, `139` fixture tests, and `298` focused tests.
- A separate focused run of `RememberConnectedDevicesRaceTests` exposed an
  existing test-order dependency: its setup patches
  `status_bar._DEVICE_IDENTITIES.snapshot` while the lazy facade cache is still
  `None`. Both direct patch sites now initialize the cache through
  `_device_identity_cache()` first, and the four race tests pass from a fresh
  process.
- The main focused verification passed 42 architecture, identity, inventory,
  projection, lifecycle, and composition tests plus 88 controller device,
  keepalive, and hardware tests with 762 deselections.
- The final `make fast` run passed in 14.11 seconds with 94 contract, 139
  fixture and schema, and 298 focused tests.
- The complete suite passed 6,874 tests plus 7 subtests in 286.40 seconds with
  four known Python multiprocessing fork warnings.
- The before and after manifests covered 492 source/test files and both hashed
  to `2683196ff1e0c08f743fae839bafe6aedbdb06207d558d75703ec0103761a2af`.
- An independent static findings-first review returned no defects.
- Evidence is source-only. No installed-app, hot-plug, physical-device,
  packaging, signing, notarization, publication, or release claim is made.

## Acceptance criteria

- No unreachable single-device chooser or priority table remains.
- The status controller owns no scalar physical-device controller state.
- Every live physical target still flows from one inventory candidate into one
  per-device controller and one opaque bounded worker key.
- Multiple connected devices remain peers. No target is silently preferred or
  discarded.
- Keepalive sees only mounted targets already owned by the live per-device maps,
  then falls back to connected inventory and reviewed mount names exactly as
  before.
- Focused tests, Ruff, the canonical fast gate, independent review, complete
  suite, and source/test fingerprint stability pass.
