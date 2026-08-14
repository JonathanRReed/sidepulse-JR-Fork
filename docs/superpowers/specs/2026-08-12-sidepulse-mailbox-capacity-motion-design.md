# SidePulse Mailbox, Capacity, Motion, and Relay Design

**Date:** 2026-08-12

**Status:** Approved for implementation by the user's direction to proceed without follow-up questions.

## Goal

Make SidePulse feel like one calm, trustworthy command center for local agents and their hardware presence. The interface must answer four questions quickly:

1. Which agents need me now?
2. What is still running, ready for review, or recently finished?
3. How much provider capacity remains, and when does it reset?
4. Does the Screen Bar and physical device express the same lifecycle truth smoothly?

The design borrows proven ideas from the locally inspected T3 Code snapshot at `b73232bdd31e83914a8a943960c7dc4b6390b39b` and CodexBar snapshot at `c4ed34d0e44a75ae2d578525d0287e9b49cfa341`. Those repositories remain read-only references. SidePulse keeps its own product language, AppKit architecture, provider model, and hardware constraints.

## Product Principles

- Attention is scarce. Only live approvals and input requests occupy persistent `Needs You` space.
- Failures are visible and durable as state, but their animated cue remains finite at exactly two repetitions.
- Activity never reshuffles a busy list. Stable lifecycle order makes muscle memory possible.
- Provider capacity is semantic. A five-hour window and a seven-day window are named by duration and provider meaning, not forced into generic daily or weekly labels.
- Freshness is visible. Missing, loading, fresh, stale, partial, and error states must not collapse into a single percentage.
- Motion follows the display. The normal Screen Bar animation is driven by display refresh, with existing power and thermal caps retained as deterministic fallbacks.
- Physical and virtual surfaces share one phase fraction. Recomputing a relay program must not restart the baton at LED 1.
- High-volume sessions are summarized before they are expanded. Main agents stay primary, workers remain attached to their parent, and the menu remains bounded.
- No raw prompts, tool inputs, transcript bodies, tokens, paths, or secrets appear in activity labels, notifications, logs, or menus.

## Comparative Findings

### T3 Code ideas to adopt

T3 Code projects server state into an explicit lifecycle vocabulary, then derives attention, working, failure, readiness, and completion presentation from that projection. Its web and mobile lists keep active rows in stable order, put status inside the row, separate snoozed and settled history, and treat worker hierarchy as first-class rather than flattening every process into the top level.

SidePulse should adopt:

- one pure mailbox projection shared by menu summary and shelves;
- stable first-seen ordering within a lifecycle episode;
- explicit `Needs You`, `In Progress`, `Ready for Review`, and `Recent` shelves;
- parent and worker hierarchy with bounded worker rollups;
- normalized, privacy-safe activity verbs;
- bounded visible rosters with truthful overflow counts;
- source coverage and partial-result semantics for transcript usage.

SidePulse should not copy T3-specific pull request, draft, or workflow routing that has no provider-neutral equivalent.

### CodexBar ideas to adopt

CodexBar models each usage window with used percentage, duration, reset time, remaining percentage, confidence, and last-known-good behavior. It refreshes on menu open, coalesces in-flight work, fences stale generations, schedules a refresh just after a reset boundary, and updates countdown text only when the displayed minute changes.

SidePulse should adopt:

- reset-aware window models and compact reset formatting;
- future-only reset backfill from the correct provider and account;
- reset-boundary refresh with grace, minimum delay, and duplicate suppression;
- minute-boundary countdown refresh without rescanning usage;
- provider-local error state so one failure cannot erase the other provider;
- explicit capacity ownership and age in the menu;
- account and settings generation fencing when identity is available.

SidePulse should keep its transcript-backed local usage scan. T3 Code supplies the stronger provenance and partial-coverage model, while CodexBar supplies quota and reset semantics.

## Information Architecture

### Menu summary

The status menu presents three stable entry points near the top:

- `Agent Mailbox`, with summary text such as `8 active · 2 need you · 3 ready`.
- `Capacity`, with provider-local compact status and reset text.
- Existing device and application controls, preserved below the agent information layer.

