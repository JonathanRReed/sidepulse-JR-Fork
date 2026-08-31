# JR Bar P1.20 executable packaging tests plan

Status: closed on 2026-08-29.

## Objective

Exercise the production packaging and release-artifact seams in deterministic
tests without signing, notarizing, installing, publishing, or claiming that a
test double is a release candidate.

## Current product contract

- The signed and notarized PKG is the only supported macOS production artifact.
- ZIP output is intentionally unsupported.
- Appcast output is intentionally unsupported until P3.32 adds a signed
  Sparkle channel bound to an exact verified candidate.
- The public artifact keeps the compatibility filename
  `SidePulse-<version>-<architecture>.pkg` while the product display name is
  JR Bar.

## Implementation

1. Add a standard-library macOS package-assembly seam that owns `pkgbuild`,
   `productbuild`, and signed-package verification. Keep production tool paths
   fixed, but allow imported tests to supply isolated executable doubles.
2. Make the shell builder delegate its final PKG stage to that seam. Preserve
   its existing signing, notarization, and evidence behavior.
3. Add a deterministic checksum-manifest tool and make the publisher use it
   instead of an inline `shasum` pipeline.
4. Extend the release-artifact contract so machine-readable output states that
   ZIP and appcast outputs are unsupported and have no artifact path.
5. Add executable tests for:
   - signed and unsigned PKG command flow and exact output naming;
   - missing packaging tools and a productbuild certificate failure;
   - current ZIP and appcast non-output behavior;
   - safe version parsing and exact artifact names;
   - deterministic SHA-256 manifest output and missing-artifact failure;
   - the full isolated builder smoke already present in the macOS suite.
6. Update active documentation and the completion contract only after the
   implementation and focused verification pass.

## Verification

- Run the new focused tests first and observe the intended red failures before
  implementation.
- Run Ruff on every changed Python file.
- Run the complete focused packaging, release-contract, supply-chain, and build
  contract slice.
- Run `make fast`.
- Run an independent findings-first review after the tree is stable.

## Evidence boundary

Passing tests prove command construction, failure handling, deterministic
checksums, and the declared supported-output contract. They do not prove a
Developer ID identity, notarization, Gatekeeper acceptance, installed-app
behavior, hardware behavior, publication, or release readiness.

## Completion receipt

- The executable PKG seam covers signed and unsigned command construction,
  exact output, missing tools, certificate failure, signature verification, and
  success-without-output refusal.
- The release gate builds the exact reviewed wheel and source distribution in
  an empty staging directory, validates both with Twine, atomically replaces
  only their exact paths, and ignores unrelated stale files.
- Checksum publication is deterministic, root-bounded, atomic, mode-stable,
  and validated against the release evidence inventory, byte counts, and
  SHA-256 digests. Malformed, incomplete, duplicated, changed, outside-root,
  and aliased inputs fail closed.
- ZIP and appcast output remain explicitly unsupported and absent until roadmap
  item 32.
- The complete focused release and packaging slice passed 131 tests.
- `make fast` passed in 7.91 seconds with 69 contract, 139 fixture and schema,
  and 222 focused semantic tests.
- A fingerprint-stable full suite passed 6,586 tests plus 7 subtests. Ruff,
  compileall, dependency policy, the 571-file tracked secret scan, version
  validation, Bash syntax, six architecture ratchets, and `git diff --check`
  passed. Four known multiprocessing fork warnings remained.
- Final independent rereview returned no findings.
