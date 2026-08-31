# JR Bar P1.15 Ordered Hook Ingress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Do not commit, push, or open a pull
> request because this task has no source-control publication authority.

**Goal:** Move expensive hook processing behind one bounded app-owned FIFO so
hook callers wait only for admission, accepted events retain order, overload is
visible, and shutdown drains accepted work or records what could not drain.

**Architecture:** A stdlib-only hook client reads stdin once and submits a
versioned, size-bounded envelope to a private same-UID Unix socket. One retained
`HookIngressService` assigns acceptance sequence numbers, processes accepted
requests serially through the existing normalization, minimization, dedupe,
private-write, compaction, and monitor-reconciliation path, and publishes
content-free receipts. App-owned work applies its refresh synchronously in the
monitor-owning process, so a successful drain includes the final reconciliation.
If no ingress listener is available, the client loads the existing synchronous
implementation, writes directly, and wakes the app through the external hint
socket; an explicit queue refusal is not retried out of order.

**Tech Stack:** Python 3.11+, standard library Unix sockets and threads,
existing private I/O and provider contracts, pytest, Ruff, shell and generated
JavaScript integration contracts.

**Spec:**
`docs/superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md`

## Global Constraints

- Keep `sidepulse`, `io.sidepulse.*`, existing support paths, Keychain services,
  and hardware names compatible.
- Do not add a production dependency.
- Do not persist raw prompts, tool input, tool output, credentials, or transcript
  content. Raw ingress bytes may exist only in bounded process and socket memory
  until the existing minimizer produces the canonical stored record.
- Accept only same-effective-UID peers on a `0600` socket in an owner-private
  state directory. Reuse the existing descriptor-relative socket guard.
- Preserve cross-process event-token deduplication and all provider routing,
  origin, Cursor stdout, and fail-open behavior.
- Accepted events are processed FIFO by acceptance sequence. Explicit queue
  refusal is recorded and is not synchronously retried ahead of older work.
- Stop admission before shutdown drain. Keep the refresh-hint server alive until
  accepted hook work drains, while app-owned refresh callbacks complete inside
  that drain. Persist content-free rejection receipts for queue
  full, closed, invalid, processing failure, and shutdown-timeout outcomes.
- Keep imports on the thin client stdlib-only. Loading the app, collector, LED,
  settings, or AppKit stack in the admission process is a regression.
- Source verification does not prove installed-app latency, signed behavior, or
  release readiness.

---

### Task 1: Versioned thin-client protocol

**Files:**
- Create: `src/sidepulse/state_paths.py`
- Create: `src/sidepulse/hook_ingress_protocol.py`
- Create: `src/sidepulse/hook_client.py`
- Modify: `src/sidepulse/providers.py`
- Create: `tests/test_hook_client.py`
- Modify: `tests/test_hook_import_cost.py`

**Interfaces:**
- Produces: `HookIngressRequest(provider: str, log_path: str, payload_text: str)`,
  `HookIngressDisposition`, `encode_hook_ingress_request()`,
  `decode_hook_ingress_request()`, `candidate_hook_ingress_socket_paths()`, and
  `submit_hook_ingress()`.
- Produces: `hook_client_main(provider: str, log_path: Path) -> int`.
- Consumes later: `HookIngressService` returns one explicit disposition token
  over the socket immediately after admission.
- Preserves: `providers.default_state_dir()` and `candidate_state_dirs()` remain
  the public compatibility API but delegate to the new stdlib-only
  `state_paths.py`, so the client and app still share one resolver.

- [x] **Step 1: Write protocol and client failure tests**

  Cover strict outer fields, duplicate-key rejection, invalid provider and path,
  UTF-8 failure, the exact maximum encoded size, candidate XDG then standard
  state paths, same socket timeout on connect/send/receive, accepted and explicit
  refusal responses, unavailable fallback, stdin read exactly once, Cursor's
  `{}` output, and no private payload in exceptions or reprs.

  ```python
  def test_explicit_full_refusal_does_not_fallback_out_of_order(monkeypatch):
      fallback = []
      monkeypatch.setattr(client, "submit_hook_ingress", lambda _request: HookIngressDisposition.REFUSED_FULL)
      monkeypatch.setattr(client, "process_hook_payload", lambda *_args: fallback.append(True))
      assert client.run_hook_client("claude", Path("/state/claude.jsonl"), "{}") == 0
      assert fallback == []
  ```

