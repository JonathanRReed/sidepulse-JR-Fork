# Contributing to JR Bar (formerly SidePulse)

JR Bar (formerly SidePulse) controls visible light, edits other tools’ hook configuration, reads private local state, can request macOS permissions, and ships a privileged installer path. Contributions are reviewed as desktop-systems changes, not as isolated Python utilities.

## Development baseline

JR Bar (formerly SidePulse) supports Python 3.10 through 3.13. macOS behavior requires PyObjC and must be tested on macOS. Use the reviewed dependency constraints:

```bash
./scripts/bootstrap-dev.sh
./scripts/verify.sh --portable
```

Before requesting review on macOS:

```bash
./scripts/verify.sh
```

Do not bypass `requirements/release-constraints.txt`, add floating dependency ranges, or use mutable GitHub Action tags. Dependency updates belong in a dedicated pull request with the full macOS gate.

## Branch and pull-request policy

- Never push feature or fix commits directly to `main`.
- Use one focused branch and one pull request per independently reviewable change.
- Keep pull requests small enough to explain the state transition, failure behavior, rollback path, and test evidence.
- Do not merge while a required local or GitHub status is failing.
- Security-sensitive changes require review of the complete source-to-effect path, not only the modified function.
- Release publication is performed only by `scripts/publish_release.sh` after the authoritative Mac gate.

## Architecture rules

1. Provider and system adapters emit typed facts. They do not update AppKit or hardware directly.
2. Pure policy modules perform no I/O and import no AppKit, Foundation, or Objective-C bridge modules.
3. AppKit objects remain main-thread-owned. Workers consume immutable request values and return immutable result values.
4. Blocking filesystem, subprocess, network, device, and persistence work never occurs during menu tracking, draw callbacks, settings interaction, or the AppKit refresh path.
5. Bursty sources use bounded latest-wins or explicitly ordered queues. Unbounded queues and caches are prohibited.
6. New refresh work declares the `CoreDomain` it changes and is admitted through the typed refresh boundary.
7. New integrations declare capabilities and preserve source, surface, provider, machine, project, thread, branch, and worktree identity where available.
8. First-party and user-authored light output passes the universal presentation compiler and exact firmware parser.
9. New settings fields require encoder, decoder, migration, schema-coverage, corrupt-file, and installed-upgrade tests.
10. Historical monoliths may shrink but may not grow. Extract behavior into named modules with narrow ownership.

## Test requirements

A change must test the seam where it becomes reachable. Unit tests alone are insufficient for:

- provider event to canonical state;
- canonical state to menu, Screen Bar, hardware, notification, or history;
- settings migration and persistence;
- worker submission, coalescing, stale-result rejection, and shutdown;
- hook install/uninstall preservation;
- signing, entitlements, package install, upgrade, and uninstall;
- exact final LED bytes and safety cadence;
- credential, webhook, loopback, or remote-peer boundaries.

Use synthetic data. Tests and fixtures must not contain real prompts, transcripts, access tokens, refresh tokens, account identifiers, private project titles, or personal paths.

## Code quality

- Ruff and bytecode compilation must pass.
- Public policy values use dataclasses, enums, and bounded validation instead of unstructured dictionaries when practical.
- Exceptions crossing subsystem boundaries use product-owned reason codes. Do not place server bodies, stderr, credentials, hostnames, or private user content in logs or UI copy.
- Broad exception handling belongs only at a documented process or framework boundary and must fail to a bounded state.
- Avoid runtime namespace injection, monkeypatch-only wiring, mutable module globals, and duplicate implementations of the same job.
- Keep comments focused on invariants, failure modes, or non-obvious platform behavior.

## macOS release verification

A production candidate requires a clean `main` checkout equal to `origin/main`, reviewed signing identities, notarization credentials, physical SidePulse hardware, an existing settings fixture for upgrade testing, and Instruments evidence. Run:

```bash
export APP_SIGN_IDENTITY='Developer ID Application: …'
export INSTALLER_SIGN_IDENTITY='Developer ID Installer: …'
export NOTARY_PROFILE='sidepulse-notary'
export SIDEPULSE_PERFORMANCE_EVIDENCE="$PWD/performance-evidence.json"
export SIDEPULSE_HARDWARE_CONFIRM=1
export SIDEPULSE_RUN_INSTALLED_UPGRADE=1
./scripts/verify_macos_release.sh
```

Passing portable tests or building an unsigned package does not establish production readiness.
