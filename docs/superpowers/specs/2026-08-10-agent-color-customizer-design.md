# Agent Color Customizer & Screen Bar Fidelity Design

Date: 2026-08-10

## Objective

Give Jonathan an intuitive, native-feeling way to customize SidePulse's LED
colors along three axes he currently can't control at all:

1. Colors for the four LED display modes (Idle, Working, Done, Ask).
2. Colors per monitored agent/provider (Codex, Claude, Devin, Grok, and any
   future provider), so an agent's identity is visible on the device, not just
   its mode.
3. A way to see multiple simultaneously-active agents in the LEDs at once,
   blending or splitting their colors rather than collapsing to one
   aggregate state.

While doing this, also fix two related display-fidelity issues that surfaced
during design: the on-screen "Screen Bar" replica drifts out of phase with
the physical device, and it currently fights the Alcove notch app for the
same screen space.

## Scope

This change will:

- Add a `ColorSettings` model (mode colors + per-provider agent colors +
  blend mode) that persists alongside existing `AgentMonitorSettings`.
- Add a native "Colors" window to the macOS status-bar app, opened from the
  existing Settings window, following the same window-per-concern pattern
  already used for the Setup window.
- Replace the single-aggregate-mode LED renderer with a multi-agent-aware
  renderer that can split LEDs spatially across active agents, blend their
  colors, cycle through them, or fall back to today's exact behavior
  ("Classic"), selectable via a Blend Mode setting.
- Fix the Screen Bar's animation phase to start from the physical device's
  actual write completion instead of the moment the mode decision was made.
- Add an Alcove-aware compact layout for the Screen Bar, auto-detected with a
  manual override.

This change will not:

- Change the physical LED firmware or the `LEDS.LED` DSL itself.
- Add color customization to the iOS companion app (it has no concept of
  agents/modes today; out of scope here).
- Change how agent statuses are collected, normalized, or aggregated
  (`collector.py`'s `AgentMonitor`/`MonitorSnapshot` already expose everything
  this design needs).
- Reflow or redesign the existing Settings window's other sections (Agent
  Hooks, Transcript Monitoring, Debug Log, Lid & Sleep). The Alcove toggle is
  the one small addition there; everything else about Colors lives in its own
  window.

## Architecture

### 1. Color data model (`src/sidepulse/colors.py`, new module)

A new module keeps this logic out of the already-large `settings.py` (588
lines) and `status_bar.py` (~3,000 lines), matching this codebase's existing
practice of one focused module per concern (`battery.py`, `led_status.py`,
`virtual_device.py`, etc.).

```python
CURATED_PALETTE: tuple[str, ...] = (
    "#3AA0FF", "#3AD6C9", "#7A5CFF", "#FF5C8A",
    "#FF8A3D", "#CAA23A", "#4DD65C", "#B0B0B4",
)  # evenly spaced hues, chosen to average into legible blends pairwise

BLEND_MODE_SPATIAL = "spatial_split"   # default
BLEND_MODE_COLOR = "color_blend"
BLEND_MODE_CYCLE = "cycle"
BLEND_MODE_CLASSIC = "classic"         # today's exact single-aggregate behavior
BLEND_MODE_CHOICES = (BLEND_MODE_SPATIAL, BLEND_MODE_COLOR, BLEND_MODE_CYCLE, BLEND_MODE_CLASSIC)

@dataclass(frozen=True)
class ColorSettings:
    mode_colors: dict[str, str]     # keys: "idle" | "working" | "done" | "ask"
    agent_colors: dict[str, str]    # keys: provider id, e.g. "codex"
    blend_mode: str = BLEND_MODE_SPATIAL

    @classmethod
    def defaults(cls) -> "ColorSettings": ...   # mode_colors seeded from
        # led_status.py's existing IDLE_DIM/WORKING_CYAN/DONE_GREEN/ASK_AMBER
        # constants, so Classic mode and today's fresh-install colors match byte-for-byte.

    def with_mode_color(self, mode: str, hex_value: str) -> "ColorSettings": ...
    def with_agent_color(self, provider: str, hex_value: str) -> "ColorSettings": ...
    def with_blend_mode(self, mode: str) -> "ColorSettings": ...
    def agent_color(self, provider: str) -> str:
        """Returns the configured color, or a deterministic default (see below)."""

    def to_dict(self) -> dict: ...

def default_agent_color(provider: str) -> str:
    """Index into CURATED_PALETTE by the provider's position in PROVIDER_SPECS,
    wrapping around. Deterministic and collision-free for the current 4
    providers; a 5th provider automatically gets CURATED_PALETTE[4] with zero
    code changes elsewhere."""
```

