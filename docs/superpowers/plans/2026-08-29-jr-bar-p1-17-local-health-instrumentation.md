# JR Bar P1.17 Local Health Instrumentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task by task. Keep the
> existing local performance and presentation metric owners. Do not add cloud
> telemetry, a production dependency, a background uploader, or a second
> diagnostics store.

**Goal:** Show nine bounded, content-free current-run health aggregates for
render duty cycle, dropped batches, delivered FPS, runtime queue depth, physical
write latency, source freshness, worker count, shutdown latency, and refresh
duration, entirely on the local Mac.

**Architecture:** Extend the existing `PresentationMetrics` snapshot with
bounded cumulative duration totals and counts so one pure `LocalHealthMonitor`
can calculate interval rates without sampling event content. The monitor also
consumes the existing `PerformanceRegistry` and `RuntimeWorkerRegistry`
snapshots plus numeric source ages. The production facade samples these
in-memory owners after admitted refreshes and on diagnostics inspection. The
existing Why panel displays a fixed-label health summary before its detailed
timings. No metric writes to disk or crosses a network boundary.

**Tech Stack:** Python 3.11+, standard library dataclasses and locks, existing
AppKit diagnostics window, pytest, Ruff, shell verification.

**Spec:**
`docs/superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md`

## Global Constraints

- Reuse `PerformanceRegistry`, `PresentationMetrics`, and
  `RuntimeWorkerRegistry`; do not create a competing telemetry pipeline.
- Store only fixed metric identities, numbers, booleans, and bounded enum
  values. Never retain prompt text, transcript content, labels, paths, URLs,
  provider payloads, device ids, credentials, or session ids.
- Keep every reservoir, counter, aggregate, and source-age input bounded.
- The display callback must not acquire a new lock, perform I/O, or format text.
- Diagnostics reads must use memory only. No settings, Keychain, provider,
  filesystem, or network read belongs in the projection path.
- Unobserved and reset intervals render as unavailable, not a fabricated zero.
- No production dependency, permission, helper, installed app, commit, push,
  package publication, deployment, or release mutation is authorized.
- Source and rendered development checks do not prove signed installed-app,
  Instruments, physical hardware, sleep and wake, or release behavior.

---

### Task 1: Preserve cumulative presentation work without hot-path cost

**Files:**
- Modify: `src/sidepulse/screen_bar_pipeline.py`
- Modify: `tests/test_screen_bar_pipeline.py`

- [x] **Step 1: Write failing bounded-total tests**

  Prove duration snapshots expose cumulative count and nanosecond total for
  every duration metric, counters and totals saturate instead of growing
  without bound, reset clears both reservoirs and totals, and existing
  reservoir behavior is unchanged.

- [x] **Step 2: Run the focused presentation tests and verify they fail**

  Run: `.venv/bin/python -m pytest -q tests/test_screen_bar_pipeline.py -k 'metric or presentation'`

- [x] **Step 3: Add lock-free bounded cumulative totals**

  Keep the current deque append and integer assignments under the GIL. Extend
  the immutable snapshot without changing existing duration or counter access.

- [x] **Step 4: Run the complete Screen Bar pipeline suite**

  Run: `.venv/bin/python -m pytest -q tests/test_screen_bar_pipeline.py tests/test_screen_bar_profile.py`

### Task 2: Build the pure local health monitor and formatter

**Files:**
- Create: `src/sidepulse/local_health.py`
- Create: `tests/test_local_health.py`
- Modify: `tests/test_performance_metrics.py`

- [x] **Step 1: Write failing aggregate and privacy tests**

  Cover two-sample delivered FPS and render-duty calculations, batch fallback
  plus invalidation counts, current and peak queue depth, registered and active
  worker counts, write, refresh, and shutdown timing selection, maximum visible
  source age, missing input as unavailable, reset and saturation rebaselining,
  bounded history, and fixed-copy formatting with no source content.

- [x] **Step 2: Run the pure tests and verify they fail**

  Run: `.venv/bin/python -m pytest -q tests/test_local_health.py tests/test_performance_metrics.py`

- [x] **Step 3: Implement one bounded in-memory monitor**

  The first presentation sample establishes a baseline. Later samples use
  monotonic deltas. Work totals include display callback, sample, Alcove,
  geometry, and paint work, but exclude sample age because age is not CPU work.
  Dropped batches mean fallbacks plus invalidated queued batches. Clamp invalid
  or impossible input to unavailable rather than coercing it to zero.

- [x] **Step 4: Run the pure health tests**

  Expected: all pass without AppKit, disk, settings, Keychain, or network use.

### Task 3: Wire production sampling and lifecycle timing

**Files:**
- Modify: `src/sidepulse/_status_bar_production.py`
- Create: `tests/test_local_health_wiring.py`
- Modify: `tests/test_sidepulse.py`

- [x] **Step 1: Write failing production-wiring tests**

  Prove a production refresh records its own duration before sampling health;
  worker snapshots come from the one runtime registry; hardware write latency
  comes from the existing `hardware_render` timing; source projection passes
  numeric ages only; diagnostics projection triggers no settings, Keychain,
  disk, or provider read; and termination records one shutdown duration even
  on an error.

- [x] **Step 2: Run the wiring tests and verify they fail**

  Run: `.venv/bin/python -m pytest -q tests/test_local_health_wiring.py tests/test_sidepulse.py -k 'local_health or performance or termination'`

- [x] **Step 3: Add bounded production facade wiring**

  Lazily retain one `LocalHealthMonitor`. Sample after admitted refreshes and
  immediately before local diagnostics rendering. Wrap the existing complete
  shutdown path in one timing observation without changing close ordering or
  swallowing errors.

