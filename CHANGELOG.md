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
