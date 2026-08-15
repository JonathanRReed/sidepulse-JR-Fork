# SidePulse production platform design

Date: 2026-08-15
Status: approved direction, implementation pending
Base: `agent/sidepulse-rescue` at `cd77de1d69b930252a59fc479ba2ddb37c63a6a1`

## 1. Purpose

This specification turns the existing SidePulse rescue, JR-BAR vision, master plan, and improvement notes into one production architecture. It covers the work requested after the rescue:

1. eliminate visible lag and sustained background cost;
2. replace full-application refreshes with bounded, event-driven updates;
3. define a stable integration SDK;
4. integrate CodexBar without copying its authentication and provider logic;
5. add a first-class T3 Code compatibility mode;
6. build a coherent native UI system instead of extending the existing settings monolith;
7. replace ad hoc effects with Animation Studio 2 and a safe cross-surface motion system;
8. establish production telemetry, security, migration, test, packaging, and release gates.

The product remains an ambient attention system. T3 owns orchestration. CodexBar owns provider accounting. SidePulse decides what matters now and expresses that decision consistently through the physical light, Screen Bar, menu-bar glance, Command Center, and diagnostics.

## 2. Decisions

### 2.1 Chosen approach: incremental strangler architecture

Three implementation approaches were considered.

#### A. Continue hardening the PyObjC application in place

Advantages:

- smallest packaging change;
- preserves every existing behavior and permission grant;
- fastest route to isolated bug fixes.

Disadvantages:

- leaves the 752 KB controller, 209 KB settings window, and broad main-thread ownership as permanent product constraints;
- makes T3, CodexBar, richer Studio features, and additional windows harder to add safely;
- keeps animation and list performance dependent on a large dynamic Python/AppKit boundary.

This approach is retained only as the compatibility bridge during migration.

#### B. Rewrite the complete application in Swift in one release

Advantages:

- clean native architecture immediately;
- best long-term access to SwiftUI, Core Animation, WidgetKit, accessibility, and unified logging.

Disadvantages:

- recreates years of provider, policy, capacity, history, hardware, installer, and safety behavior at once;
- creates a long parity gap with weak regression confidence;
- risks TCC continuity and hardware reliability.

This approach is rejected.

#### C. Incrementally split a tested core from the UI, then replace the host

Advantages:

- preserves the mature Python domain logic and hardware stack;
- lets each UI surface move independently;
- provides a stable boundary for T3 and CodexBar before either touches AppKit;
- enables measurable performance improvements before the native host is complete;
- supports rollback at every stage.

Disadvantages:

- temporarily maintains both a legacy host and a native host;
- requires a versioned local protocol and migration discipline.

This is the selected approach.

### 2.2 Product boundaries

SidePulse remains read-mostly and attention-oriented.

- It may acknowledge, allow, deny, answer, mute, snooze, and deep-link to an external tool when those capabilities are explicitly exposed.
- It does not directly edit repositories, create commits, push branches, merge pull requests, or run arbitrary shell commands.
- T3 actions that mutate a repository remain in T3. SidePulse opens the exact T3 thread, diff, worktree, or pull request where the user can perform the action.
- CodexBar credentials, browser sessions, provider tokens, and provider-specific refresh logic remain owned by CodexBar.

The existing bundle identifier, LaunchAgent identity, state paths, and permission-bearing application identity stay unchanged throughout the migration.

## 3. Production outcomes

The work is complete only when all of the following are true.

### 3.1 Responsiveness

- First usable menu-bar state appears within 500 ms after normal warm launch.
- Opening the menu or Command Center has a P95 main-thread duration below 50 ms.
- Switching a settings, Studio, or diagnostics section has a P95 main-thread duration below 100 ms.
- No routine main-thread task exceeds 16 ms while the interface is interactive.
- Menu tracking performs no provider scan, recursive directory walk, hardware write, fsync, process probe, network request, or settings serialization.
- Repeated UI actions do not trigger a full canonical ingestion pass.

### 3.2 Background cost

Measured on the owner's primary Mac after a five-minute warm period:

