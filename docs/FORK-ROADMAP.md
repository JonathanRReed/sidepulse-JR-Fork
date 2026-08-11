# SidePulse fork — master plan

> **Status (2026-08-11, end of build day): SHIPPED through Phase 5's
> feasible scope.** Twenty-plus commits on
> `feat/color-customization-and-led-behavior`. Summary of what landed
> vs. what was deferred and why:
>
> **Shipped:** Phase 0 (verified + landed). Phase 1 (System Settings
> form register, centered max-width column, fixed-size windows, instant
> apply everywhere, contextual actions, IA consolidation to 7 panes,
> dropdown tightening, debug-text cleanup). Phase 2 (auto-brightness
> that actually tracks via a 3s watcher; Screen Bar now uses effective
> brightness; per-Focus dim rules with an honest Full-Disk-Access
> call-to-action). Phase 3 (attention takeover — full-bar double flash
> for waiting/blocked agents in every blend mode; display presets
> Calm/Informative/Everything; the blend-mode engine itself predated the
> fork). Phase 4 (guided calibration: reference patches light the device
> through live gains; popover close restores status). Extras: Alcove-
> aware wrap (bracket drawn outside Alcove's overlay — measured, not
> guessed), low-power calm charge reminder, three new providers
> (Cursor / Hermes Agent / OpenClaw, each speaking its real config
> dialect), the Welcome onboarding window with a live LED demo.
>
> **NEXT ARCHITECTURAL STEP — app bundle.** The single highest-leverage
> remaining change: package the launchd process inside a real
> `SidePulse.app` bundle (py2app/briefcase, executable must be the
> actual interpreter binary inside the bundle, not a shell shim — TCC
> attributes file access to the process's real executable). It fixes,
> at once: Full Disk Access appearing as "SidePulse" by name in the
> Privacy list (today the venv `python` symlink resolves into
> Homebrew's Cellar, which no reasonable person can find), plus unlocks
> the EventKit-calendar and notification-observation features that
> require a bundle to even present their permission prompts.
>
> **Deferred, with reasons:**
> - *Calendar alerts (EventKit):* calendar TCC permission requires an
>   app **bundle** with a usage description; the current launchd-bare-
>   python architecture cannot even present the prompt. Path: bundle
>   the app (py2app/briefcase), or a Raycast extension calling the
>   existing `sidepulse` CLI.
> - *Notification blinks:* no public API to observe other apps'
>   notifications; the DB-polling route needs Full Disk Access and is
>   schema-fragile. Revisit if/when the app is bundled.
> - *Weather:* fun but lowest value; needs a display slot design first.
> - *Progress-fill display:* hooks deliver no reliable progress
>   fraction — nothing truthful to render.
> - *Per-device blend override & calibration profiles:* complexity
>   without a demonstrated need yet (single device, single
>   environment). Revisit on demand.
> - *Session-row icon redesign in the dropdown:* the wide composite
>   icons indent session text well past other rows — standard macOS
>   behavior, but worth a slimmer glyph system in a future pass.

Decisions locked 2026-08-11 (grilling session with Jonathan):

- **Fork posture:** quality-first divergence. No cap on how far the UI/UX moves,
  but history stays clean and thematic (upstream could cherry-pick), and the
  Python core stays pull-compatible with `upstream/main`.
- **Stack:** stay PyObjC/AppKit. A SwiftUI rewrite would end merge-friendliness
  with a Python upstream; instead `native_ui.py` becomes a genuinely
  first-party-grade design system.
- **Hardware is first-class:** SidePulse Pro in daily use. The physical LEDs,
  the Screen Bar widget, the menu-bar dropdown, and the Settings/Colors windows
  are ALL primary surfaces.
- **Product vision:** a *universal status indicator* — agent status at the core,
  plus computer signals (battery, calendar, focus, notifications) — deeply
  customizable, but with first-party polish and good defaults.
- **Jonathan's Focus modes** (for per-Focus rules): Do Not Disturb, Work,
  Sleep, School (custom), Personal (custom).

## Phase 0 — Prove & land the in-flight work

The wing-bracket riser + the 4-layer scroll-pane gap fix are implemented and
test-green but were never visually re-verified live.

1. Live screenshot pass (precise window-bounds cropping — see ground rules in
   the session handoff): every Settings pane top-aligned with natural card
   height, no dead gaps; Colors window still scrolls; `|____|` bracket shape
   still renders on the Screen Bar preview.
2. Full tests + `ruff` + reinstall into the venv + restart launchd job + empty
   error log.
3. Commit + push (thematic message: the scroll-pane constraint saga and the
   riser are two commits if they untangle cleanly, one if not).
4. Delete stray verification `.png`s left in the parent Codex directory.

## Phase 1 — The UI/UX overhaul ("10,000x")

Jonathan's verdict on the pre-fork UI: "everything UI & UX flow was obviously
an afterthought." All four surfaces get a deliberate design pass, in this
order (each is its own commit-sized chunk):

1. **Audit:** fresh precise screenshots of all 9 Settings panes, the Colors
   window, the dropdown, and the Screen Bar in every mode. Catalogue every
   spacing, alignment, hierarchy, and flow failure. Pick one design module
   from the anti-ui-slop stack and hold the whole pass to it.
2. **Settings window:** information architecture first (are 9 panes right?
   what does a user actually come here to do?), then per-pane layout. Every
   pane should teach — the brightness dot-strip and live Screen Bar miniature
   set the pattern: show, don't caption.
3. **Colors window:** same treatment; the pinned live preview stays, the card
   flow below it gets rethought.
4. **Menu-bar dropdown:** redesign for glanceability — current agents and
   their states should read in under a second; toggles and sessions ordered by
   actual frequency of use.
5. **Screen Bar rendering:** quality pass on the glow itself (banding,
   falloff, hotline balance) so the always-visible surface looks premium.

## Phase 2 — Auto-brightness rebuild + Focus integration

Observed bug: auto-brightness **doesn't react at all** — effectively static.

1. Root-cause the dead path (`display_brightness.py`, its polling/callback
   wiring, per-device auto flag).
2. Rebuild: live tracking of display brightness with smooth eased transitions
   (no visible steps), clean global on/off, consistent behavior across the
   Screen Bar and physical devices.
3. **Per-Focus rules** (extends `focus_sync.py`): each Focus mode can set a
   dim level or turn surfaces off entirely. Defaults: Sleep → off,
   DND → strong dim, Work/School/Personal → configurable. Settings UI for
   this lives with brightness, not buried.

## Phase 3 — LED engine: universal agent indicator

The core feature vision. All display styles must work on the 8-LED Pro, the
2-LED Dot, and the Screen Bar (which can subdivide freely).

1. **Multi-agent display styles** (a user-selectable mode, with per-device
   override):
   - *Aggregate* — today's behavior, highest-priority state wins.
   - *Cycle* — pulse one agent's state/color, transition, pulse the next;
     dwell time and transition style configurable.
   - *Zones* — each active agent owns a run of LEDs / a segment of the bar.
2. **Attention events** (moments, not states): waiting-for-input flash,
   completion sweep, error strobe — each individually toggleable with
   intensity control.
3. **Progress display:** long-task progress and battery charge as LED fill.
4. **Blend/transition engine:** clean cross-fades between modes and events so
   nothing snaps.
5. **Customization surface:** presets first ("Calm", "Informative",
   "Everything"), full per-option control behind them — customizable without
   re-cluttering the UI we just cleaned.

## Phase 4 — Calibration overhaul

Today it's blind RGB guessing. Fix = add ground truth.

1. **Guided matching wizard:** show a reference swatch on screen adjacent to
   the physical device, step through white point + primaries until they
   visibly match; write the result as the device's calibration.
2. **Profiles:** named calibration/brightness profiles (desk-day, desk-night,
   travel), switchable from the dropdown, composable with Focus rules.

## Phase 5 — Computer signals

The "fun" half. Agent status stays the core; these layer in as opt-in signal
sources with the Phase-3 event system doing the rendering.

1. **Low-battery attention event** (threshold crossing while unplugged —
   distinct from the existing battery fill mirroring).
2. **Calendar/meeting alerts:** native EventKit first (permission-gated);
   glow warning N minutes before an event, distinct state while in one.
   A Raycast extension calling the existing `sidepulse` CLI is the cheap
   integration path if EventKit friction is high.
3. **Notification blinks** (iMessage/WhatsApp/Telegram → per-app colors).
   Honest feasibility note: macOS offers no public API to observe other apps'
   notifications; the realistic routes are Focus/DND state, per-app
   integrations, or the Notification Center store (fragile, needs Full Disk
   Access). Scope this when we get here; do not promise it before then.
4. **Weather indicator** (open-meteo fetch; lowest priority, pure fun).

## Standing ground rules (from prior sessions — still binding)

- Tests constructing `StatusBarController` must patch BOTH
  `sidepulse.settings.default_settings_path` and
  `sidepulse.status_bar.default_settings_path` to a tempdir first.
- After changes: full tests with the venv python → `ruff check` clean →
  `pip install --force-reinstall --no-deps .` → `launchctl kickstart -k
  gui/$(id -u)/io.sidepulse.agentstatus` → confirm
  `~/.local/state/sidepulse/agent-monitor/status-bar.err.log` is empty.
- Visual verification: query window bounds via System Events (logical points),
  crop screenshots at 2x in PIL — never eyeball crops.
- Commit and push at the end of each clearly-scoped chunk; detailed messages
  explaining *why*, matching the style already on this branch.
