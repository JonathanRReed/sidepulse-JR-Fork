# SidePulse Community Adaptations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adapt the strongest public SidePulse improvements into the canonical runtime, diagnostics, packaging, installer, and provider seams without importing unsafe authority, private data, or whole external branches.

**Architecture:** Each idea is independently reimplemented behind current canonical facts, private I/O, typed diagnostics, bounded workers, one presentation epoch, and current package trust boundaries. Public forks supply pattern evidence only. Existing source behavior is preserved unless a dedicated RED demonstrates a defect.

**Tech Stack:** Python 3.11, existing provider adapters and collector, private export primitives, AppKit bundle builder, installer utilities, pytest, Ruff, macOS source and installed acceptance tools.

## Global Constraints

- Do not merge or cherry-pick whole community branches.
- Do not add microphone, audio, ScreenCaptureKit, browser-cookie, raw-log-bundle, or default-on surveillance behavior.
- Never classify quota exhaustion as ordinary completion.
- Never persist prompt, message, transcript, command, path, account label, raw error, or tool payload copy.
- Preserve the one canonical resolver and one relay presentation epoch.
- Preserve user-owned configuration and use private atomic writes with bounded backups.
- Do not add dependencies, commit, push, install, request permissions, or deploy without separate authority.

---

## Task 1: Prove live relay continuity on Screen Bar and hardware

**Files:**

- Modify only if a new RED proves a source defect: `src/sidepulse/status_bar.py`, `src/sidepulse/virtual_device.py`, `src/sidepulse/led_status.py`, `src/sidepulse/presentation_policy.py`
- Test: `tests/test_relay_motion.py`
- Test: `tests/test_screen_bar_motion.py`
- Test: focused relay tests in `tests/test_sidepulse.py`
- Report: `.superpowers/sdd/2026-08-13-sidepulse-community-adaptations/task-1-report.md`

- [ ] Reproduce two consecutive refreshes in one semantic episode and prove no physical program rewrite or Screen Bar generation restart occurs for phase-only advancement.
- [ ] Prove structural changes in semantic, palette, device count, brightness, calibration, Reduce Motion, or mode invalidate continuity.
- [ ] Run source tests first. Change production only if a focused RED still reproduces the first-and-second LED stutter.
- [ ] Rebuild an isolated local candidate without installing it, then observe Screen Bar relay for at least three full cycles.
- [ ] If a writable SidePulse device is mounted, observe physical relay for at least three full cycles and compare phase order with Screen Bar. Otherwise record the physical gate as unavailable.
- [ ] Record source and live observations separately. Do not commit or install because authority forbids it.

## Task 2: Add Codex usage-limit terminal recovery

**Files:**

- Modify: `src/sidepulse/collector.py`
- Modify: `src/sidepulse/completions.py` only if typed terminal-cause projection needs it
- Test: `tests/test_completions.py`
- Test: focused collector tests in `tests/test_sidepulse.py`

- [ ] Add a strict transcript fixture where terminal usage-limit evidence exists only in a structured `error.message` field.
- [ ] Assert one typed failed or exhausted terminal outcome, no Ask request, no green completion, no private copy, and no resurrection from later transcript mtime.
- [ ] Capture the missing-fallback RED.
- [ ] Add bounded structured fallback after authoritative hook terminal truth and before text-free stale expiry.
- [ ] Recognize only explicit product-owned provider error codes or allowlisted usage-limit classifications. Free-form phrases remain inert.
- [ ] Run collector, completion, interruption, notification, freshness, usage, and canonical invariant suites.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 3: Complete Hermes and Cursor official payload shaping

**Files:**

- Modify: `src/sidepulse/providers.py`
- Modify: `src/sidepulse/provider_adapters.py`
- Modify: `src/sidepulse/hook.py`
- Test: `tests/test_provider_adapters.py`
- Test: focused provider tests in `tests/test_sidepulse.py`

- [ ] Add literal official fixtures for Hermes finalize or outcome events and Cursor `conversation_id` plus `workspace_roots` shapes.
- [ ] Normalize only opaque identity and typed lifecycle. Discard workspace roots and all raw copy before persistence.
- [ ] Preserve provider-specific terminal and failure semantics instead of widening generic name heuristics.
- [ ] Prove malformed, credential-shaped, path-shaped, oversized, and ambiguous identifiers fail closed.
- [ ] Run provider adapters, hook persistence, IPC, collector, navigation, and canonical invariants.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 4: Add typed privacy-safe doctor diagnostics

**Files:**

