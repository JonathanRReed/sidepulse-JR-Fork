# JR Bar feature and readiness matrix

Updated 2026-08-31.

A local 0.6.0 app candidate is signed, notarized, stapled, Gatekeeper-accepted,
installed as matching app-tree bytes, and physically smoke-tested on one
connected SidePulse device. That does not make the release verified. The outer
PKG is unsigned and has no receipt, and installed UI, accessibility, Screen Bar
Instruments, Dot, two-candidate updater, and publication gates remain open.

Real-hardware Screen Bar profiling has a source-complete evidence contract for
static, working, asking, multi-agent, DND, low-power, and hidden scenarios. A
completed performance matrix still requires separately observed raw Instruments
traces for every scenario.

The single existing JavaScriptCore batch path is command-scoped and
finite-horizon-aware. It does not reuse prefetched frames across generation,
program, or cadence changes, and it reports invalidated and shortened work
separately from renderer fallbacks. This is source-verified behavior, not a
claim that the 24-frame ceiling is hardware-optimal.

JR Bar (formerly SidePulse) is the product's display name going forward; bundle
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
| Ordered hook admission: standard-library client, private same-user socket, one bounded FIFO worker, explicit content-free refusal receipts, synchronous no-listener fallback, and drain-on-normal-shutdown | Implemented, release-gated (Unreleased) | Automatic while app is running |
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
| Configurable global Reveal Current Ask action; visible menu command, Overview shortcut recorder, local conflict and refusal status, transactional persistence, and Agent Browser fallback | Implemented and source-verified (Unreleased) | Unassigned until the user records a shortcut |
| Manual and scheduled DND; separate Mute, Dim, Pause, Asks Only, and Fully Dark presentation policy; one daily local-time schedule; temporary Resume; public coarse macOS Focus following; exact return time; native Settings card and compact menu | Implemented and source-verified (Unreleased) | Schedule and Follow macOS Focus off |
| Debug pane shows the settings-file path; safe diagnostic export lives in the History pane (the status-audit CSV/HTML export plane was deleted 2026-08-26) | Shipped | On |

## Light surfaces

| Capability | Implementation status | Default |
| --- | --- | --- |
| Physical SidePulse Pro and Dot output (atomic `LEDS.LED` writes through the safety compiler and firmware parser) | Shipped | On when connected |
| Priority-aware physical write queue: obsolete frames coalesce while asks, failures, finite cues, explicit calibration previews, and final trailing state retain separate bounded slots | Implemented, release-gated (Unreleased) | Automatic |
| Screen Bar; contained classic mode paints only inside the measured notch silhouette (draw bodies live in `screen_bar_runtime.py` since 2026-08-26) | Shipped | Off until enabled |
| Alcove following confidence ladder; typed seven-state Settings, Doctor, Screen Bar geometry, accessibility, and bounded recovery motion | Implemented and source-verified (Unreleased) | Off until enabled |
| Multi-alert Screen Bar announcer stack; passive single-ask collapsed pill, truthful multi-ask count, stable first-seen order, expanded native keyboard traversal, and Screen-Bar-local Mark Seen receipts | Implemented and source-verified (Unreleased) | Automatic when actionable asks exist |
| Answer-in-place controls; capability-gated reply, send, retry, cancel, timeout, and Jump fallback in the expanded Screen Bar and Agent Browser | Implemented and source-verified (Unreleased) | Automatic only when a reviewed answering surface is declared |
| Screen Bar quota ember (left tip brightens below provider threshold) | Shipped (0.3.0) | Off (gauges switch) |
| Color palettes, blend modes, provider identity, per-device brightness and calibration | Shipped | Reviewed defaults |
| 18-motion vocabulary (`PROVIDER_ANIMATION_CHOICES`), including the 2026-08-26 sourced KITT, Gradient, Marquee, and Duotone; previews route through the real solo renderer | Shipped (0.4.0) | Automatic |
| Charging trickle while idle (wattage-paced, yields to any agent claim and pinned displays) | Shipped (0.4.0) | On |
| Night warmth and optional night dim (7 PM–7 AM), composed with the ambient stack | Shipped | Off |
| Lid animations (preset Lid Closed / Lid Open programs, brightness-composed) | Shipped | Presets |
| Timer/timebox display with draining fill and chime | Shipped | Off |
| Studio: hand-written LED programs, saved library, `INIT.LED` power-up burn | Shipped | Off |
| Signal engine for asks, failures, completions, low battery, reminders, calendar, weather | Shipped, per-feature opt-ins | Mixed |
| Ask escalation: menu-bar emphasis, optional sound, notification, HTTPS webhook | Shipped | Conservative |

## Power

| Capability | Implementation status | Default |
| --- | --- | --- |
| Ordinary agent hold prevents automatic system sleep only while agents work plus one release delay (`caffeinate -ims`) | Implemented, release-gated (Unreleased) | On |
| Optional display assertion during ordinary or closed-lid holds (`caffeinate -d`) | Implemented, release-gated (Unreleased) | Off |
| Independent battery policy with low-battery release | Shipped | Continue on battery |
| Independent closed-lid policy through the narrow `pmset` sudoers sleep helper | Shipped | Never |