`AgentMonitorSettings` (in `settings.py`) gains one new field,
`colors: ColorSettings = field(default_factory=ColorSettings.defaults)`, and
a `with_colors(...)` immutable updater — the same shape as every other
setting in that file. It round-trips through the existing settings JSON
file exactly like `lid_closed_animation` does today; a missing or malformed
`colors` block on load falls back to `ColorSettings.defaults()` rather than
failing settings load entirely.

Because `agent_colors` is a plain `dict[str, str]` keyed by provider id
(sourced from `providers.PROVIDER_SPECS`, not a hardcoded enum), adding a
future provider is: add its `ProviderSpec` (already required today for hook
support) and it automatically gets a row in the UI and a default color — no
`ColorSettings` changes needed.

### 2. Multi-agent rendering (`colors.py`)

Today, `led_status.write_mode_to_leds()` takes one `AgentMode` (the
aggregate) and renders one of four fixed programs. This design adds a
parallel entry point that takes the full per-agent breakdown that
`collector.MonitorSnapshot.statuses` already computes and discards nothing:

```python
def program_for_snapshot(
    statuses: tuple[AgentStatus, ...],
    *,
    led_count: int,
    colors: ColorSettings,
    brightness: int | float = 255,
) -> tuple[LedDisplayState, str]:
    """Returns (representative display state for controller bookkeeping, LED program)."""
```

Behavior by `blend_mode`:

- **Classic** — ignores per-agent breakdown entirely; calls
  `display_state_for_mode()` + `program_for_display_state()` exactly as
  today, using `colors.mode_colors` in place of the hardcoded constants.
  This guarantees the pre-existing behavior remains available byte-for-byte
  and nothing is lost by adding this feature.
