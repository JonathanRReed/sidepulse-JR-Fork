# SidePulse Provider Capacity Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show trustworthy provider, account, pool, and lane capacity for installed coding surfaces when an official source can prove the binding, while keeping cloud activity separate from local agent lifecycle.

**Architecture:** Extend the existing `SourceKey`, `QuotaLaneKey`, `CapacitySnapshot`, exact-source refresh coordinator, conservative forecast, history, and capacity views. Add immutable account-binding metadata and evidence classes around negotiated sources. Each provider adapter remains explicit and opt-in. OpenCode delegates capacity to its selected upstream provider and never creates an OpenCode quota pool.

**Tech Stack:** Python 3.11, existing capacity dataclasses and private stores, bounded runtime workers, official local or HTTPS provider APIs, AppKit settings projection, pytest, Ruff.

## Global Constraints

- Cloud or account capacity never creates local work, request, completion, notification, or hardware lifecycle truth.
- Every released lane must bind exact provider, auth mode, account discriminator, pool, source, semantic window, and observed time.
- Missing or ambiguous identity renders `Not Observable`, never a guessed merge.
- No cookies, browser profiles, private endpoints, response-body copies, raw account labels, raw URLs, or credentials in persistence, logs, diagnostics, exports, or UI.
- Network and admin sources are explicit opt-in and disabled by default.
- Preserve current Codex and Claude behavior during migration.
- Do not add production dependencies without asking.
- Do not commit, push, install, change credentials, request permissions, or deploy.

---

## Task 1: Add exact account-capacity binding types

**Files:**

- Modify: `src/sidepulse/capacity_types.py`
- Modify: `src/sidepulse/capacity_authority.py`
- Modify: `src/sidepulse/capacity_refresh.py`
- Test: `tests/test_capacity_types.py`
- Test: `tests/test_capacity_authority.py`
- Test: `tests/test_capacity_refresh.py`

- [ ] Add strict REDs for `CapacityEvidenceClass` and frozen `CapacityAccountBinding` with exact `SourceKey`, provider id, auth mode, opaque account discriminator, pool, evidence class, and bounded observed time.
- [ ] Reject empty, credential-shaped, path-shaped, control-character, oversized, nonfinite, and mismatched components.
- [ ] Require exact source, lane, execution-context, account, pool, and feature binding before release authority can expose a lane.
- [ ] Preserve independent exact-source refresh deadlines and invalidation.
- [ ] Prove one account cannot overwrite another account's last-known-good snapshot or history.
- [ ] Run capacity type, authority, refresh, reset, and canonical identity gates.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 2: Declare provider-specific capacity policies

**Files:**

- Modify: `src/sidepulse/providers.py`
- Modify: `src/sidepulse/provider_contracts.py`
- Create: `src/sidepulse/provider_capacity.py`
- Create: `tests/test_provider_capacity.py`
- Modify: `tests/test_provider_registry.py`

- [ ] Add a literal immutable policy table for OpenAI Codex consumer, OpenAI API organization, Anthropic consumer, Anthropic Team or Enterprise, Anthropic Console API, Google Gemini CLI or Code Assist, Google Antigravity, GitHub Copilot, Cursor, Devin or Windsurf, and OpenCode upstream delegation.
- [ ] Encode source class, auth mode, pool identity, semantic lanes, opt-in requirement, and unsupported or link-only state.
- [ ] Require exactly one negotiated source per declared source capability and reject implicit provider inheritance.
- [ ] Prove OpenCode selects a capacity policy only from its exact configured `provider/model` prefix and never publishes `provider_id="opencode"` capacity.
- [ ] Prove Codex consumer and OpenAI API organization remain separate pools, and Claude consumer and Console API remain separate pools.
- [ ] Run provider registry, provider contracts, capacity authority, and privacy tests.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 3: Migrate current Codex and Claude sources to exact bindings

**Files:**

- Modify: `src/sidepulse/capacity_sources.py`
- Modify: `src/sidepulse/usage_stats.py`
- Modify: `src/sidepulse/claude_quota.py`
- Modify: `src/sidepulse/usage_view.py`
- Test: `tests/test_capacity_sources.py`
- Test: `tests/test_usage_coverage.py`
- Test: `tests/test_usage_view.py`
- Test: `tests/test_claude_quota_boundary.py`

- [ ] Capture current Codex and Claude view output as literal compatibility fixtures.
- [ ] Add binding-aware source results without changing remaining-first capacity semantics or freshness truth.
- [ ] Bind Codex local transcript or rate-limit evidence to the detected Codex auth mode and refuse ambiguous consumer versus API organization identity.
- [ ] Bind Claude remote quota only to the exact enabled consumer plan source. Preserve local Claude usage when remote observation is disabled or fails.
- [ ] Keep local activity and cost estimates explicitly non-authoritative.
- [ ] Run the exact existing capacity source, usage, freshness, and controller compatibility gates.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 4: Add Google capacity sources

