# SidePulse Operator Experience Design

## Outcome

SidePulse becomes a three-layer, local-first command center for every supported local agent harness:

1. `Glance` answers whether anything needs the user, what is active, and whether capacity is safe through the menu bar, Screen Bar, and physical LEDs.
2. `Triage` turns every current agent into a stable, bounded mailbox with direct actions, watch, pin, and attention-aware snooze.
3. `Reflect` provides a private, deliberately quiet history of outcomes, attention episodes, active time, usage, and capacity pace without storing prompts, commands, paths, or raw tool payloads.

The primary interface stays the native status menu. A separate dashboard window is not the default because it would add navigation and visual density to a task that should usually take one glance and one click. Hardware-only presentation is also insufficient because it cannot carry identity, history, error, or provenance detail. Deeper Settings views remain available for history and configuration.

## Source Basis

This design uses exact clean local reference snapshots inspected on 2026-08-12. Online freshness was not checked in this pass.

- T3 Code local snapshot `b73232bdd31e83914a8a943960c7dc4b6390b39b`
  - `packages/shared/src/agentAwareness.ts:8-151` defines a small lifecycle projection with approval, input, running, completed, failed, and stale phases.
  - `apps/mobile/src/lib/threadActivity.ts:283-332` keeps internal churn out of the work log while preserving terminal signals.
  - `apps/mobile/src/lib/threadActivity.ts:443-473` collapses subagent and tool lifecycle rows by stable identity.
  - `packages/client-runtime/src/state/threadSettled.ts:120-190` makes snooze visibility-only, refuses to hide work blocked on the user, and wakes early for new failures or completions.
  - `packages/client-runtime/src/state/threadSort.ts:1-230` separates stable user ordering from changing activity, including deterministic pin keys.
- CodexBar local snapshot `c4ed34d0e44a75ae2d578525d0287e9b49cfa341`
  - `Sources/CodexBarCore/UsagePace.swift:3-151` models expected versus actual consumption, headroom, runout ETA, and whether capacity lasts until reset.
  - `Sources/CodexBar/UsageStore+HistoricalPace.swift:5-62` uses historical pace only for the matching provider identity and falls back to explicit duration-based pace.
  - `Sources/CodexBar/PredictivePaceWarnings.swift:4-100` deduplicates warnings by provider, account, semantic window, and reset cycle.
  - `Sources/CodexBarCore/AccountMenuLayoutPlanner.swift:3-185` keeps the active account full-size, orders inactive accounts by constrained headroom, identifies the healthiest usable alternative, and folds a healthy tail.

The references are evidence, not code to copy. SidePulse retains its provider-neutral lifecycle projection, local transcript scan, AppKit menu, Screen Bar, and SidePulse hardware semantics.

## Product Principles

- Attention outranks activity. Approval and user input always defeat snooze, capacity display, and decorative motion.
- Stable objects, changing signals. Rows do not jump because a tool changes. A pulse, marker, or secondary label carries the update.
- Truth before completeness. Missing, empty, partial, stale, failed, and fresh data are distinct.
- Provider and account ownership never blur. Last-known-good data, reset cycles, pace samples, and alerts stay attached to their proven identity.
- Local by default. History contains bounded derived facts only. It never stores raw prompts, assistant messages, tool arguments, commands, file paths, environment variables, or credentials.
- Hardware is a glance layer, not a second dashboard. It communicates priority, progress, and headroom with a small stable vocabulary.
- Motion has meaning. Continuous motion means active work, a short finite pulse means a transition, and static light means stable state.
- No productivity score. History describes the system and its outcomes without grading the user.

## Core User Stories

### Glance

- As a user with many agents, I can look at the Screen Bar or device and immediately tell whether anyone needs me.
- As a user focused elsewhere, I can distinguish active work, a fresh completion, a failure, and a capacity warning without opening the menu.
- As a user with no active work, I can opt into an automatic idle view that shows the most constrained credible capacity window.
- As a user with a two-LED or eight-LED device, I see the same semantic phase mapped across the full available line.
- As a user in Low Power Mode or display sleep, I get reduced or paused animation without losing state transitions.

### Triage

- As a user with hundreds of main agents and workers, I see a bounded mailbox of primary agent families rather than a flat process list.
- As a user waiting on specific work, I can watch or pin an agent without activity reshuffling it.
- As a user who wants to defer quiet work, I can snooze it for one hour, three hours, evening, tomorrow, or next week.
- As a user, I cannot snooze an approval or input request. A snoozed agent wakes early for a new approval, input request, failure, or completion after the snooze began.
- As a user returning to a snoozed agent, I see a finite `Woke` marker until I visit it.
- As a user, I can jump to the exact worker that raised the oldest actionable request.

### Capacity