- Screen Bar hidden and no active integration changes: median CPU below 1%;
- static Screen Bar visible: median CPU below 1.5%;
- gentle motion: median CPU below 3%;
- active transition: no sustained CPU budget, but the transition must return to the resting budget within two seconds of completion;
- memory reaches a stable plateau after repeatedly opening and closing every major surface;
- no unbounded process, event, metric, image, menu-row, or animation cache.

These are release gates, not aspirational targets. A release may document an approved exception only when Instruments evidence identifies an operating-system cost outside SidePulse's control.

### 3.3 Integration quality

- CodexBar data is source-attributed, freshness-aware, cached, and never fetched synchronously from a UI callback.
- T3 sessions preserve the T3 surface, actual provider, provider instance, project, thread, turn, branch, worktree, and pull-request identity.
- T3 activity never becomes a fake `provider="t3"` row that loses the underlying harness.
- Unknown integration versions degrade visibly and safely instead of silently returning idle.
- Every integration declares capabilities. UI actions appear only when the adapter declares and successfully negotiates them.

### 3.4 Visual system

- The physical device, Screen Bar, menu-bar glance, and rich windows consume one canonical semantic state.
- Each surface has a distinct information budget. The same fact is not redundantly forced onto every surface.
- Every critical state remains distinguishable in grayscale, Reduce Motion, and Differentiate Without Color modes.
- No emitted program can violate the flash safety envelope.
- Studio recipes preview and validate against every supported surface before commit.

## 4. Target topology

The migration has three topology stages. All stages use the same core interfaces.

### 4.1 Stage 1: in-process core boundary

```text
Legacy PyObjC host
  -> CoreRuntime interface
  -> canonical store and reducers
  -> surface descriptors
  -> existing AppKit, Screen Bar, and hardware adapters
```

The first objective is architectural, not process separation. Existing behavior moves behind explicit interfaces while remaining in one process. This produces immediate performance wins without changing packaging or permissions.

### 4.2 Stage 2: supervised core helper

```text
SidePulse.app host
  |-- TCC-sensitive host services
  |     Screen Bar windows, notifications, accessibility, Focus, screen geometry
  |
  `-- signed core helper child
        provider ingestion, canonical state, policy, history,
        hardware writes, CodexBar bridge, T3 bridge supervision
```

The helper is launched and supervised by the host. It is not a separate login item or independently persistent daemon. It exits when the host exits. This avoids a second user-visible service and avoids moving TCC-sensitive operations into a new executable identity.

The host and helper communicate through an inherited Unix socket pair. The socket is not published globally. The host generates an ephemeral 256-bit session secret and sends it over an inherited file descriptor, never through command-line arguments or environment visible to unrelated processes.

### 4.3 Stage 3: native host

```text
Native SwiftUI/AppKit host
  -> same versioned CoreRuntime protocol
  -> same signed core helper
```

The native host replaces the legacy host one surface at a time. The Python core remains authoritative for providers, policy, history, and hardware. The old host remains buildable behind a development fallback until native parity and migration tests pass.

## 5. Core model

### 5.1 Canonical facts

Provider and integration adapters emit facts. They never directly update UI or hardware.

```python
@dataclass(frozen=True, slots=True)
class SessionIdentity:
    surface: str                 # direct, t3code, cursor, remote, etc.
    provider_driver: str         # codex, claude, opencode, etc.
    provider_instance: str | None
    machine_id: str
    project_id: str | None
    thread_id: str
    turn_id: str | None
    parent_thread_id: str | None

@dataclass(frozen=True, slots=True)
class SessionContext:
    title: str
    repository: str | None
    branch: str | None
    worktree_path: str | None
    pull_request: PullRequestRef | None
    model: str | None
    reasoning_effort: str | None

@dataclass(frozen=True, slots=True)
class SessionFacts:
    identity: SessionIdentity
    context: SessionContext
    process_alive: bool | None
    last_activity_at: datetime | None
    pending_request: PendingRequest | None
    last_error: ErrorFact | None
    latest_turn: TurnFact | None
    children: tuple[ChildFact, ...]
    usage: UsageFact | None
    source_health: SourceHealth
