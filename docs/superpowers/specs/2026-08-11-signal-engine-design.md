# Signal Engine — unified signals, escalation, identity, style cards

Design for the next wave, from the 2026-08-11 grilling session. Approved
approach: **A — Unified Signal Engine** ("a! ltgm").

## Goals (locked decisions)

1. **Ask-escalation**: an ignored "agent needs you" gets progressively
   harder to miss. Default ceiling: light ramp + menu-bar icon flash
   (~2 min). Chime-once and full-takeover exist as **opt-in tiers**.
   All thresholds user-tunable.
2. **Agent identity**: **color = agent, pattern = state.** Every session
   gets a stable identity color (auto-assigned, overridable), consistent
   across the Pro, the Dot, the Screen Bar, and the dropdown.
3. **System signals**: Reminders-due glow ships in this wave (the
   Reminders usage key is already in the bundle). Standing principle:
   **only signals macOS doesn't already surface well** — no camera /
   network / screen-recording dupes. Future shortlist (not this wave):
   ambient timebox/Pomodoro glow, "long task finished".
4. **Customization**: every signal gets a **style card** — color well,
   pattern picker with animating thumbnails, intensity slider, and a
   live preview bar. Pick by eye, never by name. No raw data editing.

## Non-goals

- No rules/automation builder (rejected in grilling — style cards won).
- No theme presets beyond the existing Calm/Informative/Everything.
- No open signal API / Raycast / Shortcuts (family 4 was not picked).
- No weather (still lowest priority on the roadmap).

## Architecture

### The model (new module `src/sidepulse/signals.py`)

```
SignalStyle (frozen dataclass, JSON-serializable)
    color: str | "identity"      # hex, or the claiming agent's identity color
    pattern: str                 # "breathe" | "blink" | "double-blink" | "solid" | "sweep"
    speed_seconds: float         # one pattern period
    intensity: float             # 0.0-1.0 multiplier on brightness

SignalClaim (produced each tick by a source)
    key: str                     # "low_battery", "notification", "calendar",
                                 # "reminders", "battery_preview", "agent"
    style: SignalStyle           # already resolved (identity color substituted)
    holds_until: float | None    # monotonic expiry, None = while condition holds
```

One **arbiter** replaces today's scattered `active_led_display_kind_for_device`
branches: sources are polled in a fixed, documented precedence order and
the first active claim wins. Precedence (top wins):

| # | signal            | nature              |
|---|-------------------|---------------------|
| 1 | low_battery       | state (calm nag)    |
| 2 | notification      | moment (~1.8 s)     |
| 3 | reminders         | moment (~6 s glow)  |
| 4 | calendar          | state (lead window) |
| 5 | battery (preview/pinned) | state        |
| 6 | agent             | state (default)     |

This is exactly today's behavior generalized — no user-visible change at
migration time. The agent signal internally renders identity + state +
escalation (below).

### Rendering (`style_to_program` in `led_status.py`)

One function turns any `SignalStyle` into the whole-bar DSL program:
pattern template × color × speed × intensity (via `apply_brightness`).
The existing bespoke generators (`calendar_glow_program`,
`notification_blink_program`, `low_battery_program`) become default
`SignalStyle` values rendered through this one path, and are deleted as
functions. Program limits (512 B / 20 lines / 65535 ms) are asserted by
one shared test that renders every pattern at extreme settings.

### Escalation (agent signal only)

`AgentStatus` already carries `updated_at`; the monitor additionally
tracks `blocked_since` per agent (set when a status enters ask/blocked,
cleared on any other state). Escalation stage is a pure function:

```
stage(now - blocked_since, thresholds) -> 0 | 1 | 2 | 3
  0  <30 s      normal ask rendering
  1  ≥30 s      ramp: speed ×0.75, intensity +15%  (light surfaces only)
  2  ≥2 min     + menu-bar icon flash (NSTimer toggling the status item
                 between its normal icon and the ask symbol, ~1 Hz)
  3  ≥ user threshold (default 5 min), opt-in only:
                 "chime": one NSSound per block event, never repeated
                 "takeover": all surfaces strobe the ask color
```

