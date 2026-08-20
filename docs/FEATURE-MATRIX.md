# SidePulse feature and readiness matrix

Updated: 2026-08-20

This document is the status authority for product claims. A feature is **shipped** only when it is reachable from the installed application and covered at its source-to-effect seam. A feature is **release-verified** only after the signed macOS release gate has passed for that exact commit.

| Capability | Implementation status | Default | Release evidence required |
| --- | --- | --- | --- |
| Claude subscription usage via Claude Code OAuth (5-hour / weekly / model-scoped lanes) | Shipped (0.3.0; parser reads the endpoint's `utilization` field) | On once connected | Local gate |
| Usage lane meters, pace verdicts, and reset countdowns in the menu and Usage Center | Shipped (0.3.0) | On | Local gate |
| Usage menu curation (per-element switches, per-provider visibility) | Shipped (0.3.0) | All on | Local gate |
| Tightest-limit percent beside the menu-bar icon, active-provider aware, pace-colored | Shipped (0.3.0) | On | Local gate |
| Contained classic Screen Bar (paints only inside the measured notch silhouette) | Shipped (0.3.0) | On | Local gate |
| Keep-awake holds only while agents work + one grace window; caffeinate -ims | Shipped (0.3.0) | On | Local gate |
| Screen Bar quota ember (left tip brightens below provider threshold) | Shipped (0.3.0) | Off (gauges switch) | Local gate |
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
| Signed `.pkg`, payload-only installation, explicit first-run setup, LaunchAgent, helper setup, uninstall, notarization, and stapling | Implemented, release-gated | N/A | `scripts/verify_macos_release.sh` |
| Built-in timing diagnostics and typed refresh admission | Implemented in production hardening branch | On | Instruments trace and performance budgets |
| CodexBar dashboard-v1 usage, quota, account-display, cost, credit, and provider-health bridge | Implemented, release-gated | Off | Exact dashboard schema, bounded process/HTTP transport, redacted-default identity, no credential duplication, installed CLI smoke test |
| T3 Code local thread, project, provider instance, model, branch, worktree, session-status, and actionable-request compatibility | Implemented, release-gated | Off | Query-only SQLite projection, required-column contract, identity preservation, stale-last-known-good behavior, installed CLI smoke test |
| T3 Code pull-request metadata or mutation actions | Planned production wave | Off | A supported upstream projection or protocol, target identity, stale-version fallback, and mutation authorization |
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

The package installs only the signed application payload and an owned CLI link. It deliberately does not mutate provider hooks, LaunchAgents, privileged helpers, eject-guard services, T3 Code state, or CodexBar credentials from `postinstall`. Those integrations are applied through SidePulse’s reviewed first-run setup or explicit CLI commands.

T3 Code and CodexBar are opt-in. T3 is read-only and queries the documented local projection database. CodexBar remains the sole credential and provider-accounting owner; SidePulse reads its documented dashboard-v1 display snapshot through a bounded one-shot command or a supervised loopback-only child.

That release gate is intentionally separate from merge readiness because ordinary review environments cannot provide the owner’s SidePulse hardware, Developer ID identities, notarization profile, installed settings history, TCC database, or Instruments trace.