- **Spatial Split** (new default) — if `led_count >= len(statuses)`: each
  active agent gets a contiguous LED block. Block size is proportional to
  an urgency weight derived from `MODE_PRIORITY`: `weight = 8 - priority`
  (priority 1..7 per the existing table, so Blocked/Error → weight 7,
  Idle/Ready → weight 1; `UNKNOWN`'s priority of 99 is clamped to weight 1).
  Block LED count is `round(weight / total_weight * led_count)`, with a floor
  of 1 LED per active agent, and any leftover/rounding LEDs assigned to the
  highest-weight agent. Each block
  animates using that agent's configured color and its own mode's animation
  character (pulse for Ask, roll-in-place for Working, solid for Done, dim
  pulse for Idle). If agents outnumber LEDs, falls through to Color Blend.
- **Color Blend** — averages all active agents' colors, weighted by the same
  urgency weight, into one RGB value shown uniformly across every available
  LED. Used directly when selected, and as the Spatial Split fallback on the
  2-LED Dot whenever 2+ agents are active.
- **Cycle** — rotates through each active agent's full color + mode
  animation in sequence (~1.2s per agent) across all LEDs, so each agent is
  shown clearly on its own rather than mixed.

In every non-Classic mode, the single highest-priority mode among the active
set still drives overall urgency framing (matches today's aggregation
priority order: Blocked > Waiting > Tool Running > Long Task > Working >
Completed > Idle), so urgency is never lost to blending.

**Stability (debounce + crossfade).** Spatial layout (which agent occupies
which LEDs, and how large each block is) only recomputes if the new ranking
holds steady for `LAYOUT_DEBOUNCE_SECONDS` (1.5s). Colors/animations for
already-assigned agents still update immediately — only the *positions and
sizes* are debounced, so urgency is never delayed, only reshuffling is. When
a layout does change, blocks resize/recolor using the DSL's existing
`cosine` easing over ~400–500ms rather than an instant cut, so it reads as
motion, not a glitch. This is implemented as a small stateful helper,
`AgentLayoutStabilizer`, parallel to the existing `AgentLedController`
pattern (holds last-committed layout + timestamp; pure function in, stateful
wrapper out — same separation `AgentLedController` already uses around
`write_mode_to_leds`).

**Living idle touch (nice-to-have, may ship in a later pass if it adds
too much complexity to the first cut).** When 3+ agents are active on the
8-LED Pro, additionally append a slow `roll 7s linear` / `repeat` after the
per-block color assignment, giving the whole strip a gentle continuous drift
even when nothing is actively reshuffling. This is a purely cosmetic layer on
top of the assignment described above and doesn't change any priority logic.

### 3. Integration into the existing controller (`led_status.py`, `status_bar.py`)

`AgentLedController.sync_mode(mode)` is extended with a sibling method,
`sync_snapshot(statuses: tuple[AgentStatus, ...], colors: ColorSettings)`,
that calls `program_for_snapshot()` instead of `program_for_display_state()`
and dedups on a signature of `(blend_mode, layout_hash, brightness)` instead
of just `state` — otherwise identical to today's error-retry and
change-detection behavior.

In `status_bar.py`, `sync_leds_now()` currently calls
`self.agent_controller_for_device(device).sync_mode(mode)` where `mode` is
`snapshot.aggregate.mode`. This becomes
`sync_snapshot(snapshot.statuses, self.settings.colors)`, threading the full
per-agent tuple through instead of collapsing it before the call. This is
the one call-site change needed to unlock everything above — the data was
already there.

### 4. Colors window (`status_bar.py`, new `build_colors_window()`)

A new secondary `NSWindow`, opened via a "Customize Colors…" button added to
the existing Settings window, following the same pattern the app already
uses for `build_setup_window()` — a separate focused window rather than
growing the already-1,054px-tall Settings window further.

Sections, top to bottom:

1. **Live Preview** — a custom-drawn `NSView` (reusing this app's existing
   Bezier-path drawing, already used for the status-bar icon and the Screen
   Bar's `VirtualLedView`) showing both device shapes (2-LED Dot, 8-LED Pro)
   rendered live from the current `ColorSettings` and a synthetic multi-agent
   scenario, with each LED block labeled by agent name + mode directly
   underneath it. A "Blend Mode" popup sits directly below the preview so its
   effect is visible immediately when changed.
2. **Agent Colors** — one row per entry in `PROVIDER_SPECS`, each showing the
   provider name, a horizontal strip of `CURATED_PALETTE` swatches (custom
   `NSView` circles, click = instant selection + instant preview update),
   and one additional "Custom…" swatch — a plain neutral circle with a small
   "+" glyph — that opens the real `NSColorPanel` for a free-form pick, with
   the resulting hex value written back into `agent_colors`.
3. **Mode Colors** — the same swatch-strip pattern for Idle/Working/Done/Ask,
   labeled as applying "when no agent color applies (e.g. battery display)".
4. **Bottom bar** — Reset to Defaults (reverts `colors` to
   `ColorSettings.defaults()`), a "Preview live on device" switch that, while
   on, throttles real writes (a few times/second, reusing
   `AgentLedController`'s existing dedup so it doesn't spam the USB device)
   to the currently selected physical device as swatches are clicked, and
   Done.

All colors, backgrounds, and text use system-provided `NSColor` dynamic
colors (e.g. `NSColor.controlBackgroundColor`, `NSColor.labelColor`) rather
than hardcoded light-mode hex values, so the window follows
`NSAppearance`/Dark Mode automatically — this was a gap in the first mockup
pass (built light-mode-only) and is corrected here.

### 5. Screen Bar phase-sync fix (`virtual_device.py`, `status_bar.py`)

Root cause (confirmed by reading the current code): `sync_virtual_status_device()`
runs synchronously on the main thread and calls `VirtualLedView.setProgram_()`
immediately whenever the mode changes, resetting its animation clock
(`started_at = time.monotonic()`) at that instant. The real device write
happens on a background thread (`sync_leds_worker`), gated by a
busy-check and a throttle window (`led_animation_until_monotonic`), plus
actual USB/filesystem write latency. The two clocks are zeroed at different
real-world instants, so cyclical animations (pulse, roll) visibly drift
apart over time.

Fix: when at least one physical device is connected and selected, the
Screen Bar's program update moves to fire *after* a real device write
succeeds inside `sync_leds_now()` (dispatched back to the main thread the
same way `schedule_event_refresh()` already dispatches background-thread
work today), using that write's completion time as phase-zero.
`VirtualLedView.setProgram_`/`setState_brightness_` gain an optional
`started_at` override parameter for this; omitted, they behave exactly as
today (immediate local start), which is also the correct behavior when no
physical device is connected — there's nothing to sync to in that case.

This will not be cycle-accurate — there's no acknowledgment channel from the
physical firmware to know exactly when it began parsing — but it narrows the
drift from "up to the throttle window" (could be over a second) to
approximately the USB write latency (expected to be well under 100ms),
which should read as synced rather than visibly independent.

### 6. Alcove coexistence (`virtual_device.py`, `settings.py`)

Alcove (`com.henrikruscon.Alcove`, confirmed installed) is a Dynamic-Island-
style notch app with no published compatibility API — the fix is defensive,
not cooperative:

- **Detection**: check `NSWorkspace.sharedWorkspace().runningApplications()`
  for Alcove's bundle id whenever the Screen Bar repositions (it already
  re-checks screen geometry on that path today) and additionally on
  app-activate/deactivate notifications, since Alcove can launch or quit at
  any time during a session.
- **Compact layout**: a second frame/shape in `virtual_device.py` — a
  narrower pill offset to one side of the notch instead of the full-width
  wraparound bar — used automatically whenever Alcove is detected running.
- **Setting**: a new `alcove_compatibility_mode: str` field on
  `AgentMonitorSettings` with choices `"auto"` (default) / `"always"` /
  `"never"`, exposed as a popup in the existing Settings window near the
  other Screen Bar/virtual-device controls (not the new Colors window — this
  is a Screen Bar placement concern, not a color concern).
- Exact compact-layout geometry will be tuned empirically by running Alcove
  and the Screen Bar side by side on this Mac during implementation, since
  no documentation exists to design against blindly.

## Data flow

1. A hook event lands, gets normalized, and `AgentMonitor.snapshot()`
   produces a `MonitorSnapshot` with `aggregate` and the full `statuses`
   tuple — unchanged from today.
2. `status_bar.py`'s refresh path now passes `snapshot.statuses` (not just
   `snapshot.aggregate.mode`) into `AgentLedController.sync_snapshot()`.
