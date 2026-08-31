# JR Bar P3.38 Manual and Scheduled DND Design

**Date:** 2026-08-30

**Scope:** Recommendation 38, manual and scheduled Do Not Disturb with distinct
Mute, Dim, Pause, Asks Only, and Fully Dark behaviors, macOS Focus integration,
quiet hours, temporary overrides, and a visible return time.

## Product decision

JR Bar will add one explicit DND projection over presentation. DND never edits
canonical agent, request, completion, mailbox, triage, or acknowledgement truth.
It decides what may be shown now, how bright it may be, and which outbound
interruptions may fire. Agent Browser and the menu ledger remain available in
every mode.

New installations keep DND scheduling and macOS Focus reactions off. Nothing
changes until the user enables a schedule, starts a temporary override, or opts
into Focus status.

## Evidence and prior art

- Original SidePulse PR 28 is still open at head commit
  `7d9c49c5c3a4e13117bae745b8fd63c1a12dbd7b`. Its useful patterns are persisted
  schedule state, overnight boundaries, an override that survives restart, and
  tests around the next schedule transition. Its hardware-off implementation is
  too narrow for JR Bar's multi-surface model and will not be copied directly.
- T3 Code's transferable pattern is to present a pause as an explicit state with
  a visible return time, not as a failure or an unexplained lack of activity.
- CodexBar's repeated reset and expiration-time requests reinforce that the user
  needs an exact return time near the control that created the temporary state.
- Apple exposes coarse Focus status through `INFocusStatusCenter` on macOS 12 or
  later. The public API reports only whether a Focus is active. It does not
  expose the active Focus name or schedule and does not advertise a dedicated
  change notification.
- JR Bar already has per-Focus dim rules and signal policies backed by an
  undocumented, Full Disk Access-protected focusd database. Those named rules
  remain an optional compatibility enhancement. They cannot be the foundation
  of the new DND contract.

## Exact behavior vocabulary

The UI names five modes. Each maps to independent projection axes so concurrent
sources can compose strictly without inventing a misleading total order.

| Mode | Light surfaces | Brightness | Outbound interruptions |
| --- | --- | --- | --- |
| Mute | All current visual state and signals remain visible | Unchanged | Notification banners, sounds, and notification webhooks are refused |
| Dim | All current visual state and signals remain visible | Multiplied by the configured DND dim fraction | Unchanged |
| Pause | Routine and courtesy visuals are withheld; active asks, failures, escalation, and low battery may remain | Unchanged unless another source dims | Only critical interruptions may fire |
| Asks Only | Only a current actionable ask and its escalation may remain | Unchanged unless another source dims | Only actionable-ask interruptions may fire |
| Fully Dark | Physical LEDs, Screen Bar state, announcer, gauges, and finite cues are withheld | Zero, with no minimum-glow or escalation floor | All notification banners, sounds, and notification webhooks are refused |

The menu ledger, Settings, Agent Browser, usage refresh, provider ingestion,
remote ledger sync, persistence, and local history continue in every mode.
Fully Dark is not app suspension and does not stop agents.

When DND ends, current standing truth may reappear. Expired finite cues, sounds,
banners, and webhooks do not replay. An unresolved current ask is standing truth,
not a replay, so it may become visible again.

## Pure policy model

Create an AppKit-free `dnd_policy.py` with immutable, strictly validated values:

- `DndMode`: `mute`, `dim`, `pause`, `asks_only`, `dark`;
- `DndSource`: `manual`, `schedule`, `macos_focus`, `named_focus`;
- `DndSchedule`: enabled, start minute, end minute, and mode;
- `DndOverride`: either one mode or a temporary resume, plus creation and expiry
  epochs;
- `DndContribution`: one source's display admission, brightness factor, and
  interruption admission;
- `DndProjection`: composed active sources, effective axes, summary, reason,
  and next transition epoch.

Strict parsing rejects booleans as numbers, non-finite epochs, unknown modes,
out-of-range minutes, equal schedule boundaries, overlong overrides, and
unbounded collections. Malformed persisted DND data is ignored individually
and presented as a typed refusal without deleting unknown settings fields.

## Composition and precedence

Manual overrides, schedules, coarse macOS Focus, and optional named Focus detail
produce contributions. Composition takes the strictest value on each axis:

- display admission: all, critical, asks, none;
- brightness factor: minimum finite factor;
- outbound admission: all, critical, asks, none;
- banner admission: false wins;
- audible admission: false wins;
- webhook admission: false wins.

This is intentionally dimensional. A scheduled Dim contribution and a macOS
Focus Mute contribution result in dim visuals and muted outbound interruptions.
Neither source silently erases the other.

A valid manual override wins over the schedule only for the override axis it
names. A temporary resume suppresses the local schedule until its expiry, but
does not suppress an active macOS Focus contribution. Manual DND and manual
resume both persist across restart until their absolute expiry.

## Schedule and time contract

P3.38 supports one daily local-time interval. The start and end minutes must
differ. Start later than end means an overnight interval. The default stored
times are 22:00 through 07:00, but scheduling is disabled by default.

Evaluation uses an injected timezone-aware wall clock. It never adds a fixed 24
hours to find tomorrow. Extract and reuse the local-time boundary resolver now
embedded in `mailbox_preference_store.py`: a nonexistent spring-forward time
advances to the first valid local second, while an ambiguous fall-back time
considers both folds and selects the earliest valid epoch at or after the lower
bound. Tests cover same-day intervals, midnight crossover, spring-forward gaps,
fall-back folds, timezone changes, clock changes, sleep, wake, launch inside an
active interval, and launch after an override expired.

The projection publishes the next real transition epoch. UI formats that epoch
in the user's current locale and time zone. A clock or time-zone notification,
session activation, sleep, or wake invalidates the projection and recomputes the
timer. The controller arms at most one DND transition timer.

## Settings persistence

Add bounded scalar fields to `AgentMonitorSettings` and its owned Settings paths:

- `dnd_schedule_enabled`, default `False`;
- `dnd_schedule_start_minutes`, default `1320`;
- `dnd_schedule_end_minutes`, default `420`;
- `dnd_schedule_mode`, default `dark`;
- `dnd_dim_fraction`, default `0.15`;
- `dnd_override_mode`, optional mode or `resume`;
- `dnd_override_created_epoch`, optional finite epoch;
- `dnd_override_until_epoch`, optional finite epoch;
- `dnd_focus_mode`, default `pause`.

Reuse the existing persisted `focus_sync_enabled` field as the Follow macOS
Focus source of truth. Do not create a second enable bit. Its existing false
default preserves opt-in behavior and its existing round-trip contract survives
upgrade.

Persist the override with the same transactional Settings boundary already used
by P3.37. A refused or concurrent save leaves both durable and live DND state
unchanged. Unknown fields and newer schemas remain preserved and read-only under
the existing compatibility contract.

The old process-local `quiet_until_monotonic` path becomes a compatibility
adapter that creates or clears a persisted Mute override. Old selectors remain
temporarily callable so stale menu targets cannot crash, but the root menu no
longer presents a second Quiet concept.

## Public macOS Focus observation

Add a lazy, injectable `focus_status.py` adapter around
`INFocusStatusCenter.defaultCenter` on macOS 12 or later. It exposes typed
authorization and coarse active, inactive, or unavailable observations. It does
not import Intents at module import time on unsupported systems.

Add `NSFocusStatusUsageDescription` to the produced app Info.plist. The app asks
for Focus authorization only after the user activates the explicit Settings
button. Source probes may read the current authorization and coarse status, but
must never request authorization.

The runtime refreshes Focus status on launch, app/session activation, sleep,
wake, screen sleep/wake, system clock change, time-zone change, and its existing
bounded environment refresh. There is no private assumption of a push
notification or KVO contract.

When public coarse Focus is authorized and active, it contributes the configured
`dnd_focus_mode`. When optional named Focus detail is also readable, existing
per-Focus dim and signal rules contribute stricter axes. Named detail preserves
the current `focus_dim_fraction()` fallback, including the 5 percent fallback
for an unconfigured Sleep-like Focus. Explicit named rules still win. Named
detail never changes a public active Focus to inactive. Missing Full Disk Access
no longer makes coarse Focus integration unavailable.

## Runtime integration

One `DndController` owns the current projection, the single transition timer,
Settings transactions, and refresh invalidation. It receives clocks, timer
construction, Focus observation, Settings save, and the refresh callback by
injection. It owns no AppKit controls and performs no provider I/O.