- [x] **Step 2: Run the new tests and verify they fail**

  Run:
  `.venv/bin/python -m pytest -q tests/test_hook_client.py tests/test_hook_import_cost.py`

  Expected: collection or import failure because the protocol and client do not
  exist.

- [x] **Step 3: Implement the minimal strict protocol and client**

  The client must return immediately for an accepted or explicit refused token;
  only `UNAVAILABLE` loads `sidepulse.hook.process_hook_payload` lazily.

  ```python
  disposition = submit_hook_ingress(request)
  if disposition is HookIngressDisposition.UNAVAILABLE:
      from .hook import process_hook_payload
      process_hook_payload(provider, log_path, payload_text)
  return 0
  ```

- [x] **Step 4: Run the client tests and import ratchet**

  Run:
  `.venv/bin/python -m pytest -q tests/test_hook_client.py tests/test_hook_import_cost.py`

  Expected: pass, with `sidepulse.hook`, provider adapters, status bar, settings,
  and LED modules absent from the fast-client import set.

### Task 2: Bounded ordered ingress service and durable rejection receipts

**Files:**
- Create: `src/sidepulse/hook_ingress.py`
- Create: `tests/test_hook_ingress.py`
- Modify: `src/sidepulse/ipc.py`
- Modify: `tests/test_ipc_ownership.py`

**Interfaces:**
- Consumes: strict `HookIngressRequest` and wire dispositions from Task 1.
- Produces: `HookIngressReceipt`, `HookIngressSnapshot`, and
  `HookIngressService.start()`, `.submit()`, `.wait_idle()`, `.close()`.
- Produces: one guarded stream socket at `hook-ingress.sock`, owned by the
  effective user with mode `0600`.

- [x] **Step 1: Write FIFO, saturation, failure, and shutdown tests**

  Use injected operations and clocks to prove exact order, one worker only,
  bounded accepted depth, stable sequence assignment, explicit full and closed
  dispositions, continuation after one hook failure, same-UID enforcement,
  oversized-frame rejection, guarded stale-socket replacement, and rejection
  records containing only sequence, provider, reason, and timestamps.

  ```python
  assert service.submit(request("first")) is HookIngressDisposition.ACCEPTED
  assert service.submit(request("second")) is HookIngressDisposition.ACCEPTED
  release_first.set()
  assert service.wait_idle(timeout_seconds=1.0)
  assert completed == ["first", "second"]
  ```

  Add a timeout test where close records every accepted sequence that did not
  drain before the deadline and returns `False` without claiming success.

- [x] **Step 2: Run tests and verify the missing service fails**

  Run:
  `.venv/bin/python -m pytest -q tests/test_hook_ingress.py tests/test_ipc_ownership.py`

  Expected: import failure for `HookIngressService`.

- [x] **Step 3: Implement one acceptor, bounded peers, and one FIFO worker**

  Reuse `_SocketPathGuard`, `_bind_socket_in_guard`,
  `_existing_socket_refuses_connections`, and `_same_uid_peer` from `ipc.py`.
  Admission appends under one condition lock and assigns the next sequence.
  The worker always pops from the left.

  ```python
  with self._condition:
      if not self._accepting:
          return HookIngressDisposition.REFUSED_CLOSED
      if self._accepted_count >= self._maximum_accepted:
          return HookIngressDisposition.REFUSED_FULL
      self._sequence += 1
      self._pending.append(_AcceptedHook(self._sequence, request))
      self._condition.notify_all()
  ```

- [x] **Step 4: Run focused service and IPC security tests**

  Run:
  `.venv/bin/python -m pytest -q tests/test_hook_ingress.py tests/test_ipc_ownership.py`

  Expected: pass without leaked socket, peer, acceptor, or worker threads.

### Task 3: Reuse the canonical hook processor and wire app lifecycle

**Files:**
- Modify: `src/sidepulse/hook.py`
- Modify: `src/sidepulse/hook_entry.py`
- Modify: `src/sidepulse/status_bar_legacy.py`
- Modify: `src/sidepulse/_status_bar_production.py`
- Modify: `tests/test_hook_write_dedupe.py`
- Modify: `tests/test_sidepulse.py`

