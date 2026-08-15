# SidePulse Fork Rescue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a reproducible, locally verifiable SidePulse fork, fix confirmed runtime and lint defects, remove generated artifacts from source control, absorb relevant upstream reliability work, and reduce future regressions without spending GitHub Actions minutes.

**Architecture:** Stabilize before refactoring. Keep the fork's existing domain modules and behavior, repair the current controller and packaging boundaries, add local verification entry points, and defer large controller extraction until the baseline is green. Upstream changes are ported selectively rather than merged blindly because the fork has intentionally diverged by 160 commits.

**Tech Stack:** Python 3.10-3.13, PyObjC/AppKit on macOS, pytest, Ruff, setuptools, shell packaging scripts, GitHub Actions configuration retained for manual use only.

## Global Constraints

- The source of truth is `JonathanRReed/sidepulse-JR-Fork@a07895c34ad22809a2260da752c69d6bfb9036fa`.
- No paid GitHub Actions execution is required for verification.
- No behavior-changing refactor lands before lint and targeted regression tests are green locally.
- Generated installers, logs, receipts, caches, virtual environments, and rebuild trees are not tracked.
- The physical-device write path must remain isolated from tests.
- macOS-only integration tests remain explicit and fail clearly when their framework dependencies are missing.

---

### Task 1: Repair the known failing baseline

**Files:**
- Modify: `src/sidepulse/status_bar.py`
- Modify: `src/sidepulse/draw_guard.py`
- Modify: `src/sidepulse/settings.py`
- Modify: `src/sidepulse/usage_card.py`
- Modify: `src/sidepulse/virtual_device.py`
- Modify: affected test modules reported by Ruff
- Test: provider-pin regression tests

- [ ] Add a regression test proving `projection_for_device()` filters worker rows with the configured device provider pin and preserves all worker rows when unpinned.
- [ ] Run that test against the current source and confirm the undefined `pin` path fails.
- [ ] Resolve the provider pin inside `projection_for_device()` and rerun the regression test.
- [ ] Move the NSString drawing constant below the complete import block so imports remain module-top-level.
- [ ] Apply Ruff-safe import and export ordering changes and remove stale suppressions/unused imports.
- [ ] Run `ruff check src tests` until clean.

### Task 2: Make local verification authoritative

**Files:**
- Create: `scripts/verify.sh`
- Create: `scripts/bootstrap-dev.sh`
- Create: `Makefile`
- Modify: `.github/workflows/tests.yml`
- Modify: `pyproject.toml`

- [ ] Add one local command that creates/uses a virtual environment, installs the package and test tools, runs Ruff, then pytest without a pipe that can mask exit status.
- [ ] Split lint and test concerns in the workflow and change automatic triggers to manual dispatch while CI credits are unavailable.
- [ ] Add clean-install/package metadata checks modeled on upstream's post-fork reliability work.
- [ ] Document the exact macOS verification command and expected environment.

### Task 3: Port relevant upstream reliability fixes

**Files:**
- Modify: `pyproject.toml`
- Modify: status-bar terminal integration modules
- Add/port: clean-install and packaging contract tests

- [ ] Declare `pyobjc-framework-ScriptingBridge>=10` on macOS.
- [ ] Keep ScriptingBridge optional at runtime outside the terminal feature and surface the actual missing dependency in diagnostics.
- [ ] Add installed-package tests for console entry points, package resources, hook fail-open behavior, and version consistency.
- [ ] Review upstream custom-terminal and isolated-installer changes against the fork's existing navigation and packaging design; port only non-conflicting behavior.

### Task 4: Remove repository debris

**Files:**
- Modify: `.gitignore`
- Delete from index: `work/dist-live/`, timestamped rebuild logs, install receipts, release-root markers, and task-contract scratch files
- Create: `docs/REPOSITORY-HYGIENE.md`

- [ ] Remove reproducible build outputs and local receipts from the current tree.
- [ ] Add ignore rules for all local work/build/install output classes.
- [ ] Move distributable packages to GitHub Releases in the publication workflow rather than source control.
- [ ] Provide a separate, opt-in `git filter-repo` command for historical size cleanup after a backup tag.

### Task 5: Harden the release and packaging path

**Files:**
- Modify: `.github/workflows/publish.yml`
- Modify: `packaging/build_macos_pkg.sh`
- Modify: `packaging/verify_macos_app.py`
- Test: packaging/version tests

- [ ] Require tag and package versions to match.
- [ ] Smoke-test the built artifact from an isolated environment before publication.
- [ ] Preserve the existing post-signing identity and entitlement verification.
- [ ] Publish the `.pkg`, wheel, and source distribution as release artifacts.

### Task 6: Reduce controller risk without a rewrite

**Files:**
- Create: `src/sidepulse/device_projection.py`
- Modify: `src/sidepulse/status_bar.py`
- Test: `tests/test_device_projection.py`

- [ ] Extract pure per-device row filtering and projection reconstruction from the AppKit controller.
- [ ] Preserve exact existing semantics for actionable attention, provider pins, lifecycle priority, dominant provider, and click targets.
- [ ] Keep AppKit lifecycle and orchestration in `status_bar.py` during this rescue.
- [ ] Run targeted projection tests, then the complete suite.

### Task 7: Final verification and publication

- [ ] Run Ruff with no warnings.
- [ ] Run the complete pytest suite from a fresh install on macOS.
- [ ] Run package build and `twine check`.
- [ ] Build and verify the signed macOS bundle/package.
- [ ] Confirm the working tree contains no generated artifacts.
- [ ] Commit in reviewable stages and open a draft rescue PR against `main`.
