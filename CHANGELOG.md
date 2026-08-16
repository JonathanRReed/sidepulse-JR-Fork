# Changelog

All notable changes to the JR fork are documented here.

## Unreleased

### Production hardening

- Moved routine battery collection, transcript discovery, provider probing, ledger publication, and webhook delivery behind bounded background services.
- Added typed refresh admission and in-process performance diagnostics while retaining the historical AppKit controller as a compatibility host.
- Added one presentation-safety compiler for visible LED output and exact final-byte validation through the packaged firmware parser before physical writes.
- Made device settings persistence lossless and settings documents versioned, downgrade-safe, concurrency-aware, and preserving of unknown fields.
- Added a payload-only macOS package transaction, inside-out signing checks, uninstall support, dependency constraints, SBOM generation, release manifests, and an authoritative signed macOS release gate.
- Added repository governance, dependency review, self-hosted macOS verification, and architecture ratchets that prevent the retained monoliths from growing.

### External compatibility

- Added opt-in T3 Code compatibility through its query-only local SQLite projection. SidePulse preserves provider, provider instance, thread, project, model, branch, worktree, lifecycle, and actionable-request identity without reading credentials or mutating T3.
- Added opt-in CodexBar dashboard-v1 compatibility for provider usage windows, account-display rows, costs, credits, health, and errors. CodexBar remains the sole credential and accounting owner.
- Added supervised loopback CodexBar mode with an ephemeral environment token, strict process/HTTP/JSON bounds, redacted identity by default, and a one-shot dashboard fallback.
- Added versioned integration settings, a packaged compatibility manifest tied to exact reviewed upstream commits, installed-artifact smoke checks, and `sidepulse integrations` configuration and probe commands.
- Kept T3 pull-request metadata and mutation actions explicitly out of current claims because the reviewed local projection does not expose them.

## 0.2.2

- Added the JR fork’s agent status, Screen Bar, signal, quota, history, device, and macOS integration work.
- Preserved the upstream SidePulse CLI, LED format, battery tools, and device behavior.
