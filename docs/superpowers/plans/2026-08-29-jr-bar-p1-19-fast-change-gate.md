# JR Bar P1.19 Fast Change Gate Implementation Plan

**Goal:** Give ordinary source changes one deterministic, sub-minute command
for lint, real imports, lightweight contracts, tracked-file secret scanning,
literal fixture validation, and a focused semantic test slice, without running
the complete suite or any build, install, hardware, signing, notarization, or
Instruments work.

**Architecture:** Add a standard-library Python orchestrator with an explicit,
auditable step list and no shell execution. Each step runs sequentially and the
first nonzero exit code stops the gate unchanged. Keep the three pytest groups
separate so receipts identify contract, fixture, and focused-semantic failures.
Expose the gate through `make fast`, document it as the ordinary-change path,
and keep the existing complete and release gates separately named.

**Measured baseline:** The selected pytest groups currently cover 410 tests:
49 contract tests in 1.32 seconds wall time, 139 literal fixture and schema tests
in 1.37 seconds, and 222 focused semantic tests in 1.80 seconds. The actual
import smoke takes 0.63 seconds. The existing portable slice exceeded 30
seconds and the complete suite takes about four minutes, so neither is the
ordinary-change slice.

## Boundaries

- Use the interpreter executing the gate and the repository's pinned tools.
- Do not bootstrap, install, add a dependency, build, clean `dist/`, package,
  install, sign, notarize, access hardware, launch Instruments, or publish.
- Scan tracked files through the existing secret scanner. Do not duplicate or
  weaken its exclusions.
- Import the real package, hook client, settings, status-bar facade, production
  controller, and Why-light context. An AST-only contract is not an import.
- Fixture validation covers the literal provider-adapter corpus, native usage
  parser samples, integration compatibility manifest, and settings schema.
- Full suite, portable suite, clean-wheel installation, packaging, hardware,
  installed-app, signing, notarization, and Instruments remain separate gates.
- No commit, push, pull request, deployment, publication, or release mutation
  is authorized.

## Task 1: Pin the fast-gate contract with failing tests

**Files:**

- Create: `tests/test_fast_change_gate.py`

- [x] Prove the step inventory contains lint, imports, contracts, secret scan,
  fixture validation, focused tests, compilation, dependency/version policy,
  and diff hygiene in deterministic order.
- [x] Prove no step contains bootstrap, pip installation, build, `rm`, complete
  suite, hardware, install, signing, notarization, Instruments, or publication.
- [x] Prove execution stops on the first failure and returns that exact code.
- [x] Prove `--list` is read-only and exposes each exact command.

## Task 2: Implement the standard-library orchestrator

**Files:**

- Create: `scripts/verify_fast.py`

- [x] Build immutable step records with tuple commands and repository-relative
  test paths.
- [x] Run subprocesses without a shell, from the repository root, in order.
- [x] Add optional Ruff safe fixes without changing any other gate behavior.
- [x] Emit concise stage and final timing output and preserve failure status.

## Task 3: Expose and document the boundary

**Files:**

- Modify: `Makefile`
- Modify: `README.md`
- Modify: `docs/REPOSITORY-HYGIENE.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `docs/FEATURE-MATRIX.md`
- Modify: `CHANGELOG.md`

- [x] Add `make fast` and `make fast-fix` without automatic bootstrapping.
- [x] Replace the nonexistent `--targeted --no-build` documentation.
- [x] Document exact included and excluded evidence layers.

## Task 4: Protect the authoritative release candidate

**Files:**

- Modify: `scripts/verify_macos_release.sh`
- Modify: `tests/test_release_gate_contract.py`

- [x] Prove the source receipt invokes `verify.sh` with build and clean-install
  disabled after the candidate and evidence directory exist.
- [x] Prove the source receipt cannot execute the `verify.sh` branch that
  deletes and recreates `build/` and `dist/`.

## Task 5: Verify and close P1.19

- [x] Run the strengthened contract tests and verify the explicit production-controller import path.
- [x] Run `make fast` and record its test counts and wall time.
- [x] Run Ruff, compileall, targeted release-gate contracts, and
  `git diff --check`.
- [x] Run a bounded findings-first review with no unresolved correctness, security,
  failure-propagation, or release-boundary finding.
- [x] Keep complete-suite, installed-app, hardware, packaging, signing,
  notarization, Instruments, deployment, and release claims separate.
- [x] Advance the completion contract to P1.20 only after direct receipts pass.

Verification receipts on 2026-08-29:

- `.venv/bin/python -m pytest tests/test_fast_change_gate.py tests/test_release_gate_contract.py -q` passed 14 tests in 0.81 seconds.
- `make fast` passed in 6.89 seconds; an independent clean rerun passed in 8.87
  seconds. Each run included 49 contract tests, 139 fixture and schema tests,
  222 focused semantic tests, tracked-file secret scanning
  over 571 files, bytecode compilation, dependency policy, release-version
  validation, and `git diff --check`.
- The import smoke now explicitly names `sidepulse._status_bar_production` in
  addition to the status-bar facade, so the fast gate exercises that boundary
  directly instead of only by transitive import.
