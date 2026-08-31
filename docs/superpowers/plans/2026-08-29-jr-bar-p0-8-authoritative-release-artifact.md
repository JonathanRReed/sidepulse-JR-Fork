# JR Bar P0.8: Authoritative Release Artifact

Status: closed on 2026-08-29.

## Objective

Make the signed and notarized macOS PKG the only supported JR Bar production
artifact. Align the builder, release gate, publisher, evidence inputs,
checksums, workflows, and current release documentation with that decision.
Do not publish a release.

## Decision

- The authoritative macOS artifact is
  `SidePulse-<version>-<architecture>.pkg`.
- `SidePulse` remains in the filename because it is the compatibility name of
  the installed `SidePulse.app`, executable, bundle identifier, CLI, and
  package receipts. The human-facing product name remains JR Bar.
- A production build requires a Developer ID Application identity, a
  Developer ID Installer identity, and a notarytool keychain profile.
- ZIP archives are not a supported production product in this tranche. The
  existing builder's conditional ZIP output is not verified, installed,
  included in candidate evidence, checksummed by the publisher, or uploaded.
  Remove that ambiguous output instead of presenting it as release-ready.
- JR Bar has no updater or appcast today. Release metadata must say so plainly.
  A signed Sparkle channel remains roadmap item 32 and must bind itself to the
  authoritative candidate when implemented.
- Python wheels and source distributions remain developer-facing GitHub
  Release assets. They are not the authoritative installed macOS product and
  are not published to PyPI.

## Implementation sequence

1. Add executable contract tests for artifact kind, exact filename, signing
   requirements, updater status, safe version and architecture inputs, and CLI
   path output. Observe them fail before production code exists.
2. Add a pure release-artifact contract module with a small CLI so shell
   scripts share the same artifact name and policy.
3. Route the package builder, release gate, and publisher through the contract.
   Remove the unsupported ZIP build path and release wildcards.
4. Keep package verification, real installation, installed-upgrade smoke,
   SBOM, manifest, environment snapshot, and SHA-256 checks tied to the exact
   authoritative PKG path.
5. Align README, packaging, local-verification, release, architecture,
   repository-hygiene, feature-matrix, and workflow wording. State explicitly
   that no updater/appcast exists yet.
6. Run focused tests and shell syntax checks, then the complete suite, Ruff,
   dependency policy, secret scanning, version validation, compilation, and
   diff checks.
7. Obtain exactly one fresh independent candidate review and resolve every
   validated finding before closing P0.8.

## Completion evidence

P0.8 is complete only when:

- one pure source defines the exact authoritative PKG name and requirements;
- the builder produces no production ZIP;
- the release gate and publisher use the exact PKG path without globs;
- the authoritative PKG is included in SBOM, release manifest, and checksum
  inputs;
- current docs describe the same certificate and distribution contract;
- current docs do not imply that a Sparkle appcast or automatic updater exists;
- focused, full, lint, shell, secret, dependency, version, compilation, and
  diff gates pass;
- the one independent review has no unresolved release-contract finding.

## Closure receipts

- `scripts/release_artifact_contract.py` is the pure authority for the
  compatibility-named `SidePulse-<version>-<architecture>.pkg`, the three
  required signing inputs, and the explicit absence of updater metadata.
- The builder, installed-release gate, and publisher resolve the same exact
  path through that authority. Production ZIP output and wildcard package
  publication are gone.
- The production publisher cannot bypass the macOS gate. The builder fails
  before build work when any Developer ID Application, Developer ID Installer,
  or notary-profile input is absent, unless the explicit local-only unsigned
  mode is selected.
- SBOM properties and release-manifest artifact records now preserve canonical
  repository-relative paths, sizes, and SHA-256 digests. Same-named files in
  different directories remain distinguishable.
- Current README, packaging, architecture, verification, hygiene, feature, and
  release documentation agree that the PKG is the only supported production
  artifact, no automatic updater or appcast exists, and no JR Bar GitHub
  Release has been published.
- The first canonical run exposed a missing no-isolation build backend in the
  development extra. Pinned setuptools was added as a development tool, and
  dependency policy now requires every build-system requirement in that extra.
- Post-review focused gate: 96 tests passed. The complete canonical gate passed
  with 6,257 tests, 7 subtests, and 4 existing multiprocessing deprecation
  warnings, then built the wheel and source distribution, passed Twine, passed
  clean-install verification, and generated the SBOM.
- Repository Ruff, Python compilation, shell syntax, package health,
  dependency policy, release-version validation at 0.5.0, and `git diff
  --check` passed. The secret scanner passed across 571 tracked files and all
  20 untracked goal files.
- The one independent candidate review found four gaps: a Python-only publish
  bypass, optional notarization in the production builder, basename-only
  evidence records, and wording that implied a release already existed. All
  four were fixed and regression-tested. No second review was requested.
- No PKG was signed, notarized, installed, published, or represented as a
  release candidate in this source-contract tranche. Candidate-bound evidence
  remains P0.9.
