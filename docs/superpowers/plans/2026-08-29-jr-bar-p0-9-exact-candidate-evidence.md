# JR Bar P0.9: Exact Candidate Evidence

Status: completed in source on 2026-08-29. The owner-Mac signing,
notarization, installation, hardware, uninstallation, and publication run was
not performed or claimed.

## Objective

Replace asserted release booleans with one fail-closed, versioned evidence
record whose candidate identity and every required receipt refer to the same
authoritative macOS PKG. Add the capability and tests without signing,
installing, uninstalling, notarizing, publishing, or claiming a release in
this source tranche.

## Findings

- `scripts/generate_release_manifest.py` currently writes signing,
  notarization, Gatekeeper, source-gate, clean-install, upgrade, settings,
  hardware, and performance outcomes as literal `true` values.
- `scripts/verify_macos_release.sh` executes many real checks but does not
  persist their outputs as candidate-bound receipts.
- The current manifest can combine an arbitrary app and PKG under the release
  root without proving that the app payload belongs to that exact package.
- Package contents, notarization submission data, and verified uninstallation
  are not represented as first-class evidence.

## Evidence contract

- `schema_version` is explicit and validated.
- `candidate` contains the commit, version, architecture, exact PKG path,
  byte size, SHA-256, deterministic app-tree digest, bundle identifier, team
  identifier, package-contents digest, SBOM SHA-256, and performance-evidence
  SHA-256.
- Every required check is a receipt object. A receipt records its kind, tool,
  result, observed time, exact candidate PKG SHA-256, input path and digest,
  bounded output evidence, and output digest.
- Required receipt kinds are source gate, performance, PKG signature,
  notarization, stapling, PKG Gatekeeper, package contents, app code signature,
  app Gatekeeper, bundle closure, entitlements, hardware smoke, installed
  upgrade, settings preservation, uninstall, and clean PKG install.
- Manifest assembly rejects missing, failed, duplicate, unknown,
  cross-candidate, cross-input, malformed, stale-path, or secret-shaped
  receipts. It re-hashes every current input before accepting evidence.
- Notarization uses the real `notarytool --wait` submission response and
  requires an accepted status. Keychain profile names and credentials are not
  persisted.
- Uninstallation remains an explicit release-gate action guarded by a separate
  authorization variable. This implementation tranche does not invoke it.

## Implementation sequence

1. Add adversarial tests for missing receipts, failed receipts,
   cross-candidate substitution, package-content substitution, outside-root
   paths, malformed notarization evidence, and secret-shaped evidence.
2. Add a pure release-evidence module for hashing files and bundle trees,
   recording command receipts, validating the receipt set, and assembling the
   versioned document.
3. Refactor release-manifest generation to consume only validated candidate
   and receipt inputs. Remove every hard-coded verification boolean.
4. Update the macOS gate to record actual command receipts, retain the
   notarization response, record exact package contents, verify upgrade and
   settings preservation, exercise authorized uninstall, verify its result,
   reinstall the same PKG, and record clean-install evidence.
5. Align publication and release documentation with the versioned evidence
   file and explicit external/destructive gates.
6. Run focused tests and lint, shell syntax, full canonical verification,
   secret scans, compilation, dependency policy, release-version validation,
   and diff checks.
7. Obtain exactly one fresh independent candidate review and resolve every
   validated provenance or trust finding before closing P0.9.

## Completion boundary

P0.9 closes when source and adversarial tests prove that the release gate can
produce exactly one internally consistent evidence document and cannot create
one from missing or mismatched receipts. A real signed, notarized, installed,
uninstalled, or published candidate remains unclaimed until that live owner-Mac
gate is run with the required identities, profile, hardware, permissions, and
explicit destructive authorization.

## Closure evidence

- The release-evidence schema is versioned, fail-closed, and contains no
  asserted verification booleans. It rejects missing, duplicate, unknown,
  failed, malformed, stale, outside-root, secret-shaped, cross-input, and
  cross-candidate receipts.
- Notarization is bound to the pre-staple PKG through Apple's response and log
  digest. A separate stapling receipt binds that pre-staple digest to the
  final candidate digest, because stapling legitimately changes the PKG.
- Upgrade evidence requires a real prior app from the same team, a different
  version, the exact prior app-tree digest, a real package receipt, and the
  pre-upgrade settings digest.
- Uninstall evidence requires the app, exact package receipt, owned CLI link,
  provider hooks, status jobs, sleep helper, user and system eject guards,
  and release receipts to be removed while the post-upgrade user settings
  digest remains unchanged. Both fallback and XDG user guard locations are
  checked.
- One independent review raised four findings: missing prior-install proof,
  an incomplete notarization-to-final-candidate chain, comparison against the
  wrong settings snapshot, and incomplete integration cleanup proof. All four
  were resolved in the source contract and adversarial tests.
- Focused post-review release tests: 148 passed.
- Canonical gate: 6,283 tests passed with 7 subtests and 4 existing Python
  multiprocessing deprecation warnings. Dependency policy, tracked secret
  scan, Ruff, version validation, wheel and source-distribution builds, Twine,
  clean wheel install, SBOM generation, and the JR Bar verification wrapper
  all passed.
- `git diff --check` passed. A separate high-confidence scan of untracked
  source and plan files found no literal credential material.