**Interfaces:**
- Produces: `process_hook_payload(provider: str, log_path: Path,
  payload_text: str, *, refresh_hint_handler=None) -> HookProcessingOutcome`
  with no stdin or stdout ownership.
- Consumes: `HookIngressService` operation callback and lifecycle methods.

- [x] **Step 1: Write processor parity and controller lifecycle tests**

  Prove direct and queued paths produce byte-equivalent minimized records and
  refresh outcomes; app-owned refresh handlers finish before processing returns;
  duplicate tokens still write once; processing exceptions become failure
  receipts; Cursor output belongs only to the process entrypoint; startup starts
  the hint listener before ingress; termination stops ingress and drains it
  before stopping the hint listener.

- [x] **Step 2: Run the focused tests and verify they fail**

  Run:
  `.venv/bin/python -m pytest -q tests/test_hook_write_dedupe.py tests/test_sidepulse.py -k 'hook or event_server or termination'`

  Expected: failure because the payload processor and ingress lifecycle are not
  wired.

- [x] **Step 3: Extract processing without changing canonical semantics**

  `hook_log_main()` reads stdin and delegates to `process_hook_payload()`; the
  ingress worker calls that same function with its accepted payload. Keep the
  exception-swallowing and Cursor response only at the external entrypoint.

- [x] **Step 4: Start and stop ingress in the required order**

  Start the existing event-hint server first, then ingress. Give the app-owned
  ingress a synchronous monitor callback rather than sending back through its
  own socket. On termination, stop admission and drain ingress before persisting
  latest state or calling `stop_event_server()`, so a final accepted record has
  completed monitor reconciliation before the drain reports success.

- [x] **Step 5: Run processor and lifecycle tests**

  Run the command from Step 2. Expected: pass.

### Task 4: Migrate every registration and remove detached provider bridges

**Files:**
- Modify: `src/sidepulse/install.py`
- Modify: `src/sidepulse/providers.py`
- Modify: `src/sidepulse/cli.py`
- Modify: `tests/test_hook_registration_resilience.py`
- Modify: `tests/test_legacy_hook_entrypoints.py`
- Modify: `tests/test_sidepulse.py`
- Modify: `tests/test_sidepulse.py` for OpenCode and OpenClaw bridge coverage
- Modify: `tests/test_antigravity_provider.py`

**Interfaces:**
- Consumes: `sidepulse.hook_client` and the frozen `agent-monitor hook-client`
  entrypoint.
- Preserves: legacy hook command recognition and uninstall ownership checks.

- [x] **Step 1: Write migration and generated-source tests**

  Prove new installs use the client entrypoint; old `hook_entry.py`, module, and
  frozen `hook-log` commands remain detectable and removable; exact probe runs
  the new client; paths with spaces are quoted; Antigravity still emits only
  `{}`; OpenCode and OpenClaw generated bridges await a tracked child or use the
  socket directly, contain no `unref`, `detached: true`, or fire-and-forget
  process, remain bounded, and preserve event order.

- [x] **Step 2: Run registration tests and verify the old shapes fail**

  Run:
  `.venv/bin/python -m pytest -q tests/test_hook_registration_resilience.py tests/test_legacy_hook_entrypoints.py tests/test_antigravity_provider.py tests/test_sidepulse.py -k 'hook_command or hook_client or opencode or openclaw or antigravity'`

- [x] **Step 3: Migrate command generation and detectors**

  Add `hook-client` to the CLI, switch new arguments to
  `sidepulse.hook_client`, and broaden exact ownership recognizers without
  accepting unrelated shell commands.

- [x] **Step 4: Replace detached JavaScript forwarding**

  Generate tracked async forwarding that awaits the thin client admission and
  enforces a small local bound. Do not retain a child process after the provider
  callback returns.

- [x] **Step 5: Run all registration and generated-source tests**

  Run the command from Step 2. Expected: pass.

### Task 5: Benchmark, documentation, and architecture ratchets