The summary counts come from the same pure mailbox projection used to build the submenu. The menu does not derive counts from raw provider statuses independently.

### Agent Mailbox shelves

`Needs You`

- Contains only actionable projected attention.
- Ordered by the start of the current request episode, oldest first.
- Keeps one-click navigation to the correct session.
- A worker request is actionable only when `subagent_asks_alert` is enabled.

`In Progress`

- Contains active main sessions in stable first-seen order.
- Activity updates change row text without changing row position.
- Workers are summarized under their parent as a count and submenu.
- Orphan workers are grouped under one bounded `Background agents` rollup.

`Ready for Review`

- Contains plan-ready work, unseen completions, and visible failed outcomes.
- A failure row is durable until lifecycle truth changes, but does not receive a persistent Ask badge.
- Completion visibility is independent of visual sweep, notification, and webhook settings.

`Recent`

- Contains seen completions and recently idle sessions within the existing freshness horizon.
- Entries expire using the central freshness policy.

Each shelf shows at most 12 primary rows. Overflow is represented by a disabled `N more` row or an existing full-list destination. The pure projection retains at most 100 primary identities and uses deterministic eviction of expired recent rows before live rows.

### Row content

Each primary row contains:

- provider and agent identity using existing colors and labels;
- one lifecycle label;
- one normalized activity label when useful;
- worker count when nonzero;
- age only when it helps interpret stale or recent state;
- existing action submenu and navigation target.

Normalized activity labels are bounded to product-owned vocabulary such as `Running command`, `Reading files`, `Editing files`, `Searching files`, `Thinking`, `Waiting for approval`, and `Using <tool name>`. Raw arguments and payloads are never displayed.

## Capacity Model

### Window representation

Extend `UsageWindowViewModel` with enough semantic state to render:

- label;
- duration in minutes when known;
- percent used and percent remaining;
- reset timestamp when plausible and in the future;
- compact reset text;
- whether usage and reset data are known;
- confidence or provenance summary when available.

Local transcript totals carry provider-local coverage metadata: whether the source root exists, whether it was walked, files discovered, cache hits, files read, malformed candidate lines, unreadable files, skipped symlinks, and duplicate physical files. SidePulse deduplicates physical sources by device and inode before parsing, never displays raw source paths, and labels a provider `partial` when malformed or unreadable inputs could make the total incomplete.

Percent values are clamped to 0 through 100. Boolean values are not accepted as numeric usage. Reset timestamps in the past or more than 366 days ahead are rejected. A successful provider result with an explicit empty window list clears old windows. An error keeps provider-matching last-known-good windows and marks them stale.

### Menu presentation

The existing stable AppKit custom menu item is retained and renamed from `Usage` to `Capacity`. It expands vertically enough to show two compact lines per provider without truncating the provider identity:

- primary line: semantic window and remaining amount, for example `5h 38% left`;
- secondary line: reset countdown and age, for example `resets in 1h 24m · updated 2m ago`.

Missing, loading, partial, error, and stale states remain visually distinct in text. The view is mutated in place while the menu is open, preserving AppKit menu stability.

### Refresh policy

Retain the existing provider-aware generation fencing, shared transcript scan, independent adapter results, coalescing, retry, and backoff. Add:

- a pure reset formatter;
- a pure reset-boundary planner;
- a one-shot reset timer owned by the controller;
- a lightweight minute-boundary timer that redraws countdown text but does not scan providers;
- reset candidates from every current provider window;
- reset refresh at `reset_at + grace`, never earlier than five seconds from now;
- no reset timer if the normal provider refresh is already due first;
- duplicate attempted reset boundaries bounded to 64 entries;
- immediate invalidation when provider settings or account identity changes.

Provider refreshes remain isolated. A local transcript scan failure does not block Codex or Claude quota adapters, and one provider adapter failure does not block the other.

## Screen Bar Motion and Alcove Geometry

### Motion driver

