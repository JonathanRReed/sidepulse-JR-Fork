# P2.33 Adaptive Refresh Acceptance Plan

> **For Codex:** Use test-driven development and one bounded review. Do not
> add dependencies, package, install, publish, or weaken the installed
> performance-evidence gate.

**Goal:** Formalize the existing adaptive refresh behavior as one observable,
cross-layer contract covering freshness, backoff, menu-open admission, cadence,
and source-versus-installed evidence boundaries.

**Status:** Source acceptance verified on 2026-08-29. Installed performance
evidence remains an external gate.

## Existing foundation

- `refresh_policy.py` already selects missing and stale sources while excluding
  disabled, hidden, in-flight, and backed-off sources.
- `ProviderUsageService` already coalesces work and uses a 120-second recent
  menu rung, longer aging rungs, a 1,800-second idle rung, constrained mode,
  ambient visibility, and reset watch.
- `menuWillOpen_` already records a provider-service visit and routes capacity
  work through `maybe_refresh_usage_summary(reason="menu-open")`.
- The installed release gate already requires a 300-second Instruments-backed
  performance document with `menu_tracking_io_observed` set to false. Source
  tests must not fabricate that external evidence.

## Task 1: Typed cadence plan and receipt

- [x] Add an immutable adaptive cadence plan with an explicit bounded reason.
- [x] Keep `_interval_for` as the compatibility projection so existing callers
  and tests retain the same exact intervals.
- [x] Expose the current cadence plan from `ProviderUsageService` without I/O,
  user content, credentials, or AppKit types.
- [x] Prove constrained mode, recent visit, aging rungs, idle, ambient usage,
  degraded sources, and reset watch retain current precedence.

## Task 2: Explicit menu-open admission boundary

- [x] Move the provider-service visit plus menu-open refresh admission into one
  small helper used by the real `menuWillOpen_` path.
- [x] Return a bounded receipt stating whether the service was notified and
  whether the planner was invoked. Do not claim that installed I/O was measured.
- [x] Preserve the rest of the menu visit, activity acknowledgement, mailbox,
  and capacity-timer behavior.
- [x] Add a cross-layer test that invokes the real refresh planner with
  synthetic states and fails if filesystem, subprocess, browser, credential,
  or provider collection work runs on the caller thread.

## Task 3: Acceptance and evidence boundary

- [x] Add one deterministic source acceptance test covering missing, stale,
  fresh, in-flight, backed-off, low-power, idle, and recent-open behavior.
- [x] Add the new module and focused tests to `make fast`.
- [x] Keep `verify_performance_budget.py` unchanged as the authoritative
  installed 300-second trace gate.
- [x] Record that installed idle CPU, menu-open p95, and live menu-tracking I/O
  remain external evidence, not source-test claims.

## Batch gate

- [x] Run focused refresh policy, capacity refresh, provider runtime,
  menu-tracking, main-thread, controller, and release-evidence tests.
- [x] Obtain one independent findings-first review.
- [x] Run Ruff, `git diff --check`, and `make fast` once for the batch.
- [x] Run one stable-fingerprint complete suite if the source tree remains
  unchanged after review.

## Verification receipt

- The pre-review controller, policy, runtime, menu-tracking, and resource-budget
  slice passed 990 tests plus 7 subtests.
- Independent review found that a menu-open attention signal updated the
  recency timestamp but did not shorten an already cached 30-minute idle
  deadline. Menu attention and ambient visibility now replan the cached
  schedule under the service lock without collection or system I/O.
- Post-review runtime and acceptance coverage passed 49 tests. The follow-up
  review found no remaining defect in the repaired surface.
- Final `make fast` passed in 23.75 seconds with 97 contract, 150 fixture, and
  497 focused tests, plus import smoke, secret scan, bytecode, dependency,
  version, and diff-hygiene gates.
- The complete suite passed 7,017 tests plus 7 subtests in 154.44 seconds with
  four known Python 3.12 multiprocessing fork warnings.
- The 514-file `src/` plus `tests/` fingerprint remained
  `d9db33019c89fa112cea47639afa42c58d6cb9f4675224b166791d922fe2f7d7`
  before and after the complete suite.
- `verify_performance_budget.py` was not changed. Installed idle CPU,
  menu-open p95, and live menu-tracking I/O still require a current
  candidate-bound 300-second Instruments receipt.

## Completion boundary

P2.33 source acceptance is complete when the typed cadence plan and actual
menu-open admission helper are used by production source and pass the focused,
fast, review, and stable-suite gates. Installed low-idle-cost and no-live-I/O
claims remain blocked until a current attributable candidate produces the
existing 300-second Instruments evidence document.
