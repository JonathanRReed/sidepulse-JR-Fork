# JR Bar P0.4: Claude Credential Ownership

## Objective

Close the validated Claude credential confidentiality and ownership finding without changing the legitimate usage-reading flow.

JR Bar may read a valid access token from Claude Code's Keychain item after user consent and copy that access token into JR Bar's own credential store. JR Bar must not consume Claude Code's refresh token, call Anthropic's token endpoint on Claude Code's behalf, or mutate Claude Code's Keychain item.

## Source-to-sink finding

The current expired-token path is:

`repair_claude_credential` or a background collector -> `_renew_claude_from_payload` -> `refresh_claude_payload` -> `_default_claude_keychain_writer` -> `write_keychain_secret` -> `/usr/bin/security add-generic-password ... -w <secret>`.

This exposes the full rotated credential in process arguments and changes a Keychain item owned by another application. The trusted executable and list-form subprocess call prevent shell injection, but they do not prevent local argv observation or third-party credential ownership violations.

## Security invariant

1. Claude Code's Keychain item is read-only to JR Bar.
2. No Claude refresh token is sent to a network endpoint by JR Bar.
3. No secret is placed in a process argument by a JR Bar Keychain write path.
4. A valid Claude access token can still be copied into JR Bar's own credential store.
5. An expired or refresh-token-only Claude credential produces explicit Claude-owned refresh guidance; an empty or malformed credential produces sign-in guidance. Neither path mutates credentials.
6. Background sync is allowed only under the existing recorded standing grant and performs a read plus a local access-token copy, never refresh or write-back.
7. A terminal authentication gate lifts only when the externally owned Keychain fingerprint changes or the user explicitly forces a retry.

## Compatibility contract

- Keep user-initiated Keychain reads and the denial/cooldown ledger.
- Keep valid-token reconnect, expiry recording, quota fetching, redirect protection, parsing, and capacity normalization.
- Keep the external Keychain attribute fingerprint as the retry signal.
- Remove only the third-party write and refresh surfaces and the state used exclusively by them.
- Do not perform live Keychain mutation or live OAuth refresh while verifying this tranche.

## TDD sequence

1. Replace tests that encode direct refresh/write-back with tests for the read-only ownership invariant.
2. Add a source-boundary test proving `credentials` exports no Keychain writer and contains no `add-generic-password` write operation.
3. Add expired, empty, malformed, long, and flag-like credential cases proving no network, process, or store mutation occurs.
4. Preserve tests proving a valid access token is copied and its expiry is stored.
5. Add background-sync tests proving standing-grant reads can copy a newly valid access token without mutation.
6. Preserve and strengthen the terminal-gate fingerprint-change test.
7. Run the focused tests and confirm they fail for the old behavior before implementation.
8. Remove the Keychain write surface, direct Claude refresh surface, renewal backoff/state, and special terminal-gate renewal branch.
9. Run focused tests, related provider/runtime tests, the full suite, Ruff, secret scan, release-version validator, and diff checks in order.
10. Submit the candidate diff to one fresh read-only security reviewer. Address only validated findings, then rerun affected checks.

## Completion evidence

P0.4 is complete only when:

- the original argv reproduction no longer has a reachable write sink;
- source search finds no third-party Keychain write or Claude refresh-token consumer;
- adversarial tests show expired, refresh-token-only, and malformed payloads cannot reach a network/process/store sink;
- valid current access tokens still connect and collect normally;
- background sync and terminal-gate behavior match the invariant;
- focused, related, full, lint, secret, version, and diff gates pass;
- the independent candidate review reports no unresolved security or compatibility finding.