- Create: `src/sidepulse/doctor.py`
- Create: `tests/test_doctor.py`
- Modify: `src/sidepulse/cli.py`
- Reuse: `src/sidepulse/private_export.py`

- [ ] Add strict missing-module REDs for a frozen diagnostic manifest and bounded diagnostic result.
- [ ] Include only product-owned codes for package import root, signature state, LaunchAgent state, private-path modes, hook detector state, negotiated source health, worker registry bounds, timer registry bounds, mounted device health, and last sanitized failure class.
- [ ] Exclude raw status-bar logs, hook rows, process arguments, usernames, home paths, account labels, hostnames, prompts, commands, provider errors, and tokens.
- [ ] Export one bounded private JSON file through existing safe export primitives and stable public failure copy.
- [ ] Add a CLI `doctor` view with no network, permission prompt, installation, process mutation, or device write.
- [ ] Run doctor, export, private-I/O, package, launch, and security gates.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 5: Add conditional Apple Events and Ghostty acceptance

**Files:**

- Modify: `src/sidepulse/app_bundle.py`
- Modify: `src/sidepulse/status_bar_launch.py`
- Test: package and launch tests under `tests/`

- [ ] Add REDs proving Apple Events purpose text appears only when a reviewed navigation action actually requires Apple Events.
- [ ] Add a deterministic terminal launch matrix for Terminal, iTerm, Ghostty, and unsupported hosts, with product-owned fallback copy.
- [ ] Keep Apple Events absent when no runtime path uses it. Do not add entitlement or permission copy speculatively.
- [ ] Resolve Ghostty bundle and CLI targets through reviewed literal identifiers and trusted absolute paths. Never shell-search mutable user directories at action time.
- [ ] Run package, signing metadata, launch-path, navigation-policy, and security tests.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 6: Harden the installer as a transaction

**Files:**

- Modify: `src/sidepulse/install.py`
- Modify: `src/sidepulse/status_bar_launch.py`
- Modify: `src/sidepulse/private_io.py` only if a new reusable primitive is required
- Test: installer and private-I/O tests under `tests/`

- [ ] Add failure-injection REDs for every provider install stage: validate, create scratch, write, fsync, replace, directory fsync, trust refresh, post-verify, and rollback.
- [ ] Validate every target and parent before writing, preserve unrelated config and comments, cap backups, and roll back only SidePulse-owned changes.
- [ ] Refuse symlinks, hardlinks, parent swaps, non-owner files, permissive modes, growth, oversized config, duplicate keys, and unexpected schema.
- [ ] Keep provider installers independent so one failure cannot partially mutate another provider.
- [ ] Run all provider install, private-I/O, IPC, package, and security tests.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 7: Reinforce main-session projection and stale-worker retirement

**Files:**

- Modify only if a new RED proves a defect: `src/sidepulse/agent_browser.py`, `src/sidepulse/mailbox.py`, `src/sidepulse/collector.py`
- Test: `tests/test_agent_browser.py`
- Test: `tests/test_mailbox.py`
- Test: focused controller tests in `tests/test_sidepulse.py`

- [ ] Preserve 100 primary and 900 worker bounds, exact parent links, selected-family worker expansion, and canonical action-key inversion.
- [ ] Add adversarial restart, compaction, parent swap, terminal worker, missing parent, duplicate event, and late generation fixtures.
- [ ] Retire stale worker rows without changing primary completion, request, or watch state.
- [ ] Keep physical slot identity independent from dynamic provider band widths. Do not adopt the external sticky-slot algorithm unless a literal position oracle proves it stable.
- [ ] Run browser, mailbox, collector, navigation, settings, presentation, and canonical invariant gates.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 8: Independent review and source acceptance

**Files:**

- Create: `.superpowers/sdd/2026-08-13-sidepulse-community-adaptations/final-report.md`

- [ ] Run full collector, provider, completion, interruption, notification, presentation, hardware writer, doctor, export, package, installer, browser, mailbox, private-I/O, and canonical invariant suites.
- [ ] Run full Ruff, Python compile, and diff checks.
- [ ] Mutation-check relay restart, quota-as-completion, raw diagnostic copy, Apple Events overclaim, mutable terminal lookup, rollback loss, provider cross-mutation, stale-worker survival, and sticky-slot instability.
- [ ] Request independent correctness/product review and independent privacy/security/package review.
- [ ] Remediate validated findings through fresh RED/GREEN and re-review.
- [ ] Keep source, isolated package, installed app, live UI, Notification Center, provider, and physical hardware receipts separate.
- [ ] Record source receipts. Do not commit because authority forbids it.
