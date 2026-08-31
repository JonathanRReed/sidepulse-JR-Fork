# JR Bar P2.32 signed Sparkle channel implementation plan

> Use the approved design in
> `docs/superpowers/specs/2026-08-29-jr-bar-p2-32-signed-sparkle-channel-design.md`.
> Keep the dirty worktree intact. Do not commit, publish, install, or change
> permissions while executing this source tranche.

## Batch 1: dependency and release contract

- [x] Write failing tests for the schema-3 release contract, pinned Sparkle
  2.9.6 metadata, supplemental ZIP name, feed URL, channels, phased rollout,
  and required signing inputs.
- [x] Add a safe dependency preparer that verifies the official archive digest,
  rejects unsafe members, extracts only the framework, signing tools, and
  license, and verifies the extracted version.
- [x] Keep the PKG primary and authoritative while declaring the updater ZIP,
  signed feed, and channel metadata as required supplemental artifacts.
- [x] Add the dependency preparation and contract tests to the fast gate.

## Batch 2: runtime and status-bar menu

- [x] Write failing pure and AppKit-backed tests for unavailable source runs,
  exact embedded-bundle validation, main-thread controller creation, retained
  delegate/controller lifetime, stable/beta channel projection, and no forced
  automatic-check preference.
- [x] Add `sidepulse.sparkle_updater` with an injectable bundle loader,
  class lookup, user-defaults store, channel delegate, manual check, and update
  cycle reset.
- [x] Start the updater before the first production menu render and retain it
  on the final composed controller.
- [x] Add a visible `Software Update` submenu only when Sparkle is available.
- [x] Add selectors for manual checks and stable/beta selection without adding
  another status-bar controller class.

## Batch 3: bundle construction and verification

- [x] Write failing tests for framework embedding, exact Info.plist keys,
  symlink-preserving copy, version/license checks, and unchanged root
  entitlements.
- [x] Extend the package builder to prepare and embed Sparkle before signing.
- [x] Extend inside-out signing tests for Sparkle's nested XPCs, updater app,
  helper, framework, and root order.
- [x] Add exact nested Sparkle signature, TeamIdentifier, hardened-runtime,
  entitlement, version, dependency, and rpath verification.
- [x] Add production app notarization and stapling before creating the final
  updater ZIP and authoritative PKG.

## Batch 4: update archive, appcast, and evidence

- [x] Write failing tests for a ZIP containing only `SidePulse.app`, exact
  archive naming, tool failures, signed-feed marker, enclosure signature and
  length, channel encoding, phased rollout, and candidate metadata.
- [x] Add an executable supplemental ZIP packager using macOS `ditto` with
  isolated exact paths and clear missing-tool behavior.
- [x] Add an appcast generator/validator that delegates signing to the pinned
  Sparkle tools and never receives private key material in argv.
- [x] Extend candidate identity with the updater ZIP and extend required
  receipts for the update archive, nested Sparkle signing, app notarization,
  app stapling, and signed appcast.
- [x] Make the manifest and checksum paths bind the ZIP, appcast, and channel
  metadata to the exact candidate and reject post-evidence byte drift.
- [x] Extend publication tooling to upload the immutable update ZIP with the
  version release and update the durable feed only afterward.

## Batch 5: monotonic upgrades, documentation, and gates

- [x] Write failing transition tests for older-to-newer, same-version, and
  downgrade attempts.
- [x] Enforce strict monotonicity in installed-upgrade creation and evidence
  validation. Document manual PKG recovery rather than claiming an automatic
  rollback feature.
- [x] Replace unsafe notary credential argv examples with Keychain-safe,
  interactive guidance. Document Sparkle key ownership and backup without
  exposing the private key.
- [x] Update production release, packaging, architecture, feature matrix,
  local verification, changelog, and completion-contract documentation.
- [x] Run Ruff, focused tests, `git diff --check`, `make fast`, and one stable
  complete suite with a before/after source-test fingerprint.
- [x] Render and inspect the status-bar `Software Update` submenu from source.
- [x] Obtain an independent findings-first security and correctness review,
  fix validated findings, rerun the affected gates, and record receipts.

Source checkpoint receipts:

- `make fast` passed with 112 contract tests, a 571-file tracked secret scan,
  150 fixture tests, 521 focused tests, bytecode compilation, dependency
  policy, version, and diff-hygiene checks.
- The warning-hardened complete suite passed 7,203 tests plus 7 subtests in
  165.60 seconds. The only warnings were four known Python multiprocessing
  fork deprecations. The 523-file source-test fingerprint remained
  `e407004016e04a2ab06ec611fe46e1e59fef58f30168fae316d9a4e029c8c791`.
- A native AppKit source render exposed `Devices`, `Software Update`, and
  `Quit JR Bar`; the update submenu exposed `Check for Updates...`, selected
  `Stable Updates`, and unselected `Beta Updates`.
- Findings-first security review led to final appcast signature revalidation,
  raw ingress limits, exact Sparkle distribution provenance, archive
  time-of-check/time-of-use hardening, and bounded usage-refresh ownership.
  The final independent lifecycle rereview returned no findings.

## External candidate gates

- [x] Generate or retrieve the dedicated Sparkle key in the owner's login
  Keychain and embed only its public key.
- [ ] Build two differently versioned Developer ID signed, notarized candidates
  from clean attributable checkouts.
- [ ] Install `N`, update to `N+1` through the staged feed, relaunch, preserve
  settings, and verify a stale/lower feed cannot downgrade.
- [ ] Publish only after separate release authority. Source tests and local
  unsigned builds do not satisfy these rows.
