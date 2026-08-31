# JR Bar P1.13 Drainable Persistence Writers Plan

**Status:** Complete in source (2026-08-29)

**Scope:** Replace detached history and reset-state writes with one bounded,
serial, drainable persistence owner. Preserve every leaf store's existing
validation, deduplication, permissions, fsync, atomic replacement, retention,
and corruption behavior.

## Source observations

- Usage-percent history starts one untracked daemon thread per accepted batch.
  Shutdown neither joins those threads nor knows whether an append was lost.
- Provider reset-event state starts another untracked daemon thread per reset.
  Multiple resets can race atomic replacements, and termination does not drain
  the latest accepted snapshot.
- Operator history starts one daemon thread per event batch. A lock serializes
  the leaf mutation, but thread creation is unbounded and termination does not
  join accepted writes. Retention changes use a separate detached writer.
- Capacity history owns an in-memory batch and atomic leaf store, but its normal
  flush runs synchronously during AppKit reconciliation. Its existing forced
  shutdown flush is not called by controller termination.
- `LatestWinsWorker` is a useful pattern, but its `close()` intentionally
  cancels pending work. Persistence needs the opposite contract: stop accepting,
  drain accepted commands in order, and report a bounded-shutdown timeout.
- `atomic_private_write()` and `append_private_text()` already enforce the
  required private directory, no-follow, owner-only, fsync, and atomicity
  contracts. This tranche must call them through existing store functions, not
  replace them.

## Design

1. Add a small AppKit-free `SerialPersistenceWriter` with:
   - one lazy daemon worker and a bounded FIFO;
   - normalized bounded command keys;
   - explicit started, queued, replaced-pending, refused-full, and
     refused-closed dispositions;
   - optional latest-snapshot replacement that removes the older pending
     command and appends the new one at the correct submission order;
   - content-free completion receipts and bounded counters;
   - `wait_idle()` for deterministic verification;
   - `close(timeout_seconds=1.0)` that stops acceptance and drains every
     accepted running and pending command before returning success.
2. Create one writer in the retained controller and one receipt handler that
   logs only command key and stable outcome. Rejections are logged at the call
   site and never disappear silently.
3. Submit percent-history appends as ordered commands. Track accepted pending
   records separately and advance the committed dedupe watermark only after a
   successful append receipt, so refusal or disk failure remains retryable
   without suppressing a newer sample.
4. Submit provider reset-event state as a latest-snapshot command. Each payload
   contains the full bounded seen set, so replacing a pending older snapshot is
   lossless.
5. Submit operator-history event batches and retention transactions to the same
   writer. Keep the existing store merge lock and main-thread result selectors;
   remove only the per-operation daemon threads.
6. Move capacity-history flushes to the writer. Protect its mutable in-memory
   store with one narrow controller lock, coalesce pending flush commands because
   each execution reads the current full store, and enqueue a forced shutdown
   flush before writer close.
7. Close the persistence writer after runtime producers stop and before process
   teardown finishes. Reserve one bounded drain-tail slot for a final dirty
   capacity snapshot, and log a stable timeout outcome without exposing stored
   data.

## Test-first acceptance contract

1. Commands execute serially in FIFO order on a non-caller thread.
2. Replacing a pending snapshot moves the replacement to the tail and produces
   one stable replacement receipt for the displaced command.
3. Ordered append commands never coalesce.
4. Full and closed queues refuse explicitly without executing the command.
5. Operation failure produces a content-free failed receipt and does not stop
   later commands.
6. `close()` drains accepted work, refuses later submissions, and returns false
   after a bounded timeout when a running operation is still blocked.
7. Percent-history deduplication advances only for successful writes; accepted
   failures remain retryable and newer pending samples retain FIFO order.
8. Reset-event state and operator-history writes contain no direct thread start.
9. Capacity history no longer writes from its AppKit reconciliation call and a
   dirty tail is force-submitted before termination drain.
10. Leaf private-I/O, reset-store, percent-history, operator-history, and
    capacity-history tests remain green.
11. Focused tests, authoritative Ruff and compilation checks, the canonical
    gate, and one completed independent review pass with no unresolved
    correctness, privacy, data-loss, or threading finding.

## Non-goals and proof boundary

- No store schema, path, retention policy, packet format, migration, or private
  I/O primitive changes.
- No general task executor, database, production dependency, settings-save
  debounce, activity-ledger rewrite, mailbox rewrite, or device-write queue.
- Explicit settings, consent, Studio, and destructive clear actions retain their
  current synchronous transaction semantics.
- A successful bounded source drain is not proof against an operating-system
  kill after the deadline, power loss outside the leaf fsync contract, or a
  signed installed-app shutdown. Those remain separate evidence boundaries.

## Receipts

- Added `persistence_writer.py`, one lazy bounded FIFO with explicit admission,
  content-free receipts, safe pending-snapshot replacement, deterministic idle
  waits, one bounded final drain slot, and drain-on-close behavior.
- Routed usage-percent, provider reset-event, operator-history event and
  retention, and capacity-history writes through the shared owner. Existing
  leaf validation, private permissions, fsync, atomic replacement, retention,
  and corruption behavior remains authoritative.
- Extracted capacity-history lifecycle and generation fencing into
  `capacity_history_runtime.py`, keeping the AppKit controller below its
  architecture size ceiling.
- Added runtime regressions for accepted append failure and retry, a newer
  sample behind a blocked append, consent deletion behind a queued flush,
  normal termination drain, and a dirty shutdown tail with a saturated normal
  queue.
- Focused result: 167 passed, 842 deselected, and 2 subtests passed.
- Canonical result: `./scripts/verify.sh --no-bootstrap` passed with 6,344 tests,
  4 existing multiprocessing fork warnings, and 7 subtests in 231.91 seconds.
  Dependency policy, the 571-file tracked secret scan, Ruff, version validation,
  Python compilation, sdist and wheel builds, Twine, clean-wheel installation,
  SBOM generation, and diff validation all passed.
- Independent findings-first review identified accepted-write watermark loss,
  capacity-tail refusal under saturation, missing controller shutdown proof,
  and an intermediate newer-sample suppression regression. All were repaired;
  its final read-only verification reported no unresolved finding.
- Proof boundary: no force-kill, power-loss, signed installed-app shutdown,
  packaging, publication, or release result is claimed by this tranche.
