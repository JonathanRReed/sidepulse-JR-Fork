# JR Bar P0.5: Exact Devin Browser Consent

Status: closed on 2026-08-29.

## Objective

Make the existing exact browser-consent model authoritative for every Devin browser read. Enabling the broad `browser_sources` preference must not authorize a profile scan or a background session read.

## Source-to-sink finding

The current bypass has two reachable forms:

- background refresh: `ProviderUsageService` -> `collect_devin` -> `_import_devin_browser_session` -> `import_devin_session(Path.home())` -> every Firefox-family profile;
- explicit UI import/reconnect: `handle_provider_usage_action` -> `_import_browser_session` -> `import_devin_session(Path.home())` -> every Firefox-family profile.

The exact consent path already exists in `provider_browser_consent` and `provider_browser_import`, but the live UI and refresh paths do not use it.

## Security invariant

1. `browser_sources=True` is a display/source preference, not permission to read browser data.
2. No background Devin refresh reads any browser profile.
3. An explicit Import or Reconnect action may read only a profile covered by a persisted consent matching provider, browser, profile, domain, and field.
4. Missing, ambiguous, mismatched, revoked, or forward-version/read-only consent fails closed before filesystem access.
5. A granted profile change never broadens to sibling profiles or other browsers.
6. The existing CLI grant/import flow and manual pasted-token fallback remain usable.
7. Browser reads remain bounded, symlink-safe, origin-limited, and free of credential or settings mutation until the exact consent check passes.

## Compatibility contract

- Preserve stored Devin credentials, organization selection, quota requests, and parsing.
- Preserve explicit CLI import through `provider_browser_import.import_devin_browser_session`.
- Preserve user-initiated Firefox-family import only when it can resolve one exact persisted grant to one exact profile.
- Do not scan browser profiles to discover a candidate after consent.
- Do not perform a real browser read or mutate a real credential during verification.

## TDD sequence

1. Add a negative collector test proving `browser_sources=True` alone cannot invoke a browser importer.
2. Add explicit UI import tests proving absent, mismatched, and ambiguous grants cause zero filesystem reads.
3. Add an exact-profile Firefox-family test proving a granted browser/profile reads only that profile.
4. Preserve the existing exact Chromium import and CLI grant/import tests.
5. Remove the collector's background browser scan and make stored credentials its only secret source.
6. Replace the UI's generic home scan with an exact persisted-consent resolver and exact-profile reader.
7. Update product copy so it distinguishes background refresh from explicit consented import.
8. Run focused tests, related provider/runtime tests, full suite, Ruff, secret scan, release-version validation, and diff checks.
9. Submit the candidate to one fresh read-only reviewer and resolve only validated findings.

## Completion evidence

P0.5 is complete only when:

- the original `browser_sources=True` reproduction no longer invokes a browser reader;
- source search finds no production `import_devin_session(Path.home())` or equivalent broad scan;
- adversarial tests prove scope changes and ambiguity fail before filesystem access;
- an exact persisted grant imports only its named profile;
- legitimate stored-token collection and exact CLI import still work;
- focused, full, lint, secret, version, and diff gates pass;
- the independent candidate review has no unresolved security or compatibility finding.

## Closure receipts

- Focused gate: 73 tests passed.
- Canonical gate: 6,229 tests and 7 subtests passed, with 4 existing multiprocessing deprecation warnings.
- Ruff passed across source, tests, packaging, and scripts.
- Secret scan passed across 571 tracked files.
- Release version validated as 0.5.0 and `git diff --check` passed.
- Production call-site search found no caller of the legacy cross-profile Devin reader.
- Independent review found a same-name Chromium path substitution and a Firefox localStorage over-read. The Chromium path is now resolved against the canonical browser root, and the Firefox SQL query now selects only Devin session and organization key patterns. Both findings have adversarial regression tests.
