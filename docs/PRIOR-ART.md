# Prior art and attribution

JR-BAR is MIT licensed and stands on work by others. This file records
what we studied, what we adopted, and under which terms — so a reader
can trace any design decision back to its source.

## CodexBar — MIT, © 2026 Peter Steinberger

<https://github.com/steipete/CodexBar>

The closest analogue to this project: a macOS menu-bar app that reads
AI-coding-tool usage without asking the user to sign in again. We read
its source, docs, CHANGELOG, issues and pull requests in depth on
2026-08-27 and adopted these ideas (reimplemented in Python/PyObjC, no
code copied):

- **Claude OAuth token endpoint and client id.** `platform.claude.com`
  (not `console.anthropic.com`), form-encoded, using Claude Code's own
  public PKCE client id.
- **Refresh failure taxonomy.** Only a `400`/`401` whose body carries
  `error == "invalid_grant"` means the sign-in is dead; every other
  4xx is transient. Their `refreshFailureDisposition` makes exactly
  this distinction, and copying it stopped us wedging a provider behind
  a terminal gate for an hour over one bad request.
- **Keychain change detection by item attributes.** An attributes-only
  query (no secret requested, so no consent dialog) yields the item's
  modification and creation stamps, which is how a credential gate can
  notice a re-login it cannot see in any file. Throttled to 60s, as
  they throttle theirs.
- **Pre-emptive refresh.** Refresh from the stored expiry before the
  usage call rather than reactively after a 401.
- **Cadence that ignores quota level.** Their adaptive refresh
  deliberately does not poll harder when a meter runs low. We adopted
  that and kept one documented divergence: within 10 minutes of a reset
  boundary we still poll at 120s, because unlike CodexBar we celebrate
  resets and have to observe the crossing.
- **Same-origin redirect refusal** on requests that carry a bearer
  token, in the spirit of their `ProviderHTTPClient`.

**Where we deliberately diverge:** for Claude-CLI-owned credentials
CodexBar *delegates* refresh back to the CLI (driving `claude /status`
in a PTY) and never writes that Keychain item. JR-BAR refreshes
directly and writes the rotated tokens back, so it needs no PTY
subprocess and no dependency on `claude` being installed — at the cost
of owning that write-back. Their delegated path is reported unreliable
in steipete/CodexBar#1287.

## T3 Code — MIT, © 2026 T3 Tools Inc.

<https://github.com/pingdotgg/t3code>

Studied for agent/session lifecycle reporting and desktop practices.

## T3Notch

<https://github.com/zortos293/T3Notch>

Studied **conceptually only**. At the time of reading this repository
published no license, so all rights are reserved by its author and none
of its code is used here. Our notch-adjacent geometry (choosing the
notched display, observing
`NSApplicationDidChangeScreenParametersNotification`) is written from
Apple's public AppKit contracts.

## SidePulse

JR-BAR began as a fork of SidePulse (MIT, © 2026 Peter Kuhar) and has
since diverged substantially. See `LICENSE`.
