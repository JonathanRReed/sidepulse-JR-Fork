# JR-BAR feature and readiness matrix

Updated: 2026-08-26 (0.5.0 coalescence)

JR-BAR (formerly SidePulse) is the product's display name going forward; bundle
identifiers, file paths, and the `sidepulse` CLI keep the old name for now.
This document is the status authority for product claims, rewritten today from
the live source rather than patched. A feature is **shipped** only when it is
reachable from the installed application and covered at its source-to-effect
seam. A feature is **release-verified** only after the signed macOS release
gate has passed for that exact commit.

## Agents and intake

| Capability | Implementation status | Default |
| --- | --- | --- |
| Lifecycle intake for Codex, Claude, Devin, Grok, Cursor, Hermes, OpenClaw, OpenCode, Antigravity, and Kiro (`PROVIDER_SPECS`, `providers.py`) | Shipped | Provider-specific setup |
| Hook installation probe-runs the hook command before writing any provider config, and refuses with a clear error if it cannot run (`install.py`) | Shipped (2026-08-26) | Always |
| Canonical operator state: requests, workers, acknowledgements, freshness, precedence | Shipped | On |
| Triage acknowledgements prune only on established terminal canonical request truth (`local_triage.py`) | Shipped (2026-08-26) | On |
| Snooze quiets every surface — LEDs, notifications, menu — while a genuine ask still breaks through; the Agent Browser deliberately keeps showing everything | Shipped (0.4.0) | Per-session action |
| "Snooze Until Tomorrow Morning" resolves to 9 AM local through the store resolver (`mailbox_preference_store.py`) | Shipped (2026-08-26) | Per-session action |
| Agent Browser window answers Return (open), Escape (close), Cmd-F (search), and arrow keys | Shipped (2026-08-26) | On |

## Menu bar and windows

| Capability | Implementation status | Default |
| --- | --- | --- |
| Menu-bar status and session navigation (remembered per-provider openers) | Shipped | On |
| Menus rebuild only on content change (`menu_tracking.py` plans no-change / patch-in-place / defer-rebuild; the old 30-second rebuild valve is gone) | Shipped (2026-08-26) | On |
| Hidden main menu makes Cmd-C/V/W/Z/Q work in every app window (`main_menu.py`) | Shipped (2026-08-26) | On |
| Background polls and settings previews defer past scroll gestures (default-run-loop-mode timers) | Shipped (2026-08-26) | On |
| Settings: seven-category navigation, per-element usage-menu curation, per-provider visibility | Shipped (0.3.0) | All on |
| Debug pane shows the settings-file path; safe diagnostic export lives in the History pane (the status-audit CSV/HTML export plane was deleted 2026-08-26) | Shipped | On |

## Light surfaces

| Capability | Implementation status | Default |
| --- | --- | --- |
| Physical SidePulse Pro and Dot output (atomic `LEDS.LED` writes through the safety compiler and firmware parser) | Shipped | On when connected |
| Screen Bar; contained classic mode paints only inside the measured notch silhouette (draw bodies live in `screen_bar_runtime.py` since 2026-08-26) | Shipped | Off until enabled |
| Screen Bar quota ember (left tip brightens below provider threshold) | Shipped (0.3.0) | Off (gauges switch) |
| Color palettes, blend modes, provider identity, per-device brightness and calibration | Shipped | Reviewed defaults |
| 18-motion vocabulary (`PROVIDER_ANIMATION_CHOICES`), including the 2026-08-26 sourced KITT, Gradient, Marquee, and Duotone; previews route through the real solo renderer | Shipped (0.4.0) | Automatic |
| Charging trickle while idle (wattage-paced, yields to any agent claim and pinned displays) | Shipped (0.4.0) | On |
| Night warmth and optional night dim (7 PM–7 AM), composed with the ambient stack | Shipped | Warmth on, dim off |
| Lid animations (preset Lid Closed / Lid Open programs, brightness-composed) | Shipped | Presets |
| Timer/timebox display with draining fill and chime | Shipped | Off |
| Studio: hand-written LED programs, saved library, `INIT.LED` power-up burn | Shipped | Off |
| Signal engine for asks, failures, completions, low battery, reminders, calendar, weather | Shipped, per-feature opt-ins | Mixed |
| Ask escalation: menu-bar emphasis, optional sound, notification, HTTPS webhook | Shipped | Conservative |

## Power

| Capability | Implementation status | Default |
| --- | --- | --- |
| Keep-awake holds only while agents work plus one grace window (`caffeinate -ims`) | Shipped (0.3.0) | On |
| Closed-lid awake policy through the narrow `pmset` sudoers sleep helper | Shipped | Off |

## Usage and quota

