# JR Bar P0.2 XDG Event Socket Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver hook events when the hook process and LaunchAgent resolve different XDG state directories.

**Architecture:** Keep one server socket and one explicit-path behavior. When a sender has no explicit socket path, derive a bounded, deduplicated candidate list from the caller’s XDG state directory and the user’s standard state directory, then try each securely using the existing socket identity and same-UID checks.

**Tech Stack:** Python 3, Unix domain sockets, pytest, Ruff

**Spec:** `docs/superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md`

## Global Constraints

- Do not weaken socket ownership, symlink, same-UID, timeout, payload-size, or acknowledgement checks.
- An explicitly supplied socket path must remain the only attempted path.
- Candidate search is bounded to current XDG state and the standard home state directory.
- Preserve the hook’s fail-open process exit behavior while restoring the happy path.
- Do not add dependencies, commit, push, install a LaunchAgent, or mutate live provider hooks.

---

### Task 1: Prove the split with real Unix sockets

**Files:**
- Create: `tests/test_ipc_state_dirs.py`

**Interfaces:**
- Consumes: `HookEventServer`, `send_refresh_hint()`, `send_hook_event()`, `default_state_dir()`.
- Produces: End-to-end tests proving both current hint clients and legacy raw-event clients reach a server in the standard state directory when the sender has a different `XDG_STATE_HOME`.

- [x] **Step 1: Write the failing hint test**

Start a real `HookEventServer` at `default_state_dir(explicit_home) / "events.sock"`. Set the process `HOME` to that home and `XDG_STATE_HOME` to another directory. Call `send_refresh_hint()` without an explicit socket and assert delivery plus callback receipt.

- [x] **Step 2: Write the failing legacy-event test**

Use the same environment and a real server with `on_legacy_hook`. Call `send_hook_event()` without an explicit path and assert callback receipt. Preserve the current false return for pre-hint payloads because the server deliberately rejects their data authority after using them only as a wake-up signal.

- [x] **Step 3: Run both tests and observe the split**

Run: `.venv/bin/pytest tests/test_ipc_state_dirs.py -q`

Expected: The refresh-hint send returns false, and neither callback fires because the current client tries only the XDG socket while the server owns the standard-home socket.

### Task 2: Add bounded candidate state directories

**Files:**
- Modify: `src/sidepulse/providers.py`
- Modify: `src/sidepulse/ipc.py`
- Test: `tests/test_ipc_state_dirs.py`

**Interfaces:**
- Produces: `candidate_state_dirs(home: Path | None = None) -> tuple[Path, ...]` and `candidate_event_socket_paths() -> tuple[Path, ...]`.
- Invariant: Explicit `home` returns only that home’s standard state directory. Implicit resolution returns current XDG state first, then the standard home state, with duplicates removed in order.

- [x] **Step 1: Add candidate-order and deduplication assertions**

Assert XDG-first ordering, standard-home fallback, deduplication when XDG already equals the standard parent, and explicit-home isolation.

- [x] **Step 2: Implement the candidate helpers**

Build candidates only from `default_state_dir()` and `default_state_dir(Path.home())`. Deduplicate exact expanded paths while preserving order.

- [x] **Step 3: Run candidate and end-to-end tests**

Run: `.venv/bin/pytest tests/test_ipc_state_dirs.py -q`

Expected: Candidate tests pass; send tests still fail until Task 3.

### Task 3: Probe candidates without weakening IPC

**Files:**
- Modify: `src/sidepulse/ipc.py`
- Test: `tests/test_ipc_state_dirs.py`

**Interfaces:**
- Consumes: `candidate_event_socket_paths()`.
- Produces: Default-path candidate probing in `send_refresh_hint()`, `send_hook_event()`, and `another_instance_alive()`.
- Invariant: Each candidate still passes `_open_existing_socket_path()`, identity revalidation, same-UID verification, payload bounds, timeout, and acknowledgement checks.

- [x] **Step 1: Add an explicit-path isolation test**

Start a listener on a fallback candidate but pass a different explicit socket path. Assert no fallback occurs and the send returns false.

- [x] **Step 2: Implement candidate iteration**

For refresh hints, apply the breaker per candidate so a failed XDG path cannot suppress a healthy fallback path. For raw events, attempt candidates in order and return on the first acknowledged delivery. Keep the single explicit path unchanged.

- [x] **Step 3: Run the focused IPC tests**

Run: `.venv/bin/pytest tests/test_ipc_state_dirs.py tests/test_ipc_ownership.py tests/test_canonical_runtime_invariants.py -q`

Expected: All tests pass.

- [x] **Step 4: Prove breaker and first-responder behavior**

Pre-trip only the XDG candidate’s breaker and assert a non-lifecycle hint reaches the standard-home server. With both candidates live, assert hints and legacy wake-ups stop after the XDG-first responder. With only the standard-home server live, assert the default single-instance probe returns true.

### Task 4: Close the tranche with direct receipts

**Files:**
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-p0-2-xdg-event-socket.md`

**Interfaces:**
- Consumes: Passing Task 1 through Task 3 behavior.
- Produces: P0.2 completion receipt and P0.3 as the active tranche.

- [x] **Step 1: Run focused tests and changed-file lint**

Run: `.venv/bin/pytest tests/test_ipc_state_dirs.py tests/test_ipc_ownership.py tests/test_canonical_runtime_invariants.py tests/test_sidepulse.py -q`

Run: `.venv/bin/ruff check src/sidepulse/providers.py src/sidepulse/ipc.py tests/test_ipc_state_dirs.py`

Expected: Both commands exit 0.

- [x] **Step 2: Run the real socket reproduction and canonical gates**

Run: `.venv/bin/pytest tests/test_ipc_state_dirs.py -q`

Run: `.venv/bin/python scripts/scan_secrets.py --root .`

Run: `.venv/bin/python scripts/validate_release_version.py`

Expected: Socket tests and both gates pass.

- [x] **Step 3: Review and record**

Inspect `git diff --check`, obtain an independent bounded review, resolve findings, update the completion contract and this checklist, and do not commit.

## Completion Receipt

- The original split was reproduced: a server in the standard home state directory was unreachable when the sender had another `XDG_STATE_HOME`.
- The bounded resolver now tries current XDG state first and standard home state second, deduplicates identical directories, and keeps explicit paths exact.
- Refresh hints apply the circuit breaker per candidate. Legacy wake-ups and liveness probes stop at the first valid same-user responder.
- `tests/test_ipc_state_dirs.py`: 10 passed.
- Focused IPC and runtime suites: 24 passed.
- Broad regression gate: 856 passed and 7 subtests passed.
- Changed-file Ruff, the 571-file secret scan, release version validation, and `git diff --check` passed.
- Independent re-review reproduced the 24-test focused gate and reported no findings.