**Files:**

- Create: `src/sidepulse/google_capacity.py`
- Create: `tests/test_google_capacity.py`
- Modify: `src/sidepulse/providers.py`
- Modify: `src/sidepulse/status_bar.py` in an isolated capacity-source range

- [ ] Freeze official fixtures for each supported Gemini CLI, Code Assist, or Antigravity local or API capacity surface.
- [ ] Add strict missing-module and unsupported-edition REDs before production.
- [ ] Parse only allowlisted numeric quotas, reset times, edition, feature, and opaque account binding. Discard raw provider copy inside the adapter.
- [ ] Model exact shared pools only where official documentation says the edition and account share them. Otherwise keep separate lanes or render `Not Observable`.
- [ ] Use the existing long-lived bounded capacity worker and independent exact-source deadlines.
- [ ] Preserve last-known-good observations on failure, mark exact source health, and prevent stale generations from applying.
- [ ] Run provider, capacity, refresh, reset, history, privacy, and controller gates.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 5: Add optional account and admin API adapters

**Files:**

- Create: `src/sidepulse/account_capacity_adapters.py`
- Create: `tests/test_account_capacity_adapters.py`
- Modify: `src/sidepulse/settings.py`
- Modify: `tests/test_settings.py`

- [ ] Add disabled-by-default declarations for supported official OpenAI, Anthropic, Cursor, Devin or Windsurf, and GitHub account or admin APIs.
- [ ] Store only Keychain references or existing secure account handles, never tokens in SidePulse settings or history. If the current app has no reviewed secure handle path, leave the source disabled and render setup guidance.
- [ ] Bound request time, response bytes, rows, lanes, accounts, and retries. Sanitize all exception copy at the worker boundary.
- [ ] Keep UI-link-only providers free of background network calls.
- [ ] Fail closed on redirects to unapproved origins, invalid TLS, malformed JSON, duplicate keys, unexpected content type, oversized bodies, clock uncertainty, and account mismatch.
- [ ] Run settings migration, security, capacity source, refresh, and private-state gates.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 6: Generalize capacity UI by provider, account, and pool

**Files:**

- Modify: `src/sidepulse/capacity_view.py`
- Modify: `src/sidepulse/usage_view.py`
- Modify: `src/sidepulse/settings_window.py`
- Modify: `src/sidepulse/status_bar.py` in isolated capacity-view ranges
- Modify: `src/sidepulse/capacity_accessibility.py`
- Test: `tests/test_capacity_view.py`
- Test: `tests/test_capacity_accessibility.py`
- Test: focused rendered tests in `tests/test_sidepulse.py`

- [ ] Replace Codex and Claude hardcoding with deterministic negotiated capacity rows.
- [ ] Group cards by provider, then opaque account discriminator and pool, without rendering the discriminator.
- [ ] Show Available, Refreshing, Stale, Partial, Not Observable, Permission Required, and Setup Required states with product-owned copy.
- [ ] Add one explicit refresh per exact source and one product-owned official account-page link when appropriate.
- [ ] Preserve lazy panes, stable AppKit identities, focus, keyboard order, unsaved controls, VoiceOver labels, Reduce Motion, and 200 percent text scale.
- [ ] Prove capacity changes never mutate menu work rows, mailbox, completion delivery, notification routing, Screen Bar semantics, or physical LEDs.
- [ ] Run capacity, accessibility, Settings, status controller, presentation, and canonical invariant gates.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 7: Independent review and source acceptance

**Files:**

- Create: `.superpowers/sdd/2026-08-13-sidepulse-provider-capacity-plane/final-report.md`

- [ ] Run full capacity source, authority, refresh, reset, usage, history, forecast, view, Settings, privacy, and canonical invariant suites.
- [ ] Run full Ruff, Python compile, and diff checks.
- [ ] Mutation-check account collapse, pool collapse, OpenCode self-capacity, lifecycle leakage, stale result acceptance, cross-source invalidation, local estimate authority, raw error leakage, link-only network invocation, and settings downgrade.
- [ ] Request independent correctness/account-binding review and independent security/network/privacy review.
- [ ] Remediate validated findings through fresh RED/GREEN and re-review.
- [ ] Record source acceptance separately from live credentials, real provider accounts, network behavior, installed app, and hardware acceptance.
- [ ] Record source receipts. Do not commit because authority forbids it.
