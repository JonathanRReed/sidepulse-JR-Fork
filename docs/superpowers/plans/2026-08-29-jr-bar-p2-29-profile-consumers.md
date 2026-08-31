# P2.29 Provider-Instance Consumer Plan

> **For Codex:** Use test-driven development, bounded parallel consumer lanes,
> one source-AppKit render for the Settings/UI result, one independent review,
> and batched gates. Do not add dependencies, package, install, publish, or
> weaken legacy default-instance behavior.

**Goal:** Make the already validated provider-instance profile drive exact
personal/work labels, colors, native usage-history retention, outbound remote
sharing, and session-opening behavior without collapsing to provider-only
authority.

**Status:** Source integration complete on 2026-08-29.

## Locked boundaries

- Persist the five non-secret profile choices beside each exact
  `ProviderPreference`; browser consent and credentials remain in their
  existing dedicated stores.
- A legacy provider-only row migrates to the explicit `default` instance and
  preserves existing provider/origin session-opening behavior.
- Missing or invalid remote-sharing policy fails closed to `never`.
  `status_only` may publish bounded quota/status snapshots, never token/cost
  observations.
- Instance retention governs native provider-account percent history. Global
  Operator History remains a separate opt-in aggregate store and is not
  silently reinterpreted in this tranche.
- Provider-default colors remain the fallback. An exact instance override wins
  only when the consumer possesses the same `(provider_id,
  source_instance_id)` key.
- Do not expose consent references or credential-account references to color,
  history, sync, or session-action projections.

## Task 1: Durable profile source and typed policy projection

- [x] Persist label, color override, retention days, remote-sharing choice, and
  open-session action for each exact provider preference.
- [x] Migrate schema-2 rows to safe defaults and retain the existing explicit
  `default` instance migration.
- [x] Expose exact `profile(...)` and `with_profile(...)` APIs that refuse an
  identity mismatch.
- [x] Project separate immutable visual, retention, sharing, and session-action
  policy views without credential or consent fields.

## Task 2: Visual identity and labels

- [x] Use the profile label for exact Usage Center and usage-menu instance
  presentation while retaining the provider label as context.
- [x] Use exact instance color overrides for Usage Center meters and Quota
  Runway; fall back to the current provider color.
- [x] Add Settings controls with bounded choices and accessible names, then
  verify both same-provider instances render separately in source AppKit.

## Task 3: Native provider-history retention

- [x] Add `source_instance_id` to percent-history dedupe keys and records;
  migrate missing values to `default` on read.
- [x] Skip new records for zero-day instances and prune retained records by the
  exact instance policy on the persistence worker.
- [x] Keep chart labels and series exact when two instances share a provider.

## Task 4: Outbound remote sharing

- [x] Inject only the typed sharing projection into local sync packet creation.
- [x] Exclude `never` instances before signing or transport.
- [x] Let `status_only` include bounded quota/status snapshots but exclude
  token, cost, and machine-usage observations.
- [x] Preserve inbound authentication, replay protection, peer identity, and
  offline cache behavior.

## Task 5: Session-opening action

- [x] Resolve a non-default status through its exact `WorkKey.source_key`
  instance before applying the profile action.
- [x] Preserve the legacy default-instance provider/origin preference and
  fallback planner.
- [x] Refuse ambiguous provider-only mutation when multiple instances exist.

## Batch gate

- [x] Run focused settings, policy, usage UI, percent-history, sync, session,
  controller, accessibility, and architecture tests.
- [x] Render the two-instance Settings/Usage Center source-AppKit path once.
- [x] Obtain one independent findings-first review.
- [x] Run Ruff, `git diff --check`, and `make fast` after the batch settles.
- [x] Run one stable-fingerprint complete suite before closing the source path.

## Completion boundary

P2.29 source integration is complete when two instances of one provider retain
different values for all five choices and every named consumer uses the exact
instance projection. Installed account switching, provider login, physical
LED output, packaging, signing, notarization, and release readiness remain
separate evidence gates.

## Source receipts

- The isolated source-AppKit Settings and Usage Center renders showed separate
  personal and work Claude cards, exact labels, distinct color overrides, and
  all five bounded profile controls.
- Independent review found stale cached Settings controls and a merged-sync
  memo that ignored sharing-policy changes and time expiry. The repair now
  resynchronizes card headings, accessibility labels, text fields, popup
  selections, and committed payloads; sharing changes invalidate the memo,
  identical snapshots expire after 30 seconds, and a generation fence rejects
  late results from an older policy.
- The final focused architecture and P2.29 run passed 174 tests. Ruff and
  `git diff --check` passed. `make fast` passed in 16.05 seconds with 102
  contract tests, 150 fixture tests, and 517 focused tests.
- The complete suite passed 7,077 tests plus 7 subtests in 175.77 seconds with
  four known Python 3.12 multiprocessing fork warnings. All 516 bound `src/`
  and `tests/` files retained fingerprint
  `804c9abed497abb90d31dfad28510cd1f04ddfa8c4fc8b630218741424046b99`
  before and after the run.
- These receipts do not prove an installed bundle, live account switching,
  physical output, signing, notarization, packaging, publication, updater
  behavior, or release readiness.
