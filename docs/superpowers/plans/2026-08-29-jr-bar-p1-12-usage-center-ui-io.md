# JR Bar P1.12 Usage Center UI I/O Plan

**Status:** Complete in source on 2026-08-29

**Scope:** Remove disk and Keychain work from steady-state Usage Center and usage-summary UI refresh. Preserve initial state restore, explicit settings mutations, background collection, cross-Mac sync truth, edge detection, and recovery behavior.

## Source observations

- `ProviderUsageWindowController.refresh()` calls `cached_merged_sync()` on the AppKit thread. That cache expires after 30 seconds and keys by object identity, so equal replacement states can synchronously read sync settings, Keychain credentials, and cached packet files.
- `applyProviderUsageState_()` always reloads provider settings from disk before threshold, reset, and hook projection.
- `refresh_native_usage_summary()` reopens the same settings document on every summary update to resync cached checkboxes.
- `_usage_menu_settings()` also expires a disk-backed cache every 15 seconds on the presentation path.
- Provider collection, state persistence, and callback delivery already run on the `ProviderUsageService` worker. This tranche should use that boundary instead of creating another worker.
- Initial persisted-state restore and an explicit settings save are not steady-state repaint operations. They remain allowed, bounded I/O boundaries, but their results must feed an immutable in-memory snapshot used afterward.

## Design

1. Split the cross-Mac cache into two operations:
   - `refresh_cached_merged_sync(state)`, worker-only and allowed to read settings, Keychain, and cached packets;
   - `cached_merged_sync(state)`, a pure in-memory lookup keyed by the logical snapshot tuple, not object identity or a TTL.
2. Have `ProviderUsageService` retain the immutable `ProviderUsageSettings` used for its latest completed refresh.
3. Deliver one immutable apply payload from `_provider_usage_ready()` containing the worker-produced state and settings snapshot. Refresh the merged-sync cache before dispatching that payload to AppKit.
4. Make `applyProviderUsageState_()`, `refresh_native_usage_summary()`, cached checkbox reconciliation, and `_usage_menu_settings()` consume the in-memory settings snapshot only.
5. Explicit checkbox actions may still load and save settings. On success they immediately replace the in-memory snapshot and invalidate the menu signature; the next worker refresh remains the external-change reconciliation boundary.
6. Settings-pane construction may perform one initial bounded load only when no worker snapshot exists. Repainting or applying provider state must not reload it.

## Test-first acceptance contract

1. Two equal but distinct `ProviderUsageState` objects reuse the same merged result with no loader, Keychain, file, network, or subprocess call on lookup.
2. A logically different snapshot does not reuse another state’s merge.
3. Merged-sync refresh performs its local I/O on the provider worker before main-thread dispatch.
4. The service publishes the exact immutable settings snapshot used for collection.
5. `applyProviderUsageState_()` has no direct settings loader call and accepts only a state/settings apply payload.
6. `refresh_native_usage_summary()` and checkbox reconciliation perform no disk read after pane construction.
7. Menu presentation consumes the same settings snapshot without a TTL-triggered reload.
8. Initial load, explicit mutation, equal refresh, changed refresh, stale/error state, and recovery behavior remain covered.
9. Focused tests, authoritative Ruff and compilation checks, the canonical gate, and one completed independent review pass with no unresolved correctness, security, or threading finding.

## Non-goals and proof boundary

- No change to provider network collection, credential ownership, sync packet format, SFTP transport, or persistence schema.
- No claim that initial application launch is I/O-free.
- No new production dependency, thread pool, renderer, telemetry sink, or external service.
- Source verification is not installed-AppKit responsiveness evidence; installed UI timing remains a separate later gate.

## Receipts

- Provider callbacks run on the serial `SidePulseProviderUsage` worker and
  refresh cross-Mac merge evidence before dispatching an immutable
  `ProviderUsageApply` payload to AppKit.
- `cached_merged_sync()` is a value-keyed memory lookup. Usage Center,
  settings summary, checkbox repaint, status-title projection, and the native
  dropdown builder do not load provider settings, Keychain credentials, or
  cached sync packets.
- Explicit settings mutations update a revision-fenced in-memory snapshot, so
  an older worker load cannot overwrite the newer UI choice.
- 200 focused provider usage, sync, menu, settings, history, hook, and recovery
  tests passed after the independent-review repair.
- The authoritative gate passed: 6,322 tests and 7 subtests, Ruff, Python
  compilation, dependency policy, 571 tracked-file secret scan, version
  validation, wheel and source-distribution builds, Twine checks, clean-wheel
  install, SBOM generation, and diff validation. Four existing multiprocessing
  fork deprecation warnings remain.
- One independent review found the native dropdown's extracted builder still
  loading settings on AppKit. The builder now consumes `_usage_menu_settings()`
  only, and a direct AST contract prevents the I/O call from returning.
- Installed-AppKit responsiveness and interaction timing were not claimed.
