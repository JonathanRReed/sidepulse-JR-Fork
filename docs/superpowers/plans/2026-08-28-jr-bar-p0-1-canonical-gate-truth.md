# JR Bar P0.1 Canonical Gate Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the high-confidence secret scan and release-version validator truthfully pass the current JR Bar 0.5.0 source.

**Architecture:** Preserve the strict scanner and replace only the secret-shaped synthetic fixture. Validate changelog versions by an anchored Markdown level-two heading that permits descriptive text after exact whitespace separation, so `0.5.0` cannot match `0.5.0rc1` or `0.5.0.1`.

**Tech Stack:** Python 3, pytest, Ruff, repository release scripts

**Spec:** `docs/superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md`

## Global Constraints

- Human-facing product name is JR Bar; compatibility identifiers remain unchanged.
- Do not weaken or add an allowlist to the secret scanner.
- Do not add dependencies.
- Do not commit, push, publish, deploy, or mutate credentials.
- Use tests that execute behavior, not assertions against source text.

---

### Task 1: Accept exact titled changelog headings

**Files:**
- Modify: `tests/test_supply_chain_tools.py`
- Modify: `scripts/validate_release_version.py`

**Interfaces:**
- Consumes: Existing `validate_release_version.validate(tag: str | None = None) -> str`.
- Produces: `changelog_has_release(text: str, version: str) -> bool`, used by `validate()`.

- [x] **Step 1: Write the failing behavior tests**

Add controlled temporary pyproject, package, and changelog paths. Assert that `validate()` accepts `## 0.5.0: Coalescence` and rejects `## 0.5.0rc1: Preview` for project version `0.5.0`.

- [x] **Step 2: Run the tests and observe the expected failure**

Run: `.venv/bin/pytest tests/test_supply_chain_tools.py -q`

Expected: The titled-heading acceptance test fails with `CHANGELOG.md has no release section for 0.5.0`.

- [x] **Step 3: Implement exact anchored heading matching**

Add `changelog_has_release()` using `re.search()` with `re.MULTILINE`, `re.escape(version)`, a required whitespace boundary before optional title text, and an end-of-line anchor. Replace the literal newline membership check in `validate()`.

- [x] **Step 4: Run the focused tests**

Run: `.venv/bin/pytest tests/test_supply_chain_tools.py -q`

Expected: All tests pass.

### Task 2: Remove the secret-shaped synthetic fixture

**Files:**
- Modify: `tests/test_remote_peers.py`

**Interfaces:**
- Consumes: Existing `_run_tailscale_cli()` behavior and high-confidence secret patterns.
- Produces: A test that proves stderr is never read without placing a credential-shaped string in tracked source.

- [x] **Step 1: Reconfirm the scanner failure**

Run: `.venv/bin/python scripts/scan_secrets.py --root .`

Expected: Exit 1 with one `tailscale-key` finding at the synthetic fixture.

- [x] **Step 2: Replace only the synthetic value**

Use a non-secret-shaped marker such as `redacted-auth-key-from-stderr`. Keep the assertions that stderr was not read and the marker was absent from the raised error.

- [x] **Step 3: Run the remote-peer test and secret scanner**

Run: `.venv/bin/pytest tests/test_remote_peers.py::test_discovery_never_reads_tailscale_stderr -q`

Run: `.venv/bin/python scripts/scan_secrets.py --root .`

Expected: Both commands exit 0.

### Task 3: Close the tranche with direct receipts

**Files:**
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`

**Interfaces:**
- Consumes: Task 1 and Task 2 passing behavior.
- Produces: An updated completion contract with current receipts and P0.2 as the active tranche.

- [x] **Step 1: Run the complete focused gate set**

Run: `.venv/bin/pytest tests/test_supply_chain_tools.py tests/test_remote_peers.py -q`

Run: `.venv/bin/python scripts/scan_secrets.py --root .`

Run: `.venv/bin/python scripts/validate_release_version.py`

Run: `.venv/bin/ruff check scripts/validate_release_version.py tests/test_supply_chain_tools.py tests/test_remote_peers.py`

Expected: Every command exits 0, and the validator prints `0.5.0`.

- [x] **Step 2: Inspect the exact diff and worktree**

Run: `git diff --check`

Run: `git status --short`

Expected: Only the approved specification, completion contract, implementation plan, validator, and two test files are changed or untracked.

- [x] **Step 3: Record receipts and advance the active tranche**

Mark P0.1 complete in the contract using the exact command outputs. Set P0.2 as the next active bounded vertical tranche. Do not commit.

## Completion receipt

- Red test: titled 0.5.0 heading failed against the prior literal matcher.
- Mutation check: deliberately broad matching caused both `0.5.0rc1` and `0.5.0.1` rejection cases to fail; the exact matcher was restored.
- Focused tests: 126 passed.
- Secret scan: passed across 571 tracked files.
- Release version: printed `0.5.0`.
- Changed-file Ruff: passed.
- Independent review: initial low-severity coverage finding fixed; scoped re-review reported no findings.
