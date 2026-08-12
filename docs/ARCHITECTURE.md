# SidePulse Architecture

The context document: what the domains are, where state lives, and the
invariants that are expensive to rediscover. Read this before touching
`status_bar.py`.

## What the product is

A macOS menu-bar app that turns AI-agent activity (Claude Code, Codex,
Gemini, and friends) into ambient light: physical SidePulse LED devices
(USB volumes with a `LEDS.LED` program file), plus an on-screen "Screen
Bar" that hugs the MacBook notch and mirrors the same animations.
Signals beyond agents ride the same pipeline: notifications, calendar,
reminders, severe weather, battery, a working timer, and user-authored
Studio programs.

## Domain map

| Domain | Module(s) | State it owns |
| --- | --- | --- |
| Event ingestion | `ipc.py`, `hook.py`, `collector.py` | Hook events over a unix socket; transcript fallback scanning; `LiveAgentMonitor.statuses_by_key` (pruned at 24h); warm-start cache `latest.json` (debounced writes) |
| Status semantics | `collector.py` | `AgentStatus`/`MonitorSnapshot`; fresh vs stale (snapshot ALWAYS carries stale statuses — render layers decide visibility) |
| Signals | `signals.py` | `SignalStyle` (color/pattern/speed/intensity), escalation tiers (a ceiling, not a mode), one-shot vs continuous pattern rules |
| Rendering | `led_status.py`, `colors.py` | `style_to_program()` — the ONE style renderer; mode/agent/identity colors; blend modes; brightness + per-channel calibration gains |
| Device I/O | `device_writer.py` | Discovery (`/Volumes` scan), size validation, atomic program writes |
| Firmware grammar | `led_wasm.py` (+ packaged `sdled.wasm`) | The REAL parser; Screen Bar animation stepping; Studio program validation |
| Screen Bar | `virtual_device.py` | Pixel-measured notch geometry, bracket/wings drawing, ~90ms temporal smoothing, change-gated 60fps redraw |
| Orchestration + UI | `status_bar.py` (the monolith) | The controller: timers, watchers, precedence arbiter, menu, Settings window |
| Persistence | `settings.py` | `AgentMonitorSettings`, frozen-dataclass `with_*` mutators, atomic unique-scratch saves |
| Packaging | `app_bundle.py`, `status_bar_launch.py` | The sealed `SidePulse.app` + launchd agent |

## The display pipeline (per refresh)

```
hook event / 15s timer
  → LiveAgentMonitor.snapshot()
  → for each device: active_led_display_kind_for_device()   # precedence arbiter
  → signal_display_entries()[kind]                          # program factory table
  → style_to_program() / timer_fill_program() / studio program
  → apply brightness → apply channel gains → write LEDS.LED
  → Screen Bar phase-locks to the physical write completion
```

**Precedence** (first claim wins): test > escalation takeover > weather >
low battery > notification > completion > reminders > calendar >
battery > timer > studio > agent. Adding a persistent signal is one row
in `signal_display_entries()` plus one claim in
`active_led_display_kind_for_device()`.

## Invariants that were paid for in blood

- **Never emit `N:off` in an indexed DSL segment.** The firmware parse
  error strobes red and kills the program. Always `#000000`. This
  regressed twice.
- **`validate_led_text` is only a size check.** Real grammar validation
  is `SdLedWasmController.parse()` — Studio uses it; anything else that
  accepts user-authored programs must too.
- **NSColorWell is banned.** Its SwiftUI backing segfaults in this
  PyObjC host. Color pickers are swatch rows + the classic
  NSColorPanel with exclusive routing.
- **Never size the Screen Bar from Alcove's window.** Pixel-measure the
  notch (`measured_notch_bounds`). Tried twice, reverted twice.
- **Settings writes must be atomic AND uniquely named.** Two writers
  (LED worker + main thread) once shared a scratch file; a truncated
  `settings.json` loads as all-defaults.
- **TCC/FDA is bound to the sealed app bundle.** Any `Info.plist`
  change re-signs the bundle and macOS silently drops Full Disk
  Access; batch usage-key changes.
- **Weather uses IP geolocation, never CoreLocation** — a Location
  prompt would mean another `Info.plist` key and another lost FDA
  grant.
- **Watchers fail quietly.** Notification/calendar/reminders/weather
  raise their own `*UnavailableError`, callers back off; a worker
  thread must ALWAYS post its completion payload or its in-flight flag
  strands.

## Testing rules

- Every controller-building test goes through `isolate_controller()`
  (fakes settings paths, `latest.json`, and device discovery BEFORE
  construction). An un-isolated `refresh_()` once wrote an LED program
  to the developer's real mounted device mid-test-run.
- Gate: `python -m pytest tests/ -q` (exit code checked directly — a
  `| tail` pipe once swallowed a red run), then `ruff check src tests`
  (config pinned in `pyproject.toml`).
- Verify UI work rendered, not just built: window-ID `screencapture`
  and AX-driven dropdown clicks; the settings window is drivable
  headlessly.

## Known debt (deliberate)

- `status_bar.py` is ~8k lines. The extraction plan (menu builder,
  settings panes, widget helpers, formatters — all already
  `target`-parameterized module functions) is mechanical but touches
  test monkeypatching semantics; do it as its own dedicated wave, one
  module per commit.
- Open Signal API (external processes claiming a signal slot) is the
  approved next feature.
- Per-agent mode-animation overrides and session stats are explicitly
  deferred (see `docs/FORK-ROADMAP.md`).