- [x] **Step 4: Run lifecycle, worker, and production slices**

  Run: `.venv/bin/python -m pytest -q tests/test_local_health_wiring.py tests/test_runtime_scheduler.py tests/test_sidepulse.py -k 'local_health or performance or worker_registry or termination or hardware_write'`

### Task 4: Show fixed local aggregates and enforce content-free scope

**Files:**
- Modify: `src/sidepulse/_status_bar_production.py`
- Modify: `tests/test_local_health_wiring.py`
- Modify: `tests/test_operator_export.py`

- [x] **Step 1: Write failing diagnostics-copy tests**

  Require exactly nine primary rows under `Local Health (current run, never
  sent)`, stable units, `Unavailable` for missing evidence, and no provider,
  session, device, path, URL, credential, or event copy. Preserve the existing
  detailed timing section below the summary.

- [x] **Step 2: Implement the fixed in-memory projection**

  Use plain fixed labels and rounded aggregates. Do not add raw samples to the
  safe export in this tranche. A shutdown observation may appear only in tests
  or a future process-lifetime receipt because the live UI is gone after normal
  termination.

- [x] **Step 3: Run diagnostics, export, and architecture tests**

  Run: `.venv/bin/python -m pytest -q tests/test_local_health.py tests/test_local_health_wiring.py tests/test_operator_export.py tests/test_architecture_ratchets.py`

### Task 5: Rendered source verification and documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/FEATURE-MATRIX.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `CHANGELOG.md`

- [x] **Step 1: Inspect one isolated source-AppKit diagnostics pass**

  Keep the signed installed app running. Launch an isolated source process
  without application startup side effects, open Why this light, and verify the
  health heading and nine rows fit, unavailable states are explicit, the text
  is selectable and accessible, and existing decision content remains first.

- [x] **Step 2: Fix rendered defects once and confirm once**

  Use one bounded correction batch if needed. Do not install helpers, request
  permissions, touch physical hardware, or replace the installed app.

- [x] **Step 3: Document behavior and evidence boundaries**

  State that aggregates remain memory-only and current-run, explain each fixed
  row, and distinguish source rendering from signed installed and Instruments
  proof.

### Task 6: Static gate, independent review, and canonical verification

**Files:**
- Modify: `docs/superpowers/plans/2026-08-29-jr-bar-p1-17-local-health-instrumentation.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`

- [x] **Step 1: Run focused static and behavior gates**

  Run Ruff on every changed Python file, compile changed source, run Tasks 1
  through 4 suites, and run `git diff --check`.

- [x] **Step 2: Obtain one independent read-only review**

  Review privacy, cardinality, hot-path cost, counter reset and saturation,
  rate math, worker locking, shutdown ordering, unknown-state honesty, local UI
  copy, and proof language. Resolve every valid finding and rerun affected
  gates.

- [x] **Step 3: Run the canonical repository gate**

  Run: `./scripts/verify.sh --no-bootstrap`

- [x] **Step 4: Record receipts and advance only after closure**

  Mark P1.17 complete only with focused and canonical counts, rendered evidence
  or an exact blocker, final independent-review outcome, and explicit signed
  installed-app, Instruments, hardware, sleep and wake, artifact, installation,
  publication, and release boundaries. Then advance to P1.18.

## Self-review

- Coverage: all nine roadmap aggregates have one source, one memory-only
  projection, fixed copy, missing-state behavior, and direct tests.
- Architecture: the design extends existing metric owners and the production
  facade. It does not add persistence, transport, or a generic telemetry layer.
- Privacy: only fixed identities and numbers survive aggregation.
- Performance: no new lock, allocation shape, I/O, or text formatting enters
  the display callback beyond two bounded integer assignments already adjacent
  to the existing reservoir append.
- Placeholder scan: no fake producer, static sample, or unfinished UI row is
  accepted as implementation.

## Closure receipts

- Presentation metrics now retain bounded cumulative duration counts and totals
  without adding a lock to the display callback. Independent counters saturate
  independently, and a regression test prevents healthy summed counters from
  being mistaken for one saturated owner.
- `LocalHealthMonitor` projects exactly the nine roadmap aggregates from the
  existing presentation, performance, worker, and numeric freshness snapshots.
  Its state is bounded and memory-only, and its formatter has fixed labels with
  explicit `Unavailable` values.
- Production refresh records its timing before health sampling. Termination
  preserves battery, transcript, intake, ledger, webhook, and legacy close
  order, records one shutdown duration, and rethrows failures.
- Exact focused gates passed: 63 presentation and profile tests, 9 pure health
  and performance tests, 16 lifecycle and wiring tests with 854 deselected,
  and 91 diagnostics, export, and architecture tests. The broader P1.17 slice
  passed 1,024 tests plus 7 subtests.
- Ruff, bytecode compilation, and `git diff --check` passed for the changed
  source and tests.
- An isolated Python 3.13.15 AppKit process rendered the full explanation
  panel with the existing decision content first, all nine local-health rows,
  detailed timings, explicit unavailable states, selectable accessibility
  text, and no required scrolling. The temporary source processes were stopped
  and the signed installed app remained running as PID 90832.
- Independent read-only review returned no findings. Its canonical
  `./scripts/verify.sh --no-bootstrap` run passed 6,458 tests with 4 known
  Python 3.13 fork warnings and 7 subtests, then passed dependency policy,
  tracked secret scanning, lint, compilation, version validation,
  distributions, Twine, clean installation, and SBOM generation, ending with
  `JR Bar verification passed`.
- This tranche does not claim signed installed-app interaction, Instruments
  energy or thermal evidence, physical LED latency, sleep and wake behavior,
  signed artifact identity, installation, publication, deployment, or release.
