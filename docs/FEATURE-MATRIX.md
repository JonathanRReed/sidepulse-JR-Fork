# SidePulse feature and readiness matrix

Updated: 2026-08-15

This document is the status authority for product claims. A feature is **shipped** only when it is reachable from the installed application and covered at its source-to-effect seam. A feature is **release-verified** only after the signed macOS release gate has passed for that exact commit.

| Capability | Implementation status | Default | Release evidence required |
| --- | --- | --- | --- |
| Claude, Codex, Devin, Grok, Cursor, Hermes, OpenClaw, OpenCode, and Antigravity lifecycle intake | Shipped | Provider-specific setup | Hook preservation, event-to-canonical tests, installed hook smoke test |
| Canonical operator state, requests, workers, acknowledgements, freshness, and precedence | Shipped | On | Reducer, restoration, clock-continuity, and source-authority tests |
| Menu-bar status and session navigation | Shipped | On | AppKit suite, navigation allowlist, menu latency P95 |
| Physical SidePulse Pro and Dot output | Shipped | On when connected | Exact firmware parser, reversible physical writes, readback, disconnect tests |
| Screen Bar | Shipped | Off until enabled | AppKit/Quartz tests, Reduce Motion, multi-display, sleep/wake, CPU budget |
| Color palettes, blend modes, provider identity, per-device brightness and calibration | Shipped | Reviewed defaults | Cross-surface render tests and settings round trips |
| Signal engine for asks, failures, completions, low battery, reminders, calendar, weather, and notifications | Shipped with per-feature opt-ins | Mixed | Universal safety compiler and exact precedence tests |
| Ask escalation, menu-bar emphasis, optional sound, notification, and webhook | Shipped | Conservative | Focus/quiet policy, HTTPS webhook, action-token, and cadence tests |
| Local usage and cost summaries | Shipped | Local activity opt-in rules apply | Deduplication, bounded cache, source-attribution, privacy tests |
| Codex capacity windows | Shipped through capacity authority | On where evidence exists | Declared lanes, freshness, reset continuity, and authority tests |
| Claude subscription capacity windows | Shipped behind explicit credential consent | Off | Keychain-consent, source-health, lane mapping, and account-continuity tests |
| Capacity history | Shipped behind explicit retention consent | Off | Retention, migration, bounds, deletion, and export tests |
| Operator history | Shipped behind explicit retention consent | Off | Metadata-only schema, retention, local export, and privacy tests |
| Tailscale/SFTP multi-Mac ledger | Shipped, read-only | Off | No-remote-command, bounded transfer, circuit-breaker, and machine-consent tests |
| Loopback cloud-agent ingest | Shipped | Off | Loopback, bearer token, DNS/browser checks, rate/session/queue bounds |
| Calendar and Reminders | Shipped | Off | TCC permission, timeout, stale-result, and no-private-title tests |
| Severe weather | Shipped | Off | Bounded JSON, location freshness, US-only disclosure, and retry tests |
| Closed-lid awake policy | Shipped | Off | Privileged-helper install/uninstall, process cleanup, and sleep/wake tests |
| Signed `.pkg`, LaunchAgent, helper setup, uninstall, notarization, and stapling | Implemented, release-gated | N/A | `scripts/verify_macos_release.sh` |
| Built-in timing diagnostics and typed refresh admission | Implemented in production hardening branch | On | Instruments trace and performance budgets |
| CodexBar structured usage bridge | Planned production wave | Off | Protocol/version negotiation, source freshness, no credential duplication |
| T3 Code thread, worktree, branch, provider, and pull-request compatibility | Planned production wave | Off | T3 protocol contract, identity preservation, stale-version fallback |
| Native SwiftUI/AppKit Glance and Command Center | Planned migration wave | Off | Parity, TCC continuity, accessibility, installed-upgrade, performance |
| Animation Studio 2 layered composer and recipe system | Planned production wave | Off | Cross-surface compiler, accessibility previews, flash safety, migration |
| Signed helper process and versioned local core protocol | Planned migration wave | Off | Authentication, framing, backpressure, crash recovery, version negotiation |

## Readiness labels

- **Shipped:** reachable in the installed application and covered at the production seam.
- **Implemented, release-gated:** code and automated checks exist, but the exact commit still requires the signed Mac, hardware, installation, and performance gate.
- **Planned production wave:** approved design exists; the capability must not be represented as currently available.
- **Release-verified:** a GitHub Release includes the signed package, checksums, SBOM, resolved environment, and release-verification manifest for the exact commit.

## Current release status

The production-hardening branch may be merged after its code review and portable/macOS test requirements pass. It must not be published as a production release until the owner’s release Mac completes:

```bash
./scripts/verify_macos_release.sh
```

That gate is intentionally separate from merge readiness because this environment cannot provide the owner’s SidePulse hardware, Developer ID identities, notarization profile, installed settings history, TCC database, or Instruments trace.
