# P2.26 through P2.31 Architecture Batch Plan

> **For Codex:** Use test-driven development and bounded parallel workers.
> Workers own only the files named in their lane. The main agent owns shared
> integration, migrations, documentation, and the batch gate. Do not commit,
> push, package, install, publish, change permissions, or add a dependency.

**Goal:** Close the provider-facing architecture recommendations as one batch:
typed feature settings, explicit state classes, product capability declarations,
multiple provider instances, dated fixture ownership, and serialized settings
and refresh receipts.

**Verification cadence:** Each lane runs its red test, focused tests, and Ruff.
The main agent runs controller/UI integration once, obtains one independent
review, then runs `make fast` and one complete stable-fingerprint suite for the
whole batch.

**Status:** Source architecture tranche verified on 2026-08-29, with the
remaining P2.29 profile-consumer wiring recorded below as an exact continuation.

## Audit baseline

- The main settings facade already has lossless migration and stale-write
  refusal, but provider usage and provider sync settings can overwrite a file
  changed after load.
- Provider contracts already preserve exact adapter and source-instance
  identity. Product-level support for answering, costs, reset forecasts, and
  remote observation is not declared explicitly.
- Provider usage settings, snapshots, stores, sync projection, menus, consent,
  colors, retention, remote sharing, and session actions mostly collapse by
  provider id even though the canonical source layer is instance-aware.
- Provider parser and adapter tests use synthetic values, the fast gate scans
  tracked secrets, and T3 has a pinned compatibility manifest. There is no
  dated provider-fixture ownership manifest or cross-provider allowlist gate.
- Runtime workers already coalesce and drain. A refresh begun under an older
  settings revision can still persist and publish after a newer edit.

## Lane A: Typed feature settings and explicit change contract, P2.26

**Initial ownership:** New pure module and tests only. Shared runtime integration
stays with the main agent.

- [x] Define immutable collection, presentation, and sync feature projections.
- [x] Ensure collectors cannot read menu presentation or remote-sync settings.
- [x] Define a stable settings-change receipt with monotonically increasing
  revision and an exact bounded set of changed feature ids.
- [x] Preserve the versioned provider settings documents and current defaults.
- [x] Add architecture ratchets that keep feature projections AppKit-free and
  prevent broad settings from crossing the collection boundary.

## Lane B: Settings, runtime cache, permissions, and capabilities, P2.27

- [x] Keep durable settings, cached usage state, browser permission/consent,
  and detected provider capabilities in distinct types and files.
- [x] Give cache publication an explicit revision and diagnostic receipt.
- [x] Keep settings migration in settings modules, cache migration in stores,
  consent diagnostics in the consent store, and capability negotiation in the
  provider contract plane.
- [x] Prevent stale runtime work from publishing against a newer settings
  revision.
- [x] Add a boundary test proving no durable settings document serializes
  runtime cache, permission observations, or capability detection results.

## Lane C: Product capability declarations, P2.28

**Initial ownership:** `provider_contracts.py` and focused contract tests.

- [x] Declare lifecycle, questions, answering, usage, costs, reset forecasts,
  remote observation, transcript fallback, and invocation-scoped monitoring as
  explicit product capabilities.
- [x] Represent support as explicit yes/no declarations. Do not infer answering
  or mutation authority from lifecycle or question observation.
- [x] Bind supported declarations to exact low-level contract identifiers or a
  named local runtime surface.
- [x] Keep absent and unsupported capabilities visible and inert.
- [x] Preserve exact provider, adapter, instance, capability, and version
  identity for invocation.

## Lane D: Multiple instances of one provider, P2.29

**Initial ownership:** New instance identity/profile module and tests only.
Shared settings, store, sync, UI, and controller threading stays with the main
agent.

- [x] Define a bounded `ProviderInstanceKey` using provider id plus opaque
  source-instance id, without placing email, path, or secret material in it.
- [x] Define versioned instance profiles for label, color override, retention,
  remote-sharing choice, open-session action, consent reference, and credential
  account reference.
- [x] Migrate provider-only settings and snapshots to an explicit legacy/default
  instance without losing unknown fields.
- [x] Preserve two same-provider instances through settings, usage state/store,
  sync projection, menus, and Usage Center.
- [ ] Scope browser consent, credential lookup, open-session action, visual
  identity, retention, and remote sharing to the instance key.
  Browser consent, credential lookup, reconnect, refresh selection, settings,
  usage state/store/sync, menu, and Usage Center are exact-instance aware.
  `ProviderInstanceProfile` defines and validates color, retention,
  remote-sharing, and open-session choices, but those four choices do not yet
  drive the retained color, history, remote-peer, or session-action runtimes.
  This is a source integration continuation, not an external blocker.
- [x] Keep provider-wide fallback behavior for old documents and callers while
  refusing ambiguous instance selection for new mutations.

