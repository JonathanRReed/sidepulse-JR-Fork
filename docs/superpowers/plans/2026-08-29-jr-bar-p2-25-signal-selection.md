# P2.25 Signal Selection Extraction Plan

> **For Codex:** Implement this tranche with the test-driven-development and subagent-driven-development workflows. Keep the retained controller as the live-fact and error-reporting adapter. Do not commit, push, package, install, publish, change permissions, or add a dependency.

**Goal:** Move the per-device LED signal precedence decision out of the retained AppKit controller into one small, AppKit-free module without changing claim order, short-circuiting, asks-only behavior, or failure handling.

**Architecture:** `signal_selection.py` will own typed claim identities, the fixed precedence table, asks-only muting metadata, and the deterministic first-active-claim selector. `StatusBarController.active_led_display_kind_for_device` will keep reading clocks, settings, battery state, lifecycle state, and other live controller facts. It will expose those facts to the selector through one narrow claim adapter, preserve lazy evaluation, and keep the existing once-per-display-kind diagnostic side effect at the controller boundary.

**Tech Stack:** Python 3.10+, dataclasses and enums from the standard library, pytest, Ruff, AppKit only in the retained adapter.

**Status:** Completed and independently reviewed on 2026-08-29.

---

## Task 1: Pin the pure precedence contract with failing tests

**Files:**
- Create: `tests/test_signal_selection.py`
- Create: `src/sidepulse/signal_selection.py`

**Interfaces:**
- `SignalClaimKey`: one stable identity for each current claim, including separate identities for the pinned/preview battery claim and the ambient charging claim.
- `SignalClaimSpec`: frozen record with `key`, `display_kind`, and `muted_by_asks_only`.
- `SIGNAL_CLAIM_PRECEDENCE`: immutable tuple in the exact current order.
- `select_active_led_display_kind(*, evaluate: Callable[[SignalClaimKey], bool], signal_policy: str | None, default_display_kind: str) -> str`

- [x] Add a table-driven test that pins the exact 18-claim order:
  `test`, `escalation`, `weather`, `low_battery`, `failure`, `quota`, `reminders`, `completion`, `reset_celebration`, `connection`, `peek`, `all_clear`, `calendar`, `battery_selected_or_preview`, `timer`, `studio`, `quota_runway`, `charging_idle`.
- [x] Add pairwise tests proving every earlier active claim wins over every later active claim.
- [x] Add a laziness test proving evaluation stops immediately after the first active claim.
- [x] Add asks-only tests proving exactly the existing courtesy kinds are skipped while test, escalation, weather, low battery, failure, pinned display choices, and ambient charging retain their current behavior.
- [x] Add fallback and evaluator-exception tests. The pure selector must not swallow evaluator failures because diagnostic ownership remains in the retained adapter.
- [x] Run `./.venv/bin/python -m pytest -q tests/test_signal_selection.py` and record the expected red result before implementation.

## Task 2: Implement the pure selector and rewire the retained adapter

**Files:**
- Create: `src/sidepulse/signal_selection.py`
- Modify: `src/sidepulse/status_bar_legacy.py`
- Modify: `tests/test_signal_selection.py`
- Modify: `tests/test_sidepulse.py` only if an existing controller-level contract cannot express the preserved behavior.

- [x] Implement the typed immutable precedence table without importing AppKit, controller modules, filesystem/network code, or mutable settings objects.
- [x] Implement the deterministic selector so it owns ordering, asks-only muting, short-circuiting, and the default result.
- [x] Replace the controller's inline ordered lambda table with one unordered `SignalClaimKey` fact adapter. The adapter may read live state, but must not encode precedence.
- [x] Preserve the current claim predicates exactly, including hard-ask weather suppression, courtesy gates, completion/all-clear interval boundary, timebox precedence, quota-runway availability, lifecycle-gated charging, and the two distinct battery claims.
- [x] Preserve lazy evaluation. A later claim must not run after an earlier claim wins.
- [x] Preserve once-per-display-kind error reporting. A broken fact adapter is caught in the controller boundary, logged once for its output display kind, treated inactive, and selection continues.
- [x] Keep `active_led_display_kind_for_device` as the compatibility method. Do not add another retained controller method, widen the controller surface, or move I/O into the pure module.
- [x] Run the pure tests and the existing controller precedence contracts:
  `./.venv/bin/python -m pytest -q tests/test_signal_selection.py tests/test_sidepulse.py -k "display_kind or signal_test_claims or low_power_outranks or asks_only or weather_yields or charging_idle"`.

## Task 3: Ratchet, review, and verify the slice

**Files:**
- Modify: `tests/test_architecture_ratchets.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`
- Modify: this plan with the final receipt.

- [x] Add `signal_selection.py` to the pure-production-module ratchet.
- [x] Add a source contract proving the retained method delegates to the selector and no longer contains the inline precedence tuple or asks-only muting set.
- [x] Run Ruff over all changed Python files.
- [x] Run `./.venv/bin/python -m pytest -q tests/test_signal_selection.py tests/test_architecture_ratchets.py tests/test_status_bar_lifecycle_contract.py tests/test_application_composition.py tests/test_notification_arbitration.py`.
- [x] Run `make fast`.
- [x] Obtain an independent findings-first review. Fix every confirmed issue and rerun the focused gate.
- [x] Run the complete suite only after the source fingerprint is stable: `./.venv/bin/python -m pytest -q`.
- [x] Run `git diff --check` and update the architecture, verification, completion-contract, and tranche receipts with exact results and limitations.

## Completion receipt

- Required red state: `ModuleNotFoundError: sidepulse.signal_selection`.
- Pure selector: 176 tests passed after adding the muted-claim non-evaluation contract.
- Focused extraction and neighboring boundaries: 255 tests passed.
- Post-review selector, controller, and architecture slice: 185 tests passed.
- Hook-ingress byte-equivalence stress: 40 fresh pytest processes passed after external refresh delivery was isolated from that durable-record contract.
- Canonical fast gate: passed in 20.22 seconds with 91 contract, 139 fixture and schema, and 298 focused tests.
- Complete suite: 6,821 tests plus 7 subtests passed in 284.49 seconds with four known multiprocessing fork warnings.
- Stable source/test manifest: `51402bf88542a40fb673fd2a2e1cb6637924009ff6692558c40ceb127dad4eec` before and after the complete suite.
- Independent bounded review: no signal-selection findings.
- Limits: source-only proof. No installed app, physical LED, Screen Bar rendering, packaging, signing, notarization, publication, or release action was performed.

## Acceptance criteria

- The exact existing precedence and asks-only policy are directly pinned in an AppKit-free test file.
- Claim evaluation remains lazy and fail-open at the retained controller diagnostic boundary.
- `status_bar_legacy.py` no longer owns the ordered signal policy.
- No retained controller method is added and the monolith shrinks or remains below its existing ratchet ceiling.
- Focused tests, Ruff, the canonical fast gate, independent review, and the complete suite pass on a stable source fingerprint.
- Receipts remain source-only. They do not claim installed-app, physical-device, packaging, signing, notarization, deployment, or release proof.
