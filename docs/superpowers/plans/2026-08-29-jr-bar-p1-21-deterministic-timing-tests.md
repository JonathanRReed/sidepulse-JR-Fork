# JR Bar P1.21 deterministic timing tests plan

Status: closed on 2026-08-29.

## Objective

Make timing-sensitive render, worker, cooldown, notification, and IPC tests
coordinate through injected clocks, explicit completion signals, and bounded
joins instead of correctness-critical wall-clock sleeps.

## Source-backed inventory

- `ScreenBarSampler`, `LatestWinsWorker`, `HookIngressService`, and
  `SerialPersistenceWriter` already expose bounded idle or close seams. Their
  tests should use those seams directly.
- `ProviderSyncService` has no idle seam and can strand the newest usage state
  when a request arrives after the current worker has captured its input.
- `AlcoveObservationWorker`, notification delivery, usage hooks, usage graph
  refreshes, and several socket tests currently force callers to poll side
  effects.
- Screen Bar cadence tests and temporal-safety fuzz tests already use fixed
  random seeds. Production notification tokens, pairing keys, cache secrets,
  and temporary-file names are security or collision boundaries and must stay
  nondeterministic.

## Implementation

1. Add regression tests for the provider-sync latest-wins race, then make the
   service rerun for the newest state and expose a bounded idle/close contract.
2. Add only the smallest missing lifecycle seams needed to observe worker and
   delivery completion without sleeping.
3. Replace test polling loops with explicit callback events, existing
   `wait_idle()` methods, server shutdown drains, or bounded thread joins.
4. Inject clocks into remaining focused cache and cooldown owners where tests
   otherwise patch or wait on real time. Preserve production deadline values.
5. Add a deterministic-timing ratchet that rejects unapproved `time.sleep()`
   calls and unbounded thread joins in the test suite. Any true timeout
   integration case must be narrow, documented, and explicitly allowlisted.
6. Keep fixed seeds for effect and cadence simulation, and add a guardrail that
   prevents those tests from drifting back to process-global randomness.

## Verification

- Observe focused regression tests fail before the sync and lifecycle fixes.
- Run Ruff on every changed Python file.
- Run focused timing, worker, render, notification, IPC, and usage tests.
- Run `make fast`.
- Run the complete test suite and static gates on a stable source fingerprint.
- Obtain an independent findings-first review before closure.

## Evidence boundary

Passing tests prove deterministic source behavior, bounded worker shutdown,
and the absence of unapproved sleep-based correctness assertions. They do not
prove installed-app animation quality, real hardware timing, signing,
notarization, packaging, publication, or release readiness.

## Closure receipt

- Provider sync now reruns for the newest state, coalesces callbacks onto the
  final result, and exposes bounded idle and close waits.
- Notification delivery remains active until the real UserNotifications
  completion callback, duplicate callbacks are harmless, and close shares one
  deadline across worker exit and callback completion.
- Usage graph refreshes use one immutable settings snapshot for admission,
  payload construction, and the landed cache key. Cache and calendar
  boundaries are driven by injected clocks in tests.
- Timing-sensitive tests use explicit events, existing idle seams, server
  drains, bounded joins, or injected clocks. The ratchet rejects wall-clock
  sleeps, embedded JavaScript timeouts, unbounded join forms, and unseeded test
  random generators.
- The focused deterministic-timing slice passed 455 tests before the final
  notification deadline regression. The affected settings-composition order
  sequence then passed 902 tests plus 7 subtests after isolating the mutating
  provider status-bar import.
- `make fast` passed with 84 contract tests, 139 fixture and schema tests, and
  291 focused tests. The fingerprint-stable complete suite passed 6,612 tests
  plus 7 subtests with the four known Python 3.12 multiprocessing fork
  warnings. Ruff, compileall, dependency policy, the 571-file secret scan,
  version validation, six architecture ratchets, and `git diff --check`
  passed. Final independent review returned no findings.