```

No adapter stores the final presentation state. Lifecycle is derived by a pure reducer with one precedence table. Completion timestamps win over stale running flags. Pending approval or user input wins over normal activity. Broken source health remains separate from an honestly idle session.

### 5.2 State snapshots and deltas

The canonical store owns monotonically increasing generations.

```python
@dataclass(frozen=True, slots=True)
class CoreSnapshot:
    schema_version: int
    generation: int
    sessions: tuple[SessionView, ...]
    providers: tuple[ProviderView, ...]
    devices: tuple[DeviceView, ...]
    signals: tuple[SignalView, ...]
    diagnostics: DiagnosticsView

@dataclass(frozen=True, slots=True)
class StateDelta:
    schema_version: int
    from_generation: int
    to_generation: int
    changed_domains: frozenset[Domain]
    upserts: tuple[EntityUpsert, ...]
    removals: tuple[EntityRef, ...]
```

Consumers may request a full snapshot when generations do not line up. Normal updates use deltas.

### 5.3 Bounded coalescing

- Provider events are reduced immediately off the main thread.
- Bursts are coalesced for at most 50 ms before publishing one UI delta.
- Urgent transitions such as `needs_you`, `broken`, and resolved requests bypass normal coalescing.
- Each domain has a latest-wins capacity-one pending update where intermediate states have no user value.
- Ledger and history persistence are append or batch operations on worker queues, never synchronous UI work.
- Backpressure is visible in diagnostics. It is never an unbounded queue.

## 6. Local protocol

The host/helper protocol is a four-byte big-endian length prefix followed by UTF-8 JSON. Framing is deterministic and easy to inspect. Payloads use tagged unions and integer schema versions.

Every envelope contains:

```json
{
  "protocol": 1,
  "messageId": "uuid",
  "kind": "snapshot|delta|command|result|event|error|ping|pong",
  "generation": 42,
  "sentAt": "2026-08-15T20:00:00.000Z",
  "payload": {}
}
```

Protocol rules:

- maximum frame size is 4 MiB;
- malformed or oversized frames close the connection;
- unknown optional fields are ignored;
- unknown required message kinds fail negotiation;
- commands are idempotent where possible and carry correlation IDs;
- the host sends heartbeat and foreground/visibility state;
- the helper reports queue depth, worker health, integration health, and last successful persistence;
- no transcript body, token, cookie, or credential appears in routine logs;
- protocol fixtures are shared between Python and Swift tests.

## 7. Performance architecture

### 7.1 Remove `refresh_()` as a universal operation

The current full refresh remains temporarily as a cold-start and recovery path. It is not called by routine settings handlers, menu opening, pane switching, hover preview, animation frame delivery, or provider event arrival.

New operations are scoped:

```text
refresh_provider_state(provider_instance)
refresh_device_inventory()
refresh_capacity(provider_instance)
reconcile_menu(delta)
reconcile_screen_bar(delta)
reconcile_hardware(delta)
commit_settings(changed_keys)
refresh_visible_settings_section(section_id)
```

Every scoped operation has an explicit input and output. Hidden sections are not repainted.

### 7.2 UI reconciliation

Menu and list surfaces maintain stable row identities.

- Rows are keyed by canonical entity ID.
- Activity never reorders live sessions.
- Section changes use prefix/suffix or collection-diff reconciliation.
- Text or accessory changes patch an existing row.
- A tracked menu is never reconstructed wholesale.
- Accessibility labels are generated from model data, never read back from AppKit.
- UI builders are pure projections over immutable state.

### 7.3 Persistence

- Settings changes are debounced for 300 ms and committed atomically on a dedicated serial writer.
- Closing a window or terminating the app flushes pending settings within a bounded deadline.
- Ledger and history stores batch writes by time and count.
- Hardware program writes remain atomic and deduplicated.
- No settings or history path shares a temporary filename with another writer.

### 7.4 Render path

The Screen Bar render callback receives only immutable sampled colors and cached geometry.

- Program parsing and stepping remain off the main thread.
- Geometry rebuilds occur only when screen, notch, layout, accessibility, or surface settings change.
- Paint objects are cached by bounded structural keys.
- Identical frames do not call `setNeedsDisplay`.
- The display callback never waits on a lock held by a worker.
- Slow motion uses an honestly deliverable cadence.
- Static output is event-driven with a low-frequency integrity watch.
- Hidden or sleeping surfaces perform no animation work.

## 8. Observability and diagnostics

### 8.1 Instrumentation

Native code uses `Logger` and `OSSignposter`. Python emits the same categories through the unified logging bridge and maintains bounded in-memory metric reservoirs.

Required categories:

- `Launch`
- `CoreRuntime`
- `ProviderIngestion`
- `StateReduction`
- `MenuProjection`
- `MenuApply`
- `Settings`
- `ScreenBar`
- `HardwareWrite`
- `Persistence`
- `CodexBarIntegration`
- `T3Integration`
- `Migration`
- `ReleaseHealth`

Required spans and counters:

- warm and cold launch milestones;
- event receive to canonical reduction;
- reduction to published delta;
- menu open and menu apply;
- visible section construction and update;
- Screen Bar sample age, delivered FPS, dropped frames, stale samples, geometry build, paint build;
- hardware write latency and dedup rate;
- provider and integration fetch latency;
- worker queue depth, replaced pending work, failures, and circuit state;
- settings and history write latency;
- helper reconnects and protocol resets.

### 8.2 Performance page

Diagnostics includes a live local-only performance page:

- current and five-minute CPU;
- resident memory;
- longest main-thread task;
- menu open P50/P95;
- settings switch P50/P95;
- last core reduction duration;
- per-provider event and refresh age;
- worker queue depths;
- Screen Bar requested and delivered FPS;
- stale samples, dropped frames, and deduped frames;
- hardware write count and P95 latency;
- cache occupancy and hit rates;
- helper and integration process health.

A one-click capture exports redacted metrics, recent high-signal logs, schema versions, integration versions, permissions, and active feature flags. It excludes credentials, prompts, transcript bodies, file contents, and raw command output.

## 9. Integration SDK

### 9.1 Adapter contract

```python
class IntegrationAdapter(Protocol):
    @property
    def identity(self) -> IntegrationIdentity: ...

    async def negotiate(self) -> CapabilitySet: ...
    async def start(self, sink: FactSink) -> None: ...
    async def snapshot(self) -> IntegrationSnapshot: ...
    async def perform(self, action: IntegrationAction) -> ActionResult: ...
    async def stop(self) -> None: ...