The retained controller consumes one projection:

- `signal_selection.py` admits all, critical, ask-only, or no visual claims;
- the ordinary agent display is withheld when Pause, Asks Only, or Fully Dark
  does not admit the current canonical state;
- `brightness_policy.py` applies `dnd_dim` before escalation floors and treats a
  zero DND factor as authoritative for both ambient and signal paths;
- Screen Bar rendering, announcer availability, gauges, and physical writes
  consume the same display and brightness axes;
- `InterruptBudget` consumes outbound admission and produces separate
  `banner_allowed`, `audible`, and `webhook_allowed` decisions in addition to
  visual signal admission;
- semantic notifications, completion notifications, chimes, and notification
  webhooks consume their exact field from the same bounded grant, with no banner
  nested accidentally under an audio decision;
- remote state sync, ingestion, history, and non-notification automation do not
  consume DND presentation policy.

The current selected mode and return time are included in Why This Light and
local health only as bounded, content-free facts. Their existing fixed-shape
output contracts remain mandatory. No DND telemetry is added.

## UI and accessibility

Do not add a Settings category. Add a separate `dnd_settings_pane.py` card to
Settings, Notifications & Focus, Focus.

The card contains:

- one schedule switch;
- native start and end time controls;
- one scheduled-mode popup with five plain-language choices;
- one DND dim control shown when Dim can apply;
- one Follow macOS Focus switch backed by `focus_sync_enabled`, one mode popup,
  authorization status, and an explicit Allow Focus Status button;
- a Right Now status with active sources and exact return time;
- temporary action buttons for the five one-hour modes and Resume.

The root menu replaces Quiet with one `Do Not Disturb` submenu. Its parent title
shows `DND: Off`, `DND: <Mode> until <time>`, or `DND: <Mode>, scheduled until
<time>`. The submenu exposes five one-hour temporary modes, Resume Until Next
Change when applicable, End Temporary Override, and DND Settings.

Every mode has a visible text label and explanation. No state is conveyed only
by color, animation, indentation, or a checkmark. The status line has an exact
accessibility label, value, and help string. Native key-view order covers the
schedule, time, mode, Focus, authorization, temporary actions, and status.

## Lifecycle and failure behavior

- Launch is idempotent and creates at most one observer set and one timer.
- Termination invalidates the DND timer and Focus callbacks before panels,
  workers, and persistence close.
- Late timer, Focus, save, and authorization callbacks are generation-fenced.
- Sleep and screen sleep withdraw transient visuals without deleting canonical
  state or durable DND configuration.
- Wake, clock change, and time-zone change recompute from current wall truth.
- Save refusal retains the previous controls, projection, timer, and Settings.
- Focus denial or unavailability remains visible and never silently claims DND.
- A malformed persisted override is visible as a refusal while valid schedule
  and Focus settings continue to work.
- Fully Dark never writes a minimum-glow or escalation floor back onto a zero.

## Verification and receipts

Focused tests must cover pure mode matrices, schedule and DST boundaries,
strict parsing, settings compatibility, public Focus availability and
authorization, lifecycle idempotence, stale callbacks, save rollback, signal
admission, brightness, notifications, sounds, webhooks, Screen Bar and hardware
parity, menu selectors, retained Settings controls, keyboard order, and exact
accessibility text.

Source-native AppKit receipts cover at least these states in Aqua and Dark Aqua:
off, manual Mute, scheduled Dim, Focus Pause, Asks Only override, scheduled
Fully Dark, temporary Resume, and Focus unavailable. Bind every image to source
and image SHA-256 values and inspect exact copy, return time, focus, geometry,
contrast, and clipping.

An isolated public Focus probe may read authorization and status in a fresh
process. It must not request authorization or change TCC state.

## Evidence boundary and non-goals

P3.38 does not prove live Focus authorization, a real Focus transition, every
DST and locale combination, installed-app VoiceOver speech, physical hardware,
signing, notarization, packaging, publication, or release readiness. Those stay
separate installed and release gates.

P3.38 does not add multiple weekly schedules, calendar-driven DND, geofencing,
cloud DND sync, arbitrary automation, content telemetry, or a new Scene system.
The later Scenes recommendation may consume this projection instead of replacing
it.