**Files:**
- Create: `scripts/benchmark_hook_ingress.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `docs/FEATURE-MATRIX.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_architecture_ratchets.py`
- Modify: `tests/test_build_script_contract.py`

**Interfaces:**
- Produces: a local benchmark that reports sample count, median, p95, accepted,
  refused, failed, and fallback counts without storing payload text.

- [x] **Step 1: Add benchmark and architecture-contract tests**

  Require at least 50 samples, temporary private state, an explicit server-up
  and server-down mode, bounded output fields, and no prompts or raw JSON in the
  report. Ratchet one hook ingress worker and the absence of detached provider
  forwarders.

- [x] **Step 2: Implement and run the benchmark**

  Run:
  `.venv/bin/python scripts/benchmark_hook_ingress.py --samples 100`

  Record median and p95 as source-machine evidence only. Do not generalize it to
  the installed app or other Macs.

- [x] **Step 3: Document behavior and proof boundaries**

  Describe admission order, explicit refusal, raw-in-memory boundary, stored
  redaction, fallback, shutdown ordering, diagnostic receipt path, and how to
  reproduce the benchmark.

- [x] **Step 4: Run lint, compilation, focused tests, and diff checks**

  Run:
  `.venv/bin/ruff check src/sidepulse/hook_ingress_protocol.py src/sidepulse/hook_ingress.py src/sidepulse/hook_client.py src/sidepulse/hook.py src/sidepulse/ipc.py src/sidepulse/install.py src/sidepulse/providers.py src/sidepulse/cli.py tests/test_hook_client.py tests/test_hook_ingress.py`

  Run:
  `.venv/bin/python -m compileall -q src/sidepulse scripts/benchmark_hook_ingress.py`

  Run the focused suites from Tasks 1 through 4, then `git diff --check`.

### Task 6: Canonical verification and independent review

**Files:**
- Modify: `docs/superpowers/plans/2026-08-29-jr-bar-p1-15-ordered-hook-ingress.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`

**Interfaces:**
- Consumes: all prior task receipts.
- Produces: a source-complete P1.15 checkpoint or a concrete blocker.

- [x] **Step 1: Run the canonical repository gate**

  Run: `./scripts/verify.sh --no-bootstrap`

  Expected: dependency policy, secret scan, Ruff, compilation and version,
  complete tests, source and wheel builds, Twine, clean install, and SBOM all
  pass. The final exact candidate passed 6,414 tests plus 7 subtests with four
  known Python 3.13 fork deprecation warnings, then passed both distribution
  builds, Twine, clean installation, and SBOM generation. The first full run
  caught a 31-byte legacy-monolith ratchet regression; the processor was moved
  into `hook_ingress.py`, the ratchet passed, and the complete gate was rerun.

- [x] **Step 2: Obtain one independent read-only review**

  Review queue bounds, FIFO proof, socket ownership, raw-content lifetime,
  fallback and explicit-refusal distinction, dedupe, generated bridge tracking,
  shutdown drain, receipt durability, and thread cleanup. The first pass found
  three issues: shutdown snapshot ordering, ambiguous acknowledgement fallback,
  and false OpenClaw ownership detection. Follow-up review confirmed those fixes
  and found one remaining asynchronous hint race; app-owned ingress now invokes
  monitor reconciliation synchronously, with a real delayed-handler drain test.
  Final rereview of the typed `AppOwnedHookIngressProcessor` extraction reported
  no actionable findings and independently ran 20 passing focused tests.

- [x] **Step 3: Record exact receipts and advance only after closure**

  P1.15 closed in source with 167 focused tests across the thin client, import
  ratchet, ingress, IPC ownership, canonical hook processing, lifecycle,
  registration, and provider-bridge slices. The canonical gate passed 6,414
  tests plus 7 subtests. The reproducible 100-sample benchmark measured a live
  listener at 39.454 ms median and 41.812 ms p95, and synchronous fallback at
  117.559 ms median and 180.333 ms p95. Independent rereview reported no
  findings. These are source-machine and local-process receipts only. They do
  not prove frozen-client or installed-AppKit latency, signed shutdown behavior,
  another Mac, a physical device, notarization, or release readiness.

## Self-review

- Spec coverage: Task 1 moves the client to bounded admission; Task 2 owns FIFO,
  overload, receipts, and drain; Task 3 preserves canonical processing and
  lifecycle order; Task 4 removes detached bridges; Task 5 makes latency and
  architecture observable; Task 6 gates closure.
- Placeholder scan: no deferred implementation placeholder or unspecified error
  handling remains in this plan.
- Type consistency: protocol dispositions flow unchanged from server admission
  through client decisions; `process_hook_payload` is the single direct and
  queued execution seam; the service owns all queue and shutdown state.