```

Capabilities are explicit:

```text
session.lifecycle
session.pending-request
session.respond
session.open
thread.metadata
thread.plan
thread.diff
thread.children
repository.branch
repository.worktree
pull-request.reference
usage.windows
usage.credits
usage.cost
provider.incidents
source.freshness
```

An adapter may be read-only. Unsupported actions are absent, not disabled buttons with speculative behavior.

### 9.2 Adapter lifecycle

- discovery is separate from activation;
- activation is explicit in Settings;
- adapters run off the main thread;
- each has a timeout, retry policy, exponential backoff, circuit breaker, output-size limit, and last-known-good cache;
- adapters publish source health independently from session state;
- startup failures do not prevent SidePulse from launching;
- stopping an adapter cancels its workers and removes its facts through one deterministic teardown delta;
- version incompatibility produces a visible diagnostics state and actionable remediation.

## 10. CodexBar integration

### 10.1 Ownership

CodexBar remains the source of truth for provider usage, credits, costs, reset windows, account identity, fetch source, and provider incidents. SidePulse does not import browser cookies, decrypt provider credentials, duplicate OAuth, or scrape provider dashboards.

### 10.2 Connection strategy

Preferred path:

1. discover `codexbar` and validate its version;
2. launch `codexbar serve` as a supervised child on loopback;
3. generate a random dashboard token and pass it through `CODEXBAR_DASHBOARD_TOKEN`, never through process arguments;
4. query `/health` and `/dashboard/v1/snapshot`;
5. poll at an adaptive cadence and retain last-known-good snapshots;
6. terminate the child when integration is disabled or SidePulse exits.

Fallback path:

- use `codexbar dashboard --timeout 30` as a one-shot structured snapshot;
- cap stdout and stderr;
- parse only the documented dashboard schema;
- never invoke an interactive cookie or Keychain refresh from the background.

SidePulse does not connect to a non-loopback CodexBar server in the first production release.

### 10.3 Projection

CodexBar rows preserve:

- provider and configured display order;
- account identity, subject to the user's redaction preference;
- plan and source;
- primary, secondary, and additional usage windows;
- reset timestamps;
- pace and projected exhaustion when supplied;
- credits, code-review allowance, and provider-specific detail sections;
- provider incident state;
- snapshot age and last successful refresh;
- row-level errors without discarding healthy providers.

### 10.4 Presentation

- Menu-bar glance may show the single most constrained enabled window.
- Command Center shows every enabled provider and account.
- Physical LEDs do not display quota by default.
- Screen Bar may show a finite capacity warning only when a configured threshold is crossed.
- Diagnostics shows the CodexBar version, connection mode, source age, last error, and cache state.

## 11. T3 Code compatibility mode

### 11.1 Identity model

T3 is a surface and orchestrator. It is not the provider.

A T3 session retains:

```text
surface = t3code
provider_driver = codex | claude | opencode | other
provider_instance = configured T3 provider instance
project_id
thread_id
turn_id
parent_thread_id
repository identity
branch
worktree path
pull-request reference
model and effort
runtime mode
interaction mode
```

The same underlying provider session observed directly and through T3 is reconciled through provider IDs, thread IDs, process evidence, and repository context. Duplicates never produce two urgent lights for one request.

### 11.2 Bridge

The T3 protocol is consumed through an optional bundled `sidepulse-t3-bridge` helper built from pinned T3 contracts. The helper owns Effect RPC/WebSocket serialization and emits the SidePulse integration protocol over an inherited pipe.

The bridge subscribes to:

- shell snapshots and deltas;
- thread snapshots and deltas;
- session state;
- turn lifecycle;
- pending approvals and user input;
- plan and diff updates;
- token usage;
- task and subagent lifecycle;
- branch, worktree, and pull-request references when available.

No accessibility or pixel scraping is used.

The bridge carries the T3 server version and protocol fingerprint. A mismatch disables mutable capabilities and requests a fresh snapshot. Unsupported versions remain visibly disconnected rather than presenting stale sessions as idle.

### 11.3 Authentication

- Local loopback T3 environments are supported first.
- Pairing material is stored in Keychain by the SidePulse host.
- WebSocket tickets are requested through T3's documented authenticated HTTP route.
- Long-lived credentials are not placed in query strings, logs, environment dumps, or command-line arguments.
- Remote T3 environments require explicit pairing and are disabled until the adapter can negotiate an appropriate least-privilege scope.

### 11.4 State mapping

Examples:

```text
T3 session starting                         -> STARTING
active turn or active child task            -> WORKING
request.opened or user-input.requested       -> NEEDS_YOU
turn completed with unseen result            -> DONE_UNSEEN
session error or unrecoverable runtime error -> BROKEN
idle thread with no unseen result            -> SETTLED
```

A completed timestamp overrides a stale running state. Child-agent activity contributes to the parent ambient state but remains expandable in Command Center. Stable row order follows thread creation, never recent activity.

### 11.5 Actions

Initial production capabilities:

- open the exact T3 thread;
- open its diff;
- open the worktree in the configured editor;
- open the pull request;
- acknowledge or snooze the SidePulse item;
- respond to a pending approval or user-input request when T3 exposes an explicit request capability.

SidePulse does not directly commit, push, create a pull request, merge, or run arbitrary T3 commands. Those actions deep-link into T3.

## 12. Native UI system

### 12.1 Scene ownership

The native host has four durable surfaces.

#### Glance

A menu-bar extra or compact popover that answers:

- what needs the user;
- what is active;
- what finished unseen;
- which provider limit is most constrained;
- whether a source or integration is broken.

It is shallow, patchable, and opens within the menu budget.

#### Command Center

A real window containing:

- Needs You inbox;
- stable live session list;
- T3 project/thread/branch/worktree context;
- recently completed items;
- provider capacity cards;
- remote machines;
- deep links and safe actions.

Large lists are virtualized. Sections and rows use stable IDs. Selection never rebuilds the root layout.

#### Studio

A dedicated window for colors, motion, animation recipes, device previews, profiles, and imports/exports. It is not buried in Settings.

#### Diagnostics

A real window for decision traces, source health, permissions, performance, integrations, hardware health, migrations, and support export.

Settings contains only preferences, integrations, devices, notification policy, accessibility, and advanced behavior.

### 12.2 Native structure

```text
NativeHost/
  App/
    SidePulseApp.swift
    AppDelegate.swift
  Models/
    SnapshotModels.swift
    Selection.swift
    Commands.swift
  Stores/
    CoreStore.swift
    NavigationStore.swift
    StudioStore.swift
  Services/
    CoreClient.swift
    DeepLinkService.swift
    PermissionService.swift
    HostObservationService.swift
  Views/
    Glance/
    CommandCenter/
    Studio/
    Diagnostics/
    Settings/
  Support/
    Formatting/
    Accessibility/
    Logging/