## Lane E: Provider fixture ownership and provenance, P2.30

**Initial ownership:** New manifest/validator/fixtures/tests plus the existing
integration compatibility manifest. Main agent owns fast-gate integration.

- [x] Add dated, synthetic, provider-owned fixture entries for every registered
  first-party provider.
- [x] Require exact provider ownership, bounded fixture type, source reference,
  review date, fixture version, and SHA-256 fingerprint.
- [x] Add an explicit allowlist for any cross-provider identifier in a fixture;
  default to no cross-provider identifiers.
- [x] Reject paths, emails, tokens, credentials, prompts, and transcript content
  from fixture metadata and payloads.
- [x] Add `reviewedOn` to the T3 compatibility entry and validate its date.
- [x] Wire the ownership validator into the existing fixture-validation lane of
  `make fast`.

## Lane F: Serialized writes and refresh receipts, P2.31

**Initial ownership:** Provider usage settings, provider sync settings, provider
usage runtime, and their focused tests.

- [x] Add source revisions to both loaded provider settings types.
- [x] Refuse a save when the durable document changed or appeared after load.
- [x] Preserve unknown fields, atomic private writes, and future-schema
  read-only behavior.
- [x] Attach settings revision to refresh work and refuse stale persistence or
  callbacks after a newer explicit settings change.
- [x] Emit a bounded refresh receipt for accepted, superseded, refused, and
  failed publication outcomes without recording secrets or user content.
- [x] Keep latest-wins coalescing and bounded close semantics.

## Shared integration and batch gate

- [x] Add failing integration tests for two same-provider instances and one
  settings update during an in-flight refresh.
- [x] Thread feature settings, instance keys, capability declarations, and
  receipts through the existing provider adapters without adding AppKit work to
  background threads.
- [x] Render the source Usage Center and menu with two synthetic same-provider
  instances and verify distinct labels and actions.
- [x] Run focused provider settings, runtime, store, sync, menu, window,
  consent, credential, contract, registry, and architecture tests.
- [x] Obtain one independent findings-first review of the combined batch.
- [x] Run Ruff, `git diff --check`, and `make fast` once for the batch.
- [x] Run one complete suite against a stable before/after `src/` and `tests/`
  fingerprint.
- [x] Update architecture, local verification, and completion receipts with
  source-only and source-AppKit limitations.

## Rollback and compatibility boundaries

- Old provider-only records map to an explicit legacy/default instance.
- New fields preserve unknown document keys and do not rewrite future schemas.
- No secret value, email address, filesystem path, prompt, or transcript enters
  an instance id, fixture, receipt, or diagnostic.
- Unsupported product capabilities remain false and cannot grant mutation
  authority.
- A stale command is refused or superseded. It never silently overwrites a
  newer durable document or publishes an older runtime snapshot.
- Removing the new adapters must leave existing provider-wide behavior usable;
  no destructive migration runs during this batch.

## Verification receipt

- The independent combined review found four integration defects. Exact
  Settings checkbox identity, durable-versus-presentation snapshots,
  non-default Claude reconnect routing, and browser-consent stale writes were
  repaired before the batch gate.
- The source-AppKit Usage Center rendered `Claude · personal` and
  `Claude · work` as distinct cards with 72% and 24% meters and separate
  instance-bearing Reconnect actions. A flipped scroll document repaired the
  observed bottom-aligned first render.
- The status-bar facade hit its 40,000-byte ratchet at 43,441 bytes. Exact
  action routing moved to `provider_usage_controller_actions.py`; the facade
  closed at 39,042 bytes and the independent extraction review found no issue.
- Final `make fast`: 97 contract tests, 150 fixture tests, and 479 focused
  tests passed in 27.91 seconds. Ruff, import smoke, secret scan, bytecode,
  dependency policy, version contract, and diff hygiene also passed.
- Final complete suite: 6,999 tests passed, four known Python 3.12
  multiprocessing fork warnings, and seven subtests passed in 176.18 seconds.
  The 518-file before/after `src/` plus `tests/` fingerprint matched at
  `8d9265c1ab88b4dd9fdd0ed36c04ba1bab800c617de0372fe198760dd3ea73d1`.
- These receipts prove source and isolated source-AppKit behavior only. They do
  not prove the installed bundle, live browser/account switching, physical
  LEDs, Screen Bar hardware, signing, notarization, packaging, publication, or
  release readiness.

## Completion criteria

- Every recommendation from P2.26 through P2.31 has a direct source receipt or
  an exact recorded blocker.
- Two instances of one provider remain distinct across the exercised product
  path.
- Existing single-instance users retain their settings and behavior.
- The combined batch passes focused, fast, review, source-AppKit, complete-suite,
  and stable-fingerprint gates once.
