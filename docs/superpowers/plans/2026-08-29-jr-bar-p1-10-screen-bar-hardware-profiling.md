# JR Bar P1.10: Screen Bar Hardware Profiling

Status: completed in source on 2026-08-29. The required physical Instruments
matrix remains externally unclaimed because `xctrace` is unavailable in the
selected developer toolchain.

## Objective

Make every Screen Bar performance claim reproducible and fail-closed. Capture
the app's content-free runtime metrics for the required scenario matrix, bind
each run to its Screen Bar, display, power, thermal, visibility, and Focus
state, then require separate raw Instruments provenance for wakeups, energy,
memory, and real-hardware conclusions.

## Current evidence and constraint

- `PresentationMetrics` already bounds callback and worker timings in memory.
- The 24-frame JavaScriptCore batch path exists, but does not expose call,
  success, cache-hit, or fallback counters.
- The cadence policy already knows visibility, display sleep, low-power mode,
  thermal state, and delivered frame rate.
- No runtime profile bundle or complete scenario matrix exists.
- `xcrun xctrace` is not installed in the active environment. This source
  tranche must not fabricate Instruments, wakeup, energy, memory, thermal, or
  physical-device evidence.

## Required scenarios

1. static
2. working
3. asking
4. multi-agent
5. DND or Focus
6. low power
7. hidden

## Implementation sequence

1. Add adversarial tests for missing scenarios, invalid and non-finite values,
   impossible counter relationships, false Focus certainty, secret-shaped
   fields, runtime-only evidence posing as Instruments evidence, and raw-trace
   substitution.
2. Extend the existing `PresentationMetrics` registry with bounded,
   label-free JavaScriptCore, batch, presentation, and suppression counters.
3. Add a pure profile-evidence schema that summarizes the existing metrics,
   validates runtime context, binds optional Instruments metrics to a raw
   trace digest, and assembles exactly one seven-scenario matrix.
4. Add an explicit opt-in runtime export on Screen Bar termination, controlled
   by an output path and scenario environment variable. The ordinary runtime
   must perform no profile I/O. The explicit profiler samples scenario-defining
   state throughout the capture and rejects late relabeling.
5. Add a CLI helper to validate runtime captures, bind externally collected
   Instruments evidence, and assemble the matrix.
6. Document the exact manual capture procedure and the unavailable local
   `xctrace` gate without claiming that the physical run happened.
7. Run focused tests, Ruff, Python compilation, the canonical source gate,
   secret scans, and diff validation.
8. Obtain one independent performance/correctness review and resolve every
   validated finding.

## Completion boundary

The source tranche closes when JR Bar can emit and validate content-free
runtime evidence and cannot assemble a complete matrix without a matching raw
Instruments trace for every scenario. A real-hardware profiling claim remains
external until all seven runs are captured and reviewed on the target Mac and
display with `xctrace` available.

## Closure evidence

- The existing bounded presentation registry now records JavaScriptCore
  single calls, 24-frame batch calls, batch successes, cached-frame hits,
  fallbacks, processed callbacks, suppressed callbacks, and presented frames.
- Runtime export is disabled by default. Explicit profiling launches one
  bounded state sampler, writes one owner-only profile during normal
  termination, and stops the frame driver and joins the sampler before taking
  the metrics snapshot.
- Scenario state is sampled every five seconds and on Screen Bar visibility or
  display-sleep changes. The capture clock arms only when the declared state
  first matches; later mismatches, fewer than 30 samples, or less than 300
  seconds reject the profile.
- Runtime, Instruments, raw-trace, and seven-scenario matrix documents are
  content-free, versioned, hashed, and fail closed on malformed, missing,
  duplicate, unknown, non-finite, negative, secret-shaped, symlinked,
  outside-root, substituted, or internally impossible evidence.
- One independent review found three issues: a symlink-root normalization
  bypass, a shutdown snapshot race, and end-state-only scenario validation.
  All three were resolved with adversarial coverage.
- Focused post-review tranche: 513 tests passed.
- Canonical gate: 6,304 tests passed with 7 subtests and 4 existing Python
  multiprocessing deprecation warnings. Dependency policy, tracked secret
  scan, Ruff, release-version validation, wheel and source-distribution
  builds, Twine, clean wheel install, SBOM generation, and the JR Bar wrapper
  passed.
- Python compilation, shell syntax, package health, an untracked-source secret
  scan, revision equality, and `git diff --check` passed. `HEAD` and
  `origin/main` remain `10d10fc6ea320642285352eac6b211774c2ba1a1`.
- `xcrun xctrace version` reported that `xctrace` is unavailable. No raw trace,
  physical energy result, or completed real-hardware matrix was claimed.