```

The root scenes compose views. They do not own provider clients, subprocesses, persistence, or protocol decoding.

### 12.3 Interaction rules

- One stable split-view structure per window.
- Named commands and actions, not inline process logic.
- `@Observable` stores own scene state.
- AppKit bridges are narrow and limited to window, menu-bar, display-link, and other desktop-only edges.
- T3 and CodexBar source details are view-model data, not conditionals scattered across views.
- Empty, unconfigured, stale, permission-required, disconnected, and genuinely idle are separate states.

## 13. Animation Studio 2

### 13.1 Semantic recipe model

Animations are authored as semantic recipes, not raw LED text.

```json
{
  "schemaVersion": 1,
  "id": "provider-handoff",
  "name": "Provider Handoff",
  "durationMs": 1400,
  "loop": false,
  "layers": [],
  "accessibilityFallback": {},
  "surfaceOverrides": {}
}
```

Supported layer primitives:

- solid;
- linear and segmented gradient;
- breathe;
- pulse;
- chase;
- comet;
- ripple;
- wipe;
- converge;
- diverge;
- ping-pong;
- sparkle;
- edge pulse;
- progress fill;
- soft deterministic noise;
- provider handoff;
- static shape or identity marks.

Each layer declares timing, easing, direction, blend mode, intensity, repeat, and surface capability requirements.

### 13.2 Cross-surface compiler

```text
Semantic recipe
  -> safety and accessibility validator
  -> normalized timeline
  -> physical LED compiler
  -> Screen Bar renderer descriptor
  -> preview simulator
