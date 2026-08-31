# JR Bar P1.11 Existing Frame Batching Plan

**Status:** Completed in source on 2026-08-29

**Scope:** Improve the single existing Screen Bar `step_batch` path. Do not add a renderer, production dependency, persistence format, or user-facing preference.

## Source observations

- `ScreenBarSampler` always asks JavaScriptCore for 24 frames, including finite effects with only a few deliverable samples left.
- The prefetched queue has no generation, program, or cadence identity. Timestamp mismatch is its only validity check.
- `reconcile()` replaces the capacity-one command mailbox immediately, but does not invalidate prefetched work until the sampler reaches its next motion loop.
- Cadence stalls discard the remaining queue without a dedicated counter, so performance evidence cannot separate stale prefetch from renderer failure.
- The initial two-sample publication remains single-step work. This tranche does not redesign interpolation or create a second batching system.

## Acceptance contract

1. Prefetched frames are reusable only when generation, parsed program identity, and cadence match the active command.
2. A newer command, hide request, cadence mismatch, or program change invalidates queued work immediately and records one bounded invalidation event.
3. Finite effects request no more frames than can be sampled on or before `next_visual_change_at`.
4. A shortened batch request is recorded separately from a renderer fallback.
5. Batch failure continues to fall back to one safe `step` call, and the latest-wins mailbox remains capacity one.
6. Direct tests cover command replacement, generation/program/cadence isolation, finite-horizon clamping, stall invalidation, fallback accounting, and the existing cache-hit path.
7. Focused tests, lint, type-relevant checks, the canonical verification gate, and one fresh independent review pass with no unresolved correctness or timing finding.

## Test-first sequence

1. Extend the test controller to retain requested batch sizes.
2. Add failing tests for immediate command invalidation, command-scoped cache isolation, finite-horizon clamping, and separate invalidation/truncation metrics.
3. Add a small immutable batch scope and one queue invalidation helper to `ScreenBarSampler`.
4. Thread the active scope and finite frame limit through `_sample_motion`, `_step`, and `_pixels_for`.
5. Add the new content-free counters to the P1.10 runtime profile schema and its validation tests.
6. Run focused tests, then repository-required lint and canonical gates.
7. Request exactly one fresh review, resolve every valid finding, rerun affected and canonical checks, and record direct receipts here and in the completion contract.

## Non-goals and proof boundary

- No claim that the 24-frame ceiling is optimal without a real Instruments trace.
- No hardware, energy, thermal, installed-app, signing, notarization, or release claim.
- No cycle memoization across commands unless identity and timing correctness can be proved. This tranche only reuses frames already produced by the existing batch call.

## Receipts

- The first red run produced 19 expected failures because command scope and the two new counters did not exist.
- The post-review focused slice passed: `98 passed` across Screen Bar pipeline, profile, frame-cadence, and real JavaScriptCore batch coverage.
- Repository Ruff and Python compilation gates passed for the authoritative `src`, `tests`, `packaging`, and `scripts` scopes.
- The canonical gate passed after both review fixes: `6314 passed, 4 warnings, 7 subtests passed in 233.93s`. The four warnings are the existing Python multiprocessing fork deprecations.
- Dependency policy, the 571-file tracked secret scan, version validation, wheel and source-distribution builds, Twine metadata, clean-wheel installation, SBOM generation, and `git diff --check` passed.
- One completed independent review found two medium issues. The finite horizon now uses the same integer millisecond cadence sent to JavaScriptCore, and runtime-profile validation now rejects impossible cache-hit, invalidation, and truncation relationships. Both findings are resolved.
- `HEAD` and `origin/main` remained `10d10fc6ea320642285352eac6b211774c2ba1a1`. No commit, push, package signing, installation, notarization, publication, hardware, or Instruments claim was made.