Defaults ship stages 0–2 (the approved ceiling). Stage-3 behavior is
`off | chime | takeover` in settings. All thresholds are settings.

### Identity palette

`ColorSettings.agent_colors` (exists) becomes the identity store. New:
deterministic auto-assignment — an 8-color designed palette chosen to
stay clear of the state hues (working cyan, done green, ask red-orange,
calendar purple); `palette[hash(agent_key) % 8]`, first-come collision
avoidance while slots remain. Users override per agent in the Colors
window (existing rows). Blend modes consume identity colors exactly as
they consume `agent_colors` today; the dropdown gains a small color dot
per session row so the mapping is learnable.

### Reminders signal (`reminders_watch.py`)

Mirror of `calendar_watch.py` on EKEntityTypeReminder:
`authorization_status()`, `request_access()`, `due_now(window)` —
reminders with a due date inside [now − 60 s, now]. Claim: a ~6 s glow
(default amber `#FFB340`, double-blink) when one comes due; 60 s poll;
same quiet backoff contract as calendar. Card in the signals pane with
the same enable-presents-the-prompt flow.

### Style cards UI

The LED Behavior pane is renamed **Signals**. Each signal gets one card
built by a single `make_signal_style_card(signal_key, …)` component:

- **color well** (NSColorWell, instant apply; hidden for the agent
  signal where identity colors rule),
- **pattern picker**: one small `VirtualLedView` per pattern, animating
  that pattern live in the signal's color; click to select (radio
  behavior, selected ring),
- **intensity slider** (continuous, live),
- **live preview**: one miniature bar at the card's bottom rendering the
  signal's current style exactly as the Screen Bar would.
- The escalation card adds: tier picker (segmented: Light / +Menu bar /
  +Chime / Takeover) and threshold fields.
- The notification card keeps its per-app color rows (the app's color IS
  the signal's meaning); its style card controls pattern/intensity only.

Existing cards (Dimming, Focus Dimming) stay as they are — they are
modifiers, not signals.

### Settings & migration

`signal_styles: dict[str, dict]` joins `AgentMonitorSettings` (validated
per-key on load; unknown keys dropped; absent → defaults identical to
today's look). Existing feature toggles (`calendar_alerts_enabled`,
`notification_blinks_enabled`, …) stay as the per-signal enable bits.
`escalation_tier`, `escalation_stage1_seconds`, `stage2_seconds`,
`stage3_seconds` join settings. Nothing existing is renamed on disk.

## Error handling

Unchanged contract, now centralized: every source's poll is wrapped;
any failure = "no claim" + quiet backoff (existing per-source retry
timers). The arbiter never raises: a source exception falls through to
the next precedence level. Style deserialization falls back to the
signal's default style, never to a crash or a blank bar.

## Testing

- Arbiter: table-driven precedence tests (every pair of simultaneous
  claims).
- `style_to_program`: every pattern × extreme speeds/intensities stays
  inside device limits; snapshot the default styles' programs equal
  today's bespoke programs (migration is invisible).
- Escalation: pure `stage()` function tests + menu-bar flash timer
  start/stop on state transitions.
- Identity: deterministic assignment, collision behavior, override
  round-trip.
- Reminders: fixture-driven watcher tests mirroring calendar's.
- UI: cards exist per signal, controls bound (selector-existence tests),
  preview view receives the selected style.

## Phasing (each phase ships green and alone)

1. **Engine + migration** — signals.py, arbiter replaces display-kind
   branches, `style_to_program`, snapshot-equality tests. Zero visible
   change.
2. **Escalation** — blocked_since, stage(), ramp + menu-bar flash,
   settings + tiers.
3. **Identity colors** — palette assignment, dropdown dots, Colors
   window integration.
4. **Reminders** — watcher + signal + prompt flow.
5. **Style cards** — the Signals pane rebuild with pattern thumbnails
   and live previews.
