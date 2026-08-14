# SidePulse completion contract, 2026-08-13

## Objective and completion boundary

- Terminal objective: implement and verify the approved attention, failure, completion, freshness, usage refresh, notification, performance, packaging, local-data security, and hardware-manager program.
- Current completion claim: source implementation is substantial but the terminal objective is not proven. The automated tree has one known capacity-composition failure, modern UserNotifications is unfinished, and installed/runtime/hardware acceptance has not run against the current candidate.
- Active observable result: every enabled usage source publishes only its own truthful result, disabled Claude remote quota is never invoked or marked healthy, local transcript coverage remains visible, capacity timers disappear when their exact reason disappears, and the complete source gate is green.

## Current state

- Product: SidePulse in this worktree on branch `codex/sidepulse-manager-completion`, baseline commit `12e92a088648480331f25d2f1cccaede9828a488`.
- Dirty tree: extensive uncommitted implementation from the active program. Preserve unrelated ranges and do not reset or stage them.
- Installed runtime: `/Users/jonathanreed/Applications/SidePulse.app/Contents/MacOS/SidePulse` is running, but it is not evidence for the current source tree.
- Current reproducible failure: `ProviderAwareUsageRefreshTests::test_usage_refresh_worker_publishes_provider_local_coverage_without_paths` expects a disabled Claude remote quota window. Diagnostics also show the controller registers that disabled source as enabled and records quota health from transcript-only work.
- Last source checkpoint: relay source gates and full Ruff were green; the full controller file had 789 passes and this one unrelated usage-publication failure.

## Priorities

1. Close Capacity Task 10 composition, source-boundary, resource-budget, and timer-lifecycle gaps, then restore a clean source gate.
2. Implement explicit modern notification authorization, delivery, diagnostics, and click routing without a launch prompt.
3. Build and verify a candidate, then run installed UI, performance, privacy, notification, and connected-hardware acceptance.

## Authority and exclusions

- Edit and test this worktree. Do not add dependencies without approval.
- Do not commit, push, deploy, install, replace the running app, change credentials, or trigger permissions in this tranche.
- No mounted-device writes or live provider reads are permitted from tests.

## Active tranche acceptance gates

- Strict RED before production change for disabled-source truth and visibility-only timer retirement.
- Provider-local or capability-local results cannot cross source keys.
- Disabled Claude remote quota performs no source call and cannot report healthy capacity.
- Local transcript coverage remains privacy-safe and independently visible.
- One inventory build per refresh generation, zero unchanged warm cache writes, bounded workers/records/lanes, and no countdown I/O.
- Menu close removes visibility-only countdown timers; termination removes every capacity timer.
- Focused Capacity Task 10 suite, relevant controller tests, full Ruff, compile, diff check, and full pytest pass.

## Remaining external gates

- Installed AppKit rendering, explicit notification permission, signed/notarized identity, live provider payloads, long-duration CPU/RSS, and connected physical hardware remain Task 10 acceptance work after source closure.