## Usage and quota

| Capability | Implementation status | Default |
| --- | --- | --- |
| Claude subscription usage via Claude Code OAuth (5-hour / weekly / model-scoped lanes; parser reads the endpoint's `utilization` field) | Shipped (0.3.0) | On once connected |
| Usage lane meters, pace verdicts, and reset countdowns in the menu and Usage Center | Shipped (0.3.0) | On |
| Tightest-limit percent beside the menu-bar icon, active-provider aware, pace-colored | Shipped (0.3.0) | On |
| Native accounting for ChatGPT/Codex, Claude, Cursor, Devin, Grok, Antigravity, and optional OpenAI API org usage | Shipped | Provider-specific setup |
| Browser-session import for provider auth, behind per-provider consent, secrets in Keychain | Shipped | Off |
| Reconnect truth model: signed-out providers watch their own credential file, transient failures ride an exponential ladder, reconnect buttons probe before claiming success | Shipped (0.4.0) | On |
| Quota alerts switch (reset blink, pace notifications, threshold effects, connection cues) | Shipped (0.4.0) | Off (switch in Extras) |
| Reset celebrations: finite confetti sweep plus one notification per refill, courtesy-budget gated (deliberately not behind the alerts switch) | Shipped (0.4.0) | On |
| Quota Runway LED display, fed from the usage plane's own gated lanes (worst remaining lane, provider-colored) | Shipped (0.4.0) | Selectable per device |
| Local usage and cost summaries with priced-coverage disclosure ("NN% of tokens priced") | Shipped | Transcript-scan opt-ins apply |
| Codex capacity windows through the capacity authority | Shipped | On where evidence exists |
| Claude subscription capacity windows behind explicit credential consent | Shipped | Off |
| Capacity history behind explicit retention consent | Shipped | Off |
| Operator history behind explicit retention consent | Shipped | Off |
| Serial, bounded history and reset-state persistence with content-free receipts, retryable failed usage appends, consent fencing, and drain-on-normal-shutdown | Shipped (Unreleased) | Automatic |

## Multi-Mac and integrations

| Capability | Implementation status | Default |
| --- | --- | --- |
| Tailscale/SFTP multi-Mac ledger, read-only | Shipped | Off |
| Cross-Mac usage sync: HMAC-SHA256-signed JSON over SSH (not encrypted; transport privacy comes from SSH), bounded replay window, totals render in the Usage Center | Shipped (0.4.0) | Off |
| Memory-only steady-state Usage Center, menu, and settings-summary projection; settings, Keychain, and cached sync documents refresh on the provider worker | Shipped (Unreleased) | Automatic |
| Loopback cloud-agent ingest | Shipped | Off |
| `sidepulse serve` - schema-v2 redacted agent aggregates and provider quota summaries on loopback (Stream Deck, Waybar, scripts) | Shipped | Manual |
| Calendar and Reminders glows | Shipped | Off |
| Severe weather | Shipped | Off |
| T3 Code local-state compatibility (query-only SQLite projection, no mutation, no credentials) | Shipped, opt-in | Off |

## Packaging and diagnostics

| Capability | Implementation status | Default |
| --- | --- | --- |
| Signed `.pkg` via `packaging/build_macos_pkg.sh`: payload-only installation, explicit first-run setup, LaunchAgent, helper setup, uninstall, notarization, stapling | Implemented, release-gated | N/A |
| Executable PKG and supplemental Sparkle ZIP assembly: exact commands, missing-tool and certificate failures, deterministic manifest, candidate-bound hashes, and unsigned-build non-output | Implemented (Unreleased) | Automatic through `make fast` |
| Pinned Sparkle 2.9.6 updater: signed appcast, stable and beta channels, one-day stable rollout, manual check, and consent-owned automatic checks | Implemented and release-gated (Unreleased) | Consent prompt, stable channel |
| Built-in timing diagnostics and typed refresh admission | Shipped | On |
| Nine fixed local-health aggregates in the explanation panel, current-run and memory-only, with explicit unavailable states, bounded DND mode/source/return-time facts, and no cloud transmission | Implemented, release-gated (Unreleased) | On |
| Deep Why this light context: selected semantic and P1-P7 priority, oldest visible source age, bounded current finite-cue suppressions, Scene availability, global surface role, Focus/DND decision plus DND mode/source/return time, Reduce Motion substitution, source-labeled active-output timing, and position-preserving refresh | Implemented, release-gated (Unreleased) | On |
| Fast ordinary-change gate: Ruff, real imports, lightweight contracts, tracked-file secret scan, literal fixture validation, focused contract, fixture, and semantic tests, compilation, dependency/version policy, and diff hygiene | Implemented (Unreleased) | Manual through `make fast` |

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
- **Release-verified:** a GitHub Release includes the signed package,
  checksums, measured performance evidence, SBOM, resolved environment, and a
  validated receipt manifest whose commit, app tree, package contents,
  signatures, notarization, Gatekeeper, upgrade, uninstall, and clean-install
  checks all belong to the exact PKG.

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