| Capability | Implementation status | Default |
| --- | --- | --- |
| Claude subscription usage via Claude Code OAuth (5-hour / weekly / model-scoped lanes; parser reads the endpoint's `utilization` field) | Shipped (0.3.0) | On once connected |
| Usage lane meters, pace verdicts, and reset countdowns in the menu and Usage Center | Shipped (0.3.0) | On |
| Tightest-limit percent beside the menu-bar icon, active-provider aware, pace-colored | Shipped (0.3.0) | On |
| Native accounting for ChatGPT/Codex, Claude, Cursor, Devin, Grok, Antigravity, and optional OpenAI API org usage | Shipped | Provider-specific setup |
| Browser-session import for provider auth, behind per-provider consent, secrets in Keychain | Shipped | Off |
| Reconnect truth model: signed-out providers watch their own credential file, transient failures ride an exponential ladder, reconnect buttons probe before claiming success | Shipped (0.4.0) | On |
| Quota alerts switch (reset blink, pace notifications, threshold effects, connection cues) and finite reset celebrations | Shipped (0.4.0) | Alerts switch in Extras |
| Quota Runway LED display, fed from the usage plane's own gated lanes (worst remaining lane, provider-colored) | Shipped (0.4.0) | Selectable per device |
| Local usage and cost summaries with priced-coverage disclosure ("NN% of tokens priced") | Shipped | Transcript-scan opt-ins apply |
| Codex capacity windows through the capacity authority | Shipped | On where evidence exists |
| Claude subscription capacity windows behind explicit credential consent | Shipped | Off |
| Capacity history behind explicit retention consent | Shipped | Off |
| Operator history behind explicit retention consent | Shipped | Off |

## Multi-Mac and integrations

| Capability | Implementation status | Default |
| --- | --- | --- |
| Tailscale/SFTP multi-Mac ledger, read-only | Shipped | Off |
| Cross-Mac usage sync: HMAC-SHA256-signed JSON over SSH (not encrypted; transport privacy comes from SSH), bounded replay window, totals render in the Usage Center | Shipped (0.4.0) | Off |
| Loopback cloud-agent ingest | Shipped | Off |
| `sidepulse serve` — agent and usage state as JSON on loopback (Stream Deck, Waybar, scripts) | Shipped | Manual |
| Calendar and Reminders glows | Shipped | Off |
| Severe weather | Shipped | Off |
| T3 Code local-state compatibility (query-only SQLite projection, no mutation, no credentials) | Shipped, opt-in | Off |

## Packaging and diagnostics

| Capability | Implementation status | Default |
| --- | --- | --- |
| Signed `.pkg` via `packaging/build_macos_pkg.sh`: payload-only installation, explicit first-run setup, LaunchAgent, helper setup, uninstall, notarization, stapling | Implemented, release-gated | N/A |
| Built-in timing diagnostics and typed refresh admission | Shipped | On |

## Removed planes (deleted 2026-08-26, the 0.5.0 coalescence)

These are not features and must not be claimed anywhere: the
delivery-planning plane (`plan_interruptions`, the quiet plane, the delivery
ledger), `runtime_truth`, the runtime-install transaction in `install.py`, the
quota-forecast plane (`capacity_forecast`, `capacity_calibration`, the
forecast release authority), the in-view Screen Bar draw bodies (replaced by
`screen_bar_runtime`), the status-audit event log and its CSV/HTML exporters,
`AgentLayoutStabilizer`, `DeferredMenuPublication`, the `app_bundle`
development-wrapper builder, `LID_ANIMATION_CHOICES`, and the
`closed_lid_system_override` / `local_activity_history_enabled` settings
dials. The CodexBar dashboard bridge was already removed in 0.3.0; CodexBar
remains an engineering reference only.

## Readiness labels

- **Shipped:** reachable in the installed application and covered at the production seam.
- **Implemented, release-gated:** code and automated checks exist, but the exact commit still requires the signed Mac, hardware, installation, and performance gate.
- **Release-verified:** a GitHub Release includes the signed package, checksums, SBOM, resolved environment, and release-verification manifest for the exact commit.

## Current release status

Everything above marked Shipped is on `main`. Nothing may be published as a
production release until the owner's release Mac completes:

```bash
./scripts/verify_macos_release.sh
```

The package installs only the signed application payload and an owned CLI
link. It deliberately does not mutate provider hooks, LaunchAgents, privileged
helpers, eject-guard services, or T3 Code state from `postinstall`. Those
integrations are applied through the reviewed first-run setup or explicit CLI
commands.

That release gate is intentionally separate from merge readiness because
ordinary review environments cannot provide the owner's SidePulse hardware,
Developer ID identities, notarization profile, installed settings history,
TCC database, or Instruments trace.
