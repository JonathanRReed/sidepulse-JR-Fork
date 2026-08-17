# Changelog

All notable changes to the JR fork are documented here.

## Unreleased

### Runtime truth and safety

- Added explicit states for not configured, reload required, awaiting first activity, idle, working, needs input, completed, failed, and stale hook sources.
- Kept `SessionStart` as session presence rather than working activity, and added specific Grok guidance when hooks were installed after the current session began.
- Added cross-process hook-event deduplication so repeated native events are written and published once.
- Separated foreground, LaunchAgent, socket-owner, and conflict process states.
- Established collection-time test isolation for HOME, XDG paths, launchd mutations, and real `/Volumes` writes.
- Collapsed the public status-bar facade so only one PyObjC controller subclass remains.

### Devices and menu

- Replaced mount-path identity with a stable hardware key derived from a hashed serial, volume UUID, or disk identifier.
- Added bounded background `diskutil` inventory so AppKit reads only cached device metadata.
- Merge remounts, prune temporary and duplicate remembered devices, and preserve device-specific brightness, calibration, provider pins, and resting glow.
- Normalized product labels so a SidePulse Dot can never become “SidePulse Dot Dot.”
- Grouped physical devices, profiles, and timers under one compact Devices submenu.
- Wrapped provider capacity under one Usage row, removed the permanent Tip row, renamed the explanation panel to Diagnostics, and hide Setup after a healthy completed setup.
- Added bounded actionable warning rows for disconnected or silent agent intake.

### Native provider usage

- Added first-party accounting for ChatGPT/Codex, Claude, Cursor, Devin, Grok, Antigravity, and optional OpenAI API organization usage.
- Added actionable source-health states, explicit provider setup and refresh commands, dynamic model- and feature-scoped quota lanes, exact reset countdowns, and finite deduplicated reset celebrations.
- Added token and model counts, credits, incidents, estimated pricing, cache-savings estimates, and cross-Mac totals.
- Added provider-scoped browser consent and isolated browser-store import, with secrets stored in macOS Keychain.
- Added encrypted, signed cross-Mac usage sync that freshness-selects account quotas and deduplicates machine-local usage events.
- Wired native usage into Finder launch, packaged and source-checkout LaunchAgents, foreground development mode, the menu, and Usage Center.

### Settings and Screen Bar

- Consolidated the Settings sidebar into Overview, Agents & Providers, Usage, Devices & Screen Bar, Appearance & Motion, Notifications & Focus, and Advanced & Diagnostics.
- Kept the tested retained panes as child pages rather than duplicating their controls or creating another controller layer.
- Added a native Usage page with direct Usage Center and refresh actions.
- Replaced the hairline/full-width Screen Bar treatments with a centered, rounded 6-point luminous band bounded to 180–420 points on wide surfaces.
- Kept connected-but-silent visible as a dim outline, preserved production animation colors, and made Alcove corner brackets an explicit style instead of an automatic side effect.
- Removed temporary repository write-probe documents and migrated retired CodexBar settings out of the integration document.

### Production hardening

- Moved routine battery collection, transcript discovery, provider probing, ledger publication, and webhook delivery behind bounded background services.
- Added typed refresh admission and in-process performance diagnostics while retaining the historical AppKit controller as a compatibility host.
- Added one presentation-safety compiler for visible LED output and exact final-byte validation through the packaged firmware parser before physical writes.
- Made device settings persistence lossless and settings documents versioned, downgrade-safe, concurrency-aware, and preserving of unknown fields.
- Added a payload-only macOS package transaction, inside-out signing checks, uninstall support, dependency constraints, SBOM generation, release manifests, and an authoritative signed macOS release gate.
- Added repository governance, dependency review, self-hosted macOS verification, and architecture ratchets that prevent the retained monoliths from growing.

### External compatibility

- T3 Code remains the only optional external agent integration. It reads a query-only local SQLite projection and does not mutate T3 or read its credentials.
- Alcove remains the optional visual geometry integration for Screen Bar following.
- Removed the accidental CodexBar client, process supervisor, dashboard protocol, commands, settings surface, compatibility entry, and package tests. CodexBar is now only an engineering reference for native SidePulse provider accounting.
- Kept T3 pull-request metadata and mutation actions explicitly out of current claims because the reviewed local projection does not expose them.

## 0.2.2

- Added the JR fork’s agent status, Screen Bar, signal, quota, history, device, and macOS integration work.
- Preserved the upstream SidePulse CLI, LED format, battery tools, and device behavior.
