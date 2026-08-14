# SidePulse Community Adaptations Design

**Date:** 2026-08-13

**Status:** Approved component of the SidePulse installed-agents design.

## Goal

Adapt the strongest public SidePulse ideas into the current canonical runtime,
privacy, packaging, installer, presentation, and provider boundaries without
merging external branches or reintroducing legacy authority.

The decision record is the local community research brief at
`outputs/sidepulse-community-research-2026-08-13.md`. Public code is pattern
evidence. Every adopted behavior is independently specified and implemented
with SidePulse types and tests.

## Adaptation 1: Relay Continuity

Physical and Screen Bar relay share one canonical presentation epoch. A phase
advance does not reprogram or restart local playback. Structural semantic,
palette, device-count, brightness, calibration, reduced-motion, or mode changes
do invalidate the continuity token.

The current source already has a reviewed continuity fix and focused tests.
Remaining work is installed Screen Bar and physical two-LED or eight-LED
acceptance. The baton must visibly visit the entire chain without repeated
first/second-LED restart.

## Adaptation 2: Codex Usage-Limit Terminal Recovery

The bounded Codex transcript adapter recognizes the official structured
usage-limit record even when no live Stop hook arrives. It selects the newest
valid terminal evidence by canonical watermark and may use a bounded
`error.message` fallback when the normal assistant terminal field is absent.

Terminality and cause remain distinct. Capacity exhaustion closes the current
turn, clears active tool truth, and updates the exact capacity source health. It
does not become an ordinary completed outcome or trigger celebratory copy unless
a separate completion fact exists.

The adapter remains one fallback source inside the canonical collector. It does
not add a second scanner, registry, state store, notification path, or display
model.

## Adaptation 3: Hermes Lifecycle and Outcomes

Hermes normalization adds the reviewed native aliases and nested `extra`
flattening, including session finalization and API request failure. Session end
and session finalize keep their provider-defined meanings. Completed,
interrupted, failed, and stale outcomes remain separate.

Unknown nested content is discarded. Only allowlisted identifiers, timestamps,
event kinds, request state, lifecycle state, and product-owned failure causes
cross the adapter boundary.

## Adaptation 4: Cursor Payload Normalization

Cursor hook installation includes the configured event name and preserves
unrelated hooks. The adapter accepts documented conversation identity,
workspace-root shape, tool event shape, and mandatory JSON response contract.
Raw workspace paths and tool input do not cross canonical or persisted
boundaries. Stable identity uses an opaque product token derived inside the
private adapter boundary.

## Adaptation 5: Typed Runtime Diagnostics

A `sidepulse doctor` model reports allowlisted facts:

- installed surface and negotiated capability counts;
- hook configuration health;
- event socket health and same-UID enforcement;
- canonical state publication and restore health;
- provider source health and refresh state;
- private store health;
- notification authorization state;
- Screen Bar scheduler and runtime worker bounds;
- device discovery, last verified write, and generic error state;
- package identity, runtime root, and signature-verifier result.

The optional diagnostic export uses the existing private export boundary and
contains typed JSON only. It excludes raw log tails, configs, transcripts,
paths, URLs, labels, prompts, commands, payloads, emails, tokens, environment,
and exceptions. Each field has an exact schema, cap, and neutral display copy.

## Adaptation 6: Packaging and Navigation

The production package builder must include any Apple Events entitlement and
`NSAppleEventsUsageDescription` only if the current installed navigation path
actually sends Apple Events. If navigation uses Accessibility instead, the
package does not claim an unused permission.

Terminal and Ghostty navigation use exact bundle IDs, generation-validated
targets, and current macOS permission status. The signed installed package is
tested against both surfaces. Source tests or plist inspection alone are not
live navigation proof.

## Adaptation 7: Transactional Isolated Installer

The user installer uses a private staging directory, exact interpreter and
package hashes, a user-owned runtime prefix, collision preflight, atomic publish,
rollback on every failure, and app or command identity verification before
activation. It never modifies system Python or Homebrew packages.

Existing commands are refused or backed up only with explicit user choice.
Tests use a temporary prefix and simulate interrupted download, invalid archive,
link, parent swap, ownership, permissions, partial environment, command
collision, bundle verification failure, and rollback failure. The installed app
and CLI must resolve the same verified runtime.

## Adaptation 8: Main-Session Projection and Worker Retirement

Raw worker truth remains canonical. A pure display projection groups workers
under exact primary `WorkKey`, suppresses worker-level questions when policy
requires, and retires stale workers without deleting primary outcome truth.

Ordering is stable within an episode. Physical slots are not claimed stable
unless a direct invariant proves they are. A worker terminal event rolls up to
its parent while preserving exact navigation and history attribution.

## Adaptation 9: Verified Provider Patterns

Antigravity and OpenCode patterns are adopted only through the Installed Agent
Registry. An external camelCase event, `fullyIdle` field, quota result, or hook
response is accepted only after official documentation or an exact installed
fixture proves its contract. Resource exhaustion cannot map to green completion.

## Rejected Patterns

- default-on ScreenCaptureKit audio or microphone capture;
- continuous audio-driven removable-volume writes;
- raw or weakly redacted log ZIPs;
- browser-cookie and private-endpoint usage scraping;
- quota exhaustion as success;
- whole fleet-mode collector replacement;
- external provider registries parallel to SidePulse's canonical registry;
- claims of physical slot stability without a direct invariant;
- dependency additions without user approval.

## Dependency Order

1. verify and live-test relay continuity;
2. add Codex terminal recovery;
3. correct Hermes outcomes;
4. normalize Cursor payloads;
5. add typed doctor and diagnostic export;
6. conditionally correct packaging metadata and test Ghostty navigation;
7. add the transactional isolated installer;
8. add main-session projection and stale-worker retirement;
9. integrate Antigravity and OpenCode through the registry plans.

Each adaptation is a separate TDD task with one independent correctness review.
Diagnostics, packaging, installer, and provider work also receive a privacy or
security review. No task starts by copying an external patch into production.

## Acceptance

- strict RED before each source change;
- exact provider and canonical neighbor gates;
- privacy mutation tests for every external-field boundary;
- package and installer adversarial filesystem tests;
- full Ruff, compile, guarded pytest, and diff checks;
- signed installed package verification before live acceptance;
- direct Screen Bar and hardware relay observation;
- direct installed Terminal and Ghostty navigation observation;
- no commit, push, install, permission, credential, or publication action
  without its separate authority.