The current 60 Hz `NSTimer` is a good fallback but can drift against the actual display scan. On supported macOS versions, active animation should use the public AppKit display-link API exposed by the current PyObjC runtime. The callback drives one sample and one repaint at native display cadence.

The driver decision is pure and deterministic:

- hidden or display asleep: paused;
- active animation, nominal power and thermal state, display-link API available: display link;
- active animation under low-power or thermal caps: existing capped timer cadence;
- static watcher: existing low-frequency timer cadence;
- display-link API unavailable or creation fails: existing timer cadence.

Display-link creation, invalidation, and replacement follow window visibility, screen movement, sleep, wake, and teardown. A given lifecycle owns at most one active driver. Menu tracking must not freeze the timer fallback.

The view samples the WASM output once per display callback when display-link driven. Existing smoothing stays time-based. Static-frame demotion cannot occur merely because a timer callback landed between display frames.

### Alcove geometry

Alcove wings use a corner radius aligned with the existing notch-bottom language. The bracket radius becomes 8 points, clamped to half of the bracket's width and height. This is a small silhouette refinement, not a larger capsule or added ornament.

The current Alcove width hysteresis, screen bounds, click-through behavior, standing gauges, and cached geometry remain unchanged.

## Relay Motion Contract

`effective_speed_seconds(BLEND_MODE_RELAY)` means one full traversal of the available LED line, not the duration spent on each LED.

For `led_count > 0`:

- `traversal_ms = effective_speed_seconds * 1000`;
- `step_ms = traversal_ms / led_count`;
- the head visits every index exactly once during each traversal;
- the order wraps modulo `led_count`;
- two-LED and eight-LED devices obey the same contract.

Program rewrites preserve wall-clock phase. The controller owns a stable monotonic relay epoch and derives one elapsed duration from it. Each renderer maps that duration into its own LED count and rotates the LED order so the physical write and virtual preview continue at the same fractional position instead of restarting at index zero.

The physical device and Screen Bar receive programs generated from the same elapsed duration. When their LED counts match, the rotated programs match. When a two-LED device is mirrored by the eight-LED Screen Bar, each starts at the index corresponding to the same traversal fraction. The virtual view receives the physical write completion timestamp as its parse anchor when hardware is present. When no hardware is present, the virtual path uses its own monotonic generation timestamp with the same global phase fraction.

Attention preambles remain higher priority and retain existing program size and line-count limits. Static color fallback functions remain explicitly non-temporal and must not be used as proof of relay traversal.

## Error Handling

- Display-link setup failure logs a bounded diagnostic and falls back to the current timer. It does not hide the Screen Bar.
- A malformed or implausible reset is omitted while valid usage remains visible.
- A provider failure preserves only provider-matching last-known-good data and marks it stale.
- A mailbox row without a valid navigation target remains visible but disabled.
- Unknown tools receive a sanitized `Using <tool>` label.
- Relay with zero LEDs returns no instructions. Relay with one LED preserves the existing single-agent program.

## Verification

Source verification requires:

- pure table tests for mailbox shelves, stable ordering, worker rollup, privacy-safe activity, bounds, and navigation targets;
- pure table tests for reset formatting, reset-boundary scheduling, stale and error behavior, explicit empty success, and provider isolation;
- display-driver tests for nominal vsync, capped timer fallback, hidden and sleep pause, screen-change replacement, and teardown;
- geometry tests for the 8-point clamped Alcove radius;
- temporal WASM tests proving relay visits all indices within one traversal on two and eight LEDs;
- a rebuild test proving relay resumes at the wall-clock phase instead of index zero;
- Ruff, the full guarded suite, and `git diff --check`.

Installed acceptance requires a rebuilt, signed candidate and direct observation of:

- Screen Bar motion on the actual display, including menu-open and screen-wake behavior;
- Alcove silhouette against the real notch;
- relay traversal on the mounted physical SidePulse device and the virtual bar;
- capacity reset countdown and reset-boundary refresh;
- high-volume mailbox behavior with many main sessions and workers;
- no unintended hardware writes during the source test suite.

Source tests cannot be reported as proof of the installed AppKit or physical-device experience.
