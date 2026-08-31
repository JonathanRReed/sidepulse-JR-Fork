# JR Bar P0.6: Loopback Status Privacy

Status: closed on 2026-08-29.

## Objective

Make `sidepulse serve` useful to local integrations without treating loopback transport as authorization. The default response must be a deliberately redacted, versioned schema.

## Validated finding

`sidepulse serve` binds to `127.0.0.1`, but any local process can request `/status.json`. The current handler forwards persisted agent works and provider usage with no redaction. The payload includes account labels, action labels, estimated cost, cache savings, credits, incident text, lane labels, source identifiers, and synthetic session labels. Current agent serialization does not contain raw transcript or prompt text, and no server secret is present in the response.

## Security invariant

1. Loopback is a transport boundary, not a trust boundary.
2. The unauthenticated endpoint returns only an explicit allowlist of operational state.
3. The public schema never includes account identity, session or work identity, labels, actions, incidents, cost, credits, raw token counts, model detail, source identifiers, requests, messages, prompts, or credentials.
4. The public document identifies itself as redacted and increments its schema version when the contract changes.
5. Missing, malformed, oversized, or future persisted documents fail closed to `null` or empty public state.
6. The server remains loopback-only, read-only, query-insensitive, and free of CORS opt-in.
7. A future private schema must require separate explicit authorization and must never return its own credential. It is not part of this tranche.

## Public schema

- Top level: `schema_version`, `privacy`, `agents`, and `usage`.
- `agents`: persisted generation plus aggregate counts by lifecycle, next actor, source health, and source freshness. No row, key, label, request, or clock object is returned.
- `usage`: refresh timestamps plus provider rows containing only provider ID, observation timestamp, source state, and an anonymous quota summary with window count, minimum remaining percent, and next reset time.
- Every nested object is rebuilt from an allowlist. No persisted dictionary is forwarded directly.

## TDD sequence

1. Add a persisted-state fixture containing unique sentinel values in every private field.
2. Prove the current document forwards those sentinels and lacks an explicit redaction marker.
3. Add an exact public-schema assertion, including aggregate agent state and redacted provider quota state.
4. Add a recursive assertion that no private sentinel appears anywhere in the encoded response.
5. Implement pure allowlist projectors in `serve.py` and stop forwarding raw persisted dictionaries.
6. Preserve the loopback handler, cache policy, 404 behavior, and CLI contract.
7. Run focused server, state-store, CLI, and comparable loopback-security tests.
8. Run the full suite, Ruff, secret scan, release-version validation, diff checks, and one fresh independent review.

## Completion evidence

P0.6 is complete only when:

- the original unauthenticated private-field reproduction fails;
- the public document is schema version 2 and explicitly marked `redacted`;
- adversarial tests prove account, work, label, cost, credit, incident, token, model, source, request, and message sentinels never appear;
- useful aggregate agent and provider quota state remains available;
- no raw persisted mapping is returned by the server;
- focused, full, lint, secret, version, and diff gates pass;
- the independent candidate review has no unresolved security or compatibility finding.

## Closure receipts

- Focused gate: 113 tests passed.
- Canonical gate: 6,234 tests and 7 subtests passed, with 4 existing multiprocessing deprecation warnings.
- Ruff passed across source, tests, packaging, and scripts.
- Secret scan passed across 571 tracked files.
- Release version validated as 0.5.0 and `git diff --check` passed.
- Forbidden-field search found no private provider or work fields in the production status projector.
- Independent review found no direct leak or in-repository compatibility regression. Its negative-test finding was resolved with future-schema, unknown-value, oversized-file, and symlinked-input cases, all of which fail closed.
