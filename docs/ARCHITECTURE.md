# SidePulse Architecture

This document records the boundaries, state owners, and invariants that are expensive to rediscover. Read it before changing the status-bar runtime, the display pipeline, or packaging.

## Product

SidePulse is a macOS menu-bar application and command-line tool that turns AI-agent activity into ambient light. It targets physical SidePulse LED devices mounted as USB volumes and an on-screen Screen Bar around the MacBook notch. Agent state shares the signal pipeline with notifications, calendar events, reminders, severe weather, battery state, timers, quota information, and user-authored LED programs.

## Controller boundary

The historical AppKit controller grew beyond 18,000 lines. It is retained in `status_bar_legacy.py` because a large mechanical rewrite would put working macOS behavior, permissions, timers, and device control at unnecessary risk.

`status_bar.py` is now the public compatibility facade. It preserves the existing import and monkeypatch contract, delegates the application entrypoint to the retained runtime, and replaces selected controller methods with implementations extracted into small modules. New deterministic behavior must not be added to `status_bar_legacy.py`. Extract it, test it without AppKit, then wire it through the facade.

## Domain map

| Domain | Module(s) | State or responsibility |
| --- | --- | --- |
| Public runtime boundary | `status_bar.py` | Stable import surface, direct-module entrypoint, compatibility forwarding, narrow runtime patches |
| Historical AppKit runtime | `status_bar_legacy.py` | Window and menu lifecycle, timers, watchers, worker coordination, precedence integration, application assembly |
| Per-device projection | `device_projection.py`, `attention.py` | Canonical main/worker split, provider pin filtering, provider-local worker representative, lifecycle priority |
| Event ingestion | `ipc.py`, `hook.py`, `hook_entry.py`, `collector.py` | Hook events over a Unix socket, transcript fallback scanning, status collection, warm-start state |
| Compatibility entrypoints | `agent_monitor/`, `sidepulse_cli/` | Delegation for old installed hook module names; fail-open when arguments are missing |
| Canonical operator semantics | `operator_state.py`, `provider_facts.py`, `attention.py`, `mailbox.py` | Work identity, requests, transitions, parent/worker relationships, actionable attention |
| Signals and presentation | `signals.py`, `signal_coordinator.py`, `presentation_policy.py`, `presentation_scheduler.py` | Semantic precedence, finite cues, continuous state, interruption policy, schedule decisions |
| Rendering | `led_status.py`, `colors.py`, `animation.py`, `render_policy.py` | LED programs, colors, transfer functions, motion, frame cadence, brightness, calibration |
| Screen Bar | `virtual_device.py`, `screen_bar_pipeline.py`, `alcove_observation.py` | Notch geometry, Alcove observation, frame scheduling, draw safety, on-screen rendering |
| Device I/O | `device_writer.py`, `sd_eject_guard_launch.py` | Discovery, size validation, atomic program writes, eject protection |
| Firmware grammar | `led_wasm.py`, packaged `sdled.wasm` | Authoritative LED parser and animation stepping |
| Usage and capacity | `usage_stats.py`, `provider_capacity.py`, `capacity_*` modules | Local usage aggregation, provider evidence, authority gates, forecasts, history, reset handling |
| Persistence | `settings.py`, `*_store.py`, `private_io.py` | Settings, ledgers, histories, atomic private-file writes, recovery from corrupt data |
| Packaging and launch | `app_bundle.py`, `status_bar_launch.py`, `packaging/` | Sealed app bundle, launch agent, signing, verification, installer and notarization |

## Display pipeline

```text
provider hook or fallback scan
  -> collector / canonical operator state
  -> AttentionProjection
  -> provider/device projection
  -> signal and presentation resolver
  -> LED or Screen Bar program
  -> brightness and surface transfer
  -> atomic LED write and/or change-gated Screen Bar frame
```

Actionable attention is global and deliberately bypasses provider pins. Stable lifecycle rows follow a device pin. Main agents remain visible as individual rows. When a provider has only background workers, exactly one urgent worker represents that provider's background crowd. The canonical worker set must never be copied into `visible_rows`; `AttentionProjection.__post_init__` demotes workers and would otherwise duplicate them.

The persistent-signal precedence remains first-claim-wins. Test and escalation signals outrank weather, battery, notifications, completion, reminders, calendar, timer, Studio, and ordinary agent state. New signals must enter through the shared presentation and scheduling layers instead of bypassing them from a UI callback.

## Invariants

- Never emit `N:off` in an indexed LED DSL segment. Use `#000000`. The firmware parser treats the former as an error.
- `validate_led_text` validates size, not grammar. User-authored programs must pass through `SdLedWasmController.parse()`.
- `NSColorWell` is not used in this PyObjC host. Use swatches and the classic `NSColorPanel` route.
- Screen Bar geometry is derived from measured screen pixels. Alcove windows are observations, not authoritative notch geometry.
- Settings and private state writes are atomic, uniquely named, permission-restricted, and recoverable. Two writers must never share one scratch path.
- TCC grants belong to the sealed application identity. Ad-hoc or differently signed builds are different applications and lose permission continuity.
- Background watchers fail quietly, back off, and always release their in-flight state.
- Hook entrypoints fail open. A stale compatibility command may lose one update; it must never block the user's agent session.
- Physical-device writes are isolated from tests. Controller tests must replace settings, latest-state paths, and device discovery before construction.
- A requested value, an assumed value, and the value delivered by AppKit or hardware must be reconciled. Frame rate, window geometry, signing identity, and provider evidence all follow this rule.

## Verification and release

The authoritative gate is `./scripts/verify.sh` on macOS. It installs the fork in an isolated development environment, runs Ruff, validates versions, executes the complete test suite, builds distributions, checks metadata, and installs the wheel into a fresh virtual environment. `./scripts/verify.sh --portable` runs the platform-neutral rescue gate elsewhere.

GitHub Actions are manual-only while hosted minutes are unavailable. A release is created locally from the owner's Mac through `scripts/publish_release.sh`. The script requires a clean `main`, matching source/package/changelog versions, complete verification, Developer ID signing, notarization, checksums, and a GitHub Release. This fork does not automatically publish the upstream-owned `sidepulse` project name to PyPI.

## Deliberate debt

- `status_bar_legacy.py` remains large. Extract one pure decision boundary at a time, with regression tests and a facade wiring change in the same commit.
- Existing file-specific Ruff exceptions document inherited ordering debt. Do not add new exceptions for extracted modules.
- The complete AppKit, TCC, signed-package, and physical-hardware gates require macOS. A portable pass is necessary but not sufficient for release.
- Upstream changes are reviewed behavior by behavior. Do not merge the upstream controller wholesale into the divergent fork.