- As a user, I see remaining percentage and reset countdown for the primary semantic window of each provider.
- As a user, I see whether current consumption is on pace, in reserve, in deficit, or likely to run out before reset when the necessary duration and reset evidence exists.
- As a user, I do not see a forecast when the reset, window duration, provider identity, or usage value is unknown or implausible.
- As a user, I receive at most one warning for a provider, identity, semantic window, and reset cycle unless the state first recovers.
- As a user with multiple usable identities in a future provider adapter, constrained identities sort first and the healthiest proven alternative may be identified, but SidePulse never switches accounts automatically.
- As a user, local transcript coverage says whether totals are missing, complete, partial, or failed without revealing any filesystem path.

### Reflect

- As a user, I can review today, seven days, or thirty days of agent outcomes and usage for context and enjoyment.
- As a user, I can see counts of started, completed, failed, and attention-needed episodes, plus bounded active-time estimates and provider usage totals.
- As a user, I can see a quiet outcome timeline that collapses repeated tool and worker lifecycle churn by identity.
- As a privacy-conscious user, I can clear history and verify that only aggregate derived facts were stored.
- As a user with partial source coverage, the history keeps valid facts and marks the affected provider or day partial instead of deleting sibling data.

## Information Architecture

### Status item

The status item remains compact. Its identity and accessibility label use the highest-priority current state:

1. needs user;
2. fresh failure;
3. active work;
4. fresh completion;
5. capacity risk;
6. idle.

No continually blinking state is allowed. Transition signals repeat at most twice, then resolve to a stable icon, tooltip, or color.

### Menu

The menu order is:

1. Agent Mailbox summary and shelves.
2. Capacity card.
3. Today summary.
4. Devices and automatic glance mode.
5. Settings and application actions.

Mailbox rows gain local `Watch`, `Pin`, and `Snooze` actions only when their contracts permit them. The Capacity card remains one stable hosted view and updates in place during menu tracking. `Today` is a compact summary with a submenu for daily details, not an always-visible chart.

### Settings

Settings gains:

- a History pane with today, seven-day, and thirty-day aggregates;
- retention and clear controls;
- pace and capacity-warning controls;
- automatic idle hardware display controls;
- provider coverage and diagnostic summaries with no paths by default.

## State and Data Boundaries

### Mailbox preferences

`AgentMailboxPreference` stores provider-neutral agent identity, pinned state or pin order, optional snoozed-until epoch, snoozed-at epoch, and last-visited epoch. It is bounded to 100 retained primary identities.

Snooze is visibility-only. It never stops an agent. Invalid or expired timestamps fail visible. A fresh actionable request always appears. A new failure or completion after `snoozed_at` wakes the agent early. A failure or completion already present when the user snoozes stays snoozed because the user has acknowledged that state.

### Capacity pace

`CapacityPace` is pure and provider-neutral:

- expected used percentage from elapsed window time;
- actual used percentage;
- signed delta;
- remaining headroom;
- optional runout ETA;
- whether it will last to reset;
- confidence source: `linear`, `historical`, or absent.

Linear pace requires credible used percentage, reset epoch, and duration. It is suppressed near the start until at least three percent of the window has elapsed, for expired or overlong resets, and for synthetic or reset-only windows. Historical pace requires a minimum sample count, the same provider and account discriminator, the same semantic duration, chronological samples, and reset-cycle equivalence. An explicit user schedule may override learned history later, but is not part of this tranche.

### Private operator history

`OperatorHistoryDay` stores only:

- local day key and timezone offset;
- provider identifier;
- started, completed, failed, and attention-episode counts;
- bounded active seconds;
- input, cached input, and output token totals when already available;
- local cost estimate when already available;
- coverage status and sample count;
- capacity samples containing semantic window, used percentage, reset epoch, and collection epoch.

No agent title, session title, prompt, message, command, tool argument, raw error, cwd, file path, user name, account email, or credential is persisted. In-memory timeline rows may hold sanitized product-owned activity labels, but persistence stores category and counts only. Default retention is 30 days, configurable to 7, 30, or 90 days. The file uses the existing private atomic I/O and bounded retention primitives.

### Provider capabilities

Provider adapters publish capabilities rather than forcing every provider through Codex or Claude assumptions:

- live agent events;
- actionable approval or input;
- direct navigation;
- transcript usage;
- remote quota windows;
- reset metadata;
- account identity;
- account switching;
- history attribution.

Unsupported capabilities remain absent. SidePulse does not synthesize them.

## Hardware and Screen Bar Semantics

Automatic glance mode uses one priority resolver for physical and virtual surfaces:

1. actionable attention takes over with the existing finite signal;
2. active work uses the current full-line relay or spatial program;
3. a fresh failure or completion uses one or two finite repetitions;
4. when agents are quiet, the optional capacity horizon shows the most constrained credible window;
5. otherwise the device uses the configured resting glow.