3. `colors.program_for_snapshot()` consults `self.settings.colors` (mode
   colors, agent colors, blend mode) to render one LED program string.
4. That program is written to the physical device as today. On success, the
   Screen Bar is updated with the same program string, phase-zeroed to that
   write's completion time.
5. The Colors window edits `self.settings.colors` directly (immutable
   `with_*` calls + `save_settings()`, matching every other settings row in
   this app) and, when "Preview live on device" is on, triggers the same
   `sync_snapshot()` path ad hoc for instant feedback.

## Error handling

- A malformed or missing `colors` block in the settings JSON falls back to
  `ColorSettings.defaults()` without failing the rest of settings load —
  consistent with how this codebase already treats bad timestamps and
  missing fields elsewhere (e.g. `parse_datetime`'s fallback behavior).
- An unrecognized provider id encountered in a snapshot (e.g. a newly added
  provider whose app update hasn't shipped a `colors.py` default yet) is
  given a color computed live via `default_agent_color()` rather than
  requiring a settings migration or crashing the renderer.
- Invalid hex values written via the "Custom…" `NSColorPanel` path are
  normalized/validated before being stored; a rejected value leaves the
  previous color in place and surfaces a message the same way existing
  Settings-window errors do (`set_settings_message`).
- Screen Bar phase-sync fix: if the physical write fails (`DeviceWriteError`),
  the Screen Bar falls back to updating immediately (today's behavior)
  rather than waiting indefinitely for a completion that never comes.
- Alcove detection failures (e.g. `NSWorkspace` API changes) fail safe to the
  full-width layout — same as todays's behavior — not to a crash.

## Testing and verification

- Unit tests (extending `tests/test_sidepulse.py`) for: `ColorSettings`
  JSON round-trip and defaults, `default_agent_color()` determinism and
  uniqueness across `PROVIDER_SPECS` order, `program_for_snapshot()` across
  0/1/N agents, both LED counts (2 and 8), and all four blend modes —
  specifically asserting Classic mode's output is byte-identical to today's
  `program_for_display_state()` output for each of the four states.
- Unit tests for the debounce/hysteresis logic in `AgentLayoutStabilizer`
  using an injectable clock (matching how existing timing logic in this
  codebase is tested, e.g. `AgentMonitor`'s stale/timeout tests).
- Unit test for the Screen Bar's `started_at` override behavior (asserting
  omitted vs. provided timestamps produce the expected phase).
- Manual hardware verification: visually compare the Colors window's live
  preview against both a mounted SidePulse Dot and Pro; time-check Screen Bar
  vs. physical device sync with a slow-motion recording before/after the fix;
  run Alcove and SidePulse side by side on this Mac to tune and confirm the
  compact layout empirically.

## Future providers

Adding a fifth provider requires only a new `ProviderSpec` entry in
`providers.py` (already required today for hook support). No changes to
`colors.py`, the Colors window, or the renderer are needed — the new
provider gets a UI row and a deterministic default color automatically from
its position in `PROVIDER_SPECS`.