```

The physical compiler emits only firmware-supported grammar and validates the result with `sdled.wasm` before write. Unsupported layers receive an explicit documented fallback. The Screen Bar may render a richer version while preserving the same state, timing family, and accessibility meaning.

### 13.3 Safety envelope

The validator enforces:

- no general flashing above 2 Hz;
- no saturated red flashing above 0.5 Hz;
- no invalid indexed `off` segments;
- bounded brightness and transition slope;
- finite duration for one-shot cues;
- Reduce Motion static fallback;
- Differentiate Without Color shape or rhythm fallback;
- no program reaches hardware unless the real parser accepts it.

Unsafe imported recipes are rejected with a line- and layer-specific explanation. They never fall through to firmware error flashing.

### 13.4 Studio workflow

- provider and state identity are named and visible;
- current and proposed results appear side by side;
- every recipe previews on Dot, Pro, Screen Bar, Reduce Motion, high contrast, and dim/night profiles;
- hover preview is reversible and never persists;
- physical preview is opt-in and automatically times out;
- save, duplicate, rename, export, import, and restore-default operations are atomic;
- imported recipes are schema-validated, safety-validated, and size-limited;
- expert mode exposes generated LED DSL read-only first, with editable DSL retained behind an advanced warning and the same parser/safety gates.

### 13.5 First-party creative recipes

The first production library includes:

- Agent Baton: one provider hands work to another;
- Swarm Lanes: active children occupy bounded subsegments while the parent owns the base state;
- Review Runway: progress moves toward review-ready;
- Pull Request Opened: two sides converge to a stable center;
- Merge Success: opposing waves meet and dissolve into a short completion glow;
- Test Lifecycle: collecting, running, passed, and failed have distinct phases;
- Capacity Horizon: remaining allowance controls lit length and burn pace controls subtle motion;
- Capacity Deficit: an amber tail appears only when projected usage exceeds the reset horizon;
- Remote Arrival: motion direction identifies the remote machine's configured side;
- T3 Dirty Branch: a faint endpoint persists until the branch becomes clean;
- Waiting for Review: a low-motion violet ping;
- Deep Work: dim, slow, non-interrupting ambience;
- Failure Fingerprints: tool, auth, test, and disconnected-source failures have distinct safe rhythms.

Decorative recipes are opt-in. Default operation follows the four-state calm light language.

## 14. Security and privacy

- Local protocols use inherited descriptors or user-private sockets with `0600` permissions.
- Child processes receive sanitized environments.
- Executable discovery resolves trusted absolute paths and rejects writable or ambiguous locations unless explicitly approved.
- Subprocess stdout/stderr is bounded.
- All child operations have deadlines and termination escalation.
- CodexBar credentials remain in CodexBar.
- T3 pairing credentials live in Keychain and are used only for the configured endpoint.
- No integration may request interactive Keychain access during unattended refresh.
- Exported diagnostics redact account email local parts by default.
- Session prompts, transcript content, repository file contents, command output, tokens, cookies, and secrets are excluded from telemetry.
- Mutable request responses require a recent explicit user gesture and correlation to a still-open request.
- Deep links are allowlisted and structurally encoded. No user-controlled shell interpolation is permitted.

## 15. Persistence and migration

### 15.1 Schema ownership

Persistence is split by purpose:

- preferences;
- canonical ledger/history;
- integration configuration and last-known-good metadata;
- Studio recipes and profiles;
- migration journal;
- bounded diagnostics metrics.

Each store has an integer schema version, atomic writer, corruption recovery, and a size or retention bound.

### 15.2 Migration rules

- The existing state directory remains authoritative.
- Before a schema migration, SidePulse creates one private rollback snapshot.
- Migration steps are idempotent and journaled.
- Failure leaves the prior schema intact and launches in a visible degraded mode.
- Native host rollout does not change bundle ID, LaunchAgent label, hook paths, or permission-bearing identity.
- The legacy host can read the last legacy-compatible schema during the rollback window.
- Feature flags and migration versions appear in diagnostics.

## 16. Failure handling

- The host renders last-known-good state with a visible stale marker when the core reconnects.
- Core helper crashes use bounded restart backoff and a circuit breaker.
- A permanently failing integration is isolated. It does not restart the whole core.
- UI commands time out and return explicit failure results.
- An unavailable Screen Bar never blocks physical hardware updates.
- A hardware write failure never blocks menu updates.
- A corrupt optional history store does not prevent live session state.
- Unknown protocol versions fail negotiation and request an upgrade.
- All fallbacks are observable in Diagnostics and unified logs.

## 17. Test strategy

### 17.1 Python tests

- pure lifecycle and precedence reducers;
- snapshots and delta generation;
- coalescing and backpressure;
- persistence and migration;
- adapter capability negotiation;
- CodexBar dashboard fixtures, stale fallback, partial errors, and process supervision;
- T3 normalized event fixtures and duplicate reconciliation;
- animation recipe schema, safety, compiler, and hardware grammar;
- performance budget unit contracts;
- helper protocol framing and size limits.

### 17.2 Swift tests

- protocol decoding and generation recovery;
- store reconciliation;
- stable sorting and selection;
- Glance, Command Center, Studio, Diagnostics, and Settings view models;
- accessibility labels and reduced-motion projection;
- deep-link allowlisting;
- migration and feature-flag behavior.

### 17.3 Process-boundary tests

- launch the packaged helper and complete handshake;
- snapshot, delta, reconnect, and stale-generation recovery;
- kill and restart helper without losing the host;
- launch supervised CodexBar fixture server;
- launch T3 bridge fixture and replay shell/thread streams;
- verify output and stderr bounds;
- verify timeouts and termination escalation;
- install wheel/app into clean locations and exercise historical hook entrypoints.

### 17.4 UI and performance tests

On macOS:

- XCUITest opens every surface and performs primary actions;
- menu tracking test proves no blocked operations occur;
- Instruments or signpost harness measures launch, menu, pane switch, animation, and idle budgets;
- repeated open/close and integration reconnect loops check memory stability;
- VoiceOver labels, keyboard navigation, Reduce Motion, Differentiate Without Color, high contrast, and increased contrast are exercised;
- screenshots cover empty, active, needs-you, done, broken, stale, integration mismatch, and migration failure states.

### 17.5 Hardware and signing gates

- simulated Dot and Pro render tests run on every local verification;
- optional real-device smoke writes a known safe sequence and verifies mounted-volume behavior;
- app signing, helper signing, nested-code validation, TeamIdentifier, notarization, and stapling are mandatory for release;
- TCC continuity is verified by upgrading an installed prior build rather than testing only a clean app.

## 18. Release strategy

### 18.1 Feature flags

Production flags:

```text
core.delta_pipeline
core.helper_process
integration.codexbar
integration.t3
native_host.glance
native_host.command_center
native_host.studio
native_host.diagnostics
studio.v2
```

Flags are persisted, surfaced in Diagnostics, and removable after their rollback window closes. Hidden environment overrides exist only for development and tests.

### 18.2 Rollout order

#### Wave 1: performance evidence and scoped refresh

- add signposts and metrics;
- create performance diagnostics;
- remove full refresh from settings/menu interactions;
- scope settings updates to the visible section;
- add release budget harness.

Exit: current UI meets interaction budgets or every remaining blocker has Instruments evidence and an isolated follow-up.

#### Wave 2: canonical snapshots and delta pipeline

- add `CoreRuntime`, canonical store, snapshot, delta, and scoped surface projections;
- reconcile menu, Screen Bar, and hardware from deltas;
- keep full refresh only for cold start and recovery;
- move persistence fully off the UI path.

Exit: routine provider events and settings changes never run a global refresh.

#### Wave 3: helper protocol and Integration SDK

- implement framed protocol and supervised helper;
- move non-TCC core work behind the helper;
- add capability-driven adapter lifecycle;
- preserve legacy host as the first client.

Exit: killing or restarting the helper does not freeze or crash the host.

#### Wave 4: CodexBar

- implement supervised local `serve` path and one-shot fallback;
- project provider/account/window/incident/freshness data;
- add capacity UI and diagnostics;
- add threshold cues without turning LEDs into a quota dashboard.

Exit: SidePulse and CodexBar agree on fixture snapshots and real local snapshots, including partial failures.

#### Wave 5: T3 Code

- build and package the version-pinned bridge;
- subscribe to shell/thread/session/turn/request/task state;
- reconcile direct and T3-observed sessions;
- add T3 thread-first Command Center projection and deep links;
- enable request responses only after explicit capability and safety tests.

Exit: a real T3 session, child-agent run, approval request, completion, branch, worktree, and PR reference all map correctly without duplicate lights.

#### Wave 6: native Glance and Command Center

- build the native host scenes and stores;
- retain the same helper protocol;
- ship native Glance first, then Command Center;
- preserve legacy host rollback.

Exit: native surfaces meet accessibility and performance budgets and pass installed-upgrade tests.

#### Wave 7: Studio 2 and motion engine

- implement recipe schema, compiler, safety validator, simulators, library, and first-party recipes;
- move appearance editing out of Settings;
- add live reversible preview and cross-surface parity tests.

Exit: every recipe validates on all supported surfaces and accessibility modes.

#### Wave 8: native Diagnostics, Settings, migration, and release

- complete remaining native scenes;
- close migration and rollback paths;
- remove obsolete PyObjC UI only after parity;
- run full signed package, notarization, TCC upgrade, performance, and real-device gates.

Exit: production release candidate meets every outcome in this specification.

## 19. Definition of done

A wave is complete only when:

- code is reachable from the packaged application;
- focused unit, contract, process, and UI tests pass;
- performance evidence is captured where relevant;
- failure and rollback behavior is tested;
- diagnostics explains its state;
- documentation and migration notes are updated;
- no new work is added to `status_bar_legacy.py` or `settings_window.py` unless it is a temporary deletion-enabling bridge with a removal issue and test;
- the branch passes `./scripts/verify.sh --fix` on macOS;
- production claims are based on the installed signed build, not only source-tree tests.

The complete program is done when Wave 8 passes and the legacy UI can be removed without changing provider, policy, history, hardware, permission, or user-data behavior.

## 20. Non-goals

- replacing T3 as an orchestrator;
- replacing CodexBar's provider authentication or fetchers;
- building a general repository editor;
- direct commit, push, merge, or arbitrary command execution;
- cloud synchronization of private transcripts;
- remote control before local capability, authentication, and threat-model gates are complete;
- decorative animation as the default product behavior;
- changing bundle identity during the production migration.