Capacity horizon maps remaining percentage to the full line, not just the first two LEDs. Filled positions use the provider color, the boundary position breathes gently only when the window is at risk, and empty positions remain at the calibrated resting floor. A two-LED device uses continuous blend and intensity, while an eight-LED surface uses position plus intensity. The same normalized headroom and phase feed both surfaces.

The Screen Bar may add a short secondary capacity bracket when idle, but active agent identity always wins. Alcove geometry continues using the rounded, clamped bracket contract. No text is drawn into the notch region.

## Quiet History Projection

The history timeline includes semantic events only:

- agent started;
- agent needs approval or input;
- agent completed;
- agent failed;
- capacity warning began or recovered;
- device disconnected or recovered;
- provider coverage became partial, failed, or recovered.

Repeated tool updates, progress ticks, context events, and worker internals collapse by provider-neutral identity. A surface that hides internal worker rows must still preserve the worker terminal outcome in its parent rollup. Duplicate and out-of-order events canonicalize before aggregation.

## Error Handling

- Missing roots, empty roots, partial scans, and failed scans remain distinct.
- A failed provider refresh retains only that provider's last-known-good data.
- A successful explicit empty response clears previous provider windows.
- Reset callbacks preflight the complete provider group. If any member is in flight or in backoff, none starts and one bounded retry wake-up remains.
- Clock rollback or a large forward jump causes timers and snoozes to reproject from persisted epochs, never to loop.
- Menu tracking uses common run-loop modes for countdown, reset, snooze, and woke-marker timers.
- Device writes remain deduped and failure-visible. History records device health transitions, not repeated identical failures.
- Persistence failure retains dirty state and never advances a successful-write timestamp.

## Adversarial Acceptance Matrix

### Agent volume and ordering

- 1,000 current records, 100 primary identities, and 900 workers stay bounded.
- duplicate identities with stale actionable copies do not resurrect asks.
- activity changes do not reorder stable rows.
- a pinned row stays pinned without suppressing attention priority.
- a snoozed row wakes for a new ask, failure, or completion, but not for unchanged working activity.

### Capacity and clocks

- monotonic freshness and epoch reset clocks are never compared directly.
- exact 24-hour countdown transitions, multi-day hour transitions, sub-day minute transitions, early callbacks, late callbacks, sleep, wake, timezone changes, and wall-clock jumps stay bounded.
- multiple windows and providers at one reset form one atomic refresh group.
- malformed reset aliases do not hide valid fallback metadata.
- provider identity changes invalidate old reset, pace, and warning keys.

### History and privacy

- raw prompts, messages, paths, commands, authorization values, and secret-shaped strings cannot enter persisted history or UI labels.
- corrupt, oversized, symlinked, hard-linked, and parent-swapped history targets fail closed through private I/O.
- retention caps records and file size deterministically.
- valid sibling totals survive malformed, unreadable, or replaced files with partial coverage.
- history clearing removes only the intended private history file and resets in-memory projections.

### UI and accessibility

- menu opens with 100 primary agents without row movement during tracking.
- stable hosted views update without replacement or fallback text frames.
- VoiceOver labels include provider, semantic state, remaining amount, reset, and staleness without relying on color.
- reduced motion replaces continuous decorative motion with state-preserving static output.
- sleep, wake, Low Power Mode, and thermal pressure preserve semantics.

### Hardware

- two-LED and eight-LED surfaces cover the whole line for relay and capacity horizon.
- Screen Bar and physical devices share normalized semantic phase.
- deduped physical writes do not suppress a needed virtual update.
- device absence, disconnect, ENOSPC, partial write, and stale program are visible and never reported as successful.

## Implementation Tranches

1. Finish reset-aware Capacity and transcript coverage already in progress.
2. Add pure mailbox watch, pin, snooze, woke, and stable preference projection, then AppKit actions and timers.
3. Add pure capacity pace with conservative evidence gates, then menu presentation and reset-cycle warning dedupe. Modern system notification delivery remains dependent on explicit approval for its production framework dependency.
4. Add the private operator-history ledger, daily aggregation, quiet projection, Settings history surface, retention, and clear action.
5. Add automatic hardware glance resolution and capacity horizon using one normalized physical and virtual contract.
6. Run guarded source, package, installed UI, accessibility, sleep/wake, low-power, and physical-device acceptance.

Each tranche uses strict failing production tests before source changes and independent adversarial review before the next tranche starts.

## Deferred Boundaries

- SidePulse will not switch provider accounts automatically. Future adapters may expose a user-initiated best-candidate action only when account identity and activation are proven.
- Modern UserNotifications delivery remains blocked until the user explicitly approves the missing production framework dependency or the existing environment supplies it without a new dependency.
- Remote fleet sync, iOS Live Activities, and cross-machine history are separate products. They are not prerequisites for the best local Mac and hardware experience.
- A separate large dashboard window is deferred unless the native menu and Settings history surface prove insufficient in installed usability testing.
